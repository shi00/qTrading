"""Verify version consistency across project files.

Checks:
1. installer.iss fallback version matches pyproject.toml version
2. pyright version consistency across pyproject.toml / requirements-dev.txt / ci_cd.yml / package.json
3. .release-please-manifest.json version matches pyproject.toml version
4. Repo URL consistency (no stale louis2sin/AStockScreener in docs)
5. SECURITY.md supported version matches pyproject.toml major.minor
6. No empty markdown links ]() in README.md
7. CLAUDE.md reference-style pointers (见 `xxx.py`) target existing files
8. Flet 三包（flet / flet-desktop / flet-charts）版本一致性
9. Sidecar 版本一致性：Cargo.toml [package].version / pyproject.toml [tool.qtrading.sidecar] /
   src/protocol.rs PROTOCOL_VERSION 三方对齐（pg_plan §15.5 AI-12）；
   可选 ``--check-sidecar-binary`` 启用四方校验（调用 sidecar version --json）。

Usage: python scripts/verify_versions.py [--check-sidecar-binary <path>]
"""

import json
import re
import subprocess
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
SIDECAR_CARGO_PATH = ROOT / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
SIDECAR_PROTOCOL_PATH = ROOT / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs"

STALE_REPO_URL = "louis2sin/AStockScreener"
EXPECTED_REPO_URL = "shi00/qTrading"

# sidecar version --json 调用超时（秒），与 generate_sidecar_manifest.py 对齐
SIDECAR_VERSION_TIMEOUT_S = 10


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


def check_flet_version_consistency() -> list[str]:
    """Check 8: flet / flet-desktop / flet-charts 三包版本一致。

    pyproject.toml [project.dependencies] 中三包格式为 "flet==X.Y.Z" 等。
    三包必须锁定同一版本（flet-charts 自 V1 拆包后需与 flet 主包同步升级）。
    """
    errors: list[str] = []
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    deps = cfg.get("project", {}).get("dependencies", [])
    versions: dict[str, str] = {}
    for entry in deps:
        for pkg in ("flet", "flet-desktop", "flet-charts"):
            m = re.match(rf"^{re.escape(pkg)}==(\S+)$", entry.strip())
            if m:
                versions[pkg] = m.group(1)
    missing = [pkg for pkg in ("flet", "flet-desktop", "flet-charts") if pkg not in versions]
    if missing:
        errors.append(f"pyproject.toml missing flet packages: {', '.join(missing)}")
        return errors
    unique = set(versions.values())
    if len(unique) > 1:
        details = ", ".join(f"{pkg}={ver}" for pkg, ver in versions.items())
        errors.append(f"flet version mismatch: {details}")
    return errors


def get_sidecar_cargo_version() -> str:
    """读取 sidecars/qtrading-pg-sidecar/Cargo.toml [package].version。"""
    with open(SIDECAR_CARGO_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["package"]["version"]


def get_pyproject_sidecar_config() -> dict[str, str]:
    """读取 pyproject.toml [tool.qtrading.sidecar] 配置节。

    返回的 dict 至少包含 version / protocol_version / postgresql_version / crate_version。
    缺失时抛 KeyError，由调用方捕获转为 errors。
    """
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["tool"]["qtrading"]["sidecar"]


def get_sidecar_protocol_version() -> str:
    """读取 src/protocol.rs 中 ``pub const PROTOCOL_VERSION: &str = "..."`` 常量值。"""
    content = SIDECAR_PROTOCOL_PATH.read_text(encoding="utf-8")
    m = re.search(r'pub\s+const\s+PROTOCOL_VERSION\s*:\s*&str\s*=\s*"([^"]+)"', content)
    if not m:
        raise ValueError(f"Could not find PROTOCOL_VERSION const in {SIDECAR_PROTOCOL_PATH}")
    return m.group(1)


def query_sidecar_version_json(sidecar_binary: Path) -> dict[str, object]:
    """调用 ``sidecar version --json`` 获取构建元数据。

    返回 sidecar 输出的 JSON dict（schema qtrading.embedded_postgres.version.v1）。
    用于 --check-sidecar-binary 启用的四方校验。
    """
    if not sidecar_binary.exists():
        raise FileNotFoundError(f"sidecar binary not found: {sidecar_binary}")
    try:
        proc = subprocess.run(
            [str(sidecar_binary), "version", "--json"],
            capture_output=True,
            text=True,
            timeout=SIDECAR_VERSION_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"sidecar version --json timed out after {SIDECAR_VERSION_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"sidecar version --json exited {proc.returncode}; stderr={proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"sidecar version --json produced non-JSON stdout: {proc.stdout!r}") from exc


def check_sidecar_version_consistency(sidecar_binary: Path | None = None) -> list[str]:
    """Check 9: sidecar 版本一致性（pg_plan §15.5 AI-12 三方/四方校验）。

    默认三方校验（不调用 binary）：
    - Cargo.toml [package].version == pyproject.toml [tool.qtrading.sidecar] version
    - protocol.rs PROTOCOL_VERSION == pyproject.toml [tool.qtrading.sidecar] protocol_version
    - pyproject.toml [tool.qtrading.sidecar] postgresql_version == "17.2.0"（固定 17 系列）
    - pyproject.toml [tool.qtrading.sidecar] crate_version == Cargo.toml [dependencies] postgresql_embedded version

    当 ``sidecar_binary`` 非空时启用四方校验，额外检查：
    - sidecar version --json 的 sidecar_version 与上述一致
    - sidecar version --json 的 protocol_version 与上述一致
    - sidecar version --json 的 postgres_version 与上述一致
    - sidecar version --json 的 postgresql_embedded_version 与上述一致
    """
    errors: list[str] = []

    # Cargo.toml / protocol.rs 可能不存在（仓库未含 sidecar 源码时）
    if not SIDECAR_CARGO_PATH.exists():
        errors.append(f"sidecar Cargo.toml not found: {SIDECAR_CARGO_PATH}")
        return errors
    if not SIDECAR_PROTOCOL_PATH.exists():
        errors.append(f"sidecar protocol.rs not found: {SIDECAR_PROTOCOL_PATH}")
        return errors

    try:
        cargo_ver = get_sidecar_cargo_version()
    except (KeyError, ValueError) as exc:
        errors.append(f"failed to read Cargo.toml [package].version: {exc}")
        return errors

    try:
        pyproject_sidecar = get_pyproject_sidecar_config()
    except KeyError as exc:
        errors.append(f"pyproject.toml [tool.qtrading.sidecar] missing key: {exc}")
        return errors

    pyproject_ver = pyproject_sidecar.get("version", "")
    pyproject_protocol = pyproject_sidecar.get("protocol_version", "")
    pyproject_pg_ver = pyproject_sidecar.get("postgresql_version", "")
    pyproject_crate_ver = pyproject_sidecar.get("crate_version", "")

    # 9a: Cargo.toml [package].version == pyproject.toml [tool.qtrading.sidecar] version
    if cargo_ver != pyproject_ver:
        errors.append(
            f"sidecar version mismatch: Cargo.toml [package].version '{cargo_ver}' != "
            f"pyproject.toml [tool.qtrading.sidecar] version '{pyproject_ver}'"
        )

    # 9b: protocol.rs PROTOCOL_VERSION == pyproject.toml [tool.qtrading.sidecar] protocol_version
    try:
        protocol_ver = get_sidecar_protocol_version()
    except ValueError as exc:
        errors.append(f"failed to read protocol.rs PROTOCOL_VERSION: {exc}")
        protocol_ver = ""
    if protocol_ver and protocol_ver != pyproject_protocol:
        errors.append(
            f"sidecar protocol_version mismatch: protocol.rs PROTOCOL_VERSION '{protocol_ver}' != "
            f"pyproject.toml [tool.qtrading.sidecar] protocol_version '{pyproject_protocol}'"
        )

    # 9c: postgresql_version 固定 17 系列（pg_plan §15.2）
    if pyproject_pg_ver and not pyproject_pg_ver.startswith("17."):
        errors.append(f"sidecar postgresql_version '{pyproject_pg_ver}' not in 17.x series (pg_plan §15.2)")

    # 9d: crate_version 与 Cargo.toml [dependencies] postgresql_embedded 版本对齐
    cargo_crate_ver = _get_cargo_postgresql_embedded_version()
    if cargo_crate_ver and pyproject_crate_ver and cargo_crate_ver != pyproject_crate_ver:
        errors.append(
            f"sidecar crate_version mismatch: Cargo.toml postgresql_embedded '{cargo_crate_ver}' != "
            f"pyproject.toml [tool.qtrading.sidecar] crate_version '{pyproject_crate_ver}'"
        )

    # 可选四方校验
    if sidecar_binary is not None:
        try:
            version_json = query_sidecar_version_json(sidecar_binary)
        except (RuntimeError, FileNotFoundError) as exc:
            errors.append(f"--check-sidecar-binary failed: {exc}")
            return errors
        binary_sidecar_ver = str(version_json.get("sidecar_version", ""))
        binary_protocol_ver = str(version_json.get("protocol_version", ""))
        binary_pg_ver = str(version_json.get("postgres_version", ""))
        binary_crate_ver = str(version_json.get("postgresql_embedded_version", ""))
        if binary_sidecar_ver and binary_sidecar_ver != cargo_ver:
            errors.append(f"sidecar binary sidecar_version '{binary_sidecar_ver}' != Cargo.toml version '{cargo_ver}'")
        if binary_protocol_ver and binary_protocol_ver != pyproject_protocol:
            errors.append(
                f"sidecar binary protocol_version '{binary_protocol_ver}' != "
                f"pyproject.toml protocol_version '{pyproject_protocol}'"
            )
        if binary_pg_ver and binary_pg_ver != pyproject_pg_ver:
            errors.append(
                f"sidecar binary postgres_version '{binary_pg_ver}' != "
                f"pyproject.toml postgresql_version '{pyproject_pg_ver}'"
            )
        if binary_crate_ver and binary_crate_ver != pyproject_crate_ver:
            errors.append(
                f"sidecar binary postgresql_embedded_version '{binary_crate_ver}' != "
                f"pyproject.toml crate_version '{pyproject_crate_ver}'"
            )

    return errors


def _get_cargo_postgresql_embedded_version() -> str:
    """读取 Cargo.toml [dependencies] postgresql_embedded 的版本（剥离 ``=`` pin 前缀）。

    格式形如 ``postgresql_embedded = { version = "=0.21.0", ... }``，返回 ``0.21.0``。
    解析失败返回空字符串（不阻塞主流程，由 check 函数报告不一致或缺失）。
    """
    try:
        with open(SIDECAR_CARGO_PATH, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    dep = cfg.get("dependencies", {}).get("postgresql_embedded")
    if not isinstance(dep, dict):
        return ""
    raw = str(dep.get("version", ""))
    return raw.lstrip("=").strip()


def _parse_check_sidecar_binary_arg(argv: list[str]) -> Path | None:
    """解析 ``--check-sidecar-binary <path>`` 参数，未提供时返回 None。"""
    if "--check-sidecar-binary" not in argv:
        return None
    idx = argv.index("--check-sidecar-binary")
    if idx + 1 >= len(argv):
        print("ERROR: --check-sidecar-binary requires a path argument", file=sys.stderr)
        sys.exit(2)
    return Path(argv[idx + 1])


def main() -> None:
    errors: list[str] = []
    fixed_any = False
    fix_mode = "--fix" in sys.argv
    sidecar_binary = _parse_check_sidecar_binary_arg(sys.argv)

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

    # Check 8: Flet 三包版本一致性
    errors.extend(check_flet_version_consistency())

    # Check 9: Sidecar 版本一致性（pg_plan §15.5 AI-12）
    errors.extend(check_sidecar_version_consistency(sidecar_binary))

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
        # Check 9 摘要：sidecar 版本一致性
        try:
            sidecar_cargo_ver = get_sidecar_cargo_version()
            sidecar_protocol_ver = get_sidecar_protocol_version()
            sidecar_pg_ver = get_pyproject_sidecar_config().get("postgresql_version", "")
            print(
                f"  sidecar:        Cargo.toml={sidecar_cargo_ver} / "
                f"protocol={sidecar_protocol_ver} / pg={sidecar_pg_ver}"
            )
            if sidecar_binary is not None:
                print(f"  sidecar binary: {sidecar_binary} (4-way check passed)")
        except Exception:
            # 摘要失败不影响通过状态（主流程已通过 check_sidecar_version_consistency）
            pass


if __name__ == "__main__":
    main()
