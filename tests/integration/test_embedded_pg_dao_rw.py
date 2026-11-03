"""真实 sidecar + 真实 PG 的 DAO 读写集成测试。

覆盖 spec §3.4：embedded 模式下 CacheManager + DAO 读写端到端验证。

验证内容：
- CacheManager 用 embedded URL 初始化后 engine 可连
- StockDao.save_stock_basic / get_stock_basic 读写正确
- QuoteDao.save_daily_quotes 批量 upsert 正常
- ``_save_upsert`` 的 ON CONFLICT DO UPDATE 语义在 embedded PG 下正常
- 事务异常时数据回滚（防止数据丢失的错误处理）

依赖：
- ``real_embedded_pg`` fixture（session-scoped 真实 PG 实例）
- ``embedded_pg_with_schema`` fixture（session-scoped schema + CacheManager 初始化）

标记：
- ``pytest.mark.integration``
- ``pytest.mark.embedded_real``

loop_scope：
- 所有 async 测试用 ``loop_scope="session"``（对齐 ``real_embedded_pg`` session fixture，
  避免跨 loop ``Future attached to a different loop`` 错误，见 project_memory 教训）

隔离策略：
- session fixture 首次 setup 时 ``DROP SCHEMA public CASCADE`` + ``CREATE SCHEMA public``
  + ``alembic upgrade head`` + ``CacheManager.init_db``，确保从干净状态开始
- function fixture 每个测试前后清理相关表数据，保证测试间隔离
- teardown 关闭 CacheManager + reset_singleton（R7 单例隔离）
"""

# pyright: reportPrivateUsage=false
# 测试需访问 CacheManager / DAO 内部状态验证

from __future__ import annotations

import asyncio
import datetime
import typing
from contextlib import ExitStack

import pandas as pd
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import exc as sa_exc
from sqlalchemy import func, insert, select, text

import data.persistence.daos.base_dao as base_dao_module
from data.cache.cache_manager import CacheManager
from data.persistence.app_state_service import get_app_state, set_app_state_max
from data.persistence.db_url_override import override_db_url
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import (
    Base,
    DailyQuotes,
    MoneyflowDaily,
    StockBasic,
    SyncEmptyDay,
    TradeCal,
    get_model_columns,
    get_model_pk_columns,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


async def _reset_schema(url: str) -> None:
    """重置 public schema：DROP CASCADE + CREATE，清除所有表和 alembic_version 表。"""
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def embedded_pg_with_schema(real_embedded_pg: ConnectionInfo):
    """初始化 embedded PG schema + CacheManager 单例，session 期间共享。

    setup:
    1. ``_reset_schema`` 清除可能残留的表（避免与其他 embedded_real 测试文件互相干扰）
    2. ``alembic upgrade head`` 创建所有业务表
    3. ``override_db_url(info.url)`` + ``CacheManager.init_db`` 初始化引擎与 DAO

    teardown:
    1. ``CacheManager.close`` 释放连接池
    2. ``CacheManager._reset_singleton`` 清除单例（R7 测试隔离）

    注意：
    - ``auto_migrate=True`` 幂等：schema 已 upgrade head，``DatabaseMigrator.init_db``
      内部检查到 schema 与迁移头一致时不会重复迁移
    - ``override_db_url`` 持续整个 session，保证 ``EngineManager.get_connection_string``
      （review01-A4：原 CacheManager._get_connection_string 已移入 EngineManager）
      返回 embedded URL（init_db 内部 ``_create_engine`` 时读取）
    """
    info = real_embedded_pg

    # 1. reset schema（确保从干净状态开始）
    await _reset_schema(info.url)

    # 2. alembic upgrade head
    cfg = make_alembic_cfg(info.url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    # 3. override URL + init CacheManager
    with ExitStack() as url_stack:
        url_stack.enter_context(override_db_url(info.url))
        CacheManager._reset_singleton()
        cache = CacheManager()
        await cache.init_db(auto_migrate=True)
        yield info
        # teardown
        try:
            await cache.close()
        except asyncio.CancelledError:
            raise  # R2: 不吞 CancelledError
        CacheManager._reset_singleton()


@pytest_asyncio.fixture(loop_scope="session")
async def clean_embedded_pg_tables(embedded_pg_with_schema: ConnectionInfo):
    """每个测试前后清理相关表数据，保证测试间隔离。

    清理表：``daily_quotes``, ``stock_basic`` 及 D05-T02 事务/空日用例涉及的
    ``trade_cal``, ``sync_empty_days``, ``moneyflow_daily``, ``app_state``。
    使用 ``DELETE FROM`` 而非 ``TRUNCATE``，避免在事务外触发隐式 commit。
    """
    cache = CacheManager()
    tables = ["daily_quotes", "stock_basic", "trade_cal", "sync_empty_days", "moneyflow_daily", "app_state"]

    async def _cleanup() -> None:
        assert cache.engine is not None, "CacheManager.engine 未初始化"
        async with cache.engine.begin() as conn:
            for table in tables:
                await conn.execute(Base.metadata.tables[table].delete())

    await _cleanup()
    yield
    await _cleanup()


class TestEmbeddedPgDaoReadWrite:
    """embedded PG 模式下 CacheManager + DAO 读写端到端验证。

    所有测试复用 ``embedded_pg_with_schema`` session fixture（同一 CacheManager 实例），
    通过 ``clean_embedded_pg_tables`` function fixture 保证数据隔离。
    """

    pytestmark = pytest.mark.usefixtures("clean_embedded_pg_tables")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cache_manager_engine_points_to_embedded_pg(
        self,
        embedded_pg_with_schema: ConnectionInfo,
    ) -> None:
        """``CacheManager.engine`` 已初始化且可执行 ``SELECT 1``。"""
        info = embedded_pg_with_schema
        cache = CacheManager()
        assert cache.engine is not None, "CacheManager.engine 未初始化"
        # engine URL 应指向 embedded PG（端口与 info.port 一致）
        assert str(cache.engine.url).startswith("postgresql+asyncpg://")
        assert cache.engine.url.port == info.port
        # 连接验证
        async with cache.engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stock_dao_save_and_read(self, embedded_pg_with_schema: ConnectionInfo) -> None:
        """``StockDao.save_stock_basic`` 写入 2 条，``get_stock_basic`` 读回验证字段。"""
        cache = CacheManager()
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "area": "深圳",
                    "industry": "银行",
                    "market": "主板",
                    "list_date": datetime.date(1991, 4, 3),
                    "list_status": "L",
                },
                {
                    "ts_code": "600000.SH",
                    "symbol": "600000",
                    "name": "浦发银行",
                    "area": "上海",
                    "industry": "银行",
                    "market": "主板",
                    "list_date": datetime.date(1999, 11, 10),
                    "list_status": "L",
                },
            ]
        )
        written = await cache.stock_dao.save_stock_basic(df)
        assert written == 2, f"期望写入 2 条，实际 {written}"

        read_df = await cache.stock_dao.get_stock_basic()
        assert read_df is not None
        assert len(read_df) == 2, f"期望读回 2 条，实际 {len(read_df)}"
        assert set(read_df["ts_code"]) == {"000001.SZ", "600000.SH"}
        # 字段值验证
        row = read_df[read_df["ts_code"] == "000001.SZ"].iloc[0]
        assert row["name"] == "平安银行"
        assert row["list_status"] == "L"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_quote_dao_batch_upsert(self, embedded_pg_with_schema: ConnectionInfo) -> None:
        """``QuoteDao.save_daily_quotes`` 批量写入 3 条，``get_daily_quotes`` 读回验证。

        验证 ``_save_upsert`` 分块写入（``_UPSERT_CHUNK_SIZE=500``）在 embedded PG 下正常。
        """
        cache = CacheManager()
        # 先插入 stock_basic（daily_quotes 外键依赖，但实际无 FK 约束，仅为语义一致）
        stock_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "平安银行",
                    "area": "深圳",
                    "industry": "银行",
                    "market": "主板",
                    "list_date": datetime.date(1991, 4, 3),
                    "list_status": "L",
                }
            ]
        )
        await cache.stock_dao.save_stock_basic(stock_df)

        # 批量写入 3 条 daily_quotes
        quotes_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": datetime.date(2026, 7, 21),
                    "open": 10.5,
                    "high": 11.0,
                    "low": 10.3,
                    "close": 10.8,
                    "pre_close": 10.4,
                    "change": 0.4,
                    "pct_chg": 3.85,
                    "vol": 100000.0,
                    "amount": 1080000.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": datetime.date(2026, 7, 22),
                    "open": 10.8,
                    "high": 11.2,
                    "low": 10.7,
                    "close": 11.1,
                    "pre_close": 10.8,
                    "change": 0.3,
                    "pct_chg": 2.78,
                    "vol": 120000.0,
                    "amount": 1332000.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "trade_date": datetime.date(2026, 7, 23),
                    "open": 11.1,
                    "high": 11.5,
                    "low": 11.0,
                    "close": 11.3,
                    "pre_close": 11.1,
                    "change": 0.2,
                    "pct_chg": 1.80,
                    "vol": 95000.0,
                    "amount": 1073500.0,
                },
            ]
        )
        written = await cache.quote_dao.save_daily_quotes(quotes_df)
        assert written == 3, f"期望写入 3 条，实际 {written}"

        # 读回验证
        read_df = await cache.quote_dao.get_daily_quotes(ts_code="000001.SZ")
        assert read_df is not None
        assert len(read_df) == 3, f"期望读回 3 条，实际 {len(read_df)}"
        # 按交易日排序验证趋势
        # DAT-10/11: 读取边界已归一化 NUMERIC → float64，float() 仅作显式类型声明
        read_df = read_df.sort_values("trade_date", ascending=True).reset_index(drop=True)
        assert float(read_df["close"].iloc[0]) == 10.8
        assert float(read_df["close"].iloc[-1]) == 11.3

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upsert_on_conflict_update(self, embedded_pg_with_schema: ConnectionInfo) -> None:
        """``_save_upsert`` 的 ON CONFLICT DO UPDATE 语义：重复主键更新而非报错。

        场景：
        1. 插入 stock_basic 1 条（name="原名称"）
        2. 用相同 ts_code 再次 upsert（name="新名称"）
        3. 读回验证 name 已更新，且总行数仍为 1（未重复插入）
        """
        cache = CacheManager()
        original_df = pd.DataFrame(
            [
                {
                    "ts_code": "000002.SZ",
                    "symbol": "000002",
                    "name": "原名称",
                    "area": "深圳",
                    "industry": "房地产",
                    "market": "主板",
                    "list_date": datetime.date(1991, 1, 29),
                    "list_status": "L",
                }
            ]
        )
        await cache.stock_dao.save_stock_basic(original_df)

        # 再次 upsert 相同 ts_code，name 变更
        updated_df = pd.DataFrame(
            [
                {
                    "ts_code": "000002.SZ",
                    "symbol": "000002",
                    "name": "新名称",
                    "area": "深圳",
                    "industry": "房地产",
                    "market": "主板",
                    "list_date": datetime.date(1991, 1, 29),
                    "list_status": "L",
                }
            ]
        )
        await cache.stock_dao.save_stock_basic(updated_df)

        read_df = await cache.stock_dao.get_stock_basic()
        assert read_df is not None
        assert len(read_df) == 1, f"期望 1 条（upsert 更新未重复），实际 {len(read_df)}"
        row = read_df.iloc[0]
        assert row["name"] == "新名称", f"期望 name='新名称'，实际 '{row['name']}'"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_transaction_rollback_on_error(self, embedded_pg_with_schema: ConnectionInfo) -> None:
        """事务异常时数据回滚：``engine.begin()`` 块内抛异常，已插入的数据应回滚。

        验证 embedded PG 的事务 ACID 特性，确保错误处理路径不残留脏数据
        （对齐 §3.2 强制要求：防止数据丢失的错误处理）。

        场景：
        1. 在 ``engine.begin()`` 事务内插入 1 条 stock_basic
        2. 抛出 ``RuntimeError`` 触发事务回滚
        3. 读回验证表为空（事务回滚，无残留）
        """
        cache = CacheManager()
        assert cache.engine is not None

        with pytest.raises(RuntimeError, match="intentional rollback"):
            async with cache.engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO stock_basic (ts_code, symbol, name, area, industry, market, "
                        "list_date, list_status) "
                        "VALUES ('000003.SZ', '000003', '应回滚', '深圳', '银行', '主板', "
                        "'1991-01-01', 'L')"
                    )
                )
                raise RuntimeError("intentional rollback")

        # 验证回滚：表应为空
        read_df = await cache.stock_dao.get_stock_basic()
        assert read_df is None or len(read_df) == 0, (
            f"事务回滚后表应为空，实际 {len(read_df) if read_df is not None else 0} 条"
        )


def _empty_day_frame_with_bad_date() -> pd.DataFrame:
    """5 行独立主键，第 3 行日期非法（coerce 后为 NULL）以便在第二块触发约束错误。"""
    return pd.DataFrame(
        [
            {"table_name": "moneyflow_daily", "trade_date": datetime.date(2024, 1, 1)},
            {"table_name": "moneyflow_daily", "trade_date": datetime.date(2024, 1, 2)},
            {"table_name": "moneyflow_daily", "trade_date": "not-a-date"},
            {"table_name": "moneyflow_daily", "trade_date": datetime.date(2024, 1, 4)},
            {"table_name": "moneyflow_daily", "trade_date": datetime.date(2024, 1, 5)},
        ]
    )


async def _count_rows(engine: typing.Any, table: typing.Any) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(select(func.count()).select_from(table))
        return int(result.scalar_one())


class TestD05TransactionRecoveryContracts:
    """D05-T02 真库证据：回滚 / 并发单调写 / 主键幂等 / 部分批写 / 旧空日纠正。

    仅在本机存在 embedded PG sidecar 时执行；缺失时由 ``real_embedded_pg``
    fixture 统一 ``pytest.skip``（不伪造通过）。
    """

    pytestmark = pytest.mark.usefixtures("clean_embedded_pg_tables")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_shared_conn_upsert_failure_rolls_back_whole_transaction(
        self,
        embedded_pg_with_schema: ConnectionInfo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同连接多块写入中第二块失败：整个事务回滚，第一块也不得残留。"""
        cache = CacheManager()
        assert cache.engine is not None
        dao = cache.sync_dao
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)

        with pytest.raises(sa_exc.DBAPIError) as exc_info:
            async with dao._guarded_begin() as conn:
                await dao._save_upsert(
                    _empty_day_frame_with_bad_date(),
                    "sync_empty_days",
                    get_model_columns(SyncEmptyDay),
                    get_model_pk_columns(SyncEmptyDay),
                    suppress_errors=True,
                    conn=conn,
                )

        assert "null value" in str(exc_info.value).lower()
        assert await _count_rows(cache.engine, SyncEmptyDay) == 0

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unshared_long_batch_partially_commits_completed_chunks(
        self,
        embedded_pg_with_schema: ConnectionInfo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超阈值批量（每块独立事务）中第二块失败：第一块已提交，后续块不写。"""
        cache = CacheManager()
        assert cache.engine is not None
        dao = cache.sync_dao
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)
        monkeypatch.setattr(base_dao_module, "_LONG_TX_ROW_THRESHOLD", 2)

        with pytest.raises(sa_exc.DBAPIError) as exc_info:
            await dao._save_upsert(
                _empty_day_frame_with_bad_date(),
                "sync_empty_days",
                get_model_columns(SyncEmptyDay),
                get_model_pk_columns(SyncEmptyDay),
            )

        assert "null value" in str(exc_info.value).lower()
        # 仅第一块（2 行）已提交，证明部分批写语义依赖 UPSERT 幂等重跑收敛
        assert await _count_rows(cache.engine, SyncEmptyDay) == 2
        async with cache.engine.connect() as conn:
            result = await conn.execute(select(SyncEmptyDay.trade_date).order_by(SyncEmptyDay.trade_date))
            assert list(result.scalars()) == [datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_set_app_state_max_concurrent_writes_keep_max_value(
        self,
        embedded_pg_with_schema: ConnectionInfo,
    ) -> None:
        """乱序并发写入后最终值仍为最大值；随后写较小值不降低高水位。"""
        cache = CacheManager()
        assert cache.engine is not None
        key = "sync_attempted_upto:d05_t02_monotonic"
        values = ["20240101", "20250601", "20240301", "20250901", "20231231", "20250501"]

        await asyncio.gather(*(set_app_state_max(cache.engine, key, value) for value in values))

        assert await get_app_state(cache.engine, key) == "20250901"
        await set_app_state_max(cache.engine, key, "20240101")
        assert await get_app_state(cache.engine, key) == "20250901"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_mark_empty_days_is_pk_idempotent(self, embedded_pg_with_schema: ConnectionInfo) -> None:
        """重复登记同一 (table, date)：不产生重复行，返回实际落库行数。"""
        cache = CacheManager()
        assert cache.engine is not None
        dao = cache.sync_dao
        dates = [datetime.date(2024, 5, 6), datetime.date(2024, 5, 7)]

        first = await dao.mark_empty_days("moneyflow_daily", dates)
        second = await dao.mark_empty_days("moneyflow_daily", dates + [dates[0]])

        assert first == 2, f"首次应落库 2 行，实际 {first}"
        assert second == 2, f"重复登记应返回去重后 2 行，实际 {second}"
        assert await _count_rows(cache.engine, SyncEmptyDay) == 2, "复合主键必须阻止重复行"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_empty_day_marker_does_not_mask_arriving_data(
        self,
        embedded_pg_with_schema: ConnectionInfo,
    ) -> None:
        """旧空日登记在真实数据到达后不再豁免，评分改按实际行数判定。"""
        cache = CacheManager()
        assert cache.engine is not None
        trade_date = datetime.date(2024, 1, 2)

        async with cache.engine.begin() as conn:
            await conn.execute(
                insert(StockBasic).values(
                    ts_code="000001.SZ",
                    symbol="000001",
                    name="平安银行",
                    list_date=datetime.date(2020, 1, 1),
                    list_status="L",
                )
            )
            await conn.execute(insert(TradeCal).values(cal_date=trade_date, exchange="SSE", is_open=1))
            await conn.execute(insert(DailyQuotes).values(ts_code="000001.SZ", trade_date=trade_date, close=10.0))

        await cache.sync_dao.mark_empty_days("moneyflow_daily", [trade_date])
        before = await cache.quote_dao.get_bulk_sync_quality_scores(
            trade_date, trade_date, tables=["daily_quotes", "moneyflow_daily"]
        )
        before_info = before[trade_date]["tables"]["moneyflow_daily"]
        assert before_info["count"] == 0
        assert before_info["exempt"] is True

        # 数据到达后旧登记仍在，但 count > 0 必须走真实评分而非豁免
        async with cache.engine.begin() as conn:
            await conn.execute(insert(MoneyflowDaily).values(ts_code="000001.SZ", trade_date=trade_date))
        after = await cache.quote_dao.get_bulk_sync_quality_scores(
            trade_date, trade_date, tables=["daily_quotes", "moneyflow_daily"]
        )
        after_info = after[trade_date]["tables"]["moneyflow_daily"]
        assert after_info["count"] == 1
        assert after_info.get("exempt") is not True
        assert after_info["passed"] is True
