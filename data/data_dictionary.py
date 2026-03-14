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
    "pct_chg": "col_pct_chg",
    "vol": "col_vol",
    "volume": "col_volume",
    "amount": "col_amount",
    "turnover_rate": "col_turnover_rate",
    "turnover_rate_f": "col_turnover_rate_f",
    "volume_ratio": "col_volume_ratio",
    "adj_factor": "col_adj_factor",
    # Adjusted Prices
    "qfq_open": "col_qfq_open",
    "qfq_high": "col_qfq_high",
    "qfq_low": "col_qfq_low",
    "qfq_close": "col_qfq_close",
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
    # Technical Indicators (dynamic columns)
    "rsi_6": "col_rsi_6",
}

# Table Definitions with Table-Specific Column Overrides
TABLE_DEFINITIONS = {
    "stock_basic": {"alias": "tab_stock_basic", "columns": {}},
    "stock_concepts": {
        "alias": "tab_stock_concepts",
        "desc": "股票概念映射表 (包含传统 Tushare 原生概念，以及通过 AI 自动扫描剥离出的 AI_DOUBAO_<md5> 前缀概念)",
        "columns": {},
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
        "columns": {},
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
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
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
            "audit_result": "col_audit_result",
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
            "bz_item": "col_bz_item",
            "bz_sales": "col_bz_sales",
            "bz_profit": "col_bz_profit",
            "bz_cost": "col_bz_cost",
            "curr_type": "col_curr_type",
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
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "div_proc": "col_div_proc",
            "stk_div": "col_stk_div",
            "stk_bo_rate": "col_stk_bo_rate",
            "stk_co_rate": "col_stk_co_rate",
            "cash_div_tax": "col_cash_div_tax",
        },
    },
    "top_list": {
        "alias": "tab_top_list",
        "columns": {
            "reason": "col_reason",
            "l_sell": "col_l_sell",
            "l_buy": "col_l_buy",
            "l_amount": "col_l_amount",
            "net_amount": "col_net_amount",
            "net_rate": "col_net_rate",
            "amount_rate": "col_amount_rate",
        },
    },
    "block_trade": {
        "alias": "tab_block_trade",
        "columns": {
            "price": "col_price",
            "buyer": "col_buyer",
            "seller": "col_seller",
            "reason": "col_reason",
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
        "columns": {
            "ratio": "col_ratio",
            "exchange": "col_exchange",
        },
    },
    "margin_daily": {
        "alias": "tab_margin_daily",
        "desc": "融资融券",
        "quality_config": {"tier": 1, "monitor": True},
        "type": "global",
        "columns": {
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
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "pledge_count": "col_pledge_count",
            "unrest_pledge": "col_unrest_pledge",
            "rest_pledge": "col_rest_pledge",
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
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "proc": "col_proc",
            "high_limit": "col_high_limit",
            "low_limit": "col_low_limit",
        },
    },
    "limit_list": {
        "alias": "tab_limit_list",
        "columns": {
            "amp": "col_amp",
            "fc_ratio": "col_fc_ratio",
            "fl_ratio": "col_fl_ratio",
            "fd_amount": "col_fd_amount",
            "first_time": "col_first_time",
            "last_time": "col_last_time",
            "open_times": "col_open_times",
            "strth": "col_strth",
            "limit_type": "col_limit_type",
        },
    },
    "suspend_d": {
        "alias": "tab_suspend_d",
        "desc": "停复牌信息",
        "quality_config": {"tier": 1, "monitor": True},
        "type": "global",
        "columns": {
            "suspend_timing": "col_suspend_timing",
            "suspend_type_name": "col_suspend_type_name",
        },
    },
    "market_news": {"alias": "tab_market_news", "columns": {}},
    "trade_cal": {"alias": "tab_trade_cal", "columns": {}},
    "screening_history": {
        "alias": "tab_screening_history",
        "columns": {
            "ai_score": "col_ai_score",
            "ai_reason": "col_ai_reason",
            "strategy_name": "col_strategy_name",
            "prediction_result": "col_prediction_result",
            "t1_price": "col_t1_price",
            "t5_price": "col_t5_price",
            "t1_pct": "col_t1_pct",
            "t5_pct": "col_t5_pct",
        },
    },
    "sync_status": {
        "alias": "tab_sync_status",
        "columns": {
            "table_name": "col_table_name",
            "last_sync_date": "col_last_sync_date",
            "last_data_date": "col_last_data_date",
            "record_count": "col_record_count",
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
            "m2": "col_m2",
            "m2_yoy": "col_m2_yoy",
            "m1": "col_m1",
            "m1_yoy": "col_m1_yoy",
            "m0": "col_m0",
            "m0_yoy": "col_m0_yoy",
            "cpi": "col_cpi",
            "ppi": "col_ppi",
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
    "adj_factor": {
        "alias": "tab_adj_factor",
        "desc": "复权因子",
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "adj_factor": "col_adj_factor",
        },
    },
    "stk_holdernumber": {
        "alias": "tab_stk_holdernumber",
        "desc": "股东户数",
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "ann_date": "col_ann_date",
            "holder_num": "col_holder_num",
            "holder_num_change": "col_holder_num_change",
            "holder_num_ratio": "col_holder_num_ratio",
        },
    },
    "top10_holders": {
        "alias": "tab_top10_holders",
        "desc": "前十大股东",
        "quality_config": {"tier": 1, "monitor": True},
        "columns": {
            "holder_name": "col_holder_name",
            "hold_amount": "col_hold_amount",
            "hold_ratio": "col_hold_ratio",
            "hold_change": "col_hold_change",
            "holder_type": "col_holder_type",
        },
    },
    "index_weight": {
        "alias": "tab_index_weight",
        "columns": {
            "index_code": "col_index_code",
            "con_code": "col_ts_code",
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
}


def validate_schema_definitions():
    """
    Validates that all SQLAlchemy ORM models have a corresponding entry in TABLE_DEFINITIONS.
    Logs warnings for any missing definitions to help maintain data dictionary consistency.
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        from data.models import Base

        # 1. Extract database tables defined dynamically via SQLAlchemy ORM
        # This replaces the obsolete schema.sql physical file parsing.
        db_tables = set(Base.metadata.tables.keys())
        defined_tables = set(TABLE_DEFINITIONS.keys())

        # 2. Find tables in ORM models missing from our localized dictionary
        missing_defs = db_tables - defined_tables

        # 3. Filter out known internal/system tables
        IGNORED_TABLES = {
            "stock_sync_status",
            "screening_history",
            "task_history",
            "alembic_version",
        }

        missing_defs = missing_defs - IGNORED_TABLES

        if missing_defs:
            logger.warning(
                f"[DataDict] The following tables are in ORM models but missing from TABLE_DEFINITIONS: {missing_defs}",
            )
            logger.warning(
                "[DataDict] Please update data_dictionary.py to ensure health checks and UI work correctly.",
            )
        else:
            logger.info(
                f"[DataDict] Schema validation passed. {len(db_tables)} tables verified via SQLAlchemy Metadata.",
            )

    except Exception as e:
        logger.error(f"[DataDict] ORM validation failed: {e}")
