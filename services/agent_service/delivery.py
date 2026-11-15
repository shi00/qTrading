"""Windows 目标保护与安全成果交付（E-05 / §10.5 / HANDOFF §10.2 / DD-G03）。

合同：
- 暂存文件必须与目标同卷（``target.parent``），先完整写入、fsync、再回读摘要；
- 发布前重新核验目标「是否存在 + 内容摘要」与用户确认一致，父目录身份未变；
- 目标是重解析点（符号链接/junction）时拒绝覆盖；
- 发布后用 ``os.replace`` 的目标重新计算摘要；摘要不符即 ``unknown``——不把
  无法确证的结果报告成发布成功；
- 取消只清理本交付暂存文件，不触碰目标。

禁止事项：不执行任意 Shell、不写确认范围外路径、不把暂存文件当成功成果、
不在事件循环线程直接调用本模块（调用方须经 ``ThreadPoolManager``）。
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.agent_types import DeliveryTarget, validate_delivery_target_path

_READ_CHUNK = 1024 * 1024


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
    """已落盘并通过回读校验的暂存内容（含暂存文件自身身份，用于发布后确证归属）。"""

    path: Path
    digest: str
    size: int
    volume_serial: int
    file_id: int
    parent_volume_serial: int
    parent_file_id: int


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


class WindowsManagedFileStore:
    """同卷暂存 + 复核 + 原子替换的本地文件交付实现。"""

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
        self._assert_target_matches(_snapshot(target_path), target, phase="stage")
        prefix = f".{target_path.name}." if not name_hint else f".{target_path.name}.{name_hint}."
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
                file_id=staged_stat.st_ino,
                parent_volume_serial=staged_stat.st_dev,
                parent_file_id=os.stat(target_path.parent).st_ino,
            )
        except BaseException:
            staged_path.unlink(missing_ok=True)
            raise

    def publish(self, *, target: DeliveryTarget, staged: StagedFile) -> PublishResult:
        """复核目标确认依据后原子替换，并回读目标内容摘要。"""

        target_path = Path(validate_delivery_target_path(target.target_path))
        if not staged.path.is_file():
            raise DeliveryFileError("delivery_stage_missing", "staged content is no longer available")
        if staged.digest != _digest_file(staged.path):
            raise DeliveryFileError("delivery_stage_changed", "staged content changed before publish")
        before = _snapshot(target_path)
        self._assert_target_matches(before, target, phase="publish")
        if before.parent_volume_serial != staged.parent_volume_serial or before.parent_file_id != staged.parent_file_id:
            raise DeliveryFileError("delivery_target_parent_changed", "target parent changed since confirmation")
        if staged.path.parent != target_path.parent:
            raise DeliveryFileError("delivery_stage_not_same_volume", "staged content is not on the target volume")
        try:
            os.replace(staged.path, target_path)
        except OSError as exc:
            # 替换可能已生效也可能未生效；内容相同不能证明「属于本交付」，
            # 只能进入 unknown（HANDOFF §10.2：同内容不等于本交付已发布）。
            raise DeliveryUnknownError(
                "delivery_publish_unverified", "target replace failed and effect cannot be verified"
            ) from exc
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
        return PublishResult(snapshot=after)

    def cancel(self, staged: StagedFile) -> bool:
        """取消发布：只删除本交付暂存文件；返回是否实际删除。"""

        if staged.path.is_file():
            staged.path.unlink()
            return True
        return False

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
