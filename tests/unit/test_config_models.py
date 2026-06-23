import json
import logging

import pytest
from pydantic import ValidationError

from utils.config_handler import ConfigHandler
from utils.config_models import (
    AppConfig,
    ConfigValidationResult,
    DEFAULT_AI_PROMPT,
    DEFAULT_NEWS_PROMPT,
    SyncIntegrityConfig,
    get_default_config,
)


class TestSyncIntegrityConfig:
    def test_default_values(self):
        cfg = SyncIntegrityConfig()
        assert cfg.max_retry_days_per_sync == 30
        assert cfg.max_retry_stocks_per_sync == 100
        assert cfg.enable_adaptive_retry is True
        assert cfg.quality_threshold == 80
        assert cfg.quotes_tolerance_ratio == 0.95
        assert cfg.indicators_tolerance_ratio == 0.90
        assert cfg.moneyflow_tolerance_ratio == 0.80
        assert cfg.financial_min_periods == 4
        assert cfg.quality_weights == {
            "daily_quotes": 30,
            "daily_indicators": 25,
            "moneyflow_daily": 20,
            "margin_daily": 10,
        }

    def test_validation_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            SyncIntegrityConfig(quality_threshold=101)
        with pytest.raises(ValidationError):
            SyncIntegrityConfig(quality_threshold=-1)
        with pytest.raises(ValidationError):
            SyncIntegrityConfig(quotes_tolerance_ratio=1.5)
        with pytest.raises(ValidationError):
            SyncIntegrityConfig(max_retry_days_per_sync=0)


class TestAppConfig:
    def test_default_values(self):
        cfg = AppConfig()
        assert cfg.db_host == ""
        assert cfg.db_port == 5432
        assert cfg.llm_provider == "deepseek"
        assert cfg.theme_name == "dark"
        assert cfg.locale == "zh"
        assert cfg.log_level == "INFO"
        assert cfg.max_io_workers == 0
        assert cfg.onboarding_complete is False
        assert cfg.ts_token == ""
        assert cfg.config_version == 1

    def test_config_version_default(self):
        """默认 config_version 为 1"""
        cfg = AppConfig()
        assert cfg.config_version == 1

    def test_config_version_custom(self):
        """自定义 config_version 生效"""
        cfg = AppConfig(config_version=2)
        assert cfg.config_version == 2

    def test_config_version_old_config_compatibility(self):
        """旧配置（无 config_version 字段）反序列化为默认值 1"""
        raw = {"db_host": "10.0.0.1", "db_port": 5432}
        cfg = AppConfig.model_validate(raw)
        assert cfg.config_version == 1

    def test_config_version_invalid_zero(self):
        """config_version < 1 校验失败"""
        with pytest.raises(ValidationError):
            AppConfig(config_version=0)

    def test_config_version_invalid_negative(self):
        """config_version 负数校验失败"""
        with pytest.raises(ValidationError):
            AppConfig(config_version=-1)

    def test_field_validation_db_port(self):
        with pytest.raises(ValidationError):
            AppConfig(db_port=0)
        with pytest.raises(ValidationError):
            AppConfig(db_port=70000)

    def test_field_validation_log_level(self):
        with pytest.raises(ValidationError):
            AppConfig(log_level="VERBOSE")

    def test_field_validation_theme_name(self):
        with pytest.raises(ValidationError):
            AppConfig(theme_name="blue")

    def test_field_validation_locale(self):
        with pytest.raises(ValidationError):
            AppConfig(locale="fr")
        with pytest.raises(ValidationError):
            AppConfig(locale="zh_TW")

    def test_field_validation_time_format(self):
        with pytest.raises(ValidationError):
            AppConfig(auto_update_time="25:00")
        with pytest.raises(ValidationError):
            AppConfig(doubao_schedule_time="abc")

    def test_extra_allow_dynamic_keys(self):
        cfg = AppConfig.model_validate(
            {
                "ai_strategy_prompt_oversold": "custom prompt",
                "ai_strategy_prompt_value": "another prompt",
            }
        )
        assert cfg.ai_strategy_prompt_oversold == "custom prompt"  # type: ignore[attr-defined]
        assert cfg.ai_strategy_prompt_value == "another prompt"  # type: ignore[attr-defined]

    def test_nested_sync_integrity(self):
        cfg = AppConfig(sync_integrity={"quality_threshold": 90})
        assert cfg.sync_integrity.quality_threshold == 90
        assert cfg.sync_integrity.quotes_tolerance_ratio == 0.95

    def test_model_validate_from_dict(self):
        raw = {"db_host": "10.0.0.1", "db_port": 5433, "theme_name": "light"}
        cfg = AppConfig.model_validate(raw)
        assert cfg.db_host == "10.0.0.1"
        assert cfg.db_port == 5433
        assert cfg.theme_name == "light"

    def test_model_validate_fills_defaults(self):
        raw = {"db_host": "10.0.0.1"}
        cfg = AppConfig.model_validate(raw)
        assert cfg.db_port == 5432
        assert cfg.llm_provider == "deepseek"

    def test_model_dump_roundtrip(self):
        cfg = AppConfig(db_host="custom", db_port=3306)
        dumped = cfg.model_dump()
        restored = AppConfig.model_validate(dumped)
        assert restored.db_host == "custom"
        assert restored.db_port == 3306


class TestGetDefaultConfig:
    def test_returns_dict(self):
        result = get_default_config()
        assert isinstance(result, dict)
        assert "db_host" in result
        assert result["db_host"] == ""

    def test_contains_all_expected_keys(self):
        result = get_default_config()
        expected_keys = [
            "db_host",
            "db_port",
            "db_user",
            "db_name",
            "llm_provider",
            "llm_model",
            "theme_name",
            "locale",
            "log_level",
            "sync_integrity",
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_nested_sync_integrity(self):
        result = get_default_config()
        assert isinstance(result["sync_integrity"], dict)
        assert result["sync_integrity"]["quality_threshold"] == 80

    def test_cache_returns_same_object(self):
        r1 = get_default_config()
        r2 = get_default_config()
        assert r1 is r2


class TestConfigValidationResult:
    def test_valid_result(self):
        result = ConfigValidationResult(
            is_valid=True,
            config={"key": "val"},
            errors=[],
            used_defaults=False,
        )
        assert result.is_valid is True
        assert result.errors == []
        assert result.used_defaults is False

    def test_invalid_result(self):
        result = ConfigValidationResult(
            is_valid=False,
            config={},
            errors=["bad value"],
            used_defaults=True,
        )
        assert result.is_valid is False
        assert len(result.errors) == 1


class TestGetTypedSetTyped:
    def test_get_typed_returns_default_when_missing(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        assert ConfigHandler.get_typed("log_level", str, "INFO") == "INFO"

    def test_get_typed_coerces_type(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({"db_port": "5433"}, f)
        assert ConfigHandler.get_typed("db_port", int, 5432) == 5433

    def test_get_typed_bool_from_string(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({"onboarding_complete": "true"}, f)
        assert ConfigHandler.get_typed("onboarding_complete", bool, False) is True

    def test_get_typed_fallback_on_bad_type(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({"db_port": "not_a_number"}, f)
        assert ConfigHandler.get_typed("db_port", int, 5432) == 5432

    def test_set_typed_saves_value(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        result = ConfigHandler.set_typed("log_level", "DEBUG")
        assert result is True
        with open(config_file, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["log_level"] == "DEBUG"

    def test_set_typed_validator_rejects(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        result = ConfigHandler.set_typed("db_port", 0, validator=lambda v: 1 <= v <= 65535)
        assert result is False

    def test_set_typed_sanitizes_sensitive_key_in_log(self, monkeypatch, tmp_path, caplog):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        secret_value = "sk_super_secret_api_key_1234567890ab"
        with caplog.at_level(logging.WARNING):
            result = ConfigHandler.set_typed("ai_api_key", secret_value, validator=lambda v: False)
        assert result is False
        assert secret_value not in caplog.text
        assert "sk_***90ab" in caplog.text

    def test_set_typed_sanitizes_db_password_encrypted(self, monkeypatch, tmp_path, caplog):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        encrypted_value = "AESGCM_encrypted_password_data_abc123"
        with caplog.at_level(logging.WARNING):
            result = ConfigHandler.set_typed("db_password_encrypted", encrypted_value, validator=lambda v: False)
        assert result is False
        assert encrypted_value not in caplog.text

    def test_set_typed_non_sensitive_key_logged_as_is(self, monkeypatch, tmp_path, caplog):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        with caplog.at_level(logging.WARNING):
            result = ConfigHandler.set_typed("log_level", "INVALID", validator=lambda v: False)
        assert result is False
        assert "INVALID" in caplog.text


class TestLoadConfigWithValidation:
    def test_valid_config(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({"db_host": "10.0.0.1", "db_port": 5433}, f)
        result = ConfigHandler.load_config_with_validation()
        assert result.is_valid is True
        assert result.config["db_host"] == "10.0.0.1"
        assert result.used_defaults is False

    def test_invalid_config_returns_defaults(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump({"db_port": 99999}, f)
        result = ConfigHandler.load_config_with_validation()
        assert result.is_valid is False
        assert len(result.errors) > 0
        assert result.used_defaults is True

    def test_missing_file_returns_defaults(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "nonexistent.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        result = ConfigHandler.load_config_with_validation()
        assert result.is_valid is True
        assert result.used_defaults is True
        assert "db_host" in result.config


class TestSaveConfigValidation:
    def test_save_invalid_data_rejected(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        result = ConfigHandler.save_config({"db_port": 99999})
        assert result is False

    def test_save_valid_data_passes(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        result = ConfigHandler.save_config({"db_port": 5433})
        assert result is True

    def test_save_preserves_dynamic_keys(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        ConfigHandler.save_config({"ai_strategy_prompt_oversold": "custom"})
        with open(config_file, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["ai_strategy_prompt_oversold"] == "custom"


class TestLocalAiConfigConsistency:
    def test_n_ctx_default_matches_pydantic(self, monkeypatch, tmp_path):
        config_file = str(tmp_path / "test_settings.json")
        monkeypatch.setattr("utils.config_handler.CONFIG_FILE", config_file)
        ConfigHandler._config_cache = None
        ai_config = ConfigHandler.get_local_ai_config()
        pydantic_default = get_default_config()["local_n_ctx"]
        assert ai_config["n_ctx"] == pydantic_default


class TestPromptDefaultsSingleSource:
    def test_config_models_is_single_source(self):
        from utils.config_handler import DEFAULT_AI_PROMPT as ch_ai, DEFAULT_NEWS_PROMPT as ch_news

        assert ch_ai is DEFAULT_AI_PROMPT
        assert ch_news is DEFAULT_NEWS_PROMPT

    def test_app_config_uses_same_defaults(self):
        cfg = AppConfig()
        assert cfg.ai_system_prompt == DEFAULT_AI_PROMPT
        assert cfg.ai_news_prompt == DEFAULT_NEWS_PROMPT

    def test_get_default_config_uses_same_defaults(self):
        defaults = get_default_config()
        assert defaults["ai_system_prompt"] == DEFAULT_AI_PROMPT
        assert defaults["ai_news_prompt"] == DEFAULT_NEWS_PROMPT

    def test_news_prompt_is_json_classification_format(self):
        assert "JSON" in DEFAULT_NEWS_PROMPT
        assert "category_L1" in DEFAULT_NEWS_PROMPT

    def test_ai_prompt_has_analysis_framework(self):
        assert "分析框架" in DEFAULT_AI_PROMPT
        assert "技术面" in DEFAULT_AI_PROMPT


class TestTusharePointTier:
    def test_point_tier_default_is_custom(self):
        cfg = AppConfig()
        assert cfg.tushare_point_tier == "custom"

    def test_point_tier_accepts_known_tiers(self):
        for tier in ["free", "standard", "pro", "flagship", "custom"]:
            cfg = AppConfig(tushare_point_tier=tier)
            assert cfg.tushare_point_tier == tier

    def test_point_tier_rejects_unknown(self):
        with pytest.raises(ValidationError):
            AppConfig(tushare_point_tier="platinum")

    def test_rate_limit_allows_high_tier_values(self):
        cfg = AppConfig(tushare_api_rate_limit=8000)
        assert cfg.tushare_api_rate_limit == 8000
