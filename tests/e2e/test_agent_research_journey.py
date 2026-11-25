"""研究观察层端到端旅程与关键 UI 场景。

覆盖：公共区研究入口展开/收起（非模态、不卸载业务页）、首问输入区的可见
可达性、中文输入不误发送且不丢字、展开状态下跨页导航不破坏业务控件、收起后
回到原业务现场并保留首问草稿、运行未就绪/未接线时如实标注（不呈现空白）。

模型调用计数与人工许可「至多一次提交」的精确断言在单测层完成，E2E 只验证
用户可见行为，不依赖真实模型、MCP 或网络。
"""

import pytest

from core.i18n import I18n
from tests.e2e.pages import HomePage, ResearchPanelPage, ScreenerPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = [pytest.mark.timeout(180), pytest.mark.e2e, pytest.mark.timeout_e2e_standard]

_FIRST_QUESTION_TEXT = "分析新能源汽车行业的景气度与竞争格局"


def _composer_unavailable_hint() -> str:
    return I18n.get("agent_research_composer_unavailable")


async def _skip_if_composer_not_wired(panel: ResearchPanelPage) -> None:
    """首问入口未接线时如实跳过（不伪造输入行为）。"""
    if await panel.page.has_text(_composer_unavailable_hint()):
        pytest.skip("研究运行时未接线/未就绪：首问入口不可交互，跳过输入交互断言")


async def test_research_panel_opens_and_states_runtime_availability(e2e_page):
    """研究入口可达，且面板明确呈现可用主体或唯一不可用原因（不呈现空白）。"""
    panel = ResearchPanelPage(e2e_page)

    await panel.open()
    await e2e_page.expect_text(I18n.get("agent_research_sessions"), timeout_ms=TIMEOUTS.TITLE)

    available = await panel.is_body_available()
    not_wired = await e2e_page.has_text(I18n.get("agent_research_not_wired"))
    not_ready = await e2e_page.has_text(I18n.get("agent_research_not_ready"))
    state_count = sum([available, not_wired, not_ready])
    assert state_count == 1, (
        "研究面板必须恰好呈现一种状态：可用主体或唯一不可用原因"
        f"（available={available}, not_wired={not_wired}, not_ready={not_ready}）"
    )


async def test_first_question_entry_exposes_composer_and_send_action(e2e_page):
    """可用状态下首问输入区、发送动作与会话列表同时可达。"""
    panel = ResearchPanelPage(e2e_page)
    await panel.open()
    if not await panel.is_body_available():
        pytest.skip("研究运行时未接线/未就绪：首问入口不可达")

    await e2e_page.expect_text(I18n.get("agent_research_composer"), timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.expect_text(I18n.get("agent_research_composer_label"), timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.expect_text(I18n.get("agent_research_send"), timeout_ms=TIMEOUTS.TITLE)

    no_session = await e2e_page.has_text(I18n.get("agent_research_no_session"))
    has_session_title = await e2e_page.has_text("会话：")
    assert no_session != has_session_title, (
        "会话区必须明确呈现「暂无可观察的研究会话」或当前会话标题之一"
        f"（no_session={no_session}, has_session_title={has_session_title}）"
    )


async def test_chinese_first_question_is_kept_without_auto_send(e2e_page):
    """中文输入保留完整文本且不触发发送（发送中文案与直答区均不出现）。"""
    panel = ResearchPanelPage(e2e_page)
    await panel.open()
    await _skip_if_composer_not_wired(panel)

    await panel.fill_first_question(_FIRST_QUESTION_TEXT)
    await panel.expect_composer_value(_FIRST_QUESTION_TEXT, timeout_ms=TIMEOUTS.TITLE)

    sending = await e2e_page.has_text(I18n.get("agent_research_sending"))
    answer_preview = await e2e_page.has_text(I18n.get("agent_research_answer_preview"))
    assert sending is False, "仅输入中文不应进入提交中状态"
    assert answer_preview is False, "仅输入中文不应产生直答结果"


async def test_navigation_keeps_panel_and_business_controls_available(e2e_page):
    """面板展开时跨页导航不报错，业务页关键控件与面板标题仍可见（非模态）。"""
    panel = ResearchPanelPage(e2e_page)
    home = HomePage(e2e_page)
    screener = ScreenerPage(e2e_page)

    await screener.open()
    await screener.expect_run_button()
    await panel.open()

    await screener.open()
    await screener.expect_run_button()
    await panel.expect_open()

    await panel.app.goto("nav_market")
    await home.expect_title(timeout_ms=TIMEOUTS.TITLE)
    await home.expect_kpi_sh(timeout_ms=TIMEOUTS.TITLE)
    await panel.expect_open()


async def test_closing_panel_returns_to_business_page_and_keeps_draft(e2e_page):
    """收起面板回到原业务现场，重新展开后首问草稿仍在（返回现场不丢字）。"""
    panel = ResearchPanelPage(e2e_page)
    screener = ScreenerPage(e2e_page)

    await screener.open()
    await screener.expect_run_button()
    await panel.open()
    await _skip_if_composer_not_wired(panel)

    await panel.fill_first_question(_FIRST_QUESTION_TEXT)
    await panel.expect_composer_value(_FIRST_QUESTION_TEXT, timeout_ms=TIMEOUTS.TITLE)

    await panel.close()
    await screener.expect_run_button()

    await panel.open()
    await panel.expect_composer_value(_FIRST_QUESTION_TEXT, timeout_ms=TIMEOUTS.TITLE)
