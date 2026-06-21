import base64

import pytest

from ui.i18n import I18n

pytestmark = pytest.mark.e2e


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
    # [PITFALL FIX] CanvasKit 渲染下 DOM 背景透明，canvas readPixels/drawImage 也因
    #                preserveDrawingBuffer=false 失效，改用 page.screenshot() 捕获实际渲染像素
    # 坑点：apply_page_theme 设置 page.bgcolor = None，背景色由 CanvasKit 在 canvas 内渲染，
    #       DOM 元素（flutter-view/flt-glass-pane/body）的 backgroundColor 为透明，
    #       且 flt-glass-pane canvas 因 preserveDrawingBuffer=false 导致
    #       drawImage/readPixels 返回透明像素，原有检测方法在 CanvasKit 模式下全部失效。
    # 应对：通过 page.screenshot() 捕获浏览器实际渲染的像素（包含 canvas 内容），
    #       将截图作为 base64 传回浏览器，用 2D canvas 解码并采样像素颜色。
    #       加入轮询循环，等待 CanvasKit 重新渲染新主题。
    await e2e_page.page.wait_for_timeout(500)  # 等待 CanvasKit 重新渲染
    is_light_bg = False
    for _ in range(15):  # 最多 ~6s，每 400ms 检查一次
        try:
            screenshot_bytes = await e2e_page.page.screenshot(
                clip={"x": 0, "y": 0, "width": 10, "height": 10},
                type="png",
            )
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
            is_light_bg = await e2e_page.page.evaluate(
                """async (b64) => {
                    try {
                        const response = await fetch('data:image/png;base64,' + b64);
                        const blob = await response.blob();
                        const bitmap = await createImageBitmap(blob);
                        const canvas = document.createElement('canvas');
                        canvas.width = 1;
                        canvas.height = 1;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(bitmap, 0, 0, 1, 1, 0, 0, 1, 1);
                        const d = ctx.getImageData(0, 0, 1, 1).data;
                        return d[0] > 200 && d[1] > 200 && d[2] > 200;
                    } catch (e) {
                        return false;
                    }
                }""",
                screenshot_b64,
            )
        except Exception:
            is_light_bg = False
        if is_light_bg:
            break
        await e2e_page.page.wait_for_timeout(400)
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
    lang_zh = I18n.get("settings_lang_zh")

    try:
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
    finally:
        # [PITFALL FIX] 必须还原 flet_app 内存中的 I18n locale！
        # 坑点：pristine_config fixture 只还原磁盘配置文件和测试进程 I18n，
        #       但 flet_app 是 session 级单进程，其内存中的 I18n._locale 仍是 en_US。
        #       这会导致后续测试（settings_tabs/smoke/task_center）寻找中文导航文本时全部超时失败。
        # 应对：通过 UI 主动切换回中文，触发 app 进程的 I18n.set_locale("zh_CN")。
        # 此时 app 已是英文界面，dropdown label 显示为 "Language"。
        lang_label_en = I18n.get("settings_language", locale="en_US")
        try:
            await e2e_page.select_dropdown(lang_label_en, lang_zh, timeout_ms=10000)
            # 轮询等待中文导航文本重新出现，确认 locale 已还原
            nav_settings_zh = I18n.get("nav_settings", locale="zh_CN")
            for _ in range(25):
                if await e2e_page.has_text(nav_settings_zh):
                    break
                await e2e_page.page.wait_for_timeout(200)
        except Exception:
            # 还原失败时不抛出，避免掩盖原始测试失败；下游测试会显式失败暴露问题
            pass


@pytest.mark.mutates_config
async def test_settings_log_level_switch(e2e_page):
    """测试：System Tab 日志级别切换 — 切换到 DEBUG 后 snackbar 提示出现。

    on_change 即保存并调用 update_log_level（in-memory 副作用），
    需 try/finally 通过 UI 切换回 INFO 还原 logger 级别。
    """
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=15000)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=10000)

    tab_system = I18n.get("settings_tab_system")
    await e2e_page.click_text(tab_system, timeout_ms=8000)

    # 等待日志级别 Dropdown label 出现
    log_level_label = I18n.get("settings_log_level")
    await e2e_page.expect_text(log_level_label, timeout_ms=10000)

    # 等待前一个测试的 Snackbar 动画完成
    await e2e_page.page.wait_for_timeout(1000)

    log_level_debug = I18n.get("sys_opt_debug")
    log_level_info = I18n.get("sys_opt_info")
    # snackbar 文本格式: "控制系统日志详细程度: DEBUG"
    snack_prefix = I18n.get("sys_log_label")

    try:
        await e2e_page.select_dropdown(log_level_label, log_level_debug, timeout_ms=10000)
        # 验证 snackbar 出现（含日志级别名）
        await e2e_page.expect_text(f"{snack_prefix}: DEBUG", timeout_ms=5000)
    finally:
        # [PITFALL FIX] 还原 flet_app 内存中的 logger 级别
        # on_change 调用了 update_log_level(level) 修改 in-memory logger，
        # pristine_config 只还原磁盘配置，不调用 update_log_level，需通过 UI 切换回 INFO。
        try:
            await e2e_page.select_dropdown(log_level_label, log_level_info, timeout_ms=10000)
        except Exception:
            pass
