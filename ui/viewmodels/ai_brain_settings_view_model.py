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
import hashlib
import logging
import re
from dataclasses import dataclass, replace
from typing import TypedDict

from core.prompt_base import RESEARCH_SUMMARY_PROMPT_DEFAULT
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config.llm import (
    DEFAULT_RESEARCH_ROLE,
    DEFAULT_RESEARCH_SUMMARY_ENABLED,
    DEFAULT_RESEARCH_SUMMARY_KEEP_MESSAGES,
    RESEARCH_ROLE_KEYS,
    RESEARCH_SUMMARY_KEEP_MESSAGES_MAX,
    RESEARCH_SUMMARY_KEEP_MESSAGES_MIN,
    get_agent_research_role,
    get_agent_research_summary_enabled,
    get_agent_research_summary_keep_messages,
    get_agent_research_summary_prompt,
    is_agent_research_summary_prompt_default,
    save_agent_research_config,
)
from utils.config_handler import ConfigHandler
from utils.config_models import ROLE_PROMPT_DEFAULTS
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

# --- F35 / J26 研究角色 Prompt（默认正文只读 + 追加） ---
ROLE_MAIN = "main"
ROLE_SUB = "sub"
ROLE_PROMPT_ORDER: tuple[str, ...] = (ROLE_MAIN, ROLE_SUB)

#: 角色展示名 i18n key（字面量供静态 i18n 键完整性扫描识别）。
ROLE_PROMPT_NAME_KEYS: dict[str, str] = {
    ROLE_MAIN: "settings_ai_role_name_main",
    ROLE_SUB: "settings_ai_role_name_sub",
}
#: 角色教程 i18n key。
ROLE_PROMPT_TUTORIAL_KEYS: dict[str, str] = {
    ROLE_MAIN: "settings_ai_role_tutorial_main",
    ROLE_SUB: "settings_ai_role_tutorial_sub",
}

ROLE_SOURCE_DEFAULT_KEY = "settings_ai_role_source_default"
ROLE_SOURCE_APPEND_KEY = "settings_ai_role_source_default_append"
ROLE_VERSION_UNSAVED_KEY = "settings_ai_role_version_unsaved"
ROLE_VAR_UNKNOWN_KEY = "settings_ai_role_var_unknown"
ROLE_VAR_EMPTY_KEY = "settings_ai_role_var_empty"

#: 合成预览可替换变量及示例值（仅 UI 核对用，不调用模型、不代表真实取数）。
#: 示例值保持语言中立，避免 VM 感知 locale（文案由 View 经 i18n 渲染）。
ROLE_PROMPT_VARIABLES: dict[str, str] = {
    "stock_name": "Sample Stock",
    "stock_code": "600000.SH",
    "industry": "Sample Industry",
    "trade_date": "2026-11-17",
}

_ROLE_VAR_PATTERN = re.compile(r"\{([^{}]*)\}")


def _coerce_str(value: object) -> str:
    """配置读值归一：非字符串一律按空串（缺键不伪造内容）。"""
    return value if isinstance(value, str) else ""


def _replace_role_variables(text: str) -> tuple[str, str]:
    """替换已知变量；未知/空变量保留原占位符并返回错误 i18n key（不静默替换）。"""
    error_key = ""

    def _substitute(match: re.Match[str]) -> str:
        nonlocal error_key
        name = match.group(1).strip()
        if not name:
            error_key = ROLE_VAR_EMPTY_KEY
            return match.group(0)
        if name not in ROLE_PROMPT_VARIABLES:
            error_key = ROLE_VAR_UNKNOWN_KEY
            return match.group(0)
        return ROLE_PROMPT_VARIABLES[name]

    return _ROLE_VAR_PATTERN.sub(_substitute, text), error_key


def _compose_role_prompt(default_prompt: str, append_value: str) -> tuple[str, str]:
    """合成预览 = 默认正文 + 追加内容 + 变量替换；返回 (预览, 错误 i18n key)。"""
    default_preview, error_key = _replace_role_variables(default_prompt)
    append = append_value.strip()
    if not append:
        return default_preview, error_key
    append_preview, append_error = _replace_role_variables(append)
    if append_error:
        error_key = append_error
    return f"{default_preview}\n\n{append_preview}", error_key


def _compose_role_template(default_prompt: str, append_value: str) -> str:
    """原模板（不代入示例变量）：与运行期装配同一拼接规则（默认正文 + 追加）。

    追加仅在去空白后非空时参与拼接，拼接内容为**原样追加**（与
    ``leaf_agent._read_saved_role_prompt`` 的运行期装配一致），因此版本指纹
    对应「实际持久化并生效的原文」，而非示例代入后的预览。
    """
    append = append_value.strip()
    if not append:
        return default_prompt
    return f"{default_prompt}\n\n{append_value}"


def _prompt_version(text: str) -> str:
    """内容版本：原模板内容指纹（短哈希），用于「来源/版本」与旧请求绑定核对。

    必须传入**未代入示例变量**的原模板：若对预览求指纹，
    ``关注 {stock_name}`` 与 ``关注 Sample Stock`` 会得到同一版本，
    把两个不同原模板伪装成「未变化」（R21 邻域）。
    """
    return f"v-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True)
class RolePromptState:
    """单个研究角色的 Prompt 视图状态（默认只读 + 追加 + 合成预览）。"""

    role_key: str
    name_key: str
    tutorial_key: str
    default_prompt: str
    default_version: str
    append_value: str
    saved_version: str
    draft_version: str
    preview: str
    source_key: str
    variable_error_key: str = ""


def _build_role_prompt_state(role_key: str, append_value: str, saved_version: str) -> RolePromptState:
    default_prompt, default_version = ROLE_PROMPT_DEFAULTS[role_key]
    append = _coerce_str(append_value)
    preview, error_key = _compose_role_prompt(default_prompt, append)
    return RolePromptState(
        role_key=role_key,
        name_key=ROLE_PROMPT_NAME_KEYS[role_key],
        tutorial_key=ROLE_PROMPT_TUTORIAL_KEYS[role_key],
        default_prompt=default_prompt,
        default_version=default_version,
        append_value=append,
        saved_version=_coerce_str(saved_version),
        draft_version=_prompt_version(_compose_role_template(default_prompt, append)),
        preview=preview,
        source_key=ROLE_SOURCE_APPEND_KEY if append.strip() else ROLE_SOURCE_DEFAULT_KEY,
        variable_error_key=error_key,
    )


def _normalize_summary_prompt(value: str) -> str:
    """摘要 Prompt 生效值：空白视为「默认资产」（与存储语义一致）。"""
    return value if value.strip() else RESEARCH_SUMMARY_PROMPT_DEFAULT


class _ResearchConfigFields(TypedDict):
    """研究配置 state 字段集合（TypedDict 让 ``_set_state(**fields)`` 保持类型收敛）。"""

    research_role: str
    research_summary_enabled: bool
    research_summary_keep_messages_value: str
    research_summary_prompt_value: str
    research_summary_prompt_is_default: bool
    research_save_state: str
    research_warning_key: str
    research_model_label: str


def _research_config_fields(*, save_state: str = SAVE_IDLE, warning_key: str = "") -> _ResearchConfigFields:
    """从单一配置来源读取研究配置，构造 state 字段（禁止在本页另存第二份默认值）。"""
    prompt = get_agent_research_summary_prompt()
    provider = ConfigHandler.get_typed("llm_provider", str, "")
    model = ConfigHandler.get_typed("llm_model", str, "")
    model_label = (
        f"{provider}/{model}" if isinstance(provider, str) and isinstance(model, str) and provider and model else ""
    )
    return {
        "research_role": get_agent_research_role(),
        "research_summary_enabled": get_agent_research_summary_enabled(),
        "research_summary_keep_messages_value": str(get_agent_research_summary_keep_messages()),
        "research_summary_prompt_value": prompt,
        "research_summary_prompt_is_default": is_agent_research_summary_prompt_default(),
        "research_save_state": save_state,
        "research_warning_key": warning_key,
        "research_model_label": model_label,
    }


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
    # F35 / J26: 主/子研究角色 Prompt（默认只读 + 追加 + 合成预览）。
    role_prompts: tuple[RolePromptState, ...] = ()
    # --- W-18 研究配置统一入口：默认角色 + 摘要策略（单一来源 utils/config/llm.py） ---
    research_role: str = DEFAULT_RESEARCH_ROLE
    research_summary_enabled: bool = DEFAULT_RESEARCH_SUMMARY_ENABLED
    research_summary_keep_messages_value: str = str(DEFAULT_RESEARCH_SUMMARY_KEEP_MESSAGES)
    research_summary_prompt_value: str = RESEARCH_SUMMARY_PROMPT_DEFAULT
    research_summary_prompt_is_default: bool = True
    research_save_state: str = SAVE_IDLE
    research_warning_key: str = ""
    #: 研究所用模型（与 LLM 配置同一来源，只读展示；未配置时为空，不填占位模型名）。
    research_model_label: str = ""


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
        #: 已落盘的角色追加（用于判断本次保存是否需要写入角色配置）。
        self._saved_role_appends: dict[str, str] = {}
        self._init_mixin_fields()
        self._load_config_to_state()

    # --- Config loading ---

    def _load_role_prompt_states(self) -> tuple[tuple[RolePromptState, ...], dict[str, str]]:
        """从原 Prompt 配置源加载角色追加与已绑定版本（缺键按默认，不伪造内容）。"""
        states: list[RolePromptState] = []
        saved_appends: dict[str, str] = {}
        for role_key in ROLE_PROMPT_ORDER:
            append = _coerce_str(ConfigHandler.get_ai_role_prompt_append(role_key))
            version = _coerce_str(ConfigHandler.get_ai_role_prompt_version(role_key))
            states.append(_build_role_prompt_state(role_key, append, version))
            saved_appends[role_key] = append
        return tuple(states), saved_appends

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载本页仍负责的配置到 state（同步, 仅在 __init__ 调用一次）。"""
        role_prompts, saved_appends = self._load_role_prompt_states()
        self._saved_role_appends = saved_appends
        self._state = AIBrainSettingsState(
            max_candidates_value=str(ConfigHandler.get_ai_max_candidates()),
            min_turnover_value=str(ConfigHandler.get_strategy_min_turnover()),
            ai_prompt_value=ConfigHandler.get_ai_system_prompt(),
            news_prompt_value=ConfigHandler.get_ai_news_prompt(),
            save_state=SAVE_IDLE,
            ai_local_only_mode=ConfigHandler.is_ai_local_only_mode(),
            role_prompts=role_prompts,
            **_research_config_fields(),
        )

    # --- Update commands (View 通过 set_* 更新本地 state) ---

    def _apply_draft_edit(self, **changes: object) -> None:
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

    # --- 研究角色 Prompt 命令（F35 / J26） ---

    def set_role_append(self, role_key: str, value: str) -> None:
        """更新角色追加草稿并即时重算合成预览（未知变量保留草稿并标记字段错误）。"""
        updated = tuple(
            _build_role_prompt_state(role_key, value, role.saved_version) if role.role_key == role_key else role
            for role in self._state.role_prompts
        )
        self._apply_draft_edit(role_prompts=updated)

    def reset_role_append(self, role_key: str) -> None:
        """恢复默认仅清空目标角色的追加内容，不影响其他角色（取消/保存由用户决定）。"""
        self.set_role_append(role_key, "")

    def get_role_prompt(self, role_key: str) -> RolePromptState | None:
        for role in self._state.role_prompts:
            if role.role_key == role_key:
                return role
        return None

    # --- W-18 研究配置统一入口（角色 / 摘要策略；单一来源 utils/config/llm.py） ---

    def _apply_research_edit(self, **changes: object) -> None:
        """研究配置草稿编辑：复位本组保存反馈，不影响本页其它保存状态机。"""
        self._set_state(**changes, research_save_state=SAVE_IDLE, research_warning_key="")

    def set_research_role(self, role_key: str) -> None:
        if role_key not in RESEARCH_ROLE_KEYS:
            return
        self._apply_research_edit(research_role=role_key)

    def set_research_summary_enabled(self, value: bool) -> None:
        self._apply_research_edit(research_summary_enabled=bool(value))

    def set_research_summary_keep_messages(self, value: str) -> None:
        self._apply_research_edit(research_summary_keep_messages_value=value)

    def set_research_summary_prompt(self, value: str) -> None:
        self._apply_research_edit(
            research_summary_prompt_value=value,
            research_summary_prompt_is_default=_normalize_summary_prompt(value) == RESEARCH_SUMMARY_PROMPT_DEFAULT,
        )

    def reset_research_summary_prompt(self) -> None:
        """恢复默认摘要资产（内容等于默认资产时按「未自定义」持久化，跟随默认资产升级）。"""
        self._apply_research_edit(
            research_summary_prompt_value=RESEARCH_SUMMARY_PROMPT_DEFAULT,
            research_summary_prompt_is_default=True,
        )

    async def save_research_config(self) -> bool:
        """保存研究配置（角色 + 摘要策略），单次原子写，失败整体未提交。

        - 保留条数须为 1..50 的整数（与 ``utils/config/llm.py`` 同界）；
        - 摘要 Prompt 等于默认资产时以空串落盘（「未自定义」，未来默认资产升级自动生效）；
        - 保存成功后按配置来源重载本组字段，确保「改一处两处生效」（设置页与配置读数一致）；
        - R2: ``CancelledError`` 显式传播；阻塞写经 ``ThreadPoolManager`` IO 池（R16）。
        """
        if self._state.research_save_state == SAVE_SAVING:
            return False

        keep_raw = (self._state.research_summary_keep_messages_value or "").strip()
        try:
            keep = int(keep_raw)
        except (TypeError, ValueError):
            self._set_state(research_save_state=SAVE_ERROR, research_warning_key="research_config_keep_invalid")
            return False
        if not (RESEARCH_SUMMARY_KEEP_MESSAGES_MIN <= keep <= RESEARCH_SUMMARY_KEEP_MESSAGES_MAX):
            self._set_state(research_save_state=SAVE_ERROR, research_warning_key="research_config_keep_invalid")
            return False

        prompt_value = self._state.research_summary_prompt_value
        stored_prompt = (
            "" if _normalize_summary_prompt(prompt_value) == RESEARCH_SUMMARY_PROMPT_DEFAULT else prompt_value
        )

        self._set_state(research_save_state=SAVE_SAVING, research_warning_key="")
        try:
            saved = await ThreadPoolManager().run_async(
                TaskType.IO,
                save_agent_research_config,
                self._state.research_role,
                self._state.research_summary_enabled,
                keep,
                stored_prompt,
            )
            if not saved:
                self._set_state(research_save_state=SAVE_ERROR, research_warning_key="")
                return False
            self._set_state(**_research_config_fields(save_state=SAVE_SUCCESS))
            return True
        except asyncio.CancelledError:
            self._set_state(research_save_state=SAVE_ERROR, research_warning_key="")
            raise  # R2: 必须传播
        except Exception as ex:
            from utils.error_classifier import classify_error, classify_severity

            error_info = classify_error(ex, context="general")
            severity = classify_severity(ex, context="general")
            sanitized = DataSanitizer.sanitize_error(ex)  # R9 异常消息脱敏
            log = logger.critical if severity == "system" else logger.error
            log(
                "[AIBrainSettingsVM] Error saving research config (%s): %s",
                error_info["code"],
                sanitized,
                exc_info=True,
            )
            self._set_state(research_save_state=SAVE_ERROR, research_warning_key="")
            return False

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

        # F35 / J26: 角色追加含未知/空变量时保留草稿并报字段错误，不猜值、不静默替换。
        role_error_key = next(
            (role.variable_error_key for role in self._state.role_prompts if role.variable_error_key),
            "",
        )
        if role_error_key:
            self._set_state(save_state=SAVE_ERROR, warning_message=role_error_key)
            logger.warning("[AIBrainSettingsVM] role prompt validation failed: %s", role_error_key)
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

            # F35: 与读取草稿值同一同步段捕获角色追加与版本（await 之前捕获，
            # 保存完成时按发起值确认，不把保存后又编辑的追加伪装为已绑定）。
            captured_role_appends = {role.role_key: role.append_value for role in self._state.role_prompts}
            captured_role_versions = {
                role.role_key: role.draft_version
                for role in self._state.role_prompts
                if role.append_value != self._saved_role_appends.get(role.role_key, "")
            }

            def _save_configs_sync() -> bool:
                """调优参数 + prompts 单次原子保存, 在 IO 线程池执行 (R16, T3)。

                任一 prompt 校验失败或 save_config 失败时整体未提交,
                不存在部分成功 (R21 邻域: 部分成功不得伪装成已保存)。
                角色追加仅在确有变化时原子写入（默认/其他业务 Prompt 不受影响）。
                """
                if not ConfigHandler.save_ai_tuning_and_prompts(max_cand, min_turn, local_only, ai_prompt, news_prompt):
                    return False
                if not captured_role_versions:
                    return True
                changed_appends = {role_key: captured_role_appends[role_key] for role_key in captured_role_versions}
                return ConfigHandler.save_ai_role_prompts(changed_appends, captured_role_versions)

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
                # 角色绑定版本只记录本次已落盘的发起值（旧请求绑定不变）。
                self._saved_role_appends.update(
                    {role_key: captured_role_appends[role_key] for role_key in captured_role_versions}
                )
                self._set_state(
                    role_prompts=self._apply_saved_role_versions(captured_role_versions),
                    save_state=SAVE_STALE,
                    saved_revision=captured_revision,
                    warning_message=SAVED_STALE_MESSAGE_KEY,
                )
                return True

            self._saved_role_appends.update(
                {role_key: captured_role_appends[role_key] for role_key in captured_role_versions}
            )
            self._set_state(
                role_prompts=self._apply_saved_role_versions(captured_role_versions),
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

    def _apply_saved_role_versions(self, versions: dict[str, str]) -> tuple[RolePromptState, ...]:
        """把本次已落盘的角色绑定版本写回 state；未参与本次保存的角色保持原绑定。"""
        return tuple(
            replace(role, saved_version=versions[role.role_key]) if role.role_key in versions else role
            for role in self._state.role_prompts
        )

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
