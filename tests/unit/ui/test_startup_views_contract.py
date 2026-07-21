"""ui/startup_views.py 色值契约守护测试 (P1-2).

批次 1 主题批次新增契约：
- 禁止裸用 ft.Colors.RED / ft.Colors.RED_400
- 必须使用 AppColors.ERROR (Layer 2 业务语义色)
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _raw_source() -> str:
    """读取 startup_views.py 原始源码。"""
    import ui.startup_views as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


class TestStartupViewsColorContract:
    """startup_views 色值契约守护 (P1-2)。"""

    def test_no_bare_ft_colors_red(self):
        """P1-2 契约: startup_views 不再裸用 ft.Colors.RED。

        §0.5.11.1 #67: L170 ft.Colors.RED 已替换为 AppColors.ERROR。
        """
        source = _raw_source()
        import re

        red_bare = re.findall(r"ft\.Colors\.RED\b(?!_)", source)
        assert not red_bare, f"startup_views 不应裸用 ft.Colors.RED (P1-2): 发现 {len(red_bare)} 处"

    def test_no_bare_ft_colors_red_400(self):
        """P1-2 契约: startup_views 不再裸用 ft.Colors.RED_400。

        §0.5.11.1 #67: L180 ft.Colors.RED_400 已替换为 AppColors.ERROR。
        """
        source = _raw_source()
        assert "ft.Colors.RED_400" not in source, "startup_views 不应裸用 ft.Colors.RED_400 (P1-2)"

    def test_uses_app_colors_error(self):
        """P1-2 契约: startup_views 必须使用 AppColors.ERROR。

        正向守护：错误图标/文本色应通过 AppColors.ERROR 引用 (Layer 2 业务色)。
        """
        source = _raw_source()
        assert "AppColors.ERROR" in source, "startup_views 必须使用 AppColors.ERROR (P1-2 Layer 2 业务色)"
        # 验证 import AppColors
        assert "from ui.theme import" in source and "AppColors" in source, (
            "startup_views 必须从 ui.theme 导入 AppColors"
        )
