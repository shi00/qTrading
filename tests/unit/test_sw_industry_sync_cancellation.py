"""Phase 3F-1：SwIndustrySyncStrategy 循环内取消信号测试。

验证 `_run_impl` 循环体每 200 个 index_code 检查 `_check_cancelled`，
响应取消信号（Phase 3F-1 §4.3.2 新增行为）。
"""

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.persistence.quality_gate import QualityTier
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
                    "level": level,
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


class TestSwIndustryRequiredQualityTier:
    """Phase 3F-1 §4.3.2：SwIndustrySyncStrategy 声明 required_quality_tier。"""

    def test_required_quality_tier_is_bronze(self):
        """required_quality_tier 必须为 QualityTier.BRONZE（基础元数据等级）。"""
        assert SwIndustrySyncStrategy.required_quality_tier == QualityTier.BRONZE


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
    """Phase 3F-1 §4.3.2：_sync_members 循环体每 200 个 index_code 检查 _check_cancelled。"""

    @pytest.mark.asyncio
    async def test_members_cancel_at_200(self):
        """201 个 index_code，i=200 时 _check_cancelled 返回 True。

        验证：循环提前 return，save_sw_industry_member 未被调用，get_index_member_all
        只调用 200 次（i=0..199）。
        """
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=0)

        classify_df = pd.DataFrame(
            {
                "index_code": [f"8010{i:04d}.SI" for i in range(201)],
                "level": ["L1"] * 201,
            }
        )

        call_count = 0

        def check_side_effect(result):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # i=200 时第 1 次检查返回 True
                result.status = "cancelled"
                return True
            return False

        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("80100000.SI"))

        with (
            patch.object(strategy, "_check_cancelled", side_effect=check_side_effect),
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            from data.sync.base import SyncResult

            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert call_count == 1
        assert ctx.api.get_index_member_all.await_count == 200
        strategy.member_dao.save_sw_industry_member.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_members_no_cancel_under_200(self):
        """2 个 index_code，循环内不触发取消（i=200 检查点不触达）。

        验证：循环正常完成，save_sw_industry_member 被调用一次。
        """
        ctx = _make_ctx()
        strategy = SwIndustrySyncStrategy(ctx)
        strategy.member_dao = MagicMock()
        strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=2)

        classify_df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI"],
                "level": ["L1", "L1"],
            }
        )

        with (
            patch.object(strategy, "_check_cancelled", return_value=False) as mock_check,
            patch.object(strategy, "_record_skipped_permission", new=AsyncMock()),
        ):
            ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

            from data.sync.base import SyncResult

            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        # 循环内检查点不触达（i < 200），_check_cancelled 仅在调用入口被检查 0 次
        # （_sync_members 内部不调用 _check_cancelled 入口）
        assert ctx.api.get_index_member_all.await_count == 2
        strategy.member_dao.save_sw_industry_member.assert_awaited_once()
        # _check_cancelled 不应在循环内被调用（i=200 检查点未达）
        mock_check.assert_not_called()

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

        classify_df = pd.DataFrame({"index_code": ["801010.SI", "801020.SI"], "level": ["L1", "L1"]})

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
