"""AIBrainSettingsViewModel — AIBrainTab 配置编排 ViewModel (Task 5.2).

承担 AIBrainTab 中 AI 调优参数 (max_candidates/min_turnover/concurrency/news_concurrency/
ai_prompt/news_prompt) + 三阶段保存状态机编排（CLAUDE.md §3.2 MVVM）。

设计要点：
- frozen state snapshot (AIBrainSettingsState dataclass)
- 三阶段保存状态机: idle → saving → success / error
- 构造注入 LLMConfigPanelViewModel/FailoverConfigPanelViewModel/LocalModelConfigPanelViewModel
  （复用现有 config panel VM, 不再包一层薄 VM, §1.3 拒绝过度抽象）
- 同步阻塞 ConfigHandler 写入通过 ThreadPoolManager.run_async offload (R16)
- R2: asyncio.CancelledError 显式 raise, 不被 except Exception 吞没
- 重复提交检测：save_state="saving" 时拒绝新提交

不感知 locale：状态字段为字符串/布尔值，View 渲染时按当前 locale 翻译。
"""

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace

from utils.config_handler import ConfigHandler
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


# --- Validation bounds (与原 ai_brain_tab 一致) ---
_MAX_CANDIDATES_MIN = 1
_MAX_CANDIDATES_MAX = 500
_MIN_TURNOVER_MIN = 0.0
_MIN_TURNOVER_MAX = 100.0
_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 10
_NEWS_CONCURRENCY_MIN = 1
_NEWS_CONCURRENCY_MAX = 5

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
    ai_concurrency_value: str = "5"
    news_concurrency_value: str = "1"
    ai_prompt_value: str = ""
    news_prompt_value: str = ""
    save_state: str = SAVE_IDLE


class AIBrainSettingsViewModel:
    """AIBrainTab 配置编排 ViewModel。

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (AIBrainSettingsState) via subscribe/_notify
    - 三阶段保存状态机: idle → saving → success / error
    - 构造注入 LLMConfigPanelViewModel/FailoverConfigPanelViewModel/LocalModelConfigPanelViewModel
      （复用现有 config panel VM 的 save_config/get_current_config, 不再包一层薄 VM）
    - 同步阻塞 IO 通过 ThreadPoolManager.run_async offload (R16)
    - R2: CancelledError 显式 raise, 不被 except Exception 吞没
    - 重复提交检测：save_state="saving" 时拒绝新提交

    消费方 (AIBrainTab) 通过 use_viewmodel(factory=) 内部模式实例化 VM, 传入
    3 个 config panel VM 作为构造依赖。
    """

    def __init__(
        self,
        *,
        llm_vm: object,
        failover_vm: object,
        local_vm: object,
    ) -> None:
        # 3 个 config panel VM 复用: VM.save_config / VM.get_current_config
        self._llm_vm = llm_vm
        self._failover_vm = failover_vm
        self._local_vm = local_vm
        self._state = AIBrainSettingsState()
        self._subscribers: list[Callable[[AIBrainSettingsState], None]] = []
        self._load_config_to_state()

    # --- State snapshot + subscribe/_notify ---

    @property
    def state(self) -> AIBrainSettingsState:
        return self._state

    def subscribe(self, callback: Callable[[AIBrainSettingsState], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self) -> None:
        self._subscribers.clear()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步, 仅在 __init__ 调用一次）。"""
        ai_concurrency = max(_CONCURRENCY_MIN, ConfigHandler.get_ai_max_concurrent_analysis())
        self._state = AIBrainSettingsState(
            max_candidates_value=str(ConfigHandler.get_ai_max_candidates()),
            min_turnover_value=str(ConfigHandler.get_strategy_min_turnover()),
            ai_concurrency_value=str(ai_concurrency),
            news_concurrency_value=str(ConfigHandler.get_ai_news_max_concurrent()),
            ai_prompt_value=ConfigHandler.get_ai_system_prompt(),
            news_prompt_value=ConfigHandler.get_ai_news_prompt(),
            save_state=SAVE_IDLE,
        )

    # --- Update commands (View 通过 set_* 更新本地 state) ---

    def set_max_candidates_value(self, value: str) -> None:
        self._set_state(max_candidates_value=value)

    def set_min_turnover_value(self, value: str) -> None:
        self._set_state(min_turnover_value=value)

    def set_ai_concurrency_value(self, value: str) -> None:
        self._set_state(ai_concurrency_value=value)

    def set_news_concurrency_value(self, value: str) -> None:
        self._set_state(news_concurrency_value=value)

    def set_ai_prompt_value(self, value: str) -> None:
        self._set_state(ai_prompt_value=value)

    def set_news_prompt_value(self, value: str) -> None:
        self._set_state(news_prompt_value=value)

    # --- 验证 (阶段 1) ---

    def _validate_all(self) -> tuple[bool, str]:
        """验证所有输入, 返回 (是否有效, 错误 i18n key)。"""
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

        concurrency_str = (self._state.ai_concurrency_value or "").strip()
        try:
            concurrency = int(concurrency_str)
            if not (_CONCURRENCY_MIN <= concurrency <= _CONCURRENCY_MAX):
                return False, "ai_snack_invalid_range"
        except (ValueError, TypeError):
            return False, "ai_snack_invalid_range"

        news_concurrency_str = (self._state.news_concurrency_value or "").strip()
        try:
            news_concurrency = int(news_concurrency_str)
            if not (_NEWS_CONCURRENCY_MIN <= news_concurrency <= _NEWS_CONCURRENCY_MAX):
                return False, "ai_snack_invalid_range"
        except (ValueError, TypeError):
            return False, "ai_snack_invalid_range"

        return True, ""

    # --- 三阶段保存状态机 (validate → persist → reload) ---

    async def save_ai_settings(self) -> bool:
        """三阶段保存 AI 配置 (云端 LLM + 本地模型 + 调优参数 + prompts).

        链路:
        1. **验证**: 校验 max_candidates/min_turnover/concurrency/news_concurrency 范围
        2. **持久化**:
           - llm_vm.save_config() (LLM 配置)
           - ConfigHandler.save_local_ai_config(...) (本地模型)
           - ConfigHandler.save_config({...}) (调优参数)
           - ConfigHandler.save_ai_system_prompt(...)
           - ConfigHandler.set_ai_news_prompt(...)
           - LocalModelManager.commit_verification_if_active()
        3. **重载**: AIService().reload_config() + 本地模型文件存在性检查

        Returns:
            True 保存成功; False 保存失败 (验证错误 / IO 错误 / LLM save 失败)。
        Raises:
            asyncio.CancelledError: 取消时显式 raise (R2)。
        """
        # 重复提交检测
        if self._state.save_state == SAVE_SAVING:
            return False

        # ========== 阶段 1: 验证 ==========
        is_valid, err_key = self._validate_all()
        if not is_valid:
            self._set_state(save_state=SAVE_ERROR)
            logger.warning("[AIBrainSettingsVM] validation failed: %s", err_key)
            return False

        # ========== 阶段 2: 持久化 ==========
        self._set_state(save_state=SAVE_SAVING)
        try:
            # 提取 UI 值
            max_cand = int(self._state.max_candidates_value.strip())
            min_turn = float(self._state.min_turnover_value.strip())
            concurrency = int(self._state.ai_concurrency_value.strip())
            news_concurrency = int(self._state.news_concurrency_value.strip())
            ai_prompt = self._state.ai_prompt_value
            news_prompt = self._state.news_prompt_value

            # local_vm.get_current_config() 返回 dict (复用 LocalModelConfigPanelViewModel)
            local_config = self._local_vm.get_current_config()
            local_save_kwargs = {
                "model_path": local_config.get("model_path", ""),
                "timeout": local_config.get("timeout", 300),
                "n_threads": local_config.get("n_threads", 4),
                "n_batch": local_config.get("n_batch", 512),
                "n_ctx": local_config.get("n_ctx", 2048),
                "flash_attn": local_config.get("flash_attn", False),
                "n_gpu_layers": local_config.get("n_gpu_layers", 0),
            }

            # 先保存 LLM 配置 (复用 llm_vm.save_config)
            llm_saved = await self._llm_vm.save_config()
            if not llm_saved:
                self._set_state(save_state=SAVE_ERROR)
                return False

            def _save_configs_sync() -> bool:
                """LocalModel + 其他 AI 配置保存, 在 IO 线程池执行 (R16)。"""
                if not ConfigHandler.save_local_ai_config(**local_save_kwargs):
                    return False
                if not ConfigHandler.save_config(
                    {
                        "ai_max_candidates": max_cand,
                        "strategy_min_turnover": min_turn,
                        "ai_max_concurrent_analysis": concurrency,
                        "ai_news_max_concurrent": news_concurrency,
                    }
                ):
                    return False
                if not ConfigHandler.save_ai_system_prompt(ai_prompt):
                    return False
                return ConfigHandler.set_ai_news_prompt(news_prompt)

            success = await ThreadPoolManager().run_async(TaskType.IO, _save_configs_sync)
            if not success:
                self._set_state(save_state=SAVE_ERROR)
                return False

            # 提交验证模式 (如果活跃) — 验证模型成为正式模型
            from services.local_model_manager import LocalModelManager

            LocalModelManager.commit_verification_if_active()

            # ========== 阶段 3: 重载 AIService 配置 ==========
            from services.ai_service import AIService

            await AIService().reload_config()

            # 本地模型文件存在性检查 (model_path 非空时)
            local_path = local_config.get("model_path", "")
            if local_path:
                exists = await ThreadPoolManager().run_async(TaskType.IO, os.path.exists, local_path)
                if not exists:
                    self._set_state(save_state=SAVE_ERROR)
                    return False

            self._set_state(save_state=SAVE_SUCCESS)
            return True
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            from utils.error_classifier import classify_error, classify_severity

            error_info = classify_error(ex, context="general")
            severity = classify_severity(ex, context="general")
            if severity == "system":
                logger.critical(
                    "[AIBrainSettingsVM] SYSTEM-LEVEL failure saving config: %s",
                    ex,
                    exc_info=True,
                )
            else:
                logger.error(
                    "[AIBrainSettingsVM] Error saving config (%s): %s",
                    error_info["code"],
                    ex,
                    exc_info=True,
                )
            self._set_state(save_state=SAVE_ERROR)
            return False
