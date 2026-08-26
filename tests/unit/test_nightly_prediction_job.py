# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""services/scheduled_jobs/nightly_prediction 单元测试（review01-A2-1 下沉）。

原 SchedulerService._run_nightly_prediction / _prediction_logic 的业务测试迁移至此，
patch 目标从 ``utils.scheduler_service.*`` 改为 ``services.scheduled_jobs.nightly_prediction.*``。
AI 策略执行经 ``AISelectionRunner`` 注入（app 层），测试用替身 runner 验证编排行为。
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.i18n import Message
from services.scheduled_jobs.nightly_prediction import build_nightly_prediction_job

pytestmark = pytest.mark.unit

# 区分"未传 get_strategy_data"与"显式传 None（无上下文场景）"
_NO_DATA = object()


class _FakeSvc:
    """最小 SchedulerService 替身：提供 idempotency 状态与标记接口。"""

    def __init__(self) -> None:
        self._last_pred_date: str | None = None
        self.marked_dates: list[str] = []

    async def _mark_nightly_prediction_done_db(self, today_str: str) -> None:
        self.marked_dates.append(today_str)


def _make_job(svc: _FakeSvc, runner=None):
    """构造 job（注入 runner），返回可调用 job。"""
    if runner is None:
        runner = AsyncMock(return_value=pd.DataFrame())
    return build_nightly_prediction_job(runner), runner


class TestRunNightlyPrediction:
    @pytest.mark.asyncio
    async def test_disabled(self):
        svc = _FakeSvc()
        job, _ = _make_job(svc)
        with patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            await job(svc)

    @pytest.mark.asyncio
    async def test_already_done(self):
        svc = _FakeSvc()
        svc._last_pred_date = "20240615"
        job, _ = _make_job(svc)
        with (
            patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch,
            patch("services.scheduled_jobs.nightly_prediction.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await job(svc)

    @pytest.mark.asyncio
    async def test_not_trading_day(self):
        svc = _FakeSvc()
        job, _ = _make_job(svc)
        with (
            patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch,
            patch("services.scheduled_jobs.nightly_prediction.DataProcessor") as mock_dp,
            patch("services.scheduled_jobs.nightly_prediction.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=False)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await job(svc)

    @pytest.mark.asyncio
    async def test_calendar_check_fails_weekend(self):
        svc = _FakeSvc()
        job, _ = _make_job(svc)
        with (
            patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch,
            patch("services.scheduled_jobs.nightly_prediction.DataProcessor") as mock_dp,
            patch("services.scheduled_jobs.nightly_prediction.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal error"))
            mock_dp.return_value = mock_dp_instance
            mock_now_dt = MagicMock()
            mock_now_dt.date.return_value = date(2024, 6, 15)
            mock_now_dt.weekday.return_value = 6
            mock_now.return_value = mock_now_dt
            await job(svc)

    @pytest.mark.asyncio
    async def test_trading_day_submits_task(self):
        svc = _FakeSvc()
        job, _ = _make_job(svc)
        with (
            patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch,
            patch("services.scheduled_jobs.nightly_prediction.DataProcessor") as mock_dp,
            patch("services.scheduled_jobs.nightly_prediction.get_now") as mock_now,
            patch("services.scheduled_jobs.nightly_prediction.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=True)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await job(svc)
            # 强断言：提交任务的关键参数（unique_key 去重 + cancellable）
            submit_kwargs = mock_tm_instance.submit_task.call_args.kwargs
            assert submit_kwargs.get("unique_key") == "nightly_prediction"
            assert submit_kwargs.get("cancellable") is False
            assert callable(submit_kwargs.get("coroutine_factory"))


class TestNightlyPredictionLogicClosure:
    """验证 _prediction_logic 编排：数据准备 → runner.run → ReviewManager.save_results。"""

    async def _execute_logic(
        self,
        svc: _FakeSvc,
        runner,
        mock_tm: MagicMock,
        *,
        get_strategy_data=_NO_DATA,
        mock_rm: MagicMock | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        """在完整 patch 上下文中执行 job 并 await factory（patch 须覆盖 factory 执行）。"""
        if mock_rm is None:
            mock_rm = MagicMock()
        if get_strategy_data is _NO_DATA:
            get_strategy_data = {"trade_date": "20240614"}
        job = build_nightly_prediction_job(runner)
        with (
            patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch,
            patch("services.scheduled_jobs.nightly_prediction.DataProcessor") as mock_dp,
            patch("services.scheduled_jobs.nightly_prediction.get_now") as mock_now,
            patch("services.scheduled_jobs.nightly_prediction.TaskManager", return_value=mock_tm),
            patch("services.scheduled_jobs.nightly_prediction.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=True)
            mock_dp_instance.init_data = AsyncMock()
            mock_dp_instance.prepare_market_data = AsyncMock()
            mock_dp_instance.get_strategy_data = AsyncMock(return_value=get_strategy_data)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value = datetime(2024, 6, 14, 20, 30)
            await job(svc)
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            await factory("test_task")
        return mock_tm, mock_rm

    @pytest.mark.asyncio
    async def test_prediction_logic_with_empty_result(self):
        svc = _FakeSvc()
        mock_tm = MagicMock()
        runner = AsyncMock(return_value=pd.DataFrame())
        await self._execute_logic(svc, runner, mock_tm)
        # 空结果 → 不标记 done（允许重试）
        assert svc.marked_dates == []

    @pytest.mark.asyncio
    async def test_prediction_logic_with_results(self):
        svc = _FakeSvc()
        mock_tm = MagicMock()
        result_df = pd.DataFrame({"ts_code": ["000001.SZ"], "score": [80]})
        runner = AsyncMock(return_value=result_df)
        mock_rm = MagicMock()
        mock_rm.save_results = AsyncMock()
        await self._execute_logic(svc, runner, mock_tm, mock_rm=mock_rm)
        # 强断言：保存次数 + 保存的策略名 + 标记今日完成
        assert mock_rm.save_results.call_count == 1
        assert mock_rm.save_results.call_args.args[0] == "strategy_ai_nightly_name"
        assert svc.marked_dates == ["20240614"]

    @pytest.mark.asyncio
    async def test_scheduler_stores_i18n_key(self):
        """R.3.1: nightly_prediction 应存储 "strategy_ai_nightly_name" (i18n key) 而非 identifier。"""
        svc = _FakeSvc()
        mock_tm = MagicMock()
        result_df = pd.DataFrame({"ts_code": ["000001.SZ"], "score": [80]})
        runner = AsyncMock(return_value=result_df)
        mock_rm = MagicMock()
        mock_rm.save_results = AsyncMock()
        await self._execute_logic(svc, runner, mock_tm, mock_rm=mock_rm)

        assert mock_rm.save_results.call_count == 1
        stored_strategy_name = mock_rm.save_results.call_args.args[0]
        assert stored_strategy_name == "strategy_ai_nightly_name"
        assert stored_strategy_name != "AI_Auto_Nightly"

    @pytest.mark.asyncio
    async def test_nightly_prediction_ai_progress_forwards_message(self):
        """D7(E4): _ai_progress 将 runner 的 Message 透传给 update_progress，不拼接前缀。"""
        svc = _FakeSvc()
        mock_tm = MagicMock()
        result_df = pd.DataFrame({"ts_code": ["000001.SZ"], "score": [80]})

        async def _runner_with_progress(context):
            on_progress = context.get("on_progress")
            if on_progress:
                on_progress(50, 100, Message("ai_progress_done", {"done": 50, "total": 100}))
            return result_df

        mock_rm = MagicMock()
        mock_rm.save_results = AsyncMock()
        await self._execute_logic(svc, _runner_with_progress, mock_tm, mock_rm=mock_rm)

        ai_calls = [c for c in mock_tm.update_progress.call_args_list if isinstance(c.args[2], Message)]
        assert any(c.args[2].key == "ai_progress_done" and c.args[1] == 0.7 for c in ai_calls)

    @pytest.mark.asyncio
    async def test_prediction_logic_no_context_raises(self):
        svc = _FakeSvc()
        mock_tm = MagicMock()
        runner = AsyncMock(return_value=pd.DataFrame())
        with pytest.raises(RuntimeError) as exc_info:
            await self._execute_logic(svc, runner, mock_tm, get_strategy_data=None)
        # as exc_info + 后续断言：异常已抛出（i18n 消息不硬编码）+ 未标记完成
        assert exc_info.value is not None
        assert svc.marked_dates == []

    @pytest.mark.asyncio
    async def test_prediction_logic_no_trade_date_raises(self):
        svc = _FakeSvc()
        mock_tm = MagicMock()
        result_df = pd.DataFrame({"ts_code": ["000001.SZ"], "score": [80]})
        runner = AsyncMock(return_value=result_df)
        # 非空 context（避免触发 no_context 分支）但缺 trade_date → 应拒绝保存
        with pytest.raises(RuntimeError, match="missing trade_date"):
            await self._execute_logic(
                svc,
                runner,
                mock_tm,
                get_strategy_data={"screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]})},
            )

    @pytest.mark.asyncio
    async def test_prediction_calendar_fails_weekday(self):
        svc = _FakeSvc()
        job, _ = _make_job(svc)
        mock_tm = MagicMock()
        with (
            patch("services.scheduled_jobs.nightly_prediction.ConfigHandler") as mock_ch,
            patch("services.scheduled_jobs.nightly_prediction.DataProcessor") as mock_dp,
            patch("services.scheduled_jobs.nightly_prediction.get_now") as mock_now,
            patch("services.scheduled_jobs.nightly_prediction.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal err"))
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value = datetime(2024, 6, 12, 20, 30)
            await job(svc)
            # 强断言：交易日检查失败（工作日）仍提交任务
            submit_kwargs = mock_tm.submit_task.call_args.kwargs
            assert submit_kwargs.get("unique_key") == "nightly_prediction"
            assert callable(submit_kwargs.get("coroutine_factory"))
