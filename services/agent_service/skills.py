"""Skill 包预检、SKILL.md 前台校验与版本化只读发布（E-03 / F12 / D02 / §10.3）。

合同：
- 四阈值（归档 16MiB / 展开 64MiB / 1024 对象 / 10 秒）在解压前执行，恰等通过；
  目录来源逐条流式枚举、入账即判定（超一即停）。目录无压缩，「归档体积」上限
  （压缩包体积）不适用于目录：目录按文件尺寸总和走展开体积上限（64MiB），
  不与 ZIP 归档上限混用；
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
- 版本目录不可覆盖；安装失败不留下部分目录，旧版本保持可读；
- 预检/解析/暂存都在 worker 线程执行，并在分块边界检查合作取消标志（可中断）；
- 发布前门与取消共用同一把锁：取消已置位则不执行 ``os.replace``（零迟到发布）；
- waiter 取消/超时后确认 worker 退出，未确认则显式标 ``UNKNOWN``（不假装已停）。

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
import threading
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
    SkillUninstallReceipt,
    SkillVersionRef,
    SkillVersionView,
    json_doc,
    require_identity,
    resolve_skill_selection,
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
    }
)

#: Skill 名上限（Harness 合同）。
SKILL_NAME_MAX_LENGTH = 64

#: Harness 名合同：小写字母数字段以单连字符连接（不接受非 ASCII/下划线/空段）。
_SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: Win32 设备名（不带扩展名也保留；含控制台伪设备与上标数字端口）。
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }
)

#: Win32 文件名禁用字符（含 ADS 分隔符 ``:``）。
_WINDOWS_INVALID_CHARS: frozenset[str] = frozenset('<>:"|?*')


def _fail(code: str, path: str) -> NoReturn:
    raise AgentValidationError(code, path)


class SkillImportStatus(Enum):
    """导入判定结果：新建 / 未变化 / 需确认更新 / 确认更新。"""

    NEW = "new"
    UNCHANGED = "unchanged"
    UPDATE_REQUIRES_CONFIRMATION = "update_requires_confirmation"
    UPDATE = "update"


class SkillOperationState(Enum):
    """导入操作的结果事实：发布事实与 worker 退出事实分开记录，不把「不知道」当成功。"""

    RUNNING = "running"
    ABORTED = "aborted"
    PUBLISHED = "published"
    UNKNOWN = "unknown"


class SkillOperationTicket:
    """单次导入操作的票据：归属登记 + 合作取消 + 退出事实 + 发布前门。

    合同：
    - ``owner`` 登记谁发起本次预检/发布（身份 + 操作号），撤销只作用于本次操作；
    - ``checkpoint()`` 由 worker 在分块/逐条目边界调用，取消已置位则中止长段
      （ZIP 读取、摘要、暂存写入），使不可中断段变成可中断；
    - ``publish()`` 是发布前门，与 ``cancel()`` 共用同一把锁：取消已置位时**不执行**
      替换（零迟到发布）；替换成功后状态为 ``PUBLISHED``，供 waiter 判定是否需撤销；
    - ``finish()`` 由 worker 退出时置位，``wait_exit()`` 供 waiter 在取消/超时后确认
      退出；确认不到时由 waiter 显式标 ``UNKNOWN``，不假装已停。
    - ``adopt()`` / ``mark_registered()`` 记录「复用既有目录」「登记成功」事实：补偿删除
      只对「本次创建且确认未登记」成立，复用他人版本永不删除。
    """

    def __init__(self, owner: str) -> None:
        self._owner = owner
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._exited = threading.Event()
        self._state = SkillOperationState.RUNNING
        self._destination: Path | None = None
        self._claimed: SkillVersionRef | None = None
        self._claim_user_scope: str | None = None
        self._reused: Path | None = None
        self._registered = False
        self._workers_started = 0

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def state(self) -> SkillOperationState:
        with self._lock:
            return self._state

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def exited(self) -> bool:
        """worker 是否已确认退出（退出事实，与发布事实独立）。"""

        return self._exited.is_set()

    @property
    def published_destination(self) -> Path | None:
        with self._lock:
            return self._destination

    def cancel(self) -> None:
        """置位取消标志；与发布前门互斥，返回后替换要么已完成、要么不会再发生。"""

        with self._lock:
            self._cancel.set()

    def claim(self, ref: SkillVersionRef, *, user_scope: str) -> None:
        """归属登记：本次操作拥有的版本目录 + 核实登记事实所需的用户域（撤销只作用于此）。"""

        with self._lock:
            self._claimed = ref
            self._claim_user_scope = user_scope

    @property
    def claimed(self) -> SkillVersionRef | None:
        with self._lock:
            return self._claimed

    @property
    def claim_user_scope(self) -> str | None:
        """核实登记事实所需的用户域（回执丢失时向持久层查该版本号是否已登记）。"""

        with self._lock:
            return self._claim_user_scope

    def adopt(self, destination: Path) -> None:
        """记录「复用既有版本目录」事实：目录非本次产生，补偿/撤销永不删除它。"""

        with self._lock:
            self._reused = destination

    @property
    def reused_destination(self) -> Path | None:
        with self._lock:
            return self._reused

    def mark_registered(self) -> None:
        """记录登记成功事实：已登记的版本不再属于可补偿删除的产物。"""

        with self._lock:
            self._registered = True

    @property
    def registered(self) -> bool:
        with self._lock:
            return self._registered

    def begin_worker(self) -> None:
        """标记有新 worker 已提交：此前的退出事实作废，直到 ``finish()``。"""

        with self._lock:
            self._workers_started += 1
            self._exited.clear()

    @property
    def worker_started(self) -> bool:
        """是否提交过 worker（未提交时无需等待退出事实）。"""

        return self._workers_started > 0

    def checkpoint(self) -> None:
        """worker 分块边界调用：取消已置位则中止本次操作。"""

        if self._cancel.is_set():
            raise AgentValidationError("skill_cancelled", "$.skill.cancelled")

    def publish(self, replace: Callable[[], Path], *, destination: Path) -> Path:
        """发布前门（原子）：取消已置位则跳过替换并抛错，绝不迟到发布。"""

        with self._lock:
            if self._cancel.is_set():
                self._state = SkillOperationState.ABORTED
                raise AgentValidationError("skill_cancelled", "$.skill.cancelled")
            result = replace()
            self._destination = destination
            self._state = SkillOperationState.PUBLISHED
            return result

    def finish(self) -> None:
        """worker 退出：置位退出事实；取消且无发布时把 RUNNING/UNKNOWN 收敛为 ``ABORTED``。"""

        with self._lock:
            if (
                self._state in {SkillOperationState.RUNNING, SkillOperationState.UNKNOWN}
                and self._destination is None
                and self._cancel.is_set()
            ):
                self._state = SkillOperationState.ABORTED
            self._exited.set()

    def mark_aborted(self) -> None:
        with self._lock:
            self._state = SkillOperationState.ABORTED
            self._destination = None

    def mark_unknown(self) -> None:
        """显式记录「无法确认 worker 已退出」，不把它当作已停或已发布。"""

        with self._lock:
            self._state = SkillOperationState.UNKNOWN

    def wait_exit(self, timeout: float) -> bool:
        """在 worker 线程等待退出事实（不得在事件循环线程直接调用）。"""

        return self._exited.wait(timeout)


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
    #: 2.77.0 #13678：``when_to_use`` 由 Harness 原生识别，不再当作未知行为字段；
    #: 本层原样保留供目录/提示展示，不参与导入赋权。
    when_to_use: str | None = None


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
    for part in pure.parts:
        # Win32 会剥离尾随空格/点、把 ":" 当 ADS 分隔符；统一在预检拒绝，
        # 避免「预检名 ≠ 落盘名」造成覆盖或隐藏内容（F12 规范重名）。
        if part != part.rstrip(" ."):
            _fail("skill_path_escape", "$.skill.package.entry")
        if any(character in _WINDOWS_INVALID_CHARS or ord(character) < 32 for character in part):
            _fail("skill_path_escape", "$.skill.package.entry")
        if part.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
            _fail("skill_path_escape", "$.skill.package.entry")
    normalized = "/".join(pure.parts)
    if not normalized:
        _fail("skill_path_escape", "$.skill.package.entry")
    return normalized


def _entry_key(name: str) -> str:
    return "/".join(unicodedata.normalize("NFKC", part).casefold() for part in PurePosixPath(name).parts)


class _EntryLayoutTracker:
    """增量条目账本：规范化后重名 / 文件与目录冲突 + 对象集合（root 不计，隐含目录计一次）。

    每 ``add`` 一条即完成判定与对象计数，目录流式枚举因此可「超一即停」；ZIP 侧批量
    入账复用同一实现，避免两套判据漂移。
    """

    def __init__(self) -> None:
        self._file_keys: set[str] = set()
        self._dir_keys: set[str] = set()
        self._explicit_dirs: set[str] = set()
        self._objects: set[str] = set()

    @property
    def object_count(self) -> int:
        return len(self._objects)

    def add(self, name: str, is_dir: bool) -> None:
        parts = PurePosixPath(name).parts
        keys = [_entry_key(part) for part in parts]
        for index in range(1, len(parts)):
            ancestor = "/".join(keys[:index])
            if ancestor in self._file_keys:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            self._dir_keys.add(ancestor)
            self._objects.add(ancestor)
        full = "/".join(keys)
        self._objects.add(full)
        if is_dir:
            if full in self._file_keys or full in self._explicit_dirs:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            if full == VERSION_MANIFEST_FILENAME.casefold():
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            self._explicit_dirs.add(full)
            self._dir_keys.add(full)
        else:
            if full in self._file_keys or full in self._dir_keys:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            if full == VERSION_MANIFEST_FILENAME.casefold():
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            self._file_keys.add(full)


def _enforce_entry_layout(entries: Sequence[tuple[str, bool]]) -> int:
    """批量入账并返回对象总数（root 不计，隐含目录计一次）。"""

    tracker = _EntryLayoutTracker()
    for name, is_dir in entries:
        tracker.add(name, is_dir)
    return tracker.object_count


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
    checkpoint: Callable[[], None] | None = None,
) -> SkillPackageInspection:
    """ZIP 四阈值预检 + 根 ``SKILL.md`` 布局校验，返回文件清单与摘要。

    对象数按「文件 + 全部隐含/显式目录（root 不计）」计；规范化后重名、
    文件与目录冲突都拒绝（F12「规范重名/重复路径」）。尺寸/时限在解压前与
    逐块读取中双重计量；``checkpoint`` 在每个条目与每个读取块之前调用，
    供合作取消中断长段。
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
            if checkpoint is not None:
                checkpoint()
            name = _require_safe_entry_name(info.filename)
            if info.flag_bits & 0x1:
                _fail("invalid_skill_package", f"$.skill.package.entry[{name}]")
            entries.append((name, info.filename.endswith("/")))
        budget.enforce_objects(_enforce_entry_layout(entries))
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
                        if checkpoint is not None:
                            checkpoint()
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
    checkpoint: Callable[[], None] | None = None,
) -> SkillPackageInspection:
    """本地目录预检：与 ZIP 统一计量；符号链接/特殊文件拒绝，读取按同句柄最终路径核验。

    目录按 ``os.scandir`` 逐条流式枚举，条目入账（重名/冲突 + 对象数含隐含目录）即判定，
    超限立即停止，不再枚举剩余条目；目录无压缩，文件尺寸总和即展开体积，按
    ``max_expanded_bytes``（64MiB）判定——**不**套用只约束压缩包体积的
    ``max_archive_bytes``（16MiB），否则未压缩目录的内容上限与同一内容的 ZIP
    不一致。``checkpoint`` 在每个目录/条目与每个读取块之前调用，供合作取消中断长段。
    """

    from services.agent_service.skill_workspace import ReadOnlyWorkspace

    started = clock()
    workspace = ReadOnlyWorkspace(root)
    tracker = _EntryLayoutTracker()
    entries: list[tuple[str, bool]] = []
    declared = 0
    stack: list[tuple[str, str]] = [("", workspace.root)]
    while stack:
        prefix, directory = stack.pop()
        if checkpoint is not None:
            checkpoint()
        budget.enforce_elapsed(clock() - started)
        try:
            with os.scandir(directory) as iterator:
                for child in iterator:
                    if checkpoint is not None:
                        checkpoint()
                    budget.enforce_elapsed(clock() - started)
                    if child.is_symlink():
                        _fail("skill_path_escape", f"$.skill.package.entry[{child.name}]")
                    rel = _require_safe_entry_name(f"{prefix}/{child.name}" if prefix else child.name)
                    is_dir = child.is_dir(follow_symlinks=False)
                    if not is_dir and not child.is_file(follow_symlinks=False):
                        _fail("invalid_skill_package", f"$.skill.package.entry[{rel}]")
                    tracker.add(rel, is_dir)
                    entries.append((rel, is_dir))
                    # 对象数（含隐含目录）在条目入账后立即判定：1025 个即停，不继续枚举。
                    budget.enforce_objects(tracker.object_count)
                    if is_dir:
                        stack.append((rel, child.path))
                        continue
                    try:
                        declared += child.stat(follow_symlinks=False).st_size
                    except OSError as exc:
                        raise AgentValidationError("invalid_skill_package", f"$.skill.package.entry[{rel}]") from exc
                    # 目录无压缩：文件尺寸总和即展开体积，按展开上限逐条判定（超一即停）。
                    budget.enforce_expanded_bytes(declared)
        except OSError as exc:
            raise AgentValidationError("invalid_skill_package", "$.skill.package") from exc
    entries.sort(key=lambda entry: entry[0])
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
            if checkpoint is not None:
                checkpoint()
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
        when_to_use=_frontmatter_text(mapping.get("when_to_use"), "when_to_use"),
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


def _apply_layout_decision(
    decision: SkillImportDecision,
    *,
    incoming_directories: Sequence[str],
    published_directories: tuple[str, ...] | None,
    confirm_update: bool,
) -> SkillImportDecision:
    """I37：内容摘要相同还不够——目录布局（含空目录）一致才判 ``UNCHANGED``。

    ``published_directories is None``（旧版本的清单缺目录字段）时退回内容摘要语义，
    避免升级后把同内容重导误判为需确认更新。
    """

    if decision.status is not SkillImportStatus.UNCHANGED or published_directories is None:
        return decision
    if tuple(sorted(incoming_directories)) == published_directories:
        return decision
    status = SkillImportStatus.UPDATE if confirm_update else SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION
    return SkillImportDecision(
        status=status,
        next_version=decision.next_version + 1,
        existing_version=decision.existing_version,
    )


def _require_skill_id(skill_id: str) -> str:
    value = require_identity(skill_id, "$.skill.skill_id")
    if any(char in value for char in ("/", "\\", ":", "\x00")) or value in {".", ".."}:
        _fail("invalid_skill_id", "$.skill.skill_id")
    return value


def _enforce_deadline(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() > deadline:
        _fail("skill_time_exceeded", "$.skill.elapsed_s")


def _replace_staging(staging: Path, destination: Path) -> Path:
    """原子替换（发布前门内的唯一落点）：返回最终版本目录。"""

    os.replace(staging, destination)
    return destination


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
        and stored.get("directories") == list(inspection.directories)
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
        ticket: SkillOperationTicket | None = None,
    ) -> Path:
        """预检后原子发布：目标版本已存在且内容相同视为幂等恢复，异内容拒绝。

        传入 ``ticket`` 时：暂存在分块边界检查合作取消，最后替换走发布前门
        （取消已置位则不执行 ``os.replace``，不产生迟到/无主发布）；幂等恢复的既有
        目录记账为「复用」（``ticket.adopt``），并发他人先创建时明确报冲突。
        """

        if inspection is None:
            inspection = inspect_skill_package(data)
        destination = self.version_dir(ref)
        if destination.exists():
            if _version_matches(destination, inspection):
                if ticket is not None:
                    ticket.adopt(destination)
                return destination
            _fail("skill_version_exists", f"$.skill.version[{ref.skill_id}@{ref.version}]")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self._root / ".staging" / f"{ref.skill_id}-{ref.version}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True)
            self._extract(
                data,
                inspection,
                staging,
                deadline=deadline,
                clock=clock,
                checkpoint=ticket.checkpoint if ticket is not None else None,
            )
            _write_version_manifest(staging, inspection)
            self._commit(staging, destination, ref, ticket)
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
        ticket: SkillOperationTicket | None = None,
    ) -> Path:
        """本地目录原子发布：逐文件回读校验摘要与字节数，失败清理暂存。

        传入 ``ticket`` 时：暂存在条目/分块边界检查合作取消，最后替换走发布前门；
        幂等恢复的既有目录记账为「复用」，并发他人先创建时明确报冲突。
        """

        from services.agent_service.skill_workspace import ReadOnlyWorkspace

        destination = self.version_dir(ref)
        if destination.exists():
            if _version_matches(destination, inspection):
                if ticket is not None:
                    ticket.adopt(destination)
                return destination
            _fail("skill_version_exists", f"$.skill.version[{ref.skill_id}@{ref.version}]")
        workspace = ReadOnlyWorkspace(root)
        checkpoint = ticket.checkpoint if ticket is not None else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self._root / ".staging" / f"{ref.skill_id}-{ref.version}-{uuid.uuid4().hex}"
        try:
            staging.mkdir(parents=True)
            for directory in sorted(inspection.directories, key=lambda name: len(PurePosixPath(name).parts)):
                if checkpoint is not None:
                    checkpoint()
                _enforce_deadline(deadline, clock)
                staging.joinpath(*PurePosixPath(directory).parts).mkdir(parents=True, exist_ok=True)
            for entry in inspection.files:
                if checkpoint is not None:
                    checkpoint()
                _enforce_deadline(deadline, clock)
                target = staging.joinpath(*PurePosixPath(entry.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with target.open("wb") as handle:
                    for chunk in workspace.iter_bytes(entry.name, chunk_size=_READ_CHUNK):
                        if checkpoint is not None:
                            checkpoint()
                        _enforce_deadline(deadline, clock)
                        size += len(chunk)
                        digest.update(chunk)
                        handle.write(chunk)
                if size != entry.size or digest.hexdigest() != entry.sha256:
                    _fail("invalid_skill_package", f"$.skill.package.entry[{entry.name}]")
            _write_version_manifest(staging, inspection)
            self._commit(staging, destination, ref, ticket)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination

    def _commit(
        self,
        staging: Path,
        destination: Path,
        ref: SkillVersionRef,
        ticket: SkillOperationTicket | None,
    ) -> None:
        """唯一落点：走发布前门（有票据时）替换；并发他人先到即明确报冲突，不接管他人产物。"""

        try:
            if ticket is None:
                os.replace(staging, destination)
            else:
                ticket.publish(lambda: _replace_staging(staging, destination), destination=destination)
        except OSError:
            if destination.exists():
                _fail("skill_version_exists", f"$.skill.version[{ref.skill_id}@{ref.version}]")
            raise

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
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        expected = {entry.name: entry for entry in inspection.files}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if checkpoint is not None:
                    checkpoint()
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
                        if checkpoint is not None:
                            checkpoint()
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
    """安装入口：预检 → 原子发布；返回归档摘要（失败不改变旧版本）。

    仅受信调用：本入口不做 frontmatter/许可准入（I41），生产导入必须经
    ``SkillCatalogService.import_package``（解析 manifest + 许可白名单）；
    直接调用会发布缺合法 frontmatter 的版本目录。
    """

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

    async def read_role_skill_assignment(
        self, *, user_scope: str, role_id: str, role_version: str
    ) -> RoleSkillAssignment | None: ...

    async def set_skill_availability(
        self, *, user_scope: str, skill_id: str, version: int, enabled: bool
    ) -> tuple[str, bool]: ...

    async def list_skill_version_views(self, *, user_scope: str) -> tuple[SkillVersionView, ...]: ...

    async def remove_skill_version(self, *, user_scope: str, skill_id: str, version: int) -> None: ...


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


@dataclass(frozen=True, slots=True)
class SkillAvailabilityReceipt:
    """Skill 版本可用性切换结果：``read_state`` 为切换后的投影（ready/disabled）。"""

    user_scope: str
    skill_id: str
    version: int
    enabled: bool
    read_state: str
    replayed: bool


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
        cancel_exit_timeout: float = 5.0,
        operation_factory: Callable[[str], SkillOperationTicket] = SkillOperationTicket,
    ) -> None:
        self._store = store
        self._dao = dao
        self._run = blocking_runner
        self._budget = budget
        self._clock = clock
        self._cancel_exit_timeout = cancel_exit_timeout
        self._operation_factory = operation_factory

    async def import_package(self, request: SkillPackageImportRequest) -> SkillImportReceipt:
        """预检（ZIP/目录统一计量）→ 同名判定 → （发布 + 登记）。

        预检、前台解析与暂存共用同一 10 秒预算；解析与暂存都在受管线程执行，
        不在事件循环线程同步阻塞（R16）。**发布与登记同样受该边界约束**：暂存逐块、
        发布前、登记前与登记等待都按剩余预算判定，越界即报 ``skill_time_exceeded``
        并按归属与登记事实补偿，不留无主版本。同内容返回未变化；不同内容默认需确认。

        取消语义：预检/解析/暂存经同一操作票据合作取消；waiter 取消或超时后等待
        worker 退出（确认不到则显式标 ``UNKNOWN``）；已发生的替换按归属撤销，
        保证「取消保旧、无迟到、无主发布」。
        """

        identity = f"{request.user_scope}/skill/{request.directory_name}"
        ticket = self._operation_factory(f"{identity}#{uuid.uuid4().hex}")
        try:
            return await self._import_guarded(request, identity, ticket, publish=True)
        except asyncio.CancelledError:
            # R2：取消必须上抛；上抛前置位合作取消并收尾（确认退出或标未知、撤销本次发布）。
            await self._cancel_and_settle(ticket)
            raise

    async def preview_package(self, request: SkillPackageImportRequest) -> SkillImportReceipt:
        """导入预检：与导入共用阈值/解析/同名判定，但不发布、不登记（管理页确认前预览）。"""

        identity = f"{request.user_scope}/skill/{request.directory_name}"
        ticket = self._operation_factory(f"{identity}#{uuid.uuid4().hex}")
        try:
            return await self._import_guarded(request, identity, ticket, publish=False)
        except asyncio.CancelledError:
            await self._cancel_and_settle(ticket)
            raise

    async def _import_guarded(
        self,
        request: SkillPackageImportRequest,
        identity: str,
        ticket: SkillOperationTicket,
        *,
        publish: bool,
    ) -> SkillImportReceipt:
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
                inspection = await self._run_worker(
                    ticket,
                    partial(
                        inspect_skill_package,
                        budget=self._budget,
                        clock=self._clock,
                        checkpoint=ticket.checkpoint,
                    ),
                    request.data,
                )
            else:
                inspection = await self._run_worker(
                    ticket,
                    partial(
                        inspect_skill_directory,
                        budget=self._budget,
                        clock=self._clock,
                        checkpoint=ticket.checkpoint,
                    ),
                    request.directory,
                )
            remaining = deadline - self._clock()
            if remaining <= 0:
                _fail("skill_time_exceeded", "$.skill.elapsed_s")
            try:
                manifest = await asyncio.wait_for(
                    self._run_worker(
                        ticket,
                        partial(parse_skill_manifest, directory_name=request.directory_name),
                        inspection.manifest_text.encode("utf-8"),
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                # waiter 超时：先置位合作取消，再确认 worker 退出（确认不到标 UNKNOWN）。
                await self._cancel_and_settle(ticket)
                raise
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        except TimeoutError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "skill_time_exceeded", identity) from exc
        normalized_name = normalize_skill_name(manifest.name)
        skill_id = stable_id("skill", [request.user_scope, normalized_name])
        content_digest = content_digest_of_files(inspection.files)
        existing = await self._dao.list_skill_versions(user_scope=request.user_scope, skill_id=skill_id)
        decision = decide_skill_import(existing, content_digest=content_digest, confirm_update=request.confirm_update)
        decision = _apply_layout_decision(
            decision,
            incoming_directories=inspection.directories,
            published_directories=(
                None
                if decision.existing_version is None
                else self._published_directories(skill_id, decision.existing_version)
            ),
            confirm_update=request.confirm_update,
        )
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
        if not publish or decision.status in {
            SkillImportStatus.UNCHANGED,
            SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION,
        }:
            return receipt
        ref = SkillVersionRef(skill_id=skill_id, version=decision.next_version)
        ticket.claim(ref, user_scope=request.user_scope)
        try:
            if request.data is not None:
                await self._run_worker(
                    ticket,
                    partial(
                        self._store.publish_package,
                        deadline=deadline,
                        clock=self._clock,
                        ticket=ticket,
                    ),
                    ref,
                    request.data,
                    inspection,
                )
            else:
                await self._run_worker(
                    ticket,
                    partial(
                        self._store.publish_directory,
                        deadline=deadline,
                        clock=self._clock,
                        ticket=ticket,
                    ),
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
        remaining = deadline - self._clock()
        if remaining <= 0:
            # 发布已完成但已越过 10 秒处理边界：本次发布按归属与登记事实补偿后再报超时，
            # 不把超时后的版本留在受管目录里（无主产物）。
            await self._compensate_unregistered(ticket)
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "skill_time_exceeded", identity)
        try:
            await asyncio.wait_for(
                self._dao.register_skill_asset(
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
                ),
                timeout=remaining,
            )
        except TimeoutError as exc:
            # 登记等待已超边界：事务可能已提交也可能未提交 → 先核事实（已登记则保旧），
            # 只对「本次创建且确认未登记」补偿删除，不把未知当已回滚（R21）。
            await self._compensate_unregistered(ticket)
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "skill_time_exceeded", identity) from exc
        except BaseException:
            # 登记未确认（含回执丢失）：先核事实，只有「本次创建且确认未登记」才补偿删除。
            await self._compensate_unregistered(ticket)
            raise
        ticket.mark_registered()
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

    async def set_skill_enabled(
        self, ref: SkillVersionRef, *, user_scope: str, enabled: bool
    ) -> SkillAvailabilityReceipt:
        """用户级启用/停用 Skill 版本（F12）。

        停用受活动引用保护：该版本仍被角色版本绑定（研究配置引用）时由持久层
        拒绝（``POLICY_DENIED``），不静默打断既有选择；导入更新的不可变版本不
        受启停影响，旧版本目录始终可读（替换不覆盖活动版本）。

        启用前按版本内 ``SKILL.md`` 重核声明依赖（N30）：缺任一依赖即
        ``POLICY_DENIED`` 并列出全部受阻项，不落成功、不改可用性投影；补齐依赖
        后同一身份可正常启用，失败尝试零新事实（不累加修订或缺口快照）。
        """

        if not isinstance(ref, SkillVersionRef):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid_type: $.skill.ref", "skill_availability")
        identity = f"{user_scope}/skill/{ref.skill_id}@{ref.version}"
        try:
            require_identity(user_scope, "$.skill.user_scope")
            ref.stable_key()
            if not isinstance(enabled, bool):
                _fail("invalid_type", "$.skill.enabled")
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        if enabled:
            await self._require_dependencies_available(user_scope, ref, identity)
        read_state, replayed = await self._dao.set_skill_availability(
            user_scope=user_scope,
            skill_id=ref.skill_id,
            version=ref.version,
            enabled=enabled,
        )
        return SkillAvailabilityReceipt(
            user_scope=user_scope,
            skill_id=ref.skill_id,
            version=ref.version,
            enabled=enabled,
            read_state=read_state,
            replayed=replayed,
        )

    async def resolve_role_skill_selection(
        self,
        *,
        user_scope: str,
        role_id: str,
        role_version: str,
        hand_picked: Sequence[SkillVersionRef] = (),
        is_child: bool = False,
    ) -> tuple[SkillVersionRef, ...]:
        """解析角色实际使用的 Skill 版本：主角色 = 默认 ∪ 手选，子角色仅自身默认。

        只在角色启用集合内取交集；同一 ``skill_id`` 的默认与手选版本不同即冲突
        （``INPUT_INVALID``，不静默择一）；角色版本未配置绑定 → ``INPUT_INVALID``。
        """

        identity = f"{user_scope}/{role_id}@{role_version}"
        try:
            require_identity(user_scope, "$.role.user_scope")
            require_identity(role_id, "$.role.role_id")
            require_identity(role_version, "$.role.role_version")
            if not isinstance(is_child, bool):
                _fail("invalid_type", "$.agent.skills.is_child")
            picked: tuple[SkillVersionRef, ...] = tuple(hand_picked)
            for index, ref in enumerate(picked):
                if not isinstance(ref, SkillVersionRef):
                    _fail("invalid_type", f"$.agent.skills.hand_picked[{index}]")
                ref.stable_key()
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        assignment = await self._dao.read_role_skill_assignment(
            user_scope=user_scope,
            role_id=role_id,
            role_version=role_version,
        )
        if assignment is None:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "role skill assignment not found", identity)
        try:
            return resolve_skill_selection(
                enabled=assignment.enabled,
                defaults=assignment.defaults,
                hand_picked=picked,
                is_child=is_child,
            )
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

    async def list_skills(self, *, user_scope: str) -> tuple[SkillVersionView, ...]:
        """用户域已登记 Skill 版本（设置页列表；只读投影，不解包内容）。"""

        try:
            require_identity(user_scope, "$.skill.user_scope")
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", "skill_list"
            ) from exc
        return await self._dao.list_skill_version_views(user_scope=user_scope)

    async def uninstall_version(self, ref: SkillVersionRef, *, user_scope: str) -> SkillUninstallReceipt:
        """卸载一个 Skill 版本：先核角色/会话引用（阻删），再删登记与受管目录。

        被引用时持久层报 ``POLICY_DENIED`` 并指出引用方；登记删除成功后删除目录，
        目录是否确认清除由回执 ``files_removed`` 如实反映（不伪报）。
        """

        if not isinstance(ref, SkillVersionRef):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid_type: $.skill.ref", "skill_uninstall")
        identity = f"{user_scope}/skill/{ref.skill_id}@{ref.version}"
        try:
            require_identity(user_scope, "$.skill.user_scope")
            ref.stable_key()
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        await self._dao.remove_skill_version(user_scope=user_scope, skill_id=ref.skill_id, version=ref.version)
        await self._run(self._store.remove_version, ref)
        return SkillUninstallReceipt(
            user_scope=user_scope,
            skill_id=ref.skill_id,
            version=ref.version,
            files_removed=not self._store.version_dir(ref).exists(),
        )

    async def read_role_skill_assignment(
        self, *, user_scope: str, role_id: str, role_version: str
    ) -> RoleSkillAssignment | None:
        """读回角色版本的 Skill 启用/默认集合；未配置返回 ``None``（不猜默认）。"""

        identity = f"{user_scope}/{role_id}@{role_version}"
        try:
            require_identity(user_scope, "$.role.user_scope")
            require_identity(role_id, "$.role.role_id")
            require_identity(role_version, "$.role.role_version")
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        return await self._dao.read_role_skill_assignment(
            user_scope=user_scope,
            role_id=role_id,
            role_version=role_version,
        )

    async def validate_role_skill_assignment(
        self, assignment: RoleSkillAssignment, *, user_scope: str
    ) -> tuple[SkillVersionRef, ...]:
        """保存前一致性校验：默认⊆启用、引用版本已安装且可用、无版本冲突。

        返回按现有合同解析出的实际能力集合（主角色 = 默认 ∪ 空手选）；任一项
        不满足即如实报错，不静默替换版本或不存在的引用。
        """

        identity = f"{user_scope}/{assignment.role_id}@{assignment.role_version}"
        try:
            require_identity(user_scope, "$.role.user_scope")
            validate_role_skill_assignment(assignment)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        views = {
            (view.skill_id, view.version): view
            for view in await self._dao.list_skill_version_views(user_scope=user_scope)
        }
        for ref in assignment.enabled:
            view = views.get((ref.skill_id, ref.version))
            if view is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    f"skill version not installed: {ref.skill_id}@{ref.version}",
                    identity,
                )
            if view.read_state != "ready":
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    f"skill version not enabled: {ref.skill_id}@{ref.version}",
                    identity,
                )
            if view.missing_dependencies:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    f"skill dependency missing: {ref.skill_id}@{ref.version}",
                    identity,
                )
        try:
            return resolve_skill_selection(
                enabled=assignment.enabled,
                defaults=assignment.defaults,
                hand_picked=(),
                is_child=False,
            )
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

    async def _run_worker(
        self,
        ticket: SkillOperationTicket,
        func: Callable[..., Any],
        *args: Any,
    ) -> Any:
        """在受管线程执行一段 worker，并记录其退出事实（供取消后的收尾观察）。"""

        def worker(*inner: Any) -> Any:
            try:
                return func(*inner)
            finally:
                ticket.finish()

        ticket.begin_worker()
        return await self._run(worker, *args)

    async def _cancel_and_settle(self, ticket: SkillOperationTicket) -> None:
        """置位合作取消并收尾；收尾失败不得改变调用方正在传播的取消/超时事实。"""

        ticket.cancel()
        try:
            await asyncio.shield(self._settle_cancelled(ticket))
        except asyncio.CancelledError:
            # R2：再次取消继续上抛；收尾事实仍留在票据上（未确认退出即 UNKNOWN），不掩盖取消。
            raise
        except Exception:
            # 收尾失败不改变调用方正在传播的取消/超时事实（票据保留未确认退出即 UNKNOWN）。
            pass

    async def _settle_cancelled(self, ticket: SkillOperationTicket) -> None:
        """取消/超时后的收尾：确认退出或显式标未知；本次已发布版本按归属与登记事实撤销。"""

        exited = True
        if ticket.worker_started:
            exited = await self._run(ticket.wait_exit, self._cancel_exit_timeout)
        if ticket.published_destination is not None:
            # 替换已发生但登记不会再有：按归属与登记事实补偿，旧版本/他人版本不受影响。
            await self._compensate_unregistered(ticket)
        if not exited:
            # 显式记录「worker 未确认退出」，不把它当作已停或已发布。
            ticket.mark_unknown()

    async def _compensate_unregistered(self, ticket: SkillOperationTicket) -> None:
        """补偿：只有「本次创建 + 确认未登记」才删除版本目录。

        复用/幂等恢复的既有目录、已登记版本、登记事实无法核实时一律不删——把「不知道」
        当作「不能删」，不假装已回滚，也不误删并发他人创建/已提交的版本。
        """

        claimed = ticket.claimed
        if ticket.published_destination is None or claimed is None:
            return  # 未替换（复用既有目录或未发布）：不拥有产物，零删除。
        if ticket.registered:
            return
        scope = ticket.claim_user_scope
        if scope is None:
            return  # 缺核实键即事实未知：不删。
        try:
            registered = await self._version_registered(scope, claimed)
        except (Exception, asyncio.CancelledError):
            # 核实失败（含收尾期间再次取消）：事实未知，保守不删；原异常由调用方继续传播。
            return
        if registered:
            ticket.mark_registered()
            return
        await self._run(self._store.remove_version, claimed)
        ticket.mark_aborted()

    async def _version_registered(self, user_scope: str, ref: SkillVersionRef) -> bool:
        """查登记事实：该版本号已有登记行即视为已登记（含并发他人登记），不据摘要差异删目录。"""

        existing = await self._dao.list_skill_versions(user_scope=user_scope, skill_id=ref.skill_id)
        return any(version == ref.version for version, _digest in existing)

    def _published_directories(self, skill_id: str, version: int) -> tuple[str, ...] | None:
        """读取已发布版本清单中的目录集合（I37）；清单缺失或无目录字段时返回 None。"""

        try:
            manifest_path = (
                self._store.version_dir(SkillVersionRef(skill_id=skill_id, version=version)) / VERSION_MANIFEST_FILENAME
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, AgentValidationError):
            return None
        directories = payload.get("directories") if isinstance(payload, dict) else None
        if not isinstance(directories, list):
            return None
        return tuple(sorted(str(item) for item in directories))

    async def _missing_dependencies(self, user_scope: str, dependencies: Sequence[str]) -> tuple[str, ...]:
        missing: list[str] = []
        for dependency in dependencies:
            dependency_id = stable_id("skill", [user_scope, normalize_skill_name(dependency)])
            if not await self._dao.has_skill(user_scope=user_scope, skill_id=dependency_id):
                missing.append(dependency)
        return tuple(missing)

    async def _require_dependencies_available(self, user_scope: str, ref: SkillVersionRef, identity: str) -> None:
        """启用门（N30）：读回不可变版本声明的依赖并核当前可用性。

        以版本内 ``SKILL.md`` 为准重算，不信任导入时落库的缺口快照；缺任一依赖
        即报 ``POLICY_DENIED`` 并列出全部受阻项（声明顺序稳定），调用方不落成功。
        内容不可读时不猜「无依赖」，同样拒绝启用。
        """

        try:
            data = await self._run(self._store.read_file, ref, SKILL_MANIFEST_FILENAME)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "skill version content is missing", identity) from exc
        try:
            manifest = await self._run(partial(parse_skill_manifest, directory_name=ref.skill_id), data)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        missing = await self._missing_dependencies(user_scope, manifest.dependencies)
        if missing:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                f"skill dependency missing: {','.join(missing)}",
                identity,
            )
