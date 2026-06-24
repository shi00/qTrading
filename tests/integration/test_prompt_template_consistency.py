"""
P1-15 fix (rewritten): 测试 prompt_validator DataDeclaration 完整性 + prompt 模板一致性

去枚举后，prompt 不再静态声明数据字段，一致性由运行时 <available_data> 保证。
本测试验证：
1. prompt_validator 的声明层（全局能力层）完整性
2. 每条 prompt 模板包含运行时数据边界引用指令
3. 每条 prompt 模板不含静态数据枚举
"""

import asyncio
import logging

import pytest
from strategies.prompt_validator import get_declarations
from strategies.strategy_prompts import STRATEGY_PROMPTS, FORBIDDEN_STATIC_HEADERS

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("prompt_data_set")]


class TestPromptTemplateConsistency:
    """测试 DataDeclaration 声明完整性 + prompt 模板与声明对齐"""

    def test_all_declarations_have_valid_injectors(self):
        declarations = get_declarations()
        for decl in declarations:
            assert decl.injector is not None, f"Declaration {decl.name} 缺少 injector"
            assert callable(decl.injector), f"Declaration {decl.name} 的 injector 不是可调用对象"
            assert decl.prompt_claim is not None, f"Declaration {decl.name} 缺少 prompt_claim"

    def test_declarations_cover_core_financial_fields(self):
        declarations = get_declarations()
        decl_names = {d.name for d in declarations}
        core_fields = {
            "multi_period_roe",
            "cashflow_vs_profit",
            "audit_opinion",
            "dividend_history",
            "pledge_ratio",
            "top10_holders",
            "holder_number",
            "main_business",
            "monetary_capital",
            "accounts_receivable",
        }
        missing_core = core_fields - decl_names
        assert len(missing_core) == 0, f"核心财务字段未在 DataDeclaration 中声明: {missing_core}"

    def test_all_prompts_have_data_boundary_directive(self):
        """每条 prompt 必须包含运行时数据边界引用指令。"""
        for key, prompt in STRATEGY_PROMPTS.items():
            assert "【数据边界】" in prompt or "available_data" in prompt, (
                f"策略 '{key}' 缺少运行时清单引用指令（【数据边界】或 available_data）"
            )

    def test_no_prompts_have_static_data_enumeration(self):
        """每条 prompt 不应包含静态数据枚举表头。"""
        for key, prompt in STRATEGY_PROMPTS.items():
            for header in FORBIDDEN_STATIC_HEADERS:
                assert header not in prompt, (
                    f"策略 '{key}' 仍含静态数据枚举表头「{header}」，应改用 <available_data> 运行时清单"
                )

    @pytest.mark.asyncio
    async def test_declarations_injectors_are_callable(self):
        """Level 2: 验证所有 injector 可调用且返回 True（MVD 数据已就绪）"""
        declarations = get_declarations()
        errors: list[str] = []
        for decl in declarations:
            try:
                result = decl.injector()
                if asyncio.iscoroutine(result):
                    result = await result
                # Level 2: 不仅验证不抛异常，还验证返回 True
                if result is not True:
                    errors.append(f"{decl.name}: injector returned {result!r}, expected True")
            except Exception as e:
                errors.append(f"{decl.name}: {type(e).__name__}: {e}")
        assert not errors, f"Declaration injector 校验失败: {errors}"
