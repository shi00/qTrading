import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n

NAV_KEYS = [
    "nav_market",
    "nav_screener",
    "nav_backtest",
    "nav_data",
    "nav_tasks",
    "nav_settings",
]


async def test_app_boots_and_shows_nav(e2e_page):
    for key in NAV_KEYS:
        label = I18n.get(key)
        await e2e_page.expect_text(label)
