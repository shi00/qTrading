"""Phase 3F-1：SwIndustrySyncStrategy 主流程 + 异常分层 + 权限记录测试。

补充覆盖 `_run_impl` 编排（_sync_classify → _sync_members → 状态日志）、
`_sync_classify` 异常处理（EngineDisposed/TushareAPIPermission/generic）、
`_sync_members` 内层循环异常处理与空数据降级、`_record_skipped_permission`
正常路径与异常吞没。取消信号路径见 ``test_sw_industry_sync_cancellation.py``。
"""

# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import logging

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.base import SyncContext, SyncResult, SyncStatus
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


def _wire_strategy(ctx, *, classify_count=3, member_count=1, mock_record_skipped=True):
    """构造 strategy 并 mock 两个 dao，返回 strategy 便于进一步定制。

    Args:
        mock_record_skipped: True 时 mock _record_skipped_permission（用于 _sync_classify/
            _sync_members 异常测试，避免实际写库）；False 时保留真实方法（用于
            TestRecordSkippedPermission 直接测试该方法）。
    """
    strategy = SwIndustrySyncStrategy(ctx)
    strategy.classify_dao = MagicMock()
    strategy.classify_dao.save_sw_industry_classify = AsyncMock(return_value=classify_count)
    strategy.member_dao = MagicMock()
    strategy.member_dao.save_sw_industry_member = AsyncMock(return_value=member_count)
    if mock_record_skipped:
        strategy._record_skipped_permission = AsyncMock()
    return strategy


class TestRunImplOrchestration:
    """_run_impl 编排：_sync_classify → _sync_members → 状态日志。"""

    @pytest.mark.asyncio
    async def test_happy_path_logs_complete(self, caplog):
        """正常完成：status=success，日志包含 ✅ Complete 与 added 计数。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx, classify_count=5, member_count=10)
        ctx.api.get_index_classify = AsyncMock(return_value=_make_classify_df(1))
        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.INFO, logger="data.sync.sw_industry"),
        ):
            result = await strategy._run_impl()

        assert result.status == "success"
        assert result.added == 15  # 5 classify + 10 member
        complete_logs = [r for r in caplog.records if "Complete" in r.message]
        assert len(complete_logs) == 1

    @pytest.mark.asyncio
    async def test_cancelled_between_classify_and_members(self, caplog):
        """_sync_classify 后 _check_cancelled 返回 True：提前返回，不调用 _sync_members。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        ctx.api.get_index_classify = AsyncMock(return_value=_make_classify_df(1))
        ctx.api.get_index_member_all = AsyncMock()

        # _check_cancelled 在 _sync_classify 内返回 False，在 _run_impl 中间检查返回 True
        call_count = 0

        def check_side_effect(result):
            nonlocal call_count
            call_count += 1
            # _sync_classify 内部调用 1 次（False），_run_impl 中间检查 1 次（True）
            if call_count >= 2:
                result.status = "cancelled"
                return True
            return False

        with (
            patch.object(strategy, "_check_cancelled", side_effect=check_side_effect),
            caplog.at_level(logging.INFO, logger="data.sync.sw_industry"),
        ):
            result = await strategy._run_impl()

        assert result.status == "cancelled"
        ctx.api.get_index_member_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_self_cancelled_after_members_logs_cancelled(self, caplog):
        """_cancelled=True 且 result.status 未标记 failed/cancelled 时，置 cancelled 并日志 ⚠️。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        ctx.api.get_index_classify = AsyncMock(return_value=_make_classify_df(1))
        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

        # 在 _sync_members 完成后置 _cancelled=True，模拟外部 cancel() 调用
        original_sync_members = strategy._sync_members

        async def patched_sync_members(result, classify_df):
            await original_sync_members(result, classify_df)
            strategy._cancelled = True

        strategy._sync_members = patched_sync_members

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.INFO, logger="data.sync.sw_industry"),
        ):
            result = await strategy._run_impl()

        assert result.status == "cancelled"
        cancelled_logs = [r for r in caplog.records if "Cancelled" in r.message]
        assert len(cancelled_logs) == 1


class TestRunImplExceptionHandlers:
    """_run_impl 异常分层：CancelledError / EngineDisposedError / system / recoverable / operational。

    注：_sync_classify 内部 except Exception 会吞没通用异常，故 system/recoverable/operational
    测试通过 patch _sync_classify 直接抛错来触达 _run_impl 顶层异常处理。
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_sets_status_and_reraises(self):
        """asyncio.CancelledError：status=cancelled 并 re-raise（R2）。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)

        async def raise_cancelled(result):
            raise asyncio.CancelledError()

        with patch.object(strategy, "_sync_classify", side_effect=raise_cancelled):
            with pytest.raises(asyncio.CancelledError):
                await strategy._run_impl()

    @pytest.mark.asyncio
    async def test_engine_disposed_sets_status_failed_and_reraises(self, caplog):
        """EngineDisposedError：status=failed 并 re-raise（R5），日志 warning。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)

        async def raise_engine_disposed(result):
            raise EngineDisposedError("disposed")

        with (
            patch.object(strategy, "_sync_classify", side_effect=raise_engine_disposed),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            with pytest.raises(EngineDisposedError):
                await strategy._run_impl()

        # 顶层 EngineDisposed 处理器写入 warning 日志（证明 handler 被触发）
        warning_logs = [r for r in caplog.records if "Engine disposed" in r.message]
        assert len(warning_logs) == 1

    @pytest.mark.asyncio
    async def test_system_level_exception_reraises(self, caplog):
        """severity=system：critical 日志并 re-raise。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        system_error = MemoryError("system-level OOM")

        async def raise_system(result):
            raise system_error

        with (
            patch("data.sync.sw_industry.classify_severity", return_value="system"),
            patch(
                "data.sync.sw_industry.classify_error",
                return_value={"code": "system", "message_key": "system_error"},
            ),
            patch.object(strategy, "_sync_classify", side_effect=raise_system),
            caplog.at_level(logging.CRITICAL, logger="data.sync.sw_industry"),
        ):
            with pytest.raises(MemoryError):
                await strategy._run_impl()

        critical_logs = [r for r in caplog.records if "SYSTEM-LEVEL" in r.message]
        assert len(critical_logs) == 1

    @pytest.mark.asyncio
    async def test_recoverable_exception_logs_warning_and_status_failed(self, caplog):
        """severity=recoverable：warning 日志，status=failed，不 re-raise。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        recoverable_error = TimeoutError("transient timeout")

        async def raise_recoverable(result):
            raise recoverable_error

        with (
            patch("data.sync.sw_industry.classify_severity", return_value="recoverable"),
            patch(
                "data.sync.sw_industry.classify_error",
                return_value={"code": "timeout", "message_key": "recoverable_error"},
            ),
            patch.object(strategy, "_sync_classify", side_effect=raise_recoverable),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            result = await strategy._run_impl()

        assert result.status == "failed"
        assert "recoverable_error" in result.errors
        warning_logs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Recoverable" in r.message for r in warning_logs)

    @pytest.mark.asyncio
    async def test_operational_exception_logs_error_and_status_failed(self, caplog):
        """severity=operational（默认）：error 日志，status=failed，不 re-raise。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        operational_error = ValueError("bad payload")

        async def raise_operational(result):
            raise operational_error

        with (
            patch("data.sync.sw_industry.classify_severity", return_value="operational"),
            patch(
                "data.sync.sw_industry.classify_error",
                return_value={"code": "operational", "message_key": "operational_error"},
            ),
            patch.object(strategy, "_sync_classify", side_effect=raise_operational),
            caplog.at_level(logging.ERROR, logger="data.sync.sw_industry"),
        ):
            result = await strategy._run_impl()

        assert result.status == "failed"
        assert "operational_error" in result.errors
        error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("Operational" in r.message for r in error_logs)


class TestSyncClassifyExceptions:
    """_sync_classify 异常处理：全空 / EngineDisposed / 权限拒绝 / 通用异常。"""

    @pytest.mark.asyncio
    async def test_all_levels_empty_logs_warning(self, caplog):
        """所有 level 返回空 DataFrame：日志 warning 并返回空 DataFrame。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        # 三个 level 都返回空 DataFrame
        ctx.api.get_index_classify = AsyncMock(return_value=pd.DataFrame())

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            result_df = await strategy._sync_classify(SyncResult())

        assert result_df.empty
        warning_logs = [r for r in caplog.records if "All levels returned empty" in r.message]
        assert len(warning_logs) == 1
        strategy.classify_dao.save_sw_industry_classify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates(self):
        """EngineDisposedError 在 _sync_classify 内必须传播（不吞为空 DataFrame）。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        ctx.api.get_index_classify = AsyncMock(side_effect=EngineDisposedError("disposed"))

        with patch.object(strategy, "_check_cancelled", return_value=False):
            with pytest.raises(EngineDisposedError):
                await strategy._sync_classify(SyncResult())

    @pytest.mark.asyncio
    async def test_permission_denied_records_skipped_and_returns_empty(self):
        """TushareAPIPermissionError：记录 skipped_permission 状态并返回空 DataFrame。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        ctx.api.get_index_classify = AsyncMock(side_effect=TushareAPIPermissionError("index_classify", "no perm"))

        with patch.object(strategy, "_check_cancelled", return_value=False):
            result = SyncResult()
            result_df = await strategy._sync_classify(result)

        assert result_df.empty
        assert any("permission denied" in err for err in result.errors)
        strategy._record_skipped_permission.assert_awaited_once_with("sw_industry_classify")

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_and_appends_error(self, caplog):
        """通用 Exception：日志 warning，errors 追加消息，返回空 DataFrame。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        ctx.api.get_index_classify = AsyncMock(side_effect=RuntimeError("network blip"))

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            result = SyncResult()
            result_df = await strategy._sync_classify(result)

        assert result_df.empty
        assert any("SwIndustry Classify" in err for err in result.errors)
        warning_logs = [r for r in caplog.records if "Classify" in r.message]
        assert len(warning_logs) == 1


class TestSyncMembersExceptions:
    """_sync_members 内层循环异常处理与空数据降级。"""

    @pytest.mark.asyncio
    async def test_engine_disposed_in_inner_loop_propagates(self):
        """内层循环抛 EngineDisposedError 必须传播（不吞为 skip）。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        classify_df = pd.DataFrame({"index_code": ["801010.SI"], "sw_level": ["L1"]})
        ctx.api.get_index_member_all = AsyncMock(side_effect=EngineDisposedError("disposed"))

        with patch.object(strategy, "_check_cancelled", return_value=False):
            with pytest.raises(EngineDisposedError):
                await strategy._sync_members(SyncResult(), classify_df)

    @pytest.mark.asyncio
    async def test_permission_denied_in_inner_loop_records_and_returns_early(self):
        """内层 TushareAPIPermissionError：记录 skipped_permission 并提前 return。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        classify_df = pd.DataFrame({"index_code": ["801010.SI", "801020.SI"], "sw_level": ["L1", "L1"]})
        ctx.api.get_index_member_all = AsyncMock(side_effect=TushareAPIPermissionError("index_member_all", "no perm"))

        with patch.object(strategy, "_check_cancelled", return_value=False):
            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        strategy._record_skipped_permission.assert_awaited_once_with("sw_industry_member")
        strategy.member_dao.save_sw_industry_member.assert_not_awaited()
        assert any("permission denied" in err for err in result.errors)

    @pytest.mark.asyncio
    async def test_generic_exception_in_inner_loop_continues_and_counts_error(self, caplog):
        """内层通用 Exception：errors 计数 +1，循环继续，最终 save 被跳过（无数据）。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        classify_df = pd.DataFrame({"index_code": ["801010.SI", "801020.SI"], "sw_level": ["L1", "L1"]})
        # 第一次抛错，第二次返回空 → all_dfs 空，触发 no data warning
        ctx.api.get_index_member_all = AsyncMock(side_effect=[RuntimeError("blip"), pd.DataFrame()])

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            await strategy._sync_members(SyncResult(), classify_df)

        # 两次调用都被执行（异常未中断循环）
        assert ctx.api.get_index_member_all.await_count == 2
        strategy.member_dao.save_sw_industry_member.assert_not_awaited()
        no_data_logs = [r for r in caplog.records if "No data fetched" in r.message]
        assert len(no_data_logs) == 1

    @pytest.mark.asyncio
    async def test_no_data_fetched_logs_warning(self, caplog):
        """所有 index_code 返回空：all_dfs 空，日志 warning，不调用 save。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        classify_df = pd.DataFrame({"index_code": ["801010.SI"], "sw_level": ["L1"]})
        ctx.api.get_index_member_all = AsyncMock(return_value=pd.DataFrame())

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            await strategy._sync_members(SyncResult(), classify_df)

        strategy.member_dao.save_sw_industry_member.assert_not_awaited()
        warning_logs = [r for r in caplog.records if "No data fetched" in r.message]
        assert len(warning_logs) == 1

    @pytest.mark.asyncio
    async def test_outer_exception_logs_warning_and_appends_error(self, caplog):
        """_sync_members 外层 Exception（如 save_sw_industry_member 抛错）：warning + errors。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx)
        # save_sw_industry_member 抛错 → 触发外层 except
        strategy.member_dao.save_sw_industry_member = AsyncMock(side_effect=RuntimeError("save failed"))
        classify_df = pd.DataFrame({"index_code": ["801010.SI"], "sw_level": ["L1"]})
        ctx.api.get_index_member_all = AsyncMock(return_value=_make_member_df("801010.SI"))

        with (
            patch.object(strategy, "_check_cancelled", return_value=False),
            caplog.at_level(logging.WARNING, logger="data.sync.sw_industry"),
        ):
            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert any("SwIndustry Members" in err for err in result.errors)
        # T29 标准化后：RuntimeError 默认归 operational → logger.error("Operational error ...")；
        # 测试同时接受 recoverable(warning) 与 operational(error) 两条分类路径。
        warning_logs = [
            r
            for r in caplog.records
            if "Members" in r.message
            and ("⚠️" in r.message or "Recoverable error" in r.message or "Operational error" in r.message)
        ]
        assert len(warning_logs) == 1


class TestSyncMembersPartialFailure:
    """S16：循环错误分支标记 partial + errors 记录。

    部分成员 fetch 失败时，status 必须置为 partial，errors 必须记录失败
    index_code，且成功数据仍正常 save（不中断循环）。
    """

    @pytest.mark.asyncio
    async def test_partial_failure_marks_status_partial_and_records_errors(self):
        """部分 index_code fetch 失败：status=partial，errors 记录失败 index_code，成功数据仍 save。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx, member_count=2)
        classify_df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI", "801030.SI"],
                "sw_level": ["L1", "L1", "L1"],
            }
        )
        # 第一、三个失败，第二个成功
        ctx.api.get_index_member_all = AsyncMock(
            side_effect=[RuntimeError("blip 1"), _make_member_df("801020.SI"), RuntimeError("blip 3")]
        )

        with patch.object(strategy, "_check_cancelled", return_value=False):
            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert result.status == SyncStatus.PARTIAL.value
        # 两条错误记录，分别包含两个失败的 index_code
        assert len(result.errors) == 2
        assert any("801010.SI" in err for err in result.errors)
        assert any("801030.SI" in err for err in result.errors)
        # 成功数据被 save
        strategy.member_dao.save_sw_industry_member.assert_awaited_once()
        assert result.added == 2

    @pytest.mark.asyncio
    async def test_single_failure_in_loop_marks_partial(self):
        """单个 index_code 失败 + 其余成功：status=partial，errors 恰好 1 条。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx, member_count=1)
        classify_df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI"],
                "sw_level": ["L1", "L1"],
            }
        )
        ctx.api.get_index_member_all = AsyncMock(side_effect=[RuntimeError("blip"), _make_member_df("801020.SI")])

        with patch.object(strategy, "_check_cancelled", return_value=False):
            result = SyncResult()
            await strategy._sync_members(result, classify_df)

        assert result.status == SyncStatus.PARTIAL.value
        assert len(result.errors) == 1
        assert "801010.SI" in result.errors[0]
        strategy.member_dao.save_sw_industry_member.assert_awaited_once()
        assert result.added == 1


class TestRecordSkippedPermission:
    """_record_skipped_permission：正常路径 + 异常吞没（debug 日志）。

    使用 mock_record_skipped=False 保留真实方法，避免 _wire_strategy 默认 mock。
    """

    @pytest.mark.asyncio
    async def test_calls_update_sync_status_with_skipped_permission(self):
        """正常路径：调用 cache.update_sync_status 写入 skipped_permission 状态。"""
        ctx = _make_ctx()
        strategy = _wire_strategy(ctx, mock_record_skipped=False)

        await strategy._record_skipped_permission("sw_industry_classify")

        ctx.cache.update_sync_status.assert_awaited_once()
        call_kwargs = ctx.cache.update_sync_status.call_args.kwargs
        assert call_kwargs["status"] == "skipped_permission"
        # SYNC_RESULT_SKIPPED_PERMISSION 常量为大写（data/constants.py）
        assert call_kwargs["last_result_status"] == "SKIPPED_PERMISSION"
        # 第一个位置参数是表名
        assert ctx.cache.update_sync_status.call_args.args[0] == "sw_industry_classify"

    @pytest.mark.asyncio
    async def test_exception_swallowed_and_logged_debug(self, caplog):
        """update_sync_status 抛错：debug 日志，不传播异常。"""
        ctx = _make_ctx()
        ctx.cache.update_sync_status = AsyncMock(side_effect=RuntimeError("db down"))
        strategy = _wire_strategy(ctx, mock_record_skipped=False)

        with caplog.at_level(logging.DEBUG, logger="data.sync.sw_industry"):
            # 不应抛异常
            await strategy._record_skipped_permission("sw_industry_member")

        debug_logs = [r for r in caplog.records if "Failed to record skipped_permission" in r.message]
        assert len(debug_logs) == 1
