import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_settings_page_loads(e2e_page):
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=15000)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=10000)


async def test_settings_theme_switch(e2e_page):
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=15000)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=10000)

    tab_system = I18n.get("settings_tab_system")
    # 使用 click_text 而不是 click_tab，因为 click_tab 内部的 get_by_text 是 exact=False
    # 如果 exact=False，"系统" 可能会匹配到页面标题 "系统设置"，导致点击无效
    await e2e_page.click_text(tab_system, timeout_ms=8000)

    # 核心配置由于 CanvasKit 渲染为非独立语义节点可能无法被 get_by_text 获取
    # 直接验证主题下拉框的存在，这足以证明 System Tab 已加载
    theme_label = I18n.get("settings_theme")
    await e2e_page.expect_text(theme_label, timeout_ms=10000)

    theme_light = I18n.get("theme_light")
    await e2e_page.select_dropdown(theme_label, theme_light)

    theme_updated = I18n.get("settings_snack_theme_updated")
    await e2e_page.expect_text(theme_updated, timeout_ms=5000)


async def test_settings_language_switch(e2e_page):
    """D4: 设置页语言切换后 UI 文本更新。"""
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=15000)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=10000)

    tab_system = I18n.get("settings_tab_system")
    await e2e_page.click_text(tab_system, timeout_ms=8000)

    # 切换语言为 English
    # 注意：语言下拉框的实际 label 是 get_language_label()（如 "简体中文 / English"），而不是 "语言"
    actual_lang_label = I18n.get_language_label()
    await e2e_page.expect_text(actual_lang_label, timeout_ms=10000)  # Wait for the tab to render

    lang_label = I18n.get("settings_language")
    lang_en = I18n.get("settings_lang_en")
    await e2e_page.select_dropdown(lang_label, lang_en, timeout_ms=10000)

    # 验证 UI 文本已切换为英文（导航栏 "Screener" 出现）
    # 不验证 SnackBar 文本，因为 SnackBar 显示时 locale 已切换，文本语言不可预测
    await e2e_page.expect_text("Screener", timeout_ms=10000)

    # 切回中文，避免影响后续测试
    lang_zh = I18n.get("settings_lang_zh")
    await e2e_page.select_dropdown(lang_label, lang_zh, timeout_ms=10000)
    await e2e_page.expect_text("选股", timeout_ms=10000)
