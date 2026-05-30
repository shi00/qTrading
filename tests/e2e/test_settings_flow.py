import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_settings_page_loads(e2e_page):
    """测试：设置页能正常加载。"""
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label)

    settings_title = I18n.get("settings_title")
    await e2e_page.page.get_by_text(settings_title, exact=True).first.wait_for(state="attached", timeout=8000)


async def test_settings_theme_switch(e2e_page):
    """测试：设置页主题切换。"""
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label)

    settings_title = I18n.get("settings_title")
    await e2e_page.page.get_by_text(settings_title, exact=True).first.wait_for(state="attached", timeout=8000)

    tab_system = I18n.get("settings_tab_system")
    await e2e_page.click_tab(tab_system)

    core_config_label = I18n.get("sys_core_config")
    await e2e_page.expect_text(core_config_label)

    theme_label = I18n.get("settings_theme")
    await e2e_page.expect_text(theme_label)

    theme_light = I18n.get("theme_light")
    await e2e_page.select_dropdown(theme_label, theme_light)

    theme_updated = I18n.get("settings_snack_theme_updated")
    await e2e_page.expect_text(theme_updated, timeout_ms=5000)
