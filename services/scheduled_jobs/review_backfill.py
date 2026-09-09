"""T+5 复盘延迟回填定时任务（D2-4）。

SchedulerService 仅保留调度与 idempotency 状态；本模块承载 T+5 回填编排：
幂等（只处理 t5_pct IS NULL），可重复调度，无需额外幂等状态。
以 DB 最新交易日为锚（非"今天"），非交易日照常可回填历史。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Awaitable
from typing import TYPE_CHECKING

from core.i18n import I18n, Message
from data.persistence.review_manager import ReviewManager
from services.task_manager import TaskManager
from utils.correlation import ensure_correlation_id

if TYPE_CHECKING:
    from utils.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


async def _run_review_backfill(svc: SchedulerService) -> None:
    """T+5 延迟回填 job：TaskManager 提交 ReviewManager.backfill_horizon_returns。"""
    ensure_correlation_id()

    async def _factory(task_id: str, **kwargs) -> str:
        tm = TaskManager()
        tm.update_progress(task_id, 0.1, Message("sched_review_backfill_start"))
        rm = ReviewManager()
        count = await rm.backfill_horizon_returns()
        tm.update_progress(task_id, 1.0, Message("sched_review_backfill_done", {"count": count}))
        return I18n.get("sched_review_backfill_done", count=count)

    TaskManager().submit_task(
        name=I18n.get("sched_task_review_backfill"),
        task_type=I18n.get("task_type_review"),
        coroutine_factory=_factory,
        cancellable=False,
        unique_key="review_t5_backfill",
    )


def build_review_backfill_job() -> Callable[[SchedulerService], Awaitable[None]]:
    """构造 T+5 回填 job，供 SchedulerService.register_job("review_t5_backfill") 注册。"""

    async def _job(svc: SchedulerService) -> None:
        await _run_review_backfill(svc)

    return _job
