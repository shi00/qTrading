"""S01 集成：来源资源/版本/事件与工具响应登记（真实 PostgreSQL）。"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentOperationError,
    JsonDoc,
    ResourceRef,
    SubmissionKey,
    decode_json_doc,
    json_doc,
    request_digest,
    validate_request,
)
from data.persistence.daos.agent_dao import AgentDao
from services.agent.service import AgentService
from tests.builders.agent import make_request

pytestmark = pytest.mark.integration

_PROMPT_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
_DIGEST = "c" * 64
_AS_OF = "2026-04-30"


def _payload(source: JsonDoc) -> dict[str, object]:
    decoded = decode_json_doc(source)
    assert isinstance(decoded, dict)
    return decoded


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
    request = replace(make_request(), key=SubmissionKey(domain, "tests", f"source-{uuid.uuid4()}"))
    validate_request(request)
    receipt = await dao.accept_request(domain, request, request_digest(request))
    assert receipt.status == "accepted"
    async with engine.connect() as conn:
        execution_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_executions WHERE intent_id = $1", (receipt.intent_id,))
        ).scalar_one()
        task_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_tasks WHERE execution_id = $1", (execution_id,))
        ).scalar_one()
    attempt_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index, native_run_refs,"
            " validity_digest, input_manifest, observation)"
            " VALUES ($1, $2, 1, 1, '[]'::jsonb, $3, '{}'::jsonb, '{}'::jsonb)",
            (attempt_id, task_id, _DIGEST),
        )
        await conn.exec_driver_sql(
            "UPDATE agent_tasks SET current_attempt_id = $2 WHERE id = $1",
            (task_id, attempt_id),
        )
    return uuid.UUID(str(execution_id)), uuid.UUID(str(task_id)), attempt_id


def _metadata(**overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "schema_version": 1,
        "name": "公告原文",
        "as_of": _AS_OF,
        "published_at": None,
        "published_date": "2026-03-20",
        "time_precision": "date",
        "period": None,
        "effective_from": None,
        "effective_to": None,
        "units": {"roe": {"unit": "%", "basis": "report"}},
        "correction_of": None,
    }
    doc.update(overrides)
    return doc


class _Provider:
    def __init__(self, prepared: JsonDoc) -> None:
        self.prepared = prepared
        self.calls = 0

    async def read_source(self, source_object_key: str, expected_revision: str, as_of: date) -> JsonDoc:
        self.calls += 1
        return self.prepared


def _prepared(**overrides: object) -> JsonDoc:
    doc: dict[str, object] = {
        "schema_version": 1,
        "metadata": _metadata(),
        "media_type": "text/plain",
        "text": "年报摘要正文",
        "read_range": {
            "pages": [1, 2],
            "sheets": [],
            "row_start": None,
            "row_end": None,
            "locator": "p1-2",
        },
        "extraction": "partial",
        "limitations": ["仅前两页"],
    }
    doc.update(overrides)
    return json_doc(doc)


def _business(**overrides: object) -> JsonDoc:
    doc: dict[str, object] = {
        "schema_version": 1,
        "kind": "business",
        "provider_key": "stock",
        "source_object_key": "600519",
        "source_revision": "2025-12-31",
        "as_of": _AS_OF,
    }
    doc.update(overrides)
    return json_doc(doc)


async def test_import_source_persists_resource_version_and_public_event(
    test_engine: AsyncEngine, tmp_path: Path
) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    service = AgentService(domain=domain, dao=dao, blob_root=tmp_path)
    source_path = tmp_path / "notice.md"
    source_path.write_text("仅日期文件正文", encoding="utf-8")
    operation_id = uuid.uuid4()

    receipt = await service.import_source(operation_id, str(source_path), json_doc(_metadata()))

    assert receipt.status == "committed"
    assert receipt.resource_id is not None
    assert receipt.resource_id == uuid.uuid5(operation_id, "source")
    view = await dao.read_resource(domain, ResourceRef(receipt.resource_id, 1))
    assert view is not None
    payload = _payload(view)
    assert payload["origin"] == "file"
    assert payload["media_type"] == "text/markdown"
    assert payload["as_of"] == _AS_OF
    ref = cast(dict[str, object], payload["content_ref"])
    blob = tmp_path / cast(str, ref["relative_path"])
    assert blob.read_text(encoding="utf-8") == "仅日期文件正文"

    async with test_engine.connect() as conn:
        event_payload = (
            await conn.exec_driver_sql(
                "SELECT payload FROM agent_events WHERE kind = 'resource.changed' AND entity_id = $1",
                (receipt.resource_id,),
            )
        ).scalar_one()
    assert "仅日期文件正文" not in json_doc(event_payload)
    assert event_payload["kind"] == "source"


async def test_import_source_is_idempotent_and_conflicts_on_new_payload(
    test_engine: AsyncEngine, tmp_path: Path
) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    service = AgentService(domain=domain, dao=dao, blob_root=tmp_path)
    source_path = tmp_path / "notice.md"
    source_path.write_text("正文", encoding="utf-8")
    operation_id = uuid.uuid4()

    first = await service.import_source(operation_id, str(source_path), json_doc(_metadata()))
    second = await service.import_source(operation_id, str(source_path), json_doc(_metadata()))
    conflict = await service.import_source(operation_id, str(source_path), json_doc(_metadata(name="另一份")))

    assert first == second
    assert conflict.status == "conflict"
    async with test_engine.connect() as conn:
        versions = (
            await conn.exec_driver_sql(
                "SELECT count(*) FROM agent_resource_versions WHERE resource_id = $1", (first.resource_id,)
            )
        ).scalar_one()
        resources = (
            await conn.exec_driver_sql("SELECT count(*) FROM agent_resources WHERE id = $1", (first.resource_id,))
        ).scalar_one()
        commands = (
            await conn.exec_driver_sql("SELECT count(*) FROM agent_commands WHERE id = $1", (operation_id,))
        ).scalar_one()
    assert (versions, resources, commands) == (1, 1, 1)


async def test_business_source_keeps_partial_availability_without_model_turn(
    test_engine: AsyncEngine, tmp_path: Path
) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    provider = _Provider(_prepared())
    service = AgentService(domain=domain, dao=dao, blob_root=tmp_path, source_providers={"stock": provider})
    operation_id = uuid.uuid4()

    receipt = await service.register_source(operation_id, _business())

    assert receipt.status == "committed"
    assert receipt.resource_id is not None
    assert provider.calls == 1
    view = await dao.read_resource(domain, ResourceRef(receipt.resource_id, 1))
    assert view is not None
    payload = _payload(view)
    assert payload["origin"] == "business"
    assert payload["extraction"] == "partial"
    async with test_engine.connect() as conn:
        availability = (
            await conn.exec_driver_sql(
                "SELECT availability FROM agent_resource_versions WHERE resource_id = $1 AND revision = 1",
                (receipt.resource_id,),
            )
        ).scalar_one()
        turns = (await conn.exec_driver_sql("SELECT count(*) FROM agent_model_turns")).scalar_one()
    assert availability == "partial"
    assert turns == 0


async def test_tool_source_reads_committed_response_and_rejects_wrong_target(
    test_engine: AsyncEngine, tmp_path: Path
) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    service = AgentService(domain=domain, dao=dao, blob_root=tmp_path)
    execution_id, task_id, attempt_id = await _accept(test_engine, dao, domain)
    effect_key = f"effect-{uuid.uuid4()}"
    await dao.save_tool_call(
        effect_key,
        "prepared",
        json_doc(
            {
                "state": "prepared",
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "native_owner_id": "owner-1",
                "native_task_id": "native-task-1",
                "native_run_id": "native-run-1",
                "native_event_ref": "event-1",
                "tool_call_id": "call-1",
                "tool_ref": {"name": "search_news", "schema_revision": "1"},
                "input_digest": _DIGEST,
            }
        ),
    )
    await dao.save_tool_call(
        effect_key,
        "prepared",
        json_doc(
            {
                "state": "in_flight",
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "native_owner_id": "owner-1",
                "native_task_id": "native-task-1",
                "native_run_id": "native-run-1",
                "native_event_ref": "event-1",
                "tool_call_id": "call-1",
                "tool_ref": {"name": "search_news", "schema_revision": "1"},
                "input_digest": _DIGEST,
            }
        ),
    )
    response = {"items": [{"title": "样本新闻"}], "status": "ok"}
    await dao.save_tool_call(
        effect_key,
        "in_flight",
        json_doc(
            {
                "state": "committed",
                "execution_id": execution_id,
                "attempt_id": attempt_id,
                "native_owner_id": "owner-1",
                "native_task_id": "native-task-1",
                "native_run_id": "native-run-1",
                "native_event_ref": "event-1",
                "tool_call_id": "call-1",
                "tool_ref": {"name": "search_news", "schema_revision": "1"},
                "input_digest": _DIGEST,
                "response": response,
                "result_digest": _DIGEST,
            }
        ),
    )
    registration = json_doc(
        {
            "schema_version": 1,
            "kind": "tool",
            "target": {
                "execution_id": execution_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
            },
            "effect_key": effect_key,
            "as_of": _AS_OF,
        }
    )
    operation_id = uuid.uuid4()
    receipt = await service.register_source(operation_id, registration)

    assert receipt.status == "committed"
    assert receipt.resource_id is not None
    view = await dao.read_resource(domain, ResourceRef(receipt.resource_id, 1))
    assert view is not None
    payload = _payload(view)
    assert payload["origin"] == "tool"
    assert payload["time_precision"] == "unknown"
    ref = cast(dict[str, object], payload["content_ref"])
    blob = tmp_path / cast(str, ref["relative_path"])
    assert json.loads(blob.read_text(encoding="utf-8")) == response

    wrong = json_doc(
        {
            "schema_version": 1,
            "kind": "tool",
            "target": {
                "execution_id": execution_id,
                "task_id": task_id,
                "attempt_id": uuid.uuid4(),
            },
            "effect_key": effect_key,
            "as_of": _AS_OF,
        }
    )
    with pytest.raises(AgentOperationError) as caught:
        await service.register_source(uuid.uuid4(), wrong)
    assert caught.value.problem.code == "reference_conflict"
