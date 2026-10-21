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
- 三阶段保存状态机: idle → saving → success / error
- 同步阻塞 ConfigHandler 写入通过 ThreadPoolManager.run_async offload (R16)
- R2: asyncio.CancelledError 显式 raise, 不被 except Exception 吞没
- 重复提交检测：save_state="saving" 时拒绝新提交

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


class AIBrainSettingsViewModel(ObservableViewModelMixin[AIBrainSettingsState]):
    """AIBrainTab 调优参数 + Prompt 编排 ViewModel（S5 拆分后）。

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (AIBrainSettingsState) via subscribe/_set_state
    - 三阶段保存状态机: idle → saving → success / error
    - 同步阻塞 ConfigHandler/ThreadPoolManager IO 通过 ThreadPoolManager.run_async offload (R16)
    - R2: CancelledError 显式 raise, 不被 except Exception 吞没
    - 重复提交检测：save_state="saving" 时拒绝新提交

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

    def set_max_candidates_value(self, value: str) -> None:
        self._set_state(max_candidates_value=value)

    def set_min_turnover_value(self, value: str) -> None:
        self._set_state(min_turnover_value=value)

    def set_ai_prompt_value(self, value: str) -> None:
        self._set_state(ai_prompt_value=value)

    def set_news_prompt_value(self, value: str) -> None:
        self._set_state(news_prompt_value=value)

    def set_ai_local_only_mode(self, value: bool) -> None:
        self._set_state(ai_local_only_mode=bool(value))

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

        Returns:
            True 保存成功; False 保存失败 (验证错误 / IO 错误)。
        Raises:
            asyncio.CancelledError: 取消时显式 raise (R2)。
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
        self._set_state(save_state=SAVE_SAVING)
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

            success = await ThreadPoolManager().run_async(TaskType.IO, _save_configs_sync)
            if not success:
                self._set_state(save_state=SAVE_ERROR, warning_message="")
                return False

            # ========== 阶段 3: 重载 AIService 配置 ==========
            from services.ai_service import AIService

            await AIService().reload_config()

            self._set_state(save_state=SAVE_SUCCESS, warning_message="")
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
