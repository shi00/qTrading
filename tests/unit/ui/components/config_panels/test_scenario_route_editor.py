"""ScenarioRouteEditor 组件测试（S5 §11.2 / §11.4）。

组件为声明式组件（消费外部 ``ModelRoutesViewModel``），本文件聚焦运行时行为：

1. 三场景导航渲染与切换（click → ``vm.select_scenario``）
2. 候选行：序号 / 首选-备用 / 资格（能力）文本
3. 上移 / 下移首末禁用（基于 ``vm.can_move_up`` / ``can_move_down``）
4. 可搜索选择器：已选项禁用重复、不兼容项仍显示；确认追加末尾并关闭
5. 撤销可用性（``undo_available``）与高级参数折叠（``advanced_expanded``）
6. 情绪分区仅在 ``news_annotation`` 出现
7. 场景检查区授权摘要（§4.3）：本地模型提示不外发 / 未授权显示授权按钮 / 已授权显示撤销

组件签名（契约）见 ``ui/components/config_panels/scenario_route_editor.py``；VM 契约由
``tests/unit/ui/viewmodels`` 覆盖，本文件用 ``_FakeRoutesVM`` 注入确定性 state + 命令 spy。
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import FakePage, make_component, render_once, run_mount_effects
from ui.components.config_panels import scenario_route_editor as editor_module
from ui.components.config_panels.scenario_route_editor import ScenarioRouteEditor
from ui.viewmodels.model_routes_view_model import (
    ConnectionOption,
    ModelRoutesState,
    ScenarioRouteDraft,
)

pytestmark = pytest.mark.unit

_SCENARIO_IDS = ("news_annotation", "stock_analysis", "news_risk")


# ============================================================================
# 测试基础设施：_FakeRoutesVM + _make_state + 渲染/遍历 helper
# ============================================================================


class _FakeRoutesVM:
    """模拟 ModelRoutesViewModel 的状态切片。

    满足 ``use_viewmodel(vm=)`` 外部 VM 模式契约（state / subscribe / dispose）；
    命令为 MagicMock 便于断言；``can_move_up`` / ``can_move_down`` 按候选顺序实现
    （与真 VM 同算法），供首末禁用断言使用。其余 VM 能力不进入本组件不模拟。
    """

    def __init__(self, state: ModelRoutesState) -> None:
        self._state = state
        self._subscribers: list[Any] = []
        self.select_scenario = MagicMock()
        self.open_candidate_picker = MagicMock()
        self.close_candidate_picker = MagicMock()
        self.add_candidate = MagicMock()
        self.remove_candidate = MagicMock()
        self.move_up = MagicMock()
        self.move_down = MagicMock()
        self.undo_last_move = MagicMock()
        self.toggle_advanced = MagicMock()
        # F13 草稿字段命令 + 一键激活（激活区渲染时引用，缺属性会 AttributeError）
        self.set_route_enabled = MagicMock()
        self.set_sentiment_mode = MagicMock()
        self.activate_fresh_setup = AsyncMock(return_value=True)

    @property
    def state(self) -> ModelRoutesState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()

    def _draft(self) -> ScenarioRouteDraft | None:
        return next(
            (r for r in self._state.routes if r.scenario_id == self._state.selected_scenario),
            None,
        )

    def can_move_up(self, connection_id: str) -> bool:
        draft = self._draft()
        if draft is None or connection_id not in draft.candidates:
            return False
        return draft.candidates.index(connection_id) > 0

    def can_move_down(self, connection_id: str) -> bool:
        draft = self._draft()
        if draft is None or connection_id not in draft.candidates:
            return False
        return draft.candidates.index(connection_id) < len(draft.candidates) - 1


class _FakeEgressVM:
    """模拟 ModelConnectionsViewModel 的授权摘要切片（外部 VM 模式契约）。

    满足 ``use_viewmodel(vm=)`` 契约（state / subscribe / dispose）；授权命令为
    MagicMock 便于断言实参；``connection_target`` / ``grants_for`` /
    ``connection_requires_egress`` 由构造参数注入，模拟本地适配器（无外发目标 →
    None，R21 不臆造目标）与 HTTP 目标（规范化字符串 → 有 / 无 grant）。未显式
    注入 ``requires_egress`` 的连接默认 False（本地语义，与既有用例一致）；真实
    VM 对未知连接 / 未知 kind 为 fail-closed True（R07/T2），相关用例显式注入。
    授权写副作用不进入本组件，故其余 VM 能力不模拟。
    """

    def __init__(
        self,
        *,
        targets: dict[str, str | None] | None = None,
        grants: dict[tuple[str, str], tuple[Mapping[str, Any], ...]] | None = None,
        requires_egress: dict[str, bool] | None = None,
    ) -> None:
        self._state = object()  # 授权摘要不消费 egress state 字段，仅需非 None 快照
        self._subscribers: list[Any] = []
        self._targets = dict(targets or {})
        self._grants = dict(grants or {})
        self._requires_egress = dict(requires_egress or {})
        self.grant_egress = MagicMock()
        self.revoke_egress = MagicMock()

    @property
    def state(self) -> Any:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()

    def connection_target(self, connection_id: str) -> str | None:
        return self._targets.get(connection_id)

    def connection_requires_egress(self, connection_id: str) -> bool:
        return self._requires_egress.get(connection_id, False)

    def grants_for(self, connection_id: str, purpose: str) -> tuple[Mapping[str, Any], ...]:
        return self._grants.get((connection_id, purpose), ())


def _make_state(**overrides: Any) -> ModelRoutesState:
    """构造确定性 ModelRoutesState（news_annotation 三候选；conn_b 不兼容情绪）。"""
    news = ScenarioRouteDraft(
        scenario_id="news_annotation",
        enabled=True,
        candidates=("conn_a", "conn_b", "conn_c"),
        total_timeout_seconds=45,
        transient_retries=1,
        fallback_enabled=True,
        sentiment_mode="bundled",
        reuse_bundled_sentiment=True,
    )
    stock = ScenarioRouteDraft(scenario_id="stock_analysis", candidates=("conn_a",), total_timeout_seconds=180)
    risk = ScenarioRouteDraft(scenario_id="news_risk", candidates=(), total_timeout_seconds=90)
    options = (
        ConnectionOption(
            connection_id="conn_a",
            name="Conn A",
            billing_mode="usage_only",
            billing_unit="llm_tokens",
            capabilities=(("sentiment", "supported"), ("analysis", "supported"), ("risk", "supported")),
        ),
        ConnectionOption(
            connection_id="conn_b",
            name="Conn B",
            billing_mode="estimate_cost",
            billing_unit="encoder_input_tokens",
            capabilities=(("sentiment", "unsupported"),),
        ),
        ConnectionOption(connection_id="conn_c", name="Conn C", capabilities=()),
    )
    defaults: dict[str, Any] = {
        "routes": (news, stock, risk),
        "committed_routes": (news, stock, risk),
        "selected_scenario": "news_annotation",
        "candidate_picker_open": False,
        "available_connections": options,
        "dirty_counts": (("news_annotation", 2), ("stock_analysis", 0), ("news_risk", 0)),
        "undo_available": False,
        "advanced_expanded": False,
    }
    defaults.update(overrides)
    return ModelRoutesState(**defaults)


def _picker_state() -> ModelRoutesState:
    """选择器测试用 state：news 仅引用 conn_a（conn_b / conn_c 尚未选择）。"""
    news = ScenarioRouteDraft(scenario_id="news_annotation", candidates=("conn_a",))
    stock = ScenarioRouteDraft(scenario_id="stock_analysis", candidates=())
    risk = ScenarioRouteDraft(scenario_id="news_risk", candidates=())
    return _make_state(
        routes=(news, stock, risk),
        committed_routes=(news, stock, risk),
        candidate_picker_open=True,
    )


def _render(vm: _FakeRoutesVM, egress_vm: _FakeEgressVM | None = None) -> tuple[Any, Any, MagicMock]:
    """渲染组件，返回 (控件树根, component, I18n mock)；``I18n.get`` 打桩为返回 key 本身。

    ``page.run_task`` 同步执行被调度命令，使授权 / 撤销按钮点击可断言实参。
    """
    page = FakePage()
    page.run_task = lambda fn, *a, **kw: fn(*a, **kw)  # type: ignore[method-assign]
    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(editor_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: key
        component = make_component(ScenarioRouteEditor, vm=vm, egress_vm=egress_vm or _FakeEgressVM())
        run_mount_effects(component, page=page)
        root = render_once(component)
    return root, component, mock_i18n


def _walk(root: Any) -> list[Any]:
    """递归展平渲染树（Component 递归 render_once，遍历 controls/items/actions/content）。"""
    from flet.components.component import Component

    if root is None:
        return []
    if isinstance(root, Component):
        return _walk(render_once(root))
    if not isinstance(root, ft.Control):
        return []
    out: list[Any] = [root]
    for attr in ("controls", "items", "actions"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                out.extend(_walk(child))
    content = getattr(root, "content", None)
    if isinstance(content, (ft.Control, Component)):
        out.extend(_walk(content))
    return out


def _find(root: Any, key: str) -> Any:
    for ctrl in _walk(root):
        if getattr(ctrl, "key", None) == key:
            return ctrl
    raise AssertionError(f"控件 key={key} 未找到")


def _maybe_find(root: Any, key: str) -> Any | None:
    for ctrl in _walk(root):
        if getattr(ctrl, "key", None) == key:
            return ctrl
    return None


def _make_event() -> MagicMock:
    return MagicMock()


# ============================================================================
# 三场景导航
# ============================================================================


class TestScenarioNavigation:
    """三场景导航渲染与切换。"""

    def test_renders_three_scenario_tabs(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        for sid in _SCENARIO_IDS:
            assert isinstance(_find(root, f"scenario_tab_{sid}"), ft.Button)

    def test_click_tab_calls_select_scenario(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        tab = _find(root, "scenario_tab_news_risk")
        tab.on_click(_make_event())
        vm.select_scenario.assert_called_once_with("news_risk")

    def test_dirty_badge_only_on_dirty_scenario(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        assert _find(root, "scenario_dirty_news_annotation").value == "model_routes_dirty_badge"
        assert _maybe_find(root, "scenario_dirty_stock_analysis") is None


# ============================================================================
# 候选行：序号 / 首选-备用 / 资格
# ============================================================================


class TestCandidateRow:
    """候选行展示内容。"""

    def test_row_shows_index_role_and_eligibility(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        assert _find(root, "index_conn_a").value == "1."
        assert _find(root, "index_conn_b").value == "2."
        assert _find(root, "role_conn_a").value == "model_routes_preferred"
        assert _find(root, "role_conn_b").value == "model_routes_backup"
        assert _find(root, "capability_conn_a").value == "model_routes_cap_supported"
        assert _find(root, "capability_conn_b").value == "model_routes_cap_unsupported"
        assert _find(root, "capability_conn_c").value == "model_routes_cap_unknown"

    def test_expected_first_try_note_when_first_not_eligible(self, mock_i18n_state, mock_app_colors_state) -> None:
        """首行资格不满足（conn_b unsupported）→ 另标「预计首先尝试」，但不改序号。"""
        news = ScenarioRouteDraft(
            scenario_id="news_annotation",
            candidates=("conn_b", "conn_a"),
        )
        stock = ScenarioRouteDraft(scenario_id="stock_analysis", candidates=())
        risk = ScenarioRouteDraft(scenario_id="news_risk", candidates=())
        vm = _FakeRoutesVM(_make_state(routes=(news, stock, risk), committed_routes=(news, stock, risk)))
        root, _, _ = _render(vm)
        assert _find(root, "expected_first_try_conn_b").value == "model_routes_expected_first_try"
        assert _find(root, "index_conn_b").value == "1."
        assert _maybe_find(root, "expected_first_try_conn_a") is None

    def test_empty_candidates_shown(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(selected_scenario="news_risk"))
        root, _, _ = _render(vm)
        assert _find(root, "empty_candidates").value == "model_routes_empty_candidates"


# ============================================================================
# 上移 / 下移首末禁用
# ============================================================================


class TestReorderArrows:
    """上移 / 下移按钮禁用态与 can_move_* 一致。"""

    def test_first_row_up_disabled_last_row_down_disabled(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        assert _find(root, "move_up_conn_a").disabled is True
        assert _find(root, "move_down_conn_a").disabled is False
        assert _find(root, "move_down_conn_c").disabled is True
        assert _find(root, "move_up_conn_c").disabled is False

    def test_arrow_disabled_matches_can_move(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        for cid in ("conn_a", "conn_b", "conn_c"):
            assert _find(root, f"move_up_{cid}").disabled == (not vm.can_move_up(cid))
            assert _find(root, f"move_down_{cid}").disabled == (not vm.can_move_down(cid))

    def test_click_arrows_calls_commands(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        _find(root, "move_up_conn_b").on_click(_make_event())
        vm.move_up.assert_called_once_with("conn_b")
        _find(root, "move_down_conn_b").on_click(_make_event())
        vm.move_down.assert_called_once_with("conn_b")
        _find(root, "remove_conn_b").on_click(_make_event())
        vm.remove_candidate.assert_called_once_with("conn_b")


# ============================================================================
# 可搜索选择器
# ============================================================================


class TestCandidatePicker:
    """选择器：已选禁用、不兼容仍显示、确认追加末尾并关闭。"""

    def test_add_button_opens_picker(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        _find(root, "add_candidate").on_click(_make_event())
        vm.open_candidate_picker.assert_called_once_with()

    def test_picker_hidden_by_default(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        assert _maybe_find(root, "candidate_picker") is None

    def test_selected_option_disabled_and_incompatible_shown(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_picker_state())
        root, _, _ = _render(vm)
        picker = _find(root, "candidate_picker")
        assert isinstance(picker, ft.Container)
        # 已选项（conn_a 已在候选）禁用重复添加。
        assert _find(root, "picker_option_conn_a").on_click is None
        assert _find(root, "picker_added_conn_a").value == "model_routes_picker_already_added"
        # 不兼容项（conn_b 情绪 unsupported）不悄悄隐藏，且仍可选。
        incompatible = _find(root, "picker_option_conn_b")
        assert callable(incompatible.on_click)
        assert _find(root, "picker_incompatible_conn_b").value == "model_routes_picker_incompatible"
        # 所有连接都在列表中（含无能力 conn_c）。
        assert callable(_find(root, "picker_option_conn_c").on_click)

    def test_confirm_option_appends_and_closes(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_picker_state())
        root, _, _ = _render(vm)
        _find(root, "picker_option_conn_c").on_click(_make_event())
        vm.add_candidate.assert_called_once_with("conn_c")
        vm.close_candidate_picker.assert_called_once_with()

    def test_close_button_calls_close_picker(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_picker_state())
        root, _, _ = _render(vm)
        _find(root, "picker_close").on_click(_make_event())
        vm.close_candidate_picker.assert_called_once_with()


# ============================================================================
# 撤销 + 高级参数折叠
# ============================================================================


class TestUndoAndAdvanced:
    """撤销可用性与高级参数折叠。"""

    def test_undo_disabled_when_unavailable(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(undo_available=False))
        root, _, _ = _render(vm)
        assert _find(root, "undo_button").disabled is True

    def test_undo_enabled_when_available(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(undo_available=True))
        root, _, _ = _render(vm)
        assert _find(root, "undo_button").disabled is False

    def test_undo_click_calls_command(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(undo_available=True))
        root, _, _ = _render(vm)
        _find(root, "undo_button").on_click(_make_event())
        vm.undo_last_move.assert_called_once_with()

    def test_advanced_section_hidden_by_default(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(advanced_expanded=False))
        root, _, _ = _render(vm)
        assert _find(root, "advanced_section").visible is False

    def test_advanced_section_visible_when_expanded(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(advanced_expanded=True))
        root, _, _ = _render(vm)
        assert _find(root, "advanced_section").visible is True

    def test_advanced_toggle_calls_command(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        _find(root, "advanced_toggle").on_click(_make_event())
        vm.toggle_advanced.assert_called_once_with()


# ============================================================================
# 情绪分区
# ============================================================================


class TestSentimentSection:
    """新闻场景独立情绪分区。"""

    def test_section_shown_for_news_annotation(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(selected_scenario="news_annotation"))
        root, _, _ = _render(vm)
        section = _find(root, "sentiment_section")
        assert isinstance(section, ft.Container)
        assert _find(root, "sentiment_mode").value == "model_routes_sentiment_bundled"
        # 引用候选：news_annotation 三候选逐一列出。
        for cid in ("conn_a", "conn_b", "conn_c"):
            assert _maybe_find(root, f"sentiment_ref_{cid}") is not None

    def test_section_absent_for_non_news_scenario(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(selected_scenario="stock_analysis"))
        root, _, _ = _render(vm)
        assert _maybe_find(root, "sentiment_section") is None


# ============================================================================
# 场景检查区授权摘要（§4.3）
# ============================================================================


_EGRESS_KEY_PREFIXES = ("egress_local_", "egress_invalid_", "egress_target_", "egress_grant_", "egress_revoke_")


def _egress_keys(root: Any) -> list[str]:
    return [
        str(ctrl.key) for ctrl in _walk(root) if str(getattr(ctrl, "key", "") or "").startswith(_EGRESS_KEY_PREFIXES)
    ]


def _i18n_kwargs(mock_i18n: MagicMock, key: str) -> dict[str, Any]:
    for call in mock_i18n.get.call_args_list:
        if call.args and call.args[0] == key:
            return dict(call.kwargs)
    raise AssertionError(f"I18n.get 未以 key={key} 调用")


class TestEgressSummary:
    """授权摘要：本地模型提示不外发 / 未授权显示授权 / 已授权显示撤销（§4.3）。"""

    def test_summary_always_rendered(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state())
        root, _, _ = _render(vm)
        assert isinstance(_find(root, "egress_summary"), ft.Container)

    def test_local_target_renders_no_egress_action(self, mock_i18n_state, mock_app_colors_state) -> None:
        """本地适配器（无外发目标）只提示「不外发」，不渲染授权 / 撤销（R21 不臆造目标）。"""
        vm = _FakeRoutesVM(_make_state())
        egress_vm = _FakeEgressVM(
            targets={"conn_a": "https://a.example:443", "conn_b": None, "conn_c": None},
            requires_egress={"conn_b": False, "conn_c": False},
        )
        root, _, _ = _render(vm, egress_vm)
        assert _find(root, "egress_local_conn_b").value == "model_routes_egress_local"
        assert _find(root, "egress_local_conn_c").value == "model_routes_egress_local"
        assert _maybe_find(root, "egress_target_conn_b") is None
        assert _maybe_find(root, "egress_grant_conn_b") is None
        assert _maybe_find(root, "egress_revoke_conn_b") is None
        # HTTP 目标连接显示规范化目标，而非「不外发」。
        assert _find(root, "egress_target_conn_a").value == "model_routes_egress_target"

    def test_invalid_target_on_egress_kind_renders_invalid_not_local(
        self, mock_i18n_state, mock_app_colors_state
    ) -> None:
        """R07/T2：egress kind 空目标 = 目标非法，显式标注而非伪装为本地（R21）。

        egress kind（api 等，fail-closed 含未知 kind）目标非法时不渲染「不外发」
        文案、不渲染任何授权操作（无目标可授权，发 grant 即伪造授权身份）。
        """
        vm = _FakeRoutesVM(_make_state())
        egress_vm = _FakeEgressVM(
            targets={"conn_a": "https://a.example:443", "conn_b": None, "conn_c": None},
            requires_egress={"conn_b": True, "conn_c": True},
        )
        root, _, _ = _render(vm, egress_vm)
        assert _find(root, "egress_invalid_conn_b").value == "model_routes_egress_target_invalid"
        assert _find(root, "egress_invalid_conn_c").value == "model_routes_egress_target_invalid"
        # 不得伪装为本地
        assert _maybe_find(root, "egress_local_conn_b") is None
        assert _maybe_find(root, "egress_local_conn_c") is None
        # 无有效目标即无可授权对象，不渲染授权 / 撤销
        for cid in ("conn_b", "conn_c"):
            assert _maybe_find(root, f"egress_target_{cid}") is None
            assert _maybe_find(root, f"egress_grant_{cid}") is None
            assert _maybe_find(root, f"egress_revoke_{cid}") is None
        # HTTP 目标连接不受影响
        assert _find(root, "egress_target_conn_a").value == "model_routes_egress_target"

    def test_ungranted_target_renders_grant_and_dispatches(self, mock_i18n_state, mock_app_colors_state) -> None:
        """未授权 HTTP 目标 → 显示授权按钮；点击提交 grant（连接 id / 目标 / 用途）。"""
        vm = _FakeRoutesVM(_make_state())
        egress_vm = _FakeEgressVM(targets={"conn_a": "https://a.example:443", "conn_b": None, "conn_c": None})
        root, _, _ = _render(vm, egress_vm)
        grant = _find(root, "egress_grant_conn_a")
        assert isinstance(grant, ft.Button)
        assert grant.on_click is not None
        assert _maybe_find(root, "egress_revoke_conn_a") is None

        # 触发 on_click（Flet on_click Union 类型含 0 参分支，pyright 推断为 0 参，运行时接收 ControlEvent）
        grant.on_click(_make_event())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支，运行时接收 ControlEvent]

        egress_vm.grant_egress.assert_called_once_with("conn_a", "https://a.example:443", "news_annotation")

    def test_granted_target_renders_revoke_and_dispatches(self, mock_i18n_state, mock_app_colors_state) -> None:
        """已有有效 grant → 显示「已授权」与撤销按钮；点击撤销提交 grant_id。"""
        vm = _FakeRoutesVM(_make_state())
        egress_vm = _FakeEgressVM(
            targets={"conn_a": "https://a.example:443", "conn_b": None, "conn_c": None},
            grants={
                ("conn_a", "news_annotation"): (
                    {"grant_id": "g-1", "connection_id": "conn_a", "purpose": "news_annotation"},
                )
            },
        )
        root, _, _ = _render(vm, egress_vm)
        assert _find(root, "egress_granted_conn_a").value == "model_routes_egress_granted"
        assert _maybe_find(root, "egress_grant_conn_a") is None
        revoke = _find(root, "egress_revoke_conn_a")
        assert isinstance(revoke, ft.TextButton)
        assert revoke.on_click is not None

        # 触发 on_click（Flet on_click Union 类型含 0 参分支，pyright 推断为 0 参，运行时接收 ControlEvent）
        revoke.on_click(_make_event())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支，运行时接收 ControlEvent]

        egress_vm.revoke_egress.assert_called_once_with("g-1")

    def test_empty_candidates_renders_no_target_rows(self, mock_i18n_state, mock_app_colors_state) -> None:
        """当前场景无候选时摘要只列任务 / 数据类别，不渲染任何目标 / 授权行。"""
        vm = _FakeRoutesVM(_make_state(selected_scenario="news_risk"))
        egress_vm = _FakeEgressVM(targets={"conn_a": "https://a.example:443"})
        root, _, _ = _render(vm, egress_vm)
        assert isinstance(_find(root, "egress_summary"), ft.Container)
        assert _egress_keys(root) == []

    def test_task_and_data_labels_follow_scenario(self, mock_i18n_state, mock_app_colors_state) -> None:
        """任务 / 数据类别标签随选中场景变化（场景 id ↔ grant purpose 同名）。"""
        vm = _FakeRoutesVM(_make_state(selected_scenario="stock_analysis"))
        root, _, mock_i18n = _render(vm)
        assert isinstance(_find(root, "egress_summary"), ft.Container)
        assert _i18n_kwargs(mock_i18n, "model_routes_egress_task")["task"] == (
            "model_routes_egress_task_stock_analysis"
        )
        assert _i18n_kwargs(mock_i18n, "model_routes_egress_data")["data"] == (
            "model_routes_egress_data_stock_analysis"
        )

    def test_news_annotation_labels(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm = _FakeRoutesVM(_make_state(selected_scenario="news_annotation"))
        _, _, mock_i18n = _render(vm)
        assert _i18n_kwargs(mock_i18n, "model_routes_egress_task")["task"] == (
            "model_routes_egress_task_news_annotation"
        )
        assert _i18n_kwargs(mock_i18n, "model_routes_egress_data")["data"] == (
            "model_routes_egress_data_news_annotation"
        )
