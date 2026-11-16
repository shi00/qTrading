"""WindowsManagedFileStore 平台中性单测（E-06 delivery.py）。

集成层交付编排用例（tests/integration/test_agent_delivery_service.py）标注
win32-only（交付目标保护语义在 Windows 上验证）；但 ``delivery.py`` 实现本身
完全跨平台（os.lstat/st_ino/Path.replace），核心保护逻辑两平台皆可测：
同卷暂存、回读校验、目标确认核验、父目录身份一致性、原子替换、unknown 语义。

路径适配：``validate_delivery_target_path`` 强制 Windows 规范路径
（``C:\\...``），而 ``PureWindowsPath`` 的驱动器段在 POSIX ``Path`` 下是字面
目录名——用例在 ``tmp_path`` 内创建字面目录使 stage/publish 全流程在 POSIX 上
真实执行（st_ino 提供稳定文件身份）；Windows 下使用专属真实目录并清理。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import sys
from collections.abc import Iterator
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

# 平台适配：Windows 下用专属目录 ``C:\astock-delivery-test``（避免触碰系统
# ``C:\tmp``，fixture 建立并清理），4 个依赖 POSIX 字面目录技巧的用例本地
# skip；POSIX 下字面目录 ``C:\tmp`` 在 tmp_path 内创建。交付保护语义另由
# win32-only 集成测试（test_agent_delivery_service.py）覆盖。
pytestmark = []

_CONTENT = b"report-body-7f3a"
_DIGEST = hashlib.sha256(_CONTENT).hexdigest()
# Windows 下用专属目录避免触碰系统 C:\tmp；按 xdist worker 隔离（-n auto 时
# 各 worker 独立目录，消除 fixture 清理与其他 worker 用例的竞态；PYTEST_XDIST_WORKER
# 在 worker 进程内固定，模块导入时求值即正确）。POSIX 下作为字面目录名在 cwd 创建
# （tmp_path 每测试唯一，无需 worker 后缀）。
if sys.platform == "win32":
    _worker = os.environ.get("PYTEST_XDIST_WORKER")
    _WIN_DIR = "C:\\astock-delivery-test" + (f"-{_worker}" if _worker else "")
else:
    _WIN_DIR = "C:\\tmp"
_WIN_PATH = _WIN_DIR + "\\out.txt"


def _target(*, exists: bool = False, digest: str | None = None) -> DeliveryTarget:
    return DeliveryTarget(
        target_path=_WIN_PATH,
        expected_existing=exists,
        expected_existing_digest=digest,
        overwrite_confirmed=True,
    )


@pytest.fixture()
def store_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """cwd 切到 tmp_path 并创建目标字面目录（POSIX 下即真实目录）。"""

    monkeypatch.chdir(tmp_path)
    if sys.platform == "win32":
        literal = Path(_WIN_DIR)
        literal.mkdir(exist_ok=True)
    else:
        literal = tmp_path / _WIN_DIR
        literal.mkdir()
    # 清理跨用例残留（Windows 下目录为系统位置复用；POSIX 下 tmp_path 隔离仍防御）
    target_file = literal / "out.txt"
    target_file.unlink(missing_ok=True)
    for stale in literal.glob("*.staged"):
        stale.unlink(missing_ok=True)
    yield literal
    target_file.unlink(missing_ok=True)
    for stale in literal.glob("*.staged"):
        stale.unlink(missing_ok=True)
    if sys.platform == "win32":
        shutil.rmtree(literal, ignore_errors=True)


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
        staged = store.stage(target=_target(), content=_CONTENT, expected_digest=_DIGEST, name_hint="run1")
        assert staged.path.is_file()
        assert staged.path.name.startswith(".out.txt.run1.")
        assert staged.digest == _DIGEST
        assert staged.size == len(_CONTENT)
        assert staged.parent_file_id != 0
        store.cancel(staged)

    def test_stage_digest_mismatch_cleans_up(self, store_env: Path):
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(), content=_CONTENT, expected_digest="0" * 64)
        assert exc.value.code == "delivery_stage_readback_failed"
        assert not list(store_env.glob("*.staged"))

    def test_stage_parent_missing(self, tmp_path: Path, monkeypatch: pytest.Monkeypatch):
        if sys.platform == "win32":
            pytest.skip("Windows 下目标父目录为真实盘根（恒存在），仅 POSIX 字面目录可模拟缺失")
        monkeypatch.chdir(tmp_path)
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_parent_missing"

    def test_stage_rejects_existing_target_without_confirmation_basis(self, store_env: Path):
        (store_env / "out.txt").write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(exists=False), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_changed"

    def test_stage_rejects_existing_target_with_wrong_digest(self, store_env: Path):
        (store_env / "out.txt").write_bytes(b"stale")
        store = WindowsManagedFileStore()
        with pytest.raises(DeliveryFileError) as exc:
            store.stage(target=_target(exists=True, digest="1" * 64), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_changed"

    def test_stage_accepts_existing_target_with_matching_digest(self, store_env: Path):
        (store_env / "out.txt").write_bytes(_CONTENT)
        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(exists=True, digest=_DIGEST), content=_CONTENT, expected_digest=_DIGEST)
        store.cancel(staged)


class TestPublish:
    def _stage(self, store: WindowsManagedFileStore) -> object:
        return store.stage(target=_target(), content=_CONTENT, expected_digest=_DIGEST)

    def test_publish_replaces_target_and_confirms_identity(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        result = store.publish(target=_target(), staged=staged)
        assert result.snapshot.exists
        assert result.snapshot.digest == _DIGEST
        assert not staged.path.exists()
        assert (store_env / "out.txt").read_bytes() == _CONTENT

    def test_publish_missing_staged_fails(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        staged.path.unlink()
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_stage_missing"

    def test_publish_changed_staged_fails(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        staged.path.write_bytes(b"tampered")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_stage_changed"

    def test_publish_target_appeared_fails(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        (store_env / "out.txt").write_bytes(b"raced-in")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_target_changed"

    def test_publish_target_parent_changed_fails(self, store_env: Path, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Windows 下字面目录为真实系统目录，无法删除重建；仅 POSIX 可模拟身份变化")
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        # 同卷另一个真实目录（身份不同）冒充目标父目录
        other = tmp_path / _WIN_DIR
        other.unlink()
        other.mkdir()
        (other / "out.txt").write_bytes(_CONTENT)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_target_parent_changed"

    def test_publish_symlink_target_fails(self, store_env: Path):
        if sys.platform == "win32":
            pytest.skip("os.symlink 需要特权；reparse 点语义由 win32 集成测试覆盖")
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        # 目标路径 out.txt 变为符号链接 → expected_existing=False 时目标「出现」即失效
        (store_env / "out.txt").unlink()
        os.symlink(store_env / "real.txt", store_env / "out.txt")
        (store_env / "real.txt").write_bytes(_CONTENT)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_target_changed"

    def test_publish_wrong_parent_rejects_cross_volume_stage(self, store_env: Path):
        if sys.platform == "win32":
            pytest.skip("Windows 下字面目录即唯一真实目录，无法构造另一同名字典；仅 POSIX 可测")
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        # 把 staged 移到另一个真实目录（不同父目录身份）
        foreign = store_env.parent / _WIN_DIR
        foreign.mkdir()
        moved = foreign / staged.path.name
        os.replace(staged.path, moved)
        staged = dataclasses.replace(staged, path=moved)
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_stage_not_same_volume"

    def test_publish_unknown_when_replace_oserror(self, store_env: Path, monkeypatch: pytest.Monkeypatch):
        store = WindowsManagedFileStore()
        staged = self._stage(store)
        real_replace = os.replace

        def broken_replace(src: object, dst: object) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", broken_replace)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=_target(), staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        monkeypatch.setattr(os, "replace", real_replace)
        store.cancel(staged)


class TestCancel:
    def test_cancel_removes_staged(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(), content=_CONTENT, expected_digest=_DIGEST)
        assert store.cancel(staged) is True
        assert not staged.path.exists()

    def test_cancel_missing_returns_false(self, store_env: Path):
        store = WindowsManagedFileStore()
        staged = store.stage(target=_target(), content=_CONTENT, expected_digest=_DIGEST)
        staged.path.unlink()
        assert store.cancel(staged) is False
