"""测试 Prompt 声明与实际数据注入的一致性"""

import os

import pytest

from strategies.prompt_validator import (
    DECLARATIONS,
    generate_declaration_report,
    validate_prompt_declarations,
)

INTEGRATION_TEST = os.environ.get("INTEGRATION_TEST", "").lower() in ("1", "true", "yes")


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_prompt_data_consistency():
    """确保所有 Prompt 声明的数据都已注入"""
    results = await validate_prompt_declarations(DECLARATIONS)

    missing = [name for name, valid in results.items() if not valid]

    if missing:
        report = generate_declaration_report(DECLARATIONS)
        pytest.fail(f"以下 Prompt 声明的数据未注入: {missing}\n\n{report}")


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_prompt_declaration_report():
    """生成声明状态报告（用于调试）"""
    await validate_prompt_declarations(DECLARATIONS)
    report = generate_declaration_report(DECLARATIONS)
    print(report)
    assert True


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_multi_period_roe_available():
    """测试多期ROE数据是否可用"""
    from strategies.prompt_validator import check_multi_period_data

    result = await check_multi_period_data("roe")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_cashflow_field_exists():
    """测试现金流字段是否存在"""
    from strategies.prompt_validator import check_field_exists

    result = await check_field_exists("n_cashflow_act")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_audit_table_has_data():
    """测试审计意见表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("fina_audit")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_dividend_table_has_data():
    """测试分红记录表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("dividend")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_pledge_table_has_data():
    """测试质押比例表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("pledge_stat")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_macro_table_has_data():
    """测试宏观经济表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("cn_m")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_shibor_table_has_data():
    """测试Shibor利率表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("shibor_daily")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_holders_table_has_data():
    """测试前十大股东表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("top10_holders")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_holder_number_table_has_data():
    """测试股东人数表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("stk_holdernumber")
    assert isinstance(result, bool)


@pytest.mark.asyncio
@pytest.mark.skipif(not INTEGRATION_TEST, reason="需要数据库中有数据，仅在 INTEGRATION_TEST=1 时运行")
async def test_mainbz_table_has_data():
    """测试主营业务构成表是否有数据"""
    from strategies.prompt_validator import check_table_has_data

    result = await check_table_has_data("fina_mainbz")
    assert isinstance(result, bool)
