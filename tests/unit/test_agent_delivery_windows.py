"""E-05-2 Windows 目标保护 adapter 真文件验证（DD-G03）。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.agent_types import DeliveryTarget
from services.agent_service import delivery as delivery_module
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    WindowsManagedFileStore,
    _file_identity,
    _GuardedFile,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 目标保护仅在 win32 验证")

_CONTENT = "收盘价,涨跌幅\n600000,1.23\n".encode()
_DIGEST = hashlib.sha256(_CONTENT).hexdigest()


def _target(
    path: Path, *, existing: bool = False, digest: str | None = None, confirmed: bool = False
) -> DeliveryTarget:
    return DeliveryTarget(
        target_path=str(path),
        expected_existing=existing,
        expected_existing_digest=digest,
        overwrite_confirmed=confirmed,
    )


def _store() -> WindowsManagedFileStore:
    return WindowsManagedFileStore()


class TestStageAndPublish:
    def test_new_file_is_written_and_verified(self, tmp_path: Path) -> None:
        target_path = tmp_path / "研究结果.md"
        store = _store()
        staged = store.stage(target=_target(target_path), content=_CONTENT, expected_digest=_DIGEST)
        assert staged.digest == _DIGEST
        assert staged.path.parent == target_path.parent
        result = store.publish(target=_target(target_path), staged=staged)
        assert target_path.read_bytes() == _CONTENT
        assert result.snapshot.exists is True
        assert result.snapshot.digest == _DIGEST
        assert not staged.path.exists()

    def test_existing_target_overwrite_requires_matching_digest(self, tmp_path: Path) -> None:
        target_path = tmp_path / "old.csv"
        target_path.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = _store()
        target = _target(target_path, existing=True, digest=old_digest, confirmed=True)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        store.publish(target=target, staged=staged)
        assert target_path.read_bytes() == _CONTENT

    def test_cancel_keeps_old_target_and_removes_staging(self, tmp_path: Path) -> None:
        target_path = tmp_path / "keep.csv"
        target_path.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = _store()
        target = _target(target_path, existing=True, digest=old_digest, confirmed=True)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        assert store.cancel(staged) is True
        assert target_path.read_bytes() == b"old"
        assert not staged.path.exists()
        assert store.cancel(staged) is False


class TestTargetRaceProtection:
    def test_target_appearing_after_confirmation_is_refused(self, tmp_path: Path) -> None:
        target_path = tmp_path / "raced.md"
        store = _store()
        staged = store.stage(target=_target(target_path), content=_CONTENT, expected_digest=_DIGEST)
        target_path.write_bytes(b"external")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(target_path), staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_path.read_bytes() == b"external"
        assert staged.path.exists()

    def test_existing_target_content_change_is_refused(self, tmp_path: Path) -> None:
        target_path = tmp_path / "changed.csv"
        target_path.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = _store()
        target = _target(target_path, existing=True, digest=old_digest, confirmed=True)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        target_path.write_bytes(b"external-change")
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_path.read_bytes() == b"external-change"

    def test_existing_target_disappearing_is_refused(self, tmp_path: Path) -> None:
        target_path = tmp_path / "gone.csv"
        target_path.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = _store()
        target = _target(target_path, existing=True, digest=old_digest, confirmed=True)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        target_path.unlink()
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert not target_path.exists()

    def test_replacement_after_publish_is_reported_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target_path = tmp_path / "unverified.md"
        store = _store()
        staged = store.stage(target=_target(target_path), content=_CONTENT, expected_digest=_DIGEST)
        real_snapshot = delivery_module._snapshot
        calls = {"count": 0}

        def snapshot_then_tamper(path: Path, *, with_digest: bool = True) -> object:
            # 发布句柄关闭后、发布后确证读取之前被外部改写：只能 unknown。
            calls["count"] += 1
            if calls["count"] == 2:
                Path(path).write_bytes(b"external-after-publish")
            return real_snapshot(path, with_digest=with_digest)

        monkeypatch.setattr(delivery_module, "_snapshot", snapshot_then_tamper)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=_target(target_path), staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert target_path.read_bytes() == b"external-after-publish"

    def test_target_written_by_other_file_is_unknown_despite_same_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """移入窗口内被外部写入同内容新文件：文件身份不符即不可确证（不得报成功）。"""

        target_path = tmp_path / "identity.md"
        store = _store()
        staged = store.stage(target=_target(target_path), content=_CONTENT, expected_digest=_DIGEST)
        real_move = _GuardedFile.move_to

        def copy_instead_of_move(mover: _GuardedFile, destination: Path) -> None:
            real_move(mover, destination)
            if Path(destination) == target_path:
                # 模拟「内容相同但不是本交付文件」：换成新文件（新文件 ID）。
                os.close(mover._fd)
                Path(destination).unlink()
                Path(destination).write_bytes(_CONTENT)

        monkeypatch.setattr(_GuardedFile, "move_to", copy_instead_of_move)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=_target(target_path), staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert target_path.read_bytes() == _CONTENT

    def test_publish_oserror_is_unknown_even_with_identical_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """移入失败且目标已是同内容：不得凭内容判成功，只能 unknown。"""

        target_path = tmp_path / "same.md"
        target_path.write_bytes(_CONTENT)
        store = _store()
        target = _target(target_path, existing=True, digest=_DIGEST, confirmed=True)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        real_move = _GuardedFile.move_to

        def refuse_move(mover: _GuardedFile, destination: Path) -> None:
            if Path(destination) == target_path and mover._path == staged.path:
                raise OSError("file is locked")
            real_move(mover, destination)

        monkeypatch.setattr(_GuardedFile, "move_to", refuse_move)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert target_path.read_bytes() == _CONTENT

    def test_reparse_point_target_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real.txt"
        real.write_bytes(b"old")
        link = tmp_path / "link.txt"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError) as error:  # pragma: no cover - 权限受限环境
            pytest.skip(f"symlink unavailable: {error}")
        with pytest.raises(DeliveryFileError) as exc:
            _store().stage(target=_target(link, existing=True), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_reparse"

    def test_junction_parent_is_refused(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        junction = tmp_path / "junction_dir"
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(real_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:  # pragma: no cover - 受限卷
            pytest.skip(f"junction unavailable: {completed.stderr}")
        try:
            with pytest.raises(DeliveryFileError) as exc:
                _store().stage(target=_target(junction / "out.md"), content=_CONTENT, expected_digest=_DIGEST)
            assert exc.value.code == "delivery_target_reparse"
        finally:
            os.rmdir(junction)


class TestExclusiveOverwriteCondition:
    """独占句柄作为覆盖条件：目标被他人持有时不得覆盖，暂存被持有时不得删除。"""

    def test_target_held_by_another_handle_is_refused(self, tmp_path: Path) -> None:
        target_path = tmp_path / "held.csv"
        target_path.write_bytes(b"old")
        old_digest = hashlib.sha256(b"old").hexdigest()
        store = _store()
        target = _target(target_path, existing=True, digest=old_digest, confirmed=True)
        staged = store.stage(target=target, content=_CONTENT, expected_digest=_DIGEST)
        with _GuardedFile.open_exclusive(target_path):
            with pytest.raises(DeliveryFileError) as exc:
                store.publish(target=target, staged=staged)
        assert exc.value.code == "delivery_target_changed"
        assert target_path.read_bytes() == b"old"
        assert staged.path.exists()

    def test_cancel_refused_while_staged_is_exclusively_held(self, tmp_path: Path) -> None:
        store = _store()
        staged = store.stage(target=_target(tmp_path / "busy.md"), content=_CONTENT, expected_digest=_DIGEST)
        with _GuardedFile.open_exclusive(staged.path):
            assert store.cancel(staged) is False
        assert staged.path.exists()


class TestStagingSafety:
    def test_staged_name_carries_delivery_identity(self, tmp_path: Path) -> None:
        target_path = tmp_path / "named.md"
        staged = _store().stage(
            target=_target(target_path),
            content=_CONTENT,
            expected_digest=_DIGEST,
            name_hint="delivery_deadbeef",
        )
        assert "delivery_deadbeef" in staged.path.name
        assert staged.file_id is not None and staged.volume_serial is not None

    def test_invalid_name_hint_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(DeliveryFileError) as exc:
            _store().stage(
                target=_target(tmp_path / "hint.md"),
                content=_CONTENT,
                expected_digest=_DIGEST,
                name_hint="../escaped",
            )
        assert exc.value.code == "delivery_stage_name_invalid"

    def test_zero_file_identity_is_refused(self) -> None:
        """无文件索引的卷（``st_ino == 0``）不得退化为「同内容即归属」。"""

        with pytest.raises(DeliveryFileError) as exc:
            _file_identity(os.stat_result((0, 0, 0, 0, 0, 0, 0, 0, 0, 0)))
        assert exc.value.code == "delivery_file_identity_unavailable"

    def test_zero_identity_volume_is_refused_at_stage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """stage 层也必须拒绝 0 身份卷，且不残留暂存文件。"""

        real_stat = os.stat

        def fake_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
            result = real_stat(path, *args, **kwargs)  # pyright: ignore[reportCallIssue]
            if str(path).endswith(".staged"):
                values = list(result)
                values[1] = 0
                return os.stat_result(values)
            return result

        monkeypatch.setattr("services.agent_service.delivery.os.stat", fake_stat)
        with pytest.raises(DeliveryFileError) as exc:
            _store().stage(target=_target(tmp_path / "zero.md"), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_file_identity_unavailable"
        assert list(tmp_path.iterdir()) == []

    def test_readback_mismatch_removes_staging(self, tmp_path: Path) -> None:
        target_path = tmp_path / "readback.md"
        with pytest.raises(DeliveryFileError) as exc:
            _store().stage(target=_target(target_path), content=_CONTENT, expected_digest="0" * 64)
        assert exc.value.code == "delivery_stage_readback_failed"
        assert list(tmp_path.glob("*.staged")) == []
        assert not target_path.exists()

    def test_missing_staging_is_rejected(self, tmp_path: Path) -> None:
        target_path = tmp_path / "missing.md"
        store = _store()
        staged = store.stage(target=_target(target_path), content=_CONTENT, expected_digest=_DIGEST)
        staged.path.unlink()
        with pytest.raises(DeliveryFileError) as exc:
            store.publish(target=_target(target_path), staged=staged)
        assert exc.value.code == "delivery_stage_missing"

    def test_missing_parent_directory_is_rejected(self, tmp_path: Path) -> None:
        target_path = tmp_path / "absent" / "out.md"
        with pytest.raises(DeliveryFileError) as exc:
            _store().stage(target=_target(target_path), content=_CONTENT, expected_digest=_DIGEST)
        assert exc.value.code == "delivery_target_parent_missing"
