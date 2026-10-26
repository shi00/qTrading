"""ModelUsageViewModel — 「使用记录」页 ViewModel（S5 双入口，reviews/模型统一.md §11.5）。

职责（§11.2 状态所有权 / §11.5 记录、测试与键盘）：
- 请求列表分页（50/页）+ 同口径预算摘要；
- 过滤（日期 / 场景 / 子任务 / 连接 / 业务·测试 / 终态 / 费用状态）；
- 展开有序尝试与字段来源（跳过行标「未执行」，不返回虚构用量）；
- 月汇总视图切换、超出保留期提示、CSV 导出。

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
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from core.i18n import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

if TYPE_CHECKING:
    from data.persistence.daos.ai_usage_dao import AIUsageDao

#: 分页大小（§11.1「分页 50 条」）。
PAGE_SIZE = 50

ORIGIN_TEST = "test"
ORIGIN_BUSINESS = "business"

EXPORT_DETAIL = "detail"
EXPORT_MONTHLY_SUMMARY = "monthly_summary"


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
    """月度预算摘要（§11.5 三独立字段；缺失用 None，禁伪零，R21）。

    ``known_cny`` 为已结算费用（未统计 → None）；``effective_reserved_cny`` 为有效
    预留（事实零为 ``Decimal("0")``）；``unconfirmed_reserved_cny`` 为未确认预留。
    """

    known_cny: Decimal | None = None
    effective_reserved_cny: Decimal | None = None
    unconfirmed_reserved_cny: Decimal | None = None


@dataclass(frozen=True)
class ModelUsageState:
    """ModelUsageViewModel 的不可变 state snapshot。"""

    requests: tuple[dict[str, Any], ...] = ()
    page: int = 0
    total: int = 0
    filters: UsageFilters = UsageFilters()
    expanded_id: str | None = None
    attempts: tuple[dict[str, Any], ...] = ()
    budget_summary: BudgetSummary = BudgetSummary()
    unconfirmed_reservations: tuple[dict[str, Any], ...] = ()
    retention_exceeded: bool = False
    monthly_summary_mode: bool = False
    export_granularity: str = EXPORT_DETAIL
    message: Message | None = None


_USAGE_FILTER_FIELDS: frozenset[str] = frozenset(f.name for f in fields(UsageFilters))


def _csv_cell(value: Any) -> str:
    """CSV 单元格：None / 缺失 → 空串（未知不伪装为 0，R21）。"""
    return "" if value is None else str(value)


def _write_csv_text(filepath: str, text: str) -> None:
    """同步写入 UTF-8-SIG CSV 文本（经 IO 线程池提交，R16 不在事件循环内执行）。"""
    with open(filepath, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write(text)


class ModelUsageViewModel(ObservableViewModelMixin[ModelUsageState]):
    """「使用记录」页 ViewModel（请求分页 / 过滤 / 展开 / 预算 / 导出）。

    构造 DI：``dao_provider`` 惰性返回 ``AIUsageDao``（未就绪返回 None → 降级）；
    可选 ``reservation_releaser``（解除未确认预留的宿主副作用）；``detail_retention_days``
    为明细保留天数（超出提示进入月汇总）；``month_provider`` 便于测试注入当前月。
    """

    def __init__(
        self,
        *,
        dao_provider: Callable[[], AIUsageDao | None] | None = None,
        reservation_releaser: Callable[[str], Awaitable[Any]] | None = None,
        detail_retention_days: int = 180,
        month_provider: Callable[[], str] | None = None,
    ) -> None:
        self._dao_provider = dao_provider
        self._reservation_releaser = reservation_releaser
        self._detail_retention_days = int(detail_retention_days)
        self._month_provider = month_provider
        self._state = ModelUsageState()
        # F35：请求代次（沿 watchlist/_search_generation 范式）。load 按发起顺序建立
        # 全序，响应写入前校验代次——筛选 / 翻页触发的更新请求使在途旧请求失效。
        self._load_generation: int = 0
        # F35：展开代次。set_filter 清空展开时推进，在途旧展开响应不回填新条件界面。
        self._expand_generation: int = 0
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 加载（请求页 + 预算摘要）
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """按当前过滤与页读取请求页与预算摘要；DAO 不可用 / 失败均降级为消息。

        F35：发起时登记请求代次，响应（含失败降级）写入前校验——筛选 / 翻页触发的
        更新请求会使在途旧请求失效，旧代次响应一律丢弃，不覆盖新条件结果。
        """
        self._load_generation += 1
        generation = self._load_generation
        self._set_state(retention_exceeded=self._compute_retention_exceeded())
        dao = self._resolve_dao()
        if dao is None:
            self._set_state(message=Message("model_usage_dao_unavailable"))
            return

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
                offset=self._state.page * PAGE_SIZE,
            )
            summary = await dao.read_budget_summary(month=self._current_month())
            unconfirmed = await dao.read_unconfirmed_reservations()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except ValueError:
            if generation != self._load_generation:
                return  # 旧代次：不得覆盖当前消息
            self._set_state(message=Message("model_usage_filter_invalid"), requests=(), total=0)
            return
        except Exception:
            if generation != self._load_generation:
                return  # 旧代次：不得覆盖当前消息
            self._set_state(message=Message("model_usage_load_failed"))
            return

        if generation != self._load_generation:
            return  # 旧代次响应：拒绝写入，保留最新请求的结果

        self._set_state(
            requests=tuple(rows),
            total=int(total),
            budget_summary=BudgetSummary(
                known_cny=summary.get("known_cny"),
                effective_reserved_cny=summary.get("effective_reserved_cny"),
                unconfirmed_reserved_cny=summary.get("unconfirmed_reserved_cny"),
            ),
            unconfirmed_reservations=tuple(unconfirmed),
            message=None,
        )

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
            expanded_id=None,
            attempts=(),
            retention_exceeded=self._compute_retention_exceeded(new_filters.start),
        )
        await self.load()

    # ------------------------------------------------------------------
    # 分页
    # ------------------------------------------------------------------

    async def next_page(self) -> None:
        """下一页（50/页）；已到末页为 no-op。"""
        if (self._state.page + 1) * PAGE_SIZE >= self._state.total:
            return
        self._set_state(page=self._state.page + 1)
        await self.load()

    async def prev_page(self) -> None:
        """上一页；已在首页为 no-op。"""
        if self._state.page <= 0:
            return
        self._set_state(page=self._state.page - 1)
        await self.load()

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
        self._set_state(expanded_id=request_id, attempts=tuple(attempts))

    # ------------------------------------------------------------------
    # 解除未确认预留
    # ------------------------------------------------------------------

    async def release_unconfirmed_reservation(self, reservation_id: str) -> None:
        """解除未确认预留（经宿主注入的副作用 handler）；完成后刷新预算摘要。

        未注入 handler 时明确降级（不伪装成功）。解除不把历史费用改零，仅未确认
        预留口径归零。宿主返回 ``False`` 表示该预留已不处于未确认状态（no-op），
        同样如实反馈为「无变更」，不当作已解除（R21）。
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
        await self.load()
        self._set_state(message=Message("model_usage_release_done"))

    # ------------------------------------------------------------------
    # 月汇总视图 + 导出
    # ------------------------------------------------------------------

    def enter_monthly_summary(self) -> None:
        """归档切专用月汇总视图：整月粒度，禁不支持过滤。"""
        self._set_state(
            monthly_summary_mode=True,
            export_granularity=EXPORT_MONTHLY_SUMMARY,
            expanded_id=None,
            attempts=(),
        )

    async def export_csv(self) -> str:
        """按当前导出粒度构建 CSV 文本并标记就绪（文本落盘另有 ``write_csv``）。"""
        text = self._build_csv()
        self._set_state(message=Message("model_usage_export_ready"))
        return text

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

    def _resolve_dao(self) -> AIUsageDao | None:
        if self._dao_provider is None:
            return None
        return self._dao_provider()

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

    def _build_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        if self._state.export_granularity == EXPORT_MONTHLY_SUMMARY:
            writer.writerow(["month", "known_cny", "effective_reserved_cny", "unconfirmed_reserved_cny"])
            summary = self._state.budget_summary
            writer.writerow(
                [
                    self._current_month(),
                    _csv_cell(summary.known_cny),
                    _csv_cell(summary.effective_reserved_cny),
                    _csv_cell(summary.unconfirmed_reserved_cny),
                ]
            )
        else:
            writer.writerow(
                [
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
            for row in self._state.requests:
                writer.writerow(
                    [
                        _csv_cell(row.get("created_at")),
                        _csv_cell(row.get("scenario_id")),
                        _csv_cell(row.get("trigger")),
                        ORIGIN_TEST if row.get("is_test") else ORIGIN_BUSINESS,
                        _csv_cell(row.get("terminal_status")),
                        _csv_cell(row.get("cost_status")),
                        _csv_cell(row.get("task_summary")),
                        _csv_cell(row.get("total_duration_ms")),
                        _csv_cell(row.get("known_cost_cny")),
                    ]
                )
        return buf.getvalue()
