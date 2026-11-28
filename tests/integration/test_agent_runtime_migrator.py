"""Agent 运行库独立迁移器集成测试（设计 §10.6 / HANDOFF §8.1）。

真实 sidecar PG 上验证：
- 双迁移顺序与产物：dbos（系统）+ agent_ds（事务检查点）schema 建立且版本精确 136/1
- 运行角色最小权限：系统表 CRUD 授予 + dbos_migrations 写权撤销 + Datasource 最小授权
- 版本守卫：缺/旧/未来均拒绝启动
- 运行库为独立逻辑库 ``qtrading_agent_runtime``（同一 PG 实例，与业务库分离）

编排顺序契约与守卫纯函数矩阵见 ``tests/unit/test_agent_runtime_migrator.py``。

标记：``pytest.mark.integration`` + ``pytest.mark.embedded_real``。
SQL 合规（R4）：全部经静态 ``sa.text()`` 常量承载（schema/角色/库名为设计冻结值）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from data.persistence.agent_migrator import (
    AGENT_DS_SCHEMA,
    DBOS_SCHEMA,
    EXPECTED_DATASOURCE_MIGRATION_VERSION,
    EXPECTED_DBOS_MIGRATION_VERSION,
    RUNTIME_ROLE,
    AgentRuntimeMigrationError,
    run_agent_runtime_migrations,
    verify_agent_runtime_versions,
)
from tests._helpers import create_test_engine

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


async def _create_runtime_database(admin_url: str) -> None:
    """在 sidecar 实例上创建独立逻辑库 qtrading_agent_runtime（幂等）。"""
    engine = create_test_engine(admin_url)
    try:
        async with engine.connect() as conn:
            exists = (
                await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'qtrading_agent_runtime'"))
            ).fetchone()
    finally:
        await engine.dispose()
    if exists is None:
        # CREATE DATABASE 不能在事务内执行，须 AUTOCOMMIT 连接。
        admin_engine = create_test_engine(admin_url)
        try:
            async with admin_engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text("CREATE DATABASE qtrading_agent_runtime"))
        finally:
            await admin_engine.dispose()


async def _drop_runtime_database(admin_url: str) -> None:
    """删除测试用独立逻辑库（隔离 sidecar 实例上的临时产物）。"""
    engine = create_test_engine(admin_url)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = 'qtrading_agent_runtime' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text("DROP DATABASE IF EXISTS qtrading_agent_runtime"))
    finally:
        await engine.dispose()


async def _ensure_runtime_role(admin_url: str) -> None:
    """确保运行角色 agent_runtime 存在（角色为实例级，创建一次；部署流程职责的测试替身）。"""
    engine = create_test_engine(admin_url)
    try:
        async with engine.connect() as conn:
            exists = (
                await conn.execute(text("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'agent_runtime'"))
            ).fetchone()
    finally:
        await engine.dispose()
    if exists is None:
        role_engine = create_test_engine(admin_url)
        try:
            async with role_engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text("CREATE ROLE agent_runtime LOGIN"))
        finally:
            await role_engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def agent_runtime_pg_url(real_embedded_pg) -> AsyncGenerator[str]:
    """提供独立逻辑库 qtrading_agent_runtime 的同步 psycopg 迁移 URL（module 级）。"""
    info = real_embedded_pg
    admin_url = info.url
    await _create_runtime_database(admin_url)
    await _ensure_runtime_role(admin_url)
    sync_url = (
        sa.make_url(admin_url)
        .set(drivername="postgresql+psycopg", database="qtrading_agent_runtime")
        .render_as_string(hide_password=False)
    )
    yield sync_url
    await _drop_runtime_database(admin_url)


async def _reset_runtime_schemas(sync_url: str) -> None:
    """重置运行库两 schema：DROP CASCADE + 重建，保证测试顺序无关。

    schema 名为设计冻结值，SQL 全静态字面量（R4：text() 常量承载，零内插）。
    """
    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS dbos CASCADE"))
            conn.execute(text("DROP SCHEMA IF EXISTS agent_ds CASCADE"))
            conn.execute(text("CREATE SCHEMA dbos"))
            conn.execute(text("CREATE SCHEMA agent_ds"))
    finally:
        engine.dispose()


def _has_table_privilege(sync_url: str, schema: str, table: str, privilege: str) -> bool:
    """以 information_schema 查询运行角色对表的指定权限。"""
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT has_table_privilege(:role, :qualified_table, :privilege)"),
                {"role": RUNTIME_ROLE, "qualified_table": f"{schema}.{table}", "privilege": privilege},
            ).fetchone()
            assert row is not None
            return bool(row[0])
    finally:
        engine.dispose()


def _has_function_privilege(sync_url: str, schema: str, function: str) -> bool:
    """查询运行角色对 schema 内同名函数的 EXECUTE 权限（断言函数存在且唯一）。"""
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT COUNT(*), bool_and(has_function_privilege(:role, p.oid, 'EXECUTE')) "
                    "FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = :schema AND p.proname = :function"
                ),
                {"role": RUNTIME_ROLE, "schema": schema, "function": function},
            ).fetchone()
            assert row is not None
            assert row[0] == 1
            return bool(row[1])
    finally:
        engine.dispose()


class TestAgentRuntimeMigratorRealPg:
    """真实 PG 上的双迁移、最小权限与版本守卫。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_fresh_migrate_creates_schemas_and_exact_versions(self, agent_runtime_pg_url: str) -> None:
        """全新库双迁移：版本精确 136/1，两 schema 产物齐备。"""
        url = agent_runtime_pg_url
        await _reset_runtime_schemas(url)

        snapshot = run_agent_runtime_migrations(url)

        assert EXPECTED_DBOS_MIGRATION_VERSION == 136
        assert EXPECTED_DATASOURCE_MIGRATION_VERSION == 1
        assert snapshot.dbos_recorded == 136
        assert snapshot.dbos_latest == 136
        assert snapshot.datasource_recorded == 1
        assert snapshot.datasource_latest == 1

        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                dbos_tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'dbos'")
                    ).fetchall()
                }
                assert "dbos_migrations" in dbos_tables
                ds_tables = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'agent_ds'")
                    ).fetchall()
                }
                assert "datasource_outputs" in ds_tables
                assert "dbos_datasource_migrations" in ds_tables
        finally:
            engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_runtime_role_minimal_privileges(self, agent_runtime_pg_url: str) -> None:
        """运行角色：系统表 CRUD + dbos_migrations 写权撤销 + Datasource 最小三句。"""
        url = agent_runtime_pg_url
        await _reset_runtime_schemas(url)
        run_agent_runtime_migrations(url)

        # dbos 系统表：运行角色可 CRUD（抽查 workflow_status 与 dbos_migrations SELECT）
        assert _has_table_privilege(url, DBOS_SCHEMA, "workflow_status", "SELECT")
        assert _has_table_privilege(url, DBOS_SCHEMA, "workflow_status", "INSERT")
        assert _has_table_privilege(url, DBOS_SCHEMA, "workflow_status", "UPDATE")
        assert _has_table_privilege(url, DBOS_SCHEMA, "workflow_status", "DELETE")
        # dbos_migrations 写权已撤销（HANDOFF §8.1 第 3 条），SELECT 保留
        assert _has_table_privilege(url, DBOS_SCHEMA, "dbos_migrations", "INSERT") is False
        assert _has_table_privilege(url, DBOS_SCHEMA, "dbos_migrations", "UPDATE") is False
        assert _has_table_privilege(url, DBOS_SCHEMA, "dbos_migrations", "DELETE") is False
        assert _has_table_privilege(url, DBOS_SCHEMA, "dbos_migrations", "SELECT")
        # PostgreSQL 默认给 PUBLIC 授予函数 EXECUTE；先收回默认权限，再断言运行角色
        # 仍可执行，才能真正覆盖 _RUNTIME_ROLE_GRANTS 的显式 GRANT EXECUTE 授权。
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(text("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA dbos FROM PUBLIC"))
        finally:
            engine.dispose()
        assert _has_function_privilege(url, DBOS_SCHEMA, "enqueue_workflow")
        # Datasource 最小授权（HANDOFF §8.1 第 4 条）：outputs 可读写删、迁移表只读、
        # 检查点表无 UPDATE
        assert _has_table_privilege(url, AGENT_DS_SCHEMA, "datasource_outputs", "SELECT")
        assert _has_table_privilege(url, AGENT_DS_SCHEMA, "datasource_outputs", "INSERT")
        assert _has_table_privilege(url, AGENT_DS_SCHEMA, "datasource_outputs", "DELETE")
        assert _has_table_privilege(url, AGENT_DS_SCHEMA, "datasource_outputs", "UPDATE") is False
        assert _has_table_privilege(url, AGENT_DS_SCHEMA, "dbos_datasource_migrations", "SELECT")
        assert _has_table_privilege(url, AGENT_DS_SCHEMA, "dbos_datasource_migrations", "INSERT") is False

    @pytest.mark.asyncio(loop_scope="session")
    async def test_version_guard_refuses_tampered_versions(self, agent_runtime_pg_url: str) -> None:
        """版本守卫：recorded 被篡改（旧 135 / 未来 137）与缺失（0）均拒绝。"""
        url = agent_runtime_pg_url
        await _reset_runtime_schemas(url)
        run_agent_runtime_migrations(url)

        engine = sa.create_engine(url)
        try:
            for tampered in (135, 137):
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE dbos.dbos_migrations SET version = :v"),
                        {"v": tampered},
                    )
                with pytest.raises(AgentRuntimeMigrationError, match="136"):
                    verify_agent_runtime_versions(engine)
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE dbos.dbos_migrations SET version = :v"),
                        {"v": 136},
                    )
            # 缺失表（DROP 后 recorded 读作 0）→ 拒绝
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE dbos.dbos_migrations"))
            with pytest.raises(AgentRuntimeMigrationError, match="136"):
                verify_agent_runtime_versions(engine)
        finally:
            engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_datasource_guard_refuses_tampered_versions(self, agent_runtime_pg_url: str) -> None:
        """Datasource 版本守卫：recorded != 1 精确相等即拒绝（含未来版本）。"""
        url = agent_runtime_pg_url
        await _reset_runtime_schemas(url)
        run_agent_runtime_migrations(url)

        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE agent_ds.dbos_datasource_migrations SET version = :v"),
                    {"v": 2},
                )
            with pytest.raises(AgentRuntimeMigrationError, match="1"):
                verify_agent_runtime_versions(engine)
        finally:
            engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reentrant_migrate_is_idempotent(self, agent_runtime_pg_url: str) -> None:
        """重复迁移幂等：已到位后再次运行不报错、版本不变。"""
        url = agent_runtime_pg_url
        await _reset_runtime_schemas(url)
        first = run_agent_runtime_migrations(url)
        second = run_agent_runtime_migrations(url)
        assert first == second
