"""Phase 2F + S7: concept_sync 循环内取消信号测试。

验证 AKShareConceptSyncStrategy 和 LimitListSyncStrategy 的 _run_impl 循环体
响应取消信号：
- AKShareConceptSyncStrategy: S7 修复后改为时间维度（每 2 秒）检查 _check_cancelled，
  旧实现"每 200 条"在单 board 最坏 7s+ 时最坏 1000s 才响应取消，远超 2s 红线。
- LimitListSyncStrategy: 仍按每 200 条检查（Phase 2F 行为）。

AIConceptTagSyncStrategy 已有每条都检查 _cancelled + cancel_event.is_set()
（concept_sync.py:366-372），比"每 200 条"更严格，由既有测试 test_concept_sync.py
覆盖，此处不重复。DataProcessor.run_ai_concept_tagging 的取消由
test_data_processor_ai_concept.py 覆盖。
"""

import datetime

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.sync.base import SyncContext
from data.sync.concept_sync import (
    AKShareConceptSyncStrategy,
    LimitListSyncStrategy,
)

pytestmark = pytest.mark.unit


# --- Helpers ---


def _make_ctx(**overrides):
    """Build a MagicMock-backed SyncContext with all dependencies wired.

    与 tests/unit/test_concept_sync.py 中的 _make_ctx 保持一致，确保两组测试
    使用相同的上下文构造方式（便于维护、避免跨文件 helper 依赖）。
    """
    ctx = MagicMock(spec=SyncContext)
    ctx.cache = MagicMock()
    ctx.cache.stock_dao = MagicMock()
    ctx.api = MagicMock()
    ctx.ai_service = None
    ctx.cancel_event = None
    ctx.processor = None
    ctx.config.get_ai_concept_search_engine = MagicMock(return_value="search_std")
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _make_boards_df(n: int) -> pd.DataFrame:
    """Construct n AKShare concept boards for sync loop testing."""
    return pd.DataFrame(
        {
            "板块名称": [f"概念_{i}" for i in range(n)],
            "板块代码": [f"BK{i:04d}" for i in range(n)],
        }
    )


def _make_limit_list_df(n: int) -> pd.DataFrame:
    """Construct n Tushare limit_list records for sync loop testing."""
    return pd.DataFrame(
        {
            "ts_code": [f"{i:06d}.SZ" for i in range(n)],
            "trade_date": ["20240614"] * n,
            "name": [f"股票_{i}" for i in range(n)],
        }
    )


class TestAKShareLoopCancellation:
    """S7: AKShareConceptSyncStrategy 循环体按时间维度（每 2 秒）检查 _check_cancelled。

    覆盖 concept_sync.py 循环内取消检查点。_check_cancelled 调用顺序：
    1. _run_impl 入口
    2. get_concept_list 返回后
    3. 循环内距上次检查 >= 2s 时（S7 新增）
    4. gather 返回后（仅当循环内未取消）
    """

    @pytest.mark.asyncio
    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    # filterwarnings: 循环内取消时，已创建的 sync_one_board coroutine
    # 不会被 await（return 发生在循环内检查点），触发 RuntimeWarning。
    # 这是验证循环内取消行为的必要副作用，非生产代码问题。
    async def test_loop_cancel_after_2s(self):
        """循环内 get_now() 距上次检查 >= 2s，第 3 次 _check_cancelled 返回 True。

        时间序列：last_cancel_check = T0；循环第 1 次迭代 get_now() 返回 T0+3s
        （差 3s >= 2s，触发 _check_cancelled）→ 返回 True → return。

        验证：返回 CANCELLED，gather 未被调用（循环提前 return）。
        """
        ctx = _make_ctx()
        strategy = AKShareConceptSyncStrategy(ctx)
        boards_df = _make_boards_df(2)

        call_count = 0

        def check_side_effect(result):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                result.status = "cancelled"
                return True
            return False

        # 时间序列：
        # - index 0: 循环前 last_cancel_check = get_now() → T0
        # - index 1+: 循环内 get_now() → T0+3s（差 3s >= 2s 触发检查）
        t0 = datetime.datetime(2024, 6, 14, 9, 30, 0, tzinfo=datetime.UTC)
        t_loop = datetime.datetime(2024, 6, 14, 9, 30, 3, tzinfo=datetime.UTC)
        time_sequence = [t0, t_loop]
        time_idx = 0

        def mock_get_now():
            nonlocal time_idx
            t = time_sequence[time_idx] if time_idx < len(time_sequence) else t_loop
            time_idx += 1
            return t

        with (
            patch("data.sync.concept_sync.AkshareConceptClient") as MockClient,
            patch.object(strategy, "_check_cancelled", side_effect=check_side_effect),
            patch("data.sync.concept_sync.gather_return_exceptions_propagating_cancel") as mock_gather,
            patch("data.sync.concept_sync.get_now", side_effect=mock_get_now),
        ):
            MockClient.return_value.get_concept_list = AsyncMock(return_value=boards_df)

            result = await strategy.run()

            assert result.status == "cancelled"
            assert call_count == 3
            mock_gather.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_no_cancel_within_2s(self):
        """循环内每次 get_now() 距上次检查 < 2s，不触发 _check_cancelled。

        所有 get_now() 返回同一时间 T0，差 0s < 2s，循环内检查点不触发。

        验证：正常完成，_check_cancelled 调用 3 次（入口 + concept_list 后 + gather 后），
        循环内检查点不触发。
        """
        ctx = _make_ctx()
        strategy = AKShareConceptSyncStrategy(ctx)
        boards_df = _make_boards_df(2)

        fixed_time = datetime.datetime(2024, 6, 14, 9, 30, 0, tzinfo=datetime.UTC)

        with (
            patch("data.sync.concept_sync.AkshareConceptClient") as MockClient,
            patch.object(strategy, "_check_cancelled", return_value=False) as mock_check,
            patch("data.sync.concept_sync.get_now", return_value=fixed_time),
        ):
            MockClient.return_value.get_concept_list = AsyncMock(return_value=boards_df)
            MockClient.return_value.get_concept_constituents = AsyncMock(
                return_value=pd.DataFrame({"代码": [], "名称": []})
            )
            ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(return_value=0)

            result = await strategy.run()

            assert result.status == "success"
            assert mock_check.call_count == 3


class TestLimitListLoopCancellation:
    """Phase 2F: LimitListSyncStrategy 循环体每 200 条检查 _check_cancelled。

    S11 修复后顺序为 fetch→clear+upsert，_check_cancelled 调用顺序：
    1. _run_impl 入口
    2. 循环内 i=200（Phase 2F 新增）
    3. 循环结束后（仅当循环内未取消）
    """

    @pytest.mark.asyncio
    async def test_loop_cancel_at_200(self):
        """201 条 limit_list，第 2 次 _check_cancelled（循环内 i=200）返回 True。

        验证：返回 CANCELLED，clear_today_limit_concepts 与 upsert_limit_concepts 均未被调用。
        """
        ctx = _make_ctx()
        strategy = LimitListSyncStrategy(ctx)
        limit_df = _make_limit_list_df(201)

        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=limit_df)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=0)

        call_count = 0

        def check_side_effect(result):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                result.status = "cancelled"
                return True
            return False

        with patch.object(strategy, "_check_cancelled", side_effect=check_side_effect):
            result = await strategy.run()

            assert result.status == "cancelled"
            assert call_count == 2
            ctx.cache.stock_dao.clear_today_limit_concepts.assert_not_called()
            ctx.cache.stock_dao.upsert_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_loop_no_cancel_under_200(self):
        """2 条记录，循环内不触发取消（i % 200 != 0）。

        验证：正常完成，_check_cancelled 调用 2 次（入口 + 循环后），
        循环内检查点不触发。
        """
        ctx = _make_ctx()
        strategy = LimitListSyncStrategy(ctx)
        limit_df = _make_limit_list_df(2)

        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=limit_df)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=2)

        with patch.object(strategy, "_check_cancelled", return_value=False) as mock_check:
            result = await strategy.run()

            assert result.status == "success"
            assert mock_check.call_count == 2
