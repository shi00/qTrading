"""E2E mock 资产 manifest 校验的单元测试（FUP-004 回归）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e import _asset_manifest as am
from tests.e2e._asset_manifest import (
    MANIFEST_NAME,
    build_manifest,
    verify_assets,
    write_manifest,
)

FAKE_REVISION = "89c39edfdfae2949f6806e4a315c0c08522238b7"
FAKE_WEB_VERSION = "9.9.9"


@pytest.fixture
def mock_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """构造最小受管子树（canvaskit + rive）与伪造的 flet_web 元数据，并写入真实 manifest。

    不依赖当前环境安装的 flet_web：版本与 engineRevision 由 monkeypatch 注入，
    避免与其他测试对 flet_web 的搜索路径补丁相互污染（R7）。
    """
    fake_web = tmp_path / "fake_flet_web" / "web"
    fake_web.mkdir(parents=True)
    (fake_web / "flutter_bootstrap.js").write_text(f'{{"engineRevision\\":\\"{FAKE_REVISION}\\"}}', encoding="utf-8")
    monkeypatch.setattr(am, "find_flet_web_web_dir", lambda: fake_web)
    monkeypatch.setattr(am, "installed_flet_web_version", lambda: FAKE_WEB_VERSION)

    root = tmp_path / "mock_assets"
    (root / "canvaskit").mkdir(parents=True)
    (root / "canvaskit" / "canvaskit.wasm").write_bytes(b"wasm-bytes")
    (root / "canvaskit" / "chromium").mkdir()
    (root / "canvaskit" / "chromium" / "canvaskit.js").write_bytes(b"js-bytes")
    (root / "rive" / "wasm").mkdir(parents=True)
    (root / "rive" / "wasm" / "rive.wasm").write_bytes(b"rive-bytes")
    write_manifest(root)
    return root


def _load(mock_root: Path) -> dict:
    return json.loads((mock_root / MANIFEST_NAME).read_text(encoding="utf-8"))


def _dump(mock_root: Path, data: dict) -> None:
    (mock_root / MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")


def test_valid_manifest_has_no_problems(mock_root: Path) -> None:
    assert verify_assets(mock_root) == []


def test_build_manifest_records_version_and_revision(mock_root: Path) -> None:
    data = _load(mock_root)
    assert len(str(data["engine_revision"])) == 40
    assert data["flet_web_version"]
    assert {entry["path"] for entry in data["assets"]} == {
        "canvaskit/canvaskit.wasm",
        "canvaskit/chromium/canvaskit.js",
        "rive/wasm/rive.wasm",
    }
    assert all(len(entry["sha256"]) == 64 for entry in data["assets"])


def test_engine_revision_mismatch_is_reported(mock_root: Path) -> None:
    data = _load(mock_root)
    data["engine_revision"] = "0" * 40
    _dump(mock_root, data)
    problems = verify_assets(mock_root)
    assert any("engine_revision" in p for p in problems)


def test_flet_web_version_mismatch_is_reported(mock_root: Path) -> None:
    data = _load(mock_root)
    data["flet_web_version"] = "0.0.1-not-installed"
    _dump(mock_root, data)
    problems = verify_assets(mock_root)
    assert any("flet_web_version" in p for p in problems)


def test_digest_mismatch_is_reported(mock_root: Path) -> None:
    (mock_root / "canvaskit" / "canvaskit.wasm").write_bytes(b"tampered")
    problems = verify_assets(mock_root)
    assert any("摘要不匹配" in p and "canvaskit.wasm" in p for p in problems)


def test_missing_asset_is_reported(mock_root: Path) -> None:
    (mock_root / "rive" / "wasm" / "rive.wasm").unlink()
    problems = verify_assets(mock_root)
    assert any("资产缺失" in p and "rive.wasm" in p for p in problems)


def test_path_traversal_entry_is_rejected(mock_root: Path) -> None:
    data = _load(mock_root)
    data["assets"].append({"path": "../outside.wasm", "sha256": "0" * 64, "size": 1})
    _dump(mock_root, data)
    problems = verify_assets(mock_root)
    assert any("非法资产路径" in p for p in problems)


def test_missing_manifest_raises(mock_root: Path) -> None:
    (mock_root / MANIFEST_NAME).unlink()
    with pytest.raises(RuntimeError, match="清单缺失"):
        verify_assets(mock_root)


def test_verify_digests_can_be_skipped(mock_root: Path) -> None:
    (mock_root / "canvaskit" / "canvaskit.wasm").write_bytes(b"tampered")
    assert verify_assets(mock_root, verify_digests=False) == []


def test_build_manifest_ignores_unmanaged_trees(mock_root: Path) -> None:
    fonts_dir = mock_root / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    (fonts_dir / "a.woff2").write_bytes(b"font")
    data = build_manifest(mock_root)
    assert [entry["path"] for entry in data["assets"]] == [
        "canvaskit/canvaskit.wasm",
        "canvaskit/chromium/canvaskit.js",
        "rive/wasm/rive.wasm",
    ]
    assert "fonts" in data
