"""Agent 运行库独立迁移器（设计 §10.6 / HANDOFF §8.1）。

职责边界（C-05）：
- 编排双迁移：先 ``DBOS.migrate``（系统 schema ``dbos``，不传 ``application_role``
  ——其 ALL PRIVILEGES 授权过宽，HANDOFF §8.1 第 3 条），后
  ``SQLAlchemyDatasource.migrate``（事务 schema ``agent_ds`` + 运行角色最小授权）；
- 对运行角色补系统库最小权限并撤销 ``dbos.dbos_migrations`` 写权限（第 3 条）；
- 启动前版本守卫（第 6 条）：``dbos.dbos_migrations`` 与
  ``agent_ds.dbos_datasource_migrations`` **精确相等**冻结合同值，缺/旧/未来均拒绝；
  DBOS 自带校验容忍未来版本，应用层必须补此精确校验。

明确不做（部署流程职责，HANDOFF §8.1 第 1/5 条）：
- migrator / agent_runtime 角色的创建与成员资格授予；
- 运行库 PUBLIC 权限与 public schema CREATE 撤销、业务库 PUBLIC CONNECT 收紧。
本模块假设迁移连接已以 migrator 角色建立且其具备两 schema 对象所有权。

SQL 合规（R4）：全部经静态 ``sa.text()`` 常量承载——schema / 角色名 / 库名均为设计
冻结值（无用户输入、零内插、零拼接）；唯一参数化项为角色存在性查询的 ``:role``。
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from dbos import DBOS, SQLAlchemyDatasource
from dbos._datasource_migration import get_datasource_migration_versions
from dbos._migration import get_migration_versions
from sqlalchemy import text

#: 冻结合同（HANDOFF §8.1 第 6 条）：dbos==3.7.0 固定路线下系统迁移版本数。
EXPECTED_DBOS_MIGRATION_VERSION = 136
#: 冻结合同（HANDOFF §8.1 第 6 条）：Datasource 迁移版本数（datasource_outputs 一条）。
EXPECTED_DATASOURCE_MIGRATION_VERSION = 1

DBOS_SCHEMA = "dbos"
AGENT_DS_SCHEMA = "agent_ds"
RUNTIME_ROLE = "agent_runtime"
RUNTIME_DATABASE = "qtrading_agent_runtime"

# 运行角色最小权限（HANDOFF §8.1 第 3 条）：CONNECT + dbos USAGE + 系统表 CRUD + 序列
# + schema 内函数 EXECUTE（迁移 132 以 CREATE OR REPLACE 定义 dbos.enqueue_workflow；
# 显式授予运行角色，不依赖 PostgreSQL 对 PUBLIC 的默认 EXECUTE），随后撤销
# dbos_migrations 写权。全部为全静态 SQL 字面量（R4：schema/角色/库名为设计冻结值
# 直接内联，零内插零拼接，text() 常量承载）。
_RUNTIME_ROLE_GRANTS: tuple[sa.TextClause, ...] = (
    text("GRANT CONNECT ON DATABASE qtrading_agent_runtime TO agent_runtime"),
    text("GRANT USAGE ON SCHEMA dbos TO agent_runtime"),
    text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA dbos TO agent_runtime"),
    text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA dbos TO agent_runtime"),
    text("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA dbos TO agent_runtime"),
)
_RUNTIME_MIGRATIONS_WRITE_REVOKE = text(
    "REVOKE INSERT, UPDATE, DELETE ON TABLE dbos.dbos_migrations FROM agent_runtime"
)


class AgentRuntimeMigrationError(RuntimeError):
    """Agent 运行库迁移/版本守卫失败（数据层域错误，对齐 EngineDisposedError 惯例）。"""


@dataclass(frozen=True)
class AgentRuntimeMigrationSnapshot:
    """双迁移后的版本快照（recorded = 库内已应用；latest = 已安装 SDK 的最新版本数）。"""

    dbos_recorded: int
    dbos_latest: int
    datasource_recorded: int
    datasource_latest: int


def run_agent_runtime_migrations(migration_url: str) -> AgentRuntimeMigrationSnapshot:
    """执行双迁移、授予运行角色最小权限并校验版本精确相等。

    双迁移中间失败时异常原样传播（迁移各自在 DBOS 事务内，已提交部分保持原状态），
    不创建引擎、不授予运行角色权限、不返回快照——调用方不得在此异常后 launch（§10.6）。

    Args:
        migration_url: 同步 psycopg 迁移连接 URL（``postgresql+psycopg://``，含明文
            密码，仅内存传递，不入日志/manifest）。以 migrator 角色建立。

    Returns:
        校验通过的版本快照（recorded == latest == 冻结合同值）。
    """
    _run_double_migration(migration_url)
    engine = sa.create_engine(migration_url)
    try:
        snapshot = verify_agent_runtime_versions(engine)
        _apply_runtime_role_grants(engine)
        return snapshot
    finally:
        engine.dispose()


def verify_agent_runtime_versions(engine: sa.Engine) -> AgentRuntimeMigrationSnapshot:
    """启动前版本守卫：recorded 与 latest 均须与冻结合同精确相等。

    缺失（表不存在读作 0）/旧/未来版本、或已安装 SDK 的迁移版本数与冻结合同不符
    （依赖漂移，升级须重新核，HANDOFF §8.1 第 6 条「升级重新核」）均抛出
    :class:`AgentRuntimeMigrationError`。
    """
    dbos_recorded, dbos_latest = get_migration_versions(engine, DBOS_SCHEMA, use_listen_notify=True)
    with engine.connect() as conn:
        ds_recorded, ds_latest = get_datasource_migration_versions(conn, AGENT_DS_SCHEMA)
    snapshot = AgentRuntimeMigrationSnapshot(
        dbos_recorded=dbos_recorded,
        dbos_latest=dbos_latest,
        datasource_recorded=ds_recorded,
        datasource_latest=ds_latest,
    )
    _verify_snapshot(snapshot)
    return snapshot


def _verify_snapshot(snapshot: AgentRuntimeMigrationSnapshot) -> None:
    """版本守卫纯函数：四元组逐一精确相等（消息不含 URL，R9 脱敏边界）。"""
    if snapshot.dbos_latest != EXPECTED_DBOS_MIGRATION_VERSION:
        raise AgentRuntimeMigrationError(
            f"已安装 dbos 的系统迁移版本数 {snapshot.dbos_latest} 与冻结合同 "
            f"{EXPECTED_DBOS_MIGRATION_VERSION} 不一致（依赖漂移），拒绝启动；"
            "升级 dbos 须重新核迁移版本守卫（HANDOFF §8.1 第 6 条）"
        )
    if snapshot.dbos_recorded != EXPECTED_DBOS_MIGRATION_VERSION:
        raise AgentRuntimeMigrationError(
            f"运行库系统迁移版本 {snapshot.dbos_recorded} 与冻结合同 "
            f"{EXPECTED_DBOS_MIGRATION_VERSION} 不一致（缺失/旧/未来版本均拒绝），拒绝启动"
        )
    if snapshot.datasource_latest != EXPECTED_DATASOURCE_MIGRATION_VERSION:
        raise AgentRuntimeMigrationError(
            f"已安装 dbos 的 Datasource 迁移版本数 {snapshot.datasource_latest} 与冻结合同 "
            f"{EXPECTED_DATASOURCE_MIGRATION_VERSION} 不一致（依赖漂移），拒绝启动"
        )
    if snapshot.datasource_recorded != EXPECTED_DATASOURCE_MIGRATION_VERSION:
        raise AgentRuntimeMigrationError(
            f"运行库 Datasource 迁移版本 {snapshot.datasource_recorded} 与冻结合同 "
            f"{EXPECTED_DATASOURCE_MIGRATION_VERSION} 不一致（缺失/旧/未来版本均拒绝），拒绝启动"
        )


def _run_double_migration(migration_url: str) -> None:
    """按 HANDOFF §8.1 第 2 条顺序执行双迁移（DBOS 系统在前，Datasource 在后）。"""
    DBOS.migrate(migration_url, schema=DBOS_SCHEMA, sharding=False)
    SQLAlchemyDatasource.migrate(migration_url, schema=AGENT_DS_SCHEMA, application_role=RUNTIME_ROLE)


def _apply_runtime_role_grants(engine: sa.Engine) -> None:
    """授予运行角色最小权限并撤销 dbos_migrations 写权（HANDOFF §8.1 第 3 条）。"""
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = :role"),
            {"role": RUNTIME_ROLE},
        ).fetchone()
        if exists is None:
            raise AgentRuntimeMigrationError(
                f"运行角色 {RUNTIME_ROLE} 不存在（角色创建属部署流程职责，HANDOFF §8.1 第 1/5 条），拒绝授权"
            )
        for statement in _RUNTIME_ROLE_GRANTS:
            conn.execute(statement)
        conn.execute(_RUNTIME_MIGRATIONS_WRITE_REVOKE)
