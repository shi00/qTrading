"""W-12：研究侧模型/角色选择、发送前预算、费用只读投影与五段耗时分段。

覆盖重点（首版验收缺口 F16/F27/J23/P09/NFR-P06）：
- 连接选项只来自研究场景路由候选，非候选/停用/未知连接显式拒绝；
- 角色选项与版本冻结（子角色拒绝）；
- 预算可估算给金额与单位，不可估显式未计价/未知（不填 0，R21）；
- 本轮/累计费用按只读投影分列，未结算/未计价/读取失败不冒充金额；
- 五段耗时缺段显式未知，不以 0 冒充；
- 选择随首问冻结，冻结后改选不改写已冻结值。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ui.viewmodels.research_observer_view_model import (
    AGENT_RESEARCH_SCENARIO_ID,
    BUDGET_ESTIMATED,
    BUDGET_UNKNOWN,
    BUDGET_UNPRICED,
    DURATION_UNKNOWN_KEY,
    ResearchObserverViewModel,
    build_duration_segments,
    build_model_connection_options,
    build_role_options,
    derive_duration_values,
    estimate_budget,
)
from core.agent_types import MessageRole

pytestmark = pytest.mark.unit


def _connection(
    connection_id: str = "conn-a",
    *,
    name: str = "连接 A",
    model: str = "gpt-4o",
    enabled: bool = True,
    context_window: int | None = 128000,
    billing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "connection_id": connection_id,
        "name": name,
        "model": model,
        "provider": "openai",
        "enabled": enabled,
        "context_window": context_window,
    }
    if billing is not None:
        row["billing"] = billing
    return row


def _snapshot(
    *,
    candidates: tuple[str, ...] = ("conn-a", "conn-b"),
    connections: tuple[dict[str, Any], ...] | None = None,
    enabled: bool = True,
    include_route: bool = True,
) -> Any:
    routes: tuple[dict[str, Any], ...] = ()
    if include_route:
        routes = (
            {
                "scenario_id": AGENT_RESEARCH_SCENARIO_ID,
                "enabled": enabled,
                "candidates": list(candidates),
            },
        )
    return SimpleNamespace(
        connections=connections
        if connections is not None
        else (_connection("conn-a"), _connection("conn-b", name="连接 B", model="gpt-4.1")),
        scenario_routes=routes,
    )


class _Catalog:
    def __init__(self, snapshot: Any = None, *, error: Exception | None = None) -> None:
        self.snapshot = snapshot if snapshot is not None else _snapshot()
        self.error = error
        self.calls = 0

    async def get_snapshot(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


def _usage(*, cost_state: str = "known", attempt_count: int = 1, active_ms: int | None = 1500) -> Any:
    return SimpleNamespace(cost_state=cost_state, model_attempt_count=attempt_count, active_ms=active_ms)


def _execution_snapshot(
    *,
    cost_state: str = "known",
    attempt_count: int = 1,
    active_ms: int | None = 1500,
    invocation_ids: tuple[str, ...] = ("inv-1",),
    created_at: str = "2026-11-17T02:00:10.000000Z",
    ended_at: str | None = None,
    messages: tuple[Any, ...] = (),
    artifacts: tuple[Any, ...] = (),
    decisions: tuple[Any, ...] = (),
) -> Any:
    return SimpleNamespace(
        usage=_usage(cost_state=cost_state, attempt_count=attempt_count, active_ms=active_ms),
        operation_summaries=tuple(SimpleNamespace(invocation_id=value) for value in invocation_ids),
        execution=SimpleNamespace(created_at=created_at, ended_at=ended_at),
        messages_page=SimpleNamespace(items=messages),
        artifacts_page=SimpleNamespace(items=artifacts),
        decisions=decisions,
    )


def _projection(
    *,
    known_cny: str | None = "0.5",
    reserved_cny: str | None = "0",
    unpriced_attempts: int | None = 0,
    read_state: str = "ok",
    read_error: str | None = None,
) -> Any:
    return SimpleNamespace(
        known_cny=known_cny,
        reserved_cny=reserved_cny,
        unpriced_attempts=unpriced_attempts,
        read_state=read_state,
        read_error=read_error,
    )


def _session_read(*, executions: tuple[Any, ...] | None = None) -> Any:
    return SimpleNamespace(
        summary=SimpleNamespace(
            title="研究 A",
            updated_at="2026-11-17T02:00:00.000000Z",
            draft_revision=0,
            cleaned_at=None,
            revision=1,
            pinned=False,
            archived=False,
        ),
        executions=executions
        if executions is not None
        else (
            SimpleNamespace(
                execution_id="e-1",
                display_state="succeeded",
                parent_execution_id=None,
                model_attempt_count=1,
                tool_call_count=1,
                active_ms=1500,
                goal_coverage="unknown",
                ended_at=None,
            ),
        ),
        messages=SimpleNamespace(items=(), has_more=False, next_cursor=None),
        artifacts=SimpleNamespace(items=(), has_more=False, next_cursor=None),
        decisions=(),
    )


class _Service:
    """最小 facade 替身：read_session + 用量只读面（未实现的方法不冒充）。"""

    def __init__(
        self,
        *,
        snapshots: dict[str, Any] | None = None,
        projection: Any | None = None,
        snapshot_error: Exception | None = None,
        read: Any | None = None,
    ) -> None:
        self.read = read if read is not None else _session_read()
        self.snapshots = snapshots if snapshots is not None else {"e-1": _execution_snapshot()}
        self.projection = projection if projection is not None else _projection()
        self.snapshot_error = snapshot_error
        self.snapshot_calls: list[str] = []
        self.projection_calls: list[tuple[str, ...]] = []

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        return self.read

    async def get_snapshot(self, execution_id: str, **kwargs: Any) -> Any:
        self.snapshot_calls.append(execution_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        snapshot = self.snapshots.get(execution_id)
        if snapshot is None:
            raise KeyError(execution_id)
        return snapshot

    async def read_cost_projection(self, *, ledger_refs: tuple[str, ...]) -> Any:
        self.projection_calls.append(tuple(ledger_refs))
        return self.projection


class _Intake:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def start_question(self, text: str, **kwargs: Any) -> Any:
        self.calls.append({"text": text, **kwargs})
        return SimpleNamespace(main_turn=None, reason=None)


def _token_counter(_text: str, _model: str | None) -> tuple[int, bool]:
    return (max(1, len(_text)), True)


def _make_vm(service: Any, **kwargs: Any) -> ResearchObserverViewModel:
    """构造被测 VM（替身只实现本用例需要的读面；未实现方法由 VM 侧如实降级）。"""

    if "intake" in kwargs:
        kwargs["intake"] = cast(Any, kwargs["intake"])
    kwargs.setdefault("token_counter", _token_counter)
    return ResearchObserverViewModel(cast(Any, service), **kwargs)


@pytest.fixture
def vm() -> ResearchObserverViewModel:
    return _make_vm(
        _Service(),
        intake=_Intake(),
        model_catalog=_Catalog(),
        token_counter=_token_counter,
    )


class TestModelConnectionOptions:
    def test_route_candidates_in_order_with_effective_flag(self) -> None:
        options = build_model_connection_options(_snapshot())

        assert [option.connection_id for option in options] == ["conn-a", "conn-b"]
        assert options[0].effective is True and options[1].effective is False
        assert options[0].model == "gpt-4o" and options[0].context_window == 128000
        assert all(option.eligible for option in options)

    def test_disabled_and_unknown_candidate_are_visible_but_rejected(self) -> None:
        snapshot = _snapshot(
            candidates=("conn-a", "conn-b", "conn-ghost"),
            connections=(
                _connection("conn-a"),
                _connection("conn-b", enabled=False),
            ),
        )

        options = build_model_connection_options(snapshot)

        assert [option.connection_id for option in options] == ["conn-a", "conn-b", "conn-ghost"]
        assert options[0].eligible is True
        assert options[1].reject_key == "agent_research_model_selection_disabled"
        assert options[2].reject_key == "agent_research_model_selection_unknown"

    def test_route_disabled_or_missing_yields_no_options(self) -> None:
        assert build_model_connection_options(_snapshot(enabled=False)) == ()
        assert build_model_connection_options(_snapshot(include_route=False)) == ()


class TestRoleOptions:
    def test_main_role_eligible_child_role_rejected(self) -> None:
        options = build_role_options()

        by_key = {option.role_key: option for option in options}
        assert by_key["main"].eligible is True
        assert by_key["main"].version
        assert by_key["sub"].reject_key == "agent_research_role_selection_child_role"


class TestSelectionCommands:
    @pytest.mark.asyncio
    async def test_load_selects_effective_connection_and_main_role(self, vm: ResearchObserverViewModel) -> None:
        await vm.load_model_selection()

        assert vm.state.model_selection_available is True
        assert vm.state.model_selection == "conn-a"
        assert vm.state.role_selection == "main"
        assert vm.state.model_selection_error_key is None

    @pytest.mark.asyncio
    async def test_catalog_failure_reports_error_without_options(self) -> None:
        vm = _make_vm(_Service(), model_catalog=_Catalog(error=RuntimeError("boom")))

        await vm.load_model_selection()

        assert vm.state.model_options == ()
        assert vm.state.model_selection_error_key == "agent_research_model_selection_error"

    @pytest.mark.asyncio
    async def test_fallback_candidate_is_selectable(self, vm: ResearchObserverViewModel) -> None:
        await vm.load_model_selection()

        await vm.select_model_connection("conn-b")

        assert vm.state.model_selection == "conn-b"
        assert vm.state.model_selection_error_key is None

    @pytest.mark.asyncio
    async def test_unknown_connection_is_rejected_and_keeps_selection(self, vm: ResearchObserverViewModel) -> None:
        await vm.load_model_selection()

        await vm.select_model_connection("conn-x")

        assert vm.state.model_selection == "conn-a"
        assert vm.state.model_selection_error_key == "agent_research_model_selection_unknown"

    @pytest.mark.asyncio
    async def test_disabled_connection_is_rejected(self) -> None:
        catalog = _Catalog(
            _snapshot(
                candidates=("conn-a", "conn-off"),
                connections=(_connection("conn-a"), _connection("conn-off", enabled=False)),
            )
        )
        vm = _make_vm(_Service(), model_catalog=catalog)
        await vm.load_model_selection()

        await vm.select_model_connection("conn-off")

        assert vm.state.model_selection == "conn-a"
        assert vm.state.model_selection_error_key == "agent_research_model_selection_disabled"

    @pytest.mark.asyncio
    async def test_child_role_is_rejected_and_keeps_role(self, vm: ResearchObserverViewModel) -> None:
        await vm.load_model_selection()

        vm.select_role("sub")

        assert vm.state.role_selection == "main"
        assert vm.state.role_selection_error_key == "agent_research_role_selection_child_role"

    @pytest.mark.asyncio
    async def test_unknown_role_is_rejected(self, vm: ResearchObserverViewModel) -> None:
        await vm.load_model_selection()

        vm.select_role("ghost")

        assert vm.state.role_selection_error_key == "agent_research_role_selection_unknown"


class TestBudgetEstimate:
    def test_estimated_amount_carries_unit_and_basis(self) -> None:
        connection = _connection(
            billing={
                "mode": "estimate_cost",
                "pricing_unit": "llm_tokens",
                "input_price_per_m": "10",
                "output_price_per_m": "20",
            }
        )

        budget = estimate_budget(text="abcd", connection=connection, token_counter=_token_counter)

        assert budget.state == BUDGET_ESTIMATED
        assert budget.amount_cny == Decimal("0.00004") + Decimal("0.08")
        assert budget.unit_key == "agent_research_budget_unit_per_request"
        assert budget.basis_key == "agent_research_budget_basis"
        assert budget.input_tokens == 4
        assert budget.output_reserve_tokens is not None and budget.output_reserve_tokens > 0

    def test_usage_only_billing_is_unpriced(self) -> None:
        connection = _connection(billing={"mode": "usage", "pricing_unit": "llm_tokens"})

        budget = estimate_budget(text="abcd", connection=connection, token_counter=_token_counter)

        assert budget.state == BUDGET_UNPRICED
        assert budget.amount_cny is None
        assert budget.state_key == "agent_research_budget_unpriced_usage_only"

    def test_encoder_pricing_is_unpriced(self) -> None:
        connection = _connection(
            billing={
                "mode": "estimate_cost",
                "pricing_unit": "encoder_input_tokens",
                "input_price_per_m": "10",
                "output_price_per_m": "20",
            }
        )

        budget = estimate_budget(text="abcd", connection=connection, token_counter=_token_counter)

        assert budget.state == BUDGET_UNPRICED
        assert budget.amount_cny is None

    def test_missing_price_is_unpriced(self) -> None:
        connection = _connection(billing={"mode": "estimate_cost", "pricing_unit": "llm_tokens"})

        budget = estimate_budget(text="abcd", connection=connection, token_counter=_token_counter)

        assert budget.state == BUDGET_UNPRICED
        assert budget.amount_cny is None
        assert budget.state_key == "agent_research_budget_unpriced_price_missing"

    def test_no_connection_is_unknown_not_zero(self) -> None:
        budget = estimate_budget(text="abcd", connection=None, token_counter=_token_counter)

        assert budget.state == BUDGET_UNKNOWN
        assert budget.amount_cny is None
        assert budget.state_key == "agent_research_budget_unknown_no_model"

    def test_empty_text_is_unknown_not_zero(self) -> None:
        connection = _connection(
            billing={
                "mode": "estimate_cost",
                "pricing_unit": "llm_tokens",
                "input_price_per_m": "10",
                "output_price_per_m": "20",
            }
        )

        budget = estimate_budget(text="   ", connection=connection, token_counter=_token_counter)

        assert budget.state == BUDGET_UNKNOWN
        assert budget.amount_cny is None

    @pytest.mark.asyncio
    async def test_vm_estimates_after_load_and_recomputes_on_selection(self) -> None:
        billing = {
            "mode": "estimate_cost",
            "pricing_unit": "llm_tokens",
            "input_price_per_m": "10",
            "output_price_per_m": "20",
        }
        catalog = _Catalog(
            _snapshot(
                connections=(
                    _connection("conn-a", billing=billing),
                    _connection("conn-b", billing={"mode": "usage"}),
                )
            )
        )
        vm = _make_vm(_Service(), model_catalog=catalog, token_counter=_token_counter)
        await vm.load_model_selection()
        vm.set_composer_text("研究一下这些股票的估值")

        await vm.refresh_budget_hint()

        budget = vm.state.budget
        assert budget is not None and budget.state == BUDGET_ESTIMATED
        assert budget.amount_cny is not None and budget.amount_cny > Decimal("0")
        assert budget.input_tokens == len("研究一下这些股票的估值")

        await vm.select_model_connection("conn-b")

        unpriced = vm.state.budget
        assert unpriced is not None and unpriced.state == BUDGET_UNPRICED
        assert unpriced.amount_cny is None


class TestCostProjection:
    @pytest.mark.asyncio
    async def test_round_cost_known_uses_projection_amount(self) -> None:
        service = _Service(projection=_projection(known_cny="0.5", unpriced_attempts=0))
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert service.projection_calls == [("inv-1",)]
        assert vm.state.usage_round is not None
        assert vm.state.usage_round.state == "known"
        assert vm.state.usage_round.known_cny == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_zero_attempts_is_known_zero_not_unknown(self) -> None:
        service = _Service(
            snapshots={"e-1": _execution_snapshot(cost_state="known", attempt_count=0, invocation_ids=())}
        )
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert service.projection_calls == []
        assert vm.state.usage_round is not None
        assert vm.state.usage_round.state == "known"
        assert vm.state.usage_round.known_cny == Decimal("0")

    @pytest.mark.asyncio
    async def test_reserved_cost_is_not_reported_as_amount(self) -> None:
        service = _Service(snapshots={"e-1": _execution_snapshot(cost_state="reserved")})
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.usage_round is not None
        assert vm.state.usage_round.state == "reserved"
        assert vm.state.usage_round.known_cny is None

    @pytest.mark.asyncio
    async def test_unpriced_cost_is_not_reported_as_amount(self) -> None:
        service = _Service(snapshots={"e-1": _execution_snapshot(cost_state="unpriced")})
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.usage_round is not None
        assert vm.state.usage_round.state == "unpriced"
        assert vm.state.usage_round.known_cny is None

    @pytest.mark.asyncio
    async def test_projection_failure_marks_failed_without_amount(self) -> None:
        service = _Service(projection=_projection(read_state="failed", read_error="ledger_corrupt", known_cny=None))
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.usage_round is not None
        assert vm.state.usage_round.state == "failed"
        assert vm.state.usage_round.known_cny is None

    @pytest.mark.asyncio
    async def test_cumulative_sums_known_executions(self) -> None:
        executions = (
            SimpleNamespace(
                execution_id="e-1",
                display_state="succeeded",
                parent_execution_id=None,
                model_attempt_count=1,
                tool_call_count=0,
                active_ms=100,
                goal_coverage="unknown",
                ended_at=None,
            ),
            SimpleNamespace(
                execution_id="e-2",
                display_state="succeeded",
                parent_execution_id=None,
                model_attempt_count=1,
                tool_call_count=0,
                active_ms=100,
                goal_coverage="unknown",
                ended_at=None,
            ),
        )
        service = _Service(
            read=_session_read(executions=executions),
            snapshots={"e-1": _execution_snapshot(), "e-2": _execution_snapshot(invocation_ids=("inv-2",))},
        )
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.usage_cumulative is not None
        assert vm.state.usage_cumulative.state == "known"
        assert vm.state.usage_cumulative.known_cny == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_cumulative_with_unpriced_execution_is_partial(self) -> None:
        executions = (
            SimpleNamespace(
                execution_id="e-1",
                display_state="succeeded",
                parent_execution_id=None,
                model_attempt_count=1,
                tool_call_count=0,
                active_ms=100,
                goal_coverage="unknown",
                ended_at=None,
            ),
            SimpleNamespace(
                execution_id="e-2",
                display_state="succeeded",
                parent_execution_id=None,
                model_attempt_count=1,
                tool_call_count=0,
                active_ms=100,
                goal_coverage="unknown",
                ended_at=None,
            ),
        )
        service = _Service(
            read=_session_read(executions=executions),
            snapshots={
                "e-1": _execution_snapshot(),
                "e-2": _execution_snapshot(cost_state="unpriced", invocation_ids=("inv-2",)),
            },
        )
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.usage_cumulative is not None
        assert vm.state.usage_cumulative.state == "partial"
        assert vm.state.usage_cumulative.known_cny == Decimal("0.5")

    @pytest.mark.asyncio
    async def test_cumulative_truncated_beyond_limit(self) -> None:
        executions = tuple(
            SimpleNamespace(
                execution_id=f"e-{index}",
                display_state="succeeded",
                parent_execution_id=None,
                model_attempt_count=1,
                tool_call_count=0,
                active_ms=10,
                goal_coverage="unknown",
                ended_at=None,
            )
            for index in range(11)
        )
        service = _Service(
            read=_session_read(executions=executions),
            snapshots={f"e-{index}": _execution_snapshot() for index in range(11)},
        )
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        cumulative = vm.state.usage_cumulative
        assert cumulative is not None and cumulative.truncated is True
        assert cumulative.state == "known"
        assert cumulative.known_cny == Decimal("5.0")

    @pytest.mark.asyncio
    async def test_missing_usage_reader_is_reported_not_zero(self) -> None:
        class _NoUsage:
            async def read_session(self, query: Any, *, user_scope: str) -> Any:
                return _session_read()

        vm = _make_vm(_NoUsage(), model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.usage_round is None
        assert vm.state.usage_error_key == "agent_research_usage_unavailable"


class TestDurationSegments:
    def test_five_segments_with_only_work_known(self) -> None:
        segments = build_duration_segments(active_ms=1500)

        assert [segment.segment for segment in segments] == ["queue", "user_wait", "work", "model_wait", "save"]
        work = next(segment for segment in segments if segment.segment == "work")
        assert work.value_ms == 1500 and work.unknown_key is None
        assert work.source_key == "agent_research_duration_source_work"
        for segment in segments:
            if segment.segment != "work":
                assert segment.value_ms is None
                assert segment.unknown_key == DURATION_UNKNOWN_KEY
            assert segment.source_key.startswith("agent_research_duration_source_")

    def test_unknown_active_ms_does_not_fake_zero(self) -> None:
        segments = build_duration_segments(active_ms=None)

        assert all(segment.value_ms is None for segment in segments)
        assert all(segment.unknown_key == DURATION_UNKNOWN_KEY for segment in segments)

    def test_derivable_segments_with_fixed_timestamps(self) -> None:
        snapshot = _execution_snapshot(
            created_at="2026-11-17T02:00:10.000000Z",
            ended_at="2026-11-17T02:05:00.000000Z",
            messages=(
                SimpleNamespace(
                    role=MessageRole.USER,
                    committed_at="2026-11-17T02:00:00.500000Z",
                    execution_id="e-1",
                ),
            ),
            artifacts=(SimpleNamespace(published_at="2026-11-17T02:05:02.250000Z", execution_id="e-1"),),
            decisions=(
                SimpleNamespace(
                    answer_schema="{}",
                    created_at="2026-11-17T02:01:00.000000Z",
                    answer_consumed_at="2026-11-17T02:01:30.000000Z",
                ),
            ),
        )

        values = derive_duration_values(snapshot)

        assert values == {
            "queue": 9500,
            "user_wait": 30000,
            "work": 1500,
            "model_wait": None,
            "save": 2250,
        }

    def test_missing_sources_stay_unknown_without_zero(self) -> None:
        snapshot = _execution_snapshot(
            created_at="2026-11-17T02:00:10.000000Z",
            ended_at=None,
            messages=(),
            artifacts=(),
            decisions=(),
        )

        values = derive_duration_values(snapshot)
        segments = build_duration_segments(
            queue_ms=values["queue"],
            user_wait_ms=values["user_wait"],
            active_ms=values["work"],
            model_wait_ms=values["model_wait"],
            save_ms=values["save"],
        )

        assert values["queue"] is None
        assert values["user_wait"] is None
        assert values["save"] is None
        assert values["model_wait"] is None
        assert values["work"] == 1500
        unknown_segments = {segment.segment for segment in segments if segment.value_ms is None}
        assert unknown_segments == {"queue", "user_wait", "model_wait", "save"}

    def test_unconsumed_decision_keeps_user_wait_unknown(self) -> None:
        snapshot = _execution_snapshot(
            decisions=(
                SimpleNamespace(
                    answer_schema="{}",
                    created_at="2026-11-17T02:01:00.000000Z",
                    answer_consumed_at=None,
                ),
            )
        )

        assert derive_duration_values(snapshot)["user_wait"] is None

    def test_negative_save_delta_is_unknown(self) -> None:
        snapshot = _execution_snapshot(
            ended_at="2026-11-17T02:05:00.000000Z",
            artifacts=(SimpleNamespace(published_at="2026-11-17T02:04:59.000000Z"),),
        )

        assert derive_duration_values(snapshot)["save"] is None

    def test_illegal_intake_timestamp_is_unknown(self) -> None:
        snapshot = _execution_snapshot(
            messages=(SimpleNamespace(role=MessageRole.USER, committed_at="not-a-time"),),
        )

        assert derive_duration_values(snapshot)["queue"] is None

    @pytest.mark.asyncio
    async def test_refresh_usage_populates_active_ms_and_execution(self) -> None:
        service = _Service(snapshots={"e-1": _execution_snapshot(active_ms=2300)})
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        assert vm.state.durations_execution_id == "e-1"
        work = next(segment for segment in vm.state.durations if segment.segment == "work")
        assert work.value_ms == 2300

    @pytest.mark.asyncio
    async def test_refresh_usage_derives_segments_from_snapshot(self) -> None:
        snapshot = _execution_snapshot(
            created_at="2026-11-17T02:00:10.000000Z",
            ended_at="2026-11-17T02:05:00.000000Z",
            messages=(
                SimpleNamespace(
                    role=MessageRole.USER,
                    committed_at="2026-11-17T02:00:00.500000Z",
                    execution_id="e-1",
                ),
            ),
            artifacts=(SimpleNamespace(published_at="2026-11-17T02:05:02.250000Z", execution_id="e-1"),),
            decisions=(
                SimpleNamespace(
                    answer_schema="{}",
                    created_at="2026-11-17T02:01:00.000000Z",
                    answer_consumed_at="2026-11-17T02:01:30.000000Z",
                ),
            ),
        )
        service = _Service(snapshots={"e-1": snapshot})
        vm = _make_vm(service, model_catalog=_Catalog())

        await vm.refresh("s-1")

        values = {segment.segment: segment.value_ms for segment in vm.state.durations}
        assert values == {
            "queue": 9500,
            "user_wait": 30000,
            "work": 1500,
            "model_wait": None,
            "save": 2250,
        }


class TestFrozenSelection:
    @staticmethod
    def _vm_with_intake() -> tuple[ResearchObserverViewModel, _Intake]:
        intake = _Intake()
        vm = _make_vm(_Service(), intake=intake, model_catalog=_Catalog(), token_counter=_token_counter)
        return vm, intake

    @pytest.mark.asyncio
    async def test_first_question_freezes_selection_and_passes_role_refs(self) -> None:
        vm, intake = self._vm_with_intake()
        await vm.load_model_selection()
        vm.set_composer_text("研究一下银行股")

        await vm.send_first_question()

        assert vm.state.model_selection_frozen == "conn-a"
        assert vm.state.role_selection_frozen == "main"
        assert [call["text"] for call in intake.calls] == ["研究一下银行股"]
        role_refs = intake.calls[0]["role_prompt_refs"]
        assert role_refs == (("main", build_role_options()[0].version),)

    @pytest.mark.asyncio
    async def test_change_after_send_does_not_rewrite_frozen_value(self) -> None:
        vm, _intake = self._vm_with_intake()
        await vm.load_model_selection()
        vm.set_composer_text("研究一下银行股")
        await vm.send_first_question()

        await vm.select_model_connection("conn-b")

        assert vm.state.model_selection == "conn-b"
        assert vm.state.model_selection_frozen == "conn-a"

    @pytest.mark.asyncio
    async def test_invalid_role_blocks_send_without_calling_intake(self) -> None:
        vm, intake = self._vm_with_intake()
        await vm.load_model_selection()
        vm.set_composer_text("研究一下银行股")
        # 直接注入越权角色（模拟绕过选择命令的非法状态），发送前必须再次校验。
        vm._set_state(role_selection="sub")

        await vm.send_first_question()

        assert intake.calls == []
        assert vm.state.composer_error_key == "agent_research_role_selection_child_role"


_W12_I18N_KEYS = (
    "agent_research_model_selection_title",
    "agent_research_model_selection_hint",
    "agent_research_model_selection_effective",
    "agent_research_model_selection_fallback",
    "agent_research_model_selection_unknown",
    "agent_research_model_selection_disabled",
    "agent_research_model_selection_route_unavailable",
    "agent_research_model_frozen_fmt",
    "agent_research_model_frozen_fallback_fmt",
    "agent_research_role_label",
    "agent_research_role_selection_child_role",
    "agent_research_role_frozen_fmt",
    "agent_research_budget_estimated_fmt",
    "agent_research_budget_basis",
    "agent_research_budget_unit_per_request",
    "agent_research_budget_unpriced_usage_only",
    "agent_research_budget_unpriced_encoder",
    "agent_research_budget_unpriced_price_missing",
    "agent_research_budget_unknown_no_text",
    "agent_research_budget_unknown_no_model",
    "agent_research_usage_round_fmt",
    "agent_research_usage_cumulative_fmt",
    "agent_research_cost_state_known",
    "agent_research_cost_state_reserved",
    "agent_research_cost_state_unpriced",
    "agent_research_duration_title",
    "agent_research_duration_queue",
    "agent_research_duration_user_wait",
    "agent_research_duration_work",
    "agent_research_duration_model_wait",
    "agent_research_duration_save",
    "agent_research_duration_unknown",
    "agent_research_duration_row_source_fmt",
    "agent_research_duration_source_queue",
    "agent_research_duration_source_user_wait",
    "agent_research_duration_source_work",
    "agent_research_duration_source_model_wait",
    "agent_research_duration_source_save",
)


def test_w12_i18n_keys_present_in_both_locales() -> None:
    """双语齐备：新增 key 必须在 zh_CN 与 en_US 同时存在（不漏一侧）。"""

    root = Path(__file__).resolve().parents[3]
    for locale in ("zh_CN", "en_US"):
        data = json.loads((root / "locales" / locale / "strings.json").read_text(encoding="utf-8"))
        missing = [key for key in _W12_I18N_KEYS if key not in data]
        assert missing == [], f"{locale} 缺少 key: {missing}"
