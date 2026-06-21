"""契约测试：确保 mock fixture 覆盖生产代码的公开属性/方法。

生产侧新增属性/方法时，此测试提醒同步 mock。

注意：MagicMock 会自动创建属性，导致 hasattr 永远返回 True。
因此用 `attr in mock.__dict__` 检查属性是否被显式赋值
（显式赋值的属性出现在 __dict__ 中，自动创建的不出现）。
"""

import pytest

from ui.theme import AppColors, AppStyles

pytestmark = pytest.mark.unit


def _public_attrs(cls):
    """获取类自身定义的公开属性名（不含继承自 object 的成员）。"""
    return {name for name in vars(cls) if not name.startswith("_")}


def test_mock_app_colors_covers_real_attributes(mock_app_colors):
    """mock_app_colors 必须覆盖 AppColors 的所有公开非 callable 属性。

    覆盖 ui/theme.py:305-428 的 AppColors 类属性（颜色常量）。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_attrs = {name for name in _public_attrs(AppColors) if not callable(getattr(AppColors, name))}
    for attr in real_attrs:
        assert attr in mock_app_colors.__dict__, f"mock_app_colors.{attr} 未显式赋值（参考 AppColors.{attr})"


def test_mock_app_colors_covers_real_methods(mock_app_colors):
    """mock_app_colors 必须覆盖 AppColors 的所有公开方法（classmethod）。

    覆盖 ui/theme.py:374-428 的 subscribe/unsubscribe/load_theme。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_methods = {name for name in _public_attrs(AppColors) if callable(getattr(AppColors, name))}
    for method in real_methods:
        assert method in mock_app_colors.__dict__, f"mock_app_colors.{method} 未显式赋值（参考 AppColors.{method})"


def test_mock_app_styles_covers_real_attributes(mock_app_styles):
    """mock_app_styles 必须覆盖 AppStyles 的所有公开非 callable 属性。

    覆盖 ui/theme.py:434-438 的 CONTROL_WIDTH_* 常量。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_attrs = {name for name in _public_attrs(AppStyles) if not callable(getattr(AppStyles, name))}
    for attr in real_attrs:
        assert attr in mock_app_styles.__dict__, f"mock_app_styles.{attr} 未显式赋值（参考 AppStyles.{attr})"


def test_mock_app_styles_covers_real_methods(mock_app_styles):
    """mock_app_styles 必须覆盖 AppStyles 的所有公开方法（staticmethod）。

    覆盖 ui/theme.py:440-540 的 card/dashboard_card/primary_button 等。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_methods = {name for name in _public_attrs(AppStyles) if callable(getattr(AppStyles, name))}
    for method in real_methods:
        assert method in mock_app_styles.__dict__, f"mock_app_styles.{method} 未显式赋值（参考 AppStyles.{method})"


def test_mock_config_handler_uses_autospec(mock_config_handler):
    """验证 mock_config_handler 基于 create_autospec，提供签名级保护。

    P2-2a: 原"反向契约测试"（遍历 __dict__ 检查方法存在性）会空转——
    create_autospec 会把 ConfigHandler 的所有方法预填充到 __dict__，
    导致测试退化为同义反复。autospec 本身已提供等价保护：
    访问不存在的方法时抛 AttributeError，调用签名不匹配时抛 TypeError。
    本测试验证 autospec 保护生效（访问不存在的方法应抛异常）。
    """
    # autospec 后，访问 ConfigHandler 上不存在的方法应抛 AttributeError
    with pytest.raises(AttributeError):
        _ = mock_config_handler.nonexistent_method_never_in_config_handler
