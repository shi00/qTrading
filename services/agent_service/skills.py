"""Skill 包预检、SKILL.md 前台校验与版本化只读发布（E-03 / F12 / D02 / §10.3）。

合同：
- 四阈值（归档 16MiB / 展开 64MiB / 1024 对象 / 10 秒）在解压前执行，恰等通过；
- 包内路径逃逸（``..`` / 绝对路径 / 盘符 / 反斜杠）拒绝；重复名/加密包拒绝；
- 声明展开字节与实际读出字节都计量（防止中央目录谎报）；
- 根目录恰好一个 ``SKILL.md``：缺失/嵌套/多候选拒绝并提示拆包（F12）；
- ``SKILL.md`` 前台严格按 Harness 合同解析（``---`` 包裹、description 必填、
  名称 NFKC/≤64/小写字母数字与单连字符、布尔仅 true/false）；
- 未知/缺失许可拒绝；``dependencies`` 必须是合法 Skill 名且去重；
- Harness 行为字段（``allowed-tools``/``tools``/``model`` 等）只记录不实现，
  导入文本不赋权（F12「导入文本不赋权」）；
- 同名判定去首尾空白且不区分大小写（NFKC + casefold）；
- 包内脚本仅作为数据落盘，**从不执行**；
- 版本目录不可覆盖；安装失败不留下部分目录，旧版本保持可读。

本模块不做网络访问，也不给项目根以外目录写权限（root 由应用组装传入）。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import time
import unicodedata
import uuid
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, Protocol

import yaml

from core.agent_types import (
    DEFAULT_SKILL_BUDGET,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    RoleSkillAssignment,
    RoleSkillAssignmentReceipt,
    SkillAssetReceipt,
    SkillAssetRegistration,
    SkillBudget,
    SkillVersionRef,
    json_doc,
    require_identity,
    sha256_hex,
    stable_id,
    validate_role_skill_assignment,
)

_READ_CHUNK = 1024 * 1024

#: 规范 Skill 说明文件名（大小写不敏感比较；Windows 文件系统同样不区分）。
SKILL_MANIFEST_FILENAME = "SKILL.md"

#: 版本目录内的规范内容清单（幂等恢复与防篡改核对；不参与导入内容摘要）。
VERSION_MANIFEST_FILENAME = ".skill-manifest.json"

#: 本项目准入的第三方许可（用户需求 §2 NFR：Apache-2.0/MIT/标准 BSD/LGPL-3.0-only；
#: 其他 BSD 变体须单独核定，未知许可不允许使用）。
ALLOWED_SKILL_LICENSES: frozenset[str] = frozenset(
    {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "LGPL-3.0-only"}
)

#: Harness 接受但不实现行为的 frontmatter 字段（v2.66.0 ``_BEHAVIORAL_FRONTMATTER_FIELDS``）。
BEHAVIORAL_FRONTMATTER_FIELDS: frozenset[str] = frozenset(
    {
        "agent",
        "allowed-tools",
        "argument-hint",
        "arguments",
        "context",
        "dependencies",
        "disallowed-tools",
        "effort",
        "hooks",
        "model",
        "paths",
        "shell",
        "tools",
        "when_to_use",
    }
)

#: Skill 名上限（Harness 合同）。
SKILL_NAME_MAX_LENGTH = 64

#: Harness 名合同：小写字母数字段以单连字符连接（不接受非 ASCII/下划线/空段）。
_SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _fail(code: str, path: str) -> NoReturn:
    raise AgentValidationError(code, path)


class SkillImportStatus(Enum):
    """导入判定结果：新建 / 未变化 / 需确认更新 / 确认更新。"""

    NEW = "new"
    UNCHANGED = "unchanged"
    UPDATE_REQUIRES_CONFIRMATION = "update_requires_confirmation"
    UPDATE = "update"


@dataclass(frozen=True, slots=True)
class SkillFileEntry:
    """包内文件：规范化相对名 + 字节数 + 内容摘要。"""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SkillPackageInspection:
    """预检结果（不含版本发布副作用）。``source_kind`` 区分 ZIP/目录来源。"""

    files: tuple[SkillFileEntry, ...]
    total_expanded_bytes: int
    archive_digest: str | None
    manifest_name: str
    manifest_text: str
    source_kind: str = "zip"
    directories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillImportDecision:
    """同名导入判定：``next_version`` 为需要发布的版本（UNCHANGED 时为既有版）。"""

    status: SkillImportStatus
    next_version: int
    existing_version: int | None


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """``SKILL.md`` 前台解析结果（只含导入所需元数据）。"""

    name: str
    description: str
    license_id: str
    dependencies: tuple[str, ...]
    ignored_behavioral_fields: tuple[str, ...]
    model_invocable: bool
    user_invocable: bool


def _require_safe_entry_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        _fail("skill_path_escape", "$.skill.package.entry")
    if "\\" in name:
        _fail("skill_path_escape", "$.skill.package.entry")
    pure = PurePosixPath(name)
    if pure.is_absolute() or pure.drive or pure.root:
        _fail("skill_path_escape", "$.skill.package.entry")
    # zipfile 会把反斜杠归一化为 "/"，故 "C:/x" 需按盘符前缀拒绝。
    first = pure.parts[0] if pure.parts else ""
    if len(first) == 2 and first[1] == ":" and first[0].isalpha():
        _fail("skill_path_escape", "$.skill.package.entry")
    if any(part in {"..", ""} for part in pure.parts):
        _fail("skill_path_escape", "$.skill.package.entry")
    normalized = "/".join(pure.parts)
    if not normalized:
        _fail("skill_path_escape", "$.skill.package.entry")
    return normalized


def _entry_key(name: str) -> str:
    return "/".join(unicodedata.normalize("NFKC", part).casefold() for part in PurePosixPath(name).parts)


def _enforce_entry_layout(entries: Sequence[tuple[str, bool]]) -> None:
    """规范化后重名 / 文件与目录冲突 / 对象总数（root 不计，隐含目录计一次）。"""

    file_keys: set[str] = set()
    dir_keys: set[str] = set()
    explicit_dirs: set[str] = set()
    for name, is_dir in entries:
        parts = PurePosixPath(name).parts
        keys = [_entry_key(part) for part in parts]
        for index in range(1, len(parts)):
            ancestor = "/".join(keys[:index])
            if ancestor in file_keys:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            dir_keys.add(ancestor)
        full = "/".join(keys)
        if is_dir:
            if full in file_keys or full in explicit_dirs:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            explicit_dirs.add(full)
            dir_keys.add(full)
        else:
            if full in file_keys or full in dir_keys:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            if full == VERSION_MANIFEST_FILENAME.casefold():
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            file_keys.add(full)


def _object_count(entries: Sequence[tuple[str, bool]]) -> int:
    objects: set[str] = set()
    for name, _ in entries:
        parts = PurePosixPath(name).parts
        keys = [_entry_key(part) for part in parts]
        objects.add("/".join(keys))
        for index in range(1, len(parts)):
            objects.add("/".join(keys[:index]))
    return len(objects)


def _finalize_inspection(
    files: Sequence[SkillFileEntry],
    directories: Sequence[str],
    manifest_bytes: bytes,
    *,
    archive_digest: str | None,
    source_kind: str,
) -> SkillPackageInspection:
    candidates = [
        entry.name for entry in files if PurePosixPath(entry.name).name.casefold() == SKILL_MANIFEST_FILENAME.casefold()
    ]
    if not candidates:
        _fail("skill_manifest_missing", "$.skill.package")
    if len(candidates) > 1:
        _fail("skill_multiple_candidates", "$.skill.package")
    manifest_name = candidates[0]
    if len(PurePosixPath(manifest_name).parts) != 1:
        _fail("skill_manifest_not_root", "$.skill.package")
    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentValidationError("invalid_skill_manifest", "$.skill.package.SKILL.md") from exc
    return SkillPackageInspection(
        files=tuple(files),
        total_expanded_bytes=sum(entry.size for entry in files),
        archive_digest=archive_digest,
        manifest_name=manifest_name,
        manifest_text=manifest_text,
        source_kind=source_kind,
        directories=tuple(directories),
    )


def inspect_skill_package(
    data: bytes,
    *,
    budget: SkillBudget = DEFAULT_SKILL_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> SkillPackageInspection:
    """ZIP 四阈值预检 + 根 ``SKILL.md`` 布局校验，返回文件清单与摘要。

    对象数按「文件 + 全部隐含/显式目录（root 不计）」计；规范化后重名、
    文件与目录冲突都拒绝（F12「规范重名/重复路径」）。尺寸/时限在解压前与
    逐块读取中双重计量。
    """

    if not isinstance(data, (bytes, bytearray)):
        _fail("invalid_skill_package", "$.skill.package")
    started = clock()
    budget.enforce_archive_bytes(len(data))
    archive_digest = hashlib.sha256(bytes(data)).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except (zipfile.BadZipFile, ValueError):
        _fail("invalid_skill_package", "$.skill.package")
    with archive:
        infos = archive.infolist()
        entries: list[tuple[str, bool]] = []
        for info in infos:
            name = _require_safe_entry_name(info.filename)
            if info.flag_bits & 0x1:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            entries.append((name, info.filename.endswith("/")))
        _enforce_entry_layout(entries)
        budget.enforce_objects(_object_count(entries))
        declared = sum(info.file_size for info in infos)
        budget.enforce_expanded_bytes(declared)
        files: list[SkillFileEntry] = []
        manifest_bytes = b""
        total = 0
        for (name, is_dir), info in zip(entries, infos, strict=True):
            if is_dir:
                continue
            is_manifest = PurePosixPath(name).name.casefold() == SKILL_MANIFEST_FILENAME.casefold()
            digest = hashlib.sha256()
            payload = bytearray()
            size = 0
            try:
                with archive.open(info) as handle:
                    while True:
                        chunk = handle.read(_READ_CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        budget.enforce_expanded_bytes(total)
                        digest.update(chunk)
                        if is_manifest:
                            payload.extend(chunk)
            except (zipfile.BadZipFile, OSError) as exc:
                raise AgentValidationError("invalid_skill_package", f"$.skill.package.entry[{name}]") from exc
            budget.enforce_elapsed(clock() - started)
            files.append(SkillFileEntry(name=name, size=size, sha256=digest.hexdigest()))
            if is_manifest:
                manifest_bytes = bytes(payload)
    budget.enforce_elapsed(clock() - started)
    return _finalize_inspection(
        files,
        [name for name, is_dir in entries if is_dir],
        manifest_bytes,
        archive_digest=archive_digest,
        source_kind="zip",
    )


def inspect_skill_directory(
    root: str,
    *,
    budget: SkillBudget = DEFAULT_SKILL_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> SkillPackageInspection:
    """本地目录预检：与 ZIP 统一计量；符号链接/特殊文件拒绝，读取按同句柄最终路径核验。"""

    from services.agent_service.skill_workspace import ReadOnlyWorkspace

    started = clock()
    workspace = ReadOnlyWorkspace(root)
    entries: list[tuple[str, bool]] = []
    declared = 0
    stack: list[tuple[str, str]] = [("", workspace.root)]
    while stack:
        prefix, directory = stack.pop()
        budget.enforce_elapsed(clock() - started)
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except OSError as exc:
            raise AgentValidationError("invalid_skill_package", "$.skill.package") from exc
        for child in children:
            budget.enforce_elapsed(clock() - started)
            if child.is_symlink():
                _fail("skill_path_escape", f"$.skill.package.entry[{child.name}]")
            rel = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir(follow_symlinks=False):
                entries.append((rel, True))
                stack.append((rel, child.path))
            elif child.is_file(follow_symlinks=False):
                entries.append((rel, False))
                try:
                    declared += child.stat(follow_symlinks=False).st_size
                except OSError as exc:
                    raise AgentValidationError("invalid_skill_package", f"$.skill.package.entry[{rel}]") from exc
            else:
                _fail("invalid_skill_package", f"$.skill.package.entry[{rel}]")
    _enforce_entry_layout(entries)
    budget.enforce_objects(_object_count(entries))
    budget.enforce_expanded_bytes(declared)
    files: list[SkillFileEntry] = []
    manifest_bytes = b""
    total = 0
    for rel, is_dir in entries:
        if is_dir:
            continue
        manifest_name = PurePosixPath(rel).name.casefold() == SKILL_MANIFEST_FILENAME.casefold()
        digest = hashlib.sha256()
        payload = bytearray()
        size = 0
        for chunk in workspace.iter_bytes(rel, chunk_size=_READ_CHUNK):
            size += len(chunk)
            total += len(chunk)
            budget.enforce_expanded_bytes(total)
            budget.enforce_elapsed(clock() - started)
            digest.update(chunk)
            if manifest_name:
                payload.extend(chunk)
        files.append(SkillFileEntry(name=rel, size=size, sha256=digest.hexdigest()))
        if manifest_name:
            manifest_bytes = bytes(payload)
    budget.enforce_elapsed(clock() - started)
    return _finalize_inspection(
        files,
        [name for name, is_dir in entries if is_dir],
        manifest_bytes,
        archive_digest=None,
        source_kind="directory",
    )


class _UniqueKeyLoader(yaml.BaseLoader):
    """YAML BaseLoader + 重复键拒绝（与 Harness v2.66.0 同一合同）。"""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found a non-scalar key",
                    key_node.start_mark,
                )
            key = key_node.value
            if key in keys:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def _frontmatter_parts(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        _fail("invalid_skill_manifest", "$.skill.manifest")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if closing is None:
        _fail("invalid_skill_manifest", "$.skill.manifest")
    return "\n".join(lines[1:closing]), "\n".join(lines[closing + 1 :])


def _frontmatter_mapping(frontmatter: str) -> dict[str, object]:
    try:
        parsed = yaml.load(frontmatter, Loader=_UniqueKeyLoader)  # BaseLoader 不构造任意对象
    except yaml.YAMLError as exc:
        raise AgentValidationError("invalid_skill_manifest", "$.skill.manifest") from exc
    if not isinstance(parsed, dict):
        _fail("invalid_skill_manifest", "$.skill.manifest")
    return {str(key): value for key, value in parsed.items()}


def _frontmatter_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail("invalid_skill_manifest", f"$.skill.manifest.{field}")
    return value


def _frontmatter_boolean(value: object, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, str) or value.lower() not in {"true", "false"}:
        _fail("invalid_skill_manifest", f"$.skill.manifest.{field}")
    return value.lower() == "true"


def validate_skill_name(name: str) -> str:
    """Harness Skill 名合同：NFKC、≤64、小写字母数字段以单连字符连接。"""

    if not isinstance(name, str):
        _fail("invalid_skill_name", "$.skill.name")
    normalized = unicodedata.normalize("NFKC", name)
    if len(normalized) > SKILL_NAME_MAX_LENGTH or _SKILL_NAME_RE.fullmatch(normalized) is None:
        _fail("invalid_skill_name", "$.skill.name")
    return normalized


def normalize_skill_name(name: str) -> str:
    """同名判定键：去首尾空白 + NFKC + casefold（F12 不区分大小写），不要求 Harness 名合法。"""

    if not isinstance(name, str):
        _fail("invalid_skill_name", "$.skill.name")
    normalized = unicodedata.normalize("NFKC", name.strip()).casefold()
    if not normalized:
        _fail("invalid_skill_name", "$.skill.name")
    return normalized


def _skill_dependencies(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        _fail("invalid_skill_dependency", "$.skill.manifest.dependencies")
    dependencies: list[str] = []
    for index, item in enumerate(value):
        try:
            dependencies.append(validate_skill_name(item if isinstance(item, str) else ""))
        except AgentValidationError as exc:
            raise AgentValidationError("invalid_skill_dependency", f"$.skill.manifest.dependencies[{index}]") from exc
    if len(set(dependencies)) != len(dependencies):
        _fail("invalid_skill_dependency", "$.skill.manifest.dependencies")
    return tuple(dependencies)


def parse_skill_manifest(data: bytes, *, directory_name: str) -> SkillManifest:
    """严格解析 ``SKILL.md``：前台键、名称、许可、依赖与布尔拼写。"""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentValidationError("invalid_skill_manifest", "$.skill.manifest") from exc
    frontmatter, _ = _frontmatter_parts(text)
    mapping = _frontmatter_mapping(frontmatter)
    raw_name = _frontmatter_text(mapping.get("name"), "name")
    try:
        name = validate_skill_name(raw_name if raw_name is not None else directory_name)
    except AgentValidationError as exc:
        raise AgentValidationError("invalid_skill_name", "$.skill.name") from exc
    description = _frontmatter_text(mapping.get("description"), "description")
    if description is None or not description.strip():
        _fail("invalid_skill_manifest", "$.skill.manifest.description")
    license_id = _frontmatter_text(mapping.get("license"), "license")
    if license_id is None or license_id not in ALLOWED_SKILL_LICENSES:
        _fail("skill_license_unknown", "$.skill.manifest.license")
    dependencies = _skill_dependencies(mapping.get("dependencies"))
    ignored = tuple(sorted(key for key in mapping if key in BEHAVIORAL_FRONTMATTER_FIELDS))
    return SkillManifest(
        name=name,
        description=description.strip(),
        license_id=license_id,
        dependencies=dependencies,
        ignored_behavioral_fields=ignored,
        model_invocable=not _frontmatter_boolean(
            mapping.get("disable-model-invocation"), "disable-model-invocation", default=False
        ),
        user_invocable=_frontmatter_boolean(mapping.get("user-invocable"), "user-invocable", default=True),
    )


def content_digest_of_files(files: Sequence[SkillFileEntry]) -> str:
    """规范内容摘要：对文件顺序不敏感，覆盖全部路径/字节数/内容摘要（F12 同名判定）。"""

    manifest = [[entry.name, entry.size, entry.sha256] for entry in sorted(files, key=lambda entry: entry.name)]
    return sha256_hex(json_doc({"schema_version": 1, "files": manifest}))


def decide_skill_import(
    existing: Sequence[tuple[int, str]],
    *,
    content_digest: str,
    confirm_update: bool,
) -> SkillImportDecision:
    """同名导入判定：同内容未变化；不同内容先确认，确认后追加新版本。"""

    if not existing:
        return SkillImportDecision(status=SkillImportStatus.NEW, next_version=1, existing_version=None)
    ordered = sorted(existing)
    latest_version, latest_digest = ordered[-1]
    if latest_digest == content_digest:
        return SkillImportDecision(
            status=SkillImportStatus.UNCHANGED,
            next_version=latest_version,
            existing_version=latest_version,
        )
    if not confirm_update:
        return SkillImportDecision(
            status=SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION,
            next_version=latest_version + 1,
            existing_version=latest_version,
        )
    return SkillImportDecision(
        status=SkillImportStatus.UPDATE,
        next_version=latest_version + 1,
        existing_version=latest_version,
    )


def _require_skill_id(skill_id: str) -> str:
    value = require_identity(skill_id, "$.skill.skill_id")
    if any(char in value for char in ("/", "\\", ":", "\x00")) or value in {".", ".."}:
        _fail("invalid_skill_id", "$.skill.skill_id")
    return value


def _enforce_deadline(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() > deadline:
        _fail("skill_time_exceeded", "$.skill.elapsed_s")


def _write_version_manifest(staging: Path, inspection: SkillPackageInspection) -> None:
    payload = {
        "schema_version": 1,
        "source_kind": inspection.source_kind,
        "content_digest": content_digest_of_files(inspection.files),
        "files": [[entry.name, entry.size, entry.sha256] for entry in inspection.files],
        "directories": list(inspection.directories),
    }
    (staging / VERSION_MANIFEST_FILENAME).write_text(json_doc(payload), encoding="utf-8")


def _version_matches(destination: Path, inspection: SkillPackageInspection) -> bool:
    try:
        stored = json.loads((destination / VERSION_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(stored, dict)
        and stored.get("content_digest") == content_digest_of_files(inspection.files)
        and stored.get("files") == [[entry.name, entry.size, entry.sha256] for entry in inspection.files]
    )


class ManagedSkillStore:
    """受管 Skill 版本目录：不可变版本 + 原子发布（失败不残留）。"""

    def __init__(self, root: str) -> None:
        if not isinstance(root, str) or not root or not Path(root).is_absolute():
            _fail("invalid_workspace_root", "$.skill.root")
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            _fail("invalid_workspace_root", "$.skill.root")

    @property
    def root(self) -> Path:
        return self._root

    def version_dir(self, ref: SkillVersionRef) -> Path:
        ref.stable_key()  # 触发身份/版本校验
        return self._root / _require_skill_id(ref.skill_id) / str(ref.version)

    def current_version(self, skill_id: str) -> int | None:
        base = self._root / _require_skill_id(skill_id)
        if not base.is_dir():
            return None
        versions = [int(child.name) for child in base.iterdir() if child.is_dir() and child.name.isdigit()]
        return max(versions) if versions else None

    def publish_package(
        self,
        ref: SkillVersionRef,
        data: bytes,
        inspection: SkillPackageInspection | None = None,
        *,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> Path:
        """预检后原子发布：目标版本已存在且内容相同视为幂等恢复，异内容拒绝。"""

        if inspection is None:
            inspection = inspect_skill_package(data)
        destination = self.version_dir(ref)
        if destination.exists():
            if _version_matches(destination, inspection):
                return destination
            _fail("skill_version_exists", f"$.skill.version[{ref.skill_id}@{ref.version}]")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self._root / ".staging" / f"{ref.skill_id}-{ref.version}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True)
            self._extract(data, inspection, staging, deadline=deadline, clock=clock)
            _write_version_manifest(staging, inspection)
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination

    def publish_directory(
        self,
        ref: SkillVersionRef,
        root: str,
        inspection: SkillPackageInspection,
        *,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> Path:
        """本地目录原子发布：逐文件回读校验摘要与字节数，失败清理暂存。"""

        from services.agent_service.skill_workspace import ReadOnlyWorkspace

        destination = self.version_dir(ref)
        if destination.exists():
            if _version_matches(destination, inspection):
                return destination
            _fail("skill_version_exists", f"$.skill.version[{ref.skill_id}@{ref.version}]")
        workspace = ReadOnlyWorkspace(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self._root / ".staging" / f"{ref.skill_id}-{ref.version}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True)
            for directory in sorted(inspection.directories, key=lambda name: len(PurePosixPath(name).parts)):
                _enforce_deadline(deadline, clock)
                staging.joinpath(*PurePosixPath(directory).parts).mkdir(parents=True, exist_ok=True)
            for entry in inspection.files:
                _enforce_deadline(deadline, clock)
                target = staging.joinpath(*PurePosixPath(entry.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with target.open("wb") as handle:
                    for chunk in workspace.iter_bytes(entry.name, chunk_size=_READ_CHUNK):
                        _enforce_deadline(deadline, clock)
                        size += len(chunk)
                        digest.update(chunk)
                        handle.write(chunk)
                if size != entry.size or digest.hexdigest() != entry.sha256:
                    _fail("invalid_skill_package", f"$.skill.package.entry[{entry.name}]")
            _write_version_manifest(staging, inspection)
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination

    def read_file(self, ref: SkillVersionRef, name: str) -> bytes:
        normalized = _require_safe_entry_name(name)
        target = self.version_dir(ref).joinpath(*PurePosixPath(normalized).parts)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise AgentValidationError("invalid_skill_path", f"$.skill.path[{name}]") from exc

    def remove_version(self, ref: SkillVersionRef) -> None:
        """补偿删除：仅用于「刚发布成功但登记失败」的本进程版本，不用于历史版本卸载。"""

        shutil.rmtree(self.version_dir(ref), ignore_errors=True)

    def _extract(
        self,
        data: bytes,
        inspection: SkillPackageInspection,
        staging: Path,
        *,
        deadline: float | None,
        clock: Callable[[], float],
    ) -> None:
        expected = {entry.name: entry for entry in inspection.files}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                _enforce_deadline(deadline, clock)
                name = _require_safe_entry_name(info.filename)
                if info.filename.endswith("/"):
                    staging.joinpath(*PurePosixPath(name).parts).mkdir(parents=True, exist_ok=True)
                    continue
                entry = expected.get(name)
                if entry is None:
                    _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = bytearray()
                with archive.open(info) as source:
                    while True:
                        _enforce_deadline(deadline, clock)
                        chunk = source.read(_READ_CHUNK)
                        if not chunk:
                            break
                        payload.extend(chunk)
                        if len(payload) > entry.size:
                            _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
                if len(payload) != entry.size or hashlib.sha256(payload).hexdigest() != entry.sha256:
                    _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
                target.write_bytes(payload)

    def list_versions(self, skill_id: str) -> tuple[int, ...]:
        base = self._root / _require_skill_id(skill_id)
        if not base.is_dir():
            return ()
        return tuple(sorted(int(child.name) for child in base.iterdir() if child.is_dir() and child.name.isdigit()))


def install_skill_version(store: ManagedSkillStore, ref: SkillVersionRef, data: bytes) -> str:
    """安装入口：预检 → 原子发布；返回归档摘要（失败不改变旧版本）。"""

    inspection = inspect_skill_package(data)
    store.publish_package(ref, data, inspection)
    if inspection.archive_digest is None:
        _fail("invalid_skill_package", "$.skill.package")
    return inspection.archive_digest


# ---------------------------------------------------------------------------
# E-03-4b 导入编排与角色版本引用（F12/F13；管理入口留给 F 系列 UI）
# ---------------------------------------------------------------------------


class SkillCatalogPort(Protocol):
    """Skill 资产持久化端口（生产为 ``AgentDao``；测试可注入替身）。"""

    async def list_skill_versions(self, *, user_scope: str, skill_id: str) -> tuple[tuple[int, str], ...]: ...

    async def has_skill(self, *, user_scope: str, skill_id: str) -> bool: ...

    async def register_skill_asset(self, request: SkillAssetRegistration) -> SkillAssetReceipt: ...

    async def save_role_skill_assignment(
        self,
        assignment: RoleSkillAssignment,
        *,
        user_scope: str,
        display_name: str,
    ) -> RoleSkillAssignmentReceipt: ...


@dataclass(frozen=True, slots=True)
class SkillPackageImportRequest:
    """用户明确导入一个目录/ZIP Skill：``data`` 与 ``directory`` 恰一；更新需显式确认。"""

    user_scope: str
    directory_name: str
    data: bytes | None = None
    directory: str | None = None
    confirm_update: bool = False


@dataclass(frozen=True, slots=True)
class SkillImportReceipt:
    """导入回执：状态/版本/内容摘要与依赖缺口；未确认更新时不发布、不落库。"""

    status: SkillImportStatus
    user_scope: str
    skill_id: str
    version: int
    display_name: str
    content_digest: str
    license_id: str
    file_count: int
    expanded_bytes: int
    dependencies: tuple[str, ...]
    missing_dependencies: tuple[str, ...]
    requires_confirmation: bool
    source_kind: str = "zip"


async def _run_blocking(func: Callable[..., Any], /, *args: Any) -> Any:
    from utils.thread_pool import TaskType, ThreadPoolManager

    return await ThreadPoolManager().run_async(TaskType.IO, func, *args)


class SkillCatalogService:
    """Skill 导入/角色分配应用服务：复用 ``ManagedSkillStore`` 与 ``AgentDao``，不另立存储。"""

    def __init__(
        self,
        store: ManagedSkillStore,
        dao: SkillCatalogPort,
        *,
        blocking_runner: Callable[..., Any] = _run_blocking,
        budget: SkillBudget = DEFAULT_SKILL_BUDGET,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._dao = dao
        self._run = blocking_runner
        self._budget = budget
        self._clock = clock

    async def import_package(self, request: SkillPackageImportRequest) -> SkillImportReceipt:
        """预检（ZIP/目录统一计量）→ 同名判定 → （发布 + 登记）。

        预检、前台解析与暂存共用同一 10 秒预算；解析与暂存都在受管线程执行，
        不在事件循环线程同步阻塞（R16）。同内容返回未变化；不同内容默认需确认。
        """

        identity = f"{request.user_scope}/skill/{request.directory_name}"
        started = self._clock()
        deadline = started + self._budget.max_preflight_seconds
        try:
            require_identity(request.user_scope, "$.skill.user_scope")
            require_identity(request.directory_name, "$.skill.directory_name")
            if (request.data is None) == (request.directory is None):
                _fail("invalid_skill_source", "$.skill.package")
            if request.directory is not None and not os.path.isabs(request.directory):
                _fail("invalid_skill_source", "$.skill.package")
            if request.data is not None:
                inspection = await self._run(
                    partial(inspect_skill_package, budget=self._budget, clock=self._clock),
                    request.data,
                )
            else:
                inspection = await self._run(
                    partial(inspect_skill_directory, budget=self._budget, clock=self._clock),
                    request.directory,
                )
            remaining = deadline - self._clock()
            if remaining <= 0:
                _fail("skill_time_exceeded", "$.skill.elapsed_s")
            manifest = await asyncio.wait_for(
                self._run(
                    partial(parse_skill_manifest, directory_name=request.directory_name),
                    inspection.manifest_text.encode("utf-8"),
                ),
                timeout=remaining,
            )
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        except TimeoutError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "skill_time_exceeded", identity) from exc
        normalized_name = normalize_skill_name(manifest.name)
        skill_id = stable_id("skill", [request.user_scope, normalized_name])
        content_digest = content_digest_of_files(inspection.files)
        existing = await self._dao.list_skill_versions(user_scope=request.user_scope, skill_id=skill_id)
        decision = decide_skill_import(existing, content_digest=content_digest, confirm_update=request.confirm_update)
        missing_dependencies = await self._missing_dependencies(request.user_scope, manifest.dependencies)
        receipt = SkillImportReceipt(
            status=decision.status,
            user_scope=request.user_scope,
            skill_id=skill_id,
            version=decision.next_version,
            display_name=manifest.name,
            content_digest=content_digest,
            license_id=manifest.license_id,
            file_count=len(inspection.files),
            expanded_bytes=inspection.total_expanded_bytes,
            dependencies=manifest.dependencies,
            missing_dependencies=missing_dependencies,
            requires_confirmation=decision.status is SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION,
            source_kind=inspection.source_kind,
        )
        if decision.status in {SkillImportStatus.UNCHANGED, SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION}:
            return receipt
        ref = SkillVersionRef(skill_id=skill_id, version=decision.next_version)
        try:
            if request.data is not None:
                await self._run(
                    partial(self._store.publish_package, deadline=deadline, clock=self._clock),
                    ref,
                    request.data,
                    inspection,
                )
            else:
                await self._run(
                    partial(self._store.publish_directory, deadline=deadline, clock=self._clock),
                    ref,
                    request.directory,
                    inspection,
                )
        except AgentValidationError as exc:
            if exc.code == "skill_version_exists":
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT, "skill version already exists", identity
                ) from exc
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        except OSError as exc:
            raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "skill publish failed", identity) from exc
        try:
            await self._dao.register_skill_asset(
                SkillAssetRegistration(
                    user_scope=request.user_scope,
                    skill_id=skill_id,
                    version=ref.version,
                    display_name=manifest.name,
                    content_digest=content_digest,
                    normalized_name=normalized_name,
                    license_id=manifest.license_id,
                    archive_digest=inspection.archive_digest,
                    file_count=len(inspection.files),
                    expanded_bytes=inspection.total_expanded_bytes,
                    source_kind=inspection.source_kind,
                    dependencies=manifest.dependencies,
                    missing_dependencies=missing_dependencies,
                    ignored_behavioral_fields=manifest.ignored_behavioral_fields,
                )
            )
        except BaseException:
            # 登记失败不回滚已原子发布的目录：删除本次新版本（旧版本不受影响）后传播。
            await self._run(self._store.remove_version, ref)
            raise
        return receipt

    async def assign_role_skills(
        self,
        assignment: RoleSkillAssignment,
        *,
        user_scope: str,
        display_name: str,
    ) -> RoleSkillAssignmentReceipt:
        """保存角色版本对准确 Skill 版本的引用；默认⊆启用由 core 合同校验。"""

        identity = f"{user_scope}/{assignment.role_id}@{assignment.role_version}"
        try:
            require_identity(user_scope, "$.role.user_scope")
            require_identity(display_name, "$.role.display_name")
            validate_role_skill_assignment(assignment)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        return await self._dao.save_role_skill_assignment(assignment, user_scope=user_scope, display_name=display_name)

    async def _missing_dependencies(self, user_scope: str, dependencies: Sequence[str]) -> tuple[str, ...]:
        missing: list[str] = []
        for dependency in dependencies:
            dependency_id = stable_id("skill", [user_scope, normalize_skill_name(dependency)])
            if not await self._dao.has_skill(user_scope=user_scope, skill_id=dependency_id):
                missing.append(dependency)
        return tuple(missing)
