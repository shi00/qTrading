"""
Data Dictionary Definitions.
Separates business metadata from UI translations.
"""

# Common column definitions that apply across most tables
# These are used as a fallback if a table-specific definition is not found.
COMMON_COLUMNS = {
    # Identifiers
    "ts_code": "代码",
    "symbol": "股票代码",
    "name": "名称",
    "id": "ID",

    # Date & Time
    "trade_date": "交易日期",
    "ann_date": "公告日期",
    "end_date": "报告期",
    "list_date": "上市日期",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "update_flag": "更新标志",
    "publish_time": "发布时间",
    "cal_date": "日历日期",
    "pretrade_date": "上个交易日",
    "suspend_timing": "停牌时间",
    "exp_date": "过期日期",
    "record_date": "股权登记日",
    "ex_date": "除权除息日",

    # Market Data (Price & Volume)
    "open": "开盘",
    "high": "最高",
    "low": "最低",
    "close": "收盘",
    "pre_close": "昨收",
    "change": "涨跌额",
    "pct_chg": "涨跌幅",
    "vol": "成交量",
    "volume": "成交量",
    "amount": "成交额",
    "turnover_rate": "换手率",
    "turnover_rate_f": "换手率(自由)",
    "adj_factor": "复权因子",
    
    # Adjusted Prices
    "qfq_open": "前复权开盘",
    "qfq_high": "前复权最高",
    "qfq_low": "前复权最低",
    "qfq_close": "前复权收盘",

    # Valuation
    "pe": "市盈率",
    "pe_ttm": "市盈率(TTM)",
    "pb": "市净率",
    "ps": "市销率",
    "ps_ttm": "市销率(TTM)",
    "dv_ratio": "股息率",
    "dv_ttm": "股息率(TTM)",
    "total_mv": "总市值",
    "circ_mv": "流通市值",
    "float_mv": "流通市值",
    "float_values": "流通市值",

    # Share Capital
    "total_share": "总股本",
    "float_share": "流通股本",
    "free_share": "自由流通股本",

    # Financial Basics
    "revenue": "营业收入",
    "total_revenue": "营业总收入",
    "total_cost": "营业总成本",
    "total_profit": "利润总额",
    "n_income": "净利润",
    "n_income_attr_p": "归母净利润",
    "total_assets": "总资产",
    "total_liab": "总负债",
    "total_hldr_eqy_exc_min_int": "股东权益",
    
    # Financial Ratios
    "roe": "ROE",
    "roe_dt": "ROE(摊薄)",
    "grossprofit_margin": "毛利率",
    "netprofit_margin": "净利率",
    "debt_to_assets": "资产负债率",
    "or_yoy": "营收同比",
    "netprofit_yoy": "净利同比",
    "goodwill": "商誉",
    
    # Money Flow
    "buy_sm_vol": "小单买量", "sell_sm_vol": "小单卖量", 
    "buy_md_vol": "中单买量", "sell_md_vol": "中单卖量",
    "buy_lg_vol": "大单买量", "sell_lg_vol": "大单卖量",
    "buy_elg_vol": "特大单买量", "sell_elg_vol": "特大单卖量",
    "net_mf_amount": "净流入额",
    "net_mf_vol": "净流入量",

    # Others
    "area": "地域",
    "industry": "行业",
    "market": "市场",
    "list_status": "上市状态",
    "audit_result": "审计意见",
    "report_type": "报表类型",
    "comp_type": "公司类型",
    "content": "内容",
    "source": "来源",
    "tags": "标签",
    "is_open": "是否开市",
    "status": "状态",
}

# Table Definitions with Table-Specific Column Overrides
TABLE_DEFINITIONS = {
    "stock_basic": {
        "alias": "股票列表",
        "columns": {}
    },
    "daily_quotes": {
        "alias": "日线行情",
        "columns": {}
    },
    "financial_reports": {
        "alias": "财务报表",
        "columns": {}
    },
    "daily_indicators": {
        "alias": "每日指标",
        "columns": {}
    },
    "fina_forecast": {
        "alias": "业绩预告",
        "columns": {
            "type": "预告类型",
            "p_change_min": "预告幅度下限",
            "p_change_max": "预告幅度上限",
            "net_profit_min": "预告净利下限",
            "net_profit_max": "预告净利上限",
        }
    },
    "fina_mainbz": {
        "alias": "主营业务",
        "columns": {
            "bz_item": "主营项目",
            "bz_sales": "主营收入",
            "bz_profit": "主营利润",
            "bz_cost": "主营成本",
            "curr_type": "货币代码",
        }
    },
    "dividend": {
        "alias": "分红送转",
        "columns": {
            "div_proc": "实施进度",
            "stk_div": "送股",
            "stk_bo_rate": "送转比例",
            "stk_co_rate": "转增比例",
            "cash_div_tax": "派息(税前)",
            "cash_div_tax_rate": "派息税率",
        }
    },
    "top_list": {
        "alias": "龙虎榜",
        "columns": {
            "reason": "上榜原因",
            "l_sell": "卖出额",
            "l_buy": "买入额",
            "l_amount": "成交总额",
            "net_amount": "净买入额",
            "net_rate": "净买入率",
            "amount_rate": "成交占比",
        }
    },
    "block_trade": {
        "alias": "大宗交易",
        "columns": {
            "price": "成交价",
            "buyer": "买方营业部",
            "seller": "卖方营业部",
            "reason": "交易原因",
        }
    },
    "moneyflow_daily": {
        "alias": "个股资金流",
        "columns": {}
    },
    "index_daily": {
        "alias": "指数日线",
        "columns": {}
    },
    "index_dailybasic": {
        "alias": "指数每日指标",
        "columns": {}
    },
    "northbound_holding": {
        "alias": "北向资金持股",
        "columns": {
            "ratio": "持股比例",
            "exchange": "交易所",
        }
    },
    "margin_daily": {
        "alias": "融资融券",
        "columns": {
            "rzye": "融资余额",
            "rqye": "融券余额",
            "rzmre": "融资买入",
            "rqyl": "融券余量",
            "rzrqye": "两融余额",
        }
    },
    "pledge_stat": {
        "alias": "股权质押",
        "columns": {
            "pledge_count": "质押次数",
            "unrest_pledge": "无限售质押",
            "rest_pledge": "限售质押",
            "pledge_ratio": "质押比例",
        }
    },
    "repurchase": {
        "alias": "回购",
        "columns": {
             "proc": "进度",
             "high_limit": "回购上限",
             "low_limit": "回购下限",
        }
    },
    "limit_list": {
        "alias": "涨跌停列表",
        "columns": {
            "amp": "振幅",
            "fc_ratio": "封单比",
            "fl_ratio": "封单流值比",
            "fd_amount": "封单金额",
            "first_time": "首次封板",
            "last_time": "最后封板",
            "open_times": "打开次数",
            "strth": "强度",
            "limit_type": "类型",
        }
    },
    "suspend_d": {
        "alias": "停复牌信息",
        "columns": {
            "suspend_timing": "停牌时间",
            "suspend_type_name": "停牌原因",
        }
    },
    "market_news": {
        "alias": "市场新闻",
        "columns": {}
    },
    "trade_cal": {
        "alias": "交易日历",
        "columns": {}
    },
    "screening_history": {
        "alias": "选股历史",
        "columns": {
            "ai_score": "AI评分",
            "ai_reason": "AI理由",
            "strategy_name": "策略名称",
            "prediction_result": "预测结果",
             "t1_price": "T+1价格",
             "t5_price": "T+5价格",
        }
    },
    "sync_status": {
        "alias": "同步状态",
        "columns": {
            "table_name": "表名",
            "last_sync_date": "最后同步",
            "last_data_date": "最新数据",
            "record_count": "记录数",
        }
    }
}
