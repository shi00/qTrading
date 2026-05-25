"""持仓管理模块

负责持仓状态追踪、仓位分配、交易执行模拟。
从 VectorBacktestEngine 中提取，使逻辑可独立测试和复用。
"""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from data.domain_services.transaction_cost import TransactionCostModel
from strategies.backtest.config import BacktestConfig

logger = logging.getLogger(__name__)


class PortfolioSimulator:
    """
    持仓模拟器。

    职责：
    1. 持仓状态追踪（现金、持仓字典、总市值）
    2. 仓位分配（equal_weight）
    3. 交易执行（含涨跌停/停牌/资金不足检查）
    4. 持仓市值按日更新（使用 qfq_close）
    """

    def __init__(
        self,
        config: BacktestConfig,
        cost_model: TransactionCostModel,
    ):
        self.config = config
        self.cost_model = cost_model
        self.cash = config.initial_capital
        self.positions: dict[str, dict] = {}
        self.trades_list: list[dict] = []
        self.skipped_list: list[dict] = []
        self.positions_list: list[dict] = []
        self.warnings: list[str] = []

    def reset(self) -> None:
        self.cash = self.config.initial_capital
        self.positions = {}
        self.trades_list = []
        self.skipped_list = []
        self.positions_list = []
        self.warnings = []

    def process_day(
        self,
        exec_date: date,
        day_signals: pl.DataFrame,
        day_quotes: pl.DataFrame,
        is_rebalance: bool,
    ) -> None:
        if is_rebalance:
            self._sell_all_positions(exec_date, day_quotes)
            if not day_signals.is_empty():
                self._buy_signals(exec_date, day_signals, day_quotes)

        self._record_daily_positions(exec_date, day_quotes)

    def _sell_all_positions(
        self,
        exec_date: date,
        day_quotes: pl.DataFrame,
    ) -> None:
        for ts_code, pos in list(self.positions.items()):
            quote = day_quotes.filter(pl.col("ts_code") == ts_code)

            if quote.is_empty():
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "sell",
                        "reason": "no_quote",
                        "intended_volume": pos["volume"],
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} sell skipped (no_quote)")
                continue

            is_tradable = quote.select("is_tradable").item() if "is_tradable" in quote.columns else True
            if is_tradable is False:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "sell",
                        "reason": "suspended",
                        "intended_volume": pos["volume"],
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} sell skipped (suspended)")
                continue

            limit_status = quote.select("limit_status").item() if "limit_status" in quote.columns else None
            if limit_status == "down_limit" and not self.config.allow_limit_down_sell:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "sell",
                        "reason": "down_limit",
                        "intended_volume": pos["volume"],
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} sell skipped (down_limit)")
                continue

            if self.config.execution_price == "next_close":
                exit_price = float(quote.select("raw_close").item())
            else:
                exit_price = float(quote.select("raw_open").item())
            volume = pos["volume"]

            cost = self.cost_model.calculate(
                price=exit_price,
                volume=volume,
                is_buy=False,
            )

            realized_pnl = cost.net_amount - pos["cost_basis"]

            self.trades_list.append(
                {
                    "trade_date": exec_date,
                    "ts_code": ts_code,
                    "action": "sell",
                    "price": exit_price,
                    "volume": volume,
                    "gross_amount": cost.gross_amount,
                    "total_cost": cost.total_cost,
                    "net_amount": cost.net_amount,
                    "realized_pnl": realized_pnl,
                    "hold_days": (exec_date - pos["entry_date"]).days,
                }
            )

            self.cash += cost.net_amount
            del self.positions[ts_code]

    def _buy_signals(
        self,
        exec_date: date,
        day_signals: pl.DataFrame,
        day_quotes: pl.DataFrame,
    ) -> None:
        signals_sorted = day_signals.sort("signal_rank", descending=True)
        signals_sorted = signals_sorted.head(self.config.max_position_count)

        available_cash = self.cash * (1 - self.config.cash_reserve_pct)
        position_value = available_cash / min(
            len(signals_sorted),
            self.config.max_position_count,
        )

        for row in signals_sorted.iter_rows(named=True):
            ts_code = row["ts_code"]
            quote = day_quotes.filter(pl.col("ts_code") == ts_code)

            if quote.is_empty():
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "no_quote",
                        "intended_volume": 0,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (no_quote)")
                continue

            is_tradable = quote.select("is_tradable").item() if "is_tradable" in quote.columns else True
            if is_tradable is False:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "suspended",
                        "intended_volume": 0,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (suspended)")
                continue

            limit_status = quote.select("limit_status").item() if "limit_status" in quote.columns else None
            if limit_status == "up_limit" and not self.config.allow_limit_up_buy:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "up_limit",
                        "intended_volume": 0,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (up_limit)")
                continue

            if self.config.execution_price == "next_close":
                entry_price = float(quote.select("raw_close").item())
                qfq_entry_price = float(quote.select("qfq_close").item())
            else:
                entry_price = float(quote.select("raw_open").item())
                qfq_entry_price = float(quote.select("qfq_open").item())

            volume = int(position_value / entry_price / 100) * 100

            if volume <= 0:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "insufficient_cash",
                        "intended_volume": 0,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (insufficient_cash)")
                continue

            cost = self.cost_model.calculate(
                price=entry_price,
                volume=volume,
                is_buy=True,
            )

            if cost.net_amount > self.cash:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "insufficient_cash",
                        "intended_volume": volume,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (insufficient_cash)")
                continue

            self.trades_list.append(
                {
                    "trade_date": exec_date,
                    "ts_code": ts_code,
                    "action": "buy",
                    "price": entry_price,
                    "volume": volume,
                    "gross_amount": cost.gross_amount,
                    "total_cost": cost.total_cost,
                    "net_amount": cost.net_amount,
                    "realized_pnl": 0.0,
                    "hold_days": 0,
                }
            )

            self.positions[ts_code] = {
                "volume": volume,
                "cost_basis": cost.net_amount,
                "entry_date": exec_date,
                "entry_price": entry_price,
                "qfq_entry_price": qfq_entry_price,
            }

            self.cash -= cost.net_amount

    def _record_daily_positions(
        self,
        exec_date: date,
        day_quotes: pl.DataFrame,
    ) -> None:
        """
        记录每日持仓状态。

        NAV 口径统一使用 raw 价格：
        - cash 为名义金额（raw 口径）
        - 持仓市值使用 raw_close 计算
        - 除权日 NAV 不会跳变

        QFQ 价格仅用于收益计算和 PnL 展示，不参与 NAV 计算。
        """
        total_value = self.cash
        positions_detail: dict[str, dict] = {}
        for ts_code, pos in self.positions.items():
            quote = day_quotes.filter(pl.col("ts_code") == ts_code)
            if not quote.is_empty():
                qfq_market_value = pos["volume"] * float(quote.select("qfq_close").item())
                raw_market_value = pos["volume"] * float(quote.select("raw_close").item())
                total_value += raw_market_value
                qfq_entry_price = pos.get("qfq_entry_price", pos["entry_price"])
                qfq_cost_basis = pos["volume"] * qfq_entry_price
                positions_detail[ts_code] = {
                    "volume": pos["volume"],
                    "market_value": qfq_market_value,
                    "raw_market_value": raw_market_value,
                    "pnl": qfq_market_value - qfq_cost_basis,
                }

        self.positions_list.append(
            {
                "trade_date": exec_date,
                "cash": self.cash,
                "positions": positions_detail,
                "total_value": total_value,
            }
        )

    def get_results(
        self,
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, list[str]]:
        return (
            pl.DataFrame(self.trades_list) if self.trades_list else pl.DataFrame(),
            pl.DataFrame(self.positions_list),
            pl.DataFrame(self.skipped_list) if self.skipped_list else pl.DataFrame(),
            self.warnings,
        )
