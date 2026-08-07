import logging

import pytest

from ui.i18n import I18n
from tests.e2e.pages import SettingsPage

pytestmark = pytest.mark.e2e

logger = logging.getLogger(__name__)


async def test_settings_page_loads(e2e_page):
    """PR-3: 迁移到 SettingsPage anchor 操作。"""
    sp = SettingsPage(e2e_page)
    await sp.open()
    await sp.expect_title()


@pytest.mark.mutates_config
async def test_settings_language_switch(e2e_page):
    """D4: 设置页语言切换后 UI 文本更新。

    PR-3: 迁移到 SettingsPage anchor 操作（tab 切换 + 语言 dropdown）。
    """
    sp = SettingsPage(e2e_page)
    await sp.open()
    await sp.expect_title()

    await sp.click_tab("system", timeout_ms=8000)

    # 等待 System tab 渲染完成（语言设置行 title 出现）
    # [PITFALL FIX] 不用 get_language_label()（如 "简体中文 / English"），因为 CanvasKit
    # 渲染下含斜杠的复合文本无法被 expect_text 的选择器匹配，改用简单文本 "语言"
    lang_label = I18n.get("settings_language")
    lang_en = I18n.get("settings_lang_en")
    lang_zh = I18n.get("settings_lang_zh")
    await e2e_page.expect_text(lang_label, timeout_ms=10000)  # Wait for the tab to render

    try:
        # PR-3: 语言 dropdown 已 anchor 化，用 SettingsPage.select_language 定位
        await sp.select_language(lang_en, timeout_ms=10000)

        # 手动同步测试进程的 I18n 状态，以便生成正确的英文断言字符串
        I18n.set_locale("en_US")

        # 验证 UI 文本已切换为英文（导航栏 "Screener" 出现）
        # [PITFALL FIX] 不用 expect_text，因为 Flet 0.86.3 CanvasKit 下 NavigationRail
        # 语义节点合并导致 "Screener" 同时出现在父 aria-label 和子 button text 中，
        # get_by_text strict mode 匹配多个节点立即报错。改用 has_text 轮询（非 strict）。
        for _ in range(50):  # 50 * 200ms = 10s
            if await e2e_page.has_text("Screener"):
                break
            await e2e_page.page.wait_for_timeout(200)
        else:
            pytest.fail("导航栏 'Screener' 在 10000ms 内未出现（语言切换失败）")

        # 强断言：验证设置页文案已切换为英文（而非仅导航栏变化）
        theme_label_en = I18n.get("settings_theme")
        await e2e_page.expect_text(theme_label_en, timeout_ms=10000)

        # 切回中文之前的验证：确认设置页文案已切换为英文
        # [PITFALL FIX] 不用 get_language_label()，同上原因（CanvasKit 不匹配含斜杠文本）
        lang_label_en = I18n.get("settings_language")  # "Language" (locale=en_US)
        await e2e_page.expect_text(lang_label_en, timeout_ms=10000)
    finally:
        # [PITFALL FIX] 必须还原 flet_app 内存中的 I18n locale！
        # 坑点：pristine_config fixture 只还原磁盘配置文件，
        #       但 flet_app 是 session 级单进程，其内存中的 I18n._locale 仍是 en_US。
        #       这会导致后续测试（settings_tabs/smoke/task_center）寻找中文导航文本时全部超时失败。
        # 应对：通过 UI 主动切换回中文，触发 app 进程的 I18n.set_locale("zh_CN")。
        # PR-3: 语言 dropdown 已 anchor 化，无需依赖当前 locale 的 label 文本定位，
        # 直接用 anchor 定位 dropdown 并选择中文选项。
        # PR-3 改造：测试进程 I18n locale 还原由测试自治（pristine_config 不再代管），
        # 在 UI 还原之前先还原测试进程 locale，确保后续 I18n.get() 调用返回中文。
        I18n.set_locale("zh")
        try:
            await sp.select_language(lang_zh, timeout_ms=10000)
            # 轮询等待中文导航文本重新出现，确认 locale 已还原
            nav_settings_zh = I18n.get("nav_settings", locale="zh_CN")
            for _ in range(25):
                if await e2e_page.has_text(nav_settings_zh):
                    break
                await e2e_page.page.wait_for_timeout(200)
        except Exception as e:  # noqa: BLE001
            # 还原失败时不抛出，避免掩盖原始测试失败；下游测试会显式失败暴露问题
            logger.warning("[settings_flow] restore language to zh failed: %s", e, exc_info=True)


@pytest.mark.mutates_config
async def test_settings_log_level_switch(e2e_page):
    """测试：System Tab 日志级别切换 — 切换到 DEBUG 后 snackbar 提示出现。

    on_change 即保存并调用 update_log_level（in-memory 副作用），
    需 try/finally 通过 UI 切换回 INFO 还原 logger 级别。

    PR-3: 迁移到 SettingsPage anchor 操作（tab 切换 + 日志级别 dropdown）。
    """
    sp = SettingsPage(e2e_page)
    await sp.open()
    await sp.expect_title()

    await sp.click_tab("system", timeout_ms=8000)

    # 等待日志级别 Dropdown label 出现
    log_level_label = I18n.get("settings_log_level")
    await e2e_page.expect_text(log_level_label, timeout_ms=10000)

    log_level_debug = I18n.get("sys_opt_debug")
    log_level_info = I18n.get("sys_opt_info")
    log_level_error = I18n.get("sys_opt_error")
    # snackbar 文本格式: "控制系统日志详细程度: DEBUG"
    snack_prefix = I18n.get("sys_log_label")

    try:
        # [PITFALL_WARNING] 日志级别切换 flaky 防护
        # 先切到 ERROR（确定状态），再切到 DEBUG（目标状态），保证状态必变化
        await sp.select_log_level(log_level_error, timeout_ms=10000)

        await sp.select_log_level(log_level_debug, timeout_ms=10000)
        # 验证 snackbar 出现（含日志级别名）
        await e2e_page.expect_text(f"{snack_prefix}: DEBUG", timeout_ms=5000)
    finally:
        # [PITFALL FIX] 还原 flet_app 内存中的 logger 级别
        # on_change 调用了 update_log_level(level) 修改 in-memory logger，
        # pristine_config 只还原磁盘配置，不调用 update_log_level，需通过 UI 切换回 INFO。
        try:
            await sp.select_log_level(log_level_info, timeout_ms=10000)
        except Exception as e:  # noqa: BLE001
            logger.warning("[settings_flow] restore log level to INFO failed: %s", e, exc_info=True)
