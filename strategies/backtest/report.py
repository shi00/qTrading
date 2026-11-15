from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime

from core.i18n import I18n, translate_strategy_name
from strategies.backtest.config import BacktestResult
from strategies.backtest.metrics import PROFIT_THRESHOLD
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

# D12-003：D12-001 的失败日条目为 ``{"date": ..., "error": ...}``，必需数据失败的
# 错误文本由 adapter 统一为 ``<context_key> (<table>): <reason>``。报告只在此契约内
# 提取表身份；不匹配时表渲染 N/A，不猜测、不伪造。
_FAILED_TABLE_PATTERN = re.compile(r"\[BacktestAdapter\][^\n]*?\b[a-z][a-z0-9_]* \(([^()]+)\)\s*:\s*")
# D12-003：失败日条目缺字段 / 空文本时的统一占位，避免把「未知」渲染成空串。
_FAILED_UNKNOWN = "N/A"


def _normalize_failed_text(value: object) -> str:
    """折叠换行与连续空白，保证摘要逐行与 Markdown 表格结构稳定。"""
    text = "" if value is None else str(value)
    return " ".join(text.split())


def _format_failed_date(value: object) -> str:
    """失败日渲染为 ISO 日期；date/datetime 与落库重载后的字符串同口径。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    text = _normalize_failed_text(value)
    return text or _FAILED_UNKNOWN


def _failed_signal_rows(result: BacktestResult) -> list[tuple[str, str, str]]:
    """把失败信号日归一为 ``(日期, 表, 原因)`` 并稳定排序。

    错误文本在 engine 已由 ``DataSanitizer.sanitize_error`` 脱敏；报告层再做一次
    脱敏兜底（R9），避免历史/手工构造的结果把凭证明文写进导出文件。日期排序
    保证同一结果多次导出的行序一致。
    """
    rows: list[tuple[str, str, str]] = []
    for entry in result.failed_signal_dates:
        if isinstance(entry, dict):
            day = _format_failed_date(entry.get("date"))
            raw_error = entry.get("error")
        else:
            day = _FAILED_UNKNOWN
            raw_error = entry
        error = _normalize_failed_text(DataSanitizer.sanitize_error("" if raw_error is None else str(raw_error)))
        match = _FAILED_TABLE_PATTERN.search(error)
        if match is None:
            table, reason = _FAILED_UNKNOWN, error
        else:
            table = match.group(1).strip() or _FAILED_UNKNOWN
            reason = error[match.end() :].strip()
        rows.append((day, table, reason or _FAILED_UNKNOWN))
    rows.sort()
    return rows


def _escape_markdown_cell(text: str) -> str:
    """Markdown 表格单元转义：反斜杠、竖线与换行不得破坏表格结构。"""
    return _normalize_failed_text(text).replace("\\", "\\\\").replace("|", "\\|")


def _format_metric_or_na(value: object, fmt: str) -> str:
    """摘要指标渲染：缺键 / None / 非数值 / NaN / inf 渲染 N/A，不伪装成 0（R21）。

    ``fmt`` 取 ``.2%``（收益率/比率）或 ``d``（交易笔数）。bool 虽是 int 子类但非指标值，
    Decimal / 字符串数字等非 int/float 类型一律按「未测量」处理，避免静默降级或
    ``TypeError`` 中断整段摘要；非整数的交易笔数（如 2.5）同样视为不可用。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "N/A"
        if fmt == "d":
            return f"{int(value)}" if value.is_integer() else "N/A"
    return format(value, fmt)


def _format_optional_pct(value: float | None, width: int) -> str:
    """月表百分比单元：None / NaN / inf 渲染 N/A，不伪装成 0.00%（R21）。

    引擎在基准整月缺失时显式置 None（BT-04）；旧历史/畸形快照可能携带 NaN，
    一并按「未测量」处理，避免导出报告与 UI 月表口径不一致。
    """
    if value is None:
        return "N/A".rjust(width)
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A".rjust(width)
    if not math.isfinite(num):
        return "N/A".rjust(width)
    return f"{num:>{width}.2%}"


class BacktestReport:
    def format_summary(self, result: BacktestResult) -> str:
        m = result.metrics
        config = result.config
        lines = [
            # E1：报告导出接入策略名翻译（strategies 层经 core 引用，R1 合规）
            f"{I18n.get('report_strategy')}: {translate_strategy_name(result.strategy_name)}",
            f"{I18n.get('report_run_id')}: {result.run_id}",
            f"{I18n.get('report_backtest_range')}: {config.start_date} ~ {config.end_date}",
            f"{I18n.get('report_initial_capital')}: {config.initial_capital:,.2f}",
            "",
            f"{I18n.get('report_total_return')}: {_format_metric_or_na(m.get('total_return'), '.2%')}",
            f"{I18n.get('report_annualized_return')}: {_format_metric_or_na(m.get('annualized_return'), '.2%')}",
            f"{I18n.get('report_sharpe_ratio')}: {_format_metric_or_na(m.get('sharpe_ratio'), '.4f')}",
            f"{I18n.get('report_max_drawdown')}: {_format_metric_or_na(m.get('max_drawdown'), '.2%')}",
            f"{I18n.get('report_calmar_ratio')}: {_format_metric_or_na(m.get('calmar_ratio'), '.4f')}",
            "",
            f"{I18n.get('report_ic_mean')}: {_format_metric_or_na(m.get('ic_mean'), '.4f')}",
            f"{I18n.get('report_ic_ir')}: {_format_metric_or_na(m.get('ic_ir'), '.4f')}",
            f"{I18n.get('report_win_rate')}: {_format_metric_or_na(m.get('win_rate'), '.2%')}",
            f"{I18n.get('report_profit_factor')}: {_format_metric_or_na(m.get('profit_factor'), '.4f')}",
            f"{I18n.get('report_total_trades')}: {_format_metric_or_na(m.get('total_trades'), 'd')}",
            "",
            f"{I18n.get('report_duration')}: {result.duration_ms}ms",
        ]
        # BT-02: 有退市强制清算时呈现分项统计，让用户评估退市假设（delist_recovery_rate
        # 经验估计）对收益的影响权重。count=0（默认）时不输出，避免干扰无退市场景。
        if result.delist_liquidation_count > 0:
            lines.append("")
            lines.append(f"{I18n.get('report_delist_liquidation_count')}: {result.delist_liquidation_count}")
            lines.append(f"{I18n.get('report_delist_loss_amount')}: {result.delist_loss_amount:,.2f}")
            lines.append(I18n.get("report_delist_note", rate=result.config.delist_recovery_rate))
        if result.data_warnings:
            lines.append("")
            lines.append(I18n.get("report_data_warnings", count=len(result.data_warnings)) + ":")
            for w in result.data_warnings:
                lines.append(f"  - {w!s}")
        # D12-003：失败信号日必须随结果导出（表/日/原因），不得只在 UI 侧提示；
        # 无失败时不输出任何段落，避免给干净结果增加噪声。
        if result.failed_signal_dates:
            rows = _failed_signal_rows(result)
            lines.append("")
            lines.append(I18n.get("report_failed_signal_dates", count=len(rows)) + ":")
            for day, table, reason in rows:
                lines.append(
                    f"  - {day} | {I18n.get('report_failed_signal_col_table')}: {table} | "
                    f"{I18n.get('report_failed_signal_col_reason')}: {reason}"
                )
        return "\n".join(lines)

    def format_monthly_stats(self, result: BacktestResult) -> str:
        if result.period_stats.is_empty():
            return I18n.get("report_no_monthly_stats")
        header = f"{I18n.get('report_monthly_header_month'):<12}{I18n.get('report_monthly_header_return'):>10}{I18n.get('report_monthly_header_benchmark'):>12}{I18n.get('report_monthly_header_excess'):>12}"
        sep = "-" * len(header)
        lines = [header, sep]
        for row in result.period_stats.iter_rows(named=True):
            period = row.get("year_month", "N/A") or "N/A"
            ret = row.get("monthly_return")
            bench = row.get("benchmark_return")
            excess = row.get("excess_return")
            # None（该月无数据，如基准全缺失）渲染 N/A，不伪装成 0.00%
            ret_str = _format_optional_pct(ret, 9)
            bench_str = _format_optional_pct(bench, 11)
            excess_str = _format_optional_pct(excess, 11)
            lines.append(f"{period:<12}{ret_str}{bench_str}{excess_str}")
        return "\n".join(lines)

    def format_trade_summary(self, result: BacktestResult) -> str:
        if result.trades.is_empty():
            return I18n.get("report_no_trades")
        pnl_col = result.trades["realized_pnl"]
        profits = [float(v) for v in pnl_col if v is not None]
        winning = [p for p in profits if p > PROFIT_THRESHOLD]
        losing = [p for p in profits if p < PROFIT_THRESHOLD]
        # 均值/极值只从对应分组取：无盈利/亏损样本时渲染 N/A，不以 0.00 冒充已测得
        # （R21；与 max_win/max_loss 的 N/A 语义对齐）。
        avg_profit_str = f"{sum(profits) / len(profits):,.2f}" if profits else "N/A"
        avg_win_str = f"{sum(winning) / len(winning):,.2f}" if winning else "N/A"
        avg_loss_str = f"{sum(losing) / len(losing):,.2f}" if losing else "N/A"
        max_win_str = f"{max(winning):,.2f}" if winning else "N/A"
        max_loss_str = f"{min(losing):,.2f}" if losing else "N/A"
        lines = [
            f"{I18n.get('report_total_trades_count')}: {len(result.trades)}",
            f"{I18n.get('report_winning_count')}: {len(winning)}  {I18n.get('report_losing_count')}: {len(losing)}",
            f"{I18n.get('report_avg_profit')}: {avg_profit_str}",
            f"{I18n.get('report_avg_win')}: {avg_win_str}  {I18n.get('report_avg_loss')}: {avg_loss_str}",
            f"{I18n.get('report_max_win')}: {max_win_str}  {I18n.get('report_max_loss')}: {max_loss_str}",
        ]
        return "\n".join(lines)

    def to_markdown(self, result: BacktestResult) -> str:
        now = get_now().strftime("%Y-%m-%d %H:%M:%S")
        sections = [
            # E1：报告标题策略名同样接入翻译（对抗检视 A-5）
            f"# {I18n.get('report_title', strategy_name=translate_strategy_name(result.strategy_name))}",
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
                sections.append(f"- {w!s}")
        if result.failed_signal_dates:
            sections.extend(
                [
                    "",
                    f"## {I18n.get('report_section_failed_signal_dates')}",
                    "",
                    f"| {I18n.get('report_failed_signal_col_date')} "
                    f"| {I18n.get('report_failed_signal_col_table')} "
                    f"| {I18n.get('report_failed_signal_col_reason')} |",
                    "|---|---|---|",
                ]
            )
            for day, table, reason in _failed_signal_rows(result):
                sections.append(
                    f"| {_escape_markdown_cell(day)} | {_escape_markdown_cell(table)} "
                    f"| {_escape_markdown_cell(reason)} |"
                )
        return "\n".join(sections)
