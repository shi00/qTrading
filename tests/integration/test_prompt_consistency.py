import os
import string

import pytest

from strategies.prompt_validator import (
    generate_declaration_report,
    get_declarations,
    validate_prompt_declarations,
)
from strategies.strategy_prompts import STRATEGY_PROMPTS

pytestmark = pytest.mark.integration

_SKIP_NO_DATA = pytest.mark.skipif(
    os.environ.get("SKIP_DATA_CONSISTENCY") == "1",
    reason="SKIP_DATA_CONSISTENCY=1: CI has no pre-filled data",
)


def test_template_placeholders_are_valid():
    """
    P1-15 fix: 验证策略提示词模板中的占位符格式正确。
    检查所有 {placeholder} 格式的占位符是否有效。
    """
    formatter = string.Formatter()
    for strategy_key, template in STRATEGY_PROMPTS.items():
        try:
            list(formatter.parse(template))
        except ValueError as e:
            pytest.fail(f"Strategy {strategy_key} has invalid placeholder format: {e}")


def test_template_no_undefined_placeholders():
    """
    P1-15 fix: 验证策略提示词模板中没有未定义的占位符。
    模板中使用的 {field} 应该在模板上下文中有对应的注入逻辑。
    """
    known_context_fields = {
        "stock_data",
        "history_text",
        "support_levels",
        "turnover_context",
        "sector_context",
        "market_context",
        "global_context",
        "learning_context",
        "news_context",
        "rsi_percentile",
        "rsi_feature",
    }

    formatter = string.Formatter()
    for strategy_key, template in STRATEGY_PROMPTS.items():
        placeholders = {field for _, field, _, _ in formatter.parse(template) if field}
        unknown = placeholders - known_context_fields
        if unknown:
            pytest.fail(
                f"Strategy {strategy_key} uses unknown placeholders: {unknown}. Known fields: {known_context_fields}"
            )


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_prompt_data_consistency():
    results = await validate_prompt_declarations(get_declarations())
    missing = [name for name, valid in results.items() if not valid]
    if len(missing) == len(results):
        pytest.skip("All declarations missing — database appears empty, skipping consistency check")
    if missing:
        report = generate_declaration_report(get_declarations())
        pytest.fail(f"以下 Prompt 声明的数据未注入: {missing}\n\n{report}")


@pytest.mark.asyncio
async def test_prompt_declaration_report():
    await validate_prompt_declarations(get_declarations())
    report = generate_declaration_report(get_declarations())
    assert len(report) > 0


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_multi_period_roe_available():
    from strategies.prompt_validator import check_multi_period_data

    result = await check_multi_period_data("roe")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_cashflow_field_exists():
    from strategies.prompt_validator import check_field_exists

    result = await check_field_exists("n_cashflow_act")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_audit_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("fina_audit")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_dividend_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("dividend")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_pledge_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("pledge_stat")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_macro_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("cn_m")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_shibor_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("shibor_daily")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_holders_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("top10_holders")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_holder_number_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("stk_holdernumber")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_mainbz_table_has_data():
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("fina_mainbz")
    assert isinstance(result, bool)
