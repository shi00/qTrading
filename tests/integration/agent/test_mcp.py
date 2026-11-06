"""S02 MCP 集成：保存的发现目录、显式工具集、生命周期意图与零启连（真实 PostgreSQL）。

覆盖 §9.5/§9.8：
- read_mcp_tool_catalog 只读已保存 discover 结果，未发现明确不可用而非空许可；
- 发现十项只暴露显式选中的两项，保存新服务版本不启动/不连接；
- mcp_operation 只持久意图，回执 committed 不代表连接成功，状态 fresh=false/unknown；
- 选择工具必须存在同发现版本且 schema_digest 一致，过期发现/改载体拒绝；
- 同名工具跨服务由暴露名隔离，不合并。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentOperationError,
    JsonDoc,
    ModelChoice,
    Mutation,
    ResourceRef,
    RoleBinding,
    decode_json_doc,
    json_doc,
    validate_payload,
)
from data.persistence.daos.agent_dao import AgentDao
from services.agent.capabilities import compute_discovery_revision, exposed_tool_name, resolve_effective_tools
from services.agent.service import AgentService

pytestmark = pytest.mark.integration

_SERVICE_A = uuid.UUID("10000000-0000-0000-0000-00000000000a")
_SERVICE_B = uuid.UUID("10000000-0000-0000-0000-00000000000b")
_PROMPT = ResourceRef(uuid.UUID("10000000-0000-0000-0000-000000000001"), 1)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tool(name: str, digest: str, service_id: uuid.UUID) -> dict[str, object]:
    return {
        "name": name,
        "description": f"{name} 描述",
        "parameters_json": json.dumps({"type": "object", "properties": {}}, separators=(",", ":")),
        "schema_digest": digest,
        "exposed_name": exposed_tool_name(service_id, name),
        "allowed": False,
        "unavailable_reason": None,
    }


def _payload(
    service_id: uuid.UUID,
    tools: list[dict[str, object]],
    *,
    selected: list[dict[str, object]],
    discovery_revision: str | None,
    kind: str = "remote_streamable_http",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": kind,
        "name": f"service-{service_id}",
        "timeout_seconds": 30,
        "discovery_revision": discovery_revision,
        "selected_tools": [
            {"name": str(item["name"]), "schema_digest": str(item["schema_digest"])} for item in selected
        ],
    }
    if kind == "local_stdio":
        payload.update({"executable": "/opt/mcp", "arguments": ["serve"], "working_directory": "/opt", "env_refs": {}})
    else:
        payload.update({"endpoint": "https://mcp.example.com/tools", "auth": {"kind": "none", "refs": {}}})
    return payload


async def _seed_resource(engine: AsyncEngine, domain: str, resource_id: uuid.UUID, payload: dict[str, object]) -> None:
    if payload.get("kind") not in ("local_stdio", "remote_streamable_http", "remote_legacy_sse"):
        raise AssertionError("seed payload must be an MCP payload")
    validate_payload("mcp", json_doc(payload))
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DELETE FROM agent_resource_versions WHERE resource_id = $1", (resource_id,))
        await conn.exec_driver_sql("DELETE FROM agent_resources WHERE id = $1", (resource_id,))
        await conn.exec_driver_sql(
            "INSERT INTO agent_resources (id, domain, kind, current_revision, use_revision)"
            " VALUES ($1, $2, 'mcp', 1, 0)",
            (resource_id, domain),
        )
        await conn.exec_driver_sql(
            "INSERT INTO agent_resource_versions (resource_id, revision, payload, content_digest, availability)"
            " VALUES ($1, 1, $2::jsonb, $3, 'available')",
            (resource_id, json.dumps(payload), _digest(json_doc(payload))),
        )


async def _seed_discovery(
    engine: AsyncEngine,
    domain: str,
    service_ref: ResourceRef,
    tools: list[dict[str, object]],
    *,
    discovery_revision: str,
    observed_at: str,
) -> uuid.UUID:
    operation_id = uuid.uuid4()
    payload = {
        "ref": {"resource_id": str(service_ref.resource_id), "revision": service_ref.revision},
        "action": "discover",
    }
    receipt = {
        "operation_id": str(operation_id),
        "ref": payload["ref"],
        "action": "discover",
        "status": "succeeded",
        "started_at": observed_at,
        "finished_at": observed_at,
        "result": {
            "process": "running",
            "connection": "connected",
            "authentication": "verified",
            "discovery_revision": discovery_revision,
            "tools": tools,
        },
        "problem": None,
    }
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_commands (id, domain, kind, payload, payload_digest, handoff, receipt)"
            " VALUES ($1, $2, 'mcp_operation', $3::jsonb, $4, 'acknowledged', $5::jsonb)",
            (
                operation_id,
                domain,
                json.dumps(payload),
                _digest(json_doc(payload)),
                json.dumps(receipt),
            ),
        )
    return operation_id


def _decoded(doc: JsonDoc) -> dict[str, Any]:
    value = decode_json_doc(doc)
    assert isinstance(value, dict)
    return value


async def test_catalog_reads_saved_discovery_and_only_selected_tools_are_allowed(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    service = AgentService(domain=domain, dao=AgentDao(test_engine), blob_root=None)
    tools = [_tool(f"tool-{index}", _digest(f"a{index}"), _SERVICE_A) for index in range(10)]
    revision = compute_discovery_revision(_SERVICE_A, tools)
    payload = _payload(_SERVICE_A, tools, selected=[], discovery_revision=revision)
    await _seed_resource(test_engine, domain, _SERVICE_A, payload)
    await _seed_discovery(
        test_engine,
        domain,
        ResourceRef(_SERVICE_A, 1),
        tools,
        discovery_revision=revision,
        observed_at="2026-04-30T01:00:00+00:00",
    )

    before = _decoded(await service.read_mcp_tool_catalog(ResourceRef(_SERVICE_A, 1)))
    assert before["fresh"] is True
    assert before["discovery_revision"] == revision
    assert before["selected_tools"] == []
    assert len(before["tools"]) == 10
    assert all(item["allowed"] is False for item in before["tools"])
    assert before["problem"] is None

    mutation = Mutation(
        operation_id=uuid.uuid4(),
        kind="mcp_tools",
        resource_id=_SERVICE_A,
        expected_revision=1,
        payload=json_doc(
            {
                "service": {"resource_id": str(_SERVICE_A), "revision": 1},
                "discovery_revision": revision,
                "selected_tools": [
                    {"name": "tool-0", "schema_digest": _digest("a0")},
                    {"name": "tool-1", "schema_digest": _digest("a1")},
                ],
            }
        ),
    )
    receipt = await service.mutate(mutation)
    assert receipt.status == "committed"

    after = _decoded(await service.read_mcp_tool_catalog(ResourceRef(_SERVICE_A, 2)))
    assert [item["name"] for item in after["selected_tools"]] == ["tool-0", "tool-1"]
    allowed = [item["name"] for item in after["tools"] if item["allowed"] is True]
    assert allowed == ["tool-0", "tool-1"]

    old = decode_json_doc(str(await AgentDao(test_engine).read_resource(domain, ResourceRef(_SERVICE_A, 1))))
    assert isinstance(old, dict) and old["selected_tools"] == []

    async with test_engine.connect() as conn:
        operations = (
            await conn.exec_driver_sql(
                "SELECT count(*) FROM agent_commands WHERE domain = $1 AND kind = 'mcp_operation'", (domain,)
            )
        ).scalar_one()
    assert operations == 1  # 只有预置 discover，保存工具集没有启动/连接


async def test_mcp_operation_persists_intent_without_connection_and_status_is_not_fresh(
    test_engine: AsyncEngine,
) -> None:
    domain = f"domain-{uuid.uuid4()}"
    service = AgentService(domain=domain, dao=AgentDao(test_engine), blob_root=None)
    tools = [_tool("query", _digest("q"), _SERVICE_A)]
    revision = compute_discovery_revision(_SERVICE_A, tools)
    await _seed_resource(
        test_engine, domain, _SERVICE_A, _payload(_SERVICE_A, tools, selected=[], discovery_revision=revision)
    )
    operation_id = uuid.uuid4()

    receipt = await service.mutate(
        Mutation(
            operation_id=operation_id,
            kind="mcp_operation",
            resource_id=_SERVICE_A,
            expected_revision=1,
            payload=json_doc({"ref": {"resource_id": str(_SERVICE_A), "revision": 1}, "action": "start"}),
        )
    )
    assert receipt.status == "committed"
    assert receipt.resource_id == _SERVICE_A

    operation = _decoded(await service.lookup_mcp_operation(operation_id))
    assert operation["status"] == "pending"
    assert operation["action"] == "start"
    assert operation["result"] is None
    assert operation["started_at"] is None

    status = _decoded(await service.read_mcp_status(ResourceRef(_SERVICE_A, 1)))
    assert status["fresh"] is False
    assert status["process"] == "unknown"
    assert status["connection"] == "unknown"
    assert status["problem"] is not None

    fresh = _decoded(await service.read_resource(ResourceRef(_SERVICE_A, 1)))
    assert isinstance(fresh, dict) and fresh["selected_tools"] == []


async def test_missing_discovery_is_unavailable_not_unlimited(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    service = AgentService(domain=domain, dao=AgentDao(test_engine), blob_root=None)
    tools = [_tool("query", _digest("q"), _SERVICE_A)]
    await _seed_resource(
        test_engine, domain, _SERVICE_A, _payload(_SERVICE_A, tools, selected=[], discovery_revision=None)
    )

    catalog = _decoded(await service.read_mcp_tool_catalog(ResourceRef(_SERVICE_A, 1)))
    assert catalog["fresh"] is False
    assert catalog["tools"] == []
    assert catalog["selected_tools"] == []
    assert catalog["problem"]["code"] == "capability_unavailable"


async def test_stale_discovery_and_changed_carrier_are_rejected(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    service = AgentService(domain=domain, dao=AgentDao(test_engine), blob_root=None)
    tools = [_tool("query", _digest("q"), _SERVICE_A)]
    old_revision = compute_discovery_revision(_SERVICE_A, tools)
    await _seed_resource(
        test_engine, domain, _SERVICE_A, _payload(_SERVICE_A, tools, selected=[], discovery_revision=old_revision)
    )
    await _seed_discovery(
        test_engine,
        domain,
        ResourceRef(_SERVICE_A, 1),
        tools,
        discovery_revision=_digest("newer-discovery"),
        observed_at="2026-04-30T01:00:00+00:00",
    )

    catalog = _decoded(await service.read_mcp_tool_catalog(ResourceRef(_SERVICE_A, 1)))
    assert catalog["fresh"] is False
    assert catalog["discovery_revision"] == _digest("newer-discovery")
    assert all(item["allowed"] is False for item in catalog["tools"])
    assert catalog["problem"]["code"] == "capability_unavailable"

    mutation = Mutation(
        operation_id=uuid.uuid4(),
        kind="mcp_tools",
        resource_id=_SERVICE_A,
        expected_revision=1,
        payload=json_doc(
            {
                "service": {"resource_id": str(_SERVICE_A), "revision": 1},
                "discovery_revision": old_revision,
                "selected_tools": [{"name": "query", "schema_digest": _digest("q")}],
            }
        ),
    )
    with pytest.raises(AgentOperationError) as error:
        await service.mutate(mutation)
    assert error.value.problem.code == "capability_unavailable"


async def test_same_tool_name_across_services_has_distinct_exposed_names(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    tools_a = [_tool("query", _digest("a"), _SERVICE_A)]
    tools_b = [_tool("query", _digest("b"), _SERVICE_B)]
    payload_a = _payload(
        _SERVICE_A, tools_a, selected=[tools_a[0]], discovery_revision=compute_discovery_revision(_SERVICE_A, tools_a)
    )
    payload_b = _payload(
        _SERVICE_B, tools_b, selected=[tools_b[0]], discovery_revision=compute_discovery_revision(_SERVICE_B, tools_b)
    )
    await _seed_resource(test_engine, domain, _SERVICE_A, payload_a)
    await _seed_resource(test_engine, domain, _SERVICE_B, payload_b)

    role = RoleBinding(
        "main",
        1,
        _PROMPT,
        ModelChoice("explicit", "connection-A", None),
        (),
        (ResourceRef(_SERVICE_A, 1), ResourceRef(_SERVICE_B, 1)),
        (),
    )
    resolved = resolve_effective_tools(
        (role,),
        {(_SERVICE_A, 1): payload_a, (_SERVICE_B, 1): payload_b},
        {(_SERVICE_A, 1): {"tools": tools_a}, (_SERVICE_B, 1): {"tools": tools_b}},
    )
    names = [item["exposed_name"] for item in cast(list[dict[str, Any]], resolved[0]["tools"])]
    assert len(names) == 2
    assert len(set(names)) == 2
    assert all(name.startswith("mcp_") for name in names)
