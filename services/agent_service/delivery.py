"""Windows 目标保护与安全成果交付（E-05 / §10.5 / HANDOFF §10.2 / DD-G03）。

合同：
- 暂存文件必须与目标同卷（``target.parent``），先完整写入、fsync、再回读摘要；
- 发布前在**独占句柄**上核验目标「是否存在 + 文件身份 + 内容摘要」与用户确认一致，
  父目录身份未变；目标是重解析点（符号链接/junction）时拒绝覆盖；
- 覆盖只作用于「用户确认过的那个文件」：句柄身份不符（同摘要异文件、预检后被
  置换）即拒绝，绝不按路径重新猜测；确认过的目标经**句柄绑定的两步受控替换**换成
  暂存文件——先把已确认目标按句柄移为同目录私有备份，再按句柄把暂存文件不覆盖
  移入目标名，两次改名都作用在已核身份的独占句柄上（D7-10：不再释放句柄后按路径
  调用 ``ReplaceFileW``，消除句柄释放到路径替换之间的重绑窗口）。替换前后复核
  目标父目录身份，替换后复核目标文件身份与摘要；任何第三方新文件都不会被本次
  发布覆掉；「移开已确认目标」本身也是条件替换：移开后按备份名复核身份，窗口内
  被外部置换的就不是已确认文件，必须放回原处并失败（见
  ``_GuardedFile.supersede_to``）；两步之间若进程中断，目标名可能短暂无文件，
  但旧目标留在可由原回执复算的私有备份名下，可经 ``inspect_publish`` /
  ``resume_publish`` 核验续接；
- 备份名由**暂存文件身份**派生（``_superseded_name``），不随机：中断后可按原
  交付回执（manifest 的 ``staged_volume_serial`` / ``staged_file_id``）复算出备份
  路径，用 ``WindowsManagedFileStore.inspect_publish`` 查询阶段事实、
  ``WindowsManagedFileStore.resume_publish`` 按原回执续接，不按路径猜结论、
  不默认覆盖；
- 备份只在复核「身份与摘要都是用户确认过的旧目标」后按句柄删除（不按名字删）；
  不是那个文件即回滚（本次内容退回暂存名、备份放回目标名），既不覆盖也不删除
  第三方文件；确实清不掉的残余随 ``PublishResult.residue`` 如实返回；
- 发布后用目标重新计算摘要与文件身份；无法确证即 ``unknown``——不把无法确证的
  结果报告成发布成功；
- 取消只清理「身份与摘要均与暂存记录一致」的暂存文件；路径被他人替换或文件被
  在途 writer 独占时拒绝删除，不按路径误删他人文件。

身份/覆盖条件实现（标准库，无新增依赖）：
- Windows：``CreateFileW``（``dwShareMode=0``、``FILE_FLAG_OPEN_REPARSE_POINT``、
  带 ``DELETE`` 访问权）独占打开；独占期间该路径条目不能被他人替换或删除，句柄
  身份即路径身份。改名与删除经 ``SetFileInformationByHandle``（``FileRenameInfo``
  / ``FileDispositionInfo``）作用在句柄上，不再按路径重开；移入目标用
  ``ReplaceIfExists=0``，目标已存在即失败（不覆盖）。
- POSIX：无独占共享语义，描述符 ``fstat`` 只能证明「核验时该路径是谁」，不能阻止
  核验后被置换；因此「移开已确认目标」改为原子改名后立即按新名复核身份，不符即
  放回原处并失败（放回失败也只是把外部文件留在私有备份名，绝不删除或覆盖），
  移入仍用 ``link`` 不覆盖（本模块的产品平台为 Windows）。

禁止事项：不执行任意 Shell、不写确认范围外路径、不把暂存文件当成功成果、
不在事件循环线程直接调用本模块（调用方须经 ``ThreadPoolManager``）。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.agent_types import DeliveryTarget, validate_delivery_target_path

_READ_CHUNK = 1024 * 1024
_NAME_HINT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_IS_WINDOWS = sys.platform == "win32"
#: Windows ``CreateFileW`` 参数：只读 + 删除权（改名/删除都经句柄，需 DELETE）。
_WIN_ACCESS = 0x80000000 | 0x00010000
#: ``CreateFileW``：不共享（独占）→ 期间该路径条目不可被他人替换/删除。
_WIN_SHARE_NONE = 0
_WIN_OPEN_EXISTING = 3
_WIN_ATTRIBUTES = 0x80 | 0x00200000  # FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT
_WIN_FILE_RENAME_INFO = 3
_WIN_FILE_DISPOSITION_INFO = 4


class DeliveryFileError(RuntimeError):
    """本地交付失败（可确证未发布；``code`` 用于回执与 UI）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class DeliveryUnknownError(DeliveryFileError):
    """发布效果无法确证（§10.5：必须进入 unknown，不得盲重发）。"""


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """目标/暂存在某一时刻的可核对身份（不含凭据与内容）。"""

    exists: bool
    volume_serial: int | None
    file_id: int | None
    size: int | None
    digest: str | None
    is_reparse_point: bool
    parent_volume_serial: int | None
    parent_file_id: int | None
    parent_is_reparse_point: bool = False


@dataclass(frozen=True, slots=True)
class StagedFile:
    """已落盘并通过回读校验的暂存内容。

    除暂存文件自身身份外，还记录**确认时**目标的文件身份（``confirmed_target_*``，
    目标当时不存在时为 ``None``）：发布只允许覆盖这个身份的文件，同摘要异文件
    （内容一致但文件 ID 不同）会被拒绝。
    """

    path: Path
    digest: str
    size: int
    volume_serial: int
    file_id: int
    parent_volume_serial: int
    parent_file_id: int
    confirmed_target_volume_serial: int | None = None
    confirmed_target_file_id: int | None = None


@dataclass(frozen=True, slots=True)
class PublishResult:
    """发布确证结果：目标最终身份与内容摘要。

    ``residue`` 是发布已确证、但无法清理的私有备份（旧目标）路径；调用方必须
    如实呈现（例如「旧文件仍在该私有名下」），不得当作已完成清理。
    """

    snapshot: FileSnapshot
    residue: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishRecovery:
    """按原暂存回执复核的发布阶段事实（崩溃/中断后唯一的续接依据）。

    只记录可核对的事实，不声称未证明的阶段：``target_state`` 为 ``absent`` /
    ``confirmed``（仍是用户确认过的原文件）/ ``published``（已是本次暂存文件）/
    ``changed``（其他文件）/ ``unverified``（无法核验）；``staged_state`` 与
    ``backup_state`` 为 ``absent`` / ``match``（身份与摘要都与回执一致）/
    ``changed``。``resumable`` 为假时 ``reason`` 给出拒绝续接的原因码。
    """

    target_state: str
    staged_state: str
    backup_state: str
    backup_path: Path
    resumable: bool
    reason: str | None = None


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(stat_result: os.stat_result) -> int:
    """文件索引身份；为 0 表示该卷不提供文件 ID，无法证明归属（拒绝发布）。

    FAT/exFAT、部分 SMB/虚拟卷上 ``st_ino`` 恒为 0；此时 ``0 == 0`` 会让
    「目标就是本交付文件」的断言恒真，必须在此 fail-closed（§10.5）。
    """

    file_id = int(stat_result.st_ino)
    if file_id == 0:
        raise DeliveryFileError(
            "delivery_file_identity_unavailable", "target volume does not expose a stable file identity"
        )
    return file_id


def _stage_prefix(target_name: str, name_hint: str | None) -> str:
    if name_hint is None:
        return f".{target_name}."
    if not _NAME_HINT_PATTERN.fullmatch(name_hint):
        raise DeliveryFileError(
            "delivery_stage_name_invalid", "staged name hint must be 1-32 characters of [A-Za-z0-9_-]"
        )
    return f".{target_name}.{name_hint}."


def _superseded_name(target_path: Path, *, volume_serial: int, file_id: int) -> Path:
    """被替换目标的私有备份名（同目录，由**暂存文件身份**派生）。

    名称必须能从原交付回执复原：manifest 记录了 ``staged_volume_serial`` /
    ``staged_file_id``，据此可复算出同一个备份路径，崩溃后既能核验也能续接；
    随机后缀会让备份成为不可报告的残留（P1-12）。
    """

    token = hashlib.sha256(f"{volume_serial}:{file_id}".encode("ascii")).hexdigest()[:16]
    return target_path.with_name(f".{target_path.name}.superseded.{token}")


def _resume_rule(target_state: str, staged_state: str, backup_state: str) -> tuple[bool, str | None]:
    """可续接的事实组合（其余一律拒绝续接，不按路径猜结论）。"""

    if target_state == "published" and backup_state in {"absent", "match"}:
        return True, None
    if target_state in {"confirmed", "absent"} and staged_state == "match" and backup_state == "absent":
        return True, None
    if target_state == "absent" and backup_state == "match" and staged_state in {"match", "absent"}:
        return True, None
    return False, "delivery_recovery_state_conflict"


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _snapshot(path: Path, *, with_digest: bool = True) -> FileSnapshot:
    parent_stat = os.stat(path.parent)
    parent_is_reparse = _is_reparse_point(os.lstat(path.parent))
    try:
        target_stat = os.lstat(path)
    except FileNotFoundError:
        return FileSnapshot(
            exists=False,
            volume_serial=None,
            file_id=None,
            size=None,
            digest=None,
            is_reparse_point=False,
            parent_volume_serial=parent_stat.st_dev,
            parent_file_id=parent_stat.st_ino,
            parent_is_reparse_point=parent_is_reparse,
        )
    is_reparse = _is_reparse_point(target_stat)
    is_regular = stat.S_ISREG(target_stat.st_mode)
    digest = _digest_file(path) if with_digest and is_regular and not is_reparse else None
    return FileSnapshot(
        exists=True,
        volume_serial=target_stat.st_dev,
        file_id=target_stat.st_ino,
        size=target_stat.st_size,
        digest=digest,
        is_reparse_point=is_reparse,
        parent_volume_serial=parent_stat.st_dev,
        parent_file_id=parent_stat.st_ino,
        parent_is_reparse_point=parent_is_reparse,
    )


class _Win32FileApi:
    """kernel32 独占句柄调用集合（仅 win32 实例化）。

    ``ctypes`` 的 Win32 名称（``WinDLL`` / ``WinError`` / ``get_last_error``）与
    ``msvcrt`` 只在 Windows 平台存在；本类不参与 Linux CI 执行，相应行标注
    ``pragma: no cover``。
    """

    def __init__(self) -> None:  # pragma: no cover -- win32 原生 API
        import ctypes
        from ctypes import wintypes

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("ReplaceIfExists", ctypes.c_ubyte),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * 1),
            ]

        class DispositionInfo(ctypes.Structure):
            _fields_ = [("DeleteFile", wintypes.BOOL)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[reportAttributeAccessIssue]  # Windows-only：WinDLL 在 Linux 平台不存在
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32: Any = kernel32
        self._rename_info: Any = RenameInfo
        self._disposition_info: Any = DispositionInfo

    def open_exclusive(self, path: Path) -> int:  # pragma: no cover -- win32 原生 API
        """独占（``dwShareMode=0``）打开文件并转成描述符；失败抛 ``OSError``。"""

        import ctypes
        import msvcrt  # type: ignore[reportMissingImports]  # Windows-only：msvcrt 在 Linux 平台不存在

        handle = self._kernel32.CreateFileW(
            str(path),
            _WIN_ACCESS,
            _WIN_SHARE_NONE,
            None,
            _WIN_OPEN_EXISTING,
            _WIN_ATTRIBUTES,
            None,
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[reportAttributeAccessIssue]  # Windows-only：WinError 在 Linux 平台不存在
        try:
            open_osfhandle = msvcrt.open_osfhandle  # type: ignore[attr-defined]  # typeshed 3.14 stub 未收录；运行时恒提供
            return int(open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)))
        except OSError:
            self._kernel32.CloseHandle(handle)
            raise

    def rename(self, fd: int, destination: Path) -> None:  # pragma: no cover -- win32 原生 API
        """把句柄指向的文件移到 ``destination``；``ReplaceIfExists=0`` 绝不覆盖。"""

        import ctypes
        import msvcrt

        encoded = str(destination).encode("utf-16-le")
        size = ctypes.sizeof(self._rename_info) + len(encoded)
        buffer = ctypes.create_string_buffer(size)
        info = ctypes.cast(buffer, ctypes.POINTER(self._rename_info)).contents
        info.ReplaceIfExists = 0
        info.RootDirectory = None
        info.FileNameLength = len(encoded)
        ctypes.memmove(
            ctypes.addressof(buffer) + self._rename_info.FileName.offset,
            encoded,
            len(encoded),
        )
        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]  # typeshed 3.14 stub 未收录；运行时恒提供
        if not self._kernel32.SetFileInformationByHandle(handle, _WIN_FILE_RENAME_INFO, buffer, size):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[reportAttributeAccessIssue]  # Windows-only：WinError 在 Linux 平台不存在

    def delete_on_close(self, fd: int) -> None:  # pragma: no cover -- win32 原生 API
        """给句柄打删除标记（close 时删除该句柄指向的文件，按句柄而非按路径）。"""

        import ctypes
        import msvcrt

        info = self._disposition_info(DeleteFile=True)
        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]  # typeshed 3.14 stub 未收录；运行时恒提供
        if not self._kernel32.SetFileInformationByHandle(
            handle, _WIN_FILE_DISPOSITION_INFO, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[reportAttributeAccessIssue]  # Windows-only：WinError 在 Linux 平台不存在


_WIN32_API: _Win32FileApi | None = None


def _win32_api() -> _Win32FileApi:
    global _WIN32_API
    if _WIN32_API is None:
        _WIN32_API = _Win32FileApi()
    return _WIN32_API


class _GuardedFile:
    """持有已核身份的文件句柄；Windows 上独占期间路径不可被他人替换。

    改名/删除都作用在句柄上（Windows），或先经 ``fstat`` 核对身份、再按路径执行
    并在变更后复核身份（POSIX），因此调用方可以先核身份、再执行变更，而不会把
    「核验过的文件」与「真正被变更的文件」分开。
    """

    def __init__(self, *, path: Path, fd: int) -> None:
        self._path = path
        self._fd = fd

    @classmethod
    def open_exclusive(cls, path: Path) -> _GuardedFile:
        if _IS_WINDOWS:
            return cls(path=path, fd=_win32_api().open_exclusive(path))
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        return cls(path=path, fd=fd)

    def identity(self) -> tuple[int, int]:
        stat_result = os.fstat(self._fd)
        return int(stat_result.st_dev), _file_identity(stat_result)

    def digest(self) -> str:
        os.lseek(self._fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        with os.fdopen(os.dup(self._fd), "rb") as stream:
            while chunk := stream.read(_READ_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()

    def move_to(self, destination: Path) -> None:
        """把本句柄指向的文件移到 ``destination``；目标已存在即失败（不覆盖）。"""

        if _IS_WINDOWS:
            _win32_api().rename(self._fd, destination)
            self._path = destination
            return
        os.link(self._path, destination)
        os.unlink(self._path)
        self._path = destination

    def supersede_to(self, destination: Path, *, expected_identity: tuple[int, int]) -> None:
        """把已核身份的目标移为私有备份，并复核「移走的仍是已核文件」。

        Windows 独占句柄期间该路径条目不可被他人替换或删除，句柄改名必然作用于
        已核文件（平台保证条件性）；无独占共享语义的平台只能在改名后复核：窗口
        内被外部置换的文件不是已核文件，即原样放回并失败，绝不删除或覆盖它。
        """

        original = self._path
        if _IS_WINDOWS:
            _win32_api().rename(self._fd, destination)
            self._path = destination
            return
        # 备份名先以 O_EXCL 由本进程建立（绝不覆盖既有文件），再原子改名覆盖这个空占位。
        placeholder = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(placeholder)
        try:
            os.replace(original, destination)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(destination)
            raise
        self._path = destination
        try:
            moved_stat = os.lstat(destination)
            moved_identity: tuple[int, int] | None = (int(moved_stat.st_dev), _file_identity(moved_stat))
        except (OSError, DeliveryFileError):
            moved_identity = None
        if moved_identity == expected_identity:
            return
        self._path = original
        # 放回窗口内被移开的外部文件；原目标名已被再次占用时保留在私有备份名，不删除。
        with contextlib.suppress(OSError):
            os.link(destination, original)
            os.unlink(destination)
        raise DeliveryFileError("delivery_target_changed", "target changed between verification and supersede")

    def delete(self) -> None:
        if _IS_WINDOWS:
            _win32_api().delete_on_close(self._fd)
            return
        os.unlink(self._path)

    def close(self) -> None:
        """幂等关闭：同一句柄被多处释放时不能再次关闭复用了该描述符号的文件。"""

        if self._fd < 0:
            return
        fd, self._fd = self._fd, -1
        os.close(fd)

    def __enter__(self) -> _GuardedFile:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class WindowsManagedFileStore:
    """同卷暂存 + 独占句柄复核 + 不覆盖移入的本地文件交付实现。"""

    def stage(
        self,
        *,
        target: DeliveryTarget,
        content: bytes,
        expected_digest: str,
        name_hint: str | None = None,
    ) -> StagedFile:
        """把内容写入同卷暂存并回读；任何偏差都清理暂存后失败。

        ``name_hint`` 用于让暂存文件名可从交付身份派生（进程崩溃后仍可按
        原交付定位残留）；只影响名称，不影响内容与校验。
        """

        target_path = Path(validate_delivery_target_path(target.target_path))
        if not target_path.parent.is_dir():
            raise DeliveryFileError("delivery_target_parent_missing", "target parent directory does not exist")
        parent_stat = os.stat(target_path.parent)
        parent_id = _file_identity(parent_stat)
        snapshot = _snapshot(target_path)
        self._assert_target_matches(snapshot, target, phase="stage")
        # 发布只允许覆盖「用户确认时那个文件」：身份取自确认，不取暂存时的快照。
        confirmed_identity: tuple[int, int] | tuple[None, None]
        if target.expected_existing:
            volume_serial = target.expected_volume_serial
            file_id = target.expected_file_id
            if volume_serial is None or file_id is None:  # pragma: no cover - _assert_target_matches 已拒绝该情形
                raise DeliveryFileError(
                    "delivery_confirmation_identity_missing",
                    "overwrite confirmation does not carry the confirmed target file identity",
                )
            confirmed_identity = (volume_serial, file_id)
        else:
            confirmed_identity = (None, None)
        prefix = _stage_prefix(target_path.name, name_hint)
        handle, raw_path = tempfile.mkstemp(
            prefix=prefix,
            suffix=".staged",
            dir=str(target_path.parent),
        )
        staged_path = Path(raw_path)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged_stat = os.stat(staged_path)
            digest = _digest_file(staged_path)
            if digest != expected_digest:
                raise DeliveryFileError(
                    "delivery_stage_readback_failed", "staged content digest does not match expected output"
                )
            return StagedFile(
                path=staged_path,
                digest=digest,
                size=staged_stat.st_size,
                volume_serial=staged_stat.st_dev,
                file_id=_file_identity(staged_stat),
                parent_volume_serial=staged_stat.st_dev,
                parent_file_id=parent_id,
                confirmed_target_volume_serial=confirmed_identity[0],
                confirmed_target_file_id=confirmed_identity[1],
            )
        except BaseException:
            staged_path.unlink(missing_ok=True)
            raise

    def publish(self, *, target: DeliveryTarget, staged: StagedFile) -> PublishResult:
        """核验目标身份后替换为暂存内容，并回读目标内容摘要与文件身份。

        覆盖已确认目标时采用句柄绑定的两步受控替换：已确认目标按句柄移为私有备份，
        暂存文件按句柄不覆盖移入目标名；两次改名都在独占句柄上执行，替换前后复核
        父目录与文件身份，不释放句柄后按路径替换（D7-10）。备份只在身份与摘要都
        复核为原确认文件后按句柄删除。
        """

        target_path = Path(validate_delivery_target_path(target.target_path))
        try:
            before = _snapshot(target_path)
        except OSError as exc:
            raise DeliveryFileError("delivery_target_changed", "target cannot be inspected before publish") from exc
        self._assert_target_matches(before, target, phase="publish")
        if before.parent_file_id == 0:
            raise DeliveryFileError(
                "delivery_file_identity_unavailable", "target parent volume does not expose a stable identity"
            )
        if before.parent_volume_serial != staged.parent_volume_serial or before.parent_file_id != staged.parent_file_id:
            raise DeliveryFileError("delivery_target_parent_changed", "target parent changed since confirmation")
        if staged.path.parent != target_path.parent:
            raise DeliveryFileError("delivery_stage_not_same_volume", "staged content is not on the target volume")

        backup_path: Path | None = None
        confirmed_identity: tuple[int, int] | None = None
        residue: tuple[Path, ...] = ()
        if target.expected_existing:
            # 备份名可由原回执复算，且平台会静默覆盖已占用的备份名：先拒绝占用。
            backup_path = _superseded_name(target_path, volume_serial=staged.volume_serial, file_id=staged.file_id)
            self._assert_backup_available(backup_path)
            confirmed_identity = self._confirmed_identity(target, staged)

        staged_guard = self._open_staged(staged)
        try:
            self._verify_guard(
                staged_guard,
                identity=(staged.volume_serial, staged.file_id),
                digest=staged.digest,
                code="delivery_stage_changed",
                message="staged content changed before publish",
            )
            if backup_path is None or confirmed_identity is None:
                try:
                    staged_guard.move_to(target_path)
                except FileExistsError as exc:
                    raise DeliveryFileError(
                        "delivery_target_changed", "target appeared between verification and publish"
                    ) from exc
                except OSError as exc:
                    raise DeliveryUnknownError(
                        "delivery_publish_unverified", "target publish failed and effect cannot be verified"
                    ) from exc
            else:
                self._publish_over_confirmed_target(
                    target=target,
                    staged=staged,
                    staged_guard=staged_guard,
                    target_path=target_path,
                    backup_path=backup_path,
                    confirmed_identity=confirmed_identity,
                )
        finally:
            with contextlib.suppress(OSError):
                staged_guard.close()

        # 替换后先确证目标已是本交付文件（不确证就不动旧文件备份）。
        if backup_path is None or confirmed_identity is None:
            return PublishResult(snapshot=self._verified_publish_snapshot(target_path, staged))
        self._verified_publish_snapshot(target_path, staged)
        residue = self._verify_and_drop_backup(
            backup_path=backup_path,
            confirmed_identity=confirmed_identity,
            expected_digest=target.expected_existing_digest,
            staged=staged,
            target_path=target_path,
        )
        return PublishResult(snapshot=self._verified_publish_snapshot(target_path, staged), residue=residue)

    def inspect_publish(self, *, target: DeliveryTarget, staged: StagedFile) -> PublishRecovery:
        """按原暂存回执复核发布阶段事实（中断后唯一的续接依据，不按路径猜结论）。"""

        target_path = Path(validate_delivery_target_path(target.target_path))
        backup_path = _superseded_name(target_path, volume_serial=staged.volume_serial, file_id=staged.file_id)
        target_state = self._target_state(target_path, target, staged)
        staged_state = self._file_state(
            staged.path, identity=(staged.volume_serial, staged.file_id), digest=staged.digest
        )
        confirmed_identity = self._confirmed_identity_or_none(target)
        if confirmed_identity is None:
            backup_state = "changed" if os.path.lexists(backup_path) else "absent"
        else:
            backup_state = self._file_state(
                backup_path, identity=confirmed_identity, digest=target.expected_existing_digest
            )
        resumable, reason = _resume_rule(target_state, staged_state, backup_state)
        return PublishRecovery(
            target_state=target_state,
            staged_state=staged_state,
            backup_state=backup_state,
            backup_path=backup_path,
            resumable=resumable,
            reason=reason,
        )

    def resume_publish(self, *, target: DeliveryTarget, staged: StagedFile) -> PublishResult:
        """按原回执续接中断的发布：只处理身份与摘要都与回执一致的文件。"""

        recovery = self.inspect_publish(target=target, staged=staged)
        if not recovery.resumable:
            raise DeliveryUnknownError(
                "delivery_publish_unverified",
                f"publish state cannot be resumed: {recovery.reason}",
            )
        target_path = Path(validate_delivery_target_path(target.target_path))
        if recovery.target_state == "published":
            confirmed_identity = self._confirmed_identity_or_none(target)
            residue: tuple[Path, ...] = ()
            if confirmed_identity is not None:
                residue = self._verify_and_drop_backup(
                    backup_path=recovery.backup_path,
                    confirmed_identity=confirmed_identity,
                    expected_digest=target.expected_existing_digest,
                    staged=staged,
                    target_path=target_path,
                )
            return PublishResult(
                snapshot=self._verified_publish_snapshot(target_path, staged),
                residue=residue,
            )
        if recovery.staged_state == "match" and recovery.backup_state == "absent":
            return self.publish(target=target, staged=staged)
        if recovery.staged_state == "match":
            return self._resume_move_in(
                target=target, staged=staged, target_path=target_path, backup_path=recovery.backup_path
            )
        # 暂存已不在：只能按原回执把用户确认过的原文件放回目标名，不声称发布成功。
        self._restore_confirmed_backup(recovery.backup_path, target_path, target)
        raise DeliveryFileError(
            "delivery_publish_rolled_back", "publish did not complete; the confirmed target was restored"
        )

    def cancel(self, staged: StagedFile) -> bool:
        """取消发布：只删除身份与摘要都能对上原暂存记录的文件；返回是否实际删除。"""

        try:
            guard = _GuardedFile.open_exclusive(staged.path)
        except OSError:
            # 路径缺失/已被替换成目录/被在途 writer 独占：都不删除。
            return False
        try:
            if guard.identity() != (staged.volume_serial, staged.file_id):
                return False
            if guard.digest() != staged.digest:
                return False
            guard.delete()
        except OSError:
            return False
        finally:
            with contextlib.suppress(OSError):
                guard.close()
        return not os.path.lexists(staged.path)

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    @staticmethod
    def _open_staged(staged: StagedFile) -> _GuardedFile:
        try:
            return _GuardedFile.open_exclusive(staged.path)
        except FileNotFoundError as exc:
            raise DeliveryFileError("delivery_stage_missing", "staged content is no longer available") from exc
        except OSError as exc:
            raise DeliveryFileError("delivery_stage_changed", "staged content cannot be exclusively verified") from exc

    @staticmethod
    def _verify_guard(
        guard: _GuardedFile,
        *,
        identity: tuple[int, int],
        digest: str,
        code: str,
        message: str,
    ) -> None:
        if guard.identity() != identity or guard.digest() != digest:
            raise DeliveryFileError(code, message)

    def _open_confirmed_target(
        self,
        target_path: Path,
        target: DeliveryTarget,
        staged: StagedFile,
    ) -> tuple[_GuardedFile, tuple[int, int]]:
        """独占打开目标并核验「就是确认时那个文件」；不符即拒绝，绝不按路径臆测。"""

        identity = self._confirmed_identity(target, staged)
        try:
            guard = _GuardedFile.open_exclusive(target_path)
        except FileNotFoundError as exc:
            raise DeliveryFileError("delivery_target_changed", "target disappeared before publish") from exc
        except OSError as exc:
            raise DeliveryFileError(
                "delivery_target_changed", "target is held by another writer or cannot be exclusively verified"
            ) from exc
        try:
            if guard.identity() != identity:
                raise DeliveryFileError("delivery_target_changed", "target is not the file confirmed by the user")
            if target.expected_existing_digest is None or guard.digest() != target.expected_existing_digest:
                raise DeliveryFileError("delivery_target_changed", "target content changed before publish")
        except BaseException:
            with contextlib.suppress(OSError):
                guard.close()
            raise
        return guard, identity

    @staticmethod
    def _confirmed_identity(target: DeliveryTarget, staged: StagedFile) -> tuple[int, int]:
        """覆盖确认绑定的原目标身份；暂存记录与本次确认必须指向同一个文件。"""

        if target.expected_volume_serial is None or target.expected_file_id is None:
            raise DeliveryFileError(
                "delivery_confirmation_identity_missing",
                "overwrite confirmation does not carry the confirmed target file identity",
            )
        if (staged.confirmed_target_volume_serial, staged.confirmed_target_file_id) != (
            target.expected_volume_serial,
            target.expected_file_id,
        ):
            raise DeliveryFileError(
                "delivery_target_changed", "staged record was confirmed against another target file"
            )
        return (target.expected_volume_serial, target.expected_file_id)

    @staticmethod
    def _confirmed_identity_or_none(target: DeliveryTarget) -> tuple[int, int] | None:
        if not target.expected_existing:
            return None
        if target.expected_volume_serial is None or target.expected_file_id is None:
            return None
        return (target.expected_volume_serial, target.expected_file_id)

    @staticmethod
    def _assert_backup_available(backup_path: Path) -> None:
        """备份名必须空闲：两步替换的「移开旧目标」不覆盖既有文件（Windows
        ``ReplaceIfExists=0`` / POSIX ``O_EXCL`` 占位），占用即拒绝发布。"""

        try:
            occupied = os.path.lexists(backup_path)
        except OSError as exc:
            raise DeliveryFileError("delivery_target_changed", "superseded name cannot be inspected") from exc
        if occupied:
            raise DeliveryFileError("delivery_target_changed", "superseded backup name is already taken")

    def _publish_over_confirmed_target(
        self,
        *,
        target: DeliveryTarget,
        staged: StagedFile,
        staged_guard: _GuardedFile,
        target_path: Path,
        backup_path: Path,
        confirmed_identity: tuple[int, int],
    ) -> None:
        """覆盖已确认目标：句柄绑定的两步受控替换（不释放句柄后按路径替换）。"""

        # D7-10：此前先关闭 target_guard 与 staged_guard、再按路径调用 ReplaceFileW，
        # 释放句柄到路径替换之间存在重绑窗口（同名路径可被换成另一个文件）。这里
        # 两次改名都作用在已核身份的独占句柄上，并在替换前后复核父目录身份。
        self._verify_publish_parent(target_path, staged)
        target_guard, _ = self._open_confirmed_target(target_path, target, staged)
        try:
            self._verify_guard(
                staged_guard,
                identity=(staged.volume_serial, staged.file_id),
                digest=staged.digest,
                code="delivery_stage_changed",
                message="staged content changed before publish",
            )
            try:
                target_guard.supersede_to(backup_path, expected_identity=confirmed_identity)
            except OSError as exc:
                raise DeliveryFileError(
                    "delivery_target_changed", "target cannot be superseded by the confirmed publish"
                ) from exc
            try:
                staged_guard.move_to(target_path)
            except FileExistsError as exc:
                if self._restore_superseded(target_guard, backup_path, target_path):
                    raise DeliveryFileError(
                        "delivery_target_changed", "target appeared between verification and publish"
                    ) from exc
                raise DeliveryUnknownError(
                    "delivery_publish_unverified", "target appeared and the superseded file cannot be restored"
                ) from exc
            except OSError as exc:
                self._restore_superseded(target_guard, backup_path, target_path)
                raise DeliveryUnknownError(
                    "delivery_publish_unverified", "target publish failed and effect cannot be verified"
                ) from exc
            try:
                self._verify_publish_parent(target_path, staged)
            except DeliveryFileError as exc:
                raise DeliveryUnknownError(
                    "delivery_publish_unverified", "target parent changed during publish"
                ) from exc
        finally:
            with contextlib.suppress(OSError):
                target_guard.close()

    @staticmethod
    def _verify_publish_parent(target_path: Path, staged: StagedFile) -> None:
        """复核目标父目录仍是暂存时记录的目录（替换前后都必须一致）。"""

        try:
            current = os.stat(target_path.parent)
        except OSError as exc:
            raise DeliveryFileError(
                "delivery_target_parent_changed", "target parent directory cannot be inspected"
            ) from exc
        if (int(current.st_dev), _file_identity(current)) != (
            staged.parent_volume_serial,
            staged.parent_file_id,
        ):
            raise DeliveryFileError("delivery_target_parent_changed", "target parent changed since confirmation")

    @staticmethod
    def _verified_publish_snapshot(target_path: Path, staged: StagedFile) -> FileSnapshot:
        """发布后确证：目标必须是本交付的暂存文件（身份、摘要、父目录身份）。"""

        after = _snapshot(target_path)
        if not after.exists or after.digest != staged.digest or after.is_reparse_point:
            raise DeliveryUnknownError(
                "delivery_publish_unverified", "published target content does not match staged content"
            )
        if after.volume_serial != staged.volume_serial or after.file_id != staged.file_id:
            raise DeliveryUnknownError(
                "delivery_publish_unverified", "published target is not the staged file of this delivery"
            )
        if after.parent_volume_serial != staged.parent_volume_serial or after.parent_file_id != staged.parent_file_id:
            raise DeliveryUnknownError("delivery_publish_unverified", "target parent identity changed during publish")
        return after

    def _verify_and_drop_backup(
        self,
        *,
        backup_path: Path,
        confirmed_identity: tuple[int, int],
        expected_digest: str | None,
        staged: StagedFile,
        target_path: Path,
    ) -> tuple[Path, ...]:
        """复核私有备份就是用户确认过的旧目标，随后按句柄删除（不按名字删）。

        不是那个文件（预检后窗口内被置换）→ 回滚：本次内容退回暂存名、备份放回
        目标名，既不覆盖也不删除第三方文件；确实清不掉时返回残余路径，不静默丢弃。
        """

        try:
            guard = _GuardedFile.open_exclusive(backup_path)
        except FileNotFoundError:
            return ()
        except OSError:
            return (backup_path,)
        verified = False
        try:
            verified = guard.identity() == confirmed_identity and expected_digest is not None
            verified = verified and guard.digest() == expected_digest
            if verified:
                try:
                    guard.delete()
                except OSError:
                    # 只读旧目标（复制自只读介质）先清只读位再删；句柄独占期间该名字
                    # 无法被他人替换，因此这里清的就是刚复核过的那个文件。
                    with contextlib.suppress(OSError):
                        os.chmod(backup_path, stat.S_IWRITE)
                    guard.delete()
        except OSError:
            return (backup_path,)
        finally:
            with contextlib.suppress(OSError):
                guard.close()
        if not verified:
            if self._rollback_replaced(target_path=target_path, staged=staged, backup_path=backup_path):
                raise DeliveryFileError(
                    "delivery_target_changed", "superseded file is not the target confirmed by the user"
                )
            raise DeliveryUnknownError(
                "delivery_publish_unverified",
                f"superseded file cannot be verified nor restored; residue at {backup_path.name}",
            )
        return () if not os.path.lexists(backup_path) else (backup_path,)

    def _rollback_replaced(self, *, target_path: Path, staged: StagedFile, backup_path: Path) -> bool:
        """发布后复核失败的回退：本次内容退回暂存名，备份放回目标名（都不覆盖）。"""

        try:
            with _GuardedFile.open_exclusive(target_path) as guard:
                if guard.identity() != (staged.volume_serial, staged.file_id):
                    return False
                guard.move_to(staged.path)
        except OSError:
            return False
        try:
            with _GuardedFile.open_exclusive(backup_path) as guard:
                guard.move_to(target_path)
        except OSError:
            return False
        return True

    def _resume_move_in(
        self,
        *,
        target: DeliveryTarget,
        staged: StagedFile,
        target_path: Path,
        backup_path: Path,
    ) -> PublishResult:
        """续接「原目标已移开、暂存尚未移入」的中断：按原回执完成移入并清理备份。"""

        staged_guard = self._open_staged(staged)
        try:
            self._verify_guard(
                staged_guard,
                identity=(staged.volume_serial, staged.file_id),
                digest=staged.digest,
                code="delivery_stage_changed",
                message="staged content changed before the resumed publish",
            )
            try:
                staged_guard.move_to(target_path)
            except FileExistsError as exc:
                raise DeliveryFileError(
                    "delivery_target_changed", "target appeared before the resumed publish"
                ) from exc
            except OSError as exc:
                raise DeliveryUnknownError(
                    "delivery_publish_unverified", "resumed publish failed and effect cannot be verified"
                ) from exc
        finally:
            with contextlib.suppress(OSError):
                staged_guard.close()
        self._verified_publish_snapshot(target_path, staged)
        residue = self._verify_and_drop_backup(
            backup_path=backup_path,
            confirmed_identity=self._confirmed_identity(target, staged),
            expected_digest=target.expected_existing_digest,
            staged=staged,
            target_path=target_path,
        )
        return PublishResult(snapshot=self._verified_publish_snapshot(target_path, staged), residue=residue)

    @staticmethod
    def _restore_confirmed_backup(backup_path: Path, target_path: Path, target: DeliveryTarget) -> None:
        """把原确认文件从备份名放回目标名（不覆盖）；不符即拒绝，不按路径猜测。"""

        identity = WindowsManagedFileStore._confirmed_identity_or_none(target)
        try:
            with _GuardedFile.open_exclusive(backup_path) as guard:
                if identity is None or guard.identity() != identity:
                    raise DeliveryFileError(
                        "delivery_target_changed", "superseded backup is not the target confirmed by the user"
                    )
                guard.move_to(target_path)
        except FileExistsError as exc:
            raise DeliveryFileError("delivery_target_changed", "target name is occupied by another file") from exc
        except OSError as exc:
            raise DeliveryUnknownError(
                "delivery_publish_unverified", "confirmed target cannot be restored from the superseded backup"
            ) from exc

    @staticmethod
    def _target_state(target_path: Path, target: DeliveryTarget, staged: StagedFile) -> str:
        """目标当前事实：原确认文件 / 本次发布文件 / 不存在 / 其他文件 / 无法核验。"""

        try:
            snapshot = _snapshot(target_path)
        except OSError:
            return "unverified"
        if not snapshot.exists:
            return "absent"
        if (snapshot.volume_serial, snapshot.file_id) == (staged.volume_serial, staged.file_id):
            return "published" if snapshot.digest == staged.digest else "changed"
        identity = WindowsManagedFileStore._confirmed_identity_or_none(target)
        if (
            identity is not None
            and (snapshot.volume_serial, snapshot.file_id) == identity
            and snapshot.digest == target.expected_existing_digest
        ):
            return "confirmed"
        return "changed"

    @staticmethod
    def _file_state(path: Path, *, identity: tuple[int, int], digest: str | None) -> str:
        """文件事实：``absent`` / ``match``（身份与摘要都与回执一致）/ ``changed``。"""

        try:
            snapshot = _snapshot(path)
        except OSError:
            return "changed"
        if not snapshot.exists:
            return "absent"
        if (snapshot.volume_serial, snapshot.file_id) != identity:
            return "changed"
        return "match" if digest is not None and snapshot.digest == digest else "changed"

    @staticmethod
    def _restore_superseded(
        superseded: _GuardedFile | None,
        backup_path: Path | None,
        target_path: Path,
    ) -> bool:
        """把已移开的旧目标放回原位；目标名已被第三方占用时不覆盖，返回 False。"""

        if superseded is None or backup_path is None:
            return True
        try:
            if target_path.exists():
                return False
        except OSError:
            return False
        try:
            superseded.move_to(target_path)
        except OSError:
            return False
        return True

    @staticmethod
    def _assert_target_matches(snapshot: FileSnapshot, target: DeliveryTarget, *, phase: str) -> None:
        if snapshot.is_reparse_point or snapshot.parent_is_reparse_point:
            raise DeliveryFileError("delivery_target_reparse", "target or its parent is a reparse point")
        if target.expected_existing:
            if target.expected_volume_serial is None or target.expected_file_id is None:
                # 覆盖确认必须绑定原文件身份：仅凭摘要会让「同内容异文件」沿用旧确认（I79）。
                raise DeliveryFileError(
                    "delivery_confirmation_identity_missing",
                    "overwrite confirmation does not carry the confirmed target file identity",
                )
            if not snapshot.exists:
                raise DeliveryFileError("delivery_target_changed", f"target disappeared before {phase}")
            if snapshot.digest != target.expected_existing_digest:
                raise DeliveryFileError("delivery_target_changed", f"target content changed before {phase}")
            if (snapshot.volume_serial, snapshot.file_id) != (
                target.expected_volume_serial,
                target.expected_file_id,
            ):
                raise DeliveryFileError(
                    "delivery_target_changed", f"target is not the file confirmed by the user before {phase}"
                )
        elif snapshot.exists:
            raise DeliveryFileError("delivery_target_changed", f"target appeared before {phase}")
