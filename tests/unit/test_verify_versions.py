import sys
import json
from pathlib import Path
from unittest.mock import patch
import pytest

# Add scripts directory to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import verify_versions


def test_verify_versions_fix(tmp_path):
    # Create test temp files
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.6.9"\n', encoding="utf-8")

    installer = tmp_path / "installer.iss"
    installer.write_text('#define MyAppVersion "0.6.8" ; x-release-please-version\n', encoding="utf-8")

    pkg_json = tmp_path / "package.json"
    pkg_json.write_text('{"devDependencies": {"pyright": "1.1.300"}}', encoding="utf-8")

    ci_workflow = tmp_path / ".github/workflows/ci_cd.yml"
    ci_workflow.parent.mkdir(parents=True)
    ci_workflow.write_text("pip install pyright==1.1.300", encoding="utf-8")

    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text('{"packages": {".": {}}, ".": "0.6.8"}', encoding="utf-8")

    # Mock all paths and argv
    with (
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.INSTALLER_PATH", installer),
        patch("verify_versions.PACKAGE_JSON_PATH", pkg_json),
        patch("verify_versions.CI_WORKFLOW_PATH", ci_workflow),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
        patch("sys.argv", ["verify_versions.py", "--fix"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        verify_versions.main()

    # Must exit with code 1 after fixing files to alert Pre-commit
    assert exc_info.value.code == 1

    # Verify installer.iss was updated
    assert 'MyAppVersion "0.6.9"' in installer.read_text(encoding="utf-8")

    # Verify manifest was updated
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    assert data["."] == "0.6.9"


def test_update_installer_version_failure(tmp_path):
    installer = tmp_path / "installer.iss"
    installer.write_text("some random content without MyAppVersion\n", encoding="utf-8")
    with patch("verify_versions.INSTALLER_PATH", installer), pytest.raises(RuntimeError) as exc_info:
        verify_versions.update_installer_version("0.6.9")
    assert "Failed to update version" in str(exc_info.value)


def test_update_release_manifest_version_failure(tmp_path):
    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text('{"packages": {}}', encoding="utf-8")  # missing "." key
    with patch("verify_versions.RELEASE_MANIFEST_PATH", manifest), pytest.raises(ValueError) as exc_info:
        verify_versions.update_release_manifest_version("0.6.9")
    assert "Invalid manifest structure" in str(exc_info.value)
