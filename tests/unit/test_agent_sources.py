"""E-01-3 业务来源登记服务单元测试（PIT / 显式选择 / 幂等契约透传）。"""

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    BusinessSourceRegistrationReceipt,
    BusinessSourceRegistrationRequest,
    Purpose,
    ReadState,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceRegistrationRequest,
    SourceVersionRef,
    VerificationState,
    sha256_hex,
)
from services.agent_service.sources import BusinessSourceService

_DIGEST = sha256_hex(b"e01-source")


def _source(
    source_id: str = "src-1",
    version: str = "1",
    *,
    available_at: str | None = "2025-12-31T00:00:00.000000Z",
    read_state: ReadState = ReadState.READY,
) -> SourceRef:
    return SourceRef(
        user_scope="scope-1",
        source_id=source_id,
        version=version,
        digest=_DIGEST,
        source_kind=SourceKind.ATTACHMENT,
        display_name=f"{source_id}.xlsx",
        object_ref=None,
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id=source_id,
                source_version=version,
                source_digest=_DIGEST,
            ),
            selection=Selection(kind="all"),
        ),
        available_at=available_at,
        period=None,
        unit_note=None,
        read_state=read_state,
        missing_reason=None,
        provenance_refs=(),
    )


def _item(
    source_id: str = "src-1",
    version: str = "1",
    *,
    available_at: str | None = "2025-12-31T00:00:00.000000Z",
    read_state: ReadState = ReadState.READY,
) -> BusinessSourceItem:
    return BusinessSourceItem(
        source=_source(source_id, version, available_at=available_at, read_state=read_state),
        verification=VerificationState.UNVERIFIED,
    )


class FakeDao:
    def __init__(self) -> None:
        self.requests: list[BusinessSourceRegistrationRequest] = []

    async def register_business_sources(
        self, request: BusinessSourceRegistrationRequest
    ) -> BusinessSourceRegistrationReceipt:
        self.requests.append(request)
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
            link_ids=tuple(f"link-{index}" for index, _item in enumerate(request.items)),
            replayed=False,
        )


def _request(
    *,
    purpose: Purpose = Purpose.END_OF_DAY_RESEARCH,
    cutoff: str | None = "2026-01-01T00:00:00.000000Z",
    selected: tuple[BusinessSourceItem, ...] | None = None,
    unselected: tuple[BusinessSourceItem, ...] = (),
) -> SourceRegistrationRequest:
    return SourceRegistrationRequest(
        user_scope="scope-1",
        consumer_kind="execution",
        consumer_id="exec-1",
        consumer_version="1",
        purpose=purpose,
        decision_cutoff=cutoff,
        selected=selected if selected is not None else (_item(),),
        unselected=unselected,
    )


class TestRegistrationSelection:
    @pytest.mark.asyncio
    async def test_only_selected_sources_reach_dao(self) -> None:
        dao = FakeDao()
        selected = (_item("selected-1"), _item("selected-2"))
        unselected = (_item("unselected-1"),)
        receipt = await BusinessSourceService(dao).register(_request(selected=selected, unselected=unselected))
        assert [item.source.source_id for item in dao.requests[0].items] == ["selected-1", "selected-2"]
        assert all(ref.source_id != "unselected-1" for ref in receipt.registered)
        assert receipt.link_ids == ("link-0", "link-1")

    @pytest.mark.asyncio
    async def test_empty_selection_returns_empty_receipt_without_dao(self) -> None:
        dao = FakeDao()
        receipt = await BusinessSourceService(dao).register(_request(selected=()))
        assert receipt.registered == ()
        assert dao.requests == []

    @pytest.mark.asyncio
    async def test_duplicate_or_overlapping_selection_rejected_before_dao(self) -> None:
        dao = FakeDao()
        service = BusinessSourceService(dao)
        with pytest.raises(AgentServiceError) as exc_dup:
            await service.register(_request(selected=(_item(), _item())))
        assert exc_dup.value.code is AgentErrorCode.INPUT_INVALID
        with pytest.raises(AgentServiceError) as exc_overlap:
            await service.register(_request(selected=(_item(),), unselected=(_item(),)))
        assert exc_overlap.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.requests == []


class TestPointInTimeGate:
    @pytest.mark.asyncio
    async def test_historical_decision_accepts_source_available_before_cutoff(self) -> None:
        dao = FakeDao()
        request = _request(purpose=Purpose.HISTORICAL_DECISION, cutoff="2026-01-01T00:00:00.000000Z")
        receipt = await BusinessSourceService(dao).register(request)
        assert len(receipt.registered) == 1
        assert len(dao.requests) == 1

    @pytest.mark.asyncio
    async def test_historical_decision_rejects_source_available_after_cutoff_with_zero_dao_calls(self) -> None:
        dao = FakeDao()
        request = _request(purpose=Purpose.HISTORICAL_DECISION, cutoff="2025-06-01T00:00:00.000000Z")
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert dao.requests == []

    @pytest.mark.asyncio
    async def test_historical_decision_rejects_unknown_available_at(self) -> None:
        dao = FakeDao()
        request = _request(
            purpose=Purpose.HISTORICAL_DECISION,
            cutoff="2026-01-01T00:00:00.000000Z",
            selected=(_item(available_at=None),),
        )
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert dao.requests == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "purpose",
        [Purpose.HISTORICAL_DECISION, Purpose.POST_HOC_EXPLANATION, Purpose.END_OF_DAY_RESEARCH],
    )
    async def test_cutoff_purposes_require_cutoff(self, purpose: Purpose) -> None:
        dao = FakeDao()
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(_request(purpose=purpose, cutoff=None))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.requests == []

    @pytest.mark.asyncio
    async def test_naive_cutoff_rejected(self) -> None:
        dao = FakeDao()
        request = _request(purpose=Purpose.HISTORICAL_DECISION, cutoff="2026-01-01T00:00:00")
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(request)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert dao.requests == []

    @pytest.mark.asyncio
    async def test_end_of_day_research_accepts_source_disclosed_before_day_end(self) -> None:
        dao = FakeDao()
        request = _request(
            purpose=Purpose.END_OF_DAY_RESEARCH,
            cutoff="2026-01-05T17:00:00+08:00",
            selected=(_item(available_at="2026-01-05T16:59:59+08:00"),),
        )
        receipt = await BusinessSourceService(dao).register(request)
        assert len(receipt.registered) == 1
        assert len(dao.requests) == 1

    @pytest.mark.asyncio
    async def test_end_of_day_research_rejects_late_disclosure_with_zero_dao_calls(self) -> None:
        dao = FakeDao()
        request = _request(
            purpose=Purpose.END_OF_DAY_RESEARCH,
            cutoff="2026-01-05T17:00:00+08:00",
            selected=(_item(available_at="2026-01-05T18:00:00+08:00"),),
        )
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "cutoff" in exc.value.sanitized_message
        assert dao.requests == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("purpose", [Purpose.END_OF_DAY_RESEARCH, Purpose.HISTORICAL_DECISION])
    async def test_cutoff_boundary_is_exclusive(self, purpose: Purpose) -> None:
        dao = FakeDao()
        request = _request(
            purpose=purpose,
            cutoff="2026-01-05T17:00:00+08:00",
            selected=(_item(available_at="2026-01-05T17:00:00+08:00"),),
        )
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert dao.requests == []

    @pytest.mark.asyncio
    async def test_end_of_day_research_rejects_unknown_available_at(self) -> None:
        dao = FakeDao()
        request = _request(
            purpose=Purpose.END_OF_DAY_RESEARCH,
            cutoff="2026-01-05T17:00:00+08:00",
            selected=(_item(available_at=None),),
        )
        with pytest.raises(AgentServiceError) as exc:
            await BusinessSourceService(dao).register(request)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "unknown" in exc.value.sanitized_message
        assert dao.requests == []

    @pytest.mark.asyncio
    async def test_past_day_midnight_boundary_is_exclusive(self) -> None:
        dao = FakeDao()
        service = BusinessSourceService(dao)
        cutoff = "2025-06-01T00:00:00+08:00"
        with pytest.raises(AgentServiceError) as exc:
            await service.register(
                _request(
                    purpose=Purpose.HISTORICAL_DECISION,
                    cutoff=cutoff,
                    selected=(_item(available_at="2025-06-01T00:00:00+08:00"),),
                )
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert dao.requests == []
        receipt = await service.register(
            _request(
                purpose=Purpose.HISTORICAL_DECISION,
                cutoff=cutoff,
                selected=(_item(available_at="2025-05-31T23:59:59+08:00"),),
            )
        )
        assert len(receipt.registered) == 1

    @pytest.mark.asyncio
    async def test_concept_purpose_does_not_gate_or_require_cutoff(self) -> None:
        dao = FakeDao()
        request = _request(purpose=Purpose.CONCEPT, cutoff=None, selected=(_item(available_at=None),))
        receipt = await BusinessSourceService(dao).register(request)
        assert len(receipt.registered) == 1
        assert dao.requests[0].items[0].source.available_at is None
