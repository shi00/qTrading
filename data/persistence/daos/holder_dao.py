import asyncio
import logging

import pandas as pd

from data.persistence.models import StkHoldernumber, Top10Holders, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class HolderDao(BaseDao):
    """DAO for Shareholder Data (Chip Concentration, Institutional Holdings)."""

    async def save_holder_number(self, df: pd.DataFrame):
        """Save Stock Holder Number. Table: stk_holdernumber"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(StkHoldernumber)
        pk_columns = get_model_pk_columns(StkHoldernumber)
        rows = await self._save_upsert(
            df,
            "stk_holdernumber",
            cols,
            pk_columns=pk_columns,
        )
        await self._calculate_holder_changes(df["ts_code"].unique().tolist() if "ts_code" in df.columns else [])
        return rows

    async def _calculate_holder_changes(self, ts_codes: list[str]):
        if not ts_codes:
            return

        try:
            _CHUNK = 500
            for i in range(0, len(ts_codes), _CHUNK):
                chunk = ts_codes[i : i + _CHUNK]
                placeholders = ",".join([f"${j + 1}" for j in range(len(chunk))])
                sql = f"""
                    UPDATE stk_holdernumber h
                    SET holder_num_change = sub.holder_num_change,
                        holder_num_ratio = sub.holder_num_ratio
                    FROM (
                        SELECT ts_code, end_date,
                            holder_num - LAG(holder_num) OVER (
                                PARTITION BY ts_code ORDER BY end_date
                            ) as holder_num_change,
                            CASE
                                WHEN LAG(holder_num) OVER (
                                    PARTITION BY ts_code ORDER BY end_date
                                ) > 0 THEN
                                    ROUND(
                                        (holder_num - LAG(holder_num) OVER (
                                            PARTITION BY ts_code ORDER BY end_date
                                        ))::numeric / LAG(holder_num) OVER (
                                            PARTITION BY ts_code ORDER BY end_date
                                        ) * 100,
                                        2
                                    )
                                ELSE NULL
                            END as holder_num_ratio
                        FROM stk_holdernumber
                        WHERE ts_code IN ({placeholders})
                    ) sub
                    WHERE h.ts_code = sub.ts_code AND h.end_date = sub.end_date
                """
                await self._write_db(sql, tuple(chunk))
            logger.debug(f"[HolderDao] Calculated holder changes for {len(ts_codes)} stocks")
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[HolderDao] Failed to calculate holder changes: %s", DataSanitizer.sanitize_error(e))

    async def save_top10_holders(self, df: pd.DataFrame):
        """Save Top 10 Holders. Table: top10_holders"""
        if df is None or df.empty:
            return 0
        if "holder_name" in df.columns:
            null_count = df["holder_name"].isna().sum()
            if null_count > 0:
                logger.warning("[HolderDao] Filtering %s rows with NULL holder_name from top10_holders", null_count)
                df = df[df["holder_name"].notna()].copy()  # type: ignore[assignment]
            if df.empty:
                return 0
        cols = get_model_columns(Top10Holders)
        pk_columns = get_model_pk_columns(Top10Holders)
        return await self._save_upsert(
            df,
            "top10_holders",
            cols,
            pk_columns=pk_columns,
        )

    async def get_top10_holders(self, ts_code: str, as_of_date=None) -> pd.DataFrame:
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_name, hold_amount,
                           hold_ratio, hold_float_ratio, hold_change, holder_type
                    FROM top10_holders
                    WHERE ts_code = $1 AND ann_date <= $2
                    ORDER BY end_date DESC, hold_ratio DESC
                    LIMIT 20
                    """,
                    (ts_code, as_of_date),
                )
            else:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_name, hold_amount,
                           hold_ratio, hold_float_ratio, hold_change, holder_type
                    FROM top10_holders
                    WHERE ts_code = $1
                    ORDER BY end_date DESC, hold_ratio DESC
                    LIMIT 20
                    """,
                    (ts_code,),
                )
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[HolderDao] Failed to get top10 holders for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            return pd.DataFrame()

    async def get_stk_holdernumber(self, ts_code: str, as_of_date=None) -> pd.DataFrame:
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_num,
                           holder_num_change, holder_num_ratio
                    FROM stk_holdernumber
                    WHERE ts_code = $1 AND ann_date <= $2
                    ORDER BY end_date DESC
                    LIMIT 5
                    """,
                    (ts_code, as_of_date),
                )
            else:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_num,
                           holder_num_change, holder_num_ratio
                    FROM stk_holdernumber
                    WHERE ts_code = $1
                    ORDER BY end_date DESC
                    LIMIT 5
                    """,
                    (ts_code,),
                )
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[HolderDao] Failed to get holder number for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            return pd.DataFrame()

    async def get_top10_holders_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:

                def sql_template_fn(placeholders, chunk_len):
                    ann_date_param = chunk_len + 1
                    return f"""
                        SELECT DISTINCT ON (ts_code, end_date)
                            ts_code, end_date, ann_date, holder_name, hold_ratio
                        FROM top10_holders
                        WHERE ts_code IN ({placeholders})
                          AND ann_date <= ${ann_date_param}
                        ORDER BY ts_code, end_date DESC, hold_ratio DESC
                    """

                return await self.chunked_in_query(
                    self._read_db,
                    sql_template_fn,
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                return await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT DISTINCT ON (ts_code, end_date)
                        ts_code, end_date, ann_date, holder_name, hold_ratio
                    FROM top10_holders
                    WHERE ts_code IN ({placeholders})
                    ORDER BY ts_code, end_date DESC, hold_ratio DESC
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[HolderDao] Failed to get top10 holders batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_stk_holdernumber_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:

                def sql_template_fn(placeholders, chunk_len):
                    ann_date_param = chunk_len + 1
                    return f"""
                        SELECT ts_code, end_date, ann_date, holder_num,
                               holder_num_change, holder_num_ratio
                        FROM (
                            SELECT *,
                                ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                            FROM stk_holdernumber
                            WHERE ts_code IN ({placeholders}) AND ann_date <= ${ann_date_param}
                        ) sub
                        WHERE rn <= 5
                        ORDER BY ts_code, end_date DESC
                    """

                df = await self.chunked_in_query(
                    self._read_db,
                    sql_template_fn,
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                df = await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT ts_code, end_date, ann_date, holder_num,
                           holder_num_change, holder_num_ratio
                    FROM (
                        SELECT *,
                            ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                        FROM stk_holdernumber
                        WHERE ts_code IN ({placeholders})
                    ) sub
                    WHERE rn <= 5
                    ORDER BY ts_code, end_date DESC
                    """,
                    ts_codes,
                )
            if df is not None and not df.empty:
                if "rn" in df.columns:
                    df = df.drop(columns=["rn"])
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[HolderDao] Failed to get holder number batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()

    async def get_existing_top10_ts_codes(self, period: str) -> set[str]:
        """
        Get ts_codes that already have top10_holders data for a given period.

        Used for incremental sync: skip stocks that already have data,
        avoiding redundant O(N) API calls.

        Args:
            period: Reporting period in YYYYMMDD format (e.g. '20231231')

        Returns:
            Set of ts_codes that already have top10_holders data for this period
        """
        if not period:
            return set()

        try:
            df = await self._read_db(
                "SELECT DISTINCT ts_code FROM top10_holders WHERE end_date = $1",
                (period,),
            )
            if df is not None and not df.empty:
                return set(df["ts_code"].tolist())
            return set()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[HolderDao] Failed to get existing top10 ts_codes for period=%s: %s",
                period,
                DataSanitizer.sanitize_error(e),
            )
            return set()
