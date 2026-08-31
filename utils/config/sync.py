"""同步域：sync / tushare / rate limits。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 sync 相关方法，仅按域搬移、不改行为。本模块所有共享状态与
跨方法访问一律经 ``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

from utils import config_handler as cfg

DEFAULTS = cfg.ConfigHandler.DEFAULT_CONFIG


def get_sync_concurrency():
    """Alias for get_sync_max_concurrent_heavy() for backward compatibility."""
    return cfg.ConfigHandler.get_sync_max_concurrent_heavy()


def set_sync_concurrency(concurrency):
    """Alias for set_sync_max_concurrent_heavy() for backward compatibility."""
    return cfg.ConfigHandler.set_sync_max_concurrent_heavy(int(concurrency))


def get_max_batch_rows():
    return cfg.ConfigHandler.get_typed("max_batch_rows", int, DEFAULTS["max_batch_rows"])


def set_max_batch_rows(rows):
    return cfg.ConfigHandler.set_typed("max_batch_rows", int(rows))


def get_sync_max_concurrent_heavy():
    """按当前 tushare_point_tier 动态 clamp 并发上限。

    - points_120 → 1
    - points_2000 → 3
    - points_5000/10000/15000 → 8
    - 未知/非法 → 8（保守兜底，正常情况下 Pydantic pattern 已拦截）
    """
    val = cfg.ConfigHandler.get_typed("sync_max_concurrent_heavy", int, DEFAULTS["sync_max_concurrent_heavy"])
    tier = cfg.ConfigHandler.get_tushare_point_tier()
    tier_limit = {"points_120": 1, "points_2000": 3}.get(tier, 8)
    return max(1, min(val, tier_limit))


def set_sync_max_concurrent_heavy(val):
    safe_val = max(1, min(int(val), 8))
    return cfg.ConfigHandler.save_config({"sync_max_concurrent_heavy": safe_val})


def get_sync_batch_size():
    val = cfg.ConfigHandler.get_typed("sync_batch_size", int, DEFAULTS["sync_batch_size"])
    return max(5, min(val, 200))


def set_sync_batch_size(val):
    safe_val = max(5, min(int(val), 200))
    return cfg.ConfigHandler.save_config({"sync_batch_size": safe_val})


def get_sync_full_batch_size():
    val = cfg.ConfigHandler.get_typed("sync_full_batch_size", int, DEFAULTS["sync_full_batch_size"])
    return max(10, min(val, 500))


def set_sync_full_batch_size(val):
    safe_val = max(10, min(int(val), 500))
    return cfg.ConfigHandler.save_config({"sync_full_batch_size": safe_val})


def get_sync_retry_count():
    return cfg.ConfigHandler.get_typed("request_max_retries", int, DEFAULTS["request_max_retries"])


def get_request_max_retries():
    return cfg.ConfigHandler.get_typed("request_max_retries", int, DEFAULTS["request_max_retries"])


def get_tushare_timeout():
    return cfg.ConfigHandler.get_typed("tushare_timeout", int, DEFAULTS["tushare_timeout"])


def set_tushare_timeout(seconds):
    return cfg.ConfigHandler.set_typed("tushare_timeout", int(seconds))


def get_tushare_point_tier():
    return cfg.ConfigHandler.get_typed("tushare_point_tier", str, DEFAULTS["tushare_point_tier"])


def set_tushare_point_tier(tier):
    from utils.constants import TUSHARE_POINT_TIERS

    valid_tiers = set(TUSHARE_POINT_TIERS)
    if tier not in valid_tiers:
        return False
    return cfg.ConfigHandler.set_typed("tushare_point_tier", str(tier))


def get_sync_integrity_config():
    """获取数据完整性检查配置."""
    config = cfg.ConfigHandler.load_config()
    defaults = DEFAULTS["sync_integrity"]
    sync_integrity = config.get("sync_integrity", defaults)
    return {
        "quotes_tolerance_ratio": sync_integrity.get("quotes_tolerance_ratio", defaults["quotes_tolerance_ratio"]),
        "indicators_tolerance_ratio": sync_integrity.get(
            "indicators_tolerance_ratio", defaults["indicators_tolerance_ratio"]
        ),
        "moneyflow_tolerance_ratio": sync_integrity.get(
            "moneyflow_tolerance_ratio", defaults["moneyflow_tolerance_ratio"]
        ),
        "financial_min_periods": sync_integrity.get("financial_min_periods", defaults["financial_min_periods"]),
        "quality_threshold": sync_integrity.get("quality_threshold", defaults["quality_threshold"]),
        "quality_weights": sync_integrity.get("quality_weights", defaults["quality_weights"]),
    }


def get_sync_request_delay(is_heavy=False):
    if is_heavy:
        return cfg.ConfigHandler.get_typed("sync_request_delay_heavy", float, 0.0)
    return cfg.ConfigHandler.get_typed("sync_request_delay_light", float, 0.0)


def set_sync_request_delay(delay, is_heavy=False):
    key = "sync_request_delay_heavy" if is_heavy else "sync_request_delay_light"
    return cfg.ConfigHandler.set_typed(key, float(delay))
