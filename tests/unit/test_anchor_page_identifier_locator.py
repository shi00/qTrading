"""Unit tests: AnchorPage identifier 精确定位路径 (P2-2).

守护 P2-2 引入的 identifier 定位路径契约：
- 精确选择器 ``flt-semantics[flt-semantics-identifier="<EID>"]``，四类 AnchorKind
  （INTERACTIVE / INPUT / LABEL / COMPLEX）共用同一选择器，无 kind 分派 / 无启发式
  （PoC EVIDENCE.md P0-2 矩阵：identifier 节点存在性与 AnchorKind 无关）。
- INPUT 仍需下潜到后代 ``input, textarea``（P0-2 矩阵行 7-8：identifier 节点 bbox
  与真实 input bbox 不一致）。
- identifier 是唯一定位通道：不得出现任何启发式选择器（aria 后缀 / textContent
  前缀 / role 过滤），也不存在 legacy 回退路径。
- C7/C8（select_option）：菜单收合后选中值校验 + 视口外选项物理滚轮内化
  （CI run 39387686968 回归：长菜单排序靠后选项 force 点击误选相邻项 →
  下游断言 30s 超时且无明确报错方向）。

用最小 stub 替换 Playwright Page / FletPage，不依赖真实浏览器。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tests.e2e.helpers.anchor_page import AnchorPage, _display_matches_option
from ui.testing.e2e_ids import EIDS

pytestmark = pytest.mark.unit

_INTERACTIVE = EIDS.SCREENER.RUN_BUTTON  # ("e2e.screener.run_button", INTERACTIVE)
_INPUT = EIDS.DATA.FILTER_VALUE_INPUT  # ("e2e.data.filter_value_input", INPUT)
_LABEL = EIDS.NAV.MARKET  # ("e2e.nav.market", LABEL)
_COMPLEX = EIDS.SCREENER.STRATEGY_DROPDOWN  # ("e2e.screener.strategy_dropdown", COMPLEX)


def _id_selector(eid_str: str) -> str:
    return f'flt-semantics[flt-semantics-identifier="{eid_str}"]'


class _FakeLocator:
    """最小 Locator stub：记录选择器、可 wait / count / bounding_box / 下潜。"""

    def __init__(self, page: _FakePage, selector: str) -> None:
        self._page = page
        self.selector = selector
        self.first = self

    def locator(self, selector: str) -> _FakeLocator:
        self._page.descendant_selectors.append(selector)
        return _FakeLocator(self._page, selector)

    async def count(self) -> int:
        return self._page.count_value

    async def wait_for(self, state: str = "visible", timeout: int | None = None) -> None:
        self._page.wait_calls.append((self.selector, state, timeout))

    async def bounding_box(self) -> dict[str, float]:
        return self._page.box

    async def get_attribute(self, name: str) -> str | None:
        return self._page.attributes.get(name)


class _FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []
        self.moves: list[tuple[float, float]] = []
        self.wheels: list[tuple[float, float]] = []

    async def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    async def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        self.wheels.append((delta_x, delta_y))


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.typed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)

    async def type(self, value: str, delay: int | None = None) -> None:
        self.typed.append(value)


class _FakePage:
    """最小 Playwright page stub：记录所有 locator 选择器与 evaluate 调用。"""

    def __init__(self, count_value: int = 1) -> None:
        self.selectors: list[str] = []
        self.descendant_selectors: list[str] = []
        self.wait_calls: list[tuple[str, str, int | None]] = []
        self.evaluates: list[Any] = []
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.wait_for_timeout = AsyncMock()
        self.count_value = count_value
        self.viewport_size = {"width": 1400, "height": 900}
        self.box: dict[str, float] = {"x": 1.0, "y": 2.0, "width": 30.0, "height": 40.0}
        # 供 is_checked 下潜后代 checkbox 读取 aria-checked（默认未勾选）
        self.attributes: dict[str, str | None] = {"aria-checked": "false"}

    def locator(self, selector: str) -> _FakeLocator:
        self.selectors.append(selector)
        return _FakeLocator(self, selector)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluates.append((script, arg))
        return None

    async def evaluate_handle(self, script: str, arg: Any = None) -> Any:
        self.evaluates.append((script, arg))
        return None


class _FakeFletPage:
    def __init__(self) -> None:
        self._timeout_multiplier = 1.0


def _make_ap() -> tuple[AnchorPage, _FakePage]:
    page = _FakePage()
    ap = AnchorPage(
        page=page,  # type: ignore[arg-type]
        fp=_FakeFletPage(),  # type: ignore[arg-type]
        timeout_multiplier=1.0,
    )
    return ap, page


_DEFAULT_HANDLE_BOX: dict[str, float] = {"x": 500.0, "y": 400.0, "width": 200.0, "height": 36.0}


class _FakeHandle:
    """模拟下拉选项 ElementHandle：click 后菜单收合。

    ``box`` 供 C8 视口内化滚动读取 bbox：默认完全在视口内（900 高视口），
    显式传 ``None`` 模拟句柄随重渲染瞬时失效（bounding_box 返回 None）。
    """

    def __init__(self, box: dict[str, float] | None = _DEFAULT_HANDLE_BOX) -> None:
        self._visible = True
        self.box = box

    async def click(self, force: bool = False) -> None:
        self._visible = False

    async def is_visible(self) -> bool:
        return self._visible

    async def bounding_box(self) -> dict[str, float] | None:
        return self.box


def _stub_select_option(ap: AnchorPage, handle: _FakeHandle | _StubbornHandle) -> AsyncMock:
    """打桩 select_option 依赖的定位/状态方法（选项面板仍走文本匹配，见矩阵 #13-#14）。

    返回 `_find_option_element` 的 mock，供断言选项定位通道。
    C7 校验的显示值动态回显 find mock 最后一次定位的 option_text（模拟「选中项
    即目标项」的正常收合路径）；mismatch 场景由用例自行覆盖该 stub。
    """
    find_mock = AsyncMock(return_value=handle)
    ap._read_expanded_by_identifier = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    ap._find_option_element = find_mock  # type: ignore[method-assign]
    ap.scroll_into_view = AsyncMock()  # type: ignore[method-assign]
    ap._identifier_node_box = AsyncMock(  # type: ignore[method-assign]
        return_value={"x": 10.0, "y": 20.0, "width": 100.0, "height": 30.0}
    )

    async def _echo_find_target(_eid: str) -> str:
        # C7 校验发生在选项定位之后，await_args 必非 None；动态读取以兼容用例重绑定 find mock
        calls = cast(Any, ap._find_option_element).await_args
        assert calls is not None, "C7 校验前必须已完成选项定位"
        return str(calls.args[0])

    ap._read_dropdown_display_value = AsyncMock(side_effect=_echo_find_target)  # type: ignore[method-assign]
    return find_mock


# ----------------------------------------------------------------
# 四类 AnchorKind 各 ≥1 条：断言使用精确 identifier 选择器
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_interactive_uses_identifier_selector() -> None:
    """INTERACTIVE（Button）: click 走 identifier 节点自身 bbox + 精确选择器。"""
    ap, page = _make_ap()
    await ap.click(_INTERACTIVE, timeout_ms=1000)

    assert _id_selector("e2e.screener.run_button") in page.selectors
    assert page.mouse.clicks, "identifier 路径仍须走真实鼠标点击"
    # 不得回落到 legacy 的 aria-label 后缀选择器
    assert not any("aria-label$=" in s for s in page.selectors)


@pytest.mark.asyncio
async def test_fill_input_uses_identifier_selector_and_descends_to_input() -> None:
    """INPUT（TextField）: fill 下潜到 identifier 节点后代 input，取真实输入面。"""
    ap, page = _make_ap()
    await ap.fill(_INPUT, "600519", timeout_ms=1000)

    assert _id_selector("e2e.data.filter_value_input") in page.selectors
    assert "input, textarea" in page.descendant_selectors
    assert page.keyboard.typed == ["600519"]
    assert not any("aria-label$=" in s for s in page.selectors)


@pytest.mark.asyncio
async def test_expect_visible_label_uses_identifier_selector() -> None:
    """LABEL（Text）: expect_visible 走 identifier 节点的 visible 等待，不轮询 textContent。"""
    ap, page = _make_ap()
    await ap.expect_visible(_LABEL, timeout_ms=1000)

    assert _id_selector("e2e.nav.market") in page.selectors
    assert any(st == "visible" for _sel, st, _t in page.wait_calls)
    # identifier 路径不应触发 textContent 轮询 evaluate
    assert page.evaluates == []


@pytest.mark.asyncio
async def test_count_complex_uses_identifier_selector() -> None:
    """COMPLEX（Dropdown）: count 用精确选择器计数，无 role/文本启发式。"""
    ap, page = _make_ap()
    page.count_value = 1
    n = await ap.count(_COMPLEX)

    assert n == 1
    assert _id_selector("e2e.screener.strategy_dropdown") in page.selectors
    assert page.evaluates == []


# ----------------------------------------------------------------
# 边界：expect_hidden / hover / scroll_into_view / select_option 均走 identifier
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_expect_hidden_uses_identifier_hidden_state() -> None:
    ap, page = _make_ap()
    await ap.expect_hidden(_INTERACTIVE, timeout_ms=1000)

    assert _id_selector("e2e.screener.run_button") in page.selectors
    assert any(st == "hidden" for _sel, st, _t in page.wait_calls)


@pytest.mark.asyncio
async def test_hover_uses_identifier_selector() -> None:
    ap, page = _make_ap()
    await ap.hover(_INTERACTIVE, timeout_ms=1000)

    assert _id_selector("e2e.screener.run_button") in page.selectors
    assert page.mouse.moves, "hover 须走真实鼠标移动"


@pytest.mark.asyncio
async def test_scroll_into_view_uses_mouse_wheel_when_outside_viewport() -> None:
    """CanvasKit 实证：视口外控件 scroll_into_view 须通过物理鼠标滚轮 wheel 滚动，
    避免 DOM scrollIntoView 导致 Canvas 画面未滚动、造成错位误触。"""
    ap, page = _make_ap()
    page.box = {"x": 500.0, "y": 950.0, "width": 100.0, "height": 40.0}
    await ap.scroll_into_view(_COMPLEX, timeout_ms=1000)

    assert _id_selector(_COMPLEX[0]) in page.selectors
    assert page.mouse.wheels, "视口外控件 scroll_into_view 须通过物理鼠标滚轮 wheel 滚动"
    assert page.mouse.wheels[0][1] > 0, "位于视口下方的控件须向下滚动滚轮"


@pytest.mark.asyncio
async def test_scroll_into_view_noop_when_already_in_viewport() -> None:
    """已处于安全视口内部的控件不产生多余物理滚轮操作。"""
    ap, page = _make_ap()
    page.box = {"x": 500.0, "y": 400.0, "width": 100.0, "height": 40.0}
    await ap.scroll_into_view(_COMPLEX, timeout_ms=1000)

    assert _id_selector(_COMPLEX[0]) in page.selectors
    assert page.mouse.wheels == [], "已在安全视口内的控件不产生多余滚轮操作"


@pytest.mark.asyncio
async def test_scroll_into_view_label_still_rejected() -> None:
    """LABEL 不可滚动：identifier 路径仍显式拒绝（保持 legacy 语义）。"""
    ap, _page = _make_ap()
    with pytest.raises(RuntimeError, match="display-only"):
        await ap.scroll_into_view(_LABEL, timeout_ms=1000)


# ----------------------------------------------------------------
# P2-3: 逐行覆盖 P0-2 矩阵的控件族定位策略（矩阵 #1-#21）
# ----------------------------------------------------------------
#
# 定位层为纯分派逻辑，单测守护「每族 → 何种 bbox 解析策略」；具体 DOM 形态
# （节点存在性 / bbox 数值）由 P0-2 PoC 证据与 P2-4 E2E Gate 守护。


_BUTTON_FAMILY = [
    EIDS.SCREENER.RUN_BUTTON,  # 矩阵 #1-#2：ft.Button
    EIDS.SCREENER.EXPORT_CSV_BUTTON,  # 矩阵 #3-#4：ft.TextButton
    EIDS.SCREENER.EXPORT_EXCEL_BUTTON,  # 矩阵 #5-#6：ft.IconButton
]


@pytest.mark.asyncio
@pytest.mark.parametrize("eid", _BUTTON_FAMILY, ids=lambda e: e[0])
async def test_button_family_uses_node_self_bbox(eid: tuple[str, Any]) -> None:
    """矩阵 #1-#6（Button 系列）：identifier 节点 bbox 与真实可点击节点一致，
    故取节点自身 bbox，不得下潜后代 `flt-tappable`。"""
    ap, page = _make_ap()
    await ap.click(eid, timeout_ms=1000)

    assert _id_selector(eid[0]) in page.selectors
    assert page.descendant_selectors == [], "Button 族不得下潜后代（P0-2：bbox 一致）"
    assert page.mouse.clicks


@pytest.mark.asyncio
async def test_textfield_descends_to_input_bbox() -> None:
    """矩阵 #7-#8（TextField）：identifier 节点 bbox 与 input bbox 不一致 → 取后代 input。"""
    ap, page = _make_ap()
    await ap.click(_INPUT, timeout_ms=1000)

    assert _id_selector(_INPUT[0]) in page.selectors
    assert "input, textarea" in page.descendant_selectors


@pytest.mark.asyncio
@pytest.mark.parametrize("eid", [_LABEL, EIDS.NAV.SCREENER], ids=lambda e: e[0])
async def test_label_family_uses_node_self_bbox(eid: tuple[str, Any]) -> None:
    """矩阵 #9-#10（Text/LABEL）：节点自身即交互面，点击位置取节点 bbox，不下潜。"""
    ap, page = _make_ap()
    await ap.click_label(eid, timeout_ms=1000)

    assert _id_selector(eid[0]) in page.selectors
    assert page.descendant_selectors == []
    assert page.mouse.clicks


@pytest.mark.asyncio
async def test_dropdown_top_reads_aria_expanded_by_identifier() -> None:
    """矩阵 #11-#12（Dropdown 顶层）：identifier 节点带 role=button + aria-expanded，
    展开态经 identifier 属性读取，不复用 legacy 的文本前缀匹配。"""
    ap, page = _make_ap()
    await ap._read_expanded_by_identifier("e2e.screener.strategy_dropdown")

    assert page.evaluates, "identifier 展开态读取须执行 JS"
    script, arg = page.evaluates[0]
    assert "flt-semantics-identifier" in script
    assert arg == {"label": "e2e.screener.strategy_dropdown"}
    # 未打桩时不得走 legacy 文本前缀 evaluate
    assert not any("aria-label" in s for s, _a in page.evaluates)


@pytest.mark.asyncio
async def test_dropdown_option_not_located_by_identifier() -> None:
    """矩阵 #13-#14（Dropdown 选项面板）：选项由 Flet 动态生成、无 anchor 覆盖，
    仍走文本匹配 `_find_option_element`，identifier 路径不得对其下潜定位。"""
    ap, page = _make_ap()
    handle = _FakeHandle()
    find_mock = _stub_select_option(ap, handle)

    await ap.select_option(_COMPLEX, "代码", timeout_ms=1000)

    assert page.selectors == [], "选项面板不应经 identifier 选择器定位"
    find_mock.assert_awaited()
    assert not handle._visible


@pytest.mark.asyncio
async def test_listview_row_complex_uses_node_self_bbox() -> None:
    """矩阵 #15-#17（ListView 行 / GestureDetector）：COMPLEX 取节点自身 bbox，
    不下潜；视口外行由 scroll_into_view 先滚入构建窗口。"""
    eid = EIDS.SCREENER.column_header("pct_chg")
    ap, page = _make_ap()
    await ap.click(eid, timeout_ms=1000)

    assert _id_selector(eid[0]) in page.selectors
    assert page.descendant_selectors == []
    assert page.mouse.clicks


@pytest.mark.asyncio
async def test_dialog_interactive_node_uses_node_self_bbox() -> None:
    """矩阵 #18（Dialog 内 ft.TextButton）：与 Button 族同策略（INTERACTIVE → 节点自身）。"""
    ap, page = _make_ap()
    await ap.click(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, timeout_ms=1000)

    assert _id_selector("e2e.detail_dialog.close_button") in page.selectors
    assert page.descendant_selectors == []


@pytest.mark.asyncio
async def test_dialog_label_node_uses_node_self_bbox() -> None:
    """矩阵 #19（Dialog 内 ft.Text）：LABEL 节点自身即交互面。"""
    ap, page = _make_ap()
    await ap.click_label(EIDS.NEWS_RISK.RISK_LEVEL, timeout_ms=1000)

    assert _id_selector("e2e.news_risk.risk_level") in page.selectors
    assert page.descendant_selectors == []


# --- 矩阵 #20-#21：offstage（不存在于语义树） ---


@pytest.mark.asyncio
async def test_offstage_zero_bbox_raises_runtime_error() -> None:
    """矩阵 #20-#21：offstage 节点被排除出语义树 → identifier 节点 bbox 为 0
    （locator 命中 0 个时 bounding_box 返回 None）→ 显式抛错，不静默返回零坐标。"""
    ap, page = _make_ap()
    page.box = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}

    with pytest.raises(RuntimeError, match="no valid bbox"):
        await ap.click(_INTERACTIVE, timeout_ms=1000)


@pytest.mark.asyncio
async def test_offstage_count_is_zero() -> None:
    """矩阵 #20-#21：offstage 节点 count=0，精确选择器如实反映。"""
    ap, page = _make_ap()
    page.count_value = 0

    assert await ap.count(_COMPLEX) == 0
    assert _id_selector("e2e.screener.strategy_dropdown") in page.selectors


@pytest.mark.asyncio
async def test_offstage_expect_hidden_passes() -> None:
    """矩阵 #20-#21：offstage 节点 expect_hidden 走 identifier `state=hidden` 等待并通过。"""
    ap, page = _make_ap()
    await ap.expect_hidden(_COMPLEX, timeout_ms=1000)

    assert any(st == "hidden" for _sel, st, _t in page.wait_calls)


# ----------------------------------------------------------------
# Checkbox 勾选态读取（幂等「确保已勾选」读状态入口）
# ----------------------------------------------------------------
#
# Flet 1.0.2 CanvasKit 实测（reviews/poc/flet-1.0.2-identifier 同源探针）：
# ``anchored()`` 的 ``Semantics(container=True)`` 包装节点自身为 ``role="group"``，
# **无** ``aria-checked``；真实勾选态落在后代 ``flt-semantics[role="checkbox"]``
# 的 ``aria-checked``（"true"/"false"），并随真实鼠标点击翻转。

_CHECKBOX = EIDS.WIZARD.RISK_ACK  # ("e2e.wizard.risk_ack", COMPLEX)


@pytest.mark.asyncio
async def test_is_checked_descends_to_descendant_checkbox_aria_checked() -> None:
    """勾选态经 identifier 包装节点**后代** checkbox 的 aria-checked 读取，
    不得读包装节点自身属性，也不走 JS evaluate。"""
    ap, page = _make_ap()
    page.attributes["aria-checked"] = "true"

    assert await ap.is_checked(_CHECKBOX, timeout_ms=1000) is True
    assert _id_selector("e2e.wizard.risk_ack") in page.selectors
    assert 'flt-semantics[role="checkbox"]' in page.descendant_selectors
    assert page.evaluates == [], "勾选态读取不应走 JS 轮询"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", None], ids=["false", "absent"])
async def test_is_checked_false_when_not_checked(value: str | None) -> None:
    """aria-checked 为 "false" 或属性缺失时均判为未勾选（幂等置位据此决定是否点击）。"""
    ap, page = _make_ap()
    page.attributes["aria-checked"] = value

    assert await ap.is_checked(_CHECKBOX, timeout_ms=1000) is False


# ----------------------------------------------------------------
# C7/C8: select_option 选中值校验 + 视口内化滚动
# （CI run 39387686968：长菜单视口外选项 force 点击误选相邻项 macro_economy
#   → 空状态污染下游断言 '000001.SZ' 30s 超时且无明确报错方向）
# ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("display", "option_text", "expected"),
    [
        ("stock_basic", "stock_basic", True),  # prio 1 精确
        ("index_daily\n备注", "index_daily", True),  # prio 2 前缀换行
        ("index_daily (日均线)", "index_daily", True),  # prio 3 别名
        ("ts_code (代码)", "代码", True),  # prio 3：FILTER_COL 合法路径（传别名 key）
        ("index_dailybaseline", "index_daily", False),  # prio 4 裸子串拒绝
        ("macro_economy (宏观经济数据)", "stock_basic", False),  # 误选场景
        ("", "stock_basic", False),  # 空显示值
        (None, "stock_basic", False),  # 无显示值
        ("stock_basic", "", False),  # 空选项文本
    ],
    ids=[
        "exact",
        "prefix-newline",
        "alias",
        "alias-filter-col",
        "bare-substring-rejected",
        "wrong-option",
        "empty-display",
        "none-display",
        "empty-option",
    ],
)
def test_display_matches_option(display: str | None, option_text: str, expected: bool) -> None:
    """C7 判据：接受 matchPrio 1/2/3 形态、拒绝裸子串（index_daily ≠ index_dailybaseline）。"""
    assert _display_matches_option(display, option_text) is expected


@pytest.mark.asyncio
async def test_select_option_value_mismatch_raises() -> None:
    """C7：菜单正常收合但显示值非目标项（误选相邻选项）→ 显式抛 mismatch，不静默污染下游。"""
    ap, _page = _make_ap()
    handle = _FakeHandle()
    _stub_select_option(ap, handle)
    ap._read_dropdown_display_value = AsyncMock(  # type: ignore[method-assign]
        return_value="macro_economy (宏观经济数据)"
    )

    with pytest.raises(RuntimeError, match="selected value mismatch"):
        await ap.select_option(_COMPLEX, "stock_basic", timeout_ms=1000)


@pytest.mark.asyncio
async def test_select_option_confirms_display_value_matches_option_text() -> None:
    """C7：收合后显示值与目标选项匹配（动态回显 find 定位的 option_text）→ 正常通过。"""
    ap, _page = _make_ap()
    handle = _FakeHandle()
    _stub_select_option(ap, handle)

    await ap.select_option(_COMPLEX, "代码", timeout_ms=1000)

    ap._read_dropdown_display_value.assert_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_read_dropdown_display_value_returns_none_without_dom() -> None:
    """C7 数据源：真身经 evaluate 读 identifier 节点，DOM 无节点/非字符串返回 None。"""
    ap, page = _make_ap()

    assert await ap._read_dropdown_display_value("e2e.screener.strategy_dropdown") is None
    script, arg = page.evaluates[0]
    assert "flt-semantics-identifier" in script
    # 前缀剥离必须以完整 EID 为锚（EID 自身含 "."，按首个 "." 切分会把 EID 主体误剥）
    assert "startsWith(args.label" in script
    assert arg == {"label": "e2e.screener.strategy_dropdown"}


@pytest.mark.asyncio
async def test_select_option_scroll_noop_when_option_already_in_viewport() -> None:
    """C8：选项 bbox 已完全在视口内 → 不产生物理滚轮（不干扰菜单滚动状态）。"""
    ap, page = _make_ap()
    handle = _FakeHandle()
    _stub_select_option(ap, handle)

    await ap.select_option(_COMPLEX, "代码", timeout_ms=1000)

    assert page.mouse.wheels == [], "视口内选项不得触发多余滚轮操作"


@pytest.mark.asyncio
async def test_select_option_scrolls_out_of_view_option_into_viewport() -> None:
    """C8：选项在视口外（长菜单排序靠后）→ 物理滚轮向下滚动 → 重定位视口内句柄再点击。"""
    ap, page = _make_ap()
    outside = _FakeHandle(box={"x": 100.0, "y": 950.0, "width": 200.0, "height": 36.0})
    inside = _FakeHandle()
    _stub_select_option(ap, outside)
    ap._find_option_element = AsyncMock(side_effect=[outside, inside])  # type: ignore[method-assign]

    await ap.select_option(_COMPLEX, "stock_basic", timeout_ms=1000)

    assert page.mouse.wheels, "视口外选项须经物理滚轮滚动"
    assert page.mouse.wheels[0][1] > 0, "位于视口下方的选项须向下滚动"
    assert not inside._visible, "滚动入视口后应点击重定位的句柄"


@pytest.mark.asyncio
async def test_select_option_scroll_falls_back_to_dropdown_anchor_without_bbox() -> None:
    """C8：选项句柄瞬时无 bbox（重渲染失效）→ 以 Dropdown 框下方为承载面兜底滚动。"""
    ap, page = _make_ap()
    bad = _FakeHandle(box=None)
    good = _FakeHandle()
    _stub_select_option(ap, bad)
    ap._find_option_element = AsyncMock(side_effect=[bad, good])  # type: ignore[method-assign]

    await ap.select_option(_COMPLEX, "stock_basic", timeout_ms=1000)

    assert page.mouse.wheels, "无 bbox 选项须走 Dropdown 承载面兜底滚动"
    assert page.mouse.wheels[0][1] > 0
    assert not good._visible


@pytest.mark.asyncio
async def test_select_option_raises_when_option_never_within_viewport() -> None:
    """C8：滚动耗尽仍无法入视口 → 显式抛错，不静默点击视口外坐标。"""
    ap, _page = _make_ap()
    handle = _FakeHandle(box={"x": 100.0, "y": 950.0, "width": 200.0, "height": 36.0})
    _stub_select_option(ap, handle)

    with pytest.raises(RuntimeError, match="never within viewport"):
        await ap.select_option(_COMPLEX, "stock_basic", timeout_ms=1000)


class _StubbornHandle:
    """模拟点击被吞：前 stubborn_reads 次 is_visible 仍 True（菜单不收合），此后 False。"""

    def __init__(self, stubborn_reads: int) -> None:
        self._remaining = stubborn_reads

    async def click(self, force: bool = False) -> None:
        # 点击后最多再顽固 1 次（重试 attempt 内首查仍可见 → 下一次收合）
        self._remaining = min(self._remaining, 1)

    async def is_visible(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True

    async def bounding_box(self) -> dict[str, float] | None:
        return _DEFAULT_HANDLE_BOX


@pytest.mark.asyncio
async def test_select_option_retries_click_when_menu_not_collapsed() -> None:
    """收合确认（坑点 6）：主点击被吞菜单不收合 → 空等耗尽走 retry_until_triggered
    重试点击，确认收合后再过 C7 校验（while-else 分支覆盖）。"""
    ap, _page = _make_ap()
    handle = _StubbornHandle(stubborn_reads=30)  # > 空等窗口 2s / 100ms 轮询数，确保走 else 分支
    _stub_select_option(ap, handle)

    await ap.select_option(_COMPLEX, "代码", timeout_ms=1000)

    assert not await handle.is_visible(), "重试点击后菜单应收合"


@pytest.mark.asyncio
async def test_select_option_skips_scroll_without_viewport() -> None:
    """C8：无 viewport_size（极早期页面）时跳过滚动内化，不误伤既有点击路径。"""
    ap, page = _make_ap()
    handle = _FakeHandle()
    _stub_select_option(ap, handle)
    page.viewport_size = None  # type: ignore[assignment]

    await ap.select_option(_COMPLEX, "代码", timeout_ms=1000)

    assert page.mouse.wheels == [], "无 viewport 时不得产生滚轮操作"
