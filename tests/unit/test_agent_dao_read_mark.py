"""阅读水位标记契约测试（检视 D14）。

- 快照投影必须把 read_targets JSONB（``{"message_ids": [...], "artifact_refs":
  [...]}``）还原为真实精确 ID，而不是 ``list(dict)`` 得到的字段名；
- 保存→快照往返得到同一组精确 ID；
- 他会话消息、伪造成果引用、未来水位一律 ``INPUT_INVALID``，且拒绝先于写事实；
- 观测范围 digest 与已落库摘要不一致即拒绝（项目内无权威重算口径，故绑定核对，
  不静默忽略）；
- 乱序水位仍按 GREATEST 单调不回退（R22 既有语义不破坏）。

真实 PG 上的同口径语义见 ``tests/integration/test_agent_dao_read_mark.py``。
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
    ReadMarkCommand,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_DIGEST = "d" * 64


class _ReadMarkConn:
    """最小行事实替身：按 SQL 目标表返回行，并记录写事实的参数。"""

    def __init__(
        self,
        *,
        session_event_seq: int = 10,
        message_ids: set[str] | None = None,
        artifact_rows: set[tuple[str, int]] | None = None,
        marker_row: Any = None,
    ) -> None:
        self.session_event_seq = session_event_seq
        self.message_ids = message_ids or set()
        self.artifact_rows = artifact_rows or set()
        self.marker_row = marker_row
        self.writes: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        result = MagicMock()
        result.first.return_value = None
        result.all.return_value = []
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE")):
            self.writes.append(dict(statement.compile().params))
            result.rowcount = 1
        elif "FROM agent_session" in sql:
            result.first.return_value = SimpleNamespace(session_id="s1", event_seq=self.session_event_seq)
        elif "FROM agent_message" in sql:
            result.all.return_value = [
                (message_id,) for message_id in self.message_ids if message_id in _bound_values(statement, "message_id")
            ]
        elif "FROM agent_artifact" in sql:
            bound = _bound_values(statement, "artifact_id")
            result.all.return_value = [
                (artifact_id, version) for artifact_id, version in self.artifact_rows if artifact_id in bound
            ]
        elif "FROM agent_read_mark" in sql:
            result.first.return_value = self.marker_row
        return result


def _bound_values(statement: Any, column: str) -> set[str]:
    """取 ``IN (...)`` 绑定的列值（成员过滤条件的实证）。"""

    compile_fn = getattr(statement, "compile", None)
    if compile_fn is None:
        return set()
    found: set[str] = set()
    for key, value in compile_fn().params.items():
        if not key.startswith(column):
            continue
        if isinstance(value, (list, tuple, set)):
            found.update(str(item) for item in value)
        else:
            found.add(str(value))
    return found


def _dao(conn: _ReadMarkConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _command(
    *,
    message_ids: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    observed_through_seq: int | None = None,
    observation_scope_digest: str | None = None,
    command_id: str = "cmd-1",
) -> ReadMarkCommand:
    return ReadMarkCommand(
        user_scope="u1",
        session_id="s1",
        command_id=command_id,
        message_ids=message_ids,
        artifact_refs=artifact_refs,
        observed_through_seq=observed_through_seq,
        observation_scope_digest=observation_scope_digest,
    )


def _marker_row(*, last_seq: int = 0, read_targets: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(last_seq=last_seq, read_targets=read_targets)


def _assert_rejected(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> None:
    assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
    assert excinfo.value.sanitized_message


class TestSnapshotProjection:
    def test_read_targets_projected_as_exact_ids(self) -> None:
        """JSONB dict 必须还原为精确 ID 扁平元组（不是字段名）。"""

        row = SimpleNamespace(
            marker_id="marker-1",
            marker_kind="session",
            target_id="s1",
            last_seq=3,
            batch_seq=0,
            postponed_through_seq=0,
            session_id="s1",
            read_targets={"message_ids": ["m-1", "m-2"], "artifact_refs": ["a-1@2"]},
        )
        entry = AgentDao(None)._read_mark_entry_from_row(row, "u1/s1/read")
        assert entry.read_targets == ("m-1", "m-2", "a-1@2")
        # 不是 JSONB 字段名（修复前 list(dict) 的产物）
        assert sorted(entry.read_targets or ()) == ["a-1@2", "m-1", "m-2"]

    def test_legacy_flat_array_still_decodes(self) -> None:
        row = SimpleNamespace(
            marker_id="marker-1",
            marker_kind="session",
            target_id="s1",
            last_seq=0,
            batch_seq=0,
            postponed_through_seq=0,
            session_id="s1",
            read_targets=["m-1"],
        )
        assert AgentDao(None)._read_mark_entry_from_row(row, "u1/s1/read").read_targets == ("m-1",)

    def test_unexpected_shape_is_unreadable_fact(self) -> None:
        row = SimpleNamespace(
            marker_id="marker-1",
            marker_kind="session",
            target_id="s1",
            last_seq=0,
            batch_seq=0,
            postponed_through_seq=0,
            session_id="s1",
            read_targets=42,
        )
        with pytest.raises(AgentServiceError) as excinfo:
            AgentDao(None)._read_mark_entry_from_row(row, "u1/s1/read")
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN


class TestOwnershipAndWatermark:
    async def test_same_session_ids_accepted_and_persisted(self) -> None:
        conn = _ReadMarkConn(message_ids={"m-1", "m-2"}, artifact_rows={("a-1", 2)})
        receipt = await _dao(conn).save_read_mark_max(_command(message_ids=("m-1", "m-2"), artifact_refs=("a-1@2",)))
        assert receipt.last_seq == 0
        assert receipt.read_target_count == 3
        assert receipt.replayed is False
        assert conn.writes[-1]["read_targets"] == {
            "message_ids": ["m-1", "m-2"],
            "artifact_refs": ["a-1@2"],
        }

    async def test_other_session_message_rejected_without_write(self) -> None:
        conn = _ReadMarkConn(message_ids={"m-2"})
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_read_mark_max(_command(message_ids=("m-1", "m-2")))
        _assert_rejected(excinfo)
        assert conn.writes == []

    async def test_forged_artifact_ref_rejected_without_write(self) -> None:
        conn = _ReadMarkConn(artifact_rows={("a-1", 1)})
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_read_mark_max(_command(artifact_refs=("a-1@2",)))
        _assert_rejected(excinfo)
        assert conn.writes == []

    async def test_malformed_artifact_ref_rejected(self) -> None:
        conn = _ReadMarkConn()
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_read_mark_max(_command(artifact_refs=("a-1",)))
        _assert_rejected(excinfo)
        assert conn.writes == []

    async def test_future_watermark_rejected_without_write(self) -> None:
        conn = _ReadMarkConn(session_event_seq=5)
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_read_mark_max(_command(observed_through_seq=6, observation_scope_digest=_DIGEST))
        _assert_rejected(excinfo)
        assert conn.writes == []

    async def test_out_of_order_watermark_stays_monotone(self) -> None:
        conn = _ReadMarkConn(marker_row=_marker_row(last_seq=42))
        receipt = await _dao(conn).save_read_mark_max(
            _command(observed_through_seq=7, observation_scope_digest=_DIGEST)
        )
        assert receipt.last_seq == 42
        assert receipt.replayed is True
        assert conn.writes == []


class TestObservationScopeBinding:
    async def test_scope_digest_recorded_with_watermark(self) -> None:
        conn = _ReadMarkConn(session_event_seq=42)
        receipt = await _dao(conn).save_read_mark_max(
            _command(observed_through_seq=42, observation_scope_digest=_DIGEST)
        )
        assert receipt.last_seq == 42
        assert conn.writes[-1]["read_targets"]["observation_scope_digest"] == _DIGEST

    async def test_scope_digest_mismatch_rejected(self) -> None:
        conn = _ReadMarkConn(
            session_event_seq=100,
            marker_row=_marker_row(
                last_seq=42,
                read_targets={
                    "message_ids": [],
                    "artifact_refs": [],
                    "observation_scope_digest": _DIGEST,
                },
            ),
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_read_mark_max(_command(observed_through_seq=43, observation_scope_digest="e" * 64))
        _assert_rejected(excinfo)
        assert conn.writes == []

    async def test_same_scope_digest_replay_unchanged(self) -> None:
        conn = _ReadMarkConn(
            session_event_seq=42,
            marker_row=_marker_row(
                last_seq=42,
                read_targets={
                    "message_ids": [],
                    "artifact_refs": [],
                    "observation_scope_digest": _DIGEST,
                },
            ),
        )
        receipt = await _dao(conn).save_read_mark_max(
            _command(observed_through_seq=42, observation_scope_digest=_DIGEST)
        )
        assert receipt.replayed is True
        assert conn.writes == []
