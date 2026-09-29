"""Unit tests: AnchorPage.select_option 菜单展开态门控（PR 585 E2E 修复）。

验证核心逻辑：菜单关闭时预探测不得执行（避免全页面文本匹配误命中结果表表头
「代码」→ 菜单从不打开 → 点击落空），必须走展开流程；菜单已展开时才允许预探测。

覆盖三种分支：
1. 菜单关闭 → 走展开流程（`_identifier_node_box` 被调用），预探测不短路展开。
2. 菜单已展开 → 预探测命中选项（`_identifier_node_box` 不被调用）。
3. 展开流程先 `scroll_into_view` 再取 bbox（避免视口外坐标点击被静默丢弃）。

identifier 是唯一定位通道：展开态读取与顶层节点定位均走 identifier
（`_read_expanded_by_identifier` / `_identifier_node_box`），无 legacy 分支。

用最小 stub 替换 Playwright Page / FletPage，不依赖真实浏览器（同
test_data_page_select_table_retry.py 模式）。
"""

from typing import Any
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

    async def evaluate(self, script: str, arg: Any = None) -> None:
        return None


class _FakeFletPage:
    def __init__(self) -> None:
        self._timeout_multiplier = 1.0


@pytest.fixture
def ap() -> AnchorPage:
    return AnchorPage(
        page=_FakePage(),  # type: ignore[arg-type]
        fp=_FakeFletPage(),  # type: ignore[arg-type]
        timeout_multiplier=1.0,
    )


def _stub_expanded(ap: AnchorPage, values: list[str | None]) -> None:
    """展开态读取打桩（identifier 是唯一定位通道 → `_read_expanded_by_identifier`）。"""
    ap._read_expanded_by_identifier = AsyncMock(side_effect=values)


def _expand_locator_mock(ap: AnchorPage) -> AsyncMock:
    """返回展开下拉用的定位桩（identifier 唯一路径 → _identifier_node_box）。"""
    return ap._identifier_node_box  # type: ignore[return-value]


async def _run_select_option(ap: AnchorPage, expanded_values: list[str | None]) -> _FakeHandle:
    """公共跑法：注入展开态序列 + 选项 handle + 展开定位桩，执行 select_option。"""
    handle = _FakeHandle()
    _stub_expanded(ap, expanded_values)
    ap._find_option_element = AsyncMock(return_value=handle)
    ap._identifier_node_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0})
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
    _expand_locator_mock(ap).assert_awaited_once()
    assert ap.page.mouse.clicks, "菜单关闭态必须触发展开点击"
    # 展开后通过轮询找到选项并点击，选择落地 → 菜单收合 → select_option 正常返回
    assert ap._find_option_element.await_count >= 1
    assert not handle._visible  # 选项已点击


@pytest.mark.asyncio
async def test_select_option_menu_already_open_uses_preselect(ap: AnchorPage) -> None:
    """菜单已展开（expanded 读取为 "true"）→ 预探测命中选项，不再重复展开。"""
    handle = _FakeHandle()
    _stub_expanded(ap, ["true", "true"])
    ap._find_option_element = AsyncMock(return_value=handle)
    ap._identifier_node_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0})
    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=1000)

    # 展开态残留 → 先 Escape 收合
    assert "Escape" in ap.page.keyboard.pressed
    # 已展开 → 预探测直接命中选项，无需再走展开定位
    ap._find_option_element.assert_awaited()
    _expand_locator_mock(ap).assert_not_awaited()
    assert not handle._visible


@pytest.mark.asyncio
async def test_select_option_scrolls_into_view_before_click(ap: AnchorPage) -> None:
    """展开流程必须先把 Dropdown 滚入视口，再取 bbox 点击（视口外坐标点击会被静默丢弃）。

    根因（CI run 36535917168）：MAJOR-08 将日志级别收敛进折叠的「高级（开发者）」分组后，
    展开分组把 Dropdown 推到视口下方（trace 实证 identifier 节点 bbox y=991 > 视口高 900），
    策略 A/B 的 ``mouse.click`` 坐标 (y=1015) 落在视口外 → 点击被静默丢弃 → 下拉永不展开
    → "option not found"。故 scroll_into_view 必须发生在取 bbox（进而点击）之前。
    """
    events: list[str] = []

    async def _scroll(*_args: Any, **_kwargs: Any) -> None:
        events.append("scroll")

    async def _box(*_args: Any, **_kwargs: Any) -> dict[str, float]:
        events.append("box")
        return {"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0}

    handle = _FakeHandle()
    _stub_expanded(ap, [None, None])
    ap.scroll_into_view = AsyncMock(side_effect=_scroll)
    ap._identifier_node_box = AsyncMock(side_effect=_box)
    ap._find_option_element = AsyncMock(return_value=handle)

    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=1000)

    ap.scroll_into_view.assert_awaited_once()
    # 顺序保证：先滚入视口，再取 bbox（随后立即物理点击）
    assert events[:2] == ["scroll", "box"]
    assert ap.page.mouse.clicks, "滚入视口后必须发生展开点击"
    assert not handle._visible
