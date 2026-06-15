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


def update_installer_version(new_ver: str) -> None:
    content = INSTALLER_PATH.read_text(encoding="utf-8")
    new_content, count = re.subn(r'(#define\s+MyAppVersion\s+)"[^"]+"', rf'\g<1>"{new_ver}"', content)
    if count == 0:
        raise RuntimeError(f"Failed to update version in {INSTALLER_PATH}: MyAppVersion pattern not found.")
    INSTALLER_PATH.write_text(new_content, encoding="utf-8")


def update_release_manifest_version(new_ver: str) -> None:
    with open(RELEASE_MANIFEST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "." not in data:
        raise ValueError(f"Invalid manifest structure in {RELEASE_MANIFEST_PATH}: key '.' not found")
    data["."] = new_ver
    with open(RELEASE_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main() -> None:
    errors: list[str] = []
    fixed_any = False
    fix_mode = "--fix" in sys.argv

    pyproject_ver = get_pyproject_version()

    # Check 1: installer.iss fallback version
    installer_ver = get_installer_fallback_version()
    if installer_ver != pyproject_ver:
        if fix_mode:
            print(f"Auto-fixing installer.iss: {installer_ver} -> {pyproject_ver}")
            update_installer_version(pyproject_ver)
            fixed_any = True
        else:
            errors.append(
                f"installer.iss fallback version '{installer_ver}' != pyproject.toml version '{pyproject_ver}'"
            )

    # Check 2: package.json pyright version vs CI pyright version
    pkg_pyright_ver = get_package_json_pyright_version()
    ci_pyright_ver = get_ci_pyright_version()
    if pkg_pyright_ver != ci_pyright_ver:
        errors.append(f"package.json pyright version '{pkg_pyright_ver}' != CI pyright version '{ci_pyright_ver}'")

    # Check 3: release-please-manifest.json version
    manifest_ver = get_release_manifest_version()
    if manifest_ver != pyproject_ver:
        if fix_mode:
            print(f"Auto-fixing .release-please-manifest.json: {manifest_ver} -> {pyproject_ver}")
            update_release_manifest_version(pyproject_ver)
            fixed_any = True
        else:
            errors.append(
                f".release-please-manifest.json version '{manifest_ver}' != pyproject.toml version '{pyproject_ver}'"
            )

    if fixed_any:
        print("Auto-fixed version mismatches. Please stage the changes and try committing again.")
        sys.exit(1)

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
