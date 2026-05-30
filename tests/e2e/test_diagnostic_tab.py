import logging

import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n

logger = logging.getLogger(__name__)


async def test_diagnostic_all_tabs(e2e_page):
    """诊断：逐个点击所有 Tab 按钮。"""
    settings_label = I18n.get("nav_settings")
    await e2e_page.click_text(settings_label)

    settings_title = I18n.get("settings_title")
    await e2e_page.page.get_by_text(settings_title, exact=True).first.wait_for(state="attached", timeout=8000)

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
        try:
            await e2e_page.click_tab(tab_name)
            await e2e_page.page.wait_for_timeout(500)

            all_buttons = await e2e_page.page.evaluate("""() => {
                const buttons = document.querySelectorAll('flt-semantics[role="button"]');
                return Array.from(buttons).filter(b => {
                    const style = getComputedStyle(b);
                    const bg = style.backgroundColor;
                    return bg !== 'rgba(0, 0, 0, 0)';
                }).map(b => ({
                    text: (b.textContent || '').trim().slice(0, 20),
                    bg: getComputedStyle(b).backgroundColor
                }));
            }""")

            selected = [b for b in all_buttons if b["bg"] != "rgba(0, 0, 0, 0)"]
            logger.info("Tab[%d] '%s': clicked, selected tabs: %s", i, tab_name, selected)
        except Exception as ex:
            logger.info("Tab[%d] '%s': FAILED - %s", i, tab_name, ex)
