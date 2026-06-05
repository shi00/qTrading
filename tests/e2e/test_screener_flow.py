import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_screener_page_loads(e2e_page):
    """测试：选股页能正常加载。"""
    screener_label = I18n.get("nav_screener")
    await e2e_page.click_text(screener_label, timeout_ms=15000)

    screener_title = I18n.get("screener_title")
    await e2e_page.expect_text(screener_title)

    strategies_label = I18n.get("select_strategy")
    await e2e_page.expect_text(strategies_label)


@pytest.mark.skip(reason="需确认策略选择控件的语义标签后再实现")
async def test_run_screener_strategy(e2e_page):
    """测试：执行选股策略（完整流，需语义快照确认控件）。"""
    pass
