"""E2E 助手层 flake 加固单测（CI run 42488609156 core-a 回归）。

覆盖三处防护：
1. FletPage 采集「client is out of sync with the server」日志并作超时诊断上下文（不作硬失败）；
2. ScreenerPage 在视口断言前关闭意外弹出的详情对话框；
3. ``expect_result(ts_code=...)`` 以结果行 anchor 而非任意文本作为判据。
"""

# pyright: reportAttributeAccessIssue=false
# 本文件含 MagicMock 替身的动态属性赋值（return_value/AsyncMock 注入），
# pyright 无法验证替身属性类型，统一在本文件禁用该告警；行为由用例本身验证。

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
# FletPage: 客户端失步诊断注入（不作硬失败，仅超时时附加上下文）
# ---------------------------------------------------------------------------


def test_console_desync_message_detected() -> None:
    page = MagicMock()
    fp = FletPage(page)

    _console_handler(page)(SimpleNamespace(text=_DESYNC_TEXT))

    assert fp.has_client_desync is True
    assert "out of sync" in fp.desync_note()
    assert "2 条" not in fp.desync_note()


def test_console_desync_duplicate_messages_logged_once() -> None:
    """重复失步消息仅首条记 warning（计数保留），避免 CI 日志刷屏。"""
    page = MagicMock()
    fp = FletPage(page)
    handler = _console_handler(page)

    for _ in range(5):
        handler(SimpleNamespace(text=_DESYNC_TEXT))

    assert len(fp._client_desync_messages) == 5
    assert "5 条" in fp.desync_note()


def test_unrelated_console_message_ignored() -> None:
    page = MagicMock()
    fp = FletPage(page)

    _console_handler(page)(SimpleNamespace(text="just a warning"))

    assert fp.has_client_desync is False
    assert fp.desync_note() == ""


async def test_expect_text_timeout_error_includes_desync_note() -> None:
    """失步不作硬失败（绿色流水线亦常态出现）；仅在 expect_text 真实超时时注入诊断。"""
    page = MagicMock()
    fp = FletPage(page)
    handler = _console_handler(page)
    handler(SimpleNamespace(text=_DESYNC_TEXT))
    # 模拟等待与轮询全部未命中（MagicMock 动态属性，pyright 无法验证替身类型）
    fp.page.get_by_text.return_value.count = AsyncMock(return_value=0)
    locator = fp.page.locator.return_value
    locator.or_.return_value.first.wait_for = AsyncMock(side_effect=TimeoutError("timeout"))
    locator.count = AsyncMock(return_value=0)
    fp.page.wait_for_timeout = AsyncMock()
    fp._save_screenshot_only = MagicMock()

    with pytest.raises(AssertionError, match="out of sync"):
        await fp.expect_text("平安银行", timeout_ms=100)


async def test_anchor_page_expect_visible_timeout_includes_desync_note() -> None:
    """AnchorPage.expect_visible 超时且存在失步日志时，错误信息含失步诊断。"""

    class _FpStub:
        def desync_note(self) -> str:
            return "〔诊断〕出现失步日志"

    fp_stub = _FpStub()
    page = MagicMock()
    locator = page.locator.return_value.first
    locator.wait_for = AsyncMock(side_effect=TimeoutError("timeout"))
    ap = AnchorPage(page, fp_stub, timeout_multiplier=1.0)  # type: ignore[arg-type]  # stub 仅需该接口

    with pytest.raises(RuntimeError, match="失步日志"):
        await ap.expect_visible(EIDS.SCREENER.RUN_BUTTON, timeout_ms=50)


async def test_anchor_page_expect_visible_no_note_keeps_original_exc() -> None:
    """无失步日志时，超时异常原样传播（不包装 RuntimeError）。"""

    class _FpStub:
        def desync_note(self) -> str:
            return ""

    fp_stub = _FpStub()
    page = MagicMock()
    locator = page.locator.return_value.first
    locator.wait_for = AsyncMock(side_effect=TimeoutError("timeout"))
    ap = AnchorPage(page, fp_stub, timeout_multiplier=1.0)  # type: ignore[arg-type]  # stub 仅需该接口

    with pytest.raises(TimeoutError) as exc_info:
        await ap.expect_visible(EIDS.SCREENER.RUN_BUTTON, timeout_ms=50)
    # 强断言：异常类型未被包装为 RuntimeError，错误信息不含失步诊断
    assert "失步" not in str(exc_info.value)
    assert type(exc_info.value) is TimeoutError


# ---------------------------------------------------------------------------
# ScreenerPage: 意外详情对话框兜底
# ---------------------------------------------------------------------------


async def test_dismiss_detail_dialog_noop_when_closed() -> None:
    ap = _FakeAp(dialog_open=False)
    screener, _ = _make_screener(ap)

    assert await screener.dismiss_detail_dialog_if_open() is False
    assert ap.is_visible.await_count == screener._DISMISS_PROBE_ATTEMPTS
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

    # 行 anchor 是主判据（SCREEN_RESULT 级等待），文本降为 FAST 短超时内容校验
    ap.expect_visible.assert_awaited_once_with(EIDS.SCREENER.result_row("000001.SZ"), timeout_ms=TIMEOUTS.SCREEN_RESULT)
    expect_text.assert_awaited_once_with("平安银行", timeout_ms=TIMEOUTS.FAST)


async def test_expect_result_text_only_keeps_legacy_behavior() -> None:
    ap = _FakeAp()
    screener, expect_text = _make_screener(ap)

    await screener.expect_result("暂无结果")

    ap.expect_visible.assert_not_awaited()
    expect_text.assert_awaited_once_with("暂无结果", timeout_ms=TIMEOUTS.SCREEN_RESULT)
