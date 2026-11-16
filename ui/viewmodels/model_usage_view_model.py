"""ModelUsageViewModel — 「使用记录」页 ViewModel（S5 双入口，reviews/模型统一.md §11.5）。

职责（§11.2 状态所有权 / §11.5 记录、测试与键盘）：
- 请求列表分页（50/页）+ 同口径预算摘要；
- 过滤（日期 / 场景 / 子任务 / 连接 / 业务·测试 / 终态 / 费用状态）；
- 展开有序尝试与字段来源（跳过行标「未执行」，不返回虚构用量）；
- 月度软预算录入（F36：保存 / 重开核对，配置域 ``usage_policy.monthly_cny_limit``）；
- 历史月汇总视图（F36：真实读取查看月汇总数据、月份切换、返回明细）；
- 超出保留期提示、CSV 导出。

设计约束（CLAUDE.md §3.2 MVVM / docs/patterns/mvvm.md）：
- 不 import flet、不感知 locale；state 为 frozen dataclass + tuple 容器；
- 消息为 ``Message``（i18n key + 参数），View 渲染时按 locale 解析；
- DAO 经 provider 注入（惰性解析，engine 未就绪返回 None 时降级而非崩溃）；
  VM 不直接持有 engine；
- 解除未确认预留为宿主副作用，经可选注入的 handler 完成——未注入时明确降级
  （不伪装成功，R21）；CSV 文本落盘经 ``write_csv`` 走 IO 线程池（R16）；
- R2：含 ``await`` 的方法 ``except asyncio.CancelledError: raise``，不被 Exception 吞没；
- R16：DAO 调用为 async native，不重复包线程池；
- F35：load / expand 以请求代次隔离乱序响应——响应写入前校验代次，旧代次响应
  （含失败降级）一律丢弃；筛选变化（``set_filter``）统一异步重载并立即清空旧
  结果，界面不展示与新条件不符的陈旧数据。
- F36：月汇总与预算录入分离——「录入月度预算」经宿主注入的 reader / saver
  读写配置软预算（不伪装成功）；月汇总视图按查看月真实读取，历史月数据以
  月汇总代次隔离，不被当前月替代。
- F37：CSV 导出范围二选一（当前页 / 全部筛选结果）——``all`` 经 DAO 公开
  分页入口稳定遍历（页序固定从 0 起，不受当前页影响），构建前快照粒度 /
  范围 / 过滤条件；导出行保留请求身份（``invocation_id``）；未知费用空值
  （R21）；月汇总导出对齐查看月（随 ``summary_month``，不混当前月）。
- D16-004：请求页与已加载页身份分离——翻页立即清空旧页行 / 展开并标加载中，
  页号 / 行 / total 只在成功响应后按请求身份提交（``loaded_page``）；失败与
  在途期间「当前页」导出显式不可用，不生成新页号 + 旧页行的混合 CSV。
  ``all`` 导出保留独立过滤快照语义，不受当前页加载状态影响。
- D16-005：预算草稿与已提交限额是两种事实——草稿为用户输入（独立编辑版本 /
  dirty 身份），``load`` 只回填未被用户分叉的草稿；翻页 / 过滤 / 在途加载
  不得以已提交配置覆盖未保存输入。保存回执只确认发起时的草稿版本，等待期间
  的新输入保留；不把记录过滤与预算草稿绑成全局事务。
- D16-006：写入回执与回读结果分离——``load`` 返回 ``LoadOutcome``（是否完整
  采纳 + 失败原因）；预算保存 / 预留释放成功后若回读失败或被后继请求取代，
  不得再以成功消息覆盖失败证据，而是如实呈现「已提交、显示未刷新」并保留
  旧值 / unknown 标识。
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from core.i18n import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

if TYPE_CHECKING:
    from data.persistence.daos.ai_usage_dao import AIUsageDao

#: 分页大小（§11.1「分页 50 条」）。
PAGE_SIZE = 50


def total_page_count(total_records: int) -> int:
    """记录总数 → 总页数（D18-007）：``max(1, ceil(total_records / PAGE_SIZE))``。

    ``ModelUsageState.total`` 是同一过滤口径的**记录数**（DAO ``COUNT(*)``），不是
    页码总数；分页文案 / 翻页边界须经本函数换算，避免把记录数直接显示为总页数。
    空结果也至少显示 1 页（不展示「第 1 / 0 页」）。
    """
    return max(1, (total_records + PAGE_SIZE - 1) // PAGE_SIZE)


ORIGIN_TEST = "test"
ORIGIN_BUSINESS = "business"

EXPORT_DETAIL = "detail"
EXPORT_MONTHLY_SUMMARY = "monthly_summary"

#: 明细导出范围（F37）：``page`` 仅当前已加载页；``all`` 全部筛选结果。
EXPORT_SCOPE_PAGE = "page"
EXPORT_SCOPE_ALL = "all"

#: ``load()`` 未完整采纳的原因（D16-006，供调用方区分提交与回读结果）。
LOAD_FAILURE_DAO_UNAVAILABLE = "dao_unavailable"
LOAD_FAILURE_FILTER_INVALID = "filter_invalid"
LOAD_FAILURE_REQUEST = "request_load_failed"
LOAD_FAILURE_LIMIT = "limit_load_failed"
LOAD_FAILURE_SUPERSEDED = "superseded"

#: 回读失败原因 → 可展示的 i18n 消息 key（组合消息以 ``reason_key`` 参数承载）。
_LOAD_FAILURE_MESSAGE_KEYS: Mapping[str, str] = MappingProxyType(
    {
        LOAD_FAILURE_DAO_UNAVAILABLE: "model_usage_dao_unavailable",
        LOAD_FAILURE_FILTER_INVALID: "model_usage_filter_invalid",
        LOAD_FAILURE_REQUEST: "model_usage_load_failed",
        LOAD_FAILURE_LIMIT: "model_usage_limit_load_failed",
    }
)


@dataclass(frozen=True, slots=True)
class UsageFilters:
    """使用记录过滤条件（§11.5：日期 / 场景 / 子任务 / 连接 / 业务·测试 / 终态 / 费用状态）。

    ``origin`` 取值 ``"test"`` / ``"business"`` 或 None；``start`` / ``end`` 为
    ``ai_invocation.created_at``（naive UTC 墙钟）区间边界。
    """

    start: dt.datetime | None = None
    end: dt.datetime | None = None
    scenario_id: str | None = None
    task_id: str | None = None
    connection_id: str | None = None
    origin: str | None = None
    terminal_status: str | None = None
    cost_status: str | None = None


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    """月度预算摘要（§11.5 三独立字段 + F17 计价完整性；缺失用 None，禁伪零，R21）。

    ``known_cny`` 为已结算费用（未统计 → None）；``effective_reserved_cny`` 为有效
    预留（事实零为 ``Decimal("0")``）；``unconfirmed_reserved_cny`` 为未确认预留。

    ``unpriced_attempts`` / ``free_attempts`` / ``unpriced_*_tokens`` 来自 DAO 的
    ``read_budget_summary``（判据与 ``ai_usage_dao`` 统一为 ``cost_cny IS NULL``）；
    整数字段缺失（旧调用方 / 摘要降级）用 None 表示「完整性未知」，不伪零（R21）。
    """

    known_cny: Decimal | None = None
    effective_reserved_cny: Decimal | None = None
    unconfirmed_reserved_cny: Decimal | None = None
    unpriced_attempts: int | None = None
    free_attempts: int | None = None
    unpriced_input_tokens: int | None = None
    unpriced_output_tokens: int | None = None


# 月度计价完整性三态（DAO→VM→UI 单一判据，见 ``month_pricing_status``）：
# known=全部已计价（含免费行混合）；free=全部已计价且合计为零（免费）；
# partial=已计价与未计价并存；all_unpriced=全部未计价（未知，不得显示 0.00）；
# empty=无已结算数据（未知）。
PRICING_STATUS_EMPTY = "empty"
PRICING_STATUS_KNOWN = "known"
PRICING_STATUS_FREE = "free"
PRICING_STATUS_PARTIAL = "partial"
PRICING_STATUS_ALL_UNPRICED = "all_unpriced"


def month_pricing_status(summary: BudgetSummary | None) -> str:
    """月计价完整性状态（R21）：决定费用展示为数值 / 未知 / 已知+未计价。

    判据与 DAO 同源（``cost_cny IS NULL`` 即未计价）。``summary=None`` 表示摘要
    整体未就绪（调用方按行内金额原值展示）；摘要存在但 ``unpriced_attempts``
    缺失（旧调用方）同样降级为按 ``known_cny`` 展示，不把「完整性未知」伪装成
    「全部未计价」。
    """
    if summary is None:
        return PRICING_STATUS_KNOWN
    if summary.unpriced_attempts is None:
        return PRICING_STATUS_EMPTY if summary.known_cny is None else PRICING_STATUS_KNOWN
    unpriced = summary.unpriced_attempts
    if unpriced > 0:
        # 无已结算费用且无免费尝试 → 全部未计价；否则至少一行已计价（免费或真金额）
        if summary.known_cny is None or (summary.known_cny == 0 and not summary.free_attempts):
            return PRICING_STATUS_ALL_UNPRICED
        return PRICING_STATUS_PARTIAL
    if summary.known_cny is None:
        return PRICING_STATUS_EMPTY
    if summary.known_cny == 0 and summary.free_attempts:
        return PRICING_STATUS_FREE
    return PRICING_STATUS_KNOWN


def _budget_summary_from_dao(summary: dict[str, Any]) -> BudgetSummary:
    """DAO ``read_budget_summary`` → 不可变 ``BudgetSummary``（完整性字段不丢，R21）。"""
    return BudgetSummary(
        known_cny=summary.get("known_cny"),
        effective_reserved_cny=summary.get("effective_reserved_cny"),
        unconfirmed_reserved_cny=summary.get("unconfirmed_reserved_cny"),
        unpriced_attempts=summary.get("unpriced_attempts"),
        free_attempts=summary.get("free_attempts"),
        unpriced_input_tokens=summary.get("unpriced_input_tokens"),
        unpriced_output_tokens=summary.get("unpriced_output_tokens"),
    )


#: 行内未建模列共享的空只读映射（不可变默认值，避免可变默认参数）。
_EMPTY_EXTRA: Mapping[str, Any] = MappingProxyType({})


def _freeze_value(value: Any) -> Any:
    """递归冻结 DAO 行中的嵌套容器（只读映射 / tuple / frozenset）。

    顶层 tuple 只阻止行位置重绑；若行内仍是 DAO 的可变 ``dict`` / ``list``，外部
    持有原 DAO 字典即可绕过 state 通知改写内容（docs/patterns/mvvm.md 禁止
    ``tuple[dict]``）。本函数在 VM 转换边界一次深冻结，未建模列同样适用。
    """
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _extra_columns(source: Mapping[str, Any], modelled: frozenset[str]) -> Mapping[str, Any]:
    """未建模列以只读映射保留（不丢 DAO 字段；嵌套值同样冻结）。"""
    return MappingProxyType({key: _freeze_value(value) for key, value in source.items() if key not in modelled})


@dataclass(frozen=True, slots=True)
class RequestRow:
    """请求页行（展示 / CSV 所需字段；未建模列进入只读 ``extra``）。"""

    invocation_id: str
    created_at: dt.datetime | None = None
    scenario_id: str | None = None
    trigger: str | None = None
    is_test: bool = False
    task_summary: str | None = None
    total_duration_ms: int | None = None
    known_cost_cny: Decimal | None = None
    terminal_status: str | None = None
    cost_status: str | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_EXTRA)


@dataclass(frozen=True, slots=True)
class AttemptRow:
    """展开尝试行（``executed=False`` 表示未执行；保留跳过行身份与 None 费用）。"""

    kind: str | None = None
    executed: bool = True
    task_id: str | None = None
    connection_id: str | None = None
    candidate_index: int | None = None
    retry_index: int | None = None
    status: str | None = None
    reason: str | None = None
    actual_model: str | None = None
    configured_model: str | None = None
    cost_cny: Decimal | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_EXTRA)


@dataclass(frozen=True, slots=True)
class UnconfirmedReservationRow:
    """未确认预留行（金额为 JSONB 文本，缺失保持 None，不伪零）。"""

    reservation_id: str
    task_id: str | None = None
    amount_cny: str | None = None
    billing_month: str | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_EXTRA)


@dataclass(frozen=True, slots=True)
class MonthUsageRow:
    """历史月汇总场景行（查看月读取结果，字段缺失保持 None）。"""

    grouping_key: str | None = None
    scenario_id: str | None = None
    trigger: str | None = None
    invocation_count: int | None = None
    attempt_count: int | None = None
    succeeded_count: int | None = None
    cost_cny: Decimal | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_EXTRA)


_REQUEST_ROW_FIELDS: frozenset[str] = frozenset(f.name for f in fields(RequestRow)) - {"extra"}
_ATTEMPT_ROW_FIELDS: frozenset[str] = frozenset(f.name for f in fields(AttemptRow)) - {"extra"}
_RESERVATION_ROW_FIELDS: frozenset[str] = frozenset(f.name for f in fields(UnconfirmedReservationRow)) - {"extra"}
_MONTH_ROW_FIELDS: frozenset[str] = frozenset(f.name for f in fields(MonthUsageRow)) - {"extra"}


def _request_row_from_dao(row: Mapping[str, Any]) -> RequestRow:
    """请求行 DAO dict → 冻结值（None 费用与请求身份原样保留）。"""
    return RequestRow(
        invocation_id=str(_freeze_value(row.get("invocation_id")) or ""),
        created_at=_freeze_value(row.get("created_at")),
        scenario_id=_freeze_value(row.get("scenario_id")),
        trigger=_freeze_value(row.get("trigger")),
        is_test=bool(row.get("is_test")),
        task_summary=_freeze_value(row.get("task_summary")),
        total_duration_ms=_freeze_value(row.get("total_duration_ms")),
        known_cost_cny=_freeze_value(row.get("known_cost_cny")),
        terminal_status=_freeze_value(row.get("terminal_status")),
        cost_status=_freeze_value(row.get("cost_status")),
        extra=_extra_columns(row, _REQUEST_ROW_FIELDS),
    )


def _attempt_row_from_dao(row: Mapping[str, Any]) -> AttemptRow:
    """尝试行 DAO dict → 冻结值（跳过行 ``executed=False`` 与 None 费用保留）。"""
    return AttemptRow(
        kind=_freeze_value(row.get("kind")),
        executed=bool(row.get("executed", True)),
        task_id=_freeze_value(row.get("task_id")),
        connection_id=_freeze_value(row.get("connection_id")),
        candidate_index=_freeze_value(row.get("candidate_index")),
        retry_index=_freeze_value(row.get("retry_index")),
        status=_freeze_value(row.get("status")),
        reason=_freeze_value(row.get("reason")),
        actual_model=_freeze_value(row.get("actual_model")),
        configured_model=_freeze_value(row.get("configured_model")),
        cost_cny=_freeze_value(row.get("cost_cny")),
        extra=_extra_columns(row, _ATTEMPT_ROW_FIELDS),
    )


def _reservation_row_from_dao(row: Mapping[str, Any]) -> UnconfirmedReservationRow:
    """未确认预留行 DAO dict → 冻结值（身份与金额文本原样保留）。"""
    return UnconfirmedReservationRow(
        reservation_id=str(_freeze_value(row.get("reservation_id")) or ""),
        task_id=_freeze_value(row.get("task_id")),
        amount_cny=_freeze_value(row.get("amount_cny")),
        billing_month=_freeze_value(row.get("billing_month")),
        extra=_extra_columns(row, _RESERVATION_ROW_FIELDS),
    )


def _month_row_from_dao(row: Mapping[str, Any]) -> MonthUsageRow:
    """月汇总行 DAO dict → 冻结值（费用 / 计数缺失保持 None）。"""
    return MonthUsageRow(
        grouping_key=_freeze_value(row.get("grouping_key")),
        scenario_id=_freeze_value(row.get("scenario_id")),
        trigger=_freeze_value(row.get("trigger")),
        invocation_count=_freeze_value(row.get("invocation_count")),
        attempt_count=_freeze_value(row.get("attempt_count")),
        succeeded_count=_freeze_value(row.get("succeeded_count")),
        cost_cny=_freeze_value(row.get("cost_cny")),
        extra=_extra_columns(row, _MONTH_ROW_FIELDS),
    )


@dataclass(frozen=True)
class ModelUsageState:
    """ModelUsageViewModel 的不可变 state snapshot。"""

    requests: tuple[RequestRow, ...] = ()
    page: int = 0
    total: int = 0
    # D16-004：``requests`` 所属的已加载页身份（None=未加载 / 翻页在途 / 加载失败）；
    # 「当前页」导出要求该身份与 ``page`` 一致，禁止新页号 + 旧页行混合导出。
    loaded_page: int | None = None
    # D16-004：请求在途标记（翻页清空旧行后界面显式「加载中」）。
    loading: bool = False
    filters: UsageFilters = UsageFilters()
    expanded_id: str | None = None
    attempts: tuple[AttemptRow, ...] = ()
    budget_summary: BudgetSummary = BudgetSummary()
    unconfirmed_reservations: tuple[UnconfirmedReservationRow, ...] = ()
    retention_exceeded: bool = False
    monthly_summary_mode: bool = False
    export_granularity: str = EXPORT_DETAIL
    # F37：明细导出范围（page=当前已加载页 / all=全部筛选结果）
    export_scope: str = EXPORT_SCOPE_PAGE
    # F36：预算录入（草稿为输入框值；limit 未设 None / 读取失败 unknown 分离，R21）
    # D16-005：草稿为用户输入事实，load 只回填未分叉草稿（不覆盖未保存输入）
    budget_draft: str = ""
    monthly_limit_cny: Decimal | None = None
    monthly_limit_unknown: bool = False
    # F36：历史月汇总（查看月与输入草稿分离；行 / 摘要均来自查看月读取结果）
    summary_month: str = ""
    month_draft: str = ""
    monthly_rows: tuple[MonthUsageRow, ...] = ()
    summary_budget: BudgetSummary | None = None
    message: Message | None = None


_USAGE_FILTER_FIELDS: frozenset[str] = frozenset(f.name for f in fields(UsageFilters))


def _csv_cell(value: Any) -> str:
    """CSV 单元格：None / 缺失 → 空串（未知不伪装为 0，R21）。"""
    return "" if value is None else str(value)


def _write_csv_text(filepath: str, text: str) -> None:
    """同步写入 UTF-8-SIG CSV 文本（经 IO 线程池提交，R16 不在事件循环内执行）。"""
    with open(filepath, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write(text)


def _build_detail_csv(rows: Iterable[RequestRow]) -> str:
    """明细 CSV（F37）：首列 ``invocation_id`` 保留请求身份；未知费用空值（R21）。

    行集由调用方声明（当前页或全部筛选结果），本函数不感知范围语义。
    attempt 级身份在请求页粒度不可得（一请求一行，尝试明细经
    ``read_request_attempts`` 在展开视图单独读取），不虚构该列。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "invocation_id",
            "created_at",
            "scenario_id",
            "trigger",
            "origin",
            "terminal_status",
            "cost_status",
            "task_summary",
            "total_duration_ms",
            "known_cost_cny",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                _csv_cell(row.invocation_id),
                _csv_cell(row.created_at),
                _csv_cell(row.scenario_id),
                _csv_cell(row.trigger),
                ORIGIN_TEST if row.is_test else ORIGIN_BUSINESS,
                _csv_cell(row.terminal_status),
                _csv_cell(row.cost_status),
                _csv_cell(row.task_summary),
                _csv_cell(row.total_duration_ms),
                _csv_cell(row.known_cost_cny),
            ]
        )
    return buf.getvalue()


def _build_monthly_summary_csv(month: str, summary: BudgetSummary | None) -> str:
    """月汇总 CSV（F37 对齐查看月）：month / 摘要均取查看月快照，不混当前月。

    摘要未就绪（``None``）→ 全空单元格（未知不伪装为 0，R21）。全部未计价月
    的 ``known_cny`` 写空（禁止把未知成本导出为 0），并以 ``pricing_status``
    + 未计价列显式声明完整性，消费方可区分「免费 0」与「未计价未知」。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "month",
            "known_cny",
            "effective_reserved_cny",
            "unconfirmed_reserved_cny",
            "pricing_status",
            "unpriced_attempts",
            "free_attempts",
            "unpriced_input_tokens",
            "unpriced_output_tokens",
        ]
    )
    s = summary if summary is not None else BudgetSummary()
    status = month_pricing_status(s)
    known_cell = "" if status == PRICING_STATUS_ALL_UNPRICED else _csv_cell(s.known_cny)
    writer.writerow(
        [
            _csv_cell(month),
            known_cell,
            _csv_cell(s.effective_reserved_cny),
            _csv_cell(s.unconfirmed_reserved_cny),
            status,
            _csv_cell(s.unpriced_attempts),
            _csv_cell(s.free_attempts),
            _csv_cell(s.unpriced_input_tokens),
            _csv_cell(s.unpriced_output_tokens),
        ]
    )
    return buf.getvalue()


_MONTH_TEXT_RE = re.compile(r"^(\d{4})-?(\d{2})$")


def _normalize_month(text: str) -> str | None:
    """月份输入归一化（F36）：YYYY-MM / YYYYMM → YYYYMM；非法返回 None。"""
    match = _MONTH_TEXT_RE.match(str(text or "").strip())
    if match is None:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{int(match.group(1)):04d}{month:02d}"


def _format_limit_text(value: Decimal | None) -> str:
    """预算限额输入框回显文本：未设 / 未知 → 空（「未设置 / 未知」由 View 独立展示，R21）。"""
    return "" if value is None else str(value)


def _parse_budget_amount(text: str) -> Decimal | None:
    """预算金额解析（F36）：空 → None（清除限额）；非法（不可解析 / 负 / 非有限）→ ValueError。"""
    raw = str(text or "").strip()
    if raw == "":
        return None
    parsed = Decimal(raw)
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"invalid budget amount: {raw!r}")
    return parsed


@dataclass(frozen=True, slots=True)
class LoadOutcome:
    """``load()`` 结果（D16-006）：本次读取是否完整采纳及未采纳原因。

    ``adopted`` 为 True 表示请求页 / 预算摘要 / 限额三段全部读取成功并写入
    state；False 时 ``failure`` 给出原因（``dao_unavailable`` /
    ``filter_invalid`` / ``request_load_failed`` / ``limit_load_failed`` /
    ``superseded``）。调用方据此区分「写入已提交」与「回读已刷新」，不得用
    成功消息覆盖失败证据。``superseded`` 表示已有后继请求接管加载与消息，
    调用方不得再写入自己的结果消息。
    """

    adopted: bool
    failure: str | None = None


def _refresh_failure_message(message_key: str, outcome: LoadOutcome) -> Message:
    """构造「已提交但显示未刷新」消息：以 ``reason_key`` 嵌套真实回读失败原因。"""
    reason_key = _LOAD_FAILURE_MESSAGE_KEYS.get(outcome.failure or "", "model_usage_load_failed")
    return Message(message_key, {"reason_key": reason_key})


class ModelUsageViewModel(ObservableViewModelMixin[ModelUsageState]):
    """「使用记录」页 ViewModel（请求分页 / 过滤 / 展开 / 预算 / 导出）。

    构造 DI：``dao_provider`` 惰性返回 ``AIUsageDao``（未就绪返回 None → 降级）；
    可选 ``reservation_releaser``（解除未确认预留的宿主副作用）；``detail_retention_days``
    为明细保留天数（超出提示进入月汇总）；``month_provider`` 便于测试注入当前月；
    ``budget_limit_reader`` / ``budget_limit_saver``（F36：月度软预算限额读取与 CAS
    保存，宿主经 ModelConfigurationService 实现）——未注入时预算录入明确降级。
    """

    def __init__(
        self,
        *,
        dao_provider: Callable[[], AIUsageDao | None] | None = None,
        reservation_releaser: Callable[[str], Awaitable[Any]] | None = None,
        detail_retention_days: int = 180,
        month_provider: Callable[[], str] | None = None,
        budget_limit_reader: Callable[[], Awaitable[Decimal | None]] | None = None,
        budget_limit_saver: Callable[[Decimal | None], Awaitable[bool]] | None = None,
    ) -> None:
        self._dao_provider = dao_provider
        self._reservation_releaser = reservation_releaser
        self._detail_retention_days = int(detail_retention_days)
        self._month_provider = month_provider
        self._budget_limit_reader = budget_limit_reader
        self._budget_limit_saver = budget_limit_saver
        self._state = ModelUsageState()
        # F35：请求代次（沿 watchlist/_search_generation 范式）。load 按发起顺序建立
        # 全序，响应写入前校验代次——筛选 / 翻页触发的更新请求使在途旧请求失效。
        self._load_generation: int = 0
        # F35：展开代次。set_filter 清空展开时推进，在途旧展开响应不回填新条件界面。
        self._expand_generation: int = 0
        # F36：月汇总代次。进入 / 切换查看月 / 返回明细时推进，在途旧月份响应
        # （含失败降级）不回填——历史月数据不被其他月份（含当前月）替代。
        self._summary_generation: int = 0
        # D16-005：预算草稿编辑身份。草稿承载用户输入，已提交限额承载配置事实；
        # ``_budget_draft_version`` 每次输入变化推进（保存回执按发起版本判定），
        # ``_budget_draft_dirty`` 标记草稿已分叉——load 仅在未分叉时同步回填。
        self._budget_draft_version: int = 0
        self._budget_draft_dirty: bool = False
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 加载（请求页 + 预算摘要）
    # ------------------------------------------------------------------

    async def load(self) -> LoadOutcome:
        """按当前过滤与页读取请求页与预算摘要；DAO 不可用 / 失败均降级为消息。

        F35：发起时登记请求代次，响应（含失败降级）写入前校验——筛选 / 翻页触发的
        更新请求会使在途旧请求失效，旧代次响应一律丢弃，不覆盖新条件结果。
        VMD-F01：预算限额读取（``budget_limit_reader``）是末段 await，成功或降级
        完成后、最终写入 state 前须再次复核代次——否则旧请求停在此处期间被新请求
        超越后仍会覆盖新请求的 rows / total / budget，而 filters 已属新请求。
        D16-004：发起时快照请求页，成功提交 ``loaded_page`` 为同一页号；失败 / 取消
        不提交页身份（翻页已清空旧行），「当前页」导出据此拒绝混合态。
        D16-006：返回 ``LoadOutcome``——调用方（保存 / 释放）据此区分「写入已提交」
        与「回读已刷新」，不得以成功消息覆盖本次失败 / 被取代的证据。
        """
        self._load_generation += 1
        generation = self._load_generation
        request_page = self._state.page
        self._set_state(retention_exceeded=self._compute_retention_exceeded(), loading=True)
        dao = self._resolve_dao()
        if dao is None:
            self._set_state(message=Message("model_usage_dao_unavailable"), loading=False)
            return LoadOutcome(adopted=False, failure=LOAD_FAILURE_DAO_UNAVAILABLE)

        f = self._state.filters
        try:
            rows, total = await dao.read_request_page(
                start=f.start,
                end=f.end,
                scenario_id=f.scenario_id,
                task_id=f.task_id,
                connection_id=f.connection_id,
                origin=f.origin,
                terminal_status=f.terminal_status,
                cost_status=f.cost_status,
                limit=PAGE_SIZE,
                offset=request_page * PAGE_SIZE,
            )
            summary = await dao.read_budget_summary(month=self._current_month())
            unconfirmed = await dao.read_unconfirmed_reservations()
        except asyncio.CancelledError:
            if generation == self._load_generation:
                self._set_state(loading=False)  # 当前请求被取消：复位在途标记，随后仍上抛（R2）
            raise  # R2: 必须传播
        except ValueError:
            if generation != self._load_generation:
                return LoadOutcome(adopted=False, failure=LOAD_FAILURE_SUPERSEDED)  # 旧代次：不得覆盖当前消息
            self._set_state(
                message=Message("model_usage_filter_invalid"),
                requests=(),
                total=0,
                loaded_page=None,
                loading=False,
            )
            return LoadOutcome(adopted=False, failure=LOAD_FAILURE_FILTER_INVALID)
        except Exception:
            if generation != self._load_generation:
                return LoadOutcome(adopted=False, failure=LOAD_FAILURE_SUPERSEDED)  # 旧代次：不得覆盖当前消息
            self._set_state(message=Message("model_usage_load_failed"), loading=False)
            return LoadOutcome(adopted=False, failure=LOAD_FAILURE_REQUEST)

        if generation != self._load_generation:
            return LoadOutcome(adopted=False, failure=LOAD_FAILURE_SUPERSEDED)  # 旧代次响应：拒绝写入

        # F36：月度软预算限额（配置域，非账本）随 load 读取供回显核对；读取失败记
        # unknown（View 显式「未知」），不以 None 伪装「未设置」（R21）。
        limit: Decimal | None = None
        limit_unknown = False
        if self._budget_limit_reader is not None:
            try:
                limit = await self._budget_limit_reader()
            except asyncio.CancelledError:
                if generation == self._load_generation:
                    self._set_state(loading=False)  # 当前请求被取消：复位在途标记，随后仍上抛（R2）
                raise  # R2: 必须传播
            except Exception:
                limit_unknown = True

        # VMD-F01：末段 await（预算限额读取）之后统一复核代次，覆盖成功与降级两种
        # 完成路径——旧代次响应一律拒绝写入，保留最新请求的 rows / total / budget。
        if generation != self._load_generation:
            return LoadOutcome(adopted=False, failure=LOAD_FAILURE_SUPERSEDED)  # 旧代次响应：拒绝写入

        # D16-005：已提交限额照常更新（配置事实），但草稿只在未分叉时同步回填——
        # 用户已编辑的未保存输入不因翻页 / 过滤 / 在途加载被抹掉。
        draft = self._state.budget_draft if self._budget_draft_dirty else _format_limit_text(limit)
        self._set_state(
            requests=tuple(_request_row_from_dao(row) for row in rows),
            total=int(total),
            loaded_page=request_page,
            loading=False,
            budget_summary=_budget_summary_from_dao(summary),
            unconfirmed_reservations=tuple(_reservation_row_from_dao(row) for row in unconfirmed),
            budget_draft=draft,
            monthly_limit_cny=limit,
            monthly_limit_unknown=limit_unknown,
            message=Message("model_usage_limit_load_failed") if limit_unknown else None,
        )
        # D16-006：限额读取失败时 rows / 摘要虽已采纳，但整次回读不完整（限额 unknown）。
        if limit_unknown:
            return LoadOutcome(adopted=False, failure=LOAD_FAILURE_LIMIT)
        return LoadOutcome(adopted=True)

    # ------------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------------

    async def set_filter(self, **changes: Any) -> None:
        """更新过滤字段并统一异步重载（F35：筛选变化即查询，不残留陈旧结果）。

        仅接受 ``UsageFilters`` 字段；任何过滤变化重置到第 0 页，并立即清空请求
        列表 / 总数 / 展开明细（重载完成前不展示与新条件不符的旧数据），随后
        ``await self.load()`` 重载——在途旧 load 经代次校验自动失效。非法
        ``origin`` 立即 ``raise ValueError``（不静默放宽）。
        """
        updates = {k: v for k, v in changes.items() if k in _USAGE_FILTER_FIELDS}
        if not updates:
            return
        origin = updates.get("origin")
        if origin is not None and origin not in (ORIGIN_TEST, ORIGIN_BUSINESS):
            raise ValueError(f"unknown origin filter: {origin!r}")
        new_filters = replace(self._state.filters, **updates)
        # 推进展开代次使在途展开响应失效（展开明细随筛选清空，不回填旧条件）。
        self._expand_generation += 1
        self._set_state(
            filters=new_filters,
            page=0,
            requests=(),
            total=0,
            loaded_page=None,
            expanded_id=None,
            attempts=(),
            retention_exceeded=self._compute_retention_exceeded(new_filters.start),
        )
        await self.load()

    # ------------------------------------------------------------------
    # 分页
    # ------------------------------------------------------------------

    async def next_page(self) -> None:
        """下一页（50/页）；已到末页为 no-op。

        D16-004：翻页先作废旧页行 / 展开并标加载中，再按新页请求；成功前「当前页」
        导出不可用，旧页行不得以新页号导出。
        """
        if (self._state.page + 1) * PAGE_SIZE >= self._state.total:
            return
        self._begin_page_turn(self._state.page + 1)
        await self.load()

    async def prev_page(self) -> None:
        """上一页；已在首页为 no-op（D16-004：同 ``next_page`` 清空旧页身份）。"""
        if self._state.page <= 0:
            return
        self._begin_page_turn(self._state.page - 1)
        await self.load()

    def _begin_page_turn(self, page: int) -> None:
        """切页起点：清空旧页行 / 展开并推进展开代次（D16-004）。

        旧页行与展开明细不得在新页号下继续展示或导出；在途旧展开响应经代次校验
        丢弃，不回填错页。``total`` 为同一过滤口径的计数，跨页保持。
        """
        self._expand_generation += 1
        self._set_state(page=page, requests=(), loaded_page=None, expanded_id=None, attempts=())

    # ------------------------------------------------------------------
    # 展开尝试
    # ------------------------------------------------------------------

    async def expand(self, request_id: str) -> None:
        """展开 / 折叠某请求的有序尝试明细（含跳过行，标「未执行」）。

        F35：发起时登记展开代次，响应（含失败降级）写入前校验——筛选变化清空
        展开后，在途旧展开响应不得回填新条件界面。
        """
        if self._state.expanded_id == request_id:
            self._set_state(expanded_id=None, attempts=())
            return
        self._expand_generation += 1
        generation = self._expand_generation
        dao = self._resolve_dao()
        if dao is None:
            self._set_state(message=Message("model_usage_dao_unavailable"))
            return
        try:
            attempts = await dao.read_request_attempts(invocation_id=request_id)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            if generation != self._expand_generation:
                return  # 旧代次：不得覆盖当前消息
            self._set_state(message=Message("model_usage_expand_failed"))
            return
        if generation != self._expand_generation:
            return  # 旧代次展开响应：拒绝写入
        self._set_state(expanded_id=request_id, attempts=tuple(_attempt_row_from_dao(row) for row in attempts))

    # ------------------------------------------------------------------
    # 解除未确认预留
    # ------------------------------------------------------------------

    async def release_unconfirmed_reservation(self, reservation_id: str) -> None:
        """解除未确认预留（经宿主注入的副作用 handler）；完成后刷新预算摘要。

        未注入 handler 时明确降级（不伪装成功）。解除不把历史费用改零，仅未确认
        预留口径归零。宿主返回 ``False`` 表示该预留已不处于未确认状态（no-op），
        同样如实反馈为「无变更」，不当作已解除（R21）。
        D16-006：释放成功但回读失败时呈现「已释放、显示未刷新」并携带失败原因，
        不以 ``release_done`` 覆盖回读错误；回读被后继请求取代时不写消息（由后继
        请求发布最终结果）。不推断释放写入未发生。
        """
        if self._reservation_releaser is None:
            self._set_state(message=Message("model_usage_release_unavailable"))
            return
        try:
            result = await self._reservation_releaser(reservation_id)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            self._set_state(message=Message("model_usage_release_failed"))
            return
        if result is False:
            self._set_state(message=Message("model_usage_release_noop"))
            return
        outcome = await self.load()
        if outcome.failure == LOAD_FAILURE_SUPERSEDED:
            return  # 后继请求已接管加载与消息：不覆盖其结果（D16-006）
        self._set_state(
            message=Message("model_usage_release_done")
            if outcome.adopted
            else _refresh_failure_message("model_usage_release_refresh_failed", outcome)
        )

    # ------------------------------------------------------------------
    # 月度预算录入（F36）
    # ------------------------------------------------------------------

    def set_budget_draft(self, text: str) -> None:
        """记录预算金额输入草稿（纯状态同步命令，on_change 直调，沿 set_search 先例）。

        D16-005：用户输入即草稿分叉——推进草稿编辑版本并标记 dirty，后续
        ``load``（翻页 / 过滤 / 在途加载）不再以已提交配置覆盖该草稿。文本未变
        （含 load 回填后的重渲染回传）不推进版本、不改变分叉状态。
        """
        new_text = str(text or "")
        if new_text == self._state.budget_draft:
            return
        self._budget_draft_version += 1
        self._budget_draft_dirty = True
        self._set_state(budget_draft=new_text)

    async def save_monthly_budget(self) -> None:
        """录入月度软预算（F36）：解析草稿 → 经注入 saver CAS 保存 → 重载供重开核对。

        保存去向为模型管理配置 ``usage_policy.monthly_cny_limit``（人民币月软预算，
        三场景与测试共用，§9.2），非账本写入。空串清除限额（None=不设上限，R21
        禁以 0 伪装）；非法金额（不可解析 / 负 / 非有限）不触达保存，显式消息。
        未注入 saver 明确降级（不伪装成功）；保存失败（含 CAS 冲突 / 回执未确认）
        如实反馈「未提交，请重试」，不自动重试。成功后 ``load()`` 重读限额与摘要，
        输入框回显已保存值供核对。
        D16-005：回执只确认发起保存时的草稿版本——等待期间用户继续输入时保留新
        草稿（不因回执或随之重载回退为已保存值）；无新输入才解除草稿分叉并回显
        规范化已提交值。
        D16-006：保存已提交但回读失败时呈现「已保存、显示未刷新」并携带失败原因，
        保留旧值 / unknown 标识，不以 ``budget_saved`` 覆盖回读错误；回读被后继
        请求取代时不写消息（由后继请求发布最终结果）。保存只提交一次，不因回读
        失败自动重写。
        """
        saver = self._budget_limit_saver
        if saver is None:
            self._set_state(message=Message("model_usage_budget_save_unavailable"))
            return
        try:
            amount = _parse_budget_amount(self._state.budget_draft)
        except (ArithmeticError, ValueError):
            self._set_state(message=Message("model_usage_budget_invalid"))
            return
        draft_version = self._budget_draft_version
        try:
            committed = await saver(amount)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            self._set_state(message=Message("model_usage_budget_save_failed"))
            return
        if not committed:
            self._set_state(message=Message("model_usage_budget_save_failed"))
            return
        outcome = await self.load()
        if outcome.failure == LOAD_FAILURE_SUPERSEDED:
            # 后继请求已接管加载与消息：不再写入成功消息覆盖其最终结果（D16-006）。
            return
        changes: dict[str, Any] = {
            "message": Message("model_usage_budget_saved")
            if outcome.adopted
            else _refresh_failure_message("model_usage_budget_saved_refresh_failed", outcome)
        }
        if self._budget_draft_version == draft_version:
            # 等待期间无新输入：保存回执确认发起版本，草稿与已提交限额同步。
            self._budget_draft_dirty = False
            committed_limit = self._state.monthly_limit_cny
            # 仅完整回读时以读回值规范化草稿；回读失败保留用户输入与旧值 / unknown。
            if outcome.adopted and committed_limit == amount:  # None == None（清除限额）
                changes["budget_draft"] = _format_limit_text(committed_limit)
        self._set_state(**changes)

    # ------------------------------------------------------------------
    # 历史月汇总视图 + 导出
    # ------------------------------------------------------------------

    async def enter_monthly_summary(self) -> None:
        """进入历史月汇总视图（F36）：默认查看当前月并真实读取该月汇总数据。

        整月粒度、禁不支持过滤（§11.5）；与预算录入分离。响应经月汇总代次校验，
        在途旧月份响应不回填。
        """
        month = self._current_month()
        self._summary_generation += 1
        self._set_state(
            monthly_summary_mode=True,
            export_granularity=EXPORT_MONTHLY_SUMMARY,
            expanded_id=None,
            attempts=(),
            summary_month=month,
            month_draft=month,
            monthly_rows=(),
            summary_budget=None,
        )
        await self._load_month_summary()

    async def select_summary_month(self, month_text: str | None = None) -> None:
        """切换月汇总查看月份（F36）：YYYY-MM / YYYYMM 归一化为 YYYYMM。

        缺省取输入草稿（View「查询」按钮无参调用）。非法输入显式消息（不改视图、
        不查询）；合法输入清空旧月数据后按新月份读取——历史月数据不得被当前月替代。
        """
        raw = self._state.month_draft if month_text is None else month_text
        normalized = _normalize_month(raw)
        if normalized is None:
            self._set_state(message=Message("model_usage_month_invalid"))
            return
        self._summary_generation += 1
        self._set_state(summary_month=normalized, month_draft=normalized, monthly_rows=(), summary_budget=None)
        await self._load_month_summary()

    def exit_monthly_summary(self) -> None:
        """返回请求明细视图（F36）：清空月汇总数据并复位导出粒度为明细。"""
        self._summary_generation += 1
        self._set_state(
            monthly_summary_mode=False,
            export_granularity=EXPORT_DETAIL,
            summary_month="",
            month_draft="",
            monthly_rows=(),
            summary_budget=None,
        )

    def set_month_draft(self, text: str) -> None:
        """记录月汇总月份输入草稿（纯状态同步命令，on_change 直调）。"""
        self._set_state(month_draft=str(text or ""))

    async def export_csv(self) -> str:
        """按当前导出粒度与范围构建 CSV 文本（文本落盘另有 ``write_csv``）。

        F37 范围语义（detail 粒度）：``page`` 仅导出当前已加载页（不再隐式宣称
        完整筛选集）；``all`` 经 DAO 公开分页入口稳定遍历当前筛选全集（页序固定
        从 0 起，不受当前页影响）。构建（``all`` 含 async 读取）前快照粒度 /
        范围 / 过滤条件，构建途中视图切换（进出月汇总、筛选 / 翻页重载）不改变
        本次导出口径；就绪消息仅在期间无更新加载（请求代次未前移）时写入，
        不覆盖新条件结果的消息。DAO 不可用（``all``）显式抛错由 View 呈现失败，
        不返回部分文本伪装成功（R21）；DAO 读取失败沿既有降级边界
        （``_export_csv_and_notify``）向上传播。
        D16-004：``page`` 范围要求已加载页身份与当前页一致（``loaded_page``）；
        翻页在途 / 加载失败时显式不可导出（消息 + 抛错），禁止以新页号导出旧页行。
        ``all`` 范围沿用独立过滤快照，不受当前页加载状态影响。
        """
        granularity = self._state.export_granularity
        scope = self._state.export_scope
        generation = self._load_generation
        if granularity == EXPORT_MONTHLY_SUMMARY:
            text = _build_monthly_summary_csv(self._state.summary_month, self._state.summary_budget)
        elif scope == EXPORT_SCOPE_ALL:
            text = _build_detail_csv(await self._fetch_all_filtered_requests(self._state.filters))
        else:
            loaded_page = self._state.loaded_page
            if loaded_page is None or loaded_page != self._state.page:
                self._set_state(message=Message("model_usage_export_not_loaded"))
                raise RuntimeError("export scope 'page' requires the current page to be loaded")
            text = _build_detail_csv(self._state.requests)
        if generation == self._load_generation:
            self._set_state(message=Message("model_usage_export_ready"))
        return text

    def set_export_scope(self, scope: str) -> None:
        """记录明细导出范围选择（F37，纯状态同步命令，on_change 直调）。

        仅接受 ``page``（当前已加载页）/ ``all``（全部筛选结果）；未知值
        ``raise ValueError``（不静默放宽，沿 ``set_filter`` origin 先例）。
        """
        if scope not in (EXPORT_SCOPE_PAGE, EXPORT_SCOPE_ALL):
            raise ValueError(f"unknown export scope: {scope!r}")
        self._set_state(export_scope=scope)

    async def write_csv(self, filepath: str, text: str) -> None:
        """将 ``export_csv`` 构建的 CSV 文本落盘（桌面路径）。

        R16：同步文件 IO 经 IO 线程池 offload，不阻塞事件循环；web 路径走
        ``FilePicker.save_file(src_bytes=...)`` 不落盘。异常（含
        ``asyncio.CancelledError``）向上传播，由调用方 View 呈现（R2 不吞没）。
        """
        await ThreadPoolManager().run_async(TaskType.IO, _write_csv_text, filepath, text)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _load_month_summary(self) -> None:
        """读取当前查看月的预算摘要与场景粒度月汇总行（F36，S5 展示边界）。

        R21：空结果由 View 显式「该月无汇总数据」，不以 0 伪装；响应（含失败
        降级）写入前校验月汇总代次，旧月份响应一律丢弃。
        """
        generation = self._summary_generation
        month = self._state.summary_month
        if not month:
            return
        dao = self._resolve_dao()
        if dao is None:
            self._set_state(message=Message("model_usage_dao_unavailable"))
            return
        from data.persistence.daos.ai_usage_dao import (
            SCOPE_SCENARIO,
        )  # lazy-import: 保持模块加载轻量（同 TYPE_CHECKING 口径）

        try:
            summary = await dao.read_budget_summary(month=month)
            rows = await dao.read_month_usage(month=month, aggregation_scope=SCOPE_SCENARIO)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            if generation != self._summary_generation:
                return  # 旧代次：不得覆盖当前消息
            self._set_state(message=Message("model_usage_month_load_failed"))
            return
        if generation != self._summary_generation:
            return  # 旧代次响应：拒绝写入
        self._set_state(
            summary_budget=_budget_summary_from_dao(summary),
            monthly_rows=tuple(_month_row_from_dao(row) for row in rows),
            message=None,
        )

    def _resolve_dao(self) -> AIUsageDao | None:
        if self._dao_provider is None:
            return None
        return self._dao_provider()

    async def _fetch_all_filtered_requests(self, filters: UsageFilters) -> tuple[RequestRow, ...]:
        """稳定分页遍历当前筛选全集（F37，只消费 DAO 公开分页入口，不改 DAO）。

        页序固定从 0 起、按 ``PAGE_SIZE`` 前进，不受当前页 / 翻页影响；过滤条件
        由调用方传入（``export_csv`` 构建起点的快照），遍历途中筛选变化不改变
        本次导出口径。offset 按页实际行数前进：数据在遍历期间被清理（收缩）时
        不跳页；遍历期间新写入的数据不保证纳入（offset 分页无稳定快照游标，
        DAO 未提供 keyset 入口，如实声明而非暗示完整快照）。不捕获异常：
        ``asyncio.CancelledError`` 自然传播（R2），读取失败由 View 既有降级边界
        （``_export_csv_and_notify``）呈现。
        """
        dao = self._resolve_dao()
        if dao is None:
            self._set_state(message=Message("model_usage_dao_unavailable"))
            raise RuntimeError("export scope 'all' requires ai usage dao")
        collected: list[RequestRow] = []
        offset = 0
        while True:
            rows, total = await dao.read_request_page(
                start=filters.start,
                end=filters.end,
                scenario_id=filters.scenario_id,
                task_id=filters.task_id,
                connection_id=filters.connection_id,
                origin=filters.origin,
                terminal_status=filters.terminal_status,
                cost_status=filters.cost_status,
                limit=PAGE_SIZE,
                offset=offset,
            )
            collected.extend(_request_row_from_dao(row) for row in rows)
            offset += len(rows)
            if not rows or offset >= int(total):
                break
        return tuple(collected)

    def _current_month(self) -> str:
        if self._month_provider is not None:
            return self._month_provider()
        now = get_now()
        return f"{now.year:04d}{now.month:02d}"

    def _compute_retention_exceeded(self, start: dt.datetime | None = None) -> bool:
        """起始过滤早于明细保留期 → 提示进入月汇总（不静默截断）。"""
        if start is None:
            start = self._state.filters.start
        if not isinstance(start, dt.datetime):
            return False
        cutoff = get_now().date() - dt.timedelta(days=self._detail_retention_days)
        return start.date() < cutoff
