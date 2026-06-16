import asyncio
import pytest
from datetime import date, datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from utils.scheduler_service import SchedulerService


def _make_svc():
    with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
    return svc


class TestSchedulerServiceInit:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_init_creates_scheduler(self, mock_ch):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        assert svc.scheduler is not None
        assert svc._last_update_date is None

    @patch("utils.scheduler_service.ConfigHandler")
    def test_init_reads_config(self, mock_ch):
        mock_ch.get_setting.side_effect = lambda k: "20240614" if "daily" in k else None
        svc = SchedulerService()
        assert svc._last_update_date == "20240614"


class TestSchedulerServiceMarkDone:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_mark_daily_update_done(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.save_config = MagicMock()
        svc = SchedulerService()
        svc._mark_daily_update_done("20240615")
        assert svc._last_update_date == "20240615"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_mark_nightly_prediction_done(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.save_config = MagicMock()
        svc = SchedulerService()
        svc._mark_nightly_prediction_done("20240615")
        assert svc._last_pred_date == "20240615"


class TestSchedulerServicePersistRunDate:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_persist(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.save_config = MagicMock()
        SchedulerService._persist_run_date("test_key", "20240615")
        mock_ch.save_config.assert_called_once()


class TestSchedulerServiceStop:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_stop_running(self, mock_ch):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = True
        svc.stop()
        svc.scheduler.shutdown.assert_called_once_with(wait=False)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_stop_not_running(self, mock_ch):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc.stop()
        svc.scheduler.shutdown.assert_not_called()


class TestSchedulerServiceOnJobEvents:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_on_job_missed(self, mock_ch):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        event = MagicMock()
        event.job_id = "test_job"
        event.scheduled_run_time = "2024-01-01"
        svc._on_job_missed(event)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_on_job_error_cancelled(self, mock_ch):
        import asyncio

        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        event = MagicMock()
        event.job_id = "test_job"
        event.exception = asyncio.CancelledError()
        svc._on_job_error(event)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_on_job_error_other(self, mock_ch):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        event = MagicMock()
        event.job_id = "test_job"
        event.exception = RuntimeError("test error")
        svc._on_job_error(event)


class TestSchedulerServiceCheckConfigSync:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_returns_dict(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "09:30"
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        mock_ch.is_doubao_schedule_enabled.return_value = False
        svc = SchedulerService()
        result = svc._check_config_sync()
        assert result["time"] == "09:30"
        assert result["enabled"] is True
        assert result["doubao_time"] == "10:00"
        assert result["doubao_enabled"] is False


class TestSchedulerServiceScheduleJobs:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_daily_update(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job is not None

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_nightly_prediction(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("nightly_prediction")
        assert job is not None

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_doubao_weekly(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("doubao_weekly_refresh")
        assert job is not None

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_invalid_time_defaults(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = None
        mock_ch.get_doubao_schedule_time.return_value = "invalid"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job is not None

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_removes_existing(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("daily_update") is not None


class TestSchedulerServiceWatchConfigChanges:
    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_first_call_sets_config(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value={
                "time": "09:30",
                "enabled": True,
                "doubao_time": "10:00",
                "doubao_enabled": False,
            }
        )
        svc = SchedulerService()
        await svc._watch_config_changes()
        assert svc._last_known_config["time"] == "09:30"

    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_config_change_triggers_reload(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value={
                "time": "10:00",
                "enabled": True,
                "doubao_time": "10:00",
                "doubao_enabled": False,
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

    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_no_change_no_reload(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        config = {"time": "09:30", "enabled": True, "doubao_time": "10:00", "doubao_enabled": False}
        mock_tpm_instance.run_async = AsyncMock(return_value=config)
        svc = SchedulerService()
        svc._last_known_config = config.copy()
        svc._schedule_jobs = MagicMock()
        await svc._watch_config_changes()
        svc._schedule_jobs.assert_not_called()


class TestSchedulerServiceGetStatus:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_get_status(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        svc = SchedulerService()
        status = svc.get_status()
        assert status["enabled"] is True
        assert status["scheduled_time"] == "16:30"
        assert "running" in status
        assert "last_update" in status
        assert "next_run" in status


class TestSchedulerServiceSingleton:
    def test_singleton_returns_same_instance(self):
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.get_setting.return_value = None
            svc1 = SchedulerService()
            svc2 = SchedulerService()
            assert svc1 is svc2

    def test_reset_singleton_allows_new_instance(self):
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.get_setting.return_value = None
            svc1 = SchedulerService()
            SchedulerService._reset_singleton()
            svc2 = SchedulerService()
            assert svc1 is not svc2

    def test_reset_singleton_shuts_down_running_scheduler(self):
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.get_setting.return_value = None
            svc = SchedulerService()
            type(svc.scheduler).running = PropertyMock(return_value=True)
            svc.scheduler.shutdown = MagicMock()
            SchedulerService._reset_singleton()
            svc.scheduler.shutdown.assert_called_once_with(wait=False)

    def test_reset_singleton_sets_instance_to_none(self):
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.get_setting.return_value = None
            SchedulerService()
            SchedulerService._reset_singleton()
            assert SchedulerService._instance is None


class TestGetStatusWithJob:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_status_with_next_run(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        svc = SchedulerService()
        mock_job = MagicMock()
        mock_job.next_run_time = datetime(2024, 6, 15, 16, 30)
        svc.scheduler.get_job = MagicMock(return_value=mock_job)
        status = svc.get_status()
        assert "2024" in status["next_run"]

    @patch("utils.scheduler_service.ConfigHandler")
    def test_status_no_job(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        svc = SchedulerService()
        svc.scheduler.get_job = MagicMock(return_value=None)
        status = svc.get_status()
        assert status["next_run"] == "N/A"


class TestRunDailyUpdate:
    @pytest.mark.asyncio
    async def test_disabled(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            await svc._run_daily_update()

    @pytest.mark.asyncio
    async def test_already_updated(self):
        svc = _make_svc()
        today_str = "20240615"
        svc._last_update_date = today_str
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_now_dt = MagicMock()
            mock_now_dt.date.return_value = date(2024, 6, 15)
            mock_now.return_value = mock_now_dt
            await svc._run_daily_update()

    @pytest.mark.asyncio
    async def test_not_trading_day(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=False)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await svc._run_daily_update()

    @pytest.mark.asyncio
    async def test_calendar_check_fails_weekend(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal error"))
            mock_dp.return_value = mock_dp_instance
            mock_now_dt = MagicMock()
            mock_now_dt.date.return_value = date(2024, 6, 15)
            mock_now_dt.weekday.return_value = 5
            mock_now.return_value = mock_now_dt
            await svc._run_daily_update()

    @pytest.mark.asyncio
    async def test_calendar_check_fails_weekday(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal error"))
            mock_dp.return_value = mock_dp_instance
            mock_now_dt = MagicMock()
            mock_now_dt.date.return_value = date(2024, 6, 15)
            mock_now_dt.weekday.return_value = 2
            mock_now.return_value = mock_now_dt
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_daily_update()
            mock_tm_instance.submit_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_trading_day_submits_task(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=True)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_daily_update()
            mock_tm_instance.submit_task.assert_called_once()


class TestRunDoubaoTagger:
    @pytest.mark.asyncio
    async def test_disabled(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.is_doubao_schedule_enabled.return_value = False
            await svc._run_doubao_tagger()

    @pytest.mark.asyncio
    async def test_already_done(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_doubao_schedule_enabled.return_value = True
            today_str = "20240615"
            mock_now.return_value.strftime.return_value = today_str
            svc._last_doubao_date = today_str
            await svc._run_doubao_tagger()

    @pytest.mark.asyncio
    async def test_submits_task(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_doubao_schedule_enabled.return_value = True
            mock_now.return_value.strftime.return_value = "20240615"
            svc._last_doubao_date = None
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_doubao_tagger()
            mock_tm_instance.submit_task.assert_called_once()


class TestRunNightlyPrediction:
    @pytest.mark.asyncio
    async def test_disabled(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            await svc._run_nightly_prediction()

    @pytest.mark.asyncio
    async def test_already_done(self):
        svc = _make_svc()
        today_str = "20240615"
        svc._last_pred_date = today_str
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await svc._run_nightly_prediction()

    @pytest.mark.asyncio
    async def test_not_trading_day(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=False)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await svc._run_nightly_prediction()

    @pytest.mark.asyncio
    async def test_calendar_check_fails_weekend(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
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
            await svc._run_nightly_prediction()

    @pytest.mark.asyncio
    async def test_trading_day_submits_task(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=True)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_nightly_prediction()
            mock_tm_instance.submit_task.assert_called_once()


class TestScheduleJobsInvalidTime:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_invalid_auto_update_time(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "invalid"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("daily_update") is not None

    @patch("utils.scheduler_service.ConfigHandler")
    def test_none_doubao_time(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = None
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("doubao_weekly_refresh") is not None


class TestSchedulerServiceStatus:
    def test_get_status_returns_dict(self):
        with (
            patch("utils.scheduler_service.ConfigHandler.is_auto_update_enabled", return_value=True),
            patch("utils.scheduler_service.ConfigHandler.get_auto_update_time", return_value="16:30"),
        ):
            svc = SchedulerService()
            status = svc.get_status()
            assert isinstance(status, dict)
            assert "enabled" in status
            assert "scheduled_time" in status
            assert "running" in status

    def test_get_status_enabled(self):
        with (
            patch("utils.scheduler_service.ConfigHandler.is_auto_update_enabled", return_value=True),
            patch("utils.scheduler_service.ConfigHandler.get_auto_update_time", return_value="16:30"),
        ):
            svc = SchedulerService()
            status = svc.get_status()
            assert status["enabled"] is True

    def test_get_status_disabled(self):
        with (
            patch("utils.scheduler_service.ConfigHandler.is_auto_update_enabled", return_value=False),
            patch("utils.scheduler_service.ConfigHandler.get_auto_update_time", return_value="16:30"),
        ):
            svc = SchedulerService()
            status = svc.get_status()
            assert status["enabled"] is False


class TestSchedulerStart:
    def test_start_already_running(self):
        svc = _make_svc()
        svc.scheduler = MagicMock()
        svc.scheduler.running = True
        svc.start()
        svc.scheduler.add_job.assert_not_called()

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_success(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc.start()
        svc.scheduler.start.assert_called_once()
        svc.scheduler.add_job.assert_called()

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_exception(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_doubao_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc.scheduler.start.side_effect = Exception("start error")
        svc.start()


class TestWatchConfigChangesMore:
    @pytest.mark.asyncio
    async def test_cancelled_error(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError):
                await svc._watch_config_changes()

    @pytest.mark.asyncio
    async def test_general_exception(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=Exception("tpm error"))
            await svc._watch_config_changes()

    @pytest.mark.asyncio
    async def test_enabled_change_triggers_reload(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(
                return_value={
                    "time": "09:30",
                    "enabled": False,
                    "doubao_time": "10:00",
                    "doubao_enabled": False,
                }
            )
            svc._last_known_config = {
                "time": "09:30",
                "enabled": True,
                "doubao_time": "10:00",
                "doubao_enabled": False,
            }
            svc._schedule_jobs = MagicMock()
            await svc._watch_config_changes()
            svc._schedule_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_doubao_change_triggers_reload(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ThreadPoolManager") as mock_tpm:
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
            svc._last_known_config = {
                "time": "09:30",
                "enabled": True,
                "doubao_time": "10:00",
                "doubao_enabled": False,
            }
            svc._schedule_jobs = MagicMock()
            await svc._watch_config_changes()
            svc._schedule_jobs.assert_called_once()
