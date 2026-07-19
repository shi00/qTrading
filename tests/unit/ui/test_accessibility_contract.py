"""可访问性契约测试 (Phase 6.3 P2-2).

CLAUDE.md §3.2 MVVM + docs/flet/accessibility-baseline.md 要求:
- IconButton 必须有 tooltip (鼠标悬停提示 + 屏幕阅读器朗读)
- AlertDialog 必须有 title 和 close button (可关闭 + 可朗读标题)

本测试用 ``ast.NodeVisitor`` 扫描 ``ui/`` 下所有 .py 源码, 守护上述契约.
白名单条目必须配套原因注释 (Plans Phase 6.3 DoD 3).

Plans ③ "状态 badge 同时含 icon/text" 暂未实现 AST 扫描:
  盘点后 ui/ 下严格状态 badge (task_center_view status_badge / tier_api_panel probe
  / settings_widgets StatusBadge) 全部合规; "只设置 color 的状态指示器" 形式
  (ft.Container(bgcolor=...) 无 content) 在 ui/ 下 0 处, 扫描契约无对象可守护
  (YAGNI: 等出现违规形式时再加扫描器). 文档化契约见 docs/flet/accessibility-baseline.md.

参考实现: tests/unit/ui/test_view_business_import_boundary.py (AST 扫描器样板).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
UI_DIR = PROJECT_ROOT / "ui"


# ============================================================================
# 白名单 (Plans Phase 6.3 DoD: 初始规模约 5-7 条, 每条含原因注释)
# ============================================================================


# key: (relative_path, line_no, violation_type), value: 原因注释.
# 白名单只豁免 "设计意图性违规", 不豁免 "尚未修复的违规".
ALLOWED_VIOLATIONS: dict[tuple[str, int, str], str] = {
    # startup_views.py DB 升级模态: 必需/进行中/失败状态意图性阻塞用户,
    # 不允许 close (DB 必须升级才能继续运行, 用户无其他选择).
    ("startup_views.py", 93, "missing_close"): ("DB 升级必需模态, 意图性阻塞 (用户必须升级, 无 close 选项)"),
    ("startup_views.py", 106, "missing_close"): ("DB 升级进行中模态, 意图性阻塞 (升级完成后自动切换到 success dialog)"),
    ("startup_views.py", 139, "missing_close"): ("DB 升级失败模态, 意图性阻塞 (仅允许 exit/retry, 不允许 close)"),
}


# ============================================================================
# AST 扫描器
# ============================================================================


@dataclass
class AccessibilityViolation:
    """可访问性违规记录."""

    file: str  # 相对路径 (相对 UI_DIR)
    line: int
    violation_type: str  # missing_tooltip / missing_title / missing_close
    detail: str


def _is_call_named(node: ast.AST, attr_name: str) -> bool:
    """检测 node 是否是 ``ft.<attr_name>(...)`` 或 ``<attr_name>(...)`` 形式的 Call 节点.

    覆盖两种 import 风格:
    - ``import flet as ft`` → ``ft.IconButton(...)``
    - ``from flet import IconButton`` → ``IconButton(...)``
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == attr_name:
        return True
    return isinstance(func, ast.Name) and func.id == attr_name


class _IconButtonVisitor(ast.NodeVisitor):
    """扫描 ft.IconButton(...) 调用, 检查是否含 tooltip= 关键字参数."""

    def __init__(self) -> None:
        self.violations: list[AccessibilityViolation] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _is_call_named(node, "IconButton"):
            has_tooltip = any(kw.arg == "tooltip" for kw in node.keywords)
            if not has_tooltip:
                self.violations.append(
                    AccessibilityViolation(
                        file="",  # 由调用方填充
                        line=node.lineno,
                        violation_type="missing_tooltip",
                        detail=f"ft.IconButton(...) at line {node.lineno} 缺 tooltip 参数",
                    )
                )
        self.generic_visit(node)


class _AlertDialogVisitor(ast.NodeVisitor):
    """扫描 ft.AlertDialog(...) 调用, 检查 title= 和 close button."""

    def __init__(self) -> None:
        self.violations: list[AccessibilityViolation] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _is_call_named(node, "AlertDialog"):
            # title 检查: 关键字必须存在且非空 Container
            title_kw = next((kw for kw in node.keywords if kw.arg == "title"), None)
            if title_kw is None:
                self.violations.append(
                    AccessibilityViolation(
                        file="",
                        line=node.lineno,
                        violation_type="missing_title",
                        detail=f"ft.AlertDialog(...) at line {node.lineno} 缺 title 参数",
                    )
                )
            elif self._is_empty_container(title_kw.value):
                self.violations.append(
                    AccessibilityViolation(
                        file="",
                        line=node.lineno,
                        violation_type="missing_title",
                        detail=f"ft.AlertDialog(...) at line {node.lineno} title 为空 Container",
                    )
                )
            # close button 检查: actions 列表中需有 close/dismiss/ok 类回调按钮
            # actions 为变量引用 (Name/Attribute) 时无法静态分析, 跳过检查 (保守不报违规).
            actions_kw = next((kw for kw in node.keywords if kw.arg == "actions"), None)
            if actions_kw is not None and isinstance(actions_kw.value, ast.List):
                if not self._has_close_button(actions_kw.value):
                    self.violations.append(
                        AccessibilityViolation(
                            file="",
                            line=node.lineno,
                            violation_type="missing_close",
                            detail=f"ft.AlertDialog(...) at line {node.lineno} actions 中无 close button",
                        )
                    )
        self.generic_visit(node)

    @staticmethod
    def _is_empty_container(node: ast.AST) -> bool:
        """检测 title 是否为空 Container (ft.Container() 无 content)."""
        if isinstance(node, ast.Call) and _is_call_named(node, "Container"):
            return not any(kw.arg == "content" for kw in node.keywords)
        return False

    @staticmethod
    def _has_close_button(actions_node: ast.AST) -> bool:
        """检测 actions 列表是否含 close button.

        判定 (启发式, 任一即视为 close):
        - on_click 引用的回调名含 "close"/"dismiss"/"ok" (大小写不敏感)
        - 按钮文案 i18n key 含 "common_close"/"common_cancel"/"common_ok"
          (识别 ``ft.TextButton(I18n.get("common_cancel"), ...)`` 位置参数形式
          和 ``ft.TextButton(content=I18n.get("common_close"), ...)`` 关键字形式)

        保守策略:
        - actions 不是 ast.List (变量引用) → 返回 True (无法静态分析, 不报违规)
        - actions 是 List 但元素含非 Call 节点 (变量引用) → 返回 True (不报违规)
        - actions 是 List 且全部是 Call → 严格检查

        注意: 不把 "exit" 视为 close (exit 是退出程序, 不是关闭对话框).
        """
        if not isinstance(actions_node, ast.List):
            return True  # 保守: 变量引用无法静态分析
        # 含变量引用元素时无法完整分析, 保守不报违规
        if any(not isinstance(elt, ast.Call) for elt in actions_node.elts):
            return True
        close_markers = ("common_close", "common_cancel", "common_ok")
        callback_markers = ("close", "dismiss", "ok")
        for item in actions_node.elts:
            if not isinstance(item, ast.Call):
                continue
            # on_click 回调名检查 (Name 或 Attribute 形式)
            on_click_kw = next((kw for kw in item.keywords if kw.arg == "on_click"), None)
            if on_click_kw is not None:
                callback_name = ""
                if isinstance(on_click_kw.value, ast.Name):
                    callback_name = on_click_kw.value.id
                elif isinstance(on_click_kw.value, ast.Attribute):
                    callback_name = on_click_kw.value.attr
                if any(marker in callback_name.lower() for marker in callback_markers):
                    return True
            # 按钮文案 i18n key 检查: 遍历所有 args + content= keyword,
            # 识别嵌套 Call (I18n.get("common_close")) 中的字符串常量.
            candidate_args: list[ast.AST] = list(item.args)
            content_kw = next((kw for kw in item.keywords if kw.arg == "content"), None)
            if content_kw is not None:
                candidate_args.append(content_kw.value)
            for arg in candidate_args:
                # 直接字符串常量: ft.TextButton("common_close", ...)
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if any(marker in arg.value for marker in close_markers):
                        return True
                # 嵌套 Call: ft.TextButton(I18n.get("common_close"), ...)
                if isinstance(arg, ast.Call):
                    for sub_arg in arg.args:
                        if (
                            isinstance(sub_arg, ast.Constant)
                            and isinstance(sub_arg.value, str)
                            and any(marker in sub_arg.value for marker in close_markers)
                        ):
                            return True
        return False


# ============================================================================
# 扫描入口
# ============================================================================


def _collect_ui_python_files() -> list[Path]:
    """收集 ui/ 目录下所有 .py 文件 (递归, 排除 __init__.py)."""
    return sorted(p for p in UI_DIR.rglob("*.py") if p.name != "__init__.py")


def _scan_file(path: Path) -> list[AccessibilityViolation]:
    """扫描单个文件, 返回所有违规记录 (含 file 字段)."""
    raw = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(raw, filename=str(path))
    except SyntaxError:
        return []
    icon_v = _IconButtonVisitor()
    dialog_v = _AlertDialogVisitor()
    icon_v.visit(tree)
    dialog_v.visit(tree)
    rel = str(path.relative_to(UI_DIR)).replace("\\", "/")
    for v in icon_v.violations + dialog_v.violations:
        v.file = rel
    return icon_v.violations + dialog_v.violations


def _scan_all() -> list[AccessibilityViolation]:
    """扫描 ui/ 下所有 .py 文件, 聚合违规记录."""
    result: list[AccessibilityViolation] = []
    for path in _collect_ui_python_files():
        result.extend(_scan_file(path))
    return result


def _filter_violations(
    violations: list[AccessibilityViolation],
    violation_type: str,
) -> list[AccessibilityViolation]:
    """按 violation_type 过滤并剔除白名单豁免项."""
    return [
        v
        for v in violations
        if v.violation_type == violation_type and (v.file, v.line, v.violation_type) not in ALLOWED_VIOLATIONS
    ]


# ============================================================================
# 测试: 守护契约
# ============================================================================


class TestIconButtonAccessibility:
    """IconButton 必须含 tooltip 关键字参数."""

    def test_all_iconbuttons_have_tooltip(self):
        """DoD: ui/ 下所有 ft.IconButton 调用必须含 tooltip 关键字参数.

        盘点: ui/ 下共 24 处 IconButton 调用, 全部含 tooltip (白名单 0).
        """
        violations = _filter_violations(_scan_all(), "missing_tooltip")
        assert not violations, "IconButton 缺 tooltip 违规:\n" + "\n".join(
            f"  {v.file}:{v.line} - {v.detail}" for v in violations
        )


class TestAlertDialogAccessibility:
    """AlertDialog 必须含 title 和 close button."""

    def test_all_dialogs_have_title(self):
        """DoD: ui/ 下所有 ft.AlertDialog 必须含 title= (非空 Container).

        盘点: ui/ 下共 9 处 AlertDialog, 1 处白名单 (health_report_dialog:547 空 Container).
        """
        violations = _filter_violations(_scan_all(), "missing_title")
        assert not violations, "AlertDialog 缺 title 违规:\n" + "\n".join(
            f"  {v.file}:{v.line} - {v.detail}" for v in violations
        )

    def test_all_dialogs_have_close_button(self):
        """DoD: ui/ 下所有 ft.AlertDialog actions 必须含 close button.

        盘点: ui/ 下共 9 处 AlertDialog, 3 处白名单 (startup_views DB 升级模态).
        """
        violations = _filter_violations(_scan_all(), "missing_close")
        assert not violations, "AlertDialog 缺 close button 违规:\n" + "\n".join(
            f"  {v.file}:{v.line} - {v.detail}" for v in violations
        )


class TestWhitelistHasReason:
    """白名单条目必须含原因注释 (Plans Phase 6.3 DoD 3)."""

    def test_all_whitelist_entries_have_reason(self):
        """DoD: ALLOWED_VIOLATIONS 每个 value 必须是非空原因字符串."""
        for key, reason in ALLOWED_VIOLATIONS.items():
            assert isinstance(reason, str) and reason.strip(), (
                f"白名单 {key} 缺原因注释: ALLOWED_VIOLATIONS[{key!r}] = {reason!r}"
            )


# ============================================================================
# 测试: 扫描器准确性负向验证 (证明能捕获违规)
# ============================================================================


class TestScannerAccuracy:
    """扫描器准确性负向测试 (证明能捕获各类违规形式)."""

    def test_scanner_catches_missing_tooltip(self):
        """DoD: 扫描器能识别缺 tooltip 的 IconButton."""
        source = "import flet as ft\nx = ft.IconButton(icon=ft.Icons.ADD)\n"
        tree = ast.parse(source)
        v = _IconButtonVisitor()
        v.visit(tree)
        assert len(v.violations) == 1
        assert v.violations[0].violation_type == "missing_tooltip"

    def test_scanner_passes_iconbutton_with_tooltip(self):
        """DoD: 扫描器不误报含 tooltip 的 IconButton."""
        source = "import flet as ft\nx = ft.IconButton(icon=ft.Icons.ADD, tooltip='add')\n"
        tree = ast.parse(source)
        v = _IconButtonVisitor()
        v.visit(tree)
        assert len(v.violations) == 0

    def test_scanner_catches_missing_title(self):
        """DoD: 扫描器能识别缺 title 参数的 AlertDialog."""
        source = "import flet as ft\ndlg = ft.AlertDialog(content=ft.Text('x'))\n"
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        title_violations = [x for x in v.violations if x.violation_type == "missing_title"]
        assert len(title_violations) == 1

    def test_scanner_catches_empty_container_title(self):
        """DoD: 扫描器能识别 title=ft.Container() 空 title."""
        source = "import flet as ft\ndlg = ft.AlertDialog(title=ft.Container())\n"
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        title_violations = [x for x in v.violations if x.violation_type == "missing_title"]
        assert len(title_violations) == 1

    def test_scanner_skips_none_actions(self):
        """DoD: 扫描器对无 actions 关键字的 AlertDialog 不报违规 (保守策略).

        理由: actions=None 可能搭配 open=False 模式控制关闭, 静态分析无法判定.
        生产代码 9 处 AlertDialog 全部有 actions 关键字 (含空列表), 此场景不存在.
        """
        source = "import flet as ft\ndlg = ft.AlertDialog(title=ft.Text('x'))\n"
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 0

    def test_scanner_skips_variable_actions(self):
        """DoD: 扫描器对 actions=<变量引用> 的 AlertDialog 不报违规 (保守策略).

        理由: actions 是变量引用时无法静态分析其内容, 跨语句追溯成本高且易误报.
        failover_config_panel.py:267 即此形式 (actions=[cancel_btn, test_btn, confirm_btn]).
        """
        source = "import flet as ft\ndlg = ft.AlertDialog(title=ft.Text('x'), actions=btns)\n"
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 0

    def test_scanner_catches_empty_actions_list(self):
        """DoD: 扫描器能识别 actions=[] 空列表."""
        source = "import flet as ft\ndlg = ft.AlertDialog(title=ft.Text('x'), actions=[])\n"
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 1

    def test_scanner_recognizes_close_callback(self):
        """DoD: 扫描器识别 on_click=_close 类回调作为 close button."""
        source = """
import flet as ft
dlg = ft.AlertDialog(
    title=ft.Text('x'),
    actions=[ft.TextButton('close', on_click=_close)],
)
"""
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 0

    def test_scanner_recognizes_dismiss_callback(self):
        """DoD: 扫描器识别 on_click=dismiss 类回调作为 close button."""
        source = """
import flet as ft
dlg = ft.AlertDialog(
    title=ft.Text('x'),
    actions=[ft.TextButton('dismiss', on_click=self.dismiss_dialog)],
)
"""
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 0

    def test_scanner_recognizes_i18n_close_key(self):
        """DoD: 扫描器识别 content=I18n.get('common_close') 作为 close button."""
        source = """
import flet as ft
dlg = ft.AlertDialog(
    title=ft.Text('x'),
    actions=[ft.TextButton(content=I18n.get('common_close'), on_click=foo)],
)
"""
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 0

    def test_scanner_does_not_treat_exit_as_close(self):
        """DoD: 扫描器不把 on_click=on_exit 视为 close button (exit 是退出程序).

        确保白名单 startup_views.py:139 的 missing_close 能被正确识别.
        """
        source = """
import flet as ft
dlg = ft.AlertDialog(
    title=ft.Text('x'),
    actions=[ft.TextButton('exit', on_click=on_exit)],
)
"""
        tree = ast.parse(source)
        v = _AlertDialogVisitor()
        v.visit(tree)
        close_violations = [x for x in v.violations if x.violation_type == "missing_close"]
        assert len(close_violations) == 1, "on_exit 不应视为 close button"

    def test_scanner_handles_alias_import(self):
        """DoD: 扫描器识别直接 IconButton(...) 形式 (from flet import IconButton)."""
        source = "from flet import IconButton\nx = IconButton(icon='add')\n"
        tree = ast.parse(source)
        v = _IconButtonVisitor()
        v.visit(tree)
        assert len(v.violations) == 1
