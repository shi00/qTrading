import asyncio
import logging

import pandas as pd
import sqlalchemy as sa

from data.persistence.models import MacroEconomy, ShiborDaily, get_model_columns, get_model_pk_columns

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class MacroDao(BaseDao):
    """DAO for Macroeconomic data (M2, CPI, PPI) and Interbank Rates (Shibor)."""

    async def save_macro_economy(self, df: pd.DataFrame):
        """
        Save Macro Economy data (M2, CPI, etc.)
        Table: macro_economy
        Note: created_at is handled by DB-level server_default, not injected here.
        """
        if df is None or df.empty:
            return 0

        cols = get_model_columns(MacroEconomy)
        pk_columns = get_model_pk_columns(MacroEconomy)

        return await self._save_upsert(
            df,
            "macro_economy",
            cols,
            pk_columns=pk_columns,
        )

    async def save_shibor_daily(self, df: pd.DataFrame):
        """
        Save Daily Shibor rates.
        Table: shibor_daily
        Tushare 'shibor' API fields: date, on, 1w, 2w, 1m, 3m, 6m, 9m, 1y
        """
        if df is None or df.empty:
            return 0

        cols = get_model_columns(ShiborDaily)
        pk_columns = get_model_pk_columns(ShiborDaily)
        available = [c for c in cols if c in df.columns]
        return await self._save_upsert(
            df,
            "shibor_daily",
            available,
            pk_columns=pk_columns,
        )

    async def get_macro_latest_date(self):
        """Get latest period in macro_economy."""
        df = await self._read_db("SELECT MAX(period) as max_date FROM macro_economy")
        if not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    async def get_shibor_latest_date(self):
        """Get latest date in shibor_daily."""
        df = await self._read_db("SELECT MAX(date) as max_date FROM shibor_daily")
        if not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    async def get_shibor_latest(self, as_of_date=None) -> pd.DataFrame:
        """
        获取Shibor利率数据。

        Args:
            as_of_date: 截止日期（含），用于历史回放场景防止前视偏差。
                        None 表示不限制（取最新一期）。

        Returns:
            DataFrame with latest shibor rates (date, on, 1w, 2w, 1m, 3m, 6m, 9m, 1y, lpr_1y, lpr_5y)
        """
        try:
            # [DB-005] ShiborDaily contains reserved words ('on') and columns starting with digits ('1w' etc.).
            # We use SQLAlchemy Core instead of raw SQL to automatically handle identifier quoting.
            t = ShiborDaily.__table__
            cols = [
                t.c.date,
                t.c.on,
                getattr(t.c, "1w"),
                getattr(t.c, "2w"),
                getattr(t.c, "1m"),
                getattr(t.c, "3m"),
                getattr(t.c, "6m"),
                getattr(t.c, "9m"),
                getattr(t.c, "1y"),
                # Phase 3G §4.3.4：LPR 字段
                t.c.lpr_1y,
                t.c.lpr_5y,
            ]
            stmt = sa.select(*cols)
            if as_of_date is not None:
                stmt = stmt.where(t.c.date <= as_of_date)
            stmt = stmt.order_by(t.c.date.desc()).limit(1)

            df = await self._read_db_select(stmt)
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[MacroDao] Failed to get shibor latest: %s", e)
            return pd.DataFrame()

    async def get_macro_economy_latest(self, as_of_date=None) -> pd.DataFrame:
        """
        获取宏观经济数据。

        Args:
            as_of_date: 截止日期（含），用于历史回放场景防止前视偏差。
                        使用 publish_date（保守估算发布日）过滤，而非 period（报告期）。
                        None 表示不限制（取最新一期）。

        Returns:
            DataFrame with latest macro economy data
            (period, publish_date, m2, m2_yoy, m1, m1_yoy, m0, m0_yoy, cpi, ppi,
             gdp, gdp_yoy, pi, pi_yoy, si, si_yoy, ti, ti_yoy)

        Note:
            Phase 2D §3.2.6：GDP 行与月度行 period 不同（季度末日 vs 月初），
            二者作为独立行存储。返回最多 2 行（publish_date 倒序）：最新月度行
            + 最新 GDP 行。调用方需用 ``pd.notna()`` 判断各字段是否可用。
        """
        try:
            t = MacroEconomy.__table__
            cols = [
                t.c.period,
                t.c.publish_date,
                t.c.m2,
                t.c.m2_yoy,
                t.c.m1,
                t.c.m1_yoy,
                t.c.m0,
                t.c.m0_yoy,
                t.c.cpi,
                t.c.ppi,
                # Phase 2D §3.2.6：cn_gdp 全链路补全（8 个 GDP 字段）
                t.c.gdp,
                t.c.gdp_yoy,
                t.c.pi,
                t.c.pi_yoy,
                t.c.si,
                t.c.si_yoy,
                t.c.ti,
                t.c.ti_yoy,
            ]
            stmt = sa.select(*cols)
            if as_of_date is not None:
                stmt = stmt.where(t.c.publish_date <= as_of_date)
            # 返回最多 2 行：月度行（m2/cpi/ppi）与 GDP 行（gdp_yoy/pi_yoy 等）
            # period 不同（月度 YYYY-MM-01 vs 季度末日），作为独立行存储。
            stmt = stmt.order_by(t.c.publish_date.desc()).limit(2)

            df = await self._read_db_select(stmt)
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[MacroDao] Failed to get macro economy latest: %s", e)
            return pd.DataFrame()
