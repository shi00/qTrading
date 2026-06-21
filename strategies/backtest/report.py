from __future__ import annotations

import logging
from datetime import datetime

from core.i18n import I18n
from strategies.backtest.config import BacktestResult
from strategies.backtest.metrics import PROFIT_THRESHOLD

logger = logging.getLogger(__name__)


class BacktestReport:
    def format_summary(self, result: BacktestResult) -> str:
        m = result.metrics
        config = result.config
        lines = [
            f"{I18n.get('report_strategy')}: {result.strategy_name}",
            f"{I18n.get('report_run_id')}: {result.run_id}",
            f"{I18n.get('report_backtest_range')}: {config.start_date} ~ {config.end_date}",
            f"{I18n.get('report_initial_capital')}: {config.initial_capital:,.2f}",
            "",
            f"{I18n.get('report_total_return')}: {m.get('total_return', 0):.2%}",
            f"{I18n.get('report_annualized_return')}: {m.get('annualized_return', 0):.2%}",
            f"{I18n.get('report_sharpe_ratio')}: {m.get('sharpe_ratio', 0):.4f}",
            f"{I18n.get('report_max_drawdown')}: {m.get('max_drawdown', 0):.2%}",
            f"{I18n.get('report_calmar_ratio')}: {m.get('calmar_ratio', 0):.4f}",
            "",
            f"{I18n.get('report_ic_mean')}: {m.get('ic_mean', 0):.4f}"
            if m.get("ic_mean") is not None
            else f"{I18n.get('report_ic_mean')}: N/A",
            f"{I18n.get('report_ic_ir')}: {m.get('ic_ir', 0):.4f}"
            if m.get("ic_ir") is not None
            else f"{I18n.get('report_ic_ir')}: N/A",
            f"{I18n.get('report_win_rate')}: {m.get('win_rate', 0):.2%}",
            f"{I18n.get('report_profit_factor')}: {m.get('profit_factor', 0):.4f}",
            f"{I18n.get('report_total_trades')}: {m.get('total_trades', 0)}",
            "",
            f"{I18n.get('report_duration')}: {result.duration_ms}ms",
        ]
        if result.data_warnings:
            lines.append("")
            lines.append(I18n.get("report_data_warnings", count=len(result.data_warnings)) + ":")
            for w in result.data_warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)

    def format_monthly_stats(self, result: BacktestResult) -> str:
        if result.period_stats.is_empty():
            return I18n.get("report_no_monthly_stats")
        header = f"{I18n.get('report_monthly_header_month'):<12}{I18n.get('report_monthly_header_return'):>10}{I18n.get('report_monthly_header_benchmark'):>12}{I18n.get('report_monthly_header_excess'):>12}"
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
            return I18n.get("report_no_trades")
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
            f"{I18n.get('report_total_trades_count')}: {len(result.trades)}",
            f"{I18n.get('report_winning_count')}: {len(winning)}  {I18n.get('report_losing_count')}: {len(losing)}",
            f"{I18n.get('report_avg_profit')}: {avg_profit:,.2f}",
            f"{I18n.get('report_avg_win')}: {avg_win:,.2f}  {I18n.get('report_avg_loss')}: {avg_loss:,.2f}",
            f"{I18n.get('report_max_win')}: {max_win:,.2f}  {I18n.get('report_max_loss')}: {max_loss:,.2f}",
        ]
        return "\n".join(lines)

    def to_markdown(self, result: BacktestResult) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections = [
            f"# {I18n.get('report_title', strategy_name=result.strategy_name)}",
            f"> {I18n.get('report_generated_at', time=now, run_id=result.run_id)}",
            "",
            f"## {I18n.get('report_section_summary')}",
            "```",
            self.format_summary(result),
            "```",
            "",
            f"## {I18n.get('report_section_monthly')}",
            "```",
            self.format_monthly_stats(result),
            "```",
            "",
            f"## {I18n.get('report_section_trades')}",
            "```",
            self.format_trade_summary(result),
            "```",
        ]
        if result.data_warnings:
            sections.extend(
                [
                    "",
                    f"## {I18n.get('report_section_data_warnings')}",
                ]
            )
            for w in result.data_warnings:
                sections.append(f"- {w}")
        return "\n".join(sections)
