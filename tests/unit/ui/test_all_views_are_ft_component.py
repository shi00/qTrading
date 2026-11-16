"""MVVM View ``@ft.component`` 守护测试 (Phase 5 Task 5.1).

遍历 ``ui/views/**/*.py`` (含 ``ui/views/settings_tabs/``)，
验证每个模块级大写开头 def 函数都带有 Flet 真实的 ``@ft.component`` 标记：
``__is_component__ is True`` 且 ``__component_impl__`` 可调用
(CLAUDE.md §3.2 MVVM 强制要求)。

守护必须 fail-closed：源码读取/解析失败、模块导入失败、空扫描均计为违规，
不允许静默跳过。普通 ``functools.wraps`` 装饰器同样带 ``__wrapped__``，
但不是 Flet 组件，因此不能作为判据。

白名单：
- 文件级：``__init__.py`` (无 view 定义)

参考: ``tests/unit/test_no_class_attr_asyncio_primitives.py`` 的 AST 扫描风格。
"""

import ast
import functools
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

SCAN_DIR = PROJECT_ROOT / "ui" / "views"

# 文件级白名单：__init__.py 无 view 定义
FILE_WHITELIST: frozenset[str] = frozenset({"__init__.py"})


class ViewScanError(Exception):
    """View 源码读取或解析失败；必须转为违规而非静默跳过。"""


@dataclass(frozen=True)
class ViewScanResult:
    """扫描结果；空扫描由断言层判定失败，而不是依赖调用方自觉检查。"""

    files_scanned: int
    functions_checked: int
    violations: list[str]


def _is_view_function_name(name: str) -> bool:
    """判断函数名是否符合 View 命名约定：首字母大写 (PascalCase)，不以 ``_`` 开头。"""
    return bool(name) and not name.startswith("_") and name[0].isupper()


def _extract_view_function_names(path: Path) -> list[str]:
    """从文件 AST 提取模块级大写开头的 def 函数名（排除 ``_`` 前缀私有函数）。

    Raises:
        ViewScanError: 源码不可读取或不可解析时抛出，由调用方计为违规。
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ViewScanError(f"无法读取源码 ({type(exc).__name__}: {exc})") from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        raise ViewScanError(f"无法解析源码 ({type(exc).__name__}: {exc})") from exc

    names: list[str] = []
    for node in tree.body:  # 仅模块级
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _is_view_function_name(node.name):
                names.append(node.name)
    return names


def _has_ft_component_marker(func: object) -> bool:
    """判断对象是否带 Flet 1.1.0 ``@ft.component`` 的真实标记。

    Flet 的 ``component_wrapper`` 同时设置 ``__is_component__ = True`` 与
    ``__component_impl__ = fn``；普通 ``functools.wraps`` 只有 ``__wrapped__``。
    """
    return getattr(func, "__is_component__", False) is True and callable(getattr(func, "__component_impl__", None))


def _display_path(path: Path) -> str:
    """返回项目内相对路径；测试临时目录等外部路径回退为绝对路径。"""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _module_dotted_path(path: Path) -> str:
    """将文件路径转为点分模块路径 (``ui/views/screener_view.py`` → ``ui.views.screener_view``)."""
    rel = path.relative_to(PROJECT_ROOT)
    return rel.with_suffix("").as_posix().replace("/", ".")


def _collect_view_files(scan_dir: Path) -> list[Path]:
    """收集 ``scan_dir`` 下所有 .py 文件 (含子目录)，排除白名单。"""
    return [p for p in scan_dir.rglob("*.py") if p.name not in FILE_WHITELIST]


def _scan_view_components(scan_dir: Path) -> ViewScanResult:
    """扫描目录下的 View 函数并收集违规；读取/解析/导入失败均 fail-closed。"""
    violations: list[str] = []
    files_scanned = 0
    functions_checked = 0

    for path in _collect_view_files(scan_dir):
        files_scanned += 1
        rel_path = _display_path(path)
        try:
            names = _extract_view_function_names(path)
        except ViewScanError as exc:
            violations.append(f"{rel_path}: {exc}")
            continue
        if not names:
            continue
        module_path = _module_dotted_path(path)
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            violations.append(f"{rel_path}: 模块 import 失败 ({type(exc).__name__}: {exc})")
            continue
        for name in names:
            func = getattr(module, name, None)
            if func is None:
                violations.append(f"{rel_path}: 函数 {name} 未在模块中找到")
                continue
            functions_checked += 1
            if not _has_ft_component_marker(func):
                violations.append(
                    f"{rel_path}: 函数 {name} 缺少真实的 @ft.component 标记 "
                    "(需要 __is_component__ is True 与 callable __component_impl__)"
                )

    return ViewScanResult(
        files_scanned=files_scanned,
        functions_checked=functions_checked,
        violations=violations,
    )


def _assert_guard_passes(scan_dir: Path) -> ViewScanResult:
    """执行守护断言：扫描量非空、无违规、实际检查到 View 函数。"""
    result = _scan_view_components(scan_dir)
    assert result.files_scanned > 0, (
        f"MVVM 守护未扫描到任何文件：{_display_path(scan_dir)} 下没有可检查的 .py 文件（空扫描视为失败）"
    )
    assert not result.violations, (
        f"MVVM 守护失败：检测到 {len(result.violations)} 处问题 "
        f"(扫描 {result.files_scanned} 个文件, 检查 {result.functions_checked} 个函数)。\n"
        "CLAUDE.md §3.2 强制：View = @ft.component 声明式组件（须有真实的 Flet 组件标记）。\n"
        + "\n".join(f"  - {v}" for v in result.violations)
    )
    assert result.functions_checked > 0, (
        f"MVVM 守护未检查到任何 View 函数（扫描 {result.files_scanned} 个文件）；扫描口径失效或目录为空，视为失败"
    )
    return result


def _write_module(directory: Path, stem: str, source: str) -> Path:
    path = directory / f"{stem}.py"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.unit
def test_all_views_are_ft_component() -> None:
    """Phase 5 Task 5.1 MVVM 守护：遍历 ``ui/views/**/*.py``，验证每个模块级
    大写开头 def 函数都带 Flet 真实组件标记 (``__is_component__`` +
    ``__component_impl__``)，且扫描量非空。

    若失败，说明存在 View 函数未声明为声明式组件或扫描静默失效，
    违反 CLAUDE.md §3.2 MVVM 强制要求 (View = ``@ft.component`` 声明式组件)。
    """
    _assert_guard_passes(SCAN_DIR)


@pytest.mark.unit
def test_marker_rejects_plain_functools_wraps_and_non_true_flag() -> None:
    """普通 ``functools.wraps`` 不应被当成 Flet 组件；真值非 ``True`` 的伪造标记也应拒绝。"""

    def impl() -> None:
        return None

    wrapped = functools.wraps(impl)(lambda: None)
    assert hasattr(wrapped, "__wrapped__")  # 旧判据会误放行
    assert not _has_ft_component_marker(wrapped)

    wrapped.__is_component__ = 1
    wrapped.__component_impl__ = impl
    assert not _has_ft_component_marker(wrapped)

    wrapped.__is_component__ = True
    assert _has_ft_component_marker(wrapped)

    wrapped.__component_impl__ = None
    assert not _has_ft_component_marker(wrapped)


@pytest.mark.unit
def test_scan_rejects_plain_wraps_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描器必须拒绝仅带 ``__wrapped__`` 的普通装饰器 View。"""
    import tests.unit.ui.test_all_views_are_ft_component as guard

    _write_module(
        tmp_path,
        "plain_wraps_view",
        """
import functools


def passthrough(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@passthrough
def WrappedView():
    return None
""",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(guard, "_module_dotted_path", lambda path: path.stem)

    result = guard._scan_view_components(tmp_path)

    assert result.files_scanned == 1
    assert result.functions_checked == 1
    assert any("WrappedView" in v and "缺少真实的 @ft.component 标记" in v for v in result.violations)
    with pytest.raises(AssertionError, match="缺少真实的 @ft.component 标记"):
        guard._assert_guard_passes(tmp_path)


@pytest.mark.unit
def test_scan_rejects_bare_view_without_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无任何装饰器的 PascalCase 函数必须计为违规。"""
    import tests.unit.ui.test_all_views_are_ft_component as guard

    _write_module(tmp_path, "bare_view", "def BareView():\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(guard, "_module_dotted_path", lambda path: path.stem)

    result = guard._scan_view_components(tmp_path)

    assert result.functions_checked == 1
    assert any("BareView" in v and "缺少真实的 @ft.component 标记" in v for v in result.violations)
    with pytest.raises(AssertionError, match="缺少真实的 @ft.component 标记"):
        guard._assert_guard_passes(tmp_path)


@pytest.mark.unit
def test_scan_fails_closed_on_broken_source(tmp_path: Path) -> None:
    """源码不可解析时必须保留带文件定位的违规，而不是返回空结果。"""
    import tests.unit.ui.test_all_views_are_ft_component as guard

    _write_module(tmp_path, "broken_view", "def BrokenView(:\n")

    result = guard._scan_view_components(tmp_path)

    assert result.files_scanned == 1
    assert result.functions_checked == 0
    assert len(result.violations) == 1
    assert "broken_view.py" in result.violations[0]
    assert "无法解析源码" in result.violations[0]
    with pytest.raises(AssertionError, match="broken_view"):
        guard._assert_guard_passes(tmp_path)


@pytest.mark.unit
def test_guard_fails_closed_on_empty_scan(tmp_path: Path) -> None:
    """空目录（零扫描量）必须失败，不能空转通过。"""
    import tests.unit.ui.test_all_views_are_ft_component as guard

    with pytest.raises(AssertionError, match="未扫描到任何文件"):
        guard._assert_guard_passes(tmp_path)


@pytest.mark.unit
def test_scan_accepts_real_ft_component(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """合法 ``@ft.component`` View 应通过，且被实际检查计数。"""
    import tests.unit.ui.test_all_views_are_ft_component as guard

    _write_module(
        tmp_path,
        "real_component_view",
        """
import flet as ft


@ft.component
def GoodView():
    return ft.Text(value="ok")
""",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(guard, "_module_dotted_path", lambda path: path.stem)
    monkeypatch.delitem(sys.modules, "real_component_view", raising=False)

    result = guard._assert_guard_passes(tmp_path)

    assert result.files_scanned == 1
    assert result.functions_checked == 1
    assert result.violations == []
