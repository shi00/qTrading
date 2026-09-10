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
        stock_meta: dict[str, dict] | None = None,
    ):
        self.config = config
        self.cost_model = cost_model
        self.cash = config.initial_capital
        self.positions: dict[str, dict] = {}
        self.trades_list: list[dict] = []
        self.skipped_list: list[dict] = []
        self.positions_list: list[dict] = []
        self.warnings: list[str] = []
        self._last_known_prices: dict[str, float] = {}
        # BT-002: stock_meta 提供 delist_date 字段，用于区分退市与临时停牌
        # 结构: {ts_code: {"delist_date": date | None}}
        self.stock_meta: dict[str, dict] = stock_meta or {}

    def reset(self) -> None:
        self.cash = self.config.initial_capital
        self.positions = {}
        self.trades_list = []
        self.skipped_list = []
        self.positions_list = []
        self.warnings = []
        self._last_known_prices = {}

    def process_day(
        self,
        exec_date: date,
        day_signals: pl.DataFrame,
        day_quotes: pl.DataFrame,
        is_rebalance: bool,
    ) -> None:
        if is_rebalance:
            self._rebalance_diff(exec_date, day_signals, day_quotes)

        self._record_daily_positions(exec_date, day_quotes)

    def _rebalance_diff(
        self,
        exec_date: date,
        day_signals: pl.DataFrame,
        day_quotes: pl.DataFrame,
    ) -> None:
        """按目标权重差额调仓（D4-4）。

        修复前：再平衡日无条件全清全买，即使新旧信号高度重叠，
        重叠部分也被卖出再买回，产生约 0.25% 的单次往返成本，月度再平衡
        年化虚假拖累约 3%（收益率失真）。

        修复后：只交易集合差与权重差超过阈值的部分——
          1. 不在目标中的持仓 → 全额清仓；
          2. 在目标中且权重差小于 min_rebalance_delta_pct → 保留不动，避免噪声换手；
          3. 新增或需增持/减持到目标权重的标的 → 差额调仓。
        """
        if day_signals.is_empty():
            self._sell_all_positions(exec_date, day_quotes)
            return

        from strategies.backtest.position_sizer import apply_max_weight_constraint, get_sizer

        signals_sorted = day_signals.sort("signal_rank", descending=True).head(self.config.max_position_count)
        sizer = get_sizer(self.config.position_sizing)
        weights_df = sizer.compute_weights(signals_sorted, day_quotes, self.config)
        weights_df = apply_max_weight_constraint(weights_df, self.config.max_single_weight)
        target_weights = {r["ts_code"]: float(r["weight"]) for r in weights_df.iter_rows(named=True)}
        if not target_weights or sum(target_weights.values()) <= 0:
            self._sell_all_positions(exec_date, day_quotes)
            return

        # 当前各持仓在调仓时的估值口径价值（qfq，与 NAV 一致；无当日报价用最后已知价/成本兜底）
        quotes_by_code = (
            {k[0]: v for k, v in day_quotes.partition_by("ts_code", as_dict=True).items()}
            if not day_quotes.is_empty()
            else {}
        )
        current_values: dict[str, float] = {}
        for ts_code, pos in self.positions.items():
            quote = quotes_by_code.get(ts_code)
            if quote is not None and not quote.is_empty():
                px = float(quote.select("qfq_close").item())
            else:
                px = self._last_known_prices.get(ts_code, float(pos.get("entry_price", 0.0)))
            current_values[ts_code] = pos["volume"] * px

        total_assets = self.cash + sum(current_values.values())
        investable = total_assets * (1 - self.config.cash_reserve_pct)
        threshold = self.config.min_rebalance_delta_pct

        # 1) 卖出：不在目标中的持仓全额清仓
        exit_codes = {c for c in self.positions if c not in target_weights}
        self._sell_positions(exec_date, day_quotes, ts_codes=exit_codes)

        # 2) 减持/清仓目标中权重差超过阈值的持仓（向目标市值靠拢）
        for ts_code in list(self.positions):
            target_value = investable * target_weights.get(ts_code, 0.0)
            current_value = current_values.get(ts_code, 0.0)
            delta = target_value - current_value
            if delta < 0 and abs(delta) / total_assets >= threshold:
                self._sell_position_to_value(exec_date, ts_code, quotes_by_code.get(ts_code), target_value)

        # 3) 建仓/加仓：新增或需增持超过阈值的目标标的（只交易集合差与权重差）
        to_buy: dict[str, float] = {}
        for ts_code, w in target_weights.items():
            current_value = current_values.get(ts_code, 0.0)
            delta = investable * w - current_value
            # 统一阈值：与报告 _rebalance 一致，任何标的漂移低于阈值均不交易，避免噪声换手
            if delta <= 0 or abs(delta) / total_assets < threshold:
                continue
            to_buy[ts_code] = delta

        if to_buy:
            self._buy_to_target(exec_date, to_buy, quotes_by_code, investable)

    def _sell_all_positions(
        self,
        exec_date: date,
        day_quotes: pl.DataFrame,
    ) -> None:
        self._sell_positions(exec_date, day_quotes)

    def _sell_positions(
        self,
        exec_date: date,
        day_quotes: pl.DataFrame,
        ts_codes: set[str] | None = None,
    ) -> None:
        # PERF-C3: Pre-group by ts_code via partition_by to avoid O(N*M) loop filters.
        quotes_by_code = (
            {k[0]: v for k, v in day_quotes.partition_by("ts_code", as_dict=True).items()}
            if not day_quotes.is_empty()
            else {}
        )

        for ts_code, pos in list(self.positions.items()):
            if ts_codes is not None and ts_code not in ts_codes:
                continue
            quote = quotes_by_code.get(ts_code)
            self._sell_position(exec_date, ts_code, pos, quote)

    def _sell_position(
        self,
        exec_date: date,
        ts_code: str,
        pos: dict,
        quote: pl.DataFrame | None,
    ) -> None:
        if quote is None or quote.is_empty():
            # BT-002: 区分退市与临时停牌
            # 退市标的（exec_date >= delist_date）按最后已知价强制清算；
            # 临时停牌保留持仓，由 _record_daily_positions 用最后已知价估算市值。
            if self._is_delisted(ts_code, exec_date):
                self._liquidate_delisted_position(ts_code, pos, exec_date)
                return

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
            return

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
            return

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
            return

        if self.config.execution_price == "next_close":
            exit_price = float(quote.select("qfq_close").item())
        else:
            exit_price = float(quote.select("qfq_open").item())
        volume = pos["volume"]

        cost = self.cost_model.calculate(
            price=exit_price,
            volume=volume,
            is_buy=False,
            avg_daily_volume=self._get_avg_daily_volume(quote),
            trade_date=exec_date,
        )

        # 使用滑点调整后的成交价
        actual_exit_price = cost.slippage_adjusted_price if cost.slippage_adjusted_price > 0 else exit_price

        realized_pnl = cost.net_amount - pos["cost_basis"]

        self.trades_list.append(
            {
                "trade_date": exec_date,
                "ts_code": ts_code,
                "action": "sell",
                "exit_reason": "REBALANCE",
                "price": actual_exit_price,
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
        self._last_known_prices.pop(ts_code, None)

    def _sell_position_to_value(
        self,
        exec_date: date,
        ts_code: str,
        quote: pl.DataFrame | None,
        target_value: float,
    ) -> None:
        """将持仓减持至目标市值（部分卖出）。

        跳过检查与 _sell_position 对齐（无报价/停牌/跌停），避免减持恰好落到
        无法成交的标的。部分卖出按平均成本摊销 cost_basis 并记录 sell 交易，
        保证剩余持仓的成本与已实现损益记账准确。
        """
        if ts_code not in self.positions:
            return
        pos = self.positions[ts_code]

        # 无法取得有效行情：退市→清算，临时停牌/无报价→跳过
        if quote is None or quote.is_empty():
            if self._is_delisted(ts_code, exec_date):
                self._liquidate_delisted_position(ts_code, pos, exec_date)
                return
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
            return

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
            return

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
            return

        if self.config.execution_price == "next_close":
            exit_price = float(quote.select("qfq_close").item())
        else:
            exit_price = float(quote.select("qfq_open").item())
        current_value = pos["volume"] * exit_price
        sell_value = max(current_value - target_value, 0.0)
        volume = int(sell_value / exit_price / 100) * 100

        # 减持量不超过阈值或超过现有持仓 → 复用 _sell_position 全额清仓（含记账）
        if volume <= 0 or volume >= pos["volume"]:
            self._sell_position(exec_date, ts_code, pos, quote)
            return

        # 部分减持：记录 sell 交易 + 按平均成本摊销剩余 cost_basis
        cost = self.cost_model.calculate(
            price=exit_price,
            volume=volume,
            is_buy=False,
            avg_daily_volume=self._get_avg_daily_volume(quote),
            trade_date=exec_date,
        )
        actual_exit_price = cost.slippage_adjusted_price if cost.slippage_adjusted_price > 0 else exit_price
        sell_fraction = volume / pos["volume"]
        allocated_cost = pos["cost_basis"] * sell_fraction
        realized_pnl = cost.net_amount - allocated_cost

        self.trades_list.append(
            {
                "trade_date": exec_date,
                "ts_code": ts_code,
                "action": "sell",
                "exit_reason": "REBALANCE",
                "price": actual_exit_price,
                "volume": volume,
                "gross_amount": cost.gross_amount,
                "total_cost": cost.total_cost,
                "net_amount": cost.net_amount,
                "realized_pnl": realized_pnl,
                "hold_days": (exec_date - pos["entry_date"]).days,
            }
        )

        pos["volume"] -= volume
        pos["cost_basis"] -= allocated_cost
        self.cash += cost.net_amount

    def _buy_to_target(
        self,
        exec_date: date,
        buy_targets: dict[str, float],
        quotes_by_code: dict[str, pl.DataFrame],
        budget: float,
    ) -> None:
        """对指定标的按目标买入金额建仓/加仓。

        买入总额超出预算（investable）时按比例缩减各标的金额，避免超额建仓；
        复用与 _buy_signals 一致的 checks：无报价/停牌/涨停跳过、价格<=0 跳过、
        100 股取整、滑点、现金余额检查。
        """
        if budget > 0:
            total_target = sum(buy_targets.values())
            if total_target > budget:
                scale = budget / total_target
                buy_targets = {code: v * scale for code, v in buy_targets.items()}

        for ts_code, delta_value in buy_targets.items():
            quote = quotes_by_code.get(ts_code)
            if quote is None or quote.is_empty():
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

            # D4-1: 差异化调仓买入同样统一使用 qfq 复权口径，避免除权交易日虚假损益。
            if self.config.execution_price == "next_close":
                qfq_entry_price = float(quote.select("qfq_close").item())
            else:
                qfq_entry_price = float(quote.select("qfq_open").item())

            if qfq_entry_price <= 0:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "invalid_price",
                        "intended_volume": 0,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (invalid_price)")
                continue

            target_value = delta_value
            volume = int(target_value / qfq_entry_price / 100) * 100
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
                price=qfq_entry_price,
                volume=volume,
                is_buy=True,
                avg_daily_volume=self._get_avg_daily_volume(quote),
                trade_date=exec_date,
            )
            actual_price = cost.slippage_adjusted_price if cost.slippage_adjusted_price > 0 else qfq_entry_price

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
                    "exit_reason": None,
                    "price": actual_price,
                    "volume": volume,
                    "gross_amount": cost.gross_amount,
                    "total_cost": cost.total_cost,
                    "net_amount": cost.net_amount,
                    "realized_pnl": 0.0,
                    "hold_days": 0,
                }
            )

            existing = self.positions.get(ts_code)
            if existing is None:
                self.positions[ts_code] = {
                    "volume": volume,
                    "cost_basis": cost.net_amount,
                    "entry_date": exec_date,
                    "entry_price": actual_price,
                    "qfq_entry_price": qfq_entry_price,
                }
            else:
                existing["volume"] += volume
                existing["cost_basis"] += cost.net_amount
                existing["entry_price"] = actual_price
                existing["qfq_entry_price"] = qfq_entry_price

            self.cash -= cost.net_amount

    def _is_delisted(self, ts_code: str, exec_date: date) -> bool:
        """BT-002: 判断标的在 exec_date 是否已退市。

        依据 stock_meta 中的 delist_date 字段：exec_date >= delist_date 视为已退市。
        """
        meta = self.stock_meta.get(ts_code)
        if meta is None:
            return False
        delist_date = meta.get("delist_date")
        return delist_date is not None and exec_date >= delist_date

    def _liquidate_delisted_position(self, ts_code: str, pos: dict, exec_date: date) -> None:
        """BT-002: 退市标的按最后已知价清算。

        - 清算价格使用 _last_known_prices 中的最后已知价（退市前最后一个交易日的 qfq_close）
        - 不计交易成本（非真实交易，强制簿记）
        - cash += volume * last_price
        - 从 positions 移除
        - 记录 warning 日志
        """
        last_price = self._last_known_prices.get(ts_code)
        if last_price is None:
            # 无最后已知价兜底：保留持仓，按临时停牌处理
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
            return

        volume = pos["volume"]
        proceeds = volume * last_price
        realized_pnl = proceeds - pos["cost_basis"]

        self.trades_list.append(
            {
                "trade_date": exec_date,
                "ts_code": ts_code,
                "action": "sell",
                "exit_reason": "DELISTED",
                "price": last_price,
                "volume": volume,
                "gross_amount": proceeds,
                "total_cost": 0.0,
                "net_amount": proceeds,
                "realized_pnl": realized_pnl,
                "hold_days": (exec_date - pos["entry_date"]).days,
            }
        )

        self.cash += proceeds
        del self.positions[ts_code]
        self._last_known_prices.pop(ts_code, None)
        self.warnings.append(f"{exec_date}: {ts_code} liquidated (delisted) at {last_price}")

    def _buy_signals(
        self,
        exec_date: date,
        day_signals: pl.DataFrame,
        day_quotes: pl.DataFrame,
    ) -> None:
        from strategies.backtest.position_sizer import apply_max_weight_constraint, get_sizer

        signals_sorted = day_signals.sort("signal_rank", descending=True)
        signals_sorted = signals_sorted.head(self.config.max_position_count)

        if signals_sorted.is_empty():
            return

        available_cash = self.cash * (1 - self.config.cash_reserve_pct)

        sizer = get_sizer(self.config.position_sizing)
        weights_df = sizer.compute_weights(signals_sorted, day_quotes, self.config)

        weights_df = apply_max_weight_constraint(weights_df, self.config.max_single_weight)

        total_weight = float(weights_df.select(pl.col("weight").sum()).item() or 0)
        if total_weight <= 0:
            return

        # 第一遍：基于快照计算所有买单的目标金额和股数
        # PERF-C3: Pre-group by ts_code via partition_by to avoid O(N*M) loop filters.
        quotes_by_code = (
            {k[0]: v for k, v in day_quotes.partition_by("ts_code", as_dict=True).items()}
            if not day_quotes.is_empty()
            else {}
        )

        buy_plans: list[dict] = []
        for row in weights_df.iter_rows(named=True):
            ts_code = row["ts_code"]
            weight = float(row["weight"])
            quote = quotes_by_code.get(ts_code)

            if quote is None or quote.is_empty():
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

            # F3-06: 防御价格为 0/负的数据损坏，避免 ZeroDivisionError 及 pnl 计算虚高
            if entry_price <= 0 or qfq_entry_price <= 0:
                self.skipped_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "direction": "buy",
                        "reason": "invalid_price",
                        "intended_volume": 0,
                    }
                )
                self.warnings.append(f"{exec_date}: {ts_code} buy skipped (invalid_price)")
                continue

            target_value = available_cash * weight
            # D4-1: 股数按复权价计算，保证 qfq 成交额不超可用现金且买入日净值只降交易成本。
            # 持仓日收益 = qfq_close / qfq_entry_price - 1，与股数无关，股数口径不影响净值收益率。
            volume = int(target_value / qfq_entry_price / 100) * 100

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

            buy_plans.append(
                {
                    "ts_code": ts_code,
                    "qfq_entry_price": qfq_entry_price,
                    "volume": volume,
                    "target_value": target_value,
                    "quote": quote,
                }
            )

        if not buy_plans:
            return

        # 检查总目标金额是否超过可用现金，按比例缩减
        total_target = sum(p["target_value"] for p in buy_plans)
        if total_target > available_cash:
            scale = available_cash / total_target
            for plan in buy_plans:
                plan["target_value"] *= scale
                plan["volume"] = int(plan["target_value"] / plan["qfq_entry_price"] / 100) * 100

        # 第二遍：按缩减后的目标金额顺序下单
        for plan in buy_plans:
            ts_code = plan["ts_code"]
            qfq_entry_price = plan["qfq_entry_price"]
            volume = plan["volume"]
            quote = plan["quote"]

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
                price=qfq_entry_price,
                volume=volume,
                is_buy=True,
                avg_daily_volume=self._get_avg_daily_volume(quote),
                trade_date=exec_date,
            )

            # 使用滑点调整后的成交价重新计算股数（仅一次）
            if cost.slippage_adjusted_price > 0 and cost.slippage_adjusted_price != qfq_entry_price:
                adjusted_volume = int(plan["target_value"] / cost.slippage_adjusted_price / 100) * 100
                if 0 < adjusted_volume < volume:
                    volume = adjusted_volume
                    cost = self.cost_model.calculate(
                        price=qfq_entry_price,
                        volume=volume,
                        is_buy=True,
                        avg_daily_volume=self._get_avg_daily_volume(quote),
                        trade_date=exec_date,
                    )

            actual_price = cost.slippage_adjusted_price if cost.slippage_adjusted_price > 0 else qfq_entry_price

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
                    "exit_reason": None,
                    "price": actual_price,
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
                "entry_price": actual_price,
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

        NAV 口径统一使用 qfq（复权总收益）：
        - cash 为 qfq 口径余额（D4-1：买入/卖出均按复权价结算，与市值计价单位一致）
        - 持仓市值使用 qfq_close 计算
        - 除权日 NAV 不跳变（qfq 价格连续）

        交易成本和 realized_pnl 也保持 qfq 口径，NAV 代表复权总收益。
        """
        total_value = self.cash
        positions_detail: dict[str, dict] = {}
        # PERF-C3: Pre-group by ts_code via partition_by to avoid O(N*M) loop filters.
        quotes_by_code = (
            {k[0]: v for k, v in day_quotes.partition_by("ts_code", as_dict=True).items()}
            if not day_quotes.is_empty()
            else {}
        )
        for ts_code, pos in self.positions.items():
            quote = quotes_by_code.get(ts_code)
            if quote is not None and not quote.is_empty():
                qfq_close = float(quote.select("qfq_close").item())
                self._last_known_prices[ts_code] = qfq_close
                qfq_market_value = pos["volume"] * qfq_close
                raw_market_value = pos["volume"] * float(quote.select("raw_close").item())
                total_value += qfq_market_value
                qfq_entry_price = pos.get("qfq_entry_price", pos["entry_price"])
                qfq_cost_basis = pos["volume"] * qfq_entry_price
                positions_detail[ts_code] = {
                    "volume": pos["volume"],
                    "market_value": qfq_market_value,
                    "raw_market_value": raw_market_value,
                    "pnl": qfq_market_value - qfq_cost_basis,
                }
            else:
                last_price = self._last_known_prices.get(ts_code)
                if last_price is not None:
                    estimated_value = pos["volume"] * last_price
                    total_value += estimated_value
                    qfq_entry_price = pos.get("qfq_entry_price", pos["entry_price"])
                    qfq_cost_basis = pos["volume"] * qfq_entry_price
                    positions_detail[ts_code] = {
                        "volume": pos["volume"],
                        "market_value": estimated_value,
                        "raw_market_value": None,
                        "pnl": estimated_value - qfq_cost_basis,
                        "estimated": True,
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

    @staticmethod
    def _get_avg_daily_volume(quote: pl.DataFrame) -> float | None:
        """从行情数据中提取平均成交量"""
        if "avg_daily_volume" not in quote.columns:
            return None
        avg_vol_val = quote.select("avg_daily_volume").item()
        if avg_vol_val is None:
            return None
        try:
            return float(avg_vol_val)
        except (TypeError, ValueError):
            return None
