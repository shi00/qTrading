"""
Data Dictionary Definitions.
Separates business metadata from UI translations.
"""

# Common column definitions that apply across most tables
# These are used as a fallback if a table-specific definition is not found.
COMMON_COLUMNS = {
    # Identifiers
    "ts_code": "col_ts_code",
    "symbol": "col_symbol",
    "name": "col_name",
    "id": "col_id",
    # Date & Time
    "trade_date": "col_trade_date",
    "ann_date": "col_ann_date",
    "end_date": "col_end_date",
    "list_date": "col_list_date",
    "created_at": "col_created_at",
    "updated_at": "col_updated_at",
    "update_flag": "col_update_flag",
    "publish_time": "col_publish_time",
    "cal_date": "col_cal_date",
    "pretrade_date": "col_pretrade_date",
    "suspend_timing": "col_suspend_timing",
    "exp_date": "col_exp_date",
    "record_date": "col_record_date",
    "ex_date": "col_ex_date",
    # Market Data (Price & Volume)
    "open": "col_open",
    "high": "col_high",
    "low": "col_low",
    "close": "col_close",
    "pre_close": "col_pre_close",
    "change": "col_change",
    "pct_change": "col_pct_chg",
    "pct_chg": "col_pct_chg",
    "vol": "col_vol",
    "volume": "col_volume",
    "amount": "col_amount",
    "turnover_rate": "col_turnover_rate",
    "turnover_rate_f": "col_turnover_rate_f",
    "volume_ratio": "col_volume_ratio",
    "adj_factor": "col_adj_factor",
    # Valuation
    "pe": "col_pe",
    "pe_ttm": "col_pe_ttm",
    "pb": "col_pb",
    "ps": "col_ps",
    "ps_ttm": "col_ps_ttm",
    "dv_ratio": "col_dv_ratio",
    "dv_ttm": "col_dv_ttm",
    "total_mv": "col_total_mv",
    "circ_mv": "col_circ_mv",
    "float_mv": "col_float_mv",
    "float_values": "col_float_values",
    # Share Capital
    "total_share": "col_total_share",
    "float_share": "col_float_share",
    "free_share": "col_free_share",
    # Financial Basics
    "revenue": "col_revenue",
    "total_revenue": "col_total_revenue",
    "total_cost": "col_total_cost",
    "total_profit": "col_total_profit",
    "n_income": "col_n_income",
    "n_income_attr_p": "col_n_income_attr_p",
    "total_assets": "col_total_assets",
    "total_liab": "col_total_liab",
    "total_hldr_eqy_exc_min_int": "col_total_hldr_eqy_exc_min_int",
    # Financial Ratios
    "roe": "col_roe",
    "roe_dt": "col_roe_dt",
    "grossprofit_margin": "col_grossprofit_margin",
    "netprofit_margin": "col_netprofit_margin",
    "debt_to_assets": "col_debt_to_assets",
    "or_yoy": "col_or_yoy",
    "netprofit_yoy": "col_netprofit_yoy",
    "goodwill": "col_goodwill",
    # Money Flow
    "buy_sm_vol": "col_buy_sm_vol",
    "sell_sm_vol": "col_sell_sm_vol",
    "buy_sm_amount": "col_buy_sm_amount",
    "sell_sm_amount": "col_sell_sm_amount",
    "buy_md_vol": "col_buy_md_vol",
    "sell_md_vol": "col_sell_md_vol",
    "buy_md_amount": "col_buy_md_amount",
    "sell_md_amount": "col_sell_md_amount",
    "buy_lg_vol": "col_buy_lg_vol",
    "sell_lg_vol": "col_sell_lg_vol",
    "buy_lg_amount": "col_buy_lg_amount",
    "sell_lg_amount": "col_sell_lg_amount",
    "buy_elg_vol": "col_buy_elg_vol",
    "sell_elg_vol": "col_sell_elg_vol",
    "buy_elg_amount": "col_buy_elg_amount",
    "sell_elg_amount": "col_sell_elg_amount",
    "net_mf_amount": "col_net_mf_amount",
    "net_mf_vol": "col_net_mf_vol",
    "n_cashflow_act": "col_n_cashflow_act",
    "delist_date": "col_delist_date",
    # Others
    "area": "col_area",
    "industry": "col_industry",
    "market": "col_market",
    "list_status": "col_list_status",
    "audit_result": "col_audit_result",
    "report_type": "col_report_type",
    "comp_type": "col_comp_type",
    "content": "col_content",
    "source": "col_source",
    "tags": "col_tags",
    "is_open": "col_is_open",
    "status": "col_status",
    "exchange": "col_exchange",
    "concept_name": "col_concept_name",
    "concept_id": "col_concept_id",
    # AI Analysis Results (used by screener results table)
    "ai_score": "col_ai_score",
    "ai_reason": "col_ai_reason",
    "thinking": "col_thinking",
    "confidence": "col_confidence",
    # Screening & Review
    "strategy_name": "col_strategy_name",
    "prediction_result": "col_prediction_result",
    "review_status": "col_review_status",
    "t1_price": "col_t1_price",
    "t5_price": "col_t5_price",
    "t1_pct": "col_t1_pct",
    "t5_pct": "col_t5_pct",
    "index_pct": "col_index_pct",
    "alpha": "col_alpha",
    "run_id": "col_run_id",
    "params_snapshot": "col_params_snapshot",
    # Sync Status
    "table_name": "col_table_name",
    "last_sync_date": "col_last_sync_date",
    "last_data_date": "col_last_data_date",
    "record_count": "col_record_count",
    "last_result_status": "col_last_result_status",
    # Technical Indicators (dynamic columns)
    "rsi_6": "col_rsi_6",
}

# Table Definitions with Table-Specific Column Overrides
TABLE_DEFINITIONS = {
    "stock_basic": {"alias": "tab_stock_basic", "columns": {}},
    "stock_concepts": {
        "alias": "tab_stock_concepts",
        "desc": "股票概念映射表 (包含传统 Tushare 原生概念，以及通过 AI 自动扫描剥离出的 AI_LLM_<sha256> 前缀概念)",
        "columns": {},
    },
    "ai_concept_failures": {
        "alias": "tab_ai_concept_failures",
        "desc": "AI 概念打标错题本：失败股票重试队列，含 retry_count/next_retry_at 等字段",
        "columns": {
            "last_error": "col_last_error",
            "retry_count": "col_retry_count",
            "last_attempt_at": "col_last_attempt_at",
            "next_retry_at": "col_next_retry_at",
        },
    },
    "daily_quotes": {
        "alias": "tab_daily_quotes",
        "quality_config": {
            "tier": 3,
            "monitor": True,
            "critical": True,
            "frequency": "daily",
        },
        "columns": {},
    },
    "financial_reports": {
        "alias": "tab_financial_reports",
        "desc": "财务报表(主表)",
        "sync_config": {"strategy": "specialized_financial"},
        "quality_config": {"tier": 3, "monitor": True, "critical": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "report_type": "col_report_type",
            "total_revenue": "col_total_revenue",
            "revenue": "col_revenue",
            "n_income": "col_n_income",
            "n_income_attr_p": "col_n_income_attr_p",
            "total_assets": "col_total_assets",
            "total_liab": "col_total_liab",
            "total_hldr_eqy_exc_min_int": "col_total_hldr_eqy_exc_min_int",
            "roe": "col_roe",
            "roe_dt": "col_roe_dt",
            "grossprofit_margin": "col_grossprofit_margin",
            "netprofit_margin": "col_netprofit_margin",
            "debt_to_assets": "col_debt_to_assets",
            "or_yoy": "col_or_yoy",
            "netprofit_yoy": "col_netprofit_yoy",
            "goodwill": "col_goodwill",
            "audit_result": "col_audit_result",
            "n_cashflow_act": "col_n_cashflow_act",
            "money_cap": "col_money_cap",
            "accounts_receiv": "col_accounts_receiv",
        },
    },
    "daily_indicators": {
        "alias": "tab_daily_indicators",
        "desc": "每日指标(PE/PB)",
        "quality_config": {
            "tier": 3,
            "monitor": True,
            "critical": True,
            "frequency": "daily",
        },
        "columns": {},
    },
    "fina_forecast": {
        "alias": "tab_fina_forecast",
        "desc": "业绩预告",
        "sync_config": {
            "strategy": "batch",
            "api": "get_forecast",
            "date_col": "ann_date",
            "keys": ["ts_code", "end_date", "ann_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "type": "col_type",
            "p_change_min": "col_p_change_min",
            "p_change_max": "col_p_change_max",
            "net_profit_min": "col_net_profit_min",
            "net_profit_max": "col_net_profit_max",
        },
    },
    "fina_audit": {
        "alias": "tab_fina_audit",
        "desc": "审计意见",
        "sync_config": {
            "strategy": "stock",
            "api": "get_fina_audit",
            "date_col": "end_date",
            "keys": ["ts_code", "end_date"],
        },
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "audit_result": "col_audit_result",
            "audit_sign": "col_audit_sign",
            "audit_fees": "col_audit_fees",
            "audit_agency": "col_audit_agency",
        },
    },
    "fina_mainbz": {
        "alias": "tab_fina_mainbz",
        "desc": "主营业务",
        "sync_config": {
            "strategy": "stock",
            "api": "get_fina_mainbz",
            "date_col": "end_date",
            "keys": ["ts_code", "end_date"],
        },
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "bz_item": "col_bz_item",
            "bz_sales": "col_bz_sales",
            "bz_profit": "col_bz_profit",
            "bz_cost": "col_bz_cost",
            "curr_type": "col_curr_type",
            "update_flag": "col_update_flag",
        },
    },
    "dividend": {
        "alias": "tab_dividend",
        "desc": "分红送转",
        "sync_config": {
            "strategy": "batch",
            "api": "get_dividend",
            "date_col": "ann_date",
            "keys": ["ts_code", "ann_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "div_proc": "col_div_proc",
            "stk_div": "col_stk_div",
            "stk_bo_rate": "col_stk_bo_rate",
            "stk_co_rate": "col_stk_co_rate",
            "cash_div": "col_cash_div",
            "cash_div_tax": "col_cash_div_tax",
            "record_date": "col_record_date",
            "ex_date": "col_ex_date",
        },
    },
    "top_list": {
        "alias": "tab_top_list",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "trade_date": "col_trade_date",
            "ts_code": "col_ts_code",
            "name": "col_name",
            "close": "col_close",
            "pct_change": "col_pct_change",
            "turnover_rate": "col_turnover_rate",
            "amount": "col_amount",
            "reason": "col_reason",
            "l_sell": "col_l_sell",
            "l_buy": "col_l_buy",
            "l_amount": "col_l_amount",
            "net_amount": "col_net_amount",
            "net_rate": "col_net_rate",
            "amount_rate": "col_amount_rate",
            "float_values": "col_float_values",
        },
    },
    "top_inst": {
        "alias": "tab_top_inst",
        "desc": "龙虎榜机构席位交易明细（Phase 2E top_inst 已封装 API 激活）",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "name": "col_name",
            "close": "col_close",
            "pct_change": "col_pct_change",
            "amount": "col_amount",
            "net_amount": "col_net_amount",
            "buy_amount": "col_buy_amount",
            "buy_value": "col_buy_value",
            "sell_amount": "col_sell_amount",
            "sell_value": "col_sell_value",
        },
    },
    "stk_limit": {
        "alias": "tab_stk_limit",
        "desc": "每日涨跌停价格（Phase 2G stk_limit 涨跌停价格，仅数据层，不注入 AI）",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "pre_close": "col_pre_close",
            "up_limit": "col_up_limit",
            "down_limit": "col_down_limit",
            "limit_type": "col_limit_type",
        },
    },
    "block_trade": {
        "alias": "tab_block_trade",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "price": "col_price",
            "vol": "col_vol",
            "amount": "col_amount",
            "buyer": "col_buyer",
            "seller": "col_seller",
        },
    },
    "moneyflow_daily": {
        "alias": "tab_moneyflow_daily",
        "desc": "日资金流",
        "quality_config": {
            "tier": 2,
            "monitor": True,
            "critical": True,
            "frequency": "daily",
        },
        "columns": {},
    },
    "index_daily": {"alias": "tab_index_daily", "columns": {}},
    "index_dailybasic": {"alias": "tab_index_dailybasic", "columns": {}},
    "northbound_holding": {
        "alias": "tab_northbound_holding",
        "quality_config": {"tier": 2, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "name": "col_name",
            "vol": "col_vol",
            "ratio": "col_ratio",
            "exchange": "col_exchange",
        },
    },
    "margin_daily": {
        "alias": "tab_margin_daily",
        "desc": "融资融券",
        "quality_config": {"tier": 1, "monitor": True},
        "type": "stock",
        "columns": {
            "ts_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "rzye": "col_rzye",
            "rqye": "col_rqye",
            "rzmre": "col_rzmre",
            "rqyl": "col_rqyl",
            "rzrqye": "col_rzrqye",
        },
    },
    "pledge_stat": {
        "alias": "tab_pledge_stat",
        "desc": "股权质押",
        "sync_config": {
            "strategy": "stock",
            "api": "get_pledge_stat",
            "date_col": "end_date",
            "keys": ["ts_code", "end_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "pledge_count": "col_pledge_count",
            "unrest_pledge": "col_unrest_pledge",
            "rest_pledge": "col_rest_pledge",
            "total_share": "col_total_share",
            "pledge_ratio": "col_pledge_ratio",
        },
    },
    "pledge_detail": {
        "alias": "tab_pledge_detail",
        "desc": "股权质押明细",
        "sync_config": {
            "strategy": "stock",
            "api": "get_pledge_detail",
            "date_col": "end_date",
            "keys": ["ts_code", "end_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "pledge_amount": "col_pledge_amount",
            "unlimited_pledge_amount": "col_unlimited_pledge_amount",
            "limited_pledge_amount": "col_limited_pledge_amount",
            "total_pledge_amount": "col_total_pledge_amount",
            "pledge_ratio": "col_pledge_ratio",
        },
    },
    "repurchase": {
        "alias": "tab_repurchase",
        "desc": "股票回购",
        "sync_config": {
            "strategy": "batch",
            "api": "get_repurchase",
            "date_col": "ann_date",
            "keys": ["ts_code", "ann_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "ts_code": "col_ts_code",
            "ann_date": "col_ann_date",
            "end_date": "col_end_date",
            "proc": "col_proc",
            "exp_date": "col_exp_date",
            "vol": "col_vol",
            "amount": "col_amount",
            "high_limit": "col_high_limit",
            "low_limit": "col_low_limit",
        },
    },
    "limit_list": {
        "alias": "tab_limit_list",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "trade_date": "col_trade_date",
            "ts_code": "col_ts_code",
            "name": "col_name",
            "close": "col_close",
            "pct_chg": "col_pct_chg",
            "fd_amount": "col_fd_amount",
            "first_time": "col_first_time",
            "last_time": "col_last_time",
            "open_times": "col_open_times",
            "limit_type": "col_limit_type",
            "industry": "col_industry",
            "amount": "col_amount",
            "limit_amount": "col_limit_amount",
            "float_mv": "col_float_mv",
            "total_mv": "col_total_mv",
            "turnover_ratio": "col_turnover_ratio",
            "up_stat": "col_up_stat",
            "limit_times": "col_limit_times",
        },
    },
    "suspend_d": {
        "alias": "tab_suspend_d",
        "desc": "停复牌信息",
        "quality_config": {"tier": 1, "monitor": True},
        "type": "stock",
        "columns": {
            "ts_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "suspend_timing": "col_suspend_timing",
            "suspend_type": "col_suspend_type_name",
        },
    },
    "market_news": {
        "alias": "tab_market_news",
        "columns": {
            "content_hash": "col_content_hash",
        },
    },
    "trade_cal": {"alias": "tab_trade_cal", "columns": {}},
    "screening_history": {
        "alias": "tab_screening_history",
        "columns": {},
    },
    "screening_thinking": {
        "alias": "tab_screening_thinking",
        "columns": {
            "id": "col_id",
            "history_id": "col_history_id",
            "thinking": "col_thinking",
            "created_at": "col_created_at",
        },
    },
    "sync_status": {
        "alias": "tab_sync_status",
        "columns": {
            "table_name": "col_table_name",
            "last_sync_date": "col_last_sync_date",
            "last_data_date": "col_last_data_date",
            "record_count": "col_record_count",
            "status": "col_status",
            "last_result_status": "col_last_result_status",
            "error_message": "col_error_message",
            "error_count": "col_error_count",
        },
    },
    "stock_sync_status": {
        "alias": "tab_stock_sync_status",
        "columns": {
            "step4_completed_at": "col_step4_completed_at",
            "sync_version": "col_sync_version",
        },
    },
    # --- Phase 3: Policy-Driven AI Architecture ---
    "macro_economy": {
        "alias": "tab_macro_economy",
        "desc": "宏观经济",
        "sync_config": {"strategy": "macro", "type": "economic"},
        "quality_config": {"tier": 2, "monitor": True},
        "type": "global",
        "columns": {
            "period": "col_end_date",
            "publish_date": "col_publish_date",
            "m2": "col_m2",
            "m2_yoy": "col_m2_yoy",
            "m1": "col_m1",
            "m1_yoy": "col_m1_yoy",
            "m0": "col_m0",
            "m0_yoy": "col_m0_yoy",
            "cpi": "col_cpi",
            "ppi": "col_ppi",
            # Phase 2D §3.2.6：cn_gdp 全链路补全（8 个 GDP 字段）
            "gdp": "col_gdp",
            "gdp_yoy": "col_gdp_yoy",
            "pi": "col_pi",
            "pi_yoy": "col_pi_yoy",
            "si": "col_si",
            "si_yoy": "col_si_yoy",
            "ti": "col_ti",
            "ti_yoy": "col_ti_yoy",
        },
    },
    "shibor_daily": {
        "alias": "tab_shibor_daily",
        "desc": "Shibor利率",
        "sync_config": {"strategy": "macro", "type": "shibor"},
        "quality_config": {"tier": 2, "monitor": True},
        "type": "global",
        "columns": {
            "date": "col_trade_date",
            "on": "col_on",
            "1w": "col_1w",
            "2w": "col_2w",
            "1m": "col_1m",
            "3m": "col_3m",
            "6m": "col_6m",
            "9m": "col_9m",
            "1y": "col_1y",
        },
    },
    "stk_holdernumber": {
        "alias": "tab_stk_holdernumber",
        "desc": "股东户数",
        "sync_config": {"strategy": "holder", "api": "get_stk_holdernumber"},
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "holder_num": "col_holder_num",
            "holder_num_change": "col_holder_num_change",
            "holder_num_ratio": "col_holder_num_ratio",
        },
    },
    "top10_holders": {
        "alias": "tab_top10_holders",
        "desc": "前十大股东",
        "sync_config": {"strategy": "holder", "api": "get_top10_holders"},
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "ts_code": "col_ts_code",
            "end_date": "col_end_date",
            "ann_date": "col_ann_date",
            "holder_name": "col_holder_name",
            "hold_amount": "col_hold_amount",
            "hold_ratio": "col_hold_ratio",
            "hold_float_ratio": "col_hold_float_ratio",
            "hold_change": "col_hold_change",
            "holder_type": "col_holder_type",
        },
    },
    "index_weight": {
        "alias": "tab_index_weight",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "columns": {
            "index_code": "col_index_code",
            "con_code": "col_ts_code",
            "trade_date": "col_trade_date",
            "weight": "col_weight",
        },
    },
    "moneyflow_hsgt": {
        "alias": "tab_moneyflow_hsgt",
        "desc": "北向资金流",
        "sync_config": {"strategy": "market", "api": "moneyflow_hsgt"},
        "quality_config": {"tier": 2, "monitor": True},
        "type": "global",
        "columns": {
            "trade_date": "col_trade_date",
            "ggt_ss": "col_ggt_ss",
            "ggt_sz": "col_ggt_sz",
            "hgt": "col_hgt_north_money",
            "sgt": "col_sgt_north_money",
            "north_money": "col_north_money",
            "south_money": "col_south_money",
        },
    },
    "alembic_version": {
        "alias": "tab_alembic_version",
        "desc": "数据库版本",
        "columns": {"version_num": "col_version_num"},
    },
    "task_history": {
        "alias": "tab_task_history",
        "desc": "系统任务执行历史日志",
        "columns": {
            "id": "col_id",
            "name": "col_name",
            "task_type": "col_task_type",
            "status": "col_status",
            "progress": "col_progress",
            "description": "col_description",
            "error": "col_error",
            "result": "col_result",
            "created_at": "col_created_at",
            "started_at": "col_started_at",
            "completed_at": "col_completed_at",
        },
    },
    "app_state": {
        "alias": "tab_app_state",
        "desc": "应用全局状态键值存储",
        "columns": {
            "key": "col_key",
            "value": "col_value",
            "updated_at": "col_updated_at",
        },
    },
    "backtest_results": {
        "alias": "tab_backtest_results",
        "desc": "回测结果存储",
        "columns": {
            "id": "col_id",
            "run_id": "col_run_id",
            "strategy_name": "col_strategy_name",
            "params_snapshot": "col_params_snapshot",
            "start_date": "col_start_date",
            "end_date": "col_end_date",
            "initial_capital": "col_initial_capital",
            "total_return": "col_total_return",
            "annualized_return": "col_annualized_return",
            "sharpe_ratio": "col_sharpe_ratio",
            "max_drawdown": "col_max_drawdown",
            "calmar_ratio": "col_calmar_ratio",
            "ic_mean": "col_ic_mean",
            "ic_ir": "col_ic_ir",
            "win_rate": "col_win_rate",
            "profit_factor": "col_profit_factor",
            "total_trades": "col_total_trades",
            "volatility": "col_volatility",
            "information_ratio": "col_information_ratio",
            "tracking_error": "col_tracking_error",
            "nav_curve_json": "col_nav_curve_json",
            "trades_json": "col_trades_json",
            "period_stats_json": "col_period_stats_json",
            "execution_price": "col_execution_price",
            "allow_limit_up_buy": "col_allow_limit_up_buy",
            "allow_limit_down_sell": "col_allow_limit_down_sell",
            "slippage_model": "col_slippage_model",
            "app_version": "col_app_version",
            "executed_at": "col_executed_at",
            "duration_ms": "col_duration_ms",
        },
    },
}


def validate_schema_definitions(strict: bool = False):
    """
    Validates that all SQLAlchemy ORM models have a corresponding entry in TABLE_DEFINITIONS.
    Logs warnings for any missing definitions to help maintain data dictionary consistency.
    Also validates column-level consistency between ORM and data dictionary.

    For Alembic migration ↔ ORM full-attribute consistency checks (column types,
    nullable, server_default, primary keys, foreign keys, indexes, unique constraints),
    see ``tests/integration/test_orm_migration_consistency.py`` which runs against an
    isolated PostgreSQL database after ``alembic upgrade head``.

    Args:
        strict: If True, raises ValueError on any schema inconsistency.
    """
    import logging
    import os

    logger = logging.getLogger(__name__)

    try:
        from data.persistence.models import Base

        db_tables = set(Base.metadata.tables.keys())
        defined_tables = set(TABLE_DEFINITIONS.keys())

        IGNORED_TABLES = {
            "stock_sync_status",
            "alembic_version",
        }

        errors = []
        missing_defs = db_tables - defined_tables - IGNORED_TABLES
        if missing_defs:
            msg = f"The following tables are in ORM models but missing from TABLE_DEFINITIONS: {missing_defs}"
            logger.warning("[DataDict] %s", msg)
            logger.warning("[DataDict] Please update data_dictionary.py to ensure health checks and UI work correctly.")
            errors.append(msg)

        extra_defs = defined_tables - db_tables - IGNORED_TABLES
        if extra_defs:
            msg = f"The following tables are in TABLE_DEFINITIONS but not in ORM: {extra_defs}"
            logger.warning("[DataDict] %s", msg)
            errors.append(msg)

        for table_name in defined_tables - IGNORED_TABLES:
            if table_name not in db_tables:
                continue

            orm_table = Base.metadata.tables[table_name]
            orm_cols = set(c.name for c in orm_table.columns)

            dd_def = TABLE_DEFINITIONS.get(table_name, {})
            dd_table_cols = set(dd_def.get("columns", {}).keys())
            dd_cols_with_common = dd_table_cols | set(COMMON_COLUMNS.keys())

            missing_cols = orm_cols - dd_cols_with_common - {"updated_at", "created_at"}
            if missing_cols:
                msg = f"Table '{table_name}': ORM columns missing from data dictionary: {missing_cols}"
                logger.warning("[DataDict] %s", msg)
                errors.append(msg)

            phantom_cols = dd_table_cols - orm_cols
            if phantom_cols:
                msg = f"Table '{table_name}': Data dictionary has phantom columns not in ORM: {phantom_cols}"
                logger.warning("[DataDict] %s", msg)
                errors.append(msg)

        logger.info(
            "[DataDict] Schema validation completed. %s tables verified.",
            len(db_tables),
        )

        is_strict = strict or os.environ.get("STRICT_SCHEMA_GATE") == "1"
        if is_strict and errors:
            raise ValueError("Schema inconsistencies found:\n" + "\n".join(errors))

    except ValueError as e:
        logger.error("[DataDict] Schema validation failed in strict mode: %s", e)
        raise
    except Exception as e:
        logger.error("[DataDict] ORM validation failed: %s", e)
        if strict:
            raise
