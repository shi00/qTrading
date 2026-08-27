"""TushareClient API 转发子模块（review01-A5c）。

自 ``data/external/tushare_client.py`` 移出的 API 转发与错误处理职责：
重试 / 降速 / 分页（``_handle_api_call`` / ``_handle_api_call_paginated``）。

设计约定（与 A5b / capability_probe 一致，测试兼容）：
- 本模块不顶层 import ``data.external.tushare_client``（避免循环依赖），仅
  ``TYPE_CHECKING`` 引用 ``TushareClient`` 做类型标注；模块级常量与异常类
  （``PERMISSION_DENIED_KEYWORDS`` / ``TOKEN_INVALID_KEYWORDS`` /
  ``_ASYNC_TIMEOUT_MULTIPLIER`` / ``TushareAPIPermissionError`` /
  ``TushareConfigError``）在函数体内 import。
- 实例经构造参数持有组合根 ``TushareClient``（``self.client``）。
- 共享状态**读取**经 ``__getattr__`` 回落 ``self.client``（如 ``max_retries`` /
  ``timeout`` / ``_token_invalid`` / ``_rate_limiter`` / ``_COLUMN_RENAMES``）。
- 组合根方法调用显式 ``self.client.method(...)``（如 ``_capture_loop`` /
  ``is_api_available`` / ``mark_api_available`` / ``_persist_capability_safely`` /
  ``_get_token_invalid_lock``），保证测试对组合根实例的 patch / 属性赋值生效。
- ``_handle_api_call_paginated`` 内部调用 ``self.client._handle_api_call(...)``
  而非自身方法：测试对 ``client._handle_api_call`` 的 patch 才能生效。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import requests

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

if TYPE_CHECKING:
    from data.external.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class TushareApiWrapper:
    """TushareClient API 转发与错误处理子模块。

    持有组合根 ``TushareClient`` 实例经 ``self.client`` 访问共享状态
    （``max_retries`` / ``timeout`` / ``pro`` / ``token`` / ``_token_invalid`` /
    ``_rate_limiter`` / ``_api_limiters`` / ``_bg_tasks`` / ``_COLUMN_RENAMES``）。
    """

    def __init__(self, client: TushareClient) -> None:
        self.client = client

    def __getattr__(self, name: str):
        """共享状态读取回落：子类自身未定义的属性委托给组合根 TushareClient。"""
        return getattr(self.client, name)

    @log_async_operation(
        operation_name="tushare_api_call",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
        log_level=logging.DEBUG,
    )
    async def _handle_api_call(self, func: Callable, **kwargs: Any):
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

        from data.external.tushare_client import (
            PERMISSION_DENIED_KEYWORDS,
            TOKEN_INVALID_KEYWORDS,
            _ASYNC_TIMEOUT_MULTIPLIER,
            TushareAPIPermissionError,
            TushareConfigError,
        )
        from utils.thread_pool import ThreadPoolManager

        # 捕获事件循环引用，供同步 set_token 调度使用
        self.client._capture_loop()

        # B7 修复：删除 functools.partial 死代码分支（所有调用方传入的 func 都是
        # bound method，不是 partial；partial 分支语义错误且不可达）。
        api_name = getattr(func, "__name__", str(func))

        capability = self.client.is_api_available(api_name)
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

        api_limiter = getattr(self.client, "_api_limiters", {}).get(api_name)
        if api_limiter and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[tushare_api] api_name='%s' -> api_limiter active (%.0f/min)", api_name, api_limiter.rate * 60
            )

        # 入口快照 token：用于 _token_invalid 写入时检测 set_token_async 是否在 await 期间
        # 替换了 token。若已替换，旧协程不应将 _token_invalid 覆盖为 True（避免误熔断新 token）。
        # getattr 防御性读取：测试替身经 object.__new__(TushareClient) 创建时可能未设置 self.token。
        entry_token = getattr(self.client, "token", None)

        # 全局 token 熔断：token 已失效时快速失败，避免每个 API 独立重试刷屏。
        # _token_invalid 读取经 loop-local asyncio.Lock 保护（R11：禁止裸 asyncio.Lock 类属性）。
        token_invalid_lock = self.client._get_token_invalid_lock()
        async with token_invalid_lock:
            if self.client._token_invalid:
                raise TushareAPIPermissionError(
                    api_name,
                    "Token marked invalid; call set_token() to reset after updating",
                )

        # 捕获全局 rate_limiter 为局部变量：consume_async 与 on_success/reduce_rate 之间隔着网络 await，
        # 若 set_token/reload_rate_limiters 在 await 期间替换 self.client._rate_limiter，则 consume 在旧 limiter、
        # on_success/reduce_rate 在新 limiter，破坏限流语义（B1+B17 竞态修复）。
        global_limiter = self.client._rate_limiter

        for i in range(self.client.max_retries):
            # 两段消费：全局 _rate_limiter 始终先消费，per-API limiter 额外收紧
            if global_limiter:
                await global_limiter.consume_async(1)
            if api_limiter:
                await api_limiter.consume_async(1)

            try:
                if not self.client.pro:
                    raise TushareConfigError()

                import contextvars

                ctx = contextvars.copy_context()
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        ThreadPoolManager().io_pool,
                        lambda ctx=ctx: ctx.run(functools.partial(func, **kwargs)),
                    ),
                    timeout=self.client.timeout * _ASYNC_TIMEOUT_MULTIPLIER,
                )

                if result is not None and api_name in self.client._COLUMN_RENAMES:
                    result = result.rename(columns=self.client._COLUMN_RENAMES[api_name])

                self.client.mark_api_available(api_name)

                # 两段消费配套：两个桶分别 on_success（用 global_limiter 避免竞态）
                if global_limiter:
                    global_limiter.on_success()
                if api_limiter:
                    api_limiter.on_success()

                return result
            except Exception as e:
                from utils.error_classifier import classify_error, classify_severity

                error_msg = str(e)
                error_msg_lower = error_msg.lower()
                # token 认证失败独立判定：真实 Tushare 报错"您的token不对"不含权限关键字，
                # 必须独立触发全局熔断，不能被 is_permission_error 门控。
                is_token_invalid = any(k in error_msg_lower for k in TOKEN_INVALID_KEYWORDS)
                is_permission_error = is_token_invalid or any(k in error_msg_lower for k in PERMISSION_DENIED_KEYWORDS)
                is_rate_limit = (
                    "每分钟最多访问" in error_msg_lower
                    or "抱歉，每分钟" in error_msg_lower
                    or "抱歉，频次" in error_msg_lower
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

                # 使用 classify_error + classify_severity 进行标准化分类（CLAUDE.md §3.2 强制要求）
                # context="token"：classify_error 识别 token/timeout/network/server 关键字；
                # tushare 特有的 permission/rate_limit/client_param 关键字保留补充判断（语义一致）
                error_info = classify_error(e, context="token")
                severity = classify_severity(e, context="token")
                if severity == "system":
                    log_level = logging.CRITICAL
                elif severity == "recoverable":
                    log_level = logging.WARNING
                else:
                    log_level = logging.ERROR

                if is_permission_error:
                    self.client.mark_api_unavailable(api_name)
                    # 仅 token 认证失败触发全局熔断；per-API 权限错误（如积分不足）不熔断
                    # is_token_invalid 已在上方独立计算（覆盖纯 token 报错不含权限关键字的情况）
                    if is_token_invalid:
                        # 经 loop-local asyncio.Lock 保护写入：检测 entry_token 是否仍为当前 token。
                        # 若 set_token_async 在 await 期间替换了 token，旧协程不应将 _token_invalid
                        # 覆盖为 True（避免误熔断新 token，P3-Tushare-Token-Invalid-Race 修复）。
                        async with token_invalid_lock:
                            if self.client.token != entry_token:
                                logger.warning(
                                    "[tushare_api] TOKEN_INVALID (%s): token changed during call "
                                    "(old=%s, new=%s), skip stale breaker set",
                                    api_name,
                                    DataSanitizer.sanitize_token(entry_token or ""),
                                    DataSanitizer.sanitize_token(self.client.token or ""),
                                )
                            else:
                                self.client._token_invalid = True
                                logger.error(
                                    "[tushare_api] TOKEN_INVALID (%s): global breaker engaged — subsequent calls will fast-fail",
                                    api_name,
                                )
                    # B4 修复：_reset_singleton 后旧协程持有的 self.client 仍指向旧实例，
                    # 新创建的 _persist_capability_safely task 会被添加到旧实例的 _bg_tasks，
                    # 新实例无法追踪。检查 self.client is type(self.client)._instance 跳过。
                    if self.client is type(self.client)._instance:
                        try:
                            t = asyncio.create_task(self.client._persist_capability_safely())
                            self.client._bg_tasks.add(t)
                            t.add_done_callback(self.client._bg_tasks.discard)
                        except RuntimeError:
                            pass
                    logger.log(
                        log_level,
                        "[tushare_api] PERMISSION_DENIED (%s, type=%s): %s",
                        api_name,
                        error_info.get("code", "unknown"),
                        DataSanitizer.sanitize_error(e),
                    )
                    raise TushareAPIPermissionError(api_name, error_msg) from e

                is_client_param_error = any(
                    k in error_msg_lower for k in ("必填参数", "缺少参数", "invalid parameter", "missing required")
                )
                if is_client_param_error:
                    logger.log(
                        log_level,
                        "[tushare_api] INVALID_REQUEST (%s, type=%s): %s",
                        api_name,
                        error_info.get("code", "unknown"),
                        DataSanitizer.sanitize_error(e),
                    )
                    raise

                if is_rate_limit:
                    # 两段消费配套：两个桶分别 reduce_rate（用 global_limiter 避免竞态）
                    if global_limiter:
                        global_limiter.reduce_rate(factor=0.5)
                    if api_limiter:
                        api_limiter.reduce_rate(factor=0.5)

                    sleep_time = 5 + random.uniform(0, 5) + i * 5
                    current_rpm = global_limiter.current_rate_per_min if global_limiter else 0
                    logger.log(
                        log_level,
                        "[tushare_api] RATE_LIMITED (%s, type=%s): adaptive slowdown -> %.0f/min, backoff=%.1fs (attempt %d/%d)",
                        api_name,
                        error_info.get("code", "unknown"),
                        current_rpm,
                        sleep_time,
                        i + 1,
                        self.client.max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if is_network_error:
                    sleep_time = 1 * (i + 1) + random.uniform(0.1, 0.5)
                    logger.log(
                        log_level,
                        "[tushare_api] CONNECTION_ERROR (%s, type=%s): %s - retry in %.2fs (attempt %d/%d)",
                        api_name,
                        error_info.get("code", "unknown"),
                        type(e).__name__,
                        sleep_time,
                        i + 1,
                        self.client.max_retries,
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                if i == self.client.max_retries - 1:
                    logger.log(
                        log_level,
                        "[tushare_api] RETRY_EXHAUSTED (%s, type=%s): %s",
                        api_name,
                        error_info.get("code", "unknown"),
                        DataSanitizer.sanitize_error(e),
                    )
                    raise

                await asyncio.sleep(1)
        raise RuntimeError(f"[tushare_api] All {self.client.max_retries} retries exhausted for {api_name}")

    @log_async_operation(
        operation_name="TushareClient._handle_api_call_paginated",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def _handle_api_call_paginated(self, func: Callable, max_pages: int = 100, **kwargs: Any):
        import pandas as pd

        from data.external.tushare_client import TushareAPIPermissionError

        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        df_list = []
        offset = 0
        page = 0

        while page < max_pages:
            kwargs["offset"] = offset
            try:
                df = await self.client._handle_api_call(func, **kwargs)
            except TushareAPIPermissionError:
                # B10 修复：权限错误向上传播，调用方需知道数据不完整（不能视为普通分页失败吞掉）
                raise
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

            # B9 修复：分页终止条件改为空页判断，而非"页大小小于第一页"。
            # 原逻辑假设页大小恒定，若 Tushare 内部过滤导致非末页返回少于首页，
            # 会误中断丢失后续数据。空页判断更健壮（多请求一次空页的代价可接受）。
            if df is None or df.empty:
                break

            df_list.append(df)
            offset += len(df)
            page += 1

        # B12 修复：达到 max_pages 时标记 truncated=True，调用方可检查 df.attrs["truncated"]
        truncated = False
        if page >= max_pages:
            logger.warning(
                "[API] Pagination hit max_pages=%s (offset=%s). Results are INCOMPLETE. Consider increasing max_pages or using date range filters.",
                max_pages,
                offset,
            )
            truncated = True

        if not df_list:
            return None
        result = pd.concat(df_list, ignore_index=True)
        if truncated:
            result.attrs["truncated"] = True
        return result
