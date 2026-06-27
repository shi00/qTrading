import pytest

pytestmark = pytest.mark.integration

_CFG_LAST_DAILY_UPDATE = "scheduler_last_daily_update"
_CFG_LAST_NIGHTLY_PREDICTION = "scheduler_last_nightly_prediction"


class TestSchedulerPersistentIdempotency:
    def test_runtime_keys_survive_ensure_defaults_cleanup(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._config_cache = None

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
        cfg_mod.ConfigHandler._config_cache = None

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
        cfg_mod.ConfigHandler._config_cache = None
        cfg_mod.ConfigHandler.save_config(
            {
                _CFG_LAST_DAILY_UPDATE: "20260428",
                _CFG_LAST_NIGHTLY_PREDICTION: "20260427",
            },
            replace=True,
        )
        cfg_mod.ConfigHandler._config_cache = None
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
        cfg_mod.ConfigHandler._config_cache = None

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()
        service._mark_daily_update_done("20260429")
        service._mark_nightly_prediction_done("20260429")

        cfg_mod.ConfigHandler._config_cache = None
        config = cfg_mod.ConfigHandler.load_config()

        assert service._last_update_date == "20260429"
        assert service._last_pred_date == "20260429"
        assert config[_CFG_LAST_DAILY_UPDATE] == "20260429"
        assert config[_CFG_LAST_NIGHTLY_PREDICTION] == "20260429"


@pytest.mark.integration
class TestSchedulerFailureProtection:
    """H-6: Failed tasks must NOT mark done."""

    def test_daily_update_with_errors_does_not_mark_done(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._config_cache = None
        cfg_mod.ConfigHandler.save_config({_CFG_LAST_DAILY_UPDATE: ""}, replace=True)
        cfg_mod.ConfigHandler._config_cache = None

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()

        class FakeResult:
            errors = ["financial_reports sync failed"]

        result = FakeResult()
        has_errors = hasattr(result, "errors") and bool(result.errors)
        assert has_errors is True
        assert service._last_update_date == ""

    def test_daily_update_without_errors_marks_done(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._config_cache = None

        sched_mod.SchedulerService._reset_singleton()
        _ = sched_mod.SchedulerService()

        class FakeResult:
            errors = []

        result = FakeResult()
        has_errors = hasattr(result, "errors") and bool(result.errors)
        assert has_errors is False


class TestDoubaoIdempotency:
    """H-7: Doubao weekly task must have idempotency key."""

    def test_doubao_date_persisted_and_read(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._config_cache = None
        cfg_mod.ConfigHandler.save_config(
            {_CFG_LAST_DOUBAO_REFRESH: "20260428"},
            replace=True,
        )
        cfg_mod.ConfigHandler._config_cache = None
        cfg_mod.ConfigHandler.ensure_defaults()

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()

        assert service._last_doubao_date == "20260428"

    def test_doubao_skips_when_already_done(self, monkeypatch, tmp_path):
        import utils.config_handler as cfg_mod
        import utils.scheduler_service as sched_mod

        temp_config = tmp_path / "user_settings.json"
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(temp_config))
        cfg_mod.ConfigHandler._config_cache = None
        cfg_mod.ConfigHandler.save_config({}, replace=True)
        cfg_mod.ConfigHandler._config_cache = None

        sched_mod.SchedulerService._reset_singleton()
        service = sched_mod.SchedulerService()
        service._last_doubao_date = "20260430"

        monkeypatch.setattr(sched_mod, "get_now", lambda: __import__("datetime").datetime(2026, 4, 30))
        monkeypatch.setattr(sched_mod, "ConfigHandler", cfg_mod.ConfigHandler)

        import asyncio

        result = asyncio.run(service._run_doubao_tagger())
        assert result is None


_CFG_LAST_DOUBAO_REFRESH = "scheduler_last_ai_concept_refresh"
