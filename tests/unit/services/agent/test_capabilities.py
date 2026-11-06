"""S02 能力资产：Skill 预检/导入、角色双集合、MCP 目录与有效工具（§9.6—§9.9）。

先建立行为反例：
- 主角色手选非默认 Skill 生效，子角色只取自身默认；
- 目录/ZIP 预检拒绝越界/链接/脚本入口/重复覆盖，未知许可与未知依赖如实保留；
- 导入按 inspection_digest 重核同一载体，失败保旧、改载体冲突；
- MCP 仅暴露显式选中的工具（发现十项只选两项），同名跨服务不混淆、暴露名碰撞拒绝；
- 保存工具集合/生命周期意图零启动、零连接，三种传输按封闭 schema 校验。
"""

from __future__ import annotations

import json
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentOperationError,
    ModelChoice,
    Mutation,
    MutationReceipt,
    ResourceRef,
    RoleBinding,
    decode_json_doc,
    effective_skills,
    json_doc,
    validate_payload,
)
from services.agent import capabilities
from services.agent.capabilities import (
    CapabilityError,
    build_skill_payload,
    compute_discovery_revision,
    exposed_tool_name,
    inspect_skill_carrier_sync,
    publish_skill_files,
    resolve_effective_tools,
)
from services.agent.service import AgentService

pytestmark = pytest.mark.unit

_SERVICE_A = uuid.UUID("10000000-0000-0000-0000-00000000000a")
_SERVICE_B = uuid.UUID("10000000-0000-0000-0000-00000000000b")
_PROMPT = ResourceRef(uuid.UUID("10000000-0000-0000-0000-000000000001"), 1)
_SKILL_A = ResourceRef(uuid.UUID("10000000-0000-0000-0000-000000000002"), 1)
_SKILL_B = ResourceRef(uuid.UUID("10000000-0000-0000-0000-000000000003"), 1)
_SKILL_C = ResourceRef(uuid.UUID("10000000-0000-0000-0000-000000000004"), 1)


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_skill(root: Path, *, frontmatter: str, body: str = "按步骤执行。") -> Path:
    package = root / "demo-skill"
    (package / "notes").mkdir(parents=True)
    (package / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    (package / "notes" / "readme.md").write_text("补充说明", encoding="utf-8")
    return package


def _problem_codes(inspection: dict[str, object]) -> set[str]:
    problems = inspection["problems"]
    assert isinstance(problems, list)
    return {str(item["code"]) for item in problems if isinstance(item, dict)}


def _tool(name: str, digest: str, service_id: uuid.UUID | None = None) -> dict[str, object]:
    item: dict[str, object] = {
        "name": name,
        "description": f"{name} 描述",
        "parameters_json": json.dumps({"type": "object", "properties": {}}, separators=(",", ":")),
        "schema_digest": digest,
        "exposed_name": exposed_tool_name(service_id or _SERVICE_A, name),
        "allowed": False,
        "unavailable_reason": None,
    }
    return item


def _service_payload(
    service_id: uuid.UUID,
    tools: list[dict[str, object]],
    *,
    kind: str = "remote_streamable_http",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": kind,
        "name": f"service-{service_id}",
        "timeout_seconds": 30,
        "discovery_revision": compute_discovery_revision(service_id, tools),
        "selected_tools": [{"name": item["name"], "schema_digest": item["schema_digest"]} for item in tools[:2]],
    }
    if kind == "local_stdio":
        payload.update({"executable": "/opt/mcp", "arguments": ["serve"], "working_directory": "/opt", "env_refs": {}})
    else:
        payload.update({"endpoint": "https://mcp.example.com/tools", "auth": {"kind": "none", "refs": {}}})
    return payload


def _role(default_mcp: tuple[ResourceRef, ...]) -> RoleBinding:
    return RoleBinding("main", 1, _PROMPT, ModelChoice("explicit", "connection-A", None), (), default_mcp, ())


# ---------------------------------------------------------------------------
# 角色 enabled/default 与主子有效集合
# ---------------------------------------------------------------------------


def test_main_may_use_non_default_skill_but_child_keeps_own_defaults() -> None:
    main = effective_skills((_SKILL_A, _SKILL_B), (_SKILL_C,), enabled=(_SKILL_A, _SKILL_B, _SKILL_C), child=False)
    child = effective_skills((_SKILL_A, _SKILL_B), (_SKILL_C,), enabled=(_SKILL_A, _SKILL_B, _SKILL_C), child=True)

    assert main == (_SKILL_A, _SKILL_B, _SKILL_C)
    assert child == (_SKILL_A, _SKILL_B)
    assert effective_skills((), (), enabled=(), child=False) == ()


def test_role_payload_empty_enabled_is_not_unlimited_and_default_must_be_subset() -> None:
    payload = {
        "schema_version": 1,
        "role_key": "main",
        "description": "主角色",
        "prompt_ref": {"resource_id": str(_PROMPT.resource_id), "revision": 1},
        "model_choice": {"kind": "inherit", "connection_id": None, "scenario_id": None},
        "enabled_skills": [],
        "default_skills": [],
        "default_mcp": [],
        "enabled": True,
    }
    validate_payload("role", json_doc(payload))

    payload["default_skills"] = [{"resource_id": str(_SKILL_A.resource_id), "revision": 1}]
    with pytest.raises(Exception) as error:
        validate_payload("role", json_doc(payload))
    assert "default_skills" in str(error.value)


# ---------------------------------------------------------------------------
# Skill 目录/ZIP 预检
# ---------------------------------------------------------------------------


def test_directory_inspection_keeps_declared_license_and_unknown_dependencies(tmp_path: Path) -> None:
    package = _write_skill(
        tmp_path,
        frontmatter="name: demo-skill\ndescription: 演示\ndependencies: [pandas, capability:web]",
    )
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(package), "directory")

    assert inspection["input_kind"] == "directory"
    assert inspection["display_name"] == "demo-skill"
    files = inspection["files"]
    assert isinstance(files, list) and len(files) == 2
    skill = cast(list[dict[str, Any]], inspection["skills"])[0]
    assert skill["license"] == {"status": "unknown", "identifier": None, "text_ref": None}
    dependencies = skill["dependencies"]
    assert {item["name"] for item in dependencies} == {"pandas", "capability:web"}
    assert {item["kind"] for item in dependencies} == {"package", "capability"}
    assert {item["status"] for item in dependencies} == {"unknown"}
    assert "license_unknown" in _problem_codes(inspection)
    assert inspection["can_import"] is True

    payload = decode_json_doc(build_skill_payload(inspection))
    assert isinstance(payload, dict)
    assert payload["enabled"] is False
    assert payload["import_origin"]["kind"] == "directory"
    validate_payload("skill", build_skill_payload(inspection))


def test_missing_dependency_blocks_enabling_and_restricted_license_blocks_import(tmp_path: Path) -> None:
    package = _write_skill(
        tmp_path,
        frontmatter="name: demo-skill\ndescription: 演示\nlicense: All-Rights-Reserved\ndependencies: [pandas]",
    )
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(package), "directory")

    assert inspection["can_import"] is False
    assert "license_restricted" in _problem_codes(inspection)
    payload = decode_json_doc(build_skill_payload(inspection))
    assert isinstance(payload, dict)
    assert payload["enabled"] is False


def test_inspection_rejects_symlink_script_entry_and_duplicate_zip_entries(tmp_path: Path) -> None:
    package = _write_skill(tmp_path, frontmatter="name: demo\ndescription: 演示")
    outside = tmp_path / "outside.txt"
    outside.write_text("外部", encoding="utf-8")
    os.symlink(outside, package / "link.txt")
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(package), "directory")
    assert inspection["can_import"] is False
    assert "symbolic_link" in _problem_codes(inspection)

    scripted = _write_skill(tmp_path / "scripted", frontmatter="name: demo2\ndescription: 演示")
    (scripted / "run.bat").write_text("@echo off", encoding="utf-8")
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(scripted), "directory")
    assert inspection["can_import"] is False
    assert "executable_entry" in _problem_codes(inspection)

    archive = tmp_path / "dup.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("SKILL.md", "---\nname: dup\ndescription: 演示\n---\n正文")
        handle.writestr("SKILL.md", "---\nname: dup\ndescription: 演示\n---\n另一份")
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(archive), "zip")
    assert inspection["can_import"] is False
    assert "duplicate_entry" in _problem_codes(inspection)

    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as handle:
        handle.writestr("SKILL.md", "---\nname: evil\ndescription: 演示\n---\n正文")
        handle.writestr("../evil.txt", "越界")
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(traversal), "zip")
    assert inspection["can_import"] is False
    assert "invalid_relative_path" in _problem_codes(inspection)


def test_inspection_enforces_file_count_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package = _write_skill(tmp_path, frontmatter="name: demo\ndescription: 演示")
    (package / "notes" / "second.md").write_text("二", encoding="utf-8")
    monkeypatch.setattr(capabilities, "SKILL_MAX_FILES", 2)
    inspection = inspect_skill_carrier_sync(uuid.uuid4(), str(package), "directory")

    assert inspection["can_import"] is False
    assert "too_many_files" in _problem_codes(inspection)


def test_zip_inspection_is_deterministic_and_publish_is_atomic(tmp_path: Path) -> None:
    archive = tmp_path / "demo.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("SKILL.md", "---\nname: zipped\ndescription: 演示\n---\n正文")
        handle.writestr("assets/data.json", '{"b":2,"a":1}')
    first = inspect_skill_carrier_sync(uuid.uuid4(), str(archive), "zip")
    second = inspect_skill_carrier_sync(uuid.uuid4(), str(archive), "zip")
    assert first["content_digest"] == second["content_digest"]
    assert first["inspection_digest"] != second["inspection_digest"]  # inspection_id 不同

    relative = publish_skill_files(
        tmp_path,
        uuid.UUID("20000000-0000-0000-0000-000000000001"),
        2,
        {"SKILL.md": b"---\nname: zipped\ndescription: \xe6\xbc\x94\xe7\xa4\xba\n---\n\xe6\xad\xa3\xe6\x96\x87"},
    )
    assert (tmp_path / relative / "SKILL.md").is_file()

    with pytest.raises(CapabilityError):
        publish_skill_files(tmp_path, uuid.UUID("20000000-0000-0000-0000-000000000002"), 1, {"../escape": b"x"})
    assert not (tmp_path / "agent" / "skills" / "20000000-0000-0000-0000-000000000002").exists()


# ---------------------------------------------------------------------------
# MCP 目录、选择与有效工具
# ---------------------------------------------------------------------------


def test_only_selected_tools_are_visible_and_same_name_services_stay_distinct() -> None:
    tools_a = [_tool(f"tool-{index}", _digest(f"a{index}"), _SERVICE_A) for index in range(10)]
    tools_b = [_tool("query", _digest("b0"), _SERVICE_B)]
    payload_a = _service_payload(_SERVICE_A, tools_a)
    payload_b = _service_payload(_SERVICE_B, tools_b)
    catalogs = {
        (_SERVICE_A, 1): {"tools": tools_a},
        (_SERVICE_B, 1): {"tools": tools_b},
    }
    services = {(_SERVICE_A, 1): payload_a, (_SERVICE_B, 1): payload_b}

    resolved = resolve_effective_tools(
        (_role((ResourceRef(_SERVICE_A, 1), ResourceRef(_SERVICE_B, 1))),), services, catalogs
    )

    assert len(resolved) == 1
    role_key = resolved[0]["role_key"]
    tools = cast(list[dict[str, Any]], resolved[0]["tools"])
    assert role_key == "main"
    assert len(tools) == 3  # 十项中仅选两项 + 另一服务一项
    names = sorted(item["name"] for item in tools)
    assert "tool-2" not in names
    query = [item for item in tools if item["name"] == "query"]
    assert len(query) == 1
    assert query[0]["service"] == {"resource_id": str(_SERVICE_B), "revision": 1}
    assert {item["exposed_name"] for item in tools} == {
        exposed_tool_name(_SERVICE_A, "tool-0"),
        exposed_tool_name(_SERVICE_A, "tool-1"),
        exposed_tool_name(_SERVICE_B, "query"),
    }


def test_selection_with_mismatched_schema_digest_is_rejected() -> None:
    tools = [_tool("query", _digest("x"), _SERVICE_A)]
    payload = _service_payload(_SERVICE_A, tools)
    catalogs = {(_SERVICE_A, 1): {"tools": [_tool("query", _digest("y"), _SERVICE_A)]}}
    with pytest.raises(CapabilityError) as error:
        resolve_effective_tools((_role((ResourceRef(_SERVICE_A, 1),)),), {(_SERVICE_A, 1): payload}, catalogs)
    assert error.value.code == "capability_unavailable"


def test_exposed_name_is_stable_truncated_and_collision_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    name = exposed_tool_name(_SERVICE_A, "query")
    assert name.startswith("mcp_")
    assert len(name) == len("mcp_") + 32
    assert name == exposed_tool_name(_SERVICE_A, "query")
    assert name != exposed_tool_name(_SERVICE_B, "query")

    monkeypatch.setattr(capabilities, "exposed_tool_name", lambda service_id, tool_name: "mcp_collision")
    tools_a = [_tool("query", _digest("a"), _SERVICE_A)]
    tools_b = [_tool("query", _digest("b"), _SERVICE_B)]
    services = {
        (_SERVICE_A, 1): _service_payload(_SERVICE_A, tools_a),
        (_SERVICE_B, 1): _service_payload(_SERVICE_B, tools_b),
    }
    catalogs = {(_SERVICE_A, 1): {"tools": tools_a}, (_SERVICE_B, 1): {"tools": tools_b}}
    with pytest.raises(CapabilityError) as error:
        resolve_effective_tools((_role((ResourceRef(_SERVICE_A, 1), ResourceRef(_SERVICE_B, 1))),), services, catalogs)
    assert error.value.code == "exposed_name_collision"


def test_three_mcp_transports_follow_closed_schema() -> None:
    common = {
        "schema_version": 1,
        "name": "示例服务",
        "timeout_seconds": 30,
        "discovery_revision": None,
        "selected_tools": [],
    }
    stdio = {
        **common,
        "kind": "local_stdio",
        "executable": "/opt/mcp",
        "arguments": ["serve"],
        "working_directory": "/opt",
        "env_refs": {},
    }
    streamable = {
        **common,
        "kind": "remote_streamable_http",
        "endpoint": "https://mcp.example.com/tools",
        "auth": {"kind": "none", "refs": {}},
    }
    legacy = {
        **common,
        "kind": "remote_legacy_sse",
        "endpoint": "http://127.0.0.1:8080/sse",
        "auth": {"kind": "bearer", "refs": {"Authorization": {"kind": "keyring", "name": "mcp-token"}}},
    }
    for payload in (stdio, streamable, legacy):
        validate_payload("mcp", json_doc(payload))

    remote_with_stdio_field = {**streamable, "executable": "/opt/mcp"}
    with pytest.raises(Exception) as error:
        validate_payload("mcp", json_doc(remote_with_stdio_field))
    assert "executable" in str(error.value)


# ---------------------------------------------------------------------------
# 服务边界：预检查回、导入重核、零启连
# ---------------------------------------------------------------------------


class _FakeDao:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.inspections: dict[uuid.UUID, tuple[str, dict[str, object], str]] = {}
        self.resources: dict[tuple[uuid.UUID, int], str] = {}
        self.saved: list[tuple[Mutation, str, tuple[tuple[ResourceRef, str], ...], str | None]] = []
        self.discovery: dict[tuple[uuid.UUID, int], dict[str, object]] = {}
        self.operations: dict[uuid.UUID, dict[str, object]] = {}
        self.lifecycle_calls = 0

    async def lookup_skill_inspection(
        self, domain: str, operation_id: uuid.UUID
    ) -> tuple[str, dict[str, object], str] | None:
        return self.inspections.get(operation_id)

    async def save_skill_inspection(
        self,
        domain: str,
        operation_id: uuid.UUID,
        input_digest: str,
        carrier: dict[str, object],
        inspection: str,
    ) -> str:
        self.inspections[operation_id] = (input_digest, carrier, inspection)
        return inspection

    async def read_resource(self, domain: str, ref: ResourceRef) -> str | None:
        return self.resources.get((ref.resource_id, ref.revision))

    async def save_resource_mutation(
        self,
        domain: str,
        mutation: Mutation,
        *,
        kind: str,
        payload: str,
        uses: tuple[tuple[ResourceRef, str], ...],
        blob_prefix: str | None,
    ) -> MutationReceipt:
        self.saved.append((mutation, payload, uses, blob_prefix))
        return MutationReceipt(
            mutation.operation_id, "committed", mutation.resource_id, mutation.expected_revision, None
        )

    async def read_mcp_discovery(self, domain: str, ref: ResourceRef) -> dict[str, object] | None:
        return self.discovery.get((ref.resource_id, ref.revision))

    async def lookup_mcp_operation(self, domain: str, operation_id: uuid.UUID) -> dict[str, object] | None:
        return self.operations.get(operation_id)

    def start(self) -> None:
        self.lifecycle_calls += 1

    def connect(self) -> None:
        self.lifecycle_calls += 1


async def test_role_mutation_records_enabled_and_default_uses_without_unlimited_empty_set(tmp_path: Path) -> None:
    dao = _FakeDao(tmp_path)
    service = AgentService(domain="test-domain", dao=cast(Any, dao), blob_root=tmp_path)
    role_id = uuid.uuid4()

    def role_payload(enabled: list[ResourceRef], defaults: list[ResourceRef]) -> str:
        return json_doc(
            {
                "schema_version": 1,
                "role_key": "main",
                "description": "主角色",
                "prompt_ref": {"resource_id": str(_PROMPT.resource_id), "revision": _PROMPT.revision},
                "model_choice": {"kind": "inherit", "connection_id": None, "scenario_id": None},
                "enabled_skills": [{"resource_id": str(ref.resource_id), "revision": ref.revision} for ref in enabled],
                "default_skills": [{"resource_id": str(ref.resource_id), "revision": ref.revision} for ref in defaults],
                "default_mcp": [],
                "enabled": True,
            }
        )

    receipt = await service.mutate(
        Mutation(
            operation_id=uuid.uuid4(),
            kind="role",
            resource_id=role_id,
            expected_revision=None,
            payload=role_payload([_SKILL_A, _SKILL_B, _SKILL_C], [_SKILL_A, _SKILL_B]),
        )
    )
    assert receipt.status == "committed"
    uses = dao.saved[0][2]
    assert {(ref.resource_id, owner) for ref, owner in uses} == {
        (_SKILL_A.resource_id, "role_enabled"),
        (_SKILL_B.resource_id, "role_enabled"),
        (_SKILL_C.resource_id, "role_enabled"),
        (_SKILL_A.resource_id, "role_default"),
        (_SKILL_B.resource_id, "role_default"),
    }

    empty = await service.mutate(
        Mutation(
            operation_id=uuid.uuid4(),
            kind="role",
            resource_id=role_id,
            expected_revision=1,
            payload=role_payload([], []),
        )
    )
    assert empty.status == "committed"
    assert dao.saved[1][2] == ()


async def test_inspect_skill_replays_same_carrier_and_conflicts_on_change(tmp_path: Path) -> None:
    dao = _FakeDao(tmp_path)
    service = AgentService(domain="test-domain", dao=cast(Any, dao), blob_root=tmp_path)
    package = _write_skill(tmp_path, frontmatter="name: demo\ndescription: 演示")
    operation_id = uuid.uuid4()

    first = await service.inspect_skill(operation_id, str(package), "directory")
    second = await service.inspect_skill(operation_id, str(package), "directory")
    assert first == second

    (package / "notes" / "readme.md").write_text("变更载体", encoding="utf-8")
    with pytest.raises(AgentOperationError) as error:
        await service.inspect_skill(operation_id, str(package), "directory")
    assert error.value.problem.code == "identity_conflict"


async def test_skill_import_publishes_new_version_and_keeps_old_on_changed_carrier(tmp_path: Path) -> None:
    dao = _FakeDao(tmp_path)
    service = AgentService(domain="test-domain", dao=cast(Any, dao), blob_root=tmp_path)
    package = _write_skill(tmp_path, frontmatter="name: demo\ndescription: 演示")
    operation_id = uuid.uuid4()
    inspection = await service.inspect_skill(operation_id, str(package), "directory")
    decoded = decode_json_doc(inspection)
    assert isinstance(decoded, dict)

    mutation = Mutation(
        operation_id=uuid.uuid4(),
        kind="skill_import",
        resource_id=uuid.uuid5(operation_id, "skill"),
        expected_revision=None,
        payload=json_doc(
            {
                "inspection_id": str(operation_id),
                "inspection_digest": decoded["inspection_digest"],
                "expected_resource": None,
            }
        ),
    )
    receipt = await service.mutate(mutation)
    assert receipt.status == "committed"
    saved_payload = dao.saved[0][1]
    parsed = decode_json_doc(saved_payload)
    assert isinstance(parsed, dict) and parsed["name"] == "demo"
    prefix = dao.saved[0][3]
    assert prefix is not None and (tmp_path / prefix / "SKILL.md").is_file()

    (package / "notes" / "readme.md").write_text("导入前被改", encoding="utf-8")
    second = Mutation(
        operation_id=uuid.uuid4(),
        kind="skill_import",
        resource_id=mutation.resource_id,
        expected_revision=1,
        payload=json_doc(
            {
                "inspection_id": str(operation_id),
                "inspection_digest": decoded["inspection_digest"],
                "expected_resource": {"resource_id": str(mutation.resource_id), "revision": 1},
            }
        ),
    )
    with pytest.raises(AgentOperationError) as error:
        await service.mutate(second)
    assert error.value.problem.code == "identity_conflict"
    assert len(dao.saved) == 1


async def test_mcp_tools_mutation_saves_explicit_selection_without_start_or_connect(tmp_path: Path) -> None:
    dao = _FakeDao(tmp_path)
    service = AgentService(domain="test-domain", dao=cast(Any, dao), blob_root=tmp_path)
    tools = [_tool(f"tool-{index}", _digest(f"a{index}"), _SERVICE_A) for index in range(10)]
    payload = _service_payload(_SERVICE_A, tools)
    dao.resources[(_SERVICE_A, 1)] = json_doc(payload)
    revision = payload["discovery_revision"]
    assert isinstance(revision, str)
    dao.discovery[(_SERVICE_A, 1)] = {"discovery_revision": revision, "tools": tools}

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
    assert receipt.revision == 1
    saved = decode_json_doc(dao.saved[0][1])
    assert isinstance(saved, dict)
    assert [item["name"] for item in saved["selected_tools"]] == ["tool-0", "tool-1"]
    assert dao.lifecycle_calls == 0

    bad = Mutation(
        operation_id=uuid.uuid4(),
        kind="mcp_tools",
        resource_id=_SERVICE_A,
        expected_revision=1,
        payload=json_doc(
            {
                "service": {"resource_id": str(_SERVICE_A), "revision": 1},
                "discovery_revision": revision,
                "selected_tools": [{"name": "tool-9", "schema_digest": _digest("a0")}],
            }
        ),
    )
    with pytest.raises(AgentOperationError) as error:
        await service.mutate(bad)
    assert error.value.problem.code == "capability_unavailable"
    assert len(dao.saved) == 1
