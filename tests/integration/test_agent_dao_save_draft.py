"""AgentDao.save_draft 集成测试（F-03-1；DD §4.2 save_draft、§9.3、§9.13）。

真实 embedded PG 上验证单个显式事务语义：

- 首次保存（会话默认 draft_revision=1）→ 回执 2，草稿载荷/命令回执落库；
- 预期版不符 → REVISION_CONFLICT 且零新事实（草稿与命令均不变）；
- 同命令键同输入 → 查回原回执（不重复推进版本、不造新命令）；
- 同命令键异输入 → INPUT_CONFLICT；
- 会话不存在/跨用户域 → INPUT_INVALID（不静默当新会话）；
- 并发同预期版 → 行锁下仅一个胜者；
- 正文清空保存仍为可靠事实（空稿不是「无稿」）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from alembic import command
from sqlalchemy import insert, select, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryState,
    DraftPayload,
    DraftSaveCommand,
    Mode,
    Purpose,
    SourceKind,
    SourceVersionRef,
    draft_save_input_digest,
    encode_draft_payload,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentCommand, AgentSession, AgentSourceLink
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u1"
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
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置会话
        await conn.execute(
            insert(AgentSession).values(user_scope=user_scope, session_id=session_id, title=f"session {session_id}")
        )


async def _seed_asset(dao: AgentDao, asset_id: str = "src-1", version: str = "1", user_scope: str = _USER) -> None:
    """受管源版本（草稿只可引用已登记版本，不自动建资产）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试夹具
        await conn.execute(
            insert(AgentAssetVersion).values(
                user_scope=user_scope,
                asset_id=asset_id,
                asset_version=version,
                asset_kind="source",
                display_name=f"{asset_id}@{version}",
                digest=_DIGEST,
                read_state="ready",
                verification_state="unverified",
            )
        )


def _payload(**overrides: Any) -> DraftPayload:
    values: dict[str, Any] = {
        "schema_version": 2,
        "text": "研究三家公司",
        "mode": Mode.DISCUSS,
        "model_selection": json_doc({"scenario_id": "agent_research"}),
        "source_refs": (),
        "skill_versions": (),
        "mcp_selection": (),
        "purpose": Purpose.CONCEPT,
        "research_date": "2026-11-13",
        "cutoff": None,
        "send_target": None,
        "object_context": json_doc({"source_module": "ui", "object_ref": None}),
        "role_prompt_selection": None,
        "customized_fields": ("mode",),
        "defaults_baseline_digest": _BASELINE,
    }
    values.update(overrides)
    return DraftPayload(**values)


def _command(
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


async def _session_row(url: str, user_scope: str = _USER, session_id: str = "s1") -> Any:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    select(
                        AgentSession.draft_revision,
                        AgentSession.draft_payload,
                        AgentSession.revision,
                        AgentSession.updated_at,
                    ).where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id == session_id,
                    )
                )
            ).first()
    finally:
        await engine.dispose()


async def _command_rows(url: str) -> list[Any]:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                (
                    await conn.execute(
                        select(
                            AgentCommand.command_kind,
                            AgentCommand.target_id,
                            AgentCommand.expected_revision,
                            AgentCommand.input_digest,
                            AgentCommand.commit_state,
                            AgentCommand.delivery_state,
                            AgentCommand.consumption_state,
                            AgentCommand.receipt_payload,
                            AgentCommand.request_payload,
                        ).where(AgentCommand.user_scope == _USER)
                    )
                )
                .all()
                .copy()
            )
    finally:
        await engine.dispose()


async def _draft_links(url: str) -> list[Any]:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                (
                    await conn.execute(
                        select(
                            AgentSourceLink.source_id,
                            AgentSourceLink.source_version,
                            AgentSourceLink.consumer_kind,
                            AgentSourceLink.consumer_id,
                            AgentSourceLink.consumer_version,
                            AgentSourceLink.session_id,
                            AgentSourceLink.relation_kind,
                        )
                        .where(AgentSourceLink.user_scope == _USER)
                        .order_by(AgentSourceLink.source_id)
                    )
                )
                .all()
                .copy()
            )
    finally:
        await engine.dispose()


class TestSaveDraft:
    async def test_first_save_lands_draft_and_command_fact(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao)
        before = await _session_row(real_embedded_pg.url)
        assert before.draft_revision == 1 and before.draft_payload is None

        command = _command()
        receipt = await dao.save_draft(command)

        assert receipt.session_id == "s1"
        assert receipt.commit_state is CommitState.COMMITTED
        assert receipt.draft_revision == 2
        assert receipt.error is None

        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 2
        # 会话行修订随可变元数据推进（目录/历史游标指纹不静默漏页）。
        assert row.revision == before.revision + 1
        assert row.updated_at >= before.updated_at
        assert row.draft_payload == encode_draft_payload(command.payload)

        rows = await _command_rows(real_embedded_pg.url)
        assert len(rows) == 1
        stored = rows[0]
        assert stored.command_kind == "save_draft"
        assert stored.target_id == "s1"
        assert stored.expected_revision == 1
        assert stored.input_digest == draft_save_input_digest(command)
        assert stored.commit_state == CommitState.COMMITTED.value
        assert stored.delivery_state == DeliveryState.NOT_DELIVERED.value
        assert stored.consumption_state == ConsumptionState.NOT_CONSUMED.value
        assert stored.receipt_payload["draft_revision"] == 2
        assert stored.request_payload["text"] == "研究三家公司"

    async def test_stale_expected_revision_conflicts_with_zero_new_facts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_command(expected=1, command_id="cmd-1"))
        snapshot_before = await _session_row(real_embedded_pg.url)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(expected=1, command_id="cmd-2"))
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)

        snapshot_after = await _session_row(real_embedded_pg.url)
        assert snapshot_after.draft_revision == 2
        assert snapshot_after.draft_payload == snapshot_before.draft_payload
        assert snapshot_after.revision == snapshot_before.revision
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_same_command_same_input_replays_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        command = _command()
        first = await dao.save_draft(command)
        second = await dao.save_draft(command)

        assert second == first
        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 2
        rows = await _command_rows(real_embedded_pg.url)
        assert len(rows) == 1

    async def test_same_command_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_command(payload=_payload(text="研究三家公司")))
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(payload=_payload(text="研究两家公司")))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 2
        assert row.draft_payload["text"] == "研究三家公司"

    async def test_unknown_session_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(session_id="missing"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_other_user_scope_session_not_visible(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, user_scope="u2", session_id="s1")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(user_scope=_USER))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_concurrent_same_expected_revision_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)

        results = await asyncio.gather(
            dao.save_draft(_command(command_id="cmd-a", expected=1)),
            dao.save_draft(_command(command_id="cmd-b", expected=1)),
            return_exceptions=True,
        )
        errors = [item for item in results if isinstance(item, AgentServiceError)]
        receipts = [item for item in results if not isinstance(item, BaseException)]
        assert len(receipts) == 1, results
        assert len(errors) == 1
        assert errors[0].code is AgentErrorCode.REVISION_CONFLICT

        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 2
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_cleared_draft_is_reliable_empty_draft(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.save_draft(_command(expected=1, command_id="cmd-1"))
        empty = _payload(
            text="",
            model_selection=None,
            source_refs=(),
            skill_versions=(),
            mcp_selection=(),
            purpose=None,
            cutoff=None,
            send_target=None,
            object_context=None,
            role_prompt_selection=None,
            customized_fields=(),
            defaults_baseline_digest=None,
        )
        receipt = await dao.save_draft(_command(expected=2, command_id="cmd-2", payload=empty))
        assert receipt.draft_revision == 3
        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 3
        assert row.draft_payload == encode_draft_payload(empty)

    async def test_invalid_command_rejected_before_touching_rows(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(expected=0))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 1 and row.draft_payload is None
        assert await _command_rows(real_embedded_pg.url) == []


class TestDraftSourceLinks:
    """草稿来源谱系边（F-03-4）：只引用已受管版本、当前集合替换、未登记整体回滚。"""

    def _payload_with(self, *asset_ids: str) -> DraftPayload:
        return _payload(
            source_refs=tuple(
                SourceVersionRef(
                    source_kind=SourceKind.USER_MESSAGE,
                    source_id=asset_id,
                    source_version="1",
                    source_digest=_DIGEST,
                )
                for asset_id in asset_ids
            )
        )

    async def test_links_written_for_current_draft_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, "src-1")
        await _seed_asset(dao, "src-2")
        await dao.save_draft(_command(expected=1, command_id="cmd-1", payload=self._payload_with("src-1", "src-2")))

        links = await _draft_links(real_embedded_pg.url)
        assert [(row.source_id, row.consumer_version) for row in links] == [("src-1", "2"), ("src-2", "2")]
        assert {row.consumer_kind for row in links} == {"draft"}
        assert {row.consumer_id for row in links} == {"s1"}
        assert {row.session_id for row in links} == {"s1"}
        assert {row.relation_kind for row in links} == {"draft_reference"}

    async def test_next_save_replaces_links_with_current_set(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, "src-1")
        await _seed_asset(dao, "src-2")
        await dao.save_draft(_command(expected=1, command_id="cmd-1", payload=self._payload_with("src-1")))
        await dao.save_draft(_command(expected=2, command_id="cmd-2", payload=self._payload_with("src-2")))

        links = await _draft_links(real_embedded_pg.url)
        assert [(row.source_id, row.consumer_version) for row in links] == [("src-2", "3")]

    async def test_empty_draft_clears_links(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, "src-1")
        await dao.save_draft(_command(expected=1, command_id="cmd-1", payload=self._payload_with("src-1")))
        await dao.save_draft(_command(expected=2, command_id="cmd-2"))
        assert await _draft_links(real_embedded_pg.url) == []

    async def test_unregistered_source_rejected_without_side_effects(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(expected=1, payload=self._payload_with("src-missing")))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 1 and row.draft_payload is None
        assert await _command_rows(real_embedded_pg.url) == []
        assert await _draft_links(real_embedded_pg.url) == []

    async def test_other_user_scope_source_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, "src-1", user_scope="u2")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(expected=1, payload=self._payload_with("src-1")))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _draft_links(real_embedded_pg.url) == []

    async def test_source_digest_mismatch_rejected_without_side_effects(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _seed_asset(dao, "src-1")
        payload = _payload(
            source_refs=(
                SourceVersionRef(
                    source_kind=SourceKind.USER_MESSAGE,
                    source_id="src-1",
                    source_version="1",
                    source_digest="b" * 64,
                ),
            )
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_draft(_command(expected=1, payload=payload))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

        row = await _session_row(real_embedded_pg.url)
        assert row.draft_revision == 1 and row.draft_payload is None
        assert await _command_rows(real_embedded_pg.url) == []
        assert await _draft_links(real_embedded_pg.url) == []
