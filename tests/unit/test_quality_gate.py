"""
Tests for QualityGate decorator and related utilities.

验证数据质量门控装饰器的正确性。
"""

import pytest
from unittest.mock import MagicMock, patch

from data.mixins.health_mixin import _compute_tier
from data.persistence.quality_gate import (
    QualityGate,
    QualityGateError,
    QualityTier,
    _check_tier,
    _find_processor,
    require_quality,
)


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

    def test_tier_int_conversion(self):
        assert int(QualityTier.CRITICAL) == 0
        assert int(QualityTier.BRONZE) == 1
        assert int(QualityTier.SILVER) == 2
        assert int(QualityTier.GOLD) == 3

    def test_tier_from_int(self):
        assert QualityTier(0) == QualityTier.CRITICAL
        assert QualityTier(1) == QualityTier.BRONZE
        assert QualityTier(2) == QualityTier.SILVER
        assert QualityTier(3) == QualityTier.GOLD

    def test_tier_name(self):
        assert QualityTier.GOLD.name == "GOLD"
        assert QualityTier.SILVER.name == "SILVER"


class TestQualityGateError:
    def test_error_message(self):
        error = QualityGateError("Test error message")
        assert str(error) == "Test error message"

    def test_error_inheritance(self):
        error = QualityGateError("Test")
        assert isinstance(error, Exception)

    def test_error_raise(self):
        with pytest.raises(QualityGateError):
            raise QualityGateError("Quality check failed")


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

    def test_priority_instance_over_kwargs_and_args(self):
        instance = MagicMock()
        instance.data_processor = "instance_processor"
        result = _find_processor(
            instance,
            ({"data_processor": "arg_processor"},),
            {"data_processor": "kwarg_processor"},
        )
        assert result == "instance_processor"


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

    @patch("core.i18n.I18n")
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

    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_equal_tier_passes(self):
        processor = MagicMock()
        processor._quality_tier = QualityTier.SILVER
        _check_tier(processor, QualityTier.SILVER, "test_func")


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

    @patch("core.i18n.I18n")
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

    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_no_processor_bypasses(self):
        class MyStrategy:
            data_processor = None

            @require_quality(QualityTier.GOLD)
            def run(self):
                return "bypassed"

        s = MyStrategy()
        assert s.run() == "bypassed"

    def test_preserves_function_name(self):
        @require_quality(QualityTier.SILVER)
        def my_special_function(self):
            return "test"

        assert my_special_function.__name__ == "my_special_function"

    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    def test_with_kwargs_processor(self):
        class MyStrategy:
            data_processor = None

            @require_quality(QualityTier.SILVER)
            def run(self, **kwargs):
                return "success"

        s = MyStrategy()
        processor = MagicMock()
        processor._quality_tier = QualityTier.GOLD
        result = s.run(data_processor=processor)
        assert result == "success"

    @pytest.mark.asyncio
    @patch("data.persistence.quality_gate._STRICT_QUALITY_GATE", False)
    async def test_async_decorator_insufficient_raises(self):
        class MyStrategy:
            data_processor = MagicMock()
            data_processor._quality_tier = QualityTier.BRONZE

            @require_quality(QualityTier.SILVER)
            async def run(self):
                return "async_success"

        s = MyStrategy()
        with pytest.raises(QualityGateError):
            await s.run()


class TestQualityGateClass:
    def test_quality_gate_exists(self):
        gate = QualityGate()
        assert gate is not None


class TestComputeTier:
    def test_critical_missing_tables(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.9, missing_critical=True) == 0

    def test_bronze_extreme_lag(self):
        assert _compute_tier(lag_days=10, fin_fresh_ratio=0.9, missing_critical=False) == 1

    def test_bronze_moderate_lag(self):
        assert _compute_tier(lag_days=6, fin_fresh_ratio=0.9, missing_critical=False) == 1

    def test_gold_high_fin_ratio(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False, avg_fundamental=0.8) == 3

    def test_gold_fresh_fin_date(self):
        assert (
            _compute_tier(lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10, avg_fundamental=0.8)
            == 3
        )

    def test_silver_fresh_quotes_moderate_fin(self):
        assert _compute_tier(lag_days=2, fin_fresh_ratio=0.6, missing_critical=False) == 2

    def test_silver_small_lag(self):
        assert _compute_tier(lag_days=3, fin_fresh_ratio=0.3, missing_critical=False) == 2

    def test_stale_fin_date_no_gold(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=200) == 2

    def test_fast_path_low_ratio_with_fin_lag_no_gold(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.3, missing_critical=False, fin_lag_days=10) == 2

    def test_fast_path_gold_requires_both_fin_lag_and_ratio(self):
        assert (
            _compute_tier(lag_days=0, fin_fresh_ratio=0.6, missing_critical=False, fin_lag_days=10, avg_fundamental=0.8)
            == 3
        )

    def test_bronze_low_fin_ratio_with_lag(self):
        assert _compute_tier(lag_days=4, fin_fresh_ratio=0.2, missing_critical=False) == 2

    def test_bronze_zero_fin_ratio(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.0, missing_critical=False) == 1

    def test_bronze_near_zero_fin_ratio(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.05, missing_critical=False) == 1

    def test_silver_min_fin_ratio_with_small_lag(self):
        assert _compute_tier(lag_days=3, fin_fresh_ratio=0.1, missing_critical=False) == 2

    def test_silver_low_avg_fundamental(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.9, missing_critical=False, avg_fundamental=0.2) == 2

    def test_gold_blocked_by_avg_fundamental(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False, avg_fundamental=0.5) == 2

    def test_gold_with_high_avg_fundamental(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False, avg_fundamental=0.8) == 3

    def test_gold_fresh_fin_date_with_avg_fundamental(self):
        assert (
            _compute_tier(lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10, avg_fundamental=0.8)
            == 3
        )

    def test_gold_fresh_fin_date_blocked_by_avg_fundamental(self):
        assert (
            _compute_tier(lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10, avg_fundamental=0.6)
            == 2
        )

    def test_fast_path_no_gold_without_avg_fundamental(self):
        assert _compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False, avg_fundamental=None) == 2

    def test_fast_path_no_gold_without_avg_fundamental_even_with_fin_lag(self):
        assert (
            _compute_tier(
                lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10, avg_fundamental=None
            )
            == 2
        )

    def test_tier_consistency_fast_vs_deep(self):
        snapshot = dict(lag_days=0, fin_fresh_ratio=0.8, missing_critical=False, fin_lag_days=5)
        fast_tier = _compute_tier(**snapshot, avg_fundamental=None)
        deep_tier = _compute_tier(**snapshot, avg_fundamental=0.85)
        assert fast_tier <= deep_tier
        assert fast_tier == 2
        assert deep_tier == 3
