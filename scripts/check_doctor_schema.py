"""Check the doctor JSON field contract between Rust sidecar and Python service.

契约裁决（D16-Q01）：
- Rust ``struct DoctorJson`` 是 doctor stdout 的 wire schema（``doctor.v1``）。
- Python ``@dataclass class DoctorResult`` 是**消费投影**，不是 wire 镜像：只镜像 UI/维护
  包装实际消费的字段；其余诊断细节保留在 CLI JSON，故障结论由 ``issues`` 承载。
- ``REQUIRED_MIRRORED_FIELDS``：必须同时出现在 Rust 与 Python 的字段（含 ``issues`` 故障渠道）。
- ``ALLOWED_RUST_ONLY_FIELDS``：经裁决只存在于 Rust、Python 不消费的字段；必须显式登记
  理由与承载故障的 ``issues`` code（纯信息性字段的 ``issue_codes`` 为空）。
- 任何新增 Rust 字段未登记到上述两张表 → 失败；Python-only 字段 → 失败；声明的 issue code
  必须仍出现在 Rust 源码中，防止「字段还在、故障不再上报」的静默退化。

Closes P3-VerifyVersions-DoctorSchema (pg_plan §15.5 AI-12 schema drift 守护).

Usage: python scripts/check_doctor_schema.py
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUST_MAINT_PATH = ROOT / "sidecars" / "qtrading-pg-sidecar" / "src" / "maint.rs"
PYTHON_SERVICE_PATH = ROOT / "services" / "embedded_pg_maintenance_service.py"

# 必须同时镜像到 Python DoctorResult 的字段。新增/删除这些字段必须同步更新本表。
REQUIRED_MIRRORED_FIELDS: frozenset[str] = frozenset(
    {
        "schema",
        "data_dir",
        "initialized",
        "pg_version",
        "bundled_pg_major",
        "version_match",
        "critical_files_missing",
        "install_dir_complete",
        "missing_tools",
        "lock_held",
        "postgres_alive",
        "port",
        "state_file",
        "runtime_status",
        "last_start_error",
        "issues",
        "restore_residuals",
        "dump_partials",
    }
)


@dataclass(frozen=True, slots=True)
class IgnoredRustField:
    """经裁决不进入 Python DTO 的 Rust 字段。

    reason: 为什么 Python 侧不消费该字段（必填，供审计）。
    issue_codes: 该字段对应的故障在 ``issues`` 中使用的 code；纯信息性字段为空。
    """

    reason: str
    issue_codes: tuple[str, ...] = ()


# Rust-only 字段白名单：新增 Rust 字段必须在此显式登记，否则脚本失败（防止静默忽略）。
ALLOWED_RUST_ONLY_FIELDS: dict[str, IgnoredRustField] = {
    "cloud_sync_feature": IgnoredRustField(
        "网盘同步检测结果仅用于诊断细节；故障由 issues cloud_sync_path 承载",
        ("cloud_sync_path",),
    ),
    "cluster_state": IgnoredRustField(
        "cluster 状态仅用于诊断细节；异常由 issues cluster_state_abnormal 承载",
        ("cluster_state_abnormal",),
    ),
    "control_data_readable": IgnoredRustField(
        "pg_control 可读性仅用于诊断细节；故障由 issues pg_control_unreadable 承载",
        ("pg_control_unreadable",),
    ),
    "data_checksums": IgnoredRustField("纯信息性字段（cluster checksum 开关），不承载故障"),
    "free_bytes": IgnoredRustField(
        "剩余空间仅用于诊断细节；不足由 issues disk_low 承载",
        ("disk_low",),
    ),
    "fs_kind": IgnoredRustField(
        "文件系统类型仅用于诊断细节；不支持由 issues fs_unsupported 承载",
        ("fs_unsupported",),
    ),
    "fs_supported": IgnoredRustField(
        "文件系统支持判定仅用于诊断细节；不支持由 issues fs_unsupported 承载",
        ("fs_unsupported",),
    ),
    "hba_matches_template": IgnoredRustField(
        "pg_hba.conf 漂移仅用于诊断细节；故障由 issues hba_drift 承载",
        ("hba_drift",),
    ),
    "kill_fallback_count": IgnoredRustField(
        "kill fallback 次数仅用于诊断细节；历史故障由 issues kill_fallback_history 承载",
        ("kill_fallback_history",),
    ),
    "last_stop_mode": IgnoredRustField("纯信息性字段（上次停止方式），不承载故障"),
    "managed_block_drift": IgnoredRustField(
        "受管配置漂移仅用于诊断细节；故障由 issues managed_block_drift 承载",
        ("managed_block_drift",),
    ),
    "password_file_perms_ok": IgnoredRustField(
        "密码文件权限仅用于诊断细节；故障由 issues password_file_perms 承载",
        ("password_file_perms",),
    ),
    "password_file_present": IgnoredRustField(
        "密码文件存在性仅用于诊断细节；故障由 issues password_file_missing 承载",
        ("password_file_missing",),
    ),
    "postmaster_pid": IgnoredRustField("运行态 pid 仅用于展示；进程活性由 postgres_alive/port 表达"),
    "stale_postmaster_pid": IgnoredRustField(
        "pid 残留仅用于诊断细节；故障由 issues stale_postmaster_pid 承载",
        ("stale_postmaster_pid",),
    ),
}

# 匹配 `struct DoctorJson { ... }` body(到第一个 `}` 为止,DoctorJson 字段类型均无嵌套 `{}`)。
_STRUCT_DOCTOR_JSON_RE = re.compile(r"struct\s+DoctorJson\s*\{(?P<body>[^}]*)\}", re.MULTILINE)
# 匹配 struct body 内的字段名行(`pub` 可选,后跟 `name: Type`)。跳过 `//`/`///` 注释行。
_RUST_FIELD_RE = re.compile(r"^\s*(?:pub\s+)?(?P<name>\w+)\s*:", re.MULTILINE)


def parse_rust_doctor_json_fields(source: str) -> set[str]:
    """从 Rust 源代码中提取 ``struct DoctorJson`` 的字段名集合。

    解析失败或找不到 struct 时返回空集合。
    """
    m = _STRUCT_DOCTOR_JSON_RE.search(source)
    if not m:
        return set()
    body = m.group("body")
    fields: set[str] = set()
    for line in body.splitlines():
        stripped = line.lstrip()
        # 跳过行注释与块注释行
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        m_field = _RUST_FIELD_RE.match(line)
        if m_field:
            name = m_field.group("name")
            if name:
                fields.add(name)
    return fields


def parse_python_doctor_result_fields(source: str) -> set[str]:
    """从 Python 源代码中提取 ``@dataclass class DoctorResult`` 的字段名集合。

    通过 AST 解析,提取带类型注解的类属性(AnnAssign 节点)。
    解析失败或找不到类时返回空集合。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "DoctorResult"):
            continue
        # 必须有 @dataclass 装饰器(支持 @dataclass 与 @dataclass(...) 两种形式)
        if not _has_dataclass_decorator(node):
            continue
        return _extract_annotated_fields(node)
    return set()


def _has_dataclass_decorator(class_node: ast.ClassDef) -> bool:
    """判断 ClassDef 是否有 ``@dataclass`` 装饰器。"""
    for dec in class_node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "dataclass":
            return True
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "dataclass":
            return True
    return False


def _extract_annotated_fields(class_node: ast.ClassDef) -> set[str]:
    """从 ClassDef body 提取带类型注解的字段名(``name: Type = ...`` 形式)。"""
    fields: set[str] = set()
    for stmt in class_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.add(stmt.target.id)
    return fields


def _validate_contract_tables() -> list[str]:
    """校验契约表自身的一致性（配置错误应显式失败，不能静默）。"""
    errors: list[str] = []
    overlap = REQUIRED_MIRRORED_FIELDS & ALLOWED_RUST_ONLY_FIELDS.keys()
    if overlap:
        errors.append(f"contract config error: fields both required and ignored: {sorted(overlap)}")
    for name, spec in sorted(ALLOWED_RUST_ONLY_FIELDS.items()):
        if not spec.reason.strip():
            errors.append(f"contract config error: ignored Rust field {name!r} has empty reason")
    return errors


def _rust_issue_code_present(source: str, code: str) -> bool:
    """判断 issue code 是否仍以字符串字面量出现在 Rust 源码中。"""
    return re.search(rf'"{re.escape(code)}"', source) is not None


def check_doctor_schema_consistency(
    rust_path: Path = RUST_MAINT_PATH,
    python_path: Path = PYTHON_SERVICE_PATH,
) -> list[str]:
    """校验 Rust ``DoctorJson`` 与 Python ``DoctorResult`` 的字段契约。

    返回错误消息列表（空列表表示通过）。IO 失败时使用 ``classify_error`` 分类，
    其余情况返回差异描述。
    """
    errors: list[str] = _validate_contract_tables()
    if errors:
        return errors

    rust_source = _read_source(rust_path, errors, "Rust")
    python_source = _read_source(python_path, errors, "Python")
    if errors:
        return errors

    rust_fields = parse_rust_doctor_json_fields(rust_source)
    python_fields = parse_python_doctor_result_fields(python_source)

    if not rust_fields:
        errors.append(f"struct DoctorJson not found or has no fields in {rust_path}")
    if not python_fields:
        errors.append(f"@dataclass class DoctorResult not found or has no fields in {python_path}")
    if errors:
        return errors

    missing_in_rust = REQUIRED_MIRRORED_FIELDS - rust_fields
    missing_in_python = REQUIRED_MIRRORED_FIELDS - python_fields
    rust_only = rust_fields - python_fields
    python_only = python_fields - rust_fields
    unregistered_rust_only = rust_only - ALLOWED_RUST_ONLY_FIELDS.keys()
    ignored_but_mirrored = ALLOWED_RUST_ONLY_FIELDS.keys() & python_fields

    if missing_in_rust:
        errors.append(f"required DoctorJson fields missing in Rust: {sorted(missing_in_rust)}")
    if missing_in_python:
        errors.append(f"required DoctorJson fields missing in Python DoctorResult: {sorted(missing_in_python)}")
    if unregistered_rust_only:
        errors.append(
            "Rust DoctorJson has unregistered Rust-only fields "
            f"{sorted(unregistered_rust_only)}; mirror them into DoctorResult or register them in "
            "ALLOWED_RUST_ONLY_FIELDS with a reason (do not silently ignore new diagnostics)"
        )
    if python_only:
        errors.append(f"Python DoctorResult has fields not in Rust DoctorJson: {sorted(python_only)}")
    if ignored_but_mirrored:
        errors.append(
            "fields registered in ALLOWED_RUST_ONLY_FIELDS are also present in Python DoctorResult "
            f"(move them to REQUIRED_MIRRORED_FIELDS): {sorted(ignored_but_mirrored)}"
        )

    # 只检查实际出现在 Rust 中的白名单字段，避免用局部 mock 源时误报。
    for name in sorted(ALLOWED_RUST_ONLY_FIELDS.keys() & rust_fields):
        for code in ALLOWED_RUST_ONLY_FIELDS[name].issue_codes:
            if not _rust_issue_code_present(rust_source, code):
                errors.append(
                    f"ignored Rust field {name!r} declares issue code {code!r}, "
                    f"but {code!r} is not emitted in {rust_path}"
                )

    return errors


def _read_source(path: Path, errors: list[str], label: str) -> str:
    """读取源文件,IO 失败时用 ``classify_error`` 分类并追加到 errors。

    返回源代码字符串(失败时返回空字符串)。
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        from utils.error_classifier import classify_error

        info = classify_error(exc, context="file_io")
        errors.append(
            f"cannot read {label} source {path}: {exc} "
            f"(type={info.get('error_type', 'unknown')}, severity={info.get('severity', 'unknown')})"
        )
        return ""


def main() -> None:
    errors = check_doctor_schema_consistency(RUST_MAINT_PATH, PYTHON_SERVICE_PATH)
    if errors:
        print("DoctorJson schema check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print("DoctorJson schema check passed: required fields mirrored; Rust-only fields are explicitly allowlisted.")


if __name__ == "__main__":
    main()
