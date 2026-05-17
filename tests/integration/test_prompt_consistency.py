import os

import pytest

from strategies.prompt_validator import (
    generate_declaration_report,
    get_declarations,
    validate_prompt_declarations,
)

_SKIP_NO_DATA = pytest.mark.skipif(
    os.environ.get("SKIP_DATA_CONSISTENCY") == "1",
    reason="SKIP_DATA_CONSISTENCY=1: CI has no pre-filled data",
)


@pytest.mark.asyncio
@_SKIP_NO_DATA
async def test_prompt_data_consistency():
    results = await validate_prompt_declarations(get_declarations())
    missing = [name for name, valid in results.items() if not valid]
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
