"""Tests for Flet API existence referenced in accessibility-baseline.md.

把 CLAUDE.md §1.10「禁止臆造 API」从约束代码扩展到约束规范：文档中出现的
``ft.<Control>(...)`` 构造调用必须使用真实存在的控件名与关键字参数，防止
规范与锁定版 Flet API 失配后"文档臆造 API"再次复发。

仅断言 ``ft.<Control>(...)`` 构造调用中的关键字参数（``kw=`` 形式）；
位置参数 / 枚举引用（如 ``ft.Icon(ARROW_UPWARD)`` 的 ``ARROW_UPWARD`` 无
``=``）忽略，避免误判控件无该字段。非 dataclass 引用（hook 如
``ft.use_dialog``、``ft.Colors.*`` 枚举）跳过字段断言，仅校验控件名存在。

空转守护：断言文档实际抽到 TextField / Dropdown 控件名，防止正则失配后
测试无声通过。
"""

import dataclasses
import re
from pathlib import Path

import flet as ft
import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = ROOT / "docs" / "flet" / "accessibility-baseline.md"

# 匹配 ft.<Control>( 构造调用起始（控件名以字母/下划线开头，允许与 '(' 间空格）
_CONTROL_CALL_RE = re.compile(r"ft\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# 顶层参数片段起始处匹配 kw= 形式（值中嵌套 ft.Text(...) 时不在片段开头，不误配）
_KWARG_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _skip_quotes(text: str, i: int) -> int:
    """返回跳过字符串字面量后的下标（'...' / \"...\"），未在字符串内则原样返回。

    文档参数值可能含括号/逗号（如 ``error_text="a, b=c"``），引号内的
    括号与逗号不参与解析，防止误分割/误平衡。
    """
    ch = text[i]
    if ch not in ("'", '"'):
        return i
    quote = ch
    i += 1
    while i < len(text):
        if text[i] == quote:
            return i + 1
        i += 1
    return i  # 未闭合字符串：跳过到末尾，防御性处理


def _balanced_args(text: str, open_idx: int) -> str:
    """从 open_idx 的 '(' 做括号平衡扫描，返回该括号对的内容（支持嵌套）。

    open_idx 指向 '('；'ft.Colors.TRANSPARENT' 等无 '(' 的引用不会被调用，
    此处仅处理已匹配到 '(' 的调用。字符串字面量内的括号不参与平衡。
    """
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch in ("'", '"'):
            i = _skip_quotes(text, i)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i]
        i += 1
    return text[open_idx + 1 :]


def _extract_kwargs(args: str) -> list[str]:
    """提取顶层关键字参数名（逗号分割，嵌套括号与字符串内的逗号不分割）。"""
    kwargs: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(args):
        ch = args[i]
        if ch in ("'", '"'):
            i = _skip_quotes(args, i)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            _collect_kwarg(args[start:i], kwargs)
            start = i + 1
        i += 1
    _collect_kwarg(args[start:], kwargs)
    return kwargs


def _collect_kwarg(segment: str, kwargs: list[str]) -> None:
    m = _KWARG_RE.match(segment)
    if m:
        kwargs.append(m.group(1))


def _extract_control_calls(text: str) -> list[tuple[str, str]]:
    """抽取所有 ``ft.<Control>(...)`` 调用，返回 (控件名, 括号内参数) 列表。"""
    calls: list[tuple[str, str]] = []
    for m in _CONTROL_CALL_RE.finditer(text):
        args = _balanced_args(text, m.end() - 1)
        calls.append((m.group(1), args))
    return calls


def _assert_doc_params(code: str) -> None:
    """断言代码文本中的 ``ft.<Control>(kw=...)`` 引用全部真实存在（Flet 锁定版）。"""
    for control_name, args in _extract_control_calls(code):
        control = getattr(ft, control_name, None)  # type: ignore[attr-defined]  # [reason: 动态访问文档引用的 Flet 控件名，运行时验证存在性]
        assert control is not None, f"控件 {control_name} 不存在于 Flet API（文档臆造控件名，见 CLAUDE.md §1.10）"
        if dataclasses.is_dataclass(control):
            fields = {f.name for f in dataclasses.fields(control)}
            for param in _extract_kwargs(args):
                assert param in fields, f"{control_name} 无参数 {param}（文档臆造参数名）"


class TestBaselineApiExistence:
    """accessibility-baseline.md 引用的 Flet API 存在性守护。"""

    def test_baseline_controls_and_params_exist(self):
        """文档全部 ft.<Control>(...) 引用必须真实存在且参数合法。"""
        content = _read(BASELINE_PATH)
        _assert_doc_params(content)

    def test_baseline_references_expected_controls(self):
        """空转守护：文档实际抽到 TextField 与 Dropdown 控件名。"""
        content = _read(BASELINE_PATH)
        names = {name for name, _ in _extract_control_calls(content)}
        assert "TextField" in names, "正则失配：文档应引用 TextField 构造调用"
        assert "Dropdown" in names, "正则失配：文档应引用 Dropdown 构造调用"

    def test_rejects_fabricated_param(self):
        """反例自检：臆造参数（error_text 不属于 TextField）必须抛 AssertionError。"""
        with pytest.raises(AssertionError, match="无参数 error_text"):
            _assert_doc_params("ft.TextField(error_text='x')")

    def test_rejects_fabricated_control(self):
        """反例自检：臆造控件名必须抛 AssertionError（不得静默跳过）。"""
        with pytest.raises(AssertionError, match="不存在于 Flet API"):
            _assert_doc_params("ft.NonexistentControl(error='x')")

    def test_ignores_positional_args(self):
        """位置参数/枚举引用（无 kw=）不触发字段断言。"""
        _assert_doc_params("ft.Icon(ARROW_UPWARD)")  # 不应抛异常

    def test_ignores_commas_and_parens_inside_string_values(self):
        """字符串参数值内的逗号/括号不参与解析（引号状态机防误报/误平衡）。"""
        # 值含 "逗号+等号" 形态不误判为第二个 kwarg
        _assert_doc_params("ft.TextField(label='a, b=c')")
        # 值含未闭合左括号不吞掉后续参数（如 label）
        _assert_doc_params("ft.Dropdown(error_text='a(b', label='x')")

    def test_matches_control_with_space_before_paren(self):
        """控件名与 '(' 间允许空格（防正则漏匹配）。"""
        _assert_doc_params("ft.TextField (label='x')")
