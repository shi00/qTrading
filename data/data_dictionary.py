"""
Data Dictionary Definitions.
Separates business metadata from UI translations.

OSS-01（开源组件使用检视报告 §2）：列级 i18n 标签不再与 ORM 双写。
- 列名集合由 ``columns_of`` 从 ORM ``Base.metadata`` 派生（纯结构查询，不触碰 I18n）。
- 列 → 标签由 ``column_i18n_key`` 统一解析（唯一入口）：
  非 ORM 表显式标签 → ``Column.info["i18n"]`` 覆盖 → ``COMMON_COLUMNS`` 兜底 →
  ``col_<列名>`` 约定（仅当该 key 已定义）。返回 None 表示无标签。
- ``TABLE_DEFINITIONS`` 仅保留表级元数据（alias/desc/quality_config/sync_config 等），
  列级声明已从本文件移除。
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
    "block_vwap": "col_block_vwap",
    "block_discount_pct": "col_block_discount_pct",
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
    "roe_annualized": "col_roe_annualized",
    "grossprofit_margin": "col_grossprofit_margin",
    "netprofit_margin": "col_netprofit_margin",
    "debt_to_assets": "col_debt_to_assets",
    "or_yoy": "col_or_yoy",
    "netprofit_yoy": "col_netprofit_yoy",
    "growth_quality_doubt": "col_growth_quality_doubt",
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
    "industry_sw_l2": "col_industry_sw_l2",
    "industry_tushare": "col_industry_tushare",
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
    "benchmark_code": "col_benchmark_code",
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
    "stock_basic": {
        "alias": "tab_stock_basic",
        "desc": "股票基础信息（定义股票池 universe，DAT-13 纳入质量监控）",
        "quality_config": {"tier": 3, "monitor": True},
    },
    "stock_concepts": {
        "alias": "tab_stock_concepts",
        "desc": "股票概念映射表 (包含传统 Tushare 原生概念，以及通过 AI 自动扫描剥离出的 AI_LLM_<sha256> 前缀概念)",
    },
    "ai_concept_failures": {
        "alias": "tab_ai_concept_failures",
        "desc": "AI 概念打标错题本：失败股票重试队列，含 retry_count/next_retry_at 等字段",
    },
    "daily_quotes": {
        "alias": "tab_daily_quotes",
        "quality_config": {
            "tier": 3,
            "monitor": True,
            "critical": True,
            "frequency": "daily",
        },
    },
    "financial_reports": {
        "alias": "tab_financial_reports",
        "desc": "财务报表(主表,含版本维度,支持财报更正历史保留)",
        "sync_config": {"strategy": "specialized_financial"},
        "quality_config": {"tier": 3, "monitor": True, "critical": True},
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
    },
    "top_list": {
        "alias": "tab_top_list",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "top_inst": {
        "alias": "tab_top_inst",
        "desc": "龙虎榜机构席位交易明细（Phase 2E top_inst 已封装 API 激活）",
        "sync_config": {
            "strategy": "batch",
            "api": "get_top_inst",
            "date_col": "trade_date",
            "keys": ["ts_code", "trade_date", "exalter", "side", "reason"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "stk_limit": {
        "alias": "tab_stk_limit",
        "desc": "每日涨跌停价格（Phase 2G stk_limit 涨跌停价格，仅数据层，不注入 AI）",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "block_trade": {
        "alias": "tab_block_trade",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
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
    },
    "index_daily": {
        "alias": "tab_index_daily",
        "desc": "指数日线（仅 MAJOR_INDICES，DAT-13 纳入质量监控）",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "index_dailybasic": {"alias": "tab_index_dailybasic"},
    "northbound_holding": {
        "alias": "tab_northbound_holding",
        "quality_config": {"tier": 2, "monitor": True, "sparse": True},
    },
    "margin_daily": {
        "alias": "tab_margin_daily",
        "desc": "融资融券",
        "quality_config": {"tier": 1, "monitor": True},
        "type": "stock",
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
    },
    "pledge_detail": {
        "alias": "tab_pledge_detail",
        "desc": "股权质押明细",
        "sync_config": {
            "strategy": "stock",
            "api": "get_pledge_detail",
            "date_col": "ann_date",
            "keys": ["ts_code", "ann_date", "holder_name", "start_date", "pledge_amount"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "share_float": {
        "alias": "tab_share_float",
        "desc": "限售解禁",
        "sync_config": {
            "strategy": "stock",
            "api": "get_share_float",
            "date_col": "float_date",
            "keys": ["ts_code", "float_date", "holder_name"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "type": "stock",
    },
    "stk_holdertrade": {
        "alias": "tab_stk_holdertrade",
        "desc": "股东增减持",
        "sync_config": {
            "strategy": "stock",
            "api": "get_stk_holdertrade",
            "date_col": "ann_date",
            "keys": ["ts_code", "ann_date", "holder_name", "in_de"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        "type": "stock",
    },
    "sw_industry_classify": {
        "alias": "tab_sw_industry_classify",
        "desc": "申万行业分类（Phase 3F-1，全局快照，月度更新，对应 Tushare index_classify 接口）",
        "sync_config": {
            "strategy": "sw_industry",
            "api": "get_index_classify",
            "keys": ["index_code", "sw_level"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": False},
    },
    "sw_industry_member": {
        "alias": "tab_sw_industry_member",
        "desc": "申万行业成分股映射（DATA-04 L2，对应 Tushare index_member_all 接口，按真实字段重建，含纳入/剔除时间维度）",
        "sync_config": {
            "strategy": "sw_industry",
            "api": "get_index_member_all",
            "keys": ["ts_code", "l3_code", "in_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": False},
    },
    "stock_name_history": {
        "alias": "tab_stock_name_history",
        "desc": "股票名称变更历史（DATA-04 L3，对应 Tushare namechange 接口，记录每股历次名称含 ST/*ST 状态生效区间，供 as-of 还原历史时点名称消除前视偏差）",
        "sync_config": {
            "strategy": "name_change",
            "api": "get_namechange",
            "keys": ["ts_code", "start_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": False},
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
    },
    "limit_list": {
        "alias": "tab_limit_list",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "suspend_d": {
        "alias": "tab_suspend_d",
        "desc": "停复牌信息",
        "quality_config": {"tier": 1, "monitor": True},
        "type": "stock",
    },
    "market_news": {
        "alias": "tab_market_news",
        "desc": "市场新闻/快讯（新闻风险解读 Phase A 扩展字段）",
        "unique_constraints": [
            {"name": "uq_market_news_hash_time", "columns": ["content_hash", "publish_time"]},
        ],
        "indexes": [
            "idx_market_news_pub_source",
            "idx_market_news_ts_code",
            "idx_market_news_source_kind_pub_time",
        ],
    },
    "news_risk_brief": {
        "alias": "tab_news_risk_brief",
        "desc": "新闻风险解读快照（设计方案 §7.2；复合主键 ts_code+input_hash）",
        "indexes": [
            "idx_news_risk_brief_ts_code_window_created",
        ],
    },
    "trade_cal": {
        "alias": "tab_trade_cal",
        "desc": "交易日历（定义交易日基准，DAT-13 纳入质量监控）",
        "quality_config": {"tier": 3, "monitor": True},
        "type": "global",
    },
    "screening_history": {
        "alias": "tab_screening_history",
        "desc": "选股/预测历史记录（RV-03 后唯一键为 trade_date+strategy_name+ts_code+run_id，append-only 语义）",
        "unique_constraints": [
            {
                "name": "uq_screening_history_dat_strategy_code_run",
                "columns": ["trade_date", "strategy_name", "ts_code", "run_id"],
            },
        ],
        "indexes": ["idx_sh_date_strategy", "idx_sh_date_code", "idx_sh_prediction_result", "idx_sh_pending"],
    },
    "screening_thinking": {
        "alias": "tab_screening_thinking",
    },
    "sync_status": {
        "alias": "tab_sync_status",
    },
    "stock_sync_status": {
        "alias": "tab_stock_sync_status",
    },
    # --- Phase 3: Policy-Driven AI Architecture ---
    "macro_economy": {
        "alias": "tab_macro_economy",
        "desc": "宏观经济",
        "sync_config": {"strategy": "macro", "type": "economic"},
        "quality_config": {"tier": 2, "monitor": True},
        "type": "global",
    },
    "shibor_daily": {
        "alias": "tab_shibor_daily",
        "desc": "Shibor利率",
        "sync_config": {"strategy": "macro", "type": "shibor"},
        "quality_config": {"tier": 2, "monitor": True},
        "type": "global",
    },
    # Phase 3G §4.3.4：业绩快报（express API，points_2000）
    "express": {
        "alias": "tab_express",
        "desc": "业绩快报",
        "sync_config": {
            "strategy": "batch",
            "api": "get_express",
            "date_col": "ann_date",
            "keys": ["ts_code", "end_date", "ann_date"],
        },
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "stk_holdernumber": {
        "alias": "tab_stk_holdernumber",
        "desc": "股东户数",
        "sync_config": {"strategy": "holder", "api": "get_stk_holdernumber"},
        "quality_config": {"tier": 1, "monitor": True},
    },
    "top10_holders": {
        "alias": "tab_top10_holders",
        "desc": "前十大股东",
        "sync_config": {"strategy": "holder", "api": "get_top10_holders"},
        "quality_config": {"tier": 1, "monitor": True},
    },
    "index_weight": {
        "alias": "tab_index_weight",
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
    },
    "moneyflow_hsgt": {
        "alias": "tab_moneyflow_hsgt",
        "desc": "北向资金流",
        "sync_config": {"strategy": "historical", "api": "moneyflow_hsgt"},
        "quality_config": {"tier": 2, "monitor": True},
        "type": "global",
    },
    "alembic_version": {
        "alias": "tab_alembic_version",
        "desc": "数据库版本",
    },
    "task_history": {
        "alias": "tab_task_history",
        "desc": "系统任务执行历史日志",
    },
    "app_state": {
        "alias": "tab_app_state",
        "desc": "应用全局状态键值存储",
    },
    "backtest_results": {
        "alias": "tab_backtest_results",
        "desc": "回测结果存储",
    },
    "watchlist": {
        "alias": "tab_watchlist",
        "desc": "用户关注列表（FR-UX-004, Task 4.2）",
    },
}


# ---------------------------------------------------------------------------
# OSS-01：从 ORM 派生的列级解析入口（唯一正本）
# ---------------------------------------------------------------------------

# 非 ORM 表（Alembic 自建，不在 Base.metadata）的列 → 标签显式声明。
_NON_ORM_COLUMN_LABELS: dict[str, dict[str, str]] = {
    "alembic_version": {"version_num": "col_version_num"},
}


def columns_of(table_name: str) -> frozenset[str]:
    """表的列名集合（纯结构查询，不触碰 I18n）。

    ORM 表返回 ``Base.metadata`` 中的列名集合；非 ORM 表（如 alembic_version）
    返回显式声明；未知表返回空集合。
    """
    from data.persistence.models import Base

    table = Base.metadata.tables.get(table_name)
    if table is None:
        return frozenset(_NON_ORM_COLUMN_LABELS.get(table_name, {}))
    return frozenset(c.name for c in table.columns)


def column_i18n_key(table_name: str | None, col_name: str) -> str | None:
    """列 → i18n key 的唯一入口。

    解析顺序（OSS-01 实证：与重构前硬编码清单逐列一致，mismatch=0）：
    1. 非 ORM 表显式标签（``_NON_ORM_COLUMN_LABELS``）
    2. ``Column.info["i18n"]`` 覆盖（models.py 中列级例外，仅 18 条）
    3. ``COMMON_COLUMNS`` 兜底（公共列，含 pct_change→col_pct_chg 等非机械映射）
    4. ``col_<列名>`` 约定（仅当该 key 已定义；用 ``I18n.has`` 探测，避免污染
       ``_missing_keys`` 并刷告警——ORM 比旧数据字典多出的列不派生标签）
    返回 None 表示无标签，调用方回退到裸列名。
    """
    from core.i18n import I18n
    from data.persistence.models import Base

    if table_name:
        explicit = _NON_ORM_COLUMN_LABELS.get(table_name, {}).get(col_name)
        if explicit:
            return explicit
        table = Base.metadata.tables.get(table_name)
        if table is not None and col_name in table.columns:
            override = table.columns[col_name].info.get("i18n")
            if override:
                return override
    common = COMMON_COLUMNS.get(col_name)
    if common:
        return common
    derived = f"col_{col_name}"
    if I18n.has(derived):
        return derived
    return None


def validate_schema_definitions(strict: bool = False):
    """
    Validates that all SQLAlchemy ORM models have a corresponding entry in TABLE_DEFINITIONS.
    Logs warnings for any missing definitions to help maintain data dictionary consistency.
    Column-level consistency is guaranteed structurally since OSS-01: column sets are
    derived from ORM ``Base.metadata`` and no longer duplicated in this file.

    For Alembic migration ↔ ORM full-attribute consistency checks (column types,
    nullable, server_default, primary keys, foreign keys, indexes, unique constraints),
    see ``tests/integration/test_orm_migration_consistency.py`` which runs against an
    isolated PostgreSQL database after ``alembic upgrade head``.

    Args:
        strict: If True, raises ValueError on any schema inconsistency.
    """
    import logging
    import os

    from data.sync.base import safe_error

    logger = logging.getLogger(__name__)

    try:
        from data.persistence.models import Base

        db_tables = set(Base.metadata.tables.keys())
        defined_tables = set(TABLE_DEFINITIONS.keys())

        IGNORED_TABLES = {
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

        logger.info(
            "[DataDict] Schema validation completed. %s tables verified.",
            len(db_tables),
        )

        is_strict = strict or os.environ.get("STRICT_SCHEMA_GATE") == "1"
        if is_strict and errors:
            raise ValueError("Schema inconsistencies found:\n" + "\n".join(errors))

    except ValueError as e:
        logger.error("[DataDict] Schema validation failed in strict mode: %s", safe_error(e))
        raise
    except Exception as e:
        logger.error("[DataDict] ORM validation failed: %s", safe_error(e))
        if strict:
            raise
