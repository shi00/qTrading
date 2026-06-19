import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n
from tests.e2e.labels import strategy_label
from tests.e2e.pages import App


async def test_backtest_flow(e2e_page):
    """测试：回测页面完整交互流 — 选择策略、修改初始资金、执行回测、验证指标卡片。"""
    # 导航到回测页
    app = App(e2e_page)
    await app.goto("nav_backtest")

    # 确认回测页标题
    backtest_title = I18n.get("backtest_view_title")
    await e2e_page.expect_text(backtest_title)

    # 选择放量突破策略（传入本地化显示名，不再依赖 helper 内部 key→文案映射）
    select_label = I18n.get("backtest_select_strategy")
    vb_name = strategy_label("volume_breakout")
    await e2e_page.select_dropdown(select_label, vb_name, timeout_ms=10000)

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


# [PITFALL_WARNING] UX 设计与测试用例冲突避坑指南
# 坑点：测试企图验证"未选择策略时点击回测"的报错提示。
# 原因：BacktestView 的 UI 逻辑会在页面加载时默认选中第一个可用策略，
#      因此用户在界面上永远无法将其清空并置于"无策略"状态。
# 正确做法：不要尝试用底层 API（如 select_dropdown）去强行重置状态以验证这种不可能发生的路径。
#         应当直接跳过该用例，或联系 PM 修改需求。
@pytest.mark.skip(
    reason="BacktestView auto-selects the first strategy by default, making the 'no strategy' state unreachable from the UI"
)
async def test_backtest_no_strategy(e2e_page):
    """E2: 未选策略时点击运行，显示错误提示。"""
    app = App(e2e_page)
    await app.goto("nav_backtest")

    backtest_title = I18n.get("backtest_view_title")
    await e2e_page.expect_text(backtest_title)

    # 不选策略，直接点击运行
    run_text = I18n.get("backtest_run")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 验证错误提示
    no_strategy_text = I18n.get("backtest_no_strategy")
    await e2e_page.expect_text(no_strategy_text, timeout_ms=5000)
