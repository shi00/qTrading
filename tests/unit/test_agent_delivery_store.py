"""WindowsManagedFileStore 平台中性单测（E-06 delivery.py）。

集成层交付编排用例（tests/integration/test_agent_delivery_service.py）标注
win32-only（交付目标保护语义在 Windows 上验证）；但 ``delivery.py`` 实现本身
完全跨平台（os.lstat/st_ino/Path.replace），核心保护逻辑两平台皆可测：
同卷暂存、回读校验、目标确认核验、父目录身份一致性、原子替换、unknown 语义。

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
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    WindowsManagedFileStore,
    _file_identity,
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

    def test_publish_unknown_when_replace_oserror(self, store_env: Path, monkeypatch: pytest.MonkeyPatch):
        store = WindowsManagedFileStore()
        staged = self._stage(store, store_env)

        def broken_replace(src: object, dst: object) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", broken_replace)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=_target(store_env), staged=staged)
        assert exc.value.code == "delivery_publish_unverified"


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
