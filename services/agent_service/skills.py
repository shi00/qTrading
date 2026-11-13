"""Skill 包预检与版本化只读发布（E-03 / 详细设计 §10.3、§12 D02 阈值）。

合同：
- 四阈值（归档 16MiB / 展开 64MiB / 1024 对象 / 10 秒）在解压前执行，恰等通过；
- 包内路径逃逸（``..`` / 绝对路径 / 盘符 / 反斜杠）拒绝；重复名/加密包拒绝；
- 声明展开字节与实际读出字节都计量（防止中央目录谎报）；
- 包内脚本仅作为数据落盘，**从不执行**；
- 版本目录不可覆盖；安装失败不留下部分目录，旧版本保持可读。

本模块不做网络访问，也不给项目根以外目录写权限（root 由应用组装传入）。
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from core.agent_types import (
    DEFAULT_SKILL_BUDGET,
    AgentValidationError,
    SkillBudget,
    SkillVersionRef,
    require_identity,
)

_READ_CHUNK = 1024 * 1024


def _fail(code: str, path: str) -> NoReturn:
    raise AgentValidationError(code, path)


@dataclass(frozen=True, slots=True)
class SkillFileEntry:
    """包内文件：规范化相对名 + 字节数 + 内容摘要。"""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SkillPackageInspection:
    """预检结果（不含版本发布副作用）。"""

    files: tuple[SkillFileEntry, ...]
    total_expanded_bytes: int
    archive_digest: str


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
    return name


def inspect_skill_package(
    data: bytes,
    *,
    budget: SkillBudget = DEFAULT_SKILL_BUDGET,
    clock: Callable[[], float] = time.monotonic,
) -> SkillPackageInspection:
    """四阈值预检：全部在解压前/逐块读取中执行，返回文件清单与摘要。"""

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
        budget.enforce_objects(len(infos))
        seen: set[str] = set()
        declared = sum(info.file_size for info in infos)
        budget.enforce_expanded_bytes(declared)
        files: list[SkillFileEntry] = []
        total = 0
        for info in infos:
            name = _require_safe_entry_name(info.filename)
            if name in seen:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            seen.add(name)
            if info.flag_bits & 0x1:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            if name.endswith("/"):
                continue
            digest = hashlib.sha256()
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
            except (zipfile.BadZipFile, OSError) as exc:
                raise AgentValidationError("invalid_skill_package", f"$.skill.package.entry[{name}]") from exc
            budget.enforce_elapsed(clock() - started)
            files.append(SkillFileEntry(name=name, size=size, sha256=digest.hexdigest()))
    budget.enforce_elapsed(clock() - started)
    return SkillPackageInspection(
        files=tuple(files),
        total_expanded_bytes=total,
        archive_digest=archive_digest,
    )


def _require_skill_id(skill_id: str) -> str:
    value = require_identity(skill_id, "$.skill.skill_id")
    if any(char in value for char in ("/", "\\", ":", "\x00")) or value in {".", ".."}:
        _fail("invalid_skill_id", "$.skill.skill_id")
    return value


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
    ) -> Path:
        """预检后原子发布：目标版本已存在即拒绝；任何失败清理临时目录。"""

        if inspection is None:
            inspection = inspect_skill_package(data)
        destination = self.version_dir(ref)
        if destination.exists():
            _fail("skill_version_exists", f"$.skill.version[{ref.skill_id}@{ref.version}]")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self._root / ".staging" / f"{ref.skill_id}-{ref.version}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True)
            self._extract(data, inspection, staging)
            os.replace(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination

    def read_file(self, ref: SkillVersionRef, name: str) -> bytes:
        _require_safe_entry_name(name)
        target = self.version_dir(ref).joinpath(*PurePosixPath(name).parts)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise AgentValidationError("invalid_skill_path", f"$.skill.path[{name}]") from exc

    def _extract(self, data: bytes, inspection: SkillPackageInspection, staging: Path) -> None:
        expected = {entry.name: entry for entry in inspection.files}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                name = _require_safe_entry_name(info.filename)
                if name.endswith("/"):
                    staging.joinpath(*PurePosixPath(name).parts).mkdir(parents=True, exist_ok=True)
                    continue
                entry = expected.get(name)
                if entry is None:
                    _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source:
                    payload = source.read(entry.size + 1)
                if len(payload) != entry.size:
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
    return inspection.archive_digest
