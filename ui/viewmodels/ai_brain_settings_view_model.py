"""AIBrainSettingsViewModel — AIBrainTab 调优参数与 Prompt 编排 ViewModel
(Task 5.2 + S5 拆分, reviews/模型统一.md §11.1)。

承担 AIBrainTab 中仍归本页的配置：
- AI 调优参数 (max_candidates / min_turnover)
- AI / 新闻提示词 (ai_prompt / news_prompt)
- 「仅本地模式」开关 (ai_local_only_mode)
- 三阶段保存状态机 (idle → saving → success / error)

S5 变更（§11.1）：模型连接 / 场景配置 / 使用记录三类配置及其校验持久化已迁出本 VM，
改由 ``ui/components/model_management_panel.py`` 的 ModelManagementPanel 与三个专用
ViewModel（ModelConnectionsViewModel / ModelRoutesViewModel / ModelUsageViewModel）承担。
本 VM 因此不再构造注入 LLM/failover/local 子 VM，不再持有模型字段、月度成本字段、
新闻情绪打分配置字段与相应命令；save_ai_settings 只持久化本页仍负责的字段。

设计要点：
- frozen state snapshot (AIBrainSettingsState dataclass)
- 三阶段保存状态机: idle → saving → success / stale / error
- 草稿编辑版本 (draft_revision) 单调递增；保存成功只确认发起时捕获的版本，
  发起后又被编辑时完成态为 SAVE_STALE，不把当前草稿伪装成全部已保存
- 同步阻塞 ConfigHandler 写入通过 ThreadPoolManager.run_async offload (R16)
- R2: asyncio.CancelledError 显式 raise, 不被 except Exception 吞没
- 重复提交检测：save_state="saving" 时拒绝新提交
- 取消生命周期 (UIB-AIBRAIN-01)：外层保存等待被取消时, 持久化 task 经
  asyncio.shield 继续执行至写盘真正终结；占用 (SAVE_SAVING) 由持久化完成
  回调收束为非 saving 后才释放——写盘未终结前重复提交守卫持续生效,
  防并发第二次写与旧写覆盖新写；取消的保存不推进 saved_revision（不伪成功）

不感知 locale：状态字段为字符串/布尔值，View 渲染时按当前 locale 翻译。
"""

import asyncio
import logging
from dataclasses import dataclass

from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


# --- Validation bounds ---
_MAX_CANDIDATES_MIN = 1
_MAX_CANDIDATES_MAX = 500
_MIN_TURNOVER_MIN = 0.0
_MIN_TURNOVER_MAX = 100.0

# 三阶段保存状态机
SAVE_IDLE = "idle"
SAVE_SAVING = "saving"
SAVE_SUCCESS = "success"
SAVE_ERROR = "error"
# F05: 发起保存的版本已落盘，但保存完成后当前草稿又被编辑（仅部分反馈，
# 不得以 SAVE_SUCCESS 对当前草稿报「全部已保存」）。
SAVE_STALE = "stale"

# F05: 保存完成时草稿版本已前移的 i18n key（VM 只产出 key，View 按 locale 渲染）。
SAVED_STALE_MESSAGE_KEY = "settings_ai_saved_stale"


@dataclass(frozen=True)
class AIBrainSettingsState:
    """AIBrainSettingsViewModel 的不可变 state snapshot。"""

    max_candidates_value: str = "30"
    min_turnover_value: str = "2.0"
    ai_prompt_value: str = ""
    news_prompt_value: str = ""
    save_state: str = SAVE_IDLE
    # SEC-03 第一步: 「仅本地模式」开关（禁用全部云端 LLM，仅使用本地模型）
    ai_local_only_mode: bool = False
    # Phase 3.2 P1-1: 保存结果 i18n key (非空时 View 显示 WARNING snack)
    warning_message: str = ""
    # F05: 本页草稿的单调编辑版本；任一本页输入变化后 +1。
    draft_revision: int = 0
    # F05: 最近一次成功保存所确认的草稿版本（draft_revision != saved_revision 即当前草稿有未保存修改）。
    saved_revision: int = 0


class AIBrainSettingsViewModel(ObservableViewModelMixin[AIBrainSettingsState]):
    """AIBrainTab 调优参数 + Prompt 编排 ViewModel（S5 拆分后）。

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (AIBrainSettingsState) via subscribe/_set_state
    - 三阶段保存状态机: idle → saving → success / stale / error
    - 草稿编辑版本 (draft_revision) 与最近确认版本 (saved_revision) 分离
    - 同步阻塞 ConfigHandler/ThreadPoolManager IO 通过 ThreadPoolManager.run_async offload (R16)
    - R2: CancelledError 显式 raise, 不被 except Exception 吞没
    - 重复提交检测：save_state="saving" 时拒绝新提交；取消路径下占用由持久化
      完成回调收束（写盘真正终结后才释放），无永久锁定 (UIB-AIBRAIN-01)

    消费方 (AIBrainTab) 通过 use_viewmodel(factory=AIBrainSettingsViewModel) 实例化。
    """

    def __init__(self) -> None:
        self._state = AIBrainSettingsState()
        self._init_mixin_fields()
        self._load_config_to_state()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载本页仍负责的配置到 state（同步, 仅在 __init__ 调用一次）。"""
        self._state = AIBrainSettingsState(
            max_candidates_value=str(ConfigHandler.get_ai_max_candidates()),
            min_turnover_value=str(ConfigHandler.get_strategy_min_turnover()),
            ai_prompt_value=ConfigHandler.get_ai_system_prompt(),
            news_prompt_value=ConfigHandler.get_ai_news_prompt(),
            save_state=SAVE_IDLE,
            ai_local_only_mode=ConfigHandler.is_ai_local_only_mode(),
        )

    # --- Update commands (View 通过 set_* 更新本地 state) ---

    def _apply_draft_edit(self, **changes: str | bool) -> None:
        """应用本页草稿编辑：递增编辑版本并复位保存反馈。

        - 仅字段值实际变化时递增 ``draft_revision``（同值重复 set 不制造脏状态）；
        - 保存进行中保持 ``SAVE_SAVING``（保留在途反馈与重复提交检测），
          完成时由 ``save_ai_settings`` 比对发起版本；
        - 其余状态统一切回 ``SAVE_IDLE`` 并清空 ``warning_message``，
          即任一本页编辑都会清除 SAVE_SUCCESS / SAVE_STALE / SAVE_ERROR 反馈。
        """
        current = self._state
        changed = any(getattr(current, name) != value for name, value in changes.items())
        self._set_state(
            **changes,
            draft_revision=current.draft_revision + 1 if changed else current.draft_revision,
            save_state=SAVE_SAVING if current.save_state == SAVE_SAVING else SAVE_IDLE,
            warning_message="",
        )

    def set_max_candidates_value(self, value: str) -> None:
        self._apply_draft_edit(max_candidates_value=value)

    def set_min_turnover_value(self, value: str) -> None:
        self._apply_draft_edit(min_turnover_value=value)

    def set_ai_prompt_value(self, value: str) -> None:
        self._apply_draft_edit(ai_prompt_value=value)

    def set_news_prompt_value(self, value: str) -> None:
        self._apply_draft_edit(news_prompt_value=value)

    def set_ai_local_only_mode(self, value: bool) -> None:
        self._apply_draft_edit(ai_local_only_mode=bool(value))

    # --- 验证 (阶段 1) ---

    def _validate_all(self) -> tuple[bool, str]:
        """验证调优参数输入, 返回 (是否有效, 错误 i18n key)。"""
        max_cand_str = (self._state.max_candidates_value or "").strip()
        min_turn_str = (self._state.min_turnover_value or "").strip()
        if not max_cand_str or not min_turn_str:
            return False, "ai_snack_fields_empty"
        try:
            max_cand = int(max_cand_str)
            if not (_MAX_CANDIDATES_MIN <= max_cand <= _MAX_CANDIDATES_MAX):
                return False, "ai_snack_invalid_range"
            min_turn = float(min_turn_str)
            if not (_MIN_TURNOVER_MIN <= min_turn <= _MIN_TURNOVER_MAX):
                return False, "ai_snack_invalid_range"
        except (ValueError, TypeError):
            return False, "ai_snack_param_err"
        return True, ""

    # --- 三阶段保存状态机 (validate → persist → reload) ---

    async def save_ai_settings(self) -> bool:
        """三阶段保存本页配置 (调优参数 + prompts + 仅本地模式)。

        链路:
        1. **验证**: 校验 max_candidates / min_turnover 范围
        2. **持久化**:
           - ConfigHandler.save_ai_tuning_and_prompts(...)
           调优参数与 prompts 合并为单次原子 save_config (T3)：
           prompt 校验失败或磁盘写入失败时整体未提交，不存在
           部分成功（原三次独立写链路已废弃）。
           （同步阻塞 IO 经 ThreadPoolManager IO 池执行, R16）
        3. **重载**: AIService().reload_config()

        保存成功只确认**发起时捕获的草稿版本**：若发起后用户又编辑了本页
        （local-only / 调优参数 / prompt），完成态为 ``SAVE_STALE`` 且保留
        当前草稿的未保存状态，不会对当前草稿报「全部已保存」。

        取消生命周期 (UIB-AIBRAIN-01)：外层等待被取消时只终止等待——写盘线程
        无法被外部中断（run_in_executor 固有语义），持久化在独立 task 中经
        ``asyncio.shield`` 继续至真正终结，占用（SAVE_SAVING）由其完成回调
        收束为非 saving 状态后才释放（写盘未终结前重复提交守卫持续生效，
        防并发第二次写与旧写覆盖新写）。取消的保存不推进 ``saved_revision``
        （不宣称已写入），收束态为 ``SAVE_ERROR``。

        Returns:
            True 发起版本的原子保存与重载成功（若完成后草稿再被编辑，
            save_state=SAVE_STALE 表示发起版本已落盘、当前草稿仍待保存）;
            False 保存失败 (验证错误 / IO 错误 / 重复提交)。
        Raises:
            asyncio.CancelledError: 取消时显式 raise (R2)；占用由持久化 task
            完成回调或 reload 取消分支收束为非 saving 状态，无永久锁定。
        """
        # 重复提交检测
        if self._state.save_state == SAVE_SAVING:
            return False

        # ========== 阶段 1: 验证 ==========
        is_valid, err_key = self._validate_all()
        if not is_valid:
            self._set_state(save_state=SAVE_ERROR, warning_message="")
            logger.warning("[AIBrainSettingsVM] validation failed: %s", err_key)
            return False

        # ========== 阶段 2: 持久化 ==========
        self._set_state(save_state=SAVE_SAVING, warning_message="")
        # F05: 与读取草稿值同一同步段捕获发起版本（await 之前，set_* 无法插入）。
        captured_revision = self._state.draft_revision
        try:
            max_cand = int(self._state.max_candidates_value.strip())
            min_turn = float(self._state.min_turnover_value.strip())
            ai_prompt = self._state.ai_prompt_value
            news_prompt = self._state.news_prompt_value
            local_only = self._state.ai_local_only_mode

            def _save_configs_sync() -> bool:
                """调优参数 + prompts 单次原子保存, 在 IO 线程池执行 (R16, T3)。

                任一 prompt 校验失败或 save_config 失败时整体未提交,
                不存在部分成功 (R21 邻域: 部分成功不得伪装成已保存)。
                """
                return ConfigHandler.save_ai_tuning_and_prompts(max_cand, min_turn, local_only, ai_prompt, news_prompt)

            # UIB-AIBRAIN-01: 持久化放入独立 task 并经 shield 等待——外层取消只
            # 终止等待，写盘线程继续执行至真正终结；取消时挂完成回调收束占用
            # （见 _finalize_cancelled_save_persist），正常路径不挂回调，
            # 终态写入两路径互斥。
            persist_task = asyncio.create_task(ThreadPoolManager().run_async(TaskType.IO, _save_configs_sync))
            try:
                success = await asyncio.shield(persist_task)
            except asyncio.CancelledError:
                # R2: 取消传播；占用由 persist_task 完成回调收束（此时写盘可能
                # 仍在运行，不得立即解锁）。
                persist_task.add_done_callback(
                    lambda task: self._finalize_cancelled_save_persist(task, captured_revision)
                )
                raise

            if not success:
                self._set_state(save_state=SAVE_ERROR, warning_message="")
                return False

            # ========== 阶段 3: 重载 AIService 配置 ==========
            from services.ai_service import AIService

            try:
                await AIService().reload_config()
            except asyncio.CancelledError:
                # 真实持久化已终结（写盘收尾完成），占用即时释放：取消的保存
                # 不推进 saved_revision，收束为非 saving 后传播 (R2)。
                self._set_state(save_state=SAVE_ERROR, warning_message="")
                raise

            if self._state.draft_revision != captured_revision:
                # 发起后草稿又被编辑：仅确认已落盘的发起版本，当前草稿仍待保存。
                self._set_state(
                    save_state=SAVE_STALE,
                    saved_revision=captured_revision,
                    warning_message=SAVED_STALE_MESSAGE_KEY,
                )
                return True

            self._set_state(
                save_state=SAVE_SUCCESS,
                saved_revision=captured_revision,
                warning_message="",
            )
            return True
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            from utils.error_classifier import classify_error, classify_severity

            error_info = classify_error(ex, context="general")
            severity = classify_severity(ex, context="general")
            sanitized = DataSanitizer.sanitize_error(ex)  # F4-U-1: R9 异常消息脱敏
            if severity == "system":
                logger.critical(
                    "[AIBrainSettingsVM] SYSTEM-LEVEL failure saving config: %s",
                    sanitized,
                    exc_info=True,
                )
            else:
                logger.error(
                    "[AIBrainSettingsVM] Error saving config (%s): %s",
                    error_info["code"],
                    sanitized,
                    exc_info=True,
                )
            self._set_state(save_state=SAVE_ERROR, warning_message="")
            return False

    def _finalize_cancelled_save_persist(self, persist_task: "asyncio.Future[bool]", captured_revision: int) -> None:
        """取消路径的持久化完成回调：收束保存占用为非 saving 状态 (UIB-AIBRAIN-01)。

        仅在外层 ``save_ai_settings`` 的持久化等待被取消后挂载，与正常路径的
        终态写入互斥（正常路径不挂回调）。职责：
        - 消费 persist_task 结果/异常，避免 "Task exception was never retrieved"；
        - 异常与常规失败路径同口径：classify_error / classify_severity 分类、
          按严重度记日志并经 DataSanitizer 脱敏 (R9)；
        - 统一收束为非 ``SAVE_SAVING`` 状态释放占用（取消的保存不推进
          ``saved_revision``，不宣称已写入）——后续保存可用，且旧写已在回调
          触发前终结，不会覆盖新写。
        """
        if persist_task.cancelled():
            # persist_task 自身被取消仅发生在事件循环关闭收尾等场景：
            # 写盘结果未知，同样按未确认收束（VM 若已 dispose，_set_state 为 no-op）。
            logger.warning("[AIBrainSettingsVM] cancelled save persist aborted (loop shutdown)")
        else:
            exc = persist_task.exception()
            if exc is not None:
                if isinstance(exc, Exception):
                    from utils.error_classifier import classify_error, classify_severity

                    error_info = classify_error(exc, context="general")
                    severity = classify_severity(exc, context="general")
                    sanitized = DataSanitizer.sanitize_error(exc)  # R9 异常消息脱敏
                    if severity == "system":
                        logger.critical(
                            "[AIBrainSettingsVM] SYSTEM-LEVEL failure in cancelled save persist: %s",
                            sanitized,
                            exc_info=exc,
                        )
                    else:
                        logger.error(
                            "[AIBrainSettingsVM] Error in cancelled save persist (%s): %s",
                            error_info["code"],
                            sanitized,
                            exc_info=exc,
                        )
                else:
                    # R9: 非 Exception 的 BaseException 消息同样经脱敏后才进入日志
                    # （sanitize_error 签名限 Exception | str，此处经 str() 传入）。
                    sanitized_base = DataSanitizer.sanitize_error(str(exc))
                    logger.error(
                        "[AIBrainSettingsVM] cancelled save persist failed with non-exception base error: %s",
                        sanitized_base,
                        exc_info=exc,
                    )
            else:
                persisted = persist_task.result()
                logger.warning(
                    "[AIBrainSettingsVM] cancelled save persist finished (persisted=%s); captured revision %d kept unconfirmed",
                    persisted,
                    captured_revision,
                )
        # 统一收束：非 saving 反馈态 + 版本不动（无永久锁定、不伪成功）。
        self._set_state(save_state=SAVE_ERROR, warning_message="")
