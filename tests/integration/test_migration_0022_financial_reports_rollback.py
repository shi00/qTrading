"""D04-F01：0022 populated 回退集成用例（多公告版本 fail-closed / 显式有损路径）。

独立文件原因：避免与 D04-F02 / D04-F03 共享 tests/integration/test_data_db_migrator.py
的 helper 与 TestDowngradeAndReupgrade 区域产生跨问题合并冲突。

NOTE: 本文件需要真实 PostgreSQL；当前沙箱无 PostgreSQL，全部用例 not_run。
"""

import asyncio
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.db_migrator import DatabaseMigrator
from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg

pytestmark = pytest.mark.integration

# 标识符无法用 $1 绑定；沿用 conftest 的常量模板 + format(%I) 方案（R4）。
_DDL_CREATE_DB = "CREATE DATABASE %I"
_DDL_DROP_DB = "DROP DATABASE IF EXISTS %I WITH (FORCE)"

_INSERT_FINANCIAL_VERSION_SQL = text(
    """
    INSERT INTO financial_reports (ts_code, end_date, ann_date, report_type)
    VALUES (:ts_code, :end_date, :ann_date, :report_type)
    """
)

_MULTI_VERSION_FINANCIAL_ROWS = (
    ("999999.SZ", date(2025, 12, 31), date(2026, 3, 1)),
    ("999999.SZ", date(2025, 12, 31), date(2026, 4, 1)),
    ("000001.SZ", date(2025, 12, 31), date(2026, 3, 1)),
)

_SELECT_FINANCIAL_VERSIONS_SQL = text(
    "SELECT ts_code, end_date, ann_date FROM financial_reports ORDER BY ts_code, ann_date"
)


async def _execute_quoted_ddl(conn: asyncpg.Connection, template: str, db_name: str) -> None:
    """以服务端 format(%I) 引用库名执行 DDL，避免源码级 SQL 拼接（R4）。"""
    ddl = await conn.fetchval("SELECT format($1::text, $2::text)", template, db_name)
    await conn.execute(ddl)


async def _create_isolated_db(params: dict, db_name: str) -> None:
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        await _execute_quoted_ddl(conn, _DDL_CREATE_DB, db_name)
    finally:
        await conn.close()


async def _drop_isolated_db(params: dict, db_name: str) -> None:
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        await _execute_quoted_ddl(conn, _DDL_DROP_DB, db_name)
    finally:
        await conn.close()


async def _migrated_db_fixture(params: dict, db_name_prefix: str) -> tuple[AsyncEngine, str, str, Any]:
    """Create an isolated DB and migrate it to Alembic head."""
    db_name = f"{db_name_prefix}_{uuid.uuid4().hex[:8]}"
    await _create_isolated_db(params, db_name)

    sync_url, async_url = build_db_urls(params, db_name)
    sync_engine = create_engine(sync_url)

    with override_db_url(sync_url):
        cfg = make_alembic_cfg(sync_url)
        await asyncio.to_thread(command.upgrade, cfg, "head")

    async_engine = create_test_engine(async_url)
    return async_engine, db_name, sync_url, sync_engine


@pytest_asyncio.fixture(loop_scope="function")
async def head_db_engine():
    """Isolated DB at Alembic head; 复用 conftest 的隔离 fixture 约定（名称在 _ISOLATED_DB_FIXTURES）。"""
    params = get_pg_connection_params()
    async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(params, "migrator_0022")
    try:
        yield async_engine, db_name, sync_url, sync_engine
    finally:
        await async_engine.dispose()
        sync_engine.dispose()
        await _drop_isolated_db(params, db_name)


def _insert_financial_versions(sync_engine: Any) -> None:
    """Insert two announcement versions for one report period plus a single-version row."""
    with sync_engine.begin() as conn:
        for ts_code, end_date, ann_date in _MULTI_VERSION_FINANCIAL_ROWS:
            conn.execute(
                _INSERT_FINANCIAL_VERSION_SQL,
                {"ts_code": ts_code, "end_date": end_date, "ann_date": ann_date, "report_type": "Q4"},
            )


def _financial_primary_key_columns(sync_engine: Any) -> list[str]:
    return list(sa_inspect(sync_engine).get_pk_constraint("financial_reports")["constrained_columns"])


def _financial_version_rows(sync_engine: Any) -> list[tuple]:
    with sync_engine.connect() as conn:
        return [tuple(row) for row in conn.execute(_SELECT_FINANCIAL_VERSIONS_SQL)]


def _financial_column_is_nullable(sync_engine: Any, column_name: str) -> bool:
    columns = {c["name"]: c for c in sa_inspect(sync_engine).get_columns("financial_reports")}
    return bool(columns[column_name]["nullable"])


@pytest.mark.asyncio
async def test_downgrade_refuses_multi_version_financial_reports(head_db_engine):
    """D04-F01: populated 多公告版本默认 fail-closed，停在 head 且不删数据。

    NOTE: 沙箱无 PostgreSQL，本用例 not_run（真实 DB 场景）。
    """
    engine, db_name, sync_db_url, sync_engine = head_db_engine
    _insert_financial_versions(sync_engine)
    head_rev = await DatabaseMigrator._get_head_revision()

    with override_db_url(sync_db_url):

        def _do_downgrade():
            cfg = make_alembic_cfg(sync_db_url)
            command.downgrade(cfg, "0021")

        with pytest.raises(RuntimeError, match="多个公告版本"):
            await asyncio.to_thread(_do_downgrade)

    # 事务回滚：版本仍为 head，三键主键与全部行保持不变。
    current, _, _ = await DatabaseMigrator.check_schema_status(engine)
    assert current == head_rev
    assert _financial_primary_key_columns(sync_engine) == ["ts_code", "end_date", "ann_date"]
    assert len(_financial_version_rows(sync_engine)) == len(_MULTI_VERSION_FINANCIAL_ROWS)
    assert _financial_column_is_nullable(sync_engine, "ann_date") is False


@pytest.mark.asyncio
async def test_downgrade_lossy_path_keeps_latest_announcement_version(head_db_engine):
    """D04-F01: 显式 x-arg 授权后保留每组最新 ann_date，并恢复旧主键。

    NOTE: 沙箱无 PostgreSQL，本用例 not_run（真实 DB 场景）。
    """
    engine, db_name, sync_db_url, sync_engine = head_db_engine
    _insert_financial_versions(sync_engine)

    with override_db_url(sync_db_url):

        def _do_downgrade():
            cfg = make_alembic_cfg(sync_db_url)
            cfg.cmd_opts = SimpleNamespace(x=["allow_lossy_rollback=1"])
            command.downgrade(cfg, "0021")

        await asyncio.to_thread(_do_downgrade)

    rows = _financial_version_rows(sync_engine)
    assert ("999999.SZ", date(2025, 12, 31), date(2026, 4, 1)) in rows
    assert ("999999.SZ", date(2025, 12, 31), date(2026, 3, 1)) not in rows
    assert ("000001.SZ", date(2025, 12, 31), date(2026, 3, 1)) in rows
    assert len(rows) == 2
    assert _financial_primary_key_columns(sync_engine) == ["ts_code", "end_date"]
    assert _financial_column_is_nullable(sync_engine, "ann_date") is True

    # 再升级回 head：保留行非 NULL ann_date，不应被 upgrade 清理。
    with override_db_url(sync_db_url):

        def _do_upgrade():
            cfg = make_alembic_cfg(sync_db_url)
            command.upgrade(cfg, "head")

        await asyncio.to_thread(_do_upgrade)

    assert _financial_primary_key_columns(sync_engine) == ["ts_code", "end_date", "ann_date"]
    assert len(_financial_version_rows(sync_engine)) == 2
    head_rev = await DatabaseMigrator._get_head_revision()
    current, _, _ = await DatabaseMigrator.check_schema_status(engine)
    assert current == head_rev
