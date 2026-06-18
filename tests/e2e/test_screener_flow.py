import pytest
from ui.i18n import I18n

pytestmark = pytest.mark.e2e


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
    vb_name = I18n.get("strategy_volume_breakout_name")
    await e2e_page.select_dropdown(select_label, vb_name, timeout_ms=10000)

    # 点击执行选股
    run_text = I18n.get("run_screening")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 等待策略执行完成（轮询"平安银行"文本出现，超时 30s）
    await e2e_page.expect_text("平安银行", timeout_ms=30000)


async def test_screener_no_results(e2e_page):
    """E1: 选股策略返回空结果时显示无结果提示。"""
    screener_label = I18n.get("nav_screener")
    await e2e_page.click_text(screener_label, timeout_ms=15000)

    # 选择超跌反弹策略 — 种子数据不含 RSI 超卖条件，策略应返回空结果
    select_label = I18n.get("select_strategy")
    os_name = I18n.get("strategy_oversold_name")
    await e2e_page.select_dropdown(select_label, os_name, timeout_ms=10000)

    run_text = I18n.get("run_screening")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 验证空结果状态提示
    no_results_text = I18n.get("screener_no_results")
    await e2e_page.expect_text(no_results_text, timeout_ms=30000)


async def test_screener_strategy_switch(e2e_page):
    """D1: 切换策略后描述文本更新。"""
    screener_label = I18n.get("nav_screener")
    await e2e_page.click_text(screener_label, timeout_ms=15000)

    # [PITFALL_WARNING] 下拉框定位避坑指南
    # 坑点：千万不要向 select_dropdown 传递策略的 raw_key（如 "volume_breakout" 或 "oversold"）。
    # 原因：Flet 的下拉框在 UI 渲染时使用的是国际化翻译后的真实文字（如 "放量突破"）。
    #      如果在测试代码中传入 raw_key，flet_page.py 的底层匹配会静默失败（找不到对应选项），
    #      导致页面一直停留在默认选中项上，最终引发后续 expect_text 断言超时崩溃！
    # 正确做法：必须使用对应的 _name 后缀去获取 I18n 翻译文本进行点击。
    select_label = I18n.get("select_strategy")
    vb_name = I18n.get("strategy_volume_breakout_name")
    await e2e_page.select_dropdown(select_label, vb_name, timeout_ms=10000)

    # 验证放量突破策略描述出现
    vb_desc = I18n.get("strategy_volume_breakout_desc")
    await e2e_page.expect_text(vb_desc, timeout_ms=5000)

    # 切换到超跌反弹策略
    os_name = I18n.get("strategy_oversold_name")
    await e2e_page.select_dropdown(select_label, os_name, timeout_ms=10000)

    # 验证超跌反弹策略描述出现
    os_desc = I18n.get("strategy_oversold_dynamic_desc").format(period=14, threshold=30)
    await e2e_page.expect_text(os_desc, timeout_ms=5000)
