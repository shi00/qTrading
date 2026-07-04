"""分层式最小可行数据集（MVD）静态定义。

分层策略（所有数据均为 function-scoped）：
- L0 基础层：stock_basic, trade_cal
- L1 行情层：daily_quotes, daily_indicators, moneyflow_daily, northbound_holding
- L2 财务层：financial_reports, fina_audit, fina_mainbz, dividend
- L3 辅助层：pledge_stat, top10_holders, stk_holdernumber, block_trade, top_list
- L4 宏观层：macro_economy, shibor_daily, market_news

设计原则：
1. 所有金额/比率字段使用 Decimal，与 ORM Numeric 类型一致
2. ShiborDaily 的 Python 属性名（w1/w2/m1/m3/m6/m9/y1）与数据库列名（1w/2w/1m/3m/6m/9m/1y）不同，
   insert().values(dict) 按 column key（属性名）匹配，必须使用属性名
3. financial_reports 设计为 8 期 ROE 递增（12.5→16.0），支持趋势验证
4. top10_holders 设计为 3 条，支持持股比例合计验证
5. stk_holdernumber 设计为 2 期，支持股东人数变化趋势验证
"""

import datetime
from decimal import Decimal

# ===========================================================================
# L0 基础层（function-scoped）
# ===========================================================================

MVD_STOCK_BASIC = [
    {
        "ts_code": "000001.SZ",
        "symbol": "000001",
        "name": "平安银行",
        "area": "深圳",
        "industry": "银行",
        "market": "主板",
        "list_date": datetime.date(1991, 4, 3),
        "list_status": "L",
    },
    {
        "ts_code": "600000.SH",
        "symbol": "600000",
        "name": "浦发银行",
        "area": "上海",
        "industry": "银行",
        "market": "主板",
        "list_date": datetime.date(1999, 11, 10),
        "list_status": "L",
    },
]

MVD_TRADE_CAL = [
    {
        "cal_date": datetime.date(2026, 6, 22),
        "exchange": "SSE",
        "is_open": 1,
        "pretrade_date": datetime.date(2026, 6, 19),
    },
    {
        "cal_date": datetime.date(2026, 6, 23),
        "exchange": "SSE",
        "is_open": 1,
        "pretrade_date": datetime.date(2026, 6, 22),
    },
    {
        "cal_date": datetime.date(2026, 6, 24),
        "exchange": "SSE",
        "is_open": 1,
        "pretrade_date": datetime.date(2026, 6, 23),
    },
    {
        "cal_date": datetime.date(2026, 6, 25),
        "exchange": "SSE",
        "is_open": 1,
        "pretrade_date": datetime.date(2026, 6, 24),
    },
    {
        "cal_date": datetime.date(2026, 6, 26),
        "exchange": "SSE",
        "is_open": 1,
        "pretrade_date": datetime.date(2026, 6, 25),
    },
]

# ===========================================================================
# L1 行情层（function-scoped）
# ===========================================================================

# 000001.SZ 和 600000.SH 各 5 个交易日日线（足够测试，避免数据过多）
MVD_DAILY_QUOTES = [
    {
        "ts_code": "000001.SZ",
        "trade_date": datetime.date(2026, 6, 18),
        "open": Decimal("10.20"),
        "high": Decimal("10.50"),
        "low": Decimal("10.10"),
        "close": Decimal("10.40"),
        "pre_close": Decimal("10.30"),
        "change": Decimal("0.10"),
        "pct_chg": Decimal("0.97"),
        "vol": 1000000,
        "amount": Decimal("10400000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "trade_date": datetime.date(2026, 6, 19),
        "open": Decimal("10.40"),
        "high": Decimal("10.60"),
        "low": Decimal("10.30"),
        "close": Decimal("10.50"),
        "pre_close": Decimal("10.40"),
        "change": Decimal("0.10"),
        "pct_chg": Decimal("0.96"),
        "vol": 1100000,
        "amount": Decimal("11550000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "trade_date": datetime.date(2026, 6, 22),
        "open": Decimal("10.50"),
        "high": Decimal("10.80"),
        "low": Decimal("10.40"),
        "close": Decimal("10.70"),
        "pre_close": Decimal("10.50"),
        "change": Decimal("0.20"),
        "pct_chg": Decimal("1.90"),
        "vol": 1200000,
        "amount": Decimal("12840000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "trade_date": datetime.date(2026, 6, 23),
        "open": Decimal("10.70"),
        "high": Decimal("10.90"),
        "low": Decimal("10.60"),
        "close": Decimal("10.80"),
        "pre_close": Decimal("10.70"),
        "change": Decimal("0.10"),
        "pct_chg": Decimal("0.93"),
        "vol": 1050000,
        "amount": Decimal("11340000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "trade_date": datetime.date(2026, 6, 24),
        "open": Decimal("10.80"),
        "high": Decimal("11.00"),
        "low": Decimal("10.70"),
        "close": Decimal("10.90"),
        "pre_close": Decimal("10.80"),
        "change": Decimal("0.10"),
        "pct_chg": Decimal("0.93"),
        "vol": 1300000,
        "amount": Decimal("14170000.00"),
    },
    {
        "ts_code": "600000.SH",
        "trade_date": datetime.date(2026, 6, 24),
        "open": Decimal("7.50"),
        "high": Decimal("7.60"),
        "low": Decimal("7.40"),
        "close": Decimal("7.55"),
        "pre_close": Decimal("7.45"),
        "change": Decimal("0.10"),
        "pct_chg": Decimal("1.34"),
        "vol": 800000,
        "amount": Decimal("6040000.00"),
    },
]

MVD_DAILY_INDICATORS = [
    {
        "ts_code": "000001.SZ",
        "trade_date": datetime.date(2026, 6, 24),
        "pe": Decimal("8.5"),
        "pe_ttm": Decimal("8.2"),
        "pb": Decimal("0.8"),
        "ps": Decimal("1.2"),
        "ps_ttm": Decimal("1.1"),
        "dv_ratio": Decimal("3.5"),
        "dv_ttm": Decimal("3.6"),
        "total_mv": Decimal("190000000000.00"),
        "circ_mv": Decimal("190000000000.00"),
        "total_share": 19405918118,
        "float_share": 19405918118,
        "free_share": 10000000000,
        "turnover_rate": Decimal("0.8"),
        "turnover_rate_f": Decimal("1.2"),
        "volume_ratio": Decimal("1.0"),
    },
    {
        "ts_code": "600000.SH",
        "trade_date": datetime.date(2026, 6, 24),
        "pe": Decimal("5.2"),
        "pe_ttm": Decimal("5.0"),
        "pb": Decimal("0.5"),
        "ps": Decimal("0.8"),
        "ps_ttm": Decimal("0.7"),
        "dv_ratio": Decimal("5.0"),
        "dv_ttm": Decimal("5.2"),
        "total_mv": Decimal("120000000000.00"),
        "circ_mv": Decimal("120000000000.00"),
        "total_share": 29352084000,
        "float_share": 29352084000,
        "free_share": 15000000000,
        "turnover_rate": Decimal("0.5"),
        "turnover_rate_f": Decimal("0.8"),
        "volume_ratio": Decimal("0.9"),
    },
]

MVD_MONEYFLOW_DAILY = {
    "ts_code": "000001.SZ",
    "trade_date": datetime.date(2026, 6, 24),
    "buy_sm_vol": 100000,
    "buy_sm_amount": Decimal("1000000.00"),
    "sell_sm_vol": 90000,
    "sell_sm_amount": Decimal("900000.00"),
    "buy_md_vol": 50000,
    "buy_md_amount": Decimal("500000.00"),
    "sell_md_vol": 45000,
    "sell_md_amount": Decimal("450000.00"),
    "buy_lg_vol": 20000,
    "buy_lg_amount": Decimal("200000.00"),
    "sell_lg_vol": 18000,
    "sell_lg_amount": Decimal("180000.00"),
    "buy_elg_vol": 10000,
    "buy_elg_amount": Decimal("100000.00"),
    "sell_elg_vol": 9000,
    "sell_elg_amount": Decimal("90000.00"),
    "net_mf_vol": 18000,
    "net_mf_amount": Decimal("180000.00"),
}

MVD_NORTHBOUND_HOLDING = {
    "ts_code": "000001.SZ",
    "trade_date": datetime.date(2026, 6, 24),
    "name": "平安银行",
    "vol": 100000000,
    "ratio": Decimal("0.51"),
    "exchange": "SZSE",
}

# ===========================================================================
# L2 财务层（function-scoped）
# ===========================================================================

# 8 期财报，ROE 设计为递增（12.5→16.0），支持趋势验证
MVD_FINANCIAL_REPORTS = [
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2024, 3, 31),
        "ann_date": datetime.date(2024, 4, 15),
        "report_type": "1",
        "total_revenue": Decimal("4000000000.00"),
        "revenue": Decimal("4000000000.00"),
        "n_income": Decimal("600000000.00"),
        "n_income_attr_p": Decimal("600000000.00"),
        "total_assets": Decimal("200000000000.00"),
        "total_liab": Decimal("120000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("80000000000.00"),
        "roe": Decimal("12.5"),
        "roe_dt": Decimal("12.3"),
        "grossprofit_margin": Decimal("35.5"),
        "netprofit_margin": Decimal("15.0"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("10.5"),
        "netprofit_yoy": Decimal("8.2"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("800000000.00"),
        "money_cap": Decimal("12000000000.00"),
        "accounts_receiv": Decimal("3000000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2024, 6, 30),
        "ann_date": datetime.date(2024, 8, 15),
        "report_type": "1",
        "total_revenue": Decimal("8200000000.00"),
        "revenue": Decimal("8200000000.00"),
        "n_income": Decimal("1250000000.00"),
        "n_income_attr_p": Decimal("1250000000.00"),
        "total_assets": Decimal("205000000000.00"),
        "total_liab": Decimal("123000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("82000000000.00"),
        "roe": Decimal("13.0"),
        "roe_dt": Decimal("12.8"),
        "grossprofit_margin": Decimal("35.8"),
        "netprofit_margin": Decimal("15.2"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("11.0"),
        "netprofit_yoy": Decimal("8.5"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("1500000000.00"),
        "money_cap": Decimal("12500000000.00"),
        "accounts_receiv": Decimal("3100000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2024, 9, 30),
        "ann_date": datetime.date(2024, 10, 25),
        "report_type": "1",
        "total_revenue": Decimal("12500000000.00"),
        "revenue": Decimal("12500000000.00"),
        "n_income": Decimal("1900000000.00"),
        "n_income_attr_p": Decimal("1900000000.00"),
        "total_assets": Decimal("210000000000.00"),
        "total_liab": Decimal("126000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("84000000000.00"),
        "roe": Decimal("13.5"),
        "roe_dt": Decimal("13.2"),
        "grossprofit_margin": Decimal("36.0"),
        "netprofit_margin": Decimal("15.2"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("11.5"),
        "netprofit_yoy": Decimal("9.0"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("2200000000.00"),
        "money_cap": Decimal("13000000000.00"),
        "accounts_receiv": Decimal("3200000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2024, 12, 31),
        "ann_date": datetime.date(2025, 3, 15),
        "report_type": "1",
        "total_revenue": Decimal("17000000000.00"),
        "revenue": Decimal("17000000000.00"),
        "n_income": Decimal("2500000000.00"),
        "n_income_attr_p": Decimal("2500000000.00"),
        "total_assets": Decimal("215000000000.00"),
        "total_liab": Decimal("129000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("86000000000.00"),
        "roe": Decimal("14.0"),
        "roe_dt": Decimal("13.7"),
        "grossprofit_margin": Decimal("36.2"),
        "netprofit_margin": Decimal("14.7"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("12.0"),
        "netprofit_yoy": Decimal("9.5"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("3000000000.00"),
        "money_cap": Decimal("14000000000.00"),
        "accounts_receiv": Decimal("3300000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 3, 31),
        "ann_date": datetime.date(2025, 4, 15),
        "report_type": "1",
        "total_revenue": Decimal("4500000000.00"),
        "revenue": Decimal("4500000000.00"),
        "n_income": Decimal("700000000.00"),
        "n_income_attr_p": Decimal("700000000.00"),
        "total_assets": Decimal("220000000000.00"),
        "total_liab": Decimal("132000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("88000000000.00"),
        "roe": Decimal("14.5"),
        "roe_dt": Decimal("14.2"),
        "grossprofit_margin": Decimal("36.5"),
        "netprofit_margin": Decimal("15.5"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("12.5"),
        "netprofit_yoy": Decimal("16.6"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("1000000000.00"),
        "money_cap": Decimal("15000000000.00"),
        "accounts_receiv": Decimal("3400000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 6, 30),
        "ann_date": datetime.date(2025, 8, 15),
        "report_type": "1",
        "total_revenue": Decimal("9200000000.00"),
        "revenue": Decimal("9200000000.00"),
        "n_income": Decimal("1450000000.00"),
        "n_income_attr_p": Decimal("1450000000.00"),
        "total_assets": Decimal("225000000000.00"),
        "total_liab": Decimal("135000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("90000000000.00"),
        "roe": Decimal("15.0"),
        "roe_dt": Decimal("14.7"),
        "grossprofit_margin": Decimal("36.8"),
        "netprofit_margin": Decimal("15.7"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("12.2"),
        "netprofit_yoy": Decimal("16.0"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("1900000000.00"),
        "money_cap": Decimal("15500000000.00"),
        "accounts_receiv": Decimal("3500000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 9, 30),
        "ann_date": datetime.date(2025, 10, 25),
        "report_type": "1",
        "total_revenue": Decimal("14000000000.00"),
        "revenue": Decimal("14000000000.00"),
        "n_income": Decimal("2200000000.00"),
        "n_income_attr_p": Decimal("2200000000.00"),
        "total_assets": Decimal("230000000000.00"),
        "total_liab": Decimal("138000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("92000000000.00"),
        "roe": Decimal("15.5"),
        "roe_dt": Decimal("15.1"),
        "grossprofit_margin": Decimal("37.0"),
        "netprofit_margin": Decimal("15.7"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("12.0"),
        "netprofit_yoy": Decimal("15.8"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("2800000000.00"),
        "money_cap": Decimal("16000000000.00"),
        "accounts_receiv": Decimal("3600000000.00"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 12, 31),
        "ann_date": datetime.date(2026, 3, 15),
        "report_type": "1",
        "total_revenue": Decimal("19000000000.00"),
        "revenue": Decimal("19000000000.00"),
        "n_income": Decimal("2900000000.00"),
        "n_income_attr_p": Decimal("2900000000.00"),
        "total_assets": Decimal("235000000000.00"),
        "total_liab": Decimal("141000000000.00"),
        "total_hldr_eqy_exc_min_int": Decimal("94000000000.00"),
        "roe": Decimal("16.0"),
        "roe_dt": Decimal("15.6"),
        "grossprofit_margin": Decimal("37.2"),
        "netprofit_margin": Decimal("15.2"),
        "debt_to_assets": Decimal("60.0"),
        "or_yoy": Decimal("11.8"),
        "netprofit_yoy": Decimal("16.0"),
        "goodwill": Decimal("50000000.00"),
        "audit_result": "标准无保留意见",
        "n_cashflow_act": Decimal("3800000000.00"),
        "money_cap": Decimal("16500000000.00"),
        "accounts_receiv": Decimal("3700000000.00"),
    },
]

MVD_FINA_AUDIT = {
    "ts_code": "000001.SZ",
    "end_date": datetime.date(2025, 12, 31),
    "ann_date": datetime.date(2026, 3, 15),
    "audit_result": "标准无保留意见",
    "audit_sign": "签字注册会计师",
    "audit_fees": Decimal("5000000.00"),
    "audit_agency": "普华永道中天会计师事务所",
}

MVD_FINA_MAINBZ = {
    "ts_code": "000001.SZ",
    "end_date": datetime.date(2025, 12, 31),
    "ann_date": datetime.date(2026, 3, 15),
    "bz_item": "利息收入",
    "bz_sales": Decimal("80000000000.00"),
    "bz_profit": Decimal("40000000000.00"),
    "bz_cost": Decimal("40000000000.00"),
    "curr_type": "CNY",
    "update_flag": "1",
}

MVD_DIVIDEND = {
    "ts_code": "000001.SZ",
    "end_date": datetime.date(2025, 12, 31),
    "ann_date": datetime.date(2026, 3, 15),
    "div_proc": "实施",
    "stk_div": Decimal("0.0"),
    "stk_bo_rate": Decimal("0.0"),
    "stk_co_rate": Decimal("0.0"),
    "cash_div": Decimal("0.5"),
    "cash_div_tax": Decimal("0.5"),
    "record_date": datetime.date(2026, 6, 15),
    "ex_date": datetime.date(2026, 6, 16),
}

# ===========================================================================
# L3 辅助层（function-scoped）
# ===========================================================================

MVD_PLEDGE_STAT = {
    "ts_code": "000001.SZ",
    "end_date": datetime.date(2026, 5, 31),
    "ann_date": datetime.date(2026, 6, 1),
    "pledge_count": 2,
    "unrest_pledge": Decimal("100000000.00"),
    "rest_pledge": Decimal("0.00"),
    "total_share": Decimal("19405918118.00"),
    "pledge_ratio": Decimal("10.5"),
}

# 3 条前十大股东，支持持股比例合计验证（2.5 + 1.8 + 1.2 = 5.5）
MVD_TOP10_HOLDERS = [
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 12, 31),
        "ann_date": datetime.date(2026, 3, 15),
        "holder_name": "中国证券金融股份有限公司",
        "hold_amount": 500000000,
        "hold_ratio": Decimal("2.5"),
        "hold_float_ratio": Decimal("2.5"),
        "hold_change": 0,
        "holder_type": "国家队",
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 12, 31),
        "ann_date": datetime.date(2026, 3, 15),
        "holder_name": "中央汇金投资有限责任公司",
        "hold_amount": 360000000,
        "hold_ratio": Decimal("1.8"),
        "hold_float_ratio": Decimal("1.8"),
        "hold_change": 10000000,
        "holder_type": "国家队",
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 12, 31),
        "ann_date": datetime.date(2026, 3, 15),
        "holder_name": "香港中央结算有限公司",
        "hold_amount": 240000000,
        "hold_ratio": Decimal("1.2"),
        "hold_float_ratio": Decimal("1.2"),
        "hold_change": -5000000,
        "holder_type": "外资",
    },
]

# 2 期股东人数，支持变化趋势验证（300000 → 295000，减少）
MVD_STK_HOLDERNUMBER = [
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 6, 30),
        "ann_date": datetime.date(2025, 7, 25),
        "holder_num": 300000,
        "holder_num_change": -5000,
        "holder_num_ratio": Decimal("-1.6"),
    },
    {
        "ts_code": "000001.SZ",
        "end_date": datetime.date(2025, 12, 31),
        "ann_date": datetime.date(2026, 3, 15),
        "holder_num": 295000,
        "holder_num_change": -5000,
        "holder_num_ratio": Decimal("-1.7"),
    },
]

MVD_BLOCK_TRADE = {
    "ts_code": "000001.SZ",
    "trade_date": datetime.date(2026, 6, 24),
    "price": Decimal("10.20"),
    "vol": 1000000,
    "amount": Decimal("10200000.00"),
    "buyer": "国泰君安证券股份有限公司总部",
    "seller": "机构专用",
}

MVD_TOP_LIST = {
    "trade_date": datetime.date(2026, 6, 24),
    "ts_code": "000001.SZ",
    "name": "平安银行",
    "close": Decimal("10.50"),
    "pct_change": Decimal("10.01"),
    "turnover_rate": Decimal("2.5"),
    "amount": Decimal("2000000000.00"),
    "l_sell": Decimal("120000000.00"),
    "l_buy": Decimal("180000000.00"),
    "l_amount": Decimal("300000000.00"),
    "net_amount": Decimal("60000000.00"),
    "net_rate": Decimal("3.0"),
    "amount_rate": Decimal("15.0"),
    "float_values": Decimal("190000000000.00"),
    "reason": "日涨幅偏离值达到7%的前五只证券",
}

# ===========================================================================
# L4 宏观层（function-scoped）
# ===========================================================================

MVD_MACRO_ECONOMY = {
    "period": datetime.date(2026, 5, 31),
    "publish_date": datetime.date(2026, 6, 10),
    "m2": Decimal("300000000000000.00"),
    "m2_yoy": Decimal("8.5"),
    "m1": Decimal("70000000000000.00"),
    "m1_yoy": Decimal("1.2"),
    "m0": Decimal("11000000000000.00"),
    "m0_yoy": Decimal("10.5"),
    "cpi": Decimal("1.8"),
    "ppi": Decimal("-1.2"),
    # Phase 2D §3.2.6：cn_gdp 全链路补全（8 个 GDP 字段）
    "gdp": Decimal("35000000.00"),
    "gdp_yoy": Decimal("5.2"),
    "pi": Decimal("2500000.00"),
    "pi_yoy": Decimal("3.1"),
    "si": Decimal("14000000.00"),
    "si_yoy": Decimal("5.0"),
    "ti": Decimal("18500000.00"),
    "ti_yoy": Decimal("5.8"),
}

# 注意：ORM 中 Python 属性名与数据库列名不同（通过 name= 映射）。
# insert().values(dict) 按 column key（即 Python 属性名）匹配，必须使用属性名。
MVD_SHIBOR_DAILY = {
    "date": datetime.date(2026, 6, 24),
    "on": Decimal("1.85"),
    "w1": Decimal("1.95"),  # name="1w"
    "w2": Decimal("2.10"),  # name="2w"
    "m1": Decimal("2.25"),  # name="1m"
    "m3": Decimal("2.35"),  # name="3m"
    "m6": Decimal("2.40"),  # name="6m"
    "m9": Decimal("2.45"),  # name="9m"
    "y1": Decimal("2.50"),  # name="1y"
}

MVD_MARKET_NEWS = {
    "content": "央行公开市场开展1000亿元逆回购操作",
    "content_hash": "dummy_hash_value_for_prompt_consistency_testing_2026",
    "tags": "货币政策,逆回购",
    "publish_time": datetime.datetime(2026, 6, 24, 9, 0, 0),
    "source": "中国人民银行",
}
