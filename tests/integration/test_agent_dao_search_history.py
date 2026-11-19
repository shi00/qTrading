"""AgentDao C-03-3b 历史搜索集成测试（§4.2 R02 search_history + HistoryPage）。

真实 embedded PG 上验证：

- 三范围扫描：all_saved（全用户域）/current_session（限定主会话）/
  current_child（限定准确叶，parent/node_id 身份错配 INPUT_INVALID）；
- 过滤：query_text 占位 LIKE（C-03-4 全文索引前）、message_role_filter、
  type_filter（按 agent_execution.execution_kind：main=root+会话级消息/
  child=leaf+comparison_lane，消息与成果同口径）、round_selection exact、
  agent_filter execution_agent/role_identity、date 窗口（time_basis 列映射）；
- 排序与游标：全序键 (sort_moment, hit_kind, anchor_id, artifact_version)
  四分量同向 keyset（newest_first DESC/oldest_first ASC；同 artifact_id 多版本
  同时间按版本续读，不重不漏），fixed_upper_bound 首读生成、游标继承续读；
  游标内查询摘要绑定规范查询（换筛选/换范围复用即 REVISION_CONFLICT）；limit
  与游标不入 digest；
- 标题与完整公开表示（检视 D18）：中文会话标题按 ILIKE 子串命中（未清理会话）、
  成果 ``body_ref`` 载荷公开正文（含尾词）可搜、未公开/已清理成果显式排除、
  公开消息缺 ``search_text`` 投影时 OPERATION_UNKNOWN（不返回准确空页）；
- HistoryPage：query_digest/revision_fingerprint（水位变化则指纹变化；all_saved
  展开 execution 成员）/无命中空页 / REVISION_CONFLICT（翻页时覆盖域成员消失）；
- 精确目标归属（R2-I28）：exact 轮次与 execution_agent 过滤须核执行属于目标
  会话、轮次属于该执行，目标间冲突显式拒绝；合法无命中才是空页。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import func, insert, select, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CLEANUP_TOMBSTONE,
    EventKind,
    HistoryAgentFilter,
    HistoryChildTarget,
    HistoryQuery,
    HistoryRoundSelection,
    MessageRole,
    RecordEventRequest,
    compute_history_query_digest,
    json_doc,
    parse_history_cursor,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentArtifact,
    AgentBodyPayload,
    AgentExecution,
    AgentMessage,
    AgentRound,
    AgentSession,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "synthetic-user"
OTHER_SCOPE = "other-user"

#: 可选参数「未给出」哨兵（None 是有效取值：未公开/无内联正文/无投影）
_UNSET: object = object()


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


async def _seed_session(dao: AgentDao, session_id: str, *, title: str = "历史会话", archived: bool = False) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title=title, archived=archived)
        )


async def _seed_execution(
    dao: AgentDao,
    execution_id: str,
    session_id: str,
    *,
    node_id: str = "root",
    parent_execution_id: str | None = None,
    execution_kind: str | None = None,
) -> None:
    kind = execution_kind or ("leaf" if parent_execution_id else "root")
    values: dict = {
        "user_scope": USER_SCOPE,
        "execution_id": execution_id,
        "session_id": session_id,
        "execution_kind": kind,
        "node_id": node_id,
        "display_state": "queued",
        "goal_coverage": "unknown",
    }
    if parent_execution_id:
        values["parent_execution_id"] = parent_execution_id
    if kind == "leaf":
        # 叶必须为 NULL 意图键（CheckConstraint intent_keys）
        assert parent_execution_id is not None
    else:
        # root / comparison_lane 必须带意图键（CheckConstraint intent_keys）
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
        values["intent_digest"] = "a" * 64
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_round(dao: AgentDao, round_id: str, execution_id: str, *, number: int = 1) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentRound).values(
                user_scope=USER_SCOPE,
                round_id=round_id,
                execution_id=execution_id,
                round_number=number,
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


async def _seed_message(
    dao: AgentDao,
    session_id: str,
    native_event_id: str,
    *,
    execution_id: str | None = None,
    round_id: str | None = None,
    role: MessageRole = MessageRole.ASSISTANT,
    body_text: str = "默认正文",
    search_text: object = _UNSET,
    occurred_at: str = "2026-06-01T12:00:00.000000Z",
    node_id: str | None = None,
    role_id: str | None = None,
    role_version: str | None = None,
) -> int:
    request = RecordEventRequest(
        user_scope=USER_SCOPE,
        session_id=session_id,
        message_id=f"m-{native_event_id}",
        native_event_id=native_event_id,
        event_kind=EventKind.PUBLIC_MESSAGE_SAVED,
        subject_id=f"m-{native_event_id}",
        subject_revision=1,
        payload_kind="public_message_saved.v1",
        payload_canonical_json=json_doc({"role": role.value}),
        occurred_at=occurred_at,
        execution_id=execution_id,
        round_id=round_id,
        node_id=node_id if node_id is not None else ("root" if execution_id else None),
        role=role,
        role_id=role_id,
        role_version=role_version,
        body_text=body_text,
        search_text=body_text if search_text is _UNSET else search_text,  # pyright: ignore[reportArgumentType]
    )
    fact = await dao.record_event(request)
    return fact.session_seq


async def _seed_external_message(
    dao: AgentDao,
    session_id: str,
    native_event_id: str,
    *,
    body_ref: str,
    full_text: str,
    search_text: object = _UNSET,
    occurred_at: datetime | None = None,
) -> str:
    """种一条公开消息，正文**外置**到 ``agent_body_payload``（真实外置形态）。

    生产外置写入点不在本仓库（读取路径只消费已存数据），故按模型契约构造：
    ``agent_message.body_text`` 为 NULL、``body_ref`` 指向载荷、``search_text``
    为完整公开正文投影（``_UNSET`` 时取全文）。载荷↔消息的双向 FK 为
    DEFERRABLE，必须与消息行同事务写入（与 ``_seed_artifact`` 同一理由）。
    返回 message_id。
    """

    message_id = f"m-{native_event_id}"
    data = full_text.encode("utf-8")
    projection = full_text if search_text is _UNSET else search_text
    async with dao.engine.begin() as conn:
        session_seq = (
            await conn.execute(
                select(func.coalesce(func.max(AgentMessage.session_seq), 0) + 1).where(
                    AgentMessage.user_scope == USER_SCOPE,
                    AgentMessage.session_id == session_id,
                )
            )
        ).scalar_one()
        await conn.execute(
            insert(AgentBodyPayload).values(
                user_scope=USER_SCOPE,
                body_ref=body_ref,
                owner_kind="message",
                owner_id=message_id,
                owner_version="1",
                message_id=message_id,
                encoding="utf-8",
                block_ordinal=0,
                payload_bytes=data,
                byte_length=len(data),
                payload_digest=sha256_hex(data),
            )
        )
        values: dict[str, object] = {
            "user_scope": USER_SCOPE,
            "message_id": message_id,
            "session_id": session_id,
            "event_id": f"evt-{native_event_id}",
            "session_seq": session_seq,
            "event_kind": "public_message_saved",
            "payload_kind": "public_message_saved.v1",
            "subject_id": message_id,
            "subject_revision": 1,
            "role": "assistant",
            "body_ref": body_ref,
            "occurred_at": occurred_at or datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
            "committed_at": datetime.now(UTC),
            "search_text": projection,
        }
        await conn.execute(insert(AgentMessage).values(**values))
    return message_id


def _parse_wire(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


async def _seed_artifact(
    dao: AgentDao,
    artifact_id: str,
    session_id: str,
    execution_id: str,
    round_id: str,
    *,
    summary: str = "成果摘要",
    artifact_version: int = 1,
    created_at: datetime | None = None,
    input_revision: int = 1,
    body_text: object = _UNSET,
    body_ref: str | None = None,
    body_ref_text: str | None = None,
    published_at: object = _UNSET,
    cleaned_at: datetime | None = None,
) -> None:
    """种一个成果行；``body_ref_text`` 给出时同事务写 ``agent_body_payload``。

    ``body_text``/``published_at`` 用 ``_UNSET`` 区分「取默认值」与显式 ``None``
    （互斥正文 / 未公开原件）；``artifact_version`` 支持同 ID 多版本（复合 PK）。
    """

    moment = created_at or datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "user_scope": USER_SCOPE,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "session_id": session_id,
        "execution_id": execution_id,
        "round_id": round_id,
        "artifact_kind": "plan",
        "input_revision": input_revision,
        "actual_input_digest": "b" * 64,
        "actual_model_ref": "model/main",
        "result_digest": "c" * 64,
        "source_available_state": "unknown",
        "published_at": moment if published_at is _UNSET else published_at,
        "created_at": moment,
        "updated_at": moment,
        "summary": summary,
        "body_text": f"成果正文 {artifact_id}" if body_text is _UNSET else body_text,
        "body_ref": body_ref,
        "cleaned_at": cleaned_at,
    }
    async with dao.engine.begin() as conn:
        if body_ref is not None and body_ref_text is not None:
            data = body_ref_text.encode("utf-8")
            # body_ref ↔ agent_body_payload 的双向 FK 为 DEFERRABLE：载荷与成果行
            # 必须同事务写入（分别提交会在提交时被延迟约束拒绝）。
            await conn.execute(
                insert(AgentBodyPayload).values(
                    user_scope=USER_SCOPE,
                    body_ref=body_ref,
                    owner_kind="artifact",
                    owner_id=artifact_id,
                    owner_version=str(artifact_version),
                    artifact_id=artifact_id,
                    artifact_version=artifact_version,
                    encoding="utf-8",
                    block_ordinal=0,
                    payload_bytes=data,
                    byte_length=len(data),
                    payload_digest=sha256_hex(data),
                )
            )
        await conn.execute(insert(AgentArtifact).values(**values))


def _code_of(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> AgentErrorCode:
    assert isinstance(excinfo.value, AgentServiceError)
    return excinfo.value.code


def _q(**overrides: object) -> HistoryQuery:
    values: dict[str, object] = {
        "scope": "all_saved",
        "round_selection": HistoryRoundSelection(kind="all_rounds"),
        "query_text": "",
        "time_basis": "created",
        "sort": "newest_first",
    }
    values.update(overrides)
    return HistoryQuery(**values)  # pyright: ignore[reportArgumentType]


class TestScopeScanning:
    async def test_all_saved_scans_user_domain(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_message(dao, "s-a", "a1", body_text="会话A消息")
        await _seed_message(dao, "s-b", "b1", body_text="会话B消息")
        await _seed_execution(dao, "exec-b", "s-b")
        await _seed_round(dao, "round-b", "exec-b")
        await _seed_artifact(dao, "art-b", "s-b", "exec-b", "round-b")

        page = await dao.search_history(_q(), user_scope=USER_SCOPE)

        assert len(page.hits) == 3
        assert {hit.session_id for hit in page.hits} == {"s-a", "s-b"}
        # newest_first：sort_moment 单调不增（消息 created_at 为落库时刻，不依赖跨表绝对序）
        moments = [hit.sort_moment for hit in page.hits]
        assert moments == sorted(moments, reverse=True)
        artifact_hit = next(hit for hit in page.hits if hit.hit_kind == "artifact")
        assert artifact_hit.artifact_id == "art-b"
        assert artifact_hit.summary_text == "成果摘要"
        # digest/指纹/上界
        assert page.query_digest == compute_history_query_digest(_q())
        assert len(page.revision_fingerprint) == 64
        assert page.revision_fingerprint != "0" * 64
        assert page.fixed_upper_bound.endswith("Z")
        assert page.next_cursor is None

    async def test_user_scope_isolation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "a1", body_text="会话A消息")
        page = await dao.search_history(_q(), user_scope=OTHER_SCOPE)
        assert page.hits == ()

    async def test_current_session_limits_scope(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_message(dao, "s-a", "a1", body_text="A1")
        await _seed_message(dao, "s-b", "b1", body_text="B1")
        page = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-a1"]

    async def test_current_session_missing_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(_q(scope="current_session", session_id="no-such"), user_scope=USER_SCOPE)
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_current_child_limits_and_rejects_mismatch(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-root", "s-a")
        await _seed_execution(dao, "exec-leaf", "s-a", node_id="leaf", parent_execution_id="exec-root")
        await _seed_round(dao, "round-leaf", "exec-leaf")
        await _seed_message(dao, "s-a", "root1", body_text="根执行消息")
        await _seed_message(
            dao, "s-a", "leaf1", execution_id="exec-leaf", round_id="round-leaf", body_text="叶消息", node_id="leaf"
        )
        await _seed_artifact(dao, "art-leaf", "s-a", "exec-leaf", "round-leaf")

        target = HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf")
        page = await dao.search_history(
            _q(scope="current_child", session_id="s-a", child_target=target), user_scope=USER_SCOPE
        )
        assert {hit.message_id or hit.artifact_id for hit in page.hits} == {"m-leaf1", "art-leaf"}

        wrong_target = HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="else")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_child", session_id="s-a", child_target=wrong_target), user_scope=USER_SCOPE
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestFilters:
    async def test_query_text_like_placeholder(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "hit1", body_text="包含回撤关键词的消息")
        await _seed_message(dao, "s-a", "miss1", body_text="无关消息")
        page = await dao.search_history(_q(query_text="回撤"), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-hit1"]
        empty = await dao.search_history(_q(query_text="不存在的词"), user_scope=USER_SCOPE)
        assert empty.hits == ()
        assert empty.next_cursor is None

    async def test_message_role_filter_applies_to_messages_only(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "u1", role=MessageRole.USER, body_text="用户消息")
        await _seed_message(dao, "s-a", "as1", role=MessageRole.ASSISTANT, body_text="助手消息")
        await _seed_artifact(dao, "art-a", "s-a", "exec-a", "round-a")
        page = await dao.search_history(
            _q(scope="current_session", session_id="s-a", message_role_filter="user"), user_scope=USER_SCOPE
        )
        # message_role_filter 是消息维度：消息按 role 过滤，成果不受排除（独立维度，L408）
        message_hits = [hit for hit in page.hits if hit.hit_kind == "message"]
        assert [hit.message_id for hit in message_hits] == ["m-u1"]
        assert any(hit.hit_kind == "artifact" and hit.artifact_id == "art-a" for hit in page.hits)

    async def test_type_filter_follows_authoritative_execution_kind_matrix(self, real_embedded_pg) -> None:
        """检视 D17：main/child 按执行权威类型，消息与成果同口径。

        矩阵：root / leaf / comparison_lane × 消息 / 成果 × main / child。
        main = root 执行 + 会话级消息（无 execution_id）；child = leaf 与
        comparison_lane；成果 execution_id 为 NOT NULL 也不得在 main 下消失。
        """

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-root", "s-a", node_id="root")
        await _seed_execution(dao, "exec-leaf", "s-a", node_id="leaf", parent_execution_id="exec-root")
        await _seed_execution(
            dao, "exec-lane", "s-a", node_id="lane", parent_execution_id="exec-root", execution_kind="comparison_lane"
        )
        await _seed_round(dao, "round-root", "exec-root")
        await _seed_round(dao, "round-leaf", "exec-leaf")
        await _seed_round(dao, "round-lane", "exec-lane")
        await _seed_message(dao, "s-a", "session", body_text="会话级")
        await _seed_message(dao, "s-a", "root", execution_id="exec-root", round_id="round-root", body_text="根执行")
        await _seed_message(
            dao, "s-a", "leaf", execution_id="exec-leaf", round_id="round-leaf", node_id="leaf", body_text="叶执行"
        )
        await _seed_message(
            dao, "s-a", "lane", execution_id="exec-lane", round_id="round-lane", node_id="lane", body_text="比较路"
        )
        await _seed_artifact(dao, "art-root", "s-a", "exec-root", "round-root")
        await _seed_artifact(dao, "art-leaf", "s-a", "exec-leaf", "round-leaf")
        await _seed_artifact(dao, "art-lane", "s-a", "exec-lane", "round-lane")

        page_main = await dao.search_history(
            _q(scope="current_session", session_id="s-a", type_filter="main"), user_scope=USER_SCOPE
        )
        assert {hit.message_id for hit in page_main.hits if hit.hit_kind == "message"} == {"m-session", "m-root"}
        assert {hit.artifact_id for hit in page_main.hits if hit.hit_kind == "artifact"} == {"art-root"}

        page_child = await dao.search_history(
            _q(scope="current_session", session_id="s-a", type_filter="child"), user_scope=USER_SCOPE
        )
        assert {hit.message_id for hit in page_child.hits if hit.hit_kind == "message"} == {"m-leaf", "m-lane"}
        assert {hit.artifact_id for hit in page_child.hits if hit.hit_kind == "artifact"} == {"art-leaf", "art-lane"}

    async def test_role_identity_filters_messages_only_artifacts_stay_visible(self, real_embedded_pg) -> None:
        """检视 D17：成果无权威「产生时角色」，不得静默排空（literal(False) 已移除）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-root", "s-a", node_id="root")
        await _seed_execution(dao, "exec-leaf", "s-a", node_id="leaf", parent_execution_id="exec-root")
        await _seed_round(dao, "round-root", "exec-root")
        await _seed_round(dao, "round-leaf", "exec-leaf")
        await _seed_message(
            dao,
            "s-a",
            "with-role",
            execution_id="exec-root",
            round_id="round-root",
            role_id="role-1",
            role_version="1",
            body_text="带角色身份",
        )
        await _seed_message(
            dao, "s-a", "no-role", execution_id="exec-leaf", round_id="round-leaf", node_id="leaf", body_text="无角色"
        )
        await _seed_artifact(dao, "art-leaf", "s-a", "exec-leaf", "round-leaf")

        role_filter = HistoryAgentFilter(kind="role_identity", role_id="role-1", version_selection="all_versions")
        # 命中：消息按 role_id 过滤，成果保留（角色维度只约束消息）
        page = await dao.search_history(
            _q(scope="current_session", session_id="s-a", agent_filter=role_filter), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page.hits if hit.hit_kind == "message"] == ["m-with-role"]
        assert [hit.artifact_id for hit in page.hits if hit.hit_kind == "artifact"] == ["art-leaf"]
        # 不命中：无同角色消息，但成果不得被排空
        page_child = await dao.search_history(
            _q(scope="current_session", session_id="s-a", type_filter="child", agent_filter=role_filter),
            user_scope=USER_SCOPE,
        )
        assert [hit for hit in page_child.hits if hit.hit_kind == "message"] == []
        assert [hit.artifact_id for hit in page_child.hits if hit.hit_kind == "artifact"] == ["art-leaf"]

    async def test_round_selection_exact(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a", number=1)
        await _seed_message(dao, "s-a", "r1", execution_id="exec-a", round_id="round-a", body_text="第一轮")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a"),
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-r1"]

    async def test_agent_filter_execution_agent(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a", node_id="root")
        await _seed_execution(dao, "exec-b", "s-a", node_id="leaf", parent_execution_id="exec-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "ma", execution_id="exec-a", round_id="round-a", body_text="A")
        await _seed_message(dao, "s-a", "mb", body_text="B")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                agent_filter=HistoryAgentFilter(
                    kind="execution_agent", execution_id="exec-a", node_id="root", round_id="round-a"
                ),
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-ma"]

    async def test_date_window_on_time_basis_column(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "d1", body_text="六月", occurred_at="2026-06-01T12:00:00.000000Z")
        await _seed_message(dao, "s-a", "d2", body_text="七月", occurred_at="2026-07-01T12:00:00.000000Z")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                time_basis="message",
                date_from="2026-06-01",
                date_to="2026-06-30",
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-d1"]


class TestExactTargetAttribution:
    """精确目标归属（R2-I28）：会话↔执行↔轮次须完整归属于目标，错配显式拒绝。

    缺口：exact 分支只核 round 属于 execution 且在用户域，执行属于同用户其他
    会话时会被会话过滤成空页，掩盖调用方定位错误。合法但无命中才允许空页。
    """

    async def test_exact_round_cross_session_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_execution(dao, "exec-b", "s-b")
        await _seed_round(dao, "round-b", "exec-b")
        await _seed_message(dao, "s-b", "b1", execution_id="exec-b", round_id="round-b", body_text="他会话消息")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(
                    scope="current_session",
                    session_id="s-a",
                    round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-b", round_id="round-b"),
                ),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_exact_round_of_other_execution_rejected(self, real_embedded_pg) -> None:
        """轮次属于同会话的另一执行：组合不合法，不得静默空页。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_execution(dao, "exec-l", "s-a", node_id="leaf", parent_execution_id="exec-a")
        await _seed_round(dao, "round-l", "exec-l")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(
                    scope="current_session",
                    session_id="s-a",
                    round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-l"),
                ),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_exact_round_all_saved_allows_any_session(self, real_embedded_pg) -> None:
        """all_saved 无会话约束：其他会话的合法精确目标仍可检索（不误拒）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_execution(dao, "exec-b", "s-b")
        await _seed_round(dao, "round-b", "exec-b")
        await _seed_message(dao, "s-b", "b1", execution_id="exec-b", round_id="round-b", body_text="他会话消息")
        page = await dao.search_history(
            _q(round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-b", round_id="round-b")),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-b1"]

    async def test_exact_round_valid_without_hits_returns_empty_page(self, real_embedded_pg) -> None:
        """归属合法的目标无命中才是空页（不把空结果与错配混为一谈）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a"),
            ),
            user_scope=USER_SCOPE,
        )
        assert page.hits == ()
        assert page.next_cursor is None

    async def test_exact_round_archived_session_still_searchable(self, real_embedded_pg) -> None:
        """归档只影响目录视图，不改变历史归属；精确目标仍按身份检索。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a", archived=True)
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "a1", execution_id="exec-a", round_id="round-a", body_text="归档会话")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a"),
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-a1"]

    async def test_exact_round_cursor_continuation_unchanged(self, real_embedded_pg) -> None:
        """精确目标的游标续读语义不变（同查询翻页仍可用）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        for index in range(1, 3):
            await _seed_message(
                dao, "s-a", f"p{index}", execution_id="exec-a", round_id="round-a", body_text=f"消息{index}"
            )
        query = _q(
            scope="current_session",
            session_id="s-a",
            round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a"),
            limit=1,
        )
        page1 = await dao.search_history(query, user_scope=USER_SCOPE)
        assert len(page1.hits) == 1
        assert page1.next_cursor is not None
        page2 = await dao.search_history(replace(query, cursor=page1.next_cursor), user_scope=USER_SCOPE)
        assert len(page2.hits) == 1
        assert page2.hits[0].message_id != page1.hits[0].message_id

    async def test_agent_filter_cross_session_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_execution(dao, "exec-b", "s-b")
        await _seed_round(dao, "round-b", "exec-b")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(
                    scope="current_session",
                    session_id="s-a",
                    agent_filter=HistoryAgentFilter(
                        kind="execution_agent", execution_id="exec-b", node_id="root", round_id="round-b"
                    ),
                ),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_agent_filter_round_of_other_execution_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_execution(dao, "exec-b", "s-a")
        await _seed_round(dao, "round-b", "exec-b")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(
                    scope="current_session",
                    session_id="s-a",
                    agent_filter=HistoryAgentFilter(
                        kind="execution_agent", execution_id="exec-a", node_id="root", round_id="round-b"
                    ),
                ),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_agent_filter_conflicts_with_exact_round_rejected(self, real_embedded_pg) -> None:
        """两个显式精确目标指向不同执行：交集必空，须拒绝而非空页。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_execution(dao, "exec-b", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_round(dao, "round-b", "exec-b")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(
                    scope="current_session",
                    session_id="s-a",
                    round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a"),
                    agent_filter=HistoryAgentFilter(
                        kind="execution_agent", execution_id="exec-b", node_id="root", round_id="round-b"
                    ),
                ),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_agent_filter_conflicts_with_child_target_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-root", "s-a")
        await _seed_execution(dao, "exec-leaf", "s-a", node_id="leaf", parent_execution_id="exec-root")
        await _seed_round(dao, "round-root", "exec-root")
        target = HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(
                    scope="current_child",
                    session_id="s-a",
                    child_target=target,
                    agent_filter=HistoryAgentFilter(
                        kind="execution_agent", execution_id="exec-root", node_id="root", round_id="round-root"
                    ),
                ),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestDateBoundsAndStateFilter:
    async def test_date_from_only_applies_lower_bound(self, real_embedded_pg) -> None:
        """单边 date_from 仍须生效（检视 M-2：单侧 None 不丢弃过滤）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "early", body_text="六月", occurred_at="2026-06-01T12:00:00.000000Z")
        await _seed_message(dao, "s-a", "late", body_text="七月", occurred_at="2026-07-01T12:00:00.000000Z")
        page = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", date_from="2026-06-15"),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-late"]

    async def test_date_to_only_applies_upper_bound(self, real_embedded_pg) -> None:
        """单边 date_to 仍须生效：含当天（to+1d 半开上界）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "early", body_text="六月", occurred_at="2026-06-01T12:00:00.000000Z")
        await _seed_message(dao, "s-a", "late", body_text="七月", occurred_at="2026-07-01T12:00:00.000000Z")
        page = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", date_to="2026-06-01"),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-early"]

    async def test_invalid_date_value_rejected_not_bare_error(self, real_embedded_pg) -> None:
        """正则语法冻结不核取值：2026-13-40 过语法但 strptime 失败 → INPUT_INVALID（检视 m-1）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", date_from="2026-13-40"), user_scope=USER_SCOPE
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_invalid_cursor_date_value_rejected_not_bare_error(self, real_embedded_pg) -> None:
        """历史游标非法日期取值 → INPUT_INVALID（裸 ValueError 不得逃逸，检视 m-1）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        bad_cursor = f"h:{'a' * 64}:9999-99-99T12:00:00.000000Z:9999-99-99T12:00:00.000000Z:message:anchor:0"
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", cursor=bad_cursor), user_scope=USER_SCOPE
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_state_filter_limits_to_execution_display_state(self, real_embedded_pg) -> None:
        """state_filter 作用于 execution.display_state（零覆盖锚：检视复核点）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_execution(dao, "exec-b", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_round(dao, "round-b", "exec-b")
        await _seed_message(dao, "s-a", "qa", execution_id="exec-a", round_id="round-a", body_text="排队中")
        await _seed_message(dao, "s-a", "rb", execution_id="exec-b", round_id="round-b", body_text="运行中")
        async with dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_execution SET display_state = 'running' WHERE execution_id = :e"),
                {"e": "exec-b"},
            )
        page = await dao.search_history(
            _q(scope="current_session", session_id="s-a", state_filter="running"), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page.hits] == ["m-rb"]
        # 无匹配 state → 空页（不静默放宽）
        empty = await dao.search_history(
            _q(scope="current_session", session_id="s-a", state_filter="no-such-state"), user_scope=USER_SCOPE
        )
        assert empty.hits == ()


class TestPagingAndFingerprint:
    async def test_newest_first_cursor_continuation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 4):
            await _seed_message(
                dao,
                "s-a",
                f"p{index}",
                body_text=f"消息{index}",
                occurred_at=f"2026-06-0{index}T12:00:00.000000Z",
            )
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=2), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page1.hits] == ["m-p3", "m-p2"]
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        page2 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", limit=2, cursor=cursor_value),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page2.hits] == ["m-p1"]
        assert page2.next_cursor is None
        # 翻页共享同一 query_digest（limit/游标不入摘要）
        assert page2.query_digest == page1.query_digest
        # 固定上界延续首读值
        assert page2.fixed_upper_bound == page1.fixed_upper_bound

    async def test_oldest_first_cursor_continuation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 4):
            await _seed_message(
                dao,
                "s-a",
                f"o{index}",
                body_text=f"消息{index}",
                occurred_at=f"2026-06-0{index}T12:00:00.000000Z",
            )
        page1 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", sort="oldest_first", limit=2),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page1.hits] == ["m-o1", "m-o2"]
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        page2 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", sort="oldest_first", limit=2, cursor=cursor_value),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page2.hits] == ["m-o3"]

    async def test_fingerprint_changes_with_watermark(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        await _seed_message(dao, "s-a", "f1", body_text="新消息")
        page2 = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        assert page1.revision_fingerprint != page2.revision_fingerprint

    async def test_cursor_reuse_conflict_when_target_removed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        for index in range(1, 4):
            await _seed_message(
                dao, "s-a", f"c{index}", execution_id="exec-a", round_id="round-a", body_text=f"消息{index}"
            )
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=2), user_scope=USER_SCOPE)
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        # 覆盖域成员消失（模拟清理删行，按 FK 依赖序）→ 翻页 REVISION_CONFLICT
        async with dao.engine.begin() as conn:
            await conn.execute(text("DELETE FROM agent_message WHERE user_scope = :u"), {"u": USER_SCOPE})
            await conn.execute(text("DELETE FROM agent_artifact WHERE user_scope = :u"), {"u": USER_SCOPE})
            await conn.execute(text("DELETE FROM agent_round WHERE user_scope = :u"), {"u": USER_SCOPE})
            await conn.execute(text("DELETE FROM agent_execution WHERE user_scope = :u"), {"u": USER_SCOPE})
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", limit=2, cursor=cursor_value),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT

    async def test_summary_text_truncated_not_full_body(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        long_body = "长" * 500
        await _seed_message(dao, "s-a", "long1", body_text=long_body)
        page = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        summary = page.hits[0].summary_text or ""
        assert 0 < len(summary) <= 200
        assert len(summary) < len(long_body)


class TestFullOrderKeysetPaging:
    """检视 D19：全序键 (sort_moment, hit_kind, anchor_id, artifact_version)。

    同 ID 多版本同 ``sort_moment`` 时，``limit`` 翻页必须覆盖全部版本且不重复；
    消息与成果同刻混排顺序稳定可复现；锚行按完整键唯一命中。
    """

    async def _seed_exec_tree(self, dao: AgentDao) -> None:
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")

    async def test_multi_version_same_moment_pages_cover_all_versions(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await self._seed_exec_tree(dao)
        same_moment = datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
        for version in (1, 2, 3):
            await _seed_artifact(
                dao,
                "art-multi",
                "s-a",
                "exec-a",
                "round-a",
                created_at=same_moment,
                artifact_version=version,
            )

        seen: list[tuple[str, int | None]] = []
        cursor: str | None = None
        for _ in range(10):
            page = await dao.search_history(
                _q(scope="current_session", session_id="s-a", limit=1, cursor=cursor), user_scope=USER_SCOPE
            )
            seen.extend((hit.artifact_id or "", hit.artifact_version) for hit in page.hits)
            cursor = page.next_cursor
            if cursor is None:
                break

        # 三个版本全部覆盖、无重复，newest_first 按版本降序
        assert seen == [("art-multi", 3), ("art-multi", 2), ("art-multi", 1)]

    async def test_message_and_artifact_same_moment_stable_full_order(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await self._seed_exec_tree(dao)
        await _seed_message(
            dao,
            "s-a",
            "same-moment",
            execution_id="exec-a",
            round_id="round-a",
            body_text="同刻消息",
            occurred_at="2026-06-02T08:00:00.000000Z",
        )
        await _seed_artifact(
            dao, "art-same", "s-a", "exec-a", "round-a", created_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
        )

        newest = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", limit=10), user_scope=USER_SCOPE
        )
        # newest_first：同刻 hit_kind DESC（message 先于 artifact），可复现
        assert [hit.hit_kind for hit in newest.hits] == ["message", "artifact"]

        oldest = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", sort="oldest_first", limit=10),
            user_scope=USER_SCOPE,
        )
        assert [hit.hit_kind for hit in oldest.hits] == ["artifact", "message"]

        # limit=1 逐页推进不重不漏
        first = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", limit=1), user_scope=USER_SCOPE
        )
        second = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", limit=1, cursor=first.next_cursor),
            user_scope=USER_SCOPE,
        )
        assert [first.hits[0].hit_kind, second.hits[0].hit_kind] == ["message", "artifact"]
        assert second.next_cursor is None

    async def test_artifact_anchor_must_match_version(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await self._seed_exec_tree(dao)
        same_moment = datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
        for version in (1, 2):
            await _seed_artifact(
                dao, "art-multi", "s-a", "exec-a", "round-a", created_at=same_moment, artifact_version=version
            )

        first = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=1), user_scope=USER_SCOPE)
        digest, upper, sort_moment, _kind, _anchor, _version = parse_history_cursor(first.next_cursor or "")
        # 同锚 ID 但版本不存在：锚行不唯一命中即覆盖域已修订（不放行错位游标）
        bad_cursor = f"h:{digest}:{upper}:{sort_moment}:artifact:art-multi:99"
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", limit=1, cursor=bad_cursor), user_scope=USER_SCOPE
            )
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT


class TestCursorQueryBinding:
    """检视 D16：游标绑定规范查询身份，换筛选/换范围复用必须被拒。"""

    async def _paged_cursor(self, dao: AgentDao) -> tuple[str, str]:
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        for index in range(1, 3):
            await _seed_message(
                dao, "s-a", f"x{index}", execution_id="exec-a", round_id="round-a", body_text=f"消息{index}"
            )
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=1), user_scope=USER_SCOPE)
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        return cursor_value, page1.query_digest

    async def test_cursor_carries_query_digest(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        cursor_value, query_digest = await self._paged_cursor(dao)
        assert parse_history_cursor(cursor_value)[0] == query_digest

    async def test_changed_filter_with_same_cursor_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        cursor_value, _ = await self._paged_cursor(dao)
        variants: dict[str, dict[str, object]] = {
            "round_selection": {
                "round_selection": HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a")
            },
            "query_text": {"query_text": "消息"},
            "type_filter": {"type_filter": "child"},
            "state_filter": {"state_filter": "queued"},
            "message_role_filter": {"message_role_filter": "user"},
        }
        for label, overrides in variants.items():
            with pytest.raises(AgentServiceError) as exc:
                await dao.search_history(
                    _q(scope="current_session", session_id="s-a", limit=1, cursor=cursor_value, **overrides),
                    user_scope=USER_SCOPE,
                )
            assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT, label

    async def test_changed_scope_with_same_cursor_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 3):
            await _seed_message(dao, "s-a", f"y{index}", body_text=f"消息{index}")
        page1 = await dao.search_history(_q(limit=1), user_scope=USER_SCOPE)
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", limit=1, cursor=cursor_value), user_scope=USER_SCOPE
            )
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT


class TestCursorUpperBoundAndLiveness:
    """检视 D16：固定上界从游标继承；成员变化只经指纹漂移，不阻断同查询续翻。"""

    async def test_cursor_inherits_fixed_upper_bound(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 4):
            await _seed_message(
                dao,
                "s-a",
                f"u{index}",
                body_text=f"消息{index}",
                occurred_at=f"2026-06-0{index}T12:00:00.000000Z",
            )
        query = _q(scope="current_session", session_id="s-a", time_basis="message", sort="oldest_first", limit=2)
        page1 = await dao.search_history(query, user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page1.hits] == ["m-u1", "m-u2"]
        cursor_value = page1.next_cursor or ""
        digest, _upper, sort_moment, hit_kind, anchor, version = parse_history_cursor(cursor_value)
        early_upper = "2026-06-02T12:00:00.000000Z"
        narrowed_cursor = f"h:{digest}:{early_upper}:{sort_moment}:{hit_kind}:{anchor}:{version}"
        # 续读前新增更晚数据；游标上界不让位于「当前时刻」
        await _seed_message(dao, "s-a", "u4", body_text="消息4", occurred_at="2026-06-04T12:00:00.000000Z")
        page2 = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                time_basis="message",
                sort="oldest_first",
                limit=2,
                cursor=narrowed_cursor,
            ),
            user_scope=USER_SCOPE,
        )
        assert page2.fixed_upper_bound == early_upper
        assert page2.hits == ()
        # 新首读（上界=now）可见被续读上界挡住的晚数据：证明是上界继承而非数据缺失
        fresh = await dao.search_history(
            _q(scope="current_session", session_id="s-a", time_basis="message", sort="oldest_first", limit=10),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in fresh.hits] == ["m-u1", "m-u2", "m-u3", "m-u4"]

    async def test_same_query_continuation_survives_member_change(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 4):
            await _seed_message(
                dao,
                "s-a",
                f"v{index}",
                body_text=f"消息{index}",
                occurred_at=f"2026-06-0{index}T12:00:00.000000Z",
            )
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=2), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page1.hits] == ["m-v3", "m-v2"]
        cursor_value = page1.next_cursor or ""
        # 翻页间成员变化（同会话新增消息）：指纹漂移，但同查询续翻照常推进
        await _seed_message(dao, "s-a", "v4", body_text="翻页间新增", occurred_at="2026-06-04T12:00:00.000000Z")
        page2 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", limit=2, cursor=cursor_value), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page2.hits] == ["m-v1"]
        assert page2.next_cursor is None
        assert page2.revision_fingerprint != page1.revision_fingerprint


class TestAllSavedExecutionFingerprint:
    """检视 D16：all_saved 指纹纳入 execution 成员，执行内更新不再漏出。"""

    async def _seed_tree(self, dao: AgentDao) -> None:
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "e1", execution_id="exec-a", round_id="round-a", body_text="执行内消息")

    async def test_all_saved_fingerprint_tracks_execution_revision(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await self._seed_tree(dao)
        page1 = await dao.search_history(_q(), user_scope=USER_SCOPE)
        # 仅推进 execution 水位（session 行不动）：session 粒度指纹看不到该变化
        async with dao.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_execution SET revision = revision + 1, event_seq = event_seq + 1 WHERE user_scope = :u"
                ),
                {"u": USER_SCOPE},
            )
        page2 = await dao.search_history(_q(), user_scope=USER_SCOPE)
        assert page1.revision_fingerprint != page2.revision_fingerprint

    async def test_all_saved_fingerprint_tracks_execution_membership(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await self._seed_tree(dao)
        page1 = await dao.search_history(_q(), user_scope=USER_SCOPE)
        await _seed_execution(dao, "exec-b", "s-a")
        page2 = await dao.search_history(_q(), user_scope=USER_SCOPE)
        assert page1.revision_fingerprint != page2.revision_fingerprint

    async def test_current_session_fingerprint_still_tracks_execution(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await self._seed_tree(dao)
        query = _q(scope="current_session", session_id="s-a")
        page1 = await dao.search_history(query, user_scope=USER_SCOPE)
        async with dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_execution SET revision = revision + 1 WHERE user_scope = :u"), {"u": USER_SCOPE}
            )
        page2 = await dao.search_history(query, user_scope=USER_SCOPE)
        assert page1.revision_fingerprint != page2.revision_fingerprint


class TestTitleAndPublicBodySearch:
    """检视 D18：会话标题与完整公开表示（body_ref 载荷）可搜。

    中文标题按 ILIKE 子串命中（不依赖分词）；已清理会话/成果与未公开成果显式
    排除；公开消息缺 ``search_text`` 投影时明确失败而不是返回准确空页；关键词
    分支不得覆盖既有 type/role 过滤与游标 keyset。
    """

    async def test_chinese_session_title_makes_session_hits_visible(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-title", title="新能源回撤复盘")
        await _seed_session(dao, "s-other", title="无关会话")
        await _seed_message(dao, "s-title", "t1", body_text="正文不含关键词")
        await _seed_message(dao, "s-other", "o1", body_text="正文不含关键词")

        page = await dao.search_history(_q(query_text="新能源"), user_scope=USER_SCOPE)

        # 标题命中的会话在范围内的消息如实可见，命中身份仍是既有 message
        assert [hit.message_id for hit in page.hits] == ["m-t1"]
        assert page.hits[0].hit_kind == "message"

    async def test_cleaned_session_title_is_not_searchable(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-cleaned", title="将被清理的会话")
        await _seed_message(dao, "s-cleaned", "c1", body_text="正文不含关键词")
        async with dao.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_session SET title = :title, cleaned_at = now() "
                    "WHERE user_scope = :u AND session_id = 's-cleaned'"
                ),
                {"title": CLEANUP_TOMBSTONE, "u": USER_SCOPE},
            )

        page = await dao.search_history(_q(query_text=CLEANUP_TOMBSTONE), user_scope=USER_SCOPE)

        # 清理把标题改写为 tombstone：不得经标题重新暴露已清理会话
        assert page.hits == ()

    async def test_artifact_body_ref_tail_term_searchable(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_artifact(
            dao,
            "art-body",
            "s-a",
            "exec-a",
            "round-a",
            summary="摘要不含关键词",
            body_text=None,
            body_ref="ref-art-body",
            body_ref_text="开头叙述……尾部是阶段性结论词",
        )

        page = await dao.search_history(_q(query_text="阶段性结论词"), user_scope=USER_SCOPE)

        assert [hit.artifact_id for hit in page.hits] == ["art-body"]
        assert page.hits[0].hit_kind == "artifact"

    async def test_unpublished_and_cleaned_artifacts_excluded_from_keyword_hits(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        moment = datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
        await _seed_artifact(dao, "art-pub", "s-a", "exec-a", "round-a", body_text="命中词正文")
        await _seed_artifact(dao, "art-unpub", "s-a", "exec-a", "round-a", body_text="命中词正文", published_at=None)
        await _seed_artifact(
            dao,
            "art-cleaned",
            "s-a",
            "exec-a",
            "round-a",
            body_text="命中词正文",
            cleaned_at=moment,
        )

        page = await dao.search_history(_q(query_text="命中词"), user_scope=USER_SCOPE)

        # 未公开与已清理原件显式排除：只剩已公开且未清理的成果
        assert [hit.artifact_id for hit in page.hits] == ["art-pub"]

    async def test_missing_search_projection_fails_instead_of_empty_page(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        # 公开消息已有正文却缺公开文本投影（search_text=None）：搜索会漏结果
        await _seed_message(dao, "s-a", "noproj", body_text="缺投影的公开正文", search_text=None)

        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", query_text="缺投影"), user_scope=USER_SCOPE
            )

        assert _code_of(exc) == AgentErrorCode.OPERATION_UNKNOWN

    async def test_query_text_keeps_type_filter_and_cursor(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-root", "s-a", node_id="root")
        await _seed_execution(dao, "exec-leaf", "s-a", node_id="leaf", parent_execution_id="exec-root")
        await _seed_round(dao, "round-leaf", "exec-leaf")
        await _seed_message(dao, "s-a", "session-kw", body_text="关键词甲")
        await _seed_message(
            dao,
            "s-a",
            "leaf1",
            execution_id="exec-leaf",
            round_id="round-leaf",
            node_id="leaf",
            body_text="关键词乙",
            occurred_at="2026-06-01T12:00:01.000000Z",
        )
        await _seed_message(
            dao,
            "s-a",
            "leaf2",
            execution_id="exec-leaf",
            round_id="round-leaf",
            node_id="leaf",
            body_text="关键词丙",
            occurred_at="2026-06-01T12:00:02.000000Z",
        )

        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                query_text="关键词",
                time_basis="message",
                type_filter="child",
            ),
            user_scope=USER_SCOPE,
        )
        assert {hit.message_id for hit in page.hits} == {"m-leaf1", "m-leaf2"}

        first = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                query_text="关键词",
                time_basis="message",
                type_filter="child",
                limit=1,
            ),
            user_scope=USER_SCOPE,
        )
        cursor_value = first.next_cursor or ""
        assert cursor_value.startswith("h:")
        second = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                query_text="关键词",
                time_basis="message",
                type_filter="child",
                limit=1,
                cursor=cursor_value,
            ),
            user_scope=USER_SCOPE,
        )
        assert len(first.hits) == 1 and len(second.hits) == 1
        assert {first.hits[0].message_id, second.hits[0].message_id} == {"m-leaf1", "m-leaf2"}
        assert second.next_cursor is None


class TestI84ExternalBodyTailAndPagingLiveness:
    """检视 I84：大正文/外置成果的末段命中，以及追加与修订下的分页活性。

    - 消息公开正文外置（``body_text`` 为 NULL、``body_ref`` 指向载荷）时，
      ``search_text`` 是完整公开正文投影，中英末段词都必须命中；
    - 成果 ``body_ref`` 载荷的末段词可搜，同时 ``published_at IS NULL`` 的同内容
      成果不得被伪装成可搜；
    - 翻页中途追加更晚消息并修订既有成果：旧游标续读受 ``fixed_upper`` 冻结，
      追加消息不挤入后续页，但新查询可见；分页有限推进（末页游标为空）、
      已见 ID 不重不漏；
    - 外置正文缺 ``search_text`` 投影时关键词搜索显式失败，而不是静默漏搜。
    """

    async def test_externalized_message_tail_terms_searchable_by_cn_and_en(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        full_text = "公开正文开始。" + "长" * 60000 + "外置消息尾段中文标记甲 MESSAGETAILTOKENEN"
        message_id = await _seed_external_message(dao, "s-a", "ext-big", body_ref="ref-ext-big", full_text=full_text)

        # 外置形态：正文不在 agent_message.body_text，投影为完整公开正文
        async with dao.engine.begin() as conn:
            row = (
                await conn.execute(
                    select(AgentMessage.body_text, AgentMessage.body_ref, AgentMessage.search_text).where(
                        AgentMessage.user_scope == USER_SCOPE,
                        AgentMessage.message_id == message_id,
                    )
                )
            ).first()
        assert row is not None
        assert row.body_text is None
        assert row.body_ref == "ref-ext-big"
        assert len(row.search_text or "") == len(full_text)

        page_cn = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="尾段中文标记甲"), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page_cn.hits] == [message_id]
        page_en = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="MESSAGETAILTOKENEN"), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page_en.hits] == [message_id]

    async def test_artifact_body_ref_tail_terms_and_unpublished_excluded(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        full_text = "成果公开正文开始。" + "述" * 60000 + "外置成果尾段中文标记乙 ARTIFACTTAILTOKENEN"
        await _seed_artifact(
            dao,
            "art-pub-big",
            "s-a",
            "exec-a",
            "round-a",
            body_text=None,
            body_ref="ref-art-pub-big",
            body_ref_text=full_text,
        )
        # 同内容但未公开：不得因「恰好搜不到」而被视为可搜，也不得命中
        await _seed_artifact(
            dao,
            "art-unpub-big",
            "s-a",
            "exec-a",
            "round-a",
            body_text=None,
            body_ref="ref-art-unpub-big",
            body_ref_text=full_text,
            published_at=None,
        )

        page_cn = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="外置成果尾段中文标记乙"), user_scope=USER_SCOPE
        )
        assert [(hit.hit_kind, hit.artifact_id) for hit in page_cn.hits] == [("artifact", "art-pub-big")]
        page_en = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="ARTIFACTTAILTOKENEN"), user_scope=USER_SCOPE
        )
        assert [(hit.hit_kind, hit.artifact_id) for hit in page_en.hits] == [("artifact", "art-pub-big")]

    async def test_paging_stays_live_under_append_and_revision_fixed_upper(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_artifact(
            dao,
            "art-page",
            "s-a",
            "exec-a",
            "round-a",
            created_at=datetime(2026, 6, 1, 11, 0, 0, tzinfo=UTC),
            summary="原始成果摘要乙 ORIGINALSUMMARYTOKENEN",
        )
        for index in range(1, 8):
            await _seed_message(
                dao, "s-a", f"pg{index}", execution_id="exec-a", round_id="round-a", body_text=f"分页消息{index}"
            )

        # 修订前：降级正文的新词不可命中（证明后续命中来自修订，而非存量噪声）
        before_revision = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="REVISEDSUMMARYTOKENEN"), user_scope=USER_SCOPE
        )
        assert not before_revision.hits

        query = _q(scope="current_session", session_id="s-a", sort="oldest_first", limit=2)
        page1 = await dao.search_history(query, user_scope=USER_SCOPE)
        assert [hit.message_id or hit.artifact_id for hit in page1.hits] == ["art-page", "m-pg1"]
        cursor = page1.next_cursor
        assert cursor is not None

        # 翻页间：追加更晚消息（created_at 晚于首读 fixed_upper）+ 修订既有成果
        upper = _parse_wire(page1.fixed_upper_bound)
        while datetime.now(UTC) <= upper:
            await asyncio.sleep(0.001)
        await _seed_message(dao, "s-a", "late", execution_id="exec-a", round_id="round-a", body_text="追加的更晚消息")
        async with dao.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_artifact SET summary = :summary, updated_at = now(), "
                    "revision = revision + 1 WHERE user_scope = :u AND artifact_id = :a AND artifact_version = 1"
                ),
                {"summary": "修订成果摘要甲 REVISEDSUMMARYTOKENEN", "u": USER_SCOPE, "a": "art-page"},
            )

        seen: list[str] = [hit.message_id or hit.artifact_id or "" for hit in page1.hits]
        pages = 1
        last_page = page1
        while cursor is not None:
            last_page = await dao.search_history(replace(query, cursor=cursor), user_scope=USER_SCOPE)
            # 续读沿用首读上界（固定上界范围内翻页）
            assert last_page.fixed_upper_bound == page1.fixed_upper_bound
            seen.extend(hit.message_id or hit.artifact_id or "" for hit in last_page.hits)
            cursor = last_page.next_cursor
            pages += 1
            assert pages <= 10

        # 有限推进：末页游标为空
        assert last_page.next_cursor is None
        assert pages == 4
        # 已见 ID 不重不漏（固定上界内：1 成果 + 7 条分页消息）
        assert len(seen) == len(set(seen))
        assert set(seen) == {"art-page", *(f"m-pg{index}" for index in range(1, 8))}
        # 追加消息被旧游标上界挡住（oldest_first 续读方向本会纳入更晚数据）
        assert "m-late" not in seen

        # 新查询可见追加消息与修订后的成果摘要：证明是上界冻结，不是数据缺失
        fresh = await dao.search_history(
            _q(scope="current_session", session_id="s-a", sort="oldest_first", limit=50), user_scope=USER_SCOPE
        )
        fresh_message_ids = [hit.message_id for hit in fresh.hits if hit.hit_kind == "message"]
        assert "m-late" in fresh_message_ids
        assert len(fresh_message_ids) == 8
        art_hit = next(hit for hit in fresh.hits if hit.artifact_id == "art-page")
        assert art_hit.summary_text == "修订成果摘要甲 REVISEDSUMMARYTOKENEN"

        # 修订后可命中新内容（中英末段词），且旧正文词不再命中
        revised_cn = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="修订成果摘要甲"), user_scope=USER_SCOPE
        )
        assert [(hit.hit_kind, hit.artifact_id) for hit in revised_cn.hits] == [("artifact", "art-page")]
        revised_en = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="REVISEDSUMMARYTOKENEN"), user_scope=USER_SCOPE
        )
        assert [(hit.hit_kind, hit.artifact_id) for hit in revised_en.hits] == [("artifact", "art-page")]
        stale = await dao.search_history(
            _q(scope="current_session", session_id="s-a", query_text="ORIGINALSUMMARYTOKENEN"), user_scope=USER_SCOPE
        )
        assert not stale.hits

    async def test_external_body_without_search_projection_fails_closed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_external_message(
            dao, "s-a", "ext-noproj", body_ref="ref-ext-noproj", full_text="外置正文缺投影的公开内容", search_text=None
        )

        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", query_text="缺投影"), user_scope=USER_SCOPE
            )

        assert _code_of(exc) == AgentErrorCode.OPERATION_UNKNOWN
