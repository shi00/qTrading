import sys
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import pytest

# Add scripts directory to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import verify_versions  # type: ignore  # Resolved dynamically via sys.path


def setup_test_files(
    tmp_path, pyproject_v="0.6.9", installer_v="0.6.9", manifest_v="0.6.9", pkg_pyright="1.1.300", ci_pyright="1.1.300"
):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nversion = "{pyproject_v}"\n', encoding="utf-8")

    installer = tmp_path / "installer.iss"
    installer.write_text(f'#define MyAppVersion "{installer_v}" ; x-release-please-version\n', encoding="utf-8")

    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(f'{{"devDependencies": {{"pyright": "{pkg_pyright}"}}}}', encoding="utf-8")

    ci_workflow = tmp_path / ".github/workflows/ci_cd.yml"
    ci_workflow.parent.mkdir(parents=True, exist_ok=True)
    ci_workflow.write_text(f"pip install pyright=={ci_pyright}", encoding="utf-8")

    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text(f'{{".": "{manifest_v}"}}', encoding="utf-8")

    # Create doc files consistent with pyproject version
    major_minor = ".".join(pyproject_v.split(".")[:2])
    readme = tmp_path / "README.md"
    readme.write_text(
        "# README\n[![CI](https://github.com/shi00/qTrading)](https://github.com/shi00/qTrading)\n", encoding="utf-8"
    )

    contributing = tmp_path / "CONTRIBUTING.md"
    contributing.write_text("# Contributing\n", encoding="utf-8")

    security = tmp_path / "SECURITY.md"
    security.write_text(
        f"# Security\n| Version | Supported |\n| ------- | ---------- |\n| {major_minor}.x | :white_check_mark: |\n",
        encoding="utf-8",
    )

    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# CLAUDE.md\n", encoding="utf-8")

    return pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude


def _patch_all_paths(pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude):
    """Helper to patch all path constants used by verify_versions.main()."""
    return [
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.INSTALLER_PATH", installer),
        patch("verify_versions.PACKAGE_JSON_PATH", pkg_json),
        patch("verify_versions.CI_WORKFLOW_PATH", ci_workflow),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
        patch("verify_versions.README_PATH", readme),
        patch("verify_versions.CONTRIBUTING_PATH", contributing),
        patch("verify_versions.SECURITY_PATH", security),
        patch("verify_versions.CLAUDE_PATH", claude),
    ]


def test_verify_versions_fix(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, installer_v="0.6.8", manifest_v="0.6.8"
    )

    # Mock all paths and argv
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
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
    manifest.write_text("{}", encoding="utf-8")  # missing "." key
    with patch("verify_versions.RELEASE_MANIFEST_PATH", manifest), pytest.raises(ValueError) as exc_info:
        verify_versions.update_release_manifest_version("0.6.9")
    assert "Invalid manifest structure" in str(exc_info.value)


def test_get_installer_fallback_version_failure(tmp_path):
    installer = tmp_path / "installer.iss"
    installer.write_text("wrong file format", encoding="utf-8")
    with patch("verify_versions.INSTALLER_PATH", installer), pytest.raises(ValueError) as exc_info:
        verify_versions.get_installer_fallback_version()
    assert "Could not find MyAppVersion" in str(exc_info.value)


def test_get_ci_pyright_version_failure(tmp_path):
    ci_workflow = tmp_path / "ci_cd.yml"
    ci_workflow.write_text("wrong content without pyright version", encoding="utf-8")
    with patch("verify_versions.CI_WORKFLOW_PATH", ci_workflow), pytest.raises(ValueError) as exc_info:
        verify_versions.get_ci_pyright_version()
    assert "Could not find pyright version" in str(exc_info.value)


def test_verify_versions_all_consistent(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    # Test without --fix (exit 0, no exceptions)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        verify_versions.main()

    # Test with --fix (exit 0, no exceptions)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        verify_versions.main()

    # Verify files were not modified
    assert 'MyAppVersion "0.6.9"' in installer.read_text(encoding="utf-8")


def test_verify_versions_no_fix_mismatch(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pyproject_v="0.6.9", installer_v="0.6.8", manifest_v="0.6.8"
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1
    # Verify files were NOT fixed
    assert 'MyAppVersion "0.6.8"' in installer.read_text(encoding="utf-8")


def test_verify_versions_installer_mismatch_only(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pyproject_v="0.6.9", installer_v="0.6.8", manifest_v="0.6.9"
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1
    # installer.iss is fixed
    assert 'MyAppVersion "0.6.9"' in installer.read_text(encoding="utf-8")


def test_verify_versions_manifest_mismatch_only(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pyproject_v="0.6.9", installer_v="0.6.9", manifest_v="0.6.8"
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1
    # manifest is fixed
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    assert data["."] == "0.6.9"


def test_verify_versions_pyright_mismatch(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pkg_pyright="1.1.300", ci_pyright="1.1.301"
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_main_initial_read_failure(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pyproject_v="0.6.9", installer_v="0.6.9", manifest_v="0.6.9"
    )
    # Make one file missing to trigger OSError in main()
    missing_pyproject = tmp_path / "non_existent_pyproject.toml"
    patches = _patch_all_paths(
        missing_pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_main_installer_fix_failure(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pyproject_v="0.6.9", installer_v="0.6.8", manifest_v="0.6.9"
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            patch("verify_versions.update_installer_version", side_effect=RuntimeError("Mocked update failure"))
        )
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_main_manifest_fix_failure(tmp_path):
    pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude = setup_test_files(
        tmp_path, pyproject_v="0.6.9", installer_v="0.6.9", manifest_v="0.6.8"
    )
    patches = _patch_all_paths(
        pyproject, installer, pkg_json, ci_workflow, manifest, readme, contributing, security, claude
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            patch("verify_versions.update_release_manifest_version", side_effect=ValueError("Mocked update failure"))
        )
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1
