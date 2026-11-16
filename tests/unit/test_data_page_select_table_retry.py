"""Unit tests for DataPage.select_table whole-method retry (PR-4 Task 4.2).

select_table 在 CI 高负载导致的下拉展开/选项渲染抖动（option not found）或
目标表就绪锚点未按时出现时整体重试（N=3）。纯逻辑测试，不依赖真实浏览器/
Playwright：用 stub AnchorPage 替换 self.ap，注入可控的 side_effect 驱动
重试路径，并 mock asyncio.sleep 避免真实等待。

D21-OTHER-006: 等待条件改为目标表专属锚点 ``EIDS.DATA.table_ready(table)``；
「旧表 ready 仍可见、目标表未加载」必须失败（见
``test_select_table_does_not_accept_other_table_ready``）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.e2e.pages import DataPage
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import EIDS, Eid

pytestmark = pytest.mark.unit


class _FakeAp:
    """最小 AnchorPage stub，仅暴露 select_table 用到的两个方法。

    刻意不提供 ``expect_hidden``：D21-OTHER-006 后 select_table 不再以「通用 ready
    先消失」为判据；若实现回退到该写法，本 stub 会以 AttributeError 让用例失败。
    """

    def __init__(
        self,
        select_option_effects: list | None = None,
        expect_visible_effects: list | None = None,
    ) -> None:
        self.select_option = AsyncMock(side_effect=select_option_effects)
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
    # D21-OTHER-006: 等待的是目标表专属锚点，而非通用 TABLE_READY
    ap.expect_visible.assert_awaited_once_with(EIDS.DATA.table_ready("stock_basic"), timeout_ms=TIMEOUTS.TITLE)
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
    """首次目标表就绪锚点超时、第二次成功 → 整体重试 1 次。"""
    ap = _FakeAp(expect_visible_effects=[RuntimeError("table_ready not visible"), None])
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


@patch("tests.e2e.pages.asyncio.sleep", new_callable=AsyncMock)
async def test_select_table_does_not_accept_other_table_ready(_sleep: AsyncMock) -> None:
    """D21-OTHER-006 反例：旧表 ready 仍可见、目标表就绪锚点不出现 → 必须失败。

    旧实现只等通用 ``TABLE_READY``，切表未生效时上一张表的 ready 节点仍在可见，
    会静默返回假成功。本用例让「旧表 ready」恒可见、目标表锚点恒不可见，断言
    select_table 耗尽重试后抛错，且从未把旧表/通用锚点当作成功判据。
    """
    stale_eid = EIDS.DATA.table_ready("daily_quotes")
    target_eid = EIDS.DATA.table_ready("stock_basic")

    async def _visible(eid: Eid, timeout_ms: int) -> None:
        if eid == stale_eid:
            return  # 上一张表的 ready 仍未从 DOM 消失（切表未生效的假成功来源）
        raise RuntimeError(f"{eid[0]} not visible")

    ap = _FakeAp(expect_visible_effects=_visible)
    page = _make_data(ap)

    with pytest.raises(RuntimeError, match="stock_basic"):
        await page.select_table("stock_basic")

    assert ap.select_option.await_count == TIMEOUTS.RETRY_ATTEMPTS
    assert ap.expect_visible.await_count == TIMEOUTS.RETRY_ATTEMPTS
    assert all(call.args[0] == target_eid for call in ap.expect_visible.await_args_list)
    assert all(call.args[0] != EIDS.DATA.TABLE_READY for call in ap.expect_visible.await_args_list)
    assert _sleep.await_count == TIMEOUTS.RETRY_ATTEMPTS - 1
