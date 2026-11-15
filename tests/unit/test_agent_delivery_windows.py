"""E-05-2 Windows 目标保护 adapter 真文件验证（DD-G03）。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.agent_types import DeliveryTarget
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    WindowsManagedFileStore,
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
        real_replace = os.replace

        def replace_then_tamper(source: object, destination: object) -> None:
            real_replace(source, destination)
            Path(destination).write_bytes(b"external-after-publish")

        monkeypatch.setattr("services.agent_service.delivery.os.replace", replace_then_tamper)
        with pytest.raises(DeliveryUnknownError) as exc:
            store.publish(target=_target(target_path), staged=staged)
        assert exc.value.code == "delivery_publish_unverified"
        assert target_path.read_bytes() == b"external-after-publish"

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


class TestStagingSafety:
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
