import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_backtest_flow(e2e_page):
    """测试：回测页面完整交互流 — 选择策略、修改初始资金、执行回测、验证指标卡片。"""
    # 导航到回测页
    backtest_label = I18n.get("nav_backtest")
    await e2e_page.click_text(backtest_label, timeout_ms=15000)

    # 确认回测页标题
    backtest_title = I18n.get("backtest_view_title")
    await e2e_page.expect_text(backtest_title)

    # 选择放量突破策略
    strategy_label = I18n.get("backtest_select_strategy")
    await e2e_page.select_dropdown(strategy_label, "volume_breakout", timeout_ms=10000)

    # 修改初始资金
    capital_label = I18n.get("backtest_initial_capital")
    await e2e_page.fill_textbox(capital_label, "500000", timeout_ms=8000)

    # 点击开始回测
    run_text = I18n.get("backtest_run")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 等待回测完成并验证核心指标卡片可见
    # 指标卡片仅在回测完成后渲染，第一个卡片用 60s 超时兼作完成信号
    first_metric_keys = (
        "backtest_metric_total_return",
        "backtest_metric_annual_return",
        "backtest_metric_sharpe",
        "backtest_metric_max_dd",
    )
    for idx, i18n_key in enumerate(first_metric_keys):
        metric_label = I18n.get(i18n_key)
        timeout = 60000 if idx == 0 else 5000
        await e2e_page.expect_text(metric_label, timeout_ms=timeout)


async def test_backtest_no_strategy(e2e_page):
    """E2: 未选策略时点击运行，显示错误提示。"""
    backtest_label = I18n.get("nav_backtest")
    await e2e_page.click_text(backtest_label, timeout_ms=15000)

    backtest_title = I18n.get("backtest_view_title")
    await e2e_page.expect_text(backtest_title)

    # 不选策略，直接点击运行
    run_text = I18n.get("backtest_run")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 验证错误提示
    no_strategy_text = I18n.get("backtest_no_strategy")
    await e2e_page.expect_text(no_strategy_text, timeout_ms=5000)
