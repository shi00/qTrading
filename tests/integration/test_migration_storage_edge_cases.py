"""D04-F06 迁移存量/存储边界集成测试（真实 Alembic upgrade/downgrade）。

覆盖矩阵（与 PR body 一致）：
- 0016 代表旧库携数据升级到 head：结构、版本、行数与备注值守恒；
- 0022 upgrade 清理 NULL ann_date 行（不可逆）、主键扩展后保留同一报告期多版本行；
- 0018 非法 stock_basic.ts_code 使 CHECK 建立失败时不删行、不部分提交，修正后重试成功；
- screening_thinking.history_id 由 integer 升 bigint、回退窄化到 int32 的边界（> 2^31-1 失败关闭）；
- 0031 携数据 upgrade/downgrade/re-upgrade：结构、版本、行数与前映像（逻辑备份）校验。
- D06-001：0029→head 后 EXPIRED 行不携带旧口径收益/胜负（0046 前向修正），且统计
  在 DISTINCT ON 之后掩码（最新 EXPIRED + 更旧 COMPLETED 不复活）。

未覆盖（不在此伪造通过）：
- 跨进程并发迁移需要强隔离 PostgreSQL + 多进程编排，同进程 ``asyncio.gather``
  无法证明跨进程锁语义；
- 迁移中途真实连接断开（非事务内 DDL 失败）导致的重试路径，需要可注入断连的
  PG 代理，本 PR 以事务内失败注入（0018 CHECK 建立失败）覆盖「不部分提交 + 重试」；
- 取消时真实 DDL 线程退出/连接释放由单元测试覆盖「CancelledError 传播 + schema
  标记不置位」，未在真库上观测线程退出时序。
上述项如实记录在 PR body，不作为通过项。

本文件在无 PostgreSQL 群集的沙箱中不运行：fixture 连接失败会显式报错，不做静默 skip。
CI 由 ``-n auto`` 调度，但每个用例创建独立临时库（随机名），用例间无共享状态。
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 测试内使用 run_sync 注入 Inspector 与 asyncpg 连接参数，pyright 无法完整推断其结果类型。

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.db_migrator import DatabaseMigrator
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.database]


@dataclass(frozen=True)
class _MigrationDb:
    """独立临时库句柄：upsert 用 async engine，Alembic 用同步 URL。"""

    name: str
    sync_url: str
    engine: AsyncEngine


@pytest.fixture
def pg_params() -> dict:
    return get_pg_connection_params()


@pytest_asyncio.fixture(loop_scope="function")
async def alembic_db_engine(pg_params: dict) -> AsyncIterator[_MigrationDb]:
    """为单个用例创建/销毁独立数据库，避免与共享 test_engine 相互污染。"""
    db_name = f"d04f06_{uuid.uuid4().hex[:12]}"
    await _exec_ident_ddl(pg_params, "CREATE DATABASE %I", db_name)
    sync_url, async_url = build_db_urls(pg_params, db_name)
    # 本文件在 async engine 连接存活期间反复执行 Alembic DDL（schema 变更），
    # 关闭 asyncpg prepared statement 缓存，避免命中 InvalidCachedStatementError。
    engine = create_test_engine(async_url, connect_args={"statement_cache_size": 0})
    try:
        yield _MigrationDb(name=db_name, sync_url=sync_url, engine=engine)
    finally:
        await engine.dispose()
        await _exec_ident_ddl(pg_params, "DROP DATABASE IF EXISTS %I WITH (FORCE)", db_name)


async def _exec_ident_ddl(params: dict, template: str, db_name: str) -> None:
    """执行 CREATE/DROP DATABASE；标识符经 format($1, $2) 转义，不拼接 SQL。"""
    conn = await asyncpg.connect(
        host=str(params["host"]),
        port=int(params["port"]),
        user=str(params["user"]),
        password=str(params["password"]),
        database="postgres",
        timeout=10,
    )
    try:
        ddl = await conn.fetchval("SELECT format($1::text, $2::text)", template, db_name)
        await conn.execute(str(ddl))
    finally:
        await conn.close()


async def _upgrade(db: _MigrationDb, revision: str) -> None:
    cfg = make_alembic_cfg(db.sync_url)
    await asyncio.to_thread(command.upgrade, cfg, revision)


async def _downgrade(db: _MigrationDb, revision: str) -> None:
    cfg = make_alembic_cfg(db.sync_url)
    await asyncio.to_thread(command.downgrade, cfg, revision)


async def _execute(engine: AsyncEngine, statement: str, params: dict | None = None) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(statement), params or {})


async def _scalar(engine: AsyncEngine, statement: str, params: dict | None = None) -> Any:
    async with engine.connect() as conn:
        return await conn.scalar(text(statement), params or {})


async def _count_rows(engine: AsyncEngine, statement: str) -> int:
    value = await _scalar(engine, statement)
    return int(value)


async def _snapshot(engine: AsyncEngine, statement: str) -> list[tuple]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text(statement))).fetchall()
    return [tuple(row) for row in rows]


async def _assert_backup_preserved(engine: AsyncEngine, backup: dict[str, list[tuple]]) -> None:
    """逻辑备份校验：键必须是可执行的 SQL 语句，值是该语句在迁移前的行快照。"""
    for statement, expected in backup.items():
        actual = await _snapshot(engine, statement)
        assert actual == expected, f"迁移前后数据快照不一致: {statement}"


async def _current_version(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        exists = await conn.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if exists is None:
            return None
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    return str(version) if version is not None else None


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
        ).fetchall()
    return {str(row[0]) for row in rows}


async def _columns(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        columns = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns(table))
    return {str(column["name"]) for column in columns}


async def _primary_key_columns(engine: AsyncEngine, table: str) -> list[str]:
    async with engine.connect() as conn:
        primary_key = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_pk_constraint(table))
    return [str(name) for name in primary_key["constrained_columns"]]


async def _index_names(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        indexes = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_indexes(table))
    return {str(index["name"]) for index in indexes}


async def _check_constraint_names(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        constraints = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_check_constraints(table))
    return {str(constraint["name"]) for constraint in constraints}


async def _unique_constraint_names(engine: AsyncEngine, table: str) -> set[str]:
    async with engine.connect() as conn:
        constraints = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_unique_constraints(table))
    return {str(constraint["name"]) for constraint in constraints}


async def _column_info(engine: AsyncEngine, table: str, column: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT data_type, is_nullable, numeric_precision "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = :table_name AND column_name = :column_name"
                    ),
                    {"table_name": table, "column_name": column},
                )
            )
            .mappings()
            .first()
        )
    assert row is not None, f"列不存在: {table}.{column}"
    return dict(row)


async def test_0016_populated_database_upgrades_to_head(alembic_db_engine: _MigrationDb) -> None:
    """0016 旧库携 stock_basic/watchlist 数据升级到 head，结构与数据均守恒。"""
    db = alembic_db_engine
    await _upgrade(db, "0016")
    assert await _current_version(db.engine) == "0016"

    await _execute(
        db.engine,
        "INSERT INTO stock_basic (ts_code, symbol, name, list_status) VALUES (:ts_code, :symbol, :name, :list_status)",
        {"ts_code": "600000.SH", "symbol": "600000", "name": "浦发银行", "list_status": "L"},
    )
    await _execute(
        db.engine,
        "INSERT INTO watchlist (ts_code, stock_name, note) VALUES (:ts_code, :stock_name, :note)",
        {"ts_code": "600000.SH", "stock_name": "浦发银行", "note": "D04-F06 seeded"},
    )
    stock_rows = await _count_rows(db.engine, "SELECT count(*) FROM stock_basic")
    watch_rows = await _count_rows(db.engine, "SELECT count(*) FROM watchlist")
    assert stock_rows == 1
    assert watch_rows == 1

    await DatabaseMigrator.init_db(db.engine, auto_migrate=True)

    assert await _current_version(db.engine) == await DatabaseMigrator._get_head_revision()
    assert await _count_rows(db.engine, "SELECT count(*) FROM stock_basic") == stock_rows
    assert await _count_rows(db.engine, "SELECT count(*) FROM watchlist") == watch_rows
    assert (
        await _scalar(
            db.engine,
            "SELECT note FROM watchlist WHERE ts_code = :ts_code",
            {"ts_code": "600000.SH"},
        )
        == "D04-F06 seeded"
    )

    tables = await _table_names(db.engine)
    assert "stock_name_history" in tables
    assert "news_risk_brief" in tables
    assert {"added_trade_date", "added_price"} <= await _columns(db.engine, "watchlist")
    assert "persist_seq" in await _columns(db.engine, "task_history")
    assert await _primary_key_columns(db.engine, "financial_reports") == [
        "ts_code",
        "end_date",
        "ann_date",
    ]


async def test_0022_cleans_null_ann_date_and_preserves_multiversion_rows(
    alembic_db_engine: _MigrationDb,
) -> None:
    """0022 upgrade 删除 NULL ann_date 行（不可逆），同一报告期多版本行保留。"""
    db = alembic_db_engine
    await _upgrade(db, "0021")
    assert await _current_version(db.engine) == "0021"
    assert (await _column_info(db.engine, "financial_reports", "ann_date"))["is_nullable"] == "YES"

    await _execute(
        db.engine,
        "INSERT INTO financial_reports (ts_code, end_date, ann_date, total_revenue) "
        "VALUES (:ts_code, :end_date, :ann_date, :total_revenue)",
        {"ts_code": "600000.SH", "end_date": date(2024, 9, 30), "ann_date": None, "total_revenue": 100},
    )
    await _execute(
        db.engine,
        "INSERT INTO financial_reports (ts_code, end_date, ann_date, total_revenue) "
        "VALUES (:ts_code, :end_date, :ann_date, :total_revenue)",
        {"ts_code": "600000.SH", "end_date": date(2024, 12, 31), "ann_date": date(2025, 4, 1), "total_revenue": 200},
    )
    assert await _count_rows(db.engine, "SELECT count(*) FROM financial_reports") == 2

    await _upgrade(db, "0022")
    assert await _current_version(db.engine) == "0022"
    # NULL 公告日脏行被清理；有公告日的行保留。
    assert await _count_rows(db.engine, "SELECT count(*) FROM financial_reports") == 1
    assert await _scalar(
        db.engine,
        "SELECT ann_date FROM financial_reports WHERE end_date = :end_date",
        {"end_date": date(2024, 12, 31)},
    ) == date(2025, 4, 1)
    assert (await _column_info(db.engine, "financial_reports", "ann_date"))["is_nullable"] == "NO"
    assert await _primary_key_columns(db.engine, "financial_reports") == [
        "ts_code",
        "end_date",
        "ann_date",
    ]

    # 回退只还原结构与主键，无法复活被清理的 NULL 行（迁移明示不可逆）。
    await _downgrade(db, "0021")
    assert await _current_version(db.engine) == "0021"
    assert await _count_rows(db.engine, "SELECT count(*) FROM financial_reports") == 1
    assert (await _column_info(db.engine, "financial_reports", "ann_date"))["is_nullable"] == "YES"
    assert await _primary_key_columns(db.engine, "financial_reports") == ["ts_code", "end_date"]

    # 主键扩展后允许同一 (ts_code, end_date) 的多版本行，并随升级保留到 head。
    await _upgrade(db, "0022")
    await _execute(
        db.engine,
        "INSERT INTO financial_reports (ts_code, end_date, ann_date, total_revenue) "
        "VALUES (:ts_code, :end_date, :ann_date, :total_revenue)",
        {"ts_code": "600000.SH", "end_date": date(2024, 12, 31), "ann_date": date(2025, 6, 10), "total_revenue": 210},
    )
    assert await _count_rows(db.engine, "SELECT count(*) FROM financial_reports") == 2

    await DatabaseMigrator.init_db(db.engine, auto_migrate=True)
    assert await _current_version(db.engine) == await DatabaseMigrator._get_head_revision()
    assert await _count_rows(db.engine, "SELECT count(*) FROM financial_reports") == 2
    assert await _count_rows(db.engine, "SELECT count(DISTINCT ann_date) FROM financial_reports") == 2


async def test_0018_invalid_stock_code_check_failure_preserves_rows_and_retry(
    alembic_db_engine: _MigrationDb,
) -> None:
    """0018 CHECK 建立失败时不删行、不部分提交；修正脏行后重试升级成功且约束生效。"""
    db = alembic_db_engine
    await _upgrade(db, "0017")
    assert await _current_version(db.engine) == "0017"
    await _execute(
        db.engine,
        "INSERT INTO stock_basic (ts_code, name, list_status) VALUES (:ts_code, :name, :list_status)",
        {"ts_code": "600000.SH", "name": "浦发银行", "list_status": "L"},
    )
    await _execute(
        db.engine,
        "INSERT INTO stock_basic (ts_code, name, list_status) VALUES (:ts_code, :name, :list_status)",
        {"ts_code": "600000", "name": "临时非法代码", "list_status": "L"},
    )
    assert await _count_rows(db.engine, "SELECT count(*) FROM stock_basic") == 2
    assert "ck_stock_basic_valid_ts_code_format" not in await _check_constraint_names(db.engine, "stock_basic")

    with pytest.raises(IntegrityError, match="valid_ts_code_format"):
        await _upgrade(db, "0018")

    # 事务回滚的完整证据：版本未推进、行未删、0018 内全部 DDL 未部分提交。
    assert await _current_version(db.engine) == "0017"
    assert await _count_rows(db.engine, "SELECT count(*) FROM stock_basic") == 2
    assert "ck_stock_basic_valid_ts_code_format" not in await _check_constraint_names(db.engine, "stock_basic")
    assert "ix_stock_basic_list_date" in await _index_names(db.engine, "stock_basic")
    assert "uq_watchlist_ts_code" not in await _unique_constraint_names(db.engine, "watchlist")

    # 修正脏数据后重试：UPDATE 不删行，原非法行得以保留为合法代码。
    await _execute(
        db.engine,
        "UPDATE stock_basic SET ts_code = :valid WHERE ts_code = :invalid",
        {"valid": "000001.SZ", "invalid": "600000"},
    )
    await DatabaseMigrator.init_db(db.engine, auto_migrate=True)
    assert await _current_version(db.engine) == await DatabaseMigrator._get_head_revision()
    assert await _count_rows(db.engine, "SELECT count(*) FROM stock_basic") == 2
    assert "ck_stock_basic_valid_ts_code_format" in await _check_constraint_names(db.engine, "stock_basic")
    assert "ix_stock_basic_list_date" not in await _index_names(db.engine, "stock_basic")
    assert "uq_watchlist_ts_code" in await _unique_constraint_names(db.engine, "watchlist")

    with pytest.raises(IntegrityError, match="valid_ts_code_format"):
        await _execute(
            db.engine,
            "INSERT INTO stock_basic (ts_code, name) VALUES (:ts_code, :name)",
            {"ts_code": "600000", "name": "再次非法"},
        )
    assert await _count_rows(db.engine, "SELECT count(*) FROM stock_basic") == 2


async def test_screening_thinking_history_id_bigint_boundary(alembic_db_engine: _MigrationDb) -> None:
    """history_id 升 bigint 后可承载 > 2^31-1；回退窄化到 int32 失败关闭，不截断。"""
    db = alembic_db_engine
    large_id = 2**31

    await _upgrade(db, "0017")
    assert await _current_version(db.engine) == "0017"
    assert (await _column_info(db.engine, "screening_thinking", "history_id"))["data_type"] == "integer"
    await _execute(
        db.engine,
        "INSERT INTO screening_history (id, run_id, trade_date, strategy_name, ts_code) "
        "VALUES (:id, :run_id, :trade_date, :strategy_name, :ts_code)",
        {
            "id": large_id,
            "run_id": "F06RID",
            "trade_date": date(2025, 1, 2),
            "strategy_name": "d04f06",
            "ts_code": "600000.SH",
        },
    )
    assert await _count_rows(db.engine, "SELECT count(*) FROM screening_history") == 1

    # 关键：使用常量 SQL 字面量而非绑定参数，避免 asyncpg 在客户端按 int4 提前拒绝，
    # 从而真实验证旧 schema 由 PostgreSQL 服务端赋值 cast 触发 out-of-range 并回滚。
    with pytest.raises(DBAPIError, match="out of range"):
        await _execute(db.engine, "INSERT INTO screening_thinking (history_id) VALUES (2147483648)")
    assert await _count_rows(db.engine, "SELECT count(*) FROM screening_thinking") == 0
    assert await _count_rows(db.engine, "SELECT count(*) FROM screening_history") == 1

    await _upgrade(db, "0018")
    assert await _current_version(db.engine) == "0018"
    assert (await _column_info(db.engine, "screening_thinking", "history_id"))["data_type"] == "bigint"
    await _execute(
        db.engine,
        "INSERT INTO screening_thinking (history_id) VALUES (:history_id)",
        {"history_id": large_id},
    )
    assert await _count_rows(db.engine, "SELECT count(*) FROM screening_thinking") == 1
    assert (
        await _scalar(
            db.engine,
            "SELECT history_id FROM screening_thinking",
        )
        == large_id
    )

    with pytest.raises(DBAPIError, match="out of range"):
        await _downgrade(db, "0017")

    # 失败关闭：窄化失败必须整体回滚，版本/列类型/数据与 0018 状态一致。
    assert await _current_version(db.engine) == "0018"
    assert (await _column_info(db.engine, "screening_thinking", "history_id"))["data_type"] == "bigint"
    assert await _count_rows(db.engine, "SELECT count(*) FROM screening_thinking") == 1
    assert await _count_rows(db.engine, "SELECT count(*) FROM screening_history") == 1
    assert "ck_stock_basic_valid_ts_code_format" in await _check_constraint_names(db.engine, "stock_basic")


async def test_upgrade_downgrade_reupgrade_round_trip(alembic_db_engine: _MigrationDb) -> None:
    """0031 携数据 upgrade→downgrade→re-upgrade：结构、版本、行数与前映像守恒。"""
    db = alembic_db_engine
    await _upgrade(db, "0031")
    assert await _current_version(db.engine) == "0031"

    await _execute(
        db.engine,
        "INSERT INTO watchlist (ts_code, stock_name, note, added_trade_date, added_price) "
        "VALUES (:ts_code, :stock_name, :note, NULL, NULL)",
        {"ts_code": "600000.SH", "stock_name": "浦发银行", "note": "F06 watch 1"},
    )
    await _execute(
        db.engine,
        "INSERT INTO watchlist (ts_code, stock_name, note, added_trade_date, added_price) "
        "VALUES (:ts_code, :stock_name, :note, NULL, NULL)",
        {"ts_code": "000001.SZ", "stock_name": "平安银行", "note": "F06 watch 2"},
    )
    await _execute(
        db.engine,
        "INSERT INTO task_history (id, name, task_type, status) VALUES (:id, :name, :task_type, :status)",
        {"id": "f06-task-1", "name": "D04-F06 task", "task_type": "test", "status": "COMPLETED"},
    )
    await _execute(
        db.engine,
        "INSERT INTO financial_reports (ts_code, end_date, ann_date, total_revenue) "
        "VALUES (:ts_code, :end_date, :ann_date, :total_revenue)",
        {
            "ts_code": "600000.SH",
            "end_date": date(2024, 12, 31),
            "ann_date": date(2025, 4, 1),
            "total_revenue": 321.5,
        },
    )
    await _execute(
        db.engine,
        "INSERT INTO stock_name_history (ts_code, start_date, name) VALUES (:ts_code, :start_date, :name)",
        {"ts_code": "600000.SH", "start_date": date(2020, 1, 1), "name": "浦发银行"},
    )
    await _execute(
        db.engine,
        "INSERT INTO news_risk_brief (ts_code, input_hash, window_start, window_end, "
        "analysis_status, analysis_profile, prompt_version) "
        "VALUES (:ts_code, :input_hash, :window_start, :window_end, "
        ":analysis_status, :analysis_profile, :prompt_version)",
        {
            "ts_code": "600000.SH",
            "input_hash": "a" * 64,
            "window_start": datetime(2025, 1, 1),
            "window_end": datetime(2025, 1, 31),
            "analysis_status": "SUCCESS",
            "analysis_profile": "default",
            "prompt_version": "v1",
        },
    )

    # 逻辑备份键必须是可执行 SQL 语句（曾被误当作 SQL 执行的表名导致 syntax error）。
    watchlist_sql = "SELECT ts_code, note, added_trade_date, added_price FROM watchlist ORDER BY ts_code"
    task_history_sql = "SELECT id, name, task_type, status FROM task_history ORDER BY id"
    financial_reports_sql = (
        "SELECT ts_code, end_date, ann_date, total_revenue FROM financial_reports ORDER BY ts_code, end_date, ann_date"
    )
    stock_name_history_sql = "SELECT ts_code, start_date, name FROM stock_name_history ORDER BY ts_code, start_date"
    news_risk_brief_sql = (
        "SELECT ts_code, input_hash, analysis_status FROM news_risk_brief ORDER BY ts_code, input_hash"
    )
    backup = {
        watchlist_sql: await _snapshot(db.engine, watchlist_sql),
        task_history_sql: await _snapshot(db.engine, task_history_sql),
        financial_reports_sql: await _snapshot(db.engine, financial_reports_sql),
        stock_name_history_sql: await _snapshot(db.engine, stock_name_history_sql),
        news_risk_brief_sql: await _snapshot(db.engine, news_risk_brief_sql),
    }
    for statement, rows in backup.items():
        assert rows, f"{statement} 预置数据为空，快照校验将失去意义"

    await DatabaseMigrator.init_db(db.engine, auto_migrate=True)
    assert await _current_version(db.engine) == await DatabaseMigrator._get_head_revision()
    await _assert_backup_preserved(db.engine, backup)
    assert "execution_metadata" in await _columns(db.engine, "news_risk_brief")
    assert (await _column_info(db.engine, "watchlist", "added_price"))["numeric_precision"] == 24

    await _downgrade(db, "0031")
    assert await _current_version(db.engine) == "0031"
    await _assert_backup_preserved(db.engine, backup)
    assert "execution_metadata" not in await _columns(db.engine, "news_risk_brief")
    assert (await _column_info(db.engine, "watchlist", "added_price"))["numeric_precision"] == 12

    await DatabaseMigrator.init_db(db.engine, auto_migrate=True)
    assert await _current_version(db.engine) == await DatabaseMigrator._get_head_revision()
    await _assert_backup_preserved(db.engine, backup)
    assert "execution_metadata" in await _columns(db.engine, "news_risk_brief")
    assert (await _column_info(db.engine, "watchlist", "added_price"))["numeric_precision"] == 24


async def test_0046_clears_expired_review_metrics_from_0029(alembic_db_engine: _MigrationDb) -> None:
    """D06-001: 0029→head 后 EXPIRED 行不带旧口径收益/标签，窗内与 T1_DONE 状态机不变。

    覆盖：
    - 窗内 COMPLETED → 0030/0032 重置为 PENDING 且八列全 NULL（不得被 0046 误伤）；
    - 窗外 COMPLETED → EXPIRED，0046 清空 t1/t5/alpha/index_pct/prediction_result，
      研究记录（ai_score/ai_reason）保留；
    - T1_DONE（含旧基点 t1_pct）→ PENDING 且 t1_pct 清空（0032 既有语义）；
    - 已是 EXPIRED 且带旧收益 → 0046 清空；
    - UNTRADABLE 不动；
    - 最新 EXPIRED + 更旧 COMPLETED 同键：统计掩码而非「先过滤状态再 DISTINCT ON」
      （否则更旧 COMPLETED 复活被计入 N/均值/胜负）。
    """
    from data.constants import (
        REVIEW_STATUS_COMPLETED,
        REVIEW_STATUS_EXPIRED,
        REVIEW_STATUS_PENDING,
        REVIEW_STATUS_T1_DONE,
        REVIEW_STATUS_UNTRADABLE,
    )
    from data.persistence.daos.screener_dao import ScreenerDao

    db = alembic_db_engine
    await _upgrade(db, "0029")
    assert await _current_version(db.engine) == "0029"

    today = await _scalar(db.engine, "SELECT CURRENT_DATE")
    assert isinstance(today, date)
    # 连续 150 个自然日全部记为交易日：0030/0032 的 OFFSET 59 锚点 = today-59，
    # 故 today-100 超窗（EXPIRED）、today-10 窗内（重置/回填通道）。
    for offset in range(150):
        await _execute(
            db.engine,
            "INSERT INTO trade_cal (cal_date, exchange, is_open) VALUES (:d, 'SSE', 1)",
            {"d": today - timedelta(days=offset)},
        )

    async def _insert(
        run_id: str,
        trade_date: date,
        review_status: str,
        *,
        strategy: str,
        ts_code: str,
        t1_pct: float | None = None,
        t5_pct: float | None = None,
        alpha: float | None = None,
        index_pct: float | None = None,
        prediction_result: str | None = None,
        ai_score: float | None = None,
        ai_reason: str | None = None,
    ) -> None:
        await _execute(
            db.engine,
            "INSERT INTO screening_history (run_id, trade_date, strategy_name, ts_code, "
            "review_status, t1_pct, t5_pct, alpha, index_pct, prediction_result, ai_score, ai_reason) "
            "VALUES (:run_id, :trade_date, :strategy_name, :ts_code, :review_status, :t1_pct, "
            ":t5_pct, :alpha, :index_pct, :prediction_result, :ai_score, :ai_reason)",
            {
                "run_id": run_id,
                "trade_date": trade_date,
                "strategy_name": strategy,
                "ts_code": ts_code,
                "review_status": review_status,
                "t1_pct": t1_pct,
                "t5_pct": t5_pct,
                "alpha": alpha,
                "index_pct": index_pct,
                "prediction_result": prediction_result,
                "ai_score": ai_score,
                "ai_reason": ai_reason,
            },
        )

    async def _row(run_id: str) -> dict[str, Any]:
        async with db.engine.connect() as conn:
            result = (
                (
                    await conn.execute(
                        text(
                            "SELECT review_status, t1_pct, t5_pct, alpha, index_pct, "
                            "prediction_result, ai_score, ai_reason "
                            "FROM screening_history WHERE run_id = :run_id"
                        ),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .first()
            )
        assert result is not None, f"缺少测试行: {run_id}"
        return dict(result)

    await _insert(
        "d06_old_comp",
        today - timedelta(days=100),
        REVIEW_STATUS_COMPLETED,
        strategy="d06_mig",
        ts_code="000001.SZ",
        t1_pct=0.01,
        t5_pct=0.06,
        alpha=0.05,
        index_pct=0.01,
        prediction_result="WIN",
        ai_score=80.0,
        ai_reason="keep-research-record",
    )
    await _insert(
        "d06_win_comp",
        today - timedelta(days=10),
        REVIEW_STATUS_COMPLETED,
        strategy="d06_mig",
        ts_code="000002.SZ",
        t1_pct=0.02,
        t5_pct=0.08,
        alpha=0.06,
        index_pct=0.02,
        prediction_result="WIN",
    )
    await _insert(
        "d06_old_t1_done",
        today - timedelta(days=100),
        REVIEW_STATUS_T1_DONE,
        strategy="d06_mig",
        ts_code="000003.SZ",
        t1_pct=0.03,
    )
    await _insert(
        "d06_leg_exp",
        today - timedelta(days=100),
        REVIEW_STATUS_EXPIRED,
        strategy="d06_mig",
        ts_code="000004.SZ",
        t1_pct=0.04,
        t5_pct=0.09,
        alpha=0.07,
        index_pct=0.02,
        prediction_result="LOSS",
    )
    await _insert(
        "d06_untrad",
        today - timedelta(days=10),
        REVIEW_STATUS_UNTRADABLE,
        strategy="d06_mig",
        ts_code="000005.SZ",
    )

    await _upgrade(db, "head")
    assert await _current_version(db.engine) == await DatabaseMigrator._get_head_revision()

    # 窗外 COMPLETED → EXPIRED；0046 清空不可信旧收益/标签，研究记录保留。
    old = await _row("d06_old_comp")
    assert old["review_status"] == REVIEW_STATUS_EXPIRED
    assert (old["t1_pct"], old["t5_pct"], old["alpha"], old["index_pct"], old["prediction_result"]) == (
        None,
        None,
        None,
        None,
        None,
    )
    assert float(old["ai_score"]) == 80.0
    assert old["ai_reason"] == "keep-research-record"

    # 窗内 COMPLETED → PENDING 且八列全空（0030/0032 语义，不被 0046 误伤）。
    window = await _row("d06_win_comp")
    assert window["review_status"] == REVIEW_STATUS_PENDING
    assert (window["t1_pct"], window["t5_pct"], window["alpha"], window["prediction_result"]) == (
        None,
        None,
        None,
        None,
    )

    # T1_DONE + 旧基点 t1_pct → PENDING 且清空（0032 既有语义保持不变）。
    t1_done = await _row("d06_old_t1_done")
    assert t1_done["review_status"] == REVIEW_STATUS_PENDING
    assert t1_done["t1_pct"] is None

    # 已是 EXPIRED 的存量脏行同样被 0046 清空。
    legacy = await _row("d06_leg_exp")
    assert legacy["review_status"] == REVIEW_STATUS_EXPIRED
    assert (legacy["t1_pct"], legacy["t5_pct"], legacy["alpha"], legacy["prediction_result"]) == (
        None,
        None,
        None,
        None,
    )

    # UNTRADABLE 终态不被数据修正迁移改写。
    assert (await _row("d06_untrad"))["review_status"] == REVIEW_STATUS_UNTRADABLE

    # DAO 统计：同键「最新 EXPIRED + 更旧 COMPLETED」——掩码后旧 run 不得复活。
    recent = today - timedelta(days=5)
    await _insert(
        "d06_old_run",
        recent,
        REVIEW_STATUS_COMPLETED,
        strategy="d06_dao",
        ts_code="000001.SZ",
        t1_pct=0.01,
        t5_pct=0.10,
        alpha=0.10,
        prediction_result="WIN",
        ai_score=70.0,
    )
    await _insert(
        "d06_late_exp",
        recent,
        REVIEW_STATUS_EXPIRED,
        strategy="d06_dao",
        ts_code="000001.SZ",
        t1_pct=0.05,
        t5_pct=0.50,
        alpha=0.50,
        prediction_result="WIN",
        ai_score=70.0,
    )
    # 同组另一只股票为正常 COMPLETED：掩码不应把有效样本一并剔除。
    await _insert(
        "d06_valid",
        recent,
        REVIEW_STATUS_COMPLETED,
        strategy="d06_dao",
        ts_code="000002.SZ",
        t1_pct=0.02,
        t5_pct=0.02,
        alpha=0.02,
        prediction_result="WIN",
        ai_score=70.0,
    )

    dao = ScreenerDao(db.engine)
    stats = await dao.get_strategy_review_stats()
    dao_rows = stats[stats["strategy_name"] == "d06_dao"]
    assert len(dao_rows) == 1
    row = dao_rows.iloc[0]
    # 最近快照为 EXPIRED 的键仍计入当日入选数（组合规模），但不贡献任何收益/胜负；
    # 若错误地「先过滤 COMPLETED 再 DISTINCT ON」，更旧 run 复活会使 alpha_n=2、
    # alpha_mean=(0.10+0.02)/2 且 win_cnt=2。
    assert int(row["daily_cnt"]) == 2
    assert int(row["alpha_n"]) == 1
    assert float(row["alpha_mean"]) == pytest.approx(0.02)
    assert int(row["t1_n"]) == 1
    assert int(row["win_cnt"]) == 1
    assert int(row["loss_cnt"]) == 0

    attribution = await dao.get_ai_attribution_stats()
    attr_rows = attribution[attribution["strategy_name"] == "d06_dao"]
    assert len(attr_rows) == 1
    assert int(attr_rows.iloc[0]["alpha_n"]) == 1
    assert int(attr_rows.iloc[0]["win_cnt"]) == 1
