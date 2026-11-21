"""``SourceReadQuery`` 合同与 ``AgentService.read_source`` 门面（DD §4.2，F-02-1）。

覆盖：

- 查询 wire 形状（8 键、null 不省略、取值规范）与上下文互斥/完整性；
- 定位与源身份不一致拒绝（不按名称/摘要猜）；
- 服务门面委托 DAO、关闭后 fail-closed。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    Selection,
    SourceKind,
    SourceLocator,
    SourceReadQuery,
    SourceVersionRef,
    encode_source_read_query,
)
from services.agent_service.service import AgentService

_DIGEST = "a" * 64


def _locator(source_id: str = "src-1", version: str = "v1", digest: str = _DIGEST) -> SourceLocator:
    return SourceLocator(
        locator_kind="source_range",
        origin_ref=SourceVersionRef(
            source_kind=SourceKind.ATTACHMENT,
            source_id=source_id,
            source_version=version,
            source_digest=digest,
        ),
        selection=Selection(kind="all"),
    )


def _query(**overrides: Any) -> SourceReadQuery:
    base: dict[str, Any] = {
        "source_id": "src-1",
        "version": "v1",
        "digest": _DIGEST,
        "locator": _locator(),
        "draft_revision": 3,
    }
    base.update(overrides)
    return SourceReadQuery(**base)


class TestSourceReadQueryCodec:
    def test_wire_shape_keeps_all_keys_and_nulls(self) -> None:
        wire = encode_source_read_query(_query())

        assert set(wire) == {
            "source_id",
            "version",
            "digest",
            "locator",
            "draft_revision",
            "execution_id",
            "round_id",
            "input_revision",
        }
        assert wire["draft_revision"] == 3
        assert wire["execution_id"] is None
        assert wire["round_id"] is None
        assert wire["input_revision"] is None

    def test_execution_context_with_round_and_input(self) -> None:
        wire = encode_source_read_query(
            _query(draft_revision=None, execution_id="exec-1", round_id="round-1", input_revision=2)
        )

        assert wire["execution_id"] == "exec-1"
        assert wire["round_id"] == "round-1"
        assert wire["input_revision"] == 2

    def test_locator_identity_must_match_query(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(locator=_locator(version="v2")))

        assert excinfo.value.code == "identity_mismatch"

    def test_locator_digest_must_match_query(self) -> None:
        # 同 ID/版本异 digest：不得当作同一原件（摘要必须逐值一致）
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(locator=_locator(digest="b" * 64)))

        assert excinfo.value.code == "identity_mismatch"

    def test_locator_source_id_must_match_query(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(locator=_locator(source_id="src-2")))

        assert excinfo.value.code == "identity_mismatch"

    def test_missing_context_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(draft_revision=None))

        assert excinfo.value.code == "context_missing"

    def test_two_contexts_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(execution_id="exec-1"))

        assert excinfo.value.code == "context_conflict"

    def test_draft_context_cannot_carry_round(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(round_id="round-1", input_revision=1))

        assert excinfo.value.code == "context_conflict"

    def test_round_requires_input_revision(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(draft_revision=None, execution_id="exec-1", round_id="round-1"))

        assert excinfo.value.code == "context_incomplete"

    def test_digest_must_be_lower_hex_64(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(digest="A" * 64))

        assert excinfo.value.code == "invalid_digest"

    def test_bool_revision_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_source_read_query(_query(draft_revision=True))

        assert excinfo.value.code == "invalid_range"


class _RecordingDao:
    def __init__(self) -> None:
        self.calls: list[tuple[SourceReadQuery, str]] = []

    async def read_source(self, query: SourceReadQuery, *, user_scope: str) -> Any:
        self.calls.append((query, user_scope))
        return "sentinel"


class TestAgentServiceReadSource:
    @pytest.mark.asyncio
    async def test_delegates_query_and_scope(self) -> None:
        dao = _RecordingDao()
        service = AgentService(dao)  # type: ignore[arg-type]

        assert await service.read_source(_query(), user_scope="u1") == "sentinel"
        assert dao.calls == [(_query(), "u1")]

    @pytest.mark.asyncio
    async def test_closed_service_fails_closed(self) -> None:
        dao = _RecordingDao()
        service = AgentService(dao)  # type: ignore[arg-type]
        await service.aclose()

        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_source(_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert dao.calls == []
