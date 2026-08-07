"""E2E anchor smoke test: 验证 PR-1 改造的 anchor 在 CanvasKit 下的 DOM 与 click 行为.

覆盖方案 §9 PR-1 Task 1.5b 的 DoD（anchor DOM 可定位 + click 触发内部回调）：
- INTERACTIVE (run_button): aria-label 独立节点 + 内层 flt-tappable
- COMPLEX (strategy_dropdown): textContent 前缀匹配 + click 展开 aria-expanded

不重复 test_run_screener_strategy 的完整业务流程，仅验证 anchor 基础设施可用.
"""

import pytest

from tests.e2e.helpers.anchor_page import AnchorPage
from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.pages import ScreenerPage
from ui.testing.e2e_ids import EIDS

pytestmark = [pytest.mark.timeout(60), pytest.mark.e2e, pytest.mark.timeout_e2e_smoke]


async def test_screener_anchor_dom_present(e2e_page: FletPage):
    """验证 anchor 化的 strategy_dropdown + run_button 在 DOM 中可定位.

    PoC 实证（reviews/poc/EVIDENCE.md）：
    - INTERACTIVE → [aria-label="e2e.screener.run_button"] 独立节点
    - COMPLEX → textContent.startsWith("e2e.screener.strategy_dropdown")

    同时守护 container=True 生效（Task 1.3 DoD ⑤）：父节点 aria-label 不含子 EID。
    """
    ap = AnchorPage(e2e_page.page, e2e_page)
    screener = ScreenerPage(e2e_page)
    await screener.open()

    # INTERACTIVE: run_button 应生成 [aria-label] 独立节点
    await ap.expect_visible(EIDS.SCREENER.RUN_BUTTON, timeout_ms=15000)
    assert await ap.count(EIDS.SCREENER.RUN_BUTTON) == 1, "run_button anchor 应全局唯一（EIDS 命名规范强制）"

    # COMPLEX: strategy_dropdown 应通过 textContent 前缀匹配可定位
    await ap.expect_visible(EIDS.SCREENER.STRATEGY_DROPDOWN, timeout_ms=15000)
    assert await ap.count(EIDS.SCREENER.STRATEGY_DROPDOWN) == 1

    # Task 1.3 DoD ⑤: container=True 生效——父节点 aria-label 不含子 EID
    # 若 container=True 失效，父容器会合并子 label，导致外层节点 aria-label 含 EID
    parent_has_eid = await e2e_page.page.evaluate(
        """(eid) => {
            const el = document.querySelector(`flt-semantics[aria-label="${eid}"]`);
            if (!el || !el.parentElement) return false;
            const parent = el.parentElement;
            const parentLabel = parent.getAttribute('aria-label') || '';
            return parentLabel.includes(eid);
        }""",
        EIDS.SCREENER.RUN_BUTTON[0],
    )
    assert not parent_has_eid, "container=True 未生效：父节点 aria-label 含子 EID，anchor 独立性被破坏"


async def test_screener_anchor_click_strategy_dropdown_opens(e2e_page: FletPage):
    """验证 AnchorPage.click 对 COMPLEX 类 (Dropdown) 触发展开.

    PoC A5f 实证：textContent 定位后 mouse.click 触发 aria-expanded=true.
    """
    ap = AnchorPage(e2e_page.page, e2e_page)
    screener = ScreenerPage(e2e_page)
    await screener.open()

    await ap.expect_visible(EIDS.SCREENER.STRATEGY_DROPDOWN, timeout_ms=15000)
    await ap.click(EIDS.SCREENER.STRATEGY_DROPDOWN)

    # 点击后应出现 aria-expanded="true" 的 Dropdown 顶层节点
    expanded = e2e_page.page.locator('flt-semantics[role="button"][aria-expanded="true"]')
    await expanded.wait_for(state="visible", timeout=ap._tm(10000))
    assert await expanded.count() >= 1, "Dropdown 点击后应展开 (aria-expanded=true)"

    # 关闭 Dropdown 面板（按 Escape，避免影响后续测试）
    await e2e_page.page.keyboard.press("Escape")
    await e2e_page.page.wait_for_timeout(500)


async def test_screener_anchor_click_run_button_triggers_callback(e2e_page: FletPage):
    """验证 AnchorPage.click 对 INTERACTIVE 类 (Button) 触发 on_click 回调.

    方案 §9 Task 1.4②/1.5 DoD：E2E 模式下 AnchorPage.click(EIDS.SCREENER.RUN_BUTTON)
    触发 Button.on_click。回归 PoC A3 假设在生产 screener_view 场景。

    前置：选择策略以启用 run_button（run_disabled = not state.selected_strategy，
    disabled 状态下 CanvasKit 不生成内层 [flt-tappable]，click 无法定位）。
    验证方式：click 后按钮文案应从"执行选股"变为"停止选股"（state.loading=True）。
    """
    from tests.e2e.labels import strategy_label

    ap = AnchorPage(e2e_page.page, e2e_page)
    screener = ScreenerPage(e2e_page)
    await screener.open()

    # 前置：选择策略以启用 run_button（未选策略时 disabled → 无 flt-tappable）
    await ap.expect_visible(EIDS.SCREENER.STRATEGY_DROPDOWN, timeout_ms=15000)
    await ap.select_option(EIDS.SCREENER.STRATEGY_DROPDOWN, strategy_label("volume_breakout"))

    # 验证 run_button 可点击（enabled 后生成 flt-tappable）
    await ap.expect_visible(EIDS.SCREENER.RUN_BUTTON, timeout_ms=15000)
    await ap.click(EIDS.SCREENER.RUN_BUTTON)

    # on_click 触发后 state.loading=True，按钮文案变为"停止选股"
    # timeout_ms 传原始值，expect_text 内部 self._tm() 会乘以 multiplier
    await e2e_page.expect_text("停止选股", timeout_ms=15000)
