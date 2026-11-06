"""D03：历史窗口、定位、搜索与会话/成果只读视图（真实 PostgreSQL）。

覆盖 §5.7 的只读边界（seq <= 已提交 tail）、同 id 最高可见 revision、
older/newer 双向游标与 anchor 准确 revision；以及会话待决计数、归档过滤、
成果/资源读取的域隔离与删除语义。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentOperationError,
    AgentValidationError,
    HistoryQuery,
    ResourceRef,
    SubmissionKey,
    decode_json_doc,
    request_digest,
    validate_request,
)
from data.persistence.daos.agent_dao import AgentDao
from services.agent.history import AgentHistoryService
from tests.builders.agent import make_request

pytestmark = pytest.mark.integration

_PROMPT_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
_DIGEST = "c" * 64


async def _seed_prompt(engine: AsyncEngine, domain: str) -> None:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_resources (id, domain, kind, current_revision, use_revision)"
            " VALUES ($1, $2, 'prompt', 1, 0) ON CONFLICT (id) DO NOTHING",
            (_PROMPT_ID, domain),
        )
        await conn.exec_driver_sql(
            "INSERT INTO agent_resource_versions (resource_id, revision, payload, content_digest, availability)"
            " VALUES ($1, 1, '{}'::jsonb, $2, 'available') ON CONFLICT (resource_id, revision) DO NOTHING",
            (_PROMPT_ID, _DIGEST),
        )


async def _accept(engine: AsyncEngine, dao: AgentDao, domain: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    request = replace(make_request(), key=SubmissionKey(domain, "tests", f"history-{uuid.uuid4()}"))
    validate_request(request)
    receipt = await dao.accept_request(domain, request, request_digest(request))
    assert receipt.status == "accepted"
    async with engine.connect() as conn:
        conversation_id = (
            await conn.exec_driver_sql("SELECT conversation_id FROM agent_intents WHERE id = $1", (receipt.intent_id,))
        ).scalar_one()
        execution_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_executions WHERE intent_id = $1", (receipt.intent_id,))
        ).scalar_one()
        task_id = (
            await conn.execute(
                sa.text("SELECT id FROM agent_tasks WHERE execution_id = :id"),
                {"id": execution_id},
            )
        ).scalar_one()
    return uuid.UUID(str(conversation_id)), uuid.UUID(str(execution_id)), uuid.UUID(str(task_id))


async def _insert_message(
    engine: AsyncEngine,
    *,
    domain: str,
    conversation_id: uuid.UUID,
    execution_id: uuid.UUID,
    role: str,
    content: str,
    message_id: uuid.UUID | None = None,
    revision: int = 1,
    created_at: datetime | None = None,
) -> uuid.UUID:
    message_id = message_id or uuid.uuid4()
    created = created_at or datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_messages (id, revision, domain, conversation_id, execution_id, role,"
            " content, source_refs, seq, message_created_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, '[]'::jsonb, 0, $8)",
            (message_id, revision, domain, conversation_id, execution_id, role, content, created),
        )
    return message_id


async def test_read_messages_uses_committed_boundary_and_latest_revision(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    conversation_id, execution_id, _ = await _accept(test_engine, dao, domain)

    message_id = await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="assistant",
        content="旧版本",
    )
    await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="assistant",
        content="新版本",
        message_id=message_id,
        revision=2,
    )
    await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="user",
        content="用户消息",
    )

    window = await dao.read_messages(
        domain,
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=None,
        limit=10,
    )
    assert window is not None
    assert len(window.items) == 2
    versions = {(item.message_id, item.content) for item in window.items}
    assert (message_id, "新版本") in versions
    assert (message_id, "旧版本") not in versions
    assert window.read_revision >= 1

    # seq 高于已提交 tail 的 revision 不可见。
    async with test_engine.begin() as conn:
        head_tail = (
            await conn.exec_driver_sql("SELECT tail FROM agent_stream_head WHERE stream_key = 'public'")
        ).scalar_one()
    hidden = uuid.uuid4()
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_messages (id, revision, domain, conversation_id, execution_id, role,"
            " content, source_refs, seq, message_created_at)"
            " VALUES ($1, 1, $2, $3, $4, 'assistant', '未提交', '[]'::jsonb, $5, now())",
            (hidden, domain, conversation_id, execution_id, int(head_tail) + 100),
        )
    after = await dao.read_messages(
        domain,
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=None,
        limit=10,
    )
    assert after is not None
    assert hidden not in {item.message_id for item in after.items}


async def test_message_window_pages_older_and_back_to_newer(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    conversation_id, execution_id, _ = await _accept(test_engine, dao, domain)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ids = []
    for index in range(5):
        ids.append(
            await _insert_message(
                test_engine,
                domain=domain,
                conversation_id=conversation_id,
                execution_id=execution_id,
                role="assistant",
                content=f"message-{index}",
                created_at=start + timedelta(minutes=index),
            )
        )

    page1 = await dao.read_messages(
        domain,
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=None,
        limit=2,
    )
    assert page1 is not None
    assert [item.content for item in page1.items] == ["message-3", "message-4"]
    assert page1.older_cursor is not None
    assert page1.newer_cursor is None

    page2 = await dao.read_messages(
        domain,
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=page1.older_cursor,
        limit=2,
    )
    assert page2 is not None
    assert [item.content for item in page2.items] == ["message-1", "message-2"]
    assert page2.newer_cursor is not None

    page3 = await dao.read_messages(
        domain,
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=page2.older_cursor,
        limit=2,
    )
    assert page3 is not None
    assert [item.content for item in page3.items] == ["message-0"]
    assert page3.older_cursor is None

    newer = await dao.read_messages(
        domain,
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=page2.newer_cursor,
        limit=2,
    )
    assert newer is not None
    assert [item.content for item in newer.items] == ["message-3", "message-4"]
    assert newer.newer_cursor is None


async def test_locate_returns_exact_revision_with_anchor(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    conversation_id, execution_id, _ = await _accept(test_engine, dao, domain)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    target = uuid.uuid4()
    for index in range(3):
        await _insert_message(
            test_engine,
            domain=domain,
            conversation_id=conversation_id,
            execution_id=execution_id,
            role="assistant",
            content=f"neighbor-{index}",
            created_at=start + timedelta(minutes=index),
        )
    await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="assistant",
        content="anchor-v1",
        message_id=target,
        created_at=start + timedelta(minutes=3),
    )
    await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="assistant",
        content="anchor-v2",
        message_id=target,
        revision=2,
        created_at=start + timedelta(minutes=3),
    )

    window = await dao.locate(domain, target, 1)
    assert window is not None
    assert window.anchor_message_id == target
    assert window.anchor_revision == 1
    assert window.items[3].message_id == target
    assert window.items[3].revision == 1
    assert window.items[3].content == "anchor-v1"
    # 同 id 的更高 revision 不替代显式定位的旧 revision。
    assert {item.revision for item in window.items if item.message_id == target} == {1}


async def test_search_matches_title_or_content_and_isolates_domains(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    other = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    await _seed_prompt(test_engine, other)
    dao = AgentDao(test_engine)
    conversation_id, execution_id, _ = await _accept(test_engine, dao, domain)
    other_conv, other_exec, _ = await _accept(test_engine, dao, other)

    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE agent_conversations SET title = $2 WHERE id = $1",
            (conversation_id, "季度复盘 100% 完成"),
        )
    await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="assistant",
        content="净利润同比增长 12%",
    )
    await _insert_message(
        test_engine,
        domain=other,
        conversation_id=other_conv,
        execution_id=other_exec,
        role="assistant",
        content="净利润同比增长 12%",
    )

    query = HistoryQuery(
        text="净利润",
        conversation_id=None,
        execution_id=None,
        attempt_id=None,
        message_role=None,
        from_at=None,
        until_at=None,
        include_archived=False,
        statuses=(),
        fields="content",
    )
    page = await dao.search(domain, query, cursor=None, limit=10)
    assert len(page.items) == 1
    assert page.items[0].conversation_title == "季度复盘 100% 完成"

    title_query = replace(query, text="季度复盘", fields="title")
    title_page = await dao.search(domain, title_query, cursor=None, limit=10)
    assert len(title_page.items) == 1

    # 归档会话默认排除，显式 include_archived 后可见。
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("UPDATE agent_conversations SET archived = true WHERE id = $1", (conversation_id,))
    assert (await dao.search(domain, query, cursor=None, limit=10)).items == ()
    included = await dao.search(domain, replace(query, include_archived=True), cursor=None, limit=10)
    assert len(included.items) == 1


async def test_search_pagination_cursor_is_query_and_domain_bound(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    conversation_id, execution_id, _ = await _accept(test_engine, dao, domain)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(3):
        await _insert_message(
            test_engine,
            domain=domain,
            conversation_id=conversation_id,
            execution_id=execution_id,
            role="assistant",
            content=f"命中-{index}",
            created_at=start + timedelta(minutes=index),
        )
    query = HistoryQuery(
        text="命中",
        conversation_id=None,
        execution_id=None,
        attempt_id=None,
        message_role=None,
        from_at=None,
        until_at=None,
        include_archived=False,
        statuses=(),
        fields="content",
    )
    page1 = await dao.search(domain, query, cursor=None, limit=2)
    assert [hit.message.content for hit in page1.items] == ["命中-2", "命中-1"]
    assert page1.next_cursor is not None
    page2 = await dao.search(domain, query, cursor=page1.next_cursor, limit=2)
    assert [hit.message.content for hit in page2.items] == ["命中-0"]
    assert page2.next_cursor is None

    with pytest.raises(AgentValidationError):
        await dao.search(domain, replace(query, text="其他"), cursor=page1.next_cursor, limit=2)


async def test_history_service_validates_and_maps_not_found(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    service = AgentHistoryService(domain=domain, dao=cast(Any, dao))
    conversation_id, execution_id, _ = await _accept(test_engine, dao, domain)
    await _insert_message(
        test_engine,
        domain=domain,
        conversation_id=conversation_id,
        execution_id=execution_id,
        role="assistant",
        content="hello",
    )
    window = await service.read_messages(
        conversation_id=conversation_id,
        execution_id=None,
        attempt_id=None,
        cursor=None,
        limit=10,
    )
    assert len(window.items) == 1

    with pytest.raises(AgentOperationError) as error:
        await service.locate(uuid.uuid4(), 1)
    assert error.value.problem.code == "not_found"

    with pytest.raises(AgentOperationError) as error:
        await service.search(
            HistoryQuery(
                text="x" * 501,
                conversation_id=None,
                execution_id=None,
                attempt_id=None,
                message_role=None,
                from_at=None,
                until_at=None,
                include_archived=False,
                statuses=(),
                fields="content",
            ),
            cursor=None,
            limit=10,
        )
    assert error.value.problem.code == "invalid_request"


async def test_conversation_pending_count_and_archived_listing(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    other = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    await _seed_prompt(test_engine, other)
    dao = AgentDao(test_engine)
    conversation_id, execution_id, task_id = await _accept(test_engine, dao, domain)
    other_conv, _, _ = await _accept(test_engine, dao, other)

    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("UPDATE agent_conversations SET archived = true WHERE id = $1", (other_conv,))
    view = await dao.read_conversation(domain, conversation_id)
    assert view is not None
    assert view.pending_decision_count == 0
    assert view.title == make_request().text

    active = await dao.list_conversations(domain, archived=False, cursor=None, limit=10)
    assert [item.conversation_id for item in active.items] == [conversation_id]
    archived = await dao.list_conversations(domain, archived=True, cursor=None, limit=10)
    assert archived.items == ()

    # 域隔离：其他域不能读到本域会话。
    assert await dao.read_conversation(other, conversation_id) is None
    assert await dao.read_execution(other, execution_id) is None
    assert await dao.read_execution(domain, execution_id) is not None
    del task_id


async def test_artifact_and_resource_reads_respect_domain_and_tombstone(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    _, execution_id, task_id = await _accept(test_engine, dao, domain)
    attempt_id = uuid.uuid4()
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index, native_run_refs,"
            " validity_digest, input_manifest, observation)"
            " VALUES ($1, $2, 1, 1, '[]'::jsonb, $3, '{}'::jsonb, '{}'::jsonb)",
            (attempt_id, task_id, _DIGEST),
        )
    artifact_id = uuid.uuid4()
    other = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, other)
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_resources (id, domain, kind, current_revision, use_revision)"
            " VALUES ($1, $2, 'artifact', 1, 0)",
            (artifact_id, domain),
        )
        await conn.exec_driver_sql(
            "INSERT INTO agent_resource_versions (resource_id, revision, payload, content_digest, availability)"
            " VALUES ($1, 1, '{}'::jsonb, $2, 'available')",
            (artifact_id, _DIGEST),
        )
        await conn.exec_driver_sql(
            "INSERT INTO agent_artifacts (id, revision, execution_id, attempt_id, output_contract,"
            " content, markdown, source_refs, limitations, content_digest)"
            " VALUES ($1, 1, $2, $3, 'answer/1', '{}'::jsonb, '# 结果', '[]'::jsonb, '[]'::jsonb, $4)",
            (artifact_id, execution_id, attempt_id, _DIGEST),
        )
    view = await dao.read_artifact(domain, artifact_id, 1)
    assert view is not None
    assert view.markdown == "# 结果"
    assert decode_json_doc(view.content) == {}
    assert await dao.read_artifact(other, artifact_id, 1) is None

    assert (await dao.read_resource(domain, ResourceRef(artifact_id, 1))) is not None
    assert (await dao.read_resource(other, ResourceRef(artifact_id, 1))) is None
    assert (await dao.read_resource(domain, ResourceRef(artifact_id, 99))) is None
