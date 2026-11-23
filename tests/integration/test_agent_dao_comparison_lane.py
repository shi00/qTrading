"""比较组权威 manifest 读取 + 未派发路撤单 DAO 集成测试（P1-09 残余项）。

真实 embedded PG 上验证：

- ``read_comparison_manifest``：同组多版本按「最新已确认提交」取权威（``created_at``
  降序、乱序插入不取最早；未确认提交不参与）；
- ``withdraw_comparison_lane``：排队态真实撤回并关闭派发、已派发/已结束标
  ``preserved`` 不改写、重复调用幂等、未知执行/非比较路可核否定。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import Mode, Purpose, decode_json_doc
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentCommand, AgentExecution, AgentSession
from services.agent_service.comparison import (
    ComparisonLaneSelection,
    ComparisonSharedInput,
    decode_comparison_manifest,
    freeze_comparison_group,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "user-p1-09"
_GROUP_COMMAND = "cmp-authority"


def _group(*connections: str) -> Any:
    return freeze_comparison_group(
        user_scope=_USER,
        session_id="session-cmp",
        source_module="ui",
        group_command_id=_GROUP_COMMAND,
        shared=ComparisonSharedInput(text="同一任务", mode=Mode.DISCUSS, purpose=Purpose.CONCEPT),
        lanes=tuple(
            ComparisonLaneSelection(
                connection_id=connection_id,
                connection_revision=1,
                target_fingerprint=f"fp-{connection_id}",
                connection_purpose="agent_research",
                scope_version=1,
                display_name=connection_id,
            )
            for connection_id in connections
        ),
    )


async def _setup(url: str) -> AgentDao:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


async def _seed_session(dao: AgentDao, session_id: str) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造前置会话
        await conn.execute(
            insert(AgentSession).values(user_scope=_USER, session_id=session_id, title=f"session {session_id}")
        )


async def _seed_lane(
    dao: AgentDao,
    *,
    execution_id: str,
    session_id: str,
    group_ref: str | None,
    lane_ref: str | None,
    display_state: str = "queued",
    dispatch_closed: bool = False,
    current_round_id: str | None = None,
    kind: str = "comparison_lane",
) -> None:
    values: dict[str, Any] = {
        "user_scope": _USER,
        "execution_id": execution_id,
        "session_id": session_id,
        "execution_kind": kind,
        "node_id": execution_id,
        "display_state": display_state,
        "goal_coverage": "unknown",
        "dispatch_closed": dispatch_closed,
        "current_round_id": current_round_id,
        "comparison_group_ref": group_ref,
        "comparison_lane_ref": lane_ref,
    }
    if kind in ("root", "comparison_lane"):
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造前置执行
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_lane_command(
    dao: AgentDao,
    *,
    record_id: str,
    execution_id: str,
    session_id: str,
    manifest_payload: str,
    created_at: datetime,
    commit_state: str = "committed",
    revision: int = 1,
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造前置命令
        await conn.execute(
            insert(AgentCommand).values(
                user_scope=_USER,
                command_record_id=record_id,
                client_command_id=f"client-{record_id}",
                target_id=session_id,
                command_kind="submit",
                session_id=session_id,
                execution_id=execution_id,
                expected_revision=0,
                input_revision=1,
                input_digest="0" * 64,
                request_payload=json.loads(manifest_payload),
                commit_state=commit_state,
                delivery_state="not_delivered",
                consumption_state="not_consumed",
                created_at=created_at,
                updated_at=created_at,
                revision=revision,
            )
        )


class TestReadComparisonManifestAuthority:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_authoritative_version_is_latest_confirmed_not_earliest(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        group_ab = _group("conn-a", "conn-b")
        group_abc = _group("conn-a", "conn-b", "conn-c")
        group_abcd = _group("conn-a", "conn-b", "conn-c", "conn-d")
        await _seed_session(dao, "session-cmp")
        await _seed_lane(
            dao,
            execution_id="exec-a",
            session_id="session-cmp",
            group_ref=group_ab.group_ref,
            lane_ref=group_ab.lanes[0].lane_ref,
        )
        await _seed_lane(
            dao,
            execution_id="exec-c",
            session_id="session-cmp",
            group_ref=group_ab.group_ref,
            lane_ref=group_abc.lanes[2].lane_ref,
        )
        await _seed_lane(
            dao,
            execution_id="exec-d",
            session_id="session-cmp",
            group_ref=group_ab.group_ref,
            lane_ref=group_abcd.lanes[3].lane_ref,
        )
        # 乱序插入：最新确认版本先写、最早版本后写、未确认的最新时间戳最后写。
        await _seed_lane_command(
            dao,
            record_id="cmd-new",
            execution_id="exec-c",
            session_id="session-cmp",
            manifest_payload=group_abc.manifest_payload,
            created_at=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        )
        await _seed_lane_command(
            dao,
            record_id="cmd-old",
            execution_id="exec-a",
            session_id="session-cmp",
            manifest_payload=group_ab.manifest_payload,
            created_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        )
        await _seed_lane_command(
            dao,
            record_id="cmd-unconfirmed",
            execution_id="exec-d",
            session_id="session-cmp",
            manifest_payload=group_abcd.manifest_payload,
            created_at=datetime(2026, 11, 20, 13, 0, tzinfo=UTC),
            commit_state="commit_unknown",
        )

        payload = await dao.read_comparison_manifest(user_scope=_USER, group_ref=group_ab.group_ref)

        assert payload is not None
        authoritative = decode_comparison_manifest(decode_json_doc(payload))
        assert authoritative.manifest_digest == group_abc.manifest_digest
        assert authoritative.manifest_digest != group_ab.manifest_digest
        assert authoritative.manifest_digest != group_abcd.manifest_digest

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_group_returns_none(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        assert await dao.read_comparison_manifest(user_scope=_USER, group_ref="comparison_group_missing") is None


class TestWithdrawComparisonLane:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_queued_lane_is_withdrawn_and_dispatch_closed(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        group = _group("conn-a", "conn-b")
        await _seed_session(dao, "session-cmp")
        await _seed_lane(
            dao,
            execution_id="exec-a",
            session_id="session-cmp",
            group_ref=group.group_ref,
            lane_ref=group.lanes[0].lane_ref,
        )

        outcome = await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-a")

        assert (outcome.withdrawn, outcome.preserved, outcome.reason) == (True, False, "withdrawn")
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 断言落库状态
            row = (
                await conn.execute(
                    sa.select(
                        AgentExecution.display_state,
                        AgentExecution.dispatch_closed,
                        AgentExecution.ended_at,
                        AgentExecution.revision,
                    ).where(AgentExecution.execution_id == "exec-a")
                )
            ).one()
        assert row.display_state == "withdrawn"
        assert row.dispatch_closed is True
        assert row.ended_at is not None
        assert row.revision == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_repeated_withdraw_is_idempotent(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        group = _group("conn-a", "conn-b")
        await _seed_session(dao, "session-cmp")
        await _seed_lane(
            dao,
            execution_id="exec-a",
            session_id="session-cmp",
            group_ref=group.group_ref,
            lane_ref=group.lanes[0].lane_ref,
        )
        await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-a")

        outcome = await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-a")

        assert (outcome.withdrawn, outcome.preserved, outcome.reason) == (True, False, "already_withdrawn")
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 断言幂等不改写修订
            revision = (
                await conn.execute(sa.select(AgentExecution.revision).where(AgentExecution.execution_id == "exec-a"))
            ).scalar_one()
        assert revision == 2

    @pytest.mark.asyncio(loop_scope="session")
    async def test_dispatched_lane_is_preserved_not_rewritten(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        group = _group("conn-a", "conn-b")
        await _seed_session(dao, "session-cmp")
        await _seed_lane(
            dao,
            execution_id="exec-b",
            session_id="session-cmp",
            group_ref=group.group_ref,
            lane_ref=group.lanes[1].lane_ref,
            display_state="running",
        )

        outcome = await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-b")

        assert (outcome.withdrawn, outcome.preserved, outcome.reason) == (False, True, "already_dispatched")
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 断言已派发路不动
            row = (
                await conn.execute(
                    sa.select(AgentExecution.display_state, AgentExecution.dispatch_closed).where(
                        AgentExecution.execution_id == "exec-b"
                    )
                )
            ).one()
        assert row.display_state == "running"
        assert row.dispatch_closed is False

    @pytest.mark.asyncio(loop_scope="session")
    async def test_round_linked_lane_is_preserved(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        group = _group("conn-a", "conn-b")
        await _seed_session(dao, "session-cmp")
        await _seed_lane(
            dao,
            execution_id="exec-b",
            session_id="session-cmp",
            group_ref=group.group_ref,
            lane_ref=group.lanes[1].lane_ref,
            display_state="queued",
            current_round_id="round-b",
        )

        outcome = await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-b")

        assert (outcome.withdrawn, outcome.preserved) == (False, True)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_non_lane_and_unknown_execution_are_not_withdrawn(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "session-cmp")
        await _seed_lane(
            dao,
            execution_id="exec-root",
            session_id="session-cmp",
            group_ref=None,
            lane_ref=None,
            kind="root",
        )

        root_outcome = await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-root")
        missing_outcome = await dao.withdraw_comparison_lane(user_scope=_USER, execution_id="exec-missing")

        assert (root_outcome.withdrawn, root_outcome.preserved, root_outcome.reason) == (
            False,
            True,
            "not_comparison_lane",
        )
        assert (missing_outcome.withdrawn, missing_outcome.preserved, missing_outcome.reason) == (
            False,
            False,
            "not_found",
        )
