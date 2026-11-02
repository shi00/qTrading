"""R2 红线守护测试：检测 asyncio.CancelledError 吞没反模式。

依据 CLAUDE.md §3.1 R2 红线（异常吞没）：吞没 `asyncio.CancelledError` 必须配合
`raise` 以保障优雅停机。本测试通过 AST 扫描 `core/data/services/strategies/utils/`
下所有 .py 文件，检测以下"broad except + 无 raise"反模式：

1. **静默吞没**：`except Exception/BaseException: pass`（块体仅含 pass）
2. **记录但吞没**：`except BaseException` 块体内调用
   `logger.exception()` 或 `logger.error(..., exc_info=True)` 但无 `raise`
   （仅对 `BaseException` 应用，因其实际捕获 CancelledError）

误报控制（基于 Python 3.8+ 语义）：
- Python 3.8+ 中 `asyncio.CancelledError` 继承 `BaseException`，`except Exception`
  不会捕获 `CancelledError`，故 `except Exception` 的 logger 反模式不报警
- `except BaseException` 实际捕获 `CancelledError`，故其 logger 反模式报警
- `except Exception: pass` 仍报警（静默吞没是通用代码质量问题，DoD #1 要求）
- **同 try 兄弟守卫豁免（按 handler 顺序/可达性判定）**：仅当首个实际捕获
  `CancelledError` 的 handler 是 `except asyncio.CancelledError: ... raise`（含元组形式
  `except (CancelledError, X): ... raise`）时，broad except 才豁免；若其前面存在
  `except BaseException`、裸 `except:` 或 `except (BaseException, X)` 等同样捕获
  `CancelledError` 的 handler，则守卫不可达，不再豁免（前置 handler 会先吞没取消）。
  注意：Python 3.8+ 中 `except Exception` 不捕获 `CancelledError`，因此位于守卫前的
  `except Exception` 不构成遮蔽，仍按兄弟守卫豁免。
- 元组形式：`except (Exception, X)` / `except (BaseException, X)` /
  `except (CancelledError, X)` 按元素扁平识别；`from asyncio import CancelledError as X`
  的别名导入按模块级导入解析
- 间接调用（如 `_log = logger.error; _log(..., exc_info=True)`）不报警，因不能
  静态证明其指向 logger.error
- 白名单：行内或上一行 `# R2_ALLOWED: <reason>` 注释可豁免（如顶层 main.py 入口）

技术背景：Python 3.8+ 中 `asyncio.CancelledError` 继承 `BaseException`，
`except Exception` 不会捕获 `CancelledError`，但 `except BaseException` 会。
本扫描作为防御性编程守护，覆盖 broad except 的单类型与元组形式。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent

# 受扫描的顶层目录（CLAUDE.md §4.1 分层架构核心层，不含 app/ui/tests）
SCANNED_DIRS = ("core", "data", "services", "strategies", "utils")

# 白名单注释模式：# R2_ALLOWED: <reason>
_ALLOW_MARKER = re.compile(r"R2_ALLOWED:\s*\S+")

# 标记为 broad except 的类型名（Exception / BaseException）
_BROAD_EXCEPTION_NAMES = frozenset({"Exception", "BaseException"})

# 实际捕获 asyncio.CancelledError 的类型名（Python 3.8+ 中 CancelledError 继承 BaseException）
_CATCHES_CANCELLED_ERROR_NAMES = frozenset({"BaseException"})

# 默认的 CancelledError 类型名（asyncio.CancelledError 的 Attribute.attr 或直接导入的 Name.id）
_CANCELLED_ERROR_NAMES = frozenset({"CancelledError"})


# ============================================================================
# 检测纯函数
# ============================================================================


def _except_type_names(handler: ast.ExceptHandler) -> tuple[str | None, ...]:
    """提取 except handler 的全部类型名（元组扁平化）；裸 except 返回 (None,)。"""
    if handler.type is None:
        return (None,)
    collected: list[str] = []
    _collect_except_type_names(handler.type, collected)
    return tuple(collected)


def _collect_except_type_names(node: ast.expr, collected: list[str]) -> None:
    """递归收集 Name / Attribute / Tuple 中的类型名。"""
    if isinstance(node, ast.Name):
        collected.append(node.id)
    elif isinstance(node, ast.Attribute):
        collected.append(node.attr)
    elif isinstance(node, ast.Tuple):
        for element in node.elts:
            _collect_except_type_names(element, collected)


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """判断 except handler 是否捕获 Exception 或 BaseException（含元组元素）。

    覆盖语法形式：
    - `except Exception:` → ast.Name(id="Exception")
    - `except builtins.Exception:` → ast.Attribute(attr="Exception")
    - `except (Exception, ValueError):` → ast.Tuple 元素含 broad 类型
    """
    return any(name in _BROAD_EXCEPTION_NAMES for name in _except_type_names(handler))


def _catches_cancelled_error(handler: ast.ExceptHandler) -> bool:
    """判断 except handler 是否实际捕获 asyncio.CancelledError（含元组元素）。

    Python 3.8+ 中 `asyncio.CancelledError` 继承 `BaseException`：
    - `except Exception` 不捕获 CancelledError
    - `except BaseException` / `except (BaseException, X)` 捕获 CancelledError
    """
    return any(name in _CATCHES_CANCELLED_ERROR_NAMES for name in _except_type_names(handler))


def _is_cancelled_error_handler(
    handler: ast.ExceptHandler,
    cancelled_names: frozenset[str] = _CANCELLED_ERROR_NAMES,
) -> bool:
    """判断 except handler 是否捕获 asyncio.CancelledError（含元组与导入别名）。"""
    return any(name in cancelled_names for name in _except_type_names(handler))


def _collect_cancelled_error_aliases(tree: ast.Module) -> frozenset[str]:
    """收集模块级 CancelledError 名称，含 `from asyncio import CancelledError as X` 别名。"""
    names = set(_CANCELLED_ERROR_NAMES)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module != "asyncio" and not node.module.startswith("asyncio."):
            continue
        for alias in node.names:
            if alias.name == "CancelledError":
                names.add(alias.asname or alias.name)
    return frozenset(names)


def _handler_shadows_cancelled_error(
    handler: ast.ExceptHandler,
    cancelled_names: frozenset[str] = _CANCELLED_ERROR_NAMES,
) -> bool:
    """判断该 handler 是否会先于后续 handler 捕获 CancelledError。

    - 裸 `except:` 捕获所有异常；
    - `except BaseException`（含元组元素）捕获 CancelledError；
    - `except CancelledError`（含别名/元组元素）先捕获，使其后守卫不可达。

    Python 3.8+ 中 `except Exception` 不捕获 CancelledError，故不构成遮蔽。
    """
    type_names = _except_type_names(handler)
    if type_names == (None,):
        return True
    return any(name in _CATCHES_CANCELLED_ERROR_NAMES or name in cancelled_names for name in type_names)


def _try_has_cancelled_error_guard(
    try_node: ast.Try,
    cancelled_names: frozenset[str] = _CANCELLED_ERROR_NAMES,
) -> bool:
    """判断 try 语句的 CancelledError 守卫是否可达且配 raise。

    R2 标准合规模式：
        try:
            ...
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(..., exc_info=True)  # 无 raise 也合规

    顺序/可达性规则：按源码顺序遍历 handler，首个实际捕获 CancelledError 的 handler
    决定结果，其后 handler 均不可达：

    - 首个捕获者是 `except CancelledError: ... raise`（含元组/别名）→ True
    - 首个捕获者是 `except BaseException`、裸 `except:` 或
      `except CancelledError`（无 raise）→ False
    """
    for handler in try_node.handlers:
        if _is_cancelled_error_handler(handler, cancelled_names):
            return _has_raise(handler.body)
        if _handler_shadows_cancelled_error(handler, cancelled_names):
            return False
    return False


def _has_raise(body: list[ast.stmt]) -> bool:
    """判断 except 块体（含嵌套 if/try/with）中是否存在 raise 语句。"""
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Raise):
                return True
    return False


def _is_logger_exception_call(node: ast.AST) -> bool:
    """判断是否为 `<logger>.exception(...)` 调用（任意 logger 变量名）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "exception"


def _is_logger_error_with_exc_info(node: ast.AST) -> bool:
    """判断是否为 `<logger>.error(..., exc_info=True)` 调用。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "error":
        return False
    for kw in node.keywords:
        if kw.arg == "exc_info" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _is_pass_only_body(body: list[ast.stmt]) -> bool:
    """判断 except 块体是否仅含 pass 语句（允许附带 docstring 表达式）。"""
    real_stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not real_stmts:
        return False
    return all(isinstance(s, ast.Pass) for s in real_stmts)


def _has_logger_swallow(body: list[ast.stmt]) -> bool:
    """判断 except 块体（含嵌套）是否调用 logger.exception() 或 logger.error(exc_info=True)。"""
    for stmt in body:
        for sub in ast.walk(stmt):
            if _is_logger_exception_call(sub) or _is_logger_error_with_exc_info(sub):
                return True
    return False


def _is_whitelisted(source_lines: list[str], lineno: int) -> bool:
    """判断 except 起始行或上一行是否有 R2_ALLOWED 白名单注释。

    `lineno` 是 1-based；同时检查当前行（行尾注释）与上一行（独立行注释）。
    """
    for offset in (0, -1):
        idx = lineno - 1 + offset
        if 0 <= idx < len(source_lines):
            if _ALLOW_MARKER.search(source_lines[idx]):
                return True
    return False


def _find_violations(tree: ast.Module, source_lines: list[str], rel_path: Path) -> list[str]:
    """扫描 AST 中的违规 except 块，返回错误消息列表。

    遍历 `ast.Try` 节点（而非 `ast.ExceptHandler`），以便访问同一 try 的兄弟 handlers，
    检测是否存在可达的 `except asyncio.CancelledError: raise` 兄弟守卫。

    检测规则（基于 Python 3.8+ 语义）：
    - `except Exception: pass`（pass-only，无 raise）→ 报警（静默吞没，DoD #1）
    - `except BaseException: pass`（pass-only，无 raise）→ 报警（实际吞没 CancelledError）
    - `except BaseException: logger.exception()/logger.error(exc_info=True)`（无 raise）→ 报警
    - `except Exception: logger.exception()/logger.error(exc_info=True)`（无 raise）→ 不报警
      （Python 3.8+ 中 except Exception 不捕获 CancelledError）
    - 兄弟守卫仅在**可达**时豁免：若 `except BaseException`、裸 `except:` 或
      `except CancelledError`（无 raise）位于守卫之前，守卫不可达，前述 broad handler
      按原规则报警
    """
    errors: list[str] = []
    cancelled_names = _collect_cancelled_error_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        has_cancel_guard = _try_has_cancelled_error_guard(node, cancelled_names)
        for handler in node.handlers:
            if not _is_broad_except(handler):
                continue
            if has_cancel_guard:
                continue  # 兄弟 handler 已守卫 CancelledError
            if _has_raise(handler.body):
                continue
            is_pass_only = _is_pass_only_body(handler.body)
            # logger 反模式仅对实际捕获 CancelledError 的 BaseException 报警
            has_logger_swallow = _catches_cancelled_error(handler) and _has_logger_swallow(handler.body)
            if not is_pass_only and not has_logger_swallow:
                continue
            if _is_whitelisted(source_lines, handler.lineno):
                continue
            if is_pass_only:
                pattern = "except 块体仅含 pass（静默吞没 CancelledError 风险）"
            else:
                pattern = (
                    "except BaseException 块调用 logger.exception()/logger.error(exc_info=True) 但未 raise"
                    "（实际吞没 CancelledError）"
                )
            errors.append(
                f"{rel_path}:{handler.lineno}: R2 CancelledError 吞没风险 — {pattern}；"
                f"如确需吞没，添加 # R2_ALLOWED: <reason> 行内或上一行注释"
            )
    return errors


def _scan_directory(directory: Path) -> list[str]:
    """扫描目录下所有 .py 文件（跳过 __pycache__），返回所有违规错误消息。"""
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
# 纯函数测试：检测逻辑边界
# ============================================================================


def _first_handler(code: str) -> ast.ExceptHandler:
    """从代码中提取第一个 ExceptHandler 节点。"""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            return node
    raise AssertionError("No ExceptHandler found in code")


class TestIsBroadExcept:
    """验证 _is_broad_except 正确识别 except Exception / except BaseException。"""

    def test_detects_except_exception(self):
        h = _first_handler("try:\n    pass\nexcept Exception:\n    pass\n")
        assert _is_broad_except(h) is True

    def test_detects_except_base_exception(self):
        h = _first_handler("try:\n    pass\nexcept BaseException:\n    pass\n")
        assert _is_broad_except(h) is True

    def test_detects_builtins_exception(self):
        h = _first_handler("try:\n    pass\nexcept builtins.Exception:\n    pass\n")
        assert _is_broad_except(h) is True

    def test_detects_builtins_base_exception(self):
        h = _first_handler("try:\n    pass\nexcept builtins.BaseException:\n    pass\n")
        assert _is_broad_except(h) is True

    def test_ignores_specific_exception(self):
        h = _first_handler("try:\n    pass\nexcept ValueError:\n    pass\n")
        assert _is_broad_except(h) is False

    def test_ignores_cancelled_error(self):
        """asyncio.CancelledError 是 narrow except，不算 broad。"""
        h = _first_handler("try:\n    pass\nexcept asyncio.CancelledError:\n    raise\n")
        assert _is_broad_except(h) is False

    def test_detects_tuple_with_broad_element(self):
        """元组形式 except (Exception, ValueError) 含 broad 元素，算 broad。"""
        h = _first_handler("try:\n    pass\nexcept (Exception, ValueError):\n    pass\n")
        assert _is_broad_except(h) is True

    def test_detects_tuple_with_base_exception(self):
        h = _first_handler("try:\n    pass\nexcept (ValueError, BaseException):\n    pass\n")
        assert _is_broad_except(h) is True

    def test_ignores_tuple_with_specific_only(self):
        """元组形式 except (ValueError, KeyError) 不算 broad。"""
        h = _first_handler("try:\n    pass\nexcept (ValueError, KeyError):\n    pass\n")
        assert _is_broad_except(h) is False

    def test_ignores_bare_except(self):
        """裸 except 无类型，不算 broad（由其他规则管）。"""
        h = _first_handler("try:\n    pass\nexcept:\n    pass\n")
        assert _is_broad_except(h) is False


class TestIsCancelledErrorHandler:
    """验证 _is_cancelled_error_handler 识别 asyncio.CancelledError。"""

    def test_detects_asyncio_cancelled_error(self):
        h = _first_handler("try:\n    pass\nexcept asyncio.CancelledError:\n    raise\n")
        assert _is_cancelled_error_handler(h) is True

    def test_detects_direct_import_cancelled_error(self):
        """from asyncio import CancelledError 后 except CancelledError 也识别。"""
        h = _first_handler("try:\n    pass\nexcept CancelledError:\n    raise\n")
        assert _is_cancelled_error_handler(h) is True

    def test_detects_tuple_with_cancelled_error(self):
        """元组形式 except (asyncio.CancelledError, ValueError) 也识别。"""
        h = _first_handler("try:\n    pass\nexcept (asyncio.CancelledError, ValueError):\n    raise\n")
        assert _is_cancelled_error_handler(h) is True

    def test_ignores_exception(self):
        h = _first_handler("try:\n    pass\nexcept Exception:\n    pass\n")
        assert _is_cancelled_error_handler(h) is False

    def test_ignores_specific_exception(self):
        h = _first_handler("try:\n    pass\nexcept ValueError:\n    pass\n")
        assert _is_cancelled_error_handler(h) is False


class TestTryHasCancelledErrorGuard:
    """验证 _try_has_cancelled_error_guard 检测兄弟 CancelledError 守卫。"""

    def _try_node(self, code: str) -> ast.Try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                return node
        raise AssertionError("No Try found")

    def test_detects_sibling_cancelled_error_raise(self):
        code = "try:\n    foo()\nexcept asyncio.CancelledError:\n    raise\nexcept Exception:\n    pass\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is True

    def test_detects_sibling_cancelled_error_raise_in_body(self):
        """raise 不必是裸 raise，可以是 raise 在嵌套语句中。"""
        code = (
            "try:\n    foo()\n"
            "except asyncio.CancelledError:\n    logger.warning('x')\n    raise\n"
            "except Exception:\n    pass\n"
        )
        assert _try_has_cancelled_error_guard(self._try_node(code)) is True

    def test_no_sibling_cancelled_error(self):
        code = "try:\n    foo()\nexcept ValueError:\n    pass\nexcept Exception:\n    pass\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False

    def test_sibling_cancelled_error_without_raise_not_guard(self):
        """`except asyncio.CancelledError: pass`（无 raise）不算守卫。"""
        code = "try:\n    foo()\nexcept asyncio.CancelledError:\n    pass\nexcept Exception:\n    pass\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False

    def test_preceding_base_exception_shadows_guard(self):
        """`except BaseException` 位于取消守卫之前 → 守卫不可达。"""
        code = "try:\n    foo()\nexcept BaseException:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False

    def test_preceding_bare_except_shadows_guard(self):
        """裸 except 捕获所有异常（含 CancelledError），后续守卫不可达。"""
        code = "try:\n    foo()\nexcept:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False

    def test_preceding_cancelled_error_without_raise_shadows_guard(self):
        """前一个 `except CancelledError: pass` 先捕获，后续守卫不可达。"""
        code = "try:\n    foo()\nexcept asyncio.CancelledError:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False

    def test_preceding_tuple_with_base_exception_shadows_guard(self):
        """元组含 BaseException 同样遮蔽后续守卫。"""
        code = (
            "try:\n    foo()\n"
            "except (ValueError, BaseException):\n    pass\n"
            "except asyncio.CancelledError:\n    raise\n"
        )
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False

    def test_preceding_specific_exception_keeps_guard_reachable(self):
        """`except ValueError` 不捕获 CancelledError，守卫仍可达。"""
        code = "try:\n    foo()\nexcept ValueError:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is True

    def test_tuple_cancelled_error_guard_recognized(self):
        """元组形式的 CancelledError 守卫也识别为有效守卫。"""
        code = "try:\n    foo()\nexcept (asyncio.CancelledError, ValueError):\n    raise\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is True

    def test_no_handlers(self):
        """try 无 except handler 不算守卫（极端边界）。"""
        code = "try:\n    foo()\nfinally:\n    cleanup()\n"
        assert _try_has_cancelled_error_guard(self._try_node(code)) is False


class TestHasRaise:
    """验证 _has_raise 检测 raise 语句（含嵌套）。"""

    def test_direct_raise(self):
        body = ast.parse("raise").body
        assert _has_raise(body) is True

    def test_raise_in_if(self):
        body = ast.parse("if cond:\n    raise\n").body
        assert _has_raise(body) is True

    def test_raise_in_nested_try(self):
        body = ast.parse("try:\n    foo()\nexcept ValueError:\n    raise\n").body
        assert _has_raise(body) is True

    def test_raise_with_value(self):
        body = ast.parse("raise RuntimeError('x')\n").body
        assert _has_raise(body) is True

    def test_no_raise(self):
        body = ast.parse("logger.error('foo')\nreturn 1\n").body
        assert _has_raise(body) is False

    def test_empty_body_no_raise(self):
        body: list[ast.stmt] = []
        assert _has_raise(body) is False


class TestLoggerSwallowDetection:
    """验证 logger.exception() / logger.error(exc_info=True) 检测。"""

    def test_logger_exception_detected(self):
        body = ast.parse("logger.exception('foo')\n").body
        assert _has_logger_swallow(body) is True

    def test_logger_exception_any_var_name(self):
        """任意 logger 变量名（log.exception / LOGGER.exception）均识别。"""
        body = ast.parse("log.exception('foo')\n").body
        assert _has_logger_swallow(body) is True

    def test_logger_error_with_exc_info_true_detected(self):
        body = ast.parse("logger.error('foo', exc_info=True)\n").body
        assert _has_logger_swallow(body) is True

    def test_logger_error_without_exc_info_not_detected(self):
        body = ast.parse("logger.error('foo')\n").body
        assert _has_logger_swallow(body) is False

    def test_logger_error_with_exc_info_false_not_detected(self):
        body = ast.parse("logger.error('foo', exc_info=False)\n").body
        assert _has_logger_swallow(body) is False

    def test_logger_error_with_exc_info_variable_not_detected(self):
        """exc_info=<variable> 不识别（不能静态证明为 True）。"""
        body = ast.parse("logger.error('foo', exc_info=flag)\n").body
        assert _has_logger_swallow(body) is False

    def test_logger_warning_with_exc_info_not_detected(self):
        """logger.warning(..., exc_info=True) 不识别（仅 error/exception 触发）。"""
        body = ast.parse("logger.warning('foo', exc_info=True)\n").body
        assert _has_logger_swallow(body) is False

    def test_logger_critical_with_exc_info_not_detected(self):
        """logger.critical(..., exc_info=True) 不识别（仅 error/exception 触发）。"""
        body = ast.parse("logger.critical('foo', exc_info=True)\n").body
        assert _has_logger_swallow(body) is False

    def test_indirect_log_call_not_detected(self):
        """`_log = logger.error; _log(...)` 不识别（不能静态证明指向 logger.error）。

        这是误报控制的关键：现有代码常用 _log = logger.error/warning/critical 间接调用。
        """
        body = ast.parse("_log('foo', exc_info=True)\n").body
        assert _has_logger_swallow(body) is False

    def test_logger_exception_in_nested_call_detected(self):
        """嵌套调用中的 logger.exception() 也识别（如 if 分支内）。"""
        body = ast.parse("if cond:\n    logger.exception('foo')\n").body
        assert _has_logger_swallow(body) is True


class TestIsPassOnlyBody:
    """验证 _is_pass_only_body 检测纯 pass 体。"""

    def test_only_pass(self):
        body = ast.parse("pass\n").body
        assert _is_pass_only_body(body) is True

    def test_multiple_pass(self):
        body = ast.parse("pass\npass\n").body
        assert _is_pass_only_body(body) is True

    def test_pass_with_docstring(self):
        body = ast.parse("'docstring'\npass\n").body
        assert _is_pass_only_body(body) is True

    def test_pass_with_other_stmt(self):
        body = ast.parse("pass\nx = 1\n").body
        assert _is_pass_only_body(body) is False

    def test_only_docstring_not_pass_only(self):
        """仅有 docstring 不算 pass-only（不太可能在 except 中出现，但稳妥处理）。"""
        body = ast.parse("'docstring'\n").body
        assert _is_pass_only_body(body) is False

    def test_empty_body_not_pass_only(self):
        body: list[ast.stmt] = []
        assert _is_pass_only_body(body) is False


class TestWhitelist:
    """验证 # R2_ALLOWED: <reason> 白名单。"""

    def test_inline_whitelist(self):
        lines = ["try:", "    pass", "except Exception:  # R2_ALLOWED: top-level guard", "    pass"]
        assert _is_whitelisted(lines, 3) is True

    def test_prev_line_whitelist(self):
        lines = [
            "try:",
            "    pass",
            "# R2_ALLOWED: top-level guard",
            "except Exception:",
            "    pass",
        ]
        assert _is_whitelisted(lines, 4) is True

    def test_no_whitelist(self):
        lines = ["try:", "    pass", "except Exception:", "    pass"]
        assert _is_whitelisted(lines, 3) is False

    def test_whitelist_without_reason_not_matched(self):
        """`# R2_ALLOWED`（无冒号 reason）不匹配（要求 R2_ALLOWED: 后跟非空内容）。"""
        lines = ["except Exception:  # R2_ALLOWED", "    pass"]
        assert _is_whitelisted(lines, 1) is False

    def test_whitelist_with_chinese_reason(self):
        r"""白名单 reason 支持中文（\S+ 匹配 Unicode 非空白）。"""
        lines = ["except Exception:  # R2_ALLOWED: 顶层入口允许吞没", "    pass"]
        assert _is_whitelisted(lines, 1) is True


class TestFindViolations:
    """端到端验证 _find_violations 检测逻辑。"""

    def _scan_code(self, code: str) -> list[str]:
        tree = ast.parse(code)
        lines = code.splitlines()
        return _find_violations(tree, lines, Path("test.py"))

    def test_flags_pass_only_swallow(self):
        code = "try:\n    foo()\nexcept Exception:\n    pass\n"
        errors = self._scan_code(code)
        assert len(errors) == 1
        assert "R2" in errors[0]
        assert "pass" in errors[0]

    def test_flags_base_exception_pass_only(self):
        code = "try:\n    foo()\nexcept BaseException:\n    pass\n"
        errors = self._scan_code(code)
        assert len(errors) == 1

    def test_flags_logger_exception_without_raise_for_base_exception(self):
        """`except BaseException: logger.exception()` 无 raise → 报警（实际吞没 CancelledError）。"""
        code = "try:\n    foo()\nexcept BaseException:\n    logger.exception('foo')\n"
        errors = self._scan_code(code)
        assert len(errors) == 1
        assert "logger.exception" in errors[0]

    def test_flags_logger_error_exc_info_without_raise_for_base_exception(self):
        """`except BaseException: logger.error(exc_info=True)` 无 raise → 报警。"""
        code = "try:\n    foo()\nexcept BaseException:\n    logger.error('foo', exc_info=True)\n"
        errors = self._scan_code(code)
        assert len(errors) == 1
        assert "exc_info" in errors[0]

    def test_does_not_flag_logger_exception_for_exception(self):
        """`except Exception: logger.exception()` 无 raise → 不报警（Python 3.8+ 不捕获 CancelledError）。

        这是误报控制的关键：except Exception 不实际捕获 CancelledError（继承 BaseException），
        故 logger 反模式不构成 R2 风险。
        """
        code = "try:\n    foo()\nexcept Exception:\n    logger.exception('foo')\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_logger_error_exc_info_for_exception(self):
        """`except Exception: logger.error(exc_info=True)` 无 raise → 不报警。"""
        code = "try:\n    foo()\nexcept Exception:\n    logger.error('foo', exc_info=True)\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_when_raise_present(self):
        code = "try:\n    foo()\nexcept Exception:\n    logger.exception('foo')\n    raise\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_when_raise_in_if(self):
        """raise 在嵌套 if 中也算合规（任务要求：块内有 raise 即可）。"""
        code = "try:\n    foo()\nexcept Exception:\n    if cond:\n        raise\n    logger.exception('foo')\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_specific_exception(self):
        code = "try:\n    foo()\nexcept ValueError:\n    logger.exception('foo')\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_indirect_log_call(self):
        """`_log = logger.error; _log(...)` 不报警（误报控制）。"""
        code = "try:\n    foo()\nexcept Exception:\n    _log = logger.error\n    _log('foo', exc_info=True)\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_normal_handling(self):
        """正常错误处理（无 raise 也无 logger.exception/error(exc_info=True)）不报警。"""
        code = "try:\n    foo()\nexcept Exception as e:\n    logger.error('foo')\n    return None\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_logger_warning_exc_info(self):
        """logger.warning(..., exc_info=True) 不报警（仅 error/exception 触发）。"""
        code = "try:\n    foo()\nexcept Exception:\n    logger.warning('foo', exc_info=True)\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_with_sibling_cancelled_error_guard(self):
        """存在 `except asyncio.CancelledError: raise` 兄弟守卫时不报警。

        这是 R2 标准合规模式：CancelledError 由兄弟 handler 处理并 raise，
        broad except 仅处理非 CancelledError 异常。
        """
        code = (
            "try:\n    foo()\n"
            "except asyncio.CancelledError:\n    raise\n"
            "except Exception:\n    logger.error('foo', exc_info=True)\n"
        )
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_pass_with_sibling_cancelled_error_guard(self):
        """兄弟守卫豁免 pass-only 模式。"""
        code = "try:\n    foo()\nexcept asyncio.CancelledError:\n    raise\nexcept Exception:\n    pass\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_flags_when_sibling_cancelled_error_has_no_raise(self):
        """兄弟 handler 是 `except asyncio.CancelledError: pass`（无 raise）不豁免。

        使用 `except BaseException`（实际捕获 CancelledError）触发 logger 反模式报警。
        """
        code = (
            "try:\n    foo()\n"
            "except asyncio.CancelledError:\n    pass\n"
            "except BaseException:\n    logger.error('foo', exc_info=True)\n"
        )
        errors = self._scan_code(code)
        assert len(errors) == 1

    def test_flags_guard_shadowed_by_preceding_base_exception_pass(self):
        """`except BaseException: pass` 位于守卫之前 → 守卫不可达，按 pass-only 报警。"""
        code = "try:\n    foo()\nexcept BaseException:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"
        errors = self._scan_code(code)
        assert len(errors) == 1
        assert "pass" in errors[0]

    def test_flags_guard_shadowed_by_preceding_base_exception_logger(self):
        """`except BaseException: logger.exception()` 位于守卫之前 → 按 logger 反模式报警。"""
        code = (
            "try:\n    foo()\n"
            "except BaseException:\n    logger.exception('foo')\n"
            "except asyncio.CancelledError:\n    raise\n"
        )
        errors = self._scan_code(code)
        assert len(errors) == 1
        assert "logger.exception" in errors[0]

    def test_flags_tuple_broad_except_without_guard(self):
        """元组含 broad 类型同样按 broad except 处理。"""
        code = "try:\n    foo()\nexcept (Exception, ValueError):\n    pass\n"
        errors = self._scan_code(code)
        assert len(errors) == 1

    def test_tuple_cancelled_error_guard_exempts_later_broad_handler(self):
        """元组形式的取消守卫位于 broad handler 之前，后续 broad 豁免。"""
        code = "try:\n    foo()\nexcept (asyncio.CancelledError, ValueError):\n    raise\nexcept Exception:\n    pass\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_aliased_cancelled_error_guard_exempts_later_broad_handler(self):
        """`from asyncio import CancelledError as CE` 别名守卫也豁免后续 broad。"""
        code = (
            "from asyncio import CancelledError as CE\n"
            "try:\n    foo()\n"
            "except CE:\n    raise\n"
            "except Exception:\n    pass\n"
        )
        errors = self._scan_code(code)
        assert errors == []

    def test_does_not_flag_exception_before_cancelled_error_guard(self):
        """Python 3.8+ 中 except Exception 不捕获 CancelledError，守卫仍可达。"""
        code = "try:\n    foo()\nexcept Exception:\n    pass\nexcept asyncio.CancelledError:\n    raise\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_whitelist_inline_suppresses(self):
        code = "try:\n    foo()\nexcept Exception:  # R2_ALLOWED: top-level guard\n    pass\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_whitelist_prev_line_suppresses(self):
        code = "try:\n    foo()\n# R2_ALLOWED: top-level guard\nexcept Exception:\n    pass\n"
        errors = self._scan_code(code)
        assert errors == []

    def test_multiple_violations_all_reported(self):
        """多个违规均被报告：pass-only 吞没 + BaseException + logger.exception 反模式。"""
        code = (
            "try:\n    foo()\nexcept Exception:\n    pass\n"
            "try:\n    bar()\nexcept BaseException:\n    logger.exception('bar')\n"
        )
        errors = self._scan_code(code)
        assert len(errors) == 2


# ============================================================================
# 集成测试：当前代码库无 R2 吞没违规（契约测试）
# ============================================================================


class TestR2NoSwallowInCurrentCodebase:
    """集成测试：验证当前代码库无 R2 CancelledError 吞没违规。

    这是契约测试，确保新增代码不会引入静默吞没 CancelledError 的反模式。
    若失败，说明有违规引入，应立即修复或添加白名单注释。
    """

    def test_scanned_directories_exist(self):
        """验证扫描的目录都存在（防止配置漂移）。"""
        for dir_name in SCANNED_DIRS:
            directory = ROOT / dir_name
            assert directory.exists(), f"扫描目录不存在: {dir_name}"
            assert directory.is_dir(), f"扫描路径不是目录: {dir_name}"

    def test_no_silent_swallow_in_scanned_dirs(self):
        """扫描 core/data/services/strategies/utils 下所有 .py 文件，无违规。"""
        all_errors: list[str] = []
        for dir_name in SCANNED_DIRS:
            directory = ROOT / dir_name
            all_errors.extend(_scan_directory(directory))
        assert not all_errors, "R2 CancelledError 吞没违规:\n  " + "\n  ".join(all_errors)
