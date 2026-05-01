import pytest

from data.persistence.quality_gate import _STRICT_QUALITY_GATE, QualityGateError, _check_tier


class TestQualityGateStrictMode:
    """Quality Gate STRICT 模式"""

    def test_strict_mode_env_var_exists(self):
        assert isinstance(_STRICT_QUALITY_GATE, bool)

    def test_strict_mode_raises_on_missing_processor(self):
        if not _STRICT_QUALITY_GATE:
            pytest.skip("STRICT_QUALITY_GATE not enabled")
        with pytest.raises(QualityGateError, match="STRICT mode"):
            _check_tier(None, 1, "test_func")
