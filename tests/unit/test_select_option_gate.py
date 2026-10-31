"""Unit tests: AnchorPage.select_option 菜单展开态门控（PR 585 E2E 修复）。

验证核心逻辑：菜单关闭时预探测不得执行（避免全页面文本匹配误命中结果表表头
「代码」→ 菜单从不打开 → 点击落空），必须走展开流程；菜单已展开时才允许预探测。

覆盖分支：
1. 菜单关闭 → 走展开流程（`_identifier_node_box` 被调用），预探测不短路展开。
2. 菜单已展开 → 预探测命中选项（`_identifier_node_box` 不被调用）。
3. 展开流程先 `scroll_into_view` 再取 bbox（避免视口外坐标点击被静默丢弃）。
4. 收合判据以 `aria-expanded` 为权威：选项节点仍 visible 也算已收合（不重试、不滚轮）。
5. 进入 retry 时菜单已收合 → `_interact` 守卫直接返回（不重复点击、不滚轮空转）。

identifier 是唯一定位通道：展开态读取与顶层节点定位均走 identifier
（`_read_expanded_by_identifier` / `_identifier_node_box`），无 legacy 分支。

用最小 stub 替换 Playwright Page / FletPage，不依赖真实浏览器（同
test_data_page_select_table_retry.py 模式）。
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.e2e.helpers import anchor_page
from tests.e2e.helpers.anchor_page import AnchorPage
from ui.testing.e2e_ids import AnchorKind

_FILTER_COL_DROPDOWN: tuple[str, AnchorKind] = ("e2e.data.filter_col_dropdown", AnchorKind.COMPLEX)


class _FakeHandle:
    """模拟 ElementHandle：click 后模拟 on_select 触发 → 菜单收合。

    ``collapse_on_click=False`` 模拟真实 Flet 行为：选择已落地、``aria-expanded`` 已翻转
    false，但选项语义节点在收合动画中仍短暂 visible（CI run 41044829654 实证）。
    """

    def __init__(
        self,
        panel_box: dict[str, float] | None = None,
        *,
        collapse_on_click: bool = True,
    ) -> None:
        self._visible = True
        self.panel_box = panel_box
        self.collapse_on_click = collapse_on_click
        self.clicks = 0
        self.visible_calls = 0

    async def click(self, force: bool = False) -> None:
        # 选择落地 → Material Dropdown 收合，选项节点不可见/移除
        self.clicks += 1
        if self.collapse_on_click:
            self._visible = False

    async def is_visible(self) -> bool:
        self.visible_calls += 1
        return self._visible

    async def bounding_box(self) -> dict[str, float]:
        # C8 面板锚定滚动读取的 bbox：默认完全在视口内（900 高视口）→ 无需滚动
        return {"x": 500.0, "y": 400.0, "width": 200.0, "height": 36.0}

    async def evaluate(self, script: str, arg: Any = None) -> dict[str, float] | None:
        # C8 面板识别探针（选项祖先链最近的 flt-semantics[role=group] 节点 rect）：
        # 默认未识别面板（返回 None → 退回视口判据），panel_box 场景回显面板 rect
        return self.panel_box


class _FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []
        self.moves: list[tuple[float, float]] = []
        self.wheels: list[tuple[float, float]] = []

    async def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    async def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))

    async def wheel(self, dx: float, dy: float) -> None:
        self.wheels.append((dx, dy))


class _FakeClock:
    """单调时钟 stub：每次读取前进固定步长，确定性复现「慢 wait 吞掉空等期限」。"""

    def __init__(self, step: float) -> None:
        self._now = 0.0
        self._step = step

    def monotonic(self) -> float:
        self._now += self._step
        return self._now


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
        # C8 面板锚定滚动读取的视口尺寸（与 test_anchor_page_identifier_locator 一致）
        self.viewport_size = {"width": 1400, "height": 900}

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
    """展开态读取打桩（identifier 是唯一定位通道 → `_read_expanded_by_identifier`）。

    收合确认会轮询读取展开态，末值长期复用，避免序列耗尽后抛 ``StopIteration``。
    """
    remaining = iter(values)
    tail = values[-1]

    async def _read(_eid_str: str) -> str | None:
        return next(remaining, tail)

    ap._read_expanded_by_identifier = _read  # type: ignore[method-assign]


def _expand_locator_mock(ap: AnchorPage) -> AsyncMock:
    """返回展开下拉用的定位桩（identifier 唯一路径 → _identifier_node_box）。"""
    return ap._identifier_node_box  # type: ignore[return-value]


async def _run_select_option(ap: AnchorPage, expanded_values: list[str | None]) -> _FakeHandle:
    """公共跑法：注入展开态序列 + 选项 handle + 展开定位桩，执行 select_option。

    ``scroll_into_view`` 打桩：68c25052 起其实现改走真实 Playwright locator + 物理滚轮
    （``_locator_by_identifier`` → ``page.locator`` / ``mouse.wheel``），gating 单测
    不应执行真实滚动实现（``_FakePage`` 无 locator），与 ``test_select_option_scrolls_into_view_before_click``
    既有打桩模式一致。
    """
    handle = _FakeHandle()
    _stub_expanded(ap, expanded_values)
    ap._find_option_element = AsyncMock(return_value=handle)
    ap.scroll_into_view = AsyncMock()
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
    _stub_expanded(ap, ["true", "true", "false"])
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


@pytest.mark.asyncio
async def test_select_option_confirms_collapse_by_aria_expanded(ap: AnchorPage) -> None:
    """收合判据以 ``aria-expanded`` 为权威：选项节点仍 visible 也必须判为已收合。

    回归（CI run 41044829654）：点击选项后 4ms 选项语义节点仍 visible（收合动画未卸载），
    旧判据 ``not is_visible()`` 判未收合 → 误入 retry → 在已收合菜单上重定位到下拉当前值
    残留节点（bbox 为 None）→ 滚轮以视口中心为锚点空转整页 → 误报 never within。
    """
    handle = _FakeHandle(collapse_on_click=False)
    # 展开前 None、展开后 None、收合确认读到 false（选项节点仍 visible）
    _stub_expanded(ap, [None, None, "false"])
    ap._find_option_element = AsyncMock(return_value=handle)
    ap.scroll_into_view = AsyncMock()
    ap._identifier_node_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0})

    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=1000)

    assert handle.clicks == 1, "选择只应点击一次"
    assert handle.visible_calls == 0, "展开态可读时不得回退 is_visible 判据"
    assert ap.page.mouse.wheels == [], "已收合菜单不得滚轮空转"


@pytest.mark.asyncio
async def test_select_option_retry_guard_skips_click_when_menu_collapsed(
    ap: AnchorPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进入 retry 时菜单已收合 → ``_interact`` 守卫直接返回，不重定位、不点击、不滚轮。

    回归（CI run 41044829654）：CI 单次 ``wait_for_timeout(100)`` 实测 2.17s 吞掉空等期限，
    空等首查落空后进 retry；retry 的 ``_interact`` 在已收合菜单上重定位到下拉当前值残留
    节点，随后 wheel 空转 37 次（5.3s）后误报 never within。
    """
    handle = _FakeHandle(collapse_on_click=False)
    # 展开前/后 None；空等首查仍 true、慢 wait 后仍 true → 期限耗尽进 retry；retry 读到 false
    _stub_expanded(ap, [None, None, "true", "true", "false", "false"])
    ap._find_option_element = AsyncMock(return_value=handle)
    ap.scroll_into_view = AsyncMock()
    ap._identifier_node_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0})
    # 单调时钟每次读取前进 1.5s：timeout 放大到 3000ms 使展开轮询窗口（2s）容得下步进，
    # 空等窗口（2s）则在两轮内确定耗尽，测试不实等
    monkeypatch.setattr(anchor_page, "time", _FakeClock(step=1.5))

    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=3000)

    assert handle.clicks == 1, "菜单已收合时 retry 守卫不得重复点击"
    assert ap.page.mouse.wheels == [], "菜单已收合时不得进入 C8 滚轮"


class _FakeInvisibleHandle(_FakeHandle):
    """模拟 CanvasKit 语义节点：bbox 有效但 ``ElementHandle.click`` 抛 not visible。

    CI run 41145284114 实证：选项 bbox（257×40）与菜单面板均有效，``click(force=True)``
    仍抛 ``Element is not visible``（Playwright 对语义节点可见性判定不可靠）。
    """

    async def click(self, force: bool = False) -> None:
        self.clicks += 1
        raise RuntimeError(
            "ElementHandle.click: Element is not visible\n"
            "Call log:\n  - attempting click action\n    - scrolling into view if needed\n"
        )


@pytest.mark.asyncio
async def test_select_option_falls_back_to_mouse_click_when_handle_not_visible(ap: AnchorPage) -> None:
    """``ElementHandle.click`` 抛 not visible → 按 bbox 中心回落物理鼠标点击（C2）。

    只回落 ``not visible`` 这一种错误：其余异常原样抛出（不掩盖真实故障）。
    """
    handle = _FakeInvisibleHandle()
    # 展开前 None、展开后 None、收合确认读到 false（回落点击视为落地）
    _stub_expanded(ap, [None, None, "false"])
    ap._find_option_element = AsyncMock(return_value=handle)
    ap.scroll_into_view = AsyncMock()
    ap._identifier_node_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0})

    await ap.select_option(_FILTER_COL_DROPDOWN, "代码", timeout_ms=1000)

    assert handle.clicks == 1, "ElementHandle 主路径必须先尝试一次"
    # 回落坐标 = bbox (500,400,200,36) 中心 (600,418)；首个 mouse 点击在展开箭角
    assert ap.page.mouse.clicks[-1] == (600.0, 418.0)
