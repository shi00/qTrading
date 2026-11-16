import datetime
import typing

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

_CFG_LAST_DAILY_UPDATE = "scheduler_last_daily_update"
_CFG_LAST_NIGHTLY_PREDICTION = "scheduler_last_nightly_prediction"


@pytest.fixture(autouse=True)
def _isolate_scheduler_singleton():
    """R7: 集成测试不自动重置注册单例，本模块手动隔离 SchedulerService。"""
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()
    yield
    sched_mod.SchedulerService._reset_singleton()


@pytest.fixture
def scheduler_config(monkeypatch, tmp_path):
    """把配置写入临时文件，并开启自动更新，使日更走真实配置检查门。"""
    import utils.config_handler as cfg_mod

    temp_config = tmp_path / "user_settings.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
    cfg_mod.ConfigHandler._clear_cache()
    cfg_mod.ConfigHandler.save_config(
        {_CFG_LAST_DAILY_UPDATE: "", "auto_update_enabled": True},
        replace=True,
    )
    cfg_mod.ConfigHandler._clear_cache()
    yield cfg_mod
    cfg_mod.ConfigHandler._clear_cache()


def _persisted_daily_update(cfg_mod) -> str:
    cfg_mod.ConfigHandler._clear_cache()
    return cfg_mod.ConfigHandler.load_config()[_CFG_LAST_DAILY_UPDATE]


def _install_daily_update_doubles(monkeypatch, *, clock: dict[str, typing.Any], result: typing.Any):
    """隔离日更的 IO 边界：交易日历/数据同步/TaskManager/当前时间/DB 引擎。

    返回捕获容器：``factories`` 为每次 submit_task 收到的 coroutine_factory，
    供测试实际执行生产 ``_daily_update_logic``；``progress`` 记录进度回调。
    """
    import services.task_manager as tm_mod
    import utils.scheduler_service as sched_mod

    captured: dict[str, list[typing.Any]] = {"factories": [], "progress": []}

    class _FakeTradeCalendar:
        async def is_trading_day(self, _today):
            return True

    class _FakeProcessor:
        def __init__(self):
            self.trade_calendar = _FakeTradeCalendar()

        async def run_daily_update(self, progress_callback=None):
            if progress_callback:
                progress_callback(1, 2, "half")
                progress_callback(2, 2, "done")
            return result

    class _FakeTaskManager:
        def update_progress(self, task_id, progress, message=None):
            captured["progress"].append((task_id, progress, message))

        def submit_task(self, **kwargs):
            captured["factories"].append(kwargs["coroutine_factory"])
            return f"task-{len(captured['factories'])}"

    monkeypatch.setattr("data.data_processor.DataProcessor", _FakeProcessor)
    monkeypatch.setattr(tm_mod, "TaskManager", _FakeTaskManager)
    monkeypatch.setattr(sched_mod, "get_now", lambda: clock["now"])
    # 不启动真实 DB：引擎缺失时 _persist_run_date_db 只写配置缓存。
    monkeypatch.setattr("data.persistence.engine_provider.get_engine", lambda: None)
    return captured


async def _submit_daily_update(service, captured) -> typing.Any:
    await service._run_daily_update()
    assert captured["factories"], "日更任务未提交（submit_task 未被调用）"
    return captured["factories"][-1]


class TestSchedulerPersistentIdempotency:
    def test_runtime_keys_survive_ensure_defaults_cleanup(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._clear_cache()

        cfg_mod.ConfigHandler.save_config(
            {
                _CFG_LAST_DAILY_UPDATE: "20260429",
                _CFG_LAST_NIGHTLY_PREDICTION: "20260428",
                "ai_api_key": "encrypted-key",
                "ai_prompt_dump_enabled": True,
                "max_concurrent_tasks": 7,
            },
            replace=True,
        )
        cfg_mod.ConfigHandler._clear_cache()

        cfg_mod.ConfigHandler.ensure_defaults()
        config = cfg_mod.ConfigHandler.load_config()

        assert config[_CFG_LAST_DAILY_UPDATE] == "20260429"
        assert config[_CFG_LAST_NIGHTLY_PREDICTION] == "20260428"
        assert config["ai_api_key"] == "encrypted-key"
        assert config["ai_prompt_dump_enabled"] is True
        assert config["max_concurrent_tasks"] == 7

    def test_scheduler_reads_persisted_keys_after_restart(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._clear_cache()
        cfg_mod.ConfigHandler.save_config(
            {
                _CFG_LAST_DAILY_UPDATE: "20260428",
                _CFG_LAST_NIGHTLY_PREDICTION: "20260427",
            },
            replace=True,
        )
        cfg_mod.ConfigHandler._clear_cache()
        cfg_mod.ConfigHandler.ensure_defaults()

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()

        assert service._last_update_date == "20260428"
        assert service._last_pred_date == "20260427"

    def test_mark_done_sets_instance_and_persists(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._clear_cache()

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()
        # REVIEW-06 TO-05: config-only 标记方法（_mark_*_done）已随 C-P1-6 迁移移除（DB 为真相源），
        # 此处直接用保留的 _persist_run_date 表达 config 持久化语义。
        service._last_update_date = "20260429"
        service._persist_run_date(_CFG_LAST_DAILY_UPDATE, "20260429")
        service._last_pred_date = "20260429"
        service._persist_run_date(_CFG_LAST_NIGHTLY_PREDICTION, "20260429")

        cfg_mod.ConfigHandler._clear_cache()
        config = cfg_mod.ConfigHandler.load_config()

        assert service._last_update_date == "20260429"
        assert service._last_pred_date == "20260429"
        assert config[_CFG_LAST_DAILY_UPDATE] == "20260429"
        assert config[_CFG_LAST_NIGHTLY_PREDICTION] == "20260429"


class TestSchedulerFailureProtection:
    """H-6: 日更失败不得推进完成水位；只有完整成功才推进并持久化。"""

    async def test_daily_update_with_errors_does_not_mark_done(self, scheduler_config, monkeypatch):
        from core.i18n import Message
        from data.sync.base import SyncResult
        from services.task_manager import TaskIncompleteError
        import utils.scheduler_service as sched_mod

        clock = {"now": datetime.datetime(2026, 4, 29, 16, 30)}
        result = SyncResult(
            errors=["financial_reports sync failed"],
            failed_critical_tables=["financial_reports"],
        )
        captured = _install_daily_update_doubles(monkeypatch, clock=clock, result=result)
        service = sched_mod.SchedulerService()

        factory = await _submit_daily_update(service, captured)
        with pytest.raises(TaskIncompleteError) as exc_info:
            await factory("task-1")

        error = exc_info.value
        assert isinstance(error.description, Message)
        assert error.description.key == "sched_daily_incomplete"
        reason = error.reason or ""
        assert "is_complete=False" in reason
        assert "financial_reports" in reason
        assert captured["progress"], "进度回调应透传到 TaskManager"
        assert service._last_update_date == ""
        assert _persisted_daily_update(scheduler_config) == ""
        # 失败不推进水位：同日再次触发仍会提交（允许重试补跑）
        await _submit_daily_update(service, captured)
        assert len(captured["factories"]) == 2

    async def test_daily_update_without_errors_marks_done(self, scheduler_config, monkeypatch):
        from data.sync.base import SyncResult
        import utils.scheduler_service as sched_mod

        clock = {"now": datetime.datetime(2026, 4, 29, 16, 30)}
        captured = _install_daily_update_doubles(
            monkeypatch,
            clock=clock,
            result=SyncResult(days_processed=1, rows_written=42),
        )
        service = sched_mod.SchedulerService()

        factory = await _submit_daily_update(service, captured)
        message = await factory("task-1")

        assert message.params == {"days": 1, "rows": 42}
        assert service._last_update_date == "20260429"
        assert _persisted_daily_update(scheduler_config) == "20260429"

        # 同日重复触发：幂等命中水位，不再提交
        await service._run_daily_update()
        assert len(captured["factories"]) == 1

        # 跨日：水位只挡当日，次日重新提交并推进到新日期
        clock["now"] = datetime.datetime(2026, 4, 30, 16, 30)
        factory = await _submit_daily_update(service, captured)
        message = await factory("task-2")
        assert message.params == {"days": 1, "rows": 42}
        assert service._last_update_date == "20260430"
        assert _persisted_daily_update(scheduler_config) == "20260430"

    async def test_daily_update_optional_failure_still_marks_done(self, scheduler_config, monkeypatch):
        """errors 非空但仅非关键表失败：is_complete 仍为真，水位推进（不把 errors 等同于失败）。"""
        from data.sync.base import SyncResult
        import utils.scheduler_service as sched_mod

        clock = {"now": datetime.datetime(2026, 4, 29, 16, 30)}
        captured = _install_daily_update_doubles(
            monkeypatch,
            clock=clock,
            result=SyncResult(
                errors=["limit_list sync failed"],
                failed_optional_tables=["limit_list"],
            ),
        )
        service = sched_mod.SchedulerService()

        factory = await _submit_daily_update(service, captured)
        await factory("task-1")

        assert service._last_update_date == "20260429"
        assert _persisted_daily_update(scheduler_config) == "20260429"


class TestConceptSyncIdempotency:
    """H-7: Concept sync daily task must have idempotency key."""

    def test_concept_sync_date_persisted_and_read(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._clear_cache()
        cfg_mod.ConfigHandler.save_config(
            {_CFG_LAST_CONCEPT_SYNC: "20260428"},
            replace=True,
        )
        cfg_mod.ConfigHandler._clear_cache()
        cfg_mod.ConfigHandler.ensure_defaults()

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()

        assert service._last_concept_sync_date == "20260428"

    def test_concept_sync_skips_when_already_done(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._clear_cache()
        cfg_mod.ConfigHandler.save_config({}, replace=True)
        cfg_mod.ConfigHandler._clear_cache()

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()
        service._last_concept_sync_date = "20260430"

        monkeypatch.setattr(sched_mod, "get_now", lambda: __import__("datetime").datetime(2026, 4, 30))
        monkeypatch.setattr(sched_mod, "ConfigHandler", cfg_mod.ConfigHandler)

        import asyncio

        result = asyncio.run(service._run_concept_sync())
        assert result is None


_CFG_LAST_CONCEPT_SYNC = "scheduler_last_concept_sync"
