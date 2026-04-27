"""
Tests for QualityGate decorator and related utilities.

验证数据质量门控装饰器的正确性。
"""

import asyncio
import unittest
from unittest.mock import MagicMock

from data.persistence.quality_gate import (
    QualityGate,
    QualityGateError,
    QualityTier,
    _check_tier,
    _find_processor,
    require_quality,
)


class TestQualityTier(unittest.TestCase):
    """测试质量层级枚举"""

    def test_tier_comparison(self):
        """层级比较"""
        self.assertTrue(QualityTier.GOLD > QualityTier.SILVER)
        self.assertTrue(QualityTier.SILVER > QualityTier.BRONZE)
        self.assertTrue(QualityTier.BRONZE > QualityTier.CRITICAL)

    def test_tier_int_conversion(self):
        """整数转换"""
        self.assertEqual(int(QualityTier.CRITICAL), 0)
        self.assertEqual(int(QualityTier.BRONZE), 1)
        self.assertEqual(int(QualityTier.SILVER), 2)
        self.assertEqual(int(QualityTier.GOLD), 3)

    def test_tier_from_int(self):
        """从整数创建层级"""
        self.assertEqual(QualityTier(0), QualityTier.CRITICAL)
        self.assertEqual(QualityTier(1), QualityTier.BRONZE)
        self.assertEqual(QualityTier(2), QualityTier.SILVER)
        self.assertEqual(QualityTier(3), QualityTier.GOLD)

    def test_tier_name(self):
        """层级名称"""
        self.assertEqual(QualityTier.GOLD.name, "GOLD")
        self.assertEqual(QualityTier.SILVER.name, "SILVER")


class TestQualityGateError(unittest.TestCase):
    """测试质量门控异常"""

    def test_error_message(self):
        """错误消息"""
        error = QualityGateError("Test error message")
        self.assertEqual(str(error), "Test error message")

    def test_error_inheritance(self):
        """异常继承"""
        error = QualityGateError("Test")
        self.assertIsInstance(error, Exception)

    def test_error_raise(self):
        """异常抛出"""
        with self.assertRaises(QualityGateError):
            raise QualityGateError("Quality check failed")


class TestFindProcessor(unittest.TestCase):
    """测试处理器查找"""

    def test_find_processor_from_instance(self):
        """从实例属性查找"""
        instance = MagicMock()
        instance.data_processor = "processor_instance"

        result = _find_processor(instance, (), {})

        self.assertEqual(result, "processor_instance")

    def test_find_processor_from_kwargs(self):
        """从关键字参数查找"""
        instance = MagicMock()
        instance.data_processor = None

        result = _find_processor(instance, (), {"data_processor": "processor_kwarg"})

        self.assertEqual(result, "processor_kwarg")

    def test_find_processor_from_args_dict(self):
        """从位置参数字典查找"""
        instance = MagicMock()
        instance.data_processor = None

        result = _find_processor(instance, ({"data_processor": "processor_arg"},), {})

        self.assertEqual(result, "processor_arg")

    def test_find_processor_not_found(self):
        """未找到处理器"""
        instance = MagicMock()
        instance.data_processor = None

        result = _find_processor(instance, (), {})

        self.assertIsNone(result)

    def test_find_processor_priority(self):
        """处理器查找优先级 - 实例属性优先"""
        instance = MagicMock()
        instance.data_processor = "instance_processor"

        result = _find_processor(
            instance,
            ({"data_processor": "arg_processor"},),
            {"data_processor": "kwarg_processor"},
        )

        self.assertEqual(result, "instance_processor")


class TestCheckTier(unittest.TestCase):
    """测试层级检查"""

    def test_check_tier_pass(self):
        """质量达标通过"""
        processor = MagicMock()
        processor._quality_tier = QualityTier.SILVER

        _check_tier(processor, QualityTier.BRONZE, "test_func")

    def test_check_tier_fail(self):
        """质量不足拒绝"""
        processor = MagicMock()
        processor._quality_tier = QualityTier.BRONZE

        with self.assertRaises(QualityGateError):
            _check_tier(processor, QualityTier.SILVER, "test_func")

    def test_check_tier_no_processor(self):
        """无处理器时跳过检查"""
        _check_tier(None, QualityTier.GOLD, "test_func")

    def test_check_tier_uninitialized(self):
        """未初始化处理器视为 CRITICAL"""
        processor = MagicMock()
        delattr(processor, "_quality_tier")

        with self.assertRaises(QualityGateError):
            _check_tier(processor, QualityTier.BRONZE, "test_func")

    def test_check_tier_equal(self):
        """相等层级通过"""
        processor = MagicMock()
        processor._quality_tier = QualityTier.SILVER

        _check_tier(processor, QualityTier.SILVER, "test_func")


class TestRequireQualityDecorator(unittest.TestCase):
    """测试质量装饰器"""

    def test_require_quality_sync_pass(self):
        """同步方法质量达标"""

        @require_quality(QualityTier.SILVER)
        def test_method(self):
            return "success"

        instance = MagicMock()
        instance.data_processor = MagicMock()
        instance.data_processor._quality_tier = QualityTier.GOLD

        result = test_method(instance)

        self.assertEqual(result, "success")

    def test_require_quality_sync_fail(self):
        """同步方法质量不足"""

        @require_quality(QualityTier.SILVER)
        def test_method(self):
            return "success"

        instance = MagicMock()
        instance.data_processor = MagicMock()
        instance.data_processor._quality_tier = QualityTier.BRONZE

        with self.assertRaises(QualityGateError):
            test_method(instance)

    def test_require_quality_async_pass(self):
        """异步方法质量达标"""

        @require_quality(QualityTier.SILVER)
        async def test_method(self):
            return "async_success"

        instance = MagicMock()
        instance.data_processor = MagicMock()
        instance.data_processor._quality_tier = QualityTier.GOLD

        result = asyncio.run(test_method(instance))

        self.assertEqual(result, "async_success")

    def test_require_quality_async_fail(self):
        """异步方法质量不足"""

        @require_quality(QualityTier.SILVER)
        async def test_method(self):
            return "async_success"

        instance = MagicMock()
        instance.data_processor = MagicMock()
        instance.data_processor._quality_tier = QualityTier.BRONZE

        async def run_test():
            with self.assertRaises(QualityGateError):
                await test_method(instance)

        asyncio.run(run_test())

    def test_require_quality_no_processor(self):
        """无处理器时跳过检查"""

        @require_quality(QualityTier.GOLD)
        def test_method(self):
            return "bypassed"

        instance = MagicMock()
        instance.data_processor = None

        result = test_method(instance)

        self.assertEqual(result, "bypassed")

    def test_require_quality_preserves_function_name(self):
        """保留原函数名"""

        @require_quality(QualityTier.SILVER)
        def my_special_function(self):
            return "test"

        self.assertEqual(my_special_function.__name__, "my_special_function")

    def test_require_quality_with_kwargs_processor(self):
        """从 kwargs 获取处理器"""

        @require_quality(QualityTier.SILVER)
        def test_method(self, **kwargs):
            return "success"

        instance = MagicMock()
        instance.data_processor = None

        processor = MagicMock()
        processor._quality_tier = QualityTier.GOLD

        result = test_method(instance, data_processor=processor)

        self.assertEqual(result, "success")


class TestQualityGateClass(unittest.TestCase):
    """测试 QualityGate 类"""

    def test_quality_gate_exists(self):
        """QualityGate 类存在"""
        gate = QualityGate()
        self.assertIsNotNone(gate)


class TestComputeTier(unittest.TestCase):
    """测试 _compute_tier 统一 Tier 计算函数"""

    def test_critical_missing_tables(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.9, missing_critical=True), 0)

    def test_bronze_extreme_lag(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=10, fin_fresh_ratio=0.9, missing_critical=False), 1)

    def test_bronze_moderate_lag(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=6, fin_fresh_ratio=0.9, missing_critical=False), 1)

    def test_gold_high_fin_ratio(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False), 3)

    def test_gold_fresh_fin_date(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10), 3)

    def test_silver_fresh_quotes_moderate_fin(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=2, fin_fresh_ratio=0.6, missing_critical=False), 2)

    def test_silver_small_lag(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=3, fin_fresh_ratio=0.3, missing_critical=False), 2)

    def test_stale_fin_date_no_gold(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=200), 2)

    def test_fast_path_uses_fin_lag_over_ratio(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.3, missing_critical=False, fin_lag_days=10), 2)

    def test_fast_path_gold_requires_both_fin_lag_and_ratio(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.6, missing_critical=False, fin_lag_days=10), 3)

    def test_bronze_low_fin_ratio_with_lag(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=4, fin_fresh_ratio=0.2, missing_critical=False), 2)

    def test_bronze_zero_fin_ratio(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.0, missing_critical=False), 1)

    def test_bronze_near_zero_fin_ratio(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.05, missing_critical=False), 1)

    def test_silver_min_fin_ratio_with_small_lag(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=3, fin_fresh_ratio=0.1, missing_critical=False), 2)

    def test_silver_low_avg_fundamental(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(_compute_tier(lag_days=0, fin_fresh_ratio=0.9, missing_critical=False, avg_fundamental=0.2), 2)

    def test_gold_blocked_by_avg_fundamental(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False, avg_fundamental=0.5), 2
        )

    def test_gold_with_high_avg_fundamental(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(
            _compute_tier(lag_days=0, fin_fresh_ratio=0.95, missing_critical=False, avg_fundamental=0.8), 3
        )

    def test_gold_fresh_fin_date_with_avg_fundamental(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(
            _compute_tier(
                lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10, avg_fundamental=0.8
            ),
            3,
        )

    def test_gold_fresh_fin_date_blocked_by_avg_fundamental(self):
        from data.mixins.health_mixin import _compute_tier

        self.assertEqual(
            _compute_tier(
                lag_days=0, fin_fresh_ratio=0.5, missing_critical=False, fin_lag_days=10, avg_fundamental=0.6
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
