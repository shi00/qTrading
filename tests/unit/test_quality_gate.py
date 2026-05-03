import pytest
from unittest.mock import patch, MagicMock

from data.persistence.quality_gate import QualityTier, QualityGateError, _find_processor, _check_tier, require_quality


class TestQualityTier:
    def test_critical(self):
        assert QualityTier.CRITICAL == 0

    def test_bronze(self):
        assert QualityTier.BRONZE == 1

    def test_silver(self):
        assert QualityTier.SILVER == 2

    def test_gold(self):
        assert QualityTier.GOLD == 3

    def test_ordering(self):
        assert QualityTier.CRITICAL < QualityTier.BRONZE < QualityTier.SILVER < QualityTier.GOLD


class TestFindProcessor:
    def test_from_instance_attr(self):
        obj = MagicMock()
        obj.data_processor = "processor_instance"
        result = _find_processor(obj, (), {})
        assert result == "processor_instance"

    def test_from_kwargs(self):
        obj = MagicMock(spec=[])
        result = _find_processor(obj, (), {"data_processor": "from_kwargs"})
        assert result == "from_kwargs"

    def test_from_args_dict(self):
        obj = MagicMock(spec=[])
        result = _find_processor(obj, ({"data_processor": "from_args"},), {})
        assert result == "from_args"

    def test_not_found(self):
        obj = MagicMock(spec=[])
        result = _find_processor(obj, (), {})
        assert result is None


class TestCheckTier:
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_no_processor_non_strict(self):
        _check_tier(None, QualityTier.SILVER, "test_func")

    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", True)
    def test_no_processor_strict_raises(self):
        with pytest.raises(QualityGateError, match="STRICT mode"):
            _check_tier(None, QualityTier.SILVER, "test_func")

    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_processor_tier_sufficient(self):
        processor = MagicMock()
        processor._quality_tier = QualityTier.SILVER
        _check_tier(processor, QualityTier.BRONZE, "test_func")

    @patch("ui.i18n.I18n")
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_processor_tier_insufficient_raises(self, mock_i18n):
        mock_i18n.get.return_value = "quality_err_too_low"
        processor = MagicMock()
        processor._quality_tier = QualityTier.BRONZE
        with pytest.raises(QualityGateError):
            _check_tier(processor, QualityTier.SILVER, "test_func")

    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_processor_no_tier_treated_as_critical(self):
        processor = MagicMock(spec=[])
        with pytest.raises(QualityGateError):
            _check_tier(processor, QualityTier.BRONZE, "test_func")


class TestRequireQualityDecorator:
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_sync_decorator_passes(self):
        class MyStrategy:
            data_processor = MagicMock()
            data_processor._quality_tier = QualityTier.GOLD

            @require_quality(QualityTier.SILVER)
            def run(self):
                return "success"

        s = MyStrategy()
        assert s.run() == "success"

    @pytest.mark.asyncio
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    async def test_async_decorator_passes(self):
        class MyStrategy:
            data_processor = MagicMock()
            data_processor._quality_tier = QualityTier.GOLD

            @require_quality(QualityTier.SILVER)
            async def run(self):
                return "success"

        s = MyStrategy()
        assert await s.run() == "success"

    @patch("ui.i18n.I18n")
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_sync_decorator_insufficient_raises(self, mock_i18n):
        mock_i18n.get.return_value = "quality_err_too_low"

        class MyStrategy:
            data_processor = MagicMock()
            data_processor._quality_tier = QualityTier.BRONZE

            @require_quality(QualityTier.SILVER)
            def run(self):
                return "success"

        s = MyStrategy()
        with pytest.raises(QualityGateError):
            s.run()
