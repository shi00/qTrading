import pytest

from ui.i18n import I18n
from tests.e2e.labels import strategy_desc_label
from tests.e2e.pages import ScreenerPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e


async def test_screener_page_loads(e2e_page):
    """测试：选股页能正常加载。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()

    await screener.expect_text(I18n.get("screener_title"), timeout_ms=TIMEOUTS.INTERACTION)
    await screener.expect_text(I18n.get("select_strategy"), timeout_ms=TIMEOUTS.INTERACTION)


async def test_run_screener_strategy(e2e_page):
    """测试：执行放量突破策略，验证平安银行出现在选股结果中。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()

    # 等待策略执行完成（轮询"平安银行"文本出现，超时 30s）
    await screener.expect_result("平安银行")


async def test_screener_no_results(e2e_page):
    """E1: 选股策略返回空结果时显示无结果提示。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()

    # 选择超跌反弹策略 — 种子数据不含 RSI 超卖条件，策略应返回空结果
    await screener.select_strategy("oversold")
    await screener.run()

    # 验证空结果状态提示
    await screener.expect_result(I18n.get("screener_no_results"))


async def test_screener_strategy_switch(e2e_page):
    """D1: 切换策略后描述文本更新。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()

    # ScreenerPage.select_strategy 内部通过 strategy_label() 将 key 解析为本地化显示名，
    # 避免向 select_dropdown 传递 raw_key 导致匹配失败
    await screener.select_strategy("volume_breakout")

    # 验证放量突破策略描述出现
    vb_desc = strategy_desc_label("volume_breakout")
    await screener.expect_text(vb_desc, timeout_ms=TIMEOUTS.FAST)

    # 切换到超跌反弹策略
    await screener.select_strategy("oversold")

    # 验证超跌反弹策略描述出现（动态描述，需 format 参数）
    os_desc = I18n.get("strategy_oversold_dynamic_desc").format(period=14, threshold=30)
    await screener.expect_text(os_desc, timeout_ms=TIMEOUTS.FAST)
