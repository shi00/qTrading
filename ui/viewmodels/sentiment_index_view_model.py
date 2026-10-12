"""SentimentIndexViewModel — 新闻情绪指数展示 ViewModel（N5-3）。

职责（MVVM，CLAUDE.md §3.2）：
- 持有不可变状态快照 ``state``（frozen dataclass），暴露命令
  ``select_stock`` / ``select_day`` / ``cancel`` / ``dispose``
- 组件不直接调用打分服务：经本 VM 访问 :class:`NewsSentimentIndexService`
  （输出仅展示用途，ADR-0008 / N3-7 边界；strategies 层读取被守护）
- 索引加载与当日明细加载为两个独立任务槽位，互不取消踩踏；
  ``select_day`` 单飞 + 同日再点收起
- 不 import flet（R16 / 宪法：VM 层禁止 flet）；不呼叫 ``page.update()``

红线自检：
- R2：子任务 ``CancelledError`` 一律 ``raise`` 重新抛出，不吞没；取消仅经
  ``task.cancel()`` 在 await 挂起点传播（服务层无协同取消检查点参数，不引事件）
- R21：无指数/未打分一律 ``None``，不填 0
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from services.news_sentiment_index_service import NewsSentimentIndexService
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from ui.viewmodels.sentiment_index_types import (
    PHASE_ERROR,
    PHASE_LOADING,
    PHASE_READY,
    SentimentDayPoint,
    SentimentIndexState,
    SentimentNewsItem,
)
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


def _fmt_time(dt) -> str:
    """格式化发布时间为 CST 展示串；无效/缺失返回空串（不感知 locale）。"""
    if dt is None:
        return ""
    try:
        from utils.time_utils import from_utc_to_cst

        cst = from_utc_to_cst(dt)
        return cst.strftime("%Y-%m-%d %H:%M") if cst else ""
    except Exception as e:
        logger.debug("[SentimentIndexVM] format time failed: %s", DataSanitizer.sanitize_error(e))
        return ""


def _to_point(dp) -> SentimentDayPoint:
    """服务层 DailySentimentPoint → VM 不可变点（解耦服务类型，便于测试替身）。"""
    return SentimentDayPoint(
        trade_date=dp.trade_date,
        index=dp.index,
        sample_count=dp.sample_count,
        sample_insufficient=dp.sample_insufficient,
    )


def _rows_to_news_items(rows: list[dict]) -> tuple[SentimentNewsItem, ...]:
    """服务层明细行 dict → 不可变 SentimentNewsItem tuple（score/conf=None 保留，R21）。"""
    items: list[SentimentNewsItem] = []
    for r in rows or []:
        title = r.get("title")
        excerpt = r.get("content")
        items.append(
            SentimentNewsItem(
                publish_time=_fmt_time(r.get("publish_time")),
                title=title,
                excerpt=excerpt,
                source_kind=r.get("source_kind"),
                score=r.get("sentiment_score"),
                conf=r.get("sentiment_conf"),
            )
        )
    return tuple(items)


class SentimentIndexViewModel(ObservableViewModelMixin[SentimentIndexState]):
    """新闻情绪指数 ViewModel（可注入服务，非单例；构造注入便于测试隔离，R7）。"""

    def __init__(self, service: NewsSentimentIndexService | None = None, days: int = 7) -> None:
        # R16：不在 __init__ 同步构造注册单例（UI handler 路径）；service 惰性到
        # 异步任务体内首次使用时构造（DCL 幂等、无 IO，非事件处理器同步段）。
        self._service: NewsSentimentIndexService | None = service
        self._days = days
        self._state = SentimentIndexState(days=days)
        self._init_mixin_fields()

        self._index_task: asyncio.Task | None = None
        self._detail_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # state 只读访问（契约）
    # ------------------------------------------------------------------

    @property
    def state(self) -> SentimentIndexState:
        return self._state

    # ------------------------------------------------------------------
    # 命令：select_stock / select_day / cancel / dispose（同步入口）
    # ------------------------------------------------------------------

    def select_stock(self, ts_code: str | None, stock_name: str | None = None) -> None:
        """选中股票：取消在途任务并加载近 N 交易日情绪指数（仅展示，ADR-0008）。"""
        if not ts_code:
            return
        self._cancel_index()
        self._cancel_detail()
        self._set_state(
            ts_code=ts_code,
            stock_name=stock_name or "",
            phase=PHASE_LOADING,
            points=(),
            selected_day=None,
            day_news=(),
            day_news_loading=False,
            message=None,
        )
        loop = self._get_loop_or_none()
        if loop is None:
            self._set_state(phase=PHASE_ERROR, message=Message("sentiment_index_err_no_loop"))
            return
        task = loop.create_task(self._load_index_task(ts_code, stock_name or ""))
        self._index_task = task
        task.add_done_callback(self._on_task_done)

    def select_day(self, day: datetime.date | None) -> None:
        """点击某交易日：展开当日新闻明细；同日再点收起（UI 交互态语义在 VM 保持一致）。"""
        if day is None or not self._state.ts_code or not self._state.points:
            return
        if self._state.selected_day == day:
            # 收起（不取消明细任务：其完成回写有 selected_day 守卫，不会误写）
            self._set_state(selected_day=None, day_news=(), day_news_loading=False, message=None)
            return
        self._cancel_detail()
        self._set_state(selected_day=day, day_news=(), day_news_loading=True, message=None)
        loop = self._get_loop_or_none()
        if loop is None:
            self._set_state(phase=PHASE_ERROR, message=Message("sentiment_index_err_no_loop"))
            return
        task = loop.create_task(self._load_detail_task(self._state.ts_code, self._state.stock_name, day))
        self._detail_task = task
        task.add_done_callback(self._on_task_done)

    def cancel(self) -> None:
        """同步取消：取消在途任务并传播取消（详情弹窗关闭时调用，不在 handler 中 await）。"""
        self._cancel_index()
        self._cancel_detail()

    def dispose(self) -> None:
        """卸载资源：取消在途任务并清理（MVVM 生命周期）。"""
        self.cancel()
        super().dispose()

    # ------------------------------------------------------------------
    # 内部异步任务（子任务，R2：CancelledError 一律 raise）
    # ------------------------------------------------------------------

    async def _load_index_task(self, ts_code: str, stock_name: str) -> None:
        """指数加载子任务（loading → ready/error）。"""
        try:
            points = await self._require_service().get_stock_daily_index(
                ts_code, stock_name or None, days=self._days, as_of=None
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[SentimentIndexVM] load index failed (%s): %s", ts_code, DataSanitizer.sanitize_error(e))
            self._set_state(phase=PHASE_ERROR, message=Message("sentiment_index_err", params={"ts_code": ts_code}))
            return
        self._set_state(
            phase=PHASE_READY,
            points=tuple(_to_point(p) for p in points),
            message=Message("sentiment_index_empty") if not points else None,
        )

    async def _load_detail_task(self, ts_code: str, stock_name: str, day: datetime.date) -> None:
        """当日明细子任务（day_news_loading → 写回；selected_day 已变则丢弃）。"""
        try:
            rows = await self._require_service().get_stock_day_news(ts_code, stock_name or None, day)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[SentimentIndexVM] load day news failed (%s %s): %s",
                ts_code,
                day,
                DataSanitizer.sanitize_error(e),
            )
            if self._state.selected_day != day:
                return  # 用户已切换/收起：迟到失败同样丢弃，不污染当前选日状态
            self._set_state(day_news=(), day_news_loading=False, message=Message("sentiment_index_err"))
            return
        if self._state.selected_day != day:
            return  # 用户已切换/收起：迟到结果丢弃（代次守卫）
        self._set_state(day_news=_rows_to_news_items(rows), day_news_loading=False)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _require_service(self) -> NewsSentimentIndexService:
        """惰性获取服务（R16：构造不在 __init__/UI handler 同步段；DCL 幂等无 IO）。"""
        if self._service is None:
            self._service = NewsSentimentIndexService()
        return self._service

    def _cancel_index(self) -> None:
        """取消指数任务（幂等）。"""
        task = self._index_task
        self._index_task = None
        if task is not None and not task.done():
            task.cancel()

    def _cancel_detail(self) -> None:
        """取消明细任务（幂等）。"""
        task = self._detail_task
        self._detail_task = None
        if task is not None and not task.done():
            task.cancel()

    def _on_task_done(self, task: asyncio.Task) -> None:
        """任务完成回调：清引用并读取异常避免 'Task exception was never retrieved'（R2）。"""
        if self._index_task is task:
            self._index_task = None
        if self._detail_task is task:
            self._detail_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "[SentimentIndexVM] background task failed: %s",
                DataSanitizer.sanitize_error(exc) if isinstance(exc, Exception) else str(exc),
            )
