"""viewport 类控件使用守门扫描（AI 大脑空白 BUG 防复发）。

BUG 现场（2026-10）：model_management_panel 曾在宿主 scrollable Column
（无界主轴约束）内使用 ft.TabBarView，其 Flutter 侧 page-view viewport
布局崩溃产生 NaN 沿父链传染，AI 大脑整层不绘制、hit test 失效；语义树
仍完整（CanvasKit E2E anchor 断言全绿），仅 UIA 呈现 Infinity 矩形。

Flutter 规则（debugCheckHasBoundedAxis）：viewport 家族控件（TabBarView /
ListView / GridView / PageView / CustomScrollView）要求有界主轴约束；作为
scrollable 容器（Column/Row 带 scroll、ListView 等）的直接子级时布局崩溃。

静态全自动判定「跨组件父链是否有界」不可行（组件返回值可被任意容器装配），
故采用守门模式：枚举全 ui/ 的 viewport 调用点，要求全部命中白名单。
**新增调用点时本测试失败**——人工判定宿主上下文有界后，在此登记
（文件名 → 控件名 → 允许调用次数），判定依据写入白名单注释。
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

# 仓库 ui/ 目录（tests/unit/ui/test_xxx.py → 上溯 4 级到仓库根）。
_UI_DIR = Path(__file__).resolve().parents[3] / "ui"

#: 已人工判定「宿主上下文有界」的 viewport 调用点白名单。
#: 结构: {相对文件路径: {控件名: 允许调用次数}}；判定依据逐条注明。
ALLOWED_VIEWPORT_USAGE: dict[str, dict[str, int]] = {
    # TabBarView 父链: 页面 Stack → Container(expand) → Column(expand)，
    # 全程无 scrollable（回测结果面板嵌 ResizableSplitter，其内部无 scroll）。
    "components/backtest/backtest_result_panel.py": {"TabBarView": 1},
    # TabBarView 父链: DataView 页面 Stack → Container(expand)，有界（用户实测正常）。
    "views/data_view.py": {"TabBarView": 1},
    # ListView(expand=True) 父 Column 无 scroll（文件内 2 处 scroll 均在其他容器）。
    "views/screener_view.py": {"ListView": 1},
    # ListView 外层 Container(expand=True) 无 scroll（任务中心页骨架）。
    "views/task_center_view.py": {"ListView": 1},
    # 设置页签: 宿主 SettingsView Stack 有界；各 tab 用户实测正常渲染。
    "views/settings_tabs/data_source_tab.py": {"ListView": 1},
    "views/settings_tabs/system_tab.py": {"ListView": 1},
    # ListView 父 Column 无 scroll（tier_api_panel 全文件 0 处 scroll=）。
    "views/settings_tabs/tier_api_panel.py": {"ListView": 1},
}

#: 受守门的 viewport 控件名（Flutter RenderViewport 家族 + 高危候选）。
_GUARDED_CONTROLS = ("TabBarView", "ListView", "GridView", "PageView", "CustomScrollView")


def _iter_ui_python_files() -> Iterator[Path]:
    for path in sorted(_UI_DIR.rglob("*.py")):
        if path.is_file():
            yield path


def _count_viewport_calls(tree: ast.AST) -> dict[str, int]:
    """AST 统计单个文件内各 viewport 控件的调用次数（注释/docstring 不进 AST）。"""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _GUARDED_CONTROLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ft"
        ):
            counts[node.func.attr] = counts.get(node.func.attr, 0) + 1
    return counts


@pytest.mark.parametrize("control_name", _GUARDED_CONTROLS)
def test_no_unguarded_viewport_usage(control_name: str) -> None:
    """ui/ 下每处 viewport 调用点必须命中白名单（新增点需人工判定父链有界后登记）。"""
    found: list[str] = []
    for path in _iter_ui_python_files():
        rel = path.relative_to(_UI_DIR).as_posix()
        allowed = ALLOWED_VIEWPORT_USAGE.get(rel, {}).get(control_name, 0)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        actual = _count_viewport_calls(tree).get(control_name, 0)
        if actual > allowed:
            found.append(f"{rel}: {control_name} x{actual}（白名单允许 x{allowed}）")
    assert not found, (
        f"发现未登记的 ft.{control_name} 调用点：{found}。viewport 家族控件要求"
        "有界主轴约束，置于 scrollable 容器直接子级会布局崩溃产生 NaN 并传染"
        "整层不绘制（语义树仍完整，E2E 语义断言无法拦截）。请人工判定宿主"
        "上下文有界后，在 ALLOWED_VIEWPORT_USAGE 登记并注明判定依据；若宿主"
        "为 scrollable，改用条件渲染单页模式（见 model_management_panel.py）。"
    )


def test_whitelist_entries_exist() -> None:
    """白名单条目必须与实际代码同步（防白名单僵尸条目：控件改名/删除后残留）。"""
    stale: list[str] = []
    for rel, controls in ALLOWED_VIEWPORT_USAGE.items():
        path = _UI_DIR / rel
        assert path.is_file(), f"白名单文件不存在: {rel}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        actual = _count_viewport_calls(tree)
        for control_name, allowed_count in controls.items():
            if actual.get(control_name, 0) < allowed_count:
                stale.append(f"{rel}: {control_name} 允许 x{allowed_count} 但实际 x{actual.get(control_name, 0)}")
    assert not stale, f"白名单存在僵尸条目（代码已变更请同步白名单）：{stale}"
