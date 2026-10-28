"""UIR-F15-001: 数据库模式解析与持久化偏好单元测试。

覆盖 ``utils.config.db.resolve_database_mode`` 的优先级（环境变量 / 持久化偏好 /
旧配置回退 / 标准版自动降级）与 ``save_database_mode`` 的严格成功判定。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.config.db import DatabaseMode, resolve_database_mode, save_database_mode
from utils.config_models import AppConfig, get_default_config

pytestmark = pytest.mark.unit


def _cfg(**overrides) -> dict:
    config = get_default_config()
    config.update(overrides)
    return config


@pytest.fixture(autouse=True)
def _clear_mode_env(monkeypatch):
    monkeypatch.delenv("QTRADING_DATABASE_MODE", raising=False)


class TestResolveDatabaseMode:
    def test_default_config_is_embedded(self) -> None:
        resolution = resolve_database_mode(_cfg())

        assert resolution.mode is DatabaseMode.EMBEDDED
        assert resolution.configured_mode is DatabaseMode.EMBEDDED
        assert resolution.env_mode is None
        assert resolution.conflict is False
        assert resolution.restart_required is False

    def test_persisted_external_mode_wins_without_env(self) -> None:
        resolution = resolve_database_mode(_cfg(db_mode="external", embedded_pg_enabled=False))

        assert resolution.mode is DatabaseMode.EXTERNAL
        assert resolution.source == "config"
        assert resolution.configured_mode is DatabaseMode.EXTERNAL
        assert resolution.restart_required is False

    def test_inconsistent_external_mode_and_enabled_still_external(self) -> None:
        resolution = resolve_database_mode(_cfg(db_mode="external", embedded_pg_enabled=True))

        assert resolution.mode is DatabaseMode.EXTERNAL
        assert resolution.configured_mode is DatabaseMode.EXTERNAL

    def test_legacy_config_without_db_mode_falls_back_to_enabled_flag(self) -> None:
        config = _cfg(embedded_pg_enabled=False)
        config.pop("db_mode")

        resolution = resolve_database_mode(config)

        assert resolution.mode is DatabaseMode.EXTERNAL
        assert resolution.configured_mode is DatabaseMode.EXTERNAL
        assert resolution.conflict is False

    def test_legacy_appconfig_without_db_mode_keeps_external_user_external(self) -> None:
        """升级路径：旧配置无 db_mode 键，AppConfig 默认值不得把外置用户拉回内置模式。"""
        config = _cfg(embedded_pg_enabled=False)
        config.pop("db_mode")
        model = AppConfig.model_validate(config)

        resolution = resolve_database_mode(model)

        assert resolution.mode is DatabaseMode.EXTERNAL
        assert resolution.configured_mode is DatabaseMode.EXTERNAL
        assert resolution.source == "config"
        assert resolution.restart_required is False

    def test_env_external_overrides_embedded_preference(self, monkeypatch) -> None:
        monkeypatch.setenv("QTRADING_DATABASE_MODE", "external")

        resolution = resolve_database_mode(_cfg())

        assert resolution.mode is DatabaseMode.EXTERNAL
        assert resolution.source == "env"
        assert resolution.env_mode is DatabaseMode.EXTERNAL
        assert resolution.conflict is True
        assert resolution.restart_required is True

    def test_env_embedded_overrides_external_preference(self, monkeypatch) -> None:
        monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

        resolution = resolve_database_mode(_cfg(db_mode="external", embedded_pg_enabled=True))

        assert resolution.mode is DatabaseMode.EMBEDDED
        assert resolution.source == "env"
        assert resolution.env_mode is DatabaseMode.EMBEDDED
        assert resolution.conflict is True
        assert resolution.restart_required is True

    def test_standard_variant_without_sidecar_falls_back_to_external(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        resolution = resolve_database_mode(_cfg(embedded_pg_sidecar_path=""))

        assert resolution.mode is DatabaseMode.EXTERNAL
        assert resolution.source == "fallback_standard_variant"
        assert resolution.fallback_reason == "standard_variant"

    def test_load_config_failure_defaults_to_embedded(self) -> None:
        with patch("utils.config_handler.ConfigHandler.load_config", side_effect=RuntimeError("boom")):
            resolution = resolve_database_mode(None)

        assert resolution.mode is DatabaseMode.EMBEDDED


class TestSaveDatabaseMode:
    def test_writes_mode_and_enabled_flag(self) -> None:
        with patch("utils.config_handler.ConfigHandler.save_config", return_value=True) as save:
            assert save_database_mode(DatabaseMode.EXTERNAL) is True

        save.assert_called_once_with({"db_mode": "external", "embedded_pg_enabled": False})

    def test_writes_embedded_mode(self) -> None:
        with patch("utils.config_handler.ConfigHandler.save_config", return_value=True) as save:
            assert save_database_mode("embedded") is True

        save.assert_called_once_with({"db_mode": "embedded", "embedded_pg_enabled": True})

    def test_non_true_result_is_failure(self) -> None:
        with patch("utils.config_handler.ConfigHandler.save_config", return_value=None):
            assert save_database_mode(DatabaseMode.EXTERNAL) is False

    def test_invalid_mode_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unsupported database mode"):
            save_database_mode("sqlite")
