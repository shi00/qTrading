"""Verify version consistency across project files.

Checks:
1. installer.iss fallback version matches pyproject.toml version
2. package.json pyright version matches CI pinned pyright version
3. .release-please-manifest.json version matches pyproject.toml version

Usage: python scripts/verify_versions.py
"""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT_PATH = ROOT / "pyproject.toml"
INSTALLER_PATH = ROOT / "installer.iss"
PACKAGE_JSON_PATH = ROOT / "package.json"
RELEASE_MANIFEST_PATH = ROOT / ".release-please-manifest.json"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci_cd.yml"


def get_pyproject_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["project"]["version"]


def get_installer_fallback_version() -> str:
    content = INSTALLER_PATH.read_text(encoding="utf-8")
    m = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', content)
    if not m:
        print(f"ERROR: Could not find MyAppVersion in {INSTALLER_PATH}")
        sys.exit(1)
    return m.group(1)


def get_package_json_pyright_version() -> str:
    with open(PACKAGE_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["devDependencies"]["pyright"]


def get_ci_pyright_version() -> str:
    content = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    m = re.search(r"pip install pyright==(\S+)", content)
    if not m:
        print(f"ERROR: Could not find pyright version in {CI_WORKFLOW_PATH}")
        sys.exit(1)
    return m.group(1)


def get_release_manifest_version() -> str:
    with open(RELEASE_MANIFEST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["."]


def main() -> None:
    errors: list[str] = []

    pyproject_ver = get_pyproject_version()

    # Check 1: installer.iss fallback version
    installer_ver = get_installer_fallback_version()
    if installer_ver != pyproject_ver:
        errors.append(f"installer.iss fallback version '{installer_ver}' != pyproject.toml version '{pyproject_ver}'")

    # Check 2: package.json pyright version vs CI pyright version
    pkg_pyright_ver = get_package_json_pyright_version()
    ci_pyright_ver = get_ci_pyright_version()
    if pkg_pyright_ver != ci_pyright_ver:
        errors.append(f"package.json pyright version '{pkg_pyright_ver}' != CI pyright version '{ci_pyright_ver}'")

    # Check 3: release-please-manifest.json version
    manifest_ver = get_release_manifest_version()
    if manifest_ver != pyproject_ver:
        errors.append(
            f".release-please-manifest.json version '{manifest_ver}' != pyproject.toml version '{pyproject_ver}'"
        )

    if errors:
        print("Version consistency check FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("All version consistency checks passed.")
        print(f"  pyproject.toml: {pyproject_ver}")
        print(f"  installer.iss:  {get_installer_fallback_version()}")
        print(
            f"  pyright:        {get_package_json_pyright_version()} (package.json) = {get_ci_pyright_version()} (CI)"
        )
        print(f"  release-please: {get_release_manifest_version()}")


if __name__ == "__main__":
    main()
