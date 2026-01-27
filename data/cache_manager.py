import aiosqlite
import pandas as pd
import datetime
import os
import config

class CacheManager:
    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH

    async def init_db(self):
        """Initialize database tables with enhanced schema"""
        async with aiosqlite.connect(self.db_path) as db:
            # Optimize performance
            await db.execute("PRAGMA journal_mode=WAL;")  # Write-Ahead Logging
            await db.execute("PRAGMA synchronous=NORMAL;") # Faster writes
            
            # 1. Basic Stock Info (enhanced)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS stock_basic (
                    ts_code TEXT PRIMARY KEY,
                    symbol TEXT,
                    name TEXT,
                    area TEXT,
                    industry TEXT,
                    market TEXT,
                    list_date TEXT,
                    updated_at TEXT
                )
            ''')
            
            # 2. Daily Quotes - Complete OHLCV data for technical analysis
            await db.execute('''
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
                    PRIMARY KEY (ts_code, trade_date)
                )
            ''')
            
            # 3. Daily Indicators - Valuation and market cap data
            await db.execute('''
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
                )
            ''')
            
            # 4. Money Flow
            await db.execute('''
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
                )
            ''')
            
            # 5. Northbound Holding
            await db.execute('''
                CREATE TABLE IF NOT EXISTS northbound_holding (
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    name TEXT,
                    vol INTEGER,
                    ratio REAL,
                    exchange TEXT,
                    PRIMARY KEY (ts_code, trade_date)
                )
            ''')

            # 6. Dragon Tiger Board (LHB - top_list)
            await db.execute('''
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
                )
            ''')

            # 7. Block Trade
            await db.execute('''
                CREATE TABLE IF NOT EXISTS block_trade (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    price REAL,
                    vol REAL,
                    amount REAL,
                    buyer TEXT,
                    seller TEXT,
                    PRIMARY KEY (trade_date, ts_code, buyer, seller)
                )
            ''')
            
            # 4. Financial Reports - Quarterly financial data
            await db.execute('''
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
                )
            ''')

            # 5. Money Flow - Daily fund flow data (资金流向)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS moneyflow_daily (
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    buy_sm_vol REAL,
                    buy_sm_amount REAL,
                    sell_sm_amount REAL,
                    buy_md_amount REAL,
                    sell_md_amount REAL,
                    buy_lg_amount REAL,
                    sell_lg_amount REAL,
                    buy_elg_amount REAL,
                    sell_elg_amount REAL,
                    net_mf_vol REAL,
                    net_mf_amount REAL,
                    PRIMARY KEY (ts_code, trade_date)
                )
            ''')

            # 6. Northbound Holding - HK Connect holdings (北向持股)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS northbound_holding (
                    ts_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    name TEXT,
                    vol REAL,
                    ratio REAL,
                    exchange TEXT,
                    PRIMARY KEY (ts_code, trade_date)
                )
            ''')

            # 7. Sync Status Tracking (enhanced)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS sync_status (
                    table_name TEXT PRIMARY KEY,
                    last_sync_date TEXT,
                    last_data_date TEXT,
                    record_count INTEGER,
                    status TEXT,
                    updated_at TEXT
                )
            ''')

            # 8. Screening History (Review System)
            await db.execute('''
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
                    t1_pct REAL,
                    t5_pct REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(trade_date, strategy_name, ts_code)
                )
            ''')
            
            # Create indexes for faster queries
            await db.execute('CREATE INDEX IF NOT EXISTS idx_quotes_date ON daily_quotes(trade_date)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_quotes_code ON daily_quotes(ts_code)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_indicators_date ON daily_indicators(trade_date)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_fina_enddate ON financial_reports(end_date)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_mf_date ON moneyflow_daily(trade_date)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_north_date ON northbound_holding(trade_date)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_history_date ON screening_history(trade_date)')
            
            await db.commit()
            print("[Cache] Database initialized with enhanced schema (including moneyflow & northbound).")

    async def clear_all_cache(self):
        """Clear all cached data from database tables"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM daily_quotes")
            await db.execute("DELETE FROM daily_indicators")
            await db.execute("DELETE FROM financial_reports")
            await db.execute("DELETE FROM moneyflow_daily")
            await db.execute("DELETE FROM northbound_holding")
            await db.execute("DELETE FROM sync_status")
            # Keep stock_basic as it's relatively static
            await db.commit()
        print("[Cache] All cache data cleared.")

    # ========== Stock Basic ==========
    async def save_stock_basic(self, df):
        """Save stock basic info"""
        if df is None or df.empty:
            return 0
        
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        df = df.copy()
        df['updated_at'] = now
        
        cols = ['ts_code', 'symbol', 'name', 'area', 'industry', 'market', 'list_date', 'updated_at']
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO stock_basic 
                (ts_code, symbol, name, area, industry, market, list_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        
        return len(df)

    async def get_stock_basic(self):
        """Get all stock basic info"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM stock_basic") as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Daily Quotes ==========
    async def save_daily_quotes(self, df):
        """Save daily OHLCV quotes"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 
                'pre_close', 'change', 'pct_chg', 'vol', 'amount']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO daily_quotes 
                (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        
        return len(df)

    async def get_daily_quotes(self, start_date=None, end_date=None, ts_code=None):
        """Get daily quotes with optional filters"""
        query = "SELECT * FROM daily_quotes WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND trade_date <= ?"
            params.append(end_date)
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        
        query += " ORDER BY trade_date DESC"
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def get_latest_trade_date(self):
        """Get the most recent trade date in cache"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT MAX(trade_date) FROM daily_quotes")
            result = await cursor.fetchone()
            return result[0] if result else None

    async def get_cached_trade_dates(self):
        """Get all trade dates that already exist in cache (for incremental sync)"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date")
            rows = await cursor.fetchall()
            return set(row[0] for row in rows)

    async def get_sync_stats(self):
        """Get sync statistics for UI display"""
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}
            
            # Quotes count and date range
            cursor = await db.execute(
                "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_quotes"
            )
            row = await cursor.fetchone()
            stats['quotes_count'] = row[0] or 0
            stats['quotes_min_date'] = row[1]
            stats['quotes_max_date'] = row[2]
            
            # Unique dates count
            cursor = await db.execute("SELECT COUNT(DISTINCT trade_date) FROM daily_quotes")
            row = await cursor.fetchone()
            stats['quotes_dates'] = row[0] or 0
            
            # Stock count
            cursor = await db.execute("SELECT COUNT(*) FROM stock_basic")
            row = await cursor.fetchone()
            stats['stock_count'] = row[0] or 0
            
            return stats

    # ========== Daily Indicators ==========
    async def save_daily_indicators(self, df):
        """Save daily valuation indicators"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm',
                'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv', 'total_share',
                'float_share', 'free_share', 'turnover_rate', 'turnover_rate_f']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO daily_indicators 
                (ts_code, trade_date, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
                 total_mv, circ_mv, total_share, float_share, free_share, turnover_rate, turnover_rate_f)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        
        return len(df)

    async def get_latest_indicators(self, trade_date=None):
        """Get latest indicators for all stocks"""
        async with aiosqlite.connect(self.db_path) as db:
            if trade_date is None:
                cursor = await db.execute("SELECT MAX(trade_date) FROM daily_indicators")
                result = await cursor.fetchone()
                trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            async with db.execute("SELECT * FROM daily_indicators WHERE trade_date = ?", (trade_date,)) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Financial Reports ==========
    async def save_financial_reports(self, df):
        """Save quarterly financial reports"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue',
                'revenue', 'n_income', 'n_income_attr_p', 'total_assets', 'total_liab',
                'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin',
                'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO financial_reports 
                (ts_code, end_date, ann_date, report_type, total_revenue, revenue,
                 n_income, n_income_attr_p, total_assets, total_liab,
                 total_hldr_eqy_exc_min_int, roe, roe_dt, grossprofit_margin,
                 netprofit_margin, debt_to_assets, or_yoy, netprofit_yoy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        
        print(f"[Cache] Saved {len(df)} financial records.")
        return len(df)

    async def get_latest_financials(self):
        """Get latest financial report for each stock"""
        async with aiosqlite.connect(self.db_path) as db:
            # Get latest report per stock
            query = '''
                SELECT f.* FROM financial_reports f
                INNER JOIN (
                    SELECT ts_code, MAX(end_date) as max_date 
                    FROM financial_reports 
                    GROUP BY ts_code
                ) latest ON f.ts_code = latest.ts_code AND f.end_date = latest.max_date
            '''
            async with db.execute(query) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Sync Status ==========
    async def update_sync_status(self, table_name, last_data_date, record_count, status='success'):
        """Update sync status for a table"""
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO sync_status 
                (table_name, last_sync_date, last_data_date, record_count, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (table_name, now, last_data_date, record_count, status, now))
            await db.commit()

    async def get_sync_status(self, table_name=None):
        """Get sync status for tables"""
        async with aiosqlite.connect(self.db_path) as db:
            if table_name:
                async with db.execute("SELECT * FROM sync_status WHERE table_name = ?", (table_name,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        cols = [desc[0] for desc in cursor.description]
                        return dict(zip(cols, row))
                    return None
            else:
                async with db.execute("SELECT * FROM sync_status") as cursor:
                    cols = [desc[0] for desc in cursor.description]
                    rows = await cursor.fetchall()
                    return pd.DataFrame(rows, columns=cols)

    # ========== Screening Query ==========
    async def get_screening_data(self, trade_date=None):
        """
        Get merged data for screening: quotes + indicators + financials
        This is the main method used by screening strategies.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Determine trade date
            if trade_date is None:
                cursor = await db.execute("SELECT MAX(trade_date) FROM daily_indicators")
                result = await cursor.fetchone()
                trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            # Join quotes, indicators, and latest financials
            query = '''
                SELECT 
                    b.ts_code, b.name, b.industry, b.list_date,
                    q.trade_date, q.close, q.pct_chg, q.vol, q.amount,
                    i.pe_ttm, i.pb, i.ps_ttm, i.dv_ttm, i.total_mv, i.circ_mv, i.turnover_rate,
                    f.roe, f.grossprofit_margin, f.debt_to_assets, f.or_yoy, f.netprofit_yoy
                FROM stock_basic b
                LEFT JOIN daily_quotes q ON b.ts_code = q.ts_code AND q.trade_date = ?
                LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = ?
                LEFT JOIN (
                    SELECT f1.* FROM financial_reports f1
                    INNER JOIN (
                        SELECT ts_code, MAX(end_date) as max_date 
                        FROM financial_reports GROUP BY ts_code
                    ) f2 ON f1.ts_code = f2.ts_code AND f1.end_date = f2.max_date
                ) f ON b.ts_code = f.ts_code
                WHERE q.close IS NOT NULL
            '''
            
            async with db.execute(query, (trade_date, trade_date)) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Money Flow ==========
    async def save_moneyflow(self, df):
        """Save daily money flow data"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'buy_sm_vol', 'buy_sm_amount', 'sell_sm_amount',
                'buy_md_amount', 'sell_md_amount', 'buy_lg_amount', 'sell_lg_amount',
                'buy_elg_amount', 'sell_elg_amount', 'net_mf_vol', 'net_mf_amount']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO moneyflow_daily 
                (ts_code, trade_date, buy_sm_vol, buy_sm_amount, sell_sm_amount,
                 buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount,
                 buy_elg_amount, sell_elg_amount, net_mf_vol, net_mf_amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        
        return len(df)

    async def get_moneyflow(self, trade_date=None, ts_code=None):
        """Get money flow data"""
        query = "SELECT * FROM moneyflow_daily WHERE 1=1"
        params = []
        
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        
        query += " ORDER BY trade_date DESC"
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Northbound Holding ==========
    async def save_northbound(self, df):
        """Save northbound (HK Connect) holding data"""
        if df is None or df.empty:
            return 0
        
        cols = ['ts_code', 'trade_date', 'name', 'vol', 'ratio', 'exchange']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO northbound_holding 
                (ts_code, trade_date, name, vol, ratio, exchange)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        
        return len(df)

    async def get_northbound(self, trade_date=None, ts_code=None):
        """Get northbound holding data"""
        query = "SELECT * FROM northbound_holding WHERE 1=1"
        params = []
        
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
        if ts_code:
            query += " AND ts_code = ?"
            params.append(ts_code)
        
        query += " ORDER BY ratio DESC"  # Order by holding ratio
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Screening History (Review System) ==========
    
    async def save_screening_result(self, df, strategy_name, trade_date):
        """Save screening results to history"""
        if df is None or df.empty:
            return 0
        
        # Prepare data
        records = []
        for _, row in df.iterrows():
            records.append((
                trade_date,
                strategy_name,
                row['ts_code'],
                row['name'],
                row['close'],
                row['pct_chg']
            ))
            
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR IGNORE INTO screening_history 
                (trade_date, strategy_name, ts_code, name, close, pct_chg)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', records)
            await db.commit()
            
        return len(records)

    async def get_pending_reviews(self):
        """Get screening records that need performance update (T+1 or T+5 missing)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM screening_history 
                WHERE t1_price IS NULL OR t5_price IS NULL
            ''') as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_screening_performance(self, updates):
        """
        Update performance metrics for screening records.
        :param updates: List of tuples (t1_price, t1_pct, t5_price, t5_pct, id)
        """
        if not updates:
            return
            
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                UPDATE screening_history 
                SET t1_price = ?, t1_pct = ?, t5_price = ?, t5_pct = ?
                WHERE id = ?
            ''', updates)
            await db.commit()

    async def get_screening_history(self, strategy_name=None, limit=100):
        """Get screening history for UI"""
        query = "SELECT * FROM screening_history WHERE 1=1"
        params = []
        
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
            
        query += " ORDER BY trade_date DESC LIMIT ?"
        params.append(limit)
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)


    async def get_latest_northbound(self):
        """Get latest northbound holding for all stocks"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT MAX(trade_date) FROM northbound_holding")
            result = await cursor.fetchone()
            trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            return await self.get_northbound(trade_date=trade_date)

    # ========== Dragon Tiger Board (LHB) ==========
    async def save_top_list(self, df):
        """Save Dragon Tiger Board data"""
        if df is None or df.empty:
            return 0
            
        cols = ['trade_date', 'ts_code', 'name', 'close', 'pct_chg', 'turnover_rate', 
                'amount', 'l_sell', 'l_buy', 'l_amount', 'net_amount', 
                'net_rate', 'amount_rate', 'float_values', 'reason']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO top_list 
                (trade_date, ts_code, name, close, pct_chg, turnover_rate, amount, 
                 l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        return len(df)

    async def get_top_list(self, trade_date=None):
        """Get Dragon Tiger Board data"""
        query = "SELECT * FROM top_list WHERE 1=1"
        params = []
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    # ========== Block Trade ==========
    async def save_block_trade(self, df):
        """Save Block Trade data"""
        if df is None or df.empty:
            return 0
            
        cols = ['trade_date', 'ts_code', 'price', 'vol', 'amount', 'buyer', 'seller']
        df = df.copy()
        for col in cols:
            if col not in df.columns:
                df[col] = None
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany('''
                INSERT OR REPLACE INTO block_trade 
                (trade_date, ts_code, price, vol, amount, buyer, seller)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', df[cols].values.tolist())
            await db.commit()
        return len(df)

    async def get_block_trade(self, trade_date=None):
        """Get Block Trade data"""
        query = "SELECT * FROM block_trade WHERE 1=1"
        params = []
        if trade_date:
            query += " AND trade_date = ?"
            params.append(trade_date)
            
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                cols = [desc[0] for desc in cursor.description]
                rows = await cursor.fetchall()
                return pd.DataFrame(rows, columns=cols)

    async def get_latest_northbound(self):
        """Get latest northbound holding for all stocks"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT MAX(trade_date) FROM northbound_holding")
            result = await cursor.fetchone()
            trade_date = result[0] if result else None
            
            if not trade_date:
                return pd.DataFrame()
            
            return await self.get_northbound(trade_date=trade_date)

    # ========== Backward Compatibility ==========
    async def save_daily_data(self, df):
        """Backward compatibility: save to both quotes and indicators"""
        if df is None or df.empty:
            return
        await self.save_daily_quotes(df)
        await self.save_daily_indicators(df)

    async def get_latest_daily(self):
        """Backward compatibility: get latest merged daily data"""
        return await self.get_screening_data()

    async def save_financials(self, df):
        """Backward compatibility wrapper"""
        return await self.save_financial_reports(df)
