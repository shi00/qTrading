"""Unit tests: AnchorPage.select_option 菜单展开态门控（PR 585 E2E 修复）。

验证核心逻辑：菜单关闭时预探测不得执行（避免全页面文本匹配误命中结果表表头
「代码」→ 菜单从不打开 → 点击落空），必须走展开流程；菜单已展开时才允许预探测。

覆盖两种分支：
1. 菜单关闭 → 走展开流程（_wait_for_text_anchor 被调用），预探测不短路展开。
2. 菜单已展开 → 预探测命中选项（_wait_for_text_anchor 不被调用）。

用最小 stub 替换 Playwright Page / FletPage，不依赖真实浏览器（同
test_data_page_select_table_retry.py 模式）。
"""

from unittest.mock import AsyncMock

import pytest

from tests.e2e.helpers.anchor_page import AnchorPage
from ui.testing.e2e_ids import AnchorKind

_FILTER_COL_DROPDOWN: tuple[str, AnchorKind] = ("e2e.data.filter_col_dropdown", AnchorKind.COMPLEX)


class _FakeHandle:
    """模拟 ElementHandle：click 后模拟 on_select 触发 → 菜单收合。"""

    def __init__(self) -> None:
        self._visible = True

    async def click(self, force: bool = False) -> None:
        # 选择落地 → Material Dropdown 收合，选项节点不可见/移除
        self._visible = False

    async def is_visible(self) -> bool:
        return self._visible


class _FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []

    async def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class _FakePage:
    """最小 Playwright page stub：仅暴露 select_option 用到的能力。"""

    def __init__(self) -> None:
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.wait_for_timeout = AsyncMock()


class _FakeFletPage:
    def __init__(self) -> None:
        self._timeout_multiplier = 1.0


@pytest.fixture
def ap() -> AnchorPage:
    return AnchorPage(page=_FakePage(), fp=_FakeFletPage(), timeout_multiplier=1.0)  # type: ignore[arg-type]


async def _run_select_option(ap: AnchorPage, expanded_values: list[str | None]) -> _FakeHandle:
    """公共跑法：注入展开态序列 + 选项 handle + 展开定位桩，执行 select_option。"""
    handle = _FakeHandle()
    ap._read_expanded = AsyncMock(side_effect=expanded_values)
    ap._find_option_element = AsyncMock(return_value=handle)
    ap._wait_for_text_anchor = AsyncMock(return_value={"x": 10.0, "y": 20.0, "w": 100.0, "h": 30.0})
    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=1000)
    return handle


@pytest.mark.asyncio
async def test_select_option_menu_closed_goes_expand_path(ap: AnchorPage) -> None:
    """菜单关闭（expanded 读取为 None）→ 必须走展开流程，预探测不短路展开。

    旧实现（PR 585 失败根因）：菜单关闭时全页面文本匹配可能误命中结果表表头
    「代码」→ 跳过展开 → 点击落空。修复后关闭态一律先展开。
    """
    handle = await _run_select_option(ap, expanded_values=[None, None])

    # 展开流程被触发（获取 bbox + 物理点击）
    ap._wait_for_text_anchor.assert_awaited_once()
    assert ap.page.mouse.clicks, "菜单关闭态必须触发展开点击"
    # 展开后通过轮询找到选项并点击，选择落地 → 菜单收合 → select_option 正常返回
    assert ap._find_option_element.await_count >= 1
    assert not handle._visible  # 选项已点击


@pytest.mark.asyncio
async def test_select_option_menu_already_open_uses_preselect(ap: AnchorPage) -> None:
    """菜单已展开（expanded 读取为 "true"）→ 预探测命中选项，不再重复展开。"""
    ap._read_expanded = AsyncMock(side_effect=["true", "true"])
    handle = _FakeHandle()
    ap._find_option_element = AsyncMock(return_value=handle)
    ap._wait_for_text_anchor = AsyncMock(return_value={"x": 10.0, "y": 20.0, "w": 100.0, "h": 30.0})
    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=1000)

    # 展开态残留 → 先 Escape 收合
    assert "Escape" in ap.page.keyboard.pressed
    # 已展开 → 预探测直接命中选项，无需再走 _wait_for_text_anchor 展开
    ap._find_option_element.assert_awaited()
    ap._wait_for_text_anchor.assert_not_awaited()
    assert not handle._visible
