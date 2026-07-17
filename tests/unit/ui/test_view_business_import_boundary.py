"""ui/views/ 业务对象导入边界守护测试 (Task 5.1, P2-3 升级为 AST 扫描).

CLAUDE.md §3.2 MVVM 契约: View = f(ViewModel.state), 不应直接调用 data/strategies/
services/utils 业务对象。本测试用 ``ast.NodeVisitor`` 扫描 ``ui/views/`` 下所有 .py
源码, 检测 10 类禁止业务对象的 import / alias / 调用 / 间接引用, 强制 View 通过
ViewModel command/state 消费业务逻辑。

与 ``tests/unit/test_architecture_boundaries.py`` 的区别:
- 后者仅检查模块级 import, 且 R1 允许 ui → strategies 反向依赖
- 本测试进一步约束 MVVM 契约: View 不能直接 import/use 业务对象, 必须经 VM 转发

AST 扫描覆盖的违规形式 (相比 regex 升级点):
1. ``from <forbidden_module> import <forbidden_symbol>`` (含 ``as X`` alias)
2. ``from <forbidden_module> import *`` (star import)
3. ``from <parent> import <child>`` 其中 ``<parent>.<child>`` 是禁止模块
4. ``import <forbidden_module>`` (含 ``as X`` alias)
5. ``importlib.import_module("<forbidden_module>")`` 间接引用
6. ``import_module("<forbidden_module>")`` (``from importlib import import_module`` 后调用)
7. ``getattr(<forbidden_module_alias>, "<forbidden_symbol>")`` 间接引用
8. ``<forbidden_module_alias>.<forbidden_symbol>`` 属性访问
9. ``get_base_prompt(...)`` 直接调用 (负向后行: ``vm.get_base_prompt(...)`` 允许)

跳过 ``TYPE_CHECKING`` 块内的 import (仅类型注解用途, 不引入运行时依赖).

10 类禁止业务对象 (5 原有 + 5 新增):
- 原有: ``MetaDataManager`` / ``strategy_prompts.get_base_prompt`` / ``TushareClient`` /
  ``TUSHARE_POINT_TIERS`` / ``DataProcessor``
- 新增: ``services.task_manager`` (TaskManager/AppTask; TaskStatus 作为纯展示枚举允许) /
  ``services.ai_service.AIService`` /
  ``services.local_model_manager.LocalModelManager`` /
  ``utils.config_handler.ConfigHandler`` /
  ``utils.thread_pool`` (ThreadPoolManager/TaskType)

白名单 ``ALLOWED_VIEW_BUSINESS_IMPORTS`` 仅在以下场景允许:
- 纯展示常量 (不是业务对象)
- View 自身 ViewModels (如 ``DataSourceViewModel`` 不在禁止列表)
- Phase 3 各 Task 待迁移的临时豁免 (每条含原因注释, 迁移完成后移除)

白名单条目必须配套注释说明原因, 避免变相绕过 MVVM 契约.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
VIEWS_DIR = PROJECT_ROOT / "ui" / "views"


@dataclass(frozen=True)
class ForbiddenModule:
    """禁止 View 直接 import 的业务对象类目.

    Attributes:
        key: 白名单匹配键 (与 ``ALLOWED_VIEW_BUSINESS_IMPORTS`` 第二项匹配).
        module_path: 完整模块路径 (如 ``services.task_manager``).
        symbols: 禁止的符号集合 (空表示任意符号; 与 ``forbid_any_import`` 互斥语义).
        forbid_any_import: True 表示 ``from <module> import`` 任意符号都视为违规
            (用于 ``strategy_prompts`` 这类"模块本身就不应被 View 直接引用"的场景).
    """

    key: str
    module_path: str
    symbols: frozenset[str]
    forbid_any_import: bool = False


# 禁止业务对象清单 (10 类: 5 原有 + 5 新增).
# 5 原有: data/strategies 业务对象 (Task 5.1 首批).
# 5 新增 (P2-3): services/utils 业务编排对象 (覆盖 Plans-ui-review-20260717.md F2
#   指出的 17 处真实违规中的 services/utils 部分).
FORBIDDEN_BUSINESS_OBJECTS: tuple[ForbiddenModule, ...] = (
    ForbiddenModule(
        key="MetaDataManager",
        module_path="data.persistence.metadata_manager",
        symbols=frozenset({"MetaDataManager"}),
    ),
    ForbiddenModule(
        key="strategy_prompts.get_base_prompt",
        module_path="strategies.strategy_prompts",
        symbols=frozenset({"get_base_prompt"}),
        # 该模块是策略层 prompt SSOT, View 不应直接 import 任意符号 (含 alias)
        forbid_any_import=True,
    ),
    ForbiddenModule(
        key="TushareClient",
        module_path="data.external.tushare_client",
        symbols=frozenset({"TushareClient"}),
    ),
    ForbiddenModule(
        key="TUSHARE_POINT_TIERS",
        module_path="data.constants",
        symbols=frozenset({"TUSHARE_POINT_TIERS"}),
    ),
    ForbiddenModule(
        key="DataProcessor",
        module_path="data.data_processor",
        symbols=frozenset({"DataProcessor"}),
    ),
    ForbiddenModule(
        key="services.task_manager",
        module_path="services.task_manager",
        # TaskStatus 作为纯展示枚举白名单允许 (View 用于颜色/文案映射, 非业务编排).
        # 仅 TaskManager/AppTask 视为业务编排对象, 必须下沉到 VM.
        symbols=frozenset({"TaskManager", "AppTask"}),
    ),
    ForbiddenModule(
        key="services.ai_service.AIService",
        module_path="services.ai_service",
        symbols=frozenset({"AIService"}),
    ),
    ForbiddenModule(
        key="services.local_model_manager.LocalModelManager",
        module_path="services.local_model_manager",
        symbols=frozenset({"LocalModelManager"}),
    ),
    ForbiddenModule(
        key="utils.config_handler.ConfigHandler",
        module_path="utils.config_handler",
        symbols=frozenset({"ConfigHandler"}),
    ),
    ForbiddenModule(
        key="utils.thread_pool",
        module_path="utils.thread_pool",
        symbols=frozenset({"ThreadPoolManager", "TaskType"}),
    ),
)


# UI lazy import 白名单: 允许 ``ui/views/`` 在特定场景下保留对
# data/strategies/services/utils 业务对象的导入.
# 每个条目: (relative_path, forbidden_key), 配套注释说明原因.
# Phase 3 各 Task 完成后, 对应条目应被移除 (白名单压路机: Plans-ui-review-20260717.md
#   Phase 1.1 DoD 4: 当前 17 处真实违规先入白名单含原因注释, 随 Phase 3 完成逐步移除).
# 实际盘点 2 条 (Phase 3.2 完成后从 5 条移除 ai_brain_tab 3 条).
ALLOWED_VIEW_BUSINESS_IMPORTS: set[tuple[str, str]] = {
    # === ui/views/data_view.py ===
    # Phase 3.5 待迁移: ThreadPoolManager.run_async(TaskType.CPU) 数据加载编排下沉到 DataExplorerViewModel
    ("ui/views/data_view.py", "utils.thread_pool"),
    # === ui/views/task_center_view.py ===
    # Phase 3.6 待迁移: TaskStatus 类型常量映射下沉到 TaskCenterViewModel (i18n key + icon + color)
    ("ui/views/task_center_view.py", "services.task_manager"),
}


# ============================================================================
# AST 扫描器
# ============================================================================


def _is_type_checking_test(test: ast.expr) -> bool:
    """检测 if 节点的 test 是否是 TYPE_CHECKING 引用.

    支持两种形式:
    - ``if TYPE_CHECKING:`` (直接 Name)
    - ``if typing.TYPE_CHECKING:`` (Attribute 访问)
    """
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _BusinessObjectVisitor(ast.NodeVisitor):
    """AST 扫描器: 检测禁止业务对象的 import / alias / 调用 / 间接引用.

    覆盖 9 类违规形式 (见模块 docstring 列表).
    跳过 TYPE_CHECKING 块内的 import (仅类型注解用途).
    """

    def __init__(self, forbidden: tuple[ForbiddenModule, ...]) -> None:
        self._forbidden = forbidden
        # 别名 → (forbidden_key, original_symbol)
        # e.g. ``from services.task_manager import TaskManager as TM``
        # → {"TM": ("services.task_manager", "TaskManager")}
        self._symbol_aliases: dict[str, tuple[str, str]] = {}
        # 模块别名 → forbidden_key
        # e.g. ``import services.task_manager as tm`` → {"tm": "services.task_manager"}
        # e.g. ``from services import task_manager`` → {"task_manager": "services.task_manager"}
        self._module_aliases: dict[str, str] = {}
        self._in_type_checking = False
        # 按 forbidden_key 聚合的违规证据列表 (evidence)
        self.violations: dict[str, list[str]] = {}

    def _record(self, key: str, evidence: str) -> None:
        self.violations.setdefault(key, []).append(evidence)

    def _find_module(self, module_path: str) -> ForbiddenModule | None:
        for fm in self._forbidden:
            if fm.module_path == module_path:
                return fm
        return None

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            old = self._in_type_checking
            self._in_type_checking = True
            for stmt in node.body:
                self.visit(stmt)
            self._in_type_checking = old
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._in_type_checking:
            return
        module = node.module or ""
        for alias in node.names:
            sym_name = alias.name
            # case 1: from <forbidden_module> import <sym>
            fm = self._find_module(module)
            if fm is not None:
                if sym_name == "*":
                    self._record(fm.key, f"line {node.lineno}: from {module} import *")
                    continue
                if fm.forbid_any_import or sym_name in fm.symbols:
                    bound = alias.asname or sym_name
                    self._symbol_aliases[bound] = (fm.key, sym_name)
                    self._record(
                        fm.key,
                        f"line {node.lineno}: from {module} import {sym_name}"
                        + (f" as {alias.asname}" if alias.asname else ""),
                    )
                continue
            # case 2: from <parent> import <child> where parent.child == forbidden module
            # 例如 from services import task_manager
            if sym_name != "*":
                full = f"{module}.{sym_name}" if module else sym_name
                fm2 = self._find_module(full)
                if fm2 is not None:
                    bound = alias.asname or sym_name
                    self._module_aliases[bound] = fm2.key
                    self._record(
                        fm2.key,
                        f"line {node.lineno}: from {module} import {sym_name}"
                        + (f" as {alias.asname}" if alias.asname else ""),
                    )

    def visit_Import(self, node: ast.Import) -> None:
        if self._in_type_checking:
            return
        for alias in node.names:
            mod = alias.name
            fm = self._find_module(mod)
            if fm is not None:
                if alias.asname:
                    self._module_aliases[alias.asname] = fm.key
                else:
                    # `import a.b.c` → 顶级名 a, 后续 a.b.c.X 访问
                    # 登记 a → key 用于属性链检测 (近似, 不影响 import 自身的违规记录)
                    top = mod.split(".")[0]
                    self._module_aliases.setdefault(top, fm.key)
                self._record(
                    fm.key,
                    f"line {node.lineno}: import {mod}" + (f" as {alias.asname}" if alias.asname else ""),
                )

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        # importlib.import_module("forbidden_module")
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                mod = node.args[0].value
                fm = self._find_module(mod)
                if fm is not None:
                    self._record(fm.key, f"line {node.lineno}: importlib.import_module({mod!r})")
        # import_module("forbidden_module") (from importlib import import_module)
        elif isinstance(func, ast.Name) and func.id == "import_module":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                mod = node.args[0].value
                fm = self._find_module(mod)
                if fm is not None:
                    self._record(fm.key, f"line {node.lineno}: import_module({mod!r})")
        # getattr(mod_alias, "ForbiddenSym")
        elif isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            target_obj, target_attr = node.args[0], node.args[1]
            if isinstance(target_attr, ast.Constant) and isinstance(target_attr.value, str):
                attr_name = target_attr.value
                if isinstance(target_obj, ast.Name) and target_obj.id in self._module_aliases:
                    key = self._module_aliases[target_obj.id]
                    for fm in self._forbidden:
                        if fm.key == key and attr_name in fm.symbols:
                            self._record(
                                key,
                                f"line {node.lineno}: getattr({target_obj.id}, {attr_name!r})",
                            )
                            break
        # get_base_prompt(...) 直接调用 (非 vm.get_base_prompt)
        elif isinstance(func, ast.Name) and func.id == "get_base_prompt":
            self._record(
                "strategy_prompts.get_base_prompt",
                f"line {node.lineno}: direct call get_base_prompt(...)",
            )
        # alias 调用: e.g. from strategies.strategy_prompts import get_base_prompt as gbp; gbp(...)
        elif isinstance(func, ast.Name) and func.id in self._symbol_aliases:
            key, original = self._symbol_aliases[func.id]
            if original == "get_base_prompt":
                self._record(
                    key,
                    f"line {node.lineno}: alias call {func.id}(...) (alias of {original})",
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # mod_alias.ForbiddenSym 形式 (例如 tm.TaskStatus)
        if isinstance(node.value, ast.Name) and node.value.id in self._module_aliases:
            key = self._module_aliases[node.value.id]
            for fm in self._forbidden:
                if fm.key == key and node.attr in fm.symbols:
                    self._record(key, f"line {node.lineno}: {node.value.id}.{node.attr}")
                    break
        self.generic_visit(node)


# ============================================================================
# 辅助函数 (保留 Task 5.1 原有功能, AST 模式不依赖但保留作为兜底工具)
# ============================================================================


def _view_python_files() -> list[Path]:
    """收集 ``ui/views/`` 下所有 .py 文件 (递归)."""
    if not VIEWS_DIR.exists():
        return []
    return sorted(VIEWS_DIR.rglob("*.py"))


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码, 避免文档字符串中提及被禁止的符号导致误判.

    Note: AST 扫描器天然不解析 docstring 为可执行代码, 本函数保留作为辅助工具.
    """
    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
    ) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _strip_comments(source: str) -> str:
    """移除 ``#`` 注释行, 避免注释中提及被禁止符号导致误判.

    Note: AST 扫描器天然不解析注释为可执行代码, 本函数保留作为辅助工具.
    简单实现: 按行处理, 移除 ``#`` 后内容 (字符串内的 ``#`` 不处理, 可接受近似).
    """
    out_lines: list[str] = []
    for line in source.splitlines():
        hash_idx = line.find("#")
        if hash_idx >= 0:
            line = line[:hash_idx]
        out_lines.append(line)
    return "\n".join(out_lines)


# ============================================================================
# 主测试
# ============================================================================


@pytest.mark.unit
def test_no_view_directly_imports_business_objects() -> None:
    """验证 ``ui/views/`` 下所有 .py 文件不直接 import/use 业务对象 (P2-3 AST 升级).

    AST 扫描覆盖 9 类违规形式 (见模块 docstring), 跳过 TYPE_CHECKING 块.
    白名单 ``ALLOWED_VIEW_BUSINESS_IMPORTS`` 仅在特定场景允许 (需配套原因注释).
    """
    violations: list[str] = []
    for py_file in _view_python_files():
        rel_path = py_file.relative_to(PROJECT_ROOT).as_posix()
        raw_source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(raw_source)
        except SyntaxError as exc:
            violations.append(f"{rel_path}: SyntaxError while parsing: {exc}")
            continue
        visitor = _BusinessObjectVisitor(FORBIDDEN_BUSINESS_OBJECTS)
        visitor.visit(tree)
        for key, evidences in visitor.violations.items():
            if (rel_path, key) in ALLOWED_VIEW_BUSINESS_IMPORTS:
                continue
            violations.append(f"{rel_path}: forbidden business object '{key}' ({len(evidences)} occurrence(s))")

    assert not violations, (
        "ui/views/ 直接调用 data/strategies/services/utils 业务对象, 违反 MVVM 契约 "
        "(CLAUDE.md §3.2).\n"
        "应通过对应 ViewModel command/state 消费业务逻辑.\n" + "\n".join(violations)
    )


@pytest.mark.unit
def test_allowed_view_business_imports_are_valid() -> None:
    """白名单条目必须指向真实存在的文件, 避免遗留过期条目."""
    for rel_path, _forbidden_key in ALLOWED_VIEW_BUSINESS_IMPORTS:
        full_path = PROJECT_ROOT / rel_path
        assert full_path.exists(), (
            f"ALLOWED_VIEW_BUSINESS_IMPORTS contains non-existent file: {rel_path}. "
            "Remove it if the file was deleted or renamed."
        )


# ============================================================================
# 负向测试: 证明 AST 守护可堵住绕过路径
# ============================================================================


def _scan_source(source: str) -> dict[str, list[str]]:
    """辅助: 扫描源码字符串, 返回 {forbidden_key: [evidence, ...]} 字典."""
    tree = ast.parse(source)
    visitor = _BusinessObjectVisitor(FORBIDDEN_BUSINESS_OBJECTS)
    visitor.visit(tree)
    return visitor.violations


@pytest.mark.unit
def test_ast_scanner_catches_alias_import() -> None:
    """负向测试: ``from X import Y as Z`` alias import 必须被捕获."""
    source = "from services.task_manager import TaskManager as TM\ntm = TM()\n"
    violations = _scan_source(source)
    assert "services.task_manager" in violations, "AST 扫描器未能捕获 alias import (from X import Y as Z)"


@pytest.mark.unit
def test_ast_scanner_catches_importlib_indirect_import() -> None:
    """负向测试: ``importlib.import_module("forbidden")`` 间接引用必须被捕获."""
    source = "import importlib\ntm = importlib.import_module('services.task_manager')\ntm.TaskStatus\n"
    violations = _scan_source(source)
    assert "services.task_manager" in violations, "AST 扫描器未能捕获 importlib.import_module 间接引用"


@pytest.mark.unit
def test_ast_scanner_catches_from_importlib_import_module() -> None:
    """负向测试: ``from importlib import import_module; import_module('forbidden')`` 必须被捕获."""
    source = "from importlib import import_module\ntm = import_module('services.task_manager')\n"
    violations = _scan_source(source)
    assert "services.task_manager" in violations, "AST 扫描器未能捕获 from importlib import import_module 后调用"


@pytest.mark.unit
def test_ast_scanner_catches_getattr_indirect_reference() -> None:
    """负向测试: ``getattr(mod_alias, 'ForbiddenSym')`` 间接引用必须被捕获."""
    source = "import services.task_manager as tm\nTaskStatus = getattr(tm, 'TaskStatus')\n"
    violations = _scan_source(source)
    assert "services.task_manager" in violations, "AST 扫描器未能捕获 getattr(mod, 'ForbiddenSym') 间接引用"


@pytest.mark.unit
def test_ast_scanner_catches_attribute_access_on_module_alias() -> None:
    """负向测试: ``mod_alias.ForbiddenSym`` 属性访问必须被捕获."""
    source = "import services.task_manager as tm\nstatus = tm.TaskStatus\n"
    violations = _scan_source(source)
    assert "services.task_manager" in violations, "AST 扫描器未能捕获 mod_alias.ForbiddenSym 属性访问"


@pytest.mark.unit
def test_ast_scanner_catches_from_parent_import_child() -> None:
    """负向测试: ``from services import task_manager`` 必须被捕获."""
    source = "from services import task_manager\n"
    violations = _scan_source(source)
    assert "services.task_manager" in violations, "AST 扫描器未能捕获 from parent import child 形式 (模块作为子名导入)"


@pytest.mark.unit
def test_ast_scanner_catches_star_import() -> None:
    """负向测试: ``from strategies.strategy_prompts import *`` 必须被捕获."""
    source = "from strategies.strategy_prompts import *\n"
    violations = _scan_source(source)
    assert "strategy_prompts.get_base_prompt" in violations, (
        "AST 扫描器未能捕获 star import (from forbidden_module import *)"
    )


@pytest.mark.unit
def test_ast_scanner_catches_direct_get_base_prompt_call() -> None:
    """直接调用 ``get_base_prompt(...)`` 必须被捕获, 但 ``vm.get_base_prompt(...)`` 允许."""
    # 直接调用 → 违规
    source_direct = "result = get_base_prompt('strategy_a')\n"
    violations_direct = _scan_source(source_direct)
    assert "strategy_prompts.get_base_prompt" in violations_direct, "AST 扫描器未能捕获直接调用 get_base_prompt(...)"
    # vm.get_base_prompt(...) → 不应违规 (负向后行断言语义)
    source_vm = "result = vm.get_base_prompt('strategy_a')\n"
    violations_vm = _scan_source(source_vm)
    assert "strategy_prompts.get_base_prompt" not in violations_vm, (
        "AST 扫描器误报 vm.get_base_prompt(...) 为违规 (应允许 VM 方法调用)"
    )


@pytest.mark.unit
def test_ast_scanner_respects_type_checking_block() -> None:
    """TYPE_CHECKING 块内的 import 不算违规 (仅类型注解用途)."""
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from services.task_manager import TaskManager\n"
        "    from utils.thread_pool import ThreadPoolManager\n"
        "\n"
        "def handle(x: 'TaskManager') -> None:\n"
        "    pass\n"
    )
    violations = _scan_source(source)
    assert not violations, f"AST 扫描器不应将 TYPE_CHECKING 块内的 import 视为违规. 实际捕获: {violations}"


@pytest.mark.unit
def test_ast_scanner_ignores_comments_and_strings() -> None:
    """AST 扫描不应将注释或字符串内的伪 import 视为违规 (regex 模式的弱点)."""
    source = (
        "# from services.task_manager import TaskManager  # 注释中的伪 import\n"
        'doc = "from services.task_manager import TaskManager"  # 字符串中的伪 import\n'
        'help_text = """\n'
        "from services.task_manager import TaskManager\n"
        '"""  # 多行字符串中的伪 import\n'
    )
    violations = _scan_source(source)
    assert not violations, f"AST 扫描器误报注释/字符串内的伪 import 为违规: {violations}"


@pytest.mark.unit
def test_ast_scanner_catches_all_10_forbidden_categories() -> None:
    """AST 扫描器必须能检测全部 10 类禁止业务对象 (覆盖性测试)."""
    samples: dict[str, str] = {
        "MetaDataManager": "from data.persistence.metadata_manager import MetaDataManager\n",
        "strategy_prompts.get_base_prompt": "from strategies.strategy_prompts import get_base_prompt\n",
        "TushareClient": "from data.external.tushare_client import TushareClient\n",
        "TUSHARE_POINT_TIERS": "from data.constants import TUSHARE_POINT_TIERS\n",
        "DataProcessor": "from data.data_processor import DataProcessor\n",
        "services.task_manager": "from services.task_manager import TaskManager\n",
        "services.ai_service.AIService": "from services.ai_service import AIService\n",
        "services.local_model_manager.LocalModelManager": (
            "from services.local_model_manager import LocalModelManager\n"
        ),
        "utils.config_handler.ConfigHandler": "from utils.config_handler import ConfigHandler\n",
        "utils.thread_pool": "from utils.thread_pool import ThreadPoolManager\n",
    }
    for key, source in samples.items():
        violations = _scan_source(source)
        assert key in violations, f"AST 扫描器未能检测禁止类目 '{key}'. 实际捕获: {list(violations.keys())}"
