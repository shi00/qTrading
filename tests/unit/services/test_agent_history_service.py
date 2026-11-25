"""W-04 清理确认门面单元测试（替代端口，不连数据库）。

覆盖：作业缺失显式失败（不构造空作业）、预览冻结集合计数、确认必须显式、
确认透传原预览摘要/修订、准入阻断原样传播、回执投影保真（retry_pending 非完成）。
"""

from __future__ import annotations

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, CleanupJobRecord, CleanupPhase
from services.agent_service.cleanup import CleanupConfirmCommand
from services.agent_service.cleanup_execution import CleanupExecutionReceipt
from services.agent_history import HistoryCleanupGateway

pytestmark = pytest.mark.unit

_USER_SCOPE = "local-desktop"


def _record(**overrides: object) -> CleanupJobRecord:
    values: dict[str, object] = {
        "user_scope": _USER_SCOPE,
        "cleanup_job_id": "ui-cleanup:s-1",
        "owner_ref": "session-owner",
        "preview_digest": "a" * 64,
        "expected_revision": 3,
        "exact_ids": ("wf-b", "wf-a"),
        "phase": CleanupPhase.PREPARED,
        "fence_committed": False,
        "session_ids": ("s-1",),
        "asset_keys": (("asset-1", "1"),),
    }
    values.update(overrides)
    return CleanupJobRecord(**values)  # type: ignore[arg-type]  # 测试受控覆盖


class _Store:
    def __init__(self, record: CleanupJobRecord | None) -> None:
        self.record = record
        self.loaded: list[tuple[str, str]] = []

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        self.loaded.append((user_scope, cleanup_job_id))
        if self.record is None:
            return None
        if (self.record.user_scope, self.record.cleanup_job_id) != (user_scope, cleanup_job_id):
            return None
        return self.record


class _Execution:
    def __init__(
        self,
        *,
        phase: CleanupPhase = CleanupPhase.VERIFIED,
        error: Exception | None = None,
    ) -> None:
        self.phase = phase
        self.error = error
        self.commands: list[CleanupConfirmCommand] = []

    async def confirm_and_execute(self, command: CleanupConfirmCommand) -> CleanupExecutionReceipt:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return CleanupExecutionReceipt(
            cleanup_job_id=command.cleanup_job_id,
            phase=self.phase,
            replayed=False,
            verified_counts={"agent_ds.datasource_outputs": 0},
            tombstoned={"agent_session": 1},
        )


@pytest.mark.asyncio
async def test_preview_returns_frozen_identity_counts() -> None:
    store = _Store(_record())
    gateway = HistoryCleanupGateway(store=store, execution=_Execution())

    preview = await gateway.preview(_USER_SCOPE, "ui-cleanup:s-1")

    assert preview.cleanup_job_id == "ui-cleanup:s-1"
    assert preview.exact_ids == ("wf-b", "wf-a")
    assert preview.session_ids == ("s-1",)
    assert preview.expected_revision == 3
    assert preview.requires_confirmation is True
    assert store.loaded == [(_USER_SCOPE, "ui-cleanup:s-1")]


@pytest.mark.asyncio
async def test_preview_missing_job_fails_without_creating_job() -> None:
    store = _Store(None)
    gateway = HistoryCleanupGateway(store=store, execution=_Execution())

    with pytest.raises(AgentServiceError) as excinfo:
        await gateway.preview(_USER_SCOPE, "ui-cleanup:absent")

    assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
    assert "not prepared" in str(excinfo.value)


@pytest.mark.asyncio
async def test_confirm_requires_explicit_confirmation() -> None:
    store = _Store(_record())
    execution = _Execution()
    gateway = HistoryCleanupGateway(store=store, execution=execution)

    with pytest.raises(AgentServiceError) as excinfo:
        await gateway.confirm(_USER_SCOPE, "ui-cleanup:s-1", command_id="cmd-1", confirmed=False)

    assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
    assert execution.commands == []


@pytest.mark.asyncio
async def test_confirm_reuses_frozen_preview_and_returns_receipt() -> None:
    store = _Store(_record())
    execution = _Execution()
    gateway = HistoryCleanupGateway(store=store, execution=execution)

    outcome = await gateway.confirm(_USER_SCOPE, "ui-cleanup:s-1", command_id="cmd-1", confirmed=True)

    assert len(execution.commands) == 1
    command = execution.commands[0]
    assert command.preview_digest == "a" * 64
    assert command.expected_revision == 3
    assert command.confirmed is True
    assert outcome.verified is True
    assert outcome.verified_counts == (("agent_ds.datasource_outputs", 0),)


@pytest.mark.asyncio
async def test_confirm_propagates_admission_refusal() -> None:
    refusal = AgentServiceError(
        AgentErrorCode.POLICY_DENIED,
        "cleanup refused: unknown_delivery_pending",
        f"{_USER_SCOPE}/ui-cleanup:s-1",
    )
    gateway = HistoryCleanupGateway(store=_Store(_record()), execution=_Execution(error=refusal))

    with pytest.raises(AgentServiceError) as excinfo:
        await gateway.confirm(_USER_SCOPE, "ui-cleanup:s-1", command_id="cmd-1", confirmed=True)

    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED


@pytest.mark.asyncio
async def test_confirm_retry_pending_is_not_reported_verified() -> None:
    gateway = HistoryCleanupGateway(
        store=_Store(_record(phase=CleanupPhase.RETRY_PENDING)),
        execution=_Execution(phase=CleanupPhase.RETRY_PENDING),
    )

    outcome = await gateway.confirm(_USER_SCOPE, "ui-cleanup:s-1", command_id="cmd-1", confirmed=True)

    assert outcome.verified is False
    assert outcome.phase is CleanupPhase.RETRY_PENDING
