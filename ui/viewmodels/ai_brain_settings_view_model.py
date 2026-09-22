"""AIBrainSettingsViewModel — AIBrainTab 配置编排 ViewModel (Task 5.2 + Phase 3.2 P1-1).

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
- Phase 3.2 P1-1: 子 VM 业务回调 (test_connection/reload_service/verify_local_model)
  作为本 VM 静态 command 暴露, View 注入到子 VM; 本地模型 MD5 检查整合进 save_ai_settings,
  结果通过 state.warning_message 暴露给 View (View 仅调 save_ai_settings 并据 state 反馈)

不感知 locale：状态字段为字符串/布尔值，View 渲染时按当前 locale 翻译。
"""

import asyncio
import logging
import os
import typing
from dataclasses import dataclass

from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer
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
    # SEC-03 第一步: 「仅本地模式」开关（禁用全部云端 LLM，仅使用本地模型）
    ai_local_only_mode: bool = False
    # BIZ-02: 云端 LLM 是否已配置（主 provider 有 api_key 且非仅本地模式）。
    # View 据此展示「未配置 AI 不影响选股与复盘，仅关闭 AI 解读」提示。
    ai_configured: bool = False
    # AI-03 完整版 T7: 月度 AI 成本上限输入文本 (空串=不限制)
    ai_cost_limit_value: str = ""
    # review-pr1073 M3: 美元→人民币估算汇率输入文本（成本展示用，非实时汇率）
    usd_to_cny_rate_value: str = ""
    # AI-03 完整版 T7: 本月累计 AI 成本 (元, 供 UI 展示; 未加载为 None)
    month_cost_cny: float | None = None
    # AI-01/R21: 本月累计不可计价调用 (calls, tokens)；无不可计价调用时为 (0, 0)。
    # 供设置页本月累计文案在存在不可计价量时追加「成本未知」诚实说明。
    month_unpriced: tuple[int, int] = (0, 0)
    # Phase 3.2 P1-1: MD5 检查结果 i18n key (非空时 View 显示 WARNING snack)
    # 下沉自 View._check_local_model_md5, VM 在 save_ai_settings 末尾写入
    warning_message: str = ""


class AIBrainSettingsViewModel(ObservableViewModelMixin[AIBrainSettingsState]):
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
        self._init_mixin_fields()
        self._load_config_to_state()

    # --- 子 VM 回调 commands (Phase 3.2 P1-1: 下沉自 View 模块级函数) ---

    @staticmethod
    async def test_connection(
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        **kwargs,
    ) -> dict:
        """LLM 连接测试 command (注入 LLMConfigPanelViewModel/FailoverConfigPanelViewModel)。

        下沉自 View._on_llm_test_connection (Phase 3.2 P1-1)。
        静态方法: 纯业务转发, 不访问 VM state, 避免与子 VM 工厂的循环依赖。
        """
        from services.ai_service import AIService

        return await AIService.test_connection(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    @staticmethod
    async def reload_service() -> None:
        """重载 AIService 配置 command (注入 LLMConfigPanelViewModel.on_reload_service)。

        下沉自 View._on_reload_ai_service (Phase 3.2 P1-1)。
        """
        from services.ai_service import AIService

        await AIService().reload_config()

    @staticmethod
    async def verify_local_model(model_path: str, config: dict) -> bool:
        """验证本地模型 command (注入 LocalModelConfigPanelViewModel.on_verify_model)。

        下沉自 View._on_verify_local_model (Phase 3.2 P1-1)。
        """
        from services.local_model_manager import LocalModelManager

        manager = await LocalModelManager.get_instance()
        return await manager.load_model(model_path, config, is_verification=True)

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步, 仅在 __init__ 调用一次）。"""
        ai_concurrency = max(_CONCURRENCY_MIN, ConfigHandler.get_ai_max_concurrent_analysis())
        # AI-03 完整版 T7: ai_cost_limit_cny (元, None/0/负 视为不限制 → 空串)
        ai_cost_limit = ConfigHandler.get_setting("ai_cost_limit_cny")
        ai_cost_limit_value = "" if ai_cost_limit is None or ai_cost_limit <= 0 else str(ai_cost_limit)
        # review-pr1073 M3: 估算汇率配置（缺省回退 7.2 并在 UI 标注"估算汇率"）
        usd_rate = ConfigHandler.get_setting("ai_usd_to_cny_rate", 7.2)
        usd_to_cny_rate_value = str(usd_rate) if isinstance(usd_rate, (int, float)) and usd_rate > 0 else str(7.2)
        # BIZ-02: 口径对齐 AIService.is_cloud_available 的静态部分（主 provider 有
        # api_key 且非仅本地模式）。AIService 实例内的 _is_cloud_configured（load 状态）
        # 此处不可静态感知，配置存在即视为已配置——UI 提示语义是「是否配置」，非「当前可用」。
        llm_cfg = ConfigHandler.get_llm_config()
        ai_configured = bool(llm_cfg.get("api_key")) and not ConfigHandler.is_ai_local_only_mode()
        self._state = AIBrainSettingsState(
            max_candidates_value=str(ConfigHandler.get_ai_max_candidates()),
            min_turnover_value=str(ConfigHandler.get_strategy_min_turnover()),
            ai_concurrency_value=str(ai_concurrency),
            news_concurrency_value=str(ConfigHandler.get_ai_news_max_concurrent()),
            ai_prompt_value=ConfigHandler.get_ai_system_prompt(),
            news_prompt_value=ConfigHandler.get_ai_news_prompt(),
            save_state=SAVE_IDLE,
            ai_local_only_mode=ConfigHandler.is_ai_local_only_mode(),
            ai_configured=ai_configured,
            ai_cost_limit_value=ai_cost_limit_value,
            usd_to_cny_rate_value=usd_to_cny_rate_value,
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

    def set_ai_local_only_mode(self, value: bool) -> None:
        self._set_state(ai_local_only_mode=bool(value))

    def set_news_prompt_value(self, value: str) -> None:
        self._set_state(news_prompt_value=value)

    def set_ai_cost_limit_value(self, value: str) -> None:
        self._set_state(ai_cost_limit_value=value)

    def set_usd_to_cny_rate_value(self, value: str) -> None:
        self._set_state(usd_to_cny_rate_value=value)

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

        # AI-03 完整版 T7: 成本上限允许空串（不限制）或非负 float
        ai_cost_limit_str = (self._state.ai_cost_limit_value or "").strip()
        if ai_cost_limit_str:
            try:
                ai_cost_limit = float(ai_cost_limit_str)
                if ai_cost_limit < 0:
                    return False, "ai_snack_param_err"
            except (ValueError, TypeError):
                return False, "ai_snack_param_err"

        # review-pr1073 M3: 估算汇率必须为正数（成本展示换算，不可为 0/负）
        usd_rate_str = (self._state.usd_to_cny_rate_value or "").strip()
        if usd_rate_str:
            try:
                if float(usd_rate_str) <= 0:
                    return False, "ai_snack_param_err"
            except (ValueError, TypeError):
                return False, "ai_snack_param_err"

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
            self._set_state(save_state=SAVE_ERROR, warning_message="")
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
            # AI-03 完整版 T7: 成本上限空串 → None (不限制); 否则 float 元
            ai_cost_limit_str = (self._state.ai_cost_limit_value or "").strip()
            ai_cost_limit_cny = None if not ai_cost_limit_str else float(ai_cost_limit_str)
            # review-pr1073 M3: 估算汇率空串/非法回退默认 7.2（_validate_all 已保证非空时正数）
            usd_rate_str = (self._state.usd_to_cny_rate_value or "").strip()
            usd_to_cny_rate = float(usd_rate_str) if usd_rate_str else 7.2

            # local_vm.get_current_config() 返回 dict (复用 LocalModelConfigPanelViewModel)
            local_config = typing.cast(typing.Any, self._local_vm).get_current_config()
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
            llm_saved = await typing.cast(typing.Any, self._llm_vm).save_config()
            if not llm_saved:
                self._set_state(save_state=SAVE_ERROR, warning_message="")
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
                        "ai_cost_limit_cny": ai_cost_limit_cny,
                        "ai_usd_to_cny_rate": usd_to_cny_rate,
                        "ai_local_only_mode": self._state.ai_local_only_mode,
                    }
                ):
                    return False
                if not ConfigHandler.save_ai_system_prompt(ai_prompt):
                    return False
                return ConfigHandler.set_ai_news_prompt(news_prompt)

            success = await ThreadPoolManager().run_async(TaskType.IO, _save_configs_sync)
            if not success:
                self._set_state(save_state=SAVE_ERROR, warning_message="")
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
                    self._set_state(save_state=SAVE_ERROR, warning_message="")
                    return False

            # MD5 检查 (Phase 3.2 P1-1: 下沉自 View._check_local_model_md5)
            # 结果写入 state.warning_message, View 据此决定显示 WARNING snack 还是 SUCCESS snack
            warning_msg = ""
            if local_path:
                local_mgr = await LocalModelManager.get_instance()
                loaded_md5 = local_mgr.get_loaded_model_md5()
                new_md5 = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    LocalModelManager.calculate_file_md5,
                    local_path,
                )
                if loaded_md5 and new_md5 and loaded_md5 != new_md5:
                    warning_msg = "ai_local_model_changed"

            self._set_state(save_state=SAVE_SUCCESS, warning_message=warning_msg)
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

    # --- AI-03 完整版 T7: 本月累计成本加载 ---

    async def load_month_cost_cny(self) -> None:
        """读取本月累计 AI 成本（分 → 元）到 state.month_cost_cny。

        经 engine_provider 惰性注入 AIUsageTracker 的引擎（R5 判活），仅在引擎可用
        时读取；引擎不可用或读取异常时置 None（不阻断 UI，仅记日志）。
        async-native DB 读（SQLAlchemy async），按宪法 §3.2 无需包线程池（R16）。
        R2: asyncio.CancelledError 必须 raise，不被 except Exception 吞没。
        """
        from data.persistence.engine_provider import get_engine, is_disposed
        from services.ai_service.usage_tracker import AIUsageTracker  # lazy-import

        try:
            engine = get_engine()
            if engine is None or is_disposed(engine):
                self._set_state(month_cost_cny=None)
                return
            AIUsageTracker().configure(engine=engine)
            month_cost_cents = await AIUsageTracker().get_month_cost_cents()
            month_unpriced = await AIUsageTracker().get_month_unpriced()
            self._set_state(
                month_cost_cny=month_cost_cents / 100,
                month_unpriced=(int(month_unpriced[0]), int(month_unpriced[1])),
            )
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.debug(
                "[AIBrainSettingsVM] Failed to load month AI cost: %s",
                ex,
            )
            self._set_state(month_cost_cny=None)
