"""Check DoctorJson schema consistency between Rust sidecar and Python service.

Compares field name set of:
- Rust: ``struct DoctorJson { ... }`` in ``sidecars/qtrading-pg-sidecar/src/maint.rs``
- Python: ``@dataclass class DoctorResult`` in ``services/embedded_pg_maintenance_service.py``

Reports field set differences (Rust-only / Python-only) and exits non-zero on mismatch.
Closes P3-VerifyVersions-DoctorSchema (pg_plan §15.5 AI-12 schema drift 守护).

Usage: python scripts/check_doctor_schema.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUST_MAINT_PATH = ROOT / "sidecars" / "qtrading-pg-sidecar" / "src" / "maint.rs"
PYTHON_SERVICE_PATH = ROOT / "services" / "embedded_pg_maintenance_service.py"

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


def check_doctor_schema_consistency(
    rust_path: Path = RUST_MAINT_PATH,
    python_path: Path = PYTHON_SERVICE_PATH,
) -> list[str]:
    """比较 Rust ``DoctorJson`` 与 Python ``DoctorResult`` 的字段集合。

    返回错误消息列表(空列表表示一致)。IO 失败时使用 ``classify_error`` 分类,
    其他情况返回差异描述。
    """
    errors: list[str] = []

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

    rust_only = rust_fields - python_fields
    python_only = python_fields - rust_fields

    if rust_only:
        errors.append(f"Rust DoctorJson has fields not in Python DoctorResult: {sorted(rust_only)}")
    if python_only:
        errors.append(f"Python DoctorResult has fields not in Rust DoctorJson: {sorted(python_only)}")

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
    print("DoctorJson schema check passed: Rust struct fields match Python dataclass fields.")


if __name__ == "__main__":
    main()
