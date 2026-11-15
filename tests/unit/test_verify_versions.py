import sys
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import pytest

# Add scripts directory to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import verify_versions  # type: ignore[import-not-found]  # Resolved dynamically via sys.path

pytestmark = [pytest.mark.unit, pytest.mark.meta]


def setup_test_files(
    tmp_path,
    pyproject_v="0.6.9",
    manifest_v="0.6.9",
    pkg_pyright="1.1.300",
    ci_pyright="1.1.300",
    flet_v="0.86.0",
):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nversion = "{pyproject_v}"\ndependencies = ["flet=={flet_v}", "flet-desktop=={flet_v}", "flet-charts=={flet_v}", "flet-code-editor=={flet_v}"]\n\n[project.optional-dependencies]\ndev = ["pyright>={pkg_pyright}"]\n\n[tool.qtrading.sidecar]\nversion = "0.1.0"\nprotocol_version = "v1"\npostgresql_version = "16.14.0"\ncrate_version = "0.21.0"\n',
        encoding="utf-8",
    )

    # sidecar stub 文件：与 pyproject [tool.qtrading.sidecar] 版本一致，供 check_sidecar_version_consistency() 解析
    sidecar_dir = tmp_path / "sidecars" / "qtrading-pg-sidecar"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "Cargo.toml").write_text(
        '[package]\nversion = "0.1.0"\n\n[dependencies]\npostgresql_embedded = { version = "=0.21.0" }\n',
        encoding="utf-8",
    )
    sidecar_src = sidecar_dir / "src"
    sidecar_src.mkdir(parents=True, exist_ok=True)
    (sidecar_src / "protocol.rs").write_text(
        'pub const PROTOCOL_VERSION: &str = "v1";\n',
        encoding="utf-8",
    )

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
        "# README\n[![CI](https://github.com/shi00/qTrading)](https://github.com/shi00/qTrading)\n",
        encoding="utf-8",
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

    requirements_dev = tmp_path / "requirements-dev.txt"
    requirements_dev.write_text(f"pyright=={pkg_pyright}\n", encoding="utf-8")

    return (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )


def _patch_all_paths(
    pyproject,
    pkg_json,
    ci_workflow,
    manifest,
    readme,
    contributing,
    security,
    claude,
    requirements_dev,
):
    """Helper to patch all path constants used by verify_versions.main()."""
    # sidecar stub 路径从 pyproject.parent 推导，与 setup_test_files 中创建位置一致
    sidecar_dir = pyproject.parent / "sidecars" / "qtrading-pg-sidecar"
    return [
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.PACKAGE_JSON_PATH", pkg_json),
        patch("verify_versions.CI_WORKFLOW_PATH", ci_workflow),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
        patch("verify_versions.README_PATH", readme),
        patch("verify_versions.CONTRIBUTING_PATH", contributing),
        patch("verify_versions.SECURITY_PATH", security),
        patch("verify_versions.CLAUDE_PATH", claude),
        patch("verify_versions.REQUIREMENTS_DEV_PATH", requirements_dev),
        patch("verify_versions.SIDECAR_CARGO_PATH", sidecar_dir / "Cargo.toml"),
        patch("verify_versions.SIDECAR_PROTOCOL_PATH", sidecar_dir / "src" / "protocol.rs"),
    ]


def test_verify_versions_fix(tmp_path):
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path, manifest_v="0.6.8")

    # Mock all paths and argv
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()

    # Must exit with code 1 after fixing files to alert Pre-commit
    assert exc_info.value.code == 1

    # Verify manifest was updated
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    assert data["."] == "0.6.9"


def test_update_release_manifest_version_failure(tmp_path):
    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text("{}", encoding="utf-8")  # missing "." key
    with (
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
        pytest.raises(ValueError) as exc_info,
    ):
        verify_versions.update_release_manifest_version("0.6.9")
    assert "Invalid manifest structure" in str(exc_info.value)


def test_get_ci_pyright_version_failure(tmp_path):
    ci_workflow = tmp_path / "ci_cd.yml"
    ci_workflow.write_text("wrong content without pyright version", encoding="utf-8")
    with (
        patch("verify_versions.CI_WORKFLOW_PATH", ci_workflow),
        pytest.raises(ValueError) as exc_info,
    ):
        verify_versions.get_ci_pyright_version()
    assert "Could not find pyright version" in str(exc_info.value)


def test_verify_versions_all_consistent(tmp_path):
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path)
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
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

    # Verify manifest was not modified
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    assert data["."] == "0.6.9"


def test_verify_versions_no_fix_mismatch(tmp_path):
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path, pyproject_v="0.6.9", manifest_v="0.6.8")
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1
    # Verify manifest was NOT fixed
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    assert data["."] == "0.6.8"


def test_verify_versions_manifest_mismatch_only(tmp_path):
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path, pyproject_v="0.6.9", manifest_v="0.6.8")
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
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
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path, pkg_pyright="1.1.300", ci_pyright="1.1.301")
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_verify_versions_flet_mismatch(tmp_path):
    """Check 7: flet 四包版本不一致应报错。"""
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path)
    # Overwrite pyproject with mismatched flet versions
    pyproject.write_text(
        '[project]\nversion = "0.6.9"\ndependencies = ["flet==0.86.0", "flet-desktop==0.86.0", "flet-charts==0.85.3", "flet-code-editor==0.86.0"]\n\n[project.optional-dependencies]\ndev = ["pyright>=1.1.300"]\n',
        encoding="utf-8",
    )
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_verify_versions_flet_missing(tmp_path):
    """Check 7: flet 四包缺失应报错。"""
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path)
    # Overwrite pyproject without flet packages
    pyproject.write_text(
        '[project]\nversion = "0.6.9"\ndependencies = ["polars==1.0.0"]\n\n[project.optional-dependencies]\ndev = ["pyright>=1.1.300"]\n',
        encoding="utf-8",
    )
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_main_initial_read_failure(tmp_path):
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path)
    # Make one file missing to trigger OSError in main()
    missing_pyproject = tmp_path / "non_existent_pyproject.toml"
    patches = _patch_all_paths(
        missing_pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch("sys.argv", ["verify_versions.py"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


def test_main_manifest_fix_failure(tmp_path):
    (
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    ) = setup_test_files(tmp_path, manifest_v="0.6.8")
    patches = _patch_all_paths(
        pyproject,
        pkg_json,
        ci_workflow,
        manifest,
        readme,
        contributing,
        security,
        claude,
        requirements_dev,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            patch(
                "verify_versions.update_release_manifest_version",
                side_effect=ValueError("Mocked update failure"),
            )
        )
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--fix"]))
        with pytest.raises(SystemExit) as exc_info:
            verify_versions.main()
    assert exc_info.value.code == 1


# ============================================================================
# DoctorJson schema cross-check tests (P3-VerifyVersions-DoctorSchema)
# ============================================================================

# 正例 mock: 必需镜像字段在两个集合中一致（允许忽略表之外的 Rust 字段不出现）
_MOCK_RUST_CONSISTENT = """\
struct Issue {
    code: &'static str,
    message: String,
}

struct DoctorJson {
    schema: &'static str,
    data_dir: String,
    initialized: bool,
    pg_version: Option<u32>,
    bundled_pg_major: Option<u32>,
    version_match: bool,
    critical_files_missing: Vec<String>,
    install_dir_complete: bool,
    missing_tools: Vec<String>,
    lock_held: bool,
    postgres_alive: bool,
    port: Option<u16>,
    state_file: &'static str,
    runtime_status: Option<String>,
    last_start_error: Option<String>,
    issues: Vec<Issue>,
    restore_residuals: Vec<String>,
    dump_partials: Vec<String>,
}
"""

_MOCK_PYTHON_CONSISTENT = """\
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class DoctorResult:
    schema: str
    data_dir: str
    initialized: bool
    pg_version: int | None = None
    bundled_pg_major: int | None = None
    version_match: bool = False
    critical_files_missing: list[str] = field(default_factory=list)
    install_dir_complete: bool = False
    missing_tools: list[str] = field(default_factory=list)
    lock_held: bool = False
    postgres_alive: bool = False
    port: int | None = None
    state_file: str = "unknown"
    runtime_status: str | None = None
    last_start_error: str | None = None
    issues: list[dict[str, str]] = field(default_factory=list)
    restore_residuals: list[str] = field(default_factory=list)
    dump_partials: list[str] = field(default_factory=list)
"""

# 反例 mock 1: Rust 多一个未登记字段(new_rust_only_field)，Python 未同步
_MOCK_RUST_EXTRA = _MOCK_RUST_CONSISTENT.replace(
    "    issues: Vec<Issue>,\n",
    "    issues: Vec<Issue>,\n    new_rust_only_field: String,\n",
)

# 反例 mock 2: Python 多一个字段(new_python_only_field)，Rust 未同步
_MOCK_PYTHON_EXTRA = _MOCK_PYTHON_CONSISTENT.replace(
    "    dump_partials: list[str] = field(default_factory=list)\n",
    '    dump_partials: list[str] = field(default_factory=list)\n    new_python_only_field: str = ""\n',
)

# 正例 mock 3: Rust-only 字段已在允许忽略表中登记（postmaster_pid 为纯信息性字段）
_MOCK_RUST_ALLOWED_ONLY = _MOCK_RUST_CONSISTENT.replace(
    "    issues: Vec<Issue>,\n",
    "    issues: Vec<Issue>,\n    postmaster_pid: Option<u32>,\n",
)

# 反例 mock 4/5: 必需镜像字段 issues 在任一集合中缺失
_MOCK_RUST_MISSING_ISSUES = _MOCK_RUST_CONSISTENT.replace("    issues: Vec<Issue>,\n", "")
_MOCK_PYTHON_MISSING_ISSUES = _MOCK_PYTHON_CONSISTENT.replace(
    "    issues: list[dict[str, str]] = field(default_factory=list)\n",
    "",
)


def test_doctor_schema_consistent(tmp_path):
    """DoD ②: 必需镜像字段一致 + 无未登记 Rust 字段时脚本通过(exit 0)."""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_CONSISTENT, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_CONSISTENT, encoding="utf-8")

    errors = check_doctor_schema.check_doctor_schema_consistency(rust_path=rust_file, python_path=python_file)
    assert errors == []

    # main() 应不抛 SystemExit(隐式 exit 0)
    with (
        patch("check_doctor_schema.RUST_MAINT_PATH", rust_file),
        patch("check_doctor_schema.PYTHON_SERVICE_PATH", python_file),
    ):
        check_doctor_schema.main()


def test_doctor_schema_rust_extra(tmp_path):
    """DoD ③: Rust 新增未登记字段时 fail(exit 非 0)，不得静默忽略新增诊断字段."""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_EXTRA, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_CONSISTENT, encoding="utf-8")

    errors = check_doctor_schema.check_doctor_schema_consistency(rust_path=rust_file, python_path=python_file)
    assert len(errors) > 0
    joined = "\n".join(errors)
    assert "new_rust_only_field" in joined
    assert "unregistered" in joined

    with (
        patch("check_doctor_schema.RUST_MAINT_PATH", rust_file),
        patch("check_doctor_schema.PYTHON_SERVICE_PATH", python_file),
    ):
        with pytest.raises(SystemExit) as exc_info:
            check_doctor_schema.main()
        assert exc_info.value.code != 0


def test_doctor_schema_python_extra(tmp_path):
    """DoD ④: Python 新增字段 Rust 未同步时 fail(exit 非 0)并报告冗余字段."""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_CONSISTENT, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_EXTRA, encoding="utf-8")

    errors = check_doctor_schema.check_doctor_schema_consistency(rust_path=rust_file, python_path=python_file)
    assert len(errors) > 0
    joined = "\n".join(errors)
    assert "new_python_only_field" in joined

    with (
        patch("check_doctor_schema.RUST_MAINT_PATH", rust_file),
        patch("check_doctor_schema.PYTHON_SERVICE_PATH", python_file),
    ):
        with pytest.raises(SystemExit) as exc_info:
            check_doctor_schema.main()
        assert exc_info.value.code != 0


def test_doctor_schema_allowlisted_rust_only_passes(tmp_path):
    """D16-Q01: 已显式登记（允许忽略表）的 Rust-only 字段不阻断。"""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_ALLOWED_ONLY, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_CONSISTENT, encoding="utf-8")

    errors = check_doctor_schema.check_doctor_schema_consistency(rust_path=rust_file, python_path=python_file)
    assert errors == []


def test_doctor_schema_missing_required_in_python(tmp_path):
    """D16-Q01: Python DTO 缺少必需镜像字段（issues 故障渠道）时失败。"""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_CONSISTENT, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_MISSING_ISSUES, encoding="utf-8")

    errors = check_doctor_schema.check_doctor_schema_consistency(rust_path=rust_file, python_path=python_file)
    joined = "\n".join(errors)
    assert "missing in Python" in joined
    assert "issues" in joined


def test_doctor_schema_missing_required_in_rust(tmp_path):
    """D16-Q01: Rust 缺少必需镜像字段（issues 故障渠道）时失败。"""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_MISSING_ISSUES, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_CONSISTENT, encoding="utf-8")

    errors = check_doctor_schema.check_doctor_schema_consistency(rust_path=rust_file, python_path=python_file)
    joined = "\n".join(errors)
    assert "missing in Rust" in joined
    assert "issues" in joined


def test_doctor_schema_ignored_field_issue_code_must_still_emit(tmp_path):
    """D16-Q01: 允许忽略字段声明的 issue code 从 Rust 源码消失时失败（不弱化故障诊断）。"""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    rust_file = tmp_path / "maint.rs"
    rust_file.write_text(_MOCK_RUST_ALLOWED_ONLY, encoding="utf-8")
    python_file = tmp_path / "service.py"
    python_file.write_text(_MOCK_PYTHON_CONSISTENT, encoding="utf-8")

    spec = check_doctor_schema.IgnoredRustField("test only", ("removed_issue_code",))
    with patch.dict(check_doctor_schema.ALLOWED_RUST_ONLY_FIELDS, {"postmaster_pid": spec}, clear=True):
        errors = check_doctor_schema.check_doctor_schema_consistency(
            rust_path=rust_file,
            python_path=python_file,
        )

    joined = "\n".join(errors)
    assert "removed_issue_code" in joined
    assert "not emitted" in joined


def test_doctor_schema_real_sources_consistent():
    """D16-Q01: 仓库真实 Rust/Python 源必须通过字段契约（防止脚本与实源漂移）。"""
    import check_doctor_schema  # type: ignore[import-not-found]  # scripts/ via sys.path

    errors = check_doctor_schema.check_doctor_schema_consistency()
    assert errors == []


def test_check_repo_url_consistency_fail_closed_on_missing_doc(tmp_path):
    """§5.1/F17：README/CONTRIBUTING/SECURITY 任一缺失必须报错，不得静默跳过。"""
    readme = tmp_path / "README.md"
    readme.write_text("# README\n", encoding="utf-8")
    with (
        patch("verify_versions.README_PATH", readme),
        patch("verify_versions.CONTRIBUTING_PATH", tmp_path / "CONTRIBUTING.md"),
        patch("verify_versions.SECURITY_PATH", tmp_path / "SECURITY.md"),
    ):
        errors = verify_versions.check_repo_url_consistency()

    joined = "\n".join(errors)
    assert "CONTRIBUTING.md" in joined
    assert "SECURITY.md" in joined
    assert "fail-closed" in joined


def test_check_security_supported_version_fail_closed_on_missing_doc(tmp_path):
    """§5.1/F17：SECURITY.md 缺失必须报错，不得静默返回空列表。"""
    with patch("verify_versions.SECURITY_PATH", tmp_path / "SECURITY.md"):
        errors = verify_versions.check_security_supported_version("0.6.9")

    assert len(errors) == 1
    assert "fail-closed" in errors[0]


# ============================================================================
# D22-001: 发行 tag 数值门（verify_versions.py --release-tag）
# ============================================================================


def _write_version_files(tmp_path, pyproject_v: str, manifest_v: str):
    """只写产品正本两文件：--release-tag 独立模式不应读取其他路径。"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nversion = "{pyproject_v}"\n', encoding="utf-8")
    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text(json.dumps({".": manifest_v}), encoding="utf-8")
    return pyproject, manifest


@pytest.mark.parametrize("tag", ["v0.11.7", "v1.0.0", "v0.0.0"])
def test_check_release_tag_version_accepts_matching_tags(tmp_path, tag):
    """轻量 / annotated tag 的差异只在远端 ref 对象类型，数值门只看 tag 字符串本身。"""
    pyproject, manifest = _write_version_files(tmp_path, tag[1:], tag[1:])
    with (
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
    ):
        assert verify_versions.check_release_tag_version(tag) == []


def test_check_release_tag_version_rejects_numeric_mismatch(tmp_path):
    """0.11.7 的提交上推 v0.11.8：数值不符必须同时报 pyproject 与 manifest。"""
    pyproject, manifest = _write_version_files(tmp_path, "0.11.7", "0.11.7")
    with (
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
    ):
        errors = verify_versions.check_release_tag_version("v0.11.8")

    joined = "\n".join(errors)
    assert "0.11.8" in joined
    assert "pyproject.toml" in joined
    assert ".release-please-manifest.json" in joined


def test_check_release_tag_version_rejects_manifest_mismatch(tmp_path):
    """tag 与 pyproject 相符但 manifest 不同值：照样失败（正本两处不得漂移）。"""
    pyproject, manifest = _write_version_files(tmp_path, "0.11.7", "0.11.8")
    with (
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
    ):
        errors = verify_versions.check_release_tag_version("v0.11.7")

    assert len(errors) == 1
    assert "manifest" in errors[0]


@pytest.mark.parametrize(
    "tag",
    ["0.11.7", "v0.11", "v0.11.7.1", "v0.11.7-rc.1", "v0.11.7+build.5", "v01.11.7", "release-v0.11.7", ""],
)
def test_check_release_tag_version_rejects_unsupported_forms(tmp_path, tag):
    """严格受支持 semver：缺 v 前缀 / 前导零 / 预发布 / 构建元数据一律拒绝。"""
    pyproject, manifest = _write_version_files(tmp_path, "0.11.7", "0.11.7")
    with (
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
    ):
        errors = verify_versions.check_release_tag_version(tag)

    assert len(errors) == 1
    assert "not a supported form" in errors[0]


def test_check_release_tag_version_reports_unreadable_product_version(tmp_path):
    """产品正本缺失时报错而非抛栈，保持门禁 fail-closed。"""
    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text(json.dumps({".": "0.11.7"}), encoding="utf-8")
    with (
        patch("verify_versions.PYPROJECT_PATH", tmp_path / "missing.toml"),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
    ):
        errors = verify_versions.check_release_tag_version("v0.11.7")

    assert len(errors) == 1
    assert "failed to read product version" in errors[0]


def test_main_release_tag_mode_is_standard_library_only(tmp_path):
    """--release-tag 是独立模式：不读取其他路径（发行 job 只有标准库，无 docs 依赖）。"""
    pyproject, manifest = _write_version_files(tmp_path, "0.11.7", "0.11.7")
    missing = tmp_path / "missing"
    unrelated_paths = (
        "PACKAGE_JSON_PATH",
        "CI_WORKFLOW_PATH",
        "README_PATH",
        "CONTRIBUTING_PATH",
        "SECURITY_PATH",
        "CLAUDE_PATH",
        "REQUIREMENTS_DEV_PATH",
        "SIDECAR_CARGO_PATH",
        "SIDECAR_PROTOCOL_PATH",
    )
    with ExitStack() as stack:
        stack.enter_context(patch("verify_versions.PYPROJECT_PATH", pyproject))
        stack.enter_context(patch("verify_versions.RELEASE_MANIFEST_PATH", manifest))
        for name in unrelated_paths:
            stack.enter_context(patch(f"verify_versions.{name}", missing))
        stack.enter_context(patch("sys.argv", ["verify_versions.py", "--release-tag", "v0.11.7"]))
        verify_versions.main()  # 不抛 SystemExit


def test_main_release_tag_mismatch_exits_nonzero(tmp_path):
    """数值不符必须在编译 / Release 创建前以非零退出。"""
    pyproject, manifest = _write_version_files(tmp_path, "0.11.7", "0.11.7")
    with (
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
        patch("sys.argv", ["verify_versions.py", "--release-tag", "v0.11.8"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        verify_versions.main()

    assert exc_info.value.code == 1


def test_main_release_tag_requires_value():
    """缺参数值属于用法错误（退出码 2），不得静默跳过门禁。"""
    with (
        patch("sys.argv", ["verify_versions.py", "--release-tag"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        verify_versions.main()

    assert exc_info.value.code == 2


def test_main_release_tag_rejects_fix_combination():
    """--fix 会改写 manifest，与只读数值门语义冲突，须显式拒绝。"""
    with (
        patch("sys.argv", ["verify_versions.py", "--release-tag", "v0.11.7", "--fix"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        verify_versions.main()

    assert exc_info.value.code == 2


# ============================================================================
# docs_markdown 委托契约（D5 回归守护）
# ============================================================================


def test_docs_markdown_module_attribute_preserved():
    """模块级 docs_markdown 属性必须保留：tests/docs 依赖它 monkeypatch 证明唯一解析。"""
    assert verify_versions.docs_markdown is not None
    assert verify_versions._load_docs_markdown() is verify_versions.docs_markdown


def test_release_tag_mode_works_without_docs_markdown(tmp_path):
    """发行门禁环境无 docs 依赖（docs_markdown=None）时 --release-tag 仍须通过。"""
    pyproject, manifest = _write_version_files(tmp_path, "0.11.7", "0.11.7")
    with (
        patch("verify_versions.docs_markdown", None),
        patch("verify_versions.PYPROJECT_PATH", pyproject),
        patch("verify_versions.RELEASE_MANIFEST_PATH", manifest),
        patch("sys.argv", ["verify_versions.py", "--release-tag", "v0.11.7"]),
    ):
        verify_versions.main()  # 不抛 SystemExit


def test_document_checks_fail_closed_without_docs_markdown(tmp_path):
    """真正执行文档检查而依赖缺失时必须显式失败，不得静默跳过装通过（F17/F22）。"""
    readme = tmp_path / "README.md"
    readme.write_text("# 标题\n\n[中文文档]()\n", encoding="utf-8")
    with (
        patch("verify_versions.docs_markdown", None),
        patch("verify_versions.README_PATH", readme),
        pytest.raises(SystemExit) as exc_info,
    ):
        verify_versions.check_empty_markdown_links()

    assert "docs_markdown" in str(exc_info.value)
