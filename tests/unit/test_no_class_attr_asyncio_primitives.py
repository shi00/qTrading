"""R11 红线 AST 守护测试。

R11: 跨循环复用同步原语 — 直接将 asyncio.Event/Lock/Semaphore 作为类属性
（必须通过 get_loop_local() 获取以绑定当前循环）。

本测试 AST 扫描 core/data/services/strategies/utils/ui/app/ 所有 .py 文件，
检测两种违规模式：
1. 类方法（含 __init__）内 `self._x = asyncio.X()` 实例属性赋值
2. 类属性级别 `_x = asyncio.X()` 直接赋值

白名单：
- 文件级：utils/loop_local.py（get_loop_local() 实现本身需要直接构造 asyncio 原语）
- 行级：`# R11_ALLOWED: <reason>` 注释
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 扫描范围：分层架构目录（CLAUDE.md §4.1）
SCAN_DIRS: tuple[str, ...] = ("core", "data", "services", "strategies", "utils", "ui", "app")

# 文件级白名单：get_loop_local() 实现本身需要直接构造 asyncio 原语
FILE_WHITELIST: frozenset[str] = frozenset({"utils/loop_local.py"})

# R11 禁止作为类属性/实例属性直接赋值的 asyncio 原语
FORBIDDEN_PRIMITIVES: frozenset[str] = frozenset({"Event", "Lock", "Semaphore"})

# 行级白名单标记：`# R11_ALLOWED: <reason>`
R11_ALLOWED_RE = re.compile(r"R11_ALLOWED:\s*\S+")


def _get_asyncio_primitive_name(node: ast.AST) -> str | None:
    """若 node 是 asyncio.Event()/Lock()/Semaphore() 调用，返回原语名；否则 None。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in FORBIDDEN_PRIMITIVES:
        return None
    value = func.value
    if isinstance(value, ast.Name) and value.id == "asyncio":
        return func.attr
    return None


def _is_r11_allowed(source_lines: list[str], lineno: int) -> bool:
    """检查给定行号（1-based）所在行是否包含 `# R11_ALLOWED: <reason>` 注释。"""
    if lineno < 1 or lineno > len(source_lines):
        return False
    return bool(R11_ALLOWED_RE.search(source_lines[lineno - 1]))


def _scan_class_body_for_violations(
    class_node: ast.ClassDef,
    source_lines: list[str],
    rel_path: str,
) -> list[str]:
    """扫描类体内的违规赋值，返回违规描述列表。

    检测两种模式：
    - 模式 a: 方法（FunctionDef/AsyncFunctionDef）内 `self._x = asyncio.X()`
    - 模式 b: 类体直接赋值 `_x = asyncio.X()`（类属性级别，不在任何方法内）
    """
    violations: list[str] = []

    def record_self_attr(assign_node: ast.Assign, method_name: str) -> None:
        primitive = _get_asyncio_primitive_name(assign_node.value)
        if primitive is None:
            return
        if _is_r11_allowed(source_lines, assign_node.lineno):
            return
        for target in assign_node.targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                violations.append(
                    f"{rel_path}:{assign_node.lineno}: R11 违规 — "
                    f"self.{target.attr} = asyncio.{primitive}() "
                    f"在方法 {method_name} 内（必须通过 get_loop_local() 获取）"
                )

    def record_class_attr(assign_node: ast.Assign) -> None:
        primitive = _get_asyncio_primitive_name(assign_node.value)
        if primitive is None:
            return
        if _is_r11_allowed(source_lines, assign_node.lineno):
            return
        for target in assign_node.targets:
            if isinstance(target, ast.Name):
                violations.append(
                    f"{rel_path}:{assign_node.lineno}: R11 违规 — "
                    f"类属性 {target.id} = asyncio.{primitive}() "
                    f"（必须通过 get_loop_local() 获取）"
                )

    for stmt in class_node.body:
        # 模式 b: 类体直接赋值（类属性级别）
        if isinstance(stmt, ast.Assign):
            record_class_attr(stmt)
        # 模式 a: 方法内 self._x = asyncio.X()
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(stmt):
                if isinstance(inner, ast.Assign):
                    record_self_attr(inner, stmt.name)
        # 嵌套类，递归处理
        elif isinstance(stmt, ast.ClassDef):
            violations.extend(_scan_class_body_for_violations(stmt, source_lines, rel_path))

    return violations


def _scan_file(path: Path) -> list[str]:
    """扫描单个 .py 文件，返回违规描述列表。"""
    rel_path = path.relative_to(PROJECT_ROOT).as_posix()
    if rel_path in FILE_WHITELIST:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    source_lines = source.splitlines()
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            violations.extend(_scan_class_body_for_violations(node, source_lines, rel_path))
    return violations


def _collect_python_files() -> list[Path]:
    """收集扫描范围内所有 .py 文件（动态遍历，不硬编码文件列表）。"""
    files: list[Path] = []
    for dir_name in SCAN_DIRS:
        dir_path = PROJECT_ROOT / dir_name
        if not dir_path.is_dir():
            continue
        files.extend(dir_path.rglob("*.py"))
    return files


# ============================================================================
# 全量扫描门禁
# ============================================================================


@pytest.mark.unit
def test_no_class_attr_asyncio_primitives() -> None:
    """R11 AST 守护：扫描所有分层架构目录的 .py 文件，
    确保不存在 asyncio.Event/Lock/Semaphore 直接作为类属性或实例属性的赋值。

    若失败，说明存在 R11 违规：asyncio 原语被绑定为类/实例属性，
    可能在不同事件循环间复用导致跨循环死锁。
    正确做法：通过 get_loop_local(key, factory) 获取以绑定当前循环。
    如需例外，在违规行添加 `# R11_ALLOWED: <reason>` 注释。
    """
    violations: list[str] = []
    files_scanned = 0
    for path in _collect_python_files():
        files_scanned += 1
        violations.extend(_scan_file(path))

    assert not violations, (
        f"R11 违规：检测到 {len(violations)} 处 asyncio 原语直接作为类属性赋值"
        f"（扫描 {files_scanned} 个文件）。"
        "必须通过 get_loop_local() 获取以绑定当前循环。"
        "如需例外，请在违规行添加 `# R11_ALLOWED: <reason>` 注释。\n" + "\n".join(f"  - {v}" for v in violations)
    )


# ============================================================================
# 检测逻辑单元测试（使用内存源码，验证扫描器行为）
# ============================================================================


def _scan_source(source: str, rel_path: str = "<test>") -> list[str]:
    """扫描源码字符串，返回违规描述列表（仅暴露扫描器核心逻辑）。"""
    tree = ast.parse(source)
    source_lines = source.splitlines()
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            violations.extend(_scan_class_body_for_violations(node, source_lines, rel_path))
    return violations


class TestScanLogic:
    """AST 检测逻辑单元测试。"""

    def test_detects_self_lock_in_init(self) -> None:
        """模式 a: 类 __init__ 中 self._lock = asyncio.Lock() 被检测。"""
        source = "import asyncio\nclass Foo:\n    def __init__(self):\n        self._lock = asyncio.Lock()\n"
        violations = _scan_source(source)
        assert len(violations) == 1
        assert "self._lock" in violations[0]
        assert "Lock" in violations[0]
        assert "__init__" in violations[0]

    def test_detects_self_event_in_other_method(self) -> None:
        """模式 a: 非 __init__ 方法中的 self._evt = asyncio.Event() 也被检测。"""
        source = "import asyncio\nclass Foo:\n    def setup(self):\n        self._evt = asyncio.Event()\n"
        violations = _scan_source(source)
        assert len(violations) == 1
        assert "self._evt" in violations[0]
        assert "Event" in violations[0]
        assert "setup" in violations[0]

    def test_detects_class_attr_event(self) -> None:
        """模式 b: 类属性级别 _evt = asyncio.Event() 被检测。"""
        source = "import asyncio\nclass Foo:\n    _evt = asyncio.Event()\n"
        violations = _scan_source(source)
        assert len(violations) == 1
        assert "_evt" in violations[0]
        assert "类属性" in violations[0]

    def test_detects_semaphore(self) -> None:
        """asyncio.Semaphore() 也被检测。"""
        source = "import asyncio\nclass Foo:\n    def __init__(self):\n        self._sem = asyncio.Semaphore(1)\n"
        violations = _scan_source(source)
        assert len(violations) == 1
        assert "self._sem" in violations[0]
        assert "Semaphore" in violations[0]

    def test_allows_r11_allowed_comment(self) -> None:
        """行级白名单 `# R11_ALLOWED: <reason>` 豁免。"""
        source = (
            "import asyncio\n"
            "class Foo:\n"
            "    def __init__(self):\n"
            "        self._lock = asyncio.Lock()  # R11_ALLOWED: test fixture\n"
        )
        violations = _scan_source(source)
        assert violations == []

    def test_allows_r11_allowed_comment_class_attr(self) -> None:
        """类属性级别的行级白名单也生效。"""
        source = "import asyncio\nclass Foo:\n    _evt = asyncio.Event()  # R11_ALLOWED: module-level singleton\n"
        violations = _scan_source(source)
        assert violations == []

    def test_ignores_local_variable_in_method(self) -> None:
        """方法内局部变量 evt = asyncio.Event() 不算违规（不是 self.x）。"""
        source = "import asyncio\nclass Foo:\n    def make(self):\n        evt = asyncio.Event()\n        return evt\n"
        violations = _scan_source(source)
        assert violations == []

    def test_ignores_module_level_assignment(self) -> None:
        """模块级别 _GLOBAL = asyncio.Lock() 不算违规（不在类体内）。"""
        source = "import asyncio\n_GLOBAL = asyncio.Lock()\n"
        violations = _scan_source(source)
        assert violations == []

    def test_ignores_non_self_attribute(self) -> None:
        """非 self 对象的属性赋值 obj.x = asyncio.Lock() 不算违规。"""
        source = (
            "import asyncio\n"
            "class Foo:\n"
            "    def make(self):\n"
            "        obj = Other()\n"
            "        obj.lock = asyncio.Lock()\n"
        )
        violations = _scan_source(source)
        assert violations == []

    def test_ignores_non_asyncio_call(self) -> None:
        """非 asyncio 模块的 Event()/Lock() 调用不算违规。"""
        source = "import threading\nclass Foo:\n    def __init__(self):\n        self._lock = threading.Lock()\n"
        violations = _scan_source(source)
        assert violations == []

    def test_r11_allowed_requires_reason(self) -> None:
        """`# R11_ALLOWED:` 后必须有 reason 内容，否则不豁免。"""
        source = (
            "import asyncio\nclass Foo:\n    def __init__(self):\n        self._lock = asyncio.Lock()  # R11_ALLOWED:\n"
        )
        violations = _scan_source(source)
        assert len(violations) == 1, "空 reason 不应被豁免"
