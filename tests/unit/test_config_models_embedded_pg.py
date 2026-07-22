"""AppConfig.embedded_pg_* 字段单元测试（Phase 2 §3.2）。

验证：
- 默认值正确
- 超范围值抛 ValidationError
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from utils.config_models import AppConfig


class TestEmbeddedPgFields:
    def test_app_config_has_embedded_pg_fields_with_defaults(self) -> None:
        config = AppConfig()
        assert config.embedded_pg_enabled is False
        assert config.embedded_pg_sidecar_path == ""
        assert config.embedded_pg_data_root == ""
        assert config.embedded_pg_install_root == ""
        assert config.embedded_pg_log_dir == ""
        assert config.embedded_pg_start_timeout_s == 300.0
        assert config.embedded_pg_stop_timeout_s == 60.0
        assert config.embedded_pg_listen == "127.0.0.1"
        assert config.embedded_pg_username == "qtrading"
        assert config.embedded_pg_database == "qtrading"

    def test_app_config_embedded_pg_fields_validate_ranges(self) -> None:
        # match= 验证错误确实来自对应字段校验（而非其他字段的连带错误）
        with pytest.raises(ValidationError, match="embedded_pg_start_timeout_s"):
            AppConfig(embedded_pg_start_timeout_s=5.0)  # < 10.0
        with pytest.raises(ValidationError, match="embedded_pg_start_timeout_s"):
            AppConfig(embedded_pg_start_timeout_s=700.0)  # > 600.0
        with pytest.raises(ValidationError, match="embedded_pg_stop_timeout_s"):
            AppConfig(embedded_pg_stop_timeout_s=2.0)  # < 5.0
        with pytest.raises(ValidationError, match="embedded_pg_stop_timeout_s"):
            AppConfig(embedded_pg_stop_timeout_s=200.0)  # > 120.0

    def test_app_config_embedded_pg_enabled_can_be_toggled(self) -> None:
        config = AppConfig(embedded_pg_enabled=True)
        assert config.embedded_pg_enabled is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
