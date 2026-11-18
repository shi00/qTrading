"""AgentDao.read_draft 集成测试（R2-I23；§4.2 读取面）。

真实 embedded PG 上验证按会话/稿版读回完整草稿：

- 保存后可读回完整正文、资料/Skill/MCP 选择、稿版与读取时点（只读）；
- 稿版不符 → REVISION_CONFLICT（不静默返回旧稿）；
- 跨用户/会话不存在 → INPUT_INVALID（不泄露他人草稿）；
- 无稿 / 空稿 / 清理 / 移出 / 归档分列如实表示（R21，不互相伪装）；
- 存行载荷不可解码 → OPERATION_UNKNOWN（坏事实不得读成空稿）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    DraftPayload,
    DraftReadQuery,
    DraftSaveCommand,
    MCPServiceIdentity,
    Mode,
    Purpose,
    SkillVersionRef,
    SourceKind,
    SourceVersionRef,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u1"
_OTHER = "u2"
_DIGEST = "a" * 64
_BASELINE = "b" * 64


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


async def _seed_session(dao: AgentDao, user_scope: str = _USER, session_id: str = "s1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试夹具
        await conn.execute(
            insert(AgentSession).values(user_scope=user_scope, session_id=session_id, title=f"session {session_id}")
        )
        await conn.execute(
            insert(AgentAssetVersion).values(
                user_scope=user_scope,
                asset_id="src-1",
                asset_version="1",
                asset_kind="source",
                display_name="src-1@1",
                digest=_DIGEST,
                read_state="ready",
                verification_state="unverified",
            )
        )


def _payload(**overrides: Any) -> DraftPayload:
    values: dict[str, Any] = {
        "schema_version": 2,
        "text": "继续编辑：三家公司对比",
        "mode": Mode.DISCUSS,
        "model_selection": json_doc({"scenario_id": "agent_research"}),
        "source_refs": (
            SourceVersionRef(
                source_kind=SourceKind.USER_MESSAGE,
                source_id="src-1",
                source_version="1",
                source_digest=_DIGEST,
            ),
        ),
        "skill_versions": (SkillVersionRef(skill_id="skill-1", version=2),),
        "mcp_selection": (MCPServiceIdentity(service_id="mcp-1", config_version=1),),
        "purpose": Purpose.CONCEPT,
        "research_date": "2026-11-13",
        "cutoff": None,
        "send_target": json_doc({"kind": "session", "session_id": "s1"}),
        "object_context": json_doc({"source_module": "ui", "object_ref": None}),
        "role_prompt_selection": None,
        "customized_fields": ("mode",),
        "defaults_baseline_digest": _BASELINE,
    }
    values.update(overrides)
    return DraftPayload(**values)


def _empty_payload() -> DraftPayload:
    return DraftPayload(
        schema_version=2,
        text="",
        mode=Mode.EXECUTE,
        model_selection=None,
        source_refs=(),
        skill_versions=(),
        mcp_selection=(),
        purpose=None,
        research_date=None,
        cutoff=None,
        send_target=None,
        object_context=None,
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def _save_command(
    *,
    session_id: str = "s1",
    command_id: str = "cmd-1",
    expected: int = 1,
    payload: DraftPayload | None = None,
    user_scope: str = _USER,
) -> DraftSaveCommand:
    return DraftSaveCommand(
        command=CommandRef(
            user_scope=user_scope,
            source_module="ui",
            business_request_id=f"req-{command_id}",
            command_id=command_id,
            expected_revision=expected,
        ),
        session_id=session_id,
        expected_draft_revision=expected,
        payload=_payload() if payload is None else payload,
    )


def _expect_error(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    err = excinfo.value
    assert isinstance(err, AgentServiceError)
    assert err.code == code, f"expect {code.value}, got {err.code.value}: {err.sanitized_message}"


async def _mutate_session(url: str, **values: Any) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                AgentSession.__table__.update()
                .where(AgentSession.user_scope == _USER, AgentSession.session_id == "s1")
                .values(**values)
            )
    finally:
        await engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


class TestReadDraft:
    @pytest.mark.asyncio
    async def test_round_trips_full_payload_and_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        payload = _payload()
        await dao.save_draft(_save_command(payload=payload))

        read = await dao.read_draft(DraftReadQuery(session_id="s1"), user_scope=_USER)

        assert read.session_id == "s1"
        assert read.draft_revision == 2
        assert read.payload == payload
        assert read.is_empty is False
        assert read.cleaned is False
        assert read.archived is False
        assert read.transferred is False
        assert read.read_at.endswith("Z") and "T" in read.read_at

    @pytest.mark.asyncio
    async def test_matching_revision_succeeds_and_mismatch_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_save_command())

        assert (await dao.read_draft(DraftReadQuery("s1", draft_revision=2), user_scope=_USER)).draft_revision == 2
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_draft(DraftReadQuery("s1", draft_revision=1), user_scope=_USER)
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)

    @pytest.mark.asyncio
    async def test_cross_user_and_missing_session_are_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_save_command())

        with pytest.raises(AgentServiceError) as cross:
            await dao.read_draft(DraftReadQuery("s1"), user_scope=_OTHER)
        _expect_error(cross, AgentErrorCode.INPUT_INVALID)
        with pytest.raises(AgentServiceError) as missing:
            await dao.read_draft(DraftReadQuery("missing"), user_scope=_USER)
        _expect_error(missing, AgentErrorCode.INPUT_INVALID)

    @pytest.mark.asyncio
    async def test_never_saved_session_is_not_empty_draft(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)

        read = await dao.read_draft(DraftReadQuery("s1"), user_scope=_USER)

        assert read.payload is None
        assert read.is_empty is False
        assert read.cleaned is False
        assert read.transferred is False

    @pytest.mark.asyncio
    async def test_saved_empty_draft_is_explicitly_empty(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_save_command(payload=_empty_payload()))

        read = await dao.read_draft(DraftReadQuery("s1"), user_scope=_USER)

        assert read.payload is not None
        assert read.is_empty is True
        assert read.draft_revision == 2

    @pytest.mark.asyncio
    async def test_cleaned_session_reports_cleaned_not_empty(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_save_command())
        await _mutate_session(
            real_embedded_pg.url,
            draft_payload=None,
            draft_revision=3,
            cleaned_at=_now(),
        )

        read = await dao.read_draft(DraftReadQuery("s1"), user_scope=_USER)

        assert read.payload is None
        assert read.cleaned is True
        assert read.is_empty is False

    @pytest.mark.asyncio
    async def test_transferred_draft_is_not_reported_as_no_draft(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_save_command())
        await _mutate_session(
            real_embedded_pg.url,
            draft_payload=None,
            draft_revision=3,
            draft_transfer_ref="cmd-transfer",
        )

        read = await dao.read_draft(DraftReadQuery("s1"), user_scope=_USER)

        assert read.payload is None
        assert read.transferred is True
        assert read.cleaned is False

    @pytest.mark.asyncio
    async def test_archived_session_stays_readable(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_save_command())
        await _mutate_session(real_embedded_pg.url, archived=True)

        read = await dao.read_draft(DraftReadQuery("s1"), user_scope=_USER)

        assert read.archived is True
        assert read.payload is not None
        assert read.payload.text == "继续编辑：三家公司对比"

    @pytest.mark.asyncio
    async def test_corrupt_payload_fails_closed(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _mutate_session(real_embedded_pg.url, draft_payload={"schema_version": 2}, draft_revision=2)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_draft(DraftReadQuery("s1"), user_scope=_USER)
        _expect_error(excinfo, AgentErrorCode.OPERATION_UNKNOWN)
