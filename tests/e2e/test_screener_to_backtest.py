import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n
from tests.e2e.labels import strategy_label
from tests.e2e.pages import App, ScreenerPage
from tests.e2e.timeouts import TIMEOUTS


async def test_screener_then_backtest(e2e_page):
    """测试：跨视图端到端流程 — 选股运行 → 切换回测页 → 同策略回测 → 验证指标卡片。

    验证核心业务闭环：选股结果与回测共用同一策略，两个视图协作无异常。
    """
    # Step 1: 选股页运行放量突破策略
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # Step 2: 导航到回测页
    app = App(e2e_page)
    await app.goto("nav_backtest")

    # 确认回测页标题
    backtest_title = I18n.get("backtest_view_title")
    await e2e_page.expect_text(backtest_title, timeout_ms=TIMEOUTS.TITLE)

    # Step 3: 选择同策略（放量突破）
    select_label = I18n.get("backtest_select_strategy")
    vb_name = strategy_label("volume_breakout")
    await e2e_page.select_dropdown(select_label, vb_name, timeout_ms=TIMEOUTS.INTERACTION)

    # Step 4: 运行回测
    run_text = I18n.get("backtest_run")
    await e2e_page.click_button(run_text, timeout_ms=TIMEOUTS.INTERACTION)

    # Step 5: 验证核心指标卡片可见（第一个卡片用 60s 超时兼作完成信号）
    first_metric = I18n.get("backtest_metric_total_return")
    await e2e_page.expect_text(first_metric, timeout_ms=TIMEOUTS.BACKTEST)
