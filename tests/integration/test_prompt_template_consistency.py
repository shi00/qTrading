"""
P1-15 fix: 测试 STRATEGY_PROMPTS 与 prompt_validator 字段不对齐

本测试验证策略 prompt 中声明的数据与 prompt_validator 的 DataDeclaration 列表一致性。
"""

import pytest
import re
from strategies.strategy_prompts import STRATEGY_PROMPTS
from strategies.prompt_validator import get_declarations


class TestPromptTemplateConsistency:
    """测试 Prompt 模板声明与 DataDeclaration 一致性"""

    CLAIMED_DATA_PATTERNS = {
        "ROE": ["ROE", "roe"],
        "毛利率": ["毛利率", "gross_profit_ratio"],
        "营收增速": ["营收.*增速", "营收YOY", "revenue.*growth"],
        "净利润增速": ["净利润.*增速", "净利润YOY", "net_profit.*growth"],
        "经营现金流": ["经营现金流", "经营.*现金流", "cashflow", "n_cashflow_act"],
        "货币资金": ["货币资金", "cash", "货币.*资金"],
        "应收账款": ["应收账款", "accounts_receivable"],
        "商誉": ["商誉", "goodwill"],
        "审计意见": ["审计意见", "audit.*opinion"],
        "分红记录": ["分红", "dividend"],
        "质押比例": ["质押", "pledge"],
        "前十大股东": ["前十大股东", "top10.*holder", "十大股东"],
        "股东人数": ["股东人数", "holder.*number", "筹码集中度"],
        "主营业务": ["主营业务", "主营.*构成", "main.*business"],
        "宏观经济": ["宏观经济", "M2", "CPI", "PPI"],
        "Shibor": ["Shibor", "shibor"],
        "北向资金": ["北向", "northbound"],
        "龙虎榜": ["龙虎榜", "top_list"],
        "K线": ["K线", "kline", "日线"],
        "MACD": ["MACD", "macd"],
        "KDJ": ["KDJ", "kdj"],
        "均线": ["均线", "MA", "moving.*average"],
        "资金流向": ["资金流向", "moneyflow", "资金流"],
        "换手率": ["换手率", "turnover"],
        "PE": ["PE", "pe_ttm"],
        "PB": ["PB", "pb"],
        "股息率": ["股息率", "dv_ttm", "dividend.*yield"],
        "负债率": ["负债率", "debt.*ratio"],
        "概念板块": ["概念", "concept", "板块"],
        "新闻": ["新闻", "news"],
    }

    DECLARATION_NAME_MAP = {
        "multi_period_roe": ["ROE"],
        "cashflow_vs_profit": ["经营现金流"],
        "audit_opinion": ["审计意见"],
        "dividend_history": ["分红记录"],
        "pledge_ratio": ["质押比例"],
        "top10_holders": ["前十大股东"],
        "holder_number": ["股东人数"],
        "main_business": ["主营业务"],
        "macro_economy": ["宏观经济"],
        "shibor_rates": ["Shibor"],
    }

    def extract_claimed_fields_from_prompt(self, prompt: str) -> set[str]:
        """从 prompt 文本中提取声称的数据字段"""
        claimed = set()
        for field_name, patterns in self.CLAIMED_DATA_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, prompt, re.IGNORECASE):
                    claimed.add(field_name)
                    break
        return claimed

    def get_declared_fields_from_validator(self) -> set[str]:
        """从 prompt_validator 的 DataDeclaration 中提取已声明的字段"""
        declared = set()
        for decl in get_declarations():
            mapped_fields = self.DECLARATION_NAME_MAP.get(decl.name, [])
            for field in mapped_fields:
                declared.add(field)
        return declared

    @pytest.mark.asyncio
    async def test_value_strategy_prompt_consistency(self):
        """测试 value 策略 prompt 声明的数据与 validator 一致"""
        prompt = STRATEGY_PROMPTS.get("value", "")
        claimed = self.extract_claimed_fields_from_prompt(prompt)
        declared = self.get_declared_fields_from_validator()

        missing_in_validator = claimed - declared

        known_missing_acceptable = {
            "货币资金",
            "应收账款",
            "商誉",
            "PE",
            "PB",
            "股息率",
            "负债率",
            "北向资金",
            "龙虎榜",
            "K线",
            "MACD",
            "KDJ",
            "均线",
            "资金流向",
            "换手率",
            "概念板块",
            "新闻",
            "毛利率",
            "营收增速",
            "净利润增速",
        }

        unexpected_missing = missing_in_validator - known_missing_acceptable

        assert len(unexpected_missing) == 0, (
            f"value 策略 prompt 声明了以下字段，但 prompt_validator 未覆盖: {unexpected_missing}\n"
            f"已声明: {declared}\n"
            f"声称: {claimed}"
        )

    @pytest.mark.asyncio
    async def test_growth_strategy_prompt_consistency(self):
        """测试 growth 策略 prompt 声明的数据与 validator 一致"""
        prompt = STRATEGY_PROMPTS.get("growth", "")
        claimed = self.extract_claimed_fields_from_prompt(prompt)
        declared = self.get_declared_fields_from_validator()

        known_missing_acceptable = {
            "PE",
            "PB",
            "北向资金",
            "龙虎榜",
            "K线",
            "MACD",
            "KDJ",
            "均线",
            "资金流向",
            "换手率",
            "概念板块",
            "新闻",
            "毛利率",
            "营收增速",
            "净利润增速",
            "应收账款",
        }

        missing_in_validator = claimed - declared
        unexpected_missing = missing_in_validator - known_missing_acceptable

        assert len(unexpected_missing) == 0, (
            f"growth 策略 prompt 声明了以下字段，但 prompt_validator 未覆盖: {unexpected_missing}"
        )

    @pytest.mark.asyncio
    async def test_dividend_strategy_prompt_consistency(self):
        """测试 dividend 策略 prompt 声明的数据与 validator 一致"""
        prompt = STRATEGY_PROMPTS.get("dividend", "")
        claimed = self.extract_claimed_fields_from_prompt(prompt)
        declared = self.get_declared_fields_from_validator()

        known_missing_acceptable = {
            "货币资金",
            "PE",
            "PB",
            "股息率",
            "负债率",
            "北向资金",
            "K线",
            "MACD",
            "KDJ",
            "均线",
            "资金流向",
            "换手率",
            "概念板块",
            "新闻",
        }

        missing_in_validator = claimed - declared
        unexpected_missing = missing_in_validator - known_missing_acceptable

        assert len(unexpected_missing) == 0, (
            f"dividend 策略 prompt 声明了以下字段，但 prompt_validator 未覆盖: {unexpected_missing}"
        )

    @pytest.mark.asyncio
    async def test_cashflow_strategy_prompt_consistency(self):
        """测试 cashflow 策略 prompt 声明的数据与 validator 一致"""
        prompt = STRATEGY_PROMPTS.get("cashflow", "")
        claimed = self.extract_claimed_fields_from_prompt(prompt)
        declared = self.get_declared_fields_from_validator()

        known_missing_acceptable = {
            "货币资金",
            "应收账款",
            "PE",
            "PB",
            "负债率",
            "北向资金",
            "K线",
            "MACD",
            "KDJ",
            "均线",
            "资金流向",
            "换手率",
            "概念板块",
            "新闻",
        }

        missing_in_validator = claimed - declared
        unexpected_missing = missing_in_validator - known_missing_acceptable

        assert len(unexpected_missing) == 0, (
            f"cashflow 策略 prompt 声明了以下字段，但 prompt_validator 未覆盖: {unexpected_missing}"
        )

    @pytest.mark.asyncio
    async def test_oversold_strategy_prompt_consistency(self):
        """测试 oversold 策略 prompt 声明的数据与 validator 一致"""
        prompt = STRATEGY_PROMPTS.get("oversold", "")
        claimed = self.extract_claimed_fields_from_prompt(prompt)
        declared = self.get_declared_fields_from_validator()

        known_missing_acceptable = {
            "RSI",
            "K线",
            "MACD",
            "KDJ",
            "均线",
            "换手率",
            "资金流向",
            "北向资金",
            "龙虎榜",
            "PE",
            "PB",
            "ROE",
            "毛利率",
            "负债率",
            "股息率",
            "概念板块",
            "新闻",
            "营收增速",
            "净利润增速",
        }

        missing_in_validator = claimed - declared
        unexpected_missing = missing_in_validator - known_missing_acceptable

        assert len(unexpected_missing) == 0, (
            f"oversold 策略 prompt 声明了以下字段，但 prompt_validator 未覆盖: {unexpected_missing}"
        )

    def test_all_declarations_have_valid_injectors(self):
        """测试所有 DataDeclaration 都有有效的 injector"""
        declarations = get_declarations()

        for decl in declarations:
            assert decl.injector is not None, f"Declaration {decl.name} 缺少 injector"
            assert callable(decl.injector), f"Declaration {decl.name} 的 injector 不是可调用对象"
            assert decl.prompt_claim is not None, f"Declaration {decl.name} 缺少 prompt_claim"

    def test_declarations_cover_core_financial_fields(self):
        """测试 DataDeclaration 覆盖核心财务字段"""
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
        }

        missing_core = core_fields - decl_names

        assert len(missing_core) == 0, f"核心财务字段未在 DataDeclaration 中声明: {missing_core}"
