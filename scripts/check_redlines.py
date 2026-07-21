"""红线自动化检查脚本（R4/R12/R13/R14/R15 + UI 裸 ft.Colors 拦截）。

依据 CLAUDE.md §3.1 红线表，对项目代码进行静态分析：
- R4  SQL 注入：扫描 asyncpg 原生查询中的 %s 占位符（必须用 $1, $2, ...）
- R12 数据表未注册：对比 models.py 的 __tablename__ 与 data_dictionary.py 的 TABLE_DEFINITIONS
- R13 DAO 未注册：对比 daos/ 下的 DAO 类与 CacheManager.__init__ 实例化清单
- R14 策略未注册：扫描继承 BaseStrategy/PolarsBaseStrategy 的类是否使用 @register_strategy
- R15 单例未注册：扫描带 _instance/__new__ 的单例类是否使用 @register_singleton
- R_no_bare_ft_colors_in_ui: 扫描 UI 层裸 ft.Colors.<COLOR> 引用 (必须替换为 AppColors token)

退出码：0 通过，1 失败。供 pre-commit `redline-check` hook 与 pytest 契约测试调用。

R16（UI 阻塞主循环）因 AST 检查误报风险高暂未实现，登记于 docs/debt/known-technical-debt.md 已知技术债（CONTRIBUTING.md 仅保留入口索引）。
"""

from __future__ import annotations

import ast
import sys
import typing
from collections.abc import Iterator
from io import TextIOWrapper
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ============================================================================
# 公共工具
# ============================================================================

# 受检扫描应跳过的目录（第三方代码、构建产物、缓存、测试代码等）
_SKIP_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".worktrees",
        ".tmp",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "tests",
    }
)


def _iter_py_files(root: Path, exclude_dirs: frozenset[str] | None = None) -> Iterator[Path]:
    """遍历 root 下所有 .py 文件，跳过排除目录。

    用相对路径检查目录名，避免主工作区 ROOT 中 .worktrees 被误匹配。
    """
    skip = exclude_dirs if exclude_dirs is not None else _SKIP_DIRS
    for p in root.rglob("*.py"):
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip for part in rel_parts):
            continue
        yield p


def _parse_module(path: Path) -> ast.Module | None:
    """解析 .py 文件为 AST，失败返回 None。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None


def _decorator_names(node: ast.ClassDef) -> set[str]:
    """提取类装饰器名称集合（支持 @x 和 @x(...) 两种形式，含属性链）。"""
    names: set[str] = set()
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.add(dec.id)
        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
            names.add(dec.func.id)
        elif isinstance(dec, ast.Attribute):
            names.add(dec.attr)
        elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            names.add(dec.func.attr)
    return names


def _base_class_names(node: ast.ClassDef) -> set[str]:
    """提取类直接基类名称集合（Name.id 或 Attribute.attr，不递归）。"""
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


# ============================================================================
# R4: SQL 注入检查（asyncpg 原生查询中 %s 占位符）
# ============================================================================

# asyncpg 原生查询方法名（区分于 SQLAlchemy 的 conn.execute(sa.text(...))）
# SQLAlchemy 调用的第一个参数是 sa.text(...) 表达式或 stmt 变量，非字符串字面量，自然不匹配
_ASYNCPG_QUERY_METHODS = frozenset({"execute", "fetch", "fetchrow", "fetchval", "executemany"})


def _check_R4_in_tree(tree: ast.Module, source_path: Path) -> list[str]:
    """纯函数：检查 AST 中的 asyncpg 原生查询是否含 %s 占位符。"""
    errors: list[str] = []
    rel = source_path.relative_to(ROOT)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # 仅匹配 conn.<method>(...) 形式
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _ASYNCPG_QUERY_METHODS:
            continue
        if not node.args:
            continue
        # 第一个参数必须是字符串字面量（asyncpg 原生调用模式）
        # SQLAlchemy 调用模式如 conn.execute(sa.text(...)) 或 conn.execute(stmt)
        # 第一个参数是 Call 或 Name，不是 Constant，自然不被匹配
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            continue
        sql = first_arg.value
        if "%s" in sql:
            errors.append(
                f"{rel}:{node.lineno}: R4 SQL 注入 — asyncpg 原生查询使用 %s 占位符 (必须用 $1, $2, ...): {sql[:80]!r}"
            )
    return errors


def check_R4() -> list[str]:
    """R4：扫描 data/ 目录下所有 .py 文件中的 asyncpg 原生查询 %s 占位符。"""
    errors: list[str] = []
    data_dir = ROOT / "data"
    for p in _iter_py_files(data_dir):
        tree = _parse_module(p)
        if tree is None:
            continue
        errors.extend(_check_R4_in_tree(tree, p))
    return errors


# ============================================================================
# R12: 数据表未注册检查（models.py 的 __tablename__ 与 TABLE_DEFINITIONS 对比）
# ============================================================================

# Alembic 自动管理的表，不在 ORM 中是合理的（豁免项）
_R12_EXEMPT_TABLENAMES = frozenset({"alembic_version"})


def _extract_tablenames_from_models(path: Path) -> set[str]:
    """从 models.py 中提取所有 __tablename__ = "xxx" 的字符串值。"""
    tree = _parse_module(path)
    if tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id != "__tablename__":
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                names.add(node.value.value)
    return names


def _extract_table_definition_keys(path: Path) -> set[str]:
    """从 data_dictionary.py 的 TABLE_DEFINITIONS = {...} 中提取所有 key 字符串字面量。"""
    tree = _parse_module(path)
    if tree is None:
        return set()
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id != "TABLE_DEFINITIONS":
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def check_R12() -> list[str]:
    """R12：对比 models.py 的 __tablename__ 与 data_dictionary.py 的 TABLE_DEFINITIONS。"""
    models_path = ROOT / "data" / "persistence" / "models.py"
    dict_path = ROOT / "data" / "data_dictionary.py"

    model_tables = _extract_tablenames_from_models(models_path)
    dict_keys = _extract_table_definition_keys(dict_path)

    errors: list[str] = []
    # ORM 中定义但未注册到 TABLE_DEFINITIONS 的表
    missing_in_dict = model_tables - dict_keys
    for t in sorted(missing_in_dict):
        errors.append(
            f"R12 未注册数据表: models.py 定义 __tablename__='{t}' "
            f"但 data/data_dictionary.py 的 TABLE_DEFINITIONS 未包含"
        )
    # TABLE_DEFINITIONS 中有但 ORM 没有的（除豁免项如 alembic_version）
    missing_in_models = (dict_keys - model_tables) - _R12_EXEMPT_TABLENAMES
    for t in sorted(missing_in_models):
        errors.append(
            f"R12 数据表无 ORM 定义: data/data_dictionary.py 的 TABLE_DEFINITIONS "
            f"包含 '{t}' 但 models.py 中无 __tablename__ 定义"
        )
    return errors


# ============================================================================
# R13: DAO 未注册检查（daos/ 下的 DAO 类与 CacheManager.__init__ 实例化对比）
# ============================================================================


def _extract_dao_classes(daos_dir: Path) -> dict[str, Path]:
    """扫描 daos/ 目录下所有 *_dao.py 文件，提取继承 BaseDao 的类名 → 文件路径。"""
    result: dict[str, Path] = {}
    for p in sorted(daos_dir.glob("*_dao.py")):
        if p.name == "base_dao.py":
            continue
        tree = _parse_module(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = _base_class_names(node)
            if "BaseDao" in bases:
                result[node.name] = p
    return result


def _extract_cache_manager_dao_instances(path: Path) -> set[str]:
    """从 CacheManager.__init__ 方法中提取 self.<x>_dao = <ClassName>(...) 调用的 ClassName 集合。

    仅扫描 __init__ 方法体，避免误捕获 read_db/write_db 中的 BaseDao(self.engine) 调用。
    """
    tree = _parse_module(path)
    if tree is None:
        return set()
    instantiated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "CacheManager":
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name != "__init__":
                continue
            for sub in ast.walk(item):
                if not isinstance(sub, ast.Assign) or len(sub.targets) != 1:
                    continue
                target = sub.targets[0]
                if not isinstance(target, ast.Attribute) or not isinstance(sub.value, ast.Call):
                    continue
                func = sub.value.func
                if isinstance(func, ast.Name):
                    instantiated.add(func.id)
                elif isinstance(func, ast.Attribute):
                    instantiated.add(func.attr)
    return instantiated


def check_R13() -> list[str]:
    """R13：对比 daos/ 下的 DAO 类与 CacheManager.__init__ 实例化清单。

    仅检查 __init__ 实例化维度；_create_engine 中 .engine 引用更新维度未检查，
    因其与 __init__ 实例化一一对应，违反 __init__ 维度即已触发 R13。
    """
    daos_dir = ROOT / "data" / "persistence" / "daos"
    cache_manager_path = ROOT / "data" / "cache" / "cache_manager.py"

    dao_classes = _extract_dao_classes(daos_dir)
    instantiated = _extract_cache_manager_dao_instances(cache_manager_path)

    errors: list[str] = []
    for cls_name, src_path in sorted(dao_classes.items()):
        if cls_name not in instantiated:
            rel = src_path.relative_to(ROOT)
            errors.append(
                f"R13 未注册 DAO: {rel} 定义 DAO 类 '{cls_name}' "
                f"但 CacheManager.__init__ 未实例化（应在 data/cache/cache_manager.py 中 "
                f"self.<name>_dao = {cls_name}(self.engine) 并在 _create_engine 中更新 .engine 引用）"
            )
    return errors


# ============================================================================
# R14: 策略未注册检查（继承 BaseStrategy/PolarsBaseStrategy 的类需 @register_strategy）
# ============================================================================

# 策略基类与 mixin，自身不应被注册（不参与 R14 检查）
_R14_BASE_CLASSES = frozenset({"BaseStrategy", "PolarsBaseStrategy", "AIStrategyMixin"})


def _is_strategy_subclass(node: ast.ClassDef) -> bool:
    """判断类是否继承 BaseStrategy 或 PolarsBaseStrategy（不含基类自身与 mixin）。"""
    bases = _base_class_names(node)
    if not (bases & {"BaseStrategy", "PolarsBaseStrategy"}):
        return False
    # 排除基类自身（BaseStrategy / PolarsBaseStrategy 不应被注册）
    return node.name not in _R14_BASE_CLASSES


def check_R14() -> list[str]:
    """R14：扫描 strategies/ 目录中继承 BaseStrategy/PolarsBaseStrategy 的类是否使用 @register_strategy。"""
    strategies_dir = ROOT / "strategies"
    errors: list[str] = []

    for p in _iter_py_files(strategies_dir):
        tree = _parse_module(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not _is_strategy_subclass(node):
                continue
            decorators = _decorator_names(node)
            if "register_strategy" not in decorators:
                rel = p.relative_to(ROOT)
                errors.append(
                    f"R14 未注册策略: {rel}:{node.lineno} 类 '{node.name}' "
                    f'继承 BaseStrategy/PolarsBaseStrategy 但未使用 @register_strategy("key") 装饰器'
                )
    return errors


# ============================================================================
# R15: 单例未注册检查（带 _instance/__new__ 的单例类需 @register_singleton）
# ============================================================================

# 非注册单例（CLAUDE.md §4.3 明确标注为非注册单例，不参与 R15 检查）
# ConfigHandler / ProxyManager 使用模块级状态模式，无 __new__，本就不会被识别，此处保留作为防御性显式豁免
_R15_EXEMPT_CLASSES = frozenset({"ConfigHandler", "ProxyManager"})


def _is_singleton_class(node: ast.ClassDef) -> bool:
    """判断类是否为单例模式：有 __new__ 方法 + (_instance 类属性 或 _reset_singleton 方法)。

    识别信号组合避免误报：
    - 仅 __new__ 不够（任何不可变类型都可能有 __new__）
    - 仅 _instance 不够（可能是普通类属性）
    - __new__ + _reset_singleton 是单例的强信号
    """
    has_new = any(isinstance(item, ast.FunctionDef) and item.name == "__new__" for item in node.body)
    has_instance_attr = any(
        isinstance(item, ast.Assign)
        and len(item.targets) == 1
        and isinstance(item.targets[0], ast.Name)
        and item.targets[0].id == "_instance"
        for item in node.body
    )
    has_reset = any(isinstance(item, ast.FunctionDef) and item.name == "_reset_singleton" for item in node.body)
    return has_new and (has_instance_attr or has_reset)


def check_R15() -> list[str]:
    """R15：扫描所有业务层 .py 文件中带 _instance/__new__ 的单例类是否使用 @register_singleton。"""
    errors: list[str] = []
    # 扫描业务层目录（不含 tests/、scripts/）
    scan_dirs = ("core", "data", "services", "strategies", "utils", "app")
    for dir_name in scan_dirs:
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for p in _iter_py_files(target_dir):
            tree = _parse_module(p)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_singleton_class(node):
                    continue
                if node.name in _R15_EXEMPT_CLASSES:
                    continue
                decorators = _decorator_names(node)
                if "register_singleton" not in decorators:
                    rel = p.relative_to(ROOT)
                    errors.append(
                        f"R15 未注册单例: {rel}:{node.lineno} 类 '{node.name}' "
                        f"使用 __new__+_instance 单例模式但未使用 @register_singleton 装饰器 "
                        f"(或未实现 _reset_singleton)"
                    )
    return errors


# ============================================================================
# R_no_bare_ft_colors_in_ui: UI 层裸 ft.Colors.<COLOR> 引用拦截
# ============================================================================

# 灰阶色 (warning 提示，不阻断)
_GRAYSCALE_COLORS = frozenset({"GREY", "WHITE", "BLACK", "TRANSPARENT"})

# Layer 1 语义 token (已合规，完全放行)
_LAYER1_SEMANTIC_TOKENS = frozenset(
    {
        "SURFACE",
        "ON_SURFACE",
        "ON_SURFACE_VARIANT",
        "SURFACE_CONTAINER_HIGHEST",
        "PRIMARY",
        "PRIMARY_CONTAINER",
        "ON_PRIMARY",
        "ON_PRIMARY_CONTAINER",
        "SECONDARY",
        "SECONDARY_CONTAINER",
        "ON_SECONDARY",
        "ON_SECONDARY_CONTAINER",
        "TERTIARY",
        "ERROR",
        "ERROR_CONTAINER",
        "ON_ERROR",
        "ON_ERROR_CONTAINER",
        "OUTLINE",
        "OUTLINE_VARIANT",
        "SHADOW",
        "SCRIM",
        "INVERSE_PRIMARY",
        "INVERSE_SURFACE",
        "ON_INVERSE_SURFACE",
        "BACKGROUND",
        "ON_BACKGROUND",
    }
)

# 裸色值拦截名单 (非零退出)
_BARE_COLOR_INTERCEPT = frozenset(
    {
        "RED",
        "RED_400",
        "GREEN",
        "BLUE",
        "YELLOW",
        "ORANGE",
        "PURPLE",
        "TEAL",
        "CYAN",
        "INDIGO",
    }
)

# settings_tabs/ 目录下 icon_color 装饰色豁免 (warning 不阻断)
# 仅装饰性色值: system_tab 的 BLUE/PURPLE/INDIGO/ORANGE/TEAL + data_source_tab 的 PURPLE
_SETTINGS_TABS_DECORATIVE = frozenset({"BLUE", "PURPLE", "INDIGO", "ORANGE", "TEAL"})


def _is_ft_colors_attr(node: ast.AST) -> str | None:
    """识别 ``ft.Colors.X`` 表达式，返回 X 名字；非此模式返回 None。"""
    if not isinstance(node, ast.Attribute):
        return None
    if not isinstance(node.value, ast.Attribute):
        return None
    inner = node.value
    if not isinstance(inner.value, ast.Name) or inner.value.id != "ft":
        return None
    if inner.attr != "Colors":
        return None
    return node.attr


def _is_settings_tabs_dir(source_path: Path) -> bool:
    """判断文件是否位于 ui/views/settings_tabs/ 目录下 (装饰色豁免范围)。"""
    try:
        rel = source_path.relative_to(ROOT)
    except ValueError:
        return False
    parts = rel.parts
    return len(parts) >= 3 and parts[0] == "ui" and parts[1] == "views" and parts[2] == "settings_tabs"


def _check_R_no_bare_ft_colors_in_tree(tree: ast.Module, source_path: Path) -> tuple[list[str], list[str]]:
    """纯函数：检查 AST 中的 ft.Colors.X 裸色引用。

    返回 (errors, warnings) 元组。
    - Layer 1 语义 token (SURFACE/ON_SURFACE/...) → 完全放行
    - 灰阶色 (GREY/WHITE/BLACK/TRANSPARENT) → warning
    - 裸色值 (RED/GREEN/BLUE/YELLOW/ORANGE/PURPLE/TEAL/CYAN/INDIGO) → error
    - settings_tabs/ 目录下 icon_color 装饰色 (BLUE/PURPLE/INDIGO/ORANGE/TEAL) → warning (豁免)
    """
    errors: list[str] = []
    warnings: list[str] = []
    rel = source_path.relative_to(ROOT)
    is_settings_tabs = _is_settings_tabs_dir(source_path)

    for node in ast.walk(tree):
        attr = _is_ft_colors_attr(node)
        if attr is None:
            continue
        # Layer 1 语义 token 完全放行
        if attr in _LAYER1_SEMANTIC_TOKENS:
            continue
        # 灰阶色 → warning
        if attr in _GRAYSCALE_COLORS:
            warnings.append(f"{rel}:{node.lineno}: 灰阶色 ft.Colors.{attr} 建议改用 AppColors token")
            continue
        # 裸色值拦截
        if attr in _BARE_COLOR_INTERCEPT:
            # settings_tabs/ 目录下 icon_color 装饰色场景豁免（仅 warning）
            if is_settings_tabs and attr in _SETTINGS_TABS_DECORATIVE:
                warnings.append(
                    f"{rel}:{node.lineno}: 装饰色 ft.Colors.{attr} 建议改用 AppColors token "
                    f"(settings_tabs icon_color 场景豁免)"
                )
                continue
            errors.append(
                f"R_no_bare_ft_colors_in_ui: {rel}:{node.lineno}: 裸色值 ft.Colors.{attr} "
                f"必须替换为 AppColors token (RED→ERROR/GREEN→SUCCESS/BLUE→INFO 等)"
            )
    return errors, warnings


def check_R_no_bare_ft_colors_in_ui() -> list[str]:
    """扫描 UI 层裸 ft.Colors.<COLOR> 色值引用。

    扫描范围: ui/views/, ui/components/, ui/startup_views.py (不扫 tests)
    退出码: 0 通过；返回非空 list 表示有 error (1 失败)。
    warnings 输出到 stderr (不阻断)。
    """
    errors: list[str] = []
    warnings: list[str] = []

    scan_paths: list[Path] = []
    # ui/views/ + ui/components/
    for sub in ("ui/views", "ui/components"):
        d = ROOT / sub
        if d.exists():
            for p in _iter_py_files(d):
                scan_paths.append(p)
    # ui/startup_views.py
    startup = ROOT / "ui" / "startup_views.py"
    if startup.exists():
        scan_paths.append(startup)

    for p in scan_paths:
        tree = _parse_module(p)
        if tree is None:
            continue
        errs, warns = _check_R_no_bare_ft_colors_in_tree(tree, p)
        errors.extend(errs)
        warnings.extend(warns)

    # 输出 warnings 到 stderr (不阻断)
    if warnings:
        print("[WARN] UI 灰阶/装饰色 ft.Colors 引用建议替换为 AppColors token：", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

    return errors


# ============================================================================
# CLI 入口
# ============================================================================


def main() -> int:
    """运行全部红线检查，返回退出码。"""
    checks: list[tuple[str, list[str]]] = [
        ("R4 SQL 注入", check_R4()),
        ("R12 数据表未注册", check_R12()),
        ("R13 DAO 未注册", check_R13()),
        ("R14 策略未注册", check_R14()),
        ("R15 单例未注册", check_R15()),
        ("R_no_bare_ft_colors_in_ui", check_R_no_bare_ft_colors_in_ui()),
    ]
    all_errors: list[str] = []
    for _, errs in checks:
        all_errors.extend(errs)

    if all_errors:
        print("[FAIL] 红线自动化检查失败：", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("[PASS] 红线自动化检查通过（R4/R12/R13/R14/R15 + R_no_bare_ft_colors_in_ui）")
    return 0


if __name__ == "__main__":
    # 兜底：Windows PYTHONIOENCODING=gbk 等非 UTF-8 环境下，emoji/中文输出会触发
    # UnicodeEncodeError。reconfigure stdout/stderr 为 UTF-8（errors="replace" 容错），
    # 避免主输出 emoji（已改为 ASCII [PASS]/[FAIL]）之外的非 ASCII 字符崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            typing.cast(TextIOWrapper, _stream).reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
