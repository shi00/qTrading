"""Unit tests for ScreenerPage.run retry-on-confirmation (PR-4 Task 4.1).

run 用 retry_until_triggered 包裹 RUN_BUTTON 点击，以 RUN_BUTTON 消失为"已触发"
确认指标（state.loading=True 后按钮被替换为 STOP）。纯逻辑测试，不依赖真实
浏览器/Playwright：用 stub AnchorPage 替换 self.ap，注入可控的 count 返回值
驱动重试路径，并 mock asyncio.sleep 避免真实等待。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.pages import ScreenerPage
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import EIDS

pytestmark = pytest.mark.unit


class _FakeAp:
    """最小 AnchorPage stub，仅暴露 run 用到的 click / count。"""

    def __init__(self, count_results: list[int]) -> None:
        self.click = AsyncMock()
        self.count = AsyncMock(side_effect=count_results)


def _make_screener(count_results: list[int]) -> tuple[ScreenerPage, _FakeAp]:
    """构造 ScreenerPage 并替换 self.ap 为 stub（避免真实 AnchorPage/Playwright）。"""
    fake_page = MagicMock()
    screener = ScreenerPage(fake_page)
    ap = _FakeAp(count_results)
    screener.ap = ap  # type: ignore[assignment]  # 测试注入 stub 替换 AnchorPage
    return screener, ap


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_run_immediate_trigger_clicks_once(_sleep: AsyncMock) -> None:
    """首次 count==0（立即触发）→ click 只调用 1 次，run 正常返回。"""
    screener, ap = _make_screener([0])

    await screener.run()

    ap.click.assert_awaited_once_with(EIDS.SCREENER.RUN_BUTTON, timeout_ms=TIMEOUTS.TITLE)
    _sleep.assert_not_awaited()


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_run_trigger_on_second_attempt_clicks_twice(_sleep: AsyncMock) -> None:
    """首次 count>0、第二次 ==0 → click 调用 2 次，run 正常返回。"""
    screener, ap = _make_screener([1, 0])

    await screener.run()

    assert ap.click.await_count == 2
    assert ap.count.await_count == 2
    _sleep.assert_awaited_once()


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_run_always_positive_raises_runtime_error(_sleep: AsyncMock) -> None:
    """始终 count>0（3 次耗尽）→ 抛 RuntimeError，click 恰好调用 3 次。"""
    screener, ap = _make_screener([1, 1, 1])

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await screener.run()

    assert ap.click.await_count == 3
    assert ap.count.await_count == 3
    assert _sleep.await_count == 2


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_confirm_predicate_uses_count_of_run_button(_sleep: AsyncMock) -> None:
    """confirm 谓词：count(RUN_BUTTON)==0 判定已触发，且按 INTERACTIVE 走该 EID 计数。"""
    screener, ap = _make_screener([0])

    await screener.run()

    ap.count.assert_awaited_once_with(EIDS.SCREENER.RUN_BUTTON)
