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

    # 等待前一个测试的 Snackbar 动画完成，防止 CanvasKit 吞噬点击事件
    await e2e_page.page.wait_for_timeout(1000)

    try:
        lang_label = I18n.get("settings_language")
        lang_en = I18n.get("settings_lang_en")
        await e2e_page.select_dropdown(lang_label, lang_en, timeout_ms=10000)

        # 手动同步测试进程的 I18n 状态，以便生成正确的英文断言字符串
        I18n.set_locale("en_US")

        # 验证 UI 文本已切换为英文（导航栏 "Screener" 出现）
        await e2e_page.expect_text("Screener", timeout_ms=10000)

        # 切回中文之前的验证
        actual_lang_label_en = I18n.get_language_label()
        await e2e_page.expect_text(actual_lang_label_en, timeout_ms=10000)

        await e2e_page.page.wait_for_timeout(1000)

    finally:
        # 无论测试中途是否报错，都必须尝试切回中文，否则 session 级 app 将永久处于英文，导致后续所有测试失败
        try:
            # 此时的 UI 在英文状态下，我们使用英文的 label ("Language") 查找下拉框
            # 如果 I18n 已经是 en_US，则会返回 "Language"
            lang_label_en = I18n.get("settings_language")
            # 我们想选的选项是 "简体中文"
            lang_zh = I18n.get("settings_lang_zh", locale="zh_CN")

            # 使用 force 和短暂的 timeout 尝试恢复
            await e2e_page.select_dropdown(lang_label_en, lang_zh, timeout_ms=8000)
        except Exception as e:
            # 如果恢复失败，打印错误但不掩盖原测试的异常
            import logging

            logging.getLogger(__name__).error(f"Failed to restore language to zh_CN in finally block: {e}")
        finally:
            # 同步恢复测试进程的 I18n 状态
            I18n.set_locale("zh_CN")
            # 稍作等待以确保 UI 回到中文
            await e2e_page.page.wait_for_timeout(1000)

    await e2e_page.expect_text("选股", timeout_ms=10000)
