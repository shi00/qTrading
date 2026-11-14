"""``AgentDao.read_source`` 集成测试（DD §4.2/§9.10/§9.10.1，F-02-1）。

真 embedded PG 上验证来源读取：

- 只读**已登记到请求上下文**（草稿 revision / 执行输入版）的来源版本；同用户域
  但未进入该上下文的版本 → ``INPUT_INVALID``（不因同域可读就放行）；
- 仅读取**已经准备好的选定表示**：``body_text`` 或 ``body_ref`` 载荷（核 owner/
  编码/长度/SHA-256 后重建），不现场解析、不扩大范围（§4.2 line 421）；
- 未准备返回 ``unread`` 与明确原因，不伪完整；禁用/清理后 ``available=False``
  且不返回正文、原因不得为空；
- 查询摘要/定位与已存版本不一致 → ``REVISION_CONFLICT``（不静默换版）；
- 其他用户域/不存在 → ``INPUT_INVALID``；行内自相矛盾 → ``OPERATION_UNKNOWN``；
- 载荷缺失/损坏 → ``PERSISTENCE_UNAVAILABLE``（不回退最新或摘要）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    FactValue,
    ReadState,
    Selection,
    SourceKind,
    SourceLocator,
    SourceReadQuery,
    SourceRef,
    SourceVersionRef,
    VerificationState,
    encode_fact_value,
    encode_source_ref,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import (
    AgentAssetVersion,
    AgentBodyPayload,
    AgentExecution,
    AgentRound,
    AgentSession,
    AgentSourceLink,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_DIGEST = "b" * 64


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> AgentDao:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


def _locator(source_id: str = "src-1", version: str = "v1", *, limited: bool = False) -> SourceLocator:
    ranges = ()
    if limited:
        from core.agent_types import PagesRange

        ranges = (PagesRange(start=1, end=2),)
    return SourceLocator(
        locator_kind="source_range",
        origin_ref=SourceVersionRef(
            source_kind=SourceKind.ATTACHMENT,
            source_id=source_id,
            source_version=version,
            source_digest=_DIGEST,
        ),
        selection=Selection(kind="limited" if limited else "all", ranges=ranges),
    )


def _source_ref(
    *,
    user_scope: str = "u1",
    source_id: str = "src-1",
    version: str = "v1",
    read_state: ReadState = ReadState.READY,
    missing_reason: str | None = None,
) -> SourceRef:
    return SourceRef(
        user_scope=user_scope,
        source_id=source_id,
        version=version,
        digest=_DIGEST,
        source_kind=SourceKind.ATTACHMENT,
        display_name="附件.pdf",
        object_ref=None,
        locator=_locator(source_id, version),
        available_at="2026-11-01T00:00:00.000000Z",
        period=None,
        unit_note=None,
        read_state=read_state,
        missing_reason=missing_reason,
        provenance_refs=(),
    )


def _query(
    *,
    digest: str = _DIGEST,
    locator: SourceLocator | None = None,
    draft_revision: int | None = 3,
    execution_id: str | None = None,
    round_id: str | None = None,
    input_revision: int | None = None,
) -> SourceReadQuery:
    return SourceReadQuery(
        source_id="src-1",
        version="v1",
        digest=digest,
        locator=locator or _locator(),
        draft_revision=draft_revision,
        execution_id=execution_id,
        round_id=round_id,
        input_revision=input_revision,
    )


async def _seed_session(dao: AgentDao, *, draft_revision: int = 3) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.insert(AgentSession).values(
                user_scope="u1", session_id="s1", title="来源会话", draft_revision=draft_revision
            )
        )


async def _seed_execution(dao: AgentDao, *, execution_id: str = "exec-1", round_id: str = "round-1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.insert(AgentExecution).values(
                user_scope="u1",
                execution_id=execution_id,
                session_id="s1",
                execution_kind="root",
                node_id="root",
                display_state="queued",
                goal_coverage="unknown",
                source_module="ui",
                business_request_id=f"req-{execution_id}",
                intent_digest="a" * 64,
            )
        )
        await conn.execute(
            sa.insert(AgentRound).values(
                user_scope="u1",
                round_id=round_id,
                execution_id=execution_id,
                round_number=1,
                workflow_id=f"wf-{round_id}",
                workflow_code_version="wcv-1",
                run_epoch=1,
                input_revision=1,
                plan_revision=1,
                goal_coverage="unknown",
                final_saved=False,
                convergence_confirmed=False,
                active_round=True,
            )
        )


async def _seed_asset(
    dao: AgentDao,
    *,
    ref: SourceRef | None = None,
    read_state: str = "ready",
    verification_state: str = "program_verified",
    body_text: str | None = None,
    body_ref: str | None = None,
    payload_text: str | None = None,
    payload_tamper: str | None = None,
    metadata_extra: dict[str, object] | None = None,
    verification_in_metadata: str = "program_verified",
    tombstoned: bool = False,
    cleaned: bool = False,
    link_kind: str | None = "draft",
) -> None:
    ref = ref or _source_ref()
    payload: dict[str, object] = {
        "schema_version": 1,
        "source_ref": encode_source_ref(ref),
        "verification": verification_in_metadata,
    }
    if metadata_extra:
        payload.update(metadata_extra)
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        if body_ref is not None and payload_text is not None:
            data = payload_text.encode("utf-8")
            digest = sha256_hex(data)
            owner_id = ref.source_id
            if payload_tamper == "digest":
                digest = "c" * 64
            elif payload_tamper == "owner":
                owner_id = "src-other"
            await conn.execute(
                sa.insert(AgentBodyPayload).values(
                    user_scope=ref.user_scope,
                    body_ref=body_ref,
                    owner_kind="asset",
                    owner_id=owner_id,
                    owner_version=ref.version,
                    asset_id=ref.source_id,
                    asset_version=ref.version,
                    encoding="utf-8",
                    payload_bytes=data,
                    byte_length=len(data),
                    payload_digest=digest,
                )
            )
        await conn.execute(
            sa.insert(AgentAssetVersion).values(
                user_scope=ref.user_scope,
                asset_id=ref.source_id,
                asset_version=ref.version,
                asset_kind="source",
                display_name=ref.display_name,
                digest=ref.digest,
                metadata_payload=payload,
                read_state=read_state,
                verification_state=verification_state,
                available_at=datetime(2026, 11, 1, tzinfo=UTC),
                body_text=body_text,
                body_ref=body_ref,
                tombstoned=tombstoned,
                cleaned_at=datetime(2026, 11, 2, tzinfo=UTC) if cleaned else None,
            )
        )
        if link_kind is not None:
            # 同事务插入谱系边（source FK 即刻校验）：草稿上下文默认 consumer d1@3。
            consumer_id = "draft-1" if link_kind == "draft" else "exec-1"
            consumer_version = "3" if link_kind == "draft" else "1"
            await conn.execute(
                sa.insert(AgentSourceLink).values(
                    user_scope=ref.user_scope,
                    link_id=f"link-{link_kind}-{consumer_id}",
                    source_id=ref.source_id,
                    source_version=ref.version,
                    consumer_kind=link_kind,
                    consumer_id=consumer_id,
                    consumer_version=consumer_version,
                    session_id="s1" if link_kind == "draft" else None,
                    execution_id="exec-1" if link_kind == "execution" else None,
                    relation_kind="origin",
                )
            )


class TestReadSourceRepresentation:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_reads_inline_body_text_exactly(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="第一行\r\n第二行")

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.body_text == "第一行\r\n第二行"
        assert read.read_state is ReadState.READY
        assert read.source.source_id == "src-1"
        assert read.source.version == "v1"
        assert read.source.digest == _DIGEST
        assert read.verification is VerificationState.PROGRAM_VERIFIED
        assert read.available is True
        assert read.missing_reason is None
        assert read.coverage == "unknown"
        assert read.facts == ()
        assert read.structured_blocks == ()
        assert read.selected_range.selection == _locator().selection
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reads_body_ref_payload_with_verified_identity(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_ref="body-1", payload_text="载荷正文")

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.body_text == "载荷正文"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reads_prepared_range_facts_and_coverage(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        fact = FactValue(
            field_key="revenue",
            object_ref="src-1:v1",
            raw_type="json_decimal",
            raw_value="123.45",
            numeric_lexeme="123.45",
            numeric_encoding="plain",
            source_unit="元",
            currency="CNY",
            display_unit=None,
            conversion_ref=None,
            period="2025Q3",
            basis=None,
            source_locator=_locator(),
            verification=VerificationState.PROGRAM_VERIFIED,
            missing_reason=None,
            xlsx_cell_meta=None,
        )
        await _seed_asset(
            dao,
            body_text="正文",
            metadata_extra={
                "selected_range": {
                    "selection": {"kind": "all"},
                    "read_ranges": [{"kind": "all"}],
                    "unread_ranges": [],
                    "stats": {"source_bytes": 12, "pages": None, "sheets": None, "rows": None, "chars": 2},
                    "limitations": [{"code": "truncated_columns", "message": "仅前两列"}],
                    "accepted_limited_version": None,
                },
                "facts": [encode_fact_value(fact)],
                "structured_blocks": ['{"kind":"paragraph","text":"正文"}'],
                "coverage": "limited",
                "limitations": [{"code": "ocr_skipped", "message": "未 OCR"}],
            },
        )

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.coverage == "limited"
        assert read.facts[0].field_key == "revenue"
        assert read.facts[0].raw_value == "123.45"
        assert read.structured_blocks == ('{"kind":"paragraph","text":"正文"}',)
        assert [item.code for item in read.selected_range.limitations] == ["truncated_columns"]
        assert [item.code for item in read.limitations] == ["ocr_skipped"]
        assert read.selected_range.stats.chars == 2
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unread_source_reports_reason_not_empty_success(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, ref=_source_ref(read_state=ReadState.UNREAD), read_state="unread")

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.read_state is ReadState.UNREAD
        assert read.body_text is None
        assert read.available is True
        assert read.missing_reason == "representation_not_available"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_body_is_gated_by_read_state(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, ref=_source_ref(read_state=ReadState.UNREAD), read_state="unread", body_text="未读正文")

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.read_state is ReadState.UNREAD
        assert read.body_text is None
        assert read.missing_reason == "representation_not_available"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("state", "row_state", "body_visible"),
        [
            (ReadState.UNKNOWN, "unknown", False),
            (ReadState.UNAVAILABLE, "unavailable", True),
        ],
    )
    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_and_unavailable_states_keep_body_semantics(
        self,
        real_embedded_pg: ConnectionInfo,
        state: ReadState,
        row_state: str,
        body_visible: bool,
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, ref=_source_ref(read_state=state), read_state=row_state, body_text="已存正文")

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.read_state is state
        assert read.body_text == ("已存正文" if body_visible else None)
        if body_visible:
            assert read.available is False
            assert read.missing_reason == "source_unavailable"
        else:
            assert read.available is True
            assert read.missing_reason == "representation_not_available"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_disabled_and_cleaned_source_not_available(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(
            dao,
            ref=_source_ref(read_state=ReadState.DISABLED, missing_reason="用户禁用"),
            read_state="disabled",
            body_text="已存正文",
            tombstoned=True,
            cleaned=True,
        )

        read = await dao.read_source(_query(), user_scope="u1")

        assert read.available is False
        assert read.missing_reason == "用户禁用"
        assert read.body_text == "已存正文"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_draft_revision_must_match_session(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, draft_revision=3)
        await _seed_asset(dao, body_text="正文")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(draft_revision=2), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not registered" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execution_context_requires_registered_link(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_execution(dao)
        await _seed_asset(dao, body_text="正文", link_kind="execution")

        read = await dao.read_source(
            _query(draft_revision=None, execution_id="exec-1", round_id="round-1", input_revision=1), user_scope="u1"
        )

        assert read.body_text == "正文"
        await dao.engine.dispose()  # type: ignore[union-attr]


class TestReadSourceIdentityAndFailures:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_source_not_registered_to_context_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文", link_kind=None)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not registered" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_execution_without_link_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(draft_revision=None, execution_id="exec-1"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_round_of_other_execution_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_execution(dao)
        await _seed_execution(dao, execution_id="exec-2", round_id="round-x")
        await _seed_asset(dao, body_text="正文", link_kind="execution")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(
                _query(draft_revision=None, execution_id="exec-1", round_id="round-x", input_revision=1),
                user_scope="u1",
            )
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_input_revision_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_execution(dao)
        await _seed_asset(dao, body_text="正文", link_kind="execution")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(
                _query(draft_revision=None, execution_id="exec-1", round_id="round-1", input_revision=2),
                user_scope="u1",
            )
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_digest_mismatch_is_revision_conflict(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(digest="d" * 64), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_locator_mismatch_is_revision_conflict(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(locator=_locator(limited=True)), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_other_user_scope_not_found(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(), user_scope="u2")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_invalid_query_rejected_before_lookup(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(draft_revision=None), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "context_missing" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_inconsistent_metadata_is_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文", verification_in_metadata="human_verified")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_broken_source_ref_is_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, body_text="正文", metadata_extra={"source_ref": {"broken": True}})

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_source(_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_corrupt_body_payload_digest_or_owner_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        for tamper in ("digest", "owner"):
            dao = await _setup(real_embedded_pg.url)
            await _seed_session(dao)
            await _seed_asset(dao, body_ref="body-1", payload_text="正文", payload_tamper=tamper)

            with pytest.raises(AgentServiceError) as excinfo:
                await dao.read_source(_query(), user_scope="u1")
            assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE, tamper
            await dao.engine.dispose()  # type: ignore[union-attr]
