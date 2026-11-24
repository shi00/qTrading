"""历史搜索的标题/完整公开表示与缺投影失败契约（检视 D18，不连数据库）。

- 会话标题可搜：中文标题按 ILIKE 子串命中（不走 tsvector 分词），命中会话在
  范围内的消息/成果同为命中；已清理会话（cleaned_at 非空）不得经 tombstone 标题
  重新暴露；命中身份仍是既有 ``message`` / ``artifact``（无新 hit 类型）；
- 成果完整公开表示：``body_ref`` 载荷（``agent_body_payload.payload_bytes``）在
  库内按 utf-8 解码后 ILIKE（含尾词），不拉取大文本到 Python；与内联
  ``body_text`` / ``summary`` / 消息 ``search_text`` 共用同一通配符转义；
- 未公开（``published_at IS NULL``）/已清理成果在关键词搜索中显式排除；
- 缺投影明确失败：公开消息已有正文而 ``search_text IS NULL`` → OPERATION_UNKNOWN，
  不返回「看起来准确」的空页/残缺页；
- 既有 scope/type/role/state/date/游标 keyset 过滤不被关键词分支覆盖。

真实 PG 上的命中语义见 ``tests/integration/test_agent_dao_search_history.py``；
本文件只固定语句合同与失败判据，不冒充集成证据。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    HistoryAgentFilter,
    HistoryQuery,
    HistoryRoundSelection,
    compute_history_query_digest,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentArtifact, AgentMessage

pytestmark = pytest.mark.unit

# 用运行期方言（asyncpg / numeric_dollar）编译：字面量里的 % 不被 paramstyle 二次转义
_DIALECT = pg_asyncpg.PGDialect_asyncpg()


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
    """最小只读替身：记录语句、默认空行（走空页路径，不触解码）。"""

    def __init__(
        self,
        *,
        first_row_marker: str | None = None,
        session_row: bool = False,
        anchor_exists: bool = False,
        window_digest: str | None = None,
    ) -> None:
        self.statements: list[Any] = []
        self._first_row_marker = first_row_marker
        self._session_row = session_row
        self._anchor_exists = anchor_exists
        self._window_digest = window_digest

    async def execution_options(self, **_kwargs: Any) -> _HistoryConn:
        return self

    def begin(self) -> _AsyncCM:
        return _AsyncCM(self)

    async def execute(self, statement: Any, _params: Any = None) -> Any:
        self.statements.append(statement)
        sql = _literal_sql(statement)
        result = MagicMock()
        first_value = None
        if self._session_row and "FROM agent_session" in sql:
            first_value = SimpleNamespace(session_id="s-1", revision=1, event_seq=0)
        if self._anchor_exists and (
            "agent_message.message_id = 'anchor-1'" in sql or "agent_artifact.artifact_id = 'anchor-1'" in sql
        ):
            first_value = (1,)
        if self._first_row_marker is not None and self._first_row_marker in sql:
            first_value = (1,)
        if "md5(" in sql and self._window_digest is not None:
            first_value = (self._window_digest,)
        result.first.return_value = first_value
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


async def _union_sql(*, session_row: bool = False, anchor_exists: bool = False, **overrides: object) -> str:
    conn = _HistoryConn(session_row=session_row, anchor_exists=anchor_exists, window_digest="0123456789abcdef")
    dao = _dao(conn)
    await dao.search_history(_query(**overrides), user_scope="u1")
    union_statements = [
        stmt for stmt in conn.statements if "UNION ALL" in _literal_sql(stmt) and "md5(" not in _literal_sql(stmt)
    ]
    assert len(union_statements) == 1
    return _literal_sql(union_statements[0])


class TestSessionTitleSearch:
    """会话标题可搜（检视 D18）：中文 ILIKE 子串 + 已清理会话显式排除。"""

    @pytest.mark.asyncio
    async def test_chinese_title_predicate_in_both_branches(self) -> None:
        sql = await _union_sql(query_text="新能源")
        assert sql.count("agent_session.title ILIKE '%新能源%'") == 2
        assert sql.count("agent_session.cleaned_at IS NULL") == 2
        assert sql.count("agent_session.session_id = agent_message.session_id") == 1
        assert sql.count("agent_session.session_id = agent_artifact.session_id") == 1

    @pytest.mark.asyncio
    async def test_no_title_predicate_without_keyword(self) -> None:
        sql = await _union_sql()
        assert "agent_session.title" not in sql


class TestMessageSummaryInheritsPublicProjection:
    """消息摘要继承公开投影（T-05）：不得把原始 body_text 当摘要带出。"""

    @pytest.mark.asyncio
    async def test_message_summary_selects_search_text(self) -> None:
        sql = await _union_sql()
        assert "agent_message.search_text AS summary_source" in sql
        assert "agent_message.body_text AS summary_source" not in sql


class TestHistoryWindowDigest:
    """历史游标集合摘要（T-05/F-04）：固定上界内命中集合修订 → 显式失效重首读。"""

    @pytest.mark.asyncio
    async def test_window_digest_aggregates_ordered_full_key(self) -> None:
        digest = compute_history_query_digest(_query(limit=2))
        cursor = (
            f"h:{digest}:2026-07-01T00:00:00.000000Z:2026-06-30T00:00:00.000000Z:message:anchor-1:0:0123456789abcdef"
        )
        conn = _HistoryConn(anchor_exists=True, window_digest="0123456789abcdef")
        await _dao(conn).search_history(_query(limit=2, cursor=cursor), user_scope="u1")
        digest_sql = next(_literal_sql(stmt) for stmt in conn.statements if "md5(" in _literal_sql(stmt))
        assert "string_agg(" in digest_sql
        assert "ORDER BY" in digest_sql
        # 分隔符是第一实参：若误放进 aggregate_order_by，会退化为单参 string_agg(text) 而报错
        assert "string_agg(','," in digest_sql
        assert digest_sql.index("string_agg(',',") < digest_sql.index("ORDER BY")
        assert "concat_ws('|'" in digest_sql
        assert "UNION ALL" in digest_sql

    @pytest.mark.asyncio
    async def test_revised_window_rejects_cursor_before_keyset(self) -> None:
        digest = compute_history_query_digest(_query(limit=2))
        cursor = (
            f"h:{digest}:2026-07-01T00:00:00.000000Z:2026-06-30T00:00:00.000000Z:message:anchor-1:0:ffffffffffffffff"
        )
        conn = _HistoryConn(anchor_exists=True, window_digest="0123456789abcdef")
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).search_history(_query(limit=2, cursor=cursor), user_scope="u1")
        assert exc.value.code == AgentErrorCode.REVISION_CONFLICT
        # 校验先于命中扫描：不执行带 LIMIT 的命中 UNION 查询
        assert all("LIMIT" not in _literal_sql(stmt) for stmt in conn.statements)


class TestArtifactPublicBodySearch:
    """成果 body_ref 完整公开表示可搜（检视 D18），排除未公开/已清理原件。"""

    @pytest.mark.asyncio
    async def test_body_ref_payload_text_is_in_keyword_or(self) -> None:
        sql = await _union_sql(query_text="阶段性结论")
        assert "convert_from(agent_body_payload.payload_bytes, 'UTF8')" in sql
        assert "agent_body_payload.body_ref = agent_artifact.body_ref" in sql
        assert "agent_body_payload.user_scope = agent_artifact.user_scope" in sql
        assert "'%阶段性结论%'" in sql
        # 三路结构：摘要 / 内联正文 / 载荷正文（任一命中）
        assert "agent_artifact.summary ILIKE" in sql
        assert "agent_artifact.body_text ILIKE" in sql

    @pytest.mark.asyncio
    async def test_unpublished_and_cleaned_artifacts_explicitly_excluded(self) -> None:
        sql = await _union_sql(query_text="结论")
        assert "agent_artifact.published_at IS NOT NULL" in sql
        assert "agent_artifact.cleaned_at IS NULL" in sql

    @pytest.mark.asyncio
    async def test_visibility_filter_only_on_keyword_search(self) -> None:
        sql = await _union_sql()
        assert "agent_artifact.published_at IS NOT NULL" not in sql
        assert "agent_artifact.cleaned_at IS NULL" not in sql

    @pytest.mark.asyncio
    async def test_wildcards_escaped_consistently_across_paths(self) -> None:
        sql = await _union_sql(query_text="100%_a")
        escaped = "'%100\\%\\_a%'"
        # 消息 search_text + 标题 + 成果 summary/body_text/载荷正文 同一转义
        assert sql.count(escaped) == 6

    def test_body_predicate_is_correlated_exists(self) -> None:
        pattern = AgentDao._history_like_pattern("尾词")
        compiled = _literal_sql(
            select(AgentArtifact.artifact_id).where(AgentDao._history_artifact_body_predicate(pattern))
        )
        assert "EXISTS" in compiled
        assert compiled.count("agent_artifact.body_ref") >= 1
        assert "agent_body_payload.body_ref" in compiled


class TestMissingProjectionFailsExplicitly:
    """缺索引/缺投影明确失败（检视 D18）：不返回空页伪装准确。"""

    @pytest.mark.asyncio
    async def test_missing_search_text_raises_operation_unknown(self) -> None:
        conn = _HistoryConn(first_row_marker="agent_message.search_text IS NULL")
        dao = _dao(conn)
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(_query(query_text="回撤"), user_scope="u1")
        assert exc.value.code == AgentErrorCode.OPERATION_UNKNOWN
        preflight = [stmt for stmt in conn.statements if "agent_message.search_text IS NULL" in _literal_sql(stmt)]
        assert len(preflight) == 1
        sql = _literal_sql(preflight[0])
        # 判据固定在语句上：公开消息 + 已有正文（body_text 或 body_ref）
        assert "agent_message.event_kind = 'public_message_saved'" in sql
        assert "agent_message.body_text IS NOT NULL" in sql
        assert "agent_message.body_ref IS NOT NULL" in sql
        # 失败先于扫描：未生成 UNION 语句
        assert not [stmt for stmt in conn.statements if "UNION ALL" in _literal_sql(stmt)]

    @pytest.mark.asyncio
    async def test_no_projection_check_without_keyword(self) -> None:
        conn = _HistoryConn(first_row_marker="search_text IS NULL")
        dao = _dao(conn)
        page = await dao.search_history(_query(), user_scope="u1")
        assert page.hits == ()
        assert not [stmt for stmt in conn.statements if "search_text IS NULL" in _literal_sql(stmt)]

    @pytest.mark.asyncio
    async def test_projection_range_reuses_scope_type_role_state_date_filters(self) -> None:
        conn = _HistoryConn(first_row_marker="agent_message.search_text IS NULL", session_row=True)
        dao = _dao(conn)
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _query(
                    scope="current_session",
                    session_id="s-1",
                    query_text="回撤",
                    type_filter="main",
                    message_role_filter="user",
                    state_filter="running",
                    # 消息发生口径（N21：created 口径按会话创建日，不再落在行 created_at）
                    time_basis="message",
                    date_from="2026-06-01",
                    date_to="2026-06-30",
                ),
                user_scope="u1",
            )
        assert exc.value.code == AgentErrorCode.OPERATION_UNKNOWN
        preflight = next(
            _literal_sql(stmt) for stmt in conn.statements if "agent_message.search_text IS NULL" in _literal_sql(stmt)
        )
        assert "agent_message.session_id = 's-1'" in preflight
        assert "agent_execution.execution_kind IN ('root')" in preflight
        assert "agent_message.role = 'user'" in preflight
        assert "agent_execution.display_state = 'running'" in preflight
        assert "agent_message.occurred_at >= '2026-06-01" in preflight
        assert "agent_message.occurred_at < '2026-07-01" in preflight


class TestExistingFiltersNotBypassed:
    """关键词分支不得覆盖既有过滤与游标 keyset（检视 D16/D17 不回退）。"""

    @pytest.mark.asyncio
    async def test_query_text_keeps_type_role_state_date_filters(self) -> None:
        sql = await _union_sql(
            scope="current_session",
            session_id="s-1",
            query_text="回撤",
            type_filter="child",
            message_role_filter="user",
            state_filter="running",
            # 消息发生口径（N21：created 口径按会话创建日，不再落在行 created_at）
            time_basis="message",
            date_from="2026-06-01",
            date_to="2026-06-30",
            session_row=True,
        )
        assert sql.count("agent_execution.execution_kind IN ('leaf', 'comparison_lane')") == 2
        assert "agent_message.role = 'user'" in sql
        assert sql.count("agent_execution.display_state = 'running'") == 2
        assert sql.count("agent_message.occurred_at >= '2026-06-01") == 1
        assert sql.count("agent_artifact.created_at < '2026-07-01") == 1

    @pytest.mark.asyncio
    async def test_query_text_keeps_cursor_keyset(self) -> None:
        base = _query(query_text="回撤", limit=2)
        digest = compute_history_query_digest(base)
        cursor = (
            f"h:{digest}:2026-07-01T00:00:00.000000Z:2026-06-30T00:00:00.000000Z:artifact:anchor-1:2:0123456789abcdef"
        )
        sql = await _union_sql(query_text="回撤", limit=2, cursor=cursor, anchor_exists=True)
        # 成果分支 keyset 带版本分量（检视 D19：同 ID 多版本同时间不跳过）
        assert "agent_artifact.artifact_id < 'anchor-1'" in sql
        assert "agent_artifact.artifact_version < 2" in sql
        assert "'anchor-1'" in sql

    @pytest.mark.asyncio
    async def test_message_cursor_keyset_uses_full_order_without_version(self) -> None:
        digest = compute_history_query_digest(_query(query_text="回撤", limit=2))
        cursor = (
            f"h:{digest}:2026-07-01T00:00:00.000000Z:2026-06-30T00:00:00.000000Z:message:anchor-1:0:0123456789abcdef"
        )
        sql = await _union_sql(query_text="回撤", limit=2, cursor=cursor, anchor_exists=True)
        # 消息分支按 message_id 续读；消息无版本，不生成版本比较
        assert "agent_message.message_id < 'anchor-1'" in sql
        assert "0 <" not in sql
        # 同刻成果（类型 'artifact' < 'message'）在 newest_first 中排在其后整体续读
        assert "agent_artifact.created_at = '2026-06-30" in sql
        assert "agent_artifact.artifact_id <" not in sql

    @pytest.mark.asyncio
    async def test_artifact_anchor_requires_exact_version(self) -> None:
        """锚行按完整键唯一命中：同锚 ID 但版本不同不得放行（检视 D19）。"""

        digest = compute_history_query_digest(_query(limit=2))
        cursor = (
            f"h:{digest}:2026-07-01T00:00:00.000000Z:2026-06-30T00:00:00.000000Z:artifact:anchor-1:2:0123456789abcdef"
        )
        conn = _HistoryConn(window_digest="0123456789abcdef")
        dao = _dao(conn)
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(_query(limit=2, cursor=cursor), user_scope="u1")
        assert exc.value.code == AgentErrorCode.REVISION_CONFLICT
        anchor_sql = next(
            _literal_sql(stmt)
            for stmt in conn.statements
            if "agent_artifact.artifact_id = 'anchor-1'" in _literal_sql(stmt)
        )
        assert "agent_artifact.artifact_version = 2" in anchor_sql
        assert "agent_message" not in anchor_sql

    @pytest.mark.asyncio
    async def test_union_orders_by_full_total_key(self) -> None:
        """UNION 排序与两分支 keyset 共用全序键（检视 D19）。"""

        sql = await _union_sql()
        order_clause = sql.split("ORDER BY", 1)[1]
        for column in ("sort_moment", "hit_kind", "anchor_id", "sort_version"):
            assert column in order_clause
        assert order_clause.count("DESC") == 4
        oldest = await _union_sql(sort="oldest_first")
        oldest_order = oldest.split("ORDER BY", 1)[1]
        assert "DESC" not in oldest_order

    @pytest.mark.asyncio
    async def test_query_text_keeps_agent_role_identity_filter(self) -> None:
        sql = await _union_sql(
            query_text="回撤",
            agent_filter=HistoryAgentFilter(
                kind="role_identity", role_id="role-1", version_selection="exact", role_version="2"
            ),
        )
        assert "agent_message.role_id = 'role-1'" in sql
        assert "agent_message.role_version = '2'" in sql
        assert "agent_artifact.role_id" not in sql
