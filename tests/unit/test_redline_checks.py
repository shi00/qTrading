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

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_redlines import (  # noqa: E402 - sys.path 注入后导入
    _R16_SINGLETON_CLASSES,
    _base_class_names,
    _check_R16_in_tree,
    _check_R4_in_tree,
    _check_R_no_bare_ft_colors_in_tree,
    _check_R_no_bare_font_size_in_tree,
    _decorator_names,
    _extract_cache_manager_dao_instances,
    _extract_dao_classes,
    _extract_table_definition_keys,
    _extract_tablenames_from_models,
    _is_settings_tabs_dir,
    _is_singleton_class,
    _is_strategy_subclass,
    check_R12,
    check_R13,
    check_R14,
    check_R15,
    check_R16_vm_init_singleton_construction,
    check_R4,
    check_R4_in_tests,
    check_R_no_bare_ft_colors_in_ui,
    check_R_no_bare_font_size_in_ui,
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
# R4 (tests/) 函数测试：验证 check_R4_in_tests 的目录扫描与误报防护
# ============================================================================


class TestR4InTestsFunction:
    """R4 (tests/) 函数测试：验证 check_R4_in_tests 的目录扫描行为与误报防护。

    覆盖 P3-CheckRedlines-Tests-Dir 的关键场景：
    - tests/ 目录下 R4 违规（conn.execute("...%s...")）应被检测
    - tests/ 目录下 "%s" % var 字符串格式化不应被误报（BinOp 表达式，第一个参数非 Constant）
    - tests/ 目录下字符串字面量含 R4 模式不应被误报（AST 不进入字符串内部）
    - tests/ 目录下 @pytest.fixture 函数内的违规应被检测（fixture 不影响 AST 检查）
    - tests/ 目录不存在时应返回空列表
    """

    def test_violation_in_tests_detected(self, tmp_path, monkeypatch):
        """tests/ 目录下含 conn.execute("...%s...") 应被检测。"""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_bad.py").write_text(
            'async def f():\n    await conn.execute("SELECT * FROM t WHERE id = %s", x)\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert len(errors) == 1
        assert "R4" in errors[0]
        assert "%s" in errors[0]

    def test_percent_format_not_flagged(self, tmp_path, monkeypatch):
        """``"%s" % var`` 字符串格式化不应被误报（BinOp，第一个参数非 Constant）。"""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_format.py").write_text(
            'def test_something():\n    msg = "%s is broken" % name\n    assert msg\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert errors == []

    def test_string_literal_with_r4_pattern_not_flagged(self, tmp_path, monkeypatch):
        """字符串字面量含 R4 模式（作为测试用例数据）不应被误报。

        AST 不会进入字符串字面量内部解析，因此 ``code = 'await conn.execute("...%s...")'``
        中的 ``code`` 是 Constant 字符串赋值，不是真实的 conn.execute 调用。
        """
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fixture.py").write_text(
            "def test_r4_detection():\n"
            "    code = 'await conn.execute(\"SELECT * FROM t WHERE id = %s\", x)'\n"
            "    assert '%s' in code\n",
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert errors == []

    def test_violation_in_pytest_fixture_detected(self, tmp_path, monkeypatch):
        """@pytest.fixture 函数内的 R4 违规应被检测（fixture 不影响 AST 检查）。"""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fixture_bad.py").write_text(
            "import pytest\n"
            "@pytest.fixture\n"
            "async def bad_cursor():\n"
            '    await conn.execute("SELECT * FROM t WHERE id = %s", x)\n'
            "    return None\n",
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert len(errors) == 1
        assert "R4" in errors[0]

    def test_tests_dir_not_exists_returns_empty(self, tmp_path, monkeypatch):
        """tests/ 目录不存在时应返回空列表。"""
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert errors == []

    def test_skip_cache_dirs(self, tmp_path, monkeypatch):
        """__pycache__/.pytest_cache/.ruff_cache/.tmp 目录应被跳过。"""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 在缓存目录下放违规文件，应被跳过
        for cache_dir in ("__pycache__", ".pytest_cache", ".ruff_cache", ".tmp"):
            cache_path = tests_dir / cache_dir
            cache_path.mkdir()
            (cache_path / "cached_module.py").write_text(
                'async def f():\n    await conn.execute("SELECT * FROM t WHERE id = %s", x)\n',
                encoding="utf-8",
            )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert errors == []

    def test_dollar_placeholder_not_flagged(self, tmp_path, monkeypatch):
        """tests/ 目录下 $1 占位符（asyncpg 正确用法）不应被检测。"""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_ok.py").write_text(
            'async def f():\n    await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db)\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert errors == []

    def test_sync_call_without_await_detected(self, tmp_path, monkeypatch):
        """同步调用（无 await）的 conn.execute 也应被检测。

        _check_R4_in_tree 只匹配 Call 节点，不关心是否在 await 中。
        测试代码中 mock 调用通常无 await，应同样受 R4 红线约束。
        """
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sync.py").write_text(
            'def test_sync_call():\n    conn.execute("SELECT * FROM t WHERE id = %s", x)\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert len(errors) == 1
        assert "R4" in errors[0]

    def test_subdirectory_scanned_recursively(self, tmp_path, monkeypatch):
        """tests/integration/ 子目录下的违规也应被递归扫描。"""
        tests_dir = tmp_path / "tests"
        integration_dir = tests_dir / "integration"
        integration_dir.mkdir(parents=True)
        (integration_dir / "test_integration.py").write_text(
            'async def test_db():\n    await conn.execute("SELECT * FROM t WHERE id = %s", x)\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert len(errors) == 1
        assert "R4" in errors[0]

    def test_non_conn_variable_name_detected(self, tmp_path, monkeypatch):
        """变量名不是 conn 的 asyncpg 调用也应被检测。

        _check_R4_in_tree 不依赖调用者变量名，只匹配 .<method>("...%s...") 模式。
        """
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_db_var.py").write_text(
            'async def f():\n    await db.execute("SELECT * FROM t WHERE id = %s", x)\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert len(errors) == 1
        assert "R4" in errors[0]

    def test_triple_quoted_string_detected(self, tmp_path, monkeypatch):
        """三引号字符串字面量含 %s 也应被检测（AST 中是单个 Constant）。"""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_triple.py").write_text(
            'async def f():\n    await conn.execute("""SELECT * FROM t WHERE id = %s""", x)\n',
            encoding="utf-8",
        )
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        errors = check_redlines.check_R4_in_tests()
        assert len(errors) == 1
        assert "R4" in errors[0]


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
# R16 纯函数测试 (review07-G20)
# ============================================================================


def _r16_check(code: str) -> list[str]:
    """构造临时源码树并调用 _check_R16_in_tree（源码经临时文件落盘以支持 noqa 行读取）。"""
    import tempfile
    from pathlib import Path

    tree = ast.parse(code)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ui" / "viewmodels" / "fake_vm.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        return _check_R16_in_tree(tree, p)


class TestR16PureFunction:
    """R16 纯函数测试：验证 ViewModel __init__ 构造已注册单例的检测逻辑。"""

    def test_detects_singleton_construction_in_init(self):
        """VM __init__ 中构造已注册单例应被检测。"""
        code = "import services.task_manager\nclass MyVM:\n    def __init__(self):\n        self._tm = TaskManager()\n"
        errors = _r16_check(code)
        assert len(errors) == 1
        assert "R16" in errors[0]
        assert "TaskManager" in errors[0]

    def test_noqa_r16_exempts_line(self):
        """显式 # noqa: R16 豁免的调用行不应被检测。"""
        code = (
            "import services.task_manager\n"
            "class MyVM:\n"
            "    def __init__(self):\n"
            "        self._tm = TaskManager()  # noqa: R16 - 持有引用\n"
        )
        errors = _r16_check(code)
        assert errors == []

    def test_reflection_without_construction_not_detected(self):
        """仅引用（赋值给变量引用而非构造调用，如 lambda/类型传递）不涉及构造则不应报。"""
        code = "class MyVM:\n    def __init__(self):\n        self._tp = ThreadPoolManager  # 仅类型引用\n"
        errors = _r16_check(code)
        assert errors == []

    def test_non_singleton_call_not_detected(self):
        """构造非注册单例类（如普通类）不应被检测。"""
        code = "class MyVM:\n    def __init__(self):\n        self._x = SomeService()\n"
        errors = _r16_check(code)
        assert errors == []

    def test_other_methods_not_scanned(self):
        """仅 __init__ 被扫描；其他方法中构造单例不报（避免误伤命令内 DI）。"""
        code = (
            "import services.task_manager\n"
            "class MyVM:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def run(self):\n"
            "        m = TaskManager()\n"
        )
        errors = _r16_check(code)
        assert errors == []

    def test_whitelist_matches_content(self):
        """单例白名单与 singleton-lifecycle.md 注册清单保持一致（联动 G24 动态比对测试）。"""
        assert "TaskManager" in _R16_SINGLETON_CLASSES
        assert "DataProcessor" in _R16_SINGLETON_CLASSES
        assert "SomeService" not in _R16_SINGLETON_CLASSES

    def test_whitelist_matches_documented_singletons(self):
        """R16 单例白名单 == singleton-lifecycle.md 注册单例集合（防漂移传递闭包）。

        G24 的 TestSingletonRegistryConsistency 强制 文档清单 ↔ singleton_registry 一致；
        本测试再强制 白名单 ↔ 文档清单 一致，二者传递闭包确保白名单永不漂移。
        """
        import re

        content = (ROOT / "docs" / "architecture" / "singleton-lifecycle.md").read_text(encoding="utf-8")
        section = content.split("**注册单例（", 1)[1].split("**非注册单例", 1)[0]
        documented = frozenset(re.findall(r"^\| `(\w+)`", section, flags=re.M))
        assert documented == _R16_SINGLETON_CLASSES, (
            "check_redlines.py _R16_SINGLETON_CLASSES 与 singleton-lifecycle.md 注册清单不一致。"
            f"\n白名单独有: {sorted(_R16_SINGLETON_CLASSES - documented)}"
            f"\n文档独有: {sorted(documented - _R16_SINGLETON_CLASSES)}"
        )


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

    def test_check_R4_in_tests_passes(self):
        """R4（tests/）：当前 tests/ 目录无 asyncpg 原生查询 %s 占位符。"""
        errors = check_R4_in_tests()
        assert errors == [], "R4 (tests) violations found:\n  " + "\n  ".join(errors)

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

    def test_check_R16_passes(self):
        """R16（部分）：当前代码库 ViewModel __init__ 无未豁免的已注册单例构造。"""
        errors = check_R16_vm_init_singleton_construction()
        assert errors == [], "R16 (VM singleton construction) violations found:\n  " + "\n  ".join(errors)

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


# ============================================================================
# R_no_bare_ft_colors_in_ui 纯函数测试 (P1-2 #54)
# ============================================================================


class TestRNoBareFtColorsPureFunction:
    """R_no_bare_ft_colors_in_ui 纯函数测试: 灰阶/Layer1/裸色值/装饰色豁免分类逻辑。"""

    def _check(self, code: str, source_path: Path) -> tuple[list[str], list[str]]:
        tree = ast.parse(code)
        return _check_R_no_bare_ft_colors_in_tree(tree, source_path)

    def _ui_path(self, rel: str) -> Path:
        """构造 ROOT 下 ui/ 路径 (用于 rel 计算)。"""
        return ROOT / rel

    def test_layer1_surface_not_flagged(self):
        """Layer 1 语义 token ft.Colors.SURFACE 完全放行 (error/warning 均为空)。"""
        code = "x = ft.Colors.SURFACE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_layer1_on_surface_not_flagged(self):
        """Layer 1 语义 token ft.Colors.ON_SURFACE 完全放行。"""
        code = "x = ft.Colors.ON_SURFACE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_layer1_primary_not_flagged(self):
        """Layer 1 语义 token ft.Colors.PRIMARY 完全放行。"""
        code = "x = ft.Colors.PRIMARY\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_layer1_error_not_flagged(self):
        """Layer 1 语义 token ft.Colors.ERROR 完全放行。"""
        code = "x = ft.Colors.ERROR\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_grayscale_grey_only_warning(self):
        """灰阶色 ft.Colors.GREY 仅 warning, 不进入 error (不阻断)。"""
        code = "x = ft.Colors.GREY\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert len(warns) == 1
        assert "GREY" in warns[0]

    def test_grayscale_white_only_warning(self):
        """灰阶色 ft.Colors.WHITE 仅 warning。"""
        code = "x = ft.Colors.WHITE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert len(warns) == 1
        assert "WHITE" in warns[0]

    def test_grayscale_black_only_warning(self):
        """灰阶色 ft.Colors.BLACK 仅 warning。"""
        code = "x = ft.Colors.BLACK\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert len(warns) == 1
        assert "BLACK" in warns[0]

    def test_grayscale_transparent_only_warning(self):
        """灰阶色 ft.Colors.TRANSPARENT 仅 warning。"""
        code = "x = ft.Colors.TRANSPARENT\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert len(warns) == 1
        assert "TRANSPARENT" in warns[0]

    def test_bare_red_intercepted_as_error(self):
        """裸色值 ft.Colors.RED 必须进入 error (非零退出)。"""
        code = "x = ft.Colors.RED\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "RED" in errs[0]
        assert "R_no_bare_ft_colors_in_ui" in errs[0]
        assert warns == []

    def test_bare_green_intercepted_as_error(self):
        """裸色值 ft.Colors.GREEN 必须进入 error。"""
        code = "x = ft.Colors.GREEN\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "GREEN" in errs[0]

    def test_bare_blue_intercepted_as_error(self):
        """裸色值 ft.Colors.BLUE 必须进入 error。"""
        code = "x = ft.Colors.BLUE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "BLUE" in errs[0]

    def test_bare_red_400_intercepted_as_error(self):
        """裸色值 ft.Colors.RED_400 必须进入 error (startup_views L180 场景)。"""
        code = "x = ft.Colors.RED_400\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "RED_400" in errs[0]

    def test_bare_purple_intercepted_as_error(self):
        """裸色值 ft.Colors.PURPLE 必须进入 error (非 settings_tabs 目录)。"""
        code = "x = ft.Colors.PURPLE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "PURPLE" in errs[0]

    def test_bare_yellow_intercepted_as_error(self):
        """裸色值 ft.Colors.YELLOW 必须进入 error。"""
        code = "x = ft.Colors.YELLOW\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "YELLOW" in errs[0]

    def test_bare_orange_intercepted_as_error(self):
        """裸色值 ft.Colors.ORANGE 必须进入 error (非 settings_tabs 目录)。"""
        code = "x = ft.Colors.ORANGE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "ORANGE" in errs[0]

    def test_bare_teal_intercepted_as_error(self):
        """裸色值 ft.Colors.TEAL 必须进入 error (非 settings_tabs 目录)。"""
        code = "x = ft.Colors.TEAL\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "TEAL" in errs[0]

    def test_bare_cyan_intercepted_as_error(self):
        """裸色值 ft.Colors.CYAN 必须进入 error。"""
        code = "x = ft.Colors.CYAN\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "CYAN" in errs[0]

    def test_bare_indigo_intercepted_as_error(self):
        """裸色值 ft.Colors.INDIGO 必须进入 error (非 settings_tabs 目录)。"""
        code = "x = ft.Colors.INDIGO\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "INDIGO" in errs[0]

    def test_settings_tabs_decorative_blue_only_warning(self):
        """settings_tabs/ 目录下装饰色 ft.Colors.BLUE 仅 warning (icon_color 场景豁免)。"""
        code = "x = ft.Colors.BLUE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert errs == []
        assert len(warns) == 1
        assert "BLUE" in warns[0]
        assert "settings_tabs icon_color" in warns[0]

    def test_settings_tabs_decorative_purple_only_warning(self):
        """settings_tabs/ 目录下装饰色 ft.Colors.PURPLE 仅 warning。"""
        code = "x = ft.Colors.PURPLE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert errs == []
        assert len(warns) == 1
        assert "PURPLE" in warns[0]

    def test_settings_tabs_decorative_indigo_only_warning(self):
        """settings_tabs/ 目录下装饰色 ft.Colors.INDIGO 仅 warning。"""
        code = "x = ft.Colors.INDIGO\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert errs == []
        assert len(warns) == 1
        assert "INDIGO" in warns[0]

    def test_settings_tabs_decorative_orange_only_warning(self):
        """settings_tabs/ 目录下装饰色 ft.Colors.ORANGE 仅 warning。"""
        code = "x = ft.Colors.ORANGE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert errs == []
        assert len(warns) == 1
        assert "ORANGE" in warns[0]

    def test_settings_tabs_decorative_teal_only_warning(self):
        """settings_tabs/ 目录下装饰色 ft.Colors.TEAL 仅 warning。"""
        code = "x = ft.Colors.TEAL\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert errs == []
        assert len(warns) == 1
        assert "TEAL" in warns[0]

    def test_settings_tabs_red_still_intercepted(self):
        """settings_tabs/ 目录下 ft.Colors.RED 仍拦截 (RED 非装饰色, 应改 ERROR)。"""
        code = "x = ft.Colors.RED\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert len(errs) == 1
        assert "RED" in errs[0]
        assert warns == []

    def test_settings_tabs_green_still_intercepted(self):
        """settings_tabs/ 目录下 ft.Colors.GREEN 仍拦截 (GREEN 非装饰色, 应改 SUCCESS)。"""
        code = "x = ft.Colors.GREEN\n"
        errs, warns = self._check(code, self._ui_path("ui/views/settings_tabs/system_tab.py"))
        assert len(errs) == 1
        assert "GREEN" in errs[0]

    def test_non_ft_colors_attribute_not_flagged(self):
        """非 ft.Colors.X 表达式不应被检测 (如 ft.Icons.X)。"""
        code = "x = ft.Icons.RED\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_module_attribute_not_flagged(self):
        """非 ft 模块的 Colors.X 不应被检测 (如 other.Colors.RED)。"""
        code = "x = other.Colors.RED\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_multiple_violations_count_correctly(self):
        """多个违规按出现次数计数 (每个 ft.Colors.X 一个 error)。"""
        code = "x = ft.Colors.RED\ny = ft.Colors.GREEN\nz = ft.Colors.BLUE\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 3
        assert warns == []

    # ------------------------------------------------------------------
    # OBS-6: ast.IfExp hasattr + hex fallback 模式检测
    # ------------------------------------------------------------------

    def test_hasattr_hex_fallback_warning(self):
        """OBS-6: ``X if hasattr(...) else '#888888'`` 模式 → warning (不阻断)."""
        code = "color = AppColors.TEXT_TERTIARY if hasattr(AppColors, 'TEXT_TERTIARY') else '#888888'\n"
        errs, warns = self._check(code, self._ui_path("ui/components/virtual_table.py"))
        assert errs == []
        assert len(warns) == 1
        assert "hasattr + hex fallback" in warns[0]
        assert "#888888" in warns[0]

    def test_hasattr_non_hex_fallback_not_flagged(self):
        """OBS-6: hasattr + 非 hex 字符串 fallback 不应被检测 (避免误报)."""
        code = "name = obj.name if hasattr(obj, 'name') else 'unknown'\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_hex_fallback_without_hasattr_not_flagged(self):
        """OBS-6: 非 hasattr 的 ternary + hex fallback 不应被检测 (避免误报)."""
        # 例: condition 直接为 bool 表达式 (非 hasattr 调用)
        code = "color = '#FF0000' if use_red else '#00FF00'\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert warns == []

    def test_hasattr_hex_fallback_does_not_block_main(self):
        """OBS-6: hasattr + hex fallback 为 warning, 不进 errors (main 退出码仍 0)."""
        code = "color = AppColors.X if hasattr(AppColors, 'X') else '#abcdef'\n"
        errs, warns = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []
        assert len(warns) == 1


# ============================================================================
# R_no_bare_ft_colors_in_ui 辅助函数测试
# ============================================================================


class TestIsSettingsTabsDir:
    """_is_settings_tabs_dir: 判断文件是否位于 ui/views/settings_tabs/ 目录下。"""

    def test_system_tab_returns_true(self):
        assert _is_settings_tabs_dir(ROOT / "ui" / "views" / "settings_tabs" / "system_tab.py") is True

    def test_data_source_tab_returns_true(self):
        assert _is_settings_tabs_dir(ROOT / "ui" / "views" / "settings_tabs" / "data_source_tab.py") is True

    def test_other_views_returns_false(self):
        assert _is_settings_tabs_dir(ROOT / "ui" / "views" / "data_view.py") is False

    def test_components_returns_false(self):
        assert _is_settings_tabs_dir(ROOT / "ui" / "components" / "news_feed.py") is False

    def test_non_ui_returns_false(self):
        assert _is_settings_tabs_dir(ROOT / "scripts" / "check_redlines.py") is False


# ============================================================================
# R_no_bare_ft_colors_in_ui 集成测试 (当前代码库契约)
# ============================================================================


class TestRNoBareFtColorsIntegration:
    """R_no_bare_ft_colors_in_ui 集成测试: 当前代码库契约守护。

    验证当前 UI 层无裸 ft.Colors.<拦截名单色值> 引用 (RED/RED_400/GREEN/BLUE 等)。
    装饰色豁免 (settings_tabs icon_color) 与灰阶色 warning 不影响 main() 退出码 0。
    """

    def test_check_R_no_bare_ft_colors_in_ui_passes(self):
        """R_no_bare_ft_colors_in_ui: 当前代码库无裸色值 (errors 为空)。"""
        errors = check_R_no_bare_ft_colors_in_ui()
        assert errors == [], "R_no_bare_ft_colors_in_ui violations:\n  " + "\n  ".join(errors)

    def test_main_returns_zero_with_bare_color_check(self):
        """main() 包含 R_no_bare_ft_colors_in_ui 检查后仍应返回 0 (当前代码库合规)。"""
        assert main() == 0


# ============================================================================
# R_no_bare_font_size_in_ui 纯函数测试
# ============================================================================


class TestRNoBareFontSizePureFunction:
    """R_no_bare_font_size_in_ui 纯函数测试: ft.Text/ft.TextStyle size= 裸数值拦截分类逻辑。

    规则：`ft.Text(size=13)` / `ft.TextStyle(size=20)` 等 size 为 int 字面量 → 违规 (error)，
    必须改为 `size=AppStyles.FONT_SIZE_*` token。
    放行：size 为变量名、AppStyles.FONT_SIZE_* 属性引用、或非 int 字面量表达式。
    """

    def _check(self, code: str, source_path: Path) -> list[str]:
        tree = ast.parse(code)
        return _check_R_no_bare_font_size_in_tree(tree, source_path)

    def _ui_path(self, rel: str) -> Path:
        """构造 ROOT 下 ui/ 路径 (用于 rel 计算)。"""
        return ROOT / rel

    def test_text_bare_int_flagged(self):
        """ft.Text(size=13) 硬编码字号 → error。"""
        code = "t = ft.Text('hello', size=13)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "R_no_bare_font_size_in_ui" in errs[0]
        assert "size=13" in errs[0]

    def test_text_xl_int_flagged(self):
        """ft.Text(size=24) 硬编码页面主标题字号 → error (历史 Issue #445 根因场景)。"""
        code = "t = ft.Text('page title', size=24)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "size=24" in errs[0]

    def test_text_style_bare_int_flagged(self):
        """ft.TextStyle(size=20) 硬编码字号 → error。"""
        code = "s = ft.TextStyle(size=20)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "size=20" in errs[0]

    def test_token_attribute_not_flagged(self):
        """size=AppStyles.FONT_SIZE_BODY 属性引用 → 放行。"""
        code = "t = ft.Text('body', size=AppStyles.FONT_SIZE_BODY)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_token_variable_not_flagged(self):
        """size 为变量名 → 放行。"""
        code = "size = 13\nt = ft.Text('body', size=size)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_non_ft_control_not_flagged(self):
        """非 ft.Text/ft.TextStyle 的 size 参数 → 放行 (如 button.height 无关)。"""
        code = "b = ft.Button(width=13)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_other_module_text_not_flagged(self):
        """非 ft 模块的 Text 调用 → 放行 (如 other.Text(size=13))。"""
        code = "t = other.Text('x', size=13)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_positional_size_not_flagged(self):
        """size 作为位置参数 → 放行 (规则仅匹配 size= 关键字)。"""
        code = "t = ft.Text('x', 13)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_multiple_text_one_error(self):
        """单个 ft.Text 仅报一个 error (一个控件一个违规)。"""
        code = "t = ft.Text('x', size=12)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1

    def test_multiple_controls_count_correctly(self):
        """多个违规按控件数计数。"""
        code = "a = ft.Text('a', size=11)\nb = ft.TextStyle(size=12)\nc = ft.Text('c')\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 2

    def test_string_size_not_flagged(self):
        """size 为字符串 (如 'bold') → 放行 (非 int 字面量)。"""
        code = "t = ft.Text('x', size='inherit')\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_bool_size_not_flagged(self):
        """size=True/False → 放行 (bool 是 int 子类但无字号语义，排除避免误报)。"""
        code = "t = ft.Text('x', size=True)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_float_size_not_flagged(self):
        """size=13.5 (float) → 放行 (仅拦截 int，float 实践中不使用)。"""
        code = "t = ft.Text('x', size=13.5)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_text_size_keyword_not_flagged(self):
        """text_size=13 (非 size= 关键字) → 放行 (检查器仅守护 size= 关键字)。"""
        code = "d = ft.Dropdown(text_size=13)\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert errs == []

    def test_nested_text_in_container_detected(self):
        """嵌套在容器中的 ft.Text(size=13) → 仍被检测 (ast.walk 遍历所有节点)。"""
        code = "c = ft.Column([ft.Text('x', size=13)])\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "size=13" in errs[0]

    def test_textstyle_inside_text_detected(self):
        """ft.Text(style=ft.TextStyle(size=20)) 双层嵌套 → TextStyle 被检测。"""
        code = "t = ft.Text('x', style=ft.TextStyle(size=20))\n"
        errs = self._check(code, self._ui_path("ui/views/foo.py"))
        assert len(errs) == 1
        assert "size=20" in errs[0]
        assert "TextStyle" in errs[0]


# ============================================================================
# R_no_bare_font_size_in_ui 集成测试 (当前代码库契约)
# ============================================================================


class TestRNoBareFontSizeIntegration:
    """R_no_bare_font_size_in_ui 集成测试: 当前代码库契约守护。

    验证当前 UI 层无 ft.Text/ft.TextStyle 硬编码字号数值 (必须用 AppStyles.FONT_SIZE_* token)。
    """

    def test_check_R_no_bare_font_size_in_ui_passes(self):
        """R_no_bare_font_size_in_ui: 当前代码库无裸字号数值 (errors 为空)。"""
        errors = check_R_no_bare_font_size_in_ui()
        assert errors == [], "R_no_bare_font_size_in_ui violations:\n  " + "\n  ".join(errors)

    def test_main_returns_zero_with_font_size_check(self):
        """main() 包含 R_no_bare_font_size_in_ui 检查后仍应返回 0 (当前代码库合规)。"""
        assert main() == 0


# ============================================================================
# R_lazy_import_whitelist 纯函数测试 (review01-A2-2)
# ============================================================================


class TestLazyImportWhitelist:
    """review01-A2-2: 禁止方向的函数体内跨层 import 必须带 # lazy-import: 注释。

    覆盖场景：
    - utils 层函数体内跨层 import（data/services）无注释 → 报错
    - 带 # lazy-import: 注释 → 通过
    - 模块级跨层 import（非函数体内）→ 不报
    - if TYPE_CHECKING: 块内跨层 import → 豁免
    - 合法方向（services → data）→ 不报
    - import 多行化（ruff format 后注释落末行）→ 按行范围检测
    """

    def _mk(self, tmp_path, monkeypatch, layer: str, filename: str, content: str):
        """构造 layer/<filename> 文件并 monkeypatch ROOT，返回 (errors)。"""
        layer_dir = tmp_path / layer
        layer_dir.mkdir(parents=True, exist_ok=True)
        (layer_dir / filename).write_text(content, encoding="utf-8")
        import check_redlines

        monkeypatch.setattr(check_redlines, "ROOT", tmp_path)
        return check_redlines.check_R_lazy_import_whitelist()

    def test_function_body_cross_layer_import_without_marker_flagged(self, tmp_path, monkeypatch):
        """utils 层函数体内 import data 无 lazy-import 注释 → 报错。"""
        errors = self._mk(
            tmp_path,
            monkeypatch,
            "utils",
            "bad.py",
            "def f():\n    from data.data_processor import DataProcessor\n    return DataProcessor()\n",
        )
        assert len(errors) == 1
        assert "lazy-import" in errors[0]
        assert "utils/bad.py:2" in errors[0]

    def test_function_body_cross_layer_import_with_marker_ok(self, tmp_path, monkeypatch):
        """utils 层函数体内 import services 带 lazy-import 注释 → 通过。"""
        errors = self._mk(
            tmp_path,
            monkeypatch,
            "utils",
            "ok.py",
            "def f():\n    from services.task_manager import TaskManager  # lazy-import: 打破循环\n    return TaskManager()\n",
        )
        assert errors == []

    def test_module_level_cross_layer_import_not_flagged(self, tmp_path, monkeypatch):
        """模块级跨层 import（非函数体内）→ 不报（import-linter 契约 5 已守护）。"""
        errors = self._mk(
            tmp_path,
            monkeypatch,
            "utils",
            "mod.py",
            "from data.data_processor import DataProcessor\n\ndef f():\n    return 1\n",
        )
        assert errors == []

    def test_type_checking_block_exempt(self, tmp_path, monkeypatch):
        """if TYPE_CHECKING: 块内跨层 import → 豁免（仅类型检查，非运行时依赖）。"""
        errors = self._mk(
            tmp_path,
            monkeypatch,
            "services",
            "tc.py",
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from strategies.ai_strategy import AISelectionStrategy\n\ndef f() -> None:\n    return None\n",
        )
        assert errors == []

    def test_legal_direction_not_flagged(self, tmp_path, monkeypatch):
        """合法方向（services 层函数内 import data）→ 不报（services → data 属合法依赖）。"""
        errors = self._mk(
            tmp_path,
            monkeypatch,
            "services",
            "legal.py",
            "async def f():\n    from data.data_processor import DataProcessor\n    return DataProcessor()\n",
        )
        assert errors == []

    def test_multiline_import_marker_on_end_line(self, tmp_path, monkeypatch):
        """ruff format 多行化 import 后注释落在末行 → 按 [lineno, end_lineno] 范围检测。"""
        content = (
            "def f():\n"
            "    from data.persistence.db_config_service import (\n"
            "        DatabaseConfigService,\n"
            "    )  # lazy-import: 打破循环依赖\n"
            "    return DatabaseConfigService()\n"
        )
        errors = self._mk(tmp_path, monkeypatch, "utils", "multi.py", content)
        assert errors == []

    def test_multiline_import_without_marker_flagged(self, tmp_path, monkeypatch):
        """ruff format 多行化 import 且无 lazy-import 注释 → 报错。"""
        content = (
            "def f():\n"
            "    from data.persistence.db_config_service import (\n"
            "        DatabaseConfigService,\n"
            "    )\n"
            "    return DatabaseConfigService()\n"
        )
        errors = self._mk(tmp_path, monkeypatch, "utils", "multi_bad.py", content)
        assert len(errors) == 1
        assert "multi_bad.py" in errors[0]

    def test_import_multi_alias_targets(self, tmp_path, monkeypatch):
        """from services import a, b（多别名 import）→ 目标模块 'services' 命中禁止方向。"""
        errors = self._mk(
            tmp_path,
            monkeypatch,
            "utils",
            "multi_alias.py",
            "def f():\n    from services import a, b\n    return a\n",
        )
        assert len(errors) == 1
        assert "services" in errors[0]
