import asyncio
import datetime
import logging
import re
import typing

import pandas as pd
import sqlalchemy as sa

from utils.time_utils import get_now
from data.constants import (
    MAJOR_INDICES,
    attach_daily_quotes_column_units,
    attach_top_list_column_units,
    indices_to_sync,
)
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
    Base,
    get_model_columns,
    get_model_pk_columns,
)
from data.sync.base import safe_error

from .base_dao import BaseDao, EngineDisposedError
from .stock_dao import stock_alive_condition

logger = logging.getLogger(__name__)

_DEFAULT_SYNCED_TABLES: list[str] | None = None

LOW_FREQUENCY_TABLES = {"limit_list", "suspend_d", "top_list", "top_inst", "block_trade"}

# 每交易日必有数据的 dense 表（与 historical.py 的断点续传完成度判定集合一致）。
# 这些表"当日为空"必是真实缺口而非合法稀疏，故即使被误登记进 sync_empty_days
# 也不予豁免，防止"已尝试水位"式豁免退化后把 dense 缺口伪装成合法空（R21 精神）。
_DENSE_TABLES = frozenset({"daily_quotes", "daily_indicators"})

# review09-24 dim05 MAJOR-04：stock_basic 新鲜度阈值（自然日）。质量评分的理论股票数完全
# 派生自 stock_basic，该表陈旧超过此天数时在 issues 中标注"理论股票数可能偏低"。
_STOCK_BASIC_STALENESS_MAX_DAYS = 7

# DS-01: index_daily 期望行数在运行期按 indices_to_sync()（含配置基准）派生，见本
# 函数对 index_daily 的 special-case；此处保留基准下限值，供集成测试读取/断言。
FIXED_EXPECTED_TABLES: dict[str, int] = {
    "index_daily": len(MAJOR_INDICES),
    "index_dailybasic": len(MAJOR_INDICES),
    "moneyflow_hsgt": 1,
}


def _build_table_tolerance_map(config: dict[str, typing.Any]) -> dict[str, float]:
    """质量评分各表容忍系数登记表（期望行数 = int(理论存活股票数 expected_base * 容忍系数)）。

    未登记的表回落默认 0.80（见 get_bulk_sync_quality_scores）。新增同步表时必须显式
    登记于此、或登记进 LOW_FREQUENCY_TABLES / FIXED_EXPECTED_TABLES，否则会被一致性
    守护测试拦下（tests/unit/test_sync_type_consistency.py）。

    review09-24 dim05 MAJOR-01：stk_limit 与 daily_quotes 同源同密度（Tushare doc_id=183
    返回「全市场（含 A/B 股与基金）每日涨跌停价格」，每交易日每标的 1 行），故取与行情
    一致的容忍系数，仅容忍个别标的缺行。
    """
    return {
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
        "stk_limit": config["quotes_tolerance_ratio"],
        "block_trade": 0.20,
        "moneyflow_hsgt": 0.95,
    }


def _expected_count_for_table(table: str, expected_base: int, tolerance_map: dict[str, float]) -> int:
    """单表当日期望行数（质量评分与存在性检查共用的唯一正本）。

    口径与 get_bulk_sync_quality_scores 的历史实现一致（DS-01 / CRITICAL-01）：
    - index_daily：随同步目标集合动态派生（监控列表 ∪ 配置基准），避免配置外基准被写入后
      因期望行数仍按旧列表算而漏检满分；
    - index_dailybasic / moneyflow_hsgt：FIXED_EXPECTED_TABLES 的固定值；
    - 其余：int(理论存活股票数 expected_base * 该表容忍系数)。

    返回值可能为 0（expected_base 极小时）。调用方按各自语义处理：质量评分直接比较
    （``count >= 0`` 恒真），存在性检查用 ``max(1, ...)`` 兜底为"至少 1 行"。
    """
    if table == "index_daily":
        return len(indices_to_sync())
    if table in FIXED_EXPECTED_TABLES:
        return FIXED_EXPECTED_TABLES[table]
    return int(expected_base * tolerance_map.get(table, 0.80))


# review09-24 dim05 MAJOR-02：check_data_exists 按行数判定（而非"至少 1 行"）的稠密表。
# 这些表当日期望行数与全市场存活股票数同量级（行情 / 指标 / 资金流 / 涨跌停价，且涨跌停价
# 含 A/B 股与基金、行数不少于理论股票数）或为固定指数集合，截断写入（如 daily_quotes 只落
# 200/5400 行）必须判为"未完整"，否则该日会被永久当作完整日跳过。
# 未登记的表——两融 margin_daily、北向 northbound_holding 等"结构性覆盖子集"表，以及
# LOW_FREQUENCY_TABLES 低频事件表——其覆盖数结构性少于全市场，用 expected_base 推导最低
# 行数会持续误判为不完整（日常更新反复重同步同一日），故只做存在性判定（>= 1 行）。
_DENSITY_CHECKED_TABLES: frozenset[str] = frozenset(
    {
        "daily_quotes",
        "daily_indicators",
        "moneyflow_daily",
        "stk_limit",
        "index_daily",
        "index_dailybasic",
    }
)


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
        "top_inst",
        "block_trade",
        "suspend_d",
        "stk_limit",
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
    }
)

# get_cached_dates_for_table 支撑的「表名 → 日期列」登记表（断点续传用）。
# 新增同步表时须在此登记（守护测试见 tests/unit/test_sync_type_consistency.py），
# 否则 get_cached_dates_for_table 会按「非法表名」拒绝并返回空集。
_TABLE_DATE_COLUMN_MAP: dict[str, str] = {
    "daily_quotes": "trade_date",
    "daily_indicators": "trade_date",
    "moneyflow_daily": "trade_date",
    "northbound_holding": "trade_date",
    "moneyflow_hsgt": "trade_date",
    "margin_daily": "trade_date",
    "limit_list": "trade_date",
    "suspend_d": "trade_date",
    "top_list": "trade_date",
    "top_inst": "trade_date",
    "block_trade": "trade_date",
    "stk_limit": "trade_date",
    "index_daily": "trade_date",
    "index_dailybasic": "trade_date",
}


def _get_default_synced_tables() -> list[str]:
    """
    Lazy load default synced tables from HistoricalSyncStrategy.
    Avoids circular import at module load time.

    Security: Only returns tables that exist in the hardcoded safe whitelist.
    Note: This returns all tables regardless of API capability.
    For capability-aware table list, use get_effective_synced_tables() from TushareClient.
    """
    global _DEFAULT_SYNCED_TABLES
    if _DEFAULT_SYNCED_TABLES is None:
        from data.sync.historical import HistoricalSyncStrategy

        raw_tables = HistoricalSyncStrategy.SYNCED_TABLES.copy()
        _DEFAULT_SYNCED_TABLES = [t for t in raw_tables if t in _SAFE_TABLE_NAMES]
    return _DEFAULT_SYNCED_TABLES


def _get_effective_synced_tables() -> list[str]:
    """
    Get synced tables filtered by current token's API capabilities.

    Returns tables that are either:
    - Not in TABLE_TO_API_MAP (base data, always available)
    - In TABLE_TO_API_MAP with API available or unknown

    This should be used for:
    - check_data_exists() completeness check
    - get_bulk_sync_quality_scores() quality evaluation
    """
    from data.external.tushare_client import TushareClient

    all_tables = _get_default_synced_tables()
    return TushareClient().get_effective_synced_tables(all_tables)


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
            return datetime.datetime.strptime(val, "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
        except ValueError:
            return val
    return val


class QuoteDao(BaseDao):
    # --- Daily Quotes ---
    async def save_daily_quotes(
        self,
        df: pd.DataFrame,
        priority: int | None = None,
        suppress_errors: bool = False,
    ):
        cols = get_model_columns(DailyQuotes)
        pk_columns = get_model_pk_columns(DailyQuotes)
        return await self._save_upsert(
            df,
            "daily_quotes",
            cols,
            pk_columns=pk_columns,
            suppress_errors=suppress_errors,
        )

    async def check_data_exists(
        self, trade_date: datetime.date | str, tables: list | None = None, raise_on_error: bool = False
    ) -> bool:
        """
        Check if data exists for all synced tables on a given trade_date.
        This is used for reliable breakpoint resume - only skip a date if ALL
        synced tables have data.

        review09-24 dim05 MAJOR-02：原实现以"每张表当天至少 1 行"当作"已同步"，任何截断
        写入（如 daily_quotes 只落 200/5400 行后连接中断）都会通过检查，使该日被日常更新 /
        定时补偿永久跳过。现按行数判定：
        - 稠密表（``_DENSITY_CHECKED_TABLES``，行情 / 指标 / 资金流 / 涨跌停价 / 指数）：
          当日行数须 >= ``max(1, _expected_count_for_table(...))``（容忍系数与质量评分同源）；
        - 其余表（两融 / 北向等结构性覆盖子集、低频事件表）：保留存在性判定（>= 1 行）。
        理论股票数无法确定（非交易日 / stock_basic 为空 / 查询失败）时保守返回 False，
        不把"无法核对"当作"已同步"。

        :param trade_date: The trade date to check
        :param tables: List of table names to check. If None, uses tables from HistoricalSyncStrategy.SYNCED_TABLES.
        :param raise_on_error: If True, raise on DB errors instead of returning False.
            Use True for critical paths where "query failed" must not be confused with "data missing".
        :return: True if all tables have data for the given date
        """
        from utils.config_handler import ConfigHandler

        if trade_date is None:
            logger.warning("[QuoteDao] check_data_exists called with None trade_date")
            return False

        trade_date = _normalize_trade_date(trade_date)

        if tables is None:
            tables = _get_effective_synced_tables()

        allowed_tables = set(_get_default_synced_tables())
        safe_tables = []
        for table in tables:
            if table not in allowed_tables or not _is_safe_identifier(table):
                logger.warning("[QuoteDao] Invalid table name rejected: %s", table)
                return False
            safe_tables.append(table)

        if not safe_tables:
            logger.warning("[QuoteDao] check_data_exists: safe_tables is empty (all tables filtered out)")
            return False

        metadata = Base.metadata
        union_parts = []
        for t in safe_tables:
            tbl = metadata.tables.get(t)
            if tbl is None or "trade_date" not in tbl.c:
                logger.warning("[QuoteDao] Table '%s' not in metadata or missing trade_date column", t)
                return False
            if t in _DENSITY_CHECKED_TABLES:
                # review09-24 dim05 MAJOR-02：稠密表按当日实际行数判定（比较见下方），
                # 不再以"至少 1 行"当作已同步。
                part = (
                    sa.select(
                        sa.literal(t).label("tbl"),
                        sa.func.count().label("cnt"),
                    )
                    .select_from(tbl)
                    .where(tbl.c.trade_date == trade_date)
                )
            else:
                # 结构性覆盖子集表 / 低频事件表：保留存在性判定（至少 1 行），
                # 避免用全市场理论股票数推导出的最低行数持续误判。
                part = (
                    sa.select(
                        sa.literal(t).label("tbl"),
                        sa.literal(1).label("cnt"),
                    )
                    .select_from(tbl)
                    .where(tbl.c.trade_date == trade_date)
                    .limit(1)
                )
            union_parts.append(part)
        if len(union_parts) == 1:
            stmt = union_parts[0]
        else:
            stmt = sa.union_all(*union_parts)
        try:
            df = await self._read_db_select(stmt, suppress_errors=not raise_on_error)
            if df is None or df.empty:
                return False

            expected_base = 0
            tolerance_map: dict[str, float] = {}
            if any(t in _DENSITY_CHECKED_TABLES for t in safe_tables):
                expected_base = await self.get_expected_stock_count(trade_date)
                if expected_base <= 0:
                    # 无法确定理论股票数（非交易日 / stock_basic 为空 / 查询失败）：保守判为
                    # 未完整，不把"无法核对"当作"已同步"而跳过整日（R21 精神）。
                    logger.warning(
                        "[QuoteDao] check_data_exists: 无法确定 %s 的理论股票数，按未完整处理",
                        trade_date,
                    )
                    return False
                tolerance_map = _build_table_tolerance_map(ConfigHandler.get_sync_integrity_config())

            counts = {str(tbl_name): int(cnt) for tbl_name, cnt in zip(df["tbl"], df["cnt"], strict=False)}
            shortfalls = []
            for t in safe_tables:
                count = counts.get(t, 0)
                if t in _DENSITY_CHECKED_TABLES:
                    required = max(1, _expected_count_for_table(t, expected_base, tolerance_map))
                    if count < required:
                        shortfalls.append(f"{t}={count}/{required}")
                elif count < 1:
                    shortfalls.append(f"{t}=missing")
            if shortfalls:
                logger.debug("[QuoteDao] check_data_exists: %s 未完整同步（%s）", trade_date, ", ".join(shortfalls))
                return False
            return True
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as exc:
            if raise_on_error:
                raise
            logger.debug("[QuoteDao] Table existence check failed: %s", exc)
            return False

    async def get_expected_stock_count(self, trade_date: datetime.date | str) -> int:
        """
        计算指定日期的理论存活股票数。

        使用 delist_date 精确排除历史某天已退市的股票。
        同时验证该日期是否为交易日。

        Note:
            存活判定经 stock_alive_condition() 唯一正本渲染（DAT-01），
            禁止内联复制 WHERE 条件。语义：
            - list_status='L' 且 delist_date 为空或大于当前日期 → 存活
            - list_status='D' 且 delist_date 非空且大于当前日期 → 存活（退市后仍有数据）

        Args:
            trade_date: 交易日期

        Returns:
            该日理论上应该有行情数据的股票数量
        """
        try:
            # DAT-01: __STOCK_ALIVE_CONDITION__ 由 stock_alive_condition() 唯一正本渲染，
            # 与 screener_dao 模板替换模式一致（review03-C7：避免 f-string 拼 SQL）。
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
                      AND __STOCK_ALIVE_CONDITION__
                )
                SELECT
                    COALESCE((SELECT is_trade_day FROM trade_day_check), 0) as is_trade_day,
                    (SELECT cnt FROM stock_counts) as cnt
                """.replace(
                    "__STOCK_ALIVE_CONDITION__",
                    stock_alive_condition(alias="", as_of="$1"),
                ),
                (self._to_db_date(trade_date),),
            )

            if df is not None and not df.empty:
                is_trade_day = int(df["is_trade_day"].iloc[0])
                if is_trade_day == 0:
                    logger.debug("[QuoteDao] %s is not a trading day", trade_date)
                    return 0
                return int(df["cnt"].iloc[0])
            return 0
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[QuoteDao] Failed to get expected stock count for %s: %s", trade_date, safe_error(e))
            return 0

    async def get_daily_quotes(
        self,
        ts_code: str | None = None,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        ts_code_list: list | None = None,
        suppress_errors: bool = True,
    ):
        """查询日线行情，返回按 (ts_code, trade_date) 升序的行序（DAO 契约，D3-M3）。

        行序是契约：下游普遍依赖 (ts_code, trade_date) 有序做滚动窗口 / shift / iloc[-1]。
        两条返回路径必须一致：
        - 直查分支（不传 ts_code_list）：本方法在 WHERE 条件拼完后追加
          ``ORDER BY ts_code, trade_date``（见下方直查分支，与 get_index_daily_range 先例一致）；
        - ts_code_list 分块分支：chunked_in_query 各块内 SQL 虽逐块排序，但
          ``pd.concat(ignore_index=True)`` 不保证跨块全局有序，故须在 concat 后
          由方法内 ``sort_values(["ts_code", "trade_date"])`` 统一排序。
        """
        sql = "SELECT ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor FROM daily_quotes WHERE 1=1"
        params = []
        idx = 1

        if ts_code:
            sql += f" AND ts_code = ${idx}"
            params.append(ts_code)
            idx += 1
        sd = self._to_db_date(start_date) if start_date else None
        if sd:
            sql += f" AND trade_date >= ${idx}"
            params.append(sd)
            idx += 1
        ed = self._to_db_date(end_date) if end_date else None
        if ed:
            sql += f" AND trade_date <= ${idx}"
            params.append(ed)
            idx += 1

        if ts_code_list:
            sql_template = sql + " AND ts_code IN ({placeholders})"
            df = await self.chunked_in_query(
                self._read_db,
                sql_template,
                ts_code_list,
                extra_params=params,
                suppress_errors=suppress_errors,
            )
            if not df.empty:
                sort_cols = [c for c in ["ts_code", "trade_date"] if c in df.columns]
                if sort_cols:
                    df = df.sort_values(sort_cols, ignore_index=True)
            return attach_daily_quotes_column_units(df)

        # D3-M3: 直查分支补 ORDER BY，保证与 ts_code_list 分支（concat 后 sort_values）
        # 返回一致的行序契约。仅在此处追加，避免破坏上方 chunked 分支的
        # "AND ts_code IN ({placeholders})" 拼接（ORDER BY 需在全部 WHERE 之后）。
        sql += " ORDER BY ts_code, trade_date"
        df = await self._read_db(sql, params, suppress_errors=suppress_errors)
        return attach_daily_quotes_column_units(df)

    async def get_latest_quotes_bulk(self, ts_codes: list[str]) -> pd.DataFrame:
        """批量取多只股票各自最新交易日的行情（DISTINCT ON，规避 N+1，UX-09 MAJOR-04）。

        返回列：ts_code, trade_date, close, pct_chg, vol, amount。无行情记录的代码
        不出现在结果中（缺失由调用方以 None 表达，R21 不伪造）。chunked_in_query
        按块切分 ts_code，块间代码互斥，故块内 DISTINCT ON 的「每码最新一行」
        语义在全量结果上仍成立。
        """
        if not ts_codes:
            return pd.DataFrame(columns=["ts_code", "trade_date", "close", "pct_chg", "vol", "amount"])
        sql_template = (
            "SELECT DISTINCT ON (ts_code) ts_code, trade_date, close, pct_chg, vol, amount "
            "FROM daily_quotes WHERE ts_code IN ({placeholders}) "
            "ORDER BY ts_code, trade_date DESC"
        )
        df = await self.chunked_in_query(self._read_db, sql_template, list(ts_codes))
        # attach_daily_quotes_column_units 无类型注解（None 透传）；df 恒非 None，结果即 DataFrame
        return typing.cast(pd.DataFrame, attach_daily_quotes_column_units(df))

    async def get_recent_quotes(self, ts_code: str, days: int = 365) -> pd.DataFrame:
        """取该股最近 ``days`` 个交易日的行情（按 trade_date 升序），供按代码详情 K 线。

        与 ``DataProcessor.get_stock_history`` 同表同口径（daily_quotes 原始行情，
        含 adj_factor），但不依赖 trade_calendar / TushareClient 构造，使详情对话框
        可由任意页面按 ts_code 直接打开（UX-09 MAJOR-04）。无记录时返回空表。
        """
        stmt = (
            sa.select(DailyQuotes)
            .where(DailyQuotes.ts_code == ts_code)
            .order_by(DailyQuotes.trade_date.desc())
            .limit(int(days))
        )
        df = await self._read_db_select(stmt, suppress_errors=False)
        if df is None or df.empty:
            return pd.DataFrame()
        # 同上：df 已排除 None，attach 仅补列单位元数据，返回恒为 DataFrame
        return typing.cast(
            pd.DataFrame, attach_daily_quotes_column_units(df.sort_values("trade_date", ignore_index=True))
        )

    async def get_latest_trade_date(self):
        df = await self._read_db("SELECT MAX(trade_date) as max_td FROM daily_quotes")
        if df is not None and not df.empty:
            return df["max_td"].iloc[0]
        return None

    async def get_cached_trade_dates(self) -> set[datetime.date]:
        df = await self._read_db(
            "SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date",
        )
        if df is None or df.empty:
            return set()
        return set(df["trade_date"])

    async def get_cached_dates_for_table(self, table_name: str) -> set:
        """
        Get distinct dates from a table for breakpoint resume check.
        Supports tables registered in _TABLE_DATE_COLUMN_MAP.
        """
        if table_name not in _TABLE_DATE_COLUMN_MAP:
            logger.warning("[QuoteDao] Invalid table name rejected: %s", table_name)
            return set()

        date_col = _TABLE_DATE_COLUMN_MAP[table_name]

        if not _is_safe_identifier(table_name) or not _is_safe_identifier(date_col):
            logger.warning("[QuoteDao] Invalid identifier rejected: table=%s, col=%s", table_name, date_col)
            return set()

        try:
            tbl = Base.metadata.tables.get(table_name)
            if tbl is None or date_col not in tbl.c:
                logger.warning("[QuoteDao] Table '%s' not in metadata or missing column '%s'", table_name, date_col)
                return set()
            stmt = sa.select(sa.distinct(tbl.c[date_col])).order_by(tbl.c[date_col])
            # review03-C4: 无 WHERE/LIMIT 的全表 DISTINCT 扫描——显式 max_rows 守卫
            # （物化行数 = 去重后的日期数，防御 date_col 高基数 / 数据异常的意外放大）
            df = await self._read_db_select(stmt, max_rows=50_000)
            if df is None or df.empty:
                return set()
            return set(df[date_col])
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[QuoteDao] Failed to get cached dates for %s: %s",
                table_name,
                e,
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

    async def get_index_daily(self, ts_code: str | None = None, trade_date: datetime.date | str | None = None):
        sql = "SELECT * FROM index_daily WHERE 1=1"
        p = []
        idx = 1
        if ts_code:
            sql += f" AND ts_code=${idx}"
            p.append(ts_code)
            idx += 1
        td = self._to_db_date(trade_date) if trade_date else None
        if td:
            sql += f" AND trade_date=${idx}"
            p.append(td)
        sql += " ORDER BY trade_date DESC"
        return await self._read_db(sql, p)

    async def get_index_daily_range(
        self,
        ts_code_list: list,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
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

        # RV-02: 批量查询需含 open 列——复盘基准窗口起点为 T+1 开盘（与回测
        # next_open 对齐），index_daily.open 已存在（models.IndexDaily.open）。
        sql = "SELECT ts_code, trade_date, open, close, pct_chg, vol, amount FROM index_daily WHERE 1=1"
        params = []
        idx = 1

        sd = self._to_db_date(start_date) if start_date else None
        if sd:
            sql += f" AND trade_date >= ${idx}"
            params.append(sd)
            idx += 1
        ed = self._to_db_date(end_date) if end_date else None
        if ed:
            sql += f" AND trade_date <= ${idx}"
            params.append(ed)
            idx += 1

        if ts_code_list:
            sql_template = sql + " AND ts_code IN ({placeholders})"
            df = await self.chunked_in_query(
                self._read_db,
                sql_template,
                ts_code_list,
                extra_params=params,
            )
            if not df.empty:
                sort_cols = [c for c in ["ts_code", "trade_date"] if c in df.columns]
                if sort_cols:
                    df = df.sort_values(sort_cols, ignore_index=True)
            return df

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
            p.append(self._to_db_date(trade_date))
        return await self._read_db(sql, p)

    async def get_block_trade_range(self, start_date: str, end_date: str):
        sql = "SELECT * FROM block_trade WHERE trade_date >= $1 AND trade_date <= $2"
        return await self._read_db(sql, [self._to_db_date(start_date), self._to_db_date(end_date)])

    # --- Limit List ---
    async def save_limit_list(self, df: pd.DataFrame):
        # R17: Tushare API 字段名为 "limit"（SQL 保留字），数据库列名映射为 "limit_type"。
        # _save_upsert 按 get_model_columns 返回的数据库列名从 df 取列，需在写入前重命名。
        if df is not None and not df.empty and "limit" in df.columns and "limit_type" not in df.columns:
            df = df.rename(columns={"limit": "limit_type"})
        cols = get_model_columns(LimitList)
        pk_columns = get_model_pk_columns(LimitList)
        return await self._save_upsert(
            df,
            "limit_list",
            cols,
            pk_columns=pk_columns,
        )

    async def get_limit_list(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        trade_date: str | None = None,
    ):
        """
        获取涨跌停股票列表。

        Args:
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
            trade_date: 单日日期 (YYYYMMDD)，优先于 start_date/end_date

        Returns:
            DataFrame 包含 ts_code, trade_date, limit_type ('U'=涨停, 'D'=跌停) 等字段。
            注：数据库列名为 limit_type（R17: limit 是 SQL 保留字），Tushare API 原始字段名为 limit。
        """
        if trade_date:
            sql = "SELECT ts_code, trade_date, limit_type, name, close, pct_chg FROM limit_list WHERE trade_date=$1"
            return await self._read_db(sql, [self._to_db_date(trade_date)])

        sql = "SELECT ts_code, trade_date, limit_type, name, close, pct_chg FROM limit_list WHERE 1=1"
        p = []
        idx = 1
        if start_date:
            sql += f" AND trade_date>=${idx}"
            p.append(self._to_db_date(start_date))
            idx += 1
        if end_date:
            sql += f" AND trade_date<=${idx}"
            p.append(self._to_db_date(end_date))
            idx += 1
        sql += " ORDER BY trade_date, ts_code"
        return await self._read_db(sql, p)

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
            p.append(self._to_db_date(trade_date))
        df = await self._read_db(sql, p)
        return attach_top_list_column_units(df)

    async def get_top_list_range(self, start_date: str, end_date: str):
        sql = "SELECT * FROM top_list WHERE trade_date >= $1 AND trade_date <= $2"
        df = await self._read_db(sql, [self._to_db_date(start_date), self._to_db_date(end_date)])
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

    async def get_suspend_d(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        trade_date: str | None = None,
    ):
        """
        获取停牌股票列表。

        Args:
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)
            trade_date: 单日日期 (YYYYMMDD)，优先于 start_date/end_date

        Returns:
            DataFrame 包含 ts_code, trade_date, suspend_timing, suspend_type 字段
        """
        if trade_date:
            sql = "SELECT ts_code, trade_date, suspend_timing, suspend_type FROM suspend_d WHERE trade_date=$1"
            return await self._read_db(sql, [self._to_db_date(trade_date)])

        sql = "SELECT ts_code, trade_date, suspend_timing, suspend_type FROM suspend_d WHERE 1=1"
        p = []
        idx = 1
        if start_date:
            sql += f" AND trade_date>=${idx}"
            p.append(self._to_db_date(start_date))
            idx += 1
        if end_date:
            sql += f" AND trade_date<=${idx}"
            p.append(self._to_db_date(end_date))
            idx += 1
        sql += " ORDER BY trade_date, ts_code"
        return await self._read_db(sql, p)

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
            p.append(self._to_db_date(trade_date))
            idx += 1
        if ts_code:
            sql += f" AND ts_code=${idx}"
            p.append(ts_code)
            idx += 1
        return await self._read_db(sql, p)

    async def get_moneyflow_range(self, start_date: str, end_date: str):
        sql = "SELECT * FROM moneyflow_daily WHERE trade_date >= $1 AND trade_date <= $2"
        return await self._read_db(sql, [self._to_db_date(start_date), self._to_db_date(end_date)])

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
            p.append(self._to_db_date(trade_date))
            idx += 1
        if ts_code:
            sql += f" AND ts_code=${idx}"
            p.append(ts_code)
            idx += 1
        return await self._read_db(sql, p)

    async def get_northbound_range(self, start_date: str, end_date: str):
        sql = "SELECT * FROM northbound_holding WHERE trade_date >= $1 AND trade_date <= $2"
        return await self._read_db(sql, [self._to_db_date(start_date), self._to_db_date(end_date)])

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
            logger.warning("[QuoteDao] Invalid table name rejected: %s", table_name)
            return {}

        try:
            tbl = Base.metadata.tables.get(table_name)
            if tbl is None or "trade_date" not in tbl.c:
                logger.warning("[QuoteDao] Table '%s' not in metadata or missing trade_date column", table_name)
                return {}
            stmt = (
                sa.select(tbl.c.trade_date, sa.func.count().label("cnt"))
                .select_from(tbl)
                .where(tbl.c.trade_date.between(start_date, end_date))
                .group_by(tbl.c.trade_date)
            )
            df = await self._read_db_select(stmt)

            if df is None or df.empty:
                return {}

            normalized_results = {}
            for trade_date, count in zip(df["trade_date"], df["cnt"], strict=False):
                normalized_results[_normalize_trade_date(trade_date)] = count

            return normalized_results
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[QuoteDao] Failed to get bulk counts for %s: %s", table_name, safe_error(e))
            return {}

    async def get_bulk_table_counts_multi(
        self,
        tables: list[str],
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict[str, dict[datetime.date, int]]:
        """
        批量获取多张表在指定时间范围内每天的记录数（单次 UNION ALL 查询）。

        替代 get_bulk_sync_quality_scores 中对每张表逐个调用 get_bulk_table_counts 的
        N+1 模式（T 张表 → 1 次查询），语义与逐表调用等价：
        - 非法表名 / 元数据缺失 / 无 trade_date 列 → 该表返回空 dict（不中断其余表）；
        - 无数据同样以 {table: {}} 呈现，调用方按 0 处理。

        失败隔离：union 查询整体失败时降级为逐表调用 get_bulk_table_counts（错误路径付 N 次），
        避免"单次查询失败导致全表归零 → daily_quotes 一票否决 → 历史全量重同步"的放大效应。

        Args:
            tables: 表名列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            {table_name: {日期: 记录数}}
        """
        result: dict[str, dict[datetime.date, int]] = {t: {} for t in tables}
        if not tables:
            return result

        allowed_tables = set(_get_default_synced_tables())
        metadata = Base.metadata
        union_parts = []
        queried_tables: list[str] = []
        for table_name in tables:
            if table_name not in allowed_tables or not _is_safe_identifier(table_name):
                logger.warning("[QuoteDao] Invalid table name rejected: %s", table_name)
                continue
            tbl = metadata.tables.get(table_name)
            if tbl is None or "trade_date" not in tbl.c:
                logger.warning("[QuoteDao] Table '%s' not in metadata or missing trade_date column", table_name)
                continue
            part = (
                sa.select(
                    sa.literal(table_name).label("tbl"),
                    tbl.c.trade_date.label("trade_date"),
                    sa.func.count().label("cnt"),
                )
                .select_from(tbl)
                .where(tbl.c.trade_date.between(start_date, end_date))
                .group_by(tbl.c.trade_date)
            )
            union_parts.append(part)
            queried_tables.append(table_name)

        if not union_parts:
            return result

        stmt = union_parts[0] if len(union_parts) == 1 else sa.union_all(*union_parts)
        try:
            # suppress_errors=False：默认吞错会返回空 DataFrame，会把"查询失败"伪装成
            # "全表无数据"，使下方逐表降级不可达，并把单表失败放大为全表归零
            # （daily_quotes 一票否决 → 历史全量重同步）。必须显式抛出以进入降级路径。
            df = await self._read_db_select(stmt, suppress_errors=False)
            if df is None or df.empty:
                return result
            for tbl_name, trade_date, count in zip(df["tbl"], df["trade_date"], df["cnt"], strict=False):
                if tbl_name in result:
                    result[tbl_name][_normalize_trade_date(trade_date)] = count
            return result
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[QuoteDao] Failed to get bulk counts (multi): %s; falling back to per-table", safe_error(e))

        # 降级：逐表查询，保留原有的失败隔离（单表失败只影响该表，不放大为全表归零）。
        for table_name in queried_tables:
            try:
                result[table_name] = await self.get_bulk_table_counts(table_name, start_date, end_date)
            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                raise
            except Exception as e:
                logger.warning("[QuoteDao] Fallback bulk counts failed for %s: %s", table_name, safe_error(e))
                result[table_name] = {}
        return result

    async def get_bulk_expected_stock_counts(
        self,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict[datetime.date, int]:
        """
        批量获取指定时间范围内每天的理论存活股票数。

        使用 delist_date 精确排除历史退市股票。

        Note:
            存活判定经 stock_alive_condition() 唯一正本渲染（DAT-01），
            禁止内联复制 WHERE 条件。语义：
            - list_status='L' 且 delist_date 为空或大于当前日期 → 存活
            - list_status='D' 且 delist_date 非空且大于当前日期 → 存活（退市后仍有数据）

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            dict[日期, 理论存活股票数]
        """
        try:
            # DAT-01: __STOCK_ALIVE_CONDITION__ 由 stock_alive_condition() 唯一正本渲染，
            # 与 screener_dao 模板替换模式一致（review03-C7：避免 f-string 拼 SQL）。
            df = await self._read_db(
                """
                WITH trading_days AS (
                    SELECT cal_date AS trade_date
                    FROM trade_cal
                    WHERE cal_date BETWEEN $1 AND $2
                      AND is_open = 1
                      AND exchange = 'SSE'
                ),
                alive_ranges AS (
                    SELECT
                        list_date AS start_date,
                        COALESCE(delist_date, '2099-12-31'::date) AS end_date
                    FROM stock_basic
                    WHERE list_date IS NOT NULL
                      AND list_date <= $2
                      AND __STOCK_ALIVE_CONDITION__
                )
                SELECT t.trade_date, COUNT(a.start_date) as expected_count
                FROM trading_days t
                LEFT JOIN alive_ranges a ON a.start_date <= t.trade_date AND a.end_date > t.trade_date
                GROUP BY t.trade_date
                ORDER BY t.trade_date
                """.replace(
                    "__STOCK_ALIVE_CONDITION__",
                    stock_alive_condition(alias="", as_of="$1"),
                ),
                (self._to_db_date(start_date), self._to_db_date(end_date)),
            )

            if df is None or df.empty:
                logger.warning("[QuoteDao] No trading days found for range %s to %s", start_date, end_date)
                return {}

            normalized_results = {}
            for trade_date, count in zip(df["trade_date"], df["expected_count"], strict=False):
                normalized_results[_normalize_trade_date(trade_date)] = count

            return normalized_results
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[QuoteDao] Failed to get bulk expected counts: %s", safe_error(e))
            return {}

    async def get_stock_basic_latest_updated_date(self) -> datetime.date | None:
        """stock_basic 最近一次写入日期（质量评分新鲜度判据，review09-24 dim05 MAJOR-04）。

        理论股票数（``get_bulk_expected_stock_counts``）完全派生自 stock_basic；该表陈旧会
        使分母偏小、``daily_quotes`` 覆盖率被高估，故质量评分前需据此判断分母可信度。

        语义：``_save_upsert`` 在 ON CONFLICT 时写 ``updated_at = now()``（见 base_dao），
        故 ``MAX(updated_at)`` 可代表"最近一次成功写入 stock_basic 的时间"；空返回时
        ``save_stock_basic`` 提前 return 不刷新，不会被误判为新鲜。

        局限：该判据只反映"最近一次写入时刻"，无法发现"部分写入导致大量行仍缺失"
        （此时分母偏小、评分虚高而本判据仍显示新鲜）；内容级完整性需另设行数/覆盖判据。

        Returns:
            最近写入日期；无法判定（表为空 / 全为 NULL / 查询失败）时返回 ``None``——
            调用方必须按"未知"处理，不得当作"新鲜"（R21 精神：未知不伪装为正常）。
        """
        try:
            df = await self._read_db("SELECT MAX(updated_at) AS latest_updated FROM stock_basic")
            if df is None or df.empty:
                return None
            value = df["latest_updated"].iloc[0]
            if value is None or pd.isna(value):
                return None
            if isinstance(value, datetime.datetime):
                return value.date()
            if isinstance(value, datetime.date):
                return value
            return None
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug("[QuoteDao] stock_basic freshness query failed: %s", safe_error(e))
            return None

    async def _get_empty_days_map(
        self,
        tables: list | None,
        start_date: datetime.date | str,
        end_date: datetime.date | str,
    ) -> dict[str, set[datetime.date]]:
        """读取窗口内各表"已核实合法为空"的交易日集合（review09-24 dim05 MAJOR-03）。

        数据来源 ``sync_empty_days``（由同步侧仅对成功 fetch 且为空的稀疏表登记）。
        查询失败/表缺失时返回空字典——调用方据此**不做豁免**（无登记即按真实缺口处理），
        符合 R21 精神：未知不得伪装成"合法为空"。

        Args:
            tables: 待查询的表名列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            {table_name: {trade_date, ...}}；无登记/查询失败时为空字典。
        """
        if not tables:
            return {}
        tbl = Base.metadata.tables.get("sync_empty_days")
        if tbl is None:
            logger.warning("[QuoteDao] Table 'sync_empty_days' not in metadata, skip empty-day exemption")
            return {}
        try:
            stmt = sa.select(tbl.c.table_name, tbl.c.trade_date).where(
                tbl.c.table_name.in_(list(tables)),
                tbl.c.trade_date.between(start_date, end_date),
            )
            df = await self._read_db_select(stmt)
            if df is None or df.empty:
                return {}
            empty_days_map: dict[str, set[datetime.date]] = {}
            for table_name, trade_date in zip(df["table_name"], df["trade_date"], strict=False):
                empty_days_map.setdefault(table_name, set()).add(_normalize_trade_date(trade_date))
            return empty_days_map
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[QuoteDao] Failed to load sync_empty_days map: %s", safe_error(e))
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
            tables = _get_effective_synced_tables()

        config = ConfigHandler.get_sync_integrity_config()

        expected_bases = await self.get_bulk_expected_stock_counts(start_date, end_date)

        if not expected_bases:
            logger.warning("[QuoteDao] Cannot determine expected bases for quality check")
            return {}

        # MAJOR-04：stock_basic 新鲜度决定 expected_base（分母）是否可信，全批只查一次。
        stock_basic_updated = await self.get_stock_basic_latest_updated_date()

        # MINOR-01：改为单次 UNION ALL 批量查询，替代对每张表逐个 get_bulk_table_counts 的 N+1。
        table_counts = await self.get_bulk_table_counts_multi(list(tables), start_date, end_date)

        # MAJOR-03：一次性读取窗口内"已核实合法为空"登记，供稀疏表豁免判据使用。
        # 替代原单点高水位（sync_attempted_upto）——后者无法表达"区间内某些日为空、
        # 某些日为真实缺口"，会把水位之前的真实缺口一并豁免。
        empty_days_map = await self._get_empty_days_map(tables, start_date, end_date)

        table_tolerance_map = _build_table_tolerance_map(config)

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
            elif quotes_count > expected_base:
                # MAJOR-04：行数超过理论数说明 stock_basic 漏了当日实际有行情的标的
                # （正常情形下停牌股无行情，count 应小于 expected_base）；被 min(1.0, ...)
                # 截顶后看不出超额，故显式告警。
                result["issues"].append(
                    f"daily_quotes 行数({quotes_count})超过理论股票数({expected_base})，stock_basic 可能陈旧"
                )

            # MAJOR-04：stock_basic 陈旧会使理论股票数偏小、覆盖率被高估（评分虚高）。
            # 仅标注告警、不调低 score——stock_basic 不在 HistoricalSyncStrategy.SYNCED_TABLES，
            # 降分只会驱使历史同步反复重抓同一些日期却无法修复分母（成本回归且不自愈）。
            if isinstance(trade_date, datetime.datetime):
                plain_trade_date: datetime.date | None = trade_date.date()
            elif isinstance(trade_date, datetime.date):
                plain_trade_date = trade_date
            else:
                plain_trade_date = None
            if stock_basic_updated is None:
                result["issues"].append("无法确认 stock_basic 新鲜度（查询失败或表为空），理论股票数可能不准")
            elif (
                plain_trade_date is not None
                and (plain_trade_date - stock_basic_updated).days > _STOCK_BASIC_STALENESS_MAX_DAYS
            ):
                result["issues"].append(
                    f"stock_basic 最近更新 {stock_basic_updated}，早于交易日 {plain_trade_date} 超过 "
                    f"{_STOCK_BASIC_STALENESS_MAX_DAYS} 天，理论股票数可能偏低"
                )

            # review09-24 dim05 CRITICAL-01：daily_quotes 是否达标是一票否决信号。
            # 整批残缺（限流/超时/分页中断导致的整批截断）时，其余表的实际行数会与
            # daily_quotes 同步缩水；若以缩水后的 quotes_count 作参照（自我参照分母），
            # 评分退化为"各表彼此是否一致"而非"是否完整"，加权平均无法把总分压到阈值下，
            # 残缺日会被断点续传永久跳过。故 daily_quotes 不达标时最终得分强制封顶到阈值以下。
            # 仅当本次评估确实包含 daily_quotes 时否决，避免调用方显式排除该表（只评部分表）时误否决。
            quotes_veto = "daily_quotes" in tables and not quotes_passed

            # Low-frequency exemption must be controlled by explicit table allowlist,
            # not by tolerance values, to avoid accidental score inflation after config changes.
            low_frequency_tables = {t for t in LOW_FREQUENCY_TABLES if t in tables}

            for table in tables:
                if table == "daily_quotes":
                    continue

                count = table_counts.get(table, {}).get(trade_date, 0)

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

                # MAJOR-03：已核实合法为空豁免——未进低频白名单、非 dense 的稀疏表，
                # 某日 count == 0 且该 (表, 日) 已在 sync_empty_days 显式登记
                # → 判为"已尝试合法为空"，不计入加权评分，避免 dense 表已完整仍因
                # 个别稀疏空表反复触发 re-sync。单点水位改为按 (表, 日) 精确登记后，
                # 未登记的日期（含水位之前的真实缺口）不再被误豁免。
                if count == 0 and table not in _DENSE_TABLES and trade_date in empty_days_map.get(table, set()):
                    result["tables"][table] = {
                        "count": 0,
                        "expected": 0,
                        "ratio": None,
                        "passed": True,
                        "exempt": True,
                        "note": "已核实合法为空，不计入评分",
                    }
                    continue

                # review09-24 dim05 MAJOR-02：期望行数计算统一收敛到 _expected_count_for_table()
                # （DS-01 的 index_daily 动态派生 / FIXED_EXPECTED_TABLES / CRITICAL-01 的
                # expected_base 参照），与 check_data_exists 的稠密性判定共用同一正本，
                # 避免两处口径漂移。
                expected = _expected_count_for_table(table, expected_base, table_tolerance_map)

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

            if quotes_veto:
                # 封顶到阈值以下，使缓存判分路径（historical.py 的 score < quality_threshold）
                # 必然重同步；max(0, ...) 防止 quality_threshold=0 时产生负分。
                result["score"] = min(result["score"], max(0, config.get("quality_threshold", 80) - 1))

            results[trade_date] = result

        field_completeness = {}
        try:
            sorted_dates = [d for d in results if results[d].get("expected_base", 0) > 0]
            if sorted_dates:
                latest_date = max(sorted_dates)
                field_completeness = await self.get_field_completeness(latest_date)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug("[QuoteDao] Field completeness check skipped: %s", e)

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

        Note: 本查询统计「当前在市股票」的字段覆盖率（list_status='L'），
        非 PIT 存活判定（见 stock_alive_condition），禁止用历史日期作时点选股池。
        """
        field_sql = """
            SELECT
                COUNT(*) AS total,
                COUNT(indicator_date) AS indicators_available,
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
                                  ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC, ann_date DESC) AS rn  -- DAT-03: 最新一期财报口径 end_date DESC, ann_date DESC
                           FROM financial_reports WHERE ann_date IS NOT NULL AND ann_date <= $2) f
                          ON b.ts_code = f.ts_code AND f.rn = 1
                WHERE b.list_status = 'L'
            ) sub
        """
        try:
            df_fields = await self._read_db(field_sql, (self._to_db_date(trade_date), self._to_db_date(trade_date)))
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
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug("[QuoteDao] get_field_completeness failed for %s: %s", trade_date, e)
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
            normalized = datetime.datetime.strptime(trade_date, "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
        else:
            normalized = trade_date

        results = await self.get_bulk_sync_quality_scores(normalized, normalized)
        return results.get(
            normalized,
            {"score": 0, "expected_base": 0, "tables": {}, "issues": ["查询失败"]},
        )

    # --- DAT-12: 跨表/跨字段一致性校验（生产健康检查，外键替代） ---
    # 近期窗口限制扫描范围，避免对全表（千万级）做代价过高的聚合。
    _CROSS_VALIDATION_WINDOW_DAYS = 120
    # moneyflow_daily net_mf_amount 与「Σ买入 − Σ卖出」的容差（万元），容忍分项舍入差异。
    _MONEYFLOW_NET_TOLERANCE = 1.0

    async def _count_in_window(self, sql: str, params: list) -> int:
        """DAT-12: 执行带 $N 占位符的 COUNT 查询，返回首个 cnt 值（查询失败/空结果返回 0）。"""
        df = await self._read_db(sql, params)
        if df is None or df.empty:
            return 0
        return int(df["cnt"].iloc[0])

    async def count_orphan_ts_codes(self, window_days: int = _CROSS_VALIDATION_WINDOW_DAYS) -> int:
        """DAT-12: daily_quotes 中 stock_basic 不存在的 ts_code 数（外键替代，捕获脏股票代码）。

        NOT EXISTS 等价集合差；stock_basic.ts_code 为主键非 NULL，无 NULL 语义陷阱。
        """
        cutoff = self._to_db_date(get_now().date() - datetime.timedelta(days=window_days))
        return await self._count_in_window(
            """
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT DISTINCT q.ts_code
                FROM daily_quotes q
                WHERE q.trade_date >= $1
                  AND NOT EXISTS (SELECT 1 FROM stock_basic b WHERE b.ts_code = q.ts_code)
            ) orphan_codes
            """,
            [cutoff],
        )

    async def count_price_range_violations(self, window_days: int = _CROSS_VALIDATION_WINDOW_DAYS) -> int:
        """DAT-12: 行情脏数据——high < low 或 close 越界 [low, high] 的行数（近期窗口）。"""
        cutoff = self._to_db_date(get_now().date() - datetime.timedelta(days=window_days))
        return await self._count_in_window(
            """
            SELECT COUNT(*) AS cnt
            FROM daily_quotes
            WHERE trade_date >= $1
              AND (high < low OR close > high OR close < low)
            """,
            [cutoff],
        )

    async def count_moneyflow_net_mismatch(self, window_days: int = _CROSS_VALIDATION_WINDOW_DAYS) -> int:
        """DAT-12: moneyflow_daily 净流入额与「Σ买入 − Σ卖出」不符的行数（近期窗口）。

        net_mf_amount = Σbuy − Σsell；容差 _MONEYFLOW_NET_TOLERANCE（万元）容忍分项舍入。
        """
        cutoff = self._to_db_date(get_now().date() - datetime.timedelta(days=window_days))
        return await self._count_in_window(
            """
            SELECT COUNT(*) AS cnt
            FROM moneyflow_daily
            WHERE trade_date >= $1
              AND ABS(net_mf_amount - (
                  (buy_sm_amount + buy_md_amount + buy_lg_amount + buy_elg_amount)
                  - (sell_sm_amount + sell_md_amount + sell_lg_amount + sell_elg_amount)
              )) > $2
            """,
            [cutoff, self._MONEYFLOW_NET_TOLERANCE],
        )

    async def count_adj_factor_monotonic_violations(self, window_days: int = _CROSS_VALIDATION_WINDOW_DAYS) -> int:
        """DAT-12: adj_factor 相对前一交易日下降的行数（捕获 DAT-02 的 adj_factor 静默降级 1.0）。

        窗口函数 LAG 按 (ts_code, trade_date) 排序取前值；仅统计近期窗口以控制成本。
        """
        cutoff = self._to_db_date(get_now().date() - datetime.timedelta(days=window_days))
        return await self._count_in_window(
            """
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT q.ts_code,
                       q.trade_date,
                       q.adj_factor,
                       LAG(q.adj_factor) OVER (
                           PARTITION BY q.ts_code ORDER BY q.trade_date
                       ) AS prev_adj_factor
                FROM daily_quotes q
                WHERE q.trade_date >= $1
                  AND q.adj_factor IS NOT NULL
            ) t
            WHERE t.prev_adj_factor IS NOT NULL
              AND t.adj_factor < t.prev_adj_factor
            """,
            [cutoff],
        )

    async def get_index_daily_coverage_summary(self) -> pd.DataFrame:
        """DAT-13: 各指数的日线覆盖摘要——行数 + 最新交易日。

        供 dimension 检查验证每只 MAJOR_INDICES 均有近期数据（缺失的指数不会出现在结果中）。
        """
        return await self._read_db(
            """
            SELECT i.ts_code, COUNT(*) AS row_count, MAX(i.trade_date) AS latest_trade_date
            FROM index_daily i
            GROUP BY i.ts_code
            """,
        )
