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
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_types import DeliveryTarget
from services.agent_service import delivery as delivery_module
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    FileSnapshot,
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


def _target(root: Path, *, exists: bool = False, digest: str | None = None) -> DeliveryTarget:
    return DeliveryTarget(
        target_path=str(root / "out.txt"),
        expected_existing=exists,
        expected_existing_digest=digest,
        overwrite_confirmed=True,
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

    def test_stage_accepts_existing_target_with_matching_digest(self, store_env: Path):
        (store_env / "out.txt").write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        staged = store.stage(
            target=_target(store_env, exists=True, digest=_DIGEST), content=_CONTENT, expected_digest=_DIGEST
        )
        store.cancel(staged)


class TestPublish:
    def _stage(self, store: WindowsManagedFileStore, root: Path) -> object:
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
        monkeypatch.setattr(delivery_module, "_superseded_name", lambda path: occupied)
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=old_digest)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert occupied.read_bytes() == b"someone-else"
        assert target_file.read_bytes() == b"old"
        assert staged.path.read_bytes() == _CONTENT

    def test_publish_windows_exclusive_handle_blocks_foreign_replace(
        self, store_env: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Windows：独占句柄期间外部替换被拒，发布仍作用于已确认文件且不触碰外部文件。"""

        if sys.platform != "win32":
            pytest.skip("独占共享语义仅在 Windows 成立")
        target_file = store_env / "out.txt"
        target_file.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = WindowsManagedFileStore()
        target = _target(store_env, exists=True, digest=old_digest)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        replacement = store_env / "external.tmp"
        blocked: list[OSError] = []
        real_supersede = _GuardedFile.supersede_to

        def probe(self: _GuardedFile, destination: Path, *, expected_identity: tuple[int, int]) -> None:
            replacement.write_bytes(b"external-new")
            try:
                os.replace(replacement, target_file)
            except OSError as exc:
                blocked.append(exc)
            real_supersede(self, destination, expected_identity=expected_identity)

        monkeypatch.setattr(_GuardedFile, "supersede_to", probe)
        store.publish(target=target, staged=staged)
        assert blocked and isinstance(blocked[0], PermissionError)
        assert target_file.read_bytes() == _CONTENT
        assert replacement.read_bytes() == b"external-new"


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
