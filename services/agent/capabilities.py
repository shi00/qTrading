"""Agent 能力与资产解析（设计正本 §9.6—§9.9 / P01、S02）。

本模块维护型 Prompt 资产、Skill 目录/ZIP 预检、MCP 工具目录与有效工具集合的
纯解析：版本绑定、变量白名单、受管相对路径与暴露名映射。stock 仍委托既有
``get_base_prompt`` 的三级优先级，不复制第二套可编辑副本。不调用模型、不派发
工具、不启动/连接 MCP、不读写业务库（导入发布的文件写入由服务传入受管根）。
"""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import UUID

from core.agent_types import JsonDoc, ResourceRef, json_doc, validate_payload
from core.prompt_base import AGENT_PROMPT_ASSETS, STRATEGY_PROMPTS, get_base_prompt

SKILL_MAX_FILES = 1000
SKILL_MAX_BYTES = 100 * 1024 * 1024

_EXECUTABLE_SUFFIXES = frozenset(
    {".exe", ".com", ".bat", ".cmd", ".msi", ".scr", ".ps1", ".vbs", ".js", ".sh", ".lnk", ".app", ".dll"}
)
_RESTRICTED_LICENSES = frozenset(
    {"proprietary", "all-rights-reserved", "all rights reserved", "restricted", "unknown-restricted"}
)
_BLOCKING_PROBLEMS = frozenset(
    {
        "invalid_relative_path",
        "symbolic_link",
        "executable_entry",
        "duplicate_entry",
        "too_many_files",
        "too_large",
        "missing_skill_md",
        "same_name_different_content",
        "invalid_archive",
        "encrypted_archive",
        "license_restricted",
        "revision_conflict",
    }
)


class SourceProvider(Protocol):
    """业务只读资料提供方（§9.3.2）。

    app 只按固定五个 provider_key 注入；提供方按对象/版本查回并自行验证 as_of，
    返回严格的 PreparedSource。services 只消费本 Protocol，不 import 业务/UI 层。
    """

    async def read_source(self, source_object_key: str, expected_revision: str, as_of: date) -> JsonDoc: ...


class PromptAssetError(ValueError):
    """Prompt 资产键/变量/正文不合法。"""

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


class CapabilityError(ValueError):
    """Skill/MCP 能力预检或有效集合不成立（§9.6/§9.8）。"""

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


@dataclass(frozen=True, slots=True)
class PromptResolution:
    asset_key: str
    version: int
    body: str
    source: str
    allowed_variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillMeta:
    name: str
    description: str
    version_label: str | None
    license_identifier: str | None
    dependencies: tuple[str, ...]
    instructions: str


def resolve_agent_prompt(asset_key: str, variables: Mapping[str, str]) -> PromptResolution:
    """按维护资产声明的变量白名单渲染正文；缺键/未知变量均失败。"""
    asset = AGENT_PROMPT_ASSETS.get(asset_key)
    if asset is None:
        raise PromptAssetError("unknown_asset_key", "asset_key")
    declared = set(asset.allowed_variables)
    supplied = set(variables)
    missing = declared - supplied
    if missing:
        raise PromptAssetError("missing_variable", sorted(missing)[0])
    unknown = supplied - declared
    if unknown:
        raise PromptAssetError("unknown_variable", sorted(unknown)[0])
    for key in declared:
        if not isinstance(variables[key], str):
            raise PromptAssetError("invalid_variable", key)
    return PromptResolution(
        asset_key=asset.asset_key,
        version=asset.version,
        body=asset.body.format_map({key: variables[key] for key in declared}),
        source="asset",
        allowed_variables=asset.allowed_variables,
    )


def get_agent_tutorial() -> PromptResolution:
    """纯帮助资产：无模型变量，不调用模型/工具，不执行任何示例。"""
    return resolve_agent_prompt("agent_tutorial", {})


def resolve_stock_prompt(
    strategy_key: str,
    get_user_prompt: Callable[[str], str | None],
    get_global_prompt: Callable[[], str],
) -> PromptResolution:
    """沿用既有 get_base_prompt 三级优先级，只补充来源标签，不新建副本。

    来源判定：用户自定义且与策略默认同文 → ``legacy_unknown``（无法区分旧迁移
    同文与真实自定义）；真实不同文 → ``custom``；缺用户 → ``default``；缺策略
    → ``global``。
    """
    body = get_base_prompt(strategy_key, get_user_prompt, get_global_prompt)
    user_prompt = get_user_prompt(strategy_key) or ""
    default_prompt = STRATEGY_PROMPTS.get(strategy_key) or ""
    if user_prompt.strip():
        source = (
            "legacy_unknown" if default_prompt.strip() and user_prompt.strip() == default_prompt.strip() else "custom"
        )
    elif default_prompt.strip():
        source = "default"
    else:
        source = "global"
    return PromptResolution(asset_key="stock", version=1, body=body, source=source)


# ---------------------------------------------------------------------------
# Skill 目录/ZIP 预检（§9.6）
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_name(name: str) -> str:
    if not name or name.startswith(("/", "\\")) or "\\" in name or ":" in name:
        raise CapabilityError("invalid_relative_path", "files")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts) or "\x00" in name:
        raise CapabilityError("invalid_relative_path", "files")
    return name


def _is_reparse(path: Path) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & marker)


def _executable_entry(name: str) -> bool:
    return Path(name).suffix.lower() in _EXECUTABLE_SUFFIXES


def _read_directory(path: str) -> tuple[dict[str, bytes], list[tuple[str, str]]]:
    root = Path(path)
    files: dict[str, bytes] = {}
    problems: list[tuple[str, str]] = []
    total = 0
    truncated = False
    for current, directories, filenames in os.walk(root, followlinks=False):
        for name in sorted(directories):
            candidate = Path(current) / name
            if candidate.is_symlink() or _is_reparse(candidate):
                problems.append(("symbolic_link", candidate.relative_to(root).as_posix()))
                directories.remove(name)
        for name in sorted(filenames):
            candidate = Path(current) / name
            relative = candidate.relative_to(root).as_posix()
            try:
                _relative_name(relative)
            except CapabilityError:
                problems.append(("invalid_relative_path", relative))
                continue
            if candidate.is_symlink() or _is_reparse(candidate):
                problems.append(("symbolic_link", relative))
                continue
            if _executable_entry(relative):
                problems.append(("executable_entry", relative))
                continue
            try:
                data = candidate.read_bytes()
            except OSError:
                problems.append(("invalid_archive", relative))
                continue
            total += len(data)
            if len(files) + 1 > SKILL_MAX_FILES:
                problems.append(("too_many_files", "files"))
                truncated = True
                break
            if total > SKILL_MAX_BYTES:
                problems.append(("too_large", "files"))
                truncated = True
                break
            files[relative] = data
        if truncated:
            break
    return files, problems


def _read_zip(path: str) -> tuple[dict[str, bytes], list[tuple[str, str]]]:
    files: dict[str, bytes] = {}
    problems: list[tuple[str, str]] = []
    total = 0
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise CapabilityError("invalid_archive", "path") from None
    with archive:
        seen: set[str] = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name in seen:
                problems.append(("duplicate_entry", name))
                continue
            seen.add(name)
            try:
                _relative_name(name)
            except CapabilityError:
                problems.append(("invalid_relative_path", name))
                continue
            if info.flag_bits & 0x1:
                problems.append(("encrypted_archive", name))
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                problems.append(("symbolic_link", name))
                continue
            if _executable_entry(name):
                problems.append(("executable_entry", name))
                continue
            try:
                data = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile, OSError):
                problems.append(("invalid_archive", name))
                continue
            total += len(data)
            if len(files) + 1 > SKILL_MAX_FILES:
                problems.append(("too_many_files", "files"))
                break
            if total > SKILL_MAX_BYTES:
                problems.append(("too_large", "files"))
                break
            files[name] = data
    return files, problems


def _parse_frontmatter(text: str, fallback_name: str) -> SkillMeta:
    lines = text.splitlines()
    name = fallback_name
    description = ""
    version_label: str | None = None
    license_identifier: str | None = None
    dependencies: list[str] = []
    body_start = 0
    if lines and lines[0].strip() == "---":
        closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
        if closing is not None:
            body_start = closing + 1
            dependency_block = False
            for raw in lines[1:closing]:
                stripped = raw.strip()
                if dependency_block and stripped.startswith("- "):
                    dependencies.append(stripped[2:].strip().strip("'\""))
                    continue
                dependency_block = False
                if ":" not in stripped:
                    continue
                key, value = stripped.split(":", 1)
                key = key.strip().lower()
                value = value.strip().strip("'\"")
                if key == "name" and value:
                    name = value
                elif key == "description":
                    description = value
                elif key == "version" and value:
                    version_label = value
                elif key == "license" and value:
                    license_identifier = value
                elif key == "dependencies":
                    if value.startswith("[") and value.endswith("]"):
                        dependencies = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
                    elif value:
                        dependencies = [value]
                    else:
                        dependency_block = True
    body = "\n".join(lines[body_start:]).strip("\n")
    return SkillMeta(name, description, version_label, license_identifier, tuple(dependencies), body)


def _problem_entry(code: str, field_path: str) -> dict[str, object]:
    return {
        "code": code,
        "message_key": f"agent.{code}",
        "params": json_doc({"field_path": field_path}),
        "related_id": None,
    }


def _license_entry(identifier: str | None) -> dict[str, object]:
    if identifier:
        return {"status": "declared", "identifier": identifier, "text_ref": None}
    return {"status": "unknown", "identifier": None, "text_ref": None}


def _dependency_entries(names: tuple[str, ...]) -> list[dict[str, object]]:
    if not names:
        return [
            {
                "name": "未声明依赖",
                "kind": "capability",
                "constraint": None,
                "status": "unknown",
                "reason": "静态包未声明依赖，不能推断无依赖",
            }
        ]
    entries: list[dict[str, object]] = []
    for name in names:
        entries.append(
            {
                "name": name,
                "kind": "capability" if name.startswith("capability:") else "package",
                "constraint": None,
                "status": "unknown",
                "reason": "静态包声明不执行依赖检查，安装不解决依赖",
            }
        )
    return entries


def _files_manifest(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {"relative_path": name, "bytes": len(data), "digest": _sha256_bytes(data)}
        for name, data in sorted(files.items())
    ]


def inspect_skill_carrier_sync(
    operation_id: UUID,
    path: str,
    kind: str,
    replacing: ResourceRef | None = None,
) -> dict[str, object]:
    """只读预检：不落安装目录、不运行脚本、不安装依赖、不启用角色/连接 MCP。"""
    if kind not in ("directory", "zip"):
        raise CapabilityError("invalid_enum", "kind")
    target = Path(path)
    files, scan_problems = _read_directory(path) if kind == "directory" else _read_zip(path)
    problems = [_problem_entry(code, field) for code, field in scan_problems]

    skills: list[dict[str, object]] = []
    seen_names: dict[str, str] = {}
    for name, data in sorted(files.items()):
        if Path(name).name != "SKILL.md":
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(_problem_entry("invalid_archive", name))
            continue
        fallback = Path(name).parent.name if Path(name).parent.name else target.stem
        meta = _parse_frontmatter(text, fallback)
        content_digest = _sha256_bytes(data)
        if meta.name in seen_names and seen_names[meta.name] != content_digest:
            problems.append(_problem_entry("same_name_different_content", meta.name))
        seen_names[meta.name] = content_digest
        license_entry = _license_entry(meta.license_identifier)
        if license_entry["status"] == "unknown":
            problems.append(_problem_entry("license_unknown", f"skills.{meta.name}.license"))
        if meta.license_identifier and meta.license_identifier.lower() in _RESTRICTED_LICENSES:
            problems.append(_problem_entry("license_restricted", f"skills.{meta.name}.license"))
        dependencies = _dependency_entries(meta.dependencies)
        for dependency in dependencies:
            if dependency["status"] != "satisfied":
                problems.append(_problem_entry("dependency_unknown", f"skills.{meta.name}.dependency_report"))
        skills.append(
            {
                "name": meta.name,
                "description": meta.description,
                "version_label": meta.version_label,
                "content_digest": content_digest,
                "instructions": meta.instructions,
                "license": license_entry,
                "dependencies": dependencies,
            }
        )
    if not skills:
        problems.append(_problem_entry("missing_skill_md", "files"))

    manifest = _files_manifest(files)
    content_digest = hashlib.sha256(json_doc(manifest).encode("utf-8")).hexdigest()
    display_name = str(skills[0]["name"]) if skills else target.stem
    blocking = any(str(item["code"]) in _BLOCKING_PROBLEMS for item in problems)
    inspection: dict[str, object] = {
        "schema_version": 1,
        "inspection_id": str(operation_id),
        "input_kind": kind,
        "display_name": display_name,
        "replacing": None
        if replacing is None
        else {"resource_id": str(replacing.resource_id), "revision": replacing.revision},
        "content_digest": content_digest,
        "files": manifest,
        "skills": skills,
        "problems": problems,
        "can_import": not blocking,
    }
    inspection["inspection_digest"] = hashlib.sha256(json_doc(inspection).encode("utf-8")).hexdigest()
    return inspection


def read_skill_carrier_files(path: str, kind: str) -> dict[str, bytes]:
    """导入前重读同一载体字节；任何阻断问题都拒绝发布。"""
    if kind not in ("directory", "zip"):
        raise CapabilityError("invalid_enum", "kind")
    files, problems = _read_directory(path) if kind == "directory" else _read_zip(path)
    blocking = next(((code, field) for code, field in problems if code in _BLOCKING_PROBLEMS), None)
    if blocking is not None:
        raise CapabilityError(blocking[0], blocking[1])
    return files


def build_skill_payload(inspection: Mapping[str, object], *, enabled: bool | None = None) -> JsonDoc:
    """把预检结果收敛为受管 ``skill`` 资源载荷（不执行任何包/脚本）。"""
    skills = inspection.get("skills")
    if not isinstance(skills, list) or not skills or not isinstance(skills[0], dict):
        raise CapabilityError("missing_skill_md", "skills")
    skill = skills[0]
    license_entry = skill.get("license")
    dependencies = skill.get("dependencies")
    license_status = license_entry.get("status") if isinstance(license_entry, dict) else None
    identifier = str(license_entry.get("identifier") or "") if isinstance(license_entry, dict) else ""
    resolved = enabled
    if resolved is None:
        all_satisfied = isinstance(dependencies, list) and all(
            isinstance(item, dict) and item.get("status") == "satisfied" for item in dependencies
        )
        resolved = license_status == "declared" and identifier.lower() not in _RESTRICTED_LICENSES and all_satisfied
    required_capabilities = (
        [str(item["name"]) for item in dependencies if isinstance(item, dict) and item.get("kind") == "capability"]
        if isinstance(dependencies, list)
        else []
    )
    payload = json_doc(
        {
            "schema_version": 1,
            "name": str(skill.get("name", "")),
            "description": str(skill.get("description", "")),
            "instructions": str(skill.get("instructions", "")),
            "resource_refs": [],
            "compatible_roles": [],
            "required_capabilities": required_capabilities,
            "import_origin": {
                "kind": str(inspection.get("input_kind", "directory")),
                "display_name": str(inspection.get("display_name", "")),
                "content_digest": str(inspection.get("content_digest", "")),
            },
            "license": license_entry,
            "dependency_report": dependencies if isinstance(dependencies, list) else [],
            "enabled": bool(resolved),
        }
    )
    validate_payload("skill", payload)
    return payload


def publish_skill_files(
    blob_root: Path,
    resource_id: UUID,
    revision: int,
    files: Mapping[str, bytes],
) -> str:
    """原子发布到新的受管版本目录；失败清理临时目录并保持旧版本不变。"""
    if revision < 1:
        raise CapabilityError("invalid_range", "revision")
    relative = f"agent/skills/{resource_id}/{revision}"
    target = blob_root / relative
    if target.exists():
        raise CapabilityError("revision_conflict", "revision")
    temporary = blob_root / "agent" / "skills" / f".{resource_id}-{revision}-{os.getpid()}.tmp"
    try:
        for name in files:
            _relative_name(name)
        temporary.mkdir(parents=True, exist_ok=False)
        for name, data in files.items():
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.rename(target)
    except (OSError, CapabilityError):
        _remove_tree(temporary)
        raise
    return relative


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink(missing_ok=True)
    path.rmdir()


# ---------------------------------------------------------------------------
# MCP 工具目录、暴露名与有效工具（§9.8）
# ---------------------------------------------------------------------------


def exposed_tool_name(service_id: UUID, tool_name: str) -> str:
    """固定暴露名：只用稳定服务 UUID 与原工具名，不使用显示名。"""
    material = json_doc({"service_id": str(service_id), "name": tool_name})
    return "mcp_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def compute_discovery_revision(service_id: UUID, tools: Sequence[Mapping[str, object]]) -> str:
    """规范目录/参数 schema/服务身份 digest；不编造供应商序号。"""
    canonical = [
        {
            "name": str(tool.get("name", "")),
            "description": str(tool.get("description", "")),
            "parameters_json": str(tool.get("parameters_json", "")),
            "schema_digest": str(tool.get("schema_digest", "")),
        }
        for tool in tools
    ]
    material = json_doc({"service_id": str(service_id), "tools": canonical})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def tool_catalog_view(
    service_id: UUID,
    tools: Sequence[Mapping[str, object]],
    selected: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """目录 + 显式选择 → 每项 allowed 标记；空选择不等于无限许可。"""
    chosen = {(str(item.get("name", "")), str(item.get("schema_digest", ""))) for item in selected}
    view: list[dict[str, object]] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        digest = str(tool.get("schema_digest", ""))
        allowed = (name, digest) in chosen
        view.append(
            {
                "name": name,
                "description": str(tool.get("description", "")),
                "parameters_json": str(tool.get("parameters_json", "")),
                "schema_digest": digest,
                "exposed_name": exposed_tool_name(service_id, name),
                "allowed": allowed,
                "unavailable_reason": None if allowed else "未被该服务显式选择",
            }
        )
    return view


def selected_tool_entries(
    tools: Sequence[Mapping[str, object]],
    selected: object,
    field_path: str,
) -> list[dict[str, object]]:
    """显式选择必须存在于同发现版本且 schema_digest 一致。"""
    if not isinstance(selected, list):
        raise CapabilityError("invalid_type", field_path)
    available = {str(tool.get("name", "")): str(tool.get("schema_digest", "")) for tool in tools}
    entries: list[dict[str, object]] = []
    names: set[str] = set()
    for item in selected:
        if not isinstance(item, dict):
            raise CapabilityError("invalid_type", field_path)
        name = str(item.get("name", ""))
        digest = str(item.get("schema_digest", ""))
        if name in names:
            raise CapabilityError("duplicate_id", field_path)
        names.add(name)
        if available.get(name) != digest:
            raise CapabilityError("capability_unavailable", field_path)
        entries.append({"name": name, "schema_digest": digest})
    return entries


def resolve_effective_tools(
    roles: tuple[object, ...],
    services: Mapping[tuple[UUID, int], Mapping[str, object]],
    catalogs: Mapping[tuple[UUID, int], Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """按角色绑定的服务版本与显式选择求有效工具；不因选服务而暴露全部发现项。"""
    resolved: list[dict[str, object]] = []
    for role in roles:
        role_key = str(getattr(role, "role_key", ""))
        refs = _dedupe_service_refs(getattr(role, "default_mcp", ()), role_key)
        tools: list[dict[str, object]] = []
        exposed: set[str] = set()
        for ref in refs:
            payload = services.get((ref.resource_id, ref.revision))
            if payload is None:
                raise CapabilityError("capability_unavailable", f"{role_key}.default_mcp")
            try:
                validate_payload("mcp", json_doc(payload))
            except Exception as error:
                raise CapabilityError("capability_unavailable", f"{role_key}.default_mcp") from error
            discovery_revision = payload.get("discovery_revision")
            if discovery_revision is None:
                continue
            catalog = catalogs.get((ref.resource_id, ref.revision))
            if catalog is None or catalog.get("discovery_revision", discovery_revision) != discovery_revision:
                raise CapabilityError("capability_unavailable", f"{role_key}.default_mcp")
            discovered = catalog.get("tools")
            if not isinstance(discovered, list):
                raise CapabilityError("capability_unavailable", f"{role_key}.default_mcp")
            selected = selected_tool_entries(
                discovered, payload.get("selected_tools", []), f"{role_key}.default_mcp.selected_tools"
            )
            for item in selected:
                exposed_name = exposed_tool_name(ref.resource_id, str(item["name"]))
                if exposed_name in exposed:
                    raise CapabilityError("exposed_name_collision", f"{role_key}.default_mcp")
                exposed.add(exposed_name)
                tools.append(
                    {
                        "service": {"resource_id": str(ref.resource_id), "revision": ref.revision},
                        "discovery_revision": str(discovery_revision),
                        "name": str(item["name"]),
                        "schema_digest": str(item["schema_digest"]),
                        "exposed_name": exposed_name,
                    }
                )
        resolved.append({"role_key": role_key, "tools": tools})
    return tuple(resolved)


def _dedupe_service_refs(refs: object, field_path: str) -> tuple[ResourceRef, ...]:
    if not isinstance(refs, (tuple, list)):
        raise CapabilityError("invalid_type", field_path)
    unique: dict[UUID, int] = {}
    ordered: list[ResourceRef] = []
    for ref in refs:
        if not isinstance(ref, ResourceRef):
            raise CapabilityError("invalid_type", field_path)
        if ref.resource_id in unique:
            if unique[ref.resource_id] != ref.revision:
                raise CapabilityError("revision_conflict", field_path)
            continue
        unique[ref.resource_id] = ref.revision
        ordered.append(ref)
    return tuple(ordered)
