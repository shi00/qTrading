from __future__ import annotations

import logging
from datetime import datetime

from strategies.backtest.config import BacktestResult
from strategies.backtest.metrics import PROFIT_THRESHOLD

logger = logging.getLogger(__name__)


class BacktestReport:
    def format_summary(self, result: BacktestResult) -> str:
        m = result.metrics
        config = result.config
        lines = [
            f"策略: {result.strategy_name}",
            f"运行ID: {result.run_id}",
            f"回测区间: {config.start_date} ~ {config.end_date}",
            f"初始资金: {config.initial_capital:,.2f}",
            "",
            f"总收益率: {m.get('total_return', 0):.2%}",
            f"年化收益率: {m.get('annualized_return', 0):.2%}",
            f"夏普比率: {m.get('sharpe_ratio', 0):.4f}",
            f"最大回撤: {m.get('max_drawdown', 0):.2%}",
            f"卡尔马比率: {m.get('calmar_ratio', 0):.4f}",
            "",
            f"IC均值: {m.get('ic_mean', 0):.4f}" if m.get("ic_mean") is not None else "IC均值: N/A",
            f"IC信息比率: {m.get('ic_ir', 0):.4f}" if m.get("ic_ir") is not None else "IC信息比率: N/A",
            f"胜率: {m.get('win_rate', 0):.2%}",
            f"盈亏比: {m.get('profit_factor', 0):.4f}",
            f"总交易次数: {m.get('total_trades', 0)}",
            "",
            f"耗时: {result.duration_ms}ms",
        ]
        if result.data_warnings:
            lines.append("")
            lines.append(f"数据警告 ({len(result.data_warnings)}):")
            for w in result.data_warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)

    def format_monthly_stats(self, result: BacktestResult) -> str:
        if result.period_stats.is_empty():
            return "无月度统计数据"
        header = f"{'月份':<12}{'收益率':>10}{'基准收益':>12}{'超额收益':>12}"
        sep = "-" * len(header)
        lines = [header, sep]
        for row in result.period_stats.iter_rows(named=True):
            period = row.get("year_month", "N/A")
            ret = row.get("monthly_return", 0.0) or 0.0
            bench = row.get("benchmark_return", 0.0) or 0.0
            excess = row.get("excess_return", 0.0) or 0.0
            lines.append(f"{period:<12}{ret:>9.2%}{bench:>11.2%}{excess:>11.2%}")
        return "\n".join(lines)

    def format_trade_summary(self, result: BacktestResult) -> str:
        if result.trades.is_empty():
            return "无交易记录"
        pnl_col = result.trades["realized_pnl"]
        profits = [float(v) for v in pnl_col if v is not None]
        winning = [p for p in profits if p > PROFIT_THRESHOLD]
        losing = [p for p in profits if p < PROFIT_THRESHOLD]
        avg_profit = sum(profits) / len(profits) if profits else 0.0
        avg_win = sum(winning) / len(winning) if winning else 0.0
        avg_loss = sum(losing) / len(losing) if losing else 0.0
        max_win = max(profits) if profits else 0.0
        max_loss = min(profits) if profits else 0.0
        lines = [
            f"总交易: {len(result.trades)}",
            f"盈利次数: {len(winning)}  亏损次数: {len(losing)}",
            f"平均收益: {avg_profit:,.2f}",
            f"平均盈利: {avg_win:,.2f}  平均亏损: {avg_loss:,.2f}",
            f"最大单笔盈利: {max_win:,.2f}  最大单笔亏损: {max_loss:,.2f}",
        ]
        return "\n".join(lines)

    def to_markdown(self, result: BacktestResult) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections = [
            f"# 回测报告 — {result.strategy_name}",
            f"> 生成时间: {now}  |  运行ID: {result.run_id}",
            "",
            "## 摘要",
            "```",
            self.format_summary(result),
            "```",
            "",
            "## 月度统计",
            "```",
            self.format_monthly_stats(result),
            "```",
            "",
            "## 交易统计",
            "```",
            self.format_trade_summary(result),
            "```",
        ]
        if result.data_warnings:
            sections.extend(
                [
                    "",
                    "## 数据警告",
                ]
            )
            for w in result.data_warnings:
                sections.append(f"- {w}")
        return "\n".join(sections)
