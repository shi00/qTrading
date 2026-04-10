"""
SQLAlchemy ORM models for A-Stock Screener.
These models represent the database schema previously defined in schema.sql.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import MetaData
from sqlalchemy.sql import func

# Naming convention for Alembic migrations
# Ensures consistent constraint naming across environments
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=naming_convention)
Base = declarative_base(metadata=metadata)


class StockBasic(Base):
    __tablename__ = "stock_basic"
    ts_code = Column(String, primary_key=True)
    symbol = Column(String)
    name = Column(String)
    area = Column(String)
    industry = Column(String)
    market = Column(String)
    list_date = Column(Date, index=True)
    list_status = Column(String)
    delist_date = Column(Date, nullable=True, index=True)  # 退市日期
    __table_args__ = (
        Index("idx_stock_basic_dates", "list_date", "delist_date"),
        Index("idx_stock_basic_status", "list_status", "list_date"),
    )
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class StockConcepts(Base):
    __tablename__ = "stock_concepts"
    ts_code = Column(String, primary_key=True, index=True)
    concept_name = Column(String)
    concept_id = Column(String, primary_key=True)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class DailyQuotes(Base):
    __tablename__ = "daily_quotes"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    pre_close = Column(Float)
    change = Column(Float)
    pct_chg = Column(Float)
    vol = Column(Float)
    amount = Column(Float)
    adj_factor = Column(Float)
    qfq_open = Column(Float)
    qfq_high = Column(Float)
    qfq_low = Column(Float)
    qfq_close = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (Index("ix_daily_quotes_date_code", "trade_date", "ts_code"),)


class DailyIndicators(Base):
    __tablename__ = "daily_indicators"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    pe = Column(Float)
    pe_ttm = Column(Float)
    pb = Column(Float)
    ps = Column(Float)
    ps_ttm = Column(Float)
    dv_ratio = Column(Float)
    dv_ttm = Column(Float)
    total_mv = Column(Float)
    circ_mv = Column(Float)
    total_share = Column(Float)
    float_share = Column(Float)
    free_share = Column(Float)
    turnover_rate = Column(Float)
    turnover_rate_f = Column(Float)
    volume_ratio = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (Index("ix_daily_indicators_date_code", "trade_date", "ts_code"),)


class MoneyflowDaily(Base):
    __tablename__ = "moneyflow_daily"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    buy_sm_vol = Column(BigInteger)
    buy_sm_amount = Column(Float)
    sell_sm_vol = Column(BigInteger)
    sell_sm_amount = Column(Float)
    buy_md_vol = Column(BigInteger)
    buy_md_amount = Column(Float)
    sell_md_vol = Column(BigInteger)
    sell_md_amount = Column(Float)
    buy_lg_vol = Column(BigInteger)
    buy_lg_amount = Column(Float)
    sell_lg_vol = Column(BigInteger)
    sell_lg_amount = Column(Float)
    buy_elg_vol = Column(BigInteger)
    buy_elg_amount = Column(Float)
    sell_elg_vol = Column(BigInteger)
    sell_elg_amount = Column(Float)
    net_mf_vol = Column(BigInteger)
    net_mf_amount = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (Index("ix_moneyflow_daily_date_code", "trade_date", "ts_code"),)


class NorthboundHolding(Base):
    __tablename__ = "northbound_holding"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    name = Column(String)
    vol = Column(BigInteger)
    ratio = Column(Float)
    exchange = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class TopList(Base):
    __tablename__ = "top_list"
    trade_date = Column(Date, primary_key=True)
    ts_code = Column(String, primary_key=True, index=True)
    name = Column(String)
    close = Column(Float)
    pct_change = Column(Float)
    turnover_rate = Column(Float)
    amount = Column(Float)
    l_sell = Column(Float)
    l_buy = Column(Float)
    l_amount = Column(Float)
    net_amount = Column(Float)
    net_rate = Column(Float)
    amount_rate = Column(Float)
    float_values = Column(Float)
    reason = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class SyncStatus(Base):
    __tablename__ = "sync_status"
    table_name = Column(String, primary_key=True)
    last_sync_date = Column(Date)
    last_data_date = Column(Date)
    record_count = Column(Integer)
    status = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class ScreeningHistory(Base):
    __tablename__ = "screening_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    strategy_name = Column(String, nullable=False)
    ts_code = Column(String, nullable=False)
    name = Column(String)
    close = Column(Float)
    pct_chg = Column(Float)
    industry = Column(String)
    vol = Column(Float)
    amount = Column(Float)
    turnover_rate = Column(Float)
    pe_ttm = Column(Float)
    pb = Column(Float)
    ps_ttm = Column(Float)
    dv_ttm = Column(Float)
    total_mv = Column(Float)
    circ_mv = Column(Float)
    roe = Column(Float)
    grossprofit_margin = Column(Float)
    debt_to_assets = Column(Float)
    or_yoy = Column(Float)
    netprofit_yoy = Column(Float)
    t1_price = Column(Float)
    t1_pct = Column(Float)
    t5_price = Column(Float)
    t5_pct = Column(Float)
    ai_score = Column(Integer)
    ai_reason = Column(String)
    thinking = Column(String)
    prediction_result = Column(String)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "strategy_name",
            "ts_code",
            name="uq_screening_history_date_strategy_code",
        ),
        Index("idx_sh_date_strategy", "trade_date", "strategy_name"),
    )


class BlockTrade(Base):
    __tablename__ = "block_trade"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    price = Column(Float)
    vol = Column(Float)
    amount = Column(Float)
    buyer = Column(String, primary_key=True)
    seller = Column(String, primary_key=True)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MarketNews(Base):
    __tablename__ = "market_news"
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(String)
    content_hash = Column(String(64), nullable=False)
    tags = Column(String)
    publish_time = Column(DateTime(timezone=False))
    source = Column(String)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (UniqueConstraint("content_hash", "publish_time", name="uq_market_news_hash_pub"),)


class TradeCal(Base):
    __tablename__ = "trade_cal"
    cal_date = Column(Date, primary_key=True)
    exchange = Column(String)
    is_open = Column(Integer)
    pretrade_date = Column(Date)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class FinancialReports(Base):
    __tablename__ = "financial_reports"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True, index=True)
    ann_date = Column(Date)
    report_type = Column(String)
    total_revenue = Column(Float)
    revenue = Column(Float)
    n_income = Column(Float)
    n_income_attr_p = Column(Float)
    total_assets = Column(Float)
    total_liab = Column(Float)
    total_hldr_eqy_exc_min_int = Column(Float)
    roe = Column(Float)
    roe_dt = Column(Float)
    grossprofit_margin = Column(Float)
    netprofit_margin = Column(Float)
    debt_to_assets = Column(Float)
    or_yoy = Column(Float)
    netprofit_yoy = Column(Float)
    goodwill = Column(Float)
    audit_result = Column(String)
    n_cashflow_act = Column(Float)  # 经营活动产生的现金流量净额
    __table_args__ = (Index("ix_financial_reports_ts_code_ann_date", "ts_code", "ann_date"),)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class IndexDaily(Base):
    __tablename__ = "index_daily"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    close = Column(Float)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    pre_close = Column(Float)
    change = Column(Float)
    pct_chg = Column(Float)
    vol = Column(Float)
    amount = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class IndexDailyBasic(Base):
    __tablename__ = "index_dailybasic"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    total_mv = Column(Float)
    float_mv = Column(Float)
    total_share = Column(Float)
    float_share = Column(Float)
    free_share = Column(Float)
    turnover_rate = Column(Float)
    turnover_rate_f = Column(Float)
    pe = Column(Float)
    pe_ttm = Column(Float)
    pb = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MarginDaily(Base):
    __tablename__ = "margin_daily"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    rzye = Column(Float)
    rqye = Column(Float)
    rzmre = Column(Float)
    rqyl = Column(Float)
    rzrqye = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class SuspendD(Base):
    __tablename__ = "suspend_d"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    suspend_timing = Column(String)
    suspend_type = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class LimitList(Base):
    __tablename__ = "limit_list"
    trade_date = Column(Date, primary_key=True)
    ts_code = Column(String, primary_key=True, index=True)
    name = Column(String)
    close = Column(Float)
    pct_chg = Column(Float)
    amp = Column(Float)
    fc_ratio = Column(Float)
    fl_ratio = Column(Float)
    fd_amount = Column(Float)
    first_time = Column(String)
    last_time = Column(String)
    open_times = Column(Integer)
    strth = Column(Float)
    limit = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class FinaForecast(Base):
    __tablename__ = "fina_forecast"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date, primary_key=True, index=True)
    type = Column(String)
    p_change_min = Column(Float)
    p_change_max = Column(Float)
    net_profit_min = Column(Float)
    net_profit_max = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class FinaMainbz(Base):
    __tablename__ = "fina_mainbz"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True, index=True)
    bz_item = Column(String, primary_key=True)
    bz_sales = Column(Float)
    bz_profit = Column(Float)
    bz_cost = Column(Float)
    curr_type = Column(String)
    update_flag = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class PledgeStat(Base):
    __tablename__ = "pledge_stat"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    pledge_count = Column(Integer)
    unrest_pledge = Column(Float)
    rest_pledge = Column(Float)
    total_share = Column(Float)
    pledge_ratio = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class Repurchase(Base):
    __tablename__ = "repurchase"
    ts_code = Column(String, primary_key=True)
    ann_date = Column(Date, primary_key=True, index=True)
    end_date = Column(Date)
    proc = Column(String)
    exp_date = Column(Date)
    vol = Column(Float)
    amount = Column(Float)
    high_limit = Column(Float)
    low_limit = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class Dividend(Base):
    __tablename__ = "dividend"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date, primary_key=True, index=True)
    div_proc = Column(String)
    stk_div = Column(Float)
    stk_bo_rate = Column(Float)
    stk_co_rate = Column(Float)
    cash_div = Column(Float)
    cash_div_tax = Column(Float)
    record_date = Column(Date)
    ex_date = Column(Date)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class StockSyncStatus(Base):
    __tablename__ = "stock_sync_status"
    ts_code = Column(String, primary_key=True)
    step4_completed_at = Column(DateTime(timezone=False))
    sync_version = Column(Integer, default=1)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class FinaAudit(Base):
    __tablename__ = "fina_audit"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date)
    audit_result = Column(String)
    audit_sign = Column(String)
    audit_fees = Column(Float)
    audit_agency = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MacroEconomy(Base):
    __tablename__ = "macro_economy"
    period = Column(Date, primary_key=True)
    m2 = Column(Float)
    m2_yoy = Column(Float)
    m1 = Column(Float)
    m1_yoy = Column(Float)
    m0 = Column(Float)
    m0_yoy = Column(Float)
    cpi = Column(Float)
    ppi = Column(Float)
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class ShiborDaily(Base):
    __tablename__ = "shibor_daily"
    date = Column(Date, primary_key=True)
    on = Column(Float)
    w1 = Column(Float, name="1w")
    w2 = Column(Float, name="2w")
    m1 = Column(Float, name="1m")
    m3 = Column(Float, name="3m")
    m6 = Column(Float, name="6m")
    m9 = Column(Float, name="9m")
    y1 = Column(Float, name="1y")
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class StkHoldernumber(Base):
    __tablename__ = "stk_holdernumber"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True, index=True)
    ann_date = Column(Date)
    holder_num = Column(Integer)
    holder_num_change = Column(Float)
    holder_num_ratio = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class Top10Holders(Base):
    __tablename__ = "top10_holders"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date)
    holder_name = Column(String, primary_key=True, index=True)
    hold_amount = Column(Float)
    hold_ratio = Column(Float)
    hold_float_ratio = Column(Float)
    hold_change = Column(Float)
    holder_type = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class IndexWeight(Base):
    __tablename__ = "index_weight"
    index_code = Column(String, primary_key=True)
    con_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    weight = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MoneyflowHsgt(Base):
    __tablename__ = "moneyflow_hsgt"
    trade_date = Column(Date, primary_key=True)
    ggt_ss = Column(Float)
    ggt_sz = Column(Float)
    hgt = Column(Float)
    sgt = Column(Float)
    north_money = Column(Float)
    south_money = Column(Float)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class TaskHistory(Base):
    __tablename__ = "task_history"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    progress = Column(Float, default=0)
    description = Column(String)
    error = Column(String)
    result = Column(String)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime(timezone=False))
    completed_at = Column(DateTime(timezone=False))


# --- Date Column Metadata Mapping ---
# 自动通过基类注入的原生日历对象
DATE_COLUMNS = {
    "stock_basic": ["list_date"],
    "daily_quotes": ["trade_date"],
    "daily_indicators": ["trade_date"],
    "moneyflow_daily": ["trade_date"],
    "northbound_holding": ["trade_date"],
    "top_list": ["trade_date"],
    "screening_history": ["trade_date"],
    "block_trade": ["trade_date"],
    "trade_cal": ["cal_date", "pretrade_date"],
    "financial_reports": ["end_date", "ann_date"],
    "index_daily": ["trade_date"],
    "index_dailybasic": ["trade_date"],
    "margin_daily": ["trade_date"],
    "suspend_d": ["trade_date"],
    "limit_list": ["trade_date"],
    "fina_forecast": ["end_date", "ann_date"],
    "fina_mainbz": ["end_date"],
    "pledge_stat": ["end_date"],
    "repurchase": ["ann_date", "end_date", "exp_date"],
    "dividend": ["end_date", "ann_date", "record_date", "ex_date"],
    "fina_audit": ["end_date", "ann_date"],
    "stk_holdernumber": ["end_date", "ann_date"],
    "top10_holders": ["end_date", "ann_date"],
    "index_weight": ["trade_date"],
    "moneyflow_hsgt": ["trade_date"],
    "shibor_daily": ["date"],
    "macro_economy": ["period"],
    "sync_status": ["last_sync_date", "last_data_date"],
}

DATETIME_COLUMNS = {
    "market_news": ["publish_time", "created_at"],
    "screening_history": ["created_at"],
    "task_history": ["created_at", "started_at", "completed_at"],
    "stock_sync_status": ["step4_completed_at", "updated_at", "created_at"],
    "macro_economy": ["created_at"],
    # 各表通用 updated_at + created_at 支持
    "stock_basic": ["updated_at", "created_at"],
    "stock_concepts": ["updated_at", "created_at"],
    "daily_quotes": ["updated_at", "created_at"],
    "daily_indicators": ["updated_at", "created_at"],
    "moneyflow_daily": ["updated_at", "created_at"],
    "northbound_holding": ["updated_at", "created_at"],
    "top_list": ["updated_at", "created_at"],
    "sync_status": ["updated_at", "created_at"],
    "block_trade": ["updated_at", "created_at"],
    "trade_cal": ["updated_at", "created_at"],
    "financial_reports": ["updated_at", "created_at"],
    "index_daily": ["updated_at", "created_at"],
    "index_dailybasic": ["updated_at", "created_at"],
    "margin_daily": ["updated_at", "created_at"],
    "suspend_d": ["updated_at", "created_at"],
    "limit_list": ["updated_at", "created_at"],
    "fina_forecast": ["updated_at", "created_at"],
    "fina_mainbz": ["updated_at", "created_at"],
    "pledge_stat": ["updated_at", "created_at"],
    "repurchase": ["updated_at", "created_at"],
    "dividend": ["updated_at", "created_at"],
    "fina_audit": ["updated_at", "created_at"],
    "shibor_daily": ["updated_at", "created_at"],
    "stk_holdernumber": ["updated_at", "created_at"],
    "top10_holders": ["updated_at", "created_at"],
    "index_weight": ["updated_at", "created_at"],
    "moneyflow_hsgt": ["updated_at", "created_at"],
}
