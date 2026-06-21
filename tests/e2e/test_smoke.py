import pytest

from ui.i18n import I18n
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e
NAV_KEYS = [
    "nav_market",
    "nav_screener",
    "nav_backtest",
    "nav_data",
    "nav_tasks",
    "nav_settings",
]


async def test_app_boots_and_shows_nav(e2e_page):
    # Wait for main app to fully render (async init in main.py)
    # The first nav element should appear within timeout
    first_label = I18n.get(NAV_KEYS[0])
    await e2e_page.expect_text(first_label, timeout_ms=TIMEOUTS.NAV)

    for key in NAV_KEYS:
        label = I18n.get(key)
        await e2e_page.expect_text(label)
