"""红线自动化检查脚本（R4/R12/R13/R14/R15/R16 + UI 裸 ft.Colors 拦截 + Tushare token 日志脱敏）。

依据 CLAUDE.md §3.1 红线表，对项目代码进行静态分析：
- R4  SQL 注入：扫描 data/services/strategies/app 与 tests/ 目录下 asyncpg 原生查询中的 %s 占位符
  （必须用 $1, $2, ...）；补充检测"SQL 开头字面量 + %s"（绕过 1，ERROR + # noqa: R4）与 f-string
  SQL 模板（绕过 2，WARNING）
- R12 数据表未注册：对比 models.py 的 __tablename__ 与 data_dictionary.py 的 TABLE_DEFINITIONS
- R13 DAO 未注册：对比 daos/ 下的 DAO 类与 CacheManager.__init__ 实例化清单
- R14 策略未注册：扫描继承 BaseStrategy/PolarsBaseStrategy 的类是否使用 @register_strategy
- R15 单例未注册：扫描带 _instance/__new__ 的单例类是否使用 @register_singleton
- R16 UI 阻塞主循环（部分守护）：扫描 ViewModel __init__ 中构造已注册单例（B11 类重型初始化风险）；
  事件处理器内同步 IO 仍为人工评审
- R_no_bare_ft_colors_in_ui: 扫描 UI 层裸 ft.Colors.<COLOR> 引用 (必须替换为 AppColors token)
- R_tushare_token_log: 扫描 tushare_client.py 中 logger 调用是否直接打印 self.token / token 明文 (R9 红线)
- R_lazy_import_whitelist: 扫描函数体内禁止方向的跨层 import 是否带 # lazy-import: <原因> 注释（review01-A2-2）

退出码：0 通过，1 失败。供 pre-commit `redline-check` hook 与 pytest 契约测试调用。

R16 说明（review07-G20）：最小可行切面为"ViewModel __init__ 同步构造已注册单例"。单例首次构造可能执行
重型初始化（B11：DataProcessor 阻塞 34s），VM 构造路径位于 Flet 渲染/事件线程，有阻塞主循环风险。
rule_type=NEW_CODE（docs/governance/redlines.yml）：存量 8 处持有引用已显式 # noqa: R16 豁免，
新增构造默认 ERROR，须显式声明原因或改造为惰性/命令内注入。
"""

from __future__ import annotations

import ast
import re
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
    """R4：扫描 data/ services/ strategies/ app/ 目录下所有 .py 文件中的 asyncpg 原生查询 %s 占位符。

    review07-G18：扫描范围从仅 data/ 扩展至业务层全部目录（原绕过路径 3）。
    """
    errors: list[str] = []
    for dir_name in ("data", "services", "strategies", "app"):
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for p in _iter_py_files(target_dir):
            tree = _parse_module(p)
            if tree is None:
                continue
            errors.extend(_check_R4_in_tree(tree, p))
    return errors


# review07-G18: 补充检测 —— "以 SQL 关键字开头的字符串字面量 + %s"（绕过路径 1：SQL 存入变量）
_SQL_KEYWORD_LEAD_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_R4_NOQA_MARKER = "# noqa: R4"


def _line_has_noqa_marker(path: Path, lineno: int, marker: str) -> bool:
    """检查指定行是否带指定 # noqa 豁免标记。"""
    try:
        line = path.read_text(encoding="utf-8").splitlines()[lineno - 1]
    except (OSError, IndexError, UnicodeDecodeError):
        return False
    return marker in line


def _R4_direct_query_constant_positions(tree: ast.Module) -> set[tuple[int, int]]:
    """收集被 conn.<query>(<SQL 常量>) 直接传参规则命中的常量位置 (lineno, col_offset)，用于去重。"""
    positions: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _ASYNCPG_QUERY_METHODS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and "%s" in first.value:
            positions.add((first.lineno, first.col_offset))
    return positions


def _check_R4_literal_assignments_in_tree(tree: ast.Module, source_path: Path) -> list[str]:
    """纯函数：检查"以 SQL 关键字开头且含 %s"的字符串字面量（绕过路径 1：SQL 存入变量后执行）。

    排除已由 _check_R4_in_tree 直接传参规则命中的常量（去重）；行尾 ``# noqa: R4`` 豁免。
    """
    errors: list[str] = []
    try:
        rel = source_path.relative_to(ROOT)
    except ValueError:
        # 契约测试用临时文件构造 AST（不在 ROOT 下），fallback 到绝对路径显示
        rel = source_path
    positions = _R4_direct_query_constant_positions(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if (node.lineno, node.col_offset) in positions:
            continue
        if "%s" not in node.value or not _SQL_KEYWORD_LEAD_RE.search(node.value):
            continue
        if _line_has_noqa_marker(source_path, node.lineno, _R4_NOQA_MARKER):
            continue
        errors.append(
            f"{rel}:{node.lineno}: R4 潜在 SQL 注入 — 字符串字面量以 SQL 关键字开头且含 %s 占位符"
            f" (asyncpg 原生查询必须用 $1, $2, ...；若为合法日志/文案请加 {_R4_NOQA_MARKER} 豁免): {node.value[:80]!r}"
        )
    return errors


def check_R4_literal_assignments() -> list[str]:
    """R4 补充（review07-G18）：扫描业务层目录中"SQL 开头 + %s"的字符串字面量。"""
    errors: list[str] = []
    for dir_name in ("data", "services", "strategies", "app", "core", "utils", "ui"):
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for p in _iter_py_files(target_dir):
            tree = _parse_module(p)
            if tree is None:
                continue
            errors.extend(_check_R4_literal_assignments_in_tree(tree, p))
    return errors


def _check_R4_fstring_in_tree(tree: ast.Module, source_path: Path) -> list[str]:
    """纯函数：检查"以 SQL 关键字开头"的 f-string 模板（绕过路径 2：f-string 拼接）。

    WARNING 语义：DAO 层存在合法 SQL 模板 f-string（报告 03 C7），故不阻断，
    仅提示人工确认拼接值已参数化。
    """
    warnings: list[str] = []
    rel = source_path.relative_to(ROOT)
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        consts = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        joined = " ".join(consts)
        if _SQL_KEYWORD_LEAD_RE.search(joined):
            warnings.append(
                f"{rel}:{node.lineno}: R4 f-string 以 SQL 关键字开头 — 合法 SQL 模板可忽略；"
                f"若拼接外部输入须参数化 (asyncpg $1, $2, ...): {joined[:80]!r}"
            )
    return warnings


def check_R4_fstring_sql() -> None:
    """R4 补充（review07-G18）：f-string SQL 模板检测，WARNING 输出到 stderr（不阻断）。"""
    warnings: list[str] = []
    for dir_name in ("data", "services", "strategies", "app", "core", "utils", "ui"):
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for p in _iter_py_files(target_dir):
            tree = _parse_module(p)
            if tree is None:
                continue
            warnings.extend(_check_R4_fstring_in_tree(tree, p))
    if warnings:
        print("[WARN] R4 f-string SQL 模板（合法用法可忽略，拼接外部输入须参数化）：", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)


# tests/ 目录扫描时跳过缓存与构建产物，但保留 tests 自身（复用 _SKIP_DIRS，移除 "tests" 以允许扫描）
_TESTS_SCAN_SKIP_DIRS = _SKIP_DIRS - {"tests"}


def check_R4_in_tests() -> list[str]:
    """R4（tests/）：扫描 tests/ 目录下所有 .py 文件中的 asyncpg 原生查询 %s 占位符。

    P3-CheckRedlines-Tests-Dir: 扩展 R4 检查至 tests/ 目录。仅启用 R4（SQL 注入）检查，
    R12/R13/R14/R15 不适用于测试代码（测试中可自由定义 mock 类/单例/策略子类用于验证
    装饰器逻辑，不应被红线检查拦截）。tests/ 目录默认在 _SKIP_DIRS 中被其他检查跳过，
    本函数显式扫描 tests/ 目录。

    反例识别：``@pytest.fixture`` 函数内的 ``"%s" % var`` 字符串格式化不被误报，因为
    R4 检查仅匹配 ``conn.<method>("...%s...")`` 形式的 asyncpg 原生调用（第一个参数是
    字符串字面量且包含 %s），``"%s" % var`` 是 BinOp 表达式，第一个参数不是 Constant，
    天然不匹配。测试文件中构造 R4 测试用例的字符串字面量（如 ``code = 'await conn.execute("...%s...")'``）
    也不会被误报，因为 AST 不会进入字符串字面量内部解析。
    """
    errors: list[str] = []
    tests_dir = ROOT / "tests"
    if not tests_dir.exists():
        return errors
    for p in _iter_py_files(tests_dir, exclude_dirs=_TESTS_SCAN_SKIP_DIRS):
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

    仅检查 __init__ 实例化维度；_create_engine 的 .engine 引用更新由 _DAO_REGISTRY
    驱动循环同步（cache_manager.py），结构上不可漏改（review07-G19 与宪法 R13 描述一致）。
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
    """判断类是否为单例模式（review07-G19 扩展识别条件）。

    原规则：__new__ + (_instance 类属性 或 _reset_singleton)。
    G19 扩展：_instance 类属性 + (__new__ | _reset_singleton | 公开访问器) 任一组合。
    公开访问器 = 不以 _ 开头的方法 def 且函数体引用 _instance（近似"公开获取方法"）。

    组合判定避免误报：
    - 仅 __new__ 不够（任何不可变类型都可能有 __new__）
    - 仅 _instance 不够（可能是普通类属性，需配合 __new__/_reset/公开访问器）
    - 保留原规则命中面（__new__ + _reset_singleton，无显式 _instance 类属性）不丢失
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
    has_public_accessor = any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not item.name.startswith("_")
        and any(isinstance(sub, ast.Attribute) and sub.attr == "_instance" for sub in ast.walk(item))
        for item in node.body
    )
    # 原规则命中面（__new__ 单例，_instance 动态创建于 __new__）
    legacy_hit = has_new and (has_instance_attr or has_reset)
    # G19 扩展命中面：_instance 类属性 + 任一辅助信号（模块级事实单例 / 纯 get_instance 模式）
    expanded_hit = has_instance_attr and (has_new or has_reset or has_public_accessor)
    return legacy_hit or expanded_hit


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
                # G19 豁免：# not-a-singleton: <原因>（模块级常量持有者 / 普通 _instance 类属性误报对象）
                if _line_has_noqa_marker(p, node.lineno, "# not-a-singleton:") or _line_has_noqa_marker(
                    p, node.lineno - 1, "# not-a-singleton:"
                ):
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


def _is_hex_color_constant(node: ast.AST) -> bool:
    """识别 ``'#RRGGBB'`` / ``'#RRGGBBAA'`` 字符串字面量 (hex color fallback 模式)。"""
    if not isinstance(node, ast.Constant):
        return False
    if not isinstance(node.value, str):
        return False
    val = node.value
    if not val.startswith("#"):
        return False
    return len(val) in (7, 9) and all(c in "0123456789abcdefABCDEF" for c in val[1:])


def _detect_hasattr_hex_fallback(node: ast.IfExp) -> str | None:
    """识别 ``X if hasattr(...) else '#hex'`` 模式, 返回 hex 字符串; 非此模式返回 None。

    OBS-6 (review fix): 堵塞 MAJ-1 类 hasattr + hex fallback 逃过 CI 的漏洞。
    检测: orelse 为 hex color 字符串字面量, test 为 hasattr() 调用。
    """
    if not _is_hex_color_constant(node.orelse):
        return None
    test = node.test
    if not isinstance(test, ast.Call):
        return None
    if not isinstance(test.func, ast.Name) or test.func.id != "hasattr":
        return None
    return node.orelse.value  # type: ignore[return-value]


def _check_R_no_bare_ft_colors_in_tree(tree: ast.Module, source_path: Path) -> tuple[list[str], list[str]]:
    """纯函数：检查 AST 中的 ft.Colors.X 裸色引用。

    返回 (errors, warnings) 元组。
    - Layer 1 语义 token (SURFACE/ON_SURFACE/...) → 完全放行
    - 灰阶色 (GREY/WHITE/BLACK/TRANSPARENT) → warning
    - 裸色值 (RED/GREEN/BLUE/YELLOW/ORANGE/PURPLE/TEAL/CYAN/INDIGO) → error
    - settings_tabs/ 目录下 icon_color 装饰色 (BLUE/PURPLE/INDIGO/ORANGE/TEAL) → warning (豁免)
    - OBS-6: ``X if hasattr(...) else '#hex'`` 模式 → warning (hasattr + hex fallback)
    """
    errors: list[str] = []
    warnings: list[str] = []
    rel = source_path.relative_to(ROOT)
    is_settings_tabs = _is_settings_tabs_dir(source_path)

    for node in ast.walk(tree):
        # OBS-6: 先检测 ast.IfExp hasattr+hex fallback 模式 (不与 ft.Colors.X 检测冲突)
        if isinstance(node, ast.IfExp):
            hex_val = _detect_hasattr_hex_fallback(node)
            if hex_val is not None:
                warnings.append(
                    f"{rel}:{node.lineno}: hasattr + hex fallback ({hex_val}) 模式建议直接使用 AppColors token"
                )
            continue
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


def _iter_ui_scan_files() -> list[Path]:
    """枚举 UI 生产代码待扫描文件（ui/views/ + ui/components/ + ui/startup_views.py）。

    供 R_no_bare_ft_colors_in_ui 与 R_no_bare_font_size_in_ui 复用，保证扫描域一致。
    theme.py 是 token 定义源头，不在 ui/views|ui/components|startup_views 范围内，天然排除。
    """
    scan_paths: list[Path] = []
    for sub in ("ui/views", "ui/components"):
        d = ROOT / sub
        if d.exists():
            scan_paths.extend(_iter_py_files(d))
    startup = ROOT / "ui" / "startup_views.py"
    if startup.exists():
        scan_paths.append(startup)
    return scan_paths


def check_R_no_bare_ft_colors_in_ui() -> list[str]:
    """扫描 UI 层裸 ft.Colors.<COLOR> 色值引用。

    扫描范围: ui/views/, ui/components/, ui/startup_views.py (不扫 tests)
    退出码: 0 通过；返回非空 list 表示有 error (1 失败)。
    warnings 输出到 stderr (不阻断)。
    """
    errors: list[str] = []
    warnings: list[str] = []

    scan_paths = _iter_ui_scan_files()

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
# R_tushare_token_log: Tushare token 日志脱敏检查 (R9 红线专属守护)
# ============================================================================

# logger 调用方法名（覆盖 logging 模块标准 level + Logger.exception）
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "critical", "exception", "log"})

# 已脱敏调用名（self.token 在这些 Call 子树中视为已脱敏，放行）
# 覆盖：DataSanitizer.sanitize_token / sanitize_error / sanitize + hashlib.sha256 / hexdigest
_SANITIZED_CALL_NAMES = frozenset({"sanitize_token", "sanitize_error", "sanitize", "sha256", "hexdigest"})


def _is_self_token_ref(node: ast.AST) -> bool:
    """识别 ``self.token`` 表达式 (ast.Attribute with value=Name('self'), attr='token')。"""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == "token"
    )


def _collect_self_token_ids(node: ast.AST) -> set[int]:
    """收集 AST 子树中所有 self.token 引用的 id() 集合。"""
    return {id(sub) for sub in ast.walk(node) if _is_self_token_ref(sub)}


def _collect_sanitized_self_token_ids(node: ast.AST) -> set[int]:
    """收集 AST 子树中所有位于 sanitize_*/sha256/hexdigest 调用子树内的 self.token 引用 id()。

    这些引用视为已脱敏，从违规集合中排除。
    """
    sanitized: set[int] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        func_name: str | None = None
        if isinstance(func, ast.Attribute):
            func_name = func.attr
        elif isinstance(func, ast.Name):
            func_name = func.id
        if func_name is None or func_name not in _SANITIZED_CALL_NAMES:
            continue
        sanitized.update(_collect_self_token_ids(sub))
    return sanitized


def _contains_unsanitized_self_token(node: ast.AST) -> bool:
    """检测 AST 节点中是否存在未脱敏的 self.token 引用。

    判定：所有 self.token 引用 id 减去 sanitize_*/sha256/hexdigest 调用子树内的引用 id，
    剩余非空即存在未脱敏引用。
    """
    all_refs = _collect_self_token_ids(node)
    if not all_refs:
        return False
    sanitized_refs = _collect_sanitized_self_token_ids(node)
    return bool(all_refs - sanitized_refs)


def _check_R_tushare_token_log_in_tree(tree: ast.Module, source_path: Path) -> list[str]:
    """纯函数：检查 AST 中 logger 调用是否直接打印 self.token 明文。

    覆盖以下形式：
    - 直接引用：``logger.info("...", self.token)``
    - f-string 内嵌：``logger.info(f"...{self.token}...")``
    - format 参数：``logger.info("...".format(self.token))``
    - % 格式化：``logger.info("...%s..." % self.token)``
    - 字典/列表包装：``logger.info("...", {"token": self.token})``

    放行以下合规形式（已脱敏）：
    - ``DataSanitizer.sanitize_token(self.token or "")``
    - ``hashlib.sha256(self.token.encode()).hexdigest()[:16]``
    """
    errors: list[str] = []
    rel = source_path.relative_to(ROOT)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOGGER_METHODS:
            continue
        # 检查位置参数
        for arg in node.args:
            if _contains_unsanitized_self_token(arg):
                errors.append(
                    f"{rel}:{node.lineno}: R_tushare_token_log — logger.{node.func.attr}(...) "
                    f"直接传入 self.token 明文，必须经 DataSanitizer.sanitize_token() 脱敏 (R9 红线)"
                )
        # 检查关键字参数
        for kw in node.keywords:
            if _contains_unsanitized_self_token(kw.value):
                errors.append(
                    f"{rel}:{node.lineno}: R_tushare_token_log — logger.{node.func.attr}(...) "
                    f"关键字参数 '{kw.arg}' 直接传入 self.token 明文，必须经 DataSanitizer.sanitize_token() 脱敏 (R9 红线)"
                )
    return errors


def check_R_tushare_token_log() -> list[str]:
    """扫描 tushare_client.py 中 logger 调用是否直接打印 self.token 明文 (R9 红线守护)。

    扫描范围：data/external/tushare_client.py（token 仅在此处出现，避免误报）
    退出码：0 通过；返回非空 list 表示有 error (1 失败)。

    设计取舍：
    - 仅检测 ``self.token`` 直接引用（含 f-string/format/%/dict 等包装），不检测局部变量 token
      （局部变量可能来自其他来源，避免误报）。
    - 不检测字符串字面量中的 'token' 关键字（如 "Token not set" 提示信息）。
    - ``DataSanitizer.sanitize_token(self.token or "")`` 视为合规（已脱敏）。
    - ``hashlib.sha256(self.token.encode()).hexdigest()[:16]`` 视为合规（已 hash）。
    """
    errors: list[str] = []
    target = ROOT / "data" / "external" / "tushare_client.py"
    if not target.exists():
        return errors
    tree = _parse_module(target)
    if tree is None:
        return errors
    errors.extend(_check_R_tushare_token_log_in_tree(tree, target))
    return errors


# ============================================================================
# R_no_bare_font_size_in_ui: UI 层裸字号数值拦截（必须用 AppStyles.FONT_SIZE_* token）
# ============================================================================


def _is_ft_control_call(node: ast.AST, control_name: str) -> str | None:
    """识别 ``ft.Text(...)`` / ``ft.TextStyle(...)`` 调用（value 为 Name 'ft'）。

    命中时返回控件名（即 ``func.attr``，如 ``"Text"``），否则返回 None。
    返回字符串而非 bool，便于调用方直接使用控件名，避免对 ``node.func.attr`` 的属性访问。
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr != control_name:
        return None
    if isinstance(func.value, ast.Name) and func.value.id == "ft":
        return func.attr
    return None


def _check_R_no_bare_font_size_in_tree(tree: ast.Module, source_path: Path) -> list[str]:
    """纯函数：检查 AST 中 ft.Text/ft.TextStyle 的 size= 字面量数值。

    规则：`ft.Text(size=13)` / `ft.TextStyle(size=20)` 等 size 为 int 字面量 → 违规，
    必须改为 `size=AppStyles.FONT_SIZE_*` token。
    放行：size 为变量名、AppStyles.FONT_SIZE_* 属性引用、或非 int 字面量表达式。
    """
    errors: list[str] = []
    rel = source_path.relative_to(ROOT)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        control_name = _is_ft_control_call(node, "Text") or _is_ft_control_call(node, "TextStyle")
        if control_name is None:
            continue
        for kw in node.keywords:
            if kw.arg != "size":
                continue
            value = kw.value
            # 仅拦截 int 字面量（13/20/24 等），排除 bool（bool 是 int 子类但 size=True 无意义）；
            # 变量/属性/表达式/float 放行
            if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
                errors.append(
                    f"R_no_bare_font_size_in_ui: {rel}:{node.lineno}: "
                    f"ft.{control_name}(size={value.value}) 硬编码字号数值，"
                    f"必须引用 AppStyles.FONT_SIZE_CAPTION/BODY_SM/BODY/LG/TITLE/HEADLINE/XL/DISPLAY token"
                )
    return errors


def check_R_no_bare_font_size_in_ui() -> list[str]:
    """扫描 UI 层裸字号数值引用（必须用 AppStyles.FONT_SIZE_* token）。

    扫描范围: ui/views/, ui/components/, ui/startup_views.py（与 R_no_bare_ft_colors_in_ui 一致）
    退出码: 0 通过；返回非空 list 表示有 error (1 失败)。
    """
    errors: list[str] = []
    for p in _iter_ui_scan_files():
        tree = _parse_module(p)
        if tree is None:
            continue
        errors.extend(_check_R_no_bare_font_size_in_tree(tree, p))
    return errors


# ============================================================================
# review01-A2-2: 函数体内跨层 lazy import 白名单
# ============================================================================

# 禁止方向与 tests/unit/test_architecture_boundaries.py FORBIDDEN_IMPORTS 保持一致
_LAZY_IMPORT_FORBIDDEN_DIRECTIONS: dict[str, frozenset[str]] = {
    "core": frozenset({"data", "services", "strategies", "ui", "app", "utils"}),
    "data": frozenset({"services", "strategies", "ui", "app"}),
    "services": frozenset({"strategies", "ui", "app"}),
    "strategies": frozenset({"ui", "app"}),
    "ui": frozenset({"app"}),
    "utils": frozenset({"data", "services", "strategies", "ui", "app"}),
}

_LAZY_IMPORT_MARKER = "# lazy-import:"
_LAZY_IMPORT_SCAN_LAYERS = ("core", "data", "services", "strategies", "ui", "app", "utils")


def _node_has_lazy_import_marker(path: Path, lineno: int, end_lineno: int) -> bool:
    """检查 import 语句行范围 [lineno, end_lineno] 内是否含 ``# lazy-import:`` 标记。

    ruff format 会把含行尾注释的 import 多行化（注释落在末行），故按行范围扫描。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return True  # 读取失败不阻断检查
    return any(_LAZY_IMPORT_MARKER in lines[i] for i in range(lineno - 1, min(end_lineno, len(lines))))


class _LazyImportWhitelistVisitor(ast.NodeVisitor):
    """AST 遍历：收集函数体内禁止方向的跨层 import 缺 lazy-import 标记的错误。"""

    def __init__(self, layer: str, forbidden: frozenset[str], py_file: Path, errors: list[str]) -> None:
        self._layer = layer
        self._forbidden = forbidden
        self._py_file = py_file
        self._errors = errors
        self._in_function = False
        self._in_type_checking = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._in_function = True
        self.generic_visit(node)
        self._in_function = False

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._in_function = True
        self.generic_visit(node)
        self._in_function = False

    def visit_If(self, node: ast.If) -> None:
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            # TYPE_CHECKING 块仅类型检查，非运行时依赖，豁免
            self._in_type_checking = True
            self.generic_visit(node)
            self._in_type_checking = False
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._check(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check(node)

    def _check(self, node: ast.AST) -> None:
        if not self._in_function or self._in_type_checking:
            return
        if isinstance(node, ast.Import):
            targets = [alias.name.split(".")[0] for alias in node.names]
        else:
            # ImportFrom（node.module 可为 None，如 from . import x）
            node_from = node.module or ""
            targets = [node_from.split(".")[0]] if node.level == 0 and node_from else []
        hit = [t for t in targets if t in self._forbidden]
        if not hit:
            return
        end_lineno = getattr(node, "end_lineno", node.lineno)
        if _node_has_lazy_import_marker(self._py_file, node.lineno, end_lineno):
            return
        rel = self._py_file.relative_to(ROOT).as_posix()
        self._errors.append(
            f"[lazy-import] {rel}:{node.lineno} 函数体内跨层 import '{', '.join(hit)}' "
            f"缺 '# lazy-import: <原因>' 注释（review01-A2-2）"
        )


def check_R_lazy_import_whitelist() -> list[str]:
    """review01-A2-2: 禁止方向的函数体内跨层 import 必须带 ``# lazy-import:`` 注释。

    规则：函数体（含嵌套闭包）内的跨层 import，若 源层→目标层 属于禁止方向
    （与 test_architecture_boundaries.py FORBIDDEN_IMPORTS 一致），必须带行尾
    ``# lazy-import: <原因>`` 注释（显式白名单登记），否则报错。
    ``if TYPE_CHECKING:`` 块内导入豁免（仅类型检查，非运行时依赖）。

    补充：import-linter 契约 5/6 亦分析函数体内 import（ignore_imports 白名单），
    二者双保险——本检查确保代码层面显式标注，契约确保新增违规被拦截。
    """
    errors: list[str] = []
    for layer in _LAZY_IMPORT_SCAN_LAYERS:
        layer_dir = ROOT / layer
        if not layer_dir.is_dir():
            continue
        forbidden = _LAZY_IMPORT_FORBIDDEN_DIRECTIONS.get(layer, frozenset())
        if not forbidden:
            continue
        for py_file in _iter_py_files(layer_dir):
            tree = _parse_module(py_file)
            if tree is None:
                continue
            _LazyImportWhitelistVisitor(layer, forbidden, py_file, errors).visit(tree)
    return errors


# ============================================================================
# R16: UI 阻塞主循环（部分守护：ViewModel __init__ 构造已注册单例检测）
# ============================================================================

# 已注册单例白名单（供 VM __init__ 构造单例检测；与 docs/architecture/singleton-lifecycle.md
# 注册清单保持一致，但该白名单无自动化比对守护，新增注册单例须人工同步补充）
_R16_SINGLETON_CLASSES = frozenset(
    {
        "CacheManager",
        "ThreadPoolManager",
        "TaskManager",
        "AIService",
        "SchedulerService",
        "DataProcessor",
        "MarketDataService",
        "NewsSubscriptionService",
        "TushareClient",
        "AkshareConceptClient",
        "LocalModelManager",
        "StrategyManager",
        "MetaDataManager",
        "EmbeddedPostgresService",
        "EmbeddedPgMaintenanceService",
        "AppColors",
    }
)

# 检测目录：ViewModel 与 UI 组件工厂域（B11 类同步重型初始化风险路径）
_R16_SCAN_DIRS = ("ui/viewmodels", "ui/components")

_R16_NOQA_MARKER = "# noqa: R16"


def _noqa_r16_on_line(path: Path, lineno: int) -> bool:
    """检查指定行是否带 # noqa: R16 豁免标记（存量持有引用显式豁免）。"""
    try:
        line = path.read_text(encoding="utf-8").splitlines()[lineno - 1]
    except (OSError, IndexError, UnicodeDecodeError):
        return False
    return _R16_NOQA_MARKER in line


def _check_R16_in_tree(tree: ast.Module, source_path: Path) -> list[str]:
    """纯函数：检查 AST 中 ViewModel __init__ 内构造已注册单例的调用。"""
    errors: list[str] = []
    try:
        rel = source_path.relative_to(ROOT)
    except ValueError:
        # 契约测试用临时文件构造 AST（不在 ROOT 下），fallback 到绝对路径显示
        rel = source_path

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or item.name != "__init__":
                continue
            for call in ast.walk(item):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                else:
                    continue
                if name not in _R16_SINGLETON_CLASSES:
                    continue
                if _noqa_r16_on_line(source_path, call.lineno):
                    continue
                errors.append(
                    f"R16 UI 阻塞主循环: {rel}:{call.lineno} ViewModel {node.name}.__init__ "
                    f"同步构造已注册单例 {name}()（首次构造可能触发阻塞主循环的重型初始化；"
                    f"应改为惰性获取/命令内依赖注入，存量持有引用须显式 {_R16_NOQA_MARKER} 声明原因）"
                )
    return errors


def check_R16_vm_init_singleton_construction() -> list[str]:
    """R16（部分）：扫描 ui/viewmodels/、ui/components/ 下 ViewModel __init__ 构造已注册单例。

    review07-G20 最小可行切面：捕获 B11 类问题（VM 同步构造重单例阻塞事件循环）。
    事件处理器内同步 IO 等其他 R16 场景仍为人工评审（诚实降级范围）。
    """
    errors: list[str] = []
    for sub in _R16_SCAN_DIRS:
        target_dir = ROOT / sub
        if not target_dir.exists():
            continue
        for p in _iter_py_files(target_dir):
            tree = _parse_module(p)
            if tree is None:
                continue
            errors.extend(_check_R16_in_tree(tree, p))
    return errors


# ============================================================================
# CLI 入口
# ============================================================================


def main() -> int:
    """运行全部红线检查，返回退出码。"""
    checks: list[tuple[str, list[str]]] = [
        ("R4 SQL 注入", check_R4()),
        ("R4 潜在 SQL 字面量", check_R4_literal_assignments()),
        ("R4 SQL 注入 (tests)", check_R4_in_tests()),
        ("R12 数据表未注册", check_R12()),
        ("R13 DAO 未注册", check_R13()),
        ("R14 策略未注册", check_R14()),
        ("R15 单例未注册", check_R15()),
        ("R16 UI 阻塞主循环 (VM 构造单例)", check_R16_vm_init_singleton_construction()),
        ("R_no_bare_ft_colors_in_ui", check_R_no_bare_ft_colors_in_ui()),
        ("R_no_bare_font_size_in_ui", check_R_no_bare_font_size_in_ui()),
        ("R_tushare_token_log", check_R_tushare_token_log()),
        ("R_lazy_import_whitelist", check_R_lazy_import_whitelist()),
    ]
    # R4 f-string SQL 模板为 WARNING（不阻断），输出到 stderr
    check_R4_fstring_sql()
    all_errors: list[str] = []
    for _, errs in checks:
        all_errors.extend(errs)

    if all_errors:
        print("[FAIL] 红线自动化检查失败：", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "[PASS] 红线自动化检查通过（R4/R12/R13/R14/R15/R16 + R_no_bare_ft_colors_in_ui + R_no_bare_font_size_in_ui + R_tushare_token_log + R_lazy_import_whitelist）"
    )
    return 0


if __name__ == "__main__":
    # 兜底：Windows PYTHONIOENCODING=gbk 等非 UTF-8 环境下，emoji/中文输出会触发
    # UnicodeEncodeError。reconfigure stdout/stderr 为 UTF-8（errors="replace" 容错），
    # 避免主输出 emoji（已改为 ASCII [PASS]/[FAIL]）之外的非 ASCII 字符崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            typing.cast(TextIOWrapper, _stream).reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
