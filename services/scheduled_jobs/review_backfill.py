"""T+1 / T+5 延迟回填定时任务（D2-4 / BIZ-03）。

SchedulerService 每个交易日固定触发一次，对本任务无需 idempotency 标记——
``ReviewManager.backfill_t1_returns`` 只回填 ``t1_pct IS NULL`` 的记录、
``backfill_horizon_returns`` 只处理 ``review_status='T1_DONE'`` 且
（缺 ``t5_pct`` 数值或标签未定稿，RV-04）的记录，均天然幂等；
每日运行是刚需（新预测逐日满足 1 / 5 个交易日成熟）。

顺序约束（BIZ-03）：必须先 T+1 后 T+5。T+1 回填把 PENDING 推进到 T1_DONE 后，
T+5 回填通道（只取 ``review_status='T1_DONE'``）才能取到该记录；反向执行会让
T+5 通道漏掉当日刚由 T+1 推进的记录。

RV-04: 经 TaskManager 提交（对齐 nightly_prediction 先例），任务结果字符串含
回填计数与基准诊断（降级/缺失说明）——此前基准缺失仅 logger.warning，
用户在任务中心完全不可见（检视报告 RV-04 建议修复 2）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from core.i18n import I18n
from data.persistence.review_manager import ReviewManager
from services.task_manager import TaskManager
from utils.correlation import ensure_correlation_id
from utils.error_classifier import log_classified
from utils.time_utils import get_now

if TYPE_CHECKING:
    from utils.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


async def _review_backfill_logic(svc: SchedulerService, task_id: str, **kwargs) -> str:
    """回填逻辑：先 T+1（打标签依据），再 T+5（远期收益），最后常驻过期清扫（RV-11）。

    后置过期清扫理由：backfill 先尝试补数据（行情晚到可回填），expire 再清理
    仍未填的超窗 PENDING——次序不可颠倒（先 expire 会误伤本可回填的行）。
    返回结果描述供任务中心展示。
    """
    try:
        rm = ReviewManager()
        t1_count = await rm.backfill_t1_returns()
        t5_count = await rm.backfill_horizon_returns()
        expired_count = await rm.expire_stale_pending()
        result = f"T+1 backfilled: {t1_count}, T+5 backfilled: {t5_count}"
        # RV-11: 清理了僵尸 PENDING 时拼一句用户可见说明（对齐 RV-04 可见诊断原则），
        # 避免「复盘悄然消失而不自知」。
        if expired_count:
            result = f"{result}, stale expired: {expired_count}"
        # RV-04: 基准降级/缺失诊断拼进任务结果（用户可见），不再只有日志 warning。
        if rm._benchmark_diag:
            result = f"{result} — {rm._benchmark_diag}"
        logger.info(
            "[Scheduler] Review backfill completed: T+1=%s, T+5=%s records updated, stale expired=%s.",
            t1_count,
            t5_count,
            expired_count,
        )
        return result
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Scheduler] Review backfill job failed (%s): %s",
            exc_info=True,
        )
        raise


async def _run_review_backfill(svc: SchedulerService) -> None:
    """延迟回填 job：TaskManager 提交（结果对用户可见），实际逻辑在 coroutine_factory 内。"""
    ensure_correlation_id()

    today_str = get_now().strftime("%Y%m%d")

    async def _factory(task_id: str, **kwargs) -> str:
        return await _review_backfill_logic(svc, task_id, **kwargs)

    TaskManager().submit_task(
        name=I18n.get("sched_task_review_backfill", date=today_str),
        task_type=I18n.get("task_type_data_sync"),
        coroutine_factory=_factory,
        cancellable=False,
        unique_key="review_backfill",
    )


def build_review_backfill_job() -> Callable[[SchedulerService], Awaitable[None]]:
    """构造回填 job，供 SchedulerService.register_job("review_backfill") 注册。"""

    async def _job(svc: SchedulerService) -> None:
        await _run_review_backfill(svc)

    return _job
