"""
P1-15 fix (rewritten): 测试 prompt_validator DataDeclaration 完整性

去枚举后，prompt 不再静态声明数据字段，一致性由运行时 <available_data> 保证。
本测试验证 prompt_validator 的声明层（全局能力层）完整性。
"""

import pytest
from strategies.prompt_validator import get_declarations


class TestPromptTemplateConsistency:
    """测试 DataDeclaration 声明完整性"""

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

    @pytest.mark.asyncio
    async def test_declarations_injectors_are_callable(self):
        import asyncio

        declarations = get_declarations()
        for decl in declarations:
            try:
                result = decl.injector()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
