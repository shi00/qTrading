"""ResearchObserverViewModel 单测（检视 P02：共享研究观察层 + 统一决定）。"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import DraftPayload, DraftRead, Mode, Purpose
from ui.viewmodels.research_observer_view_model import (
    UNAVAILABLE_NOT_READY,
    UNAVAILABLE_NOT_WIRED,
    ResearchObserverState,
    ResearchObserverViewModel,
    reset_shared_research_observer,
    resolve_agent_research_service,
    shared_research_observer,
)

pytestmark = pytest.mark.unit


class _FakeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = SimpleNamespace(value=code)


def _decision(
    decision_id: str = "d-1",
    *,
    kind: str = "user_choice",
    revision: int = 3,
    answered: bool = False,
    question_payload: str | None = '{"question": "选哪个候选？"}',
    execution_id: str = "e-1",
    round_id: str = "r-1",
    expires_at: str | None = None,
) -> Any:
    return SimpleNamespace(
        decision_id=decision_id,
        execution_id=execution_id,
        round_id=round_id,
        decision_kind=kind,
        revision=revision,
        created_at="2026-11-17T01:02:03.000000Z",
        expires_at=expires_at,
        question_payload=question_payload,
        answer_command_id="cmd-1" if answered else None,
    )


def _message(
    message_id: str = "m-1",
    *,
    role: str | None = "assistant",
    text: str | None = "研究结论：候选 A",
    execution_id: str | None = "e-1",
) -> Any:
    return SimpleNamespace(
        message_id=message_id,
        role=role,
        body_text=text,
        body_ref=None if text is not None else "chunk-ref",
        execution_id=execution_id,
        occurred_at="2026-11-17T01:30:00.000000Z",
    )


def _artifact(
    artifact_id: str = "a-1",
    *,
    execution_id: str = "e-1",
    round_id: str = "r-1",
    version: int = 1,
    summary: str | None = "候选 A 评分",
    body_text: str | None = None,
) -> Any:
    return SimpleNamespace(
        artifact_id=artifact_id,
        artifact_version=version,
        execution_id=execution_id,
        round_id=round_id,
        round=round_id,
        artifact_kind="report",
        summary=summary,
        body_text=body_text,
        body_ref=None if body_text is not None else "chunk-ref",
        result_payload=None,
        published_at="2026-11-17T01:40:00.000000Z",
    )


def _execution(
    execution_id: str = "e-1",
    *,
    display_state: str = "running",
    parent_execution_id: str | None = None,
    model_attempt_count: int = 1,
    tool_call_count: int = 2,
    active_ms: int = 1500,
    goal_coverage: str = "partial",
) -> Any:
    return SimpleNamespace(
        execution_id=execution_id,
        display_state=display_state,
        parent_execution_id=parent_execution_id,
        model_attempt_count=model_attempt_count,
        tool_call_count=tool_call_count,
        active_ms=active_ms,
        goal_coverage=goal_coverage,
        ended_at=None,
    )


def _session_read(
    *,
    decisions: tuple[Any, ...] = (),
    title: str = "研究 A",
    executions: tuple[Any, ...] | None = None,
    messages: tuple[Any, ...] = (),
    artifacts: tuple[Any, ...] = (),
    messages_has_more: bool = False,
    artifacts_has_more: bool = False,
    draft_revision: int = 0,
) -> Any:
    return SimpleNamespace(
        summary=SimpleNamespace(
            title=title,
            updated_at="2026-11-17T02:00:00.000000Z",
            draft_revision=draft_revision,
        ),
        executions=(_execution(),) if executions is None else executions,
        messages=SimpleNamespace(items=messages, has_more=messages_has_more, next_cursor=None),
        artifacts=SimpleNamespace(items=artifacts, has_more=artifacts_has_more, next_cursor=None),
        decisions=decisions,
    )


_UNSET = object()


def _draft_read(
    *,
    session_id: str = "s-1",
    draft_revision: int = 2,
    payload: DraftPayload | object | None = _UNSET,
    is_empty: bool = False,
    cleaned: bool = False,
    archived: bool = False,
    transferred: bool = False,
) -> DraftRead:
    if payload is _UNSET:
        payload = DraftPayload(
            schema_version=2,
            text="继续编辑的草稿正文",
            mode=Mode.DISCUSS,
            model_selection='{"scenario_id": "agent_research"}',
            source_refs=(),
            skill_versions=(),
            mcp_selection=(),
            purpose=Purpose.CONCEPT,
            research_date="2026-11-13",
            cutoff=None,
            send_target=None,
            object_context=None,
            role_prompt_selection=None,
            customized_fields=("mode",),
            defaults_baseline_digest="b" * 64,
        )
    return DraftRead(
        session_id=session_id,
        draft_revision=draft_revision,
        payload=payload if isinstance(payload, DraftPayload) else None,
        is_empty=is_empty,
        cleaned=cleaned,
        archived=archived,
        transferred=transferred,
        read_at="2026-11-17T04:00:00.000000Z",
    )


def _pending_fact(
    decision: Any,
    *,
    stage: str = "answer",
    session_id: str = "s-1",
    session_title: str | None = "研究 A",
    command_record_id: str | None = None,
    delivery_state: str | None = None,
    consumption_state: str | None = None,
) -> Any:
    return SimpleNamespace(
        session_id=session_id,
        session_title=session_title,
        decision_id=decision.decision_id,
        execution_id=decision.execution_id,
        round_id=decision.round_id,
        decision_kind=decision.decision_kind,
        revision=decision.revision,
        created_at=decision.created_at,
        expires_at=decision.expires_at,
        question_payload=decision.question_payload,
        stage=stage,
        command_record_id=command_record_id,
        delivery_state=delivery_state,
        consumption_state=consumption_state,
    )


def _pending_page(items: tuple[Any, ...], *, truncated: bool = False) -> Any:
    return SimpleNamespace(
        items=items,
        read_moment="2026-11-17T05:00:00.000000Z",
        truncated=truncated,
        answer_count=sum(1 for item in items if item.stage == "answer"),
        delivery_count=sum(1 for item in items if item.stage == "delivery"),
        consumption_count=sum(1 for item in items if item.stage == "consumption"),
    )


class _FakeService:
    def __init__(
        self,
        *,
        hits: tuple[Any, ...] = (SimpleNamespace(session_id="s-1"),),
        read: Any | None = None,
        search_error: Exception | None = None,
        answer_error: Exception | None = None,
        artifact_read: Any | None = None,
        artifact_error: Exception | None = None,
        draft_read: Any | None = None,
        draft_error: Exception | None = None,
        pending_page: Any | None = None,
        pending_error: Exception | None = None,
        deliver_error: Exception | None = None,
        delivery_state: str = "delivered",
    ) -> None:
        self.hits = hits
        self.read = read if read is not None else _session_read()
        self.search_error = search_error
        self.answer_error = answer_error
        self.artifact_read = (
            artifact_read
            if artifact_read is not None
            else SimpleNamespace(
                body_text="完整成果正文",
                next_cursor=None,
                body_available=True,
                unavailable_reason=None,
            )
        )
        self.artifact_error = artifact_error
        self.draft_read = draft_read if draft_read is not None else _draft_read()
        self.draft_error = draft_error
        self.pending_page = pending_page
        self.pending_error = pending_error
        self.deliver_error = deliver_error
        self.delivery_state = delivery_state
        self.search_calls: list[Any] = []
        self.read_calls: list[Any] = []
        self.answer_calls: list[Any] = []
        self.artifact_calls: list[Any] = []
        self.draft_calls: list[Any] = []
        self.pending_calls: list[Any] = []
        self.deliver_calls: list[Any] = []

    async def search(self, query: Any, *, user_scope: str) -> Any:
        self.search_calls.append((query, user_scope))
        if self.search_error is not None:
            raise self.search_error
        return SimpleNamespace(hits=self.hits, read_moment="2026-11-17T03:00:00.000000Z")

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        self.read_calls.append((query, user_scope))
        return self.read

    async def read_artifact(self, query: Any, *, user_scope: str) -> Any:
        self.artifact_calls.append((query, user_scope))
        if self.artifact_error is not None:
            raise self.artifact_error
        return self.artifact_read

    async def answer(self, request: Any) -> Any:
        self.answer_calls.append(request)
        if self.answer_error is not None:
            raise self.answer_error
        return SimpleNamespace(
            command_record_id="cmd-1",
            delivery_state=SimpleNamespace(value="not_delivered"),
        )

    async def read_pending_answers(self, *, user_scope: str, limit: int = 200) -> Any:
        self.pending_calls.append((user_scope, limit))
        if self.pending_error is not None:
            raise self.pending_error
        if self.pending_page is not None:
            return self.pending_page
        decisions = tuple(
            decision
            for decision in getattr(self.read, "decisions", ()) or ()
            if getattr(decision, "answer_command_id", None) is None
        )
        return _pending_page(tuple(_pending_fact(decision) for decision in decisions))

    async def deliver_answer(self, request: Any) -> Any:
        self.deliver_calls.append(request)
        if self.deliver_error is not None:
            raise self.deliver_error
        return SimpleNamespace(
            decision_id="d-1",
            command_record_id=request.command_record_id,
            delivery_state=SimpleNamespace(value=self.delivery_state),
            consumption_state=SimpleNamespace(value="not_consumed"),
            reason=None,
            verified_at="2026-11-17T05:00:00.000000Z",
        )

    async def read_draft(self, query: Any, *, user_scope: str) -> Any:
        self.draft_calls.append((query, user_scope))
        if self.draft_error is not None:
            raise self.draft_error
        return self.draft_read


class _BlockingSearchService(_FakeService):
    """search 阻塞直到被取消（验证取消传播与 loading 释放）。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def search(self, query: Any, *, user_scope: str) -> Any:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _BlockingArtifactService(_FakeService):
    """read_artifact 阻塞直到被取消（验证在途标记释放）。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def read_artifact(self, query: Any, *, user_scope: str) -> Any:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _BlockingDraftService(_FakeService):
    """read_draft 阻塞直到被取消（验证在途标记释放）。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def read_draft(self, query: Any, *, user_scope: str) -> Any:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.fixture(autouse=True)
def _reset_shared() -> Any:
    reset_shared_research_observer()
    yield
    reset_shared_research_observer()


class TestUnavailableState:
    def test_no_service_is_unavailable_and_never_raises(self) -> None:
        vm = ResearchObserverViewModel(None)
        assert vm.state.available is False
        assert vm.state.unavailable_reason_key == UNAVAILABLE_NOT_WIRED

    @pytest.mark.asyncio
    async def test_refresh_without_service_is_noop(self) -> None:
        vm = ResearchObserverViewModel(None)
        await vm.refresh()
        assert vm.state.available is False
        assert vm.state.session_id is None

    @pytest.mark.asyncio
    async def test_submit_without_service_reports_reason(self) -> None:
        vm = ResearchObserverViewModel(None, unavailable_reason_key=UNAVAILABLE_NOT_READY)
        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")
        assert vm.state.error_key == UNAVAILABLE_NOT_READY


class TestResolveService:
    def test_none_wiring(self) -> None:
        assert resolve_agent_research_service(None) == (None, UNAVAILABLE_NOT_WIRED)

    def test_service_but_not_ready(self) -> None:
        wiring = SimpleNamespace(service=object(), ready=False)
        assert resolve_agent_research_service(wiring) == (None, UNAVAILABLE_NOT_READY)

    def test_ready_wiring_returns_service(self) -> None:
        service = _FakeService()
        wiring = SimpleNamespace(service=service, ready=True)
        assert resolve_agent_research_service(wiring) == (service, "")


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_projects_session_and_pending_decisions(self) -> None:
        service = _FakeService(
            read=_session_read(
                decisions=(
                    _decision("d-1"),
                    _decision("d-2", kind="tool_grant", question_payload=None),
                    _decision("d-3", answered=True),
                )
            )
        )
        vm = ResearchObserverViewModel(service)

        await vm.refresh()

        state = vm.state
        assert state.available is True
        assert state.session_id == "s-1"
        assert state.session_title == "研究 A"
        assert state.executions == (("e-1", "running"),)
        assert state.pending_count == 2
        assert [d.decision_id for d in state.decisions] == ["d-1", "d-2"]
        assert state.decisions[0].question_text == "选哪个候选？"
        assert state.decisions[0].answerable is True
        assert state.decisions[1].answerable is False
        assert state.last_updated_at == "2026-11-17T02:00:00.000000Z"
        # 只读查询：不触碰任何控制入口
        assert len(service.search_calls) == 1
        assert len(service.read_calls) == 1

    @pytest.mark.asyncio
    async def test_refresh_without_hits_reports_no_session(self) -> None:
        service = _FakeService(hits=())
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        assert vm.state.session_id is None
        assert vm.state.pending_count == 0
        assert service.read_calls == []

    @pytest.mark.asyncio
    async def test_refresh_failure_reports_code_not_empty_results(self) -> None:
        service = _FakeService(search_error=_FakeError("PERSISTENCE_UNAVAILABLE"))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        assert vm.state.last_error_code == "PERSISTENCE_UNAVAILABLE"
        assert vm.state.loading is False
        assert vm.state.session_id is None

    @pytest.mark.asyncio
    async def test_refresh_cancellation_propagates_and_releases_loading(self) -> None:
        service = _BlockingSearchService()
        vm = ResearchObserverViewModel(service)
        task = asyncio.create_task(vm.refresh())
        await asyncio.wait_for(service.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert exc_info.type is asyncio.CancelledError
        assert vm.state.loading is False, "取消后不得永久停在加载态（否则后续刷新被拦截）"


class TestPreciseSessionNavigation:
    """T-09: 建稿回执驱动精确会话导航，壳层不得以「历史首条」代替。"""

    @pytest.mark.asyncio
    async def test_open_session_reads_exact_receipt_session_even_if_history_is_newer(self) -> None:
        service = _FakeService(hits=(SimpleNamespace(session_id="s-newer"),))
        vm = ResearchObserverViewModel(service)
        await vm.open_session("s-receipt")
        assert vm.state.session_id == "s-receipt"
        assert [call[0].session_id for call in service.read_calls] == ["s-receipt"]

    @pytest.mark.asyncio
    async def test_refresh_keeps_current_session_instead_of_jumping_to_history_first(self) -> None:
        service = _FakeService(hits=(SimpleNamespace(session_id="s-1"),))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        assert vm.state.session_id == "s-1"
        # 历史首条变为更新的会话后，只读刷新仍停留在当前观察会话
        service.hits = (SimpleNamespace(session_id="s-2"),)
        await vm.refresh()
        assert vm.state.session_id == "s-1"
        assert [call[0].session_id for call in service.read_calls] == ["s-1", "s-1"]

    @pytest.mark.asyncio
    async def test_open_session_without_service_reports_unavailable(self) -> None:
        vm = ResearchObserverViewModel(None)
        await vm.open_session("s-1")
        assert vm.state.error_key == UNAVAILABLE_NOT_WIRED


class TestDraftAndSubmit:
    @pytest.mark.asyncio
    async def test_draft_survives_refresh_and_state_updates(self) -> None:
        service = _FakeService(read=_session_read(decisions=(_decision("d-1"),)))
        vm = ResearchObserverViewModel(service)
        vm.set_answer_draft("d-1", "选择第一家")
        await vm.refresh()
        assert vm.state.draft_for("d-1") == "选择第一家"

    @pytest.mark.asyncio
    async def test_submit_rejects_non_answerable_decision(self) -> None:
        service = _FakeService(read=_session_read(decisions=(_decision("d-1", kind="data_consent"),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "同意")
        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")
        assert vm.state.error_key == "agent_research_decision_dedicated"
        assert service.answer_calls == []

    @pytest.mark.asyncio
    async def test_submit_rejects_empty_draft(self) -> None:
        service = _FakeService(read=_session_read(decisions=(_decision("d-1"),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")
        assert vm.state.error_key == "agent_research_empty_draft"
        assert service.answer_calls == []

    @pytest.mark.asyncio
    async def test_submit_goes_through_facade_with_expected_revision(self) -> None:
        service = _FakeService(read=_session_read(decisions=(_decision("d-1", revision=7),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "  选择第一家  ")

        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")

        assert len(service.answer_calls) == 1
        request = service.answer_calls[0]
        assert request.decision_id == "d-1"
        assert request.expected_decision_revision == 7
        assert request.expected_execution_id == "e-1"
        assert request.expected_round_id == "r-1"
        assert request.user_scope == "local-desktop"
        assert '"选择第一家"' in request.answer_payload
        assert vm.state.submitting is False
        assert vm.state.last_answer_delivery_state == "delivered"
        assert request.client_command_id == "ui-answer:d-1"
        # 交付一次后才有确认，草稿此时才清理（保存成功 ≠ 已交付）
        assert len(service.deliver_calls) == 1
        assert service.deliver_calls[0].command_record_id == "cmd-1"
        # 提交成功后刷新当前会话（不再重查历史首条）；草稿按已交付决定清理
        assert len(service.search_calls) == 1
        assert len(service.read_calls) == 2
        assert vm.state.draft_for("d-1") == ""

    @pytest.mark.asyncio
    async def test_submit_failure_keeps_draft_and_reports_code(self) -> None:
        service = _FakeService(
            read=_session_read(decisions=(_decision("d-1"),)),
            answer_error=_FakeError("REVISION_CONFLICT"),
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "选择第一家")
        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")
        assert vm.state.last_error_code == "REVISION_CONFLICT"
        assert vm.state.draft_for("d-1") == "选择第一家"

    @pytest.mark.asyncio
    async def test_submit_rejects_execution_round_mismatch_without_writing(self) -> None:
        """异轮/异执行视图不得按同 decision_id 作答（R2-I04 归属绑定）。"""
        service = _FakeService(read=_session_read(decisions=(_decision("d-1"),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "选择第一家")

        await vm.submit_answer("d-1", execution_id="e-2", round_id="r-1")

        assert vm.state.error_key == "agent_research_decision_stale"
        assert service.answer_calls == []
        assert vm.state.draft_for("d-1") == "选择第一家"

    @pytest.mark.asyncio
    async def test_submit_rejects_expired_decision_without_writing(self) -> None:
        service = _FakeService(
            read=_session_read(decisions=(_decision("d-1", expires_at="2020-01-01T00:00:00.000000Z"),))
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "选择第一家")

        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")

        assert vm.state.error_key == "agent_research_decision_expired"
        assert service.answer_calls == []


class _SessionOnlyService:
    """旧服务桩：未接线跨会话聚合，验证徽标范围如实退回本会话（P1-05）。"""

    def __init__(self, read: Any) -> None:
        self.read = read

    async def search(self, query: Any, *, user_scope: str) -> Any:
        return SimpleNamespace(hits=(SimpleNamespace(session_id="s-1"),), read_moment="2026-11-17T03:00:00.000000Z")

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        return self.read


class TestPendingAggregationAndDeliveryRetry:
    @pytest.mark.asyncio
    async def test_badge_aggregates_all_sessions_instead_of_first_hit(self) -> None:
        """B 会话的新决定进入徽标：不再只由「历史首条命中会话」决定（P1-05）。"""

        current = _session_read(decisions=(_decision("d-1"),))
        other = _decision("d-9", execution_id="e-9", round_id="r-9")
        page = _pending_page(
            (
                _pending_fact(_decision("d-1")),
                _pending_fact(other, session_id="s-9", session_title="研究 B"),
            )
        )
        service = _FakeService(read=current, pending_page=page)
        vm = ResearchObserverViewModel(service)

        await vm.refresh()

        assert vm.state.pending_count == 2
        assert vm.state.pending_scope == "all_sessions"
        assert [item.decision_id for item in vm.state.pending_for_other_sessions()] == ["d-9"]
        assert service.pending_calls[0][0] == "local-desktop"

    @pytest.mark.asyncio
    async def test_badge_includes_delivery_stage_and_marks_truncation(self) -> None:
        page = _pending_page(
            (
                _pending_fact(_decision("d-1")),
                _pending_fact(
                    _decision("d-2", answered=True),
                    stage="delivery",
                    command_record_id="cmd-2",
                    delivery_state="not_delivered",
                ),
                _pending_fact(
                    _decision("d-3", answered=True),
                    stage="consumption",
                    command_record_id="cmd-3",
                    delivery_state="delivered",
                    consumption_state="not_consumed",
                ),
            ),
            truncated=True,
        )
        service = _FakeService(read=_session_read(decisions=(_decision("d-1"),)), pending_page=page)
        vm = ResearchObserverViewModel(service)

        await vm.refresh()

        assert vm.state.pending_count == 2  # 待答复 + 待投递需处理；待消费不计入徽标
        assert vm.state.pending_items_truncated is True
        assert [item.stage for item in vm.state.pending_for("consumption")] == ["consumption"]
        assert vm.state.pending_for("delivery")[0].command_record_id == "cmd-2"

    @pytest.mark.asyncio
    async def test_badge_falls_back_to_session_scope_when_aggregate_not_wired(self) -> None:
        service = _SessionOnlyService(_session_read(decisions=(_decision("d-1"), _decision("d-2"))))
        vm = ResearchObserverViewModel(cast(Any, service))

        await vm.refresh()

        assert vm.state.pending_count == 2
        assert vm.state.pending_scope == "session_only"
        assert [item.stage for item in vm.state.pending_items] == ["answer", "answer"]

    @pytest.mark.asyncio
    async def test_badge_read_failure_is_reported_not_read_as_zero(self) -> None:
        service = _FakeService(
            read=_session_read(decisions=(_decision("d-1"),)),
            pending_error=_FakeError("PERSISTENCE_UNAVAILABLE"),
        )
        vm = ResearchObserverViewModel(service)

        await vm.refresh()

        assert vm.state.pending_scope == "session_only"
        assert vm.state.pending_count == 1
        assert vm.state.last_error_code == "PERSISTENCE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_repeated_submit_reuses_same_command_key(self) -> None:
        service = _FakeService(
            read=_session_read(decisions=(_decision("d-1"),)),
            answer_error=_FakeError("PERSISTENCE_UNAVAILABLE"),
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "选择第一家")
        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")
        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")

        assert [call.client_command_id for call in service.answer_calls] == [
            "ui-answer:d-1",
            "ui-answer:d-1",
        ]

    @pytest.mark.asyncio
    async def test_delivery_failure_keeps_draft_and_reports_reason(self) -> None:
        service = _FakeService(
            read=_session_read(decisions=(_decision("d-1"),)),
            deliver_error=_FakeError("PERSISTENCE_UNAVAILABLE"),
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "选择第一家")

        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")

        assert vm.state.last_answer_delivery_state == "not_delivered"
        assert vm.state.last_answer_delivery_reason == "PERSISTENCE_UNAVAILABLE"
        assert vm.state.draft_for("d-1") == "选择第一家"

    @pytest.mark.asyncio
    async def test_delivery_not_wired_is_fail_closed(self) -> None:
        class _NoDeliveryService:
            """已保存但组合根未注入发送器：交付入口不存在（fail-closed）。"""

            def __init__(self, read: Any) -> None:
                self.read = read

            async def search(self, query: Any, *, user_scope: str) -> Any:
                return SimpleNamespace(hits=(SimpleNamespace(session_id="s-1"),), read_moment="")

            async def read_session(self, query: Any, *, user_scope: str) -> Any:
                return self.read

            async def answer(self, request: Any) -> Any:
                return SimpleNamespace(command_record_id="cmd-1", delivery_state=None)

        service = _NoDeliveryService(_session_read(decisions=(_decision("d-1"),)))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh()
        vm.set_answer_draft("d-1", "选择第一家")

        await vm.submit_answer("d-1", execution_id="e-1", round_id="r-1")

        assert vm.state.last_answer_delivery_state == "not_delivered"
        assert vm.state.last_answer_delivery_reason == "delivery_not_wired"
        assert vm.state.draft_for("d-1") == "选择第一家"

    @pytest.mark.asyncio
    async def test_retry_delivery_uses_original_command_without_new_answer(self) -> None:
        other = _decision("d-2", answered=True)
        page = _pending_page(
            (
                _pending_fact(
                    other,
                    stage="delivery",
                    command_record_id="cmd-2",
                    delivery_state="not_delivered",
                ),
            )
        )
        service = _FakeService(read=_session_read(), pending_page=page)
        vm = ResearchObserverViewModel(service)
        await vm.refresh()

        await vm.retry_delivery("d-2")

        assert service.answer_calls == []
        assert [call.command_record_id for call in service.deliver_calls] == ["cmd-2"]
        assert vm.state.last_answer_delivery_state == "delivered"

    @pytest.mark.asyncio
    async def test_retry_delivery_refuses_unknown_state(self) -> None:
        page = _pending_page(
            (
                _pending_fact(
                    _decision("d-2", answered=True),
                    stage="delivery",
                    command_record_id="cmd-2",
                    delivery_state="delivery_unknown",
                ),
            )
        )
        service = _FakeService(read=_session_read(), pending_page=page)
        vm = ResearchObserverViewModel(service)
        await vm.refresh()

        await vm.retry_delivery("d-2")

        assert vm.state.error_key == "agent_research_delivery_unknown"
        assert service.deliver_calls == []


class TestMainSessionReadOnlyProjection:
    @pytest.mark.asyncio
    async def test_messages_and_artifacts_are_projected_honestly(self) -> None:
        service = _FakeService(
            read=_session_read(
                messages=(
                    _message("m-1", text="短正文", execution_id="e-1"),
                    _message("m-2", text=None, role="tool", execution_id="e-2"),
                ),
                artifacts=(
                    _artifact("a-1", summary="候选 A"),
                    _artifact("a-2", summary=None, body_text="内联正文"),
                ),
                messages_has_more=True,
                artifacts_has_more=True,
                draft_revision=4,
            )
        )
        vm = ResearchObserverViewModel(service)

        await vm.refresh()

        state = vm.state
        assert [m.message_id for m in state.messages] == ["m-1", "m-2"]
        assert state.messages[0].body_text == "短正文"
        assert state.messages[0].body_available is True
        # body_ref 未携带正文：如实标记不可读，不伪造空正文（R21）
        assert state.messages[1].body_text is None
        assert state.messages[1].body_available is False
        assert [a.artifact_id for a in state.artifacts] == ["a-1", "a-2"]
        assert state.artifacts[1].body_text == "内联正文"
        assert state.messages_has_more is True
        assert state.artifacts_has_more is True
        assert state.session_draft_revision == 4

    @pytest.mark.asyncio
    async def test_missing_messages_marker_does_not_claim_empty_body(self) -> None:
        service = _FakeService(read=_session_read(messages=(_message("m-9", text=None),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        assert vm.state.messages[0].body_available is False
        assert vm.state.messages[0].body_text is None


class TestExecutionTree:
    @pytest.mark.asyncio
    async def test_tree_builds_parent_child_depth_and_orphans_as_roots(self) -> None:
        service = _FakeService(
            read=_session_read(
                executions=(
                    _execution("e-1", display_state="running"),
                    _execution("e-2", parent_execution_id="e-1", display_state="complete"),
                    _execution("e-3", parent_execution_id="e-2", display_state="queued"),
                    _execution("e-orphan", parent_execution_id="e-missing"),
                )
            )
        )
        vm = ResearchObserverViewModel(service)

        await vm.refresh()

        nodes = vm.state.execution_nodes
        assert [(n.execution_id, n.depth) for n in nodes] == [
            ("e-1", 0),
            ("e-2", 1),
            ("e-3", 2),
            ("e-orphan", 0),
        ]
        assert nodes[0].has_children is True
        assert nodes[1].has_children is True
        assert nodes[2].has_children is False
        assert nodes[0].display_state == "running"
        assert nodes[0].tool_call_count == 2
        assert nodes[0].active_ms == 1500

    @pytest.mark.asyncio
    async def test_toggle_execution_collapses_and_restores_without_service_calls(self) -> None:
        service = _FakeService(
            read=_session_read(
                executions=(
                    _execution("e-1"),
                    _execution("e-2", parent_execution_id="e-1"),
                )
            )
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        search_calls = len(service.search_calls)

        vm.toggle_execution("e-1")
        assert [n.execution_id for n in vm.state.execution_nodes] == ["e-1"]
        assert "e-1" in vm.state.collapsed_execution_ids

        vm.toggle_execution("e-1")
        assert [n.execution_id for n in vm.state.execution_nodes] == ["e-1", "e-2"]
        assert vm.state.collapsed_execution_ids == ()
        assert len(service.search_calls) == search_calls, "展开/收起只读，不重新查询、不控制执行"

    @pytest.mark.asyncio
    async def test_toggle_unknown_execution_is_ignored(self) -> None:
        vm = ResearchObserverViewModel(_FakeService())
        await vm.refresh()
        vm.toggle_execution("e-missing")
        assert vm.state.collapsed_execution_ids == ()


class TestObservationPosition:
    @pytest.mark.asyncio
    async def test_select_execution_filters_messages_artifacts_and_tree(self) -> None:
        service = _FakeService(
            read=_session_read(
                executions=(
                    _execution("e-1"),
                    _execution("e-2", parent_execution_id="e-1"),
                    _execution("e-9"),
                ),
                messages=(
                    _message("m-1", execution_id="e-1"),
                    _message("m-2", execution_id="e-2"),
                    _message("m-9", execution_id="e-9"),
                ),
                artifacts=(
                    _artifact("a-1", execution_id="e-1"),
                    _artifact("a-2", execution_id="e-2"),
                    _artifact("a-9", execution_id="e-9"),
                ),
            )
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()

        vm.select_execution("e-2")

        state = vm.state
        assert state.selected_execution_id == "e-2"
        assert [m.message_id for m in state.messages] == ["m-2"]
        assert [a.artifact_id for a in state.artifacts] == ["a-2"]
        assert [(n.execution_id, n.depth) for n in state.execution_nodes] == [("e-2", 0)]

        vm.select_execution(None)
        assert vm.state.selected_execution_id is None
        assert [m.message_id for m in vm.state.messages] == ["m-1", "m-2", "m-9"]

    @pytest.mark.asyncio
    async def test_select_unknown_execution_falls_back_to_session_view(self) -> None:
        vm = ResearchObserverViewModel(_FakeService())
        await vm.refresh()
        vm.select_execution("e-missing")
        assert vm.state.selected_execution_id is None

    @pytest.mark.asyncio
    async def test_selection_draft_and_collapsed_state_survive_refresh(self) -> None:
        service = _FakeService(
            read=_session_read(
                decisions=(_decision("d-1"),),
                executions=(
                    _execution("e-1"),
                    _execution("e-2", parent_execution_id="e-1"),
                ),
                messages=(_message("m-1", execution_id="e-1"), _message("m-2", execution_id="e-2")),
            )
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "保留的草稿")
        vm.toggle_execution("e-1")
        vm.select_execution("e-2")

        await vm.refresh()

        assert vm.state.selected_execution_id == "e-2"
        assert vm.state.collapsed_execution_ids == ("e-1",)
        assert vm.state.draft_for("d-1") == "保留的草稿"


class TestArtifactBodyRead:
    @pytest.mark.asyncio
    async def test_load_artifact_uses_read_only_query_and_caches_body(self) -> None:
        service = _FakeService()
        vm = ResearchObserverViewModel(service)

        await vm.load_artifact(artifact_id="a-1", artifact_version=3, execution_id="e-1", round_id="r-1")

        assert len(service.artifact_calls) == 1
        query, user_scope = service.artifact_calls[0]
        assert query.artifact_id == "a-1"
        assert query.artifact_version == 3
        assert query.execution_id == "e-1"
        assert query.round_id == "r-1"
        assert user_scope == "local-desktop"
        body = vm.state.artifact_body_for("a-1", 3)
        assert body is not None
        assert body.body_text == "完整成果正文"
        assert body.truncated is False
        # 只读：答复写入口一次都不应被触发
        assert service.answer_calls == []

        await vm.load_artifact(artifact_id="a-1", artifact_version=3, execution_id="e-1", round_id="r-1")
        assert len(service.artifact_calls) == 1, "已载入正文不重复读取"

    @pytest.mark.asyncio
    async def test_load_artifact_reports_unavailable_body_and_truncation(self) -> None:
        unavailable_read = SimpleNamespace(
            body_text=None,
            next_cursor=None,
            body_available=False,
            unavailable_reason="body_not_saved",
        )
        service = _FakeService(artifact_read=unavailable_read)
        vm = ResearchObserverViewModel(service)

        await vm.load_artifact(artifact_id="a-2", artifact_version=1, execution_id="e-1", round_id="r-1")

        body = vm.state.artifact_body_for("a-2", 1)
        assert body is not None
        assert body.body_available is False
        assert body.unavailable_reason == "body_not_saved"
        assert body.body_text is None

    @pytest.mark.asyncio
    async def test_load_artifact_failure_reports_code_and_clears_pending(self) -> None:
        service = _FakeService(artifact_error=_FakeError("PERSISTENCE_UNAVAILABLE"))
        vm = ResearchObserverViewModel(service)

        await vm.load_artifact(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1")

        assert vm.state.last_error_code == "PERSISTENCE_UNAVAILABLE"
        assert vm.state.artifact_body_pending_keys == ()
        assert vm.state.artifact_body_for("a-1", 1) is None

    @pytest.mark.asyncio
    async def test_loaded_body_survives_same_session_refresh_but_not_session_switch(self) -> None:
        service = _FakeService()
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        await vm.load_artifact("a-1", 1, "e-1", "r-1")
        assert [(body.key, body.body_text) for body in vm.state.artifact_bodies] == [("a-1:1", "完整成果正文")]

        await vm.refresh()
        assert [(body.key, body.body_text) for body in vm.state.artifact_bodies] == [("a-1:1", "完整成果正文")], (
            "同会话刷新应保留已读正文（切页继续阅读）"
        )

        service.hits = (SimpleNamespace(session_id="s-2"),)
        await vm.open_session("s-2")
        assert vm.state.artifact_bodies == (), "换会话不得复用上一会话正文缓存"

    @pytest.mark.asyncio
    async def test_load_artifact_cancellation_releases_pending_marker(self) -> None:
        service = _BlockingArtifactService()
        vm = ResearchObserverViewModel(service)
        task = asyncio.create_task(vm.load_artifact("a-1", 1, "e-1", "r-1"))
        await asyncio.wait_for(service.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert exc_info.type is asyncio.CancelledError
        assert vm.state.artifact_body_pending_keys == ()

    @pytest.mark.asyncio
    async def test_load_artifact_without_service_is_noop(self) -> None:
        vm = ResearchObserverViewModel(None)
        await vm.load_artifact(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1")
        assert vm.state.available is False
        assert vm.state.artifact_body_for("a-1", 1) is None


class TestSessionDraftRead:
    """R2-I23：草稿正文读回只读投影（不发送、不创建研究）。"""

    @pytest.mark.asyncio
    async def test_load_draft_projects_body_and_selection(self) -> None:
        service = _FakeService(read=_session_read(draft_revision=2))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()

        await vm.load_draft("s-1", 2)

        query, user_scope = service.draft_calls[0]
        assert (query.session_id, query.draft_revision) == ("s-1", 2)
        assert user_scope == "local-desktop"
        draft = vm.state.session_draft
        assert draft is not None
        assert draft.text == "继续编辑的草稿正文"
        assert draft.mode == "discuss"
        assert draft.purpose == "concept"
        assert draft.customized_fields == ("mode",)
        assert draft.draft_revision == 2
        assert vm.state.draft_pending is False
        # 只读：答复/保存类写入口一次都不应被触发
        assert service.answer_calls == []

    @pytest.mark.asyncio
    async def test_revision_conflict_reports_code_and_clears_previous_body(self) -> None:
        service = _FakeService(read=_session_read(draft_revision=2))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        await vm.load_draft("s-1", 2)
        assert vm.state.session_draft is not None

        service.draft_error = _FakeError("REVISION_CONFLICT")
        await vm.load_draft("s-1", 1)

        assert vm.state.draft_error_code == "REVISION_CONFLICT"
        assert vm.state.session_draft is None, "旧稿正文不得继续显示为当前可恢复内容"
        assert vm.state.draft_pending is False

    @pytest.mark.asyncio
    async def test_refresh_drops_loaded_draft_when_revision_advances(self) -> None:
        service = _FakeService(read=_session_read(draft_revision=2))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        await vm.load_draft("s-1", 2)
        assert vm.state.session_draft is not None

        service.read = _session_read(draft_revision=3)
        await vm.refresh()

        assert vm.state.session_draft is None, "稿版已推进，旧稿不得继续显示"
        assert vm.state.session_draft_revision == 3

    @pytest.mark.asyncio
    async def test_refresh_drops_loaded_draft_when_session_switches(self) -> None:
        service = _FakeService(read=_session_read(draft_revision=2))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        await vm.load_draft("s-1", 2)

        service.hits = (SimpleNamespace(session_id="s-2"),)
        service.read = _session_read(draft_revision=2)
        await vm.open_session("s-2")

        assert vm.state.session_draft is None

    @pytest.mark.asyncio
    async def test_cleaned_draft_is_exposed_not_as_empty(self) -> None:
        service = _FakeService(
            read=_session_read(draft_revision=4),
            draft_read=_draft_read(payload=None, draft_revision=4, cleaned=True),
        )
        vm = ResearchObserverViewModel(service)
        await vm.refresh()

        await vm.load_draft("s-1", 4)

        draft = vm.state.session_draft
        assert draft is not None
        assert draft.cleaned is True
        assert draft.text is None
        assert draft.is_empty is False

    @pytest.mark.asyncio
    async def test_load_draft_cancellation_releases_pending_marker(self) -> None:
        service = _BlockingDraftService()
        vm = ResearchObserverViewModel(service)
        task = asyncio.create_task(vm.load_draft("s-1", 2))
        await asyncio.wait_for(service.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert exc_info.type is asyncio.CancelledError
        assert vm.state.draft_pending is False


class TestSharedInstance:
    def test_same_service_shares_one_instance(self) -> None:
        service = _FakeService()
        first = shared_research_observer(service)
        second = shared_research_observer(service)
        assert first is second

    def test_different_service_recreates(self) -> None:
        first = shared_research_observer(_FakeService())
        second = shared_research_observer(_FakeService())
        assert first is not second
        assert first.disposed is True

    def test_reset_disposes_shared_instance(self) -> None:
        vm = shared_research_observer(_FakeService())
        reset_shared_research_observer()
        assert vm.disposed is True

    def test_state_reason_key_defaults(self) -> None:
        assert ResearchObserverState().unavailable_reason_key == UNAVAILABLE_NOT_WIRED


class _FakeIntake:
    def __init__(
        self,
        *,
        dispatch: Any | None = None,
        error: Exception | None = None,
    ) -> None:
        self.dispatch = dispatch
        self.error = error
        self.question_calls: list[tuple[str, Any, str | None]] = []
        self.plan_calls: list[tuple[Any, str, int]] = []

    async def start_question(
        self,
        text: str,
        *,
        mode: Any,
        session_id: str | None = None,
        session_title: str | None = None,
        expected_revision: int | None = None,
    ) -> Any:
        self.question_calls.append((text, mode, session_id, expected_revision))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(receipt=object(), dispatch=self.dispatch, plan_reference=None)

    async def start_plan(self, reference: Any, *, session_id: str, expected_revision: int) -> Any:
        self.plan_calls.append((reference, session_id, expected_revision))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(receipt=object(), dispatch=self.dispatch, plan_reference=reference)


class _PlanService(_FakeService):
    def __init__(self, proposal: Any, *, error: Exception | None = None) -> None:
        super().__init__()
        self.proposal = proposal
        self.plan_error = error
        self.plan_calls: list[tuple[str, str, int]] = []

    async def read_plan(self, *, user_scope: str, execution_id: str, plan_revision: int) -> Any:
        self.plan_calls.append((user_scope, execution_id, plan_revision))
        if self.plan_error is not None:
            raise self.plan_error
        return self.proposal


class TestComposer:
    @pytest.mark.asyncio
    async def test_send_without_intake_reports_unavailable(self) -> None:
        service = _FakeService()
        vm = ResearchObserverViewModel(service)

        assert vm.state.composer_available is False
        vm.set_composer_text("研究问题")
        await vm.send_first_question()

        assert vm.state.composer_error_key == UNAVAILABLE_NOT_WIRED
        assert vm.state.composer_text == "研究问题"

    @pytest.mark.asyncio
    async def test_send_first_question_uses_selected_mode_and_clears_text(self) -> None:
        service = _FakeService()
        intake = _FakeIntake(dispatch=SimpleNamespace(submitted=False, reason="mode_research_disabled"))
        vm = ResearchObserverViewModel(service, intake=intake)
        vm.set_composer_text("  研究银行股  ")
        vm.set_composer_mode("discuss")

        await vm.send_first_question()

        assert intake.question_calls == [("研究银行股", Mode.DISCUSS, None, 0)]
        assert vm.state.composer_text == ""
        assert vm.state.composer_pending is False
        assert vm.state.last_dispatch_reason == "mode_research_disabled"

    @pytest.mark.asyncio
    async def test_empty_question_is_not_submitted(self) -> None:
        intake = _FakeIntake()
        vm = ResearchObserverViewModel(_FakeService(), intake=intake)
        vm.set_composer_text("   ")

        await vm.send_first_question()

        assert intake.question_calls == []
        assert vm.state.composer_error_key == "agent_research_empty_draft"

    def test_unknown_mode_is_ignored(self) -> None:
        vm = ResearchObserverViewModel(_FakeService(), intake=_FakeIntake())

        vm.set_composer_mode("autopilot")

        assert vm.state.composer_mode == "execute"

    @pytest.mark.asyncio
    async def test_start_plan_requires_visible_reference(self) -> None:
        intake = _FakeIntake()
        vm = ResearchObserverViewModel(_FakeService(), intake=intake)

        await vm.start_confirmed_plan()

        assert intake.plan_calls == []
        assert vm.state.composer_error_key == "agent_research_plan_missing"

    @pytest.mark.asyncio
    async def test_start_plan_uses_visible_reference_and_revision(self) -> None:
        intake = _FakeIntake(dispatch=SimpleNamespace(submitted=True, reason=None))
        vm = ResearchObserverViewModel(_FakeService(), intake=intake)
        vm._set_state(plan_reference=("execution-0", 2), session_id="s-1", session_draft_revision=4)

        await vm.start_confirmed_plan()

        reference, session_id, revision = intake.plan_calls[0]
        assert (reference.execution_id, reference.plan_revision) == ("execution-0", 2)
        assert (session_id, revision) == ("s-1", 4)
        assert vm.state.last_dispatch_reason is None

    @pytest.mark.asyncio
    async def test_load_plan_preview_projects_steps(self) -> None:
        proposal = SimpleNamespace(
            items=(
                SimpleNamespace(id="s1", content="步骤一", depends_on=()),
                SimpleNamespace(id="s2", content="步骤二", depends_on=("s1",)),
            )
        )
        service = _PlanService(proposal)
        vm = ResearchObserverViewModel(service)

        await vm.load_plan_preview("execution-0", 1)

        assert [(step.step_id, step.content) for step in vm.state.plan_steps] == [
            ("s1", "步骤一"),
            ("s2", "步骤二"),
        ]
        assert vm.state.plan_reference == ("execution-0", 1)

    @pytest.mark.asyncio
    async def test_load_plan_preview_keeps_error_and_empty_steps(self) -> None:
        service = _PlanService(None, error=_FakeError("PERSISTENCE_UNAVAILABLE"))
        vm = ResearchObserverViewModel(service)

        await vm.load_plan_preview("execution-0", 1)

        assert vm.state.plan_steps == ()
        assert vm.state.plan_reference is None
        assert vm.state.plan_error_key == "PERSISTENCE_UNAVAILABLE"


class _MainTurnIntake:
    def __init__(self, *, answer: str = "直答", plan_reference: Any = None) -> None:
        self._answer = answer
        self._plan_reference = plan_reference

    async def start_question(self, text: str, **kwargs: Any) -> Any:
        return SimpleNamespace(
            receipt=object(),
            dispatch=SimpleNamespace(submitted=False, reason="mode_research_disabled"),
            plan_reference=None,
            main_turn=SimpleNamespace(
                outcome=SimpleNamespace(result=SimpleNamespace(body_text=self._answer)),
                plan_reference=self._plan_reference,
            ),
        )

    async def start_plan(self, reference: Any, **kwargs: Any) -> Any:
        raise AssertionError("not used")


class _MainTurnComposerTests:
    @pytest.mark.asyncio
    async def test_send_surfaces_main_turn_answer(self) -> None:
        vm = ResearchObserverViewModel(_FakeService(), intake=_MainTurnIntake())
        vm.set_composer_text("解释量价背离")

        await vm.send_first_question()

        assert vm.state.last_answer_text == "直答"
        assert vm.state.last_dispatch_reason is None
        assert vm.state.composer_text == ""

    @pytest.mark.asyncio
    async def test_send_surfaces_plan_reference_from_main_turn(self) -> None:
        reference = SimpleNamespace(execution_id="execution-0", plan_revision=2)
        vm = ResearchObserverViewModel(_FakeService(), intake=_MainTurnIntake(plan_reference=reference))
        vm.set_composer_text("出计划")
        vm.set_composer_mode("plan")

        await vm.send_first_question()

        assert vm.state.plan_reference == ("execution-0", 2)
