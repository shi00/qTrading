"""I63 受理期业务来源 PIT 准入接线。"""

from __future__ import annotations

import json

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    BusinessSourceRegistrationReceipt,
    BusinessSourceRegistrationRequest,
    CommitState,
    CommandRef,
    Mode,
    Purpose,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceVersionRef,
    SubmissionReceipt,
    VerificationState,
    encode_source_version_ref,
)
from services.agent_service.service import AgentService

_CUTOFF = "2026-11-20T17:00:00+08:00"


def _source(available_at: str | None = "2026-11-20T16:00:00+08:00") -> SourceRef:
    return SourceRef(
        user_scope="u1",
        source_id="backtest:run-1",
        version="1",
        digest="e" * 64,
        source_kind=SourceKind.BUSINESS_SNAPSHOT,
        display_name="回测结果 run-1",
        object_ref="backtest:run-1",
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.BUSINESS_SNAPSHOT,
                source_id="backtest:run-1",
                source_version="1",
                source_digest="e" * 64,
            ),
            selection=Selection(kind="all"),
        ),
        available_at=available_at,
        period=None,
        unit_note=None,
        read_state="ready",
        missing_reason=None,
        provenance_refs=(),
    )


def _ref() -> SourceVersionRef:
    return SourceVersionRef(
        source_kind=SourceKind.BUSINESS_SNAPSHOT,
        source_id="backtest:run-1",
        source_version="1",
        source_digest="e" * 64,
    )


def _item(available_at: str | None = "2026-11-20T16:00:00+08:00") -> BusinessSourceItem:
    return BusinessSourceItem(source=_source(available_at), verification=VerificationState.PROGRAM_VERIFIED)


def _request(*, purpose: Purpose = Purpose.END_OF_DAY_RESEARCH, cutoff: str | None = _CUTOFF, refs: bool = True):
    from core.agent_types import AcceptRequest

    manifest: dict[str, object] = {"mode": "execute", "purpose": purpose.value}
    if cutoff is not None:
        manifest["cutoff"] = cutoff
    if refs:
        manifest["source_refs"] = [encode_source_version_ref(_ref())]
    return AcceptRequest(
        user_scope="u1",
        session_id="s-1",
        source_module="backtest",
        business_request_id="br-1",
        client_command_id="cmd-1",
        expected_revision=1,
        node_id="root",
        workflow_id="wf-1",
        mode=Mode.EXECUTE,
        purpose=purpose,
        text="研究该回测",
        manifest_payload=json.dumps(manifest),
        source_display_name="用户消息",
    )


def _receipt() -> SubmissionReceipt:
    return SubmissionReceipt(
        command=CommandRef("u1", "backtest", "br-1", "cmd-1", 1),
        command_record_id="cr-1",
        session_id="s-1",
        execution_id="ex-1",
        workflow_id="wf-1",
        intent_digest="f" * 64,
        commit_state=CommitState.COMMITTED,
        verified_at="2026-11-20T09:00:00.000000Z",
        next_action=None,
    )


class _Dao:
    def __init__(self, items: tuple[BusinessSourceItem, ...]) -> None:
        self.items = items
        self.registered: list[BusinessSourceRegistrationRequest] = []
        self.reads = 0

    async def read_business_source_items(self, *, user_scope: str, refs: tuple[SourceVersionRef, ...]):
        self.reads += 1
        return self.items

    async def register_business_sources(self, request: BusinessSourceRegistrationRequest):
        self.registered.append(request)
        return BusinessSourceRegistrationReceipt(
            registered=tuple(
                SourceVersionRef(
                    source_kind=item.source.source_kind,
                    source_id=item.source.source_id,
                    source_version=item.source.version,
                    source_digest=item.source.digest,
                )
                for item in request.items
            ),
            link_ids=("link-1",),
            replayed=False,
        )


async def _admit(dao: _Dao, request) -> None:
    service = AgentService(dao, None)  # type: ignore[arg-type]  # [reason: 本用例只触达来源准入，不构造费用账本]
    await service._admit_business_sources(request, _receipt())


class TestAcceptSourceAdmission:
    async def test_source_after_cutoff_blocks_dispatch(self) -> None:
        dao = _Dao((_item("2026-11-20T18:00:00+08:00"),))
        with pytest.raises(AgentServiceError) as exc:
            await _admit(dao, _request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert dao.registered == []

    async def test_source_exactly_at_cutoff_is_excluded(self) -> None:
        dao = _Dao((_item(_CUTOFF),))
        with pytest.raises(AgentServiceError) as exc:
            await _admit(dao, _request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert dao.registered == []

    async def test_missing_cutoff_is_rejected(self) -> None:
        dao = _Dao((_item(),))
        with pytest.raises(AgentServiceError) as exc:
            await _admit(dao, _request(cutoff=None))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.registered == []

    async def test_no_sources_skips_database(self) -> None:
        dao = _Dao(())
        await _admit(dao, _request(refs=False))
        assert dao.reads == 0
        assert dao.registered == []

    async def test_admitted_source_registers_execution_consumer(self) -> None:
        dao = _Dao((_item(),))
        await _admit(dao, _request())
        assert len(dao.registered) == 1
        registered = dao.registered[0]
        assert (registered.consumer_kind, registered.consumer_id, registered.consumer_version) == (
            "execution",
            "ex-1",
            "1",
        )
        assert [item.source.source_id for item in registered.items] == ["backtest:run-1"]

    async def test_concept_purpose_allows_unknown_cutoff(self) -> None:
        dao = _Dao((_item(),))
        await _admit(dao, _request(purpose=Purpose.CONCEPT, cutoff=None))
        assert len(dao.registered) == 1
