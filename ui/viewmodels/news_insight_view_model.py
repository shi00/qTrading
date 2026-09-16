"""NewsInsightViewModel — 新闻风险解读状态机（新闻风险解读第一期 Phase D1）。

设计依据：``reviews/新闻风险解读方案.md`` §12（UI 状态机 + 默认交互）。

职责（MVVM，CLAUDE.md §3.2）：
- 持有不可变状态快照 ``state``（frozen dataclass），暴露命令方法
  ``select_stock`` / ``generate`` / ``retry`` / ``cancel``
- 打开详情只加载证据（``loading_evidence`` → ``evidence_ready``），不自动触发
  AI（§12 第 2 步）；用户点"生成风险解读"才进入 ``analyzing``
- 单飞任务保护：同一次分析只允许一个在途任务，新请求合并为一次（§12）
- ``cancel`` 传播取消并禁止已关闭视图接收迟到更新（卸载守卫）
- 不 import flet（R16 / 宪法：VM 层禁止 flet）；不呼叫 ``page.update()``

红线自检：
- R2：子任务 ``CancelledError`` 一律 ``raise`` 重新抛出，不吞没
- R11：单飞用 ``get_loop_local()`` 获取 ``asyncio.Event``，绑定当前循环
- R21：无置信度/无风险等级用 ``None`` 表示未知，不填充低风险
"""

from __future__ import annotations

import asyncio
import logging

from services.news_insight_models import EvidenceDocument
from services.news_insight_service import NewsInsightService
from ui.viewmodels import Message
from ui.viewmodels.news_insight_types import (
    EvidenceItem,
    NewsInsightState,
    PHASE_ANALYZING,
    PHASE_DEGRADED,
    PHASE_ERROR,
    PHASE_EVIDENCE_READY,
    PHASE_LOADING_EVIDENCE,
    PHASE_READY,
    RiskEvent,
    RiskQuote,
    SourceCoverage,
)
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.loop_local import get_loop_local
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

# 服务层成功分析态（单一数据源）
_SUCCESS = frozenset({"analyzed_with_events", "analyzed_no_event"})


def _fmt_time(dt) -> str:
    """格式化发布/完成时间为 CST 展示串；无效/缺失返回空串（不感知 locale）。"""
    if dt is None:
        return ""
    try:
        from utils.time_utils import from_utc_to_cst

        cst = from_utc_to_cst(dt)
        return cst.strftime("%Y-%m-%d %H:%M") if cst else ""
    except Exception as e:  # noqa: BLE001 - 展示降级为空串
        logger.debug("[NewsInsightVM] format time failed: %s", DataSanitizer.sanitize_error(e))
        return ""


def _coverage_to_source(coverage: dict) -> tuple[SourceCoverage, ...]:
    """把服务层 coverage dict 转不可变 SourceCoverage tuple（announcement/news/telegraph）。"""
    out: list[SourceCoverage] = []
    for src in ("announcement", "news", "telegraph"):
        item = coverage.get(src) if isinstance(coverage, dict) else {}
        out.append(
            SourceCoverage(
                source=src,
                status=str((item or {}).get("status", "fail")),
                fetched=int((item or {}).get("fetched", 0) or 0),
                adopted=int((item or {}).get("adopted", 0) or 0),
                earliest=(item or {}).get("earliest"),
                latest=(item or {}).get("latest"),
            )
        )
    return tuple(out)


def _evidence_to_items(docs: list[EvidenceDocument]) -> tuple[EvidenceItem, ...]:
    """把 EvidenceDocument 列表转不可变 EvidenceItem tuple（展示原始来源，§12 必须项）。"""
    items: list[EvidenceItem] = []
    for d in docs or []:
        items.append(
            EvidenceItem(
                news_id=int(getattr(d, "news_id", 0)),
                source_kind=getattr(d, "source_kind", None),
                source=getattr(d, "source", None),
                title=getattr(d, "title", None),
                quoteable_text=getattr(d, "quoteable_text", "") or "",
                url=getattr(d, "url", None),
                publish_time=_fmt_time(getattr(d, "publish_time", None)),
            )
        )
    return tuple(items)


def _events_to_tuple(events: list) -> tuple[RiskEvent, ...]:
    """把服务层事件 dict 列表转不可变 RiskEvent tuple。"""
    out: list[RiskEvent] = []
    for ev in events or []:
        quotes = tuple(
            RiskQuote(news_id=int(q.get("news_id", 0)), quote=str(q.get("quote", "")))
            for q in (ev.get("evidence_quotes") or [])
            if isinstance(q, dict)
        )
        out.append(
            RiskEvent(
                event_type=str(ev.get("event_type", "other")),
                severity=str(ev.get("severity", "")),
                company_role=str(ev.get("company_role", "")),
                event_status=str(ev.get("event_status", "")),
                impact_horizon=str(ev.get("impact_horizon", "")),
                fact=str(ev.get("fact", "")),
                impact_reasoning=str(ev.get("impact_reasoning", "")),
                uncertainty=str(ev.get("uncertainty", "")),
                confidence=int(ev.get("confidence", 0)),
                evidence_quotes=quotes,
            )
        )
    return tuple(out)


class NewsInsightViewModel(ObservableViewModelMixin[NewsInsightState]):
    """新闻风险解读 ViewModel（可注入普通服务，非单例）。

    构造注入 ``service``（NewsInsightService）便于测试隔离（R7/§16）。
    """

    def __init__(self, service: NewsInsightService | None = None) -> None:
        self._service = service if service is not None else NewsInsightService()
        self._state: NewsInsightState = NewsInsightState()
        self._init_mixin_fields()

        self._active_task: asyncio.Task | None = None
        self._selected_ts_code: str = ""

    # ------------------------------------------------------------------
    # state 只读访问（契约）
    # ------------------------------------------------------------------

    @property
    def state(self) -> NewsInsightState:
        return self._state

    # ------------------------------------------------------------------
    # 命令：select_stock / generate / retry / cancel（同步入口）
    # ------------------------------------------------------------------

    def select_stock(self, ts_code: str | None, stock_name: str | None = None) -> None:
        """选中股票：取消上一任务并加载证据；不触发 AI（§12 第 2 步）。"""
        if not ts_code:
            return
        self._selected_ts_code = ts_code
        self._cancel_active()
        self._begin_load(
            ts_code=ts_code,
            stock_name=stock_name or "",
        )

    def generate(self) -> None:
        """生成风险解读：单飞检查后启动分析（缓存命中直接转 ready）。"""
        if not self._selected_ts_code:
            return
        if self._active_task is not None and not self._active_task.done():
            return  # 单飞：合并为一次请求
        self._set_state(phase=PHASE_ANALYZING, message=None)
        loop = self._get_loop_or_none()
        if loop is None:
            self._set_state(phase=PHASE_ERROR, message=Message("news_insight_err_no_loop"))
            return
        task = loop.create_task(self._analyze_task(self._selected_ts_code))
        self._active_task = task
        task.add_done_callback(self._on_task_done)

    def retry(self) -> None:
        """重试：重新生成风险解读。"""
        self.generate()

    def cancel(self) -> None:
        """同步取消：取消在途任务并传播取消（§12 第 6 步，不在 handler 中 await）。"""
        self._cancel_active()

    def dispose(self) -> None:
        """卸载资源：取消在途任务并清理（MVVM 生命周期）。"""
        self._cancel_active()
        self._selected_ts_code = ""
        self._state = NewsInsightState()
        super().dispose()

    # ------------------------------------------------------------------
    # 内部异步任务（子任务，R2：CancelledError 一律 raise）
    # ------------------------------------------------------------------

    def _begin_load(self, *, ts_code: str, stock_name: str) -> None:
        """起证据加载任务（loading_evidence → evidence_ready/degraded）。"""
        self._set_state(
            ts_code=ts_code,
            stock_name=stock_name,
            phase=PHASE_LOADING_EVIDENCE,
            risk_level=None,
            risk_level_text="",
            confidence=None,
            summary=None,
            events=(),
            reused=False,
            reuse_type="none",
            message=None,
            evidence=(),
            analysis_time=None,
        )
        loop = self._get_loop_or_none()
        if loop is None:
            self._set_state(phase=PHASE_ERROR, message=Message("news_insight_err_no_loop"))
            return
        task = loop.create_task(self._load_evidence_task(ts_code, stock_name))
        self._active_task = task
        task.add_done_callback(self._on_task_done)

    async def _load_evidence_task(self, ts_code: str, stock_name: str) -> None:
        """证据加载子任务（不触发 AI）。"""
        cancel_event = self._cancel_event()
        try:
            evidence, coverage = await self._service.load_evidence_preview(
                ts_code, stock_name or None, cancel_event=cancel_event
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[NewsInsightVM] load evidence failed (%s): %s", ts_code, DataSanitizer.sanitize_error(e))
            self._set_state(
                phase=PHASE_ERROR,
                message=Message("news_insight_err_evidence", params={"ts_code": ts_code}),
            )
            return
        has_evidence = bool(evidence)
        self._set_state(
            phase=PHASE_EVIDENCE_READY if has_evidence else PHASE_DEGRADED,
            evidence=_evidence_to_items(evidence),
            coverage=_coverage_to_source(coverage),
            message=Message("news_insight_no_evidence") if not has_evidence else None,
        )

    async def _analyze_task(self, ts_code: str) -> None:
        """分析子任务（ready/error；缓存命中直接 ready 并标注复用）。"""
        cancel_event = self._cancel_event()
        try:
            outcome = await self._service.analyze(ts_code, self._state.stock_name or None, cancel_event=cancel_event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[NewsInsightVM] analyze failed (%s): %s", ts_code, DataSanitizer.sanitize_error(e))
            self._set_state(
                phase=PHASE_ERROR,
                message=Message("news_insight_err_analyze", params={"ts_code": ts_code}),
            )
            return
        self._apply_outcome(outcome)

    def _apply_outcome(self, outcome) -> None:
        """把 NewsInsightOutcome 映射到 state（success → ready；失败/degraded → 对应态）。"""
        result = outcome.result
        status = result.analysis_status
        reused = bool(getattr(outcome, "reused", False))
        reuse_type = getattr(outcome, "reuse_type", "none")
        if status in _SUCCESS:
            phase = PHASE_READY
            message = None
        elif status == "failed":
            phase = PHASE_ERROR
            message = Message("news_insight_analysis_failed", params={"status": status})
        else:  # evidence_only / no_evidence → degraded
            phase = PHASE_DEGRADED
            message = Message("news_insight_degraded", params={"status": status})
        self._set_state(
            phase=phase,
            risk_level=result.risk_level,
            risk_level_text="",
            confidence=result.confidence,
            summary=result.summary,
            events=_events_to_tuple(result.events or []),
            reused=reused,
            reuse_type=reuse_type,
            coverage=_coverage_to_source(result.coverage or {}),
            message=message,
            window_label=self._window_label(),
            analysis_time=self._now_str(),
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _window_label(self) -> str:
        """分析窗口标签（CST 自然日）。"""
        try:
            return self._service.analysis_window_label()
        except Exception as e:  # noqa: BLE001
            logger.debug("[NewsInsightVM] window label failed: %s", DataSanitizer.sanitize_error(e))
            return "unknown"

    def _now_str(self) -> str:
        """当前时间 CST 展示串。"""
        try:
            from utils.time_utils import get_now

            return _fmt_time(get_now())
        except Exception as e:  # noqa: BLE001
            logger.debug("[NewsInsightVM] now failed: %s", DataSanitizer.sanitize_error(e))
            return ""

    def _cancel_event(self) -> asyncio.Event:
        """取消边界原语：经 get_loop_local 绑定当前循环（R11），避免跨循环复用。"""
        return get_loop_local("news_insight_vm_cancel_event", asyncio.Event)

    def _cancel_active(self) -> None:
        """取消在途任务（幂等）。"""
        task = self._active_task
        self._active_task = None
        if task is not None and not task.done():
            task.cancel()

    def _on_task_done(self, task: asyncio.Task) -> None:
        """任务完成回调：清除引用并读取异常避免 'Task exception was never retrieved'（R2/DoD）。"""
        if self._active_task is task:
            self._active_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "[NewsInsightVM] background task failed: %s",
                DataSanitizer.sanitize_error(exc) if isinstance(exc, Exception) else str(exc),
            )

    def _set_state(self, **changes) -> None:
        """更新 state 并通知订阅者（disposed guard）。"""
        super()._set_state(**changes)
