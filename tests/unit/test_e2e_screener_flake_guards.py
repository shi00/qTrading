"""E2E 助手层 flake 加固单测（CI run 42488609156 core-a 回归）。

覆盖三处防护：
1. FletPage 检测控制台「client is out of sync with the server」后快速失败；
2. ScreenerPage 在视口断言前关闭意外弹出的详情对话框；
3. ``expect_result(ts_code=...)`` 以结果行 anchor 而非任意文本作为判据。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.e2e.helpers.anchor_page import AnchorPage
from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.pages import ScreenerPage
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import EIDS

pytestmark = pytest.mark.unit

_DESYNC_TEXT = (
    "Flet: dropped a patch for unknown control 123 - the client is out of sync with the server and needs a reload"
)


def _console_handler(page: MagicMock):
    """取 FletPage 注册到 Playwright 的 console 监听器。"""
    for call in page.on.call_args_list:
        if call.args[0] == "console":
            return call.args[1]
    raise AssertionError("FletPage 未注册 console 监听（无法捕获客户端失步）")


class _FakeAp:
    """最小 AnchorPage stub，仅暴露 flake 防护用到的异步方法。"""

    def __init__(self, dialog_open: bool = False) -> None:
        self.is_visible = AsyncMock(return_value=dialog_open)
        self.click = AsyncMock()
        self.expect_hidden = AsyncMock()
        self.expect_visible = AsyncMock()
        self.expect_in_viewport = AsyncMock()


def _make_screener(ap: _FakeAp) -> tuple[ScreenerPage, AsyncMock]:
    """构造 ScreenerPage 并注入 stub，返回 (screener, expect_text mock)。"""
    page = MagicMock()
    expect_text = AsyncMock()
    page.expect_text = expect_text
    screener = ScreenerPage(page)
    screener.ap = ap  # type: ignore[assignment]  # 测试注入 stub 替换 AnchorPage
    return screener, expect_text


# ---------------------------------------------------------------------------
# FletPage: 客户端失步快速失败
# ---------------------------------------------------------------------------


def test_console_desync_message_detected() -> None:
    page = MagicMock()
    fp = FletPage(page)

    _console_handler(page)(SimpleNamespace(text=_DESYNC_TEXT))

    assert fp.has_client_desync is True
    with pytest.raises(AssertionError, match="out of sync with the server"):
        fp.raise_if_client_desync()


def test_unrelated_console_message_ignored() -> None:
    page = MagicMock()
    fp = FletPage(page)

    _console_handler(page)(SimpleNamespace(text="just a warning"))

    assert fp.has_client_desync is False
    fp.raise_if_client_desync()  # 不抛


async def test_expect_text_fails_fast_after_desync() -> None:
    """失步后 expect_text 不再等满超时，直接抛带诊断信息的 AssertionError。"""
    page = MagicMock()
    fp = FletPage(page)
    _console_handler(page)(SimpleNamespace(text=_DESYNC_TEXT))

    with pytest.raises(AssertionError, match="out of sync with the server"):
        await fp.expect_text("平安银行", timeout_ms=60000)


async def test_anchor_page_expect_visible_fails_fast_after_desync() -> None:
    """AnchorPage.expect_visible 同样在失步后快速失败（不触碰 Playwright 定位器）。"""

    class _FpStub:
        def raise_if_client_desync(self) -> None:
            raise AssertionError("out of sync with the server")

    ap = AnchorPage(MagicMock(), _FpStub(), timeout_multiplier=1.0)  # type: ignore[arg-type]  # stub 仅需该接口

    with pytest.raises(AssertionError, match="out of sync with the server"):
        await ap.expect_visible(EIDS.SCREENER.RUN_BUTTON)


# ---------------------------------------------------------------------------
# ScreenerPage: 意外详情对话框兜底
# ---------------------------------------------------------------------------


async def test_dismiss_detail_dialog_noop_when_closed() -> None:
    ap = _FakeAp(dialog_open=False)
    screener, _ = _make_screener(ap)

    assert await screener.dismiss_detail_dialog_if_open() is False
    ap.click.assert_not_awaited()
    ap.expect_hidden.assert_not_awaited()


async def test_dismiss_detail_dialog_closes_unexpected_dialog() -> None:
    ap = _FakeAp(dialog_open=True)
    screener, _ = _make_screener(ap)

    assert await screener.dismiss_detail_dialog_if_open() is True
    ap.click.assert_awaited_once_with(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, timeout_ms=TIMEOUTS.FAST)
    ap.expect_hidden.assert_awaited_once_with(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, timeout_ms=TIMEOUTS.FAST)


async def test_expect_row_in_viewport_dismisses_dialog_first() -> None:
    ap = _FakeAp(dialog_open=True)
    screener, _ = _make_screener(ap)

    await screener.expect_row_in_viewport("000001.SZ")

    ap.is_visible.assert_awaited_once_with(EIDS.DETAIL_DIALOG.CLOSE_BUTTON)
    ap.expect_in_viewport.assert_awaited_once_with(
        EIDS.SCREENER.result_row("000001.SZ"), timeout_ms=TIMEOUTS.SCREEN_RESULT
    )


# ---------------------------------------------------------------------------
# ScreenerPage.expect_result: 行 anchor 判据
# ---------------------------------------------------------------------------


async def test_expect_result_waits_row_anchor_when_ts_code_given() -> None:
    ap = _FakeAp()
    screener, expect_text = _make_screener(ap)

    await screener.expect_result("平安银行", ts_code="000001.SZ")

    ap.expect_visible.assert_awaited_once_with(EIDS.SCREENER.result_row("000001.SZ"), timeout_ms=TIMEOUTS.SCREEN_RESULT)
    expect_text.assert_awaited_once_with("平安银行", timeout_ms=TIMEOUTS.SCREEN_RESULT)


async def test_expect_result_text_only_keeps_legacy_behavior() -> None:
    ap = _FakeAp()
    screener, expect_text = _make_screener(ap)

    await screener.expect_result("暂无结果")

    ap.expect_visible.assert_not_awaited()
    expect_text.assert_awaited_once_with("暂无结果", timeout_ms=TIMEOUTS.SCREEN_RESULT)
