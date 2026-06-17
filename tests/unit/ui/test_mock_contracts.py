"""契约测试：确保 mock fixture 覆盖生产代码的公开属性/方法。

生产侧新增属性/方法时，此测试提醒同步 mock。

注意：MagicMock 会自动创建属性，导致 hasattr 永远返回 True。
因此用 `attr in mock.__dict__` 检查属性是否被显式赋值
（显式赋值的属性出现在 __dict__ 中，自动创建的不出现）。
"""

from ui.theme import AppColors, AppStyles


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
        assert attr in mock_app_colors.__dict__, f"mock_app_colors.{attr} 未显式赋值（参考 AppColors.{attr}）"


def test_mock_app_colors_covers_real_methods(mock_app_colors):
    """mock_app_colors 必须覆盖 AppColors 的所有公开方法（classmethod）。

    覆盖 ui/theme.py:374-428 的 subscribe/unsubscribe/load_theme。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_methods = {name for name in _public_attrs(AppColors) if callable(getattr(AppColors, name))}
    for method in real_methods:
        assert method in mock_app_colors.__dict__, f"mock_app_colors.{method} 未显式赋值（参考 AppColors.{method}）"


def test_mock_app_styles_covers_real_attributes(mock_app_styles):
    """mock_app_styles 必须覆盖 AppStyles 的所有公开非 callable 属性。

    覆盖 ui/theme.py:434-438 的 CONTROL_WIDTH_* 常量。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_attrs = {name for name in _public_attrs(AppStyles) if not callable(getattr(AppStyles, name))}
    for attr in real_attrs:
        assert attr in mock_app_styles.__dict__, f"mock_app_styles.{attr} 未显式赋值（参考 AppStyles.{attr}）"


def test_mock_app_styles_covers_real_methods(mock_app_styles):
    """mock_app_styles 必须覆盖 AppStyles 的所有公开方法（staticmethod）。

    覆盖 ui/theme.py:440-540 的 card/dashboard_card/primary_button 等。
    用 __dict__ 检查确保被显式赋值（非自动创建）。
    """
    real_methods = {name for name in _public_attrs(AppStyles) if callable(getattr(AppStyles, name))}
    for method in real_methods:
        assert method in mock_app_styles.__dict__, f"mock_app_styles.{method} 未显式赋值（参考 AppStyles.{method}）"
