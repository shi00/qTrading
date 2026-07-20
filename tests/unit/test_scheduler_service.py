# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import pytest
import pandas as pd
from datetime import date, datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from utils.scheduler_service import SchedulerService

pytestmark = pytest.mark.unit


def _make_svc():
    with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
    return svc


def _get_patches(mock_dp, mock_tm, now_val):
    return (
        patch("utils.scheduler_service.ConfigHandler"),
        patch("data.data_processor.DataProcessor", return_value=mock_dp),
        patch("utils.scheduler_service.get_now", return_value=now_val),
        patch("services.task_manager.TaskManager", return_value=mock_tm),
    )


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
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        with patch("utils.scheduler_service.asyncio.get_running_loop", return_value=mock_loop):
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
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        mock_ch.is_ai_concept_schedule_enabled.return_value = False
        svc = SchedulerService()
        result = svc._check_config_sync()
        assert result["time"] == "09:30"
        assert result["enabled"] is True
        assert result["ai_concept_time"] == "10:00"
        assert result["ai_concept_enabled"] is False


class TestSchedulerServiceScheduleJobs:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_daily_update(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job.id == "daily_update"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_nightly_prediction(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("nightly_prediction")
        assert job.id == "nightly_prediction"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_ai_concept_daily(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("ai_concept_daily_refresh")
        assert job is not None
        # Trigger must be daily: str(trigger) must NOT restrict day_of_week to a specific day (e.g. sat)
        trigger_str = str(job.trigger)
        assert "sat" not in trigger_str.lower()
        # Hour must match configured value (10:00)
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        assert "10" in str(hour_field)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_invalid_time_defaults(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = None
        mock_ch.get_ai_concept_schedule_time.return_value = "invalid"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job.id == "daily_update"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_removes_existing(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job.id == "daily_update"


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
                "ai_concept_time": "10:00",
                "ai_concept_enabled": False,
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
                "ai_concept_time": "10:00",
                "ai_concept_enabled": False,
            }
        )
        svc = SchedulerService()
        svc._last_known_config = {
            "time": "09:30",
            "enabled": True,
            "ai_concept_time": "10:00",
            "ai_concept_enabled": False,
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
        config = {
            "time": "09:30",
            "enabled": True,
            "ai_concept_time": "10:00",
            "ai_concept_enabled": False,
        }
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
            mock_loop = MagicMock()
            mock_loop.is_closed.return_value = False
            with patch("utils.scheduler_service.asyncio.get_running_loop", return_value=mock_loop):
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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


class TestRunAiConceptTagger:
    @pytest.mark.asyncio
    async def test_disabled(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            await svc._run_ai_concept_tagger()

    @pytest.mark.asyncio
    async def test_already_done(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now") as mock_now,
        ):
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            today_str = "20240615"
            mock_now.return_value.strftime.return_value = today_str
            svc._last_ai_concept_date = today_str
            await svc._run_ai_concept_tagger()

    @pytest.mark.asyncio
    async def test_submits_task(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            mock_now.return_value.strftime.return_value = "20240615"
            svc._last_ai_concept_date = None
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_ai_concept_tagger()
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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
            patch("data.data_processor.DataProcessor") as mock_dp,
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
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("daily_update") is not None  # noqa: weak-assertion APScheduler job 注册存在性，trigger 配置由专项测试覆盖

    @patch("utils.scheduler_service.ConfigHandler")
    def test_none_ai_concept_time(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = None
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("ai_concept_daily_refresh") is not None  # noqa: weak-assertion APScheduler job 注册存在性，trigger 配置由专项测试覆盖


class TestSchedulerServiceStatus:
    def test_get_status_returns_dict(self):
        with (
            patch(
                "utils.scheduler_service.ConfigHandler.is_auto_update_enabled",
                return_value=True,
            ),
            patch(
                "utils.scheduler_service.ConfigHandler.get_auto_update_time",
                return_value="16:30",
            ),
        ):
            svc = SchedulerService()
            status = svc.get_status()
            assert isinstance(status, dict)
            assert "enabled" in status
            assert "scheduled_time" in status
            assert "running" in status

    def test_get_status_enabled(self):
        with (
            patch(
                "utils.scheduler_service.ConfigHandler.is_auto_update_enabled",
                return_value=True,
            ),
            patch(
                "utils.scheduler_service.ConfigHandler.get_auto_update_time",
                return_value="16:30",
            ),
        ):
            svc = SchedulerService()
            status = svc.get_status()
            assert status["enabled"] is True

    def test_get_status_disabled(self):
        with (
            patch(
                "utils.scheduler_service.ConfigHandler.is_auto_update_enabled",
                return_value=False,
            ),
            patch(
                "utils.scheduler_service.ConfigHandler.get_auto_update_time",
                return_value="16:30",
            ),
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
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc.start()
        svc.scheduler.start.assert_called_once()
        assert svc.scheduler.add_job.call_count == 5  # 多次调用预期 (3 schedule_jobs + config_watchdog + load_db_state)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_exception(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
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
                    "ai_concept_time": "10:00",
                    "ai_concept_enabled": False,
                }
            )
            svc._last_known_config = {
                "time": "09:30",
                "enabled": True,
                "ai_concept_time": "10:00",
                "ai_concept_enabled": False,
            }
            svc._schedule_jobs = MagicMock()
            await svc._watch_config_changes()
            svc._schedule_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_ai_concept_change_triggers_reload(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(
                return_value={
                    "time": "09:30",
                    "enabled": True,
                    "ai_concept_time": "11:00",
                    "ai_concept_enabled": True,
                }
            )
            svc._last_known_config = {
                "time": "09:30",
                "enabled": True,
                "ai_concept_time": "10:00",
                "ai_concept_enabled": False,
            }
            svc._schedule_jobs = MagicMock()
            await svc._watch_config_changes()
            svc._schedule_jobs.assert_called_once()


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
    def test_start_adds_listeners(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc._schedule_jobs = MagicMock()
        svc.start()
        assert svc.scheduler.add_listener.call_count >= 2


class TestWatchConfigChangesDeep:
    @pytest.mark.asyncio
    @patch("utils.scheduler_service.ConfigHandler")
    @patch("utils.scheduler_service.ThreadPoolManager")
    async def test_ai_concept_config_change(self, mock_tpm, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_tpm_instance = MagicMock()
        mock_tpm.return_value = mock_tpm_instance
        mock_tpm_instance.run_async = AsyncMock(
            return_value={
                "time": "09:30",
                "enabled": True,
                "ai_concept_time": "11:00",
                "ai_concept_enabled": True,
            }
        )
        svc = SchedulerService()
        svc._last_known_config = {
            "time": "09:30",
            "enabled": True,
            "ai_concept_time": "10:00",
            "ai_concept_enabled": False,
        }
        svc._schedule_jobs = MagicMock()
        await svc._watch_config_changes()
        svc._schedule_jobs.assert_called_once()


class TestDailyUpdateLogicClosure:
    @pytest.mark.asyncio
    async def test_daily_update_logic_with_result_none(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=None)
        mock_tm = MagicMock()
        now_val = datetime(2024, 6, 14, 16, 30)

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
        now_val = datetime(2024, 6, 14, 16, 30)

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
        now_val = datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            await factory("test_task")
            assert svc._last_update_date != "20240614"

    @pytest.mark.asyncio
    async def test_daily_update_logic_dataframe_result(self):
        svc = _make_svc()
        mock_result = pd.DataFrame({"ts_code": ["000001.SZ"]})
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_daily_update_logic_empty_dataframe(self):
        svc = _make_svc()
        mock_result = pd.DataFrame()
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime(2024, 6, 14, 16, 30)

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
        now_val = datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with patches[0] as mock_ch, patches[1], patches[2], patches[3]:
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)


class TestAiConceptLogicClosure:
    @pytest.mark.asyncio
    async def test_ai_concept_logic_closure(self):
        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.run_ai_concept_tagging = AsyncMock()
        mock_tm = MagicMock()
        sentinel_cancel_event = MagicMock()
        mock_tm.get_cancel_event.return_value = sentinel_cancel_event
        now_val = datetime(2024, 6, 15, 10, 0)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            await svc._run_ai_concept_tagger()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)
            # 验证通过 get_cancel_event 访问器获取取消事件（而非穿透 _cancel_event）
            mock_tm.get_cancel_event.assert_called_once_with("test_task")
            # 验证 manual_trigger=False（调度场景不调用 LLM）
            mock_dp.run_ai_concept_tagging.assert_called_once()
            call_kwargs = mock_dp.run_ai_concept_tagging.call_args.kwargs
            assert call_kwargs.get("manual_trigger") is False
            # 验证 cancel_event 被正确传递给 run_ai_concept_tagging（P0-2 取消链路）
            assert call_kwargs.get("cancel_event") is sentinel_cancel_event

    @pytest.mark.asyncio
    async def test_t8_update_progress_false_raises_cancelled(self):
        """T8 fix: update_progress 返回 False 时立即 raise CancelledError 早退。"""
        import asyncio

        svc = _make_svc()
        mock_dp = MagicMock()
        mock_dp.run_ai_concept_tagging = AsyncMock()
        mock_tm = MagicMock()
        mock_tm.get_cancel_event.return_value = MagicMock()
        mock_tm.update_progress = MagicMock(return_value=False)  # 模拟任务已取消
        now_val = datetime(2024, 6, 15, 10, 0)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            await svc._run_ai_concept_tagger()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            with pytest.raises(asyncio.CancelledError):
                await factory("test_task")
            # 验证后续的 run_ai_concept_tagging 未执行（早退生效）
            mock_dp.run_ai_concept_tagging.assert_not_called()


class TestNightlyPredictionLogicClosure:
    @pytest.mark.asyncio
    async def test_prediction_logic_with_empty_result(self):
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
        now_val = datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("data.persistence.review_manager.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_prediction_logic_with_results(self):
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
        now_val = datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("data.persistence.review_manager.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            result_msg = await factory("test_task")
            assert isinstance(result_msg, str)

    @pytest.mark.asyncio
    async def test_scheduler_stores_i18n_key(self):
        """R.3.1: nightly_prediction 应存储 "strategy_ai_nightly_name" (i18n key) 而非 "AI_Auto_Nightly" identifier。"""
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
        now_val = datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("data.persistence.review_manager.ReviewManager", return_value=mock_rm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            await factory("test_task")

        mock_rm.save_results.assert_called_once()
        stored_strategy_name = mock_rm.save_results.call_args.args[0]
        assert stored_strategy_name == "strategy_ai_nightly_name"
        # 不应等于旧 identifier
        assert stored_strategy_name != "AI_Auto_Nightly"

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
        now_val = datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
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
        now_val = datetime(2024, 6, 14, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("strategies.ai_strategy.AISelectionStrategy", return_value=mock_strategy),
            patch("data.persistence.review_manager.ReviewManager", return_value=mock_rm),
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
        now_val = datetime(2024, 6, 12, 20, 30)

        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("utils.scheduler_service.get_now", return_value=now_val),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_nightly_prediction()
            mock_tm.submit_task.assert_called_once()
