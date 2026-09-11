"""D7-5 守护测试：services/data/strategies 层不得产出硬编码用户可见文案。

依据 CLAUDE.md §3.2 i18n 纪律：非 UI 层无 locale 概念，用户可见文本必须是
`Message` 或 i18n key，不得是硬编码自然语言字符串字面量。D7-1（task_manager
"Waiting..."/"Starting..."）与 D7-2（scheduler_service 调度期 I18n.get()）证明
绕过路径曾存在且已修复。

本测试通过 AST 扫描 `services/data/strategies/` 下所有 .py 文件守护两类反模式
（风格对齐 test_no_cancelled_error_swallow.py / test_no_class_attr_asyncio_primitives.py）：

1. **`_USER_FACING_FIELDS` 赋硬编码文案**：对这些字段名（description/name/task_type/
   status_message）赋值自然语言字符串字面量（含空格的英文短语或 CJK 文本）→ 报警。
   i18n key（形如 `strategy_oversold_name`，无空格）不报警（AST 语义判定靠正则信号）。
2. **非 UI 层调用 `I18n.get()`**：`services/data/strategies` 不应直接调用 `I18n.get()`。
   存量合法用法（LLM 上下文构建、回测报告 Markdown、本地化日志文本等）经
   `_ALLOWED_I18N_GET_FILES` 文件级白名单登记豁免；**新增文件**的 `I18n.get()` 必须
   显式登记或改为产出 Message，未登记即红灯（存量豁免、防扩散，与 R1 exceptions
   治理精神一致）。

AST 扫描天然排除 docstring/注释中的提及（选择 AST 而非文本 grep 的原因）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent

# 受扫描的顶层目录（CLAUDE.md §4.1 分层架构中无 locale 概念的层）
_GUARDED_LAYERS = ("services", "data", "strategies")

# 赋值给这些字段名的字符串字面量视为用户可见文案（D7-1/D7-2 根因模式）
_USER_FACING_FIELDS = frozenset({"description", "name", "task_type", "status_message"})

# 高置信度"硬编码用户文案"信号：含空格的英文短语（"Unknown Task"），或 CJK 文本。
# i18n key（`strategy_oversold_name`）与单英文单词标签（"System"/"Error"）不匹配，
# 以控制误伤（见对抗检视：聚焦"短语/句子"硬编码，规避对大量单词字段值的误报）。
_NATURAL_LANG = re.compile(r"(?:[A-Za-z]{2,}\s+[A-Za-z])|[\u3400-\u4dbf\u4e00-\u9fff]")  # 含空格英文短语 或 CJK

# 存量合法的 I18n.get() 文件（D7-5 基线，2026-09-11 main）。这些文件的 I18n.get()
# 用于 LLM 上下文构建 / 回测报告 Markdown / 本地化日志文本等，属正确 i18n 用法。
# 新增文件含 I18n.get() 必须在此登记（含 reason）或改为产出 Message，否则红灯。
# 路径为仓库相对路径（POSIX 分隔符）。
_ALLOWED_I18N_GET_FILES = frozenset(
    {
        # --- data 层 ---
        "data/data_processor.py",
        "data/domain_services/market_data_service.py",
        "data/mixins/health_mixin.py",
        "data/persistence/db_config_service.py",
        "data/persistence/metadata_manager.py",
        "data/persistence/quality_gate.py",
        "data/persistence/review_manager.py",
        # --- services 层 ---
        "services/ai_service/labels.py",
        "services/ai_service/news_classifier.py",
        "services/ai_service/stock_analysis.py",
        "services/news_subscription_service.py",
        "services/scheduled_jobs/nightly_prediction.py",
        "services/task_manager.py",
        # --- strategies 层（含 LLM 上下文与回测报告生成）---
        "strategies/ai_context/auxiliary.py",
        "strategies/ai_context/capital_flow.py",
        "strategies/ai_context/financials.py",
        "strategies/ai_context/history.py",
        "strategies/ai_context/macro.py",
        "strategies/ai_context/technical.py",
        "strategies/ai_mixin.py",
        "strategies/all_strategies.py",
        "strategies/backtest/report.py",
        "strategies/base_strategy.py",
        "strategies/oversold_strategy.py",
    }
)

# ============================================================================
# 检测纯函数
# ============================================================================


def _is_natural_literal(const: ast.AST | None) -> bool:
    """判断 AST 节点是否为自然语言字符串字面量（硬编码用户文案信号）。

    仅对 ``ast.Constant`` 且值为 ``str`` 的节点判定；非字符串字面量（变量、表达式、
    f-string 等）不报警（AST 语义判定，避免误伤动态赋值）。
    """
    if not isinstance(const, ast.Constant) or not isinstance(const.value, str):
        return False
    return bool(_NATURAL_LANG.search(const.value))


def _assignment_targets(node: ast.AST) -> list[ast.expr]:
    """提取赋值语句的目标表达式列表。

    覆盖 `Assign`（`x = ...`）与 `AnnAssign`（类字段/注解赋值 `x: T = ...`）。
    """
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign) and node.target is not None:
        return [node.target]
    return []


def _find_user_facing_violations(tree: ast.Module, rel_path: str) -> list[str]:
    """扫描 AST 中 `_USER_FACING_FIELDS` 赋自然语言字符串字面量的违规。"""
    errors: list[str] = []
    for node in ast.walk(tree):
        targets = _assignment_targets(node)
        if not targets:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in _USER_FACING_FIELDS:
                continue
            if _is_natural_literal(node.value):
                errors.append(
                    f"{rel_path}:{node.lineno}: 对用户可见字段 {target.id!r} 赋值硬编码文案"
                    f" {node.value.value!r}；非 UI 层应使用 Message 或 i18n key (§3.2)"
                )
    return errors


def _scan_i18n_get_files(directory: Path) -> list[str]:
    """扫描目录下含 `I18n.get(` 调用的文件相对路径（POSIX），用于白名单比对。"""
    hits: list[str] = []
    if not directory.exists():
        return hits
    for p in directory.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            source = p.read_text(encoding="utf-8")
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        if "I18n.get(" in source:
            hits.append(p.relative_to(ROOT).as_posix())
    return sorted(hits)


def _scan_directory(directory: Path) -> list[str]:
    """扫描目录下所有 .py 文件，返回用户面对字段硬编码违规。"""
    errors: list[str] = []
    if not directory.exists():
        return errors
    for p in directory.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        rel = p.relative_to(ROOT).as_posix()
        errors.extend(_find_user_facing_violations(tree, rel))
    return errors


# ============================================================================
# 纯函数测试：检测逻辑边界
# ============================================================================


class TestIsNaturalLiteral:
    """验证 _is_natural_literal 正确识别自然语言字符串。"""

    def test_english_phrase_with_space_detected(self):
        assert _is_natural_literal(ast.parse('"Unknown Task"').body[0].value) is True

    def test_cjk_detected(self):
        assert _is_natural_literal(ast.parse('"未知任务"').body[0].value) is True

    def test_i18n_key_not_detected(self):
        """snake_case i18n key（无空格、无 CJK）不误判。"""
        assert _is_natural_literal(ast.parse('"strategy_oversold_name"').body[0].value) is False

    def test_single_english_word_not_detected(self):
        """单英文单词标签（"System"）不误判（避免对大量单词字段值误报）。"""
        assert _is_natural_literal(ast.parse('"System"').body[0].value) is False

    def test_non_string_not_detected(self):
        assert _is_natural_literal(ast.parse("1").body[0].value) is False

    def test_variable_name_not_detected(self):
        """f-string / 变量 / 表达式节点不判定（AST Constant 语义）。"""
        assert _is_natural_literal(ast.Name(id="x")) is False


class TestFindUserFacingViolations:
    """验证 _find_user_facing_violations 检测逻辑。"""

    def _scan_code(self, code: str) -> list[str]:
        return _find_user_facing_violations(ast.parse(code), "test.py")

    def test_flags_assignment_of_natural_phrase(self):
        code = 'def f():\n    description = "An unknown operation"\n'
        errs = self._scan_code(code)
        assert len(errs) == 1
        assert "description" in errs[0]

    def test_flags_annassign_class_field_default(self):
        """类字段默认值（dataclass AnnAssign）也扫（D7-1 默认值模式）。"""
        code = 'class T:\n    name: str = "Unknown Task"\n'
        errs = self._scan_code(code)
        assert len(errs) == 1
        assert "name" in errs[0]

    def test_does_not_flag_message_or_key(self):
        code = 'description = Message("task_failed_desc")\nname = "strategy_x_name"\n'
        assert self._scan_code(code) == []

    def test_does_not_flag_non_user_facing_field(self):
        code = 'error = "Backend down unexpectedly"\nlabel = "Starting..."\n'
        assert self._scan_code(code) == []

    def test_flags_status_message_cjk(self):
        code = 'status_message = "网络错误"\n'
        errs = self._scan_code(code)
        assert len(errs) == 1
        assert "status_message" in errs[0]


# ============================================================================
# 集成测试：当前代码库无 D7-5 违规（契约测试）
# ============================================================================


class TestNoI18nGetOutsideUi:
    """非 UI 层 `I18n.get()` 守护：新增文件必须登记豁免，存量经白名单豁免。"""

    def test_guarded_layers_exist(self):
        for layer in _GUARDED_LAYERS:
            assert (ROOT / layer).exists(), f"守护目录不存在: {layer}"

    def test_allowed_files_exist(self):
        """白名单登记的文件必须真实存在（防止登记漂移/失效登记）。"""
        for rel in _ALLOWED_I18N_GET_FILES:
            p = ROOT / rel
            assert p.exists(), f"白名单文件不存在（应移除登记）: {rel}"

    def test_no_i18n_get_in_unregistered_file(self):
        """services/data/strategies 中 `I18n.get(` 所在文件必须 ∈ 白名单。"""
        unregistered: list[str] = []
        for layer in _GUARDED_LAYERS:
            for f in _scan_i18n_get_files(ROOT / layer):
                if f not in _ALLOWED_I18N_GET_FILES:
                    unregistered.append(f)
        assert not unregistered, (
            "非 UI 层出现未登记的 I18n.get() 调用，应改为产出 Message 或登记豁免"
            "（D7-5 存量豁免、防扩散）：\n  " + "\n  ".join(unregistered)
        )


class TestNoHardcodedUserFacingString:
    """services/data/strategies 不得对用户可见字段赋值硬编码文案。"""

    def test_guarded_layers_exist(self):
        for layer in _GUARDED_LAYERS:
            assert (ROOT / layer).exists(), f"守护目录不存在: {layer}"

    def test_no_hardcoded_user_facing_string(self):
        errors: list[str] = []
        for layer in _GUARDED_LAYERS:
            errors.extend(_scan_directory(ROOT / layer))
        assert not errors, "非 UI 层硬编码用户文案违规:\n  " + "\n  ".join(errors)
