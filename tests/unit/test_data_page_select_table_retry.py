"""Unit tests for DataPage.select_table whole-method retry (PR-4 Task 4.2).

select_table 在 CI 高负载导致的下拉展开/选项渲染抖动（option not found）或
TABLE_READY 未按时出现时整体重试（N=3）。纯逻辑测试，不依赖真实浏览器/
Playwright：用 stub AnchorPage 替换 self.ap，注入可控的 side_effect 驱动
重试路径，并 mock asyncio.sleep 避免真实等待。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.pages import DataPage
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import EIDS

pytestmark = pytest.mark.unit


class _FakeAp:
    """最小 AnchorPage stub，仅暴露 select_table 用到的三个方法。"""

    def __init__(
        self,
        select_option_effects: list | None = None,
        expect_visible_effects: list | None = None,
    ) -> None:
        self.select_option = AsyncMock(side_effect=select_option_effects)
        self.expect_hidden = AsyncMock()
        self.expect_visible = AsyncMock(side_effect=expect_visible_effects)


def _make_data(ap: _FakeAp) -> DataPage:
    """构造 DataPage 并替换 self.ap 为 stub（避免真实 AnchorPage/Playwright）。"""
    fake_page = MagicMock()
    page = DataPage(fake_page)
    page.ap = ap  # type: ignore[assignment]  # 测试注入 stub 替换 AnchorPage
    return page


@patch("tests.e2e.pages.asyncio.sleep", new_callable=AsyncMock)
async def test_select_table_first_attempt_success_no_retry(_sleep: AsyncMock) -> None:
    """首次即成功 → 不重试，select_option / expect_visible 各只调用 1 次。"""
    ap = _FakeAp()
    page = _make_data(ap)

    await page.select_table("stock_basic")

    ap.select_option.assert_awaited_once_with(EIDS.DATA.TABLE_DROPDOWN, "stock_basic", timeout_ms=TIMEOUTS.TITLE)
    ap.expect_visible.assert_awaited_once_with(EIDS.DATA.TABLE_READY, timeout_ms=TIMEOUTS.TITLE)
    _sleep.assert_not_awaited()


@patch("tests.e2e.pages.asyncio.sleep", new_callable=AsyncMock)
async def test_select_table_retries_on_option_not_found(_sleep: AsyncMock) -> None:
    """首次 select_option 抛 option not found、第二次成功 → 整体重试 1 次。"""
    ap = _FakeAp(select_option_effects=[RuntimeError("option not found"), None])
    page = _make_data(ap)

    await page.select_table("stock_basic")

    assert ap.select_option.await_count == 2
    assert ap.expect_visible.await_count == 1
    _sleep.assert_awaited_once()


@patch("tests.e2e.pages.asyncio.sleep", new_callable=AsyncMock)
async def test_select_table_retries_on_table_ready_timeout(_sleep: AsyncMock) -> None:
    """首次 expect_visible(TABLE_READY) 抛错、第二次成功 → 整体重试 1 次。"""
    ap = _FakeAp(expect_visible_effects=[RuntimeError("TABLE_READY not visible"), None])
    page = _make_data(ap)

    await page.select_table("stock_basic")

    assert ap.select_option.await_count == 2
    assert ap.expect_visible.await_count == 2
    _sleep.assert_awaited_once()


@patch("tests.e2e.pages.asyncio.sleep", new_callable=AsyncMock)
async def test_select_table_exhausts_retries_raises_original(_sleep: AsyncMock) -> None:
    """3 次都失败 → 抛原始异常（保留末次 cause，而非包装成 RuntimeError）。"""
    orig = RuntimeError("option not found")
    ap = _FakeAp(select_option_effects=[orig, orig, orig])
    page = _make_data(ap)

    with pytest.raises(RuntimeError) as exc_info:
        await page.select_table("stock_basic")

    assert exc_info.value is orig
    assert ap.select_option.await_count == 3
    assert _sleep.await_count == 2
