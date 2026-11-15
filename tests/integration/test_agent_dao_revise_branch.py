"""会话修订/分支 DAO 集成测试（F-03-2b，真实 embedded PG）。

验证 §4.2 L436 + F29 的「核原消息身份/版本/范围 → 保存新因果前缀」与分支会话
创建：

- ``revise`` 只改本会话 ``causal_head_ref``（源消息之前最近一条或显式截点；
  首条消息 → 空前缀），不改消息、不建会话、不派发；
- ``branch`` 新建会话，``parent_session_id`` + 冻结键集 ``branch_metadata`` +
  ``causal_head_ref`` 引用原前缀，不复制正文/消息；
- 源消息版本不符 ``REVISION_CONFLICT`` 零新事实；跨会话/跨用户域/截点晚于
  源消息 ``INPUT_INVALID``；同命令重放查回原回执，重复分支目标不静默复用。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from sqlalchemy import insert, select, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommandReceipt,
    SessionAction,
    SessionChangeCommand,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentCommand, AgentMessage, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u1"


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


async def _seed_session(
    dao: AgentDao, *, session_id: str = "s1", user_scope: str = _USER, title: str = "源会话"
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试夹具
        await conn.execute(insert(AgentSession).values(user_scope=user_scope, session_id=session_id, title=title))


async def _seed_message(
    dao: AgentDao,
    *,
    message_id: str,
    session_id: str = "s1",
    seq: int,
    subject_revision: int = 1,
    user_scope: str = _USER,
) -> None:
    now = datetime.now(UTC)
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试夹具
        await conn.execute(
            insert(AgentMessage).values(
                user_scope=user_scope,
                message_id=message_id,
                session_id=session_id,
                event_id=f"evt-{message_id}",
                session_seq=seq,
                event_kind="public_message_saved",
                payload_kind="text",
                subject_id=message_id,
                subject_revision=subject_revision,
                role="user",
                body_text=f"body {message_id}",
                occurred_at=now,
                committed_at=now,
            )
        )


def _command(
    *,
    action: SessionAction = SessionAction.REVISE,
    session_id: str = "s1",
    expected: int = 1,
    title: str | None = None,
    source_message_id: str | None = "m3",
    source_message_revision: int | None = 1,
    cutoff: str | None = None,
    command_id: str = "cmd-rb-1",
) -> SessionChangeCommand:
    return SessionChangeCommand(
        command=CommandRef(
            user_scope=_USER,
            source_module="ui",
            business_request_id="req-rb",
            command_id=command_id,
            expected_revision=expected,
        ),
        action=action,
        session_id=session_id,
        expected_session_revision=expected,
        title=title,
        source_message_id=source_message_id,
        source_message_revision=source_message_revision,
        causal_cutoff_message_id=cutoff,
    )


def _expect_error(excinfo: Any, code: AgentErrorCode) -> None:
    err = excinfo.value
    assert isinstance(err, AgentServiceError)
    assert err.code == code, f"expect {code.value}, got {err.code.value}: {err.sanitized_message}"


async def _session_row(url: str, session_id: str = "s1") -> Any:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    select(
                        AgentSession.revision,
                        AgentSession.title,
                        AgentSession.parent_session_id,
                        AgentSession.branch_metadata,
                        AgentSession.causal_head_ref,
                        AgentSession.source_module,
                    ).where(AgentSession.user_scope == _USER, AgentSession.session_id == session_id)
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
                            AgentCommand.request_payload,
                            AgentCommand.receipt_payload,
                        ).where(AgentCommand.user_scope == _USER)
                    )
                )
                .all()
                .copy()
            )
    finally:
        await engine.dispose()


async def _message_count(url: str, session_id: str) -> int:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        select(text("count(*)"))
                        .select_from(AgentMessage)
                        .where(AgentMessage.user_scope == _USER, AgentMessage.session_id == session_id)
                    )
                ).scalar_one()
            )
    finally:
        await engine.dispose()


async def _seed_thread(dao: AgentDao) -> None:
    await _seed_session(dao)
    await _seed_message(dao, message_id="m1", seq=1)
    await _seed_message(dao, message_id="m2", seq=2)
    await _seed_message(dao, message_id="m3", seq=3)


class TestRevise:
    async def test_revise_sets_previous_message_as_causal_head(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        receipt = await dao.change_session(_command())

        assert isinstance(receipt, CommandReceipt)
        assert receipt.target_id == "s1"
        assert receipt.current_revision == 2
        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 2
        assert row.causal_head_ref == "m2"
        assert row.branch_metadata is None
        assert row.parent_session_id is None
        assert await _message_count(real_embedded_pg.url, "s1") == 3
        commands = await _command_rows(real_embedded_pg.url)
        assert len(commands) == 1
        assert commands[0].command_kind == "change_session"
        assert commands[0].request_payload["source_message_id"] == "m3"
        assert commands[0].request_payload["source_message_revision"] == 1

    async def test_revise_explicit_cutoff(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        await dao.change_session(_command(cutoff="m1"))

        assert (await _session_row(real_embedded_pg.url)).causal_head_ref == "m1"

    async def test_revise_first_message_yields_empty_prefix(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        await dao.change_session(_command(source_message_id="m1"))

        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 2
        assert row.causal_head_ref is None

    async def test_stale_source_message_version_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(source_message_revision=2))
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)

        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 1
        assert row.causal_head_ref is None
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_source_message_outside_target_session_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)
        await _seed_session(dao, session_id="s2")
        await _seed_message(dao, message_id="other", session_id="s2", seq=1)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(source_message_id="other"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_cutoff_after_source_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)
        await _seed_message(dao, message_id="m4", seq=4)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(source_message_id="m3", cutoff="m4"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert (await _session_row(real_embedded_pg.url)).revision == 1
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_replay_returns_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)
        command = _command()

        first = await dao.change_session(command)
        second = await dao.change_session(command)

        assert second == first
        assert (await _session_row(real_embedded_pg.url)).revision == 2
        assert len(await _command_rows(real_embedded_pg.url)) == 1


class TestBranch:
    async def test_branch_creates_session_with_frozen_metadata(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)
        source_before = await _session_row(real_embedded_pg.url)

        receipt = await dao.change_session(
            _command(
                action=SessionAction.BRANCH,
                session_id="s-branch",
                expected=0,
                title="分支会话",
                cutoff="m2",
            )
        )

        assert receipt.target_id == "s-branch"
        assert receipt.current_revision == 1
        branch = await _session_row(real_embedded_pg.url, "s-branch")
        assert branch.revision == 1
        assert branch.title == "分支会话"
        assert branch.parent_session_id == "s1"
        assert branch.causal_head_ref == "m2"
        assert branch.source_module == "ui"
        assert branch.branch_metadata == {
            "schema_version": 1,
            "source_session_id": "s1",
            "source_message_id": "m3",
            "source_message_version": 1,
            "causal_cutoff_message_id": "m2",
        }
        # 不复制消息正文，原会话不被移动。
        assert await _message_count(real_embedded_pg.url, "s-branch") == 0
        assert source_before.revision == (await _session_row(real_embedded_pg.url)).revision == 1

    async def test_branch_default_cutoff_is_previous_message(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        await dao.change_session(
            _command(action=SessionAction.BRANCH, session_id="s-branch", expected=0, title="分支会话")
        )

        branch = await _session_row(real_embedded_pg.url, "s-branch")
        assert branch.causal_head_ref == "m2"
        assert branch.branch_metadata["causal_cutoff_message_id"] == "m2"

    async def test_branch_from_first_message_has_empty_prefix(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        await dao.change_session(
            _command(
                action=SessionAction.BRANCH,
                session_id="s-branch",
                expected=0,
                title="分支会话",
                source_message_id="m1",
            )
        )

        branch = await _session_row(real_embedded_pg.url, "s-branch")
        assert branch.causal_head_ref is None
        assert branch.branch_metadata["causal_cutoff_message_id"] is None

    async def test_branch_stale_source_version_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(
                _command(
                    action=SessionAction.BRANCH,
                    session_id="s-branch",
                    expected=0,
                    title="分支会话",
                    source_message_revision=9,
                )
            )
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)
        assert await _session_row(real_embedded_pg.url, "s-branch") is None
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_branch_unknown_source_message_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(
                _command(
                    action=SessionAction.BRANCH,
                    session_id="s-branch",
                    expected=0,
                    title="分支会话",
                    source_message_id="missing",
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _session_row(real_embedded_pg.url, "s-branch") is None
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_branch_other_user_scope_source_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, user_scope="u2")
        await _seed_message(dao, message_id="m-other", user_scope="u2", seq=1)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(
                _command(
                    action=SessionAction.BRANCH,
                    session_id="s-branch",
                    expected=0,
                    title="分支会话",
                    source_message_id="m-other",
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _session_row(real_embedded_pg.url, "s-branch") is None

    async def test_branch_duplicate_target_not_reused(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_thread(dao)
        await dao.change_session(
            _command(action=SessionAction.BRANCH, session_id="s-branch", expected=0, title="分支会话")
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(
                _command(
                    action=SessionAction.BRANCH,
                    session_id="s-branch",
                    expected=0,
                    title="另一个分支",
                    command_id="cmd-rb-2",
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        branch = await _session_row(real_embedded_pg.url, "s-branch")
        assert branch.title == "分支会话"
        assert branch.revision == 1
