"""Phase 3F-1 + A1：SwIndustrySyncStrategy 循环内取消信号测试。

验证 `_sync_members` 循环体按时间维度（每 2 秒）检查 `_check_cancelled`，
响应取消信号（A1 修复后行为）。

旧实现按条数维度（每 200 个 index_code）检查，每个迭代含网络 IO（约 1-2
秒），最坏需 200-400 秒才响应取消信号，违反项目硬约束"long-running 操作
必须每 2 秒检查 cancel_event"。A1 改用 `time.monotonic()` 时间维度测量。
"""

import asyncio

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.sync.base import SyncContext
from data.sync.sw_industry import SwIndustrySyncStrategy

pytestmark = pytest.mark.unit


def _make_ctx(**overrides):
    """Build a MagicMock-backed SyncContext with all dependencies wired."""
    ctx = MagicMock(spec=SyncContext)
    ctx.cache = MagicMock()
    ctx.cache.engine = MagicMock()
    ctx.cache.update_sync_status = AsyncMock(return_value=None)
    ctx.api = MagicMock()
    ctx.processor = None
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _make_classify_df(n_levels: int = 3, codes_per_level: int = 1) -> pd.DataFrame:
    """Build classification DataFrame with n_levels * codes_per_level rows."""
    rows = []
    levels = ["L1", "L2", "L3"][:n_levels]
    for level in levels:
        for i in range(codes_per_level):
            rows.append(
                {
                    "index_code": f"8010{i:03d}.SI",
                    "index_name": f"行业_{level}_{i}",
                    "sw_level": level,
                    "industry_code": f"{i:06d}",
                    "industry_name": f"行业_{i}",
                    "parent_code": "",
                    "is_sw": "1",
                }
            )
    return pd.DataFrame(rows)


def _make_member_df(index_code: str) -> pd.DataFrame:
    """Build a single-row member DataFrame for the given index_code."""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "index_code": [index_code],
            "index_name": [f"行业_{index_code}"],
            "sw_l1_code": ["110000"],
            "sw_l1_name": ["农林牧渔"],
            "sw_l2_code": ["110100"],
            "sw_l2_name": ["种植业"],
            "sw_l3_code": ["110101"],
            "sw_l3_name": ["玉米"],
        }
    )


class TestSyncClassifyCancellation:
    """Phase 3F-1 §4.3.2：_sync_classify 在每个 level 之间检查 _check_cancelled。"""

    @pytest.mark.asyncio
    async def test_classify_cancel_between_levels(self):
        """L1 同步完成后，_sync_classify 在 L2 调用前检查 _check_cancelled 返回 True。

        验证：返回空 DataFrame，L2/L3 未调用。
        """
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.classify_dao = MagicMock()
        strategy.classify_dao.save_sw_industry_classify = AsyncMock(return_value=5)

        call_count = 0

        def check_side_effect(result):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # L1 之后第 2 次检查
                result.status = "cancelled"
                return True
            return False

        with (
            patch.object(strategy, "_check_cancelled", side_effect=check_side_effect),
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            ctx.api.get_index_classify = AsyncMock(return_value=_make_classify_df(1))

            result_df = await strategy._sync_classify(MagicMock())

        assert result_df.empty
        assert call_count == 2
        strategy.classify_dao.save_sw_industry_classify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_classify_no_cancel_completes_all_levels(self):
        """无取消信号时，L1/L2/L3 三级均调用 get_index_classify，返回拼接 DataFrame。"""
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.classify_dao = MagicMock()
        strategy.classify_dao.save_sw_industry_classify = AsyncMock(return_value=3)

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            ctx.api.get_index_classify = AsyncMock(side_effect=[_make_classify_df(1)] * 3)

            from data.sync.base import SyncResult

            result = SyncResult()
            result_df = await strategy._sync_classify(result)

        assert not result_df.empty
        assert len(result_df) == 3  # 3 levels × 1 code each
        assert ctx.api.get_index_classify.await_count == 3
        strategy.classify_dao.save_sw_industry_classify.assert_awaited_once()


class TestSyncMembersCancellation:
    """A1: _sync_members 循环体按时间维度（每 2 秒）检查 _check_cancelled。

    旧实现按条数维度（每 200 个 index_code）检查，每个迭代含网络 IO（约 1-2
    秒），最坏需 200-400 秒才响应取消信号，违反 2s 红线。A1 改用
    `time.monotonic()` 时间维度测量。
    """

    @pytest.mark.asyncio
    async def test_members_cancel_after_2_seconds(self):
        """循环内 time.monotonic() 距上次检查 >= 2s，_check_cancelled 返回 True。

        时间序列：
        - 循环前 last_cancel_check = T0（第 1 次 monotonic 调用）
        - i=0: now=T0+1s（diff=1s < 2s，不触发），执行 api call
        - i=1: now=T0+3s（diff=3s >= 2s，触发 _check_cancelled → True），提前 return

        验证：循环提前 return，save_sw_industry_member 未被调用，
        get_index_member_all 只调用 1 次（i=0）。
        """
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=0)

        classify_df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI"],
                "sw_level": ["L1", "L1"],
            }
        )

        call_count = 0

        def check_side_effect(result):
            nonlocal call_count
            call_count += 1
            result.status = "cancelled"
            return True

        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

        # 时间序列：
        # - last_cancel_check = monotonic() → T0（第 1 次调用）
        # - i=0: now = monotonic() → T0+1s（diff=1s < 2s，不触发），api call 执行
        # - i=1: now = monotonic() → T0+3s（diff=3s >= 2s，触发 _check_cancelled → True）
        t0 = 1000.0
        time_sequence = [t0, t0 + 1.0, t0 + 3.0]
        time_idx = 0

        def mock_monotonic():
            nonlocal time_idx
            t = time_sequence[time_idx] if time_idx < len(time_sequence) else t0 + 3.0
            time_idx += 1
            return t

        with (
            patch("data.sync.sw_industry.time.monotonic", side_effect=mock_monotonic),
            patch.object(strategy, "_check_cancelled", side_effect=check_side_effect),
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            from data.sync.base import SyncResult

            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert call_count == 1
        assert ctx.api.get_index_member_all.await_count == 1
        strategy.member_dao.save_sw_industry_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_members_no_cancel_within_2_seconds(self):
        """循环内每次 time.monotonic() 距上次检查 < 2s，不触发 _check_cancelled。

        所有 time.monotonic() 返回同一时间 T0，diff=0s < 2s，循环内检查点不触发。

        验证：循环正常完成，save_sw_industry_member 被调用一次，
        _check_cancelled 在循环内不被调用。
        """
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=2)

        classify_df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI"],
                "sw_level": ["L1", "L1"],
            }
        )

        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

        fixed_t = 1000.0
        with (
            patch("data.sync.sw_industry.time.monotonic", return_value=fixed_t),
            patch.object(strategy, "_check_cancelled", return_value=False) as mock_check,
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            from data.sync.base import SyncResult

            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert ctx.api.get_index_member_all.await_count == 2
        strategy.member_dao.save_sw_industry_member.assert_awaited_once()
        # _check_cancelled 不应在循环内被调用（时间差 < 2s，检查点未触发）
        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_members_cancel_event_set_propagates_within_2s(self):
        """cancel_event 被 set 后，循环内 ≤2 秒响应取消（_check_cancelled 真实路径）。

        不 mock _check_cancelled，使用真实实现读取 ctx.cancel_event.is_set()。

        时间序列：
        - 循环前 last_cancel_check = T0（第 1 次 monotonic 调用）
        - i=0: now=T0+2s（diff=2s >= 2s，触发 _check_cancelled），cancel_event 已 set
          → result.status="cancelled"，return

        验证：result.status == "cancelled"，save_sw_industry_member 未被调用，
        get_index_member_all 调用 0 次（i=0 检查点即取消，未及 api call）。
        """
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=0)

        cancel_event = asyncio.Event()
        cancel_event.set()
        ctx.cancel_event = cancel_event

        classify_df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI"],
                "sw_level": ["L1", "L1"],
            }
        )

        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

        t0 = 1000.0
        time_sequence = [t0, t0 + 2.0]
        time_idx = 0

        def mock_monotonic():
            nonlocal time_idx
            t = time_sequence[time_idx] if time_idx < len(time_sequence) else t0 + 2.0
            time_idx += 1
            return t

        with (
            patch("data.sync.sw_industry.time.monotonic", side_effect=mock_monotonic),
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            from data.sync.base import SyncResult

            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert result.status == "cancelled"
        ctx.api.get_index_member_all.assert_not_awaited()
        strategy.member_dao.save_sw_industry_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_members_empty_classify_skips_loop(self):
        """classify_df 为空时，_sync_members 直接 return，不进入循环。"""
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=0)
        ctx.api.get_index_member_all = AsyncMock()

        from data.sync.base import SyncResult

        result = SyncResult()
        await strategy._sync_members(result, pd.DataFrame())

        ctx.api.get_index_member_all.assert_not_awaited()
        strategy.member_dao.save_sw_industry_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_members_dedup_on_ts_code_index_code(self):
        """combined DataFrame 在 ts_code+index_code 上去重（同一股票多次出现保留首条）。"""
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=1)

        classify_df = pd.DataFrame({"index_code": ["801010.SI", "801020.SI"], "sw_level": ["L1", "L1"]})

        # 两次调用都返回相同 ts_code+index_code，应去重为 1 行
        ctx.api.get_index_member_all = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "index_code": ["801010.SI"],
                    "index_name": ["农林牧渔"],
                    "sw_l1_code": ["110000"],
                    "sw_l1_name": ["农林牧渔"],
                    "sw_l2_code": ["110100"],
                    "sw_l2_name": ["种植业"],
                    "sw_l3_code": ["110101"],
                    "sw_l3_name": ["玉米"],
                }
            )
        )

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            from data.sync.base import SyncResult

            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        strategy.member_dao.save_sw_industry_member.assert_awaited_once()
        saved_df = strategy.member_dao.save_sw_industry_member.call_args.args[0]
        assert len(saved_df) == 1  # 去重后只剩 1 行
