"""回测结果 → 业务来源严格读适配（E-01 D5，§9.13）。

分支区分（不把失败伪装成已读/已核验）：
- 成功且有行（时间可证绝对时刻）→ ``read_state=ready``、``verification=program_verified``，
  并按结果版本准备**完整受管正文**（参数/指标/质量告警/失败日期/已知限制；
  缺失项显式标注「未提供/未知」，不填 0，R21）；
- 成功且无行 → ``unavailable`` + ``result_not_found``；
- 行内时间无时区（来源时区约定不可验证）→ ``unavailable`` + ``available_at_zone_unknown``，
  不按 UTC 猜测、不补午夜（R21/R24）；
- 数据库失败 → ``PERSISTENCE_UNAVAILABLE``（不构造来源）；
- disposed → ``EngineDisposedError`` 原样传播；取消 → ``CancelledError`` 必须 raise（R2）。

正文口径：只渲染结果行内已有内容（不重算指标、不推断业务单位），以确定性文本
（固定段落顺序 + 参数键排序）输出，并挂在 ``BusinessSourceItem.body_text`` 上随
源版登记为内联受管正文；不走外置 ``body_ref``（确需外置时必须经既有
``agent_body_payload`` 受管存储）。
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    ReadState,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceVersionRef,
    VerificationState,
    sha256_hex,
)
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError

#: 可用的回测时间列：``executed_at`` 是 ``backtest_results`` 的真实列（无 ``created_at``）。
_TIMESTAMP_KEYS = ("executed_at", "created_at", "timestamp", "run_time", "created_time")


class BacktestResultReader(Protocol):
    """回测结果严格读端口（生产为 ``BacktestDao.get_result``）。"""

    async def get_result(self, run_id: str, *, suppress_errors: bool = True) -> dict | None: ...


@dataclass(frozen=True, slots=True)
class BusinessSnapshotRead:
    """业务快照读取结果：失败时 ``source=None``，绝不携带可登记「已核验」来源。"""

    source: BusinessSourceItem | None
    read_state: ReadState
    missing_reason: str | None


async def read_backtest_snapshot(
    dao: BacktestResultReader,
    *,
    user_scope: str,
    result_id: str,
    cutoff: str | None = None,
) -> BusinessSnapshotRead:
    """严格读取回测结果并构造业务快照来源（不登记；登记由 ``BusinessSourceService``）。"""

    identity = f"{user_scope}/backtest/{result_id}"
    if not isinstance(result_id, str) or not result_id:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "result_id must be non-empty", identity)
    try:
        row = await dao.get_result(result_id, suppress_errors=False)
    except asyncio.CancelledError:
        raise
    except EngineDisposedError:
        raise
    except DatabaseQueryError as exc:
        raise AgentServiceError(
            AgentErrorCode.PERSISTENCE_UNAVAILABLE, "backtest result read failed", identity
        ) from exc

    if row is None:
        return BusinessSnapshotRead(source=None, read_state=ReadState.UNAVAILABLE, missing_reason="result_not_found")
    if not isinstance(row, Mapping):
        raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "backtest row shape invalid", identity)

    safe_row = _json_safe(dict(row))
    digest = sha256_hex(
        json.dumps(safe_row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    raw_timestamp = _first_timestamp(row)
    available_at = _wire_timestamp(raw_timestamp)
    if raw_timestamp is not None and available_at is None:
        # 行内时间未带时区（``backtest_results.executed_at`` 为 timestamp without time
        # zone，由会话时区的 ``now()`` 写入），绝对时刻不可证：不得按 UTC 猜测放行。
        return BusinessSnapshotRead(
            source=None, read_state=ReadState.UNAVAILABLE, missing_reason="available_at_zone_unknown"
        )
    cutoff_dt = _parse_timestamp(cutoff, identity)
    if cutoff_dt is not None:
        if available_at is None:
            # 时点未知不能伪装成「晚于截止」（R21）：单独给出可审计原因。
            return BusinessSnapshotRead(
                source=None, read_state=ReadState.UNAVAILABLE, missing_reason="available_at_unknown"
            )
        available_dt = _parse_timestamp(available_at, identity)
        if available_dt is None or available_dt > cutoff_dt:
            return BusinessSnapshotRead(
                source=None, read_state=ReadState.UNAVAILABLE, missing_reason="available_after_cutoff"
            )

    source_id = f"backtest:{result_id}"
    body_text = _render_backtest_body(row, digest=digest, available_at=available_at)
    source = SourceRef(
        user_scope=user_scope,
        source_id=source_id,
        version="1",
        digest=digest,
        source_kind=SourceKind.BUSINESS_SNAPSHOT,
        display_name=f"回测结果 {result_id}",
        object_ref=source_id,
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.BUSINESS_SNAPSHOT,
                source_id=source_id,
                source_version="1",
                source_digest=digest,
            ),
            selection=Selection(kind="all"),
        ),
        available_at=available_at,
        period=_period_of(row),
        unit_note=None,
        read_state=ReadState.READY,
        missing_reason=None,
        provenance_refs=(),
    )
    return BusinessSnapshotRead(
        source=BusinessSourceItem(
            source=source,
            verification=VerificationState.PROGRAM_VERIFIED,
            body_text=body_text,
        ),
        read_state=ReadState.READY,
        missing_reason=None,
    )


def _first_timestamp(row: Mapping[str, Any]) -> datetime | None:
    """取行内可得时点；纯 ``date`` 不含时刻（不补午夜），按未知处理。"""
    for key in _TIMESTAMP_KEYS:
        value = row.get(key)
        # NaT/NaN 不与自身相等：必须按「时点未知」处理，不能进入 astimezone 抛裸异常。
        if isinstance(value, datetime) and value == value:
            return value
    return None


def _period_of(row: Mapping[str, Any]) -> str | None:
    start = row.get("start_date") or row.get("period_start")
    end = row.get("end_date") or row.get("period_end")
    if start and end:
        return f"{start}/{end}"
    return None


#: 显式缺失/未知标记（R21：不用 0、默认值或空段冒充「无问题」）。
_MISSING = "未提供"
_UNKNOWN = "未知"

#: 指标列（名称即 ``backtest_results`` 列，顺序固定保证正文确定性）。
_METRIC_FIELDS: tuple[tuple[str, str], ...] = (
    ("total_return", "总收益（比例小数）"),
    ("annualized_return", "年化收益（比例小数）"),
    ("sharpe_ratio", "夏普比率"),
    ("max_drawdown", "最大回撤（比例小数）"),
    ("calmar_ratio", "卡玛比率"),
    ("ic_mean", "IC 均值"),
    ("ic_ir", "ICIR"),
    ("win_rate", "胜率（比例小数）"),
    ("profit_factor", "盈亏比"),
    ("total_trades", "交易笔数"),
    ("volatility", "年化波动率（比例小数）"),
    ("information_ratio", "信息比率"),
    ("tracking_error", "跟踪误差"),
)

#: 完整配置快照（``config_json``）中与结论解释相关的参数键与中文标签。
_CONFIG_PARAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("initial_capital", "初始资金（元）"),
    ("commission_rate", "佣金费率"),
    ("commission_min", "最低佣金（元）"),
    ("stamp_duty_rate", "印花税率（null=按政策分段）"),
    ("stamp_duty_buy", "买入收印花税"),
    ("transfer_fee_rate", "过户费率"),
    ("slippage_model", "滑点模型"),
    ("slippage_bps", "滑点（bps）"),
    ("rebalance_freq", "再平衡频率"),
    ("position_sizing", "持仓分配方式"),
    ("max_position_count", "最大持仓数"),
    ("max_single_weight", "单票上限（比例小数）"),
    ("on_empty_signal", "空信号处理"),
    ("delist_recovery_rate", "退市清算回收率"),
    ("benchmark_code", "基准代码"),
    ("risk_free_rate", "无风险利率"),
    ("price_adjustment", "复权口径"),
    ("execution_price", "成交价口径"),
    ("allow_limit_up_buy", "允许涨停买入"),
    ("allow_limit_down_sell", "允许跌停卖出"),
    ("cash_reserve_pct", "现金预留比例"),
    ("min_rebalance_delta_pct", "最小调仓幅度"),
)

#: 完整配置快照未落库时仍可从平铺列读到的参数。
_FLAT_PARAM_FIELDS: tuple[tuple[str, str], ...] = (
    ("initial_capital", "初始资金（元）"),
    ("execution_price", "成交价口径"),
    ("slippage_model", "滑点模型"),
    ("allow_limit_up_buy", "允许涨停买入"),
    ("allow_limit_down_sell", "允许跌停卖出"),
)


def _render_backtest_body(row: Mapping[str, Any], *, digest: str, available_at: str | None) -> str:
    """按结果版本渲染完整受管正文（确定性文本：段落顺序固定、参数键排序）。

    只渲染落库内容，不重算指标、不推断单位：缺失/未知逐项显式标注（R21），
    使正文导出后仍自带「说明不清/不可用」信息。
    """

    lines = [
        f"# 回测结果 {_value_text(row.get('run_id'))}",
        f"- 策略：{_value_text(row.get('strategy_name'))}",
        f"- 区间：{_period_of(row) or _MISSING}",
        f"- 执行时点（UTC）：{available_at or _UNKNOWN + '（结果行无可证时点）'}",
        f"- 结果版本（内容摘要前缀）：{digest[:12]}",
        f"- 应用版本：{_value_text(row.get('app_version'))}",
        f"- 运行时长（毫秒）：{_value_text(row.get('duration_ms'))}",
        "",
        "## 参数",
    ]
    lines.extend(_param_lines(row))
    lines.extend(["", "## 指标"])
    lines.extend(f"- {label}：{_value_text(row.get(key))}" for key, label in _METRIC_FIELDS)
    lines.extend(["", "## 数据质量告警"])
    lines.extend(_quality_lines(row.get("quality_json")))
    lines.extend(["", "## 策略运行失败日"])
    lines.extend(_failed_date_lines(row.get("quality_json")))
    lines.extend(["", "## 已知限制"])
    lines.extend(_limitation_lines(row))
    lines.extend(
        [
            "",
            "## 可用性说明",
            "- 本正文为只读渲染，与结果版本摘要绑定；缺失项标注「未提供/未知」，",
            "  未用 0 或默认值填充（R21），导出后该说明随正文保留。",
        ]
    )
    return "\n".join(lines) + "\n"


def _param_lines(row: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    params = row.get("params_snapshot")
    if isinstance(params, Mapping):
        if params:
            lines.extend(f"- 策略参数 {key}：{_value_text(params[key])}" for key in sorted(params, key=str))
        else:
            lines.append(f"- 策略参数：{_MISSING}（参数快照为空）")
    else:
        lines.append(f"- 策略参数：{_MISSING}（结果行未持久化参数快照）")
    config = row.get("config_json")
    if isinstance(config, Mapping):
        lines.extend(f"- {label}：{_value_text(config.get(key))}" for key, label in _CONFIG_PARAM_FIELDS)
    else:
        lines.append(f"- 完整配置快照：{_MISSING}（存量记录未落库，成交/成本口径不可完整还原）")
        lines.extend(f"- {label}：{_value_text(row.get(key))}" for key, label in _FLAT_PARAM_FIELDS)
    return lines


def _quality_lines(quality: Any) -> list[str]:
    if not isinstance(quality, Mapping):
        return [f"- 质量快照：{_MISSING}（存量记录未落库；质量未知不得视为无告警，R21）"]
    lines: list[str] = []
    warnings = quality.get("data_warnings")
    if warnings is None:
        lines.append(f"- 数据告警：{_MISSING}（质量快照未含该字段，未知）")
    elif isinstance(warnings, (list, tuple)):
        if not warnings:
            lines.append("- 数据告警：无（质量快照中为空列表）")
        else:
            lines.append(f"- 数据告警（{len(warnings)} 条，按落库顺序）：")
            lines.extend(f"  - {_entry_text(item)}" for item in warnings)
    else:
        lines.append(f"- 数据告警：{_UNKNOWN}（字段形态不可读）")
    for key, label in (
        ("skipped_order_count", "跳过订单数"),
        ("delist_liquidation_count", "退市清算笔数"),
        ("delist_loss_amount", "退市折扣损失（元）"),
        ("has_real_score", "IC 是否来自独立打分"),
        ("real_score_ratio", "独立打分占比（比例小数）"),
    ):
        lines.append(f"- {label}：{_value_text(quality.get(key))}")
    return lines


def _failed_date_lines(quality: Any) -> list[str]:
    if not isinstance(quality, Mapping):
        return [f"- 失败日清单：{_MISSING}（质量快照未落库，未知）"]
    failed = quality.get("failed_signal_dates")
    if failed is None:
        return [f"- 失败日清单：{_MISSING}（质量快照未含该字段，未知）"]
    if not isinstance(failed, (list, tuple)):
        return [f"- 失败日清单：{_UNKNOWN}（字段形态不可读）"]
    if not failed:
        return ["- 失败日清单：无（质量快照中为空列表）"]
    lines = [f"- 失败日清单（{len(failed)} 条）："]
    lines.extend(f"  - {_entry_text(item)}" for item in failed)
    return lines


def _limitation_lines(row: Mapping[str, Any]) -> list[str]:
    config = row.get("config_json")
    config_map = config if isinstance(config, Mapping) else {}
    adjustment = config_map.get("price_adjustment")
    adjustment_note = "（前复权：历史价随后续除权除息变化，同区间不同日期运行结论不可精确复现）"
    lines = [
        f"- 复权口径：{_value_text(adjustment)}" + (adjustment_note if adjustment == "qfq" else ""),
        "- 行业分类为当前快照（成员表主键不含日期）：跨分类调整期回测可能存在前视（DAT-08②）。",
        "- 基本面数据为最新修订版：含存量区间（DATA-05 前写入）的基本面回测结果可能随时间漂移（DAT-07）。",
        "- 质量门控为代理等级（BT-05）：无告警不代表历史数据质量达到策略声明等级。",
    ]
    params = row.get("params_snapshot")
    recovery = params.get("delist_recovery_rate") if isinstance(params, Mapping) else None
    if recovery is None:
        recovery = config_map.get("delist_recovery_rate")
    lines.append(f"- 退市清算回收率假设：{_value_text(recovery)}（经验估计值，非精确变现价）")
    return lines


def _entry_text(value: Any) -> str:
    """告警/失败日条目文本：映射按 ``signal_date`` 优先输出 ``键=值`` 形式。"""
    if isinstance(value, Mapping):
        keys = (["signal_date"] if "signal_date" in value else []) + [
            key for key in sorted(value, key=str) if key != "signal_date"
        ]
        parts = [f"{key}={_value_text(value[key])}" for key in keys]
        return "，".join(parts) if parts else _UNKNOWN
    return _value_text(value)


def _value_text(value: Any) -> str:
    """值 → 可读文本；缺失/未知/空串一律显式标注（不填业务上合法的具体值，R21）。"""
    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return _MISSING if math.isnan(value) or math.isinf(value) else str(value)
    if isinstance(value, Decimal):
        # 数值列可落 NaN/Infinity：与 float 同口径按未知处理，原样输出会伪装成有效值。
        return _MISSING if value.is_nan() or value.is_infinite() else str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, str):
        return value if value.strip() else _MISSING
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)


def _wire_timestamp(value: datetime | None) -> str | None:
    """aware datetime → UTC 微秒文本；naive → ``None``（时区未定，返回未知而非猜 UTC）。

    ``datetime`` 无时区时其绝对时刻取决于一个本地未声明的来源时区（数据库会话时区），
    补成 UTC 会让「可得时点」系统性偏移；故只接受自带偏移的时间（R21/R24）。
    """

    if value is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: str | None, identity: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp is not ISO-8601", identity) from exc
    if parsed.tzinfo is None:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp must carry a timezone", identity)
    return parsed


def _json_safe(value: Any) -> Any:
    """行内容 → 规范 JSON 安全结构（用于摘要；不推断业务单位/语义）。"""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    item_method = getattr(value, "item", None)
    if callable(item_method):
        return _json_safe(item_method())
    return str(value)
