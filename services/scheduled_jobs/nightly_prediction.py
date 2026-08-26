"""夜间 AI 预测定时任务编排（review01-A2-1 下沉自 utils/scheduler_service.py）。

SchedulerService 仅保留调度与 idempotency 状态；本模块承载夜间预测的完整业务编排：
enabled/idempotency/交易日检查 → TaskManager 提交 → AI 选股 → 结果保存。

分层约束：services 禁入 strategies（契约 3 / R1），故 AI 策略执行经 ``AISelectionRunner``
协议由 app 层注入（app 层可合法 import strategies），实现依赖倒置——本模块不感知具体策略类，
消除原 ``utils/scheduler_service.py -> strategies.ai_strategy`` 的方向性违规与隐藏三角依赖。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Awaitable
from typing import TYPE_CHECKING, Protocol

import pandas as pd

from core.i18n import I18n, Message
from data.data_processor import DataProcessor
from data.persistence.review_manager import ReviewManager
from services.task_manager import TaskManager
from utils.config_handler import ConfigHandler
from utils.correlation import ensure_correlation_id
from utils.error_classifier import log_classified
from utils.time_utils import get_now

if TYPE_CHECKING:
    from utils.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


class AISelectionRunner(Protocol):
    """AI 选股执行器（依赖倒置：由 app 层注入的可调用对象）。

    经 ``__call__`` 定义，兼容 async 函数与带 ``run``/``__call__`` 的类实例。
    app 层注入 ``async def _ai_runner(context) -> pd.DataFrame | None`` 即可。
    """

    async def __call__(self, context: dict) -> pd.DataFrame | None: ...


async def _prediction_logic(
    svc: SchedulerService,
    runner: AISelectionRunner,
    today_str: str,
    task_id: str,
    **kwargs,
) -> str:
    """夜间 AI 预测编排：init_data → prepare_market_data → get_strategy_data → runner.run → save_results。

    行为与原 ``SchedulerService._run_nightly_prediction`` 的 ``_prediction_logic`` 闭包一致：
    - Message 透传（E4）：``_ai_progress`` 不拼接 f"[{current}/{total}] " 前缀
    - R.3.1：存储 i18n key ``"strategy_ai_nightly_name"``（非 identifier）
    - idempotency：结果保存成功后经 svc 标记今日已完成
    """
    tm = TaskManager()
    tm.update_progress(task_id, 0.1, Message("sched_pred_init"))
    processor = DataProcessor()
    await processor.init_data()

    tm.update_progress(task_id, 0.2, Message("sched_pred_prepare"))
    await processor.prepare_market_data()

    tm.update_progress(task_id, 0.3, Message("sched_pred_context"))
    context = await processor.get_strategy_data()
    if not context:
        raise RuntimeError(I18n.get("sched_pred_no_context"))
    context["data_processor"] = processor

    tm.update_progress(task_id, 0.5, Message("sched_pred_running"))

    # Inject progress callback so strategy.filter() reports AI analysis sub-progress
    def _ai_progress(current, total, msg):
        # Map to 50%→90% range
        sub_pct = 0.5 + (current / max(total, 1)) * 0.4
        tm.update_progress(task_id, sub_pct, msg)

    context["on_progress"] = _ai_progress

    result_df: pd.DataFrame | None = await runner(context)

    if result_df is not None and not result_df.empty:
        tm.update_progress(task_id, 0.9, Message("sched_pred_saving"))
        rm = ReviewManager()
        analysis_trade_date = context.get("trade_date")
        if not analysis_trade_date:
            raise RuntimeError("Nightly prediction context missing trade_date; refusing to save results")

        run_id = uuid.uuid4().hex[:16]
        # R.3.1: 存储 i18n key (非 identifier)。
        # 这里有意使用 "strategy_ai_nightly_name" 而非 AISelectionStrategy.name_key
        # (= "strategy_ai_active_name")：夜间定时预测与用户交互式 AI 选股是两个
        # 语义场景，UI 上需区分显示（"夜间 AI 预测" vs "AI 主动选股"），非 DRY 违反。
        await rm.save_results(
            "strategy_ai_nightly_name",
            result_df,
            trade_date=analysis_trade_date,
            run_id=run_id,
            params_snapshot={},
        )
        await svc._mark_nightly_prediction_done_db(today_str)
        return I18n.get("sched_pred_done_found", count=len(result_df))

    logger.info("[Scheduler] Nightly prediction found no candidates, NOT marking done to allow retry")
    return I18n.get("sched_pred_done_empty")


async def _run_nightly_prediction(svc: SchedulerService, runner: AISelectionRunner) -> None:
    """夜间 AI 预测 job：enabled/idempotency/交易日检查 + TaskManager 提交。"""
    ensure_correlation_id()

    if not ConfigHandler.is_auto_update_enabled():
        return

    today = get_now().date()
    today_str = today.strftime("%Y%m%d")
    if svc._last_pred_date == today_str:
        return

    try:
        processor = DataProcessor()
        is_trading = await processor.trade_calendar.is_trading_day(today)
        if not is_trading:
            logger.info(
                "[Scheduler] Prediction skipped (%s is not a trading day)",
                today_str,
            )
            return
    except Exception as e:
        log_classified(
            logger,
            e,
            "general",
            "[Scheduler] Trade calendar check failed for prediction (%s): %s",
            exc_info=True,
        )
        if get_now().weekday() >= 5:
            return

    async def _factory(task_id: str, **kwargs) -> str:
        return await _prediction_logic(svc, runner, today_str, task_id, **kwargs)

    TaskManager().submit_task(
        name=I18n.get("sched_task_prediction", date=today_str),
        task_type=I18n.get("task_type_ai_screening"),
        coroutine_factory=_factory,
        cancellable=False,
        unique_key="nightly_prediction",
    )


def build_nightly_prediction_job(runner: AISelectionRunner) -> Callable[[SchedulerService], Awaitable[None]]:
    """构造夜间预测 job，供 SchedulerService.register_job("nightly_prediction") 注册。

    Args:
        runner: AI 选股执行器，由 app 层注入（services 禁入 strategies，契约 3 / R1）。
    """

    async def _job(svc: SchedulerService) -> None:
        await _run_nightly_prediction(svc, runner)

    return _job
