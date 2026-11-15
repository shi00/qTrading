import asyncio
import logging
from dataclasses import dataclass

import pandas as pd

from data.persistence.models import StkHoldernumber, Top10Holders, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, DatabaseQueryError, EngineDisposedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Top10PersistResult:
    """top10_holders 单次写入的接纳统计（D09-002）。

    字段口径与 ``_save_upsert`` 的落库返回值对齐：
    - ``fetched``：调用方提交的输入行数；
    - ``rejected``：因 ``holder_name`` 为 NULL 被 DAO 过滤掉的输入行数；
    - ``accepted``：过滤后按主键（UPSERT 冲突键）去重得到的唯一业务键数；
    - ``saved``：``_save_upsert`` 实际落库行数。

    调用方据此确认落库事实，不得以 ``fetched`` 宣称保存成功。
    """

    fetched: int
    rejected: int
    accepted: int
    saved: int

    @property
    def confirmed(self) -> bool:
        """全部被接纳的唯一业务键均已落库；零写入不算确认。"""
        return self.accepted > 0 and self.saved >= self.accepted


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
            sql_template = """
                UPDATE stk_holdernumber h
                SET holder_num_change = sub.holder_num_change,
                    holder_num_ratio = sub.holder_num_ratio,
                    updated_at = now()
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
            async with self._guarded_begin() as tx_conn:
                await self.chunked_in_write(
                    self._write_db,
                    sql_template,
                    ts_codes,
                    conn=tx_conn,
                )
            logger.debug("[HolderDao] Calculated holder changes for %d stocks", len(ts_codes))
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except DatabaseQueryError:
            # R5/R9 一致性: DatabaseQueryError 是显式 suppress_errors=False 抛出的预期异常,
            # 不应被 except Exception 吞没为 warning (与 P1-4 修复目标"消除静默不更新"同根).
            raise
        except Exception as e:
            logger.warning("[HolderDao] Failed to calculate holder changes: %s", DataSanitizer.sanitize_error(e))

    async def save_top10_holders(self, df: pd.DataFrame) -> Top10PersistResult:
        """Save Top 10 Holders. Table: top10_holders

        D09-002: 返回真实接纳/落库/拒绝计数，调用方须以 ``saved`` 而非输入行数确认成功；
        全部行因 ``holder_name`` 为 NULL 被拒时返回 ``accepted=0, saved=0``（confirmed=False），
        不再以裸 0 让调用方误判为「已保存」。
        """
        if df is None or df.empty:
            return Top10PersistResult(fetched=0, rejected=0, accepted=0, saved=0)
        fetched = len(df)
        rejected = 0
        if "holder_name" in df.columns:
            null_flags = df["holder_name"].isna().to_numpy()
            rejected = int(null_flags.sum())
            if rejected > 0:
                logger.warning("[HolderDao] Filtering %s rows with NULL holder_name from top10_holders", rejected)
                df = df.loc[~null_flags].copy()
            if df.empty:
                return Top10PersistResult(fetched=fetched, rejected=rejected, accepted=0, saved=0)
        cols = get_model_columns(Top10Holders)
        pk_columns = get_model_pk_columns(Top10Holders)
        # D09-002: accepted 与 _save_upsert 的冲突键口径一致（过滤后按主键去重），
        # 使「重复 UPSERT 静默合并」不被误判为短写，只有真实丢行才算未确认。
        # 主键列缺失时 _save_upsert 会拒写（返回 0），此处 accepted 记 0 以免去重报错。
        if all(col in df.columns for col in pk_columns):
            accepted = len(df.drop_duplicates(subset=pk_columns))
        else:
            accepted = 0
        saved = int(
            await self._save_upsert(
                df,
                "top10_holders",
                cols,
                pk_columns=pk_columns,
            )
        )
        if saved < accepted:
            logger.warning(
                "[HolderDao] top10_holders short write: accepted=%s saved=%s (fetched=%s, rejected=%s)",
                accepted,
                saved,
                fetched,
                rejected,
            )
        return Top10PersistResult(fetched=fetched, rejected=rejected, accepted=accepted, saved=saved)

    async def get_top10_holders(self, ts_code: str, as_of_date=None) -> pd.DataFrame:
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_name, hold_amount,
                           hold_ratio, hold_float_ratio, hold_change, holder_type
                    FROM top10_holders
                    WHERE ts_code = $1 AND ann_date IS NOT NULL AND ann_date <= $2
                    ORDER BY end_date DESC, hold_ratio DESC NULLS LAST
                    LIMIT 20
                    """,
                    (ts_code, self._to_db_date(as_of_date)),
                )
            else:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_name, hold_amount,
                           hold_ratio, hold_float_ratio, hold_change, holder_type
                    FROM top10_holders
                    WHERE ts_code = $1 AND ann_date IS NOT NULL
                    ORDER BY end_date DESC, hold_ratio DESC NULLS LAST
                    LIMIT 20
                    """,
                    (ts_code,),
                )
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
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
                    WHERE ts_code = $1 AND ann_date IS NOT NULL AND ann_date <= $2
                    ORDER BY end_date DESC
                    LIMIT 5
                    """,
                    (ts_code, self._to_db_date(as_of_date)),
                )
            else:
                df = await self._read_db(
                    """
                    SELECT ts_code, end_date, ann_date, holder_num,
                           holder_num_change, holder_num_ratio
                    FROM stk_holdernumber
                    WHERE ts_code = $1 AND ann_date IS NOT NULL
                    ORDER BY end_date DESC
                    LIMIT 5
                    """,
                    (ts_code,),
                )
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[HolderDao] Failed to get holder number for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            return pd.DataFrame()

    async def get_top10_holders_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        def sql_fn(as_of):
            if as_of is not None:

                def sql_template_fn(placeholders, chunk_len):
                    ann_date_param = chunk_len + 1
                    return f"""
                        SELECT DISTINCT ON (ts_code, end_date)
                            ts_code, end_date, ann_date, holder_name, hold_ratio
                        FROM top10_holders
                        WHERE ts_code IN ({placeholders})
                          AND ann_date IS NOT NULL
                          AND ann_date <= ${ann_date_param}
                        ORDER BY ts_code, end_date DESC, hold_ratio DESC NULLS LAST
                    """

                return (sql_template_fn, lambda chunk: [as_of])
            return (
                """
                SELECT DISTINCT ON (ts_code, end_date)
                    ts_code, end_date, ann_date, holder_name, hold_ratio
                FROM top10_holders
                WHERE ts_code IN ({placeholders})
                  AND ann_date IS NOT NULL
                ORDER BY ts_code, end_date DESC, hold_ratio DESC NULLS LAST
                """,
                None,
            )

        return await self._batch_get_with_as_of_date(sql_fn, ts_codes, as_of_date, "Failed to get top10 holders batch")

    async def get_stk_holdernumber_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        if not ts_codes:
            return pd.DataFrame()

        def sql_fn(as_of):
            if as_of is not None:

                def sql_template_fn(placeholders, chunk_len):
                    ann_date_param = chunk_len + 1
                    return f"""
                        SELECT ts_code, end_date, ann_date, holder_num,
                               holder_num_change, holder_num_ratio
                        FROM (
                            SELECT *,
                                ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                            FROM stk_holdernumber
                            WHERE ts_code IN ({placeholders}) AND ann_date IS NOT NULL
                              AND ann_date <= ${ann_date_param}
                        ) sub
                        WHERE rn <= 5
                        ORDER BY ts_code, end_date DESC
                    """

                return (sql_template_fn, lambda chunk: [as_of])
            return (
                """
                SELECT ts_code, end_date, ann_date, holder_num,
                       holder_num_change, holder_num_ratio
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                    FROM stk_holdernumber
                    WHERE ts_code IN ({placeholders}) AND ann_date IS NOT NULL
                ) sub
                WHERE rn <= 5
                ORDER BY ts_code, end_date DESC
                """,
                None,
            )

        def post_process(df):
            if "rn" in df.columns:
                df = df.drop(columns=["rn"])
            return df

        return await self._batch_get_with_as_of_date(
            sql_fn,
            ts_codes,
            as_of_date,
            "Failed to get holder number batch",
            post_process=post_process,
        )

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
                (self._to_db_date(period),),
            )
            if df is not None and not df.empty:
                return set(df["ts_code"].tolist())
            return set()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[HolderDao] Failed to get existing top10 ts_codes for period=%s: %s",
                period,
                DataSanitizer.sanitize_error(e),
            )
            return set()
