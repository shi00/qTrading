"""资料许可认领轮归属守卫契约测试（检视 M19）。

``claim_data_consent`` 必须在同一事务内由 operation 反查权威
execution/round/owner_epoch/run_epoch，并与许可绑定的 execution/round 及 claim
声明的代次逐项核对：借用其他轮/旧代次的合法 operation 一律 ``POLICY_DENIED``
且零写（不落 claim、不产生许可事实）；准确 operation 正常追加，已提交重放返回
原事实，缺 operation 事实 fail-closed ``INPUT_INVALID``。

真实行锁与 PG 约束行为由 sidecar PG 集成测试
（``tests/integration/test_agent_dao_data_consent.py::TestClaimDataConsent``）
覆盖；本文件用 capture conn 断言校验分支、写入字段与「拒绝先于写事实」。
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
    AgentValidationError,
    ConnectionAuthorizationRef,
    DataAttemptOutcome,
    DataConsentClaim,
    encode_data_consent_claim,
    validate_data_consent_claim,
)
from data.persistence.daos.agent_dao import AgentDao
from tests.unit.test_agent_data_consent import (
    ALL_SCOPE_DIGEST,
    LOCAL_FP_A_R2,
    _authorization,
    _binding_payload,
)

pytestmark = pytest.mark.unit


class _ClaimGuardConn:
    """按 SQL 目标表返回许可决定/operation 事实，并记录写入序列。"""

    def __init__(self, *, decision_payload: dict[str, Any], operation: Any) -> None:
        self.decision_payload = decision_payload
        self.operation = operation
        self.reads: list[str] = []
        self.updates: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        result = MagicMock()
        result.first.return_value = None
        result.rowcount = 1
        if sql.lstrip().upper().startswith("UPDATE"):
            self.updates.append(statement)
        elif "FROM agent_decision" in sql:
            self.reads.append("agent_decision")
            result.first.return_value = SimpleNamespace(data_consent_payload=self.decision_payload)
        elif "FROM agent_operation" in sql:
            self.reads.append("agent_operation")
            result.first.return_value = self.operation
        return result


def _dao(conn: _ClaimGuardConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _binding(**overrides: Any) -> dict[str, Any]:
    base = _binding_payload({"expires_at": "2099-01-01T00:00:00.000000Z"})
    base.update(overrides)
    return base


def _operation(
    *,
    execution_id: str = "exec-1",
    round_id: str = "round-1",
    owner_epoch: int = 1,
    run_epoch: int = 1,
    claims: list[dict[str, Any]] | None = None,
) -> Any:
    return SimpleNamespace(
        data_consent_claims=claims,
        execution_id=execution_id,
        round_id=round_id,
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
    )


def _claim(**overrides: Any) -> DataConsentClaim:
    values: dict[str, Any] = {
        "schema_version": 1,
        "consent_id": "dec-consent-1",
        "consent_revision": 1,
        "connection_authorization_ref": ConnectionAuthorizationRef(
            connection_id="synthetic-local-A",
            target_fingerprint=LOCAL_FP_A_R2,
            purpose="agent_research",
            scope_version=1,
            verified_connection_grant_id=None,
        ),
        "security_revision": 0,
        "credential_version": None,
        "scope_version": 1,
        "source_scope_digest": ALL_SCOPE_DIGEST,
        "purpose": "agent_research",
        "target_fingerprint": LOCAL_FP_A_R2,
        "attempt_id": "attempt-1",
        "operation_id": "op-1",
        "owner_epoch": 1,
        "run_epoch": 1,
        "authorized_at": "2026-06-01T12:00:00.000000Z",
        "outcome": DataAttemptOutcome.ALLOW,
    }
    values.update(overrides)
    return DataConsentClaim(**values)


def _appended_claims(update: Any) -> list[dict[str, Any]]:
    return list(dict(update.compile().params)["data_consent_claims"])


class TestClaimDataConsentAuthority:
    async def test_foreign_round_operation_denied_zero_write(self) -> None:
        # A 轮有效许可 + B 轮合法 operation + 新 attempt：不得借道落 claim。
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=_operation(round_id="round-2"))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert exc.value.code == AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "operation_scope_mismatch"
        assert conn.reads == ["agent_decision", "agent_operation"]
        assert conn.updates == []

    async def test_foreign_execution_operation_denied_zero_write(self) -> None:
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=_operation(execution_id="exec-2"))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert exc.value.code == AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "operation_scope_mismatch"
        assert conn.updates == []

    async def test_stale_owner_epoch_denied_zero_write(self) -> None:
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=_operation(owner_epoch=0))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert exc.value.code == AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "operation_owner_epoch_mismatch"
        assert conn.updates == []

    async def test_stale_run_epoch_denied_zero_write(self) -> None:
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=_operation(run_epoch=2))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert exc.value.code == AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "operation_run_epoch_mismatch"
        assert conn.updates == []

    async def test_matching_operation_claims(self) -> None:
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=_operation())
        fact = await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert fact.replayed is False
        assert fact.claim.outcome is DataAttemptOutcome.ALLOW
        assert len(conn.updates) == 1
        assert _appended_claims(conn.updates[0]) == [encode_data_consent_claim(_claim())]

    async def test_replay_returns_original_fact(self) -> None:
        stored = encode_data_consent_claim(_claim())
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=_operation(claims=[stored]))
        fact = await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert fact.replayed is True
        assert fact.claim == _claim()
        assert conn.updates == []

    async def test_missing_operation_fail_closed(self) -> None:
        conn = _ClaimGuardConn(decision_payload=_binding(), operation=None)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_data_consent(_authorization(), _claim())
        assert exc.value.code == AgentErrorCode.INPUT_INVALID
        assert conn.updates == []


def test_claim_epoch_mismatch_rejected_by_core() -> None:
    """claim 代次必须与被核权请求一致（core 一致性强断言，M19 配套）。"""

    with pytest.raises(AgentValidationError) as exc:
        validate_data_consent_claim(_claim(owner_epoch=2), _authorization(), recomputed_digest=ALL_SCOPE_DIGEST)
    assert exc.value.code == "identity_mismatch"
