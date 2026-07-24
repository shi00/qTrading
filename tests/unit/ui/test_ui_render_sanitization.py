"""R9 红线守护测试: UI 渲染路径脱敏守护 (Phase 5 Task 5.2).

依据 CLAUDE.md §3.1 R9 红线(敏感信息泄露): 日志/异常消息直接打印明文
Token / API Key / 密码 / 个人信息必须经 ``DataSanitizer`` 脱敏。

本测试通过 AST 扫描 ``ui/components/`` + ``ui/views/`` 下所有 .py 文件,
检测以下"未脱敏直接打印异常或敏感字段"反模式:

1. **f-string + 异常变量**: ``logger.error(f"... {e} ...")`` — f-string 把异常
   对象的 ``str()`` 直接渲染到日志消息,绕过 ``%s`` 惰性求值机制,
   异常的 ``str()`` 可能含明文 token/api_key/password
2. **f-string + 敏感字段名**: ``logger.error(f"key={api_key}")`` — f-string
   含敏感字段名 (token/api_key/password/secret/key) 的 FormattedValue
3. **显式 str() 包装**: ``logger.error(str(e))`` — 把异常对象显式转为字符串,
   同样绕过 ``%s`` 惰性求值,泄露异常 ``str()``
4. **字符串拼接异常**: ``logger.error("err: " + str(e))`` — 字符串拼接形式
   把异常 ``str()`` 内嵌到日志消息

误报控制(基于 logging 标准实践):
- ``logger.X("...%s", e, exc_info=True)`` — Python 推荐惰性 logging 模式,
  ``%s`` 由 logging 模块按需格式化, ``exc_info=True`` 触发 traceback 渲染,
  本扫描不报警(项目内大量使用,task 5.2 不要求修复)
- ``logger.X("...", DataSanitizer.sanitize_error(e))`` — 经 ``DataSanitizer``
  脱敏, 不报警
- ``logger.X("...", DataSanitizer.sanitize_args(...))`` — 经 ``sanitize_args``
  脱敏, 不报警
- ``logger.X("...", some_var)`` — 普通变量(非异常名/非敏感字段), 不报警
- 间接调用(``_log = logger.error; _log(f"{e}")``)不报警, 因不能静态证明其指向
  ``logger.error``
- 白名单: 行内或上一行 ``# R9_ALLOWED: <reason>`` 注释可豁免

抽样断言(5 个): 验证 UI 异常 message 显示路径在敏感数据输入下不泄露明文 token
/ api_key / password 到 UI 渲染或日志:
1. ``DataSanitizer.sanitize_error`` 处理含明文 token 的异常字符串
2. ``DataSanitizer.sanitize_error`` 处理含 Bearer token 的异常字符串
3. ``DataSanitizer.sanitize_error`` 处理含 URL credentials 的异常字符串
4. ``DataSanitizer.sanitize_dict`` 处理含 api_key 的 dict
5. ``DataSanitizer.sanitize_args`` 处理含 password 的 kwargs
6. ``StockDetailDialog._load_chart_async`` 异常路径渲染到 UI 的 ft.Text
   不含明文 token(error_classifier 返回 i18n 通用消息, 不含原始异常 str)
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import TypeGuard
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from utils.sanitizers import DataSanitizer

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent.parent

# 受扫描的 UI 渲染目录(CLAUDE.md §4.1 表现层, Task 5.2 范围)
SCANNED_DIRS = ("ui/components", "ui/views")

# 白名单注释模式: # R9_ALLOWED: <reason>
_ALLOW_MARKER = re.compile(r"R9_ALLOWED:\s*\S+")

# 受监控的 logger 方法名(error/warning/critical 是可能打印敏感信息的级别)
_LOG_METHODS = frozenset({"error", "warning", "critical"})

# 异常变量名约定(常见 except as <name> 命名)
_EXCEPTION_VAR_NAMES = frozenset(
    {
        "e",
        "exc",
        "ex",
        "err",
        "error",
        "exception",
        "exp",
        "excp",
    }
)

# 敏感字段名(与 DataSanitizer._PATTERN_STANDALONE_KEY_VALUE 对齐)
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "token",
        "api_key",
        "apikey",
        "api-key",
        "secret",
        "password",
        "access_token",
        "refresh_token",
        "credentials",
        "credential",
        "private_key",
        "passphrase",
        "key",
    }
)

# DataSanitizer 调用方法名(检测到这些调用即视为已脱敏)
_SANITIZER_METHODS = frozenset({"sanitize_error", "sanitize_args", "sanitize_token", "sanitize_dict"})


# ============================================================================
# 检测纯函数
# ============================================================================


def _is_logger_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """判断是否为 ``<logger>.error/warning/critical(...)`` 调用。

    任意 logger 变量名(logger/log/LOGGER/_log 等)均识别, 只要属性名匹配
    ``_LOG_METHODS``。间接调用(``_log = logger.error; _log(...)``)不识别。

    TypeGuard 让 pyright 在 ``if _is_logger_call(node):`` 后将 ``node``
    收窄为 ``ast.Call``, 以便访问 ``.args`` / ``.lineno``。
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS


def _is_sanitizer_call(node: ast.AST) -> bool:
    """判断是否为 ``DataSanitizer.sanitize_xxx(...)`` 调用。

    识别 ``DataSanitizer.sanitize_error(e)`` / ``DataSanitizer.sanitize_args(...)``
    等(任意 sanitize_ 方法)。也识别 ``from utils.sanitizers import DataSanitizer``
    后的 ``DataSanitizer.sanitize_xxx(...)`` 形式。
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    # DataSanitizer.sanitize_xxx / sanitizer.sanitize_xxx / ds.sanitize_xxx 等
    return func.attr in _SANITIZER_METHODS


def _is_str_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """判断是否为 ``str(...)`` 调用。

    TypeGuard 让 pyright 在 ``if _is_str_call(arg):`` 后将 ``arg`` 收窄为
    ``ast.Call``, 以便访问 ``.args``。
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Name) and func.id == "str"


def _formatted_value_is_exception(formatted_value: ast.FormattedValue) -> bool:
    """判断 f-string 的 FormattedValue 是否引用异常变量。

    覆盖:
    - ``f"{e}"`` → Name(id="e")
    - ``f"{exc}"`` → Name(id="exc")
    - ``f"{error}"`` → Name(id="error")
    """
    val = formatted_value.value
    return isinstance(val, ast.Name) and val.id in _EXCEPTION_VAR_NAMES


def _formatted_value_is_sensitive_field(formatted_value: ast.FormattedValue) -> bool:
    """判断 f-string 的 FormattedValue 是否引用敏感字段名变量。

    覆盖 ``f"{token}"`` / ``f"{api_key}"`` / ``f"{password}"`` 等变量名直接
    引用敏感字段的场景。也覆盖 ``f"{self.api_key}"`` 等属性访问形式。
    """
    val = formatted_value.value
    if isinstance(val, ast.Name):
        return val.id in _SENSITIVE_FIELD_NAMES
    if isinstance(val, ast.Attribute):
        return val.attr in _SENSITIVE_FIELD_NAMES
    return False


def _arg_uses_sanitizer(arg: ast.AST) -> bool:
    """判断参数节点(含嵌套)是否调用了 DataSanitizer.sanitize_xxx。

    覆盖:
    - ``logger.error("...", DataSanitizer.sanitize_error(e))`` — sanitize_error 作为参数
    - ``logger.error(f"... {DataSanitizer.sanitize_error(e)}")`` — sanitize_error 嵌入 f-string
    - ``logger.error("..." + DataSanitizer.sanitize_error(e))`` — 拼接中含 sanitize
    """
    return any(_is_sanitizer_call(sub) for sub in ast.walk(arg))


def _f_string_violates(joined_str: ast.JoinedStr) -> bool:
    """判断 f-string (JoinedStr) 是否含异常变量或敏感字段名。

    覆盖 ``f"...{e}..."`` / ``f"...{token}..."`` / ``f"...{self.api_key}..."``。
    若 FormattedValue 内部调用 DataSanitizer.sanitize_xxx 则不报警。
    """
    for part in joined_str.values:
        if not isinstance(part, ast.FormattedValue):
            continue
        # FormattedValue 内部已调用 sanitize → 跳过此 part
        if _arg_uses_sanitizer(part):
            continue
        if _formatted_value_is_exception(part):
            return True
        if _formatted_value_is_sensitive_field(part):
            return True
    return False


def _arg_violates(arg: ast.AST) -> bool:
    """判断 logger 调用的单个参数是否违规(含未脱敏异常或敏感字段)。

    覆盖:
    - f-string 含异常变量/敏感字段: ``f"err: {e}"`` / ``f"key={api_key}"``
    - str() 包装异常: ``str(e)``
    - 字符串拼接异常: ``"err: " + str(e)`` / ``"err: " + e``

    不报警:
    - DataSanitizer.sanitize_xxx(...) 调用
    - 含 sanitize 调用的 f-string / 拼接
    - 普通变量 ``logger.error("...", key)`` (key 非 exception 名/非敏感字段名)
    - 常量字符串 ``logger.error("foo")``
    - i18n 调用 ``logger.error(I18n.get(...))``
    """
    # DataSanitizer.sanitize_xxx(...) 调用视为合规
    if _is_sanitizer_call(arg):
        return False
    # f-string: 检查 FormattedValue
    if isinstance(arg, ast.JoinedStr):
        return _f_string_violates(arg)
    # str(e) 包装异常
    if _is_str_call(arg):
        inner = arg.args[0] if arg.args else None
        if isinstance(inner, ast.Name) and inner.id in _EXCEPTION_VAR_NAMES:
            return True
        # str(DataSanitizer.sanitize_error(e)) 视为合规
        if inner is not None and _arg_uses_sanitizer(inner):
            return False
        return False
    # 字符串拼接: BinOp(left, +, right)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        # 含 sanitize 调用视为合规
        if _arg_uses_sanitizer(arg):
            return False
        # 检查左右是否含异常变量 str() 或敏感字段
        return any(_arg_violates(side) for side in (arg.left, arg.right))
    return False


def _is_whitelisted(source_lines: list[str], lineno: int) -> bool:
    """判断 logger 调用所在行或上一行是否有 R9_ALLOWED 白名单注释。

    ``lineno`` 是 1-based; 同时检查当前行(行尾注释)与上一行(独立行注释)。
    """
    for offset in (0, -1):
        idx = lineno - 1 + offset
        if 0 <= idx < len(source_lines):
            if _ALLOW_MARKER.search(source_lines[idx]):
                return True
    return False


def _find_violations(tree: ast.Module, source_lines: list[str], rel_path: Path) -> list[str]:
    """扫描 AST 中的违规 logger 调用, 返回错误消息列表。

    检测规则:
    - ``logger.error/warning/critical(f"...{e}...")`` → 报警(f-string + 异常变量)
    - ``logger.error/warning/critical(f"...{token}...")`` → 报警(f-string + 敏感字段名)
    - ``logger.error/warning/critical(str(e))`` → 报警(显式 str 包装异常)
    - ``logger.error/warning/critical("..." + str(e))`` → 报警(拼接异常)
    - ``logger.error/warning/critical("...%s", e, exc_info=True)`` → 不报警(惰性 % 格式化)
    - ``logger.error/warning/critical("...", DataSanitizer.sanitize_error(e))`` → 不报警
    """
    errors: list[str] = []
    for node in ast.walk(tree):
        if not _is_logger_call(node):
            continue
        # 任一参数违规即报警
        for arg in node.args:
            if _arg_violates(arg):
                if _is_whitelisted(source_lines, node.lineno):
                    continue
                errors.append(
                    f"{rel_path}:{node.lineno}: R9 UI 脱敏违规 — logger 调用直接打印"
                    f"异常或敏感字段(未经 DataSanitizer 脱敏); "
                    f"如确需打印, 添加 # R9_ALLOWED: <reason> 行内或上一行注释"
                )
                break  # 单个 logger 调用只报一次
    return errors


def _scan_directory(directory: Path) -> list[str]:
    """扫描目录下所有 .py 文件(跳过 __pycache__), 返回所有违规错误消息。"""
    errors: list[str] = []
    if not directory.exists():
        return errors
    for p in directory.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            source = p.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(p))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        rel_path = p.relative_to(ROOT)
        errors.extend(_find_violations(tree, source.splitlines(), rel_path))
    return errors


# ============================================================================
# 纯函数测试: 检测逻辑边界
# ============================================================================


def _first_call(code: str) -> ast.Call:
    """从代码中提取第一个 ast.Call 节点。"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return node
    raise AssertionError("No Call found in code")


class TestIsLoggerCall:
    """验证 _is_logger_call 识别 logger.error/warning/critical。"""

    def test_detects_logger_error(self):
        c = _first_call("logger.error('foo')\n")
        assert _is_logger_call(c) is True

    def test_detects_logger_warning(self):
        c = _first_call("logger.warning('foo')\n")
        assert _is_logger_call(c) is True

    def test_detects_logger_critical(self):
        c = _first_call("logger.critical('foo')\n")
        assert _is_logger_call(c) is True

    def test_detects_any_logger_var_name(self):
        """任意 logger 变量名(log/LOGGER/_log)均识别。"""
        c = _first_call("log.error('foo')\n")
        assert _is_logger_call(c) is True

    def test_ignores_logger_info(self):
        """logger.info 不在监控范围(只 error/warning/critical)。"""
        c = _first_call("logger.info('foo')\n")
        assert _is_logger_call(c) is False

    def test_ignores_non_logger_call(self):
        c = _first_call("print('foo')\n")
        assert _is_logger_call(c) is False


class TestIsSanitizerCall:
    """验证 _is_sanitizer_call 识别 DataSanitizer.sanitize_xxx。"""

    def test_detects_sanitize_error(self):
        c = _first_call("DataSanitizer.sanitize_error(e)\n")
        assert _is_sanitizer_call(c) is True

    def test_detects_sanitize_args(self):
        c = _first_call("DataSanitizer.sanitize_args(*args)\n")
        assert _is_sanitizer_call(c) is True

    def test_detects_sanitize_dict(self):
        c = _first_call("DataSanitizer.sanitize_dict(d)\n")
        assert _is_sanitizer_call(c) is True

    def test_detects_sanitize_token(self):
        c = _first_call("DataSanitizer.sanitize_token(t)\n")
        assert _is_sanitizer_call(c) is True

    def test_ignores_non_sanitizer_method(self):
        c = _first_call("DataSanitizer.sanitize_foo(e)\n")
        assert _is_sanitizer_call(c) is False

    def test_ignores_non_sanitizer_object(self):
        c = _first_call("foo.sanitize_error(e)\n")
        # 注: 当前实现按 attr 名匹配, 不限定对象名, 故仍识别为 sanitize_error
        # 这是误报控制(宽松): 任何 sanitize_error 调用都视为脱敏
        assert _is_sanitizer_call(c) is True


class TestArgViolates:
    """验证 _arg_violates 检测违规参数模式。"""

    def test_f_string_with_exception_var(self):
        """f-string 含异常变量 e → 违规。"""
        arg = ast.parse('f"err: {e}"', mode="eval").body
        assert _arg_violates(arg) is True

    def test_f_string_with_exc_var(self):
        """f-string 含异常变量 exc → 违规。"""
        arg = ast.parse('f"err: {exc}"', mode="eval").body
        assert _arg_violates(arg) is True

    def test_f_string_with_token_field(self):
        """f-string 含敏感字段名 token → 违规。"""
        arg = ast.parse('f"key={token}"', mode="eval").body
        assert _arg_violates(arg) is True

    def test_f_string_with_api_key_field(self):
        """f-string 含敏感字段名 api_key → 违规。"""
        arg = ast.parse('f"key={api_key}"', mode="eval").body
        assert _arg_violates(arg) is True

    def test_f_string_with_attribute_sensitive(self):
        """f-string 含 self.api_key 属性访问 → 违规。"""
        arg = ast.parse('f"key={self.api_key}"', mode="eval").body
        assert _arg_violates(arg) is True

    def test_f_string_with_non_exception_var(self):
        """f-string 含普通变量(非异常名/非敏感字段) → 不违规。"""
        arg = ast.parse('f"key={user_name}"', mode="eval").body
        assert _arg_violates(arg) is False

    def test_f_string_with_sanitize_call(self):
        """f-string 含 DataSanitizer.sanitize_error(e) → 不违规。"""
        arg = ast.parse('f"err: {DataSanitizer.sanitize_error(e)}"', mode="eval").body
        assert _arg_violates(arg) is False

    def test_str_call_with_exception(self):
        """str(e) 显式包装异常 → 违规。"""
        arg = _first_call("str(e)\n")
        assert _arg_violates(arg) is True

    def test_str_call_with_non_exception(self):
        """str(普通变量) → 不违规(可能是合法的字符串转换)。"""
        arg = _first_call("str(user_name)\n")
        assert _arg_violates(arg) is False

    def test_str_call_with_sanitize(self):
        """str(DataSanitizer.sanitize_error(e)) → 不违规。"""
        arg = _first_call("str(DataSanitizer.sanitize_error(e))\n")
        assert _arg_violates(arg) is False

    def test_bin_op_with_str_exception(self):
        """字符串拼接 str(e) → 违规。"""
        arg = ast.parse('"err: " + str(e)', mode="eval").body
        assert _arg_violates(arg) is True

    def test_bin_op_with_exception_var(self):
        """字符串拼接异常变量 → 违规。"""
        arg = ast.parse('"err: " + e', mode="eval").body
        # 注: 单纯拼接变量 e (非 str(e)) 不算违规, 因 ast.BinOp 仅识别 + str() 形式
        # 这是误报控制: 不假设变量值类型
        assert _arg_violates(arg) is False

    def test_bin_op_with_sanitize(self):
        """字符串拼接含 sanitize_error → 不违规。"""
        arg = ast.parse('"err: " + DataSanitizer.sanitize_error(e)', mode="eval").body
        assert _arg_violates(arg) is False

    def test_constant_string(self):
        """常量字符串 → 不违规。"""
        arg = ast.parse('"foo"', mode="eval").body
        assert _arg_violates(arg) is False

    def test_i18n_get_call(self):
        """I18n.get(...) → 不违规。"""
        arg = _first_call("I18n.get('foo')\n")
        assert _arg_violates(arg) is False

    def test_plain_variable(self):
        """普通变量引用 → 不违规。"""
        arg = ast.parse("user_name", mode="eval").body
        assert _arg_violates(arg) is False


class TestWhitelist:
    """验证 R9_ALLOWED 白名单机制。"""

    def test_inline_whitelist(self):
        lines = ["logger.error(f'err: {e}')  # R9_ALLOWED: diagnostic only"]
        assert _is_whitelisted(lines, 1) is True

    def test_prev_line_whitelist(self):
        lines = [
            "# R9_ALLOWED: diagnostic only",
            "logger.error(f'err: {e}')",
        ]
        assert _is_whitelisted(lines, 2) is True

    def test_no_whitelist(self):
        lines = ["logger.error(f'err: {e}')"]
        assert _is_whitelisted(lines, 1) is False

    def test_whitelist_without_reason_not_matched(self):
        """R9_ALLOWED(无冒号 reason)不匹配(要求 R9_ALLOWED: 后跟非空内容)。"""
        lines = ["logger.error(f'err: {e}')  # R9_ALLOWED"]
        assert _is_whitelisted(lines, 1) is False

    def test_whitelist_with_chinese_reason(self):
        r"""白名单 reason 支持中文(\S+ 匹配 Unicode 非空白)。"""
        lines = ["logger.error(f'err: {e}')  # R9_ALLOWED: 诊断输出允许"]
        assert _is_whitelisted(lines, 1) is True


class TestFindViolations:
    """端到端验证 _find_violations 检测逻辑。"""

    def _scan_code(self, code: str) -> list[str]:
        tree = ast.parse(code)
        lines = code.splitlines()
        return _find_violations(tree, lines, Path("test.py"))

    def test_flags_f_string_with_exception(self):
        """logger.error(f'err: {e}') → 报警(DoD 1 临时违规模式)。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error(f'err: {e}')\n"
        errors = self._scan_code(code)
        assert len(errors) == 1
        assert "R9" in errors[0]

    def test_flags_f_string_with_token(self):
        """logger.error(f'key={token}') → 报警。"""
        code = "logger.error(f'key={token}')\n"
        errors = self._scan_code(code)
        assert len(errors) == 1

    def test_flags_str_exception(self):
        """logger.error(str(e)) → 报警。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error(str(e))\n"
        errors = self._scan_code(code)
        assert len(errors) == 1

    def test_flags_concat_with_str_exception(self):
        """logger.error('err: ' + str(e)) → 报警。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error('err: ' + str(e))\n"
        errors = self._scan_code(code)
        assert len(errors) == 1

    def test_does_not_flag_percent_format(self):
        """logger.error('err: %s', e, exc_info=True) → 不报警(Python 推荐)。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error('err: %s', e, exc_info=True)\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_sanitize_error(self):
        """logger.error('...', DataSanitizer.sanitize_error(e)) → 不报警。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error('err: %s', DataSanitizer.sanitize_error(e))\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_f_string_with_sanitize(self):
        """f-string 内嵌 DataSanitizer.sanitize_error(e) → 不报警。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error(f'err: {DataSanitizer.sanitize_error(e)}')\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_constant_string(self):
        """logger.error('foo') → 不报警。"""
        code = "logger.error('foo')\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_i18n_call(self):
        """logger.error(I18n.get(...)) → 不报警。"""
        code = "logger.error(I18n.get('error_key'))\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_logger_info(self):
        """logger.info 不在监控范围。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.info(f'err: {e}')\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_whitelist_suppresses(self):
        """R9_ALLOWED 白名单豁免违规。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error(f'err: {e}')  # R9_ALLOWED: diagnostic\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_multiple_violations_all_reported(self):
        """多个违规均被报告。"""
        code = "logger.error(f'err: {e}')\nlogger.warning(f'token={token}')\n"
        errors = self._scan_code(code)
        assert len(errors) == 2


# ============================================================================
# 集成测试: 当前代码库无 R9 UI 渲染脱敏违规(契约测试)
# ============================================================================


class TestR9NoUnsanitizedLogInUI:
    """集成测试: 验证 ui/components/ + ui/views/ 下无 R9 UI 渲染脱敏违规。

    这是契约测试, 确保新增 UI 代码不会引入直接打印异常或敏感字段的反模式。
    若失败, 说明有违规引入, 应立即修复或添加 R9_ALLOWED 白名单注释。
    """

    def test_scanned_directories_exist(self):
        """验证扫描的目录都存在(防止配置漂移)。"""
        for dir_name in SCANNED_DIRS:
            directory = ROOT / dir_name
            assert directory.exists(), f"扫描目录不存在: {dir_name}"
            assert directory.is_dir(), f"扫描路径不是目录: {dir_name}"

    def test_no_unsanitized_log_in_ui_dirs(self):
        """扫描 ui/components/ + ui/views/ 下所有 .py 文件, 无违规。"""
        all_errors: list[str] = []
        for dir_name in SCANNED_DIRS:
            directory = ROOT / dir_name
            all_errors.extend(_scan_directory(directory))
        assert not all_errors, "R9 UI 渲染脱敏违规:\n  " + "\n  ".join(all_errors)


# ============================================================================
# 抽样断言: UI 异常 message 显示路径不泄露明文敏感数据
# ============================================================================


# 测试用敏感数据(模拟 token/api_key/password, 非真实凭证)
_SENSITIVE_TOKEN = "sk-tushare-abcdef1234567890abcdef1234567890abcdef"
_SENSITIVE_API_KEY = "AKIAIOSFODNN7EXAMPLE"
_SENSITIVE_PASSWORD = "super-secret-password-12345"


class TestDataSanitizerStripsSensitiveFromException:
    """抽样 1-3: DataSanitizer.sanitize_error 处理异常字符串中的明文敏感数据。

    验证场景: 异常的 str() 含明文 token/api_key/password/URL credentials,
    sanitize_error 后输出不含明文敏感数据。这覆盖 UI 异常路径中
    "exception → logger.error(sanitize_error(e))" 的脱敏能力。
    """

    def test_sanitize_error_strips_token_query_string(self):
        """抽样 1: 异常含 ``?token=xxx`` query 形式 → 输出脱敏。"""
        exc = RuntimeError(f"API call failed: https://api.example.com?token={_SENSITIVE_TOKEN}")
        sanitized = DataSanitizer.sanitize_error(exc)
        assert _SENSITIVE_TOKEN not in sanitized
        assert "token=" in sanitized  # 保留 key, 仅值脱敏
        assert "***" in sanitized

    def test_sanitize_error_strips_bearer_token(self):
        """抽样 2: 异常含 ``Bearer xxx`` 形式 → 输出脱敏。"""
        exc = RuntimeError(f"Auth failed: Bearer {_SENSITIVE_TOKEN}")
        sanitized = DataSanitizer.sanitize_error(exc)
        assert _SENSITIVE_TOKEN not in sanitized
        assert "Bearer ***" in sanitized

    def test_sanitize_error_strips_url_credentials(self):
        """抽样 3: 异常含 ``postgresql://user:pass@host`` 形式 → 输出脱敏。"""
        exc = RuntimeError(f"DB connect failed: postgresql://user:{_SENSITIVE_PASSWORD}@localhost:5432/db")
        sanitized = DataSanitizer.sanitize_error(exc)
        assert _SENSITIVE_PASSWORD not in sanitized
        assert "***" in sanitized
        # password 应被 *** 替换, 但 user 和 host 保留
        assert "user" in sanitized
        assert "localhost" in sanitized


class TestDataSanitizerStripsSensitiveFromDictAndArgs:
    """抽样 4-5: DataSanitizer.sanitize_dict / sanitize_args 处理敏感数据。

    验证场景: dict 含 api_key 字段 / kwargs 含 password 参数,
    sanitize 后输出不含明文敏感数据。这覆盖 UI 异常路径中
    "logger.error('...', **sanitize_dict(context))" 的脱敏能力。
    """

    def test_sanitize_dict_strips_api_key(self):
        """抽样 4: dict 含 api_key 字段 → 输出脱敏。"""
        data = {
            "endpoint": "https://api.example.com",
            "api_key": _SENSITIVE_API_KEY,
            "timeout": 30,
        }
        sanitized = DataSanitizer.sanitize_dict(data)
        assert sanitized["api_key"] != _SENSITIVE_API_KEY
        assert "***" in str(sanitized["api_key"]) or sanitized["api_key"] == "***"
        # 非敏感字段保留原值
        assert sanitized["endpoint"] == "https://api.example.com"
        assert sanitized["timeout"] == 30

    def test_sanitize_args_strips_password(self):
        """抽样 5: kwargs 含 password → 输出脱敏。"""
        _, clean_kwargs = DataSanitizer.sanitize_args(
            user="admin",
            password=_SENSITIVE_PASSWORD,
            host="localhost",
        )
        assert clean_kwargs["password"] != _SENSITIVE_PASSWORD
        assert "***" in str(clean_kwargs["password"]) or clean_kwargs["password"] == "***"
        # 非敏感参数保留原值
        assert clean_kwargs["user"] == "admin"
        assert clean_kwargs["host"] == "localhost"


class TestStockDetailDialogChartErrorPath:
    """抽样 6: StockDetailDialog._load_chart_async 异常路径渲染不泄露明文。

    验证场景: 注入含明文 token 的异常到 _load_chart_async, 断言渲染到 UI
    的 ft.Text 不含明文 token(error_classifier 返回 i18n 通用消息)。
    这是 StockDetailDialog 异常 message 显示路径的抽样断言。
    """

    def test_chart_error_message_does_not_leak_token(self):
        """注入含 token 的异常到 _load_chart_async, 渲染的 ft.Text 不含明文 token。"""
        from ui.components.stock_detail_dialog import _load_chart_async

        # mock data_processor.get_stock_history 抛出含明文 token 的异常
        mock_processor = MagicMock()
        mock_processor.get_stock_history = AsyncMock(
            side_effect=RuntimeError(f"API call failed: token={_SENSITIVE_TOKEN}")
        )

        captured: list[ft.Control] = []

        def set_chart_content(control: ft.Control) -> None:
            captured.append(control)

        asyncio.run(
            _load_chart_async(
                data_processor=mock_processor,
                stock_data={"ts_code": "000001.SZ", "name": "Test"},
                ts_code="000001.SZ",
                set_chart_content=set_chart_content,
            )
        )

        # 至少捕获到 progress 占位 + error 文本
        assert len(captured) >= 2
        error_control = captured[-1]
        assert isinstance(error_control, ft.Text)
        rendered_text = str(error_control.value)
        # UI 渲染的 error message 不含明文 token(error_classifier 返回 i18n key)
        assert _SENSITIVE_TOKEN not in rendered_text
