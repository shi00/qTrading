# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import pytest
import pandas as pd
from datetime import date, datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from data.sync.base import SyncResult
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


async def _dummy_job(svc):
    """D6-6: 满足 _REQUIRED_JOBS 装配校验的占位 job（仅用于不验证业务逻辑的 start 路径测试）。"""
    return None


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

    @patch("utils.scheduler_service.logger")
    @patch("utils.scheduler_service.ConfigHandler")
    def test_on_job_error_other(self, mock_ch, mock_logger):
        mock_ch.get_setting.return_value = None
        svc = SchedulerService()
        event = MagicMock()
        event.job_id = "test_job"
        event.exception = RuntimeError("sensitive token=sk-secret123")
        svc._on_job_error(event)
        from utils.sanitizers import DataSanitizer

        mock_logger.error.assert_called_once_with(
            "[Scheduler] Job '%s' raised an exception: %s",
            "test_job",
            DataSanitizer.sanitize_error(event.exception, show_traceback=True),
            exc_info=True,
        )
        args, _ = mock_logger.error.call_args
        assert "sk-secret123" not in str(args)


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
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job.id == "daily_update"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_nightly_prediction(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("nightly_prediction")
        assert job.id == "nightly_prediction"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_nightly_prediction_uses_configured_time(self, mock_ch):
        """Task 7.3: nightly_prediction 时辰从 ConfigHandler 读取 (原硬编码 20:30)。"""
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        mock_ch.get_nightly_prediction_time.return_value = "21:45"
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("nightly_prediction")
        assert job is not None
        hour_field = next(f for f in job.trigger.fields if f.name == "hour")
        minute_field = next(f for f in job.trigger.fields if f.name == "minute")
        assert "21" in str(hour_field)
        assert "45" in str(minute_field)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_adds_ai_concept_daily(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
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
        mock_ch.get_nightly_prediction_time.return_value = None
        svc = SchedulerService()
        svc._schedule_jobs()
        job = svc.scheduler.get_job("daily_update")
        assert job.id == "daily_update"

    @patch("utils.scheduler_service.ConfigHandler")
    def test_schedule_jobs_removes_existing(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
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
    async def test_unknown_trading_day_skips(self):
        """is_trading_day 返回 None（离线日历超可信区间）时告警并跳过、不提交（D2-7）。"""
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("utils.scheduler_service.logger.warning") as mock_warn,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(return_value=None)
            mock_dp.return_value = mock_dp_instance
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await svc._run_daily_update()
            mock_warn.assert_called_once_with(
                "[Scheduler] Update skipped (%s status unknown: offline calendar beyond trusted interval, D2-7)",
                date(2024, 6, 15).strftime("%Y%m%d"),
            )

    @pytest.mark.asyncio
    async def test_calendar_check_fails_weekend(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch(
                "data.domain_services.offline_calendar.OfflineCalendar.is_trading_day",
                return_value=False,
            ) as mock_offline,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal error"))
            mock_dp.return_value = mock_dp_instance
            today = date(2024, 6, 15)  # Saturday (非交易日)
            mock_now_dt = MagicMock()
            mock_now_dt.date.return_value = today
            mock_now_dt.weekday.return_value = 5
            mock_now.return_value = mock_now_dt
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_daily_update()
            # D6-2: 日历检查失败时降级到离线日历三态判定（非交易日→跳过，不再依赖 weekday）
            mock_offline.assert_called_once_with(today)
            mock_tm_instance.submit_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_calendar_check_fails_weekday_offline_trading_day_proceeds(self):
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch(
                "data.domain_services.offline_calendar.OfflineCalendar.is_trading_day",
                return_value=True,
            ) as mock_offline,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal error"))
            mock_dp.return_value = mock_dp_instance
            today = date(2024, 6, 14)  # Friday（工作日且为交易日）
            mock_now.return_value.date.return_value = today
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_daily_update()
            # D6-2: 离线日历判定交易日（True）→ 继续提交
            mock_offline.assert_called_once_with(today)
            mock_tm_instance.submit_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_calendar_check_fails_offline_unknown_skips(self):
        """D6-2: 日历检查失败且离线日历返回 None（超可信区间）→ 保守跳过，交由补偿机制回补。"""
        svc = _make_svc()
        with (
            patch("utils.scheduler_service.ConfigHandler") as mock_ch,
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("utils.scheduler_service.get_now") as mock_now,
            patch(
                "data.domain_services.offline_calendar.OfflineCalendar.is_trading_day",
                return_value=None,
            ) as mock_offline,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            mock_dp_instance = MagicMock()
            mock_dp_instance.trade_calendar = MagicMock()
            mock_dp_instance.trade_calendar.is_trading_day = AsyncMock(side_effect=Exception("cal error"))
            mock_dp.return_value = mock_dp_instance
            today = date(2024, 6, 14)
            mock_now.return_value.date.return_value = today
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._run_daily_update()
            mock_offline.assert_called_once_with(today)
            mock_tm_instance.submit_task.assert_not_called()

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


class TestSchedulerDispatchNightlyPrediction:
    """review01-A2-1: svc._run_nightly_prediction 仅调度注册的 job（业务编排已下沉）。

    夜间预测业务逻辑（enabled/idempotency/交易日检查 + AI 选股 + 保存）已迁移至
    ``services/scheduled_jobs/nightly_prediction.py``，对应业务测试见
    ``tests/unit/test_nightly_prediction_job.py``。本类验证 SchedulerService 的
    依赖注入调度机制（register_job → 调用）。
    """

    @pytest.mark.asyncio
    async def test_unregistered_job_warns_and_returns(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.logger") as mock_logger:
            await svc._run_nightly_prediction()
        # 强断言：警告消息包含"未注册"提示（而非仅 .called 布尔标志）
        warning_calls = [c for c in mock_logger.warning.call_args_list]
        assert any("not registered" in str(c.args[0]) for c in warning_calls)

    @pytest.mark.asyncio
    async def test_registered_job_is_invoked_with_svc(self):
        svc = _make_svc()
        mock_job = AsyncMock()
        svc.register_job("nightly_prediction", mock_job)
        await svc._run_nightly_prediction()
        mock_job.assert_awaited_once_with(svc)

    def test_register_job_stores_and_reset_clears(self):
        svc = _make_svc()
        mock_job = MagicMock()
        svc.register_job("nightly_prediction", mock_job)
        assert svc._registered_jobs["nightly_prediction"] is mock_job
        # reset 后新实例注册表为空（新实例 __init__ 初始化 _registered_jobs）
        SchedulerService._reset_singleton()
        svc2 = _make_svc()
        assert "nightly_prediction" not in svc2._registered_jobs


class TestSchedulerDispatchReviewBackfill:
    """D2-4: svc._run_review_backfill 仅调度注册的 T+5 回填 job（业务逻辑在 services 层）。"""

    @pytest.mark.asyncio
    async def test_unregistered_job_warns_and_returns(self):
        svc = _make_svc()
        with patch("utils.scheduler_service.logger") as mock_logger:
            await svc._run_review_backfill()
        warning_calls = [c for c in mock_logger.warning.call_args_list]
        assert any("not registered" in str(c.args[0]) for c in warning_calls)

    @pytest.mark.asyncio
    async def test_registered_job_is_invoked_with_svc(self):
        svc = _make_svc()
        mock_job = AsyncMock()
        svc.register_job("review_backfill", mock_job)
        await svc._run_review_backfill()
        mock_job.assert_awaited_once_with(svc)


class TestScheduleJobsInvalidTime:
    @patch("utils.scheduler_service.ConfigHandler")
    def test_invalid_auto_update_time(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "invalid"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("daily_update") is not None  # noqa: weak-assertion APScheduler job 注册存在性，trigger 配置由专项测试覆盖

    @patch("utils.scheduler_service.ConfigHandler")
    def test_none_ai_concept_time(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = None
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("ai_concept_daily_refresh") is not None  # noqa: weak-assertion APScheduler job 注册存在性，trigger 配置由专项测试覆盖

    @patch("utils.scheduler_service.ConfigHandler")
    def test_review_backfill_job_registered(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = None
        mock_ch.get_nightly_prediction_time.return_value = "20:30"
        svc = SchedulerService()
        svc._schedule_jobs()
        assert svc.scheduler.get_job("review_backfill") is not None  # noqa: weak-assertion APScheduler job 注册存在性，trigger 配置由专项测试覆盖


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
        svc.register_job("nightly_prediction", _dummy_job)
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc.start()
        svc.scheduler.start.assert_called_once()
        assert svc.scheduler.add_job.call_count == 6  # 多次调用预期 (4 schedule_jobs + config_watchdog + load_db_state)

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_exception(self, mock_ch):
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.register_job("nightly_prediction", _dummy_job)
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
        svc.register_job("nightly_prediction", _dummy_job)
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc._schedule_jobs = MagicMock()
        svc.start()
        assert svc.scheduler.add_listener.call_count >= 2

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_missing_required_job_raises(self, mock_ch):
        """D6-6: 必需 job 未注册时 start() 启动期即抛 RuntimeError，而非静默跳过。"""
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        with pytest.raises(RuntimeError, match="必需的定时 job 未注册"):
            svc.start()
        svc.scheduler.start.assert_not_called()

    @patch("utils.scheduler_service.ConfigHandler")
    def test_start_required_job_registered_proceeds(self, mock_ch):
        """D6-6: 装配完整（必需 job 已注册）时 start() 正常继续调度。"""
        mock_ch.get_setting.return_value = None
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_ai_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
        svc.register_job("nightly_prediction", _dummy_job)
        svc.scheduler = MagicMock()
        svc.scheduler.running = False
        svc._schedule_jobs = MagicMock()
        svc.start()
        svc.scheduler.start.assert_called_once()


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
    async def test_daily_update_logic_with_sync_result_days_rows(self):
        svc = _make_svc()
        # D1-2: 用真实 SyncResult 承载完整性；is_complete=True → 写入幂等键
        # D1-4: 完成消息按 days/rows 双参呈现——42 为条数，1 为交易日数
        mock_result = SyncResult(days_processed=1, rows_written=42)
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
            # D1-4: 消息应包含条数 42（用户据此判断是否真的拉到数据）
            assert "42" in result_msg
            assert svc._last_update_date == "20240614"

    @pytest.mark.asyncio
    async def test_daily_update_empty_day_warns(self):
        svc = _make_svc()
        # D1-4: 处理了交易日却 0 行落库（days>0, rows=0）→ 显式警告，区分"拉到空"与"拉到数据"
        mock_result = SyncResult(days_processed=1, rows_written=0)
        mock_dp = MagicMock()
        mock_dp.trade_calendar = MagicMock()
        mock_dp.trade_calendar.is_trading_day = AsyncMock(return_value=True)
        mock_dp.run_daily_update = AsyncMock(return_value=mock_result)
        mock_tm = MagicMock()
        now_val = datetime(2024, 6, 14, 16, 30)

        patches = _get_patches(mock_dp, mock_tm, now_val)
        with (
            patches[0] as mock_ch,
            patches[1],
            patches[2],
            patches[3],
            patch("utils.scheduler_service.logger.warning") as mock_warn,
        ):
            mock_ch.is_auto_update_enabled.return_value = True
            await svc._run_daily_update()
            factory = mock_tm.submit_task.call_args.kwargs["coroutine_factory"]
            await factory("test_task")
            # D1-4: 断言空日警告被触发且携带正确交易日数（1）；未调用时 call_args 为 None 触发 AttributeError，强断言
            assert mock_warn.call_args.args[1] == 1

    @pytest.mark.asyncio
    async def test_daily_update_logic_with_critical_failure_not_marked(self):
        svc = _make_svc()
        # D1-2: 关键表失败 → is_complete=False → 不写入幂等键（即使 errors 也有值，幂等判定以 failed_critical_tables 为准）
        mock_result = SyncResult(errors=["some error"], failed_critical_tables=["daily_quotes"])
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
    async def test_daily_update_logic_optional_failure_still_marked(self):
        svc = _make_svc()
        # D1-2: 仅非关键表失败 → is_complete 仍 True → 仍写入幂等键（降级由 UI 提示，不阻断幂等）
        mock_result = SyncResult(added=30, failed_optional_tables=["limit_list"])
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
            assert svc._last_update_date == "20240614"

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


class TestSafeShutdownSchedulerGenericError:
    """_safe_shutdown_scheduler 在 shutdown 抛非 RuntimeError 异常时走 log_classified 分支。

    PR #640 diff-coverage 补覆盖：utils/scheduler_service.py L85。
    """

    @pytest.mark.asyncio
    async def test_generic_error_log_via_log_classified(self, caplog):
        import logging

        scheduler = MagicMock()
        scheduler.running = True
        scheduler.shutdown.side_effect = ValueError("shutdown worker crashed")

        with caplog.at_level(logging.WARNING, logger="utils.scheduler_service"):
            SchedulerService._safe_shutdown_scheduler(scheduler, context="reset")

        assert any("[Scheduler] Error during shutdown" in r.message for r in caplog.records)


class TestCatchUpMissedUpdates:
    """D6-1: 补偿机制 — 检查遗漏交易日并提交补偿任务。"""

    @pytest.mark.asyncio
    async def test_no_last_update_date_returns(self):
        svc = _make_svc()
        svc._last_update_date = None
        with patch("data.data_processor.DataProcessor") as mock_dp:
            await svc._catch_up_missed_updates()
            mock_dp.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_last_update_date_returns(self):
        """空串基准（_persist_run_date 以空串表示无值）直接跳过，避免 parse_date('') 抛 ValueError。"""
        svc = _make_svc()
        svc._last_update_date = ""
        with (
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            await svc._catch_up_missed_updates()
            mock_dp.assert_not_called()
            mock_tm_instance = mock_tm.return_value
            mock_tm_instance.submit_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_up_to_date_no_submit(self):
        svc = _make_svc()
        svc._last_update_date = "20240614"
        with (
            patch("data.data_processor.DataProcessor"),
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager") as mock_tm,
        ):
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            await svc._catch_up_missed_updates()
            mock_tm_instance.submit_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_submits_catchup_task_with_independent_key(self):
        """D6-1 Q2 修订：补偿任务使用独立 unique_key 'daily_sync_catchup'，与常规同步解耦。"""
        svc = _make_svc()
        svc._last_update_date = "20240610"
        mock_dp_instance = MagicMock()
        mock_dp_instance.trade_calendar = MagicMock()
        mock_dp_instance.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[date(2024, 6, 10), date(2024, 6, 11), date(2024, 6, 12), date(2024, 6, 13)]
        )
        mock_tm_instance = MagicMock()
        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp_instance),
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager", return_value=mock_tm_instance),
        ):
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await svc._catch_up_missed_updates()
            kwargs = mock_tm_instance.submit_task.call_args.kwargs
            # 过滤基准日 20240610 后只剩 [11, 12, 13] 三天
            assert len(kwargs["missed_dates"]) == 3
            assert kwargs["unique_key"] == "daily_sync_catchup"
            assert kwargs["cancellable"] is True

    @pytest.mark.asyncio
    async def test_include_today_true_backfills_today(self):
        """D6-1 Q21: misfire 路径（include_today=True）回补范围延伸到今天（已收盘）。"""
        svc = _make_svc()
        svc._last_update_date = "20240613"
        mock_dp_instance = MagicMock()
        mock_dp_instance.trade_calendar = MagicMock()
        all_dates = [date(2024, 6, 13), date(2024, 6, 14), date(2024, 6, 15)]

        async def _get_trade_dates(start_date=None, end_date=None):
            # 模拟真实行为：按闭区间裁剪交易日列表（None 兜底避免可选操作数参与比较）
            s = start_date if start_date is not None else date.min
            e = end_date if end_date is not None else date.max
            return [d for d in all_dates if s <= d <= e]

        mock_dp_instance.trade_calendar.get_trade_dates = AsyncMock(side_effect=_get_trade_dates)
        mock_tm_instance = MagicMock()
        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp_instance),
            patch("utils.scheduler_service.get_now") as mock_now,
            patch("services.task_manager.TaskManager", return_value=mock_tm_instance),
        ):
            # 今天=6/15：misfire 路径（include_today=True）end=today=6/15，
            # 排除基准日 6/13 后回补 [14, 15]。
            mock_now.return_value.date.return_value = date(2024, 6, 15)
            await svc._catch_up_missed_updates(include_today=True)
            kwargs = mock_tm_instance.submit_task.call_args.kwargs
            assert kwargs["missed_dates"] == [date(2024, 6, 14), date(2024, 6, 15)]
            assert mock_dp_instance.trade_calendar.get_trade_dates.await_args.kwargs == {
                "start_date": date(2024, 6, 13),
                "end_date": date(2024, 6, 15),
            }
            # 对照：常规路径（include_today=False）end=昨天=6/14，同一天应不提交今天
            mock_tm_instance.submit_task.reset_mock()
            await svc._catch_up_missed_updates()
            kwargs_default = mock_tm_instance.submit_task.call_args.kwargs
            assert kwargs_default["missed_dates"] == [date(2024, 6, 14)]
            assert mock_dp_instance.trade_calendar.get_trade_dates.await_args.kwargs == {
                "start_date": date(2024, 6, 13),
                "end_date": date(2024, 6, 14),
            }

    @pytest.mark.asyncio
    async def test_load_db_state_triggers_catchup(self):
        """D6-1 触发点 1：DB 幂等状态就绪后立即启动补偿检查。"""
        svc = _make_svc()
        mock_engine = MagicMock()
        svc._catch_up_missed_updates = AsyncMock()
        with (
            patch(
                "data.cache.cache_manager.CacheManager._instance",
                MagicMock(engine=mock_engine),
            ),
            patch(
                "data.persistence.app_state_service.get_app_state",
                AsyncMock(side_effect=["20240613", "20240614", "20240615"]),
            ) as mock_get_state,
        ):
            await svc._load_db_state()
        assert svc._last_update_date == "20240613"
        assert svc._db_state_loaded is True
        svc._catch_up_missed_updates.assert_awaited_once()
        assert mock_get_state.await_count == 3

    @pytest.mark.asyncio
    async def test_watch_config_changes_triggers_catchup(self):
        """D6-1 触发点 2：30 秒周期配置看门狗复用为补偿检查周期。"""
        svc = _make_svc()
        svc._last_update_date = "20240613"  # 非 None → 走完整补偿检查
        svc._catch_up_missed_updates = AsyncMock()
        config = {
            "time": "09:30",
            "enabled": True,
            "ai_concept_time": "10:00",
            "ai_concept_enabled": False,
        }
        svc._last_known_config = config.copy()
        with patch("utils.scheduler_service.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=config)
            await svc._watch_config_changes()
        svc._catch_up_missed_updates.assert_awaited_once()


class TestCatchupLogic:
    """D6-1: 补偿执行逻辑 — 逐日同步 + is_complete 判定幂等键。"""

    @pytest.mark.asyncio
    async def test_complete_marks_done(self):
        svc = _make_svc()
        mock_dp_instance = MagicMock()
        mock_dp_instance.sync_daily_market_snapshot = AsyncMock()
        mock_sr_instance = SyncResult()  # 真实对象：failed_critical_tables 空 → is_complete=True
        mock_tm_instance = MagicMock()
        mock_tm_instance.update_progress.return_value = True
        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp_instance),
            patch("services.task_manager.TaskManager", return_value=mock_tm_instance),
            patch(
                "data.sync.base.SyncResult",
                return_value=mock_sr_instance,
            ),
        ):
            # 保留真实 _mark_daily_update_done_db（内部推进 _last_update_date），
            # 仅 mock 其 DB/config 持久化（D6-1 幂等键推进由该方法完成）。
            svc._persist_run_date_db = AsyncMock()
            await svc._catchup_logic(
                "task1",
                [date(2024, 6, 11), date(2024, 6, 12)],
            )
            # is_complete=True → 写幂等键推进到最后一个遗漏交易日
            assert svc._last_update_date == "20240612"
            svc._persist_run_date_db.assert_awaited_once_with(
                "sched_last_daily_update",
                "scheduler_last_daily_update",
                "20240612",
            )

    @pytest.mark.asyncio
    async def test_incomplete_not_marked(self):
        svc = _make_svc()
        mock_dp_instance = MagicMock()
        mock_dp_instance.sync_daily_market_snapshot = AsyncMock()
        mock_sr_instance = SyncResult(failed_critical_tables=["daily_quotes"])  # is_complete=False
        mock_tm_instance = MagicMock()
        mock_tm_instance.update_progress.return_value = True
        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp_instance),
            patch("services.task_manager.TaskManager", return_value=mock_tm_instance),
            patch(
                "data.sync.base.SyncResult",
                return_value=mock_sr_instance,
            ),
            patch("utils.scheduler_service.logger.warning") as mock_warn,
        ):
            svc._mark_daily_update_done_db = AsyncMock()
            await svc._catchup_logic("task1", [date(2024, 6, 11)])
            # 强断言：警告携带关键表失败清单，且不推进幂等键
            mock_warn.assert_called_once_with(
                "[Scheduler] Catch-up NOT complete (critical=%s), NOT marking done",
                ["daily_quotes"],
            )
            svc._mark_daily_update_done_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_raises(self):
        svc = _make_svc()
        mock_tm_instance = MagicMock()
        mock_tm_instance.update_progress.return_value = False  # 模拟任务已取消
        with (
            patch("services.task_manager.TaskManager", return_value=mock_tm_instance),
        ):
            with pytest.raises(asyncio.CancelledError):
                await svc._catchup_logic("task1", [date(2024, 6, 11)])

    @pytest.mark.asyncio
    async def test_single_day_failure_continues(self):
        """D6-1 Q3 修订：单日同步失败不中断整批。"""
        svc = _make_svc()
        mock_dp_instance = MagicMock()
        calls = {"n": 0}

        async def _sync(trade_date=None, sync_result=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("sync day1 failed")
            return None

        mock_dp_instance.sync_daily_market_snapshot = AsyncMock(side_effect=_sync)
        mock_tm_instance = MagicMock()
        mock_tm_instance.update_progress.return_value = True
        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp_instance),
            patch("services.task_manager.TaskManager", return_value=mock_tm_instance),
            patch(
                "data.sync.base.SyncResult",
                return_value=SyncResult(),
            ),
            patch("utils.scheduler_service.logger.warning"),
        ):
            await svc._catchup_logic("task1", [date(2024, 6, 11), date(2024, 6, 12)])
            # 两天都尝试同步（首日失败后继续次日）
            assert mock_dp_instance.sync_daily_market_snapshot.await_count == 2


class TestOnJobMissedCatchup:
    """D6-1: _on_job_missed 对业务 job 触发补偿检查。"""

    def test_business_job_triggers_catchup_loop(self):
        svc = _make_svc()
        event = MagicMock()
        event.job_id = "daily_update"
        event.scheduled_run_time = "2024-01-01"
        mock_loop = MagicMock()
        created_task = MagicMock()
        mock_loop.create_task.return_value = created_task
        with patch("utils.scheduler_service.asyncio.get_running_loop", return_value=mock_loop):
            svc._on_job_missed(event)
        # 校验调度的是 _catch_up_missed_updates 协程（call_args 访问即证明被调用）
        coro = mock_loop.create_task.call_args.args[0]
        assert coro.cr_code.co_name == "_catch_up_missed_updates"
        # D6-1 Q21: misfire 已过计划时刻+宽限（已收盘），须回补今天（include_today=True），
        # 否则"当天永久跳过"依旧存在。
        assert coro.cr_frame.f_locals.get("include_today") is True
        coro.close()  # 测试中不真正调度，close 释放协程避免 RuntimeWarning

    def test_non_business_job_no_trigger(self):
        svc = _make_svc()
        event = MagicMock()
        event.job_id = "some_other_job"
        event.scheduled_run_time = "2024-01-01"
        with patch("utils.scheduler_service.asyncio.get_running_loop") as mock_loop:
            svc._on_job_missed(event)
        mock_loop.assert_not_called()


class TestMisfireGraceTime:
    def test_misfire_grace_time_increased(self):
        """D6-1: misfire_grace_time 从 60s 提升到 1800s（30 分钟余量，缓解 misfire）。"""
        svc = _make_svc()
        # AsyncIOScheduler 将 job_defaults 存入私有 _job_defaults（APScheduler 无公共属性）
        defaults = svc.scheduler._job_defaults
        assert defaults["misfire_grace_time"] == 1800
