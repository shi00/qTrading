"""
P1-15 fix: Template placeholder consistency test.

This test ensures that the data items declared in STRATEGY_PROMPTS
are properly validated by the prompt_validator's DataDeclaration list.
"""

import re
import string
import pytest

from strategies.strategy_prompts import STRATEGY_PROMPTS
from strategies.prompt_validator import get_declarations


def extract_declared_data_items(prompt_text: str) -> set[str]:
    """
    Extract data items declared in the 'Available Data' section of a prompt.

    The prompts use a format like:
    【可用数据】你将收到以下数据用于分析：
    - 近8个季度的ROE、毛利率、营收/净利润增速趋势
    - 经营现金流与净利润对比（含现金流/利润比率）
    ...

    We extract the text after each bullet point and normalize it.
    """
    items = set()

    available_data_match = re.search(r"【可用数据】[^-]*?(?=- )", prompt_text, re.DOTALL)
    if not available_data_match:
        available_data_match = re.search(r"【可用数据】(.*?)(?=【|$)", prompt_text, re.DOTALL)

    if not available_data_match:
        return items

    section = available_data_match.group(0)

    bullet_pattern = re.compile(r"-\s*([^\n]+)")
    for match in bullet_pattern.finditer(section):
        item = match.group(1).strip()
        item = re.sub(r"（[^）]*）", "", item)
        item = re.sub(r"\([^)]*\)", "", item)
        item = item.strip()
        if item:
            items.add(item)

    return items


def normalize_data_item(item: str) -> str:
    """Normalize a data item for comparison."""
    item = item.lower()
    item = re.sub(r"[，。、；：！？,.:;!?]", "", item)
    item = re.sub(r"\s+", "", item)
    return item


KNOWN_DATA_MAPPINGS = {
    "近8个季度的roe、毛利率、营收/净利润增速趋势": "multi_period_roe",
    "近8个季度的营收yoy、净利润yoy、毛利率变化趋势": "multi_period_roe",
    "近8个季度的负债率、经营现金流、roe趋势": "multi_period_roe",
    "近8季度的roe、经营现金流与毛利率趋势数据": "multi_period_roe",
    "经营现金流与净利润对比": "cashflow_vs_profit",
    "经营现金流与净利润对比（含现金流/利润比率）": "cashflow_vs_profit",
    "经营现金流趋势": "cashflow_vs_profit",
    "审计意见": "audit_opinion",
    "历年分红记录": "dividend_history",
    "历年分红记录（每股派息金额、分红频率）": "dividend_history",
    "大股东质押比例": "pledge_ratio",
    "质押比例": "pledge_ratio",
    "宏观经济指标": "macro_economy",
    "宏观经济指标（m2增速、cpi、ppi）": "macro_economy",
    "宏观经济指标（行业景气度参考）": "macro_economy",
    "shibor利率": "shibor_rates",
    "shibor利率（隔夜/1周/3个月，反映市场流动性）": "shibor_rates",
    "前十大股东持股变动": "top10_holders",
    "前十大股东变动": "top10_holders",
    "前十大股东变动（机构是否在加仓）": "top10_holders",
    "筹码集中度（股东人数变化）": "holder_number",
    "股东人数变化": "holder_number",
    "主营业务构成": "main_business",
    "主营业务构成（收入来源拆解）": "main_business",
    "主营业务构成（判断赛道）": "main_business",
}

ITEMS_NOT_REQUIRING_VALIDATION = {
    "货币资金余额",
    "应收账款规模",
    "应收账款规模变化",
    "商誉占总资产比例",
    "当前估值指标",
    "近期新闻",
    "你还会收到k线和技术指标数据，但请完全忽略，本策略仅做基本面分析。",
    "近8个季度的经营现金流、roe、负债率趋势",
    "经营现金流与净利润对比（含现金流/利润比率）",
    "货币资金余额（账上现金储备）",
    "主营业务的收入来源拆解",
    "主营业务的收入来源拆解（判断赛道）",
    "主营业务的收入来源拆解（判断公司靠什么赚钱）",
    "主营业务的收入来源拆解（判断公司靠什么赚钱，以及是否有第二增长曲线）",
    "主营业务的收入来源拆解（判断赛道与竞争格局）",
}


class TestPromptTemplateConsistency:
    """Test that STRATEGY_PROMPTS data claims match prompt_validator declarations."""

    def test_strategy_prompts_exist(self):
        """Verify STRATEGY_PROMPTS is not empty."""
        assert len(STRATEGY_PROMPTS) > 0, "STRATEGY_PROMPTS should not be empty"

    def test_declarations_exist(self):
        """Verify DataDeclaration list is not empty."""
        declarations = get_declarations()
        assert len(declarations) > 0, "DataDeclaration list should not be empty"

    def test_all_declarations_have_valid_injectors(self):
        """Verify all declarations have callable injectors."""
        declarations = get_declarations()
        for decl in declarations:
            assert callable(decl.injector), f"Declaration {decl.name} injector should be callable"
            assert decl.name, "Declaration should have a name"
            assert decl.prompt_claim, f"Declaration {decl.name} should have a prompt_claim"

    def test_known_mappings_cover_key_items(self):
        """Verify that key data items have mappings to declarations."""
        declarations = get_declarations()
        decl_names = {d.name for d in declarations}

        for item, expected_decl in KNOWN_DATA_MAPPINGS.items():
            assert expected_decl in decl_names, f"Expected declaration '{expected_decl}' for item '{item}' not found"

    @pytest.mark.parametrize("strategy_key", list(STRATEGY_PROMPTS.keys()))
    def test_strategy_prompt_has_available_data_section(self, strategy_key: str):
        """Verify each strategy prompt has an available data section."""
        prompt = STRATEGY_PROMPTS[strategy_key]
        has_data_section = (
            "【可用数据】" in prompt
            or "可用数据" in prompt
            or "【你将收到的分析材料】" in prompt
            or "你将收到以下数据用于分析" in prompt
        )
        assert has_data_section, f"Strategy {strategy_key} should declare available data"

    def test_no_format_placeholders_in_prompts(self):
        """Verify that prompts don't use Python format placeholders."""
        formatter = string.Formatter()
        for strategy_key, prompt in STRATEGY_PROMPTS.items():
            placeholders = {field for _, field, _, _ in formatter.parse(prompt) if field}
            assert not placeholders, (
                f"Strategy {strategy_key} prompt should not use format placeholders: {placeholders}"
            )

    def test_declaration_names_are_unique(self):
        """Verify all declaration names are unique."""
        declarations = get_declarations()
        names = [d.name for d in declarations]
        assert len(names) == len(set(names)), "Declaration names should be unique"

    def test_declaration_prompt_claims_are_unique(self):
        """Verify all declaration prompt_claims are unique."""
        declarations = get_declarations()
        claims = [d.prompt_claim for d in declarations]
        assert len(claims) == len(set(claims)), "Declaration prompt_claims should be unique"
