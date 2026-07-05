import logging

import pytest

from ui.i18n import I18n
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e
logger = logging.getLogger(__name__)


async def test_settings_all_tabs(e2e_page):
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=TIMEOUTS.NAV)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=TIMEOUTS.TITLE)

    tab_keys = [
        "settings_tab_data",
        "settings_tab_database",
        "settings_tab_ai",
        "settings_tab_tasks",
        "settings_tab_notify",
        "settings_tab_system",
    ]

    for i, key in enumerate(tab_keys):
        tab_name = I18n.get(key)
        await e2e_page.click_tab(tab_name)
        await e2e_page.expect_text(tab_name, timeout_ms=TIMEOUTS.FAST)
        logger.info("Tab[%d] '%s': clicked and verified", i, tab_name)


async def test_system_tab_tier_api_panel_rendered(e2e_page):
    """Phase 2A.1：System Tab 中 TierApiPanel 渲染验证。

    覆盖：system tab 可切换 + TierApiPanel 关键 i18n 文本可见性。
    不触发实际 probe 调用（避免 flaky）。TierApiPanel 在 system tab 中部，
    需滚动后检测；若 CanvasKit 下语义节点延迟渲染，则放宽为 has_text 容错检测。
    """
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label, timeout_ms=TIMEOUTS.NAV)

    settings_title = I18n.get("settings_title")
    await e2e_page.expect_text(settings_title, timeout_ms=TIMEOUTS.TITLE)

    tab_system = I18n.get("settings_tab_system")
    await e2e_page.click_text(tab_system, timeout_ms=TIMEOUTS.FAST)

    # 先验证 system tab 顶部可见文本（确认 tab 切换成功）
    theme_label = I18n.get("settings_theme")
    await e2e_page.expect_text(theme_label, timeout_ms=TIMEOUTS.INTERACTION)

    # 滚动到 system tab 底部，使 TierApiPanel 进入视口
    await e2e_page.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await e2e_page.page.wait_for_timeout(1500)  # 等待 CanvasKit 重新渲染

    # TierApiPanel 标题（容错检测：has_text 不阻塞，验证已渲染即可）
    panel_title = I18n.get("sys_tier_panel_title")
    tier_label = I18n.get("sys_label_point_tier")
    probe_button = I18n.get("sys_tier_probe_button")

    # 轮询检测 TierApiPanel 关键文本，最多 15s
    found_keys: list[str] = []
    for _ in range(30):
        if await e2e_page.has_text(panel_title):
            found_keys.append("panel_title")
        if await e2e_page.has_text(tier_label):
            found_keys.append("tier_label")
        if await e2e_page.has_text(probe_button):
            found_keys.append("probe_button")
        if len(found_keys) >= 2:
            break
        await e2e_page.page.wait_for_timeout(500)

    assert len(found_keys) >= 2, (
        f"TierApiPanel 关键元素未渲染，仅检测到: {found_keys}。期望至少 2 项(panel_title/tier_label/probe_button)"
    )
    logger.info("TierApiPanel 关键元素渲染验证通过: %s", found_keys)
