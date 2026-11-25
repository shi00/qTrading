"""写入族同事务可靠事件契约测试（检视 D29）。

观察侧只读 ``agent_message``（``read_events`` / ``observe_session_events``）：
``save_draft``、``save_plan_proposal`` 与 ``change_session``（改名/置顶/归档/解档）
提交后必须留下可被 ``after_seq`` 过滤取到的事件；同命令键（草稿）/同内容（提案）
重放、元数据动作重放/预期版冲突均不写第二条事件；D08 清理 fence 下仍拒绝且零事件。
真实事务/唯一约束行为由 sidecar PG 集成测试覆盖，本文件用 capture conn 断言语句
序列与行载荷。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    DraftPayload,
    DraftSaveCommand,
    EventKind,
    EventReadQuery,
    Mode,
    PlanItemSnapshot,
    Purpose,
    SessionAction,
    SessionChangeCommand,
    draft_save_input_digest,
    encode_command_receipt,
    encode_draft_receipt,
    session_change_input_digest,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


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


def _event_row(values: dict[str, Any]) -> Any:
    """insert 参数 → AgentMessage 行事实（供 read_events 严格解码）。"""

    return SimpleNamespace(
        user_scope=values["user_scope"],
        message_id=values["message_id"],
        session_id=values["session_id"],
        execution_id=values.get("execution_id"),
        node_id=values.get("node_id"),
        round_id=values.get("round_id"),
        event_id=values["event_id"],
        session_seq=values["session_seq"],
        execution_seq=values.get("execution_seq"),
        event_kind=values["event_kind"],
        payload_kind=values["payload_kind"],
        subject_id=values["subject_id"],
        subject_revision=values["subject_revision"],
        input_revision=values.get("input_revision"),
        occurred_at=values["occurred_at"],
        committed_at=values["committed_at"],
        native_fact_ref=values.get("native_fact_ref"),
        payload=values.get("payload"),
    )


def _asset_row(values: dict[str, Any]) -> Any:
    """insert 参数 → 计划版本 head 行事实。"""

    return SimpleNamespace(
        body_text=values["body_text"],
        digest=values["digest"],
        asset_version=values["asset_version"],
        read_state=values["read_state"],
    )


class _WriteEventConn:
    """按表分派最小行事实并记录事件行（同一 conn 同时服务写事务与读取）。"""

    def __init__(
        self,
        *,
        draft_revision: int = 1,
        event_seq: int = 0,
        revision: int = 1,
        fenced_sessions: list[str] | None = None,
        execution_session_id: str | None = "s1",
        command_row: Any = None,
    ) -> None:
        self.session = SimpleNamespace(draft_revision=draft_revision, event_seq=event_seq, revision=revision)
        self.fenced_sessions = [] if fenced_sessions is None else list(fenced_sessions)
        self.execution = None
        if execution_session_id is not None:
            self.execution = SimpleNamespace(execution_id="exec-1", session_id=execution_session_id)
        self.command_row = command_row
        self.messages: list[dict[str, Any]] = []
        self.assets: list[Any] = []
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM agent_cleanup_job" in sql:
            return _Result(
                rows=[{"native_ids": [], "session_ids": list(self.fenced_sessions), "asset_keys": []}]
                if self.fenced_sessions
                else []
            )
        if "INSERT INTO agent_message" in sql:
            values = dict(statement.compile().params)
            self.messages.append(values)
            self.session.event_seq = max(int(self.session.event_seq), int(values["session_seq"]))
            return _Result()
        if "INSERT INTO agent_asset_version" in sql:
            values = dict(statement.compile().params)
            self.assets.append(_asset_row(values))
            return _Result()
        if "INSERT INTO agent_command" in sql:
            return _Result()
        if "FROM agent_message" in sql:
            if "agent_message.session_seq > " in sql:
                bound = dict(statement.compile().params)
                after_seq = int(next(v for k, v in bound.items() if "session_seq" in k))
                rows = [_event_row(values) for values in self.messages if int(values["session_seq"]) > after_seq]
                return _Result(rows=sorted(rows, key=lambda row: row.session_seq))
            # 事件身份查重（_append_session_event）：同 (session, event_id) 已存在。
            bound = dict(statement.compile().params)
            wanted = next((v for k, v in bound.items() if "event_id" in k), None)
            hit = next((item for item in self.messages if item["event_id"] == wanted), None)
            return _Result(first=None if hit is None else SimpleNamespace(message_id=hit["message_id"]))
        if "FROM agent_execution" in sql:
            return _Result(first=self.execution)
        if "FROM agent_session" in sql:
            return _Result(first=self.session)
        if "FROM agent_command" in sql:
            return _Result(first=self.command_row)
        if "FROM agent_asset_version" in sql:
            return _Result(rows=self.assets)
        return _Result()


def _dao(conn: _WriteEventConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm_begin = MagicMock()
    cm_begin.__aenter__ = AsyncMock(return_value=conn)
    cm_begin.__aexit__ = AsyncMock(return_value=False)
    cm_connect = MagicMock()
    cm_connect.__aenter__ = AsyncMock(return_value=conn)
    cm_connect.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm_begin)
    engine.connect = MagicMock(return_value=cm_connect)
    return AgentDao(engine)


def _draft_payload(text: str = "hello") -> DraftPayload:
    return DraftPayload(
        schema_version=2,
        text=text,
        mode=Mode.EXECUTE,
        model_selection=None,
        source_refs=(),
        skill_versions=(),
        mcp_selection=(),
        purpose=None,
        research_date=None,
        cutoff=None,
        send_target=None,
        object_context=None,
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def _draft_command(command_id: str = "cmd-1", expected: int = 1) -> DraftSaveCommand:
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
        payload=_draft_payload(),
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


class TestSaveDraftEvent:
    async def test_committed_draft_leaves_observable_event(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        receipt = await dao.save_draft(_draft_command())
        assert receipt.draft_revision == 2

        assert len(conn.messages) == 1
        values = conn.messages[0]
        assert values["event_kind"] == EventKind.DRAFT_SAVED.value
        assert values["payload_kind"] == "draft_saved"
        assert values["session_seq"] == 1
        assert values["subject_id"] == "s1"
        assert values["subject_revision"] == 2
        assert values["payload"]["draft_revision"] == 2
        # 事件与命令同事务：事件写入在命令事实之后。
        assert conn.statements.index(next(s for s in conn.statements if "INSERT INTO agent_command" in s)) < (
            conn.statements.index(next(s for s in conn.statements if "INSERT INTO agent_message" in s))
        )

        page = await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=0))
        assert len(page.events) == 1
        assert page.events[0].event_kind is EventKind.DRAFT_SAVED
        assert page.events[0].session_seq == 1
        assert page.last_seq == 1

        # after_seq 过滤生效：已追平到 seq=1 后不再返回该事件。
        assert (await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=1))).events == ()

    async def test_replay_does_not_append_second_event(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        command = _draft_command()
        first = await dao.save_draft(command)
        # 第二次同命令键：命令键查重返回原回执（不推进版本、不写第二条事件）。
        conn.command_row = SimpleNamespace(
            input_digest=draft_save_input_digest(command),
            receipt_payload=encode_draft_receipt(first),
        )
        replay = await dao.save_draft(command)

        assert replay == first
        assert len(conn.messages) == 1

    async def test_cleanup_fenced_session_rejected_without_event(self) -> None:
        conn = _WriteEventConn(fenced_sessions=["s1"])
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).save_draft(_draft_command())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.messages == []

    async def test_existing_event_identity_not_duplicated(self) -> None:
        # 同 (session, event_id) 已存在（命令行缺失的重放/中断窗口）：只返回既有
        # 事件事实，不写第二条（幂等；崩溃重放不漏投、不重复）。
        conn = _WriteEventConn()
        dao = _dao(conn)
        command = _draft_command()
        await dao.save_draft(command)
        conn.command_row = None  # 绕过命令键查回，直击事件身份幂等分支
        await dao.save_draft(command)
        assert len(conn.messages) == 1
        assert conn.session.event_seq == 1


class TestSavePlanProposalEvent:
    async def test_committed_proposal_leaves_observable_event(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        saved = await dao.save_plan_proposal(
            user_scope="u1", execution_id="exec-1", expected_revision=0, items=_plan_items()
        )
        assert saved.plan_revision == 1

        assert len(conn.messages) == 1
        values = conn.messages[0]
        assert values["event_kind"] == EventKind.ARTIFACT_SAVED.value
        assert values["payload_kind"] == "plan_proposal"
        assert values["session_seq"] == 1
        assert values["subject_revision"] == 1
        assert values["payload"]["execution_id"] == "exec-1"
        assert values["payload"]["plan_revision"] == 1

        page = await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=0))
        assert [event.event_kind for event in page.events] == [EventKind.ARTIFACT_SAVED]
        assert (await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=1))).events == ()

    async def test_same_content_replay_does_not_append_second_event(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=_plan_items())
        again = await dao.save_plan_proposal(
            user_scope="u1", execution_id="exec-1", expected_revision=1, items=_plan_items()
        )
        assert again.plan_revision == 1
        assert len(conn.messages) == 1
        assert len(conn.assets) == 1

    async def test_cleanup_fenced_execution_session_rejected_without_event(self) -> None:
        conn = _WriteEventConn(fenced_sessions=["s1"])
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).save_plan_proposal(
                user_scope="u1", execution_id="exec-1", expected_revision=0, items=_plan_items()
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.messages == []


def _session_command(
    *,
    action: SessionAction,
    expected: int = 1,
    title: str | None = None,
    command_id: str = "cmd-s1",
) -> SessionChangeCommand:
    return SessionChangeCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="ui",
            business_request_id=f"req-{command_id}",
            command_id=command_id,
            expected_revision=expected,
        ),
        action=action,
        session_id="s1",
        expected_session_revision=expected,
        title=title,
    )


class TestChangeSessionEvent:
    """N22：改名/置顶/归档提交后留下可被 after_seq 追平的 session_changed 事件。"""

    async def test_rename_leaves_single_observable_event(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        receipt = await dao.change_session(_session_command(action=SessionAction.RENAME, title="改名后"))
        assert receipt.current_revision == 2

        assert len(conn.messages) == 1
        values = conn.messages[0]
        assert values["event_kind"] == EventKind.SESSION_CHANGED.value
        assert values["payload_kind"] == "session_changed"
        assert values["session_seq"] == 1
        assert values["subject_id"] == "s1"
        assert values["subject_revision"] == 2
        assert values["payload"]["action"] == SessionAction.RENAME.value
        assert values["payload"]["session_revision"] == 2
        assert values["payload"]["title"] == "改名后"
        # 事件与命令同事务：命令事实先写，事件随后。
        assert conn.statements.index(next(s for s in conn.statements if "INSERT INTO agent_command" in s)) < (
            conn.statements.index(next(s for s in conn.statements if "INSERT INTO agent_message" in s))
        )

        page = await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=0))
        assert [event.event_kind for event in page.events] == [EventKind.SESSION_CHANGED]
        assert page.last_seq == 1
        assert (await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=1))).events == ()

    async def test_pin_archive_unarchive_each_leave_accurate_event(self) -> None:
        cases = (
            (SessionAction.PIN, "pinned", True),
            (SessionAction.ARCHIVE, "archived", True),
            (SessionAction.UNARCHIVE, "archived", False),
        )
        for action, field, expected in cases:
            conn = _WriteEventConn()
            await _dao(conn).change_session(_session_command(action=action))
            assert len(conn.messages) == 1
            payload = conn.messages[0]["payload"]
            assert payload["action"] == action.value
            assert payload[field] is expected
            assert conn.messages[0]["event_kind"] == EventKind.SESSION_CHANGED.value

    async def test_sequential_metadata_changes_advance_watermark_by_one(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        await dao.change_session(_session_command(action=SessionAction.RENAME, title="第一版", command_id="c1"))
        conn.session.revision = 2
        await dao.change_session(_session_command(action=SessionAction.PIN, expected=2, command_id="c2"))

        assert [values["session_seq"] for values in conn.messages] == [1, 2]
        page = await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=1))
        assert [event.session_seq for event in page.events] == [2]
        assert page.events[0].payload_kind == "session_changed"

    async def test_same_command_replay_does_not_append_event(self) -> None:
        conn = _WriteEventConn()
        dao = _dao(conn)
        command = _session_command(action=SessionAction.RENAME, title="改名后")
        first = await dao.change_session(command)
        conn.command_row = SimpleNamespace(
            input_digest=session_change_input_digest(command),
            receipt_payload=encode_command_receipt(first),
        )
        replay = await dao.change_session(command)

        assert replay == first
        assert len(conn.messages) == 1

    async def test_same_command_key_with_different_input_conflicts_without_event(self) -> None:
        # 同命令键异输入：在行锁内查重即拒（零新事实），不追加事件。
        conn = _WriteEventConn()
        conn.command_row = SimpleNamespace(input_digest="stale-digest", receipt_payload=None)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).change_session(_session_command(action=SessionAction.RENAME, title="C", command_id="c2"))
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert conn.messages == []

    async def test_revision_conflict_writes_no_event(self) -> None:
        conn = _WriteEventConn(revision=5)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).change_session(_session_command(action=SessionAction.ARCHIVE, expected=1))
        assert exc.value.code is AgentErrorCode.REVISION_CONFLICT
        assert conn.messages == []
