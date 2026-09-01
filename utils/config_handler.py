# ruff: noqa: F401  # re-export facade: 多个模块级名字仅供领域模块（cfg.<name>）与单测 mock 读取，此处定义即"用"
import contextlib
import contextvars
import json
import logging
import os
from typing import TypeVar

import keyring
from readerwriterlock import rwlock

import config
from utils.config_models import (
    AppConfig,
    ConfigValidationResult,
    DEFAULT_AI_PROMPT,
    DEFAULT_NEWS_PROMPT,
    get_default_config,
)
from utils.sanitizers import DataSanitizer
from utils.security_utils import DecryptionError, SecurityError, SecurityManager

logger = logging.getLogger(__name__)

CONFIG_FILE = os.environ.get("ASTOCK_CONFIG_FILE") or os.path.join(config.APP_ROOT, "user_settings.json")
KEYRING_SERVICE_NAME = "AStockScreener"

ENV_FALLBACK_MAP = {
    "ts_token": "TS_TOKEN",
    "db_password": "DB_PASSWORD",
    "ai_api_key": "AI_API_KEY",
}

# NOTE(lazy): 配置敏感键集合供 set_typed / load_config_with_validation 脱敏使用。
# 同时被领域模块经 ``cfg.SENSITIVE_KEYS`` 引用。ceiling: 新增敏感配置键时需手动并入
# 此集合，可能遗漏. upgrade: 由配置 schema/元数据自动推导敏感键集合.
SENSITIVE_KEYS = frozenset({"ts_token", "db_password", "db_password_encrypted", "ai_api_key"})


class ConfigHandler:
    """配置总门口（薄 facade，review05-E11）。

    迁徙期临时状态：原 ``utils/config_handler.py`` 的按域逻辑已迁至
    ``utils/config/{storage,secrets,db,llm,sync,app_prefs}.py``。本类仅保留共享
    状态与按域转发方法，以维持既有调用点与单测 mock（``patch("utils.config_handler.
    ConfigHandler.xxx")`` / ``patch("utils.config_handler.keyring")`` 等）不被破坏。

    调用点归零后应删除本 facade 或将其降级为最小 re-export。
    """

    _config_cache = None
    _lock = rwlock.RWLockFair()
    _io_workers_cap_warned: bool = False
    # P3-M4-DbUrlOverride-Mock-In-Prod: async-safe thread-local override for
    # get_db_url(). Propagated to worker threads via contextvars.copy_context()
    _db_url_override: contextvars.ContextVar[str | None] = contextvars.ContextVar("_db_url_override", default=None)
    # Priority 1.5 (embedded): 模块级完整 URL override，跨 asyncio task / 线程一致可见。
    _embedded_db_url: str | None = None

    DEFAULT_CONFIG = get_default_config()

    T = TypeVar("T")

    # === storage 域 ===
    @classmethod
    def _clear_cache(cls) -> None:
        return storage._clear_cache()

    @staticmethod
    def get_typed(key: str, expected_type: type[T], default: T) -> T:
        return storage.get_typed(key, expected_type, default)

    @staticmethod
    def set_typed(key: str, value: object, validator=None) -> bool:
        return storage.set_typed(key, value, validator)

    @staticmethod
    def _deep_merge_defaults(current: dict, defaults: dict) -> tuple[dict, bool]:
        return storage._deep_merge_defaults(current, defaults)

    @staticmethod
    def _migrate_custom_models_credentials(current_config: dict) -> bool:
        return storage._migrate_custom_models_credentials(current_config)

    @staticmethod
    def _save_json_atomically(data, path):
        return storage._save_json_atomically(data, path)

    @staticmethod
    def load_config():
        return storage.load_config()

    @staticmethod
    def load_config_with_validation():
        return storage.load_config_with_validation()

    @staticmethod
    def save_config(config_data, replace=False):
        return storage.save_config(config_data, replace)

    @staticmethod
    def ensure_defaults():
        return storage.ensure_defaults()

    @staticmethod
    def _persist_migration(update: dict, migration_name: str) -> bool:
        return storage._persist_migration(update, migration_name)

    @staticmethod
    def get_config(key, default=None):
        return storage.get_config(key, default)

    @staticmethod
    def get_setting(key, default=None):
        return storage.get_setting(key, default)

    # === secrets 域 ===
    @staticmethod
    def _try_decrypt(value):
        return secrets._try_decrypt(value)

    @staticmethod
    def get_token():
        return secrets.get_token()

    @staticmethod
    def save_token(token):
        return secrets.save_token(token)

    @staticmethod
    def get_db_password():
        return secrets.get_db_password()

    @staticmethod
    def save_db_password(password: str) -> bool:
        return secrets.save_db_password(password)

    @staticmethod
    def save_provider_credential(
        provider: str,
        api_key: str | None = None,
        base_url: str | None = None,
        models: list[str] | None = None,
    ) -> bool:
        return secrets.save_provider_credential(provider, api_key, base_url, models)

    @staticmethod
    def get_provider_credential(provider: str, fallback_to_global: bool = True) -> dict:
        return secrets.get_provider_credential(provider, fallback_to_global)

    @staticmethod
    def validate_failover_credentials() -> list[str]:
        return secrets.validate_failover_credentials()

    # === db 域 ===
    @classmethod
    def set_embedded_db_url(cls, url: str) -> None:
        return db.set_embedded_db_url(url)

    @classmethod
    def clear_embedded_db_url(cls) -> None:
        return db.clear_embedded_db_url()

    @staticmethod
    @contextlib.contextmanager
    def with_db_url_override(url: str):
        with db.with_db_url_override(url):
            yield

    @classmethod
    def is_embedded_mode(cls) -> bool:
        return db.is_embedded_mode()

    @staticmethod
    def get_db_url():
        return db.get_db_url()

    @staticmethod
    def save_db_config(host: str, port: int, user: str, password: str, database: str) -> bool:
        return db.save_db_config(host, port, user, password, database)

    @staticmethod
    def get_db_config() -> dict:
        return db.get_db_config()

    @staticmethod
    def get_db_connection_pool_size():
        return db.get_db_connection_pool_size()

    @staticmethod
    def set_db_connection_pool_size(size):
        return db.set_db_connection_pool_size(size)

    @staticmethod
    def get_db_pool_pre_ping():
        return db.get_db_pool_pre_ping()

    @staticmethod
    def get_db_pool_recycle():
        return db.get_db_pool_recycle()

    @staticmethod
    def get_db_pool_timeout():
        return db.get_db_pool_timeout()

    @staticmethod
    def set_db_pool_timeout(timeout):
        return db.set_db_pool_timeout(timeout)

    @staticmethod
    def get_db_max_overflow():
        return db.get_db_max_overflow()

    @staticmethod
    def set_db_max_overflow(overflow):
        return db.set_db_max_overflow(overflow)

    @staticmethod
    def get_max_io_workers():
        return db.get_max_io_workers()

    @classmethod
    def _reset_io_cap_warning(cls):
        return db._reset_io_cap_warning()

    @staticmethod
    def set_max_io_workers(count):
        return db.set_max_io_workers(count)

    # === llm 域 ===
    @staticmethod
    def get_llm_provider() -> str:
        return llm.get_llm_provider()

    @staticmethod
    def save_llm_config(provider: str, model: str, base_url: str, api_key: str | None = None, **kwargs) -> bool:
        return llm.save_llm_config(provider, model, base_url, api_key, **kwargs)

    @staticmethod
    def get_llm_config() -> dict:
        return llm.get_llm_config()

    @staticmethod
    def get_failover_config() -> dict:
        return llm.get_failover_config()

    @staticmethod
    def get_llm_config_for_provider(provider: str) -> dict:
        return llm.get_llm_config_for_provider(provider)

    @staticmethod
    def get_local_ai_timeout() -> int | None:
        return llm.get_local_ai_timeout()

    @staticmethod
    def set_local_ai_timeout(seconds: int) -> bool:
        return llm.set_local_ai_timeout(seconds)

    @staticmethod
    def get_local_ai_config() -> dict:
        return llm.get_local_ai_config()

    @staticmethod
    def save_local_ai_config(model_path: str, timeout: int = 30, **kwargs) -> bool:
        return llm.save_local_ai_config(model_path, timeout, **kwargs)

    @staticmethod
    def get_ai_system_prompt():
        return llm.get_ai_system_prompt()

    @staticmethod
    def save_ai_system_prompt(prompt):
        return llm.save_ai_system_prompt(prompt)

    @staticmethod
    def get_strategy_prompt(strategy_key):
        return llm.get_strategy_prompt(strategy_key)

    @staticmethod
    def set_strategy_prompt(strategy_key, prompt):
        return llm.set_strategy_prompt(strategy_key, prompt)

    @staticmethod
    def get_ai_news_prompt():
        return llm.get_ai_news_prompt()

    @staticmethod
    def set_ai_news_prompt(prompt):
        return llm.set_ai_news_prompt(prompt)

    @staticmethod
    def get_strategy_presets(strategy_key: str) -> dict[str, dict]:
        return llm.get_strategy_presets(strategy_key)

    @staticmethod
    def save_strategy_preset(strategy_key: str, name: str, params: dict) -> bool:
        return llm.save_strategy_preset(strategy_key, name, params)

    @staticmethod
    def delete_strategy_preset(strategy_key: str, name: str) -> bool:
        return llm.delete_strategy_preset(strategy_key, name)

    @staticmethod
    def get_ai_max_candidates():
        return llm.get_ai_max_candidates()

    @staticmethod
    def set_ai_max_candidates(val):
        return llm.set_ai_max_candidates(val)

    @staticmethod
    def get_ai_free_text_max_len():
        return llm.get_ai_free_text_max_len()

    @staticmethod
    def get_ai_max_concurrent_analysis():
        return llm.get_ai_max_concurrent_analysis()

    @staticmethod
    def set_ai_max_concurrent_analysis(val):
        return llm.set_ai_max_concurrent_analysis(val)

    @staticmethod
    def get_ai_news_max_concurrent():
        return llm.get_ai_news_max_concurrent()

    @staticmethod
    def set_ai_news_max_concurrent(val):
        return llm.set_ai_news_max_concurrent(val)

    @staticmethod
    def get_strategy_min_turnover():
        return llm.get_strategy_min_turnover()

    @staticmethod
    def set_strategy_min_turnover(val):
        return llm.set_strategy_min_turnover(val)

    # === sync 域 ===
    @staticmethod
    def get_sync_concurrency():
        return sync.get_sync_concurrency()

    @staticmethod
    def set_sync_concurrency(concurrency):
        return sync.set_sync_concurrency(concurrency)

    @staticmethod
    def get_max_batch_rows():
        return sync.get_max_batch_rows()

    @staticmethod
    def set_max_batch_rows(rows):
        return sync.set_max_batch_rows(rows)

    @staticmethod
    def get_sync_max_concurrent_heavy():
        return sync.get_sync_max_concurrent_heavy()

    @staticmethod
    def set_sync_max_concurrent_heavy(val):
        return sync.set_sync_max_concurrent_heavy(val)

    @staticmethod
    def get_sync_batch_size():
        return sync.get_sync_batch_size()

    @staticmethod
    def set_sync_batch_size(val):
        return sync.set_sync_batch_size(val)

    @staticmethod
    def get_sync_full_batch_size():
        return sync.get_sync_full_batch_size()

    @staticmethod
    def set_sync_full_batch_size(val):
        return sync.set_sync_full_batch_size(val)

    @staticmethod
    def get_sync_retry_count():
        return sync.get_sync_retry_count()

    @staticmethod
    def get_request_max_retries():
        return sync.get_request_max_retries()

    @staticmethod
    def get_tushare_timeout():
        return sync.get_tushare_timeout()

    @staticmethod
    def set_tushare_timeout(seconds):
        return sync.set_tushare_timeout(seconds)

    @staticmethod
    def get_tushare_point_tier():
        return sync.get_tushare_point_tier()

    @staticmethod
    def set_tushare_point_tier(tier):
        return sync.set_tushare_point_tier(tier)

    @staticmethod
    def get_sync_integrity_config():
        return sync.get_sync_integrity_config()

    @staticmethod
    def get_sync_request_delay(is_heavy=False):
        return sync.get_sync_request_delay(is_heavy)

    @staticmethod
    def set_sync_request_delay(delay, is_heavy=False):
        return sync.set_sync_request_delay(delay, is_heavy)

    # === app_prefs 域 ===
    @staticmethod
    def is_onboarding_complete():
        return app_prefs.is_onboarding_complete()

    @staticmethod
    def set_onboarding_complete(complete=True):
        return app_prefs.set_onboarding_complete(complete)

    @staticmethod
    def is_ai_external_acknowledged() -> bool:
        return app_prefs.is_ai_external_acknowledged()

    @staticmethod
    def set_ai_external_acknowledged(acknowledged: bool) -> bool:
        return app_prefs.set_ai_external_acknowledged(acknowledged)

    @staticmethod
    def is_auto_update_enabled():
        return app_prefs.is_auto_update_enabled()

    @staticmethod
    def get_auto_update_time():
        return app_prefs.get_auto_update_time()

    @staticmethod
    def get_log_level():
        return app_prefs.get_log_level()

    @staticmethod
    def set_log_level(level):
        return app_prefs.set_log_level(level)

    @staticmethod
    def get_log_format():
        return app_prefs.get_log_format()

    @staticmethod
    def set_log_format(log_format):
        return app_prefs.set_log_format(log_format)

    @staticmethod
    def get_log_max_mb():
        return app_prefs.get_log_max_mb()

    @staticmethod
    def get_log_backup_count():
        return app_prefs.get_log_backup_count()

    @classmethod
    def get_init_history_years(cls) -> int:
        return app_prefs.get_init_history_years()

    @classmethod
    def set_init_history_years(cls, years: int):
        return app_prefs.set_init_history_years(years)

    @classmethod
    def is_ai_concept_schedule_enabled(cls) -> bool:
        return app_prefs.is_ai_concept_schedule_enabled()

    @classmethod
    def set_ai_concept_schedule_enabled(cls, enabled: bool):
        return app_prefs.set_ai_concept_schedule_enabled(enabled)

    @classmethod
    def get_ai_concept_schedule_time(cls) -> str:
        return app_prefs.get_ai_concept_schedule_time()

    @classmethod
    def set_ai_concept_schedule_time(cls, time_str: str):
        return app_prefs.set_ai_concept_schedule_time(time_str)

    @classmethod
    def get_ai_concept_search_engine(cls) -> str:
        return app_prefs.get_ai_concept_search_engine()

    @classmethod
    def set_ai_concept_search_engine(cls, engine: str):
        return app_prefs.set_ai_concept_search_engine(engine)

    @classmethod
    def get_nightly_prediction_time(cls) -> str:
        return app_prefs.get_nightly_prediction_time()

    @classmethod
    def set_nightly_prediction_time(cls, time_str: str) -> bool:
        return app_prefs.set_nightly_prediction_time(time_str)

    @staticmethod
    def get_locale():
        return app_prefs.get_locale()

    @staticmethod
    def set_locale(locale):
        return app_prefs.set_locale(locale)

    @staticmethod
    def get_theme_name():
        return app_prefs.get_theme_name()

    @staticmethod
    def set_theme_name(theme_name):
        return app_prefs.set_theme_name(theme_name)

    @staticmethod
    def get_no_proxy_domains():
        return app_prefs.get_no_proxy_domains()

    # Alias for backward compatibility if needed
    get_proxy_domains = get_no_proxy_domains

    @staticmethod
    def set_no_proxy_domains(domains):
        return app_prefs.set_no_proxy_domains(domains)

    @staticmethod
    def get_max_cpu_workers():
        return app_prefs.get_max_cpu_workers()

    @staticmethod
    def set_max_cpu_workers(count):
        return app_prefs.set_max_cpu_workers(count)

    @staticmethod
    def get_max_concurrent_tasks():
        return app_prefs.get_max_concurrent_tasks()

    @staticmethod
    def set_max_concurrent_tasks(count):
        return app_prefs.set_max_concurrent_tasks(count)

    @staticmethod
    def get_news_poll_interval():
        return app_prefs.get_news_poll_interval()

    @staticmethod
    def set_news_poll_interval(seconds):
        return app_prefs.set_news_poll_interval(seconds)

    @staticmethod
    def get_market_data_poll_interval():
        return app_prefs.get_market_data_poll_interval()

    @staticmethod
    def set_market_data_poll_interval(seconds):
        return app_prefs.set_market_data_poll_interval(seconds)


# noqa: E402 — 领域模块在模块级 ``cfg.ConfigHandler.DEFAULT_CONFIG`` 上求值，
# 必须在本 facade 的 ``ConfigHandler`` 定义之后再导入，以避免循环导入
# （domain → utils.config_handler；见 utils/config/*.py 顶部 docstring）。
from utils.config import app_prefs, db, llm, secrets, storage, sync  # noqa: E402
