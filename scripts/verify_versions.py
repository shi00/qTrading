"""Verify version consistency across project files.

Checks:
1. installer.iss fallback version matches pyproject.toml version
2. pyright version consistency across pyproject.toml / requirements-dev.txt / ci_cd.yml / package.json
3. .release-please-manifest.json version matches pyproject.toml version
4. Repo URL consistency (no stale louis2sin/AStockScreener in docs)
5. SECURITY.md supported version matches pyproject.toml major.minor
6. No empty markdown links ]() in README.md
7. CLAUDE.md reference-style pointers (见 `xxx.py`) target existing files
8. Flet three-package (flet/flet-desktop/flet-charts) version consistency in pyproject.toml
9. Doc current-version Flet declarations match pyproject.toml flet version

Usage: python scripts/verify_versions.py
"""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT_PATH = ROOT / "pyproject.toml"
REQUIREMENTS_DEV_PATH = ROOT / "requirements-dev.txt"
INSTALLER_PATH = ROOT / "installer.iss"
PACKAGE_JSON_PATH = ROOT / "package.json"
RELEASE_MANIFEST_PATH = ROOT / ".release-please-manifest.json"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci_cd.yml"
README_PATH = ROOT / "README.md"
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
SECURITY_PATH = ROOT / "SECURITY.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"
MAN_FLET_BEST_PRACTICES_PATH = ROOT / "man" / "flet-best-practices.md"

STALE_REPO_URL = "louis2sin/AStockScreener"
EXPECTED_REPO_URL = "shi00/qTrading"

FLET_PACKAGES = ("flet", "flet-desktop", "flet-charts")
HISTORICAL_FLET_VERSIONS = {"0.28.3"}
DOC_PATHS_FOR_FLET_CHECK = [CLAUDE_PATH, CONTRIBUTING_PATH, README_PATH, MAN_FLET_BEST_PRACTICES_PATH]


def get_pyproject_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["project"]["version"]


def get_installer_fallback_version() -> str:
    content = INSTALLER_PATH.read_text(encoding="utf-8")
    m = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', content)
    if not m:
        raise ValueError(f"Could not find MyAppVersion in {INSTALLER_PATH}")
    return m.group(1)


def get_package_json_pyright_version() -> str:
    with open(PACKAGE_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["devDependencies"]["pyright"]


def get_ci_pyright_version() -> str:
    content = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    m = re.search(r"pip install pyright==(\S+)", content)
    if not m:
        raise ValueError(f"Could not find pyright version in {CI_WORKFLOW_PATH}")
    return m.group(1)


def get_pyproject_pyright_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    dev_deps = cfg["project"]["optional-dependencies"]["dev"]
    for entry in dev_deps:
        if entry.startswith("pyright"):
            m = re.search(r"pyright(?:==|>=)(\S+)", entry)
            if m:
                return m.group(1)
    raise ValueError(f"Could not find pyright constraint in {PYPROJECT_PATH} [project.optional-dependencies.dev]")


def get_requirements_dev_pyright_version() -> str:
    content = REQUIREMENTS_DEV_PATH.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("pyright=="):
            return line.split("==", 1)[1].strip()
    raise ValueError(f"Could not find pyright==X.Y.Z line in {REQUIREMENTS_DEV_PATH}")


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


def check_repo_url_consistency() -> list[str]:
    """Check 4: No stale repo URL in docs."""
    errors = []
    for doc_path in [README_PATH, CONTRIBUTING_PATH, SECURITY_PATH]:
        if not doc_path.exists():
            continue
        content = doc_path.read_text(encoding="utf-8")
        if STALE_REPO_URL in content:
            errors.append(f"{doc_path.name} contains stale repo URL '{STALE_REPO_URL}', expected '{EXPECTED_REPO_URL}'")
    return errors


def check_security_supported_version(pyproject_ver: str) -> list[str]:
    """Check 5: SECURITY.md supported version matches pyproject major.minor."""
    errors = []
    if not SECURITY_PATH.exists():
        return errors
    content = SECURITY_PATH.read_text(encoding="utf-8")
    major_minor = ".".join(pyproject_ver.split(".")[:2])
    pattern = rf"\| {re.escape(major_minor)}\.x\s+\| :white_check_mark:"
    if not re.search(pattern, content):
        errors.append(
            f"SECURITY.md supported version table missing '{major_minor}.x' (pyproject version: {pyproject_ver})"
        )
    return errors


def check_empty_markdown_links() -> list[str]:
    """Check 6: No empty markdown links ]() in README.md."""
    errors = []
    if not README_PATH.exists():
        return errors
    content = README_PATH.read_text(encoding="utf-8")
    for i, line in enumerate(content.splitlines(), 1):
        if "]()" in line:
            errors.append(f"README.md:{i} contains empty markdown link ']()'")
    return errors


def check_claude_references() -> list[str]:
    """Check 7: CLAUDE.md reference-style pointers target existing files."""
    errors = []
    if not CLAUDE_PATH.exists():
        return errors
    content = CLAUDE_PATH.read_text(encoding="utf-8")
    # Match patterns like: 见 `xxx.py` or 见 `xxx/yyy.py`
    refs = re.findall(r"见 `([^`]+)`", content)
    for ref in refs:
        # Skip non-file references (e.g., section references like §4.1)
        if ref.startswith("§") or not (
            ref.endswith(".py") or ref.endswith(".yml") or ref.endswith(".yaml") or ref.endswith(".json")
        ):
            continue
        # Try as full path from root first
        target = ROOT / ref
        if target.exists():
            continue
        # Try as filename (search recursively)
        matches = list(ROOT.rglob(ref))
        if not matches:
            errors.append(f"CLAUDE.md references '{ref}' but file does not exist")
    return errors


def get_pyproject_flet_versions() -> dict[str, str]:
    """Extract Flet package versions from pyproject.toml dependencies."""
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    deps = cfg.get("project", {}).get("dependencies", [])
    versions: dict[str, str] = {}
    for entry in deps:
        for pkg in FLET_PACKAGES:
            if entry.startswith(pkg + "=="):
                versions[pkg] = entry.split("==", 1)[1].strip()
    return versions


def check_flet_package_consistency() -> tuple[list[str], str | None]:
    """Check 8: Flet three-package version consistency in pyproject.toml.

    Returns (errors, common_version). If no Flet packages found, returns ([], None).
    """
    errors: list[str] = []
    versions = get_pyproject_flet_versions()
    if not versions:
        return errors, None
    unique_versions = set(versions.values())
    if len(unique_versions) > 1:
        details = ", ".join(f"{pkg}={ver}" for pkg, ver in sorted(versions.items()))
        errors.append(f"Flet package version mismatch in pyproject.toml: {details}")
    return errors, next(iter(unique_versions)) if len(unique_versions) == 1 else None


def check_doc_flet_version_consistency(expected_ver: str | None) -> list[str]:
    """Check 9: Doc current-version Flet declarations match pyproject.toml.

    Scans doc files for Flet-related version patterns. Historical versions
    (e.g. 0.28.3 referencing v0) are whitelisted.
    """
    if expected_ver is None:
        return []
    errors: list[str] = []
    version_pattern = re.compile(r"0\.\d+\.\d+")
    for doc_path in DOC_PATHS_FOR_FLET_CHECK:
        if not doc_path.exists():
            continue
        content = doc_path.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), 1):
            if "flet" not in line.lower():
                continue
            for m in version_pattern.finditer(line):
                ver = m.group(0)
                if ver == expected_ver or ver in HISTORICAL_FLET_VERSIONS:
                    continue
                errors.append(
                    f"{doc_path.name}:{i} Flet version '{ver}' != pyproject.toml flet version '{expected_ver}'"
                )
    return errors


def main() -> None:
    errors: list[str] = []
    fixed_any = False
    fix_mode = "--fix" in sys.argv

    try:
        pyproject_ver = get_pyproject_version()
        installer_ver = get_installer_fallback_version()
        pkg_pyright_ver = get_package_json_pyright_version()
        ci_pyright_ver = get_ci_pyright_version()
        manifest_ver = get_release_manifest_version()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Check 1: installer.iss fallback version
    if installer_ver != pyproject_ver:
        if fix_mode:
            print(f"Auto-fixing installer.iss: {installer_ver} -> {pyproject_ver}")
            try:
                update_installer_version(pyproject_ver)
                fixed_any = True
            except Exception as e:
                print(f"ERROR: {e}")
                sys.exit(1)
        else:
            errors.append(
                f"installer.iss fallback version '{installer_ver}' != pyproject.toml version '{pyproject_ver}'"
            )

    # Check 2b: pyright version consistency across pyproject.toml / requirements-dev.txt / ci_cd.yml / package.json
    pyproject_pyright_ver = get_pyproject_pyright_version()
    requirements_dev_pyright_ver = get_requirements_dev_pyright_version()
    pyright_versions = {
        "pyproject.toml": pyproject_pyright_ver,
        "requirements-dev.txt": requirements_dev_pyright_ver,
        "ci_cd.yml": ci_pyright_ver,
        "package.json": pkg_pyright_ver,
    }
    unique_pyright_versions = set(pyright_versions.values())
    if len(unique_pyright_versions) > 1:
        details = ", ".join(f"{src}={ver}" for src, ver in pyright_versions.items())
        errors.append(f"pyright version mismatch: {details}")

    # Check 3: release-please-manifest.json version
    if manifest_ver != pyproject_ver:
        if fix_mode:
            print(f"Auto-fixing .release-please-manifest.json: {manifest_ver} -> {pyproject_ver}")
            try:
                update_release_manifest_version(pyproject_ver)
                fixed_any = True
            except Exception as e:
                print(f"ERROR: {e}")
                sys.exit(1)
        else:
            errors.append(
                f".release-please-manifest.json version '{manifest_ver}' != pyproject.toml version '{pyproject_ver}'"
            )

    # Check 4: Repo URL consistency
    errors.extend(check_repo_url_consistency())

    # Check 5: SECURITY.md supported version
    errors.extend(check_security_supported_version(pyproject_ver))

    # Check 6: Empty markdown links
    errors.extend(check_empty_markdown_links())

    # Check 7: CLAUDE.md reference validity
    errors.extend(check_claude_references())

    # Check 8: Flet three-package version consistency
    flet_errors, flet_ver = check_flet_package_consistency()
    errors.extend(flet_errors)

    # Check 9: Doc Flet version consistency
    errors.extend(check_doc_flet_version_consistency(flet_ver))

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
            f"  pyright:        {pyproject_pyright_ver} (pyproject.toml) = "
            f"{requirements_dev_pyright_ver} (requirements-dev.txt) = "
            f"{ci_pyright_ver} (CI) = {pkg_pyright_ver} (package.json)"
        )
        print(f"  release-please: {get_release_manifest_version()}")
        if flet_ver:
            print(f"  flet:           {flet_ver} (flet + flet-desktop + flet-charts)")


if __name__ == "__main__":
    main()
