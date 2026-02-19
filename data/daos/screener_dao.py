import logging
import pandas as pd
from .base_dao import BaseDao
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)

class ScreenerDao(BaseDao):

    # --- Screening History ---
    async def save_screening_result(self, df, strategy_name, trade_date):
        if df is None or df.empty: return 0

        # Prepare params
        # Prepare params using vectorized helper for performance
        cols = ['trade_date', 'strategy_name', 'ts_code', 'name', 'close', 'pct_chg']
        
        # Ensure df has these columns, fill logic if needed
        df_to_save = df.copy()
        df_to_save['trade_date'] = trade_date
        df_to_save['strategy_name'] = strategy_name
        
        # Map or ensure columns exist
        if 'name' not in df_to_save.columns: df_to_save['name'] = None
        if 'close' not in df_to_save.columns: df_to_save['close'] = None
        if 'pct_chg' not in df_to_save.columns: df_to_save['pct_chg'] = None
        
        sql = "INSERT OR IGNORE INTO screening_history (trade_date, strategy_name, ts_code, name, close, pct_chg) VALUES (?, ?, ?, ?, ?, ?)"
        
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df_to_save, cols)
        return await self._write_db(sql, params, is_many=True)

    async def get_screening_history(self, strategy_name=None, limit=100):
        sql = "SELECT * FROM screening_history WHERE 1=1"
        p = []
        if strategy_name: sql += " AND strategy_name=?"; p.append(strategy_name)
        sql += " ORDER BY trade_date DESC LIMIT ?"
        p.append(limit)
        return await self._read_db(sql, p)

    async def get_pending_reviews(self):
        async with self.engine.connect() as conn:
            res = await conn.exec_driver_sql(
                "SELECT * FROM screening_history WHERE t1_price IS NULL OR t5_price IS NULL")
            rows = res.fetchall()
            return [dict(zip(list(res.keys()), row)) for row in rows]

    async def update_screening_performance(self, updates):
        # updates = list of tuples (t1_price, t1_pct, t5_price, t5_pct, id)
        if not updates: return
        sql = "UPDATE screening_history SET t1_price = ?, t1_pct = ?, t5_price = ?, t5_pct = ? WHERE id = ?"
        await self._write_db(sql, updates, is_many=True)

    async def get_learning_examples(self, limit=3):
        wins, losses = None, None
        async with self.engine.connect() as conn:
            # Wins
            r = await conn.exec_driver_sql(
                "SELECT * FROM screening_history WHERE prediction_result='WIN' ORDER BY t1_pct DESC LIMIT ?", (limit,))
            rows = r.fetchall()
            wins = pd.DataFrame(rows, columns=list(r.keys()))

            # Losses
            r = await conn.exec_driver_sql(
                "SELECT * FROM screening_history WHERE prediction_result='LOSS' ORDER BY t1_pct ASC LIMIT ?", (limit,))
            rows = r.fetchall()
            losses = pd.DataFrame(rows, columns=list(r.keys()))

        return wins, losses

    # --- Screening Data Fetch for Logic ---
    async def get_screening_data(self, trade_date, latest_trade_date_func):
        if not trade_date:
            trade_date = await latest_trade_date_func()

        sql = '''
              SELECT b.ts_code,
                     b.name,
                     b.industry,
                     b.list_date,
                     b.list_status,
                     q.trade_date,
                     q.close,
                     q.pct_chg,
                     q.vol,
                     q.amount,
                     i.pe_ttm,
                     i.pb,
                     i.ps_ttm,
                     i.dv_ttm,
                     i.total_mv,
                     i.circ_mv,
                     i.turnover_rate,
                     f.roe,
                     f.grossprofit_margin,
                     f.debt_to_assets,
                     f.or_yoy,
                     f.netprofit_yoy
              FROM stock_basic b
                       LEFT JOIN daily_quotes q ON b.ts_code = q.ts_code AND q.trade_date = ?
                       LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = ?
                       LEFT JOIN (SELECT f1.*
                                  FROM financial_reports f1
                                           INNER JOIN (SELECT ts_code, MAX(end_date) as max_date
                                                       FROM financial_reports
                                                       GROUP BY ts_code) f2
                                                      ON f1.ts_code = f2.ts_code AND f1.end_date = f2.max_date) f
                                 ON b.ts_code = f.ts_code
              WHERE q.close IS NOT NULL \
              '''
        return await self._read_db(sql, (trade_date, trade_date))
