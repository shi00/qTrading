"""ScreenerViewModel 策略元数据与 tier 提示 mixin（C3-6 拆分）。

从 ``screener_view_model.py`` 按职责拆出：策略查询（``get_strategies``/``get_strategy_desc``/
``get_strategy_params``/``get_base_prompt``）、参数草稿（``init_strategy_params``/
``set_strategy_param``/``reset_strategy_params``/``reset_strategy_prompt``/``save_strategy_prompt``）、
预设保存复用（``get_preset_names``/``save_preset``/``load_preset``/``delete_preset``）、
策略选择与描述（``select_strategy``/``load_strategies``/``_build_strategy_dep_rows``/
``update_strategy_desc``/``_compute_tier_hint``）、列别名（``get_column_alias``）。

本 mixin 无独立状态，依赖宿主 VM 的共享成员（``_state``/``_set_state``/``strategy_mgr`` 等）；
``select_strategy`` 经组合实例调用 AIStream 的 ``_on_card_error``（取消重试时终结占位卡）。
``_state`` 以类级注解声明以满足类型检查。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from types import MappingProxyType

from ui.viewmodels import Message
from ui.viewmodels.screener_types import ScreenerState, StrategyDepRow
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class StrategyMetaMixin:
    """策略元数据与 tier 提示职责（C3-6）。组合进 ``ScreenerViewModel``。"""

    _state: ScreenerState

    async def get_strategies(self) -> dict[str, str]:
        return self.strategy_mgr.get_all_names()

    def get_strategy_desc(self, key: str) -> Message | None:
        """获取策略描述的 Message (i18n key + params), VM 不感知 locale (§3.2).

        View 渲染时通过翻译 ``msg.key`` + ``msg.params`` 为当前 locale 字符串.
        """
        st = self.strategy_mgr.get_strategy(key)
        return Message(st.desc_key, {}) if st else None

    def get_strategy_params(self, key: str) -> list:
        """Get dynamic parameter definitions for a strategy."""
        # Defensive copy to prevent mutating a strategy's cached class attributes
        params = list(self.strategy_mgr.get_strategy_params(key))

        # Inject AI System Prompt override parameter globally so ALL strategies can use it.
        # Check to avoid duplicate if a strategy still happens to implement it natively.
        if not any(p.get("name") == "ai_system_prompt" for p in params):
            params.append(
                {
                    "name": "ai_system_prompt",
                    "label_key": "ai_system_prompt",
                    "type": "textarea",
                    "default": "",  # UI uses vm.get_base_prompt to map the value dynamically
                    "group": "advanced",
                },
            )

        return params

    def get_base_prompt(self, strategy_key: str) -> str:
        """获取策略基础 prompt (Task 5.1: 从 View 迁入, 内聚到 VM).

        View 通过本方法消费 ``strategy_prompts.get_base_prompt``，不再直接 import
        ``strategies`` 业务对象 (CLAUDE.md §3.2 MVVM 契约)。
        """
        from strategies.strategy_prompts import get_base_prompt

        return get_base_prompt(strategy_key)

    # --- D3: 策略参数草稿 (View 编辑中间态下沉 VM, 消除 View params_ref 双轨) ---

    def init_strategy_params(self, strategy_key: str) -> None:
        """初始化策略参数默认值 (原 View 两处重复逻辑内聚于此).

        ai_system_prompt 用 get_base_prompt 映射; 其余参数用定义 default.
        切换策略/深链进入时调用, 保证草稿与 selected_strategy 同步 (报告 04 D3).
        """
        params_def = self.get_strategy_params(strategy_key)
        defaults: dict[str, Any] = {}
        for p in params_def:
            if p.get("name") == "ai_system_prompt":
                defaults[p["name"]] = self.get_base_prompt(strategy_key) or p.get("default", "")
            else:
                defaults[p["name"]] = p.get("default")
        self._set_state(strategy_params=MappingProxyType(defaults))

    def set_strategy_param(self, name: str, value: Any) -> None:
        """更新单个策略参数 (生成新 dict 保持不可变快照)."""
        self._set_state(strategy_params=MappingProxyType({**self._state.strategy_params, name: value}))

    def reset_strategy_params(self) -> None:
        """清空策略参数草稿 (策略取消选中时调用)."""
        self._set_state(strategy_params=MappingProxyType({}))

    async def reset_strategy_prompt(self, strategy_key: str) -> str:
        """重置策略 prompt 为默认值 (Phase 3.3: 从 View 迁入, 内聚到 VM).

        通过 ``ConfigHandler.set_strategy_prompt(strategy_key, None)`` 清除用户覆盖,
        然后返回基础 prompt 字符串供 View 更新 UI state.

        Args:
            strategy_key: 策略 key

        Returns:
            基础 prompt 字符串

        Raises:
            Exception: ConfigHandler 失败时抛出 (View 负责展示错误)
        """

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strategy_key, None)
        # get_base_prompt 内部调 ConfigHandler.get_strategy_prompt / get_ai_system_prompt (load_config IO),
        # 需 ThreadPoolManager 包装 (R16).
        return str(await ThreadPoolManager().run_async(TaskType.IO, self.get_base_prompt, strategy_key))

    async def save_strategy_prompt(self, strategy_key: str, prompt: str) -> tuple[bool, str | None]:
        """保存策略 prompt (Phase 3.3: 从 View 迁入, 内聚到 VM).

        内部完成 ``validate_prompt`` + ``ConfigHandler.set_strategy_prompt`` 编排.

        Args:
            strategy_key: 策略 key
            prompt: 用户输入的 prompt 字符串

        Returns:
            (success, error_key): 成功时 (True, None); 失败时 (False, error_key) 其中
            error_key 为 i18n key (如 ``prompt_err_length`` / ``prompt_err_injection``)
        """
        from utils.prompt_guard import validate_prompt

        is_valid, warning = validate_prompt(prompt)
        if not is_valid:
            return False, warning

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strategy_key, prompt)
        return True, None

    # --- Task 4.1: 筛选方案保存/复用 (FR-UX-003) ---

    def get_preset_names(self, strategy_key: str) -> list[str]:
        """获取策略已保存的预设名称列表 (Task 4.1).

        ConfigHandler._config_cache 命中时为纯内存读 (非 IO); 首次未命中
        触发小 JSON 文件读 (< 5ms), 在 use_effect 上下文中可接受。
        """
        presets = ConfigHandler.get_strategy_presets(strategy_key)
        return list(presets.keys())

    async def save_preset(self, name: str, strategy_key: str, params: dict) -> None:
        """保存命名参数预设 (Task 4.1). 重名覆盖.

        Raises:
            Exception: ConfigHandler 失败时抛出 (View 负责展示错误)
        """

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.save_strategy_preset, strategy_key, name, params)

    def load_preset(self, name: str, strategy_key: str) -> dict:
        """载入命名参数预设 (Task 4.1).

        Returns:
            参数 dict; 预设不存在时返回空 dict.
        """

        presets = ConfigHandler.get_strategy_presets(strategy_key)
        return presets.get(name, {})

    async def delete_preset(self, name: str, strategy_key: str) -> bool:
        """删除命名参数预设 (Task 4.1).

        Returns:
            bool: True 表示已删除, False 表示预设不存在.
        """

        return bool(
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.delete_strategy_preset, strategy_key, name)
        )

    def get_column_alias(self, table_name: str | None, col: str) -> str:
        """获取列别名 (Task 5.1: 从 View 迁入, 内聚到 VM).

        View 通过本方法消费 ``MetaDataManager.get_column_alias``，不再直接 import
        ``data`` 业务对象 (CLAUDE.md §3.2 MVVM 契约)。
        """
        from data.persistence.metadata_manager import MetaDataManager

        return MetaDataManager.get_column_alias(table_name, col)

    # --- 策略选择 / 描述 / tier 提示 ---

    def select_strategy(self, key: str | None) -> None:
        """选中策略 + 计算 tier_hint（R.2.1: 内聚到 VM, 消除 View 双源真相）。

        Args:
            key: 策略 key, None 表示清空选择
        """
        # UX-2.3 v4 P0-2: 重试中切换策略 → 只取消重试 task（不取消 persist_splitter_width / _flush_ai_buffer 等其它后台任务）
        if self._retrying:
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
        tier_hint = self._compute_tier_hint(key)
        self._set_state(selected_strategy=key, tier_hint=tier_hint, is_retrying=False)

    def load_strategies(self) -> None:
        """加载策略列表到 state (R.2.6.1: 业务状态迁入 VM).

        从 strategy_mgr 获取策略+依赖信息, 存入 state.strategies_with_dep.
        View 渲染时调 _build_strategy_options(state.strategies_with_dep) 构建 Flet Options,
        确保 locale 切换后 Options 自动重新翻译 (避免 use_state 缓存旧 locale 翻译).
        """
        try:
            strategies_with_dep = self._build_strategy_dep_rows()
            self._set_state(
                strategies_with_dep=strategies_with_dep,
                strategies_loaded=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerVM] Failed to load strategies: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            self._set_state(
                status_message=Message("screener_load_failed", {}),
                status_color="error",
                status_action_key=None,
            )

    def _build_strategy_dep_rows(self) -> tuple[StrategyDepRow, ...]:
        """将 strategy_mgr 的依赖 dict 装配为不可变行序列 (D10: frozen 契约).

        ``name`` 用 ``name_key`` (raw i18n key) 替代已翻译显示名 — VM 不感知 locale
        (§3.2), View 渲染时按当前 locale 翻译 (消除 stale 翻译, D16 同理念).
        """
        rows: list[StrategyDepRow] = []
        for key, info in self.strategy_mgr.get_all_with_dependencies().items():
            strategy_obj = self.strategy_mgr.get_strategy(key)
            name_key = getattr(strategy_obj, "name_key", key) or key
            rows.append(
                StrategyDepRow(
                    key=key,
                    name_key=name_key,
                    missing_apis=tuple(info.get("missing_apis", [])),
                )
            )
        return tuple(rows)

    def update_strategy_desc(self, selected_strategy: str | None, params: dict | None = None) -> None:
        """更新策略描述 Message 和颜色到 state (R.2.6.2: 业务状态迁入 VM).

        构造 ``state.strategy_desc`` 为 ``Message`` (desc_key + params), VM 不感知 locale
        (§3.2). View 渲染时通过翻译 ``msg.key`` + ``msg.params`` 为当前 locale 字符串.

        当 ``dep_info.missing_apis`` 非空时, 在 params 中追加 ``missing_apis`` 字段
        (逗号分隔字符串) 并设 ``strategy_desc_color="warning"``; View 渲染时识别该字段
        追加 ``strategy_missing_apis`` 翻译后缀.

        Args:
            selected_strategy: 策略 key, None 表示清空
            params: 动态参数 (可选, 用于 get_dynamic_description; None 时用策略默认参数)
        """
        if not selected_strategy:
            self._set_state(strategy_desc=None, strategy_desc_color="default")
            return

        try:
            strategy_obj = self.strategy_mgr.get_strategy(selected_strategy)
            dep_info = next(
                (r for r in self._build_strategy_dep_rows() if r.key == selected_strategy),
                None,
            )

            if strategy_obj:
                if params is None:
                    params = {p["name"]: p.get("default") for p in strategy_obj.get_parameters()}
                desc_msg = strategy_obj.get_dynamic_description(params)
            else:
                desc_msg = self.get_strategy_desc(selected_strategy)

            if desc_msg is None:
                self._set_state(strategy_desc=None, strategy_desc_color="default")
                return

            # missing_apis 非空时: params 追加 missing_apis 字段, color=warning
            # View 渲染时识别 missing_apis 字段追加 "strategy_missing_apis" 翻译后缀
            missing_apis = dep_info.missing_apis if dep_info else ()
            if missing_apis:
                merged_params = dict(desc_msg.params)
                merged_params["missing_apis"] = ", ".join(missing_apis)
                desc_msg = Message(desc_msg.key, merged_params)
                color = "warning"
            else:
                color = "default"

            self._set_state(strategy_desc=desc_msg, strategy_desc_color=color)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[ScreenerVM] update_strategy_desc failed: %s", DataSanitizer.sanitize_error(e), exc_info=True
            )
            self._set_state(strategy_desc=None, strategy_desc_color="default")

    @staticmethod
    def _compute_tier_hint(selected_strategy: str | None) -> str | None:
        """检查策略档位是否足够，不足时返回 i18n key，否则 None。

        返回 i18n key（非翻译值），符合 §3.2 "VM 只产出 i18n key"。
        View 渲染时翻译 ``state.tier_hint``。
        """
        if not selected_strategy:
            return None
        try:
            from data.external.tushare_client import TushareClient
            from services.ai_service import get_strategy_min_tier

            current_tier = ConfigHandler.get_tushare_point_tier()
            min_tier = get_strategy_min_tier(selected_strategy)
            client = TushareClient()
            if client.get_tier_order(current_tier) < client.get_tier_order(min_tier):
                return "sys_strategy_tier_hint"
        except Exception as e:
            logger.debug("[ScreenerVM] tier hint check skipped: %s", DataSanitizer.sanitize_error(e), exc_info=True)
        return None
