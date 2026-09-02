"""Unit tests for SettingsPage.click_tushare_verify confirm-window scaling.

点击「验证 Token」按钮后，_confirm 短轮询等待「验证中」warning 文本或错误文本。
轮询窗口 `deadline = time.monotonic() + 2.0` 必须按 E2E_TIMEOUT_MULTIPLIER 缩放
（对齐 ScreenerPage.run 的 expect_hidden 内部 _tm 缩放），否则 CI 慢速 VM 下
（multiplier=2.0）窗口仍为 2s，渲染延迟超过 7s 总预算时 3 次 confirm 全部超时
→ retry_until_triggered 误报"interaction not triggered"（CI 50% flaky 根因）。

纯逻辑测试：用可控时钟 stub 替换 FletPage/AnchorPage，patch time.monotonic 与
asyncio.sleep 避免真实等待（对齐 test_screener_page_run_retry.py 范式）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.pages import SettingsPage

pytestmark = pytest.mark.unit


class _FakeAp:
    """最小 AnchorPage stub，仅暴露 click_tushare_verify 用到的 click。"""

    def __init__(self) -> None:
        self.click = AsyncMock()


class _SimPage:
    """可控时钟 FletPage stub：has_text 在模拟时间 t >= text_appears_at 后返回 True。

    _t 由 wait_for_timeout 与 asyncio.sleep side_effect 推进，模拟真实时间流逝；
    _confirm 的 deadline 基于 time.monotonic()（被 patch 到 _t）。
    """

    def __init__(self, multiplier: float, text_appears_at: float) -> None:
        self._timeout_multiplier = multiplier
        self._t = 0.0
        self._text_appears_at = text_appears_at
        self.page = MagicMock()
        self.page.wait_for_timeout = AsyncMock(side_effect=self._advance_ms)

    def _advance_ms(self, ms: int) -> None:
        self._t += ms / 1000.0

    def advance_s(self, s: float) -> None:
        self._t += s

    async def has_text(self, text: str) -> bool:
        return self._t >= self._text_appears_at


def _make_settings(page: _SimPage) -> tuple[SettingsPage, _FakeAp]:
    """构造 SettingsPage 并替换 self.ap 为 stub（避免真实 AnchorPage/Playwright）。"""
    settings = SettingsPage(page)  # type: ignore[arg-type]  # 测试注入时钟 stub 替换 FletPage
    ap = _FakeAp()
    settings.ap = ap  # type: ignore[assignment]  # 测试注入 stub 替换 AnchorPage
    return settings, ap


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_confirm_window_scaled_survives_ci_slow_render(_sleep: AsyncMock) -> None:
    """CI 慢渲染（文本第 8s 才出现）：multiplier=2.0 时轮询窗口 4s/次、总预算 13s → 重试 1 次后命中。

    修复前 _confirm 窗口固定 2.0s（未乘 multiplier），总预算约 7s < 8s → 3 次
    confirm 全部超时抛 RuntimeError。修复后窗口 2.0*2.0=4.0s，第 2 次 confirm 的
    新鲜窗口（t≈4.5s→8.5s）可命中 8.0s 出现的文本。
    """
    page = _SimPage(multiplier=2.0, text_appears_at=8.0)
    settings, ap = _make_settings(page)
    _sleep.side_effect = lambda s: page.advance_s(float(s))

    with patch("tests.e2e.pages.time.monotonic", side_effect=lambda: page._t):
        await settings.click_tushare_verify()

    assert ap.click.await_count == 2
    assert _sleep.await_count == 1  # 两次 attempt 之间的一次间隔


@patch("tests.e2e.helpers.anchor_page.asyncio.sleep", new_callable=AsyncMock)
async def test_confirm_window_with_multiplier_one_keeps_original_budget(_sleep: AsyncMock) -> None:
    """本地 multiplier=1.0：窗口保持 2s（修复前后行为一致），文本在窗口内出现 → 首击即命中。"""
    page = _SimPage(multiplier=1.0, text_appears_at=1.0)
    settings, ap = _make_settings(page)

    with patch("tests.e2e.pages.time.monotonic", side_effect=lambda: page._t):
        await settings.click_tushare_verify()

    ap.click.assert_awaited_once()
    _sleep.assert_not_awaited()
