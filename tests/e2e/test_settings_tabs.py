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
