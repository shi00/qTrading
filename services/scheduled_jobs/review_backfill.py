"""T+1 / T+5 延迟回填定时任务（D2-4 / BIZ-03）。

SchedulerService 每个交易日固定触发一次，对本任务无需 idempotency 标记——
``ReviewManager.backfill_t1_returns`` 只回填 ``t1_pct IS NULL`` 的记录、
``backfill_horizon_returns`` 只回填 ``t5_pct IS NULL`` 的记录，均天然幂等；
每日运行是刚需（新预测逐日满足 1 / 5 个交易日成熟）。

顺序约束（BIZ-03）：必须先 T+1 后 T+5。T+1 回填把 PENDING 推进到 T1_DONE 后，
T+5 回填通道（只取 ``review_status='T1_DONE'`` 且 ``t5_pct IS NULL``）才能取到
该记录；反向执行会让 T+5 通道漏掉当日刚由 T+1 推进的记录。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from data.persistence.review_manager import ReviewManager
from utils.error_classifier import log_classified

if TYPE_CHECKING:
    from utils.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


async def _run_review_backfill(svc: SchedulerService) -> str:
    """延迟回填 job：先 T+1（打标签依据），再 T+5（远期收益），返回合计回填描述。"""
    try:
        rm = ReviewManager()
        t1_count = await rm.backfill_t1_returns()
        t5_count = await rm.backfill_horizon_returns()
        logger.info(
            "[Scheduler] Review backfill completed: T+1=%s, T+5=%s records updated.",
            t1_count,
            t5_count,
        )
        return f"T+1 backfilled: {t1_count}, T+5 backfilled: {t5_count}"
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Scheduler] Review backfill job failed (%s): %s",
            exc_info=True,
        )
        raise


def build_review_backfill_job() -> Callable[[SchedulerService], Awaitable[None]]:
    """构造回填 job，供 SchedulerService.register_job("review_backfill") 注册。"""

    async def _job(svc: SchedulerService) -> None:
        await _run_review_backfill(svc)

    return _job
