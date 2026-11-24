"""历史搜索日期窗口的 time_basis 口径契约（N21，不连数据库）。

F18：时间筛选必须明确为「会话创建」或「消息发生」时刻，两者不得混用。
``time_basis='created'`` 的日期窗口须落在所属会话的 ``agent_session.created_at``，
而不是消息/成果行各自的 ``created_at``（行时点为写入时刻，跨日会话的次日消息/
成果会被漏掉，或按次日误纳该会话）；``time_basis='message'`` 的消息侧仍按
``agent_message.occurred_at``。

真实 PG 上的跨两日会话结果差异见
``tests/integration/test_agent_dao_search_history.py``；本文件只核语句口径，
不冒充集成证据。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import HistoryQuery, HistoryRoundSelection
from data.persistence.daos.agent_dao import AgentDao

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


async def _search_sql(**overrides: object) -> str:
    conn = _HistoryConn()
    dao = _dao(conn)
    await dao.search_history(_query(**overrides), user_scope="u1")
    union_statements = [stmt for stmt in conn.statements if "UNION ALL" in _literal_sql(stmt)]
    assert len(union_statements) == 1
    return _literal_sql(union_statements[0])


class TestCreatedBasisUsesSessionCreationMoment:
    @pytest.mark.asyncio
    async def test_date_window_filters_on_session_created_at_for_both_branches(self) -> None:
        sql = await _search_sql(time_basis="created", date_from="2026-06-01", date_to="2026-06-01")
        # 消息与成果两分支都以会话创建时刻判定，同一口径（EXISTS 各一次）
        assert sql.count("agent_session.created_at >= '2026-06-01 00:00:00+00:00'") == 2
        assert sql.count("agent_session.created_at < '2026-06-02 00:00:00+00:00'") == 2
        assert sql.count("agent_session.session_id = agent_message.session_id") == 1
        assert sql.count("agent_session.session_id = agent_artifact.session_id") == 1

    @pytest.mark.asyncio
    async def test_date_window_does_not_compare_row_created_at_to_day_bounds(self) -> None:
        sql = await _search_sql(time_basis="created", date_from="2026-06-01", date_to="2026-06-01")
        # 行时点（写入时刻）不得作为会话创建日筛选依据
        assert "agent_message.created_at >= '2026-06-01 00:00:00+00:00'" not in sql
        assert "agent_artifact.created_at >= '2026-06-01 00:00:00+00:00'" not in sql
        assert "agent_message.created_at < '2026-06-02 00:00:00+00:00'" not in sql
        assert "agent_artifact.created_at < '2026-06-02 00:00:00+00:00'" not in sql

    @pytest.mark.asyncio
    async def test_no_date_window_adds_no_session_creation_predicate(self) -> None:
        sql = await _search_sql(time_basis="created")
        assert "agent_session.created_at" not in sql


class TestMessageBasisKeepsOccurrenceMoment:
    @pytest.mark.asyncio
    async def test_message_basis_date_window_uses_occurred_at_not_session_creation(self) -> None:
        sql = await _search_sql(time_basis="message", date_from="2026-06-01", date_to="2026-06-01")
        assert "agent_message.occurred_at >= '2026-06-01 00:00:00+00:00'" in sql
        assert "agent_message.occurred_at < '2026-06-02 00:00:00+00:00'" in sql
        # 消息发生口径不得掺入会话创建日过滤
        assert "agent_session.created_at" not in sql
