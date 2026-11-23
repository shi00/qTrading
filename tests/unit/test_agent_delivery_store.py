"""WindowsManagedFileStore 平台中性单测（E-06 delivery.py）。

集成层交付编排用例（tests/integration/test_agent_delivery_service.py）标注
win32-only（交付目标保护语义在 Windows 上验证）；但 ``delivery.py`` 实现本身
跨平台（os.lstat/st_ino 文件身份 + link/rename 不覆盖移入），核心保护逻辑两
平台皆可测：同卷暂存、回读校验、目标确认核验（含同摘要异文件与预检后置换）、
父目录身份一致性、不覆盖移入、unknown 语义、取消身份核定。

路径适配：``validate_delivery_target_path`` 强制 Windows 规范路径（``C:\\...``），
而 Windows 盘符路径在 POSIX 上无法映射为真实文件系统路径。store 逻辑与盘符
校验正交——本文件 fixture 把该校验替换为恒等，使两平台均在各自 ``tmp_path``
真实文件系统（st_ino 提供稳定文件身份）上执行 stage/publish 全流程；
``C:\\...`` 校验语义另由 core.agent_types 单测与 win32-only 集成测试覆盖。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_types import DeliveryTarget
from services.agent_service import delivery as delivery_module
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    FileSnapshot,
    StagedFile,
    WindowsManagedFileStore,
    _file_identity,
    _GuardedFile,
    _snapshot,
    _stage_prefix,
)

_CONTENT = b"report-body-7f3a"
_DIGEST = hashlib.sha256(_CONTENT).hexdigest()


@pytest.fixture()
def store_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Windows 盘符路径校验替换为恒等，两平台统一在 tmp_path 真实执行。"""

    monkeypatch.setattr("services.agent_service.delivery.validate_delivery_target_path", lambda p: p)
    return tmp_path


def _target(
    root: Path,
    *,
    exists: bool = False,
    digest: str | None = None,
    identity: tuple[int, int] | None = None,
) -> DeliveryTarget:
    """构造目标；``exists=True`` 时绑定当前文件身份（覆盖确认必须带原文件身份）。"""

    target_path = root / "out.txt"
    volume_serial: int | None = None
    file_id: int | None = None
    if exists:
        if identity is None:
            stat_result = os.stat(target_path)
            identity = (int(stat_result.st_dev), _file_identity(stat_result))
        volume_serial, file_id = identity
    return DeliveryTarget(
        target_path=str(target_path),
        expected_existing=exists,
        expected_existing_digest=digest,
        overwrite_confirmed=True,
        expected_volume_serial=volume_serial,
        expected_file_id=file_id,
    )


def _swap_after_first_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    target_file: Path,
    *,
    content: bytes = _CONTENT,
) -> None:
    """在 publish 首次目标快照返回后把目标换成新文件（新文件 ID），模拟预检后置换。"""

    calls = {"count": 0}

    def racing_snapshot(path: Path, *, with_digest: bool = True) -> FileSnapshot:
        snapshot = _snapshot(path, with_digest=with_digest)
        calls["count"] += 1
        if calls["count"] == 1:
            replacement = root / "racing.tmp"
            replacement.write_bytes(content)
            os.replace(replacement, target_file)
        return snapshot

    monkeypatch.setattr(delivery_module, "_snapshot", racing_snapshot)


class TestStagePrefix:
    def test_no_hint(self):
        assert _stage_prefix("out.txt", None) == ".out.txt."

    def test_valid_hint(self):
        assert _stage_prefix("out.txt", "run-1") == ".out.txt.run-1."

    @pytest.mark.parametrize("bad", ["", "含中文", "a" * 33, "bad space", "bad.dot"])
    def test_invalid_hint_rejected(self, bad: str):
        with pytest.raises(DeliveryFileError) as exc:
            _stage_prefix("out.txt", bad)
        assert exc.value.code == "delivery_stage_name_invalid"


class TestFileIdentity:
    def test_zero_inode_fail_closed(self):
        with pytest.raises(DeliveryFileError) as exc:
            _file_identity(SimpleNamespace(st_ino=0))
        assert exc.value.code == "delivery_file_identity_unavailable"

    def test_nonzero_inode_returned(self):
        assert _file_identity(SimpleNamespace(st_ino=42)) == 42


class TestStage:
    def test_stage_creates_staged_file_and_verifies(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST, name_hint="run1")
        assert staged.path.is_file()
        assert staged.path.name.startswith(".out.txt.run1.")
        assert staged.digest == _DIGEST
        assert staged.size == len(_CONTENT)
        assert staged.parent_file_id != 0
        store.cancel(staged)

    def test_stage_digest_mismatch_cleans_up(self, store_env: Path):
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(store_env), content=_CONTENT, expected_digest="0" * 64)
        assert exc.value.code == "delivery_stage_readback_failed"
        assert not list(store_env.glob("*.staged"))

    def test_stage_parent_missing(self, store_env: Path):
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(store_env / "no-such-dir"), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_parent_missing"

    def test_stage_rejects_existing_target_without_confirmation_basis(self, store_env: Path):
        (store_env / "out.txt").write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(store_env, exists=False), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_changed"

    def test_stage_rejects_existing_target_with_wrong_digest(self, store_env: Path):
        (store_env / "out.txt").write_bytes(b"stale")
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(
                target=_target(store_env, exists=True, digest="1" * 64), content=_CONTENT, expected_digest=_DIGEST
            )
        assert exc.value.code == "delivery_target_changed"

    def test_stage_rejects_confirmation_without_file_identity(self, store_env: Path):
        """仅带摘要的覆盖确认（旧记录）不得沿用：缺原文件身份即 fail-closed。"""

        (store_env / "out.txt").write_bytes(_CONTENT)
        legacy = DeliveryTarget(
            target_path=str(store_env / "out.txt"),
            expected_existing=True,
            expected_existing_digest=_DIGEST,
            overwrite_confirmed=True,
        )
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=legacy, content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_confirmation_identity_missing"
        assert not list(store_env.glob("*.staged"))

    def test_stage_refuses_same_content_different_file_after_confirmation(self, store_env: Path):
        """确认与暂存之间同内容换文件（文件 ID 变了）：原确认失效，必须重新确认。"""

        target_file = store_env / "out.txt"
        target_file.write_bytes(_CONTENT)
        confirmed = _target(store_env, exists=True, digest=_DIGEST)
        replacement = store_env / "same-content.tmp"
        replacement.write_bytes(_CONTENT)
        os.replace(replacement, target_file)
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=confirmed, content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_changed"
        assert target_file.read_bytes() == _CONTENT
        assert not list(store_env.glob("*.staged"))

    def test_stage_accepts_existing_target_with_matching_digest(self, store_env: Path):
        (store_env / "out.txt").write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        staged = store.stage(
            target=_target(store_env, exists=True, digest=_DIGEST), content=_CONTENT, expected_digest=_DIGEST
        )
        store.cancel(staged)


class TestPublish:
    def _stage(self, store: WindowsManagedFileStore, root: Path) -> StagedFile:
        return store.stage(target=_target(root), content=_CONTENT, expected_digest=_DIGEST)

    def test_publish_replaces_target_and_confirms_identity(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        result = store.publish(target=_target(store_env), staged=staged)
        assert result.snapshot.exists
        assert result.snapshot.digest == _DIGEST
        assert not staged.path.exists()
        assert (store_env / "out.txt").read_bytes() == _CONTENT

    def test_publish_missing_staged_fails(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        staged.path.unlink()
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_stage_missing"

    def test_publish_changed_staged_fails(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        staged.path.write_bytes(b"tampered")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_stage_changed"

    def test_publish_replaced_staged_file_fails(self, store_env: Path):
        """暂存文件被换成同内容的新文件（新文件 ID）：不得按路径当成本交付的暂存。"""

        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        replacement = store_env / "foreign-stage.tmp"
        replacement.write_bytes(_CONTENT)
        os.replace(replacement, staged.path)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_stage_changed"
        assert staged.path.read_bytes() == _CONTENT
        assert not (store_env / "out.txt").exists()

    def test_publish_target_appeared_fails(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        (store_env / "out.txt").write_bytes(b"raced-in")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_target_changed"

    def test_publish_target_parent_changed_fails(self, store_env: Path):
        """父目录被移走并以新目录顶替（新身份）；两平台皆可模拟。"""

        store = WindowsManagedFileStore()
        work = store_env / "sub"
        work.mkdir()
        staged = self._stage(store, work)
        # 父目录被移走并以新目录顶替（新身份）；staged 记录路径同步更新到
        # 实际新位置，保留 stage 时记录的旧父目录身份
        os.rename(work, store_env / "moved-away")
        work.mkdir()
        staged = dataclasses.replace(staged, path=(store_env / "moved-away") / staged.path.name)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(work), staged=staged)
        assert exc.value.code == "delivery_target_parent_changed"

    def test_publish_symlink_target_fails(self, store_env: Path):
        if sys.platform == "win32":
            pytest.skip("os.symlink 需要特权；reparse 点拒绝语义由 win32 集成测试覆盖")
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        # 目标路径 out.txt 变为符号链接 → expected_existing=False 时目标「出现」即失效
        os.symlink(store_env / "real.txt", store_env / "out.txt")
        (store_env / "real.txt").write_bytes(_CONTENT)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_target_changed"

    def test_publish_rejects_foreign_stage_directory(self, store_env: Path):
        """staged 不在目标父目录（同卷路径不同目录）→ 拒绝发布。"""

        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        foreign = store_env / "elsewhere"
        foreign.mkdir()
        moved = foreign / staged.path.name
        os.replace(staged.path, moved)
        staged = dataclasses.replace(staged, path=moved)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_stage_not_same_volume"

    def test_publish_refuses_same_digest_different_file(self, store_env: Path):
        """同摘要异文件（内容一致但文件 ID 不同）不得作为「确认过的目标」被覆掉。"""

        target_file = store_env / "out.txt"
        target_file.write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        staged = store.stage(
            target=_target(store_env, exists=True, digest=_DIGEST), content=_CONTENT, expected_digest=_DIGEST
        )
        replacement = store_env / "same-content.tmp"
        replacement.write_bytes(_CONTENT)
        os.replace(replacement, target_file)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env, exists=True, digest=_DIGEST), staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_file.read_bytes() == _CONTENT
        assert staged.path.read_bytes() == _CONTENT

    def test_publish_detects_target_swap_after_precheck(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """预检返回后、移入之前被外部置换：独占句柄按文件身份发现并拒绝。"""

        target_file = store_env / "out.txt"
        target_file.write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        staged = store.stage(
            target=_target(store_env, exists=True, digest=_DIGEST), content=_CONTENT, expected_digest=_DIGEST
        )
        _swap_after_first_snapshot(monkeypatch, store_env, target_file)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env, exists=True, digest=_DIGEST), staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_file.read_bytes() == _CONTENT
        assert staged.path.read_bytes() == _CONTENT

    def test_publish_refuses_target_appearing_in_move_window(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """创建目标时预检后出现第三方文件：不覆盖移入失败即拒绝，文件原样保留。"""

        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        target_file = store_env / "out.txt"
        _swap_after_first_snapshot(monkeypatch, store_env, target_file, content=b"raced-in")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_file.read_bytes() == b"raced-in"
        assert staged.path.read_bytes() == _CONTENT

    def test_publish_unknown_when_move_oserror(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)
        target_file = store_env / "out.txt"

        def broken_move(self: _GuardedFile, destination: Path) -> None:
            if destination == target_file:
                raise OSError("simulated move failure")
            _real_move(self, destination)

        _real_move = _GuardedFile.move_to
        monkeypatch.setattr(_GuardedFile, "move_to", broken_move)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert not target_file.exists()

    def test_publish_overwrite_leaves_no_superseded_residue(self, store_env: Path):
        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=old_digest)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        store.publish(target=target, staged=staged)
        assert target_file.read_bytes() == _CONTENT
        assert list(store_env.glob("*.superseded.*")) == []


class TestConditionalSupersede:
    """「移开已确认目标」的条件性：核验后窗口内的外部新文件不得被当旧目标移走或删除。"""

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        stat_result = os.stat(path)
        return int(stat_result.st_dev), _file_identity(stat_result)

    @staticmethod
    def _verified_guard(path: Path) -> _GuardedFile:
        """核验后即释放句柄：无独占共享语义的平台不以句柄加锁，复现「核验之后」的窗口。"""

        guard = _GuardedFile(path=path, fd=os.open(path, os.O_RDONLY))
        guard.close()
        return guard

    @pytest.mark.parametrize("foreign", [b"external-new", b"old"])
    def test_supersede_returns_foreign_file_in_window(
        self, store_env: Path, monkeypatch: pytest.MonkeyPatch, foreign: bytes
    ):
        """核验后目标被外部置换：移走的不是已确认文件，必须放回并失败（同内容换文件同此）。"""

        monkeypatch.setattr(delivery_module, "_IS_WINDOWS", False)
        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        confirmed_identity = self._identity(target_file)
        guard = self._verified_guard(target_file)
        replacement = store_env / "external.tmp"
        replacement.write_bytes(foreign)
        os.replace(replacement, target_file)
        backup = store_env / ".out.txt.superseded.0123456789abcdef"
        with pytest.raises(DeliveryFileError) as exc:
            guard.supersede_to(backup, expected_identity=confirmed_identity)
        assert exc.value.code == "delivery_target_changed"
        assert target_file.read_bytes() == foreign
        assert not backup.exists()

    def test_supersede_moves_the_verified_file(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(delivery_module, "_IS_WINDOWS", False)
        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        identity = self._identity(target_file)
        guard = self._verified_guard(target_file)
        backup = store_env / ".out.txt.superseded.0123456789abcdef"
        guard.supersede_to(backup, expected_identity=identity)
        assert not target_file.exists()
        assert backup.read_bytes() == b"old"

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows 由独占句柄阻断，见下方 win32 用例")
    def test_publish_posix_window_refuses_foreign_file(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """发布流程：身份核验后、移开之前被外部置换 → 拒绝发布，外部新文件保持原位。"""

        monkeypatch.setattr(delivery_module, "_IS_WINDOWS", False)
        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=old_digest)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        real_replace = os.replace
        injected = {"done": False}

        def racing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
            if not injected["done"] and Path(source) == target_file:
                injected["done"] = True
                replacement = store_env / "external.tmp"
                replacement.write_bytes(b"external-new")
                real_replace(replacement, target_file)
            real_replace(source, destination)

        monkeypatch.setattr(delivery_module.os, "replace", racing_replace)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_file.read_bytes() == b"external-new"
        assert staged.path.read_bytes() == _CONTENT
        assert list(store_env.glob("*.superseded.*")) == []

    def test_publish_refuses_when_backup_name_is_taken(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """备份名已被他人占用：拒绝发布且不覆盖该文件、不移走已确认目标。"""

        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        occupied = store_env / ".out.txt.superseded.occupied"
        occupied.write_bytes(b"someone-else")
        monkeypatch.setattr(delivery_module, "_superseded_name", lambda path, **_: occupied)
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=old_digest)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert occupied.read_bytes() == b"someone-else"
        assert target_file.read_bytes() == b"old"
        assert staged.path.read_bytes() == _CONTENT

    def test_windows_overwrite_never_splits_into_two_steps(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """Windows 覆盖已确认目标由平台单步完成：不得先移走旧目标再移入（P1-12）。"""

        if sys.platform != "win32":
            pytest.skip("单步原子替换仅在 Windows 成立")
        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=old_digest)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        calls = {"supersede": 0, "move": 0}
        real_supersede = _GuardedFile.supersede_to
        real_move = _GuardedFile.move_to

        def spy_supersede(self: _GuardedFile, destination: Path, *, expected_identity: tuple[int, int]) -> None:
            calls["supersede"] += 1
            real_supersede(self, destination, expected_identity=expected_identity)

        def spy_move(self: _GuardedFile, destination: Path) -> None:
            calls["move"] += 1
            real_move(self, destination)

        monkeypatch.setattr(_GuardedFile, "supersede_to", spy_supersede)
        monkeypatch.setattr(_GuardedFile, "move_to", spy_move)
        result = store.publish(target=target, staged=staged)
        assert calls == {"supersede": 0, "move": 0}
        assert result.residue == ()
        assert target_file.read_bytes() == _CONTENT
        assert list(store_env.glob("*.superseded.*")) == []


class TestPublishRecovery:
    """中断后只能按原回执复核阶段并续接：不默认覆盖、不留未报告残余（P1-12）。"""

    @staticmethod
    def _confirmed_target(root: Path, content: bytes = b"old") -> DeliveryTarget:
        (root / "out.txt").write_bytes(content)
        return _target(root, exists=True, digest=hashlib.sha256(content).hexdigest())

    @staticmethod
    def _backup_path(root: Path, staged: StagedFile) -> Path:
        return delivery_module._superseded_name(
            root / "out.txt", volume_serial=staged.volume_serial, file_id=staged.file_id
        )

    def test_interrupted_after_supersede_is_resumable(self, store_env: Path):
        """「已移开原目标、尚未移入」的硬退出：目标名无文件，但原文件可按回执核验并续接。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = self._backup_path(store_env, staged)
        os.replace(store_env / "out.txt", backup)

        recovery = store.inspect_publish(target=target, staged=staged)
        assert (recovery.target_state, recovery.staged_state, recovery.backup_state) == ("absent", "match", "match")
        assert recovery.resumable is True
        assert recovery.backup_path == backup
        assert backup.read_bytes() == b"old"

        result = store.resume_publish(target=target, staged=staged)
        assert result.snapshot.digest == _DIGEST
        assert result.residue == ()
        assert (store_env / "out.txt").read_bytes() == _CONTENT
        assert not backup.exists()
        assert not staged.path.exists()

    def test_interrupted_after_replace_finalizes_without_republishing(self, store_env: Path):
        """「已替换、备份尚未清理」：续接只清理可核备份，不再动目标。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = self._backup_path(store_env, staged)
        os.replace(store_env / "out.txt", backup)
        os.replace(staged.path, store_env / "out.txt")

        recovery = store.inspect_publish(target=target, staged=staged)
        assert (recovery.target_state, recovery.staged_state, recovery.backup_state) == ("published", "absent", "match")
        assert recovery.resumable is True

        result = store.resume_publish(target=target, staged=staged)
        assert result.snapshot.digest == _DIGEST
        assert result.residue == ()
        assert (store_env / "out.txt").read_bytes() == _CONTENT
        assert not backup.exists()

    def test_interrupted_before_publish_retries_by_receipt(self, store_env: Path):
        """尚未动手的中断：目标仍是原确认文件，续接即按原回执完成发布。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)

        recovery = store.inspect_publish(target=target, staged=staged)
        assert (recovery.target_state, recovery.staged_state, recovery.backup_state) == (
            "confirmed",
            "match",
            "absent",
        )
        assert recovery.resumable is True

        result = store.resume_publish(target=target, staged=staged)
        assert result.snapshot.digest == _DIGEST
        assert (store_env / "out.txt").read_bytes() == _CONTENT

    def test_changed_staged_file_blocks_resume(self, store_env: Path):
        """暂存已不是回执记录的文件：拒绝续接，不按路径重做。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = self._backup_path(store_env, staged)
        os.replace(store_env / "out.txt", backup)
        staged.path.write_bytes(b"tampered")

        recovery = store.inspect_publish(target=target, staged=staged)
        assert recovery.staged_state == "changed"
        assert recovery.resumable is False
        with pytest.raises(DeliveryUnknownError) as exc:
            store.resume_publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert (store_env / "out.txt").exists() is False
        assert backup.read_bytes() == b"old"

    def test_missing_staged_restores_confirmed_target(self, store_env: Path):
        """暂存已不在：按原回执把原确认文件放回目标名，并如实报告未完成发布。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = self._backup_path(store_env, staged)
        os.replace(store_env / "out.txt", backup)
        staged.path.unlink()

        recovery = store.inspect_publish(target=target, staged=staged)
        assert (recovery.target_state, recovery.staged_state, recovery.backup_state) == ("absent", "absent", "match")
        assert recovery.resumable is True
        with pytest.raises(DeliveryFileError) as exc:
            store.resume_publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_rolled_back"
        assert (store_env / "out.txt").read_bytes() == b"old"
        assert not backup.exists()

    def test_foreign_backup_blocks_resume_and_is_kept(self, store_env: Path):
        """备份名被外部文件占用（已发布）：不删除他人文件，也不声称可续接。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = self._backup_path(store_env, staged)
        os.replace(staged.path, store_env / "out.txt")
        backup.write_bytes(b"someone-else")

        recovery = store.inspect_publish(target=target, staged=staged)
        assert recovery.target_state == "published"
        assert recovery.backup_state == "changed"
        assert recovery.resumable is False
        with pytest.raises(DeliveryUnknownError) as exc:
            store.resume_publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert backup.read_bytes() == b"someone-else"
        assert (store_env / "out.txt").read_bytes() == _CONTENT

    def test_resume_move_in_refuses_target_reappearing(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """续接移入前目标名被占：拒绝，不覆盖第三方文件，旧目标留在可核备份名。"""

        store = WindowsManagedFileStore()
        target = self._confirmed_target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = self._backup_path(store_env, staged)
        os.replace(store_env / "out.txt", backup)
        real_move = _GuardedFile.move_to

        def race_move(mover: _GuardedFile, destination: Path) -> None:
            if Path(destination) == store_env / "out.txt":
                Path(destination).write_bytes(b"third-party")
            real_move(mover, destination)

        monkeypatch.setattr(_GuardedFile, "move_to", race_move)
        with pytest.raises(DeliveryFileError) as exc:
            store.resume_publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert (store_env / "out.txt").read_bytes() == b"third-party"
        assert backup.read_bytes() == b"old"


class _FakeWin32Api:
    """在 POSIX 上模拟 ``_Win32FileApi``：真实 fd + 可注入的内核调用结果。

    Linux CI 无法执行 Windows 原生 API，但发布分支（``ReplaceFileW`` 单步替换、
    句柄改名、``FileDispositionInfo`` 删除）必须同样被覆盖：本替身把内核调用映射为
    POSIX 真实文件操作，并允许注入异常，从而在 Linux 上跑到同一段生产代码。
    真实内核语义（独占共享、只读拒绝、原子性）仍由 win32-only 用例验证。
    """

    def __init__(self) -> None:
        self.paths: dict[int, Path] = {}
        self.replace_calls: list[tuple[Path, Path, Path]] = []
        self.replace_hook: Callable[[Path, Path, Path], None] | None = None
        self.replace_errors: list[OSError] = []
        self.rename_errors: list[OSError] = []
        self.rename_hook: Callable[[Path, Path], None] | None = None
        self.rename_fail_destinations: set[Path] = set()
        self.delete_errors: list[OSError] = []
        self.delete_calls = 0
        self.open_errors: dict[Path, OSError] = {}

    def open_exclusive(self, path: Path) -> int:
        path = Path(path)
        if path in self.open_errors:
            raise self.open_errors[path]
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        self.paths[fd] = path
        return fd

    @staticmethod
    def _release(fd: int) -> None:
        """提前释放句柄，并用 devnull 占住同一描述符号。

        真实实现是「改名/删除在句柄上生效」；替身必须提前 ``close`` 才能让 Windows
        允许 rename/unlink 打开中的文件，故用同号 devnull 兜住随后
        ``_GuardedFile.close()`` 的 ``os.close``，避免误关被复用的描述符号。
        """

        os.close(fd)
        spare = os.open(os.devnull, os.O_RDONLY)
        if spare != fd:
            os.dup2(spare, fd)
            os.close(spare)

    def rename(self, fd: int, destination: Path) -> None:
        destination = Path(destination)
        if self.rename_errors:
            raise self.rename_errors.pop(0)
        if destination in self.rename_fail_destinations:
            raise OSError("destination is locked")
        source = self.paths[fd]
        self._release(fd)
        os.link(source, destination)  # 目标已存在 → FileExistsError（不覆盖）
        os.unlink(source)
        self.paths[fd] = destination
        if self.rename_hook is not None:
            self.rename_hook(source, destination)

    def delete_on_close(self, fd: int) -> None:
        self.delete_calls += 1
        if self.delete_errors:
            raise self.delete_errors.pop(0)
        path = self.paths.pop(fd)
        self._release(fd)
        os.unlink(path)

    def replace_file(self, replaced: Path, replacement: Path, backup: Path) -> None:
        replaced, replacement, backup = Path(replaced), Path(replacement), Path(backup)
        self.replace_calls.append((replaced, replacement, backup))
        if self.replace_hook is not None:
            self.replace_hook(replaced, replacement, backup)
        if self.replace_errors:
            raise self.replace_errors.pop(0)
        os.replace(replaced, backup)
        os.replace(replacement, replaced)


def _staged_stub(**overrides: object) -> StagedFile:
    """构造回执替身（不落盘），用于直接调用私有核验函数。"""

    values: dict[str, object] = {
        "path": Path("staged.bin"),
        "digest": _DIGEST,
        "size": len(_CONTENT),
        "volume_serial": 11,
        "file_id": 22,
        "parent_volume_serial": 33,
        "parent_file_id": 44,
        "confirmed_target_volume_serial": 55,
        "confirmed_target_file_id": 66,
    }
    values.update(overrides)
    return StagedFile(**values)  # type: ignore[arg-type]  # 测试替身按字段名逐项覆盖，键集合由 StagedFile 保证


@pytest.mark.usefixtures("store_env")
class TestSimulatedWindowsPublish:
    """模拟 Windows 内核调用的发布分支（Linux CI 必须能跑到）。"""

    @pytest.fixture()
    def fake_windows(self, store_env: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeWin32Api:
        fake = _FakeWin32Api()
        monkeypatch.setattr(delivery_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(delivery_module, "_WIN32_API", fake)
        return fake

    @staticmethod
    def _setup(
        store_env: Path, old: bytes = b"old"
    ) -> tuple[WindowsManagedFileStore, DeliveryTarget, StagedFile, Path]:
        (store_env / "out.txt").write_bytes(old)
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=hashlib.sha256(old).hexdigest())
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        backup = delivery_module._superseded_name(
            store_env / "out.txt", volume_serial=staged.volume_serial, file_id=staged.file_id
        )
        return store, target, staged, backup

    def test_atomic_replace_is_single_step_and_drops_verified_backup(
        self, store_env: Path, fake_windows: _FakeWin32Api
    ):
        """平台单步替换被真正使用：旧目标只按句柄删除一次，无残余。"""

        store, target, staged, backup = self._setup(store_env)
        result = store.publish(target=target, staged=staged)
        assert fake_windows.replace_calls == [(store_env / "out.txt", staged.path, backup)]
        assert fake_windows.delete_calls == 1
        assert result.residue == ()
        assert (store_env / "out.txt").read_bytes() == _CONTENT
        assert not backup.exists()
        assert not staged.path.exists()

    def test_atomic_replace_reports_applied_even_when_signalled_error(
        self, store_env: Path, fake_windows: _FakeWin32Api
    ):
        """平台报错但事实证明已替换：按事实继续，不凭错误码判失败。"""

        store, target, staged, _ = self._setup(store_env)

        def applied_then_error(replaced: Path, replacement: Path, backup: Path) -> None:
            os.replace(replaced, backup)
            os.replace(replacement, replaced)

        fake_windows.replace_hook = applied_then_error
        fake_windows.replace_errors = [OSError("platform reported failure")]
        result = store.publish(target=target, staged=staged)
        assert (store_env / "out.txt").read_bytes() == _CONTENT
        assert result.residue == ()

    def test_atomic_replace_refused_falls_back_to_two_step(self, store_env: Path, fake_windows: _FakeWin32Api):
        """平台拒绝（如只读目标）且事实未变：退回两步法完成发布，能力不回退。"""

        store, target, staged, backup = self._setup(store_env)
        fake_windows.replace_errors = [OSError("access denied")]
        result = store.publish(target=target, staged=staged)
        assert len(fake_windows.replace_calls) == 1
        assert (store_env / "out.txt").read_bytes() == _CONTENT
        assert result.residue == ()
        assert not backup.exists()

    def test_target_appearing_before_move_in_blocks_publish(self, store_env: Path, fake_windows: _FakeWin32Api):
        """两步法窗口内目标被第三方占位：不覆盖、不放回失败的旧目标，只能 unknown。"""

        store, target, staged, backup = self._setup(store_env)
        fake_windows.replace_errors = [OSError("access denied")]

        def place_foreign(source: Path, destination: Path) -> None:
            if destination == backup:
                (store_env / "out.txt").write_bytes(b"third-party")

        fake_windows.rename_hook = place_foreign
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert (store_env / "out.txt").read_bytes() == b"third-party"
        assert staged.path.read_bytes() == _CONTENT
        assert backup.read_bytes() == b"old"

    def test_move_in_oserror_after_supersede_is_unknown(self, store_env: Path, fake_windows: _FakeWin32Api):
        """移开旧目标后移入失败：只能 unknown，旧目标留在可核备份名。"""

        store, target, staged, backup = self._setup(store_env)
        fake_windows.replace_errors = [OSError("access denied")]
        fake_windows.rename_fail_destinations.add(store_env / "out.txt")
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert backup.read_bytes() == b"old"
        assert staged.path.read_bytes() == _CONTENT

    def test_replace_failure_with_missing_target_is_unknown(self, store_env: Path, fake_windows: _FakeWin32Api):
        """替换报错后目标已不存在：无法证明替换未发生，只能 unknown。"""

        store, target, staged, _ = self._setup(store_env)
        fake_windows.replace_hook = lambda replaced, replacement, backup: replaced.unlink()
        fake_windows.replace_errors = [OSError("access denied")]
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert staged.path.read_bytes() == _CONTENT

    def test_foreign_file_displaced_by_replace_is_restored(self, store_env: Path, fake_windows: _FakeWin32Api):
        """窗口内目标被换成外部文件：回滚还原外部文件、本次内容退回暂存名，不删他人文件。"""

        store, target, staged, backup = self._setup(store_env)

        def swap_before_replace(replaced: Path, replacement: Path, backup_path: Path) -> None:
            replaced.unlink()
            replaced.write_bytes(b"external-new")

        fake_windows.replace_hook = swap_before_replace
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert (store_env / "out.txt").read_bytes() == b"external-new"
        assert staged.path.read_bytes() == _CONTENT
        assert not backup.exists()
        assert fake_windows.delete_calls == 0

    def test_unverifiable_backup_with_failed_rollback_is_unknown(self, store_env: Path, fake_windows: _FakeWin32Api):
        """备份身份不符且回滚失败：保持现场并报告残余，不静默丢弃。"""

        store, target, staged, backup = self._setup(store_env)

        def swap_before_replace(replaced: Path, replacement: Path, backup_path: Path) -> None:
            replaced.unlink()
            replaced.write_bytes(b"external-new")

        fake_windows.replace_hook = swap_before_replace
        fake_windows.rename_errors = [OSError("rollback locked")]
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert backup.exists()

    def test_backup_open_failure_is_reported_as_residue(self, store_env: Path, fake_windows: _FakeWin32Api):
        """备份无法独占复核：不删除、如实返回残余路径。"""

        store, target, staged, backup = self._setup(store_env)
        fake_windows.open_errors[backup] = PermissionError("held by another process")
        result = store.publish(target=target, staged=staged)
        assert result.residue == (backup,)
        assert backup.exists()
        assert fake_windows.delete_calls == 0

    def test_backup_delete_failure_is_reported_as_residue(self, store_env: Path, fake_windows: _FakeWin32Api):
        """只读备份两次删除都失败：发布已确证但残余必须如实返回。"""

        store, target, staged, backup = self._setup(store_env)
        fake_windows.delete_errors = [OSError("readonly"), OSError("readonly")]
        result = store.publish(target=target, staged=staged)
        assert result.residue == (backup,)
        assert backup.exists()
        assert fake_windows.delete_calls == 2

    def test_backup_delete_retries_after_readonly_attribute(self, store_env: Path, fake_windows: _FakeWin32Api):
        """只读旧目标：清只读位后重试删除成功，不留残余。"""

        store, target, staged, backup = self._setup(store_env)
        fake_windows.delete_errors = [OSError("readonly")]
        result = store.publish(target=target, staged=staged)
        assert result.residue == ()
        assert not backup.exists()
        assert fake_windows.delete_calls == 2

    def test_confirmed_identity_requires_bound_file_identity(self, store_env: Path):
        """覆盖确认未带原文件身份：拒绝，不凭摘要沿用旧确认。"""

        target = DeliveryTarget(
            target_path="C:/out.txt",
            expected_existing=True,
            expected_existing_digest=_DIGEST,
            overwrite_confirmed=True,
        )
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._confirmed_identity(target, _staged_stub())
        assert exc.value.code == "delivery_confirmation_identity_missing"
        bound = dataclasses.replace(target, expected_volume_serial=7, expected_file_id=8)
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._confirmed_identity(bound, _staged_stub())
        assert exc.value.code == "delivery_target_changed"

    def test_inspect_publish_without_overwrite_confirmation(self, store_env: Path):
        """新建目标（未确认覆盖）：备份名只要有东西就是 changed，不当作可续接。"""

        store_env.joinpath("out.txt").unlink(missing_ok=True)
        store = WindowsManagedFileStore()
        target = _target(store_env)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        absent = store.inspect_publish(target=target, staged=staged)
        assert absent.backup_state == "absent"
        absent.backup_path.write_bytes(b"stranger")
        assert store.inspect_publish(target=target, staged=staged).backup_state == "changed"

    def test_target_state_and_file_state_degrade_to_facts(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """无法核验时只报 ``unverified`` / ``changed``，不猜测阶段。"""

        target_path = store_env / "out.txt"
        target_path.write_bytes(b"old")
        target = _target(store_env, exists=True, digest=hashlib.sha256(b"old").hexdigest())
        staged = _staged_stub()
        assert WindowsManagedFileStore._target_state(target_path, target, staged) == "confirmed"
        target_path.write_bytes(b"stranger")
        assert WindowsManagedFileStore._target_state(target_path, target, staged) == "changed"
        identity = (int(os.stat(target_path).st_dev), _file_identity(os.stat(target_path)))
        assert WindowsManagedFileStore._file_state(target_path, identity=identity, digest=None) == "changed"
        assert WindowsManagedFileStore._file_state(target_path, identity=(1, 1), digest=_DIGEST) == "changed"
        assert WindowsManagedFileStore._file_state(target_path, identity=identity, digest=_DIGEST) == "changed"
        real_snapshot = delivery_module._snapshot

        def broken_snapshot(path: Path) -> FileSnapshot:
            raise PermissionError("unreadable")

        monkeypatch.setattr(delivery_module, "_snapshot", broken_snapshot)
        assert WindowsManagedFileStore._target_state(target_path, target, staged) == "unverified"
        assert WindowsManagedFileStore._file_state(target_path, identity=identity, digest=_DIGEST) == "changed"
        monkeypatch.setattr(delivery_module, "_snapshot", real_snapshot)

    def test_restore_superseded_refuses_occupied_target(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """放回旧目标时目标名被占用/移动失败：不覆盖，返回 False。"""

        assert WindowsManagedFileStore._restore_superseded(None, None, store_env / "out.txt") is True
        backup = store_env / "backup.bin"
        backup.write_bytes(b"old")
        target_path = store_env / "out.txt"
        target_path.write_bytes(b"third-party")
        guard = _GuardedFile.open_exclusive(backup)
        try:
            assert WindowsManagedFileStore._restore_superseded(guard, backup, target_path) is False
        finally:
            guard.close()
        target_path.unlink()

        def refuse_move(self: _GuardedFile, destination: Path) -> None:
            raise OSError("locked")

        monkeypatch.setattr(_GuardedFile, "move_to", refuse_move)
        guard = _GuardedFile.open_exclusive(backup)
        try:
            assert WindowsManagedFileStore._restore_superseded(guard, backup, target_path) is False
        finally:
            guard.close()

    def test_publish_before_snapshot_failure_is_refused(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """发布前目标无法核验：拒绝，不进入任何替换动作。"""

        store, target, staged, backup = self._setup(store_env)

        def broken_snapshot(path: Path) -> FileSnapshot:
            raise PermissionError("unreadable")

        monkeypatch.setattr(delivery_module, "_snapshot", broken_snapshot)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert not backup.exists()

    def test_publish_without_parent_identity_is_refused(
        self, store_env: Path, fake_windows: _FakeWin32Api, monkeypatch: pytest.MonkeyPatch
    ):
        """目标父目录不暴露稳定身份：拒绝发布（无法证明同卷与父目录未变）。"""

        store, target, staged, _ = self._setup(store_env)
        real_snapshot = delivery_module._snapshot
        real_path = store_env / "out.txt"

        def zero_parent(path: Path) -> FileSnapshot:
            snapshot = real_snapshot(path)
            if Path(path) == real_path:
                return dataclasses.replace(snapshot, parent_file_id=0)
            return snapshot

        monkeypatch.setattr(delivery_module, "_snapshot", zero_parent)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_file_identity_unavailable"
        assert fake_windows.replace_calls == []

    def test_replace_failure_with_unreadable_target_is_unknown(
        self, store_env: Path, fake_windows: _FakeWin32Api, monkeypatch: pytest.MonkeyPatch
    ):
        """替换报错且事后无法读取目标：无法证明，只能 unknown。"""

        store, target, staged, _ = self._setup(store_env)
        fake_windows.replace_errors = [OSError("access denied")]
        real_snapshot = delivery_module._snapshot
        calls = {"count": 0}

        def flaky_snapshot(path: Path) -> FileSnapshot:
            calls["count"] += 1
            if calls["count"] == 2:
                raise PermissionError("unreadable")
            return real_snapshot(path)

        monkeypatch.setattr(delivery_module, "_snapshot", flaky_snapshot)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"


@pytest.mark.usefixtures("store_env")
class TestPublishHelperGuards:
    """发布辅助核验的拒绝分支（直接调用私有函数，验证不猜测、不覆盖）。"""

    def test_verified_publish_snapshot_refuses_content_and_identity_mismatch(self, store_env: Path):
        """发布后确证：内容不符或不是本交付文件都只能 unknown。"""

        target_path = store_env / "out.txt"
        target_path.write_bytes(b"stranger")
        staged = _staged_stub(path=target_path, digest=_DIGEST)
        with pytest.raises(DeliveryUnknownError) as exc:
            WindowsManagedFileStore._verified_publish_snapshot(target_path, staged)
        assert exc.value.code == "delivery_publish_unverified"

        target_path.write_bytes(_CONTENT)  # 新文件、内容相同：身份仍不符
        with pytest.raises(DeliveryUnknownError) as exc:
            WindowsManagedFileStore._verified_publish_snapshot(target_path, staged)
        assert exc.value.code == "delivery_publish_unverified"

    def test_rollback_replaced_refuses_foreign_target(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """回滚只作用于本交付文件：目标不是它就不动，返回 False。"""

        target_path = store_env / "out.txt"
        target_path.write_bytes(_CONTENT)
        backup = store_env / "backup.bin"
        backup.write_bytes(b"old")
        staged = _staged_stub(path=store_env / "staged.bin", digest=_DIGEST)
        monkeypatch.setattr(delivery_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(delivery_module, "_WIN32_API", _FakeWin32Api())
        store = WindowsManagedFileStore()
        assert store._rollback_replaced(target_path=target_path, staged=staged, backup_path=backup) is False
        assert target_path.read_bytes() == _CONTENT
        assert backup.read_bytes() == b"old"

    def test_rollback_replaced_reports_backup_move_failure(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """回滚第二步失败（备份放不回目标名）：返回 False 并保持现场。"""

        fake = _FakeWin32Api()
        target_path = store_env / "out.txt"
        backup = store_env / "backup.bin"
        backup.write_bytes(b"old")
        staged_path = store_env / "staged.bin"
        staged_path.write_bytes(_CONTENT)
        staged_identity = (int(os.stat(staged_path).st_dev), _file_identity(os.stat(staged_path)))
        os.replace(staged_path, target_path)
        staged = _staged_stub(
            path=staged_path,
            digest=_DIGEST,
            volume_serial=staged_identity[0],
            file_id=staged_identity[1],
        )
        monkeypatch.setattr(delivery_module, "_IS_WINDOWS", True)
        monkeypatch.setattr(delivery_module, "_WIN32_API", fake)
        fake.rename_fail_destinations.add(target_path)
        store = WindowsManagedFileStore()
        assert store._rollback_replaced(target_path=target_path, staged=staged, backup_path=backup) is False
        assert backup.read_bytes() == b"old"

    def test_restore_confirmed_backup_refusals(self, store_env: Path):
        """按备份放回原确认文件：身份不符/目标被占/备份缺失一律拒绝。"""

        target_path = store_env / "out.txt"
        backup = store_env / "backup.bin"
        backup.write_bytes(b"old")
        identity = (int(os.stat(backup).st_dev), _file_identity(os.stat(backup)))
        target = DeliveryTarget(
            target_path=str(target_path),
            expected_existing=True,
            expected_existing_digest=hashlib.sha256(b"old").hexdigest(),
            overwrite_confirmed=True,
            expected_volume_serial=identity[0],
            expected_file_id=identity[1] + 1,  # 与备份身份不符
        )
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._restore_confirmed_backup(backup, target_path, target)
        assert exc.value.code == "delivery_target_changed"

        bound = dataclasses.replace(target, expected_file_id=identity[1])
        target_path.write_bytes(b"third-party")
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._restore_confirmed_backup(backup, target_path, bound)
        assert exc.value.code == "delivery_target_changed"
        assert target_path.read_bytes() == b"third-party"
        target_path.unlink()
        backup.unlink()
        with pytest.raises(DeliveryUnknownError) as exc:
            WindowsManagedFileStore._restore_confirmed_backup(backup, target_path, bound)
        assert exc.value.code == "delivery_publish_unverified"

    def test_identity_helpers_and_backup_precheck(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """无覆盖确认/无身份时不给身份；备份名无法探测时拒绝发布。"""

        create_new = DeliveryTarget(
            target_path="C:/out.txt",
            expected_existing=False,
            expected_existing_digest=None,
            overwrite_confirmed=False,
        )
        assert WindowsManagedFileStore._confirmed_identity_or_none(create_new) is None
        missing_identity = dataclasses.replace(create_new, expected_existing=True)
        assert WindowsManagedFileStore._confirmed_identity_or_none(missing_identity) is None

        def broken_lexists(path: object) -> bool:
            raise PermissionError("unreadable directory")

        monkeypatch.setattr(delivery_module.os.path, "lexists", broken_lexists)
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._assert_backup_available(store_env / "backup.bin")
        assert exc.value.code == "delivery_target_changed"


class TestPublishRefusalPaths:
    """发布过程中句柄获取/内容核验的拒绝分支（独占与身份核验必须 fail-closed）。"""

    @staticmethod
    def _staged(store_env: Path) -> tuple[WindowsManagedFileStore, DeliveryTarget, StagedFile]:
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=hashlib.sha256(b"old").hexdigest())
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        return store, target, staged

    def test_staged_guard_oserror_is_stage_changed(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """暂存文件被在途 writer 独占：拒绝发布，不清理也不移入。"""

        (store_env / "out.txt").write_bytes(b"old")
        store, target, staged = self._staged(store_env)
        real_open = _GuardedFile.open_exclusive

        def refuse(path: Path) -> _GuardedFile:
            if Path(path) == staged.path:
                raise PermissionError("held by another writer")
            return real_open(Path(path))

        monkeypatch.setattr(_GuardedFile, "open_exclusive", staticmethod(refuse))
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_stage_changed"
        assert (store_env / "out.txt").read_bytes() == b"old"

    def test_target_guard_missing_is_refused(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """独占打开时目标已消失：拒绝发布并保留暂存。"""

        (store_env / "out.txt").write_bytes(b"old")
        store, target, staged = self._staged(store_env)
        real_open = _GuardedFile.open_exclusive
        target_path = store_env / "out.txt"

        def refuse(path: Path) -> _GuardedFile:
            if Path(path) == target_path:
                raise FileNotFoundError(str(path))
            return real_open(Path(path))

        monkeypatch.setattr(_GuardedFile, "open_exclusive", staticmethod(refuse))
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert staged.path.read_bytes() == _CONTENT

    def test_target_open_failure_and_content_change_are_refused(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """目标被独占 / 内容在核验前被改：都按事实拒绝，不覆盖。"""

        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        store, target, staged = self._staged(store_env)
        real_open = _GuardedFile.open_exclusive

        def refuse(path: Path) -> _GuardedFile:
            if Path(path) == target_file:
                raise PermissionError("held by another writer")
            return real_open(Path(path))

        with monkeypatch.context() as scoped:
            scoped.setattr(_GuardedFile, "open_exclusive", staticmethod(refuse))
            with pytest.raises(DeliveryFileError) as exc:
                store._open_confirmed_target(target_file, target, staged)
            assert exc.value.code == "delivery_target_changed"
        target_file.write_bytes(b"old-changed")  # 同一 inode、内容变了
        with pytest.raises(DeliveryFileError) as exc:
            store._open_confirmed_target(target_file, target, staged)
        assert exc.value.code == "delivery_target_changed"

    def test_assert_target_matches_refuses_reparse_and_missing(self, store_env: Path):
        """预检快照：reparse point 或已消失一律拒绝。"""

        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        target = _target(store_env, exists=True, digest=hashlib.sha256(b"old").hexdigest())
        snapshot = _snapshot(target_file)
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._assert_target_matches(
                dataclasses.replace(snapshot, is_reparse_point=True), target, phase="publish"
            )
        assert exc.value.code == "delivery_target_reparse"
        with pytest.raises(DeliveryFileError) as exc:
            WindowsManagedFileStore._assert_target_matches(
                dataclasses.replace(snapshot, exists=False), target, phase="publish"
            )
        assert exc.value.code == "delivery_target_changed"

    def test_cancel_refuses_content_changed_staged(self, store_env: Path):
        """取消只删身份与摘要都对得上的暂存；内容被改即保留（不误删他人内容）。"""

        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST)
        staged.path.write_bytes(b"tampered")
        assert store.cancel(staged) is False
        assert staged.path.read_bytes() == b"tampered"


class TestCancel:
    def test_cancel_removes_staged(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST)
        assert store.cancel(staged) is True
        assert not staged.path.exists()

    def test_cancel_missing_returns_false(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST)
        staged.path.unlink()
        assert store.cancel(staged) is False

    def test_cancel_refuses_foreign_replacement_at_staged_path(self, store_env: Path):
        """暂存路径被换成另一个文件（新文件 ID）：不得按路径误删他人文件。"""

        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST)
        replacement = store_env / "foreign.tmp"
        replacement.write_bytes(_CONTENT)
        os.replace(replacement, staged.path)
        assert store.cancel(staged) is False
        assert staged.path.read_bytes() == _CONTENT

    def test_cancel_refuses_while_writer_holds_staged(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        """在途 writer 独占暂存文件（或临时占用）时不删除。"""

        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST)

        def locked(cls: type[_GuardedFile], path: Path) -> _GuardedFile:
            raise PermissionError("staged file is locked by a writer")

        monkeypatch.setattr(_GuardedFile, "open_exclusive", classmethod(locked))
        assert store.cancel(staged) is False
        assert staged.path.read_bytes() == _CONTENT

    def test_cancel_after_publish_keeps_target(self, store_env: Path):
        """发布后台账里的暂存路径已不存在：取消不得删除已发布目标。"""

        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(store_env), content=_CONTENT, expected_digest=_DIGEST)
        store.publish(target=_target(store_env), staged=staged)
        assert store.cancel(staged) is False
        assert (store_env / "out.txt").read_bytes() == _CONTENT
