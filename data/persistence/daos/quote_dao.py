import datetime
import logging
import re
import typing

import pandas as pd

from data.constants import MAJOR_INDICES, attach_top_list_column_units
from data.persistence.models import (
    BlockTrade,
    DailyQuotes,
    IndexDaily,
    IndexDailyBasic,
    LimitList,
    MarginDaily,
    MoneyflowDaily,
    NorthboundHolding,
    SuspendD,
    TopList,
    get_model_columns,
    get_model_pk_columns,
)

from .base_dao import BaseDao

logger = logging.getLogger(__name__)

_DEFAULT_SYNCED_TABLES: list[str] | None = None

LOW_FREQUENCY_TABLES = {"limit_list", "suspend_d", "top_list", "block_trade"}

FIXED_EXPECTED_TABLES: dict[str, int] = {
    "index_daily": len(MAJOR_INDICES),
    "index_dailybasic": len(MAJOR_INDICES),
    "moneyflow_hsgt": 1,
}

_SAFE_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "daily_quotes",
        "daily_indicators",
        "moneyflow_daily",
        "margin_daily",
        "northbound_holding",
        "moneyflow_hsgt",
        "index_daily",
        "index_dailybasic",
        "index_weight",
        "limit_list",
        "top_list",
        "block_trade",
        "suspend_d",
        "financial_reports",
        "fina_audit",
        "fina_forecast",
        "fina_mainbz",
        "dividend",
        "repurchase",
        "pledge_stat",
        "shibor_daily",
        "stk_holdernumber",
        "top10_holders",
        "trade_cal",
        "stock_basic",
        "macro_economy",
        "market_news",
        "cn_m",
    }
)


def _get_default_synced_tables() -> list[str]:
    """
    Lazy load default synced tables from HistoricalSyncStrategy.
    Avoids circular import at module load time.

    Security: Only returns tables that exist in the hardcoded safe whitelist.
    """
    global _DEFAULT_SYNCED_TABLES
    if _DEFAULT_SYNCED_TABLES is None:
        from data.sync.historical import HistoricalSyncStrategy

        raw_tables = HistoricalSyncStrategy.SYNCED_TABLES.copy()
        _DEFAULT_SYNCED_TABLES = [t for t in raw_tables if t in _SAFE_TABLE_NAMES]
    return _DEFAULT_SYNCED_TABLES


_SAFE_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _is_safe_identifier(name: str) -> bool:
    """Check if a name is a safe SQL identifier (lowercase letters, digits, underscores)."""
    return bool(_SAFE_IDENTIFIER_RE.match(name))


def _normalize_trade_date(val: typing.Any) -> typing.Any:
    """
    Normalize trade date value to datetime.date.

    Handles datetime.datetime, str (YYYYMMDD format), and datetime.date inputs.
    Returns the original value if conversion fails.
    """
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, str):
        try:
            return datetime.datetime.strptime(val, "%Y%m%d").date()
        except ValueError:
            return val
    return val


class QuoteDao(BaseDao):
    # --- Daily Quotes ---
    async def save_daily_quotes(
        self,
        df: pd.DataFrame,
        priority: int | None = None,
        suppress_errors: bool = True,
    ):
        df = df.copy()
        if "adj_factor" in df.columns:
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    df[f"qfq_{col}"] = df[col] * df["adj_factor"]
        cols = get_model_columns(DailyQuotes)
        pk_columns = get_model_pk_columns(DailyQuotes)
        return await self._save_upsert(
            df,
            "daily_quotes",
            cols,
            pk_columns=pk_columns,
            suppress_errors=suppress_errors,
        )

    async def check_data_exists(self, trade_date: datetime.date | str, tables: list | None = None) -> bool:
        """
        Check if data exists for all synced tables on a given trade_date.
        This is used for reliable breakpoint resume - only skip a date if ALL
        synced tables have data.

        :param trade_date: The trade date to check
        :param tables: List of table names to check. If None, uses tables from HistoricalSyncStrategy.SYNCED_TABLES.
        :return: True if all tables have data for the given date
        """
        if tables is None:
            tables = _get_default_synced_tables()

        allowed_tables = set(_get_default_synced_tables())
        for table in tables:
            if table not in allowed_tables or not _is_safe_identifier(table):
                logger.warning(f"[QuoteDao] Invalid table name rejected: {table}")
                return False
            try:
                df = await self._read_db(
                    f"SELECT 1 as val FROM {table} WHERE trade_date=$1 LIMIT 1",
                    (trade_date,),
                )
                if df is None or df.empty:
                    return False
            except Exception:
                return False

        return True

    async def get_expected_stock_count(self, trade_date: datetime.date | str) -> int:
        """
        计算指定日期的理论存活股票数。

        使用 delist_date 精确排除历史某天已退市的股票。
        同时验证该日期是否为交易日。

        Note:
            退市逻辑必须与 get_bulk_expected_stock_counts 保持同步。
            WHERE 条件语义：
            - list_status='L' 且 delist_date 为空或大于当前日期 → 存活
            - list_status='D' 且 delist_date 非空且大于当前日期 → 存活（退市后仍有数据）

        Args:
            trade_date: 交易日期

        Returns:
            该日理论上应该有行情数据的股票数量
        """
        try:
            df = await self._read_db(
                """
                WITH trade_day_check AS (
                    SELECT 1 as is_trade_day
                    FROM trade_cal
                    WHERE cal_date = $1
                      AND is_open = 1
                      AND exchange = 'SSE'
                ),
                stock_counts AS (
                    SELECT COUNT(*) as cnt
                    FROM stock_basic
                    WHERE list_date <= $1
                      AND (
                        (list_status = 'L' AND (delist_date IS NULL OR delist_date > $1))
                        OR
                        (list_status = 'D' AND delist_date IS NOT NULL AND delist_date > $1)
                      )
                )
                SELECT
                    COALESCE((SELECT is_trade_day FROM trade_day_check), 0) as is_trade_day,
                    (SELECT cnt FROM stock_counts) as cnt
                """,
                (trade_date,),
            )

            if df is not None and not df.empty:
                is_trade_day = int(df["is_trade_day"].iloc[0])
                if is_trade_day == 0:
                    logger.debug(f"[QuoteDao] {trade_date} is not a trading day")
                    return 0
                return int(df["cnt"].iloc[0])
            return 0
        except Exception as e:
            logger.warning(f"[QuoteDao] Failed to get expected stock count for {trade_date}: {e}")
            return 0

    async def get_daily_quotes(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code_list: list | None = None,
    ):
        sql = "SELECT ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor FROM daily_quotes WHERE 1=1"
        params = []
        idx = 1

        if ts_code:
            sql += f" AND ts_code = ${idx}"
            params.append(ts_code)
            idx += 1
        if start_date:
            sql += f" AND trade_date >= ${idx}"
            params.append(start_date)
            idx += 1
        if end_date:
            sql += f" AND trade_date <= ${idx}"
            params.append(end_date)
            idx += 1

        if ts_code_list:
            # Split into chunks of 500 for large queries
            chunk_size = 500
            if len(ts_code_list) > chunk_size:
                logger.debug(f"[QuoteDao] Chunking query for {len(ts_code_list)} codes")
                all_results = []

                # Base SQL without the IN clause
                base_sql = sql
                base_params = params.copy()
                base_idx = idx

                for i in range(0, len(ts_code_list), chunk_size):
                    chunk = ts_code_list[i : i + chunk_size]
                    placeholders = ",".join(
                        [f"${base_idx + j}" for j in range(len(chunk))],
                    )
                    chunk_sql = base_sql + f" AND ts_code IN ({placeholders})"
                    chunk_params = base_params + chunk

                    df_chunk = await self._read_db(chunk_sql, chunk_params)
                    if not df_chunk.empty:
                        all_results.append(df_chunk)

                if all_results:
                    return pd.concat(all_results, ignore_index=True)
                return pd.DataFrame()
            placeholders = ",".join(
                [f"${idx + j}" for j in range(len(ts_code_list))],
            )
            sql += f" AND ts_code IN ({placeholders})"
            params.extend(ts_code_list)

        return await self._read_db(sql, params)

    async def get_latest_trade_date(self):
        df = await self._read_db("SELECT MAX(trade_date) as max_td FROM daily_quotes")
        if df is not None and not df.empty:
            return df["max_td"].iloc[0]
        return None

    async def get_cached_trade_dates(self):
        df = await self._read_db(
            "SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date",
        )
        if df is None or df.empty:
            return set()
        return set(df["trade_date"])

    async def get_cached_dates_for_table(self, table_name: str) -> set:
        """
        Get distinct dates from a table for breakpoint resume check.
        Supports tables with trade_date, end_date, or ann_date columns.
        """
        date_col_map = {
            "daily_quotes": "trade_date",
            "daily_indicators": "trade_date",
            "moneyflow_daily": "trade_date",
            "northbound_holding": "trade_date",
            "moneyflow_hsgt": "trade_date",
            "margin_daily": "trade_date",
            "limit_list": "trade_date",
            "suspend_d": "trade_date",
            "top_list": "trade_date",
            "block_trade": "trade_date",
            "index_daily": "trade_date",
            "index_dailybasic": "trade_date",
        }

        if table_name not in date_col_map:
            logger.warning(f"[QuoteDao] Invalid table name rejected: {table_name}")
            return set()

        date_col = date_col_map[table_name]

        if not _is_safe_identifier(table_name) or not _is_safe_identifier(date_col):
            logger.warning(f"[QuoteDao] Invalid identifier rejected: table={table_name}, col={date_col}")
            return set()

        try:
            df = await self._read_db(
                f"SELECT DISTINCT {date_col} FROM {table_name} ORDER BY {date_col}",
            )
            if df is None or df.empty:
                return set()
            return set(df[date_col])
        except Exception as e:
            logger.warning(
                f"[QuoteDao] Failed to get cached dates for {table_name}: {e}",
            )
            return set()

    async def get_date_range(self):
        """
        Get the min and max trade dates from daily_quotes.
        Used for health check baseline calculation.

        Returns:
            tuple: (min_date, max_date) or (None, None)
        """
        df = await self._read_db("SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date FROM daily_quotes")
        if df is None or df.empty:
            return None, None
        return df["min_date"].iloc[0], df["max_date"].iloc[0]

    # --- Index Data ---
    async def save_index_daily(self, df: pd.DataFrame):
        cols = get_model_columns(IndexDaily)
        pk_columns = get_model_pk_columns(IndexDaily)
        return await self._save_upsert(
            df,
            "index_daily",
            cols,
            pk_columns=pk_columns,
        )

    async def save_index_dailybasic(self, df: pd.DataFrame):
        cols = get_model_columns(IndexDailyBasic)
        pk_columns = get_model_pk_columns(IndexDailyBasic)
        return await self._save_upsert(
            df,
            "index_dailybasic",
            cols,
            pk_columns=pk_columns,
        )

    async def get_index_daily(self, ts_code: str | None = None, trade_date: str | None = None):
        sql = "SELECT * FROM index_daily WHERE 1=1"
        p = []
        idx = 1
        if ts_code:
            sql += f" AND ts_code=${idx}"
            p.append(ts_code)
            idx += 1
        if trade_date:
            sql += f" AND trade_date=${idx}"
            p.append(trade_date)
        sql += " ORDER BY trade_date DESC"
        return await self._read_db(sql, p)

    async def get_index_daily_range(
        self,
        ts_code_list: list,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """
        批量获取多只指数的日线数据。

        Args:
            ts_code_list: 指数代码列表 (如 ['000001.SH', '399001.SZ'])
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame 包含所有指定指数的日线数据
        """
        if not ts_code_list:
            return await self._read_db("SELECT * FROM index_daily WHERE 1=0", [])

        sql = "SELECT ts_code, trade_date, close, pct_chg, vol, amount FROM index_daily WHERE 1=1"
        params = []
        idx = 1

        if start_date:
            sql += f" AND trade_date >= ${idx}"
            params.append(start_date)
            idx += 1
        if end_date:
            sql += f" AND trade_date <= ${idx}"
            params.append(end_date)
            idx += 1

        placeholders = ",".join([f"${idx + j}" for j in range(len(ts_code_list))])
        sql += f" AND ts_code IN ({placeholders})"
        params.extend(ts_code_list)
        sql += " ORDER BY ts_code, trade_date"
        return await self._read_db(sql, params)

    # --- Block Trade ---
    async def save_block_trade(self, df: pd.DataFrame):
        cols = get_model_columns(BlockTrade)
        pk_columns = get_model_pk_columns(BlockTrade)
        return await self._save_upsert(
            df,
            "block_trade",
            cols,
            pk_columns=pk_columns,
        )

    async def get_block_trade(self, trade_date: str | None = None):
        sql = "SELECT * FROM block_trade WHERE 1=1"
        p = []
        if trade_date:
            sql += " AND trade_date=$1"
            p.append(trade_date)
        return await self._read_db(sql, p)

    # --- Limit List ---
    async def save_limit_list(self, df: pd.DataFrame):
        cols = get_model_columns(LimitList)
        pk_columns = get_model_pk_columns(LimitList)
        return await self._save_upsert(
            df,
            "limit_list",
            cols,
            pk_columns=pk_columns,
        )

    # --- Top List ---
    async def save_top_list(self, df: pd.DataFrame):
        cols = get_model_columns(TopList)
        pk_columns = get_model_pk_columns(TopList)
        return await self._save_upsert(
            df,
            "top_list",
            cols,
            pk_columns=pk_columns,
        )

    async def get_top_list(self, trade_date: str | None = None):
        sql = "SELECT * FROM top_list WHERE 1=1"
        p = []
        if trade_date:
            sql += " AND trade_date=$1"
            p.append(trade_date)
        df = await self._read_db(sql, p)
        return attach_top_list_column_units(df)

    # --- Margin ---
    async def save_margin_daily(self, df: pd.DataFrame):
        cols = get_model_columns(MarginDaily)
        pk_columns = get_model_pk_columns(MarginDaily)
        return await self._save_upsert(
            df,
            "margin_daily",
            cols,
            pk_columns=pk_columns,
        )

    # --- Suspend ---
    async def save_suspend_d(self, df: pd.DataFrame):
        cols = get_model_columns(SuspendD)
        pk_columns = get_model_pk_columns(SuspendD)
        return await self._save_upsert(
            df,
            "suspend_d",
            cols,
            pk_columns=pk_columns,
        )

    # --- Moneyflow ---
    async def save_moneyflow(self, df: pd.DataFrame):
        cols = get_model_columns(MoneyflowDaily)
        pk_columns = get_model_pk_columns(MoneyflowDaily)
        return await self._save_upsert(
            df,
            "moneyflow_daily",
            cols,
            pk_columns=pk_columns,
        )

    async def get_moneyflow(self, trade_date: str | None = None, ts_code: str | None = None):
        sql = "SELECT * FROM moneyflow_daily WHERE 1=1"
        p = []
        idx = 1
        if trade_date:
            sql += f" AND trade_date=${idx}"
            p.append(trade_date)
            idx += 1
        if ts_code:
            sql += f" AND ts_code=${idx}"
            p.append(ts_code)
        return await self._read_db(sql, p)

    # --- Northbound ---
    async def save_northbound(self, df: pd.DataFrame):
        cols = get_model_columns(NorthboundHolding)
        pk_columns = get_model_pk_columns(NorthboundHolding)
        return await self._save_upsert(
            df,
            "northbound_holding",
            cols,
            pk_columns=pk_columns,
        )

    async def get_northbound(self, trade_date: str | None = None, ts_code: str | None = None):
        sql = "SELECT * FROM northbound_holding WHERE 1=1"
        p = []
        idx = 1
        if trade_date:
            sql += f" AND trade_date=${idx}"
            p.append(trade_date)
            idx += 1
        if ts_code:
            sql += f" AND ts_code=${idx}"
            p.append(ts_code)
        return await self._read_db(sql, p)

    async def get_latest_northbound(self):
        df = await self._read_db(
            "SELECT MAX(trade_date) as max_td FROM northbound_holding",
        )
        td = df["max_td"].iloc[0] if df is not None and not df.empty else None
        if not td:
            return pd.DataFrame()
        return await self.get_northbound(trade_date=td)

    async def get_bulk_table_counts(
        self,
        table_name: str,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict[datetime.date, int]:
        """
        批量获取指定时间范围内每天的记录数。

        避免逐日查询，单次SQL返回所有日期的统计。

        Args:
            table_name: 表名
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            dict[日期, 记录数]
        """
        allowed_tables = set(_get_default_synced_tables())
        if table_name not in allowed_tables:
            logger.warning(f"[QuoteDao] Invalid table name rejected: {table_name}")
            return {}

        try:
            df = await self._read_db(
                f"""
                SELECT trade_date, COUNT(*) as cnt
                FROM {table_name}
                WHERE trade_date BETWEEN $1 AND $2
                GROUP BY trade_date
                """,
                (start_date, end_date),
            )

            if df is None or df.empty:
                return {}

            normalized_results = {}
            for trade_date, count in zip(df["trade_date"], df["cnt"], strict=False):
                normalized_results[_normalize_trade_date(trade_date)] = count

            return normalized_results
        except Exception as e:
            logger.warning(f"[QuoteDao] Failed to get bulk counts for {table_name}: {e}")
            return {}

    async def get_bulk_expected_stock_counts(
        self,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict[datetime.date, int]:
        """
        批量获取指定时间范围内每天的理论存活股票数。

        使用 delist_date 精确排除历史退市股票。

        Note:
            退市逻辑必须与 get_expected_stock_count 保持同步。
            WHERE 条件语义：
            - list_status='L' 且 delist_date 为空或大于当前日期 → 存活
            - list_status='D' 且 delist_date 非空且大于当前日期 → 存活（退市后仍有数据）

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            dict[日期, 理论存活股票数]
        """
        try:
            df = await self._read_db(
                """
                WITH trading_days AS (
                    SELECT cal_date AS trade_date
                    FROM trade_cal
                    WHERE cal_date BETWEEN $1 AND $2
                      AND is_open = 1
                      AND exchange = 'SSE'
                ),
                stock_counts AS (
                    SELECT
                        t.trade_date,
                        COUNT(s.ts_code) as expected_count
                    FROM trading_days t
                    LEFT JOIN stock_basic s ON s.list_date <= t.trade_date
                        AND (
                            (s.list_status = 'L' AND (s.delist_date IS NULL OR s.delist_date > t.trade_date))
                            OR
                            (s.list_status = 'D' AND s.delist_date IS NOT NULL AND s.delist_date > t.trade_date)
                        )
                    GROUP BY t.trade_date
                )
                SELECT trade_date, expected_count FROM stock_counts
                ORDER BY trade_date
                """,
                (start_date, end_date),
            )

            if df is None or df.empty:
                logger.warning(f"[QuoteDao] No trading days found for range {start_date} to {end_date}")
                return {}

            normalized_results = {}
            for trade_date, count in zip(df["trade_date"], df["expected_count"], strict=False):
                normalized_results[_normalize_trade_date(trade_date)] = count

            return normalized_results
        except Exception as e:
            logger.warning(f"[QuoteDao] Failed to get bulk expected counts: {e}")
            return {}

    async def get_bulk_sync_quality_scores(
        self,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
        tables: list | None = None,
    ) -> dict[datetime.date, dict]:
        """
        批量评估指定时间范围内每天的数据同步质量。

        使用批量聚合查询避免 N+1 问题，性能提升数百倍。

        Args:
            start_date: 开始日期
            end_date: 结束日期
            tables: 要检查的表列表

        Returns:
            {trade_date: quality_info} 字典，其中 quality_info 包含：
            {
                "score": 0-100,
                "expected_base": int,
                "tables": {table_name: {"count": int, "expected": int, "ratio": float, "passed": bool}},
                "issues": [str],
            }
        """
        from utils.config_handler import ConfigHandler

        if tables is None:
            tables = _get_default_synced_tables()

        config = ConfigHandler.get_sync_integrity_config()

        expected_bases = await self.get_bulk_expected_stock_counts(start_date, end_date)

        if not expected_bases:
            logger.warning("[QuoteDao] Cannot determine expected bases for quality check")
            return {}

        table_counts = {}
        for table in tables:
            table_counts[table] = await self.get_bulk_table_counts(table, start_date, end_date)

        table_tolerance_map = {
            "daily_quotes": config["quotes_tolerance_ratio"],
            "daily_indicators": config["indicators_tolerance_ratio"],
            "moneyflow_daily": config["moneyflow_tolerance_ratio"],
            "margin_daily": config["moneyflow_tolerance_ratio"],
            "northbound_holding": 0.50,
            "limit_list": 0.30,
            "suspend_d": 0.10,
            # tolerance values below are not used for expected count calculation
            # (FIXED_EXPECTED_TABLES provides fixed expected counts instead).
            "index_daily": 0.95,
            "index_dailybasic": 0.95,
            "top_list": 0.30,
            "block_trade": 0.20,
            "moneyflow_hsgt": 0.95,
        }

        results = {}

        for trade_date, expected_base in expected_bases.items():
            result = {
                "score": 0,
                "expected_base": expected_base,
                "tables": {},
                "issues": [],
            }

            if expected_base == 0:
                result["issues"].append("无法计算理论股票数")
                results[trade_date] = result
                continue

            quotes_count = table_counts.get("daily_quotes", {}).get(trade_date, 0)
            quotes_ratio = min(1.0, quotes_count / expected_base) if expected_base > 0 else 0
            quotes_passed = quotes_ratio >= config["quotes_tolerance_ratio"]

            result["tables"]["daily_quotes"] = {
                "count": quotes_count,
                "expected": expected_base,
                "ratio": quotes_ratio,
                "passed": quotes_passed,
            }

            if not quotes_passed:
                result["issues"].append(f"daily_quotes: {quotes_count}/{expected_base} ({quotes_ratio:.1%})")

            reference_count = quotes_count if quotes_count > 0 else expected_base

            # Low-frequency exemption must be controlled by explicit table allowlist,
            # not by tolerance values, to avoid accidental score inflation after config changes.
            low_frequency_tables = {t for t in LOW_FREQUENCY_TABLES if t in tables}

            for table in tables:
                if table == "daily_quotes":
                    continue

                count = table_counts.get(table, {}).get(trade_date, 0)
                tolerance = table_tolerance_map.get(table, 0.80)

                if table in low_frequency_tables:
                    result["tables"][table] = {
                        "count": count,
                        "expected": 0,
                        "ratio": None,
                        "passed": True,
                        "exempt": True,
                        "note": "低频事件表，不计入评分",
                    }
                    continue

                if table in FIXED_EXPECTED_TABLES:
                    expected = FIXED_EXPECTED_TABLES[table]
                else:
                    expected = int(reference_count * tolerance)

                ratio = min(1.0, count / expected) if expected > 0 else 0
                passed = count >= expected

                result["tables"][table] = {
                    "count": count,
                    "expected": expected,
                    "ratio": ratio,
                    "passed": passed,
                }

                if not passed:
                    result["issues"].append(f"{table}: {count}/{expected}")

            quality_weights = config.get("quality_weights", {})
            total_weight = 0
            weighted_score = 0

            for table, info in result["tables"].items():
                if info.get("exempt") or info.get("ratio") is None:
                    continue
                if "ratio" in info:
                    weight = quality_weights.get(table, 5)
                    weighted_score += info["ratio"] * weight
                    total_weight += weight

            if total_weight > 0:
                result["score"] = int(min(100, (weighted_score / total_weight) * 100))

            results[trade_date] = result

        field_completeness = {}
        try:
            sorted_dates = [d for d in results if results[d].get("expected_base", 0) > 0]
            if sorted_dates:
                latest_date = max(sorted_dates)
                field_completeness = await self.get_field_completeness(latest_date)
        except Exception as e:
            logger.debug(f"[QuoteDao] Field completeness check skipped: {e}")

        if field_completeness:
            for trade_date in results:
                results[trade_date]["field_completeness"] = field_completeness

        return results

    async def get_field_completeness(self, trade_date: str | datetime.date) -> dict[str, float]:
        """Query field-level fundamental completeness for a given trade_date.

        Returns a dict mapping field names (roe, or_yoy, etc.) to their non-null ratio
        across all listed stocks. Returns empty dict on failure.

        For historical dates where daily_indicators data is unavailable,
        indicator fields are excluded from the result to avoid conflating
        "not yet synced" with "field missing".
        """
        field_sql = """
            SELECT
                COUNT(*) AS total,
                COUNT(i.trade_date) AS indicators_available,
                COUNT(roe) AS roe_count,
                COUNT(or_yoy) AS or_yoy_count,
                COUNT(netprofit_yoy) AS netprofit_yoy_count,
                COUNT(dv_ttm) AS dv_ttm_count,
                COUNT(pe_ttm) AS pe_ttm_count,
                COUNT(pb) AS pb_count,
                COUNT(debt_to_assets) AS debt_to_assets_count
            FROM (
                SELECT b.ts_code,
                       i.trade_date AS indicator_date,
                       i.pe_ttm, i.pb, i.dv_ttm,
                       f.roe, f.or_yoy, f.netprofit_yoy, f.debt_to_assets
                FROM stock_basic b
                LEFT JOIN daily_indicators i ON b.ts_code = i.ts_code AND i.trade_date = $1
                LEFT JOIN (SELECT ts_code, roe, or_yoy, netprofit_yoy, debt_to_assets,
                                  ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY ann_date DESC, end_date DESC) AS rn
                           FROM financial_reports WHERE ann_date <= $2) f
                          ON b.ts_code = f.ts_code AND f.rn = 1
                WHERE b.list_status = 'L'
            ) sub
        """
        try:
            df_fields = await self._read_db(field_sql, (trade_date, trade_date))
            if df_fields is not None and not df_fields.empty:
                row_f = df_fields.iloc[0]
                total = int(row_f["total"]) if row_f["total"] else 0
                if total > 0:
                    indicators_available = int(row_f["indicators_available"]) if row_f["indicators_available"] else 0
                    indicator_coverage = indicators_available / total if total > 0 else 0.0
                    result = {}
                    fin_fields = ["roe", "or_yoy", "netprofit_yoy", "debt_to_assets"]
                    ind_fields = ["dv_ttm", "pe_ttm", "pb"]
                    for col in fin_fields:
                        result[col] = float(row_f[f"{col}_count"]) / total
                    if indicator_coverage >= 0.5:
                        for col in ind_fields:
                            result[col] = float(row_f[f"{col}_count"]) / total
                    else:
                        for col in ind_fields:
                            result[col] = None
                    return result
        except Exception as e:
            logger.debug(f"[QuoteDao] get_field_completeness failed for {trade_date}: {e}")
        return {}

    async def get_sync_quality_score(self, trade_date: datetime.date | str) -> dict:
        """
        评估单个日期的数据同步质量（相对基准法）。

        注意：此方法用于单日期实时检查。
        批量检查请使用 get_bulk_sync_quality_scores 以避免 N+1 查询风暴。

        Returns:
            {
                "score": 0-100,
                "expected_base": int,
                "tables": {table_name: {"count": int, "expected": int, "ratio": float, "passed": bool}},
                "issues": [str],
            }
        """
        if isinstance(trade_date, str):
            normalized = datetime.datetime.strptime(trade_date, "%Y%m%d").date()
        else:
            normalized = trade_date

        results = await self.get_bulk_sync_quality_scores(normalized, normalized)
        return results.get(
            normalized,
            {"score": 0, "expected_base": 0, "tables": {}, "issues": ["查询失败"]},
        )
