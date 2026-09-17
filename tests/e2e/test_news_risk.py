import pytest

from tests.e2e.pages import NewsRiskPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = [pytest.mark.timeout(240), pytest.mark.e2e, pytest.mark.timeout_e2e_standard]


async def test_news_risk_success_flow(news_risk_page_success):
    """新闻风险解读成功场景：六验收点之 XP2/XP5/XP1/XP4/XP6。

    与 noevents 分离成独立场景池（news_risk_app_success，NEWS_AISERVICE_MOCK=success_events，
    DELAY_MS=1500 提供可捕获的 analyzing 阶段供取消恢复验证）。

    执行顺序刻意设计（XP5 取消恢复须在 000001.SZ 首次成功分析——即 brief 落库缓存命中——
    之前运行，否则再次生成走缓存立即返回，无法捕获 analyzing 阶段进行取消）。
    """
    nrp = NewsRiskPage(news_risk_page_success)

    # XP2：AI 生成前（证据模式）不显示风险等级。
    await nrp.open_detail()
    await nrp.wait_evidence_ready()
    await nrp.assert_risk_level_hidden()
    await nrp.close_detail()

    # XP5：分析中取消后恢复——关闭详情（取消任务）→ 重开证据恢复 → 重新生成成功。
    await nrp.open_detail()
    await nrp.wait_evidence_ready()
    await nrp.generate()
    await nrp.wait_analyzing()
    await nrp.close_detail()
    await nrp.open_detail()
    await nrp.wait_evidence_ready()
    await nrp.generate()
    await nrp.wait_ready(timeout_ms=TIMEOUTS.BACKTEST)

    # 至此 000001.SZ 已成功落库 brief；XP1：再次进入详情后点「生成风险解读」，
    # analyze 内缓存命中快速返回 ready（VM 契约：打开详情恒为 evidence_ready，不
    # 自动触发 AI；需点 generate 才进入 analyze，其中缓存命中会秒回 ready，见
    # news_insight_view_model.generate / news_insight_service.analyze）→ RISK_LEVEL 渲染。
    await nrp.close_detail()
    await nrp.open_detail()
    await nrp.wait_evidence_ready()
    await nrp.generate()
    await nrp.wait_ready()

    # XP4：合法结果展示风险等级 / 事件卡 / 置信度覆盖 / 总结。
    await nrp.assert_risk_level_visible()
    await nrp.assert_event_card_visible(idx=0)
    await nrp.assert_coverage_visible()
    await nrp.assert_summary_visible()
    await nrp.close_detail()

    # XP6：风险信息不改变候选——生成前后选股结果主体不变（平安银行仍在）。
    # 详情对话框已关闭，底层选股结果表格仍保持生成前状态。
    await nrp.screener.expect_result("平安银行")
    await nrp.screener.expect_text("000001.SZ", timeout_ms=TIMEOUTS.INTERACTION)


async def test_news_risk_no_events(news_risk_page_noevents):
    """XP3：AI 成功但未识别到重大风险事件 → 显示「未识别到重大风险」占位。

    独立 noevents 场景池（NEWS_AISERVICE_MOCK=success_no_events），R21：缺失以
    None/占位表达，不渲染低风险等级。
    """
    nrp = NewsRiskPage(news_risk_page_noevents)
    await nrp.open_detail()
    await nrp.wait_evidence_ready()
    await nrp.generate()
    await nrp.wait_ready(timeout_ms=TIMEOUTS.BACKTEST)
    # 无事件 → EVENT_EMPTY 占位可见；RISK_LEVEL 仍渲染但为"未知"（R21：不填充低风险）
    await nrp.assert_event_empty_visible()
    await nrp.close_detail()
