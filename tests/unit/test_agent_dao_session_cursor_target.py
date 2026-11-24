"""会话/快照游标的目标绑定（T-05）单测。

覆盖：
- 读取目标摘要（用户域/会话/执行/轮次）稳定且逐字段可区分；
- 摘要落在游标语法冻结的 16 位十六进制段内（decode_session_read_query 接受）；
- 消息/成果游标来自不同目标 → INPUT_INVALID（不静默混用）；
- get_session_read 在触碰数据库前即以 REVISION_CONFLICT 拒绝跨目标游标。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentErrorCode, AgentServiceError, SessionReadQuery, decode_session_read_query
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_USER_SCOPE = "local-desktop"
_DIGEST = "0123456789abcdef"


def _target(**overrides: str | None) -> str:
    values: dict[str, str | None] = {
        "user_scope": _USER_SCOPE,
        "session_id": "s-1",
        "execution_id": None,
        "round_id": None,
    }
    values.update(overrides)
    return AgentDao._session_cursor_target(**values)  # type: ignore[arg-type]  # 测试受控入参


def _query_wire(**overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "session_id": "s-1",
        "limit": 50,
        "message_cursor": None,
        "artifact_cursor": None,
        "execution_id": None,
        "round_id": None,
        "draft_revision": None,
    }
    values.update(overrides)
    return values


class TestCursorTargetDigest:
    def test_digest_is_stable_and_sixteen_hex(self) -> None:
        digest = _target()
        assert digest == _target()
        assert len(digest) == 16
        assert all(char in "0123456789abcdef" for char in digest)
        decode_session_read_query(_query_wire(message_cursor=f"m:{digest}:3"))
        decode_session_read_query(_query_wire(artifact_cursor=f"a:{digest}:2026-11-17T02:00:00.000000Z:a-1:1"))

    def test_digest_distinguishes_each_target_field(self) -> None:
        baseline = _target()
        assert _target(user_scope="other-user") != baseline
        assert _target(session_id="s-2") != baseline
        assert _target(execution_id="e-1") != baseline
        assert _target(execution_id="e-1", round_id="r-1") != _target(execution_id="e-1")


class TestMixedCursorTargets:
    def test_message_and_artifact_cursors_from_different_targets_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            AgentDao._parse_session_cursors(
                "m:0123456789abcdef:3",
                "a:fedcba9876543210:2026-11-17T02:00:00.000000Z:a-1:1",
                "u/s",
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    def test_same_target_cursors_parsed(self) -> None:
        digest, seq, key = AgentDao._parse_session_cursors(
            f"m:{_DIGEST}:3",
            f"a:{_DIGEST}:2026-11-17T02:00:00.000000Z:a-1:2",
            "u/s",
        )
        assert digest == _DIGEST
        assert seq == 3
        assert key is not None and key[1:] == ("a-1", 2)


class TestSessionReadTargetBinding:
    @pytest.mark.asyncio
    async def test_cross_target_cursor_rejected_before_database(self) -> None:
        engine = MagicMock(spec=AsyncEngine)
        dao = AgentDao(engine)
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_session_read(
                SessionReadQuery(session_id="s-1", limit=10, message_cursor=f"m:{'f' * 16}:3"),
                user_scope=_USER_SCOPE,
            )
        assert exc.value.code is AgentErrorCode.REVISION_CONFLICT
        cast(MagicMock, engine).connect.assert_not_called()
