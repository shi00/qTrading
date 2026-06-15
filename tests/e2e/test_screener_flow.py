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


async def test_run_screener_strategy(e2e_page):
    """测试：执行放量突破策略，验证平安银行出现在选股结果中。"""
    # 导航到选股页
    screener_label = I18n.get("nav_screener")
    await e2e_page.click_text(screener_label, timeout_ms=15000)

    # 选择放量突破策略
    select_label = I18n.get("select_strategy")
    await e2e_page.select_dropdown(select_label, "volume_breakout", timeout_ms=10000)

    # 点击执行选股
    run_text = I18n.get("run_screening")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 等待策略执行完成（轮询"平安银行"文本出现，超时 30s）
    await e2e_page.expect_text("平安银行", timeout_ms=30000)
