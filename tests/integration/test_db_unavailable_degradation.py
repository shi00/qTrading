"""集成测试：DB 不可用降级（Phase 6 Task 6.3）。

覆盖 3 个场景：
1. 同步过程 DB 断开 → EngineDisposedError 传播 + SyncResult.status == "failed"
2. DB 不可用时 UI 读取缓存 → 降级提示展示 + 缓存命中
3. DB 恢复后重同步 → 数据一致性

R5 守卫：EngineDisposedError 必须传播（不被吞没）。
"""

import asyncio
import logging
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text

from data.cache.cache_manager import CacheManager
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.base import SyncResult, SyncStatus
from tests._helpers import create_test_engine
from tests.integration.conftest import TEST_DB_URL
from tests.integration.test_infra_base import TestDatabaseBase

pytestmark = pytest.mark.integration

logger = logging.getLogger(__name__)

_TODAY = date.today()
_RECENT = _TODAY - timedelta(days=1)


def _make_stock_basic_df(ts_code: str = "000001.SZ") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [ts_code],
            "symbol": ["000001"],
            "name": ["PingAn"],
            "area": ["Shenzhen"],
            "industry": ["Bank"],
            "market": ["Main"],
            "list_date": ["19910403"],
        },
    )


def _make_daily_quotes_df(ts_code: str = "000001.SZ", trade_date: date | None = None) -> pd.DataFrame:
    td = (trade_date or _RECENT).strftime("%Y%m%d")
    return pd.DataFrame(
        {
            "ts_code": [ts_code],
            "trade_date": [td],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "pre_close": [10.0],
            "change": [0.5],
            "pct_chg": [5.0],
            "vol": [1000],
            "amount": [10000],
            "adj_factor": [1.0],
        },
    )


async def _sync_wrapper_save_daily_quotes(cache: CacheManager, df: pd.DataFrame) -> SyncResult:
    """模拟 sync strategy 在 EngineDisposedError 时的降级行为。

    镜像 ``data/sync/historical.py`` 中 ``except EngineDisposedError`` 块的处理：
    设置 ``result.status="failed"`` + 记录错误 + ``raise`` 传播。
    """
    result = SyncResult()
    try:
        await cache.save_daily_quotes(df)
        result.status = SyncStatus.SUCCESS
    except asyncio.CancelledError:
        result.status = SyncStatus.CANCELLED
        raise
    except EngineDisposedError as e:
        logger.warning("[TestSync] Engine disposed during sync: %s", e)
        result.status = SyncStatus.FAILED
        result.errors.append("Engine disposed during sync")
        raise
    return result


class TestSyncDBDisconnectDegradation(TestDatabaseBase):
    """场景 1: 同步过程中 DB 断开 → EngineDisposedError 传播 + SyncResult.status==failed。"""

    async def test_save_daily_quotes_raises_when_disposed(self):
        """DB disposed 时 save_daily_quotes 抛 EngineDisposedError（R5 守卫）。"""
        # 模拟 disposed 中间态：_disposed=True 但 engine 引用仍存在
        # (CacheManager.close() 会同时置 engine=None，此处仅模拟 disposed 标志)
        self.cache._disposed = True
        df = _make_daily_quotes_df()
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await self.cache.save_daily_quotes(df)

    async def test_save_stock_basic_raises_when_disposed(self):
        """DB disposed 时 save_stock_basic 抛 EngineDisposedError（覆盖另一写路径）。"""
        self.cache._disposed = True
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await self.cache.save_stock_basic(_make_stock_basic_df())

    async def test_sync_wrapper_reraises_engine_disposed(self, caplog):
        """sync wrapper 在 EngineDisposedError 时记录日志并 raise（R5 传播）。

        镜像 ``data/sync/historical.py`` 中 ``except EngineDisposedError`` 块的 raise 行为：
        EngineDisposedError 必须传播到调用方，不被吞没。
        """
        self.cache._disposed = True

        with caplog.at_level(logging.WARNING, logger=__name__):
            with pytest.raises(EngineDisposedError):
                await _sync_wrapper_save_daily_quotes(self.cache, _make_daily_quotes_df())

        # 验证 wrapper 执行了 except 块（日志记录了降级）
        assert "Engine disposed during sync" in caplog.text

    async def test_sync_result_failed_status_propagates_through_merge(self):
        """SyncResult.merge 应将子任务的 failed 状态正确传播到主 result。

        验证 ``data/sync/base.py`` SyncResult.merge 的真实合并逻辑：
        - success + failed → partial（主任务由成功降级为部分失败，传播生效）
        - failed + failed → failed（双重失败保持 failed，强化传播语义）
        """
        self.cache._disposed = True

        # 触发 EngineDisposedError，构造子任务 failed result
        sub_result = SyncResult()
        try:
            await self.cache.save_daily_quotes(_make_daily_quotes_df())
        except EngineDisposedError:
            sub_result.status = SyncStatus.FAILED
            sub_result.errors.append("Engine disposed during sync")

        # 路径 1: 主任务 success + 子任务 failed → partial（failed 传播到主 result）
        main_success = SyncResult()
        main_success.merge(sub_result)
        assert main_success.status == SyncStatus.PARTIAL
        assert "Engine disposed during sync" in main_success.errors

        # 路径 2: 主任务 failed + 子任务 failed → failed（双重失败保持 failed）
        main_failed = SyncResult()
        main_failed.status = SyncStatus.FAILED
        main_failed.merge(sub_result)
        assert main_failed.status == SyncStatus.FAILED
        assert SyncStatus.FAILED == "failed"


class TestDBUnavailableReadCacheDegradation(TestDatabaseBase):
    """场景 2: DB 不可用时 UI 读取缓存 → 降级提示展示 + 缓存命中。"""

    async def test_get_stock_basic_raises_when_disposed(self):
        """DB disposed 时读操作抛 EngineDisposedError（R5 守卫）。"""
        await self.cache.save_stock_basic(_make_stock_basic_df())
        # 模拟 DB 断开
        self.cache._disposed = True
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await self.cache.get_stock_basic()

    async def test_get_screening_data_raises_when_disposed(self):
        """DB disposed 时 get_screening_data 抛 EngineDisposedError（覆盖 screening 读路径）。"""
        await self.cache.save_stock_basic(_make_stock_basic_df())
        await self.cache.save_daily_quotes(_make_daily_quotes_df())
        self.cache._disposed = True
        with pytest.raises(EngineDisposedError):
            await self.cache.get_screening_data(trade_date=_RECENT.strftime("%Y%m%d"))

    async def test_ui_degradation_wrapper_catches_and_returns_flag(self, caplog):
        """UI service 层 catch EngineDisposedError → 返回降级标志 + 空数据。

        模拟 UI/service 层的降级处理：
        - catch EngineDisposedError
        - 记录降级日志
        - 返回空 DataFrame + degraded=True 标志
        """
        await self.cache.save_stock_basic(_make_stock_basic_df())
        self.cache._disposed = True

        async def _ui_get_stock_basic_with_degradation() -> tuple[pd.DataFrame, bool]:
            try:
                df = await self.cache.get_stock_basic()
                return df, False
            except EngineDisposedError as e:
                logger.warning("[UI] DB 不可用，返回降级提示: %s", e)
                return pd.DataFrame(), True

        with caplog.at_level(logging.WARNING, logger=__name__):
            df, degraded = await _ui_get_stock_basic_with_degradation()

        assert degraded is True
        assert df.empty
        assert "DB 不可用" in caplog.text

    async def test_data_remains_in_db_after_disposed(self):
        """CacheManager disposed 后，DB 数据仍在，可通过独立 engine 读到（缓存命中）。

        场景：CacheManager 单例 _disposed=True 模拟应用层 DB 不可用，
        但 PostgreSQL 数据库本身未停止，数据仍在。通过独立 engine 验证。
        """
        # 通过 CacheManager 写入数据
        await self.cache.save_stock_basic(_make_stock_basic_df(ts_code="600519.SH"))
        # 模拟 DB 不可用
        self.cache._disposed = True

        # CacheManager 读会抛 EngineDisposedError
        with pytest.raises(EngineDisposedError):
            await self.cache.get_stock_basic()

        # 独立 engine（同一 DB）可读到数据 — 缓存命中
        independent_engine = create_test_engine(TEST_DB_URL, echo=False)
        try:
            async with independent_engine.connect() as conn:
                result = await conn.execute(text("SELECT ts_code, name FROM stock_basic WHERE ts_code='600519.SH'"))
                rows = result.fetchall()
        finally:
            await independent_engine.dispose()

        assert len(rows) == 1
        assert rows[0][0] == "600519.SH"
        assert rows[0][1] == "PingAn"


class TestDBRecoveryResyncConsistency(TestDatabaseBase):
    """场景 3: DB 恢复后重同步 → 数据一致性。

    模拟流程：
    1. 写入数据 → 记录快照
    2. close() 释放引擎（DB 不可用）
    3. init_db(force=True) 重建引擎（DB 恢复）
    4. 读数据 → 验证与快照一致
    5. 重新写入 → 验证可继续工作
    """

    async def test_init_db_force_recreates_engine_after_close(self):
        """close() 后 init_db(force=True) 重建 engine，_disposed 复位为 False。"""
        # 初始状态：engine 已创建
        assert self.cache.engine is not None
        assert self.cache._disposed is False

        # close() 释放引擎
        await self.cache.close()
        assert self.cache._disposed is True
        assert self.cache.engine is None

        # init_db(force=True) 重建引擎
        await self.cache.init_db(force=True, auto_migrate=True)

        # 验证引擎已重建
        assert self.cache.engine is not None
        assert self.cache._disposed is False

    async def test_data_consistent_after_reinit(self):
        """重建引擎后，DB 中的数据仍可读到且一致。"""
        # 写入数据
        await self.cache.save_stock_basic(_make_stock_basic_df(ts_code="600519.SH"))
        df_before = await self.cache.get_stock_basic()
        ts_codes_before = set(df_before["ts_code"].tolist())

        # close → init_db(force=True) 重建
        await self.cache.close()
        await self.cache.init_db(force=True, auto_migrate=True)

        # 重建后读数据，验证一致性
        df_after = await self.cache.get_stock_basic()
        ts_codes_after = set(df_after["ts_code"].tolist())

        assert ts_codes_before == ts_codes_after
        assert "600519.SH" in ts_codes_after

    async def test_save_after_reinit_succeeds(self):
        """重建引擎后，新数据可正常写入（DB 已恢复）。"""
        # 初始写入
        await self.cache.save_stock_basic(_make_stock_basic_df(ts_code="000001.SZ"))

        # close → init_db(force=True) 重建
        await self.cache.close()
        await self.cache.init_db(force=True, auto_migrate=True)

        # 重建后写入新数据
        df_new = _make_stock_basic_df(ts_code="600519.SH")
        await self.cache.save_stock_basic(df_new)

        # 验证新数据可读
        df = await self.cache.get_stock_basic()
        ts_codes = set(df["ts_code"].tolist())
        assert "000001.SZ" in ts_codes
        assert "600519.SH" in ts_codes
