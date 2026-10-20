"""ui/components/model_management_panel.py「使用记录」/「预算摘要」区纯函数测试（S5 §11.5）。

覆盖（模块级纯函数，不进入 flet 渲染上下文）：

- ``_build_budget_summary``：B1「解除未确认预留」入口不受未确认金额数值门控——只要存在
  可解除明细（含 ``reservation_id``）即渲染；无可解除对象不渲染（R21 不伪装）。
- ``_build_release_controls``：明细行月份取 ``billing_month``（② 修复）。
- ``_build_usage_filters``：七类过滤（日期起止 / 场景 / 子任务 / 连接 / 来源 / 终态 / 费用状态，
  共 8 个控件），连接下拉取已提交连接（名称优先，缺失回退 id）。
- ``_select_filter_handler`` / ``_date_filter_handler``：下拉 / 日期输入的过滤更新语义
  （``all`` → None；半截或非法日期不改动；合法日期按 Asia/Shanghai → naive UTC 边界换算）。
- ``_parse_date_filter`` / ``_format_date_filter``：本地日期与 ``created_at`` 墙钟边界往返。
- ``_build_usage_view`` / ``_export_csv_and_notify``：CSV 导出真实落盘（web 走 ``src_bytes``，
  桌面经线程池写路径，C-1）。
"""

import asyncio
import datetime as dt
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, call, patch

import flet as ft
import pytest

from core.i18n import Message
from tests.unit.ui.component_renderer import FakePage
from ui.components import model_management_panel as panel_module
from ui.components.model_management_panel import (
    _build_attempts,
    _build_budget_summary,
    _build_connection_row,
    _build_release_controls,
    _build_request_rows,
    _build_usage_filters,
    _build_usage_view,
    _date_filter_handler,
    _export_csv_and_notify,
    _format_date_filter,
    _parse_date_filter,
    _reservation_amount,
    _select_filter_handler,
)
from ui.i18n import I18n
from ui.theme import AppColors
from ui.viewmodels.model_connections_view_model import (
    BillingSummary,
    ConnectionRow,
    ConnectionsSummary,
    ModelConnectionsState,
)
from ui.viewmodels.model_routes_view_model import ModelRoutesState
from ui.viewmodels.model_usage_view_model import (
    ORIGIN_TEST,
    BudgetSummary,
    ModelUsageState,
    UsageFilters,
)

pytestmark = pytest.mark.unit


def _state(*, amount: Decimal | None, reservations: tuple = ()) -> ModelUsageState:
    return ModelUsageState(
        budget_summary=BudgetSummary(unconfirmed_reserved_cny=amount),
        unconfirmed_reservations=reservations,
    )


def _usage_state(**overrides: Any) -> ModelUsageState:
    defaults: dict[str, Any] = {"budget_summary": BudgetSummary()}
    defaults.update(overrides)
    return ModelUsageState(**defaults)


def _row(connection_id: str, name: str = "") -> ConnectionRow:
    return ConnectionRow(
        connection_id=connection_id,
        name=name,
        model="gpt-4o",
        adapter_kind="openai",
        status="enabled",
        enabled=True,
        billing=BillingSummary(mode="usage_only", pricing_unit="llm_tokens"),
        credential_ref=None,
        scenario_ref_count=0,
    )


def _release_rows(control: ft.Control) -> list[ft.Control]:
    """取 budget 摘要列内除首个 chip 行外的 release 控件（含 hint + 每条按钮行）。"""
    assert isinstance(control, ft.Column)
    return control.controls[1:]


class TestBudgetSummaryReleaseButton:
    def test_button_shown_when_unconfirmed_positive_with_details(self):
        state = _state(
            amount=Decimal("0.02"),
            reservations=({"reservation_id": "res-1", "task_id": "sentiment", "amount_cny": "0.02"},),
        )
        col = _build_budget_summary(state, MagicMock())
        rows = _release_rows(col)
        # hint + 1 条按钮行
        assert len(rows) == 2
        assert isinstance(rows[0], ft.Text)
        button_row = rows[1]
        assert isinstance(button_row, ft.Row)
        button = button_row.controls[1]
        assert isinstance(button, ft.TextButton)
        assert button.content == I18n.get("model_usage_release_button")
        assert button.icon == ft.Icons.LOCK_OPEN
        assert button.on_click is not None

    def test_button_rendered_per_reservation(self):
        state = _state(
            amount=Decimal("0.04"),
            reservations=(
                {"reservation_id": "res-1", "task_id": "sentiment", "amount_cny": "0.02"},
                {"reservation_id": "res-2", "task_id": "news", "amount_cny": "0.02"},
            ),
        )
        col = _build_budget_summary(state, MagicMock())
        rows = _release_rows(col)
        # hint + 2 条按钮行
        assert len(rows) == 3
        assert all(isinstance(r, ft.Row) for r in rows[1:])

    def test_release_entry_not_gated_by_zero_amount(self):
        """B1：未确认金额为 0 但存在 held_unconfirmed 明细时仍渲染解除入口。"""
        state = _state(amount=Decimal("0"), reservations=({"reservation_id": "res-1"},))
        col = _build_budget_summary(state, MagicMock())
        assert len(_release_rows(col)) == 2

    def test_release_entry_not_gated_by_none_amount(self):
        """B1：未确认金额缺失（None）时仍渲染解除入口，不被数值遮蔽。"""
        state = _state(amount=None, reservations=({"reservation_id": "res-1"},))
        col = _build_budget_summary(state, MagicMock())
        assert len(_release_rows(col)) == 2

    def test_no_button_without_reservation_id(self):
        """无 reservation_id 的明细不渲染按钮（不伪装为可解除对象）。"""
        state = _state(amount=Decimal("0.02"), reservations=({"task_id": "sentiment"},))
        col = _build_budget_summary(state, MagicMock())
        assert _release_rows(col) == []


class TestReleaseControls:
    def test_target_text_uses_billing_month(self):
        """明细行月份取 ``billing_month``（② 修复：月份经 i18n 参数而非固定占位）。"""
        state = _state(
            amount=Decimal("0.02"),
            reservations=(
                {
                    "reservation_id": "res-1",
                    "billing_month": "2026-10",
                    "task_id": "sentiment",
                    "amount_cny": "0.02",
                },
            ),
        )
        controls = _build_release_controls(state, MagicMock())
        assert len(controls) == 2
        text = controls[1].controls[0]  # type: ignore[attr-defined]
        assert "2026-10" in text.value
        assert "0.02" in text.value

    def test_missing_month_falls_back_to_unknown(self):
        """缺失月份回退「未知」，不伪造成具体月份（R21）。"""
        state = _state(
            amount=None,
            reservations=({"reservation_id": "res-1", "task_id": "sentiment"},),
        )
        controls = _build_release_controls(state, MagicMock())
        assert I18n.get("model_usage_unknown") in controls[1].controls[0].value  # type: ignore[attr-defined]


class TestReservationAmount:
    def test_none_stays_none(self):
        assert _reservation_amount(None) is None

    def test_numeric_string_parsed(self):
        assert _reservation_amount("0.02") == Decimal("0.02")

    def test_garbage_returns_none(self):
        assert _reservation_amount("garbage") is None


def _filter_controls(state: ModelUsageState, vm: MagicMock, connections: tuple = ()) -> list[Any]:
    row = _build_usage_filters(state, vm, list(connections))
    assert isinstance(row, ft.Row)
    return row.controls


class TestUsageFilters:
    def test_seven_filters_rendered_in_order(self):
        controls = _filter_controls(_usage_state(), MagicMock())
        assert len(controls) == 8
        assert isinstance(controls[0], ft.TextField)  # 起始日期
        assert isinstance(controls[1], ft.TextField)  # 结束日期
        for ctrl in controls[2:]:
            assert isinstance(ctrl, ft.Dropdown)

    def test_filter_values_derive_from_state(self):
        filters = UsageFilters(
            scenario_id="news_risk",
            task_id="risk",
            connection_id="c1",
            origin=ORIGIN_TEST,
            terminal_status="failed",
            cost_status="unknown",
        )
        controls = _filter_controls(_usage_state(filters=filters), MagicMock(), (_row("c1", "Conn One"),))
        assert controls[2].value == "news_risk"
        assert controls[3].value == "risk"
        assert controls[4].value == "c1"
        assert controls[5].value == "test"
        assert controls[6].value == "failed"
        assert controls[7].value == "unknown"

    def test_none_filters_show_all_option(self):
        controls = _filter_controls(_usage_state(), MagicMock())
        assert controls[2].value == "all"
        assert controls[7].value == "all"

    def test_connection_options_from_committed_connections(self):
        controls = _filter_controls(_usage_state(), MagicMock(), (_row("c1", "Conn One"), _row("c2", "")))
        conn_dd = controls[4]
        assert [o.key for o in conn_dd.options] == ["all", "c1", "c2"]
        texts = {o.key: o.text for o in conn_dd.options}
        assert texts["c1"] == "Conn One"
        assert texts["c2"] == "c2"  # 名称缺失回退 id


class TestFilterHandlers:
    def test_select_filter_all_maps_to_none(self):
        vm = MagicMock()
        handler = _select_filter_handler(vm, "terminal_status")
        event = MagicMock()
        event.control = ft.Dropdown(value="all")
        handler(event)
        vm.set_filter.assert_called_once_with(terminal_status=None)

    def test_select_filter_value_passthrough(self):
        vm = MagicMock()
        handler = _select_filter_handler(vm, "cost_status")
        event = MagicMock()
        event.control = ft.Dropdown(value="unknown")
        handler(event)
        vm.set_filter.assert_called_once_with(cost_status="unknown")

    def test_date_filter_incomplete_input_no_op(self):
        vm = MagicMock()
        handler = _date_filter_handler(vm, "start", end_of_day=False)
        event = MagicMock()
        event.control = ft.TextField(value="2026-1")
        handler(event)
        vm.set_filter.assert_not_called()

    def test_date_filter_invalid_date_no_op(self):
        vm = MagicMock()
        handler = _date_filter_handler(vm, "start", end_of_day=False)
        event = MagicMock()
        event.control = ft.TextField(value="2026-13-40")
        handler(event)
        vm.set_filter.assert_not_called()

    def test_date_filter_empty_clears(self):
        vm = MagicMock()
        handler = _date_filter_handler(vm, "start", end_of_day=False)
        event = MagicMock()
        event.control = ft.TextField(value="")
        handler(event)
        vm.set_filter.assert_called_once_with(start=None)

    def test_date_filter_valid_start_converts_to_utc(self):
        vm = MagicMock()
        handler = _date_filter_handler(vm, "start", end_of_day=False)
        event = MagicMock()
        event.control = ft.TextField(value="2026-10-19")
        handler(event)
        # 2026-10-19 00:00 Asia/Shanghai（UTC+8）→ 2026-10-18T16:00 naive UTC
        vm.set_filter.assert_called_once_with(start=dt.datetime(2026, 10, 18, 16, 0, 0))


class TestDateBoundaryConversions:
    def test_parse_start_boundary(self):
        assert _parse_date_filter("2026-10-19", end_of_day=False) == dt.datetime(2026, 10, 18, 16)

    def test_parse_end_boundary_is_next_day(self):
        # 排他上界：end_of_day 取次日零点（本地）→ 2026-10-19T16:00 naive UTC
        assert _parse_date_filter("2026-10-19", end_of_day=True) == dt.datetime(2026, 10, 19, 16)

    def test_parse_empty_returns_none(self):
        assert _parse_date_filter("", end_of_day=False) is None

    def test_parse_garbage_returns_none(self):
        assert _parse_date_filter("not-a-date", end_of_day=False) is None

    def test_format_start_boundary(self):
        assert _format_date_filter(dt.datetime(2026, 10, 18, 16), end_of_day=False) == "2026-10-19"

    def test_format_end_boundary(self):
        assert _format_date_filter(dt.datetime(2026, 10, 19, 16), end_of_day=True) == "2026-10-19"

    def test_format_non_datetime_returns_empty(self):
        assert _format_date_filter(None, end_of_day=False) == ""


class TestUsageView:
    def test_view_contains_budget_filters_and_export(self):
        vm = MagicMock()
        view = _build_usage_view(_usage_state(), vm, [], None, MagicMock())
        assert isinstance(view, ft.Column)
        # [0] 预算摘要列、[1] 过滤行
        assert isinstance(view.controls[0], ft.Column)
        assert isinstance(view.controls[1], ft.Row)
        export = next(
            c
            for c in view.controls[2].controls  # type: ignore[attr-defined]
            if getattr(c, "key", None) == "usage_export"
        )
        assert export.on_click is not None

    def test_monthly_mode_hides_request_rows(self):
        vm = MagicMock()
        view = _build_usage_view(_usage_state(monthly_summary_mode=True), vm, [], None, MagicMock())
        keys = [getattr(c, "key", None) for c in view.controls]
        assert "model_usage_monthly_mode_note" in keys
        assert all(not str(getattr(c, "key", "") or "").startswith("request_row_") for c in view.controls)


def _fake_page(*, web: bool) -> FakePage:
    page = FakePage()
    page.web = web
    return page


class TestExportCsvAndNotify:
    @pytest.mark.asyncio
    async def test_web_export_passes_src_bytes(self):
        vm = MagicMock()
        vm.export_csv = AsyncMock(return_value="csv-body")
        picker = MagicMock()
        picker.save_file = AsyncMock()
        page = _fake_page(web=True)
        show_snack = MagicMock()

        with patch.object(panel_module, "_get_page", return_value=page):
            await _export_csv_and_notify(vm, picker, show_snack)

        picker.save_file.assert_awaited_once()
        assert picker.save_file.await_args.kwargs["src_bytes"] == "csv-body".encode("utf-8-sig")
        show_snack.assert_called_once_with(I18n.get("model_usage_export_ready"), color=AppColors.SUCCESS)

    @pytest.mark.asyncio
    async def test_desktop_export_delegates_write_to_vm(self):
        vm = MagicMock()
        vm.export_csv = AsyncMock(return_value="csv-body")
        vm.write_csv = AsyncMock()
        picker = MagicMock()
        picker.save_file = AsyncMock(return_value="/tmp/out.csv")
        page = _fake_page(web=False)
        show_snack = MagicMock()

        with patch.object(panel_module, "_get_page", return_value=page):
            await _export_csv_and_notify(vm, picker, show_snack)

        vm.write_csv.assert_awaited_once_with("/tmp/out.csv", "csv-body")
        show_snack.assert_called_once_with(I18n.get("model_usage_export_ready"), color=AppColors.SUCCESS)

    @pytest.mark.asyncio
    async def test_desktop_export_cancelled_path_no_write(self):
        vm = MagicMock()
        vm.export_csv = AsyncMock(return_value="csv-body")
        vm.write_csv = AsyncMock()
        picker = MagicMock()
        picker.save_file = AsyncMock(return_value="")  # 用户取消
        page = _fake_page(web=False)
        show_snack = MagicMock()

        with patch.object(panel_module, "_get_page", return_value=page):
            await _export_csv_and_notify(vm, picker, show_snack)

        vm.write_csv.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_export_failure_notifies_error(self):
        vm = MagicMock()
        vm.export_csv = AsyncMock(side_effect=RuntimeError("boom"))
        picker = MagicMock()
        show_snack = MagicMock()

        await _export_csv_and_notify(vm, picker, show_snack)

        show_snack.assert_called_once_with(I18n.get("model_usage_export_failed"), color=AppColors.ERROR)


def _texts(control: Any) -> list[str]:
    """递归收集控件树内所有非空文本值（用于断言本地化文案，替代裸枚举）。

    ``control`` 可为单个控件（``_build_connection_row``）或控件列表
    （``_build_request_rows``），二者均支持。
    """
    if isinstance(control, (list, tuple)):
        out: list[str] = []
        for item in control:
            out.extend(_texts(item))
        return out
    out = []
    value = getattr(control, "value", None)
    if isinstance(value, str) and value:
        out.append(value)
    content = getattr(control, "content", None)
    if content is not None:
        out.extend(_texts(content))
    for child in getattr(control, "controls", None) or []:
        out.extend(_texts(child))
    return out


class TestAttemptsDetail:
    """§11.5 展开明细：跳过行标「未执行」、状态本地化、原因 / 费用按可用性展示。"""

    def test_skipped_row_labeled_not_executed_without_fabricated_cost(self):
        controls = _build_attempts(
            [
                {
                    "kind": "skipped",
                    "executed": False,
                    "candidate_index": 1,
                    "status": "skipped",
                    "reason": "budget_blocked",
                    "cost_cny": None,
                    "actual_model": None,
                    "configured_model": "glm-4-air",
                }
            ]
        )
        line = controls[0].value
        assert I18n.get("model_usage_attempt_not_executed") in line
        assert "skipped" not in line  # 不展示原始枚举
        assert I18n.get("model_usage_attempt_reason", reason="budget_blocked") in line
        # 跳过行不展示虚构费用（§10.1 / R21）
        assert I18n.get("model_usage_attempt_cost", cost="") not in line

    def test_executed_row_localizes_status_and_shows_retry_reason_cost(self):
        controls = _build_attempts(
            [
                {
                    "kind": "attempt",
                    "executed": True,
                    "candidate_index": 0,
                    "retry_index": 1,
                    "status": "failed",
                    "reason": "timeout",
                    "cost_cny": "0.03",
                    "actual_model": "glm-4-air",
                    "configured_model": None,
                }
            ]
        )
        line = controls[0].value
        assert I18n.get("model_usage_terminal_failed") in line
        assert "failed" not in line  # 不展示原始枚举
        assert I18n.get("model_usage_attempt_retry", retry=1) in line
        assert I18n.get("model_usage_attempt_reason", reason="timeout") in line
        assert I18n.get("model_usage_attempt_cost", cost="0.03") in line

    def test_dispatched_attempt_maps_to_running(self):
        controls = _build_attempts([{"executed": True, "candidate_index": 0, "status": "dispatched"}])
        assert I18n.get("model_usage_terminal_running") in controls[0].value


class TestRequestRowEnumLocalization:
    """请求列表终态 / 费用状态必须经映射本地化，不展示原始枚举（§11.5）。"""

    def test_terminal_and_cost_status_localized(self):
        state = _usage_state(
            requests=(
                {
                    "invocation_id": "inv-1",
                    "created_at": "2026-10-19T00:00:00",
                    "scenario_id": "news_risk",
                    "terminal_status": "failed",
                    "cost_status": "partial",
                },
            )
        )
        texts = _texts(_build_request_rows(state, MagicMock()))
        assert I18n.get("model_usage_terminal_failed") in texts
        assert I18n.get("model_usage_cost_partial") in texts
        assert "failed" not in texts
        assert "partial" not in texts

    def test_unknown_status_falls_back_to_explicit_unknown(self):
        state = _usage_state(
            requests=(
                {"invocation_id": "inv-1", "scenario_id": "news_risk", "terminal_status": "bogus", "cost_status": ""},
            )
        )
        texts = _texts(_build_request_rows(state, MagicMock()))
        assert I18n.get("model_usage_terminal_unknown") in texts
        assert I18n.get("model_usage_cost_unknown") in texts
        assert "bogus" not in texts


class TestConnectionRowBillingLocalized:
    """连接行计量口径经 i18n 展示，不展示 ``usage_only`` / ``estimate_cost`` 裸枚举。"""

    def test_estimate_cost_mode_localized(self):
        row = replace(_row("c1", "Conn"), billing=BillingSummary(mode="estimate_cost", pricing_unit="llm_tokens"))
        texts = _texts(_build_connection_row(row, MagicMock()))
        assert I18n.get("model_connection_billing_estimate_cost") in texts
        assert "estimate_cost" not in texts

    def test_usage_only_mode_localized(self):
        texts = _texts(_build_connection_row(_row("c1", "Conn"), MagicMock()))
        assert I18n.get("model_connection_billing_usage_only") in texts
        assert "usage_only" not in texts


class TestConnectionRowEditEntry:
    """F12：连接行必须提供可达的编辑入口（显式按钮绑定 ``vm.edit``，不做行级隐式点击）。"""

    def _edit_button(self, row_control: ft.Control) -> ft.TextButton:
        for ctrl in _walk_controls(row_control):
            if getattr(ctrl, "key", None) == "model_connection_edit":
                assert isinstance(ctrl, ft.TextButton)
                return ctrl
        raise AssertionError("edit button not found in connection row")

    def _fire(self, button: ft.TextButton) -> None:
        """触发编辑按钮 on_click（Flet ControlEventHandler 为 0/1 参联合，测试侧 cast 边界）。"""
        handler = button.on_click
        assert handler is not None
        cast(Callable[[Any], None], handler)(MagicMock(name="event"))

    def test_edit_button_bound_to_vm_edit(self):
        vm = MagicMock()
        button = self._edit_button(_build_connection_row(_row("c1", "Conn One"), vm))
        assert button.icon == ft.Icons.EDIT
        self._fire(button)
        vm.edit.assert_called_once_with("c1")

    def test_edit_button_localized(self):
        button = self._edit_button(_build_connection_row(_row("c1", "Conn"), MagicMock()))
        assert button.content == I18n.get("model_management_edit_connection")
        assert button.tooltip == I18n.get("model_management_edit_connection")

    def test_rows_bind_their_own_connection_id(self):
        """每行 handler 独立绑定本行 id（无闭包晚绑定串行）。"""
        vm = MagicMock()
        buttons = [self._edit_button(_build_connection_row(row, vm)) for row in (_row("c1"), _row("c2"))]
        for button in buttons:
            self._fire(button)
        assert vm.edit.call_args_list == [call("c1"), call("c2")]

    def test_row_has_no_container_level_click(self):
        """行容器不接 on_click：编辑只走显式按钮，避免行点击与按钮语义冲突。"""
        container = _build_connection_row(_row("c1", "Conn"), MagicMock())
        assert isinstance(container, ft.Container)
        assert container.on_click is None

    def test_connections_view_rows_carry_edit_entry(self):
        vm = MagicMock()
        vm.visible_connections.return_value = (_row("c1", "Conn One"),)
        view = panel_module._build_connections_view(ModelConnectionsState(), vm, compact=False)
        assert "model_connection_edit" in _collect_keys(view)


# ============================================================================
# 覆盖率门禁补充：事件包装 / FilePicker 服务 / 摘要行 / 三页签构建器（S5 §11.1）
# ============================================================================


class _ExplodingPageContext:
    """模拟「无页面渲染上下文」：访问 page 属性抛 RuntimeError（对齐 flet.context 行为）。"""

    @property
    def page(self) -> ft.Page:
        raise RuntimeError("no page context")


def _walk_controls(control: Any):
    """先序遍历控件树（controls 列表 + content 单子控件）。"""
    stack = [control]
    while stack:
        cur = stack.pop()
        yield cur
        children = getattr(cur, "controls", None)
        if isinstance(children, list):
            stack.extend(children)
        content = getattr(cur, "content", None)
        if content is not None:
            stack.append(content)


def _collect_keys(control: Any) -> list[str]:
    """递归收集控件树内所有 str key（用于断言 e2e / 业务锚点）。"""
    return [key for c in _walk_controls(control) if isinstance(key := getattr(c, "key", None), str)]


class TestRenderMessage:
    def test_none_renders_empty(self, monkeypatch):
        mock_i18n = MagicMock()
        monkeypatch.setattr(panel_module, "I18n", mock_i18n)
        assert panel_module._render_message(None) == ""
        mock_i18n.get.assert_not_called()

    def test_plain_params_passthrough(self, monkeypatch):
        mock_i18n = MagicMock()
        mock_i18n.get = MagicMock(return_value="TEXT")
        monkeypatch.setattr(panel_module, "I18n", mock_i18n)
        assert panel_module._render_message(Message("k", {"a": 1})) == "TEXT"
        mock_i18n.get.assert_called_once_with("k", a=1)

    def test_key_suffix_params_translated_first(self, monkeypatch):
        """``*_key`` 后缀参数先翻译后以原名填入主模板（MVVM i18n 约定）。"""
        mock_i18n = MagicMock()
        mock_i18n.get = MagicMock(side_effect=lambda key, **kw: f"T[{key}]")
        monkeypatch.setattr(panel_module, "I18n", mock_i18n)
        assert panel_module._render_message(Message("k", {"name_key": "strategy_x"})) == "T[k]"
        mock_i18n.get.assert_any_call("strategy_x")
        mock_i18n.get.assert_any_call("k", name="T[strategy_x]")


class TestClickWrapper:
    def test_wraps_sync_command(self):
        calls: list[int] = []
        handler = panel_module._click(lambda: calls.append(1))
        handler(MagicMock(name="event"))
        assert calls == [1]


class TestRunTaskHandler:
    def test_submits_via_page_run_task(self, monkeypatch):
        page = MagicMock(name="page")
        monkeypatch.setattr(ft, "context", SimpleNamespace(page=page))
        coro = MagicMock(name="coro_func")
        panel_module._run_task_handler(coro, "arg1")(MagicMock(name="event"))
        page.run_task.assert_called_once_with(coro, "arg1")

    def test_no_page_context_degrades_to_debug_log(self, monkeypatch):
        monkeypatch.setattr(ft, "context", _ExplodingPageContext())
        mock_logger = MagicMock()
        monkeypatch.setattr(panel_module, "logger", mock_logger)
        panel_module._run_task_handler(MagicMock(name="coro_func"))(MagicMock(name="event"))
        assert mock_logger.debug.call_count == 1
        assert "page unavailable" in mock_logger.debug.call_args.args[0]


class TestGetPage:
    def test_returns_none_outside_page_context(self, monkeypatch):
        monkeypatch.setattr(ft, "context", _ExplodingPageContext())
        assert panel_module._get_page() is None

    def test_returns_page_in_context(self, monkeypatch):
        page = MagicMock(name="page")
        monkeypatch.setattr(ft, "context", SimpleNamespace(page=page))
        assert panel_module._get_page() is page


class TestSyncFilePickerService:
    def test_detach_removes_existing_picker(self):
        page = MagicMock(name="page")
        page.services = [MagicMock(), "picker"]
        panel_module._sync_file_picker_service(page, "picker", attach=False)
        assert "picker" not in page.services

    def test_attach_skips_when_already_registered(self):
        page = MagicMock(name="page")
        picker = object()
        page.services = [picker]
        panel_module._sync_file_picker_service(page, picker, attach=True)
        assert page.services == [picker]

    def test_noop_when_page_or_picker_none(self):
        panel_module._sync_file_picker_service(None, object(), attach=True)
        panel_module._sync_file_picker_service(MagicMock(name="page"), None, attach=False)


class TestExportCsvFailurePaths:
    @pytest.mark.asyncio
    async def test_export_cancelled_propagates(self):
        """R2：export_csv 的 CancelledError 必须传播，不得降级为失败提示。"""
        vm = MagicMock()
        vm.export_csv = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2: CancelledError 无属性载荷，类型即完整契约
            await panel_module._export_csv_and_notify(vm, MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_no_file_picker_returns_silently(self):
        vm = MagicMock()
        vm.export_csv = AsyncMock(return_value="csv")
        show_snack = MagicMock()
        with patch.object(panel_module, "_get_page", return_value=MagicMock(name="page")):
            await panel_module._export_csv_and_notify(vm, None, show_snack)
        show_snack.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_failure_logs_and_notifies(self):
        vm = MagicMock()
        vm.export_csv = AsyncMock(return_value="csv-body")
        vm.write_csv = AsyncMock()
        picker = MagicMock()
        picker.save_file = AsyncMock(side_effect=RuntimeError("disk full"))
        show_snack = MagicMock()
        with (
            patch.object(panel_module, "_get_page", return_value=_fake_page(web=False)),
            patch.object(panel_module, "logger") as mock_logger,
        ):
            await panel_module._export_csv_and_notify(vm, picker, show_snack)
        vm.write_csv.assert_not_awaited()
        show_snack.assert_called_once_with(I18n.get("model_usage_export_failed"), color=AppColors.ERROR)
        assert mock_logger.error.call_count == 1
        assert "[ModelManagementPanel] CSV export failed" in mock_logger.error.call_args.args[0]

    @pytest.mark.asyncio
    async def test_save_cancelled_propagates(self):
        """R2：save_file 阶段的 CancelledError 必须传播。"""
        vm = MagicMock()
        vm.export_csv = AsyncMock(return_value="csv-body")
        picker = MagicMock()
        picker.save_file = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(panel_module, "_get_page", return_value=_fake_page(web=False)):
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2: CancelledError 无属性载荷，类型即完整契约
                await panel_module._export_csv_and_notify(vm, picker, MagicMock())


class TestMessageText:
    def test_renders_message_to_text(self):
        ctrl = panel_module._message_text(Message("model_usage_export_ready"))
        assert isinstance(ctrl, ft.Text)
        assert ctrl.value == I18n.get("model_usage_export_ready")


class TestBuildSummaryRow:
    def test_full_summary_renders_three_chips(self):
        state = ModelConnectionsState(
            summary=ConnectionsSummary(executable_scenarios=3, configs_needing_attention=1, known_cost_cny="12.50")
        )
        row = panel_module._build_summary_row(state)
        assert isinstance(row, ft.Row)
        texts = [t.value for c in row.controls for t in c.controls]  # type: ignore[attr-defined]
        assert "3" in texts and "1" in texts and "12.50" in texts
        assert len(row.controls) == 3

    def test_missing_cost_and_optional_counts(self):
        """R21：已知费用缺失渲染「未知」；未知 / 未统计数存在时追加 chip。"""
        state = ModelConnectionsState(summary=ConnectionsSummary(unknown_cost_count=2, unpriced_count=1))
        row = panel_module._build_summary_row(state)
        texts = [t.value for c in row.controls for t in c.controls]  # type: ignore[attr-defined]
        assert I18n.get("model_usage_unknown") in texts
        assert "2" in texts and "1" in texts
        assert len(row.controls) == 5


class TestBuildConnectionEditor:
    def _editor(self, monkeypatch, *, commit_unknown_pending: bool):
        form_mock = MagicMock(return_value=ft.Text("form-stub"))
        monkeypatch.setattr(panel_module, "ModelConnectionForm", form_mock)
        state = ModelConnectionsState(editor_step=1, commit_unknown_pending=commit_unknown_pending)
        vm = MagicMock()
        return panel_module._build_connection_editor(state, vm, compact=False), form_mock, vm

    def test_renders_form_and_step_actions(self, monkeypatch):
        container, form_mock, vm = self._editor(monkeypatch, commit_unknown_pending=False)
        assert isinstance(container, ft.Container)
        assert container.key == "model_connection_editor"
        form_mock.assert_called_once_with(vm=vm, editor_step=1, compact=False)
        keys = set(_collect_keys(container))
        assert {"model_editor_prev", "model_editor_next", "model_editor_test", "model_editor_save"} <= keys
        assert "model_editor_resolve" not in keys

    def test_commit_unknown_pending_inserts_resolve_button(self, monkeypatch):
        container, _, _ = self._editor(monkeypatch, commit_unknown_pending=True)
        assert "model_editor_resolve" in set(_collect_keys(container))


class TestBuildConnectionsView:
    def test_lists_rows_with_filter_and_add(self):
        vm = MagicMock()
        vm.visible_connections.return_value = (_row("c1", "Conn One"), _row("c2", "Conn Two"))
        state = ModelConnectionsState(search_query="q", status_filter="enabled")
        view = panel_module._build_connections_view(state, vm, compact=False)
        assert isinstance(view, ft.Column)
        assert len(view.controls) == 3  # 过滤行 + 2 连接行
        vm.visible_connections.assert_called_once_with()

    def test_empty_state_renders_empty_hint(self):
        vm = MagicMock()
        vm.visible_connections.return_value = []
        view = panel_module._build_connections_view(ModelConnectionsState(), vm, compact=False)
        assert I18n.get("model_management_no_connections") in _texts(view)

    def test_editor_open_and_message_appended(self, monkeypatch):
        form_mock = MagicMock(return_value=ft.Text("form-stub"))
        monkeypatch.setattr(panel_module, "ModelConnectionForm", form_mock)
        vm = MagicMock()
        vm.visible_connections.return_value = [_row("c1", "Conn One")]
        state = ModelConnectionsState(editor_open=True, message=Message("model_management_no_connections"))
        view = panel_module._build_connections_view(state, vm, compact=True)
        assert "model_connection_editor" in set(_collect_keys(view))
        assert I18n.get("model_management_no_connections") in _texts(view)
        form_mock.assert_called_once_with(vm=vm, editor_step=1, compact=True)


class TestBuildRoutesView:
    def test_renders_editor_stub_and_save_row(self, monkeypatch):
        editor_mock = MagicMock(return_value=ft.Text("editor-stub"))
        monkeypatch.setattr(panel_module, "ScenarioRouteEditor", editor_mock)
        vm = MagicMock()
        connections_vm = MagicMock()
        view = panel_module._build_routes_view(ModelRoutesState(), vm, connections_vm, compact=False)
        assert isinstance(view, ft.Column)
        editor_mock.assert_called_once_with(vm=vm, egress_vm=connections_vm, compact=False)
        save_btn = next(c for c in _walk_controls(view) if getattr(c, "key", None) == "routes_save_current")
        assert save_btn.content == I18n.get("model_routes_save_current")

    def test_message_appended(self, monkeypatch):
        monkeypatch.setattr(panel_module, "ScenarioRouteEditor", MagicMock(return_value=ft.Text("editor-stub")))
        view = panel_module._build_routes_view(
            ModelRoutesState(message=Message("model_management_no_connections")),
            MagicMock(),
            MagicMock(),
            compact=True,
        )
        assert I18n.get("model_management_no_connections") in _texts(view)


class TestRequestRowsExpandedDetail:
    def test_expanded_row_includes_attempts_detail(self):
        """展开行渲染 attempts 明细；空 attempts 显示显式「无尝试」提示。"""
        state = _usage_state(
            requests=(
                {
                    "invocation_id": "inv-1",
                    "created_at": "2026-10-19 10:00:00",
                    "scenario_id": "news_annotation",
                    "terminal_status": "succeeded",
                    "cost_status": "known",
                },
            ),
            expanded_id="inv-1",
            attempts=(),
        )
        rows = panel_module._build_request_rows(state, MagicMock())
        assert len(rows) == 1
        assert I18n.get("model_usage_no_attempts") in _texts(rows[0])


class TestUsageViewRetentionAndMessage:
    def test_retention_hint_rendered(self):
        view = panel_module._build_usage_view(_usage_state(retention_exceeded=True), MagicMock(), [], None, MagicMock())
        assert I18n.get("model_usage_retention_hint") in _texts(view)

    def test_message_rendered(self):
        view = panel_module._build_usage_view(
            _usage_state(message=Message("model_usage_export_ready")), MagicMock(), [], None, MagicMock()
        )
        assert I18n.get("model_usage_export_ready") in _texts(view)
