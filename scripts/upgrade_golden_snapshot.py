"""依赖升级金标准快照工具（D0.2）。

用途
----
在依赖全量升级前后，用固定的「合成冻结快照」重跑有代表性的策略与回测，
把输出固化为可逐项比对的 expected 文件，从而在升级后快速回答「计算结果是否漂移」。

三种模式（互斥，默认 --check）
-----------------------------
- ``--check``   ：从 inputs 重建全部结果并与 expected 逐项比对。
  * 全部一致（SAME）：打印 SAME，退出码 0；
  * 存在差异：逐项打印「策略名 / 差异类型 / 首个差异样本」，退出码 1。
- ``--update``  ：与 --check 走完全相同的计算路径，只是把结果覆写到 expected。
- ``--generate-inputs``：按固定随机种子确定性重建 inputs 目录（不读取系统日期）。

退出码
------
- 0：check 一致，或 update / generate-inputs 成功；
- 1：check 发现差异；
- 2：inputs 缺失、manifest 校验失败、expected 缺失或运行异常。

输入 / 输出目录
---------------
- 输入：``tests/fixtures/upgrade_golden/inputs/``
  ``universe.csv``（40 只标的，含 1~2 只 ST）、``market_snapshot.parquet``（冻结日快照）、
  ``history_quotes.parquet``（约 130 个交易日）、``trade_calendar.parquet``、``manifest.json``
  （freeze_date / universe_size / 各文件 sha256 / 生成命令 / 合成数据声明）。
- 输出：``tests/fixtures/upgrade_golden/expected/``
  ``picks_<strategy_key>.csv``（固定列 ``rank,ts_code,score,ai_score``，score 12 位小数）、
  ``backtest_nav.csv``、``backtest_metrics.json``、``summary.json``。

如何重建
--------
1. ``python scripts/upgrade_golden_snapshot.py --generate-inputs``（重建合成输入）
2. ``python scripts/upgrade_golden_snapshot.py --update``（重建 expected）
3. ``python scripts/upgrade_golden_snapshot.py --check``（校验，可连续执行多次）

离线与确定性声明
----------------
- 全程不联网、不访问数据库、不读取 Keyring、不读取系统日期；冻结交易日固定为
  ``2025-12-31``，所有输入均声明为「合成的冻结快照」，满足 R24 时点正确性
  （快照日即决策日，历史区间只使用当日及更早的数据）。
- AI 场景只挂接确定性 mock LLM（``AIService.analyze_stock``），不发起任何真实调用、不产生费用。
- 版本号 / 时间戳 / 耗时不会写入 expected，只可能出现在日志中。
- 任何失败均不吞异常（R2：``asyncio.CancelledError`` 直接传播）。
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import contextlib
import hashlib
import importlib
import json
import math
import numbers
import random
import sys
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "upgrade_golden"
INPUT_DIR = FIXTURE_DIR / "inputs"
EXPECTED_DIR = FIXTURE_DIR / "expected"

FREEZE_DATE = date(2025, 12, 31)
UNIVERSE_SIZE = 40
HISTORY_TRADING_DAYS = 130
CALENDAR_TRADING_DAYS = 150
BACKTEST_TRADING_DAYS = 20
SEED = 20251231
BENCHMARK_CODE = "000300.SH"

PICK_COLUMNS = ["rank", "ts_code", "score", "ai_score"]
NAV_COLUMNS = ["trade_date", "nav"]
SCORE_DECIMALS = 12
SCORE_ABS_TOL = 1e-9
NAV_REL_TOL = 1e-10

INPUT_FILES = (
    "universe.csv",
    "market_snapshot.parquet",
    "history_quotes.parquet",
    "trade_calendar.parquet",
)

# 按文本参与 sha256 的扩展名；其余按原始字节（parquet 内可能偶然出现 0D 0A 序列）
_TEXT_SUFFIXES = frozenset({".csv", ".json"})

_SYNTHETIC_STATEMENT = (
    "本目录全部数据均为脚本按固定随机种子合成的冻结快照（freeze_date=2025-12-31），"
    "不包含真实市场数据，也不读取系统日期/数据库/Keyring/网络。"
)


class _InputError(RuntimeError):
    """inputs 缺失、损坏或 manifest 校验失败（对应退出码 2）。"""


# ============================================================================
# 确定性合成输入
# ============================================================================
def _calendar_frame() -> tuple[pd.DataFrame, list[date]]:
    """生成交易日历：最近 150 个工作日为交易日，其余日为非交易日。"""
    days: list[date] = []
    cursor = FREEZE_DATE
    while len(days) < CALENDAR_TRADING_DAYS:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()

    open_days = set(days)
    rows: list[dict[str, Any]] = []
    current = days[0]
    while current <= days[-1]:
        rows.append(
            {
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": "1" if current in open_days else "0",
            }
        )
        current += timedelta(days=1)
    return pd.DataFrame(rows), days


def _universe_frame() -> pd.DataFrame:
    """40 只标的（20 只 .SZ + 20 只 .SH），其中 2 只为 ST（索引 3 与 8）。"""
    rows: list[dict[str, Any]] = []
    for index in range(UNIVERSE_SIZE):
        if index < 20:
            ts_code = f"{index + 1:06d}.SZ"
        else:
            ts_code = f"{600000 + index - 19:06d}.SH"
        is_st = index in (3, 8)
        name = f"{'*ST' if index == 8 else 'ST' if is_st else ''}测试股份{index + 1:03d}"
        rows.append(
            {
                "ts_code": ts_code,
                "name": name,
                "is_st": is_st,
                "list_date": "20200101",
                "industry_sw_l2": f"合成行业{(index % 6) + 1}",
            }
        )
    return pd.DataFrame(rows)


def _history_frame(universe: pd.DataFrame, trading_days: list[date]) -> pd.DataFrame:
    """确定性行情：0~9 号标的末日附近持续下跌且放量（供 RSI 超跌场景）。"""
    rng = random.Random(SEED)
    n_days = len(trading_days)
    n_stocks = len(universe)

    returns = [[rng.gauss(0.0, 0.012) for _ in range(n_stocks)] for _ in range(n_days)]
    for day in range(n_days - 15, n_days):
        for stock in range(10):
            returns[day][stock] = -0.010
    for stock in range(10, 20):
        returns[-1][stock] = 0.035
    for stock in range(20, 30):
        returns[-1][stock] = 0.055
    for stock in range(30, 40):
        returns[-1][stock] = 0.010

    turnover_base = [2.0 if i < 10 else 4.5 if i < 20 else 6.0 if i < 30 else 2.5 for i in range(n_stocks)]
    base_price = [8.0 + i * 0.9 for i in range(n_stocks)]
    base_volume = [1_000_000.0 + i * 10_000.0 for i in range(n_stocks)]

    prices: list[list[float]] = []
    for day in range(n_days):
        prev = base_price if day == 0 else prices[day - 1]
        prices.append([round(prev[i] * (1.0 + returns[day][i]), 2) for i in range(n_stocks)])

    rows: list[dict[str, Any]] = []
    for day, trade_date in enumerate(trading_days):
        prev = base_price if day == 0 else prices[day - 1]
        for stock in range(n_stocks):
            close = prices[day][stock]
            open_price = round(prev[stock] * (1.0 + returns[day][stock] * 0.3), 2)
            volume = base_volume[stock] * (
                2.5 if day == n_days - 1 and stock < 10 else 1.3 if day == n_days - 1 else 1.0
            )
            rows.append(
                {
                    "ts_code": universe.at[stock, "ts_code"],
                    "trade_date": trade_date,
                    "open": open_price,
                    "high": round(max(open_price, close) * 1.01, 2),
                    "low": round(min(open_price, close) * 0.99, 2),
                    "close": close,
                    "vol": round(volume),
                    "amount": round(close * round(volume), 2),
                    "adj_factor": 1.0,
                    "pct_chg": round((close / prev[stock] - 1.0) * 100.0, 4),
                    "turnover_rate": turnover_base[stock],
                    "is_tradable": True,
                }
            )
    return pd.DataFrame(rows)


def _snapshot_frame(universe: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """冻结日快照：行情列取历史最后一日，基本面列按标的下标确定性派生。"""
    last = history[history["trade_date"] == FREEZE_DATE].reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for index in range(len(universe)):
        item = last.iloc[index]
        rows.append(
            {
                "ts_code": universe.at[index, "ts_code"],
                "trade_date": FREEZE_DATE.strftime("%Y%m%d"),
                "name": universe.at[index, "name"],
                "industry_sw_l2": universe.at[index, "industry_sw_l2"],
                "close": float(item["close"]),
                "pct_chg": float(item["pct_chg"]),
                "turnover_rate": float(item["turnover_rate"]),
                "vol": float(item["vol"]),
                "amount": float(item["amount"]),
                "pe_ttm": round(4.0 + ((index * 3) % 18) + (0.5 if index % 2 else 0.0), 2),
                "pb": round(0.8 + (index % 8) * 0.4, 2),
                "dv_ttm": round(1.0 + (index % 10) * 0.5, 2),
                "total_mv": float((20 + index * 35) * 10000),
                "roe": round(8.0 + index * 0.3, 2),
                "or_yoy": round(5.0 + index * 0.5, 2),
                "netprofit_yoy": round(3.0 + index * 0.4, 2),
                "debt_to_assets": round(40.0 + index * 0.2, 2),
                "is_st": bool(universe.at[index, "is_st"]),
                "is_delisting": False,
                "is_tradable": True,
            }
        )
    return pd.DataFrame(rows)


def _sha256(path: Path) -> str:
    """计算文件 sha256（文本输入按 LF 归一化后计算）。

    Git 在 Windows（``core.autocrlf=true``）检出文本文件时会写入 CRLF，若直接对
    原始字节取哈希，同一提交在 Windows 与 Linux 上会得出不同结果，manifest 校验
    会把平台差异误报为「输入被改动」。二进制输入（parquet）不参与归一化，避免
    改写其字节序列。
    """
    payload = path.read_bytes()
    if path.suffix.lower() in _TEXT_SUFFIXES:
        payload = payload.replace(b"\r\n", b"\n")
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def _generate_inputs() -> None:
    """确定性重建 inputs 目录（含 manifest.json 的 sha256）。"""
    calendar, trading_days = _calendar_frame()
    history_days = trading_days[-HISTORY_TRADING_DAYS:]
    universe = _universe_frame()
    history = _history_frame(universe, history_days)
    snapshot = _snapshot_frame(universe, history)

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    universe.to_csv(INPUT_DIR / "universe.csv", index=False, lineterminator="\n")
    snapshot.to_parquet(INPUT_DIR / "market_snapshot.parquet", index=False)
    history.to_parquet(INPUT_DIR / "history_quotes.parquet", index=False)
    calendar.to_parquet(INPUT_DIR / "trade_calendar.parquet", index=False)

    manifest = {
        "freeze_date": FREEZE_DATE.isoformat(),
        "universe_size": UNIVERSE_SIZE,
        "history_trading_days": HISTORY_TRADING_DAYS,
        "sha256": {name: _sha256(INPUT_DIR / name) for name in INPUT_FILES},
        "generated_by": "python scripts/upgrade_golden_snapshot.py --generate-inputs",
        "data_source": "确定性合成（synthetic）",
        "synthetic_statement": _SYNTHETIC_STATEMENT,
    }
    _write_json(INPUT_DIR / "manifest.json", manifest)


def _load_inputs() -> dict[str, Any]:
    """读取并校验 inputs；缺失/损坏时抛 ``_InputError``（退出码 2）。"""
    for name in (*INPUT_FILES, "manifest.json"):
        if not (INPUT_DIR / name).exists():
            raise _InputError(f"缺少输入文件：{INPUT_DIR / name}")

    manifest = json.loads((INPUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("freeze_date") != FREEZE_DATE.isoformat():
        raise _InputError(f"manifest.freeze_date={manifest.get('freeze_date')!r} 与脚本冻结日不一致")
    if manifest.get("universe_size") != UNIVERSE_SIZE:
        raise _InputError(f"manifest.universe_size={manifest.get('universe_size')!r} 与脚本预期不一致")
    recorded = manifest.get("sha256")
    if not isinstance(recorded, dict):
        raise _InputError("manifest.sha256 缺失或格式非法")
    for name in INPUT_FILES:
        actual = _sha256(INPUT_DIR / name)
        if recorded.get(name) != actual:
            raise _InputError(f"{name} 的 sha256 与 manifest 不一致（输入被改动，请重新生成）")

    universe = pd.read_csv(INPUT_DIR / "universe.csv", dtype={"ts_code": str})
    snapshot = pd.read_parquet(INPUT_DIR / "market_snapshot.parquet")
    history = pd.read_parquet(INPUT_DIR / "history_quotes.parquet")
    calendar = pd.read_parquet(INPUT_DIR / "trade_calendar.parquet")
    if len(universe) != UNIVERSE_SIZE:
        raise _InputError(f"universe.csv 行数为 {len(universe)}，预期 {UNIVERSE_SIZE}")

    trading_days = sorted(_norm_date(value) for value in calendar.loc[calendar["is_open"] == "1", "cal_date"].tolist())
    if FREEZE_DATE not in trading_days:
        raise _InputError("trade_calendar.parquet 未包含冻结交易日")
    return {
        "universe": universe,
        "snapshot": snapshot,
        "history": history,
        "calendar": calendar,
        "trading_days": trading_days,
        "manifest": manifest,
    }


# ============================================================================
# 离线数据替身（只实现被调用到的方法）
# ============================================================================
def _norm_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).replace("-", "").strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"无法解析为日期：{value!r}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame()


class _StubDao:
    """未被显式实现的 DAO 方法统一返回空表（显式离线替身，不触达数据库）。"""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        async def _returns_empty(*_args: Any, **_kwargs: Any) -> pd.DataFrame:
            return _empty_frame()

        return _returns_empty


class _QuoteDao(_StubDao):
    def __init__(self, history: pd.DataFrame) -> None:
        self._history = history

    async def get_daily_quotes(
        self,
        start_date: Any = None,
        end_date: Any = None,
        ts_code_list: Any = None,
        suppress_errors: bool = False,
    ) -> pd.DataFrame:
        frame = self._history
        start = _norm_date(start_date)
        end = _norm_date(end_date)
        if start is not None:
            frame = frame[frame["trade_date"] >= start]
        if end is not None:
            frame = frame[frame["trade_date"] <= end]
        if ts_code_list:
            frame = frame[frame["ts_code"].isin(list(ts_code_list))]
        return frame.reset_index(drop=True)

    async def get_quote_trade_dates(self, start_date: Any, end_date: Any) -> list[date]:
        frame = await self.get_daily_quotes(start_date=start_date, end_date=end_date)
        return sorted(set(frame["trade_date"].tolist()))


class _ScreenerDao(_StubDao):
    """screening 数据只保留当日时点可得的行情列（不含当期基本面，避免 R24 前视）。"""

    def __init__(self, history: pd.DataFrame) -> None:
        self._history = history

    def _frame(self, start: Any, end: Any) -> pd.DataFrame:
        start_date = _norm_date(start)
        end_date = _norm_date(end)
        frame = self._history
        if start_date is not None:
            frame = frame[frame["trade_date"] >= start_date]
        if end_date is not None:
            frame = frame[frame["trade_date"] <= end_date]
        columns = ["ts_code", "trade_date", "close", "pct_chg", "turnover_rate", "is_tradable"]
        return frame[columns].reset_index(drop=True)

    async def get_fundamental_screening_data_range(
        self, start_date: Any, end_date: Any, max_rows: Any = None
    ) -> pd.DataFrame:
        return self._frame(start_date, end_date)

    async def get_screening_data_range(self, start_date: Any, end_date: Any, max_rows: Any = None) -> pd.DataFrame:
        return self._frame(start_date, end_date)

    async def get_screening_data(self, trade_date: Any) -> pd.DataFrame:
        return self._frame(trade_date, trade_date)

    async def get_fundamental_screening_data(self, trade_date: Any) -> pd.DataFrame:
        return self._frame(trade_date, trade_date)


class _StockDao(_StubDao):
    def __init__(self, universe: pd.DataFrame, calendar: pd.DataFrame) -> None:
        self._universe = universe
        self._calendar = calendar

    async def get_trade_cal(
        self,
        start_date: Any = None,
        end_date: Any = None,
        is_open: Any = None,
    ) -> pd.DataFrame:
        frame = self._calendar
        start = _norm_date(start_date)
        end = _norm_date(end_date)
        if start is not None:
            frame = frame[frame["cal_date"] >= start.strftime("%Y%m%d")]
        if end is not None:
            frame = frame[frame["cal_date"] <= end.strftime("%Y%m%d")]
        if is_open is not None:
            frame = frame[frame["is_open"] == str(is_open)]
        return frame.reset_index(drop=True)

    async def count_expected_rows(self, start_date: Any, end_date: Any) -> int:
        start = _norm_date(start_date)
        end = _norm_date(end_date)
        if start is None or end is None:
            return 1
        open_days = self._calendar[
            (self._calendar["is_open"] == "1")
            & (self._calendar["cal_date"] >= start.strftime("%Y%m%d"))
            & (self._calendar["cal_date"] <= end.strftime("%Y%m%d"))
        ]
        return max(1, len(open_days) * UNIVERSE_SIZE)

    async def get_stock_basic(self) -> pd.DataFrame:
        frame = self._universe.copy()
        frame["delist_date"] = None
        return frame


class _MarketDao(_StubDao):
    pass


class _StkLimitDao(_StubDao):
    pass


class _NameHistoryDao:
    async def get_name_ranges(self, ts_code: str) -> None:
        return None


class _FakeCache:
    """显式离线 fake cache：只覆盖策略/回测实际调用的 DAO 方法集。"""

    def __init__(self, inputs: dict[str, Any]) -> None:
        self.quote_dao = _QuoteDao(inputs["history"])
        self.screener_dao = _ScreenerDao(inputs["history"])
        self.stock_dao = _StockDao(inputs["universe"], inputs["calendar"])
        self.market_dao = _MarketDao()
        self.stk_limit_dao = _StkLimitDao()
        self.stock_name_history_dao = _NameHistoryDao()
        self._calendar = inputs["calendar"]
        self._benchmark = _benchmark_frame(inputs["history"])

    async def get_index_daily_range(self, ts_code_list: Any = None, start_date: Any = None, end_date: Any = None):
        frame = self._benchmark
        start = _norm_date(start_date)
        end = _norm_date(end_date)
        if start is not None:
            frame = frame[frame["trade_date"] >= start]
        if end is not None:
            frame = frame[frame["trade_date"] <= end]
        return frame.reset_index(drop=True)

    async def get_trade_cal_range(self) -> tuple[str | None, str | None]:
        cal_dates = self._calendar["cal_date"].tolist()
        if not cal_dates:
            return None, None
        return str(min(cal_dates)), str(max(cal_dates))

    async def get_concepts(self, ts_codes: Any) -> dict[str, list[str]]:
        return {}

    async def prefetch_auxiliary_data(self, ts_codes: Any, as_of_date: Any = None) -> dict[str, Any]:
        return {}

    async def get_top_inst_batch(self, ts_codes: Any, as_of_date: Any = None) -> pd.DataFrame:
        return _empty_frame()

    async def get_financial_reports_history(
        self, ts_code: str, periods: int = 8, as_of_date: Any = None
    ) -> pd.DataFrame:
        return _empty_frame()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        async def _returns_empty(*_args: Any, **_kwargs: Any) -> pd.DataFrame:
            return _empty_frame()

        return _returns_empty


class _OfflineTradeCalendar:
    def __init__(self, trading_days: list[date]) -> None:
        self._days = sorted(trading_days)

    async def get_latest_trade_date(self) -> date:
        return self._days[-1]

    async def get_start_date_by_trade_days(self, end_date: Any, days: int) -> date:
        anchor = _norm_date(end_date)
        index = bisect.bisect_left(self._days, anchor) if anchor is not None else len(self._days) - 1
        index = min(max(index, 0), len(self._days) - 1)
        return self._days[max(0, index - int(days) + 1)]


class _OfflineDataProcessor:
    """质量门控 stub：注入 GOLD 等级与 per-table 等级，不使用 STRICT_QUALITY_GATE=false。"""

    def __init__(self, cache: _FakeCache, calendar: _OfflineTradeCalendar) -> None:
        from data.persistence.quality_gate import QualityTier

        self.cache = cache
        self.trade_calendar = calendar
        self._quality_tier = QualityTier.GOLD
        self._quality_tier_by_table = {
            "daily_quotes": QualityTier.GOLD,
            "financial_reports": QualityTier.GOLD,
            "moneyflow_hsgt": QualityTier.GOLD,
        }
        self._scan_missing_dates = frozenset()
        self._health_cache = None

    def is_cancelled(self) -> bool:
        return False


def _benchmark_frame(history: pd.DataFrame) -> pd.DataFrame:
    """基准指数由全部标的当日涨跌幅等权平均确定性派生。"""
    grouped = history.groupby("trade_date", sort=True)["pct_chg"].mean()
    rows: list[dict[str, Any]] = []
    level = 3000.0
    for trade_date, pct_chg in grouped.items():
        level = level * (1.0 + float(pct_chg) / 100.0)
        rows.append(
            {
                "ts_code": BENCHMARK_CODE,
                "trade_date": trade_date,
                "pct_chg": round(float(pct_chg), 4),
                "close": round(level, 4),
            }
        )
    return pd.DataFrame(rows)


def _northbound_flow_frame(trading_days: list[date]) -> pd.DataFrame:
    """北向资金流（单位：百万元）由交易日历确定性派生，末日 7400 > 50 亿阈值。"""
    rows = [{"trade_date": day, "north_money": 5000.0 + 600.0 * index} for index, day in enumerate(trading_days[-5:])]
    return pd.DataFrame(rows)


# ============================================================================
# AI 确定性 mock 接缝
# ============================================================================
class _FakeAIService:
    """替换 ``AIService``：不联网、不读配置、不产生费用。"""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def is_cloud_available(self) -> bool:
        return True

    async def analyze_stock(self, stock_info: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        ts_code = str(stock_info.get("ts_code", ""))
        score = round(40.0 + (zlib.crc32(ts_code.encode("utf-8")) % 600) / 10.0, 1)
        return {
            "score": score,
            "confidence": 80.0,
            "summary": "离线金标准快照的确定性模拟结论",
            "thinking": "",
            "conclusion_label": "bullish" if score >= 60 else "neutral",
            "uncertainty_factors": [],
            "usage": {"total_tokens": 128},
            "cost": 0.0,
        }


class _FakeNewsFetcher:
    @staticmethod
    async def get_stock_news(ts_code: str, limit: int = 5, as_of: Any = None) -> list[Any]:
        return []

    @staticmethod
    async def get_us_major_moves(as_of: Any = None) -> str:
        return ""


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    return None


async def _async_false(*_args: Any, **_kwargs: Any) -> bool:
    return False


def _patch_ai_on_instance(strategy: Any) -> None:
    """把成本/预算护栏替换为 no-op，避免任何持久化或真实计费。"""
    strategy._ensure_cost_tracker_engine = _async_noop
    strategy._ai_budget_exhausted = _async_false
    strategy._should_prompt_unpriced = _async_false
    strategy._track_cost = _async_noop


def _ai_patch_stack() -> contextlib.ExitStack:
    """AI 链路的离线接线：只替换真实外部接缝，保留策略与 mixin 的真实计算路径。"""
    from unittest.mock import MagicMock, patch

    from utils.config_handler import ConfigHandler

    stack = contextlib.ExitStack()
    stack.enter_context(patch("strategies.ai_mixin.AIService", new=_FakeAIService))
    stack.enter_context(patch("strategies.ai_mixin.NewsFetcher", new=_FakeNewsFetcher))
    stack.enter_context(patch("strategies.ai_mixin.collect_cloud_ack_providers", new=lambda provider: (provider,)))
    stack.enter_context(patch.object(ConfigHandler, "get_init_history_years", new=MagicMock(return_value=2)))
    stack.enter_context(patch.object(ConfigHandler, "get_ai_max_concurrent_analysis", new=MagicMock(return_value=1)))
    stack.enter_context(patch.object(ConfigHandler, "get_ai_max_candidates", new=MagicMock(return_value=40)))
    stack.enter_context(patch.object(ConfigHandler, "get_llm_provider", new=MagicMock(return_value="offline-mock")))
    stack.enter_context(patch.object(ConfigHandler, "is_ai_external_acknowledged", new=MagicMock(return_value=True)))
    stack.enter_context(
        patch(
            "services.ai_service.scenario_dispatch.resolve_analysis_track",
            new=MagicMock(return_value=_await_none()),
        )
    )
    stack.enter_context(
        patch(
            "services.ai_service.scenario_dispatch.evaluate_legacy_admission",
            new=MagicMock(side_effect=lambda: _await_allowed()),
        )
    )
    return stack


async def _await_none() -> None:
    return None


async def _await_allowed() -> str:
    return "allowed"


# ============================================================================
# 策略执行
# ============================================================================
@dataclass(frozen=True)
class _StrategySpec:
    output_key: str
    strategy_key: str
    score_column: str
    ai_enabled: bool = False
    skip_api_probe: bool = False


STRATEGY_SPECS: tuple[_StrategySpec, ...] = (
    _StrategySpec("value", "value", "dv_ttm"),
    _StrategySpec("volume_breakout", "volume_breakout", "pct_chg"),
    _StrategySpec("northbound_flow", "northbound_flow", "total_mv", skip_api_probe=True),
    _StrategySpec("oversold", "oversold", "rsi_14"),
    _StrategySpec("oversold_ai", "oversold", "rsi_14", ai_enabled=True),
)


def _import_strategy_modules() -> None:
    """导入策略模块以触发 ``@register_strategy`` 注册。"""
    for module_name in ("strategies.fundamental", "strategies.market", "strategies.oversold_strategy"):
        importlib.import_module(module_name)


def _make_context(inputs: dict[str, Any], ai_enabled: bool) -> dict[str, Any]:
    cache = _FakeCache(inputs)
    dp = _OfflineDataProcessor(cache, _OfflineTradeCalendar(inputs["trading_days"]))
    snapshot = inputs["snapshot"].copy()
    context: dict[str, Any] = {
        "screening_data": snapshot,
        "fundamental_screening_data": snapshot.copy(),
        "northbound_flow_data": _northbound_flow_frame(inputs["trading_days"]),
        "data_processor": dp,
        "params": {},
        "warnings": [],
        "exclude_st": True,
        "trade_date": FREEZE_DATE,
    }
    if not ai_enabled:
        # 只跑 Phase 1：run_ai_analysis 在 _disable_ai 时原样返回数学筛选结果。
        context["_disable_ai"] = True
    return context


async def _run_strategy(spec: _StrategySpec, inputs: dict[str, Any]) -> pd.DataFrame:
    from strategies.base_strategy import get_strategy_registry

    _import_strategy_modules()
    strategy = get_strategy_registry()[spec.strategy_key]()
    if spec.skip_api_probe:
        # 离线快照跳过 Tushare API 探测（避免 Keyring/网络探测，required_apis 仅用于能力声明）。
        strategy.required_apis = ()
    context = _make_context(inputs, spec.ai_enabled)
    if spec.ai_enabled:
        _patch_ai_on_instance(strategy)
        with _ai_patch_stack():
            return await strategy.filter(context)
    return await strategy.filter(context)


def _picks_frame(result: pd.DataFrame, score_column: str) -> pd.DataFrame:
    if result is None or len(result) == 0:
        return pd.DataFrame({"rank": [], "ts_code": [], "score": [], "ai_score": []})
    scores = (
        pd.to_numeric(result[score_column], errors="coerce").astype(float).tolist()
        if score_column in result.columns
        else [math.nan] * len(result)
    )
    ai_scores = (
        pd.to_numeric(result["ai_score"], errors="coerce").astype(float).tolist()
        if "ai_score" in result.columns
        else [math.nan] * len(result)
    )
    return pd.DataFrame(
        {
            "rank": list(range(1, len(result) + 1)),
            "ts_code": result["ts_code"].astype(str).tolist(),
            "score": scores,
            "ai_score": ai_scores,
        }
    )


async def _run_backtest(inputs: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    from strategies.backtest.config import BacktestConfig
    from strategies.backtest.engine import VectorBacktestEngine
    from strategies.base_strategy import get_strategy_registry

    _import_strategy_modules()
    trading_days = inputs["trading_days"]
    end_index = len(trading_days) - 2
    start_index = end_index - BACKTEST_TRADING_DAYS + 1
    start_date = trading_days[start_index]
    end_date = trading_days[end_index]

    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=1_000_000.0,
        max_position_count=10,
        fail_fast=True,
    )
    cache = _FakeCache(inputs)
    # type: ignore[arg-type]  # 显式离线 fake cache 仅实现回测实际调用的 DAO 方法集
    engine = VectorBacktestEngine(cache, config)
    strategy = get_strategy_registry()["volume_breakout"]()
    result = await engine.run(strategy, params=None)

    nav = result.nav_curve
    nav_frame = pd.DataFrame(
        {
            "trade_date": [item.isoformat() for item in nav["trade_date"].to_list()],
            "nav": [float(value) for value in nav["nav"].to_list()],
        }
    )
    meta = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "trading_days": end_date.toordinal() - start_date.toordinal(),
        "bar_count": len(trading_days[start_index : end_index + 1]),
    }
    return nav_frame, dict(result.metrics), meta


def _build_summary(
    picks: dict[str, pd.DataFrame],
    nav_frame: pd.DataFrame,
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "freeze_date": FREEZE_DATE.isoformat(),
        "synthetic_statement": _SYNTHETIC_STATEMENT,
        "strategies": {key: {"rows": len(frame), "members": frame["ts_code"].tolist()} for key, frame in picks.items()},
        "backtest": {
            "strategy": "volume_breakout",
            "start_date": meta["start_date"],
            "end_date": meta["end_date"],
            "bar_count": meta["bar_count"],
            "nav_last": float(nav_frame["nav"].iloc[-1]) if len(nav_frame) else None,
        },
    }


async def _compute_all(inputs: dict[str, Any]) -> dict[str, Any]:
    picks: dict[str, pd.DataFrame] = {}
    for spec in STRATEGY_SPECS:
        result = await _run_strategy(spec, inputs)
        picks[spec.output_key] = _picks_frame(result, spec.score_column)
    nav_frame, metrics, meta = await _run_backtest(inputs)
    return {
        "picks": picks,
        "nav": nav_frame,
        "metrics": metrics,
        "summary": _build_summary(picks, nav_frame, meta),
    }


# ============================================================================
# 写盘与比对
# ============================================================================
def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        number = float(value)
        return number if math.isfinite(number) else None
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_expected(results: dict[str, Any]) -> None:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    for key, frame in results["picks"].items():
        frame.to_csv(
            EXPECTED_DIR / f"picks_{key}.csv",
            index=False,
            float_format=f"%.{SCORE_DECIMALS}f",
            lineterminator="\n",
        )
    results["nav"].to_csv(
        EXPECTED_DIR / "backtest_nav.csv",
        index=False,
        float_format=f"%.{SCORE_DECIMALS}f",
        lineterminator="\n",
    )
    _write_json(EXPECTED_DIR / "backtest_metrics.json", _jsonable(results["metrics"]))
    _write_json(EXPECTED_DIR / "summary.json", _jsonable(results["summary"]))


def _format_number(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "缺失"
    return repr(float(value))


def _abs_close(expected: Any, actual: Any, tolerance: float) -> bool:
    if pd.isna(expected) and pd.isna(actual):
        return True
    if pd.isna(expected) or pd.isna(actual):
        return False
    return abs(float(expected) - float(actual)) <= tolerance


def _rel_close(expected: Any, actual: Any) -> bool:
    if pd.isna(expected) and pd.isna(actual):
        return True
    if pd.isna(expected) or pd.isna(actual):
        return False
    left = float(expected)
    right = float(actual)
    if left == right:
        return True
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= NAV_REL_TOL * scale


def _read_csv(path: Path, name: str, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        raise _InputError(f"缺少 expected 文件：{path}（请先执行 --update）")
    frame = pd.read_csv(path, dtype={"ts_code": str})
    if list(frame.columns) != columns:
        raise _InputError(f"{name} 列名异常：{list(frame.columns)}，预期 {columns}")
    return frame


def _diff_picks(name: str, expected: pd.DataFrame, actual: pd.DataFrame) -> list[str]:
    diffs: list[str] = []
    if list(actual.columns) != PICK_COLUMNS:
        return [f"{name}: 生成结果列名异常（{list(actual.columns)}），预期 {PICK_COLUMNS}"]
    if len(expected) != len(actual):
        diffs.append(f"{name}: 行数不一致（期望 {len(expected)}，实际 {len(actual)}）")
    for index in range(min(len(expected), len(actual))):
        row_expected = expected.iloc[index]
        row_actual = actual.iloc[index]
        same_member = str(row_expected["ts_code"]) == str(row_actual["ts_code"])
        same_rank = int(row_expected["rank"]) == int(row_actual["rank"])
        if not (same_member and same_rank):
            diffs.append(
                f"{name}: 第 {index + 1} 行成员/名次变化"
                f"（期望 rank={int(row_expected['rank'])} ts_code={row_expected['ts_code']}；"
                f"实际 rank={int(row_actual['rank'])} ts_code={row_actual['ts_code']}）"
            )
            break
        float_diff = None
        for column in ("score", "ai_score"):
            if not _abs_close(row_expected[column], row_actual[column], SCORE_ABS_TOL):
                float_diff = (
                    f"{name}: 第 {index + 1} 行 {column} 超出容差"
                    f"（期望 {_format_number(row_expected[column])}，"
                    f"实际 {_format_number(row_actual[column])}，容差 ≤{SCORE_ABS_TOL:g}）"
                )
                break
        if float_diff:
            diffs.append(float_diff)
            break
    return diffs


def _diff_nav(name: str, expected: pd.DataFrame, actual: pd.DataFrame) -> list[str]:
    diffs: list[str] = []
    if len(expected) != len(actual):
        diffs.append(f"{name}: 行数不一致（期望 {len(expected)}，实际 {len(actual)}）")
    for index in range(min(len(expected), len(actual))):
        row_expected = expected.iloc[index]
        row_actual = actual.iloc[index]
        if str(row_expected["trade_date"]) != str(row_actual["trade_date"]):
            diffs.append(
                f"{name}: 第 {index + 1} 行交易日变化"
                f"（期望 {row_expected['trade_date']}，实际 {row_actual['trade_date']}）"
            )
            break
        if not _rel_close(row_expected["nav"], row_actual["nav"]):
            diffs.append(
                f"{name}: 第 {index + 1} 行 nav 超出相对容差"
                f"（期望 {_format_number(row_expected['nav'])}，"
                f"实际 {_format_number(row_actual['nav'])}，容差 ≤{NAV_REL_TOL:g}（相对））"
            )
            break
    return diffs


def _diff_json(name: str, expected: Any, actual: Any, path: str = "") -> list[str]:
    location = path or "<root>"
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                diffs.append(f"{name}: {location}.{key} 在 expected 中不存在（实际 {actual[key]!r}）")
            elif key not in actual:
                diffs.append(f"{name}: {location}.{key} 在生成结果中缺失（期望 {expected[key]!r}）")
            else:
                diffs.extend(_diff_json(name, expected[key], actual[key], f"{location}.{key}"))
            if diffs:
                break
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{name}: {location} 长度不一致（期望 {len(expected)}，实际 {len(actual)}）"]
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            diffs = _diff_json(name, left, right, f"{location}[{index}]")
            if diffs:
                return diffs
        return []
    if isinstance(expected, bool) or isinstance(actual, bool):
        return [] if expected == actual else [f"{name}: {location} 布尔值变化（期望 {expected!r}，实际 {actual!r}）"]
    if expected is None or actual is None:
        return [] if expected == actual else [f"{name}: {location} 缺失状态变化（期望 {expected!r}，实际 {actual!r}）"]
    if isinstance(expected, numbers.Real) and isinstance(actual, numbers.Real):
        if _rel_close(expected, actual):
            return []
        return [
            f"{name}: {location} 数值超出相对容差"
            f"（期望 {_format_number(expected)}，实际 {_format_number(actual)}，容差 ≤{NAV_REL_TOL:g}）"
        ]
    return [] if expected == actual else [f"{name}: {location} 取值变化（期望 {expected!r}，实际 {actual!r}）"]


def _compare_all(results: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for spec in STRATEGY_SPECS:
        name = f"picks_{spec.output_key}.csv"
        expected = _read_csv(EXPECTED_DIR / name, name, PICK_COLUMNS)
        diffs.extend(_diff_picks(name, expected, results["picks"][spec.output_key]))

    nav_name = "backtest_nav.csv"
    expected_nav = _read_csv(EXPECTED_DIR / nav_name, nav_name, NAV_COLUMNS)
    diffs.extend(_diff_nav(nav_name, expected_nav, results["nav"]))

    for json_name, key in (("backtest_metrics.json", "metrics"), ("summary.json", "summary")):
        path = EXPECTED_DIR / json_name
        if not path.exists():
            raise _InputError(f"缺少 expected 文件：{path}（请先执行 --update）")
        expected_json = json.loads(path.read_text(encoding="utf-8"))
        diffs.extend(_diff_json(json_name, expected_json, _jsonable(results[key])))
    return diffs


# ============================================================================
# CLI
# ============================================================================
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="依赖升级金标准快照工具（离线、确定性；默认 --check）",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="比对 expected 与重算结果（默认模式）")
    group.add_argument("--update", action="store_true", help="重算并覆写 expected")
    group.add_argument("--generate-inputs", action="store_true", help="确定性重建 inputs 目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.generate_inputs:
            _generate_inputs()
            print(f"OK: 已重建合成输入目录 {INPUT_DIR}")
            return 0

        inputs = _load_inputs()
        results = asyncio.run(_compute_all(inputs))

        if args.update:
            _write_expected(results)
            print(f"OK: 已重建 expected 目录 {EXPECTED_DIR}")
            return 0

        diffs = _compare_all(results)
        if diffs:
            print(f"DIFF: 共 {len(diffs)} 项差异")
            for item in diffs:
                print(f"  - {item}")
            return 1
        print(f"SAME: 全部 {len(STRATEGY_SPECS) + 3} 项快照一致")
        return 0
    except _InputError as error:
        print(f"输入错误: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001  # 顶层统一转退出码 2，原始异常照常打印
        print(f"运行异常: {type(error).__name__}: {error}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
