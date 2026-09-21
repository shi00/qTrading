"""UX-03 选股参数影响反馈 + 空态原因单元测试。

验证 ScreenerViewModel 在 run_strategy 后的两个 UX-03 行为（分支于
``ui/viewmodels/ai_stream_mixin.py``）:
- 筛选结果非空且较候选池明显收窄时，向 D3-4 警告通道追加
  ``screener_param_impact``（候选从 N 收窄至 M），暴露参数实际效力；
- 有候选数据但筛选后无匹配时，在结果区空态设置可操作原因
  ``screener_nomatch``（共考虑 N 只候选，可调低条件），区分「无匹配」与「无数据」。

View 层单位拼接（``screener_view._UNIT_SUFFIX_I18N_KEYS``）与 empty_state 渲染
属薄渲染逻辑，另行由 ``test_screener_view*.py`` 覆盖，本文件聚焦 VM 状态契约。
"""

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from core.i18n import Message
from ui.viewmodels.screener_view_model import ScreenerViewModel

pytestmark = pytest.mark.unit


@pytest.fixture
def vm():
    """ScreenerViewModel with mocked dependencies（与 test_screener_view_model 同构）。"""
    with (
        patch("ui.viewmodels.screener_view_model.DataProcessor") as mock_dp_cls,
        patch("ui.viewmodels.screener_view_model.StrategyManager"),
        patch("ui.viewmodels.screener_view_model.ReviewManager"),
    ):
        vm = ScreenerViewModel()
        vm.data_processor = mock_dp_cls.return_value
        return vm


def _build_sync_submit_task_holder():
    """submit_task 同步调度 coro_factory 并通过 holder 暴露 task 供后续 await。"""
    holder = type("Holder", (), {"task": None})()

    def _sync_submit_task(*args, **kwargs):
        coro_factory = kwargs["coroutine_factory"]
        coro = coro_factory(task_id="test-task-id")
        holder.task = asyncio.ensure_future(coro)
        return "test-task-id"

    return holder, _sync_submit_task


def _wire(vm, *, screening_count: int, result_rows: list):
    """配置 mock 策略 + data_processor + save_results 成功。"""
    strategy = type("MockStrategy", (), {})()
    strategy.name_key = "strategy_test"
    strategy.filter = lambda ctx: pd.DataFrame(result_rows)
    vm.strategy_mgr.get_strategy.return_value = strategy
    vm.data_processor.get_strategy_data = AsyncMock(
        return_value={
            "screening_data": pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(screening_count)]}),
            "trade_date": dt.date(2026, 7, 29),
        },
    )
    vm.review_mgr.save_results = AsyncMock(return_value=None)


class TestUX03ParamImpact:
    """筛选结果较候选池收窄时的影响反馈横幅。"""

    @pytest.mark.asyncio
    async def test_param_impact_added_when_narrowed(self, vm):
        """结果非空且较候选池收窄 (3→2) 时, warnings 含 screener_param_impact。"""
        from services.task_manager import TaskManager

        _wire(
            vm,
            screening_count=3,
            result_rows=[
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {"ts_code": "000002.SZ", "name": "万科A"},
            ],
        )
        holder, _sync_submit = _build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        keys = [m.key for m in vm.state.warnings]
        assert "screener_param_impact" in keys
        impact = next(m for m in vm.state.warnings if m.key == "screener_param_impact")
        assert impact.params["before"] == 3
        assert impact.params["after"] == 2
        # 成功路径不设空态原因 (仅空结果时设置)
        assert vm.state.empty_message is None

    @pytest.mark.asyncio
    async def test_param_impact_absent_when_no_narrow(self, vm):
        """结果行数不少于候选池 (未明显收窄) 时, 不追加影响反馈横幅。"""
        from services.task_manager import TaskManager

        _wire(
            vm,
            screening_count=2,
            result_rows=[
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {"ts_code": "000002.SZ", "name": "万科A"},
            ],
        )
        holder, _sync_submit = _build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        assert all(m.key != "screener_param_impact" for m in vm.state.warnings), "未收窄时应不产出影响反馈横幅"


class TestUX03EmptyReason:
    """有候选但筛选后无匹配时的空态原因 (区分「无匹配」与「无数据」)。"""

    @pytest.mark.asyncio
    async def test_no_match_sets_empty_message_with_total(self, vm):
        """有候选数据 (3 只) 但筛选无匹配时, empty_message 为 screener_nomatch(total=3)。"""
        from services.task_manager import TaskManager

        _wire(vm, screening_count=3, result_rows=[])
        holder, _sync_submit = _build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        assert vm.state.empty_message is not None
        assert vm.state.empty_message.key == "screener_nomatch"
        assert vm.state.empty_message.params["total"] == 3
        # 空结果路径 status 仍为无结果提示, 非误报成功
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "screener_no_results"

    @pytest.mark.asyncio
    async def test_no_match_records_empty_full_results(self, vm):
        """无匹配时结果区落空切片 (不残留上一轮结果)。"""
        from services.task_manager import TaskManager

        _wire(vm, screening_count=1, result_rows=[])
        holder, _sync_submit = _build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        assert vm._full_results is not None
        assert vm._full_results.empty


class TestUX03DataMissingEmptyReason:
    """DS-04: 基本面数据缺失导致的空结果（守卫拦截）抑制「无匹配」空态提示。

    策略在 fundamental 覆盖率不足时设 ``_empty_reason=fundamental_data_missing``
    并产出 ``strategy_fundamental_data_missing`` 警告；VM 此时不得再叠加
    「共考虑 N 只候选可调低条件」（指引与事实相反，实际缺财务数据）。
    """

    @staticmethod
    def _wire_missing(vm, *, screening_count: int):
        """Mock 策略模拟守卫拦截：设数据缺失原因 + 警告，返回空结果。"""
        strategy = type("MockStrategy", (), {})()
        strategy.name_key = "strategy_test"

        def _filter(ctx):
            ctx["_empty_reason"] = "fundamental_data_missing"
            ctx["warnings"] = [Message("strategy_fundamental_data_missing", {"coverage": 0.0})]
            return pd.DataFrame()

        strategy.filter = _filter
        vm.strategy_mgr.get_strategy.return_value = strategy
        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(screening_count)]}),
                "trade_date": dt.date(2026, 7, 29),
            },
        )
        vm.review_mgr.save_results = AsyncMock(return_value=None)

    @pytest.mark.asyncio
    async def test_data_missing_suppresses_nomatch_even_with_candidates(self, vm):
        """候选池非空(3 只)但数据缺失被守卫拦截 → 不设「无匹配」空态提示。"""
        from services.task_manager import TaskManager

        self._wire_missing(vm, screening_count=3)
        holder, _sync_submit = _build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        assert vm.state.empty_message is None
        keys = [m.key for m in vm.state.warnings]
        assert "strategy_fundamental_data_missing" in keys


class TestUX03ModeSwitchContext:
    """UX-03 上下文随 REALTIME/HISTORY 切换正确清空/恢复 (防残留污染 HISTORY)。"""

    @staticmethod
    async def _run_no_match(vm):
        """驱动 run_strategy 至「无匹配」空态, 设置 empty_message。"""
        from services.task_manager import TaskManager

        _wire(vm, screening_count=3, result_rows=[])
        holder, _sync_submit = _build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

    @pytest.mark.asyncio
    async def test_switch_to_history_clears_empty_message(self, vm):
        """无匹配后切 HISTORY: empty_message/warnings 清空, 快照留存供切回恢复。"""
        await self._run_no_match(vm)
        assert vm.state.empty_message is not None

        vm.switch_to_history()

        assert vm.state.mode == "HISTORY"
        assert vm.state.empty_message is None
        assert vm.state.warnings == ()
        # 快照留存原空态原因, 供切回实时态恢复
        assert vm._realtime_snapshot is not None
        assert vm._realtime_snapshot.empty_message is not None

    @pytest.mark.asyncio
    async def test_switch_back_realtime_restores_empty_message(self, vm):
        """切回 REALTIME 时恢复快照中的空态原因, 不丢失实时筛选上下文。"""
        await self._run_no_match(vm)

        vm.switch_to_history()
        assert vm.state.empty_message is None

        vm.switch_to_realtime()

        assert vm.state.mode == "REALTIME"
        assert vm.state.empty_message is not None
        assert vm.state.empty_message.key == "screener_nomatch"
