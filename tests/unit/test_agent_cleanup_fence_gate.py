"""清理 fence 对象门契约测试（检视 D08 / D28 恢复子项）。

``commit_cleanup_fence`` 写下的作业事实是唯一门禁源：清理后保留会话不得再接受
新正文（save_draft / accept_request / transfer_draft）、新引用（草稿来源边 /
资源票据）与恢复写入（grant_recovery_epoch）。已存在的旧事实重放保持原语义。

真实行锁竞争由 sidecar PG 集成测试覆盖；本文件用 capture conn 断言门禁位置、
``FOR SHARE`` 行锁与拒绝错误码。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    CLEANUP_TOMBSTONE_DIGEST,
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    CommandReceipt,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryManifest,
    DeliveryState,
    DeliveryTarget,
    DraftPayload,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferSourceKind,
    EventKind,
    Mode,
    PlanItemSnapshot,
    PlanProposal,
    Purpose,
    RecordEventRequest,
    RecoveryGrantRequest,
    ResourceClaimRequest,
    ResourceSlotKind,
    SaveAnswerRequest,
    SessionAction,
    SessionChangeCommand,
    SourceClaimBinding,
    SourceClaimRequest,
    SourceKind,
    SourceVersionRef,
    derive_draft_transfer_id,
    derive_event_identity,
    draft_save_input_digest,
    draft_transfer_payload_digest,
    encode_command_receipt,
    encode_delivery_manifest,
    encode_draft_receipt,
    json_doc,
    normalize_plan_proposal,
    plan_proposal_digest,
    session_change_input_digest,
    sha256_hex,
    stable_id,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_WIRE_TIME = "2026-06-01T12:00:00.000000Z"


def _fenced(session_ids: list[str] | None = None, asset_keys: list[list[str]] | None = None) -> dict[str, object]:
    return {
        "native_ids": [],
        "session_ids": session_ids or [],
        "asset_keys": asset_keys or [],
    }


class _Result:
    def __init__(self, *, first: Any = None, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._first = first
        self._rows = [] if rows is None else list(rows)
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _Result:
        return self

    def scalar_one(self) -> Any:
        return self._rows[0] if self._rows else 0

    def scalar_one_or_none(self) -> Any:
        return self._first


class _FenceConn:
    """按表名分派最小行事实，并记录全部执行语句（含锁序）。"""

    def __init__(
        self,
        *,
        fence_rows: list[dict[str, object]] | None = None,
        session_first: Any = None,
        session_rows: list[Any] | None = None,
        command_row: Any = None,
        execution_first: Any = None,
        execution_rows: list[Any] | None = None,
        open_operations: list[Any] | None = None,
        operation_first: Any = None,
        guard_row: Any = None,
        slot_row: Any = None,
        asset_rows: list[Any] | None = None,
        inherited_count: int = 0,
    ) -> None:
        self.fence_rows = [] if fence_rows is None else fence_rows
        self.session_first = session_first
        self.session_rows = [] if session_rows is None else session_rows
        self.command_row = command_row
        self.execution_first = execution_first
        self.execution_rows = [] if execution_rows is None else execution_rows
        self.open_operations = [] if open_operations is None else open_operations
        self.operation_first = operation_first
        self.guard_row = guard_row
        self.slot_row = slot_row
        self.asset_rows = [] if asset_rows is None else list(asset_rows)
        self.inherited_count = inherited_count
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=self.fence_rows)
        if "FROM agent_asset_version" in sql:
            # 源锁新鲜重读：按调用顺序逐行给出资产事实（含清理事实四列）。
            row = self.asset_rows.pop(0) if self.asset_rows else None
            return _Result(first=row)
        if "FROM agent_source_link" in sql:
            return _Result(rows=[self.inherited_count])
        if "FROM agent_operation" in sql:
            if self.operation_first is not None:
                return _Result(first=self.operation_first)
            return _Result(rows=self.open_operations)
        if "INSERT INTO agent_resource_slot" in sql:
            return _Result()
        if "FROM agent_resource_slot" in sql:
            if "agent_resource_slot.slot_kind" in sql:
                return _Result(first=self.slot_row)
            return _Result(first=self.guard_row)
        if "FROM agent_execution" in sql:
            return _Result(first=self.execution_first, rows=self.execution_rows)
        if "FROM agent_session" in sql:
            return _Result(first=self.session_first, rows=self.session_rows)
        if "FROM agent_command" in sql:
            return _Result(first=self.command_row)
        return _Result()

    def _fence_statements(self) -> list[str]:
        return [sql for sql in self.statements if "FROM agent_cleanup_job" in sql]


def _dao(conn: _FenceConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _payload(text: str = "hello") -> DraftPayload:
    return DraftPayload(
        schema_version=1,
        text=text,
        mode=Mode.EXECUTE,
        model_selection=None,
        source_refs=(),
        skill_versions=(),
        mcp_selection=(),
        purpose=None,
        cutoff=None,
        send_target=None,
        object_context=None,
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def _draft_command(expected: int = 1, command_id: str = "cmd-1") -> DraftSaveCommand:
    return DraftSaveCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="mod",
            business_request_id="req-1",
            command_id=command_id,
            expected_revision=expected,
        ),
        session_id="s1",
        expected_draft_revision=expected,
        payload=_payload(),
    )


def _accept_request() -> AcceptRequest:
    return AcceptRequest(
        user_scope="u1",
        session_id="s1",
        source_module="mod",
        business_request_id="req-1",
        client_command_id="cmd-1",
        expected_revision=0,
        node_id="root",
        workflow_id="wf-1",
        mode=Mode.DISCUSS,
        purpose=Purpose.CONCEPT,
        text="hello",
        manifest_payload=json_doc({"schema_version": 1}),
        source_display_name="src",
    )


def _transfer_command() -> DraftTransferCommand:
    payload = _payload("moved")
    base = {
        "command": CommandRef(
            user_scope="u1",
            source_module="mod",
            business_request_id="req-2",
            command_id="cmd-2",
            expected_revision=2,
        ),
        "source_kind": DraftTransferSourceKind.SESSION_DRAFT,
        "source_id": "s1",
        "source_session_id": "s1",
        "expected_source_draft_revision": 3,
        "target_session_id": "s2",
        "expected_target_draft_revision": 2,
        "payload": payload,
        "payload_digest": draft_transfer_payload_digest(payload),
        "confirmed": True,
    }
    probe = DraftTransferCommand(transfer_id="", **base)
    return DraftTransferCommand(transfer_id=derive_draft_transfer_id(probe), **base)


class TestSaveDraftFenceGate:
    async def test_new_key_rejected_when_session_fenced(self) -> None:
        conn = _FenceConn(
            fence_rows=[_fenced(["s1"])],
            session_first=SimpleNamespace(draft_revision=1, event_seq=0),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).save_draft(_draft_command())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        fence_sql = conn._fence_statements()
        assert len(fence_sql) == 1
        # 默认方言渲染为 FOR UPDATE；PG 方言下 with_for_update(read=True) 落 FOR SHARE。
        assert "FOR UPDATE" in fence_sql[0]
        assert not any("INSERT INTO agent_command" in sql for sql in conn.statements)

    async def test_existing_command_replay_returns_original_receipt(self) -> None:
        command = _draft_command()
        receipt = DraftReceipt(
            session_id="s1",
            command_record_id="cmd-rec-1",
            commit_state=CommitState.COMMITTED,
            draft_revision=1,
            verified_at=_WIRE_TIME,
            error=None,
        )
        conn = _FenceConn(
            fence_rows=[_fenced(["s1"])],
            session_first=SimpleNamespace(draft_revision=1, event_seq=0),
            command_row=SimpleNamespace(
                input_digest=draft_save_input_digest(command),
                receipt_payload=encode_draft_receipt(receipt),
            ),
        )
        replay = await _dao(conn).save_draft(command)
        assert replay == receipt
        # 重放先于门禁：旧事实重放不因新 fence 被判新拒绝。
        assert conn._fence_statements() == []


class TestAcceptRequestFenceGate:
    async def test_new_key_rejected_when_session_fenced(self) -> None:
        conn = _FenceConn(
            fence_rows=[_fenced(["s1"])],
            session_first=SimpleNamespace(session_id="s1"),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).accept_request(_accept_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_command" in sql for sql in conn.statements)
        assert not any("INSERT INTO agent_message" in sql for sql in conn.statements)


class TestTransferDraftFenceGate:
    async def test_target_fence_rejected_before_install(self) -> None:
        conn = _FenceConn(fence_rows=[_fenced(["s2"])])
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).transfer_draft(_transfer_command())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_command" in sql for sql in conn.statements)


class TestDraftSourceLinkFenceGate:
    async def test_fenced_asset_reference_rejected(self) -> None:
        conn = _FenceConn(fence_rows=[_fenced(asset_keys=[["asset-1", "1"]])])
        ref = SourceVersionRef(
            source_kind=SourceKind.USER_MESSAGE,
            source_id="asset-1",
            source_version="1",
            source_digest="a" * 64,
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn)._sync_draft_source_links(
                conn,  # type: ignore[arg-type]  # capture conn 只需 execute/all
                user_scope="u1",
                session_id="s1",
                draft_revision=2,
                source_refs=(ref,),
                identity="u1/s1/cmd-1",
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_source_link" in sql for sql in conn.statements)


class TestClaimResourceFenceGate:
    async def test_new_ticket_rejected_for_fenced_session(self) -> None:
        conn = _FenceConn(
            fence_rows=[_fenced(["s1"])],
            guard_row=SimpleNamespace(slot_id="global_guard"),
            execution_first=SimpleNamespace(
                execution_kind="root",
                session_id="s1",
                parent_execution_id=None,
                deletion_fenced=False,
            ),
        )
        request = ResourceClaimRequest(
            user_scope="u1",
            slot_id="slot-1",
            slot_kind=ResourceSlotKind.TOTAL,
            execution_id="exec-1",
            node_id="root",
            round_id=None,
            owner_epoch=1,
            operation_refs=None,
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        execution_index = next(i for i, sql in enumerate(conn.statements) if "FROM agent_execution" in sql)
        fence_index = next(i for i, sql in enumerate(conn.statements) if "FROM agent_cleanup_job" in sql)
        assert execution_index < fence_index
        inserts = [sql for sql in conn.statements if "INSERT INTO agent_resource_slot" in sql]
        assert len(inserts) == 1  # 仅 guard 幂等预置，无票据落库


class TestRecoveryFenceGate:
    async def test_fenced_session_execution_is_blocked(self) -> None:
        conn = _FenceConn(
            fence_rows=[_fenced(["s1"])],
            execution_rows=[
                SimpleNamespace(
                    execution_id="exec-1",
                    session_id="s1",
                    display_state="running",
                    owner_epoch=1,
                    round_id="round-1",
                    workflow_id="wf-1",
                    run_epoch=1,
                    stop_reason=None,
                    active_round=True,
                    workflow_code_version="1.0.0",
                )
            ],
        )
        fact = await _dao(conn).grant_recovery_epoch(
            RecoveryGrantRequest(
                user_scope="u1",
                execution_ids=("exec-1",),
                owner_epoch=2,
                run_epoch=2,
                application_version="1.0.0",
            )
        )
        assert fact.blocked == (("exec-1", "deletion_fenced"),)
        assert fact.granted_execution_ids == ()
        assert not any("UPDATE agent_execution" in sql for sql in conn.statements)


class TestCleanupFenceHelper:
    async def test_corrupt_identity_set_fails_closed(self) -> None:
        conn = _FenceConn(fence_rows=[{"native_ids": "bad"}])
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn)._cleanup_fenced_identities(conn, user_scope="u1")  # type: ignore[arg-type]
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


class TestClaimSourceCleanupGate:
    """D09：源锁内读完整清理事实，已清理 ready 资产零新 claim；旧 claim 只查原事实。

    清理（``tombstone_cleanup_text``）保留 ``read_state='ready'`` 与
    ``disabled_revision``，因此不能只按禁用状态判定；三事实任一成立即已清理。
    """

    @pytest.mark.parametrize(
        "fact",
        [
            {"tombstoned": True},
            {"cleaned_at": datetime(2026, 6, 1, 12, 0, tzinfo=UTC)},
            {"digest": CLEANUP_TOMBSTONE_DIGEST},
        ],
        ids=["tombstoned", "cleaned_at", "tombstoned_digest"],
    )
    async def test_cleaned_ready_asset_rejects_new_claim(self, fact: dict[str, Any]) -> None:
        conn = _FenceConn(
            asset_rows=[_asset_row(**fact)],
            operation_first=SimpleNamespace(data_consent_claims=[]),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_source_versions(_claim_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        # 零新事实：不追加认领、不推进 operation 修订。
        assert not any("UPDATE agent_operation" in sql for sql in conn.statements)

    async def test_cleaned_beats_disabled_projection(self) -> None:
        # 清理后 disabled_revision/read_state 被保留：仍须按清理事实报 POLICY_DENIED。
        conn = _FenceConn(
            asset_rows=[_asset_row(tombstoned=True, disabled_revision=2, read_state="disabled")],
            operation_first=SimpleNamespace(data_consent_claims=[]),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_source_versions(_claim_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

    async def test_existing_claim_replay_returns_original_fact(self) -> None:
        saved = {
            "kind": "source_revision",
            "claim_ref": "claim-1",
            "operation_id": "op-1",
            "sources": [{"source_id": "src-1", "source_version": "1", "disabled_revision": 0}],
        }
        conn = _FenceConn(
            asset_rows=[_asset_row(tombstoned=True, cleaned_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC))],
            operation_first=SimpleNamespace(data_consent_claims=[saved]),
        )
        fact = await _dao(conn).claim_source_versions(_claim_request())
        assert fact.replayed is True
        assert fact.bindings == (SourceClaimBinding(source_id="src-1", source_version="1", disabled_revision=0),)
        assert not any("UPDATE agent_operation" in sql for sql in conn.statements)

    async def test_uncleaned_asset_persists_new_claim(self) -> None:
        conn = _FenceConn(
            asset_rows=[_asset_row()],
            operation_first=SimpleNamespace(data_consent_claims=[]),
        )
        fact = await _dao(conn).claim_source_versions(_claim_request())
        assert fact.replayed is False
        assert fact.bindings == (SourceClaimBinding(source_id="src-1", source_version="1", disabled_revision=0),)
        assert any("UPDATE agent_operation" in sql for sql in conn.statements)

    async def test_source_lock_reads_cleanup_columns(self) -> None:
        # 清理事实必须在源行锁内读取（同一 FOR UPDATE 查询含 tombstoned/cleaned_at/digest）。
        conn = _FenceConn(
            asset_rows=[_asset_row()],
            operation_first=SimpleNamespace(data_consent_claims=[]),
        )
        await _dao(conn).claim_source_versions(_claim_request())
        asset_sql = next(sql for sql in conn.statements if "FROM agent_asset_version" in sql)
        assert "FOR UPDATE" in asset_sql
        assert "agent_asset_version.digest" in asset_sql
        assert "agent_asset_version.cleaned_at" in asset_sql
        assert "agent_asset_version.tombstoned" in asset_sql


def _asset_row(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "disabled_revision": 0,
        "read_state": "ready",
        "digest": "a" * 64,
        "cleaned_at": None,
        "tombstoned": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _claim_request(claim_ref: str = "claim-1") -> SourceClaimRequest:
    return SourceClaimRequest(
        user_scope="u1",
        claim_ref=claim_ref,
        operation_id="op-1",
        sources=(
            SourceVersionRef(
                source_kind=SourceKind.USER_MESSAGE,
                source_id="src-1",
                source_version="1",
                source_digest="b" * 64,
            ),
        ),
    )


# ----------------------------------------------------------------------
# 下批次入口（D08 补齐）：record_event / save_answer / save_delivery_phase /
# save_plan_proposal / change_session(branch)
# ----------------------------------------------------------------------


class _StopFlow(RuntimeError):
    """流程级假连接的提前终止哨兵：证明门禁已放行并到达目标写入语句。"""


class _FlowConn:
    """覆盖事件/答复/交付/提案/会话命令的最小行事实路由。"""

    def __init__(
        self,
        *,
        fence_rows: list[dict[str, object]] | None = None,
        session_row: Any = None,
        execution_row: Any = None,
        round_row: Any = None,
        command_row: Any = None,
        decision_row: Any = None,
        operation_row: Any = None,
        head_rows: list[Any] | None = None,
        message_event_row: Any = None,
        message_source_row: Any = None,
        message_cutoff_previous: Any = None,
        message_replay_row: Any = None,
        message_insert_error: Exception | None = None,
        stop_on: str | None = None,
    ) -> None:
        self.fence_rows = [] if fence_rows is None else fence_rows
        self.session_row = session_row
        self.execution_row = execution_row
        self.round_row = round_row
        self.command_row = command_row
        self.decision_row = decision_row
        self.operation_row = operation_row
        self.head_rows = [] if head_rows is None else head_rows
        self.message_event_row = message_event_row
        self.message_source_row = message_source_row
        self.message_cutoff_previous = message_cutoff_previous
        self.message_replay_row = message_replay_row
        self.message_insert_error = message_insert_error
        self.stop_on = stop_on
        self.statements: list[str] = []

    def _maybe_stop(self, sql: str) -> None:
        if self.stop_on is not None and self.stop_on in sql:
            raise _StopFlow(sql)

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=self.fence_rows)
        if "pg_notify" in sql:
            return _Result()
        if "INSERT INTO agent_message" in sql:
            if self.message_insert_error is not None:
                raise self.message_insert_error
            self._maybe_stop(sql)
            return _Result()
        if "INSERT INTO agent_command" in sql:
            self._maybe_stop(sql)
            return _Result()
        if "INSERT INTO agent_session" in sql:
            self._maybe_stop(sql)
            return _Result()
        if "INSERT INTO agent_asset_version" in sql:
            self._maybe_stop(sql)
            return _Result()
        if sql.startswith("UPDATE "):
            self._maybe_stop(sql)
            return _Result(rowcount=1)
        if "FROM agent_message" in sql:
            if "agent_message.committed_at" in sql:
                return _Result(first=self.message_replay_row)
            if "agent_message.event_id" in sql:
                return _Result(first=self.message_event_row)
            if "agent_message.subject_revision" in sql:
                return _Result(first=self.message_source_row)
            if "session_seq <" in sql:
                return _Result(first=self.message_cutoff_previous)
            return _Result(first=None)
        if "FROM agent_round" in sql:
            return _Result(first=self.round_row)
        if "FROM agent_decision" in sql:
            return _Result(first=self.decision_row)
        if "FROM agent_execution" in sql:
            return _Result(first=self.execution_row)
        if "FROM agent_session" in sql:
            if "agent_session.cleaned_at" in sql:
                return _Result(first=None if self.session_row is None else self.session_row.cleaned_at)
            return _Result(first=self.session_row)
        if "FROM agent_operation" in sql:
            return _Result(first=self.operation_row)
        if "FROM agent_command" in sql:
            return _Result(first=self.command_row)
        if "FROM agent_asset_version" in sql:
            return _Result(rows=self.head_rows)
        return _Result()

    def _fence_statements(self) -> list[str]:
        return [sql for sql in self.statements if "FROM agent_cleanup_job" in sql]

    def begin_nested(self) -> Any:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm


def _flow_dao(conn: _FlowConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _record_event_request() -> RecordEventRequest:
    return RecordEventRequest(
        user_scope="u1",
        session_id="s1",
        message_id="m1",
        native_event_id="n1",
        event_kind=EventKind.PUBLIC_MESSAGE_SAVED,
        subject_id="s1",
        subject_revision=1,
        payload_kind="public_message",
        payload_canonical_json=json_doc({"k": 1}),
        occurred_at=_WIRE_TIME,
        body_text="hi",
    )


def _answer_request() -> SaveAnswerRequest:
    return SaveAnswerRequest(
        user_scope="u1",
        decision_id="d1",
        client_command_id="c1",
        expected_decision_revision=1,
        answer_payload=json_doc({"choice": "a"}),
    )


def _decision_row(*, answer_command_id: str | None = None) -> Any:
    return SimpleNamespace(
        session_id="s1",
        execution_id="e1",
        round_id="r1",
        revision=1,
        answer_command_id=answer_command_id,
        expires_at=None,
        answer_schema=None,
    )


def _answerable_rows() -> dict[str, Any]:
    return {
        "execution_row": SimpleNamespace(current_round_id="r1", display_state="running", owner_epoch=1),
        "round_row": SimpleNamespace(active_round=True, stop_reason=None, run_epoch=1),
    }


def _delivery_manifest(
    *, artifact_id: str = "art-1", artifact_version: int = 1, source_state: str = "available"
) -> DeliveryManifest:
    return DeliveryManifest(
        delivery_id=stable_id("delivery", ["u1", "cmd-4", artifact_id, str(artifact_version), "md"]),
        command=CommandRef(
            user_scope="u1",
            source_module="mod",
            business_request_id="req-4",
            command_id="cmd-4",
            expected_revision=0,
        ),
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        artifact_digest="a" * 64,
        format="md",
        format_rule_version=1,
        target=DeliveryTarget(
            target_path="D:\\tmp\\out.md",
            expected_existing=False,
            expected_existing_digest=None,
            overwrite_confirmed=False,
        ),
        phase="manifest_saved",
        source_state=source_state,
        output_digest=None,
        output_bytes=None,
        staged_path=None,
        verified_at=_WIRE_TIME,
        error_code=None,
    )


def _operation_row() -> Any:
    return SimpleNamespace(
        delivery_manifest=None,
        revision=0,
        artifact_id=None,
        artifact_version=None,
        session_id="s1",
        execution_id="e1",
    )


def _plan_items() -> tuple[PlanItemSnapshot, ...]:
    return (
        PlanItemSnapshot(
            id="1",
            content="step",
            status="pending",
            active_form="stepping",
            parent_id=None,
            depends_on=(),
        ),
    )


def _branch_command() -> SessionChangeCommand:
    return SessionChangeCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="mod",
            business_request_id="req-3",
            command_id="cmd-3",
            expected_revision=0,
        ),
        action=SessionAction.BRANCH,
        session_id="new-s",
        expected_session_revision=0,
        title="branch",
        source_message_id="m1",
        source_message_revision=1,
    )


def _message_replay_row(event_id: str) -> Any:
    occurred = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        message_id="m1",
        event_id=event_id,
        session_seq=1,
        execution_seq=None,
        event_kind=EventKind.PUBLIC_MESSAGE_SAVED.value,
        subject_id="s1",
        subject_revision=1,
        payload_kind="public_message",
        payload={"k": 1},
        input_revision=None,
        role=None,
        role_id=None,
        role_version=None,
        body_text="hi",
        body_ref=None,
        search_text=None,
        native_fact_ref=None,
        tool_call_id=None,
        occurred_at=occurred,
        committed_at=occurred,
    )


class TestRecordEventFenceGate:
    async def test_new_event_rejected_when_session_fenced(self) -> None:
        conn = _FlowConn(fence_rows=[_fenced(["s1"])], session_row=SimpleNamespace(event_seq=0))
        with pytest.raises(AgentServiceError) as exc:
            await _flow_dao(conn).record_event(_record_event_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_message" in sql for sql in conn.statements)

    async def test_unfenced_session_passes_gate(self) -> None:
        conn = _FlowConn(session_row=SimpleNamespace(event_seq=0), stop_on="INSERT INTO agent_message")
        with pytest.raises(_StopFlow) as exc:
            await _flow_dao(conn).record_event(_record_event_request())
        assert "INSERT INTO agent_message" in str(exc.value)
        assert len(conn._fence_statements()) == 1

    async def test_existing_event_replay_skips_gate(self) -> None:
        request = _record_event_request()
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            session_row=SimpleNamespace(event_seq=1),
            message_event_row=SimpleNamespace(message_id="m1"),
            message_replay_row=_message_replay_row(
                derive_event_identity(execution_id=None, round_id=None, node_id=None, native_event_id="n1")
            ),
            message_insert_error=_unique_violation(),
        )
        fact = await _flow_dao(conn).record_event(request)
        assert fact.replayed is True
        assert conn._fence_statements() == []


def _unique_violation() -> IntegrityError:
    orig = Exception("duplicate key")
    orig.sqlstate = "23505"  # type: ignore[attr-defined]  # 模拟 asyncpg UniqueViolation.sqlstate
    return IntegrityError("INSERT INTO agent_message", {}, orig)


class TestSaveAnswerFenceGate:
    async def test_new_answer_rejected_when_session_fenced(self) -> None:
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            decision_row=_decision_row(),
            **_answerable_rows(),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _flow_dao(conn).save_answer(_answer_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_command" in sql for sql in conn.statements)

    async def test_unfenced_session_passes_gate(self) -> None:
        conn = _FlowConn(
            decision_row=_decision_row(),
            stop_on="INSERT INTO agent_command",
            **_answerable_rows(),
        )
        with pytest.raises(_StopFlow) as exc:
            await _flow_dao(conn).save_answer(_answer_request())
        assert "INSERT INTO agent_command" in str(exc.value)
        assert len(conn._fence_statements()) == 1

    async def test_command_replay_returns_original_facts(self) -> None:
        request = _answer_request()
        command_record_id = stable_id("command", ["u1", "d1", "c1"])
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            decision_row=_decision_row(answer_command_id=command_record_id),
            command_row=SimpleNamespace(
                input_digest=sha256_hex(request.answer_payload),
                delivery_state=DeliveryState.NOT_DELIVERED.value,
                consumption_state=ConsumptionState.NOT_CONSUMED.value,
                verified_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            ),
        )
        receipt = await _flow_dao(conn).save_answer(request)
        assert receipt.command_record_id == command_record_id
        assert conn._fence_statements() == []


class TestSaveDeliveryPhaseFenceGate:
    async def test_new_phase_rejected_when_session_fenced(self) -> None:
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            operation_row=_operation_row(),
            session_row=SimpleNamespace(cleaned_at=None),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _flow_dao(conn).save_delivery_phase(
                user_scope="u1", operation_id="op-1", expected_revision=0, manifest=_delivery_manifest()
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("UPDATE agent_operation" in sql for sql in conn.statements)

    async def test_unfenced_session_passes_gate(self) -> None:
        conn = _FlowConn(
            operation_row=_operation_row(),
            session_row=SimpleNamespace(cleaned_at=None),
        )
        receipt = await _flow_dao(conn).save_delivery_phase(
            user_scope="u1", operation_id="op-1", expected_revision=0, manifest=_delivery_manifest()
        )
        assert receipt.idempotent is False
        assert len(conn._fence_statements()) == 1

    async def test_revision_branch_precedes_gate(self) -> None:
        # 版本不符分支（含幂等回执/冲突）在门禁之前：即使会话已 fence 也先按原语义返回。
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            operation_row=SimpleNamespace(
                delivery_manifest=encode_delivery_manifest(_delivery_manifest(source_state="disabled")),
                revision=3,
                artifact_id=None,
                artifact_version=None,
                session_id="s1",
                execution_id="e1",
            ),
            session_row=SimpleNamespace(cleaned_at=None),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _flow_dao(conn).save_delivery_phase(
                user_scope="u1", operation_id="op-1", expected_revision=0, manifest=_delivery_manifest()
            )
        assert exc.value.code is AgentErrorCode.REVISION_CONFLICT
        assert conn._fence_statements() == []


class TestSavePlanProposalFenceGate:
    async def test_new_proposal_rejected_when_session_fenced(self) -> None:
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            execution_row=SimpleNamespace(execution_id="e1", session_id="s1"),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _flow_dao(conn).save_plan_proposal(
                user_scope="u1", execution_id="e1", expected_revision=0, items=_plan_items()
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_asset_version" in sql for sql in conn.statements)

    async def test_unfenced_session_passes_gate(self) -> None:
        conn = _FlowConn(
            execution_row=SimpleNamespace(execution_id="e1", session_id="s1"),
            stop_on="INSERT INTO agent_asset_version",
        )
        with pytest.raises(_StopFlow) as exc:
            await _flow_dao(conn).save_plan_proposal(
                user_scope="u1", execution_id="e1", expected_revision=0, items=_plan_items()
            )
        assert "INSERT INTO agent_asset_version" in str(exc.value)
        assert len(conn._fence_statements()) == 1

    async def test_same_content_replay_skips_gate(self) -> None:
        probe = PlanProposal(execution_id="e1", plan_revision=1, items=_plan_items())
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            execution_row=SimpleNamespace(execution_id="e1", session_id="s1"),
            head_rows=[
                SimpleNamespace(
                    body_text=normalize_plan_proposal(probe),
                    digest=plan_proposal_digest(probe),
                    asset_version="1",
                    read_state="ready",
                )
            ],
        )
        proposal = await _flow_dao(conn).save_plan_proposal(
            user_scope="u1", execution_id="e1", expected_revision=1, items=_plan_items()
        )
        assert proposal.plan_revision == 1
        assert conn._fence_statements() == []


class TestChangeSessionBranchFenceGate:
    async def test_branch_from_fenced_source_rejected(self) -> None:
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            message_source_row=SimpleNamespace(message_id="m1", session_id="s1", session_seq=2, subject_revision=1),
            message_cutoff_previous=None,
        )
        with pytest.raises(AgentServiceError) as exc:
            await _flow_dao(conn).change_session(_branch_command())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not any("INSERT INTO agent_session" in sql for sql in conn.statements)

    async def test_branch_from_unfenced_source_passes_gate(self) -> None:
        conn = _FlowConn(
            message_source_row=SimpleNamespace(message_id="m1", session_id="s1", session_seq=2, subject_revision=1),
            message_cutoff_previous=None,
            stop_on="INSERT INTO agent_session",
        )
        with pytest.raises(_StopFlow) as exc:
            await _flow_dao(conn).change_session(_branch_command())
        assert "INSERT INTO agent_session" in str(exc.value)
        assert len(conn._fence_statements()) == 1

    async def test_command_replay_returns_original_receipt(self) -> None:
        command = _branch_command()
        receipt = CommandReceipt(
            command=command.command,
            target_id="new-s",
            current_revision=1,
            commit_state=CommitState.COMMITTED,
            delivery_state=DeliveryState.NOT_DELIVERED,
            consumption_state=ConsumptionState.NOT_CONSUMED,
            updated_at=_WIRE_TIME,
            error_code=None,
            next_action=None,
        )
        conn = _FlowConn(
            fence_rows=[_fenced(["s1"])],
            command_row=SimpleNamespace(
                input_digest=session_change_input_digest(command),
                receipt_payload=encode_command_receipt(receipt),
            ),
        )
        replay = await _flow_dao(conn).change_session(command)
        assert replay.target_id == "new-s"
        assert conn._fence_statements() == []
