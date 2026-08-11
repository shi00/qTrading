"""Unit tests for ScreenerPage.run retry-on-confirmation (PR-4 Task 4.1).

run 用 retry_until_triggered 包裹 RUN_BUTTON 点击，以 RUN_BUTTON 消失为"已触发"
确认指标（state.loading=True 后按钮被替换为 STOP）。纯逻辑测试，不依赖真实
浏览器/Playwright：用 stub AnchorPage 替换 self.ap，注入可控的 expect_hidden
结果驱动重试路径，并 mock asyncio.sleep 避免真实等待。

评审 F1: _confirm 改用短轮询等待 RUN_BUTTON 消失（expect_hidden 2s），测试相应
改为 mock expect_hidden 而非 count，并区分"渲染延迟"与"点击被吞"两种场景。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tests.e2e.pages import ScreenerPage
from ui.testing.e2e_ids import EIDS

pytestmark = pytest.mark.unit

# 与 ScreenerPage.run 内 _confirm 的短轮询超时对齐
_CONFIRM_TIMEOUT_MS = 2000


class _FakeAp:
    """最小 AnchorPage stub，仅暴露 run 用到的 click / expect_hidden。"""

    def __init__(self, expect_hidden_results: list) -> None:
        self.click = AsyncMock()
        self.expect_hidden = AsyncMock(side_effect=expect_hidden_results)


def _make_screener(expect_hidden_results: list) -> tuple[ScreenerPage, _FakeAp]:
    """构造 ScreenerPage 并替换 self.ap 为 stub（避免真实 AnchorPage/Playwright）。"""
    fake_page = MagicMock()
    screener = ScreenerPage(fake_page)
    ap = _FakeAp(expect_hidden_results)
    screener.ap = ap  # type: ignore[assignment]  # 测试注入 stub 替换 AnchorPage
    return screener, ap


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_render_delay_absorbed_without_reclick(_sleep: AsyncMock) -> None:
    """点击成功但按钮延迟消失（渲染延迟）：expect_hidden 首次即返回 → 不重复点击。

    旧实现用 count(RUN_BUTTON)==0 即时判定，按钮未消失时会返回 1 误判"未触发"
    而重复点击；新实现短轮询等待，按钮在轮询窗口内消失即视为已触发，click 只
    调用 1 次。
    """
    screener, ap = _make_screener([None])

    await screener.run()

    assert ap.click.await_count == 1
    assert ap.expect_hidden.await_count == 1
    _sleep.assert_not_awaited()


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_render_delay_resolved_on_second_confirm(_sleep: AsyncMock) -> None:
    """渲染延迟较长：首次 expect_hidden 超时、第二次成功 → 重试 1 次后返回。"""
    screener, ap = _make_screener([PlaywrightTimeoutError, None])

    await screener.run()

    assert ap.click.await_count == 2
    assert ap.expect_hidden.await_count == 2
    _sleep.assert_awaited_once()


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_click_swallowed_button_stays_visible_raises(_sleep: AsyncMock) -> None:
    """点击被吞、RUN_BUTTON 持续可见：expect_hidden 始终抛超时 → 3 次重试后抛 RuntimeError。"""
    screener, ap = _make_screener([PlaywrightTimeoutError] * 3)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await screener.run()

    assert ap.click.await_count == 3
    assert ap.expect_hidden.await_count == 3
    assert _sleep.await_count == 2


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_confirm_predicate_uses_expect_hidden_of_run_button(_sleep: AsyncMock) -> None:
    """confirm 谓词：短轮询等待 RUN_BUTTON 消失（expect_hidden 2s），返回即视为已触发。"""
    screener, ap = _make_screener([None])

    await screener.run()

    ap.expect_hidden.assert_awaited_once_with(EIDS.SCREENER.RUN_BUTTON, timeout_ms=_CONFIRM_TIMEOUT_MS)
