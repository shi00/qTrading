import pytest


_CFG_LAST_DAILY_UPDATE = "scheduler_last_daily_update"
_CFG_LAST_NIGHTLY_PREDICTION = "scheduler_last_nightly_prediction"


@pytest.mark.unit
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
