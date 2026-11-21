"""历史搜索 main/child 权威类型与角色筛选契约（检视 D17，不连数据库）。

- main/child 必须按 ``agent_execution.execution_kind`` 判定：main=root（另含无
  execution_id 的会话级消息），child=leaf/comparison_lane；不得再用
  ``execution_id IS NULL`` 代替（成果列 NOT NULL，旧口径 main 下零成果）；
- 关联必须是相关 EXISTS 子查询，不 JOIN 放大行数；
- role_identity 只约束消息：成果无权威「产生时角色」来源，不得以
  ``literal(False)`` 静默排空。

真实 PG 上的矩阵语义（root/leaf/comparison_lane × 消息/成果 × main/child 与
role_identity 命中/不命中）见 ``tests/integration/test_agent_dao_search_history.py``；
本文件不冒充集成证据。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import HistoryAgentFilter, HistoryQuery, HistoryRoundSelection
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentArtifact, AgentMessage

pytestmark = pytest.mark.unit

_DIALECT = postgresql.dialect()


def _literal_sql(statement: Any) -> str:
    return str(statement.compile(dialect=_DIALECT, compile_kwargs={"literal_binds": True}))


class _AsyncCM:
    """最小异步上下文管理器（连接/事务替身）。"""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _HistoryConn:
    """最小只读替身：记录语句、统一返回空行（走空页路径，不触解码）。"""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execution_options(self, **_kwargs: Any) -> _HistoryConn:
        return self

    def begin(self) -> _AsyncCM:
        return _AsyncCM(self)

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(statement)
        result = MagicMock()
        result.first.return_value = None
        result.all.return_value = []
        return result


def _dao(conn: _HistoryConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    engine.connect = MagicMock(return_value=_AsyncCM(conn))
    return AgentDao(engine)


def _query(**overrides: object) -> HistoryQuery:
    values: dict[str, object] = {
        "scope": "all_saved",
        "round_selection": HistoryRoundSelection(kind="all_rounds"),
        "query_text": "",
        "time_basis": "created",
        "sort": "newest_first",
    }
    values.update(overrides)
    return HistoryQuery(**values)  # type: ignore[arg-type]  # 测试受控入参


async def _union_sql(**overrides: object) -> str:
    conn = _HistoryConn()
    dao = _dao(conn)
    await dao.search_history(_query(**overrides), user_scope="u1")
    union_statements = [stmt for stmt in conn.statements if "UNION ALL" in _literal_sql(stmt)]
    assert len(union_statements) == 1
    return _literal_sql(union_statements[0])


class TestExecutionKindPredicate:
    def test_main_predicate_uses_root_kind_and_session_level_null(self) -> None:
        predicate = AgentDao._history_execution_kind_predicate(
            AgentMessage.execution_id,
            user_scope="u1",
            kinds=AgentDao._HISTORY_MAIN_EXECUTION_KINDS,
            null_counts_as_match=True,
        )
        sql = _literal_sql(select(AgentMessage.message_id).where(predicate))
        assert "agent_execution.execution_kind IN ('root')" in sql
        assert "agent_message.execution_id IS NULL" in sql
        assert "EXISTS" in sql

    def test_child_predicate_covers_leaf_and_comparison_lane_without_null_match(self) -> None:
        predicate = AgentDao._history_execution_kind_predicate(
            AgentArtifact.execution_id,
            user_scope="u1",
            kinds=AgentDao._HISTORY_CHILD_EXECUTION_KINDS,
        )
        sql = _literal_sql(select(AgentArtifact.artifact_id).where(predicate))
        assert "agent_execution.execution_kind IN ('leaf', 'comparison_lane')" in sql
        assert "agent_artifact.execution_id" in sql
        assert "IS NULL" not in sql


class TestSearchHistoryStatementContract:
    @pytest.mark.asyncio
    async def test_type_filter_applies_authoritative_kind_to_both_branches(self) -> None:
        sql = await _union_sql(type_filter="child")
        assert sql.count("agent_execution.execution_kind IN ('leaf', 'comparison_lane')") == 2
        # 不再以执行列是否为空代替权威类型
        assert "agent_message.execution_id IS NOT NULL" not in sql
        assert "agent_artifact.execution_id IS NOT NULL" not in sql

    @pytest.mark.asyncio
    async def test_main_filter_includes_root_and_session_level_messages(self) -> None:
        sql = await _union_sql(type_filter="main")
        assert sql.count("agent_execution.execution_kind IN ('root')") == 2
        assert "agent_message.execution_id IS NULL" in sql

    @pytest.mark.asyncio
    async def test_role_identity_filter_never_empties_artifacts(self) -> None:
        sql = await _union_sql(
            agent_filter=HistoryAgentFilter(kind="role_identity", role_id="role-1", version_selection="all_versions")
        )
        assert "false" not in sql.lower()
        # 角色筛选只落在消息分支
        assert "agent_message.role_id = 'role-1'" in sql
        assert "agent_artifact.role_id" not in sql
