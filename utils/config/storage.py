"""配置持久化基础设施：加载/保存/原子写/RWLock/缓存/深合并/迁移。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler``，仅按域搬移、不改行为。为保持现有 2396 行单测对
``utils.config_handler` 命名空间的 mock 有效，本模块所有共享状态与跨方法访问
一律经 ``cfg = utils.config_handler`` 间接引用。
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import json
import os
from collections.abc import Callable
from collections.abc import Iterator

import filelock
from pydantic import ValidationError

from utils import config_handler as cfg
from utils.config_models import (
    AI_EXTERNAL_ACK_GLOBAL_KEY,
    AppConfig,
    ConfigValidationResult,
    ModelManagementSaveResult,
    SecurityStateCorruptionError,
    get_default_config,
    model_management_payload_fingerprint,
    validate_security_subdomain_integrity,
)
from utils.sanitizers import DataSanitizer

# 跨进程配置文件锁等待上限（§5.1：锁等待有界，超时显示可重试错误）。
_CONFIG_FILE_LOCK_TIMEOUT_SECONDS = 10.0


class ConfigFreshReadError(RuntimeError):
    """新鲜读取时磁盘损坏 / 不可读 / 校验失败（§5.1：禁止静默落默认）。"""


class ConfigFileLockTimeoutError(RuntimeError):
    """跨进程配置文件锁等待超时（可重试）。"""


@contextlib.contextmanager
def _config_file_lock() -> Iterator[filelock.FileLock]:
    """跨进程配置文件锁（filelock，同一路径锁文件）。

    锁顺序固定为「文件锁 → RWLock」；所有会替换配置文件的路径
    （save_config / ensure_defaults / compare_and_save_model_management）同用此顺序，
    锁内重读磁盘，只合并调用方实际变更键，不用旧缓存整字典覆盖新内容（§5.1）。
    RWLock 只保证进程内并发，跨进程互斥由本文件锁承载。
    """
    lock = filelock.FileLock(f"{cfg.CONFIG_FILE}.lock", timeout=_CONFIG_FILE_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            yield lock
    except filelock.Timeout as e:
        raise ConfigFileLockTimeoutError(
            f"配置文件锁等待超时（{_CONFIG_FILE_LOCK_TIMEOUT_SECONDS}s），请重试: {DataSanitizer.sanitize_error(e)}"
        ) from e


def _read_disk_dict() -> tuple[dict | None, str | None]:
    """读磁盘原始 JSON（无锁，调用方必须已持「文件锁 → RWLock」）。

    Returns:
        (data, error)：文件不存在返回 (None, None)；损坏 / 不可读返回 (None, 脱敏错误)。
    """
    if not os.path.exists(cfg.CONFIG_FILE):
        return None, None
    try:
        with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, DataSanitizer.sanitize_error(e)
    if not isinstance(data, dict):
        return None, "config root is not an object"
    return data, None


def _open_builtin():
    """返回内置 ``open`` 的可调用对象。

    review05-E11 迁移兼容：原实现在 ``utils/config_handler.py`` 函数体内直接调用未限定
    ``open()``，单测以 ``patch.object(cfg_mod, "open", create=True)`` 在 config_handler
    命名空间注入 mock。move to storage.py 后需经 ``cfg`` 命名空间回退读取该 mock；
    生产路径 ``cfg.open`` 不存在，回退内置 ``open``，行为不变。upgrade: 调用点归零后可删除。
    """
    return getattr(cfg, "open", open)


def _clear_cache() -> None:
    """Clear the in-memory config cache.

    Intended for test isolation only — prevents cross-test state leakage
    when tests modify config on disk.  Production code should never need
    this because ``save_config`` / ``ensure_defaults`` already keep the
    cache in sync.
    """
    with cfg.ConfigHandler._lock.gen_wlock():
        cfg.ConfigHandler._config_cache = None


def get_typed[T](key: str, expected_type: type[T], default: T) -> T:
    """类型安全的通用 getter。

    E12: 当配置值存在但类型转换失败时，不再静默回落默认值，而记 WARNING
    （含配置项名、脱敏后的非法值、实际使用的默认值）。
    """
    config = cfg.ConfigHandler.load_config()
    val = config.get(key, default)
    try:
        if expected_type is bool and isinstance(val, str):
            return expected_type(val.lower() == "true")  # type: ignore[return-value]
        return expected_type(val)  # type: ignore[call-arg]
    except (ValueError, TypeError):
        cfg.logger.warning(
            "[ConfigHandler] Invalid value for %s: %s. Falling back to default: %s",
            key,
            DataSanitizer.sanitize_token(str(val)),
            DataSanitizer.sanitize_token(str(default)),
        )
        return default


def set_typed(key: str, value: object, validator: Callable[..., bool] | None = None) -> bool:
    if validator and not validator(value):
        display_value = DataSanitizer.sanitize_token(str(value)) if key in cfg.SENSITIVE_KEYS else value
        cfg.logger.warning("[ConfigHandler] Validation failed for %s: %s", key, display_value)
        return False
    return cfg.ConfigHandler.save_config({key: value})


def _deep_merge_defaults(current: dict, defaults: dict) -> tuple[dict, bool]:
    """Recursively merge default values into current config.

    Returns:
        (merged_config, dirty) - merged config and whether any changes were made
    """
    result = current.copy()
    dirty = False

    for key, default_val in defaults.items():
        if key not in result:
            result[key] = default_val
            dirty = True
        elif isinstance(default_val, dict) and isinstance(result.get(key), dict):
            nested_result, nested_dirty = cfg.ConfigHandler._deep_merge_defaults(result[key], default_val)
            if nested_dirty:
                result[key] = nested_result
                dirty = True

    return result, dirty


def _normalize_ai_external_acknowledged(config: dict) -> bool:
    """AI-04 / SEC-01 配置迁移：将旧形态 ``ai_external_acknowledged`` 统一为 ``dict[str, int]``。

    迁移历史：
    - AI-04：旧单一 ``bool`` 迁移为 ``{AI_EXTERNAL_ACK_GLOBAL_KEY: bool}``，
      表示用户曾做全局知情确认，避免升级后被迫重复确认。
    - SEC-01：记录值从 ``bool`` 升级为 ``scope_version:int``（确认时的外发范围版本）。
      ``bool True`` / dict 中的 ``True`` 均迁移为版本 1（与当前 AI_EGRESS_SCOPE_VERSION 对齐）。
      旧 ``False`` 等价于未确认，迁移为 0。

    Returns:
        bool: 是否发生了迁移（dirty）。
    """
    val = config.get("ai_external_acknowledged")
    if isinstance(val, bool):
        config["ai_external_acknowledged"] = {AI_EXTERNAL_ACK_GLOBAL_KEY: 1 if val else 0}
        return True
    if isinstance(val, dict):
        dirty = False
        new_dict: dict = {}
        for key, record in val.items():
            if record is True:
                new_dict[key] = 1
                dirty = True
            elif record is False:
                new_dict[key] = 0
                dirty = True
            else:
                new_dict[key] = record
        if dirty:
            config["ai_external_acknowledged"] = new_dict
        return dirty
    return False


def _migrate_custom_models_credentials(current_config: dict) -> bool:
    """迁移 custom_models 中的凭证到 llm_provider_credentials。

    旧格式: llm_custom_models: {provider: {api_key..., base_url..., models...}}
    新格式: llm_custom_models: {provider: [model_id...,]} + llm_provider_credentials。

    Returns:
        bool: 是否进行了迁移。
    """
    custom_models = current_config.get("llm_custom_models", {})
    if not custom_models:
        return False

    needs_migration = False
    provider_credentials = current_config.get("llm_provider_credentials", {})
    cleaned_custom_models: dict[str, list[str]] = {}

    for provider, value in custom_models.items():
        if isinstance(value, list):
            cleaned_custom_models[provider] = [str(m) for m in value]
        elif isinstance(value, dict):
            needs_migration = True

            if value.get("api_key"):
                try:
                    cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, f"ai_api_key_{provider}", str(value["api_key"]))
                # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
                except Exception as e:
                    cfg.logger.debug(
                        "[ConfigHandler] Config encrypt fallback triggered: %s",
                        DataSanitizer.sanitize_error(e),
                        exc_info=True,
                    )
                    encrypted = cfg.SecurityManager.encrypt_data(str(value["api_key"]))
                    cred = provider_credentials.get(provider, {})
                    cred["api_key_encrypted"] = encrypted
                    provider_credentials[provider] = cred

            if value.get("base_url"):
                cred = provider_credentials.get(provider, {})
                cred["base_url"] = str(value["base_url"])
                provider_credentials[provider] = cred

            if isinstance(value.get("models"), list):
                cleaned_custom_models[provider] = [str(m) for m in value["models"]]

            cfg.logger.info("[ConfigHandler] Migrated credentials from custom_models for provider: %s", provider)

    if needs_migration:
        current_config["llm_custom_models"] = cleaned_custom_models
        current_config["llm_provider_credentials"] = provider_credentials
        cfg.logger.info("[ConfigHandler] Credential migration from custom_models completed")

    for provider, cred in provider_credentials.items():
        if "models" in cred:
            if provider not in cleaned_custom_models and isinstance(cred["models"], list):
                cleaned_custom_models[provider] = [str(m) for m in cred["models"]]
                cfg.logger.info("[ConfigHandler] Migrated 'models' from credentials to custom_models for: %s", provider)

            del cred["models"]
            needs_migration = True
            cfg.logger.info("[ConfigHandler] Removed legacy 'models' from credentials for: %s", provider)

    if needs_migration and cleaned_custom_models:
        current_config["llm_custom_models"] = cleaned_custom_models

    return needs_migration


def _save_json_atomically(data, path):
    """Helper: Atomic write for JSON config."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_file = path + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_file, path)
        return True
    # NOTE(lazy): 配置文件 IO 失败兜底. ceiling: 系统级磁盘故障/权限拒绝. upgrade: 引入文件可读性预检或重试.
    except Exception as e:
        cfg.logger.error("Atomic save failed for %s: %s", path, DataSanitizer.sanitize_error(e))
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        return False


def _migrate_config_location() -> None:
    """把 APP_ROOT 下的旧 user_settings.json 一次性迁移到 USER_DATA_ROOT。

    仅在未用 ``ASTOCK_CONFIG_FILE`` 覆盖（即 CONFIG_FILE 为默认用户数据目录路径）、
    新文件缺失、旧文件存在时复制一次；迁移后**保留源文件**，防降级安装失效。
    惰性在读写入口触发，避免 import 副作用；幂等，重复调用为 no-op。
    """
    if os.environ.get("ASTOCK_CONFIG_FILE"):
        return
    target = cfg.CONFIG_FILE
    if os.path.exists(target):
        return
    source = cfg._CONFIG_FILE_LEGACY
    if not os.path.exists(source):
        return
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # 字节级复制，保留原始内容与注释格式（一次性迁移，非热路径）。
        import shutil

        shutil.copy2(source, target)
        cfg.logger.info("[ConfigHandler] Migrated user settings to %s", target)
    except OSError as e:
        cfg.logger.warning(
            "[ConfigHandler] Config migration from legacy path skipped: %s",
            DataSanitizer.sanitize_error(e),
        )


def ensure_defaults():
    """Ensure default settings exist AND remove unused keys from user_settings.json.

    Uses the cross-process file lock and write lock for the entire
    read-modify-write cycle (§5.1: 文件锁 → RWLock，锁内重读磁盘，防止 TOCTOU
    与跨进程覆盖)。

    F10：磁盘损坏 / 暂不可读时中止补全与清理（不写盘、不动缓存），保留损坏原件
    字节供用户手工恢复——与 save_config 的「损坏拒写」语义对齐，禁止从空 dict
    重建默认覆盖现场（会连带丢失连接凭据、撤销事实与迁移上下文）。

    F08 边界（划归 F10）：磁盘 JSON 合法但已初始化 ``model_management`` 安全子域
    结构损坏（缺安全三键）时同样中止——深合并会把缺失的 ``security_state`` 补成
    默认空安全态（零修订 / 空 grants / 空撤销）洗白后落盘，违反 fail closed。
    """
    _migrate_config_location()
    try:
        with _config_file_lock():
            with cfg.ConfigHandler._lock.gen_wlock():
                data, err = _read_disk_dict()
                if err is not None:
                    cfg.logger.error(
                        "[ConfigHandler] Config file unreadable, keeping file untouched"
                        " (skipping defaults/cleanup): %s",
                        err,
                    )
                    return
                if data is None:
                    current_config = {}
                else:
                    current_config = data
                    try:
                        validate_security_subdomain_integrity(current_config.get("model_management"))
                    except SecurityStateCorruptionError as e:
                        cfg.logger.error(
                            "[ConfigHandler] ensure_defaults: security subdomain corruption,"
                            " aborting to avoid whitewash: %s",
                            DataSanitizer.sanitize_error(e),
                        )
                        return

                dirty = False

                if _normalize_ai_external_acknowledged(current_config):
                    dirty = True

                for key, default_val in cfg.ConfigHandler.DEFAULT_CONFIG.items():
                    if key not in current_config:
                        current_config[key] = default_val
                        dirty = True
                        cfg.logger.info("Initialized default config: %s", key)
                    elif isinstance(default_val, dict) and isinstance(current_config.get(key), dict):
                        nested_result, nested_dirty = cfg.ConfigHandler._deep_merge_defaults(
                            current_config[key], default_val
                        )
                        if nested_dirty:
                            current_config[key] = nested_result
                            dirty = True
                            cfg.logger.info("Updated nested config: %s", key)

                valid_keys = set(cfg.ConfigHandler.DEFAULT_CONFIG.keys())
                existing_keys = list(current_config.keys())

                for key in existing_keys:
                    if key.startswith("ai_strategy_prompt_"):
                        continue
                    if key not in valid_keys:
                        cfg.logger.info("Removing deprecated/unused config: %s", key)
                        current_config.pop(key)
                        dirty = True

                if cfg.ConfigHandler._migrate_custom_models_credentials(current_config):
                    dirty = True

                if dirty:
                    success = cfg.ConfigHandler._save_json_atomically(current_config, cfg.CONFIG_FILE)
                    if success:
                        cfg.ConfigHandler._config_cache = current_config
                    cfg.logger.info(
                        "Configuration (defaults & cleanup) synchronized. Cleared deprecated keys: %s",
                        set(existing_keys) - valid_keys,
                    )

    # NOTE(lazy): 配置管理整体兜底避免单点失败阻断流程. ceiling: 配置管理内部逻辑不应抛异常. upgrade: 配置管理内部统一走 classify_error.
    except Exception as e:
        cfg.logger.error("Failed to ensure default config: %s", DataSanitizer.sanitize_error(e))


def load_config():
    """Load config with Read Lock and Validation."""
    _migrate_config_location()
    with cfg.ConfigHandler._lock.gen_rlock():
        if cfg.ConfigHandler._config_cache is not None:
            return cfg.ConfigHandler._config_cache.copy()

        if os.path.exists(cfg.CONFIG_FILE):
            try:
                with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
                    raw_data = json.load(f)
                    # AI-04: 先把旧 bool 形态迁移为 dict，否则 AppConfig.model_validate 会
                    # 因类型不符抛 ValidationError，导致整份配置回退默认（丢失其它配置）。
                    # 此处仅做读时内存迁移（不落盘：读锁下不可再取写锁保存，避免死锁）；
                    # 持久化由 ensure_defaults（写锁）及后续 save_config 复用归一化后的缓存完成。
                    _normalize_ai_external_acknowledged(raw_data)
                    # F10：与 F08 同一损坏语义——已初始化安全子域结构损坏时禁止把
                    # Pydantic 默认值补出的空安全态装入缓存（缓存命中路径不再校验，
                    # 不变量改为「所有缓存写入点均已校验或中止」）。此处沿用本函数对
                    # ValidationError 的既有降级语义（显式记日志 + 落默认缓存，不写回
                    # 磁盘）；安全关键消费方按契约走 read_model_management_fresh（fail closed）。
                    try:
                        validate_security_subdomain_integrity(raw_data.get("model_management"))
                    except SecurityStateCorruptionError as e:
                        cfg.logger.error(
                            "[ConfigHandler] Config security subdomain corruption, using defaults (file preserved): %s",
                            DataSanitizer.sanitize_error(e),
                        )
                        cfg.ConfigHandler._config_cache = get_default_config()
                        return cfg.ConfigHandler._config_cache.copy()
                    validated = AppConfig.model_validate(raw_data)
                    cfg.ConfigHandler._config_cache = validated.model_dump()
                    return cfg.ConfigHandler._config_cache.copy()
            except ValidationError as e:
                cfg.logger.warning("[ConfigHandler] Config validation failed: %s", DataSanitizer.sanitize_error(e))
                cfg.ConfigHandler._config_cache = get_default_config()
                return cfg.ConfigHandler._config_cache.copy()
            # NOTE(lazy): 配置文件 IO 失败兜底. ceiling: 系统级磁盘故障/权限拒绝. upgrade: 引入文件可读性预检或重试.
            except Exception as e:
                cfg.logger.warning(
                    "[ConfigHandler] Failed to load config file, using defaults: %s",
                    DataSanitizer.sanitize_error(e),
                )
                cfg.ConfigHandler._config_cache = get_default_config()
                return cfg.ConfigHandler._config_cache.copy()
        return {}


def load_config_with_validation() -> ConfigValidationResult:
    """加载配置并返回验证详情 (供 UI 层使用)."""
    _migrate_config_location()
    with cfg.ConfigHandler._lock.gen_rlock():
        if os.path.exists(cfg.CONFIG_FILE):
            try:
                with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
                    raw_data = json.load(f)
                    # AI-04: 读时内存迁移旧 bool 形态，与 load_config 一致（同样不落盘避免读锁取写锁死锁）。
                    _normalize_ai_external_acknowledged(raw_data)
                    validated = AppConfig.model_validate(raw_data)
                    return ConfigValidationResult(
                        is_valid=True,
                        config=validated.model_dump(),
                        errors=[],
                        used_defaults=False,
                    )
            except ValidationError as e:
                errors = []
                for err in e.errors():
                    err_str = str(err)
                    if err.get("input") is not None and err.get("loc") and err["loc"][-1] in cfg.SENSITIVE_KEYS:
                        err_str = err_str.replace(str(err["input"]), "***")
                    errors.append(err_str)
                return ConfigValidationResult(
                    is_valid=False,
                    config=get_default_config(),
                    errors=errors,
                    used_defaults=True,
                )
            # NOTE(lazy): 配置验证失败降级返回错误详情不阻断 UI. ceiling: 配置验证逻辑异常. upgrade: 配置验证统一走 classify_error.
            except Exception as e:
                return ConfigValidationResult(
                    is_valid=False,
                    config={},
                    errors=[DataSanitizer.sanitize_error(e)],
                    used_defaults=False,
                )
        return ConfigValidationResult(
            is_valid=True,
            config=get_default_config(),
            errors=[],
            used_defaults=True,
        )


def save_config(config_data, replace=False):
    """Save config with File Lock, Write Lock, Validation and Atomic Write.

    :param config_data: Dict to save
    :param replace: If True, replaces entire config with config_data. If False, merges.

    §5.1: 锁顺序固定「文件锁 → RWLock」；锁内重读磁盘为基底，只合并调用方
    实际变更键，不用旧缓存整字典覆盖新内容（防止跨进程丢失他进程写入）。
    磁盘损坏时中止保存返回 False，不以空/缓存基底覆盖损坏文件（保留用户
    可手工恢复现场）。
    """
    _migrate_config_location()
    try:
        with _config_file_lock():
            with cfg.ConfigHandler._lock.gen_wlock():
                if replace:
                    current_config = config_data.copy()
                else:
                    data, err = _read_disk_dict()
                    if err is not None:
                        cfg.logger.error(
                            "[ConfigHandler] Config file unreadable, aborting save to avoid overwrite: %s", err
                        )
                        return False
                    if data is not None:
                        # F08/F10 纵深拦截：磁盘安全子域已初始化但结构损坏时中止，
                        # 禁止经 model_validate 默认值补全洗白后落盘（JSON 损坏已由
                        # 上方 err 分支拒写，此处补齐「合法 JSON + 结构损坏」的拒写面）。
                        try:
                            validate_security_subdomain_integrity(data.get("model_management"))
                        except SecurityStateCorruptionError as e:
                            cfg.logger.error(
                                "[ConfigHandler] save_config: security subdomain corruption,"
                                " aborting to avoid whitewash: %s",
                                DataSanitizer.sanitize_error(e),
                            )
                            return False
                        # AI-04/SEC-01: 磁盘基底与 load_config 对称，先归一化旧形态，
                        # 否则 model_validate 因类型不符抛 ValidationError → 静默 False。
                        _normalize_ai_external_acknowledged(data)
                        current_config = data
                    elif cfg.ConfigHandler._config_cache is not None:
                        current_config = cfg.ConfigHandler._config_cache.copy()
                    else:
                        current_config = {}
                    current_config.update(config_data)
                    # F09 遗留①纵深拦截：合并结果的安全子域仍须结构完整——普通
                    # save_config 按契约不得携带 model_management 域（该域只走 CAS），
                    # 调用方若经此入口带入部分安全子域，禁止以 Pydantic 默认值洗白落盘。
                    try:
                        validate_security_subdomain_integrity(current_config.get("model_management"))
                    except SecurityStateCorruptionError as e:
                        cfg.logger.error(
                            "[ConfigHandler] save_config: merged security subdomain incomplete, aborting: %s",
                            DataSanitizer.sanitize_error(e),
                        )
                        return False

                try:
                    validated = AppConfig.model_validate(current_config)
                    current_config = validated.model_dump()
                except ValidationError as e:
                    cfg.logger.error("[ConfigHandler] Invalid config data: %s", DataSanitizer.sanitize_error(e))
                    return False

                success = cfg.ConfigHandler._save_json_atomically(current_config, cfg.CONFIG_FILE)

                if success:
                    cfg.ConfigHandler._config_cache = current_config
                    return True
                return False
    # NOTE(lazy): 配置管理整体兜底避免单点失败阻断流程. ceiling: 配置管理内部逻辑不应抛异常. upgrade: 配置管理内部统一走 classify_error.
    except Exception as e:
        cfg.logger.error("Error saving config: %s", DataSanitizer.sanitize_error(e))
        return False


def compare_and_save_model_management(
    expected_revision: int,
    payload: dict,
    operation_id: str,
    *,
    update_security_state: bool = False,
) -> ModelManagementSaveResult:
    """CAS 保存 model_management 域（§5.2）。

    锁顺序固定「文件锁 → RWLock」；锁内重读磁盘比对 revision，成功时把
    config_revision+1 与 last_config_commit 回执随同一次 os.replace 落盘。
    结果明确前不自动重试、不轮换凭据、不回滚（由调用方决定下一步）。

    update_security_state=False（save_draft 草稿路径）：payload 携带的安全键被
    无条件剥离（草稿不能修改 security_state）；磁盘已有安全域时回填磁盘现值，
    首次合法初始化（磁盘无安全域）交由 schema 默认值落出明确空安全状态
    （零修订 / 空 grants / 空撤销）。
    update_security_state=True（安全命令专用路径）：payload 安全子域原样提交。

    F08：磁盘上已初始化的安全子域存在结构损坏（安全键缺失等）时确定不提交
    （not_committed），禁止把损坏经 Pydantic 默认值洗白为合法空安全态后落盘。

    F09：剥离不依赖磁盘已有安全域——无文件 / 旧配置首次初始化时 payload 自带
    grants 同样不得经草稿入口写入（授权只能来自安全命令）。
    """
    _migrate_config_location()
    try:
        with _config_file_lock():
            with cfg.ConfigHandler._lock.gen_wlock():
                data, err = _read_disk_dict()
                if err is not None:
                    cfg.logger.error("[ConfigHandler] compare_and_save: config file unreadable, aborting: %s", err)
                    return ModelManagementSaveResult(status="not_committed")

                base = data if data is not None else {}
                disk_mm = base.get("model_management")
                try:
                    validate_security_subdomain_integrity(disk_mm)
                except SecurityStateCorruptionError as e:
                    cfg.logger.error(
                        "[ConfigHandler] compare_and_save: security subdomain corruption, aborting: %s",
                        DataSanitizer.sanitize_error(e),
                    )
                    return ModelManagementSaveResult(status="not_committed")
                if isinstance(disk_mm, dict):
                    try:
                        current_revision = int(disk_mm.get("config_revision", 1))
                    except (TypeError, ValueError):
                        current_revision = 1
                else:
                    current_revision = 1

                if current_revision != expected_revision:
                    return ModelManagementSaveResult(status="revision_conflict", current_revision=current_revision)

                merged_mm = copy.deepcopy(payload)
                if not update_security_state:
                    # F09：草稿路径无条件剥离输入安全子域（授权只能经安全命令修改），
                    # 不依赖磁盘已有安全域；磁盘已有安全域时以磁盘现值为准，首次合法
                    # 初始化（磁盘无安全域）保持缺失，由 schema 默认值落出明确空安全态。
                    merged_mm.pop("security_state", None)
                    if isinstance(disk_mm, dict) and "security_state" in disk_mm:
                        merged_mm["security_state"] = copy.deepcopy(disk_mm["security_state"])

                committed_revision = current_revision + 1
                merged_mm["config_revision"] = committed_revision
                merged_mm["last_config_commit"] = {
                    "operation_id": operation_id,
                    "committed_revision": committed_revision,
                    "payload_fingerprint": model_management_payload_fingerprint(merged_mm),
                    "committed_at": dt.datetime.now(dt.UTC).isoformat(),
                }

                new_config = copy.deepcopy(base)
                new_config["model_management"] = merged_mm

                try:
                    validated = AppConfig.model_validate(new_config)
                except ValidationError as e:
                    cfg.logger.error(
                        "[ConfigHandler] compare_and_save: invalid payload, aborting: %s",
                        DataSanitizer.sanitize_error(e),
                    )
                    return ModelManagementSaveResult(status="not_committed", current_revision=current_revision)

                final_config = validated.model_dump()
                if not cfg.ConfigHandler._save_json_atomically(final_config, cfg.CONFIG_FILE):
                    return ModelManagementSaveResult(status="not_committed", current_revision=current_revision)
                cfg.ConfigHandler._config_cache = final_config
                return ModelManagementSaveResult(
                    status="committed",
                    current_revision=committed_revision,
                    committed_revision=committed_revision,
                    last_config_commit=final_config["model_management"].get("last_config_commit"),
                )
    except ConfigFileLockTimeoutError:
        return ModelManagementSaveResult(status="lock_timeout", current_revision=None)
    # NOTE(lazy): 配置管理整体兜底避免单点失败阻断流程. ceiling: 配置管理内部逻辑不应抛异常. upgrade: 配置管理内部统一走 classify_error.
    except Exception as e:
        cfg.logger.error("compare_and_save_model_management failed: %s", DataSanitizer.sanitize_error(e))
        return ModelManagementSaveResult(status="not_committed")


def read_model_management_fresh() -> dict:
    """新鲜读取完整配置（§5.2）：磁盘为唯一事实源，绕过进程内缓存。

    成功时刷新进程内缓存并返回深拷贝完整配置 dict（model_management 域由
    调用方按 ``fresh["model_management"]`` 取用）。磁盘损坏 / 不可读 /
    校验失败 / 已初始化安全子域结构损坏（F08）时抛 :class:`ConfigFreshReadError`，
    禁止静默落默认。
    """
    _migrate_config_location()
    with _config_file_lock():
        with cfg.ConfigHandler._lock.gen_rlock():
            data, err = _read_disk_dict()
            if err is not None:
                raise ConfigFreshReadError(f"config file unreadable: {err}")
            if data is None:
                raise ConfigFreshReadError("config file does not exist")
            try:
                _normalize_ai_external_acknowledged(data)
                validate_security_subdomain_integrity(data.get("model_management"))
                validated = AppConfig.model_validate(data)
            except ValidationError as e:
                raise ConfigFreshReadError(f"config validation failed: {DataSanitizer.sanitize_error(e)}") from e
            except SecurityStateCorruptionError as e:
                raise ConfigFreshReadError(f"security subdomain corruption: {DataSanitizer.sanitize_error(e)}") from e
            fresh = validated.model_dump()
            cfg.ConfigHandler._config_cache = fresh
            return copy.deepcopy(fresh)


def _persist_migration(update: dict, migration_name: str) -> bool:
    """P3-Config-Return-Propagation-Gaps: 读时迁移路径持久化助手。

    Returns:
        True 表示 save_config 成功；False 表示失败（已记 warning，调用方可忽略）。
    """
    success = cfg.ConfigHandler.save_config(update)
    if not success:
        cfg.logger.warning(
            "[ConfigHandler] config migration failed: %s (will retry on next startup)",
            migration_name,
        )
    return success


def get_config(key, default=None):
    config = cfg.ConfigHandler.load_config()
    return config.get(key, default)


def get_setting(key, default=None):
    config = cfg.ConfigHandler.load_config()
    return config.get(key, default)
