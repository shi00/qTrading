"""TushareClient 限流器子模块（review01-A5c）。

自 ``data/external/tushare_client.py`` 移出的限流器构建职责：
档位预设解析（``_resolve_rate_limit``）、限流器重建（``reload_rate_limiters``）、
全局桶 + per-API 慢桶 + probe 专用桶构建（``_build_rate_limiters``）。

设计约定（与 A5b 一致，测试兼容）：
- 本模块不顶层 import ``data.external.tushare_client``（避免循环依赖），仅
  ``TYPE_CHECKING`` 引用 ``TushareClient`` 做类型标注。
- 实例经构造参数持有组合根 ``TushareClient``（``self.client``）。
- 共享状态**读取**经 ``__getattr__`` 回落 ``self.client``（如 ``self._POINT_TIER_PRESETS``
  / ``self._SLOW_API_OVERRIDES`` / ``self._lock`` / ``self._get_tushare_point_tier()``）。
- 共享状态**属性赋值**必须显式 ``self.client.xxx = ...``（``__getattr__`` 不处理赋值，
  否则写入子类自身导致组合根状态不同步）。
- 组合根方法调用显式 ``self.client.method(...)``（保证外部对组合根的 patch 生效）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils.rate_limiter import TokenBucket

if TYPE_CHECKING:
    from data.external.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class TushareRateLimiter:
    """TushareClient 限流器构建子模块。

    持有组合根 ``TushareClient`` 实例经 ``self.client`` 访问共享状态
    （``_rate_limiter`` / ``_api_limiters`` / ``_probe_rate_limiter`` /
    ``_POINT_TIER_PRESETS`` / ``_SLOW_API_OVERRIDES`` / ``_PROBE_RATE_LIMIT_RPM``）。
    """

    def __init__(self, client: TushareClient) -> None:
        self.client = client

    def __getattr__(self, name: str):
        """共享状态读取回落：子类自身未定义的属性委托给组合根 TushareClient。"""
        return getattr(self.client, name)

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
            self.client._rate_limiter, self.client._api_limiters, self.client._probe_rate_limiter = (
                self._build_rate_limiters()
            )
        logger.info("[API] Rate limiters reloaded from config")

    def _build_rate_limiters(self) -> tuple[TokenBucket | None, dict[str, TokenBucket], TokenBucket]:
        """
        Build rate limiters based on config.

        Phase 2B §3.2.5: 返回三元组（全局 + per-API + probe 专用 50/min）。
        probe 专用桶与全局桶同步创建（避免 R11 跨循环复用同步原语）。
        """
        limit_per_min = self.client._resolve_rate_limit()
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
