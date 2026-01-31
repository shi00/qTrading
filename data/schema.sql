-- Database Schema for A-Stock Screener
-- Usage: Execute using executescript() in SQLite

-- Enable Write-Ahead Logging for concurrency
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- 1. Basic Stock Info
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    area TEXT,
    industry TEXT,
    market TEXT,
    list_date TEXT,
    list_status TEXT,
    updated_at TEXT
);

-- 2. Daily Quotes - Complete OHLCV data for technical analysis
CREATE TABLE IF NOT EXISTS daily_quotes (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    pre_close REAL,
    change REAL,
    pct_chg REAL,
    vol REAL,
    amount REAL,
    adj_factor REAL, 
    PRIMARY KEY (ts_code, trade_date)
);

-- 3. Daily Indicators - Valuation and market cap data
CREATE TABLE IF NOT EXISTS daily_indicators (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    pe REAL,
    pe_ttm REAL,
    pb REAL,
    ps REAL,
    ps_ttm REAL,
    dv_ratio REAL,
    dv_ttm REAL,
    total_mv REAL,
    circ_mv REAL,
    total_share REAL,
    float_share REAL,
    free_share REAL,
    turnover_rate REAL,
    turnover_rate_f REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 4. Money Flow
CREATE TABLE IF NOT EXISTS moneyflow_daily (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    buy_sm_vol INTEGER,
    buy_sm_amount REAL,
    sell_sm_vol INTEGER,
    sell_sm_amount REAL,
    buy_md_vol INTEGER,
    buy_md_amount REAL,
    sell_md_vol INTEGER,
    sell_md_amount REAL,
    buy_lg_vol INTEGER,
    buy_lg_amount REAL,
    sell_lg_vol INTEGER,
    sell_lg_amount REAL,
    buy_elg_vol INTEGER,
    buy_elg_amount REAL,
    sell_elg_vol INTEGER,
    sell_elg_amount REAL,
    net_mf_vol INTEGER,
    net_mf_amount REAL,
    PRIMARY KEY (ts_code, trade_date)
);

-- 5. Northbound Holding
CREATE TABLE IF NOT EXISTS northbound_holding (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    name TEXT,
    vol INTEGER,
    ratio REAL,
    exchange TEXT,
    PRIMARY KEY (ts_code, trade_date)
);

-- 6. Dragon Tiger Board (LHB - top_list)
CREATE TABLE IF NOT EXISTS top_list (
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT,
    close REAL,
    pct_chg REAL,
    turnover_rate REAL,
    amount REAL,
    l_sell REAL,
    l_buy REAL,
    l_amount REAL,
    net_amount REAL,
    net_rate REAL,
    amount_rate REAL,
    float_values REAL,
    reason TEXT,
    PRIMARY KEY (trade_date, ts_code)
);

-- 7. Sync Status Tracking
CREATE TABLE IF NOT EXISTS sync_status (
    table_name TEXT PRIMARY KEY,
    last_sync_date TEXT,
    last_data_date TEXT,
    record_count INTEGER,
    status TEXT,
    updated_at TEXT
);

-- 8. Screening History (Review System)
CREATE TABLE IF NOT EXISTS screening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    name TEXT,
    close REAL,
    pct_chg REAL,
    t1_price REAL,
    t5_price REAL,
    ai_score INTEGER,
    ai_reason TEXT,
    prediction_result TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, strategy_name, ts_code)
);

-- 9. Block Trades
CREATE TABLE IF NOT EXISTS block_trade (
    ts_code TEXT,
    trade_date TEXT,
    price REAL,
    volume REAL,
    amount REAL,
    buyer TEXT,
    seller TEXT,
    reason TEXT,
    updated_at TEXT,
    PRIMARY KEY (ts_code, trade_date, buyer, seller)
);

-- 10. Market News (Real-time storage for AI)
CREATE TABLE IF NOT EXISTS market_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT,
    tags TEXT,
    publish_time TEXT,
    source TEXT,
    created_at TEXT,
    UNIQUE(content, publish_time)
);

-- 11. Trade Calendar (Persistent Cache)
CREATE TABLE IF NOT EXISTS trade_cal (
    cal_date TEXT PRIMARY KEY,
    exchange TEXT,
    is_open INTEGER,
    pretrade_date TEXT
);

-- 12. Financial Reports
CREATE TABLE IF NOT EXISTS financial_reports (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    ann_date TEXT,
    report_type TEXT,
    total_revenue REAL,
    revenue REAL,
    n_income REAL,
    n_income_attr_p REAL,
    total_assets REAL,
    total_liab REAL,
    total_hldr_eqy_exc_min_int REAL,
    roe REAL,
    roe_dt REAL,
    grossprofit_margin REAL,
    netprofit_margin REAL,
    debt_to_assets REAL,
    or_yoy REAL,
    netprofit_yoy REAL,
    PRIMARY KEY (ts_code, end_date)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_quotes_date ON daily_quotes(trade_date);
CREATE INDEX IF NOT EXISTS idx_quotes_code ON daily_quotes(ts_code);
CREATE INDEX IF NOT EXISTS idx_indicators_date ON daily_indicators(trade_date);
CREATE INDEX IF NOT EXISTS idx_fina_enddate ON financial_reports(end_date);
CREATE INDEX IF NOT EXISTS idx_mf_date ON moneyflow_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_north_date ON northbound_holding(trade_date);
CREATE INDEX IF NOT EXISTS idx_history_date ON screening_history(trade_date);
CREATE INDEX IF NOT EXISTS idx_cal_date ON trade_cal(cal_date);
