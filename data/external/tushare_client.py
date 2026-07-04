import asyncio
import datetime
import logging
import threading
import time
import typing
from collections.abc import Callable

import pandas as pd
import requests
import tushare as ts

from data.constants import attach_hsgt_column_units, attach_top_list_column_units
from utils.config_handler import ConfigHandler
from utils.rate_limiter import TokenBucket
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now
from utils.log_decorators import log_async_operation, track_performance, PerfThreshold

logger = logging.getLogger(__name__)


class TushareProApi(typing.Protocol):
    """Structural type for the tushare pro_api object (SDK lacks type stubs).

    Each attribute is a callable accepting keyword arguments and returning a DataFrame.
    Defining this Protocol eliminates # type: ignore[untyped] across 30+ API wrappers.
    """

    trade_cal: Callable[..., pd.DataFrame]
    stock_basic: Callable[..., pd.DataFrame]
    daily: Callable[..., pd.DataFrame]
    adj_factor: Callable[..., pd.DataFrame]
    daily_basic: Callable[..., pd.DataFrame]
    income: Callable[..., pd.DataFrame]
    cashflow: Callable[..., pd.DataFrame]
    balancesheet: Callable[..., pd.DataFrame]
    top_list: Callable[..., pd.DataFrame]
    top_inst: Callable[..., pd.DataFrame]
    hk_hold: Callable[..., pd.DataFrame]
    moneyflow: Callable[..., pd.DataFrame]
    block_trade: Callable[..., pd.DataFrame]
    fina_indicator: Callable[..., pd.DataFrame]
    disclosure_date: Callable[..., pd.DataFrame]
    concept: Callable[..., pd.DataFrame]
    concept_detail: Callable[..., pd.DataFrame]
    index_daily: Callable[..., pd.DataFrame]
    moneyflow_hsgt: Callable[..., pd.DataFrame]
    index_dailybasic: Callable[..., pd.DataFrame]
    limit_list_d: Callable[..., pd.DataFrame]
    suspend_d: Callable[..., pd.DataFrame]
    margin_detail: Callable[..., pd.DataFrame]
    fina_audit: Callable[..., pd.DataFrame]
    forecast: Callable[..., pd.DataFrame]
    fina_mainbz: Callable[..., pd.DataFrame]
    pledge_stat: Callable[..., pd.DataFrame]
    repurchase: Callable[..., pd.DataFrame]
    dividend: Callable[..., pd.DataFrame]
    shibor: Callable[..., pd.DataFrame]
    top10_holders: Callable[..., pd.DataFrame]
    index_weight: Callable[..., pd.DataFrame]
    stk_holdernumber: Callable[..., pd.DataFrame]
    cn_gdp: Callable[..., pd.DataFrame]
    stk_limit: Callable[..., pd.DataFrame]


class TushareAPIPermissionError(Exception):
    """
    P1-26 fix: Structured exception for Tushare API permission errors.

    Raised when the user's Tushare account lacks permission to access
    a specific API endpoint. This error should be caught by sync strategies
    to skip unavailable APIs and update UI capability indicators.
    """

    def __init__(self, api_name: str, message: str):
        self.api_name = api_name
        self.message = message
        super().__init__(f"Permission denied for API '{api_name}': {message}")

    def __str__(self) -> str:
        return f"TushareAPIPermissionError(api={self.api_name}, message={self.message})"


PERMISSION_DENIED_KEYWORDS = (
    "权限",
    "积分不足",
    "未授权",
    "请求接口的权限",
    "no permission",
    "permission denied",
    "没有权限",
    "无权访问",
    # Tushare 网关对未授权接口的伪装报错（实际是权限不足，但消息不含"权限"字样）
    "请指定正确的接口名",
)

# Token 认证失败关键字：触发全局熔断，避免无效 token 下每个 API 独立重试刷屏。
# 注意：必须与 PERMISSION_DENIED_KEYWORDS 严格分离，避免"积分不足"等 per-API 错误误触发全局熔断。
TOKEN_INVALID_KEYWORDS = ("您的token不对",)


from utils.singleton_registry import register_singleton


@register_singleton
class TushareClient:
    """
    Enhanced Tushare API client with timeout, retry, trade calendar support, and TokenBucket Rate Limiting.
    """

    pro: TushareProApi | None
    _instance = None
    _initialized = False
    _lock = threading.Lock()

    _ASYNC_TIMEOUT_MULTIPLIER = 1.5

    # Phase 2B §3.2.5：probe 专用桶配额（50/min 独立配额，避免被同步任务挤压）
    _PROBE_RATE_LIMIT_RPM = 50

    # Phase 2B §3.2.5：probe 互斥标志（单线程 asyncio 同步段内原子，避免档位切换/手动按钮/启动自动并发）
    _probe_in_progress: bool = False

    _COLUMN_RENAMES = {
        "cn_cpi": {"month": "period", "nt_val": "cpi"},
        "cn_ppi": {"month": "period", "ppi_yoy": "ppi"},
        "cn_m": {"month": "period"},
        # Phase 2D §3.2.6：cn_gdp API 返回 quarter 列，重命名为 period 统一处理
        "cn_gdp": {"quarter": "period"},
    }

    _SLOW_API_OVERRIDES: typing.ClassVar[dict[str, float]] = {
        "top10_holders": 0.5,
        "stk_holdernumber": 0.5,
        "concept_detail": 0.3,
        "top_list": 0.5,
        "top_inst": 0.5,
        "moneyflow": 0.5,
        "moneyflow_hsgt": 0.5,
        "hk_hold": 0.5,
        "limit_list_d": 0.5,
        "margin_detail": 0.5,
        "fina_audit": 0.5,
        "fina_mainbz": 0.5,
        "repurchase": 0.5,
        "income": 0.3,
        "balancesheet": 0.3,
        "cashflow": 0.3,
        "fina_indicator": 0.3,
        "disclosure_date": 0.5,
        "forecast": 0.5,
        "stk_limit": 0.5,
    }

    # 积分档位 → 全局 req/min 预设。官方档位对照见 docs/积分挡位.PNG
    # 官方实际频次只有三档：120分=50/min，2000/3000分=200/min，5000分及以上=500/min
    # 5000+ 积分频次均为 500/min，差异在于数据权限（更多特色接口）
    _POINT_TIER_PRESETS: typing.ClassVar[dict[str, int]] = {
        "points_120": 50,
        "points_2000": 200,
        "points_5000": 500,
        "points_10000": 500,
        "points_15000": 500,
    }

    # 档位顺序值（用于 get_tier_apis 合并低档位 API 集合）
    _TIER_ORDER: typing.ClassVar[dict[str, int]] = {
        "points_120": 0,
        "points_2000": 1,
        "points_5000": 2,
        "points_10000": 3,
        "points_15000": 4,
    }

    # 档位 API 覆盖映射（层层包含，每档只列新增项，get_tier_apis 合并低档位）
    # 按设计文档 v1.10.0 §3.2.1 完整定义
    _TIER_API_COVERAGE: typing.ClassVar[dict[str, frozenset[str]]] = {
        "points_120": frozenset(
            {
                # 50/min 档位：基础元数据 + 日线 + shibor + shibor_lpr
                "trade_cal",
                "stock_basic",
                "daily",
                "daily_basic",
                "adj_factor",
                "index_daily",
                "index_dailybasic",
                "index_weight",
                "shibor",
                "shibor_lpr",
            }
        ),
        "points_2000": frozenset(
            {
                # 200/min 档位：+ 财务报表 + 股东 + 龙虎榜 + 概念 + 宏观 + 资金流 + 市场异动
                "income",
                "balancesheet",
                "cashflow",
                "fina_indicator",
                "fina_mainbz",
                "fina_audit",
                "forecast",
                "dividend",
                "repurchase",
                "stk_holdernumber",
                "top10_holders",
                "pledge_stat",
                "pledge_detail",
                "top_list",
                "top_inst",
                "concept",
                "concept_detail",
                "moneyflow",
                "moneyflow_hsgt",
                "hk_hold",
                "block_trade",
                "limit_list_d",
                "suspend_d",
                "margin_detail",
                "stk_holdertrade",
                "stk_limit",
                "express",
                "index_classify",
                "index_member_all",
                "cn_m",
                "cn_cpi",
                "cn_ppi",
                "cn_gdp",
                "stock_company",
                "stk_managers",
                "stk_surv",
                "stk_factor_pro",
                "top10_floatholders",  # 假设 points_2000 可访问（待 probe 验证）
                "disclosure_date",
            }
        ),
        "points_5000": frozenset(
            {
                # 500/min 档位：+ share_float（3000 积分）+ 无日上限
                "share_float",
            }
        ),
        "points_10000": frozenset(
            {
                # 常规 500/min + 特色 300/min（特色数据需独立购买）
                "cyq_perf",
                "forecast_eps",  # 需独立购买
            }
        ),
        "points_15000": frozenset(
            {
                # 常规 500/min，特色无上限（特色数据仍需独立购买才能访问）
            }
        ),
    }

    # 需独立购买的特色数据 API（即使积分足够也需单独付费）
    _INDEPENDENT_PURCHASE_APIS: typing.ClassVar[frozenset[str]] = frozenset(
        {
            "cyq_perf",  # 筹码分布
            "forecast_eps",  # 盈利预测
            "rating",  # 券商评级（"券商研报库"独立权限）
        }
    )

    TABLE_TO_API_MAP: dict[str, str] = {
        "moneyflow_hsgt": "moneyflow_hsgt",
        "northbound_holding": "hk_hold",
        "moneyflow_daily": "moneyflow",
        "top_list": "top_list",
        "top_inst": "top_inst",
        "limit_list": "limit_list_d",
        "margin_daily": "margin_detail",
        "block_trade": "block_trade",
        "stk_limit": "stk_limit",
    }

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            if cls._instance is not None:
                if hasattr(cls._instance, "_bg_tasks"):
                    for t in cls._instance._bg_tasks:
                        t.cancel()
                    cls._instance._bg_tasks.clear()
                # 显式重置熔断标志，符合 _bg_tasks 显式清理风格（虽实例销毁后冗余，但防御性写法）
                if hasattr(cls._instance, "_token_invalid"):
                    cls._instance._token_invalid = False
            cls._instance = None
            cls._initialized = False

    @classmethod
    def _atexit_cleanup(cls):
        """Cancel background tasks on process exit."""
        if cls._instance is not None and hasattr(cls._instance, "_bg_tasks"):
            for t in cls._instance._bg_tasks:
                t.cancel()
            cls._instance._bg_tasks.clear()

    def _resolve_rate_limit(self) -> int:
        """Resolve effective rate limit based on point tier preset.

        Returns:
            Effective rate limit (requests per minute), or 0 if tier unknown.
        """
        tier = self._get_tushare_point_tier()
        return self._POINT_TIER_PRESETS.get(tier, 0)

    def reload_rate_limiters(self):
        """Rebuild rate limiters from current config. Call after tier/limit change in settings."""
        with self._lock:
            self._rate_limiter, self._api_limiters, self._probe_rate_limiter = self._build_rate_limiters()
        logger.info("[API] Rate limiters reloaded from config")

    def _build_rate_limiters(self) -> tuple[TokenBucket | None, dict[str, TokenBucket], TokenBucket]:
        """
        Build rate limiters based on config.

        Phase 2B §3.2.5: 返回三元组（全局 + per-API + probe 专用 50/min）。
        probe 专用桶与全局桶同步创建（避免 R11 跨循环复用同步原语）。
        """
        limit_per_min = self._resolve_rate_limit()
        if not limit_per_min or limit_per_min <= 0:
            logger.info("[API] Rate Limiter disabled (No limit set)")
            # probe 专用桶仍创建（probe 不依赖档位，独立 50/min 配额）
            probe_limiter = self._build_probe_rate_limiter()
            return None, {}, probe_limiter

        rate_per_sec = limit_per_min / 60.0
        capacity = max(10, rate_per_sec * 2)
        rate_limiter = TokenBucket(
            start_tokens=capacity,
            capacity=capacity,
            rate=rate_per_sec,
        )
        logger.info(
            "[API] Rate Limiter initialized: %s req/min (%.2f req/s)",
            limit_per_min,
            rate_per_sec,
        )

        api_limiters: dict[str, TokenBucket] = {}

        for api_name, factor in self._SLOW_API_OVERRIDES.items():
            slow_rate = rate_per_sec * factor
            slow_capacity = max(5, slow_rate * 2)
            api_limiters[api_name] = TokenBucket(
                start_tokens=slow_capacity,
                capacity=slow_capacity,
                rate=slow_rate,
            )
            logger.info(
                "[API] Slow API limiter for '%s': %.0f req/min (factor=%s)",
                api_name,
                slow_rate * 60,
                factor,
            )

        probe_limiter = self._build_probe_rate_limiter()
        return rate_limiter, api_limiters, probe_limiter

    def _build_probe_rate_limiter(self) -> TokenBucket:
        """Phase 2B §3.2.5: probe 专用桶（50/min 独立配额，与全局桶同步创建）。"""
        probe_rate_per_sec = self._PROBE_RATE_LIMIT_RPM / 60.0
        probe_capacity = max(5, probe_rate_per_sec * 2)
        return TokenBucket(
            start_tokens=probe_capacity,
            capacity=probe_capacity,
            rate=probe_rate_per_sec,
        )

    def __init__(self, token: str | None = None, *, config=None, clock=None):
        if self._initialized:
            if token and token != self.token:
                self.set_token(token)
            return

        with self._lock:
            if self._initialized:
                return

            self._config = config  # 若 None 则后续走 ConfigHandler
            self._clock = clock or time.monotonic  # 默认走 time.monotonic

            self._trade_cal_cache: set[str] = set()
            self._loaded_years: set[str] = set()
            self._calendar_lock = threading.Lock()

            self._capability_cache: dict[str, bool | None] = {}
            self._capability_cache_lock = threading.Lock()
            self._bg_tasks: set[asyncio.Task] = set()
            # 全局 token 熔断标志：token 失效时置 True，阻止后续 API 调用避免无效重试刷屏。
            # 由 set_token() 重置，_reset_singleton() 销毁实例时随实例回收。
            self._token_invalid: bool = False
            # Phase 2A.1 §3.2.10：上次 probe 时间，由 persist/load_capabilities_to_app_state 维护
            self._last_probe_time: datetime.datetime | None = None

            self.token = token or self._get_token()
            self.timeout = self._get_tushare_timeout()
            self.max_retries = self._get_request_max_retries()

            self._rate_limiter, self._api_limiters, self._probe_rate_limiter = self._build_rate_limiters()

            if self.token:
                ts.set_token(self.token)
                # Pass timeout to requests via tushare SDK
                # 显式传 token，避免依赖 tushare SDK 全局状态（~/tk.csv 或环境变量）
                # tushare SDK has no type stubs; cast to Protocol for typed access
                self.pro = typing.cast(TushareProApi, ts.pro_api(token=self.token, timeout=self.timeout))
                logger.info(
                    "[API] Tushare Client initialized with timeout=%ss",
                    self.timeout,
                )
            else:
                self.pro = None

            self._initialized = True

    def _get_token(self):
        if self._config is not None:
            return self._config.get_token()
        return ConfigHandler.get_token()

    def _get_tushare_timeout(self):
        if self._config is not None:
            return self._config.get_tushare_timeout()
        return ConfigHandler.get_tushare_timeout()

    def _get_request_max_retries(self):
        if self._config is not None:
            return self._config.get_request_max_retries()
        return ConfigHandler.get_request_max_retries()

    def _get_tushare_point_tier(self):
        if self._config is not None:
            return self._config.get_tushare_point_tier()
        return ConfigHandler.get_tushare_point_tier()

    @property
    def is_token_invalid(self) -> bool:
        """Token 是否已失效（用户需重新 set_token 后才能恢复）。"""
        return self._token_invalid

    def set_token(self, token: str | None) -> bool:
        """
        Set token and clear capability cache.

        Thread-safe: uses _lock to protect concurrent access.

        Args:
            token: New Tushare API token

        Returns:
            True if caller should trigger capability probe (cache was cleared or empty).
            False if no action needed (token unchanged).

        Note:
            This method is synchronous. Caller (usually async context like verify_token)
            should call probe_api_capabilities() if this returns True.

        Example:
            if client.set_token(new_token):
                await client.probe_api_capabilities()
        """
        with self._lock:
            if token == self.token:
                # 同 token 提交时也重置熔断标志：用户可能在 Tushare 官网修复了 token 权限但 token 字符串不变
                if self._token_invalid:
                    self._token_invalid = False
                    logger.info("[API] Token breaker reset (same token resubmitted)")
                logger.debug("[API] Token unchanged, skipping cache clear")
                return False

            old_token = self.token
            self.token = token
            ts.set_token(token)
            # 显式传 token，避免依赖 tushare SDK 全局状态
            self.pro = ts.pro_api(token=token, timeout=self.timeout) if token else None

            self._rate_limiter, self._api_limiters, self._probe_rate_limiter = self._build_rate_limiters()

            cache_size = len(self._capability_cache)
            self._capability_cache.clear()
            # 新 token 提交，重置熔断标志
            self._token_invalid = False

            logger.info(
                "[API] Token updated: %s -> %s. Cache cleared (%d entries).",
                DataSanitizer.sanitize_token(old_token or ""),
                DataSanitizer.sanitize_token(token or ""),
                cache_size,
            )
            return True

    def is_api_available(self, api_name: str) -> bool | None:
        """
        Check if an API is available for the current token.

        Returns:
            True: API is available
            False: API is known to be unavailable (permission denied)
            None: Unknown (not tested yet)
        """
        with self._capability_cache_lock:
            return self._capability_cache.get(api_name)

    def mark_api_unavailable(self, api_name: str) -> None:
        """Mark an API as unavailable for the current token."""
        with self._capability_cache_lock:
            self._capability_cache[api_name] = False
            logger.warning("[API] Capability cached: '%s' marked as UNAVAILABLE for current token", api_name)

    def mark_api_available(self, api_name: str) -> None:
        """Mark an API as available for the current token."""
        with self._capability_cache_lock:
            self._capability_cache[api_name] = True

    def clear_capability_cache(self) -> None:
        """Clear all cached capabilities. Call after token change."""
        with self._capability_cache_lock:
            self._capability_cache.clear()
            logger.info("[API] Capability cache cleared")

    def get_capability_cache(self) -> dict[str, bool | None]:
        """Get a copy of the capability cache."""
        with self._capability_cache_lock:
            return dict(self._capability_cache)

    def get_tier_order(self, tier: str) -> int:
        """返回档位的顺序值（公共方法，避免外部访问私有 _TIER_ORDER）。"""
        return self._TIER_ORDER.get(tier, 0)

    def get_tier_apis(self, tier: str | None = None) -> frozenset[str]:
        """获取档位覆盖的 API 集合（内部合并所有 ≤当前档位的集合）。

        _TIER_API_COVERAGE 每个档位只列新增项，本方法合并低档位。
        """
        tier = tier or self._get_tushare_point_tier()
        order = self._TIER_ORDER.get(tier, 0)
        return frozenset().union(
            *(apis for t, apis in self._TIER_API_COVERAGE.items() if self._TIER_ORDER.get(t, 0) <= order)
        )

    def is_api_covered_by_tier(self, api_name: str, tier: str | None = None) -> bool:
        """检查 API 是否被档位覆盖（不含独立付费判断）。"""
        return api_name in self.get_tier_apis(tier)

    def is_independent_purchase(self, api_name: str) -> bool:
        """检查 API 是否需要独立购买。"""
        return api_name in self._INDEPENDENT_PURCHASE_APIS

    async def _persist_capability_safely(self) -> None:
        """Fire-and-forget persistence of capability cache to AppState.

        Catches all exceptions so that persistence failure never disrupts
        the caller (typically _handle_api_call raising TushareAPIPermissionError).
        """
        try:
            await self.persist_capabilities_to_app_state()
        except Exception as exc:
            logger.debug(
                "[TushareClient] Capability persist failed (non-critical): %s", DataSanitizer.sanitize_error(exc)
            )

    def get_effective_synced_tables(self, all_tables: list[str]) -> list[str]:
        """
        Return list of tables that are available for the current token.

        Phase 2A.1 §3.2.7 双层过滤：
        - 第一层（档位覆盖）：API 必须在当前档位覆盖内（``is_api_covered_by_tier``）
        - 第二层（probe 验证）：API 在档位覆盖内但 probe 验证为 False 时排除；
          None（未探测）不阻塞（保留以允许首次启动尚未 probe 时同步基础数据）
        - 不在 TABLE_TO_API_MAP 的表（基础数据）始终包含
        - 独立付费 API（cyq_perf/forecast_eps）即使在档位覆盖内，仍受 probe 验证约束

        Args:
            all_tables: List of table names to filter

        Returns:
            List of table names that can be synced for current token
        """
        effective = []
        for table in all_tables:
            api_name = self.TABLE_TO_API_MAP.get(table)
            if api_name is None:
                # 基础数据表（无 API 依赖）：始终包含
                effective.append(table)
                continue
            # 第一层：档位覆盖
            if not self.is_api_covered_by_tier(api_name):
                # 档位不足：跳过同步（DB 历史数据保留，由 stale 标注机制处理）
                continue
            # 第二层：probe 验证（None 不阻塞）
            if self.is_api_available(api_name) is False:
                continue
            effective.append(table)
        return effective

    @log_async_operation(
        operation_name="TushareClient.persist_capabilities_to_app_state",
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def persist_capabilities_to_app_state(self) -> None:
        """
        Persist capability cache to AppState for cross-session durability.

        Writes a JSON payload containing:
        - token_hash: SHA256 hash of current token (first 16 chars)
        - capabilities: dict of api_name -> bool

        Called after probe_api_capabilities or when capabilities change.
        Safe to call when engine is not ready (no-op).
        """
        import hashlib
        import json

        from data.cache.cache_manager import CacheManager
        from data.persistence.app_state_service import set_app_state

        engine = CacheManager().engine
        if engine is None:
            logger.debug("[TushareClient] Engine not ready, skipping capability persist")
            return

        token_hash = hashlib.sha256(self.token.encode()).hexdigest()[:16] if self.token else None
        with self._capability_cache_lock:
            capabilities = dict(self._capability_cache)

        # Phase 2A.1 §3.2.10：追加 last_probe_time ISO 8601 字符串，用于启动时自动 probe 判断
        last_probe_iso = self._last_probe_time.isoformat() if self._last_probe_time else None
        payload = {
            "token_hash": token_hash,
            "capabilities": capabilities,
            "last_probe_time": last_probe_iso,
        }
        await set_app_state(engine, "tushare_capabilities", json.dumps(payload))
        logger.info("[TushareClient] Persisted %s capabilities to AppState", len(capabilities))

    @log_async_operation(
        operation_name="TushareClient.load_capabilities_from_app_state",
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def load_capabilities_from_app_state(self) -> None:
        """
        Load capability cache from AppState on startup.

        Only loads if token_hash matches current token.
        Called after CacheManager engine is created.
        """
        import hashlib
        import json

        from data.cache.cache_manager import CacheManager
        from data.persistence.app_state_service import get_app_state

        engine = CacheManager().engine
        if engine is None:
            return

        stored = await get_app_state(engine, "tushare_capabilities")
        if not stored:
            return

        try:
            payload = json.loads(stored)
            token_hash = hashlib.sha256(self.token.encode()).hexdigest()[:16] if self.token else None

            if payload.get("token_hash") == token_hash:
                with self._capability_cache_lock:
                    self._capability_cache.update(payload.get("capabilities", {}))
                # Phase 2A.1 §3.2.10：同步读取 last_probe_time（ISO 8601）
                last_probe_str = payload.get("last_probe_time")
                if last_probe_str:
                    try:
                        self._last_probe_time = datetime.datetime.fromisoformat(last_probe_str)
                    except (ValueError, TypeError):
                        logger.warning("[TushareClient] Invalid last_probe_time format: %s", last_probe_str)
                        self._last_probe_time = None
                else:
                    self._last_probe_time = None
                logger.info("[TushareClient] Loaded %s capabilities from AppState", len(self._capability_cache))
            else:
                logger.debug("[TushareClient] Token hash mismatch, skipping capability load")
        except Exception as e:
            logger.warning("[TushareClient] Failed to load capabilities: %s", DataSanitizer.sanitize_error(e))

    @log_async_operation(
        operation_name="TushareClient.probe_api_capabilities",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def probe_api_capabilities(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, bool | None]:
        """
        Probe key APIs to determine their availability for current token.

        Phase 2B §3.2.5 实测基础设施：
        - 入口互斥（``_probe_in_progress`` bool 标志，单线程 asyncio 同步段内原子）
        - 入口快照（取消/异常时回滚 ``_capability_cache``，避免部分污染）
        - 档位预筛（``is_api_covered_by_tier`` 过滤候选池）
        - 并行探测（semaphore=4 + ``gather_return_exceptions_propagating_cancel`` 传播 CancelledError）
        - 三态分类（True/False/None，None 不写入 ``_capability_cache``）
        - 服务不可用检测（None 比例 >80% 保留旧缓存）
        - Token 无效检测（False 比例 >90% 记 error 日志）
        - 进度回调（``progress_callback(completed, total)``）
        - 取消回滚 + finally 置 ``_probe_in_progress=False``

        Args:
            progress_callback: 可选进度回调，签名为 (completed_count, total_count)

        Returns:
            dict mapping API names to availability:
            - True: API is available
            - False: API is not available (permission denied)
            - None: Unable to determine (other error)
        """
        from utils.async_utils import gather_return_exceptions_propagating_cancel
        from utils.time_utils import get_now

        # probe 互斥：单线程 asyncio 同步段内 ``if _probe_in_progress`` 与
        # ``self._probe_in_progress = True`` 之间无 await，理论原子。
        if self._probe_in_progress:
            logger.warning("[TushareClient] Probe already in progress, skipping")
            # 浅拷贝（bool 不可变足够）；调用方检测 _probe_in_progress 决定 UI 反馈
            return dict(self._capability_cache)
        self._probe_in_progress = True

        # 入口快照：取消/异常时回滚到入口状态（v1.9.0 P0-3/M-3）
        cache_snapshot = dict(self._capability_cache)

        try:
            recent_date = get_now().strftime("%Y%m%d")
            PROBE_STOCK_CODE = "000001.SZ"
            PROBE_RECENT_PERIOD = f"{get_now().year - 1}1231"

            # 完整候选池（29 项 = 现有 12 + 追加 17 含 cyq_perf/forecast_eps 独立付费）
            probe_configs: list[tuple[str, dict]] = [
                # 现有 12 项
                ("daily", {"trade_date": recent_date}),
                ("moneyflow_hsgt", {"trade_date": recent_date}),
                ("moneyflow", {"trade_date": recent_date}),
                ("hk_hold", {"trade_date": recent_date}),
                ("top_list", {"trade_date": recent_date}),
                ("limit_list_d", {"trade_date": recent_date}),
                ("margin_detail", {"trade_date": recent_date}),
                ("block_trade", {"trade_date": recent_date}),
                ("fina_indicator", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
                ("fina_mainbz", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
                ("stk_holdernumber", {"ts_code": PROBE_STOCK_CODE, "enddate": PROBE_RECENT_PERIOD}),
                ("top10_holders", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
                # P0 必接入（5 个，官方确认 ≤5000）
                ("share_float", {"ts_code": PROBE_STOCK_CODE, "ann_date": recent_date}),
                ("stk_holdertrade", {"ts_code": PROBE_STOCK_CODE, "ann_date": recent_date}),
                ("index_classify", {"level": "L1", "src": "SW2021"}),
                ("index_member_all", {"index_code": "801010.SI"}),
                ("top_inst", {"trade_date": recent_date}),
                # P0 待实测（2 个）
                ("stk_factor_pro", {"ts_code": PROBE_STOCK_CODE, "trade_date": recent_date}),
                ("top10_floatholders", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
                # P1 推荐接入（4 个）
                ("stk_limit", {"ts_code": PROBE_STOCK_CODE, "trade_date": recent_date}),
                ("express", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
                ("pledge_detail", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
                ("shibor_lpr", {"date": recent_date}),
                # P1 待实测（3 个）
                ("stock_company", {"ts_code": PROBE_STOCK_CODE}),
                ("stk_managers", {"ts_code": PROBE_STOCK_CODE}),
                ("stk_surv", {"ts_code": PROBE_STOCK_CODE, "start_date": recent_date, "end_date": recent_date}),
                # cn_gdp 激活（1 个，v1.10.0 P0-1：quarter 参数）
                ("cn_gdp", {"quarter": f"{get_now().year - 1}Q4"}),
                # 独立付费特色数据（2 个，仅 points_10000+ 档位会探测）
                ("cyq_perf", {"ts_code": PROBE_STOCK_CODE, "trade_date": recent_date}),
                ("forecast_eps", {"ts_code": PROBE_STOCK_CODE, "period": PROBE_RECENT_PERIOD}),
            ]

            # 档位预筛：仅 probe 当前档位覆盖内的 API
            current_tier = self._get_tushare_point_tier()
            filtered_configs = [
                (api, params) for api, params in probe_configs if self.is_api_covered_by_tier(api, current_tier)
            ]
            skipped = len(probe_configs) - len(filtered_configs)
            if skipped > 0:
                logger.info(
                    "[TushareClient] Probe pre-filtered by tier=%s: %d candidates → %d to probe (skipped %d not covered)",
                    current_tier,
                    len(probe_configs),
                    len(filtered_configs),
                    skipped,
                )

            total = len(filtered_configs)
            if total == 0:
                self._last_probe_time = get_now()
                await self.persist_capabilities_to_app_state()
                return {}

            # 并行探测过滤后的候选（semaphore=4 + gather 传播 CancelledError）
            semaphore = asyncio.Semaphore(4)
            completed_counter = [0]  # list 包装以便闭包内修改

            async def _probe_with_progress(name: str, params: dict) -> tuple[str, bool | None]:
                result = await self._probe_one(semaphore, name, params)
                completed_counter[0] += 1
                if progress_callback is not None:
                    try:
                        progress_callback(completed_counter[0], total)
                    except Exception as cb_exc:  # pragma: no cover - UI 回调异常不应阻塞 probe
                        logger.warning("[TushareClient] Probe progress_callback failed: %s", cb_exc)
                return result

            results_list = await gather_return_exceptions_propagating_cancel(
                *[_probe_with_progress(name, params) for name, params in filtered_configs]
            )
            results: dict[str, bool | None] = {}
            for item in results_list:
                if isinstance(item, Exception):
                    # _probe_one 已捕获内部异常，此处 Exception 不应发生；
                    # 防御性处理：跳过该 item
                    logger.warning("[TushareClient] Probe returned unexpected exception: %s", item)
                    continue
                if isinstance(item, tuple):
                    api_name, available = item
                    results[api_name] = available

            # 服务不可用检测（None 比例 >80% 保留旧缓存，不清空）
            none_count = sum(1 for v in results.values() if v is None)
            if total > 0 and none_count / total > 0.8:
                logger.warning(
                    "[TushareClient] Probe %d/%d APIs returned None (network error?), Tushare service may be unavailable; "
                    "preserving existing _capability_cache for degraded run",
                    none_count,
                    total,
                )
                # 保留旧缓存，仅更新 last_probe_time 标记本次 probe 尝试过
                self._last_probe_time = get_now()
                await self.persist_capabilities_to_app_state()
                return self.get_capability_cache()

            # Token 无效检测（False 比例 >90% 记 error 日志）
            false_count = sum(1 for v in results.values() if v is False)
            if total > 0 and false_count / total > 0.9:
                logger.error(
                    "[TushareClient] Probe %d/%d APIs returned False (permission denied), Token may be invalid or积分严重不足",
                    false_count,
                    total,
                )

            # gather 全部成功后统一写入 _capability_cache（None 不写入，避免污染）
            for api_name, available in results.items():
                if available is True:
                    self.mark_api_available(api_name)
                elif available is False:
                    self.mark_api_unavailable(api_name)
                # None 不写入 _capability_cache（保持原值或不存在）

            self._last_probe_time = get_now()
            await self.persist_capabilities_to_app_state()
            return results
        except asyncio.CancelledError:
            # 取消时回滚 _capability_cache 到入口快照（R2 红线：raise 传播）
            logger.info("[TushareClient] Probe cancelled, rolling back _capability_cache to entry snapshot")
            self._capability_cache = cache_snapshot
            raise
        except Exception as exc:
            # 其他异常（网络抖动等非取消）也回退到入口快照，避免部分污染
            logger.warning(
                "[TushareClient] Probe failed, rolling back _capability_cache to entry snapshot: %s",
                exc,
            )
            self._capability_cache = cache_snapshot
            return self.get_capability_cache()
        finally:
            self._probe_in_progress = False

    async def _handle_probe_call(self, api_name: str, func: typing.Callable, **params: typing.Any) -> None:
        """Phase 2B §3.2.5: probe 专用调用 wrapper。

        与 ``_handle_api_call`` 的差异：
        - 两段消费：全局桶 + probe 专用桶（50/min 独立配额）
        - 复用 io_pool / timeout / DataSanitizer / 权限判定
        - 跳过 reduce_rate / on_success（probe 是一次性探测，不永久降速）
        - 权限拒绝抛 TushareAPIPermissionError（由 ``_probe_one`` 分类为 False）

        R2 红线：内部 await 必须响应 CancelledError，不吞没。
        """
        import contextvars
        import functools

        from utils.thread_pool import ThreadPoolManager

        if not self.pro:
            raise Exception("Tushare Token not set. Please set your token in settings.")

        # 两段消费：全局桶 + probe 专用桶
        if self._rate_limiter is not None:
            await self._rate_limiter.consume_async(1)
        await self._probe_rate_limiter.consume_async(1)

        # 格式化日期参数（与 _handle_api_call 一致）
        formatted_kwargs = {}
        for k, v in params.items():
            if isinstance(v, (datetime.date, datetime.datetime)):
                formatted_kwargs[k] = v.strftime("%Y%m%d")
            else:
                formatted_kwargs[k] = v

        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(
                    ThreadPoolManager().io_pool,
                    lambda ctx=ctx: ctx.run(functools.partial(func, **formatted_kwargs)),
                ),
                timeout=self.timeout * self._ASYNC_TIMEOUT_MULTIPLIER,
            )
        except Exception as e:
            error_msg = str(e)
            error_msg_lower = error_msg.lower()
            # 权限判定（复用 PERMISSION_DENIED_KEYWORDS + TOKEN_INVALID_KEYWORDS）
            is_token_invalid = any(k in error_msg_lower for k in TOKEN_INVALID_KEYWORDS)
            is_permission_error = is_token_invalid or any(k in error_msg_lower for k in PERMISSION_DENIED_KEYWORDS)
            if is_permission_error:
                # 权限拒绝抛 TushareAPIPermissionError，由 _probe_one 分类为 False
                raise TushareAPIPermissionError(api_name, error_msg) from e
            # 其他异常（429 / 网络错误等）原样抛出，由 _probe_one 分类为 None
            # 不调用 reduce_rate（probe 一次性探测，不永久降速）
            raise

    async def _probe_one(
        self,
        semaphore: asyncio.Semaphore,
        api_name: str,
        params: dict,
    ) -> tuple[str, bool | None]:
        """Phase 2B §3.2.5: 单个 API 探测，返回三态结果。

        三态分类：
        - True: API 可用（_handle_probe_call 成功）
        - False: 权限拒绝（TushareAPIPermissionError）
        - None: 未知（其他异常，如网络错误 / 429）

        v1.9.0 P0-3 修订：本方法**不**直接调用 ``mark_api_available`` / ``mark_api_unavailable``，
        只返回 ``(api_name, True/False/None)``。统一由 ``probe_api_capabilities`` 主体在 gather
        全部成功后写入 ``_capability_cache``，避免并行期间中间污染 + 取消时回滚失效。
        """
        from utils.error_classifier import classify_error, classify_severity

        async with semaphore:
            func = getattr(self.pro, api_name, None)
            if func is None:
                logger.warning("[TushareClient] Probe %s: API not found in SDK", api_name)
                return (api_name, None)
            try:
                await self._handle_probe_call(api_name, func, **params)
                return (api_name, True)
            except TushareAPIPermissionError:
                # 区分"积分不足"vs"需独立购买"
                if self.is_independent_purchase(api_name):
                    logger.info(
                        "[TushareClient] Probe %s: permission denied (requires independent purchase, points sufficient but not purchased)",
                        api_name,
                    )
                else:
                    logger.info("[TushareClient] Probe %s: permission denied (insufficient points)", api_name)
                return (api_name, False)
            except Exception as e:
                # 区分 429 限流 vs 网络错误（429 不 reduce_rate，仅记日志，下次 probe 重试）
                error_msg = str(e).lower()
                if "429" in error_msg or "rate" in error_msg:
                    logger.warning(
                        "[TushareClient] Probe %s: 429 rate limited (will retry next probe cycle)",
                        api_name,
                    )
                else:
                    # 使用 classify_error + classify_severity 分类（CLAUDE.md §5.7 错误处理标准模式）
                    error_type = classify_error(e, context="probe")
                    severity = classify_severity(e, context="probe")
                    log_level = logging.WARNING if severity != "system" else logging.ERROR
                    logger.log(
                        log_level,
                        "[TushareClient] Probe %s error (type=%s): %s",
                        api_name,
                        error_type.get("code", "unknown"),
                        DataSanitizer.sanitize_error(e),
                    )
                return (api_name, None)

    @log_async_operation(
        operation_name="tushare_api_call",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
        log_level=logging.DEBUG,
    )
    async def _handle_api_call(self, func: typing.Callable, **kwargs: typing.Any):
        """Async wrapper that yields to event loop during rate limit / backoff

        Adaptive Rate Limiting:
        - Per-API slow limiters for known throttled APIs (top10_holders, etc.)
        - On rate-limit error: reduce_rate() on the bucket (permanent slowdown)
        - On success: on_success() for gradual rate recovery
        - Shorter backoff (5-15s) instead of 60-240s exponential

        Capability Caching (P1-#26):
        - Check capability cache before making API call
        - Cache permission denied errors to avoid repeated failed calls
        - Clear cache on token change
        """
        import functools

        import functools as _functools

        from utils.thread_pool import ThreadPoolManager

        if isinstance(func, _functools.partial) and func.args:
            api_name = str(func.args[0])
        else:
            api_name = getattr(func, "__name__", str(func))

        capability = self.is_api_available(api_name)
        if capability is False:
            logger.debug("[tushare_api] SKIPPING %s: known unavailable (cached)", api_name)
            raise TushareAPIPermissionError(api_name, f"API '{api_name}' is cached as unavailable for current token")

        formatted_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, (datetime.date, datetime.datetime)):
                formatted_kwargs[k] = v.strftime("%Y%m%d")
            else:
                formatted_kwargs[k] = v
        kwargs = formatted_kwargs

        api_limiter = getattr(self, "_api_limiters", {}).get(api_name)
        if api_limiter and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[tushare_api] api_name='%s' -> api_limiter active (%.0f/min)", api_name, api_limiter.rate * 60
            )

        # 全局 token 熔断：token 已失效时快速失败，避免每个 API 独立重试刷屏。
        # 注意：_token_invalid 的读写未持锁（async 路径不能持 threading.Lock），
        # 与 set_token 的有锁写入存在极窄竞态窗口；最坏情况是 set_token 后被旧协程
        # 覆盖为 True 导致持续误熔断，需用户再次 set_token 自愈。
        if self._token_invalid:
            raise TushareAPIPermissionError(
                api_name,
                "Token marked invalid; call set_token() to reset after updating",
            )

        for i in range(self.max_retries):
            # 两段消费：全局 _rate_limiter 始终先消费，per-API limiter 额外收紧
            if self._rate_limiter:
                await self._rate_limiter.consume_async(1)
            if api_limiter:
                await api_limiter.consume_async(1)

            try:
                if not self.pro:
                    raise Exception(
                        "Tushare Token not set. Please set your token in settings.",
                    )

                import contextvars

                ctx = contextvars.copy_context()
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        ThreadPoolManager().io_pool,
                        lambda ctx=ctx: ctx.run(functools.partial(func, **kwargs)),
                    ),
                    timeout=self.timeout * self._ASYNC_TIMEOUT_MULTIPLIER,
                )

                if result is not None and api_name in self._COLUMN_RENAMES:
                    result = result.rename(columns=self._COLUMN_RENAMES[api_name])

                self.mark_api_available(api_name)

                # 两段消费配套：两个桶分别 on_success
                if self._rate_limiter:
                    self._rate_limiter.on_success()
                if api_limiter:
                    api_limiter.on_success()

                return result
            except Exception as e:
                import random

                error_msg = str(e)
                error_msg_lower = error_msg.lower()
                # token 认证失败独立判定：真实 Tushare 报错"您的token不对"不含权限关键字，
                # 必须独立触发全局熔断，不能被 is_permission_error 门控。
                is_token_invalid = any(k in error_msg_lower for k in TOKEN_INVALID_KEYWORDS)
                is_permission_error = is_token_invalid or any(k in error_msg_lower for k in PERMISSION_DENIED_KEYWORDS)
                is_rate_limit = (
                    "每分钟最多访问" in error_msg_lower
                    or "抱歉" in error_msg_lower
                    or "检测到" in error_msg_lower
                    or "429" in error_msg_lower
                    or "rate limit" in error_msg_lower
                    or "频次超限" in error_msg_lower
                )
                is_network_error = (
                    isinstance(e, (requests.exceptions.RequestException, TimeoutError, asyncio.TimeoutError))
                    or "timeout" in error_msg_lower
                    or "connection" in error_msg_lower
                    or "timed out" in error_msg_lower
                )

                if is_permission_error:
                    self.mark_api_unavailable(api_name)
                    # 仅 token 认证失败触发全局熔断；per-API 权限错误（如积分不足）不熔断
                    # is_token_invalid 已在上方独立计算（覆盖纯 token 报错不含权限关键字的情况）
                    if is_token_invalid:
                        self._token_invalid = True
                        logger.error(
                            "[tushare_api] TOKEN_INVALID (%s): global breaker engaged — subsequent calls will fast-fail",
                            api_name,
                        )
                    try:
                        t = asyncio.create_task(self._persist_capability_safely())
                        self._bg_tasks.add(t)
                        t.add_done_callback(self._bg_tasks.discard)
                    except RuntimeError:
                        pass
                    logger.error(
                        "[tushare_api] PERMISSION_DENIED (%s): %s",
                        api_name,
                        DataSanitizer.sanitize_error(e),
                    )
                    raise TushareAPIPermissionError(api_name, error_msg) from e

                is_client_param_error = any(
                    k in error_msg_lower for k in ("必填参数", "缺少参数", "invalid parameter", "missing required")
                )
                if is_client_param_error:
                    logger.error(
                        "[tushare_api] INVALID_REQUEST (%s): %s",
                        api_name,
                        DataSanitizer.sanitize_error(e),
                    )
                    raise

                if is_rate_limit:
                    # 两段消费配套：两个桶分别 reduce_rate
                    if self._rate_limiter:
                        self._rate_limiter.reduce_rate(factor=0.5)
                    if api_limiter:
                        api_limiter.reduce_rate(factor=0.5)

                    sleep_time = 5 + random.uniform(0, 5) + i * 5
                    current_rpm = self._rate_limiter.current_rate_per_min if self._rate_limiter else 0
                    logger.warning(
                        "[tushare_api] RATE_LIMITED (%s): adaptive slowdown -> %.0f/min, backoff=%.1fs (attempt %d/%d)",
                        api_name,
                        current_rpm,
                        sleep_time,
                        i + 1,
                        self.max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if is_network_error:
                    sleep_time = 1 * (i + 1) + random.uniform(0.1, 0.5)
                    logger.warning(
                        "[tushare_api] CONNECTION_ERROR (%s): %s - retry in %.2fs (attempt %d/%d)",
                        api_name,
                        type(e).__name__,
                        sleep_time,
                        i + 1,
                        self.max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if i == self.max_retries - 1:
                    logger.error(
                        "[tushare_api] RETRY_EXHAUSTED (%s): %s",
                        api_name,
                        DataSanitizer.sanitize_error(e),
                    )
                    raise e

                await asyncio.sleep(1)
        raise RuntimeError(f"[tushare_api] All {self.max_retries} retries exhausted for {api_name}")

    async def _handle_api_call_paginated(self, func: typing.Callable, max_pages: int = 100, **kwargs: typing.Any):
        import pandas as pd

        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        df_list = []
        offset = 0
        page = 0
        full_page_size = None

        while page < max_pages:
            kwargs["offset"] = offset
            try:
                df = await self._handle_api_call(func, **kwargs)
            except Exception as exc:
                if page == 0:
                    raise
                logger.warning(
                    "[API] Pagination failed on page %d (offset=%d): %s. Returning %d partial pages already fetched.",
                    page,
                    offset,
                    DataSanitizer.sanitize_error(exc),
                    len(df_list),
                )
                break

            if df is None or df.empty:
                break

            df_list.append(df)
            returned_len = len(df)

            if full_page_size is None:
                full_page_size = returned_len

            if returned_len < full_page_size:
                break

            offset += returned_len
            page += 1

        if page >= max_pages:
            logger.warning(
                "[API] Pagination hit max_pages=%s (offset=%s). Results are INCOMPLETE. Consider increasing max_pages or using date range filters.",
                max_pages,
                offset,
            )

        if not df_list:
            return None
        return pd.concat(df_list, ignore_index=True)

    @track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    def get_trade_dates(self, start_date: datetime.date | str | None, end_date: datetime.date | str | None):
        """Get list of actual trading dates (includes holidays handling).
        NOTE: This is a SYNC method — must remain sync for APScheduler (non-asyncio thread).
        For async contexts, use get_trade_cal() instead."""
        if not self.pro:
            raise Exception("Tushare Token not set. Please set your token in settings.")

        if isinstance(start_date, (datetime.date, datetime.datetime)):
            start_date = start_date.strftime("%Y%m%d")
        if isinstance(end_date, (datetime.date, datetime.datetime)):
            end_date = end_date.strftime("%Y%m%d")

        try:
            df = self.pro.trade_cal(
                exchange="SSE",
                start_date=start_date,
                end_date=end_date,
                is_open="1",
            )
            if df is not None and not df.empty:
                return df["cal_date"].tolist()
        except Exception as e:
            logger.warning("[API] get_trade_dates sync call failed: %s", DataSanitizer.sanitize_error(e))
        return []

    @track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    def is_trading_day(self, date_str: typing.Any = None):
        """
        Check if a given date is a trading day with optimized caching.
        Strategy: Year-based lazy loading with Double-Checked Locking.

        Args:
            date_str: Date in YYYYMMDD format, or a native datetime.date object. If None, uses today.

        Returns:
            bool: True if trading day, False if holiday/weekend
        """
        if date_str is None:
            date_str = get_now().strftime("%Y%m%d")
        elif isinstance(date_str, (datetime.date, datetime.datetime)):
            date_str = date_str.strftime("%Y%m%d")
        elif not isinstance(date_str, str):
            date_str = str(date_str)

        year = date_str[:4]

        if year in self._loaded_years:
            return date_str in self._trade_cal_cache

        try:
            with self._calendar_lock:
                if year in self._loaded_years:
                    return date_str in self._trade_cal_cache

                logger.info("[Cache] Loading trading calendar for year %s...", year)

                start_date = f"{year}0101"
                end_date = f"{year}1231"

                if not self.pro:
                    raise Exception("Tushare Token not set")
                df = self.pro.trade_cal(
                    exchange="SSE",
                    start_date=start_date,
                    end_date=end_date,
                    is_open="1",
                )

                if df is not None and not df.empty:
                    dates = set(df["cal_date"].tolist())
                    self._trade_cal_cache.update(dates)
                    self._loaded_years.add(year)
                    logger.info(
                        "[Cache] Successfully loaded %s trading days for %s",
                        len(dates),
                        year,
                    )

                    return date_str in dates
                logger.warning(
                    "[Cache] Failed to load calendar for %s (Empty response)",
                    year,
                )
                # Do not mark as loaded so we retry next time, or logic below deals with it

        except Exception as e:
            logger.warning(
                "[API] Trade calendar cache load failed: %s, falling back to Offline Calendar",
                DataSanitizer.sanitize_error(e),
            )

        # 3. Fallback: Offline Calendar (pandas_market_calendars)
        try:
            from data.domain_services.offline_calendar import OfflineCalendar

            return OfflineCalendar.is_trading_day(date_str)
        except Exception as ex:
            logger.error("[API] Offline calendar check failed: %s", DataSanitizer.sanitize_error(ex))
            # Ultimate Fallback: Simple weekday check (Mon-Fri)
            try:
                dt = datetime.datetime.strptime(date_str, "%Y%m%d")
                is_weekday = dt.weekday() < 5
                if is_weekday:
                    logger.warning(
                        "[API] UNSAFE_FALLBACK: Assuming %s is trading day (weekday check). May be inaccurate for holidays!",
                        date_str,
                    )
                return is_weekday
            except (ValueError, TypeError):
                logger.warning("[API] Invalid date format '%s', defaulting to non-trading day", date_str)
                return False

    # ========== Policy-Driven AI Extensions ==========

    # Whitelist of allowed macro API names to prevent arbitrary API injection
    _MACRO_API_WHITELIST = {"cn_m", "cn_cpi", "cn_ppi", "cn_gdp"}

    async def get_trade_cal(
        self, start_date: str | None, end_date: str | None, exchange: str = "SSE", is_open: int | None = None
    ):
        """
        Get trade calendar.
        Note: This is the raw API wrapper. For is_trading_day checks, use is_trading_day()
        which implements optimized year-based caching.
        """
        if not self.pro:
            raise Exception("Tushare Token not set. Please set your token in settings.")
        kwargs = dict(exchange=exchange, start_date=start_date, end_date=end_date)
        if is_open is not None:
            kwargs["is_open"] = str(is_open)
        return await self._handle_api_call(
            self.pro.trade_cal,
            **kwargs,
        )

    async def get_stock_basic(self, list_status: str = "L"):
        """
        Get basic list of stocks.

        Args:
            list_status: 上市状态过滤
                - "L": 仅上市中（默认，保持向后兼容）
                - "D": 仅退市
                - "": 全部（用于数据同步）

        Returns:
            DataFrame with columns: ts_code, symbol, name, area, industry,
                                list_date, delist_date, market, list_status
        """
        return await self._handle_api_call(
            self.pro.stock_basic,
            exchange="",
            list_status=list_status,
            fields="ts_code,symbol,name,area,industry,list_date,delist_date,market,list_status",
        )

    async def get_stock_basic_all(self):
        """Get all stocks (including delisted stocks) - for data sync"""
        return await self.get_stock_basic(list_status="")

    async def get_stock_list(self):
        """Alias for get_stock_basic"""
        return await self.get_stock_basic()

    async def get_daily_quotes(
        self,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get daily quotes with adj_factor joined"""
        # 1. Fetch Daily Quotes
        df_daily = await self._handle_api_call(
            self.pro.daily,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            trade_date=trade_date,
        )

        if df_daily is None or df_daily.empty:
            return df_daily

        # 2. Fetch Adj Factor
        # Tushare adj_factor API has same signature logic
        try:
            df_adj = await self._handle_api_call(
                self.pro.adj_factor,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                trade_date=trade_date,
            )

            if df_adj is not None and not df_adj.empty:
                # Merge logic
                # Tushare returns trade_date, ts_code, adj_factor
                # Ensure keys specifically
                if "trade_date" in df_adj.columns and "ts_code" in df_adj.columns:
                    df_daily = pd.merge(
                        df_daily,
                        df_adj[["ts_code", "trade_date", "adj_factor"]],
                        on=["ts_code", "trade_date"],
                        how="left",
                    )
        except Exception as e:
            logger.warning("[API] Failed to fetch adj_factor: %s, using default 1.0", DataSanitizer.sanitize_error(e))

        # Fill NaN adj_factor with 1.0
        if "adj_factor" in df_daily.columns:
            df_daily["adj_factor"] = df_daily["adj_factor"].fillna(1.0)
        else:
            df_daily["adj_factor"] = 1.0

        return df_daily

    async def get_daily_basic(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get daily basic indicators (PE, PB, Turnover, etc.)"""
        return await self._handle_api_call(
            self.pro.daily_basic,
            ts_code=ts_code,
            trade_date=trade_date,
            fields="ts_code,trade_date,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv,total_share,float_share,free_share,turnover_rate,turnover_rate_f,volume_ratio",
        )

    async def get_income(
        self,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get income statement data"""
        return await self._handle_api_call(
            self.pro.income,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields="ts_code,end_date,ann_date,report_type,n_income,revenue,operate_profit,total_revenue,n_income_attr_p",
        )

    async def get_cashflow(
        self,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get cashflow statement data"""
        return await self._handle_api_call(
            self.pro.cashflow,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,n_cashflow_act,c_cashflow_return_pay,n_cashflow_inv",
        )

    async def get_balancesheet(
        self,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ts_code: str | None = None,
    ):
        """Get balance sheet data"""
        return await self._handle_api_call(
            self.pro.balancesheet,
            period=period,
            start_date=start_date,
            end_date=end_date,
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,goodwill,money_cap,accounts_receiv",
        )

    async def get_top_list(self, trade_date: str | None):
        """Dragon Tiger Board (LHB) data. top_list.net_amount is stored in yuan."""

        df = await self._handle_api_call(
            self.pro.top_list,
            trade_date=trade_date,
            fields="trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason",
        )
        return attach_top_list_column_units(df)

    async def get_top_inst(self, trade_date: str | None):
        """LHB Institutional Seat Transaction Detail.

        Phase 2E §3.2.7：激活已封装 API，挂 @log_async_operation（由 _handle_api_call 提供）+ 显式 fields。
        """

        return await self._handle_api_call(
            self.pro.top_inst,
            trade_date=trade_date,
            fields="ts_code,trade_date,name,close,pct_change,amount,net_amount,buy_amount,buy_value,sell_amount,sell_value",
        )

    async def get_stk_limit(self, trade_date: str | None):
        """Daily Limit Up/Down Price.

        Phase 2G §3.2：stk_limit 涨跌停价格（仅数据层，不注入 AI），
        挂 @log_async_operation（由 _handle_api_call 提供）+ 显式 fields。

        Note: Tushare API 返回字段名为 "limit"（SQL 保留字），DAO 层 save_stk_limit
        在写入数据库时重命名为 "limit_type"（R17）。
        """

        return await self._handle_api_call(
            self.pro.stk_limit,
            trade_date=trade_date,
            fields="ts_code,trade_date,pre_close,up_limit,down_limit,limit",
        )

    async def get_hk_hold(self, trade_date: str | None):
        """Northbound (HK->Connect) holdings"""

        return await self._handle_api_call(
            self.pro.hk_hold,
            trade_date=trade_date,
            fields="ts_code,trade_date,name,vol,ratio,exchange",
        )

    async def get_moneyflow(self, trade_date: str | None):
        """Individual stock money flow (Main force)"""

        return await self._handle_api_call(
            self.pro.moneyflow,
            trade_date=trade_date,
            fields="ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount",
        )

    async def get_block_trade(self, trade_date: str | None):
        """Block trade data"""

        return await self._handle_api_call(
            self.pro.block_trade,
            trade_date=trade_date,
            fields="ts_code,trade_date,price,vol,amount,buyer,seller",
        )

    async def get_fina_indicator(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """
        Get financial indicators (ROE, growth rates, etc.)
        Can query by:
        1. ts_code + start_date/end_date (Get history for one stock)
        2. period (Get all stocks for one quarter - Requires permissions)
        """
        return await self._handle_api_call(
            self.pro.fina_indicator,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,roe,roe_waa,roe_dt,netprofit_margin,grossprofit_margin,debt_to_assets,q_sales_yoy,q_profit_yoy,or_yoy,netprofit_yoy",
        )

    async def get_disclosure_date(self, date: str):
        """
        Get disclosure list for a specific date (Incremental Sync).
        Uses 'actual_date' to find reports released on this day.
        """
        return await self._handle_api_call(
            self.pro.disclosure_date,
            actual_date=date,
            fields="ts_code,ann_date,end_date,actual_date",
        )

    async def get_concept_list(self, src: str = "ts"):
        """Get all concept categories"""
        return await self._handle_api_call(self.pro.concept, src=src)

    async def get_concept_detail_by_id(self, concept_id: str):
        """
        Get all stocks in a specific concept group by concept ID.
        Unlike get_concept_detail(ts_code), this fetches members of a concept.
        """
        return await self._handle_api_call(
            self.pro.concept_detail,
            id=concept_id,
            fields="id,concept_name,ts_code",
        )

    async def get_concept_detail(self, ts_code: str | None):
        """
        Get concepts for a specific stock (e.g. Lithium, Sora, etc.)
        """
        return await self._handle_api_call(
            self.pro.concept_detail,
            ts_code=ts_code,
            fields="id,concept_name",
        )

    async def get_index_daily(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get index daily data"""
        # Index Daily
        return await self._handle_api_call(
            self.pro.index_daily,
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg,vol,amount",
        )

    async def get_moneyflow_hsgt(self, trade_date: str | None = None):
        """Get Northbound (HSGT) money flow"""
        df = await self._handle_api_call(
            self.pro.moneyflow_hsgt,
            trade_date=trade_date,
            fields="trade_date,ggt_ss,ggt_sz,hgt,sgt,north_money,south_money",
        )
        if df is not None and not df.empty:
            df = attach_hsgt_column_units(df)
        return df

    async def get_index_dailybasic(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get index daily indicators (PE, PB, etc.)"""
        return await self._handle_api_call(
            self.pro.index_dailybasic,
            trade_date=trade_date,
            ts_code=ts_code,
            fields="ts_code,trade_date,total_mv,float_mv,total_share,float_share,free_share,turnover_rate,turnover_rate_f,pe,pe_ttm,pb",
        )

    async def get_limit_list(self, trade_date: str | None = None):
        """Get daily limit up/down list

        Tushare API (limit_list_d) returns 18 fields (doc_id=298):
        - trade_date: 交易日期
        - ts_code: 股票代码
        - industry: 所属行业
        - name: 股票名称
        - close: 收盘价
        - pct_chg: 涨跌幅
        - amount: 成交额
        - limit_amount: 板上成交金额（仅跌停有）
        - float_mv: 流通市值
        - total_mv: 总市值
        - turnover_ratio: 换手率
        - fd_amount: 封单金额
        - first_time: 首次封板时间
        - last_time: 最后封板时间
        - open_times: 炸板次数（跌停为开板次数）
        - up_stat: 涨停统计（"N/T" 格式：T 天有 N 次涨停）
        - limit_times: 连板数
        - limit: D跌停 U涨停 Z炸板

        Note: amp/fc_ratio/fl_ratio/strth 属于旧 limit_list 接口，limit_list_d 不提供，已永久删除。
        """
        return await self._handle_api_call(
            self.pro.limit_list_d,
            trade_date=trade_date,
            fields="trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,float_mv,total_mv,turnover_ratio,fd_amount,first_time,last_time,open_times,up_stat,limit_times,limit",
        )

    async def get_suspend_d(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get daily suspension list"""
        return await self._handle_api_call(
            self.pro.suspend_d,
            trade_date=trade_date,
            ts_code=ts_code,
            suspend_type="S",
            fields="ts_code,trade_date,suspend_timing,suspend_type",
        )

    async def get_margin_detail(self, trade_date: str | None = None, ts_code: str | None = None):
        """Get individual stock margin detail"""

        return await self._handle_api_call(
            self.pro.margin_detail,
            trade_date=trade_date,
            ts_code=ts_code,
            fields="ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzrqye",
        )

    async def get_fina_audit(
        self,
        ts_code: str | None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get financial audit opinion"""
        return await self._handle_api_call(
            self.pro.fina_audit,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,end_date,ann_date,audit_result,audit_agency,audit_sign,audit_fees",
        )

    async def get_forecast(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get performance forecast"""
        return await self._handle_api_call(
            self.pro.forecast,
            ts_code=ts_code,
            period=period,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max",
        )

    async def get_fina_mainbz(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get main business composition"""
        return await self._handle_api_call(
            self.pro.fina_mainbz,
            ts_code=ts_code,
            period=period,
            start_date=start_date,
            end_date=end_date,
            type="P",
            fields="ts_code,end_date,ann_date,bz_item,bz_sales,bz_profit,bz_cost,curr_type,update_flag",
        )

    async def get_pledge_stat(self, ts_code: str | None = None, end_date: str | None = None):
        """Get share pledge statistics"""
        return await self._handle_api_call_paginated(
            self.pro.pledge_stat,
            ts_code=ts_code,
            end_date=end_date,
            fields="ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio",
        )

    async def get_repurchase(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get share repurchase"""
        return await self._handle_api_call(
            self.pro.repurchase,
            ts_code=ts_code,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit",
        )

    async def get_dividend(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get dividend history"""

        return await self._handle_api_call(
            self.pro.dividend,
            ts_code=ts_code,
            ann_date=ann_date,
            end_date=end_date,
            fields="ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date",
        )

    async def get_shibor(self, start_date: str | None = None, end_date: str | None = None):
        """Get Shibor rates"""
        return await self._handle_api_call(
            self.pro.shibor,
            start_date=start_date,
            end_date=end_date,
            fields="date,on,1w,2w,1m,3m,6m,9m,1y",
        )

    async def get_top10_holders(
        self,
        ts_code: str | None = None,
        period: str | None = None,
        end_date: str | None = None,
        start_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get Top 10 Holders

        Args:
            ts_code: TS代码 (required by Tushare API for per-stock queries)
            period: 报告期 (e.g. '20251231'), typically the quarter-end date
        """
        return await self._handle_api_call(
            self.pro.top10_holders,
            ts_code=ts_code,
            period=period,
            ann_date=ann_date,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio,hold_float_ratio,hold_change,holder_type",
        )

    async def get_index_weight(
        self,
        index_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        """Get Index Component Weights"""
        return await self._handle_api_call(
            self.pro.index_weight,
            index_code=index_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            fields="index_code,con_code,trade_date,weight",
        )

    async def get_stk_holdernumber(
        self,
        ts_code: str | None = None,
        enddate: str | None = None,
        end_date: str | None = None,
        start_date: str | None = None,
        ann_date: str | None = None,
    ):
        """Get Stock Holder Number (Chip Concentration)

        Args:
            enddate: 截止日期/报告期 (e.g. '20251231'), distinct from end_date which is 公告结束日期
        """
        return await self._handle_api_call_paginated(
            self.pro.stk_holdernumber,
            ts_code=ts_code,
            ann_date=ann_date,
            enddate=enddate,
            end_date=end_date,
            start_date=start_date,
            fields="ts_code,end_date,ann_date,holder_num",
        )

    async def get_macro_data(self, api_name: str, start_m: str | None = None, end_m: str | None = None):
        if api_name not in self._MACRO_API_WHITELIST:
            logger.error("[API] Rejected macro API: %s (not in whitelist)", api_name)
            return None
        func = getattr(self.pro, api_name, None)
        if not func:
            logger.error("[API] Macro API not found: %s", api_name)
            return None
        # Defensive: ensure start_m/end_m are YYYYMM strings, not date objects.
        # _handle_api_call formats date/datetime as YYYYMMDD, but macro APIs expect YYYYMM.
        if isinstance(start_m, (datetime.date, datetime.datetime)):
            start_m = f"{start_m.year}{start_m.month:02d}"
        if isinstance(end_m, (datetime.date, datetime.datetime)):
            end_m = f"{end_m.year}{end_m.month:02d}"
        return await self._handle_api_call(func, start_m=start_m, end_m=end_m)

    async def get_cn_gdp(self, quarter: str):
        """Get China GDP data by quarter.

        Phase 2D §3.2.6 v1.10.0 P0-1：cn_gdp API 期望 ``quarter`` 参数（如 "2024Q4"），
        与 ``get_macro_data(api_name, start_m, end_m)`` 的 YYYYMM 参数 schema 不兼容，
        故新增专用 wrapper。

        Args:
            quarter: 季度字符串，格式 "YYYYQN"（如 "2024Q4"）。

        Returns:
            DataFrame with columns: quarter (renamed to period by _COLUMN_RENAMES),
            gdp, gdp_yoy, pi, pi_yoy, si, si_yoy, ti, ti_yoy。
        """
        if self.pro is None:
            return None
        return await self._handle_api_call(
            self.pro.cn_gdp,
            quarter=quarter,
            fields="quarter,gdp,gdp_yoy,pi,pi_yoy,si,si_yoy,ti,ti_yoy",
        )
