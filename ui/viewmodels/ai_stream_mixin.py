"""ScreenerViewModel AI 流式聚合 mixin（C3-4 拆分）。

从 ``screener_view_model.py`` 按职责拆出：策略筛选编排 ``run_strategy``/``cancel_strategy``、
流式卡片生命周期（``start_stream_card``/``append_stream_chunk``/``_flush_stream_card``/``_on_card_error``）、
AI 批量缓冲 ``_on_ai_result_stream``/``_flush_ai_buffer``、单股重试 ``retry_single_stock``/``schedule_retry``。

本 mixin 无独立状态，依赖宿主 VM 的共享成员（``_state``/``_full_results``/``_ai_buffer``/
``_stream_buffers``/``_set_state`` 等）；跨 mixin 方法（如 ``_update_pagination``、
``_sort_helper``）在组合实例上解析。``_state`` 以类级注解声明以满足类型检查
（沿用 observable_mixin.py 的 mixin 类型化惯例；其余共享成员访问为 pyright warning，
不构成 error 阻断）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import typing
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pandas as pd

from core.errors import StrategyParamError
from data.persistence.quality_gate import QualityGateError
from services.task_manager import TaskManager
from ui.viewmodels import Message
from ui.viewmodels.screener_types import (
    LogEntry,
    ScreenerState,
    StreamCard,
    _MAX_LOG_CARDS,
)
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

if TYPE_CHECKING:
    from data.data_processor import DataProcessor
    from data.persistence.review_manager import ReviewManager
    from strategies.all_strategies import StrategyManager

logger = logging.getLogger(__name__)

# Stream card throttle
# NOTE(lazy): 流式节流 50ms (~20fps) 平衡流畅度与 reconcile 压力. ceiling: 策略结果行数 >5000 时 20fps 可能卡顿. upgrade: 行数突破 ceiling 或用户反馈卡顿时改 33ms/动态节流.
_STREAM_THROTTLE = 0.05  # seconds


class AIStreamMixin:
    """AI 流式聚合职责（C3-4）。组合进 ``ScreenerViewModel``。"""

    AI_UPDATE_INTERVAL = 0.5  # Seconds

    _state: ScreenerState
    _full_results: pd.DataFrame | None
    _ai_buffer: list[dict]
    _discarded_buffer: list[dict]
    _stream_buffers: dict[str, dict]
    _last_ai_update: float
    _flush_pending: bool
    _background_tasks: set[asyncio.Task]
    _strategy_submitted: bool
    _active_task_id: str | None
    _last_ai_context: dict | None
    _last_strategy_key: str | None
    _retrying: bool
    _retrying_name: str | None
    _retrying_prev_error: str | None
    _retry_task: asyncio.Task | None
    strategy_mgr: StrategyManager
    review_mgr: ReviewManager
    _set_state: Callable[..., None]
    _update_pagination: Callable[..., None]
    _sort_helper: Callable[..., Any]
    _ensure_processor: Callable[[], Coroutine[Any, Any, DataProcessor]]
    _get_loop_or_none: Callable[[], asyncio.AbstractEventLoop | None]
    _on_background_task_done: Callable[[asyncio.Task], None]

    def clear_stream_cards(self) -> None:
        """Clear all stream cards and buffers (called on new run)."""
        self._stream_buffers.clear()
        self._set_state(stream_cards=(), stream_cards_truncated=False)

    def start_stream_card(self, name: str, is_analyzing: bool = False) -> None:
        """Create a new stream/placeholder card."""
        self._stream_buffers[name] = {"reasoning": "", "content": "", "last_flush": 0.0, "pending": False}
        card = StreamCard(name=name, is_analyzing=is_analyzing)
        # Task 8.4: 检测截断 — 新增卡片导致超出 _MAX_LOG_CARDS 时标记 truncated
        truncated = len(self._state.stream_cards) + 1 > _MAX_LOG_CARDS
        new_cards = (self._state.stream_cards + (card,))[-_MAX_LOG_CARDS:]
        self._set_state(
            stream_cards=new_cards,
            stream_cards_truncated=self._state.stream_cards_truncated or truncated,
        )

    def append_stream_chunk(self, name: str, chunk: str, is_reasoning: bool) -> None:
        """Accumulate LLM chunk, throttle-flush to state."""
        buf = self._stream_buffers.get(name)
        if not buf:
            return
        if is_reasoning:
            buf["reasoning"] += chunk
        else:
            buf["content"] += chunk
        now = time.time()
        if now - buf["last_flush"] >= _STREAM_THROTTLE:
            self._flush_stream_card(name)
        else:
            buf["pending"] = True

    def finalize_stream_card(self, name: str) -> None:
        """Force flush pending buffer (called by strategy on completion)."""
        buf = self._stream_buffers.get(name)
        if buf and buf.get("pending"):
            self._flush_stream_card(name)

    def _flush_stream_card(self, name: str) -> None:
        """Flush single card buffer to state."""
        buf = self._stream_buffers.get(name)
        if not buf:
            return
        # Guard: card may have been truncated by _MAX_LOG_CARDS; avoid orphan buffer + noop notify
        if not any(c.name == name for c in self._state.stream_cards):
            self._stream_buffers.pop(name, None)
            return
        new_cards = tuple(
            replace(c, reasoning=buf["reasoning"], content=buf["content"], is_analyzing=False) if c.name == name else c
            for c in self._state.stream_cards
        )
        self._set_state(stream_cards=new_cards)
        buf["last_flush"] = time.time()
        buf["pending"] = False

    def _on_stream_start_adapter(self, name: str):
        """Adapter for strategy's on_stream_start contract (returns on_chunk closure)."""
        self.start_stream_card(name, is_analyzing=False)

        def _on_chunk(chunk_text: str, is_reasoning: bool = False) -> None:
            self.append_stream_chunk(name, chunk_text, is_reasoning)

        _on_chunk.final_flush = lambda: self.finalize_stream_card(name)  # type: ignore[attr-defined]  # [reason: ai_mixin.py:576 用 hasattr 检查 final_flush]
        return _on_chunk

    def _on_card_start_adapter(self, name: str) -> None:
        """Adapter for strategy's on_card_start contract."""
        self.start_stream_card(name, is_analyzing=True)

    def _on_card_error(self, name: str, error: str) -> None:
        """UX-2.3: 单股 AI 分析失败时终结占位卡为错误状态。"""
        new_cards = tuple(
            replace(c, error=error, is_analyzing=False) if c.name == name and c.is_analyzing else c
            for c in self._state.stream_cards
        )
        self._set_state(stream_cards=new_cards)

    async def retry_single_stock(self, name: str) -> None:
        """UX-2.3: 重试单股 AI 分析。

        流程：防抖检查 → 策略一致性检查 → 移除失败卡片 → 构建新 context → 调用策略 retry_single。
        """
        # 1. 防抖：重试中拒绝再次触发
        if self._retrying:
            logger.debug("[ScreenerVM] retry_single_stock: already retrying, skip")
            return
        # 2. 策略一致性检查：策略切换后 _last_candidates_df 已失效
        if self._last_strategy_key != self._state.selected_strategy:
            self._set_state(
                status_message=Message("ai_retry_strategy_changed"),
                status_color="warning",
            )
            return
        if not self._last_ai_context:
            return
        # R1-6: 策略能力检查前移到卡片转换之前，避免无 retry_single 时卡片卡在 is_analyzing
        strategy = self.strategy_mgr.get_strategy(self._state.selected_strategy)
        if not strategy or not hasattr(strategy, "retry_single"):
            logger.warning(
                "[ScreenerVM] retry_single_stock: strategy %s has no retry_single",
                self._state.selected_strategy,
            )
            return  # 卡片保持 error 状态，用户可切换策略或重试
        # UX-2.3 v4 P0-1: 将失败卡转为重试中占位卡（不删除，避免 on_card_error 找不到目标卡片）
        # 重试成功由 on_result 自然终结；重试失败由 on_card_error 重新标记 error
        # P1-1: 记录重试前的原始错误文案，供 select_strategy 取消重试时还原（VM 不感知 locale, §3.2）
        self._retrying_prev_error = next(
            (c.error for c in self._state.stream_cards if c.name == name and c.error), None
        )
        new_cards = tuple(
            replace(c, error=None, is_analyzing=True) if c.name == name and c.error else c
            for c in self._state.stream_cards
        )
        # UX-2.3: is_retrying 进 state，View 派生 run_disabled 禁用主运行按钮
        self._set_state(stream_cards=new_cards, is_retrying=True)
        self._retrying = True
        self._retrying_name = name  # P1-1: 记录重试中的占位卡名，供 select_strategy 取消时终结
        # 4. 构建新 context（替换 _task_id 和 on_progress，避免污染原批次）
        retry_context = dict(self._last_ai_context)
        retry_context["_task_id"] = None  # 不关联 TaskManager
        retry_context["on_progress"] = lambda c, t, m: None  # noop，不更新进度
        retry_context["strategy_key"] = self._last_strategy_key  # 透传给 retry_single
        # on_result / on_card_start / on_card_error 保持原回调（更新 StreamCard）
        # 5. 调用策略重试
        try:
            await strategy.retry_single(name, retry_context)
        except asyncio.CancelledError:
            raise  # R2 合规（占位卡终结由 select_strategy 取消路径处理）
        except Exception as e:
            logger.error("[ScreenerVM] retry_single_stock failed: %s", e, exc_info=True)
            # A-1: retry_single 抛异常时（含 try 块之前的异常）恢复卡片为 error 状态避免假死
            # _on_card_error 仅更新 is_analyzing=True 的卡片，成功路径不受影响
            self._on_card_error(name, DataSanitizer.sanitize_error(e))
            self._set_state(
                status_message=Message("ai_retry_failed"),
                status_color="error",
            )
        finally:
            self._retrying = False
            self._retrying_name = None
            self._set_state(is_retrying=False)

    def schedule_retry(self, name: str) -> None:
        """UX-2.3: 调度单股重试，task 加入 _background_tasks 跟踪（VM dispose 时自动取消）。

        同步签名以满足 Flet on_click 回调契约；内部经 loop.create_task 提交，
        task 加入 _background_tasks + _on_background_task_done 跟踪生命周期。
        """
        if self._retrying:
            return
        loop = self._get_loop_or_none()
        if loop is None:
            return  # 无事件循环（测试环境/已 disposed），静默跳过
        task = loop.create_task(self.retry_single_stock(name))
        self._retry_task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    def cancel_retry(self) -> None:
        """UX-2.3: 取消当前正在执行的单股重试任务并清理重试状态上下文。"""
        if not self._retrying:
            return
        if self._retry_task is not None and not self._retry_task.done():
            self._retry_task.cancel()
        self._retry_task = None
        self._retrying = False
        # P1-1: 取消重试后终结占位卡（否则 is_analyzing=True 卡永久停留在"分析中"旋转假死）。
        # 仅当确有重试中的占位卡名时还原为错误态，后续 on_result/on_card_error 均不会再来。
        # 还原重试前的原始错误文案（VM 不感知 locale，§3.2），不调用 I18n。
        if self._retrying_name:
            self._on_card_error(self._retrying_name, self._retrying_prev_error or "screener_ai_incomplete")
        self._retrying_name = None
        self._retrying_prev_error = None
        # 清空重试上下文（防止 retry_single 完成后回调污染新策略）
        self._last_ai_context = None
        self._last_strategy_key = None
        self._set_state(is_retrying=False)

    # --- AI Streaming Handlers ---

    def _on_ai_progress(self, current, total, msg):
        # D7: msg 为 data 层 ai_mixin 的 Message (key+params), VM 只透传 key:
        # msg_key 走 *_key 约定由 View 翻译, 内层 params (done/total) 平铺供模板填入.
        params: dict[str, Any] = {"done": current, "total": total}
        if isinstance(msg, Message):
            params["msg_key"] = msg.key
            params.update(msg.params)
        else:  # 兜底: 非 Message 路径 (向后兼容)
            params["msg"] = msg
        self._set_state(
            status_message=Message("screener_ai_analyzing", params),
            status_color="info",
            status_action_key=None,
        )

    def _on_ai_result_stream(self, row_data):
        """Buffer incoming AI results and update in batches"""
        if not row_data:
            return

        # 1. Update Log immediately (append-only tuple in state, §3.2 H5)
        name = row_data.get("name", "Unknown")
        score = row_data.get("ai_score", 0)
        thinking = str(row_data.get("thinking", ""))
        entry = LogEntry(name=name, score=score, thinking=thinking)
        new_logs = self._state.logs + (entry,)

        # Task 3.1: 终结并发模式占位卡 (concurrency>1, is_analyzing=True).
        # _on_card_start_adapter 在并发模式创建 is_analyzing=True 占位卡; 结果到达时
        # 写入最终 reasoning/content 并 is_analyzing=False, 避免占位卡假死.
        # 流式卡 (is_analyzing=False, concurrency=1) 不受影响: 其内容由 chunk 流式写入.
        content = str(row_data.get("ai_reason", ""))
        new_cards = tuple(
            replace(c, reasoning=thinking, content=content, is_analyzing=False)
            if c.name == name and c.is_analyzing
            else c
            for c in self._state.stream_cards
        )
        # 合并为单次 _set_state：2 次 state 写 + 1 次 notify → 1 次 replace + 1 次 _notify
        # (修复 Skeptic P2 #5：语义等价，notify 次数从 2 次潜在可能性收敛为 1 次)
        if new_cards != self._state.stream_cards:
            self._set_state(logs=new_logs, stream_cards=new_cards)
        else:
            self._set_state(logs=new_logs)

        # 2. Buffer for Table Update
        self._ai_buffer.append(row_data)

        now = time.time()
        if now - self._last_ai_update > self.AI_UPDATE_INTERVAL or len(self._ai_buffer) >= 20:
            # Trigger Batch Update
            # Note: We trigger a task to run the update on main thread context eventually,
            # but here we are likely in a background thread from AI Strategy?
            # Actually AI Strategy runs awaitable, so we are in async context.
            # We can't await here directly if this is called synchronously.
            # But on_result is usually called from async loop.

            # Schedule update if not already pending
            if not self._flush_pending:
                self._flush_pending = True
                # 统一通过 Mixin 的 lazy getter 获取 loop（架构 P1-2 修复）
                loop = self._get_loop_or_none()
                if loop is not None and loop.is_running():
                    task = loop.create_task(self._flush_ai_buffer())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._on_background_task_done)
                else:
                    # 同步上下文（无 loop）场景：同步执行，不创建 task
                    # （通常发生在单测中）
                    self._flush_pending = False
                    logger.warning("[ScreenerVM] Cannot schedule flush: no running loop; sync flush skipped")

    async def _flush_ai_buffer(self):
        """Flush buffer to main DataFrame"""
        try:
            if not self._ai_buffer:
                return
            # U-3 fix: Race guard - save buffer to discarded_buffer if user has switched to history mode
            if self._state.mode != "REALTIME":
                self._discarded_buffer.extend(self._ai_buffer)
                self._ai_buffer = []
                self._flush_pending = False
                logger.debug(
                    "[ScreenerVM] Saved %s items to discarded_buffer during HISTORY mode",
                    len(self._discarded_buffer),
                )
                return

            # Swap buffer to process safely
            current_batch = self._ai_buffer
            self._ai_buffer = []

            new_df = pd.DataFrame(current_batch)

            # Offload Concatenation
            if self._full_results is None or self._full_results.empty:
                self._full_results = new_df
            else:
                # Append
                self._full_results = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    pd.concat,
                    [self._full_results, new_df],
                    ignore_index=True,
                )

            # Sort by Score (Best on top)
            if "ai_score" in self._full_results.columns:
                self._full_results = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._sort_helper,
                    self._full_results,
                    "ai_score",
                    False,
                )

                # Pin ai_score and ai_reason to the front (after name)
                # Ensure ai_reason column exists (some AI results may only return score)
                if "ai_reason" not in typing.cast("pd.DataFrame", self._full_results).columns:
                    typing.cast("pd.DataFrame", self._full_results)["ai_reason"] = ""
                cols = list(self._full_results.columns)  # type: ignore[untyped]
                # Remove if exists
                if "ai_score" in cols:
                    cols.remove("ai_score")
                if "ai_reason" in cols:
                    cols.remove("ai_reason")

                # Find insertion index (after 'name', or else at idx 1)
                insert_idx = cols.index("name") + 1 if "name" in cols else 1

                # Insert back
                cols.insert(insert_idx, "ai_score")
                cols.insert(insert_idx + 1, "ai_reason")

                self._full_results = self._full_results[cols]  # type: ignore[untyped]
            # C2b H1: 数据内容变更后经唯一 owner 原子产出分页元数据 + 当前页切片
            self._update_pagination()

            self._last_ai_update = time.time()

        except Exception as e:
            logger.error("Error flushing AI buffer: %s", DataSanitizer.sanitize_error(e), exc_info=True)
        finally:
            self._flush_pending = False

    # --- Strategy execution orchestration (TaskManager) ---

    async def run_strategy(
        self,
        strategy_key: str,
        save_results: bool = True,
        params: dict | None = None,  # type: ignore[untyped]
    ):
        """Execute strategy screening via the global TaskManager."""
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        self.clear_stream_cards()

        strategy = self.strategy_mgr.get_strategy(strategy_key)
        if not strategy:
            logger.error("[ScreenerVM] Strategy not found: %s", strategy_key)
            self._set_state(
                status_message=Message("screener_strategy_not_found"),
                status_color="error",
                status_action_key=None,
            )
            return

        # Define the inner coroutine for the task manager
        async def _execute_screening(task_id: str, **kwargs):
            try:
                # 1. Prepare Context (may trigger massive data load)
                # 首次筛选在此懒异步构造 DataProcessor（IO 线程池 offload，R16）
                dp = await self._ensure_processor()
                TaskManager().update_progress(
                    task_id,
                    0.05,
                    Message("task_loading_data"),
                )
                context = await dp.get_strategy_data()
                if not context:
                    TaskManager().update_progress(
                        task_id,
                        0.1,
                        Message("task_cache_empty_init"),
                    )
                    await dp.init_data()
                    context = await dp.get_strategy_data()

                if not context or "screening_data" not in context or context["screening_data"].empty:
                    raise RuntimeError("No valid screening data available")

                diagnostics = context.get("_diagnostics") if isinstance(context, dict) else None
                if isinstance(diagnostics, dict) and diagnostics.get("strategy_ready") is False:
                    table_status = diagnostics.get("table_status") or {}
                    not_ready = [
                        key
                        for key, status in table_status.items()
                        if isinstance(status, dict) and not status.get("ready", True)
                    ]
                    if not_ready:
                        self._set_state(
                            status_message=Message(
                                "strategy_dep_degraded_detail",
                                {"tables": ", ".join(not_ready)},
                            ),
                            status_color="warning",
                            status_action_key=None,
                        )
                    else:
                        self._set_state(
                            status_message=Message("strategy_dep_degraded"),
                            status_color="warning",
                            status_action_key=None,
                        )

                context["data_processor"] = dp
                context["params"] = params or {}  # Dynamic strategy parameters from UI

                # Setup AI Callbacks
                # (Forward updates both to ViewModel local UI and Global TaskManager)
                def _combined_ai_progress(current, total, msg):
                    self._on_ai_progress(current, total, msg)  # For local View UI
                    # D7: msg 为 Message, 直接透传 (不再拼 "[c/t] " 前缀, 防 dataclass repr)
                    TaskManager().update_progress(
                        task_id,
                        current / total if total > 0 else 0,
                        msg,
                    )

                context["on_progress"] = _combined_ai_progress
                context["on_result"] = self._on_ai_result_stream
                context["on_stream_start"] = self._on_stream_start_adapter
                context["on_card_start"] = self._on_card_start_adapter
                # UX-2.3: 单股失败回调 + 策略 key 透传给 mixin
                context["on_card_error"] = self._on_card_error
                context["strategy_key"] = strategy_key
                self._last_ai_context = context
                self._last_strategy_key = strategy_key

                # We inject the task_id into context so deep AI tasks can check cancellation
                context["_task_id"] = task_id

                TaskManager().update_progress(
                    task_id,
                    0.2,
                    Message("task_executing_strategy", {"name_key": strategy.name_key}),
                )

                if inspect.iscoroutinefunction(strategy.filter):
                    # Async strategy (e.g. PolarsBaseStrategy) — CPU-intensive work
                    # is already offloaded inside the strategy's filter() method,
                    # so awaiting here only blocks for IO (thread pool result, AI API calls)
                    result_df = await strategy.filter(context)
                else:
                    # Sync strategy — offload entire filter() to CPU thread pool
                    result_df = await ThreadPoolManager().run_async(
                        TaskType.CPU,
                        strategy.filter,
                        context,
                    )

                TaskManager().update_progress(
                    task_id,
                    0.95,
                    Message("task_aggregating_results"),
                )

                if result_df is not None and not result_df.empty:
                    self._full_results = result_df
                    self._update_pagination(page_no=1)

                    # Task 3.3: save_results 失败不再落入 screener_exec_error.
                    # 结果已写入 _full_results 照常上屏, 状态栏提示「未保存：原因」.
                    # trade_date 缺失属于程序错误 (context 协议违规), 仍 raise.
                    save_failed_reason: str | None = None
                    if save_results:
                        analysis_trade_date = context.get("trade_date")
                        if not analysis_trade_date:
                            raise RuntimeError(
                                "Missing analysis trade_date in screening context; refusing to save results",
                            )
                        import uuid as _uuid

                        run_id = _uuid.uuid4().hex[:16]
                        try:
                            await self.review_mgr.save_results(
                                strategy.name_key,
                                result_df,
                                trade_date=analysis_trade_date,
                                run_id=run_id,
                                params_snapshot=params or {},
                            )
                        except Exception as save_err:
                            # 必须捕获 Exception 而非 BaseException, 保留 CancelledError
                            # 传播 (R2 红线).
                            logger.error(
                                "[ScreenerVM] save_results failed (results retained in memory): %s",
                                DataSanitizer.sanitize_error(save_err),
                                exc_info=True,
                            )
                            save_failed_reason = DataSanitizer.sanitize_error(save_err) or save_err.__class__.__name__

                    if save_failed_reason is not None:
                        self._set_state(
                            loading=False,
                            status_message=Message(
                                "screener_done_unsaved",
                                {
                                    "count": len(result_df),
                                    "reason": save_failed_reason,
                                },
                            ),
                            status_color="warning",
                            status_action_key=None,
                        )
                    else:
                        self._set_state(
                            loading=False,
                            status_message=Message(
                                "screener_done_saved",
                                {"count": len(result_df)},
                            ),
                            status_color="success",
                            status_action_key=None,
                        )
                    return Message("task_screening_success", {"count": len(result_df)})

                self._full_results = pd.DataFrame()
                # C2b H1: 无结果退出路径经唯一 owner 单帧原子产出 (空切片 + loading=False + 提示),
                # 避免分两帧触发重复通知 (第 3 轮对抗检视)
                self._update_pagination(
                    page_no=1,
                    loading=False,
                    status_message=Message("screener_no_results"),
                    status_color="warning",
                    status_action_key=None,
                )
                return Message("screener_no_results")

            except asyncio.CancelledError:
                self._set_state(
                    loading=False,
                    status_message=Message("screener_cancelled"),
                    status_color="warning",
                    status_action_key=None,
                )
                raise
            except QualityGateError as e:
                logger.warning(
                    "[ScreenerVM] Strategy execution blocked by Quality Gate: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                self._set_state(
                    loading=False,
                    status_message=Message("screener_blocked", {"reason": DataSanitizer.sanitize_error(e)}),
                    status_color="warning",
                    # Task 5.2: 附 "前往同步" 跳转 action 供 View 渲染按钮
                    status_action_key="screener_action_go_sync",
                )
                return Message("screener_blocked", {"reason": DataSanitizer.sanitize_error(e)})
            except StrategyParamError as e:
                logger.warning(
                    "[ScreenerVM] Invalid strategy parameter: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                self._set_state(
                    loading=False,
                    status_message=e.message,
                    status_color="warning",
                    status_action_key=None,
                )
                return e.message
            except Exception as e:
                logger.error(
                    "[ScreenerVM] Strategy execution failed: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                # Show generic user-friendly message, avoid raw traceback on UI
                self._set_state(
                    loading=False,
                    status_message=Message("screener_exec_error"),
                    status_color="error",
                    status_action_key=None,
                )
                raise RuntimeError(f"Strategy execution crashed: {DataSanitizer.sanitize_error(e)}") from e
            finally:
                self._active_task_id = None  # Task 3.2: 所有退出路径清空, 防止误取消已结束的 task

        # Reset Local UI State
        self._full_results = None
        self._ai_buffer = []
        # C2b H1: 清空结果后经唯一 owner 单帧原子产出 (空切片 + loading=True + 状态消息),
        # 避免「loading=False + 空切片」导致 View 闪烁渲染 EmptyState (第 3 轮对抗检视)
        self._update_pagination(
            page_no=1,
            loading=True,
            # §3.2: VM 只产出 i18n key (name_key), View 渲染时翻译为当前 locale 策略名.
            # 避免 VM 持有翻译字符串导致 locale 切换后 state 残留旧 locale 翻译.
            status_message=Message(
                "screener_running_strategy",
                {"name_key": strategy.name_key},
            ),
            status_color="info",
            # Task 5.2: 重置上一次 QualityGateError 残留的 action key
            status_action_key=None,
        )

        # Dispatch to TaskManager!
        # Task 3.1: name 改为 Message (复用 screener_running_strategy key + name_key params),
        # task_type 也是 Message. _on_tasks_updated 通过 task_type.key 检测策略任务 (替代
        # 旧 TASK_NAME_PREFIX in t.name 字符串检测, 因 t.name 现为 Message 实例不支持 `in`).
        task_id = TaskManager().submit_task(
            name=Message("screener_running_strategy", {"name_key": strategy.name_key}),
            task_type=Message("task_type_ai_screening"),
            coroutine_factory=_execute_screening,
            cancellable=True,
        )

        if task_id is None:
            self._set_state(
                loading=False,
                status_message=Message("screener_task_rejected"),
                status_color="warning",
                status_action_key=None,
            )
        else:
            self._strategy_submitted = True
            self._active_task_id = task_id  # Task 3.2: 保存供 cancel_strategy

    def cancel_strategy(self) -> None:
        """Task 3.2: 取消正在运行的选股策略任务 (本页取消).

        线程安全: TaskManager.cancel_task 通过 call_soon_threadsafe 调度到事件循环,
        可在 Flet 同步 handler 中直接调用 (R16 不适用: 无 IO/CPU 阻塞).
        """
        if self._active_task_id is not None:
            TaskManager().cancel_task(self._active_task_id)
