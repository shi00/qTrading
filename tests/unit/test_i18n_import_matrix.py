"""i18n 分层 import 矩阵守护测试 (R.5.2)。

守护 i18n 相关 import 的分层约束，防止回归：
1. core/i18n.py 不 import flet/ui/utils/data/services/strategies (§4.2 core 隔离)
2. ui/i18n.py 可 import flet + core.i18n (UI 层对 core.i18n 的薄封装)
3. ui/viewmodels/ import from core.i18n (非 ui.i18n，避免 flet 污染 VM)
4. strategies/services/data/utils import from core.i18n (非 ui.i18n)

与 test_core_i18n_purity.py 互补：
- test_core_i18n_purity.py: 守护 core/i18n.py 不导入 flet + 不定义 Observable state
- 本文件: 守护 i18n import 的分层矩阵 (VM 层 + 业务层从 core.i18n 导入)
"""

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent

pytestmark = pytest.mark.unit


def _get_module_level_imports(file_path: pathlib.Path) -> list[tuple[str, str]]:
    """提取文件中模块级 import 的 (模块, 符号) 列表。

    仅检查 tree.body 的直接子节点（模块级 import），跳过函数体内的
    lazy import（如 R.4.2 的 MetaDataManager lazy import）。
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    imports: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, alias.asname or alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((module, alias.name))
    return imports


def test_ui_i18n_import_matrix():
    """ui/i18n.py 可 import flet + core.i18n (UI 层薄封装)。

    ui/i18n.py 是 UI 层对 core.i18n 的薄封装 (Flet 文本绑定)，
    允许导入 flet + core.i18n，但不导入 data/services/strategies。
    """
    ui_i18n = PROJECT_ROOT / "ui" / "i18n.py"
    imports = _get_module_level_imports(ui_i18n)

    modules = {imp[0].split(".")[0] for imp in imports}

    # 允许 flet + core
    assert "flet" in modules or any("flet" in imp[0] for imp in imports), "ui/i18n.py 应导入 flet (UI 层薄封装)"
    assert "core" in modules, "ui/i18n.py 应导入 core.i18n"

    # 禁止业务层
    forbidden = modules & {"data", "services", "strategies"}
    assert not forbidden, f"ui/i18n.py 不应导入业务层: {forbidden}"


def test_viewmodel_i18n_import():
    """ui/viewmodels/ import from core.i18n (非 ui.i18n，避免 flet 污染 VM)。

    VM 层是纯状态+命令层，禁止 import flet (§3.2 MVVM)。
    如果从 ui.i18n 导入 I18n，会间接引入 flet 依赖。
    """
    vm_dir = PROJECT_ROOT / "ui" / "viewmodels"
    if not vm_dir.exists():
        pytest.skip("viewmodels directory does not exist")

    violations: list[str] = []
    for py_file in vm_dir.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        imports = _get_module_level_imports(py_file)
        for module, symbol in imports:
            if module == "ui.i18n" or module.startswith("ui.i18n."):
                violations.append(f"{py_file.name}: from {module} import {symbol}")

    assert not violations, "VM 层应从 core.i18n 导入 (非 ui.i18n)，避免 flet 污染:\n" + "\n".join(violations)


def test_business_layer_i18n_import():
    """strategies/services/data/utils import from core.i18n (非 ui.i18n)。

    业务层不应从 ui.i18n 导入，避免 UI 层 flet 依赖污染。
    """
    business_layers = ["strategies", "services", "data", "utils"]
    violations: list[str] = []

    for layer in business_layers:
        layer_dir = PROJECT_ROOT / layer
        if not layer_dir.exists():
            continue
        for py_file in layer_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            imports = _get_module_level_imports(py_file)
            for module, symbol in imports:
                if module == "ui.i18n" or module.startswith("ui.i18n."):
                    violations.append(f"{layer}/{py_file.relative_to(layer_dir)}: from {module} import {symbol}")

    assert not violations, "业务层应从 core.i18n 导入 (非 ui.i18n):\n" + "\n".join(violations)


# ============================================================
# VM 层 I18n.get() 调用 AST 守卫 (Task 3.1, CLAUDE.md §3.2 MVVM)
# ============================================================


def _has_whitelist_marker(src: str) -> bool:
    """检查源码首部 (前 30 行) 是否有文件级白名单标记 ``# I18N_GET_ALLOWED: <reason>``.

    白名单用于已知未迁移的例外, 必须附带迁移说明 (reason).
    文件级白名单会豁免整个文件的所有 I18n.get 调用; 优先使用函数级豁免 (见
    ``_function_has_whitelist``) 以缩小豁免范围.
    """
    for line in src.splitlines()[:30]:
        stripped = line.strip()
        if stripped.startswith("# I18N_GET_ALLOWED:"):
            return True
        # 模块 docstring 开始后, 注释行仍可标记
    return False


def _function_has_whitelist(
    src_lines: list[str],
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """检查函数体内是否有 ``# I18N_GET_ALLOWED: <reason>`` 标记 (函数级豁免).

    Task 3.1: 为精确豁免个别无法彻底迁移的函数 (如 strategy_desc 拼接路径),
    引入函数级白名单. 标记必须出现在函数体行范围内, 且必须附带迁移说明.
    """
    if func_node.end_lineno is None:
        return False
    for i in range(func_node.lineno - 1, func_node.end_lineno):
        if i < len(src_lines):
            stripped = src_lines[i].strip()
            if stripped.startswith("# I18N_GET_ALLOWED:"):
                return True
    return False


def _find_enclosing_function(
    tree: ast.Module,
    func_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef],
    lineno: int,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """找到包含给定行号的最内层函数定义."""
    enclosing = None
    for fn in func_nodes:
        end = fn.end_lineno or fn.lineno
        if fn.lineno <= lineno <= end:
            # 取最内层 (lineno 最大的)
            if enclosing is None or fn.lineno > enclosing.lineno:
                enclosing = fn
    return enclosing


def _is_i18n_reference(node: ast.AST) -> bool:
    """检查 AST 节点是否引用 ``I18n`` 类 (如 ``I18n.get`` / ``core.i18n.I18n.get``)."""
    if isinstance(node, ast.Name):
        return node.id == "I18n"
    if isinstance(node, ast.Attribute):
        # core.i18n.I18n 形式
        if node.attr == "I18n":
            return True
        return _is_i18n_reference(node.value)
    return False


def test_viewmodel_no_i18n_get_call():
    """ui/viewmodels/**/*.py 禁止 ``I18n.get()`` 调用 (§3.2 MVVM 契约, Task 3.1).

    VM 只产出 i18n key + params (via Message), View 按当前 locale 渲染.
    VM 中调 ``I18n.get`` 会污染 state with locale-dependent string, 违反 MVVM
    "VM 不感知 locale" 契约.

    白名单 (两级):
    1. 文件级: 源码首部 ``# I18N_GET_ALLOWED: <reason>`` 标记豁免整个文件 (慎用).
    2. 函数级: 函数体内 ``# I18N_GET_ALLOWED: <reason>`` 标记豁免单个函数
       (优先使用, 缩小豁免范围, 如 strategy_desc 拼接路径).

    标记必须附带迁移说明 (reason), 否则视为 no-trigger 高风险.
    """
    vm_dir = PROJECT_ROOT / "ui" / "viewmodels"
    if not vm_dir.exists():
        pytest.skip("viewmodels directory does not exist")

    violations: list[str] = []
    for py_file in vm_dir.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        src = py_file.read_text(encoding="utf-8")
        src_lines = src.splitlines()
        if _has_whitelist_marker(src):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            violations.append(f"{py_file.name}: SyntaxError {e}")
            continue
        # 预收集所有函数节点用于查找 I18n.get 调用所属函数
        func_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "get":
                continue
            if _is_i18n_reference(func.value):
                # 函数级豁免: 检查调用所在函数是否有 # I18N_GET_ALLOWED: 标记
                enclosing = _find_enclosing_function(tree, func_nodes, node.lineno)
                if enclosing is not None and _function_has_whitelist(src_lines, enclosing):
                    continue
                violations.append(
                    f"{py_file.name}: I18n.get() 调用 (line {node.lineno})",
                )

    assert not violations, "VM 层禁止 I18n.get() 调用 (§3.2 MVVM, VM 只产出 key+params):\n" + "\n".join(violations)


def test_message_locale_aware_render():
    """同一 Message 在 locale 切换后渲染为新语言 (DoD #1, Task 3.1).

    验证 ``I18n.get(msg.key, **msg.params)`` 在不同 locale 下返回不同字符串,
    确保存 Message (key+params) 而非已翻译字符串能正确响应 locale 切换.
    """
    from core.i18n import I18n, Message

    # 使用已存在的 i18n key (i18n 矩阵测试已确认存在)
    msg = Message("task_type_ai_screening", {})

    I18n.set_locale("zh_CN")
    zh_text = I18n.get(msg.key, **msg.params)

    I18n.set_locale("en_US")
    en_text = I18n.get(msg.key, **msg.params)

    assert zh_text != en_text, f"locale 切换后文本未变化: zh={zh_text!r}, en={en_text!r}"


def test_task_center_view_render_field_message_locale_aware():
    """TaskCenterView ``_render_task_field`` 按 locale 渲染 Message (DoD #1)."""
    from core.i18n import I18n, Message

    from ui.views.task_center_view import _render_task_field

    msg = Message("task_type_ai_screening", {})

    I18n.set_locale("zh_CN")
    zh_text = _render_task_field(msg)

    I18n.set_locale("en_US")
    en_text = _render_task_field(msg)

    assert zh_text != en_text, f"locale 切换后渲染未变化: zh={zh_text!r}, en={en_text!r}"


def test_task_center_view_render_field_str_passthrough():
    """TaskCenterView ``_render_task_field`` 对 str 直接透传 (DoD #3, 向后兼容旧持久化)."""
    from ui.views.task_center_view import _render_task_field

    assert _render_task_field("plain string") == "plain string"
    assert _render_task_field("") == ""


def test_apptask_supports_message_fields():
    """AppTask 的 name/task_type/description 字段支持 Message 类型 (Task 3.1).

    验证 services.task_manager.AppTask 字段可存 Message | str,
    VM 提交任务时传 Message, TaskManager 不调 I18n.get.
    """
    from core.i18n import Message
    from services.task_manager import AppTask

    task = AppTask(
        name=Message("task_type_ai_screening", {}),
        task_type=Message("task_type_backtest", {}),
        description=Message("task_loading_data", {}),
    )
    assert isinstance(task.name, Message)
    assert isinstance(task.task_type, Message)
    assert isinstance(task.description, Message)


def test_apptask_persist_roundtrip_with_message():
    """AppTask 持久化 Message 字段后, 加载回 Message (DoD #3 兼容旧 str)."""
    from core.i18n import Message
    from services.task_manager import AppTask, _serialize_msg_field, _deserialize_msg_field

    # Message 序列化/反序列化往返
    msg = Message("task_loading_data", {"count": 5})
    serialized = _serialize_msg_field(msg)
    deserialized = _deserialize_msg_field(serialized)
    assert isinstance(deserialized, Message)
    assert deserialized.key == "task_loading_data"
    assert deserialized.params == {"count": 5}

    # 旧 str 持久化字符串仍可显示 (向后兼容)
    legacy = "legacy translated text"
    assert _deserialize_msg_field(legacy) == legacy
    assert _serialize_msg_field(legacy) == legacy

    # AppTask 默认值仍为 str (向后兼容)
    task = AppTask()
    assert isinstance(task.name, str)
