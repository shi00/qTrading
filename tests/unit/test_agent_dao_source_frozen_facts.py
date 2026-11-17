"""来源冻结事实与当前可用性投影分离契约测试（检视 D10/D11）。

- D10：``user_message_origin`` 来源沿消息 owner（``agent_message`` / ``body_ref``
  载荷）读回正文并核版本/摘要，不复制第二份私人正文；``source_ref`` 类来源维持
  既有登记口径。
- D11：``source_ref`` 内冻结 ``read_state`` 与行内当前投影列不一致**不是**坏元数据
  （``stop_source_use`` 只改投影）；已停止来源历史可读、研究不可用由投影标注。

真实 PG 端到端链路（受理 → 读取 → 禁用 → 读取 / 研究拒绝）由
``tests/integration/test_agent_dao_read_source.py`` 与
``tests/integration/test_agent_dao_source_stop.py`` 覆盖；本文件用 capture conn
断言类别分支、冻结事实校验与投影标注。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ReadState,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceVersionRef,
    VerificationState,
    encode_source_ref,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_TEXT = "研究三家公司：A、B、C"
_DIGEST = sha256_hex(_TEXT)
_MESSAGE_ID = "msg-1"
_SESSION_ID = "s1"
_SOURCE_ID = "src-msg"
_AVAILABLE_AT = datetime(2026, 11, 1, tzinfo=UTC)


def _locator() -> SourceLocator:
    return SourceLocator(
        locator_kind="source_range",
        origin_ref=SourceVersionRef(
            source_kind=SourceKind.ATTACHMENT,
            source_id="src-1",
            source_version="v1",
            source_digest=_DIGEST,
        ),
        selection=Selection(kind="all"),
    )


def _source_ref(*, read_state: ReadState = ReadState.READY) -> SourceRef:
    return SourceRef(
        user_scope="u1",
        source_id="src-1",
        version="v1",
        digest=_DIGEST,
        source_kind=SourceKind.ATTACHMENT,
        display_name="附件.pdf",
        object_ref=None,
        locator=_locator(),
        available_at="2026-11-01T00:00:00.000000Z",
        period=None,
        unit_note=None,
        read_state=read_state,
        missing_reason=None,
        provenance_refs=(),
    )


def _asset_row(
    *,
    metadata: Any,
    read_state: str = "ready",
    verification_state: str = "program_verified",
    digest: str = _DIGEST,
    body_text: str | None = None,
    body_ref: str | None = None,
    disabled_at: datetime | None = None,
    cleaned_at: datetime | None = None,
    tombstoned: bool = False,
    asset_id: str = _SOURCE_ID,
    asset_version: str = "1",
    display_name: str = "用户消息",
) -> Any:
    return SimpleNamespace(
        asset_id=asset_id,
        asset_version=asset_version,
        digest=digest,
        display_name=display_name,
        metadata_payload=metadata,
        read_state=read_state,
        verification_state=verification_state,
        body_text=body_text,
        body_ref=body_ref,
        available_at=_AVAILABLE_AT,
        disabled_at=disabled_at,
        cleaned_at=cleaned_at,
        tombstoned=tombstoned,
    )


def _origin_metadata(*, message_version: str = "1") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "user_message_origin": {
            "session_id": _SESSION_ID,
            "message_id": _MESSAGE_ID,
            "message_version": message_version,
        },
    }


def _message_row(
    *,
    body_text: str | None = _TEXT,
    body_ref: str | None = None,
    session_id: str = _SESSION_ID,
    role: str = "user",
    payload: Any = None,
) -> Any:
    if payload is None:
        payload = {
            "schema_version": 1,
            "message_id": _MESSAGE_ID,
            "role": "user",
            "source_id": _SOURCE_ID,
            "source_version": "1",
        }
    return SimpleNamespace(
        session_id=session_id,
        role=role,
        body_text=body_text,
        body_ref=body_ref,
        payload=payload,
    )


def _payload_row(data: str) -> Any:
    raw = data.encode("utf-8")
    return SimpleNamespace(
        owner_kind="message",
        owner_id=_MESSAGE_ID,
        owner_version="1",
        message_id=_MESSAGE_ID,
        artifact_id=None,
        artifact_version=None,
        asset_id=None,
        asset_version=None,
        encoding="utf-8",
        payload_bytes=raw,
        byte_length=len(raw),
        payload_digest=sha256_hex(raw),
    )


class _FrozenFactsConn:
    """按 SQL 目标表返回消息/载荷行事实（capture conn）。"""

    def __init__(self, *, message: Any = None, payload: Any = None) -> None:
        self.message = message
        self.payload = payload
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        result = MagicMock()
        result.first.return_value = None
        if "FROM agent_message" in sql:
            result.first.return_value = self.message
        elif "FROM agent_body_payload" in sql:
            result.first.return_value = self.payload
        return result


async def _resolve(conn: Any, row: Any) -> Any:
    return await AgentDao(None)._resolve_frozen_source_facts(conn, row=row, user_scope="u1", identity="u1/src-msg@1")


def _expect(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    assert excinfo.value.code is code, excinfo.value.sanitized_message


class TestUserMessageOriginFrozenFacts:
    """D10：个人消息来源沿 owner 读正文，身份/版本/摘要不符显式失败。"""

    async def test_reads_owner_inline_body_and_frozen_version(self) -> None:
        row = _asset_row(metadata=_origin_metadata())
        facts = await _resolve(_FrozenFactsConn(message=_message_row()), row)

        assert facts.body_text == _TEXT
        assert facts.source.source_kind is SourceKind.USER_MESSAGE
        assert facts.source.read_state is ReadState.READY
        assert facts.source.digest == _DIGEST
        assert facts.source.locator.selection == Selection(kind="all")
        assert facts.verification is VerificationState.PROGRAM_VERIFIED
        # 资产表不复制第二份私人正文：行内 body_text 保持为空
        assert row.body_text is None

    async def test_reads_owner_body_ref_payload(self) -> None:
        row = _asset_row(metadata=_origin_metadata())
        message = _message_row(body_text=None, body_ref="body-msg")
        facts = await _resolve(_FrozenFactsConn(message=message, payload=_payload_row(_TEXT)), row)

        assert facts.body_text == _TEXT

    async def test_body_digest_mismatch_rejected(self) -> None:
        row = _asset_row(metadata=_origin_metadata())
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=_message_row(body_text="被改写的正文")), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    async def test_origin_version_mismatch_rejected(self) -> None:
        row = _asset_row(metadata=_origin_metadata(message_version="2"))
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=_message_row()), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    async def test_unreadable_origin_descriptor_rejected(self) -> None:
        metadata = {"schema_version": 1, "user_message_origin": {"session_id": _SESSION_ID}}
        row = _asset_row(metadata=metadata)
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=_message_row()), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    async def test_missing_owner_message_rejected(self) -> None:
        row = _asset_row(metadata=_origin_metadata())
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=None), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    @pytest.mark.parametrize(
        ("session_id", "role"),
        [("s-other", "user"), (_SESSION_ID, "assistant")],
    )
    async def test_session_or_role_mismatch_rejected(self, session_id: str, role: str) -> None:
        row = _asset_row(metadata=_origin_metadata())
        message = _message_row(session_id=session_id, role=role)
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=message), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    async def test_payload_cross_reference_mismatch_rejected(self) -> None:
        row = _asset_row(metadata=_origin_metadata())
        message = _message_row(payload={"schema_version": 1, "message_id": "other", "role": "user"})
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=message), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    async def test_missing_body_is_explicit_unavailable(self) -> None:
        row = _asset_row(metadata=_origin_metadata())
        message = _message_row(body_text=None, body_ref=None)
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=message), row)
        _expect(excinfo, AgentErrorCode.PERSISTENCE_UNAVAILABLE)

    async def test_projection_state_outside_ready_disabled_rejected(self) -> None:
        row = _asset_row(metadata=_origin_metadata(), read_state="unread")
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=_message_row()), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    async def test_metadata_without_category_fact_rejected(self) -> None:
        row = _asset_row(metadata={"schema_version": 1})
        with pytest.raises(AgentServiceError) as excinfo:
            await _resolve(_FrozenFactsConn(message=_message_row()), row)
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)


class TestFrozenFactsVersusProjection:
    """D11：冻结事实与当前投影分开，禁用不是坏元数据。"""

    def test_source_ref_frozen_read_state_survives_disable_projection(self) -> None:
        metadata = {
            "schema_version": 1,
            "source_ref": encode_source_ref(_source_ref(read_state=ReadState.READY)),
            "verification": "program_verified",
        }
        row = _asset_row(
            metadata=metadata,
            read_state="disabled",
            disabled_at=datetime.now(UTC),
            asset_id="src-1",
            asset_version="v1",
        )

        source_ref, verification, _metadata = AgentDao(None)._decode_source_asset_row(
            row, user_scope="u1", identity="u1/src-1@v1"
        )

        assert source_ref.read_state is ReadState.READY
        assert verification is VerificationState.PROGRAM_VERIFIED
        assert source_ref.source_id == "src-1"

    async def test_source_ref_business_path_keeps_frozen_ref(self) -> None:
        metadata = {
            "schema_version": 1,
            "source_ref": encode_source_ref(_source_ref()),
            "verification": "unverified",
        }
        row = _asset_row(
            metadata=metadata,
            read_state="disabled",
            disabled_at=datetime.now(UTC),
            asset_id="src-1",
            asset_version="v1",
            digest=_DIGEST,
            body_text="已存正文",
            verification_state="unverified",
        )

        facts = await _resolve(_FrozenFactsConn(), row)

        assert facts.source.source_kind is SourceKind.ATTACHMENT
        assert facts.source.read_state is ReadState.READY
        assert facts.body_text == "已存正文"

    def test_metadata_verification_mismatch_still_rejected(self) -> None:
        metadata = {
            "schema_version": 1,
            "source_ref": encode_source_ref(_source_ref()),
            "verification": "human_verified",
        }
        row = _asset_row(metadata=metadata, asset_id="src-1", asset_version="v1")

        with pytest.raises(AgentServiceError) as excinfo:
            AgentDao(None)._decode_source_asset_row(row, user_scope="u1", identity="u1/src-1@v1")
        _expect(excinfo, AgentErrorCode.OPERATION_UNKNOWN)

    def test_disabled_projection_is_explicit_and_history_kept(self) -> None:
        row = _asset_row(metadata={}, read_state="disabled", disabled_at=datetime.now(UTC))

        available, reason = AgentDao._source_projection(row, ReadState.DISABLED, body_text=_TEXT, frozen_reason=None)

        assert available is False
        assert reason == "source_disabled"

    def test_cleaned_projection_reports_unavailable(self) -> None:
        row = _asset_row(metadata={}, read_state="ready", cleaned_at=datetime.now(UTC), tombstoned=True)

        available, reason = AgentDao._source_projection(row, ReadState.READY, body_text=None, frozen_reason=None)

        assert available is False
        assert reason == "source_unavailable"

    def test_frozen_reason_has_precedence(self) -> None:
        row = _asset_row(metadata={}, read_state="disabled", disabled_at=datetime.now(UTC))

        available, reason = AgentDao._source_projection(
            row, ReadState.DISABLED, body_text=_TEXT, frozen_reason="用户禁用"
        )

        assert available is False
        assert reason == "用户禁用"

    def test_unprepared_representation_reports_reason_without_faking_success(self) -> None:
        row = _asset_row(metadata={}, read_state="unread")

        available, reason = AgentDao._source_projection(row, ReadState.UNREAD, body_text=None, frozen_reason=None)

        assert available is True
        assert reason == "representation_not_available"
