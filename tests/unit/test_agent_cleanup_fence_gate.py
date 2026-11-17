"""清理 fence 对象门契约测试（检视 D08 / D28 恢复子项）。

``commit_cleanup_fence`` 写下的作业事实是唯一门禁源：清理后保留会话不得再接受
新正文（save_draft / accept_request / transfer_draft）、新引用（草稿来源边 /
资源票据）与恢复写入（grant_recovery_epoch）。已存在的旧事实重放保持原语义。

真实行锁竞争由 sidecar PG 集成测试覆盖；本文件用 capture conn 断言门禁位置、
``FOR SHARE`` 行锁与拒绝错误码。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommitState,
    DraftPayload,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferSourceKind,
    Mode,
    Purpose,
    RecoveryGrantRequest,
    ResourceClaimRequest,
    ResourceSlotKind,
    SourceKind,
    SourceVersionRef,
    derive_draft_transfer_id,
    draft_save_input_digest,
    draft_transfer_payload_digest,
    encode_draft_receipt,
    json_doc,
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
        guard_row: Any = None,
        slot_row: Any = None,
    ) -> None:
        self.fence_rows = [] if fence_rows is None else fence_rows
        self.session_first = session_first
        self.session_rows = [] if session_rows is None else session_rows
        self.command_row = command_row
        self.execution_first = execution_first
        self.execution_rows = [] if execution_rows is None else execution_rows
        self.open_operations = [] if open_operations is None else open_operations
        self.guard_row = guard_row
        self.slot_row = slot_row
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=self.fence_rows)
        if "FROM agent_operation" in sql:
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
            session_first=SimpleNamespace(draft_revision=1),
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
            session_first=SimpleNamespace(draft_revision=1),
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
