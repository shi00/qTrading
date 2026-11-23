"""I81：清理准入事实与原生核验的生产提供器。"""

from __future__ import annotations

import asyncio

import pytest

from core.agent_types import CleanupJobRecord, CleanupNativeFacts, CleanupPhase, CleanupPrecondition
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError
from services.agent_service.cleanup import preview_digest
from services.agent_service.cleanup_ports import (
    DaoCleanupAdmissionFacts,
    DaoNativeVerification,
    DbosDeletionClient,
    preview_is_valid,
)


def _record(*ids: str) -> CleanupJobRecord:
    return CleanupJobRecord(
        user_scope="u1",
        cleanup_job_id="job-1",
        owner_ref="u1",
        preview_digest=preview_digest(("u1",), tuple(ids)),
        expected_revision=1,
        exact_ids=tuple(ids),
        phase=CleanupPhase.PREPARED,
        fence_committed=True,
    )


def _facts(**overrides: object) -> CleanupNativeFacts:
    values: dict[str, object] = {
        "resolvable_ids": ("wf-1",),
        "foreign_ids": (),
        "non_terminal_ids": (),
        "externally_referenced_ids": (),
        "writers_running_ids": (),
        "unknown_delivery_ids": (),
        "closure_complete": True,
        "new_reference_conflict": False,
    }
    values.update(overrides)
    return CleanupNativeFacts(**values)  # type: ignore[arg-type]  # [reason: 用例以 kwargs 覆盖事实字段，取值在用例内约束]


class _Dao:
    def __init__(
        self,
        record: CleanupJobRecord | None,
        facts: CleanupNativeFacts | None = None,
        *,
        error: Exception | None = None,
        manifest: dict[str, object] | None = None,
    ) -> None:
        self.record = record
        self.facts = facts or _facts()
        self.error = error
        self.manifest: dict[str, object] | None = {"preview_fields": ["u1"]} if manifest is None else manifest

    async def load_cleanup_job(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        if self.error is not None:
            raise self.error
        return self.record

    async def read_cleanup_native_facts(
        self,
        *,
        user_scope: str,
        ids: tuple[str, ...],
        session_ids: tuple[str, ...] = (),
        asset_keys: tuple[tuple[str, str], ...] = (),
    ) -> CleanupNativeFacts:
        if self.error is not None:
            raise self.error
        return self.facts

    async def read_cleanup_content_manifest(self, user_scope: str, cleanup_job_id: str) -> dict[str, object] | None:
        if self.error is not None:
            raise self.error
        return self.manifest


class TestDaoCleanupAdmissionFacts:
    async def test_missing_record_denies(self) -> None:
        assert await DaoCleanupAdmissionFacts(_Dao(None)).derive_precondition("u1", "job-1") is None

    async def test_unresolvable_id_denies(self) -> None:
        dao = _Dao(_record("wf-1", "wf-2"), _facts(resolvable_ids=("wf-1",)))
        assert await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1") is None

    async def test_read_failure_denies(self) -> None:
        dao = _Dao(_record("wf-1"), error=DatabaseQueryError("db down"))
        assert await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1") is None

    async def test_clean_facts_allow(self) -> None:
        precondition = await DaoCleanupAdmissionFacts(_Dao(_record("wf-1"))).derive_precondition("u1", "job-1")
        assert precondition == CleanupPrecondition(
            scope_settled=True,
            writers_exited=True,
            closure_known=True,
            preview_valid=True,
            new_reference_conflict=False,
            unknown_deliveries=False,
        )

    async def test_non_terminal_blocks_scope_settled(self) -> None:
        dao = _Dao(_record("wf-1"), _facts(non_terminal_ids=("wf-1",)))
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and not precondition.scope_settled

    async def test_unknown_delivery_is_reported(self) -> None:
        dao = _Dao(_record("wf-1"), _facts(unknown_delivery_ids=("wf-1",)))
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and precondition.unknown_deliveries

    async def test_external_reference_is_conflict(self) -> None:
        dao = _Dao(_record("wf-1"), _facts(externally_referenced_ids=("wf-1",)))
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and precondition.new_reference_conflict

    async def test_closure_not_verified_denies(self) -> None:
        """P1-11：会话/资产闭包未完整核查时 closure_known 不得硬编码为真。"""

        dao = _Dao(_record("wf-1"), _facts(closure_complete=False))
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and not precondition.closure_known

    async def test_cross_session_reference_is_conflict(self) -> None:
        """P1-11：集合外会话的新引用由事实派生，不依赖原生 ID 的派生引用。"""

        dao = _Dao(_record("wf-1"), _facts(new_reference_conflict=True))
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and precondition.new_reference_conflict

    async def test_missing_preview_manifest_invalidates_preview(self) -> None:
        """P1-11：预览绑定字段不可得时 preview_valid=False（不把无从核对当作有效）。"""

        dao = _Dao(_record("wf-1"), manifest={})
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and not precondition.preview_valid

    async def test_preview_digest_mismatch_invalidates_preview(self) -> None:
        record = _record("wf-1")
        dao = _Dao(record, manifest={"preview_fields": ["other-owner"]})
        precondition = await DaoCleanupAdmissionFacts(dao).derive_precondition("u1", "job-1")
        assert precondition is not None and not precondition.preview_valid

    def test_preview_is_valid_matches_recomputed_digest(self) -> None:
        record = _record("wf-1")
        assert preview_is_valid(record, {"preview_fields": ["u1"]}) is True
        assert preview_is_valid(record, {"preview_fields": []}) is False
        assert preview_is_valid(record, None) is False

    async def test_engine_disposed_and_cancellation_propagate(self) -> None:
        with pytest.raises(EngineDisposedError) as exc:
            await DaoCleanupAdmissionFacts(_Dao(_record("wf-1"), error=EngineDisposedError("x"))).derive_precondition(
                "u1", "job-1"
            )
        assert "x" in str(exc.value)
        with pytest.raises(asyncio.CancelledError) as exc:
            await DaoCleanupAdmissionFacts(_Dao(_record("wf-1"), error=asyncio.CancelledError())).derive_precondition(
                "u1", "job-1"
            )
        assert type(exc.value) is asyncio.CancelledError


class TestDaoNativeVerification:
    async def test_empty_ids_are_trivially_authoritative(self) -> None:
        assert (await DaoNativeVerification(_Dao(None)).verify((), user_scope="u1")).authoritative

    async def test_unknown_id_is_not_authoritative(self) -> None:
        dao = _Dao(_record("wf-1"), _facts(resolvable_ids=("wf-1",)))
        result = await DaoNativeVerification(dao).verify(("wf-1", "wf-9"), user_scope="u1")
        assert result.authoritative is False
        assert result.refusal_reason() == "verification_unavailable"

    async def test_resolvable_foreign_id_is_authoritative_refusal(self) -> None:
        dao = _Dao(_record("wf-1"), _facts(resolvable_ids=(), foreign_ids=("wf-1",)))
        result = await DaoNativeVerification(dao).verify(("wf-1",), user_scope="u1")
        assert result.authoritative is True
        assert result.refusal_reason() == "ownership_mismatch"

    async def test_clean_verification_has_no_reason(self) -> None:
        result = await DaoNativeVerification(_Dao(_record("wf-1"))).verify(("wf-1",), user_scope="u1")
        assert result.refusal_reason() is None

    async def test_read_failure_is_not_authoritative(self) -> None:
        dao = _Dao(_record("wf-1"), error=DatabaseQueryError("db down"))
        result = await DaoNativeVerification(dao).verify(("wf-1",), user_scope="u1")
        assert result.authoritative is False


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None:
        self.calls.append((workflow_ids, delete_children))


class TestDbosDeletionClient:
    def test_forwards_to_dbos_client(self) -> None:
        client = _Client()
        DbosDeletionClient(client).delete_workflows(["wf-1"], delete_children=True)
        assert client.calls == [(["wf-1"], True)]

    def test_missing_api_fails_closed(self) -> None:
        with pytest.raises(TypeError) as exc:
            DbosDeletionClient(object())  # type: ignore[arg-type]  # [reason: 显式验证缺 API 时 fail-closed]
        assert "delete_workflows" in str(exc.value)
