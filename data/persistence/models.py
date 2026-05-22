"""
SQLAlchemy ORM models for A-Stock Screener.
These models represent the database schema previously defined in schema.sql.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    ts_code = Column(String, primary_key=True)
    concept_name = Column(String)
    concept_id = Column(String, primary_key=True)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class DailyQuotes(Base):
    __tablename__ = "daily_quotes"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(8, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))
    adj_factor = Column(Numeric(20, 12))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (Index("ix_daily_quotes_date_code", "trade_date", "ts_code"),)


class DailyIndicators(Base):
    __tablename__ = "daily_indicators"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    pe = Column(Numeric(12, 4))
    pe_ttm = Column(Numeric(12, 4))
    pb = Column(Numeric(12, 4))
    ps = Column(Numeric(12, 4))
    ps_ttm = Column(Numeric(12, 4))
    dv_ratio = Column(Numeric(12, 4))
    dv_ttm = Column(Numeric(12, 4))
    total_mv = Column(Numeric(20, 4))
    circ_mv = Column(Numeric(20, 4))
    total_share = Column(BigInteger)
    float_share = Column(BigInteger)
    free_share = Column(BigInteger)
    turnover_rate = Column(Numeric(12, 4))
    turnover_rate_f = Column(Numeric(12, 4))
    volume_ratio = Column(Numeric(12, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (Index("ix_daily_indicators_date_code", "trade_date", "ts_code"),)


class MoneyflowDaily(Base):
    __tablename__ = "moneyflow_daily"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    buy_sm_vol = Column(BigInteger)
    buy_sm_amount = Column(Numeric(20, 4))
    sell_sm_vol = Column(BigInteger)
    sell_sm_amount = Column(Numeric(20, 4))
    buy_md_vol = Column(BigInteger)
    buy_md_amount = Column(Numeric(20, 4))
    sell_md_vol = Column(BigInteger)
    sell_md_amount = Column(Numeric(20, 4))
    buy_lg_vol = Column(BigInteger)
    buy_lg_amount = Column(Numeric(20, 4))
    sell_lg_vol = Column(BigInteger)
    sell_lg_amount = Column(Numeric(20, 4))
    buy_elg_vol = Column(BigInteger)
    buy_elg_amount = Column(Numeric(20, 4))
    sell_elg_vol = Column(BigInteger)
    sell_elg_amount = Column(Numeric(20, 4))
    net_mf_vol = Column(BigInteger)
    net_mf_amount = Column(Numeric(20, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (Index("ix_moneyflow_daily_date_code", "trade_date", "ts_code"),)


class NorthboundHolding(Base):
    """港资持股比例 & 持股变动 (source: hk_hold & hk_hold_detail).

    NOTE: This model stores *holding ratio* data, NOT net capital flow.
    For northbound money flow (净流入), use `moneyflow_hsgt` via TushareClient.
    """

    __tablename__ = "northbound_holding"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    name = Column(String)
    vol = Column(BigInteger)
    ratio = Column(Numeric(12, 4))
    exchange = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class TopList(Base):
    __tablename__ = "top_list"
    trade_date = Column(Date, primary_key=True)
    ts_code = Column(String, primary_key=True, index=True)
    name = Column(String)
    close = Column(Numeric(12, 4))
    pct_change = Column(Numeric(8, 4))
    turnover_rate = Column(Numeric(12, 4))
    amount = Column(Numeric(20, 4))
    l_sell = Column(Numeric(20, 4))
    l_buy = Column(Numeric(20, 4))
    l_amount = Column(Numeric(20, 4))
    net_amount = Column(Numeric(20, 4))
    net_rate = Column(Numeric(12, 4))
    amount_rate = Column(Numeric(12, 4))
    float_values = Column(Numeric(20, 4))
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
    last_result_status = Column(String)
    error_message = Column(String)
    error_count = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class ScreeningHistory(Base):
    __tablename__ = "screening_history"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(16), nullable=False)
    trade_date = Column(Date, nullable=False)
    strategy_name = Column(String, nullable=False)
    ts_code = Column(String, nullable=False)
    name = Column(String)
    close = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(8, 4))
    industry = Column(String)
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))
    turnover_rate = Column(Numeric(12, 4))
    pe_ttm = Column(Numeric(12, 4))
    pb = Column(Numeric(12, 4))
    ps_ttm = Column(Numeric(12, 4))
    dv_ttm = Column(Numeric(12, 4))
    total_mv = Column(Numeric(20, 4))
    circ_mv = Column(Numeric(20, 4))
    roe = Column(Numeric(12, 4))
    grossprofit_margin = Column(Numeric(12, 4))
    debt_to_assets = Column(Numeric(12, 4))
    or_yoy = Column(Numeric(12, 4))
    netprofit_yoy = Column(Numeric(12, 4))
    t1_price = Column(Numeric(12, 4))
    t1_pct = Column(Numeric(8, 4))
    t5_price = Column(Numeric(12, 4))
    t5_pct = Column(Numeric(8, 4))
    index_pct = Column(Numeric(8, 4))
    alpha = Column(Numeric(12, 4))
    ai_score = Column(Numeric(12, 4))
    ai_reason = Column(String)
    prediction_result = Column(String)
    review_status = Column(String, server_default="PENDING")
    params_snapshot = Column(JSONB)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ts_code",
            name="uq_screening_history_run_code",
        ),
        Index("idx_sh_date_strategy", "trade_date", "strategy_name"),
        Index("idx_sh_date_code", "trade_date", "ts_code"),
        Index("idx_sh_run_id", "run_id"),
        Index("idx_sh_prediction_result", "prediction_result", postgresql_where=text("prediction_result IS NOT NULL")),
        Index(
            "idx_sh_pending",
            "review_status",
            postgresql_where=text("review_status IN ('PENDING', 'T1_DONE') OR review_status IS NULL"),
        ),
        Index(
            "idx_sh_params_gin",
            "params_snapshot",
            postgresql_using="gin",
            postgresql_ops={"params_snapshot": "jsonb_path_ops"},
        ),
    )


class ScreeningThinking(Base):
    __tablename__ = "screening_thinking"
    id = Column(Integer, primary_key=True, autoincrement=True)
    history_id = Column(
        Integer,
        ForeignKey("screening_history.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    thinking = Column(String)
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class BlockTrade(Base):
    __tablename__ = "block_trade"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    price = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))
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

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_market_news_hash"),
        Index("ix_market_news_source", "source"),
        Index("idx_market_news_pub_source", "publish_time", "source"),
    )


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
    total_revenue = Column(Numeric(20, 4), info={"null_protected": True})
    revenue = Column(Numeric(20, 4), info={"null_protected": True})
    n_income = Column(Numeric(20, 4), info={"null_protected": True})
    n_income_attr_p = Column(Numeric(20, 4), info={"null_protected": True})
    total_assets = Column(Numeric(20, 4), info={"null_protected": True})
    total_liab = Column(Numeric(20, 4), info={"null_protected": True})
    total_hldr_eqy_exc_min_int = Column(Numeric(20, 4), info={"null_protected": True})
    roe = Column(Numeric(12, 4), info={"null_protected": True})
    roe_dt = Column(Numeric(12, 4), info={"null_protected": True})
    grossprofit_margin = Column(Numeric(12, 4), info={"null_protected": True})
    netprofit_margin = Column(Numeric(12, 4), info={"null_protected": True})
    debt_to_assets = Column(Numeric(12, 4), info={"null_protected": True})
    or_yoy = Column(Numeric(12, 4), info={"null_protected": True})
    netprofit_yoy = Column(Numeric(12, 4), info={"null_protected": True})
    goodwill = Column(Numeric(20, 4), info={"null_protected": True})
    audit_result = Column(String)
    n_cashflow_act = Column(Numeric(20, 4), info={"null_protected": True})
    __table_args__ = (
        Index("ix_financial_reports_ts_code_ann_date", "ts_code", "ann_date"),
        Index("ix_financial_reports_ann_date", "ann_date"),
    )
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class IndexDaily(Base):
    __tablename__ = "index_daily"
    __table_args__ = (Index("idx_index_daily_date_code", "trade_date", "ts_code"),)
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    close = Column(Numeric(12, 4))
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(8, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class IndexDailyBasic(Base):
    __tablename__ = "index_dailybasic"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True)
    total_mv = Column(Numeric(20, 4))
    float_mv = Column(Numeric(20, 4))
    total_share = Column(BigInteger)
    float_share = Column(BigInteger)
    free_share = Column(BigInteger)
    turnover_rate = Column(Numeric(12, 4))
    turnover_rate_f = Column(Numeric(12, 4))
    pe = Column(Numeric(12, 4))
    pe_ttm = Column(Numeric(12, 4))
    pb = Column(Numeric(12, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MarginDaily(Base):
    __tablename__ = "margin_daily"
    ts_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    rzye = Column(Numeric(20, 4))
    rqye = Column(Numeric(20, 4))
    rzmre = Column(Numeric(20, 4))
    rqyl = Column(Numeric(20, 4))
    rzrqye = Column(Numeric(20, 4))
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
    close = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(8, 4))
    amp = Column(Numeric(12, 4))
    fc_ratio = Column(Numeric(12, 4))
    fl_ratio = Column(Numeric(12, 4))
    fd_amount = Column(Numeric(20, 4))
    first_time = Column(String)
    last_time = Column(String)
    open_times = Column(Integer)
    strth = Column(Numeric(12, 4))
    limit = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class FinaForecast(Base):
    __tablename__ = "fina_forecast"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date, primary_key=True, index=True)
    type = Column(String)
    p_change_min = Column(Numeric(12, 4))
    p_change_max = Column(Numeric(12, 4))
    net_profit_min = Column(Numeric(20, 4))
    net_profit_max = Column(Numeric(20, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class FinaMainbz(Base):
    __tablename__ = "fina_mainbz"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True, index=True)
    bz_item = Column(String, primary_key=True)
    bz_sales = Column(Numeric(20, 4))
    bz_profit = Column(Numeric(20, 4))
    bz_cost = Column(Numeric(20, 4))
    curr_type = Column(String)
    update_flag = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class PledgeStat(Base):
    __tablename__ = "pledge_stat"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    pledge_count = Column(Integer)
    unrest_pledge = Column(Numeric(20, 4))
    rest_pledge = Column(Numeric(20, 4))
    total_share = Column(Numeric(20, 4))
    pledge_ratio = Column(Numeric(12, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class Repurchase(Base):
    __tablename__ = "repurchase"
    ts_code = Column(String, primary_key=True)
    ann_date = Column(Date, primary_key=True, index=True)
    end_date = Column(Date)
    proc = Column(String)
    exp_date = Column(Date)
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))
    high_limit = Column(Numeric(12, 4))
    low_limit = Column(Numeric(12, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class Dividend(Base):
    __tablename__ = "dividend"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date, primary_key=True, index=True)
    div_proc = Column(String)
    stk_div = Column(Numeric(12, 4))
    stk_bo_rate = Column(Numeric(12, 4))
    stk_co_rate = Column(Numeric(12, 4))
    cash_div = Column(Numeric(12, 4))
    cash_div_tax = Column(Numeric(12, 4))
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
    audit_fees = Column(Numeric(20, 4))
    audit_agency = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MacroEconomy(Base):
    __tablename__ = "macro_economy"
    period = Column(Date, primary_key=True)
    m2 = Column(Numeric(20, 4))
    m2_yoy = Column(Numeric(12, 4))
    m1 = Column(Numeric(20, 4))
    m1_yoy = Column(Numeric(12, 4))
    m0 = Column(Numeric(20, 4))
    m0_yoy = Column(Numeric(12, 4))
    cpi = Column(Numeric(12, 4))
    ppi = Column(Numeric(12, 4))
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class ShiborDaily(Base):
    __tablename__ = "shibor_daily"
    date = Column("date", Date, primary_key=True)
    on = Column("on", Numeric(12, 4))
    w1 = Column(Numeric(12, 4), name="1w")
    w2 = Column(Numeric(12, 4), name="2w")
    m1 = Column(Numeric(12, 4), name="1m")
    m3 = Column(Numeric(12, 4), name="3m")
    m6 = Column(Numeric(12, 4), name="6m")
    m9 = Column(Numeric(12, 4), name="9m")
    y1 = Column(Numeric(12, 4), name="1y")
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class StkHoldernumber(Base):
    __tablename__ = "stk_holdernumber"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True, index=True)
    ann_date = Column(Date)
    holder_num = Column(BigInteger)
    holder_num_change = Column(BigInteger)
    holder_num_ratio = Column(Numeric(12, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class Top10Holders(Base):
    __tablename__ = "top10_holders"
    ts_code = Column(String, primary_key=True)
    end_date = Column(Date, primary_key=True)
    ann_date = Column(Date)
    holder_name = Column(String, primary_key=True, index=True, nullable=False)
    hold_amount = Column(BigInteger)
    hold_ratio = Column(Numeric(12, 4))
    hold_float_ratio = Column(Numeric(12, 4))
    hold_change = Column(BigInteger)
    holder_type = Column(String)
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class IndexWeight(Base):
    __tablename__ = "index_weight"
    index_code = Column(String, primary_key=True)
    con_code = Column(String, primary_key=True)
    trade_date = Column(Date, primary_key=True, index=True)
    weight = Column(Numeric(12, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class MoneyflowHsgt(Base):
    __tablename__ = "moneyflow_hsgt"
    trade_date = Column(Date, primary_key=True)
    ggt_ss = Column(Numeric(20, 4))
    ggt_sz = Column(Numeric(20, 4))
    hgt = Column(Numeric(20, 4))
    sgt = Column(Numeric(20, 4))
    north_money = Column(Numeric(20, 4))
    south_money = Column(Numeric(20, 4))
    updated_at = Column(DateTime(timezone=False), server_default=func.now())
    created_at = Column(DateTime(timezone=False), server_default=func.now())


class TaskHistory(Base):
    __tablename__ = "task_history"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    progress = Column(Numeric(5, 2), default=0)
    description = Column(String)
    error = Column(String)
    result = Column(String)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False, index=True)
    started_at = Column(DateTime(timezone=False))
    completed_at = Column(DateTime(timezone=False))

    __table_args__ = (
        Index("idx_task_history_status_created", "status", "created_at"),
        Index("idx_task_history_completed", "completed_at"),
    )


class AppState(Base):
    __tablename__ = "app_state"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())


class BacktestResultModel(Base):
    __tablename__ = "backtest_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(16), unique=True, nullable=False)
    strategy_name = Column(String, nullable=False)
    params_snapshot = Column(JSONB)

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    initial_capital = Column(Numeric(20, 4))

    total_return = Column(Numeric(12, 6))
    annualized_return = Column(Numeric(12, 6))
    sharpe_ratio = Column(Numeric(12, 6))
    max_drawdown = Column(Numeric(12, 6))
    calmar_ratio = Column(Numeric(12, 6))
    ic_mean = Column(Numeric(12, 6))
    ic_ir = Column(Numeric(12, 6))
    win_rate = Column(Numeric(8, 4))
    profit_factor = Column(Numeric(12, 6))
    total_trades = Column(Integer)
    volatility = Column(Numeric(12, 6))
    information_ratio = Column(Numeric(12, 6))
    tracking_error = Column(Numeric(12, 6))

    nav_curve_json = Column(JSONB)
    trades_json = Column(JSONB)
    period_stats_json = Column(JSONB)

    execution_price = Column(String(20))
    allow_limit_up_buy = Column(Boolean)
    allow_limit_down_sell = Column(Boolean)
    slippage_model = Column(String(20))
    app_version = Column(String(32))

    executed_at = Column(DateTime(timezone=False), server_default=func.now())
    duration_ms = Column(Integer)

    __table_args__ = (
        Index("ix_backtest_results_strategy", "strategy_name"),
        Index("ix_backtest_results_date", "executed_at"),
    )


def get_model_columns(model_class: type, exclude: set[str] | None = None) -> list[str]:
    exclude = exclude or {"updated_at", "created_at"}
    return [c.name for c in model_class.__table__.columns if c.name not in exclude]


def get_model_pk_columns(model_class: type) -> list[str]:
    return [c.name for c in model_class.__table__.primary_key.columns]
