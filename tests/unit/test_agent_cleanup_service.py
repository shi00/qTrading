"""E-06-2a 清理确认合同单元测试（无数据库；DAO 集成分片另测）。

覆盖：显式 confirmed、原 preview/revision 绑定、准入未过零副作用、
fence 只提交一次、重复确认幂等、retry_pending 保原集合、摘要顺序无关。
"""

from __future__ import annotations

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, CleanupPrecondition
from services.agent_service.cleanup import (
    CleanupConfirmCommand,
    CleanupJobRecord,
    CleanupJobState,
    CleanupService,
    preview_digest,
)

_IDS = ("wf-b", "wf-a")


def _record(**overrides: object) -> CleanupJobRecord:
    values: dict[str, object] = {
        "user_scope": "owner-1",
        "cleanup_job_id": "job-1",
        "owner_ref": "session-1",
        "preview_digest": preview_digest(("session-1",), _IDS),
        "expected_revision": 4,
        "exact_ids": _IDS,
        "phase": CleanupJobState.PREPARED,
        "fence_committed": False,
    }
    values.update(overrides)
    return CleanupJobRecord(**values)  # type: ignore[arg-type]  # 测试受控覆盖


class _Store:
    def __init__(self, record: CleanupJobRecord | None) -> None:
        self.record = record
        self.commits: list[CleanupJobRecord] = []

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        if self.record is None:
            return None
        if (self.record.user_scope, self.record.cleanup_job_id) != (user_scope, cleanup_job_id):
            return None
        return self.record

    async def commit_fence(self, record: CleanupJobRecord) -> None:
        self.commits.append(record)
        self.record = record


def _precondition(**overrides: bool) -> CleanupPrecondition:
    values: dict[str, bool] = {
        "scope_settled": True,
        "writers_exited": True,
        "closure_known": True,
        "preview_valid": True,
        "new_reference_conflict": False,
    }
    values.update(overrides)
    return CleanupPrecondition(**values)  # type: ignore[arg-type]  # 测试受控覆盖


def _command(record: CleanupJobRecord, **overrides: object) -> CleanupConfirmCommand:
    values: dict[str, object] = {
        "user_scope": record.user_scope,
        "cleanup_job_id": record.cleanup_job_id,
        "command_id": "cmd-1",
        "preview_digest": record.preview_digest,
        "expected_revision": record.expected_revision,
        "confirmed": True,
    }
    values.update(overrides)
    return CleanupConfirmCommand(**values)  # type: ignore[arg-type]  # 测试受控覆盖


class TestConfirmationBinding:
    @pytest.mark.asyncio
    async def test_explicit_confirmation_required(self) -> None:
        store = _Store(_record())
        service = CleanupService(store)
        with pytest.raises(AgentServiceError) as exc:
            await service.confirm(_command(_record(), confirmed=False), _precondition())
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert store.commits == []

    @pytest.mark.asyncio
    async def test_digest_mismatch_does_not_commit_fence(self) -> None:
        record = _record()
        store = _Store(record)
        service = CleanupService(store)
        with pytest.raises(AgentServiceError) as exc:
            await service.confirm(_command(record, preview_digest="0" * 64), _precondition())
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert store.commits == []

    @pytest.mark.asyncio
    async def test_stale_revision_rejected(self) -> None:
        record = _record()
        store = _Store(record)
        service = CleanupService(store)
        with pytest.raises(AgentServiceError) as exc:
            await service.confirm(_command(record, expected_revision=3), _precondition())
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert store.commits == []

    @pytest.mark.asyncio
    async def test_missing_job_is_unavailable(self) -> None:
        service = CleanupService(_Store(None))
        with pytest.raises(AgentServiceError) as exc:
            await service.confirm(_command(_record()), _precondition())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE


class TestAdmissionGate:
    @pytest.mark.asyncio
    async def test_writers_running_blocks_without_side_effects(self) -> None:
        record = _record()
        store = _Store(record)
        service = CleanupService(store)
        with pytest.raises(AgentServiceError) as exc:
            await service.confirm(_command(record), _precondition(writers_exited=False))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert store.commits == []

    @pytest.mark.asyncio
    async def test_new_reference_after_fence_blocks(self) -> None:
        record = _record()
        store = _Store(record)
        service = CleanupService(store)
        with pytest.raises(AgentServiceError) as exc:
            await service.confirm(_command(record), _precondition(new_reference_conflict=True))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert store.commits == []


class TestFenceCommit:
    @pytest.mark.asyncio
    async def test_allowed_confirm_commits_fence_once(self) -> None:
        record = _record()
        store = _Store(record)
        service = CleanupService(store)
        receipt = await service.confirm(_command(record), _precondition())
        assert receipt.replayed is False
        assert receipt.phase is CleanupJobState.PREPARED
        assert receipt.exact_ids == ("wf-a", "wf-b")
        assert [commit.fence_committed for commit in store.commits] == [True]

    @pytest.mark.asyncio
    async def test_second_confirm_is_idempotent(self) -> None:
        record = _record()
        store = _Store(record)
        service = CleanupService(store)
        await service.confirm(_command(record), _precondition())
        receipt = await service.confirm(_command(record), _precondition())
        assert receipt.replayed is True
        assert len(store.commits) == 1
        assert receipt.exact_ids == ("wf-a", "wf-b")

    @pytest.mark.asyncio
    async def test_retry_pending_reuses_original_set(self) -> None:
        record = _record(phase=CleanupJobState.RETRY_PENDING)
        store = _Store(record)
        service = CleanupService(store)
        receipt = await service.confirm(_command(record), _precondition())
        assert receipt.phase is CleanupJobState.RETRY_PENDING
        # 复用原集合（规范化排序），不扩大范围。
        assert receipt.exact_ids == ("wf-a", "wf-b")
        assert set(receipt.exact_ids) == set(record.exact_ids)

    @pytest.mark.asyncio
    async def test_verified_job_reports_replay_without_new_fence(self) -> None:
        record = _record(phase=CleanupJobState.VERIFIED, fence_committed=True)
        store = _Store(record)
        service = CleanupService(store)
        receipt = await service.confirm(_command(record), _precondition())
        assert receipt.replayed is True
        assert receipt.admission.reason == "already_verified"
        assert store.commits == []


class TestPreviewDigest:
    def test_digest_is_order_insensitive_and_deduplicated(self) -> None:
        assert preview_digest(("s",), ("b", "a", "b")) == preview_digest(("s",), ("a", "b"))

    def test_digest_changes_with_fields_or_ids(self) -> None:
        assert preview_digest(("s",), ("a",)) != preview_digest(("s", "extra"), ("a",))
        assert preview_digest(("s",), ("a",)) != preview_digest(("s",), ("a", "b"))
