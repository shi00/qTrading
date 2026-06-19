import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_settings_page_loads(e2e_page):
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=15000)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=10000)


@pytest.mark.mutates_config
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

    # [PITFALL_WARNING] 主题切换 flaky 防护
    # 坑点：若 session app 当前主题已是目标主题，Dropdown 的 on_change 事件不会触发，
    #       导致 snackbar 不弹出，测试 flaky 失败。
    # 应对：先切到一个确定状态（深色），再切到目标状态（浅色），保证状态必变化。
    theme_dark = I18n.get("theme_dark")
    await e2e_page.select_dropdown(theme_label, theme_dark)

    # 等待前一次切换的 snackbar 动画完成，防止 CanvasKit 吞噬点击事件
    await e2e_page.page.wait_for_timeout(1000)

    theme_light = I18n.get("theme_light")
    await e2e_page.select_dropdown(theme_label, theme_light)

    theme_updated = I18n.get("settings_snack_theme_updated")
    await e2e_page.expect_text(theme_updated, timeout_ms=5000)

    # 强断言：验证主题色真实变化（深色 → 浅色），而非仅 snackbar 提示
    is_light_bg = await e2e_page.page.evaluate(
        """() => {
            const candidates = ['flutter-view', 'flt-glass-pane', 'body'];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (!el) continue;
                const c = getComputedStyle(el).backgroundColor;
                const parts = c.match(/\\d+/g);
                if (!parts || parts.length < 3) continue;
                if (parseInt(parts[0]) > 200 && parseInt(parts[1]) > 200 && parseInt(parts[2]) > 200) return true;
            }
            return false;
        }"""
    )
    assert is_light_bg, "切换到浅色主题后，页面背景色应为浅色"


@pytest.mark.mutates_config
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

    # 等待前一个测试的 Snackbar 动画完成，防止 CanvasKit 吞噬点击事件
    await e2e_page.page.wait_for_timeout(1000)

    lang_label = I18n.get("settings_language")
    lang_en = I18n.get("settings_lang_en")
    await e2e_page.select_dropdown(lang_label, lang_en, timeout_ms=10000)

    # 手动同步测试进程的 I18n 状态，以便生成正确的英文断言字符串
    I18n.set_locale("en_US")

    # 验证 UI 文本已切换为英文（导航栏 "Screener" 出现）
    await e2e_page.expect_text("Screener", timeout_ms=10000)

    # 强断言：验证设置页文案已切换为英文（而非仅导航栏变化）
    theme_label_en = I18n.get("settings_theme")
    await e2e_page.expect_text(theme_label_en, timeout_ms=10000)

    # 切回中文之前的验证
    actual_lang_label_en = I18n.get_language_label()
    await e2e_page.expect_text(actual_lang_label_en, timeout_ms=10000)

    # 配置还原由 pristine_config fixture 自动处理（见 tests/e2e/conftest.py）
