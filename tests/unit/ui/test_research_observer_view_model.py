"""ResearchObserverViewModel 单测（检视 P02：共享研究观察层 + 统一决定）。"""

from types import SimpleNamespace
from typing import Any

import pytest

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
) -> Any:
    return SimpleNamespace(
        decision_id=decision_id,
        decision_kind=kind,
        revision=revision,
        created_at="2026-11-17T01:02:03.000000Z",
        expires_at=None,
        question_payload=question_payload,
        answer_command_id="cmd-1" if answered else None,
    )


def _session_read(*, decisions: tuple[Any, ...] = (), title: str = "研究 A") -> Any:
    return SimpleNamespace(
        summary=SimpleNamespace(title=title, updated_at="2026-11-17T02:00:00.000000Z"),
        executions=(SimpleNamespace(execution_id="e-1", display_state="running"),),
        decisions=decisions,
    )


class _FakeService:
    def __init__(
        self,
        *,
        hits: tuple[Any, ...] = (SimpleNamespace(session_id="s-1"),),
        read: Any | None = None,
        search_error: Exception | None = None,
        answer_error: Exception | None = None,
    ) -> None:
        self.hits = hits
        self.read = read if read is not None else _session_read()
        self.search_error = search_error
        self.answer_error = answer_error
        self.search_calls: list[Any] = []
        self.read_calls: list[Any] = []
        self.answer_calls: list[Any] = []

    async def search(self, query: Any, *, user_scope: str) -> Any:
        self.search_calls.append((query, user_scope))
        if self.search_error is not None:
            raise self.search_error
        return SimpleNamespace(hits=self.hits, read_moment="2026-11-17T03:00:00.000000Z")

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        self.read_calls.append((query, user_scope))
        return self.read

    async def answer(self, request: Any) -> Any:
        self.answer_calls.append(request)
        if self.answer_error is not None:
            raise self.answer_error
        return SimpleNamespace(delivery_state=SimpleNamespace(value="not_delivered"))


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
        await vm.submit_answer("d-1")
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
        await vm.submit_answer("d-1")
        assert vm.state.error_key == "agent_research_decision_dedicated"
        assert service.answer_calls == []

    @pytest.mark.asyncio
    async def test_submit_rejects_empty_draft(self) -> None:
        service = _FakeService(read=_session_read(decisions=(_decision("d-1"),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        await vm.submit_answer("d-1")
        assert vm.state.error_key == "agent_research_empty_draft"
        assert service.answer_calls == []

    @pytest.mark.asyncio
    async def test_submit_goes_through_facade_with_expected_revision(self) -> None:
        service = _FakeService(read=_session_read(decisions=(_decision("d-1", revision=7),)))
        vm = ResearchObserverViewModel(service)
        await vm.refresh()
        vm.set_answer_draft("d-1", "  选择第一家  ")

        await vm.submit_answer("d-1")

        assert len(service.answer_calls) == 1
        request = service.answer_calls[0]
        assert request.decision_id == "d-1"
        assert request.expected_decision_revision == 7
        assert request.user_scope == "local-desktop"
        assert '"选择第一家"' in request.answer_payload
        assert vm.state.submitting is False
        assert vm.state.last_answer_delivery_state == "not_delivered"
        # 提交成功后刷新；草稿按已提交决定清理
        assert len(service.search_calls) == 2
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
        await vm.submit_answer("d-1")
        assert vm.state.last_error_code == "REVISION_CONFLICT"
        assert vm.state.draft_for("d-1") == "选择第一家"


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
