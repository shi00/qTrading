"""清理作业登记的身份核对单测（检视 D26）。

``expected_revision`` 属不可变身份：同 job 同身份同版本重放幂等返回 False；
仅版本不同必须冲突（``REVISION_CONFLICT``）且已登记记录保持不变——否则登记
接受新版、``commit_cleanup_fence`` 仍按库内旧版匹配而永久拒绝该作业。

真实唯一约束/行事实由 sidecar PG 集成测试覆盖；本文件用假连接断言分支与错误码。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CleanupJobRecord,
    CleanupPhase,
    encode_cleanup_identity_set,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_USER_SCOPE = "u-d26"
_JOB_ID = "job-d26"


class _Result:
    def __init__(self, *, first: Any = None) -> None:
        self._first = first

    def first(self) -> Any:
        return self._first

    def scalar_one_or_none(self) -> Any:
        return self._first


class _RegistrationConn:
    """INSERT 必撞唯一约束；读路径回放已登记行的最小行事实。"""

    def __init__(self, existing: CleanupJobRecord) -> None:
        self.existing = existing
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("INSERT INTO agent_cleanup_job"):
            raise IntegrityError(sql, {}, Exception("duplicate key"))
        if "agent_cleanup_job.content_manifest" in sql:
            return _Result(first=None)
        if "FROM agent_cleanup_job" in sql:
            record = self.existing
            return _Result(
                first=(
                    record.user_scope,
                    record.cleanup_job_id,
                    record.owner_ref,
                    record.preview_digest,
                    record.expected_revision,
                    encode_cleanup_identity_set(record),
                    record.phase.value,
                    record.fence_committed,
                    record.prior_error_category,
                )
            )
        raise AssertionError(f"unexpected statement: {sql}")

    def _writes(self) -> list[str]:
        return [sql for sql in self.statements if sql.startswith(("UPDATE ", "DELETE "))]


def _dao(conn: _RegistrationConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    for name in ("begin", "connect"):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        setattr(engine, name, MagicMock(return_value=cm))
    return AgentDao(engine)


def _record(**overrides: object) -> CleanupJobRecord:
    values: dict[str, object] = {
        "user_scope": _USER_SCOPE,
        "cleanup_job_id": _JOB_ID,
        "owner_ref": "session-owner",
        "preview_digest": "a" * 64,
        "expected_revision": 7,
        "exact_ids": ("wf-a", "wf-b"),
        "phase": CleanupPhase.PREPARED,
        "fence_committed": False,
        "session_ids": ("session-1",),
        "asset_keys": (("asset-a", "1"),),
    }
    values.update(overrides)
    return CleanupJobRecord(**values)  # type: ignore[arg-type]  # 测试受控覆盖


class TestCleanupJobRegistrationIdentity:
    @pytest.mark.asyncio
    async def test_same_identity_and_revision_replay_is_idempotent(self) -> None:
        conn = _RegistrationConn(_record())
        assert await _dao(conn).save_cleanup_job(_record()) is False
        assert conn._writes() == []

    @pytest.mark.asyncio
    async def test_revision_only_change_is_conflict_and_keeps_original(self) -> None:
        conn = _RegistrationConn(_record())
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).save_cleanup_job(_record(expected_revision=8))
        assert exc.value.code is AgentErrorCode.REVISION_CONFLICT
        # 冲突不落库：无 UPDATE/DELETE，已登记记录仍为原预期版。
        assert conn._writes() == []
        loaded = await _dao(conn).load_cleanup_job(_USER_SCOPE, _JOB_ID)
        assert loaded is not None
        assert loaded.expected_revision == 7

    @pytest.mark.asyncio
    async def test_identity_change_is_conflict(self) -> None:
        conn = _RegistrationConn(_record())
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).save_cleanup_job(_record(expected_revision=8, exact_ids=("wf-c",)))
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert conn._writes() == []
