import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_switch_to_settings(e2e_page):
    """测试：从市场页切换到设置页。"""
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=15000)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=10000)


async def test_switch_to_screener(e2e_page):
    """测试：从市场页切换到选股页。"""
    screener_label = I18n.get("nav_screener")
    await e2e_page.click_text(screener_label, timeout_ms=15000)

    screener_title = I18n.get("screener_title")
    await e2e_page.expect_text(screener_title, timeout_ms=10000)
