"""AgentService 只读门面单测（DD §4.1/§4.2，F-01 前置）。

服务层是事实读写的唯一边界：本片只验证「委托 + 关闭语义 + 错误原样传播」，
DAO 事务语义由集成测试覆盖。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    CommandReceipt,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryRead,
    DeliveryState,
    DraftPayload,
    DraftRead,
    DraftReadQuery,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferPhase,
    DraftTransferReceipt,
    DraftTransferSourceKind,
    EventPage,
    EventReadQuery,
    HistoryQuery,
    HistoryRoundSelection,
    Mode,
    Purpose,
    ReadMarkCommand,
    ResearchSnapshot,
    ReviseQueuedRequest,
    SaveAnswerRequest,
    SendableOwner,
    SessionRead,
    SessionReadQuery,
    SessionAction,
    SessionChangeCommand,
    SourceStopCommand,
    SubmissionReceipt,
    derive_draft_transfer_id,
    draft_transfer_payload_digest,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from services.agent_service.service import AgentService, WaitResult

_REF = CommandRef(
    user_scope="u1",
    source_module="ui",
    business_request_id="req-1",
    command_id="client-1",
    expected_revision=0,
)

_RECEIPT = CommandReceipt(
    command=_REF,
    target_id="s1",
    current_revision=3,
    commit_state=CommitState.COMMITTED,
    delivery_state=DeliveryState.NOT_DELIVERED,
    consumption_state=ConsumptionState.NOT_CONSUMED,
    updated_at="2026-11-14T00:00:00.000000Z",
    error_code=None,
    next_action=None,
)

_SUBMISSION_RECEIPT = SubmissionReceipt(
    command=_REF,
    command_record_id="cmd-1",
    session_id="s1",
    execution_id="execution-1",
    workflow_id="wf-1",
    intent_digest="a" * 64,
    commit_state=CommitState.COMMITTED,
    verified_at="2026-11-14T00:00:00.000000Z",
    next_action="await workflow dispatch; query read_command for delivery facts",
)


def _draft_payload(**overrides: Any) -> DraftPayload:
    values: dict[str, Any] = {
        "schema_version": 2,
        "text": "草稿正文",
        "mode": Mode.DISCUSS,
        "model_selection": None,
        "source_refs": (),
        "skill_versions": (),
        "mcp_selection": (),
        "purpose": None,
        "research_date": None,
        "cutoff": None,
        "send_target": None,
        "object_context": None,
        "role_prompt_selection": None,
        "customized_fields": (),
        "defaults_baseline_digest": None,
    }
    values.update(overrides)
    return DraftPayload(**values)


def _draft_command(
    *, ref_revision: int = 3, expected: int = 3, payload: DraftPayload | None = None
) -> DraftSaveCommand:
    return DraftSaveCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="ui",
            business_request_id="req-1",
            command_id="client-1",
            expected_revision=ref_revision,
        ),
        session_id="s1",
        expected_draft_revision=expected,
        payload=_draft_payload() if payload is None else payload,
    )


_DRAFT_RECEIPT = DraftReceipt(
    session_id="s1",
    command_record_id="cmd-1",
    commit_state=CommitState.COMMITTED,
    draft_revision=4,
    verified_at="2026-11-14T00:00:00.000000Z",
    error=None,
)


def _transfer_command(
    *,
    target: str = "s2",
    expected_source: int = 2,
    expected_target: int = 1,
    payload: DraftPayload | None = None,
    payload_digest: str | None = None,
    confirmed: Any = True,
    ref_revision: int | None = None,
) -> DraftTransferCommand:
    payload = _draft_payload() if payload is None else payload
    command = DraftTransferCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="ui",
            business_request_id="req-transfer",
            command_id="client-transfer-1",
            expected_revision=expected_target if ref_revision is None else ref_revision,
        ),
        transfer_id="transfer-pending",
        source_kind=DraftTransferSourceKind.SESSION_DRAFT,
        source_id="s1",
        source_session_id="s1",
        expected_source_draft_revision=expected_source,
        target_session_id=target,
        expected_target_draft_revision=expected_target,
        payload=payload,
        payload_digest=draft_transfer_payload_digest(payload) if payload_digest is None else payload_digest,
        confirmed=confirmed,
    )
    return replace(command, transfer_id=derive_draft_transfer_id(command))


_TRANSFER_RECEIPT = DraftTransferReceipt(
    command_record_id="cmd-transfer-1",
    transfer_id="draft_transfer_abc",
    source_kind=DraftTransferSourceKind.SESSION_DRAFT,
    source_id="s1",
    source_session_id="s1",
    target_session_id="s2",
    expected_source_draft_revision=3,
    expected_target_draft_revision=1,
    payload_digest="a" * 64,
    commit_state=CommitState.COMMITTED,
    phase=DraftTransferPhase.COMPLETED,
    sendable_owner=SendableOwner.TARGET,
    source_revision=4,
    target_revision=2,
    verified_at="2026-11-14T00:00:00.000000Z",
    next_action=None,
)


def _session_command(
    *,
    action: SessionAction = SessionAction.PIN,
    expected: int = 1,
    title: str | None = None,
    ref_revision: int | None = None,
) -> SessionChangeCommand:
    return SessionChangeCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="ui",
            business_request_id="req-1",
            command_id="client-1",
            expected_revision=expected if ref_revision is None else ref_revision,
        ),
        action=action,
        session_id="s1",
        expected_session_revision=expected,
        title=title,
    )


def _request(**overrides: Any) -> AcceptRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "session_id": "s1",
        "source_module": "ui",
        "business_request_id": "req-1",
        "client_command_id": "client-1",
        "expected_revision": 0,
        "node_id": "root",
        "workflow_id": "wf-1",
        "mode": Mode.DISCUSS,
        "purpose": Purpose.CONCEPT,
        "text": "研究三家公司",
        "manifest_payload": json_doc({"schema_version": 1, "text": "研究三家公司"}),
        "source_display_name": "用户消息",
    }
    values.update(overrides)
    return AcceptRequest(**values)


class _FakeDao:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.error: Exception | None = None
        self.session = object()
        self.snapshot = object()
        self.events = object()
        self.observed = object()
        self.receipt = _SUBMISSION_RECEIPT
        self.revision_receipt = object()
        self.answer_receipt = object()
        self.history = object()
        self.stop_receipt = object()
        self.read_mark_receipt = object()
        self.draft_read = object()

    async def accept_request(self, request: AcceptRequest) -> SubmissionReceipt:
        self.calls.append(("accept_request", (request,), {}))
        if self.error is not None:
            raise self.error
        return self.receipt

    async def save_draft(self, command: DraftSaveCommand) -> DraftReceipt:
        self.calls.append(("save_draft", (command,), {}))
        if self.error is not None:
            raise self.error
        return _DRAFT_RECEIPT

    async def change_session(self, command: SessionChangeCommand) -> CommandReceipt:
        self.calls.append(("change_session", (command,), {}))
        if self.error is not None:
            raise self.error
        return _RECEIPT

    async def transfer_draft(self, command: DraftTransferCommand) -> DraftTransferReceipt:
        self.calls.append(("transfer_draft", (command,), {}))
        if self.error is not None:
            raise self.error
        return _TRANSFER_RECEIPT

    async def read_draft_transfer(
        self, ref: CommandRef, *, user_scope: str, target_session_id: str | None = None
    ) -> DraftTransferReceipt:
        self.calls.append(
            ("read_draft_transfer", (ref,), {"user_scope": user_scope, "target_session_id": target_session_id})
        )
        if self.error is not None:
            raise self.error
        return _TRANSFER_RECEIPT

    async def read_draft(self, query: DraftReadQuery, *, user_scope: str) -> DraftRead:
        self.calls.append(("read_draft", (query,), {"user_scope": user_scope}))
        if self.error is not None:
            raise self.error
        return cast(DraftRead, self.draft_read)

    async def read_command(self, ref: CommandRef, *, user_scope: str, target_id: str | None = None) -> CommandReceipt:
        self.calls.append(("read_command", (ref,), {"user_scope": user_scope, "target_id": target_id}))
        if self.error is not None:
            raise self.error
        return _RECEIPT

    async def read_delivery(self, *, user_scope: str, operation_id: str) -> DeliveryRead:
        self.calls.append(("read_delivery", (), {"user_scope": user_scope, "operation_id": operation_id}))
        if self.error is not None:
            raise self.error
        return DeliveryRead(operation_id=operation_id, revision=4, manifest=None)

    async def get_session_read(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead:
        self.calls.append(("get_session_read", (query,), {"user_scope": user_scope}))
        return cast(SessionRead, self.session)

    async def get_execution_snapshot(self, execution_id: str, **kwargs: Any) -> ResearchSnapshot:
        self.calls.append(("get_execution_snapshot", (execution_id,), dict(kwargs)))
        return cast(ResearchSnapshot, self.snapshot)

    async def read_events(self, query: EventReadQuery) -> EventPage:
        self.calls.append(("read_events", (query,), {}))
        return cast(EventPage, self.events)

    def observe_session_events(self, session_id: str, **kwargs: Any) -> Any:
        self.calls.append(("observe_session_events", (session_id,), dict(kwargs)))
        return self.observed

    async def revise_queued(self, request: ReviseQueuedRequest) -> Any:
        self.calls.append(("revise_queued", (request,), {}))
        if self.error is not None:
            raise self.error
        return self.revision_receipt

    async def save_answer(self, request: SaveAnswerRequest) -> Any:
        self.calls.append(("save_answer", (request,), {}))
        if self.error is not None:
            raise self.error
        return self.answer_receipt

    async def search_history(self, query: HistoryQuery, *, user_scope: str) -> Any:
        self.calls.append(("search_history", (query,), {"user_scope": user_scope}))
        if self.error is not None:
            raise self.error
        return self.history

    async def stop_source_use(self, request: SourceStopCommand) -> Any:
        self.calls.append(("stop_source_use", (request,), {}))
        if self.error is not None:
            raise self.error
        return self.stop_receipt

    async def save_read_mark_max(self, command: ReadMarkCommand) -> Any:
        self.calls.append(("save_read_mark_max", (command,), {}))
        if self.error is not None:
            raise self.error
        return self.read_mark_receipt


def _service(fake: _FakeDao) -> AgentService:
    return AgentService(cast(AgentDao, fake))


class TestReadDelegation:
    @pytest.mark.asyncio
    async def test_read_command_passes_caller_scope(self) -> None:
        fake = _FakeDao()
        receipt = await _service(fake).read_command(_REF, user_scope="u1", target_id="s1")
        assert receipt is _RECEIPT
        assert fake.calls == [("read_command", (_REF,), {"user_scope": "u1", "target_id": "s1"})]

    @pytest.mark.asyncio
    async def test_get_snapshot_passes_all_read_bounds(self) -> None:
        fake = _FakeDao()
        snapshot = await _service(fake).get_snapshot(
            "execution_1",
            user_scope="u1",
            limit=10,
            round_id="round_1",
            message_cursor="m:3",
            artifact_cursor="a:2026-11-14T00:00:00.000000Z:art_1:2",
        )
        assert snapshot is fake.snapshot
        call = fake.calls[0]
        assert call[0] == "get_execution_snapshot"
        assert call[1] == ("execution_1",)
        assert call[2] == {
            "user_scope": "u1",
            "limit": 10,
            "round_id": "round_1",
            "message_cursor": "m:3",
            "artifact_cursor": "a:2026-11-14T00:00:00.000000Z:art_1:2",
        }

    @pytest.mark.asyncio
    async def test_read_session_and_events_delegate_verbatim(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        query = SessionReadQuery(session_id="s1")
        assert await service.read_session(query, user_scope="u1") is fake.session
        event_query = EventReadQuery(user_scope="u1", session_id="s1", after_seq=0)
        assert await service.read_events(event_query) is fake.events
        assert [call[0] for call in fake.calls] == ["get_session_read", "read_events"]

    @pytest.mark.asyncio
    async def test_read_draft_passes_query_and_scope(self) -> None:
        fake = _FakeDao()
        query = DraftReadQuery(session_id="s1", draft_revision=2)
        assert await _service(fake).read_draft(query, user_scope="u1") is fake.draft_read
        assert fake.calls == [("read_draft", (query,), {"user_scope": "u1"})]

    @pytest.mark.asyncio
    async def test_read_draft_invalid_query_never_reaches_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).read_draft(DraftReadQuery(session_id=""), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_read_draft_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_draft(DraftReadQuery(session_id="s1"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_read_delivery_passes_operation_identity(self) -> None:
        fake = _FakeDao()
        read = await _service(fake).read_delivery(user_scope="u1", operation_id="op-1")
        assert read.operation_id == "op-1"
        assert read.manifest is None
        assert fake.calls == [("read_delivery", (), {"user_scope": "u1", "operation_id": "op-1"})]

    @pytest.mark.asyncio
    async def test_read_delivery_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_delivery(user_scope="u1", operation_id="op-1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []

    def test_observe_session_delegates_iterator(self) -> None:
        fake = _FakeDao()
        iterator = _service(fake).observe_session("s1", user_scope="u1", after_seq=7)
        assert iterator is fake.observed
        assert fake.calls[0][2] == {"user_scope": "u1", "after_seq": 7, "poll_interval_s": 0.25}


class TestSubmitAuthority:
    """受理入口：可信命令引用与受理输入必须逐字段一致（INV-01）。"""

    @pytest.mark.asyncio
    async def test_matching_ref_delegates_and_returns_receipt(self) -> None:
        fake = _FakeDao()
        receipt = await _service(fake).submit(_request(), command=_REF)
        assert receipt is _SUBMISSION_RECEIPT
        assert [call[0] for call in fake.calls] == ["accept_request"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("user_scope", "u2"),
            ("source_module", "api"),
            ("business_request_id", "req-2"),
            ("command_id", "client-2"),
            ("expected_revision", 3),
        ],
    )
    @pytest.mark.asyncio
    async def test_ref_mismatch_rejected_before_dao(self, field: str, value: Any) -> None:
        fake = _FakeDao()
        values = {
            "user_scope": "u1",
            "source_module": "ui",
            "business_request_id": "req-1",
            "command_id": "client-1",
            "expected_revision": 0,
        }
        values[field] = value
        command = CommandRef(**values)
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).submit(_request(), command=command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert field in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_invalid_request_rejected_as_input_invalid(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).submit(_request(text=""), command=_REF)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.text" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_submit_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.submit(_request(), command=_REF)
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestDraftSaveAuthority:
    """草稿保存入口：边界整份校验，失败不触达 DAO（F-03-1）。"""

    @pytest.mark.asyncio
    async def test_valid_command_delegates(self) -> None:
        fake = _FakeDao()
        assert await _service(fake).save_draft(_draft_command()) is _DRAFT_RECEIPT
        assert [call[0] for call in fake.calls] == ["save_draft"]

    @pytest.mark.asyncio
    async def test_invalid_payload_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        command = _draft_command(payload=_draft_payload(cutoff="2026-13-01"))
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).save_draft(command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.payload.cutoff" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_revision_mismatch_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        command = _draft_command(ref_revision=3, expected=4)
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).save_draft(command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.command.expected_revision" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_save_draft_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.save_draft(_draft_command())
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestSessionChangeAuthority:
    """会话元数据入口：边界校验动作自身字段，失败不触达 DAO（F-03-2）。"""

    @pytest.mark.asyncio
    async def test_valid_command_delegates(self) -> None:
        fake = _FakeDao()
        assert await _service(fake).change_session(_session_command()) is _RECEIPT
        assert [call[0] for call in fake.calls] == ["change_session"]

    @pytest.mark.asyncio
    async def test_revise_without_source_message_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        command = _session_command(action=SessionAction.REVISE, expected=1)
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).change_session(command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.source_message_id" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_create_without_title_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        command = _session_command(action=SessionAction.CREATE, expected=0, title=None)
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).change_session(command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.title" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_revision_mismatch_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        command = _session_command(expected=2, ref_revision=1)
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).change_session(command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.command.expected_revision" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_change_session_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.change_session(_session_command())
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestDraftTransferAuthority:
    """草稿移入入口：边界整份校验，失败不触达 DAO（F-03-3）。"""

    @pytest.mark.asyncio
    async def test_valid_command_delegates(self) -> None:
        fake = _FakeDao()
        assert await _service(fake).transfer_draft(_transfer_command()) is _TRANSFER_RECEIPT
        assert [call[0] for call in fake.calls] == ["transfer_draft"]

    @pytest.mark.asyncio
    async def test_payload_digest_mismatch_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        command = _transfer_command(payload_digest="a" * 64)
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).transfer_draft(command)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.payload_digest" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_unconfirmed_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).transfer_draft(_transfer_command(confirmed=False))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.confirmed" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_self_target_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).transfer_draft(_transfer_command(target="s1"))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.target_session_id" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_revision_mismatch_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).transfer_draft(_transfer_command(ref_revision=9))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.command.expected_revision" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_transfer_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.transfer_draft(_transfer_command())
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_read_draft_transfer_delegates_caller_scope(self) -> None:
        fake = _FakeDao()
        receipt = await _service(fake).read_draft_transfer(_REF, user_scope="u1", target_session_id="s2")
        assert receipt is _TRANSFER_RECEIPT
        assert fake.calls == [("read_draft_transfer", (_REF,), {"user_scope": "u1", "target_session_id": "s2"})]

    @pytest.mark.asyncio
    async def test_read_draft_transfer_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_draft_transfer(_REF, user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestClosedService:
    @pytest.mark.asyncio
    async def test_aclose_is_idempotent_and_blocks_reads(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_command(_REF, user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []

    def test_observe_session_rejected_after_close(self) -> None:
        service = _service(_FakeDao())
        service._closed = True  # 直接置位以覆盖同步入口
        with pytest.raises(AgentServiceError) as excinfo:
            service.observe_session("s1", user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


class TestErrorPropagation:
    @pytest.mark.asyncio
    async def test_dao_service_error_not_rewrapped(self) -> None:
        fake = _FakeDao()
        fake.error = AgentServiceError(AgentErrorCode.INPUT_INVALID, "not found", "u1/client-1")
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).read_command(_REF, user_scope="u1")
        assert excinfo.value is fake.error


def _revise_request(**overrides: Any) -> ReviseQueuedRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "session_id": "s1",
        "execution_id": "execution-1",
        "client_command_id": "client-revise-1",
        "expected_execution_revision": 1,
        "node_id": "root",
        "text": "修订后的问题",
        "manifest_payload": json_doc({"schema_version": 1, "text": "修订后的问题"}),
        "source_display_name": "用户消息",
    }
    values.update(overrides)
    return ReviseQueuedRequest(**values)


def _answer_request(**overrides: Any) -> SaveAnswerRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "decision_id": "decision-1",
        "client_command_id": "client-answer-1",
        "expected_decision_revision": 1,
        "answer_payload": json_doc({"schema_version": 1, "choice": "a"}),
    }
    values.update(overrides)
    return SaveAnswerRequest(**values)


def _history_query(**overrides: Any) -> HistoryQuery:
    values: dict[str, Any] = {
        "scope": "all_saved",
        "round_selection": HistoryRoundSelection(kind="all_rounds"),
        "query_text": "",
        "time_basis": "created",
        "sort": "newest_first",
    }
    values.update(overrides)
    return HistoryQuery(**values)


def _stop_command(**overrides: Any) -> SourceStopCommand:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "source_id": "source-1",
        "source_version": "1",
        "command_id": "client-stop-1",
    }
    values.update(overrides)
    return SourceStopCommand(**values)


def _read_mark(**overrides: Any) -> ReadMarkCommand:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "session_id": "s1",
        "command_id": "client-read-1",
        "message_ids": ("msg-1",),
        "artifact_refs": (),
        "observed_through_seq": None,
        "observation_scope_digest": None,
    }
    values.update(overrides)
    return ReadMarkCommand(**values)


def _snapshot(display_state: str) -> ResearchSnapshot:
    return cast(
        ResearchSnapshot,
        SimpleNamespace(
            identity=SimpleNamespace(session_id="s1"),
            execution=SimpleNamespace(display_state=display_state),
        ),
    )


class TestControlAndAnswer:
    """统一合同写入口：委托原 DAO 事务，重放返回原回执（不伪造新事实）。"""

    @pytest.mark.asyncio
    async def test_control_delegates_revise_queued(self) -> None:
        fake = _FakeDao()
        request = _revise_request()
        assert await _service(fake).control(request) is fake.revision_receipt
        assert fake.calls == [("revise_queued", (request,), {})]

    @pytest.mark.asyncio
    async def test_answer_delegates_save_answer(self) -> None:
        fake = _FakeDao()
        request = _answer_request()
        assert await _service(fake).answer(request) is fake.answer_receipt
        assert fake.calls == [("save_answer", (request,), {})]

    @pytest.mark.asyncio
    async def test_control_replay_returns_original_receipt(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        request = _revise_request()
        first = await service.control(request)
        second = await service.control(request)
        assert first is second is fake.revision_receipt
        assert [call[0] for call in fake.calls] == ["revise_queued", "revise_queued"]

    @pytest.mark.asyncio
    async def test_answer_dao_conflict_propagates_unwrapped(self) -> None:
        fake = _FakeDao()
        fake.error = AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "same key different input", "u1/decision-1")
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).answer(_answer_request())
        assert excinfo.value is fake.error

    @pytest.mark.asyncio
    async def test_control_and_answer_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        for coroutine in (
            service.control(_revise_request()),
            service.answer(_answer_request()),
        ):
            with pytest.raises(AgentServiceError) as excinfo:
                await coroutine
            assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_delegates_with_caller_scope(self) -> None:
        fake = _FakeDao()
        query = _history_query()
        assert await _service(fake).search(query, user_scope="u1") is fake.history
        assert fake.calls == [("search_history", (query,), {"user_scope": "u1"})]

    @pytest.mark.asyncio
    async def test_invalid_scope_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).search(_history_query(scope="other"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.scope" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_missing_user_scope_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).search(_history_query(), user_scope="")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.user_scope" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_search_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.search(_history_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestStopUsingAndMarkRead:
    @pytest.mark.asyncio
    async def test_stop_using_delegates(self) -> None:
        fake = _FakeDao()
        command = _stop_command()
        assert await _service(fake).stop_using(command) is fake.stop_receipt
        assert fake.calls == [("stop_source_use", (command,), {})]

    @pytest.mark.asyncio
    async def test_stop_using_replay_returns_original_receipt(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        command = _stop_command()
        first = await service.stop_using(command)
        second = await service.stop_using(command)
        assert first is second is fake.stop_receipt

    @pytest.mark.asyncio
    async def test_mark_read_delegates(self) -> None:
        fake = _FakeDao()
        command = _read_mark()
        assert await _service(fake).mark_read(command) is fake.read_mark_receipt
        assert fake.calls == [("save_read_mark_max", (command,), {})]

    @pytest.mark.asyncio
    async def test_mark_read_scope_pair_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).mark_read(_read_mark(observed_through_seq=3))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.observed_through_seq" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_stop_using_and_mark_read_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        for coroutine in (
            service.stop_using(_stop_command()),
            service.mark_read(_read_mark()),
        ):
            with pytest.raises(AgentServiceError) as excinfo:
                await coroutine
            assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []


class TestWaitResult:
    """只读等待：收敛/超时如实分列，超时不伪造成果，零持久化。"""

    @pytest.mark.asyncio
    async def test_returns_converged_snapshot(self) -> None:
        fake = _FakeDao()
        snapshot = _snapshot("complete")
        fake.snapshot = snapshot
        fake.events = EventPage(events=(), last_seq=7, has_more=False)
        result = await _service(fake).wait_result("execution-1", user_scope="u1", timeout_s=1, poll_interval_s=0.01)
        assert isinstance(result, WaitResult)
        assert result.converged is True
        assert result.timed_out is False
        assert result.display_state == "complete"
        assert result.snapshot is snapshot
        assert result.events == ()
        event_call = next(call for call in fake.calls if call[0] == "read_events")
        assert event_call[1][0].session_id == "s1"
        assert event_call[1][0].after_seq == 0

    @pytest.mark.asyncio
    async def test_timeout_returns_unconverged_fact_without_write(self) -> None:
        fake = _FakeDao()
        snapshot = _snapshot("running")
        fake.snapshot = snapshot
        fake.events = EventPage(events=(), last_seq=0, has_more=False)
        result = await _service(fake).wait_result("execution-1", user_scope="u1", timeout_s=0.05, poll_interval_s=0.01)
        assert result.converged is False
        assert result.timed_out is True
        assert result.display_state == "running"
        assert result.snapshot is snapshot
        assert {call[0] for call in fake.calls} == {"get_execution_snapshot", "read_events"}

    @pytest.mark.asyncio
    async def test_invalid_timeout_rejected_before_dao(self) -> None:
        fake = _FakeDao()
        with pytest.raises(AgentServiceError) as excinfo:
            await _service(fake).wait_result("execution-1", user_scope="u1", timeout_s=0)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "$.timeout_s" in excinfo.value.sanitized_message
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_wait_result_rejected_after_close(self) -> None:
        fake = _FakeDao()
        service = _service(fake)
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.wait_result("execution-1", user_scope="u1", timeout_s=1)
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert fake.calls == []
