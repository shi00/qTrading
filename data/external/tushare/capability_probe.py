"""TushareClient 能力探测子模块（review01-A5c）。

自 ``data/external/tushare_client.py`` 移出的能力探测与缓存职责：
档位覆盖判定（``get_tier_apis`` / ``is_api_covered_by_tier`` / ``get_tier_order`` /
``is_independent_purchase``）、capability 缓存读写（``is_api_available`` /
``mark_api_available`` / ``mark_api_unavailable`` / ``clear_capability_cache`` /
``get_capability_cache`` / ``get_last_probe_time``）、AppState 持久化
（``persist_capabilities_to_app_state`` / ``load_capabilities_from_app_state`` /
``_persist_capability_safely``）、同步过滤（``get_effective_synced_tables``）与
并行探测（``probe_api_capabilities`` / ``_handle_probe_call`` / ``_probe_one``）。

设计约定（与 A5b / rate_limiter 一致，测试兼容）：
- 本模块不顶层 import ``data.external.tushare_client``（避免循环依赖），仅
  ``TYPE_CHECKING`` 引用 ``TushareClient`` 做类型标注；模块级常量与异常类
  （``PERMISSION_DENIED_KEYWORDS`` / ``TOKEN_INVALID_KEYWORDS`` /
  ``_ASYNC_TIMEOUT_MULTIPLIER`` / ``TushareAPIPermissionError`` /
  ``TushareConfigError``）在函数体内 import。
- 实例经构造参数持有组合根 ``TushareClient``（``self.client``）。
- 共享状态**读取**经 ``__getattr__`` 回落 ``self.client``（如 ``_capability_cache`` /
  ``_capability_cache_lock`` / ``_last_probe_time`` / ``token`` / ``_TIER_ORDER``）。
- 共享状态**属性赋值**必须显式 ``self.client.xxx = ...``（如 ``_probe_in_progress`` /
  ``_last_probe_time``），``__getattr__`` 不处理赋值，否则写入子类自身导致组合根状态不同步。
- 组合根方法调用显式 ``self.client.method(...)``（本子模块亦定义了同名方法如
  ``_handle_probe_call`` / ``_probe_one``，直接 ``self.xxx`` 会绑定子类方法而绕过
  测试对组合根实例的 patch / 属性赋值，故统一走 ``self.client``）。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

if TYPE_CHECKING:
    from data.external.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class CapabilityProbeService:
    """TushareClient 能力探测与缓存子模块。

    持有组合根 ``TushareClient`` 实例经 ``self.client`` 访问共享状态
    （``_capability_cache`` / ``_capability_cache_lock`` / ``_last_probe_time`` /
    ``_probe_in_progress`` / ``token`` / ``pro`` / ``_rate_limiter`` /
    ``_probe_rate_limiter`` / ``timeout`` / ``TABLE_TO_API_MAP`` 及档位常量）。
    """

    def __init__(self, client: TushareClient) -> None:
        self.client = client

    def __getattr__(self, name: str):
        """共享状态读取回落：子类自身未定义的属性委托给组合根 TushareClient。"""
        return getattr(self.client, name)

    # ------------------------------------------------------------------
    # capability 缓存读写
    # ------------------------------------------------------------------

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

    def get_last_probe_time(self) -> datetime.datetime | None:
        """返回上次 probe 完成时间（公共 getter，避免外部访问私有 _last_probe_time）。"""
        return self._last_probe_time

    # ------------------------------------------------------------------
    # 档位覆盖判定
    # ------------------------------------------------------------------

    def get_tier_order(self, tier: str) -> int:
        """返回档位的顺序值（公共方法，避免外部访问私有 _TIER_ORDER）。"""
        return self._TIER_ORDER.get(tier, 0)

    def get_tier_apis(self, tier: str | None = None) -> frozenset[str]:
        """获取档位覆盖的 API 集合（内部合并所有 ≤当前档位的集合）。

        _TIER_API_COVERAGE 每个档位只列新增项，本方法合并低档位。
        """
        tier = tier or self.client._get_tushare_point_tier()
        order = self._TIER_ORDER.get(tier, 0)
        return frozenset().union(
            *(apis for t, apis in self._TIER_API_COVERAGE.items() if self._TIER_ORDER.get(t, 0) <= order)
        )

    def is_api_covered_by_tier(self, api_name: str, tier: str | None = None) -> bool:
        """检查 API 是否被档位覆盖（不含独立付费判断）。"""
        return api_name in self.client.get_tier_apis(tier)

    def is_independent_purchase(self, api_name: str) -> bool:
        """检查 API 是否需要独立购买。"""
        return api_name in self._INDEPENDENT_PURCHASE_APIS

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
            if not self.client.is_api_covered_by_tier(api_name):
                # 档位不足：跳过同步（DB 历史数据保留，由 stale 标注机制处理）
                continue
            # 第二层：probe 验证（None 不阻塞）
            if self.client.is_api_available(api_name) is False:
                continue
            effective.append(table)
        return effective

    # ------------------------------------------------------------------
    # AppState 持久化
    # ------------------------------------------------------------------

    async def _persist_capability_safely(self) -> None:
        """Fire-and-forget persistence of capability cache to AppState.

        Catches all exceptions so that persistence failure never disrupts
        the caller (typically _handle_api_call raising TushareAPIPermissionError).
        """
        try:
            await self.client.persist_capabilities_to_app_state()
        except Exception as exc:
            logger.debug(
                "[TushareClient] Capability persist failed (non-critical): %s", DataSanitizer.sanitize_error(exc)
            )

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
                        self.client._last_probe_time = datetime.datetime.fromisoformat(last_probe_str)
                    except (ValueError, TypeError):
                        logger.warning("[TushareClient] Invalid last_probe_time format: %s", last_probe_str)
                        self.client._last_probe_time = None
                else:
                    self.client._last_probe_time = None
                logger.info("[TushareClient] Loaded %s capabilities from AppState", len(self._capability_cache))
            else:
                logger.debug("[TushareClient] Token hash mismatch, skipping capability load")
        except Exception as e:
            logger.warning("[TushareClient] Failed to load capabilities: %s", DataSanitizer.sanitize_error(e))

    # ------------------------------------------------------------------
    # 并行探测
    # ------------------------------------------------------------------

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
        # ``self.client._probe_in_progress = True`` 之间无 await，理论原子。
        if self._probe_in_progress:
            logger.warning("[TushareClient] Probe already in progress, skipping")
            # B5/B19 修复：持锁读取当前 cache 快照（所有 _capability_cache 访问持锁）
            return self.client.get_capability_cache()
        self.client._probe_in_progress = True

        # 入口持锁快照：取消/异常时回滚到入口状态（v1.9.0 P0-3/M-3）
        # B5/B19 修复：持锁访问 _capability_cache；B3 修复：同时快照 token，
        # set_token 在 probe 期间替换 token + 清空 cache + 重建 pro，回滚/写入时
        # 检查 token 一致性避免污染新 token 的 cache。
        with self._capability_cache_lock:
            cache_snapshot = dict(self._capability_cache)
        token_snapshot = self.token

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
                ("pledge_detail", {"ts_code": PROBE_STOCK_CODE, "ann_date": recent_date}),
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
            current_tier = self.client._get_tushare_point_tier()
            filtered_configs = [
                (api, params) for api, params in probe_configs if self.client.is_api_covered_by_tier(api, current_tier)
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
                self.client._last_probe_time = get_now()
                await self.client.persist_capabilities_to_app_state()
                return {}

            # 并行探测过滤后的候选（semaphore=4 + gather 传播 CancelledError）
            semaphore = asyncio.Semaphore(4)
            completed_counter = [0]  # list 包装以便闭包内修改

            async def _probe_with_progress(name: str, params: dict) -> tuple[str, bool | None]:
                result = await self.client._probe_one(semaphore, name, params)
                completed_counter[0] += 1
                if progress_callback is not None:
                    try:
                        progress_callback(completed_counter[0], total)
                    except Exception as cb_exc:  # pragma: no cover - UI 回调异常不应阻塞 probe
                        logger.warning(
                            "[TushareClient] Probe progress_callback failed: %s",
                            DataSanitizer.sanitize_error(cb_exc),
                        )
                return result

            results_list = await gather_return_exceptions_propagating_cancel(
                *[_probe_with_progress(name, params) for name, params in filtered_configs]
            )
            results: dict[str, bool | None] = {}
            for item in results_list:
                if isinstance(item, Exception):  # pragma: no cover - 防御性兜底，_probe_one 已捕获内部异常
                    # _probe_one 已捕获内部异常，此处 Exception 不应发生；
                    # 防御性处理：跳过该 item
                    logger.warning(
                        "[TushareClient] Probe returned unexpected exception: %s",
                        DataSanitizer.sanitize_error(item),
                    )
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
                self.client._last_probe_time = get_now()
                await self.client.persist_capabilities_to_app_state()
                return self.client.get_capability_cache()

            # Token 无效检测（False 比例 >90% 记 error 日志）
            false_count = sum(1 for v in results.values() if v is False)
            if total > 0 and false_count / total > 0.9:
                logger.error(
                    "[TushareClient] Probe %d/%d APIs returned False (permission denied), Token may be invalid or积分严重不足",
                    false_count,
                    total,
                )

            # B3 修复：写入前检查 token 一致性，set_token 后旧 probe 持有的结果丢弃，
            # 避免旧 token 的 probe 结果覆盖新 token 的 cache（路径 B 污染）
            if self.token != token_snapshot:
                logger.info(
                    "[TushareClient] Token changed during probe (entry=%s, current=%s), "
                    "discarding probe results to avoid cache pollution",
                    DataSanitizer.sanitize_token(token_snapshot or ""),
                    DataSanitizer.sanitize_token(self.token or ""),
                )
                return self.client.get_capability_cache()

            # gather 全部成功后统一写入 _capability_cache（None 不写入，避免污染）
            for api_name, available in results.items():
                if available is True:
                    self.client.mark_api_available(api_name)
                elif available is False:
                    self.client.mark_api_unavailable(api_name)
                # None 不写入 _capability_cache（保持原值或不存在）

            self.client._last_probe_time = get_now()
            await self.client.persist_capabilities_to_app_state()
            return results
        except asyncio.CancelledError:
            # 取消时回滚 _capability_cache 到入口快照（R2 红线：raise 传播）
            # B3+B5/B19 修复：持锁回滚 + token 一致性检查，避免污染新 token 的 cache
            logger.info("[TushareClient] Probe cancelled, rolling back _capability_cache to entry snapshot")
            if self.token == token_snapshot:
                with self._capability_cache_lock:
                    self._capability_cache.clear()
                    self._capability_cache.update(cache_snapshot)
            else:
                logger.info("[TushareClient] Token changed during probe, skip rollback to avoid cache pollution")
            raise
        except Exception as exc:
            # 其他异常（网络抖动等非取消）也回退到入口快照，避免部分污染
            # B3+B5/B19 修复：持锁回滚 + token 一致性检查
            logger.warning(
                "[TushareClient] Probe failed, rolling back _capability_cache to entry snapshot: %s",
                DataSanitizer.sanitize_error(exc),
            )
            if self.token == token_snapshot:
                with self._capability_cache_lock:
                    self._capability_cache.clear()
                    self._capability_cache.update(cache_snapshot)
            else:
                logger.info("[TushareClient] Token changed during probe, skip rollback to avoid cache pollution")
            return self.client.get_capability_cache()
        finally:
            self.client._probe_in_progress = False

    @log_async_operation(
        operation_name="TushareClient._handle_probe_call",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def _handle_probe_call(self, api_name: str, func: Callable, **params: Any) -> None:
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

        from data.external.tushare_client import (
            PERMISSION_DENIED_KEYWORDS,
            TOKEN_INVALID_KEYWORDS,
            _ASYNC_TIMEOUT_MULTIPLIER,
            TushareAPIPermissionError,
            TushareConfigError,
        )
        from utils.thread_pool import ThreadPoolManager

        if not self.client.pro:
            raise TushareConfigError()

        # 捕获限速器为局部变量：consume_async 与网络 await 之间若被 set_token/reload_rate_limiters
        # 替换 self.client._rate_limiter/self.client._probe_rate_limiter，会破坏限流语义（B1+B17 竞态修复）。
        global_limiter = self.client._rate_limiter
        probe_limiter = self.client._probe_rate_limiter

        # 两段消费：全局桶 + probe 专用桶
        if global_limiter is not None:
            await global_limiter.consume_async(1)
        await probe_limiter.consume_async(1)

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
                timeout=self.client.timeout * _ASYNC_TIMEOUT_MULTIPLIER,
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
            # B13 修复：client_param_error（必填参数缺失等）分类为不可用（False）。
            # probe 使用固定参数，参数错误说明该 API 在当前 probe 参数下不可用，
            # 应记 ERROR 日志并通过 TushareAPIPermissionError 让 _probe_one 分类为 False。
            is_client_param_error = any(
                k in error_msg_lower for k in ("必填参数", "缺少参数", "invalid parameter", "missing required")
            )
            if is_client_param_error:
                logger.error(
                    "[TushareClient] Probe %s: client param error (API not available with probe params): %s",
                    api_name,
                    DataSanitizer.sanitize_error(e),
                )
                raise TushareAPIPermissionError(api_name, f"client_param_error: {error_msg}") from e
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
        from data.external.tushare_client import TushareAPIPermissionError
        from utils.error_classifier import classify_error, classify_severity

        async with semaphore:
            func = getattr(self.client._get_pro(), api_name, None)
            if func is None:
                logger.warning("[TushareClient] Probe %s: API not found in SDK", api_name)
                return (api_name, None)
            try:
                await self.client._handle_probe_call(api_name, func, **params)
                return (api_name, True)
            except TushareAPIPermissionError:
                # 区分"积分不足"vs"需独立购买"
                if self.client.is_independent_purchase(api_name):
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
