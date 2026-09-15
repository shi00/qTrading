"""应用偏好域：onboarding / theme / locale / log / scheduler 时辰 / 轮询等。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的应用偏好方法，仅按域搬移、不改行为。本模块所有共享状态与
跨方法访问一律经 ``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

from utils import config_handler as cfg
from utils.config_models import AI_EGRESS_SCOPE_VERSION, AI_EXTERNAL_ACK_GLOBAL_KEY

DEFAULTS = cfg.ConfigHandler.DEFAULT_CONFIG


def is_onboarding_complete():
    return cfg.ConfigHandler.get_typed("onboarding_complete", bool, DEFAULTS["onboarding_complete"])


def set_onboarding_complete(complete=True):
    return cfg.ConfigHandler.save_config({"onboarding_complete": complete})


def is_ai_external_acknowledged(provider: str | None = None, *, scope_version: int = AI_EGRESS_SCOPE_VERSION) -> bool:
    """Task 2.2 / AI-04 / SEC-01: 用户是否已确认某 provider 的 AI 外发数据知情政策。

    存储值语义（SEC-01）：``ai_external_acknowledged`` 为 ``{provider: scope_version}``，
    即「该 provider 在哪个外发范围版本下被确认过」。仅当记录值不小于当前
    ``scope_version`` 时视为已确认——外发内容范围版本升级（向 prompt 新增数据类别时
    ＂AI_EGRESS_SCOPE_VERSION 递增）后，旧确认自动失效，强制用户重新确认。

    ``provider=None`` 用于 UI 层「是否展示外发提示文案」的宽松判断（任意 provider 在当前
    版本下已确认即 True）；传入具体 provider 时精确判断「该 provider 是否确认过」。历史
    全局确认（旧 bool ``true`` 迁移到的 ``__global__`` 键，值视为 scope_version 1）对任意
    provider 回落——仅当当前版本仍与迁移版本一致时生效。

    兼容读取：迁移前的 ``bool`` 记录（True）视作 scope_version 1。
    """

    def _version(record: object) -> int:
        # 迁移兜底：旧 bool ``True`` / 数值记录都解释为版本号。
        if record is True:
            return 1
        try:
            return int(record)
        except (TypeError, ValueError):
            return 0

    ack_dict = cfg.ConfigHandler.get_typed("ai_external_acknowledged", dict, {})
    if provider is None:
        return any(_version(v) >= scope_version for v in ack_dict.values())
    # 精确 provider 优先；无专属记录时回落全局确认（等价于以 scope_version 1 确认过）
    provider_version = _version(ack_dict.get(provider))
    global_version = _version(ack_dict.get(AI_EXTERNAL_ACK_GLOBAL_KEY))
    return provider_version >= scope_version or global_version >= scope_version


def set_ai_external_acknowledged(
    provider: str, acknowledged: bool, *, scope_version: int = AI_EGRESS_SCOPE_VERSION
) -> bool:
    """Task 2.2 / AI-04 / SEC-01: 按 provider 持久化 AI 外发知情确认状态。

    每次确认都以当前外发范围版本写入；scope_version 升级后旧记录自动失效。
    """
    ack_dict = cfg.ConfigHandler.get_typed("ai_external_acknowledged", dict, {})
    ack_dict[provider] = scope_version if bool(acknowledged) else 0
    return cfg.ConfigHandler.set_typed("ai_external_acknowledged", ack_dict)


def is_auto_update_enabled():
    return cfg.ConfigHandler.get_typed("auto_update_enabled", bool, DEFAULTS["auto_update_enabled"])


def is_ai_local_only_mode() -> bool:
    """SEC-03 第一步: 「仅本地模式」——禁用全部云端 LLM，仅使用本地模型。"""
    return bool(cfg.ConfigHandler.get_typed("ai_local_only_mode", bool, DEFAULTS["ai_local_only_mode"]))


def set_ai_local_only_mode(enabled: bool) -> bool:
    """SEC-03 第一步: 设置「仅本地模式」开关。"""
    return cfg.ConfigHandler.set_typed("ai_local_only_mode", bool(enabled))


def get_auto_update_time():
    return cfg.ConfigHandler.get_typed("auto_update_time", str, DEFAULTS["auto_update_time"])


def get_log_level():
    return cfg.ConfigHandler.get_typed("log_level", str, DEFAULTS["log_level"]).upper()


def set_log_level(level):
    return cfg.ConfigHandler.set_typed("log_level", level.upper())


def get_log_format():
    return cfg.ConfigHandler.get_typed("log_format", str, DEFAULTS["log_format"]).lower()


def set_log_format(log_format):
    return cfg.ConfigHandler.set_typed("log_format", log_format.lower())


def get_log_max_mb():
    return cfg.ConfigHandler.get_typed("log_max_mb", int, DEFAULTS["log_max_mb"])


def get_log_backup_count():
    return cfg.ConfigHandler.get_typed("log_backup_count", int, DEFAULTS["log_backup_count"])


def get_init_history_years() -> int:
    return cfg.ConfigHandler.get_typed("init_history_years", int, DEFAULTS["init_history_years"])


def set_init_history_years(years: int):
    years = max(1, min(5, int(years)))
    return cfg.ConfigHandler.set_typed("init_history_years", years)


def is_ai_concept_schedule_enabled() -> bool:
    return cfg.ConfigHandler.get_typed("ai_concept_schedule_enabled", bool, DEFAULTS["ai_concept_schedule_enabled"])


def set_ai_concept_schedule_enabled(enabled: bool):
    return cfg.ConfigHandler.set_typed("ai_concept_schedule_enabled", bool(enabled))


def get_ai_concept_schedule_time() -> str:
    return cfg.ConfigHandler.get_typed("ai_concept_schedule_time", str, DEFAULTS["ai_concept_schedule_time"])


def set_ai_concept_schedule_time(time_str: str):
    return cfg.ConfigHandler.set_typed("ai_concept_schedule_time", str(time_str))


def get_ai_concept_search_engine() -> str:
    return cfg.ConfigHandler.get_typed("ai_concept_search_engine", str, DEFAULTS["ai_concept_search_engine"])


def set_ai_concept_search_engine(engine: str):
    return cfg.ConfigHandler.set_typed("ai_concept_search_engine", str(engine))


def get_nightly_prediction_time() -> str:
    """Task 7.3: 获取夜间 AI 预测时辰."""
    return cfg.ConfigHandler.get_typed("nightly_prediction_time", str, DEFAULTS["nightly_prediction_time"])


def set_nightly_prediction_time(time_str: str) -> bool:
    """Task 7.3: 设置夜间 AI 预测时辰 (HH:MM)."""
    return cfg.ConfigHandler.set_typed("nightly_prediction_time", str(time_str))


def get_locale():
    # spec.md §3 不变量 4：默认 "zh_CN"
    return cfg.ConfigHandler.get_typed("locale", str, DEFAULTS["locale"])


def set_locale(locale):
    return cfg.ConfigHandler.set_typed("locale", locale)


def get_theme_name():
    return cfg.ConfigHandler.get_typed("theme_name", str, DEFAULTS["theme_name"])


def set_theme_name(theme_name):
    return cfg.ConfigHandler.set_typed("theme_name", theme_name)


def get_no_proxy_domains():
    """Get domains that should BYPASS proxy (NO_PROXY)."""
    config = cfg.ConfigHandler.load_config()
    val = config.get("no_proxy_domains", [])
    if isinstance(val, list):
        return list(val)
    return []


# Alias for backward compatibility if needed, but we refactored callers
get_proxy_domains = get_no_proxy_domains


def set_no_proxy_domains(domains):
    if not isinstance(domains, list) or not all(isinstance(x, str) for x in domains):
        cfg.logger.error("Invalid no-proxy domains format: must be list of strings")
        return False
    return cfg.ConfigHandler.save_config({"no_proxy_domains": domains})


def get_max_cpu_workers():
    """Get max CPU threads from config."""
    return cfg.ConfigHandler.get_typed("max_cpu_workers", int, DEFAULTS["max_cpu_workers"])


def set_max_cpu_workers(count):
    return cfg.ConfigHandler.set_typed("max_cpu_workers", int(count))


def get_max_concurrent_tasks():
    """Get max concurrent tasks for TaskManager.

    Defaults to cpu_workers count to avoid overwhelming the CPU pool.
    Falls back to 5 if neither is configured.
    """
    val = cfg.ConfigHandler.get_typed("max_concurrent_tasks", int, DEFAULTS["max_concurrent_tasks"])
    if val > 0:
        return val
    cpu = cfg.ConfigHandler.get_max_cpu_workers()
    return cpu if cpu > 0 else 5


def set_max_concurrent_tasks(count):
    return cfg.ConfigHandler.set_typed("max_concurrent_tasks", int(count))


def get_news_poll_interval():
    return cfg.ConfigHandler.get_typed("news_poll_interval", int, DEFAULTS["news_poll_interval"])


def set_news_poll_interval(seconds):
    return cfg.ConfigHandler.set_typed("news_poll_interval", int(max(10, seconds)))


def get_market_data_poll_interval():
    return cfg.ConfigHandler.get_typed("market_data_poll_interval", int, DEFAULTS["market_data_poll_interval"])


def set_market_data_poll_interval(seconds):
    return cfg.ConfigHandler.set_typed("market_data_poll_interval", int(max(10, seconds)))
