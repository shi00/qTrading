import pytest
import asyncio
import datetime
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from utils.scheduler_service import SchedulerService


@pytest.fixture(autouse=True)
def reset_singletons():
    SchedulerService._reset_singleton()
    yield
    SchedulerService._reset_singleton()


def _make_svc():
    with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
    return svc


class TestStopNotRunning:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_stop_logs_not_running(self, mock_ch):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc.stop()
        svc.scheduler.shutdown.assert_not_called()


class TestStartDeep:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_already_running(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = True
        svc.start()
        svc.scheduler.add_job.assert_not_called()

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_adds_listeners(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc._schedule_jobs = MagicMock()
        svc.start()
        assert svc.scheduler.add_listener.call_count >= 2

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_exception(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc._schedule_jobs = MagicMock()
        svc.scheduler.start.side_effect = Exception("start fail")
        svc.start()


class TestWatchConfigChangesDeep:
    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_cancelled_error(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        svc = SchedulerService()
        with pytest.raises(asyncio.CancelledError):
            await svc._watch_config_changes()

    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_general_exception(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(side_effect=RuntimeError("err"))
        svc = SchedulerService()
        await svc._watch_config_changes()

    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_doubao_config_change(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value={
                "time": "09:30",
                "enabled": True,
                "doubao_time": "11:00",
                "doubao_enabled": True,
            }
        )
        svc = SchedulerService()
        svc._last_known_config = {
            "time": "09:30",
            "enabled": True,
            "doubao_time": "10:00",
            "doubao_enabled": False,
        }
        svc._schedule_jobs = MagicMock()
        await svc._watch_config_changes()
        svc._schedule_jobs.assert_called_once()


def _get_patches(mock_dp, mock_tm, now_val):
    return (
        patch("utils.scheduler_service.ConfigHandler"),
        patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
        patch("utils.scheduler_service.get_now", return_value=now_val),
        patch("services.task_manager.TaskManager", return_value=mock_tm),
    )


class TestDailyUpdateLogicClosure:
    @pytest.mark.asyncio
    async def test_daily_update_logic_with_result_none(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=None)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_daily_update_logic_with_sync_result_added(self):
        svc = _make_svc()
        mock_result = MagicMock()
        mock_result.errors = []
        mock_result.added = 42
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_daily_update_logic_with_errors_not_marked(self):
        svc = _make_svc()
        mock_result = MagicMock()
        mock_result.errors = ["some error"]
        mock_result.added = 10
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            await factory("test_task")
            assert svc._last_update_date != "20240614"

    @pytest.mark.asyncio
    async def test_daily_update_logic_dataframe_result(self):
        import pandas as pd

        svc = _make_svc()
        mock_result = pd.DataFrame({"ts_code": ["000001.SZ"]})
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_daily_update_logic_empty_dataframe(self):
        import pandas as pd

        svc = _make_svc()
        mock_result = pd.DataFrame()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_daily_update_logic_int_result(self):
        svc = _make_svc()
        mock_result = 15
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)


class TestDoubaoLogicClosure:
    @pytest.mark.asyncio
    async def test_doubao_logic_closure(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.run_doubao_tagging = AsyncMock()
        mock_tm = MagicMock()
        mock_task = MagicMock()
        mock_task._cancel_event = MagicMock()
        mock_tm.get_task.return_value = mock_task
        now_val = datetime.datetime(2024, 6, 15, 10, 0)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_doubao_schedule_enabled.return_value = True
            await svc._run_doubao_tagger()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)


class TestNightlyPredictionLogicClosure:
    @pytest.mark.asyncio
    async def test_prediction_logic_with_empty_result(self):
        import pandas as pd

        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.init_data = AsyncMock()
        mock_dp.prepare_market_data = AsyncMock()
        mock_dp.get_strategy_data = AsyncMock(return_value={"trade_date": "20240614"})
        mock_strategy = MagicMock()
        mock_strategy.filter = AsyncMock(return_value=pd.DataFrame())
        mock_tm = MagicMock()
        mock_rm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("utils.scheduler_service.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_prediction_logic_with_results(self):
        import pandas as pd

        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.init_data = AsyncMock()
        mock_dp.prepare_market_data = AsyncMock()
        mock_dp.get_strategy_data = AsyncMock(return_value={"trade_date": "20240614"})
        mock_strategy = MagicMock()
        result_df = pd.DataFrame({"ts_code": ["000001.SZ"], "score": [80]})
        mock_strategy.filter = AsyncMock(return_value=result_df)
        mock_tm = MagicMock()
        mock_rm = MagicMock()
        mock_rm.save_results = AsyncMock()
        now_val = datetime.datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("utils.scheduler_service.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_prediction_logic_no_context_raises(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.init_data = AsyncMock()
        mock_dp.prepare_market_data = AsyncMock()
        mock_dp.get_strategy_data = AsyncMock(return_value=None)
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            with pytest.raises(RuntimeError):
                await factory("test_task")

    @pytest.mark.asyncio
    async def test_prediction_logic_no_trade_date_raises(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.init_data = AsyncMock()
        mock_dp.prepare_market_data = AsyncMock()
        mock_dp.get_strategy_data = AsyncMock(return_value={})
        mock_strategy = MagicMock()
        result_df = pd.DataFrame({"ts_code": ["000001.SZ"], "score": [80]})
        mock_strategy.filter = AsyncMock(return_value=result_df)
        mock_tm = MagicMock()
        mock_rm = MagicMock()
        now_val = datetime.datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("utils.scheduler_service.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            with pytest.raises(RuntimeError):
                await factory("test_task")

    @pytest.mark.asyncio
    async def test_prediction_calendar_fails_weekday(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal err"))
        mock_tm = MagicMock()
        now_val = datetime.datetime(2024, 6, 12, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            mock_tm.submit_task.assert_called_once()
