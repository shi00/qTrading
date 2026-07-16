"""Tests for scripts/check_redlines.py redline automation checks.

验证 R4/R12/R13/R14/R15 五项红线检查的纯函数逻辑与集成正确性：
- 纯函数测试：构造 AST/临时文件验证检测逻辑（误报与漏报边界）
- 集成测试：验证当前代码库通过所有检查（契约测试）
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_redlines import (  # noqa: E402 - sys.path 注入后导入
    _base_class_names,
    _check_R4_in_tree,
    _decorator_names,
    _extract_cache_manager_dao_instances,
    _extract_dao_classes,
    _extract_table_definition_keys,
    _extract_tablenames_from_models,
    _is_singleton_class,
    _is_strategy_subclass,
    check_R12,
    check_R13,
    check_R14,
    check_R15,
    check_R4,
    main,
)


def _first_class_def(code: str) -> ast.ClassDef:
    """从代码中提取第一个 ClassDef 节点。"""
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node
    raise AssertionError("No ClassDef found in code")


# ============================================================================
# R4 纯函数测试
# ============================================================================


class TestR4PureFunction:
    """R4 纯函数测试：直接调用 _check_R4_in_tree 验证 %s 占位符检测。"""

    def _check(self, code: str) -> list[str]:
        tree = ast.parse(code)
        fake_path = ROOT / "data" / "fake_module.py"
        return _check_R4_in_tree(tree, fake_path)

    def test_detects_percent_s_in_execute(self):
        """conn.execute 字符串中含 %s 应被检测。"""
        code = 'async def f():\n    await conn.execute("SELECT * FROM users WHERE id = %s", user_id)\n'
        errors = self._check(code)
        assert len(errors) == 1
        assert "R4" in errors[0]
        assert "%s" in errors[0]

    def test_detects_percent_s_in_fetchval(self):
        """conn.fetchval 字符串中含 %s 应被检测。"""
        code = 'async def f():\n    await conn.fetchval("SELECT version() WHERE x = %s", x)\n'
        errors = self._check(code)
        assert len(errors) == 1

    def test_detects_percent_s_in_fetchrow(self):
        """conn.fetchrow 字符串中含 %s 应被检测。"""
        code = 'async def f():\n    await conn.fetchrow("SELECT * FROM t WHERE id = %s", x)\n'
        errors = self._check(code)
        assert len(errors) == 1

    def test_dollar_placeholder_not_flagged(self):
        """$1 占位符（asyncpg 正确用法）不应被检测。"""
        code = 'async def f():\n    await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)\n'
        errors = self._check(code)
        assert errors == []

    def test_sqlalchemy_text_not_flagged(self):
        """SQLAlchemy conn.execute(sa.text(...)) 不应被检测（参数是 Call 不是 Constant）。"""
        code = 'async def f():\n    await conn.execute(sa.text("SELECT * FROM users WHERE id = :id"))\n'
        errors = self._check(code)
        assert errors == []

    def test_sqlalchemy_stmt_not_flagged(self):
        """SQLAlchemy conn.execute(stmt) 不应被检测（参数是 Name 不是 Constant）。"""
        code = "async def f():\n    stmt = sa.select(User)\n    await conn.execute(stmt)\n"
        errors = self._check(code)
        assert errors == []

    def test_fstring_not_flagged(self):
        """f-string 字符串不应被检测（ast.JoinedStr 不是 ast.Constant）。

        R4 仅针对字符串字面量中的 %s 占位符；f-string 的 SQL 注入是另一个问题
        （应通过白名单校验，而非参数化）。
        """
        code = 'async def f():\n    await conn.execute(f"SELECT * FROM {table}")\n'
        errors = self._check(code)
        assert errors == []

    def test_non_string_constant_not_flagged(self):
        """非字符串常量参数不应被检测。"""
        code = "async def f():\n    await conn.execute(123)\n"
        errors = self._check(code)
        assert errors == []

    def test_no_percent_s_not_flagged(self):
        """字符串中无 %s 不应被检测。"""
        code = 'async def f():\n    await conn.execute("SELECT 1")\n'
        errors = self._check(code)
        assert errors == []

    def test_multiple_percent_s_in_one_call_count_once(self):
        """单个调用中多个 %s 只报一个错误（一个调用一个错误）。"""
        code = 'async def f():\n    await conn.execute("SELECT * FROM users WHERE id = %s AND name = %s", id, name)\n'
        errors = self._check(code)
        assert len(errors) == 1

    def test_non_query_method_not_flagged(self):
        """非查询方法（如 conn.close()）不应被检测。"""
        code = "async def f():\n    await conn.close()\n"
        errors = self._check(code)
        assert errors == []


# ============================================================================
# R12 纯函数测试
# ============================================================================


class TestR12PureFunction:
    """R12 纯函数测试：验证 __tablename__ 与 TABLE_DEFINITIONS key 提取逻辑。"""

    def test_extract_tablenames(self, tmp_path):
        """从 models.py 提取 __tablename__ 字符串值。"""
        models_py = tmp_path / "models.py"
        models_py.write_text(
            'class Stock:\n    __tablename__ = "stock_basic"\nclass DailyQuotes:\n    __tablename__ = "daily_quotes"\n',
            encoding="utf-8",
        )
        names = _extract_tablenames_from_models(models_py)
        assert names == {"stock_basic", "daily_quotes"}

    def test_extract_table_definition_keys(self, tmp_path):
        """从 data_dictionary.py 提取 TABLE_DEFINITIONS 字典 key。"""
        dict_py = tmp_path / "data_dictionary.py"
        dict_py.write_text(
            "TABLE_DEFINITIONS = {\n"
            '    "stock_basic": {"alias": "tab_stock_basic"},\n'
            '    "daily_quotes": {"alias": "tab_daily_quotes"},\n'
            "}\n",
            encoding="utf-8",
        )
        keys = _extract_table_definition_keys(dict_py)
        assert keys == {"stock_basic", "daily_quotes"}

    def test_extract_tablenames_ignores_non_string(self, tmp_path):
        """非字符串 __tablename__ 赋值应被忽略。"""
        models_py = tmp_path / "models.py"
        models_py.write_text("class Foo:\n    __tablename__ = 123\n", encoding="utf-8")
        names = _extract_tablenames_from_models(models_py)
        assert names == set()

    def test_extract_tablenames_ignores_non_assign(self, tmp_path):
        """__tablename__ 作为类属性注解（AnnAssign）应被忽略（项目用 Assign 不用 AnnAssign）。"""
        models_py = tmp_path / "models.py"
        models_py.write_text('class Foo:\n    __tablename__: str = "foo"\n', encoding="utf-8")
        names = _extract_tablenames_from_models(models_py)
        assert names == set()

    def test_extract_table_definition_keys_ignores_wrong_var(self, tmp_path):
        """非 TABLE_DEFINITIONS 的字典赋值应被忽略。"""
        dict_py = tmp_path / "data_dictionary.py"
        dict_py.write_text(
            'OTHER_DICT = {"foo": 1}\nTABLE_DEFINITIONS = {"stock_basic": {}}\n',
            encoding="utf-8",
        )
        keys = _extract_table_definition_keys(dict_py)
        assert keys == {"stock_basic"}


# ============================================================================
# R13 纯函数测试
# ============================================================================


class TestR13PureFunction:
    """R13 纯函数测试：验证 DAO 类提取与 CacheManager 实例化提取逻辑。"""

    def test_extract_dao_classes(self, tmp_path):
        """从 daos/ 目录提取继承 BaseDao 的类名。"""
        daos_dir = tmp_path / "daos"
        daos_dir.mkdir()
        (daos_dir / "stock_dao.py").write_text(
            "from data.persistence.daos.base_dao import BaseDao\nclass StockDao(BaseDao):\n    pass\n",
            encoding="utf-8",
        )
        (daos_dir / "base_dao.py").write_text("class BaseDao:\n    pass\n", encoding="utf-8")
        result = _extract_dao_classes(daos_dir)
        assert "StockDao" in result
        assert "BaseDao" not in result  # base_dao.py 被排除

    def test_extract_dao_classes_ignores_non_base_dao(self, tmp_path):
        """不继承 BaseDao 的类不应被提取。"""
        daos_dir = tmp_path / "daos"
        daos_dir.mkdir()
        (daos_dir / "foo_dao.py").write_text("class FooDao:\n    pass\n", encoding="utf-8")
        result = _extract_dao_classes(daos_dir)
        assert "FooDao" not in result

    def test_extract_cache_manager_instances(self, tmp_path):
        """从 CacheManager.__init__ 提取实例化的 DAO 类名。"""
        cm_py = tmp_path / "cache_manager.py"
        cm_py.write_text(
            "class CacheManager:\n"
            "    def __init__(self):\n"
            "        self.stock_dao = StockDao(self.engine)\n"
            "        self.quote_dao = QuoteDao(self.engine)\n"
            "    def read_db(self):\n"
            "        dao = BaseDao(self.engine)  # __init__ 外不应被捕获\n"
            "        return dao\n",
            encoding="utf-8",
        )
        result = _extract_cache_manager_dao_instances(cm_py)
        assert "StockDao" in result
        assert "QuoteDao" in result
        assert "BaseDao" not in result  # __init__ 外的不被捕获

    def test_extract_cache_manager_instances_only_init(self, tmp_path):
        """仅扫描 __init__ 方法，其他方法的实例化不被捕获。"""
        cm_py = tmp_path / "cache_manager.py"
        cm_py.write_text(
            "class CacheManager:\n"
            "    def __init__(self):\n"
            "        self.stock_dao = StockDao(self.engine)\n"
            "    def other_method(self):\n"
            "        self.extra_dao = ExtraDao(self.engine)\n",
            encoding="utf-8",
        )
        result = _extract_cache_manager_dao_instances(cm_py)
        assert "StockDao" in result
        assert "ExtraDao" not in result  # other_method 内的不被捕获


# ============================================================================
# R14 纯函数测试
# ============================================================================


class TestR14PureFunction:
    """R14 纯函数测试：验证策略子类识别与装饰器检测逻辑。"""

    def test_strategy_subclass_detected(self):
        """继承 BaseStrategy 的类应被识别为策略子类。"""
        node = _first_class_def("class MyStrategy(BaseStrategy):\n    pass\n")
        assert _is_strategy_subclass(node) is True

    def test_polars_subclass_detected(self):
        """继承 PolarsBaseStrategy 的类应被识别为策略子类。"""
        node = _first_class_def("class MyStrategy(PolarsBaseStrategy):\n    pass\n")
        assert _is_strategy_subclass(node) is True

    def test_base_strategy_not_flagged(self):
        """BaseStrategy 基类自身不应被识别为策略子类。"""
        node = _first_class_def("class BaseStrategy(ABC):\n    pass\n")
        assert _is_strategy_subclass(node) is False

    def test_polars_base_not_flagged(self):
        """PolarsBaseStrategy 基类自身不应被识别为策略子类。"""
        node = _first_class_def("class PolarsBaseStrategy(BaseStrategy, AIStrategyMixin):\n    pass\n")
        assert _is_strategy_subclass(node) is False

    def test_mixin_not_flagged(self):
        """AIStrategyMixin 不应被识别为策略子类。"""
        node = _first_class_def("class AIStrategyMixin:\n    pass\n")
        assert _is_strategy_subclass(node) is False

    def test_non_strategy_not_flagged(self):
        """不继承策略基类的类不应被识别。"""
        node = _first_class_def("class Foo:\n    pass\n")
        assert _is_strategy_subclass(node) is False

    def test_decorator_names_register_strategy_call(self):
        """@register_strategy("key") 装饰器应被识别。"""
        node = _first_class_def('@register_strategy("oversold")\nclass Foo:\n    pass\n')
        assert "register_strategy" in _decorator_names(node)

    def test_decorator_names_register_singleton_name(self):
        """@register_singleton 装饰器应被识别。"""
        node = _first_class_def("@register_singleton\nclass Foo:\n    pass\n")
        assert "register_singleton" in _decorator_names(node)


# ============================================================================
# R15 纯函数测试
# ============================================================================


class TestR15PureFunction:
    """R15 纯函数测试：验证单例类识别逻辑。"""

    def test_singleton_with_instance_attr_detected(self):
        """有 _instance 类属性 + __new__ 的类应被识别为单例。"""
        node = _first_class_def(
            "class MySingleton:\n"
            "    _instance = None\n"
            "    def __new__(cls):\n"
            "        if cls._instance is None:\n"
            "            cls._instance = super().__new__(cls)\n"
            "        return cls._instance\n"
        )
        assert _is_singleton_class(node) is True

    def test_singleton_with_reset_detected(self):
        """有 __new__ + _reset_singleton 的类应被识别为单例（无显式 _instance 类属性）。"""
        node = _first_class_def(
            "class MySingleton:\n"
            "    def __new__(cls):\n"
            '        if not hasattr(cls, "_instance"):\n'
            "            cls._instance = super().__new__(cls)\n"
            "        return cls._instance\n"
            "    @classmethod\n"
            "    def _reset_singleton(cls):\n"
            "        cls._instance = None\n"
        )
        assert _is_singleton_class(node) is True

    def test_plain_class_not_detected(self):
        """普通类不应被识别为单例。"""
        node = _first_class_def("class Foo:\n    pass\n")
        assert _is_singleton_class(node) is False

    def test_class_with_new_only_not_detected(self):
        """仅有 __new__ 但无 _instance/_reset_singleton 的类不应被识别（避免误报不可变类型）。"""
        node = _first_class_def("class Foo:\n    def __new__(cls):\n        return super().__new__(cls)\n")
        assert _is_singleton_class(node) is False

    def test_class_with_instance_only_not_detected(self):
        """仅有 _instance 类属性但无 __new__ 的类不应被识别（可能是普通类属性）。"""
        node = _first_class_def("class Foo:\n    _instance = None\n")
        assert _is_singleton_class(node) is False

    def test_class_with_reset_only_not_detected(self):
        """仅有 _reset_singleton 但无 __new__ 的类不应被识别（如 ProxyManager 模块级状态单例）。"""
        node = _first_class_def("class Foo:\n    @classmethod\n    def _reset_singleton(cls):\n        pass\n")
        assert _is_singleton_class(node) is False


# ============================================================================
# 辅助函数测试
# ============================================================================


class TestBaseClassNames:
    """验证 _base_class_names 正确提取直接基类名称。"""

    def test_name_base(self):
        node = _first_class_def("class Foo(Bar):\n    pass\n")
        assert _base_class_names(node) == {"Bar"}

    def test_attribute_base(self):
        """属性链基类（如 module.BaseClass）应提取 attr 名。"""
        node = _first_class_def("class Foo(base.BaseClass):\n    pass\n")
        assert _base_class_names(node) == {"BaseClass"}

    def test_multiple_bases(self):
        node = _first_class_def("class Foo(Bar, Baz):\n    pass\n")
        assert _base_class_names(node) == {"Bar", "Baz"}

    def test_no_bases(self):
        node = _first_class_def("class Foo:\n    pass\n")
        assert _base_class_names(node) == set()


# ============================================================================
# 集成测试：验证当前代码库通过所有红线检查（契约测试）
# ============================================================================


class TestRedlineIntegrationOnCurrentCodebase:
    """集成测试：验证当前代码库通过所有红线检查。

    这些测试作为契约测试，确保代码库始终符合 R4/R12/R13/R14/R15 红线。
    如果某项检查失败，说明有违规引入，应立即修复。
    """

    def test_check_R4_passes(self):
        """R4：当前代码库无 asyncpg 原生查询 %s 占位符。"""
        errors = check_R4()
        assert errors == [], "R4 violations found:\n  " + "\n  ".join(errors)

    def test_check_R12_passes(self):
        """R12：当前代码库 models.py 的 __tablename__ 与 TABLE_DEFINITIONS 一致。"""
        errors = check_R12()
        assert errors == [], "R12 violations found:\n  " + "\n  ".join(errors)

    def test_check_R13_passes(self):
        """R13：当前代码库所有 DAO 类在 CacheManager.__init__ 中实例化。"""
        errors = check_R13()
        assert errors == [], "R13 violations found:\n  " + "\n  ".join(errors)

    def test_check_R14_passes(self):
        """R14：当前代码库所有策略子类使用 @register_strategy 装饰器。"""
        errors = check_R14()
        assert errors == [], "R14 violations found:\n  " + "\n  ".join(errors)

    def test_check_R15_passes(self):
        """R15：当前代码库所有单例类使用 @register_singleton 装饰器。"""
        errors = check_R15()
        assert errors == [], "R15 violations found:\n  " + "\n  ".join(errors)

    def test_main_returns_zero(self):
        """脚本 main() 在当前代码库状态下应返回 0（全部通过）。"""
        assert main() == 0, "check_redlines.py main() should return 0 when all checks pass"


# ============================================================================
# GBK 编码兼容性测试（Windows PYTHONIOENCODING=gbk 兜底）
# ============================================================================


class TestGBKEncodingCompatibility:
    """验证脚本在 GBK 编码环境下不会触发 UnicodeEncodeError。

    Windows 环境下 PYTHONIOENCODING=gbk 时，emoji（❌✅）输出会触发
    UnicodeEncodeError，导致 pre-commit hook 异常退出。脚本应将输出改为
    纯 ASCII（如 [PASS]/[FAIL]），并在入口处 reconfigure sys.stdout 兜底。
    """

    def test_no_unicode_error_under_gbk(self):
        """PYTHONIOENCODING=gbk 下运行脚本，应无 UnicodeEncodeError 且退出码正确。"""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "gbk"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_redlines.py")],
            capture_output=True,
            env=env,
            cwd=ROOT,
            timeout=60,
            check=False,
        )
        # 退出码符合检查结果（当前代码库应通过所有检查，退出码为 0）
        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        # 不应有 UnicodeEncodeError（emoji 输出在 GBK 下会触发）
        combined = result.stdout + result.stderr
        assert b"UnicodeEncodeError" not in combined, (
            f"UnicodeEncodeError detected under GBK encoding\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
