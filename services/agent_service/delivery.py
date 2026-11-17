"""Windows 目标保护与安全成果交付（E-05 / §10.5 / HANDOFF §10.2 / DD-G03）。

合同：
- 暂存文件必须与目标同卷（``target.parent``），先完整写入、fsync、再回读摘要；
- 发布前在**独占句柄**上核验目标「是否存在 + 文件身份 + 内容摘要」与用户确认一致，
  父目录身份未变；目标是重解析点（符号链接/junction）时拒绝覆盖；
- 覆盖只作用于「用户确认过的那个文件」：句柄身份不符（同摘要异文件、预检后被
  置换）即拒绝，绝不按路径重新猜测；确认过的目标先移到同目录私有备份名，再把
  暂存文件以「不覆盖」方式移入，因此任何第三方新文件都不会被本次发布覆掉；
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
- POSIX：无独占共享语义，退化为描述符 ``fstat`` 核对身份 + ``link``/``rename``
  不覆盖移入（尽力而为；本模块的产品平台为 Windows）。

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
    """发布确证结果：目标最终身份与内容摘要。"""

    snapshot: FileSnapshot


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


def _require_identity(snapshot: FileSnapshot) -> tuple[int, int]:
    """快照 → (卷序列号, 文件 ID)；身份缺失即拒绝（不得退化为「同内容即同一文件」）。"""

    if snapshot.volume_serial is None or snapshot.file_id is None or snapshot.file_id == 0:
        raise DeliveryFileError(
            "delivery_file_identity_unavailable", "target volume does not expose a stable file identity"
        )
    return int(snapshot.volume_serial), int(snapshot.file_id)


def _stage_prefix(target_name: str, name_hint: str | None) -> str:
    if name_hint is None:
        return f".{target_name}."
    if not _NAME_HINT_PATTERN.fullmatch(name_hint):
        raise DeliveryFileError(
            "delivery_stage_name_invalid", "staged name hint must be 1-32 characters of [A-Za-z0-9_-]"
        )
    return f".{target_name}.{name_hint}."


def _superseded_name(target_path: Path) -> Path:
    """被替换目标的私有备份名（同目录、随机后缀，避免与用户文件名冲突）。"""

    return target_path.with_name(f".{target_path.name}.superseded.{os.urandom(8).hex()}")


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
            return int(msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)))
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
        handle = msvcrt.get_osfhandle(fd)
        if not self._kernel32.SetFileInformationByHandle(handle, _WIN_FILE_RENAME_INFO, buffer, size):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[reportAttributeAccessIssue]  # Windows-only：WinError 在 Linux 平台不存在

    def delete_on_close(self, fd: int) -> None:  # pragma: no cover -- win32 原生 API
        """给句柄打删除标记（close 时删除该句柄指向的文件，按句柄而非按路径）。"""

        import ctypes
        import msvcrt

        info = self._disposition_info(DeleteFile=True)
        handle = msvcrt.get_osfhandle(fd)
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

    改名/删除都作用在句柄上（Windows）或先经 ``fstat`` 核对再按路径执行
    （POSIX），因此调用方可以先核身份、再执行变更，而不会把「核验过的文件」
    与「真正被变更的文件」分开。
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
            return
        os.link(self._path, destination)
        os.unlink(self._path)

    def delete(self) -> None:
        if _IS_WINDOWS:
            _win32_api().delete_on_close(self._fd)
            return
        os.unlink(self._path)

    def close(self) -> None:
        os.close(self._fd)

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
        confirmed_identity = _require_identity(snapshot) if snapshot.exists else (None, None)
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
        """独占核验目标身份后不覆盖移入，并回读目标内容摘要与文件身份。"""

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

        staged_guard = self._open_staged(staged)
        superseded: _GuardedFile | None = None
        backup_path: Path | None = None
        try:
            self._verify_guard(
                staged_guard,
                identity=(staged.volume_serial, staged.file_id),
                digest=staged.digest,
                code="delivery_stage_changed",
                message="staged content changed before publish",
            )
            if target.expected_existing:
                superseded = self._open_confirmed_target(target_path, target, staged)
                backup_path = _superseded_name(target_path)
                try:
                    superseded.move_to(backup_path)
                except OSError as exc:
                    raise DeliveryFileError(
                        "delivery_target_changed", "target cannot be superseded by the confirmed publish"
                    ) from exc
            try:
                staged_guard.move_to(target_path)
            except FileExistsError as exc:
                if self._restore_superseded(superseded, backup_path, target_path):
                    raise DeliveryFileError(
                        "delivery_target_changed", "target appeared between verification and publish"
                    ) from exc
                raise DeliveryUnknownError(
                    "delivery_publish_unverified", "target appeared and the superseded file cannot be restored"
                ) from exc
            except OSError as exc:
                self._restore_superseded(superseded, backup_path, target_path)
                raise DeliveryUnknownError(
                    "delivery_publish_unverified", "target publish failed and effect cannot be verified"
                ) from exc
        finally:
            with contextlib.suppress(OSError):
                staged_guard.close()
            if superseded is not None:
                with contextlib.suppress(OSError):
                    superseded.close()

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
        if backup_path is not None:
            # 覆盖已确证；旧目标是用户确认可替换的内容，清理备份（残留不影响发布结论）。
            with contextlib.suppress(OSError):
                backup_path.unlink()
        return PublishResult(snapshot=after)

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
    ) -> _GuardedFile:
        """独占打开目标并核验「就是确认时那个文件」；不符即拒绝，绝不按路径臆测。"""

        if staged.confirmed_target_volume_serial is None or staged.confirmed_target_file_id is None:
            raise DeliveryFileError("delivery_target_changed", "staged record has no confirmed target identity")
        identity = (staged.confirmed_target_volume_serial, staged.confirmed_target_file_id)
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
        return guard

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
            if not snapshot.exists:
                raise DeliveryFileError("delivery_target_changed", f"target disappeared before {phase}")
            if snapshot.digest != target.expected_existing_digest:
                raise DeliveryFileError("delivery_target_changed", f"target content changed before {phase}")
        elif snapshot.exists:
            raise DeliveryFileError("delivery_target_changed", f"target appeared before {phase}")
