"""AgentDao.change_session 集成测试（F-03-2；DD §4.2 change_session、§9.3）。

真实 embedded PG 上验证单个显式事务语义：

- create：新会话行（revision=1、draft_revision=1、source_module 取命令入口），
  同 session_id 已存在 → INPUT_INVALID 且零新命令；重放查回原回执；
- rename/pin/archive/unarchive：行锁 CAS，成功 revision+1；归档不改运行；
- 预期版不符 → REVISION_CONFLICT 且零新事实；并发同预期版单胜者；
- 跨用户域/不存在会话 → INPUT_INVALID；
- revise/branch 需源消息身份/版本（缺失 → INPUT_INVALID，不写半链事实）；
- 并发同命令键（update/create）均查回同一原回执，不重复推进。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from alembic import command
from sqlalchemy import insert, select, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommitState,
    EventKind,
    EventReadQuery,
    SessionAction,
    SessionChangeCommand,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentCommand, AgentSession
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


async def _seed_session(dao: AgentDao, user_scope: str = _USER, session_id: str = "s1", title: str = "会话") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置会话
        await conn.execute(insert(AgentSession).values(user_scope=user_scope, session_id=session_id, title=title))


def _command(
    *,
    action: SessionAction = SessionAction.CREATE,
    session_id: str = "s1",
    expected: int = 0,
    title: str | None = "新会话",
    command_id: str = "cmd-1",
    user_scope: str = _USER,
) -> SessionChangeCommand:
    return SessionChangeCommand(
        command=CommandRef(
            user_scope=user_scope,
            source_module="ui",
            business_request_id=f"req-{command_id}",
            command_id=command_id,
            expected_revision=expected,
        ),
        action=action,
        session_id=session_id,
        expected_session_revision=expected,
        title=title,
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
                        AgentSession.title,
                        AgentSession.title_user_set,
                        AgentSession.pinned,
                        AgentSession.archived,
                        AgentSession.revision,
                        AgentSession.draft_revision,
                        AgentSession.source_module,
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
                            AgentCommand.receipt_payload,
                        ).where(AgentCommand.user_scope == _USER)
                    )
                )
                .all()
                .copy()
            )
    finally:
        await engine.dispose()


class TestCreateSession:
    async def test_create_lands_session_and_command(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await dao.change_session(_command())

        assert receipt.target_id == "s1"
        assert receipt.current_revision == 1
        assert receipt.commit_state is CommitState.COMMITTED
        row = await _session_row(real_embedded_pg.url)
        assert row.title == "新会话"
        assert row.revision == 1
        assert row.draft_revision == 1
        assert row.source_module == "ui"
        assert row.archived is False and row.pinned is False and row.title_user_set is False

        rows = await _command_rows(real_embedded_pg.url)
        assert len(rows) == 1
        assert rows[0].command_kind == "change_session"
        assert rows[0].expected_revision == 0
        assert rows[0].receipt_payload["current_revision"] == 1

    async def test_create_existing_session_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, title="已有会话")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(title="覆盖标题"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

        row = await _session_row(real_embedded_pg.url)
        assert row.title == "已有会话"
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_create_replay_returns_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        first = await dao.change_session(_command())
        second = await dao.change_session(_command())
        assert second == first
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_create_same_command_different_title_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.change_session(_command(title="新会话"))
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(title="另一标题"))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)
        row = await _session_row(real_embedded_pg.url)
        assert row.title == "新会话"

    async def test_concurrent_create_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        results = await asyncio.gather(
            dao.change_session(_command(command_id="cmd-a")),
            dao.change_session(_command(command_id="cmd-b")),
            return_exceptions=True,
        )
        receipts = [item for item in results if not isinstance(item, BaseException)]
        errors = [item for item in results if isinstance(item, AgentServiceError)]
        assert len(receipts) == 1, results
        assert len(errors) == 1
        assert errors[0].code in (AgentErrorCode.INPUT_INVALID, AgentErrorCode.INPUT_CONFLICT)
        assert len(await _command_rows(real_embedded_pg.url)) == 1


class TestMetadataActions:
    async def test_rename_updates_title_and_flags(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        before = await _session_row(real_embedded_pg.url)
        receipt = await dao.change_session(_command(action=SessionAction.RENAME, expected=1, title="改名后"))
        assert receipt.current_revision == 2

        row = await _session_row(real_embedded_pg.url)
        assert row.title == "改名后"
        assert row.title_user_set is True
        assert row.revision == 2
        assert row.updated_at > before.updated_at

    async def test_stale_rename_conflicts_with_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.change_session(_command(action=SessionAction.RENAME, expected=1, title="第一版"))
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(
                _command(action=SessionAction.RENAME, expected=1, title="越权覆盖", command_id="cmd-2")
            )
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)
        row = await _session_row(real_embedded_pg.url)
        assert row.title == "第一版" and row.revision == 2
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_pin_archive_unarchive_flags(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.change_session(_command(action=SessionAction.PIN, expected=1, title=None, command_id="c-pin"))
        row = await _session_row(real_embedded_pg.url)
        assert row.pinned is True and row.revision == 2

        await dao.change_session(_command(action=SessionAction.ARCHIVE, expected=2, title=None, command_id="c-arc"))
        row = await _session_row(real_embedded_pg.url)
        assert row.archived is True and row.revision == 3
        # 归档不改草稿版本，也不清标题（归档可搜）。
        assert row.draft_revision == 1 and row.title == "会话"

        await dao.change_session(_command(action=SessionAction.UNARCHIVE, expected=3, title=None, command_id="c-un"))
        row = await _session_row(real_embedded_pg.url)
        assert row.archived is False and row.revision == 4

    async def test_unknown_session_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(action=SessionAction.PIN, expected=1, title=None))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_other_user_scope_not_visible(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, user_scope="u2")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(action=SessionAction.ARCHIVE, expected=1, title=None))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_concurrent_metadata_cas_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        results = await asyncio.gather(
            dao.change_session(_command(action=SessionAction.RENAME, expected=1, title="A", command_id="cmd-a")),
            dao.change_session(_command(action=SessionAction.RENAME, expected=1, title="B", command_id="cmd-b")),
            return_exceptions=True,
        )
        receipts = [item for item in results if not isinstance(item, BaseException)]
        errors = [item for item in results if isinstance(item, AgentServiceError)]
        assert len(receipts) == 1, results
        assert len(errors) == 1 and errors[0].code is AgentErrorCode.REVISION_CONFLICT
        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 2
        assert row.title in ("A", "B")
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_concurrent_same_key_update_replays_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        command = _command(action=SessionAction.RENAME, expected=1, title="同一标题")
        results = await asyncio.gather(
            dao.change_session(command),
            dao.change_session(command),
            return_exceptions=True,
        )
        receipts = [item for item in results if not isinstance(item, BaseException)]
        assert len(receipts) == 2, results
        assert receipts[0] == receipts[1]
        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 2
        assert row.title == "同一标题"
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_concurrent_same_key_create_replays_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        command = _command(action=SessionAction.CREATE, expected=0, title="并发会话")
        results = await asyncio.gather(
            dao.change_session(command),
            dao.change_session(command),
            return_exceptions=True,
        )
        receipts = [item for item in results if not isinstance(item, BaseException)]
        assert len(receipts) == 2, results
        assert receipts[0] == receipts[1]
        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 1
        assert row.title == "并发会话"
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_concurrent_different_keys_same_session_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        results = await asyncio.gather(
            dao.change_session(_command(action=SessionAction.CREATE, expected=0, title="A", command_id="cmd-a")),
            dao.change_session(_command(action=SessionAction.CREATE, expected=0, title="B", command_id="cmd-b")),
            return_exceptions=True,
        )
        receipts = [item for item in results if not isinstance(item, BaseException)]
        errors = [item for item in results if isinstance(item, AgentServiceError)]
        assert len(receipts) == 1, results
        assert len(errors) == 1 and errors[0].code is AgentErrorCode.INPUT_INVALID
        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 1
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    @pytest.mark.parametrize("action", [SessionAction.REVISE, SessionAction.BRANCH])
    async def test_revise_and_branch_require_source_message(
        self, real_embedded_pg: ConnectionInfo, action: SessionAction
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        expected = 0 if action is SessionAction.BRANCH else 1
        title = "分支会话" if action is SessionAction.BRANCH else None
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(action=action, expected=expected, title=title))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert "source_message_id" in excinfo.value.sanitized_message
        row = await _session_row(real_embedded_pg.url)
        assert row.revision == 1
        assert await _command_rows(real_embedded_pg.url) == []


class TestMetadataEvents:
    """N22：元数据动作提交后从原水位可追平一条准确 ``session_changed`` 事件。"""

    async def test_rename_pin_archive_append_accurate_watermarked_events(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.change_session(
            _command(action=SessionAction.RENAME, expected=1, title="改名后", command_id="c-rename")
        )
        await dao.change_session(_command(action=SessionAction.PIN, expected=2, title=None, command_id="c-pin"))
        await dao.change_session(_command(action=SessionAction.ARCHIVE, expected=3, title=None, command_id="c-arc"))

        page = await dao.read_events(EventReadQuery(user_scope=_USER, session_id="s1", after_seq=0))
        assert [event.session_seq for event in page.events] == [1, 2, 3]
        assert all(event.event_kind is EventKind.SESSION_CHANGED for event in page.events)
        assert all(event.payload_kind == "session_changed" for event in page.events)
        assert [event.subject_revision for event in page.events] == [2, 3, 4]
        payloads = [json.loads(event.payload_canonical_json) for event in page.events]
        assert [payload["action"] for payload in payloads] == ["rename", "pin", "archive"]
        assert [payload["session_revision"] for payload in payloads] == [2, 3, 4]
        assert payloads[0]["title"] == "改名后" and payloads[0]["title_user_set"] is True
        assert payloads[1]["pinned"] is True
        assert payloads[2]["archived"] is True

        # 水位推进：从 seq=2 续读只得到第 3 条（增量订阅不再漏报）。
        tail = await dao.read_events(EventReadQuery(user_scope=_USER, session_id="s1", after_seq=2))
        assert [event.session_seq for event in tail.events] == [3]

    async def test_replay_and_conflict_append_no_extra_events(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        command = _command(action=SessionAction.RENAME, expected=1, title="改名后")
        first = await dao.change_session(command)
        assert await dao.change_session(command) == first

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.change_session(_command(action=SessionAction.PIN, expected=1, title=None, command_id="c-stale"))
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)

        page = await dao.read_events(EventReadQuery(user_scope=_USER, session_id="s1", after_seq=0))
        assert [event.session_seq for event in page.events] == [1]
        assert json.loads(page.events[0].payload_canonical_json)["session_revision"] == 2
