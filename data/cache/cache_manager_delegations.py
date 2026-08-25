"""CacheManager 委托 Mixin（review03-C11 拆分，机械迁移，行为不变）。

由 ``CacheManagerDelegationMixin`` 承载原 CacheManager 委托区（LDELAGATIONS START HERE 起）中的
纯转发方法；CacheManager 继承本 Mixin。引擎生命周期、全局门面与协调逻辑保留在
``cache_manager.py``。调用方零变更（继承透明）。
"""

from __future__ import annotations

import datetime

import pandas as pd


class CacheManagerDelegationMixin:
    """纯转发委托（self.xxx_dao.xxx），依赖实例属性 self.stock_dao / self.quote_dao ...。"""

    async def save_stock_basic(self, df: pd.DataFrame, priority: int | None = None):
        return await self.stock_dao.save_stock_basic(df, priority)

    async def get_stock_basic(self):
        return await self.stock_dao.get_stock_basic()

    async def save_concepts(self, df: pd.DataFrame):
        return await self.stock_dao.save_concepts(df)

    async def overwrite_concepts(self, df: pd.DataFrame):
        return await self.stock_dao.overwrite_concepts(df)

    async def save_daily_quotes(
        self,
        df: pd.DataFrame,
        priority: int | None = None,
        suppress_errors: bool = False,
    ):
        return await self.quote_dao.save_daily_quotes(
            df,
            priority,
            suppress_errors=suppress_errors,
        )

    async def check_data_exists(self, trade_date: str) -> bool:
        await self.wait_for_maintenance()
        return await self.quote_dao.check_data_exists(trade_date)

    async def get_daily_quotes(
        self,
        ts_code: str | None = None,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        ts_code_list: list | None = None,
        suppress_errors: bool = True,
    ):
        return await self.quote_dao.get_daily_quotes(
            ts_code,
            start_date,
            end_date,
            ts_code_list,
            suppress_errors=suppress_errors,
        )

    async def save_daily_indicators(self, df: pd.DataFrame, suppress_errors: bool = False):
        return await self.market_dao.save_daily_indicators(
            df,
            suppress_errors=suppress_errors,
        )

    async def get_daily_indicators(
        self,
        ts_code: str | None = None,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        limit: int | None = None,
    ):
        return await self.market_dao.get_daily_indicators(
            ts_code,
            start_date,
            end_date,
            limit,
        )

    async def get_latest_trade_date(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_latest_trade_date()

    async def get_cached_trade_dates(self):
        await self.wait_for_maintenance()
        return await self.quote_dao.get_cached_trade_dates()

    async def get_latest_indicators(self, trade_date: str | None = None):
        return await self.financial_dao.get_latest_indicators(trade_date)

    async def get_cached_indicator_dates(self):
        return await self.financial_dao.get_cached_indicator_dates()

    async def save_financial_reports(self, df: pd.DataFrame, conn=None):
        return await self.financial_dao.save_financial_reports(df, conn=conn)

    async def get_cached_financial_records(self, period: str | None = None):
        return await self.financial_dao.get_cached_financial_records(period)

    async def save_moneyflow(self, df: pd.DataFrame):
        return await self.quote_dao.save_moneyflow(df)

    async def save_northbound(self, df: pd.DataFrame):
        return await self.quote_dao.save_northbound(df)

    async def save_market_news(self, news_item: dict, wait: bool = False):
        return await self.market_dao.save_market_news(news_item, wait)

    async def get_market_news(
        self,
        limit: int | None = 50,
        offset: int = 0,
        min_publish_time: datetime.date | str | None = None,
    ):
        return await self.market_dao.get_market_news(limit, offset, min_publish_time)

    async def get_screening_data(self, trade_date: str | None = None):
        return await self.screener_dao.get_screening_data(trade_date)

    async def get_fundamental_screening_data(self, trade_date: str | None = None):
        return await self.screener_dao.get_fundamental_screening_data(trade_date)

    async def get_screening_data_range(self, start_date: str, end_date: str):
        return await self.screener_dao.get_screening_data_range(start_date, end_date)

    async def get_fundamental_screening_data_range(self, start_date: str, end_date: str):
        return await self.screener_dao.get_fundamental_screening_data_range(start_date, end_date)

    async def update_sync_status(
        self,
        table_name: str,
        last_data_date: str,
        record_count: int,
        status: str = "success",
        last_result_status: str | None = None,
    ):
        return await self.sync_dao.update_sync_status(
            table_name,
            last_data_date,
            record_count,
            status,
            last_result_status,
        )

    async def get_sync_status(self, table_name: str | None = None) -> pd.DataFrame | dict | None:
        return await self.sync_dao.get_sync_status(table_name)

    async def save_fina_forecast(self, df: pd.DataFrame):
        return await self.financial_dao.save_fina_forecast(df)

    async def save_express(self, df: pd.DataFrame):
        return await self.express_dao.save_express(df)

    async def save_fina_mainbz(self, df: pd.DataFrame):
        return await self.financial_dao.save_fina_mainbz(df)

    async def save_pledge_stat(self, df: pd.DataFrame):
        return await self.financial_dao.save_pledge_stat(df)

    async def save_repurchase(self, df: pd.DataFrame):
        return await self.financial_dao.save_repurchase(df)

    async def save_dividend(self, df: pd.DataFrame):
        return await self.financial_dao.save_dividend(df)

    async def save_index_daily(self, df: pd.DataFrame):
        return await self.quote_dao.save_index_daily(df)

    async def save_index_dailybasic(self, df: pd.DataFrame):
        return await self.quote_dao.save_index_dailybasic(df)

    async def get_index_daily(self, ts_code: str | None = None, trade_date: datetime.date | str | None = None):
        return await self.quote_dao.get_index_daily(ts_code, trade_date)

    async def save_limit_list(self, df: pd.DataFrame):
        return await self.quote_dao.save_limit_list(df)

    async def get_limit_list(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        trade_date: str | None = None,
    ):
        return await self.quote_dao.get_limit_list(start_date, end_date, trade_date)

    async def save_margin_daily(self, df: pd.DataFrame):
        return await self.quote_dao.save_margin_daily(df)

    async def save_suspend_d(self, df: pd.DataFrame):
        return await self.quote_dao.save_suspend_d(df)

    async def get_suspend_d(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        trade_date: str | None = None,
    ):
        return await self.quote_dao.get_suspend_d(start_date, end_date, trade_date)

    async def save_fina_audit(self, df: pd.DataFrame):
        return await self.financial_dao.save_fina_audit(df)

    async def save_top_list(self, df: pd.DataFrame):
        return await self.quote_dao.save_top_list(df)

    async def get_top_list(self, trade_date: str | None = None):
        return await self.quote_dao.get_top_list(trade_date)

    async def get_top_list_range(self, start_date: str, end_date: str):
        return await self.quote_dao.get_top_list_range(start_date, end_date)

    async def save_block_trade(self, df: pd.DataFrame):
        return await self.quote_dao.save_block_trade(df)

    async def get_block_trade(self, trade_date: str | None = None):
        return await self.quote_dao.get_block_trade(trade_date)

    async def get_block_trade_range(self, start_date: str, end_date: str):
        return await self.quote_dao.get_block_trade_range(start_date, end_date)

    async def get_moneyflow(self, trade_date: str | None = None, ts_code: str | None = None):
        return await self.quote_dao.get_moneyflow(trade_date, ts_code)

    async def get_moneyflow_range(self, start_date: str, end_date: str):
        return await self.quote_dao.get_moneyflow_range(start_date, end_date)

    async def get_northbound(self, trade_date: str | None = None, ts_code: str | None = None):
        return await self.quote_dao.get_northbound(trade_date, ts_code)

    async def get_northbound_range(self, start_date: str, end_date: str):
        return await self.quote_dao.get_northbound_range(start_date, end_date)

    async def get_screening_history(self, strategy_name: str | None = None, limit: int | None = 100):
        return await self.screener_dao.get_screening_history(strategy_name, limit)

    async def get_history_tree(self, offset: int = 0, limit: int | None = 30):
        return await self.screener_dao.get_history_tree(offset, limit)

    async def get_history_records(
        self, trade_date: str | None, strategy_name: str | None = None, run_id: str | None = None
    ):
        return await self.screener_dao.get_history_records(trade_date, strategy_name, run_id)

    async def get_pending_reviews(self):
        return await self.screener_dao.get_pending_reviews()

    async def get_learning_examples(self, limit: int | None = 3):
        return await self.screener_dao.get_learning_examples(limit)

    async def get_completed_step4_stocks(self, sync_version: int = 1):
        return await self.sync_dao.get_completed_step4_stocks(sync_version)

    async def mark_stock_step4_completed(self, ts_code: str | None, sync_version: int = 1, conn=None):
        return await self.sync_dao.mark_stock_step4_completed(ts_code, sync_version, conn=conn)

    async def clear_step4_sync_status(self):
        return await self.sync_dao.clear_step4_sync_status()

    async def save_trade_cal(self, df: pd.DataFrame):
        return await self.stock_dao.save_trade_cal(df)

    async def get_trade_cal(
        self,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        is_open: int | str | None = None,
    ):
        return await self.stock_dao.get_trade_cal(start_date, end_date, is_open)

    async def get_start_date_by_trade_days(self, end_date: datetime.date | str | None, trade_days: int):
        return await self.stock_dao.get_start_date_by_trade_days(end_date, trade_days)

    async def get_latest_northbound(self):
        return await self.quote_dao.get_latest_northbound()

    async def save_macro_economy(self, df: pd.DataFrame):
        return await self.macro_dao.save_macro_economy(df)

    async def save_shibor_daily(self, df: pd.DataFrame):
        return await self.macro_dao.save_shibor_daily(df)

    async def save_holder_number(self, df: pd.DataFrame):
        return await self.holder_dao.save_holder_number(df)

    async def save_top10_holders(self, df: pd.DataFrame):
        return await self.holder_dao.save_top10_holders(df)

    async def save_index_weights(self, df: pd.DataFrame):
        return await self.market_dao.save_index_weights(df)

    async def get_index_weights(self, index_code: str | None, trade_date: str | None):
        return await self.market_dao.get_index_weights(index_code, trade_date)

    async def get_latest_index_weight_date(self):
        return await self.market_dao.get_latest_index_weight_date()

    async def save_moneyflow_hsgt(self, df: pd.DataFrame):
        return await self.market_dao.save_moneyflow_hsgt(df)

    async def get_moneyflow_hsgt(self, trade_date: datetime.date | str | None = None, limit: int | None = None):
        return await self.market_dao.get_moneyflow_hsgt(trade_date, limit)

    async def get_moneyflow_hsgt_range(self, start_date: str, end_date: str):
        return await self.market_dao.get_moneyflow_hsgt_range(start_date, end_date)
