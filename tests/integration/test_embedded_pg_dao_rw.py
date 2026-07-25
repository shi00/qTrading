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
from contextlib import ExitStack

import pandas as pd
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text

from data.cache.cache_manager import CacheManager
from data.persistence.db_url_override import override_db_url
from data.persistence.embedded_postgres.protocol import ConnectionInfo
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
    - ``override_db_url`` 持续整个 session，保证 ``CacheManager._get_connection_string``
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

    清理表：``daily_quotes``, ``stock_basic``（测试涉及的两张表）。
    使用 ``DELETE FROM`` 而非 ``TRUNCATE``，避免在事务外触发隐式 commit。
    """
    cache = CacheManager()
    tables = ["daily_quotes", "stock_basic"]

    async def _cleanup() -> None:
        assert cache.engine is not None, "CacheManager.engine 未初始化"
        async with cache.engine.begin() as conn:
            for table in tables:
                await conn.execute(text(f"DELETE FROM {table}"))

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
        # NOTE: asyncpg 从 PG NUMERIC 列读回 Decimal，与 float 比较时 10.8 因二进制不精确而
        # 不相等（Decimal('10.8000') == 10.8 → False），需 float() 转换后比较
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
