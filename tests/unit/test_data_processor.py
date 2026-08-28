# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from core.i18n import Message
from data.data_processor import DataProcessor

pytestmark = pytest.mark.unit


def _make_dp():
    DataProcessor._reset_singleton()
    with (
        patch("data.data_processor.CacheManager"),
        patch("data.data_processor.TushareClient"),
        patch("data.data_processor.TradeCalendarService"),
        patch("data.data_processor.ConfigHandler") as mock_ch,
    ):
        mock_ch.get_token.return_value = "test_token"
        dp = DataProcessor()
        # LOG-1.1: initialize_system 起始新增 await cache.init_db()，测试默认 mock 为 awaitable
        dp.cache.init_db = AsyncMock()
    return dp


class TestDataProcessorRefreshToken:
    def test_refresh_with_new_token(self):
        dp = _make_dp()
        with patch("data.data_processor.TushareClient"):
            dp.refresh_token("new_token")
            assert dp._current_token == "new_token"

    def test_refresh_auto_detect(self):
        dp = _make_dp()
        with (
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch("data.data_processor.TushareClient"),
        ):
            mock_ch.get_token.return_value = "auto_token"
            dp.refresh_token()
            assert dp._current_token == "auto_token"


class TestDataProcessorCancel:
    @pytest.mark.asyncio
    async def test_get_cancel_event(self):
        dp = _make_dp()
        evt = dp._get_cancel_event()
        assert evt is not None

    @pytest.mark.asyncio
    async def test_is_cancelled(self):
        dp = _make_dp()
        dp.clear_cancel()
        assert not dp.is_cancelled()
        dp._get_cancel_event().set()
        assert dp.is_cancelled()

    @pytest.mark.asyncio
    async def test_clear_cancel(self):
        dp = _make_dp()
        dp._get_cancel_event().set()
        dp.clear_cancel()
        assert not dp.is_cancelled()

    @pytest.mark.asyncio
    async def test_request_cancel(self):
        dp = _make_dp()
        for s in dp.strategies.values():
            s.cancel = MagicMock()
        await dp.request_cancel()
        assert dp.is_cancelled()

    @pytest.mark.asyncio
    async def test_stop(self):
        dp = _make_dp()
        for s in dp.strategies.values():
            s.cancel = MagicMock()
        await dp.stop()
        assert dp.is_cancelled()

    @pytest.mark.asyncio
    async def test_stop_calls_strategy_cancel(self):
        dp = _make_dp()
        # Track cancel calls
        cancel_calls = {}
        for name, s in dp.strategies.items():
            s.cancel = MagicMock()
            cancel_calls[name] = s.cancel

        await dp.stop()

        # Verify all strategies were cancelled
        for _name, mock_cancel in cancel_calls.items():
            mock_cancel.assert_called_once()


class TestDataProcessorClose:
    @pytest.mark.asyncio
    async def test_close_with_cache(self):
        dp = _make_dp()
        dp.cache = MagicMock()
        dp.cache.close = AsyncMock()
        for s in dp.strategies.values():
            s.cancel = MagicMock()
        await dp.close()
        dp.cache.close.assert_called_once()


class TestDataProcessorSyncHistorical:
    @pytest.mark.asyncio
    async def test_sync_historical_data(self):
        dp = _make_dp()
        mock_result = MagicMock()
        mock_result.status = "completed"
        dp.strategies["historical"].run = AsyncMock(return_value=mock_result)
        result = await dp.sync_historical_data(days=100)
        assert result.status == "completed"


class TestDataProcessorSyncFinancial:
    @pytest.mark.asyncio
    async def test_sync_financial_reports(self):
        dp = _make_dp()
        mock_result = MagicMock()
        mock_result.added = 50
        dp.strategies["financial"].run = AsyncMock(return_value=mock_result)
        result = await dp.sync_financial_reports()
        assert result == 50

    @pytest.mark.asyncio
    async def test_sync_comprehensive_fundamentals(self):
        dp = _make_dp()
        mock_result = MagicMock()
        mock_result.status = "completed"
        dp.strategies["financial"].run = AsyncMock(return_value=mock_result)
        result = await dp.sync_comprehensive_fundamentals()
        assert result.status == "completed"


class TestDataProcessorSyncDailyMarket:
    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_with_date(self):
        dp = _make_dp()
        dp.strategies["historical"].sync_daily_market_snapshot = AsyncMock()
        dp.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        await dp.sync_daily_market_snapshot(trade_date="20240614")
        dp.strategies["historical"].sync_daily_market_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_no_date(self):
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value="20240614")
        dp.strategies["historical"].sync_daily_market_snapshot = AsyncMock()
        dp.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        await dp.sync_daily_market_snapshot()
        dp.strategies["historical"].sync_daily_market_snapshot.assert_called_once_with("20240614", force=False)


class TestDataProcessorShouldSyncFinancials:
    @pytest.mark.asyncio
    async def test_force(self):
        dp = _make_dp()
        result, reason = await dp.should_sync_financials(force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_never_synced(self):
        dp = _make_dp()
        dp.cache.sync_dao.get_sync_status = AsyncMock(return_value=None)
        result, reason = await dp.should_sync_financials()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_last_sync_date(self):
        dp = _make_dp()
        dp.cache.sync_dao.get_sync_status = AsyncMock(return_value={"last_sync_date": None})
        result, reason = await dp.should_sync_financials()
        assert result is True

    @pytest.mark.asyncio
    async def test_recent_sync(self):
        dp = _make_dp()
        recent = datetime.datetime(2024, 6, 15, 10, 30, 0) - datetime.timedelta(days=5)
        dp.cache.sync_dao.get_sync_status = AsyncMock(return_value={"last_sync_date": recent.strftime("%Y-%m-%d")})
        with patch(
            "data.data_processor.get_now",
            return_value=datetime.datetime(2024, 6, 15, 10, 30, 0),
        ):
            result, reason = await dp.should_sync_financials()
            assert result is False

    @pytest.mark.asyncio
    async def test_old_sync(self):
        dp = _make_dp()
        old = datetime.datetime(2024, 6, 15, 10, 30, 0) - datetime.timedelta(days=35)
        dp.cache.sync_dao.get_sync_status = AsyncMock(return_value={"last_sync_date": old.strftime("%Y-%m-%d")})
        with patch(
            "data.data_processor.get_now",
            return_value=datetime.datetime(2024, 6, 15, 10, 30, 0),
        ):
            result, reason = await dp.should_sync_financials()
            assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_true(self):
        dp = _make_dp()
        dp.cache.sync_dao.get_sync_status = AsyncMock(side_effect=Exception("db error"))
        result, reason = await dp.should_sync_financials()
        assert result is True


class TestDataProcessorSyncStockBasic:
    @pytest.mark.asyncio
    async def test_cancelled(self):
        dp = _make_dp()
        dp._get_cancel_event().set()
        result = await dp.sync_stock_basic()
        assert result == 0

    @pytest.mark.asyncio
    async def test_already_syncing(self):
        dp = _make_dp()
        dp._is_syncing_basic = True
        result = await dp.sync_stock_basic()
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_api_result(self):
        dp = _make_dp()
        dp.api.get_stock_basic_all = AsyncMock(return_value=None)
        dp.clear_cancel()
        result = await dp.sync_stock_basic()
        assert result == 0

    @pytest.mark.asyncio
    async def test_successful_sync(self):
        dp = _make_dp()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "list_status": ["L", "D"],
            }
        )
        dp.api.get_stock_basic_all = AsyncMock(return_value=df)
        dp.cache.stock_dao.save_stock_basic = AsyncMock(return_value=2)
        dp.cache.sync_dao.update_sync_status = AsyncMock()
        dp.clear_cancel()
        with patch(
            "data.data_processor.get_now",
            return_value=datetime.datetime(2024, 6, 15, 10, 30, 0),
        ):
            result = await dp.sync_stock_basic()
            assert result == 2

    @pytest.mark.asyncio
    async def test_no_stocks_saved(self):
        dp = _make_dp()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "list_status": ["L"]})
        dp.api.get_stock_basic_all = AsyncMock(return_value=df)
        dp.cache.stock_dao.save_stock_basic = AsyncMock(return_value=0)
        dp.clear_cancel()
        with patch(
            "data.data_processor.get_now",
            return_value=datetime.datetime(2024, 6, 15, 10, 30, 0),
        ):
            result = await dp.sync_stock_basic()
            assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self):
        dp = _make_dp()
        dp.api.get_stock_basic_all = AsyncMock(side_effect=Exception("api error"))
        dp.clear_cancel()
        result = await dp.sync_stock_basic()
        assert result == 0


class TestDataProcessorErrorClassification:
    """FIND-R2-001: D2 修复 — sync_stock_basic/sync_concepts 错误三分类测试。"""

    @pytest.mark.asyncio
    async def test_sync_stock_basic_system_error_raises(self):
        """D2: system 级异常（如 MemoryError）raise 传播。"""
        dp = _make_dp()
        dp.clear_cancel()
        dp.api.get_stock_basic_all = AsyncMock(side_effect=MemoryError("oom"))
        with patch("data.data_processor.classify_severity", return_value="system"):
            with pytest.raises(MemoryError, match="oom"):
                await dp.sync_stock_basic()

    @pytest.mark.asyncio
    async def test_sync_stock_basic_recoverable_error_returns_zero(self):
        """D2: recoverable 级异常（如网络错误）降级返回 0。"""
        dp = _make_dp()
        dp.clear_cancel()
        dp.api.get_stock_basic_all = AsyncMock(side_effect=OSError("network timeout"))
        with patch("data.data_processor.classify_severity", return_value="recoverable"):
            result = await dp.sync_stock_basic()
        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_concepts_operational_error_returns_zero(self):
        """D2: operational 级异常（如数据格式错误）降级返回 0。"""
        dp = _make_dp()
        dp.clear_cancel()
        dp.api.get_concept_list = AsyncMock(side_effect=ValueError("bad format"))
        with patch("data.data_processor.classify_severity", return_value="operational"):
            result = await dp.sync_concepts()
        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_concepts_system_error_raises(self):
        """D2: system 级异常（如 MemoryError）raise 传播（覆盖 L720/L726）。"""
        dp = _make_dp()
        dp.clear_cancel()
        dp.api.get_concept_list = AsyncMock(side_effect=MemoryError("oom"))
        with patch("data.data_processor.classify_severity", return_value="system"):
            with pytest.raises(MemoryError, match="oom"):
                await dp.sync_concepts()


class TestDataProcessorSyncConcepts:
    @pytest.mark.asyncio
    async def test_cancelled(self):
        dp = _make_dp()
        dp._get_cancel_event().set()
        result = await dp.sync_concepts()
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_concept_list(self):
        dp = _make_dp()
        dp.api.get_concept_list = AsyncMock(return_value=None)
        dp.clear_cancel()
        result = await dp.sync_concepts()
        assert result == 0

    @pytest.mark.asyncio
    async def test_successful_sync(self):
        dp = _make_dp()
        df_c = pd.DataFrame({"code": ["TS1"]})
        dp.api.get_concept_list = AsyncMock(return_value=df_c)
        detail_df = pd.DataFrame(
            {
                "id": ["TS1"],
                "concept_name": ["Concept1"],
                "ts_code": ["000001.SZ"],
                "name": ["Stock1"],
            }
        )
        dp.api.get_concept_detail_by_id = AsyncMock(return_value=detail_df)
        dp.cache.stock_dao.overwrite_concepts = AsyncMock(return_value=1)
        dp.clear_cancel()
        # P3-SyncConcepts-Dual-RateLimit: fetch_one 不再使用 wait_for，
        # 改用纯 is_cancelled() check（O(1) flag 读取，无等待语义）。
        # 正常路径：API 调用 → is_cancelled() False → return result。
        result = await dp.sync_concepts()
        assert result == 1

    @pytest.mark.asyncio
    async def test_sync_concepts_cancel_event_set_before_fetch_one(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ②: cancel_event.set() 后 fetch_one
        在 API 调用前 is_cancelled() True → return None（fail fast，不消耗 semaphore）。"""
        dp = _make_dp()
        df_c = pd.DataFrame({"code": ["TS1"]})
        dp.api.get_concept_list = AsyncMock(return_value=df_c)
        dp.api.get_concept_detail_by_id = AsyncMock()
        dp.cache.stock_dao.overwrite_concepts = AsyncMock(return_value=0)
        # 在 fetch_one 进入前 set cancel_event
        dp._get_cancel_event().set()
        result = await dp.sync_concepts()
        # sync_concepts 在 fetch_one 返回 None 后，results 为 [None]，all_dfs 为空，
        # overwrite_concepts 被调用返回 0
        assert result == 0
        # API 不应被调用（fail fast 在 semaphore 前）
        dp.api.get_concept_detail_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_concepts_cancel_event_set_after_api_call(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ②: cancel_event.set() 在 API 调用后
        发生 → fetch_one 的 post-API is_cancelled() True → raise CancelledError。
        gather_return_exceptions_propagating_cancel 传播 → sync_concepts except 重新 raise。
        R2 红线契约：CancelledError 必须传播，不得吞没。"""
        dp = _make_dp()
        df_c = pd.DataFrame({"code": ["TS1"]})
        dp.api.get_concept_list = AsyncMock(return_value=df_c)
        detail_df = pd.DataFrame(
            {
                "id": ["TS1"],
                "concept_name": ["Concept1"],
                "ts_code": ["000001.SZ"],
                "name": ["Stock1"],
            }
        )

        # API 调用后立即 set cancel_event，模拟取消信号在 API 完成后到达
        async def _api_then_set_cancel(c):
            dp._get_cancel_event().set()
            return detail_df

        dp.api.get_concept_detail_by_id = AsyncMock(side_effect=_api_then_set_cancel)
        dp.cache.stock_dao.overwrite_concepts = AsyncMock(return_value=1)
        dp.clear_cancel()
        # R2 红线契约：CancelledError 必须传播。强化断言：cancel 后 cache.stock_dao.overwrite_concepts
        # 不应被调用（cancel 优先于保存），证明 CancelledError 在 gather 传播后立即 raise，
        # 未进入保存分支。
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线契约仅验证 CancelledError 类型传播即可，无有意义 message 可 match；后续 overwrite_concepts.assert_not_called 已强断言
            await dp.sync_concepts()
        dp.cache.stock_dao.overwrite_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_concepts_cancel_within_2s_during_blocked_api_call(self):
        """P3-SyncConcepts-Cancel-Response-2s DoD: get_concept_detail_by_id 阻塞
        （模拟 TokenBucket 限速 sleep / 网络等待）时，cancel_event.set() 后
        sync_concepts 应在约 2s 内 raise CancelledError。

        修复前 fetch_one 全靠 API await 前后的 is_cancelled() 轮询，阻塞期内
        无法中断，取消响应可能超 2s；_await_cancel_aware 以 0.25s 轮询打断阻塞
        await，保证 ≤2s 取消响应（project_memory 硬约束）。"""
        dp = _make_dp()
        df_c = pd.DataFrame({"code": ["TS1"]})
        dp.api.get_concept_list = AsyncMock(return_value=df_c)

        # 用信号同步取代固定 sleep，消除「task 尚未进入阻塞 await 即触发取消」
        # 的时序竞态（该竞态会让 fetch_one 预检短路 return，测试误报失败）
        _entered_block = asyncio.Event()

        # 阻塞的 API：永不返回，直到其所在 task 被取消
        async def _blocking_detail(c):
            _entered_block.set()
            await asyncio.Event().wait()

        dp.api.get_concept_detail_by_id = AsyncMock(side_effect=_blocking_detail)
        dp.cache.stock_dao.overwrite_concepts = AsyncMock()
        dp.clear_cancel()

        task = asyncio.create_task(dp.sync_concepts())
        await _entered_block.wait()  # 确保已进入阻塞的 API await
        await dp.request_cancel()  # 触发 cancel_event
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion weak_raises_only  # [reason: body 内无法插入 assert（期望异常会提前终止 body）；已在 with 后补强断言验证取消后未落库]
            await asyncio.wait_for(task, timeout=2.0)
        # 强断言：取消后 sync_concepts 未走到落库（overwrite_concepts 未被调用）
        dp.cache.stock_dao.overwrite_concepts.assert_not_called()
        assert dp.is_cancelled()

    @pytest.mark.asyncio
    async def test_sync_concepts_no_concept_delay_literal(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ①: CONCEPT_DELAY = 3.0 字面值赋值
        从 data_processor.py 移除（grep 验证）。注释中提及 CONCEPT_DELAY 是允许的
        （解释修复历史），但赋值语句 `CONCEPT_DELAY = ...` 必须不存在。"""
        import inspect

        from data.data_processor import DataProcessor

        source = inspect.getsource(DataProcessor.sync_concepts)
        # 移除注释行后再检查（# 开头的行）
        code_lines = [line for line in source.splitlines() if not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "CONCEPT_DELAY =" not in code_only, (
            "CONCEPT_DELAY = ... 赋值语句应从 sync_concepts 移除（P3-SyncConcepts-Dual-RateLimit DoD ①）"
        )
        # 验证 3.0 字面值不在代码行中
        assert "3.0" not in code_only, "sync_concepts 代码行不应残留 3.0 字面值（CONCEPT_DELAY=3.0 移除后）"

    @pytest.mark.asyncio
    async def test_sync_concepts_concurrency_is_five(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ④: CONCEPT_CONCURRENCY 上调到 5
        （与 TokenBucket capacity=max(5, ...) 对齐，让 TokenBucket 成为唯一限速源）。"""
        import inspect

        from data.data_processor import DataProcessor

        source = inspect.getsource(DataProcessor.sync_concepts)
        assert "CONCEPT_CONCURRENCY = 5" in source, "CONCEPT_CONCURRENCY 应为 5（与 TokenBucket capacity 对齐）"
        assert "CONCEPT_CONCURRENCY = 2" not in source, "CONCEPT_CONCURRENCY = 2 应被替换为 5"

    @pytest.mark.asyncio
    async def test_sync_concepts_no_wait_for_in_fetch_one(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ③: fetch_one 正常路径无额外 sleep/wait_for。
        对抗性 review 发现：wait_for(timeout=2.0) 在 event 未 set 时会强制等待 2s，
        等于 per-request 2s 限速，与"TokenBucket 唯一限速真相源"冲突。
        验证 fetch_one 内不再调用 asyncio.wait_for（grep 源码）。"""
        import inspect

        from data.data_processor import DataProcessor

        source = inspect.getsource(DataProcessor.sync_concepts)
        # 移除注释行后再检查（# 开头的行）
        code_lines = [line for line in source.splitlines() if not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "wait_for" not in code_only, (
            "fetch_one 不应使用 asyncio.wait_for（对抗性 review：event 未 set 时强制等待 N 秒，"
            "等于 per-request 限速，与 TokenBucket 唯一限速真相源冲突）"
        )

    @pytest.mark.asyncio
    async def test_sync_concepts_fetch_one_no_extra_sleep_on_normal_path(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ③ 行为验证: fetch_one 正常路径
        （cancel_event 未 set）不应有额外 sleep/wait_for 延迟。
        通过测量 fetch_one 执行时间验证：< 100ms（无 2s/3s 限速延迟）。"""
        import time

        dp = _make_dp()
        dp.clear_cancel()
        detail_df = pd.DataFrame(
            {
                "id": ["TS1"],
                "concept_name": ["Concept1"],
                "ts_code": ["000001.SZ"],
                "name": ["Stock1"],
            }
        )
        dp.api.get_concept_detail_by_id = AsyncMock(return_value=detail_df)

        # 直接调用 sync_concepts 内部的 fetch_one 逻辑（通过反射获取闭包不可行，
        # 改用整体 sync_concepts 调用并测量耗时，单个 concept 应在 100ms 内完成）
        df_c = pd.DataFrame({"code": ["TS1"]})
        dp.api.get_concept_list = AsyncMock(return_value=df_c)
        dp.cache.stock_dao.overwrite_concepts = AsyncMock(return_value=1)

        start = time.monotonic()
        result = await dp.sync_concepts()
        elapsed = time.monotonic() - start

        # 单个 concept + 无限速 sleep，应在 100ms 内完成（容错 500ms 适应 CI 慢环境）
        assert result == 1
        assert elapsed < 0.5, (
            f"sync_concepts 单 concept 耗时 {elapsed:.3f}s > 0.5s，"
            "可能残留限速 sleep/wait_for（DoD ③：正常路径无额外 sleep）"
        )

    @pytest.mark.asyncio
    async def test_sync_concepts_token_bucket_is_rate_source(self):
        """P3-SyncConcepts-Dual-RateLimit DoD ④ 行为验证: TokenBucket 是唯一限速源。
        验证 fetch_one 不含任何 sleep/timeout 限速语义，仅依赖 _handle_api_call 的 TokenBucket。
        通过源码检查：fetch_one 内无 asyncio.sleep / asyncio.wait_for / time.sleep 调用。"""
        import inspect

        from data.data_processor import DataProcessor

        source = inspect.getsource(DataProcessor.sync_concepts)
        # 提取 fetch_one 函数体
        fetch_one_start = source.find("async def fetch_one")
        assert fetch_one_start != -1, "fetch_one 函数应存在于 sync_concepts"
        # fetch_one 函数体到下一个 async def 或到 sync_concepts 结束
        next_def = source.find("\n            async def ", fetch_one_start + 1)
        if next_def == -1:
            next_def = source.find("\n            tasks = ", fetch_one_start)
        fetch_one_body = source[fetch_one_start : next_def if next_def != -1 else len(source)]
        # 移除注释行
        code_lines = [line for line in fetch_one_body.splitlines() if not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        # fetch_one 内不应有限速语义（sleep/wait_for）
        assert "asyncio.sleep" not in code_only, "fetch_one 不应含 asyncio.sleep（限速应由 TokenBucket 负责）"
        assert "time.sleep" not in code_only, "fetch_one 不应含 time.sleep（限速应由 TokenBucket 负责）"
        assert "wait_for" not in code_only, "fetch_one 不应含 asyncio.wait_for（限速应由 TokenBucket 负责）"

    @pytest.mark.asyncio
    async def test_exception(self):
        dp = _make_dp()
        dp.api.get_concept_list = AsyncMock(side_effect=Exception("api error"))
        dp.clear_cancel()
        result = await dp.sync_concepts()
        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_concepts_engine_disposed_in_gather_results_propagates(self):
        """R5: gather_return_exceptions_propagating_cancel 返回的 EngineDisposedError
        必须显式 raise 传播，不得作为普通 subtask 失败被静默吞没。

        场景：fetch_one 内部 API 调用抛 EngineDisposedError，gather 以 return_exceptions
        模式收集后，sync_concepts 必须优先识别并 raise，由外层 except 捕获后通过
        classify_severity='system' 路径再次 raise（系统级异常不降级）。
        """
        from data.persistence.daos.base_dao import EngineDisposedError

        dp = _make_dp()
        df_c = pd.DataFrame({"code": ["TS1", "TS2"]})
        dp.api.get_concept_list = AsyncMock(return_value=df_c)
        # 模拟其中一个 fetch_one 任务抛出 EngineDisposedError
        detail_df = pd.DataFrame(
            {
                "id": ["TS2"],
                "concept_name": ["Concept2"],
                "ts_code": ["000002.SZ"],
                "name": ["Stock2"],
            }
        )

        async def _api_with_engine_disposed(c):
            if c == "TS1":
                raise EngineDisposedError("engine closed during concept detail fetch")
            return detail_df

        dp.api.get_concept_detail_by_id = AsyncMock(side_effect=_api_with_engine_disposed)
        dp.cache.stock_dao.overwrite_concepts = AsyncMock(return_value=1)
        dp.clear_cancel()

        with pytest.raises(EngineDisposedError):  # noqa: weak-assertion R5 红线契约仅验证 EngineDisposedError 类型传播即可，无有意义 message 可 match；后续 overwrite_concepts.assert_not_called 已强断言
            await dp.sync_concepts()
        # 验证：EngineDisposedError 在保存前抛出，未触发 overwrite_concepts
        dp.cache.stock_dao.overwrite_concepts.assert_not_called()


class TestDataProcessorInitData:
    @pytest.mark.asyncio
    async def test_init_data(self):
        dp = _make_dp()
        dp.cache.init_db = AsyncMock()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        await dp.init_data()
        dp.cache.init_db.assert_called_once()


class TestDataProcessorNormalizeContextTradeDate:
    def test_none(self):
        assert DataProcessor._normalize_context_trade_date(None) is None

    def test_nan(self):
        assert DataProcessor._normalize_context_trade_date(float("nan")) is None

    def test_string(self):
        assert DataProcessor._normalize_context_trade_date("20240614") == "20240614"

    def test_datetime(self):
        dt = datetime.datetime(2024, 6, 14)
        assert DataProcessor._normalize_context_trade_date(dt) == "20240614"

    def test_date(self):
        d = datetime.date(2024, 6, 14)
        assert DataProcessor._normalize_context_trade_date(d) == "20240614"


class TestDataProcessorResolveScreeningTradeDate:
    def test_explicit_only(self):
        result = DataProcessor._resolve_screening_trade_date("20240614", None)
        assert result == "20240614"

    def test_from_data(self):
        df = pd.DataFrame({"trade_date": ["20240614"]})
        result = DataProcessor._resolve_screening_trade_date(None, df)
        assert result == "20240614"

    def test_both_match(self):
        df = pd.DataFrame({"trade_date": ["20240614"]})
        result = DataProcessor._resolve_screening_trade_date("20240614", df)
        assert result == "20240614"

    def test_mismatch(self):
        df = pd.DataFrame({"trade_date": ["20240615"]})
        with pytest.raises(RuntimeError, match="mismatch"):
            DataProcessor._resolve_screening_trade_date("20240614", df)

    def test_multiple_dates(self):
        df = pd.DataFrame({"trade_date": ["20240614", "20240615"]})
        with pytest.raises(RuntimeError, match="multiple"):
            DataProcessor._resolve_screening_trade_date(None, df)

    def test_no_date_available(self):
        with pytest.raises(RuntimeError, match="No analysis"):
            DataProcessor._resolve_screening_trade_date(None, None)


class TestDataProcessorPrepareMarketData:
    @pytest.mark.asyncio
    async def test_latest_not_today(self):
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 13))
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            result = await dp.prepare_market_data()
            assert result == datetime.date(2024, 6, 13)

    @pytest.mark.asyncio
    async def test_latest_is_today_cached(self):
        dp = _make_dp()
        today = datetime.date(2024, 6, 14)
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=today)
        dp.cache.quote_dao.get_latest_trade_date = AsyncMock(return_value=today)
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            result = await dp.prepare_market_data()
            assert result == today

    @pytest.mark.asyncio
    async def test_calendar_unavailable_falls_back_to_today(self):
        """覆盖 L677-682: latest is None 时 sync_daily_market_snapshot(today) 并返回 today"""
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        dp.sync_daily_market_snapshot = AsyncMock()
        today = datetime.date(2024, 6, 14)
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            result = await dp.prepare_market_data()
        assert result == today
        dp.sync_daily_market_snapshot.assert_called_once_with(today)


class TestDataProcessorGetMarketOverview:
    @pytest.mark.asyncio
    async def test_exception(self):
        dp = _make_dp()
        dp.trade_calendar = MagicMock()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            result = await dp.get_market_overview()
            assert result is None

    @pytest.mark.asyncio
    async def test_batch_query_success(self):
        dp = _make_dp()
        dp.trade_calendar = MagicMock()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        index_df = pd.DataFrame(
            {
                "ts_code": ["000001.SH", "399001.SZ", "399006.SZ"],
                "pct_chg": [1.5, -2.0, 0.0],
                "close": [3000.0, 10000.0, 2000.0],
            }
        )
        dp.cache.get_index_daily_range = AsyncMock(return_value=index_df)
        dp.cache.market_dao.get_moneyflow_hsgt = AsyncMock(return_value=None)
        dp.api.get_moneyflow_hsgt = AsyncMock(return_value=None)
        with patch(
            "data.data_processor.NewsFetcher.get_hot_concepts",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ):
                result = await dp.get_market_overview()
        assert result is not None
        assert len(result["indices"]) == 3
        assert result["indices"][0]["color"] == "red"
        assert result["indices"][1]["color"] == "green"
        assert result["indices"][2]["color"] == "grey"
        dp.cache.get_index_daily_range.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_miss_falls_back_to_api(self):
        dp = _make_dp()
        dp.trade_calendar = MagicMock()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        dp.cache.get_index_daily_range = AsyncMock(return_value=None)
        api_df = pd.DataFrame(
            {
                "ts_code": ["000001.SH"],
                "pct_chg": [0.5],
                "close": [3100.0],
            }
        )
        dp.api.get_index_daily = AsyncMock(return_value=api_df)
        dp.cache.market_dao.get_moneyflow_hsgt = AsyncMock(return_value=None)
        dp.api.get_moneyflow_hsgt = AsyncMock(return_value=None)
        with patch(
            "data.data_processor.NewsFetcher.get_hot_concepts",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ):
                result = await dp.get_market_overview()
        assert result is not None
        assert len(result["indices"]) == 3
        assert result["indices"][0]["color"] == "red"
        assert result["indices"][1]["color"] == "grey"
        assert result["indices"][2]["color"] == "grey"
        dp.api.get_index_daily.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_unavailable_returns_empty_dict(self):
        """覆盖 L700-705: latest_date is None 时返回 {}"""
        dp = _make_dp()
        dp.trade_calendar = MagicMock()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            result = await dp.get_market_overview()
        assert result == {}


class TestDataProcessorGetStockHistory:
    @pytest.mark.asyncio
    async def test_with_end_date_string(self):
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        dp.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)]
        )
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        await dp.get_stock_history("000001.SZ", days=365, end_date="20240614")
        daily_call = dp.cache.quote_dao.get_daily_quotes.call_args
        assert daily_call.kwargs["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_with_end_date_date(self):
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        dp.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)]
        )
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        await dp.get_stock_history("000001.SZ", end_date=datetime.date(2024, 6, 14))
        daily_call = dp.cache.quote_dao.get_daily_quotes.call_args
        assert daily_call.kwargs["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_no_end_date(self):
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        dp.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)]
        )
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            await dp.get_stock_history("000001.SZ")
            daily_call = dp.cache.quote_dao.get_daily_quotes.call_args
            assert daily_call.kwargs["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_end_date_exception_fallback(self):
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
        dp.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)]
        )
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            await dp.get_stock_history("000001.SZ")
            daily_call = dp.cache.quote_dao.get_daily_quotes.call_args
            assert daily_call.kwargs["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_latest_trade_date_none_falls_back_to_today(self):
        """覆盖 L984-989: latest_closed_trade_date is None 时 end = get_now().date()"""
        dp = _make_dp()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        dp.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)]
        )
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
            await dp.get_stock_history("000001.SZ")
        dp.cache.quote_dao.get_daily_quotes.assert_called_once_with(
            ts_code="000001.SZ",
            start_date=datetime.date(2024, 1, 2),
            end_date=datetime.date(2024, 6, 14),
        )


class TestDataProcessorRunDailyUpdate:
    @pytest.mark.asyncio
    async def test_run_daily_update(self):
        dp = _make_dp()
        dp.init_data = AsyncMock()
        dp.sync_daily_market_snapshot = AsyncMock(return_value=pd.DataFrame())
        dp.sync_financial_reports = AsyncMock()
        with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
            mock_instance = MagicMock()
            mock_instance.run_review = AsyncMock()
            mock_rm.return_value = mock_instance
            await dp.run_daily_update()
            dp.init_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_callback(self):
        dp = _make_dp()
        dp.init_data = AsyncMock()
        dp.sync_daily_market_snapshot = AsyncMock(return_value=pd.DataFrame())
        dp.sync_financial_reports = AsyncMock()
        callback = MagicMock()
        with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
            mock_instance = MagicMock()
            mock_instance.run_review = AsyncMock()
            mock_rm.return_value = mock_instance
            await dp.run_daily_update(progress_callback=callback)
            assert callback.call_count >= 4


class TestDataProcessorPrepareScreeningContext:
    @pytest.mark.asyncio
    async def test_basic_context(self):
        dp = _make_dp()
        dp._quality_tier = 3
        dp.cache.screener_dao.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                    "is_tradable": [True],
                }
            )
        )
        dp.cache.screener_dao.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "is_tradable": [True]})
        )
        dp.cache.quote_dao.get_northbound = AsyncMock(return_value=pd.DataFrame())
        dp.cache.market_dao.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        dp.cache.quote_dao.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.quote_dao.get_top_list = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_block_trade = AsyncMock(return_value=None)
        result = await dp.prepare_screening_context(trade_date="20240614")
        assert "screening_data" in result
        assert "_diagnostics" in result

    @pytest.mark.asyncio
    async def test_with_suspended_stocks(self):
        dp = _make_dp()
        dp._quality_tier = 3
        dp.cache.screener_dao.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240614", "20240614"],
                    "is_tradable": [True, False],
                }
            )
        )
        dp.cache.screener_dao.get_fundamental_screening_data = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_northbound = AsyncMock(return_value=None)
        dp.cache.market_dao.get_moneyflow_hsgt = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_moneyflow = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_top_list = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_block_trade = AsyncMock(return_value=None)
        result = await dp.prepare_screening_context(trade_date="20240614")
        assert len(result["screening_data"]) == 1

    @pytest.mark.asyncio
    async def test_no_quality_tier(self):
        dp = _make_dp()
        dp._quality_tier = None
        dp._assign_basic_tier = AsyncMock()
        dp.cache.screener_dao.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                    "is_tradable": [True],
                }
            )
        )
        dp.cache.screener_dao.get_fundamental_screening_data = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_northbound = AsyncMock(return_value=None)
        dp.cache.market_dao.get_moneyflow_hsgt = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_moneyflow = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_top_list = AsyncMock(return_value=None)
        dp.cache.quote_dao.get_block_trade = AsyncMock(return_value=None)
        await dp.prepare_screening_context(trade_date="20240614")
        dp._assign_basic_tier.assert_called_once()


class TestDataProcessorInitializeSystem:
    @pytest.mark.asyncio
    async def test_quick_mode(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.check_data_health = AsyncMock(return_value={"tier": 3})
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system(quick=True)
            assert result is not None

    @pytest.mark.asyncio
    async def test_reports_progress_messages(self):
        """D7: initialize_system 成功路径经 progress_callback 上报 Message(key)（直透/缺省生成/完成）。"""
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.check_data_health = AsyncMock(return_value={"tier": 3})
        dp.clear_cancel()
        progress: list = []
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system(
                quick=True,
                progress_callback=lambda c, t, m: progress.append((c, t, m)),
            )
            assert result is not None

        # report_step 缺省生成 Message（无 sub_msg）与直透 Message（同步子进度）均生效
        assert any(m.key == "init_step_1" for c, t, m in progress)
        assert any(m.key == "init_sync_concepts" for c, t, m in progress)
        # 完成上报 init_step_complete（非 init_step_N）
        assert any(m.key == "init_step_complete" for c, t, m in progress)
        # D7: 全部消息均为 Message 而非已翻译字符串
        assert all(isinstance(m, Message) for c, t, m in progress)
        # 加权进度单调上升到 100
        values = [c for c, t, m in progress]
        assert values == sorted(values)
        assert values[-1] == 100

    @pytest.mark.asyncio
    async def test_stock_basic_returns_zero(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=0)
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            result = await dp.initialize_system()
            assert result is None

    @pytest.mark.asyncio
    async def test_calendar_fails(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=False)
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system()
            assert result is None

    @pytest.mark.asyncio
    async def test_full_mode(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["historical"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.strategies["financial"].run = AsyncMock(return_value=MagicMock(status="completed", added=10))
        dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.check_data_health = AsyncMock(return_value={"tier": 3})
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system()
            assert result is not None

    @pytest.mark.asyncio
    async def test_historical_failed(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["historical"].run = AsyncMock(return_value=MagicMock(status="failed", errors=["err"]))
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system()
            assert result is None

    @pytest.mark.asyncio
    async def test_financial_failed(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["historical"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.strategies["financial"].run = AsyncMock(return_value=MagicMock(status="failed", errors=["err"]))
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system()
            assert result is None

    @pytest.mark.asyncio
    async def test_cancelled_after_step1(self):
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.clear_cancel()
        dp._get_cancel_event().set()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            result = await dp.initialize_system()
            assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_exception_reraised(self):
        """覆盖 L963-971: except Exception as e: ... raise（异常向上传播）"""
        dp = _make_dp()
        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["historical"].run = AsyncMock(side_effect=RuntimeError("unexpected"))
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            with pytest.raises(RuntimeError, match="unexpected"):
                await dp.initialize_system()

    @pytest.mark.asyncio
    async def test_init_db_called_before_sync_stock_basic(self):
        """P1 时序修复：initialize_system 须在 sync_stock_basic 之前 await cache.init_db()。"""
        dp = _make_dp()
        call_order = []

        def _idb():
            call_order.append("init_db")

        def _ssb():
            call_order.append("sync_stock_basic")
            return 5

        dp.cache.init_db = AsyncMock(side_effect=_idb)
        dp.sync_stock_basic = AsyncMock(side_effect=_ssb)
        dp.sync_concepts = AsyncMock(return_value=3)
        dp.trade_calendar.ensure_calendar_range = AsyncMock(return_value=True)
        dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="completed"))
        dp.check_data_health = AsyncMock(return_value={"tier": 3})
        dp.clear_cancel()
        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            result = await dp.initialize_system(quick=True)
            assert result is not None
        assert call_order.index("init_db") < call_order.index("sync_stock_basic")


class TestDataProcessorCancelControl:
    def test_is_cancelled_default(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc._cancel_event = None
        with patch("data.data_processor.get_loop_local") as mock_gll:
            mock_evt = MagicMock()
            mock_evt.is_set.return_value = False
            mock_gll.return_value = mock_evt
            assert proc.is_cancelled() is False

    def test_clear_cancel(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc._cancel_event = None
        # FIND-R3-001: clear_cancel 访问 self.context.cancel_event（FIND-R1-004 修复），
        # __new__ 绕过 __init__ 需补 context 属性避免 AttributeError
        proc.context = MagicMock()
        proc.context.cancel_event = None
        with patch("data.data_processor.get_loop_local") as mock_gll:
            mock_evt = MagicMock()
            mock_gll.return_value = mock_evt
            proc.clear_cancel()
            mock_evt.clear.assert_called_once()


class TestDataProcessorCancelEvent:
    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_get_cancel_event(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        evt = dp._get_cancel_event()
        assert evt is not None

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_is_cancelled_initially_false(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        assert dp.is_cancelled() is False


class TestDataProcessorClearCancel:
    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_clear_cancel(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        dp._get_cancel_event().set()
        dp.clear_cancel()
        assert dp.is_cancelled() is False

    @pytest.mark.asyncio
    async def test_clear_cancel_propagates_to_context_cancel_event(self):
        """FIND-R1-006: A4 修复 — clear_cancel 传播到 context.cancel_event（覆盖 L180）。"""
        dp = _make_dp()
        # 注入 mock context.cancel_event（模拟 run_ai_concept_tagging DI）
        mock_cancel_event = MagicMock()
        dp.context.cancel_event = mock_cancel_event
        dp._get_cancel_event().set()
        dp.clear_cancel()
        assert dp.is_cancelled() is False
        mock_cancel_event.clear.assert_called_once_with()


class TestDataProcessorGetFundamentalScreeningData:
    @pytest.mark.asyncio
    async def test_delegates_to_cache(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc.cache = MagicMock()
        proc.cache.screener_dao.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        await proc.get_fundamental_screening_data("20240614")
        proc.cache.screener_dao.get_fundamental_screening_data.assert_called_once_with("20240614")


class TestDataProcessorGetScreeningData:
    @pytest.mark.asyncio
    async def test_delegates_to_cache(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc.cache = MagicMock()
        proc.cache.screener_dao.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        await proc.get_screening_data("20240614")
        proc.cache.screener_dao.get_screening_data.assert_called_once_with("20240614")


class TestDataProcessorInit:
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.ConfigHandler")
    def test_init_sets_token(self, mock_ch, mock_tc, mock_cm):
        mock_ch.get_token.return_value = "test_token"
        dp = DataProcessor()
        assert dp._current_token == "test_token"

    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.ConfigHandler")
    def test_init_creates_api(self, mock_ch, mock_tc, mock_cm):
        mock_ch.get_token.return_value = "test_token"
        DataProcessor()
        mock_tc.assert_called_once_with(token="test_token")


class TestDataProcessorRequestCancel:
    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_request_cancel_sets_event(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        for _name, strategy in dp.strategies.items():
            strategy.cancel = MagicMock()
        await dp.request_cancel()
        assert dp.is_cancelled() is True

    @pytest.mark.asyncio
    async def test_request_cancel_propagates_to_context_cancel_event(self):
        """FIND-R1-006: A4 修复 — request_cancel 传播到 context.cancel_event。"""
        dp = _make_dp()
        for s in dp.strategies.values():
            s.cancel = MagicMock()
        # 注入 mock context.cancel_event（模拟 run_ai_concept_tagging DI）
        mock_cancel_event = MagicMock()
        dp.context.cancel_event = mock_cancel_event
        await dp.request_cancel()
        assert dp.is_cancelled() is True
        mock_cancel_event.set.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_request_cancel_context_cancel_event_none_safe(self):
        """FIND-R1-006: A4 修复 — context.cancel_event 为 None 时安全降级（不抛异常）。"""
        dp = _make_dp()
        for s in dp.strategies.values():
            s.cancel = MagicMock()
        # context.cancel_event 默认 None（无 run_ai_concept_tagging 注入）
        assert dp.context.cancel_event is None
        await dp.request_cancel()
        assert dp.is_cancelled() is True


class TestDataProcessorResetSingleton:
    def test_reset(self):
        DataProcessor._reset_singleton()
        assert DataProcessor._instance is None


class TestDataProcessorSingleton:
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.ConfigHandler")
    def test_singleton_same_instance(self, mock_ch, mock_tc, mock_cm):
        mock_ch.get_token.return_value = "test_token"
        dp1 = DataProcessor()
        dp2 = DataProcessor()
        assert dp1 is dp2


class TestDataProcessorStop:
    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_stop_sets_cancel(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        for _name, strategy in dp.strategies.items():
            strategy.cancel = MagicMock()
        await dp.stop()
        assert dp.is_cancelled() is True

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_stop_handles_exception_in_strategy_cancel(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        # One strategy raises exception, others don't
        for name, strategy in dp.strategies.items():
            if name == "financial":
                strategy.cancel = MagicMock(side_effect=Exception("cancel failed"))
            else:
                strategy.cancel = MagicMock()

        # Should not raise exception overall
        await dp.stop()

        # Verify cancel event is still set
        assert dp.is_cancelled() is True

    @pytest.mark.asyncio
    async def test_stop_propagates_to_context_cancel_event(self):
        """FIND-R1-006: A4 修复 — stop 传播到 context.cancel_event（覆盖 L188）。"""
        dp = _make_dp()
        for s in dp.strategies.values():
            s.cancel = MagicMock()
        # 注入 mock context.cancel_event（模拟 run_ai_concept_tagging DI）
        mock_cancel_event = MagicMock()
        dp.context.cancel_event = mock_cancel_event
        await dp.stop()
        assert dp.is_cancelled() is True
        mock_cancel_event.set.assert_called_once_with()
