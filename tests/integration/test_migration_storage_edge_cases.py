"""D04-F06 迁移存量/存储边界集成测试（真实 Alembic upgrade/downgrade）。

覆盖矩阵（与 PR body 一致）：
- 0016 代表旧库携数据升级到 head：结构、版本、行数与备注值守恒；
- 0022 upgrade 清理 NULL ann_date 行（不可逆）、主键扩展后保留同一报告期多版本行；
- 0018 非法 stock_basic.ts_code 使 CHECK 建立失败时不删行、不部分提交，修正后重试成功；
- screening_thinking.history_id 由 integer 升 bigint、回退窄化到 int32 的边界（> 2^31-1 失败关闭）；
- 0031 携数据 upgrade/downgrade/re-upgrade：结构、版本、行数与前映像（逻辑备份）校验。

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
from datetime import date, datetime
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError
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
    engine = create_test_engine(async_url)
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
    """逻辑备份校验：升级/回退/再升级后各表行与关键值必须与迁移前快照一致。"""
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

    # 旧 schema 的 int32 列拒绝超界值；异常类型随驱动可能是 OverflowError/DataError，
    # 因此用 exc_info 校验语义并断言失败后无残留行（回滚）。
    with pytest.raises(Exception) as exc_info:
        await _execute(
            db.engine,
            "INSERT INTO screening_thinking (history_id) VALUES (:history_id)",
            {"history_id": large_id},
        )
    assert "range" in str(exc_info.value).lower() or "overflow" in str(exc_info.value).lower()
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

    with pytest.raises(DataError, match="out of range"):
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

    backup = {
        "watchlist": await _snapshot(
            db.engine,
            "SELECT ts_code, note, added_trade_date, added_price FROM watchlist ORDER BY ts_code",
        ),
        "task_history": await _snapshot(db.engine, "SELECT id, name, task_type, status FROM task_history ORDER BY id"),
        "financial_reports": await _snapshot(
            db.engine,
            "SELECT ts_code, end_date, ann_date, total_revenue FROM financial_reports "
            "ORDER BY ts_code, end_date, ann_date",
        ),
        "stock_name_history": await _snapshot(
            db.engine,
            "SELECT ts_code, start_date, name FROM stock_name_history ORDER BY ts_code, start_date",
        ),
        "news_risk_brief": await _snapshot(
            db.engine,
            "SELECT ts_code, input_hash, analysis_status FROM news_risk_brief ORDER BY ts_code, input_hash",
        ),
    }
    for table, rows in backup.items():
        assert rows, f"{table} 预置数据为空，快照校验将失去意义"

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
