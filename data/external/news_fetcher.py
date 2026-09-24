import asyncio
import datetime
import json
import logging
import math
import threading
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import httpx
from cachetools import TTLCache

from data.external.akshare_rate_limiter import get_akshare_rate_limiter
from utils.sanitizers import DataSanitizer
from utils.log_decorators import log_async_operation, PerfThreshold
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import CST_TZ, get_now, to_utc_for_db
from utils.error_classifier import classify_error, classify_severity

logger = logging.getLogger(__name__)


def _log_with_severity(
    e: Exception,
    msg: str,
    *args,
    context: str = "general",
    exc_info: bool = False,
) -> None:
    """按 classify_severity 选择日志级别；classify_error 提供 code 一并记入。

    降级语义由调用方保留（仍返回空列表/空字符串，不 raise）。
    """
    error_info = classify_error(e, context=context)
    severity = classify_severity(e, context=context)
    if severity == "system":
        log_fn = logger.error
    elif severity == "recoverable":
        log_fn = logger.warning
    else:  # operational
        log_fn = logger.info
    log_fn(f"{msg} [code=%s]", *args, error_info["code"], exc_info=exc_info)


_US_MOVES_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)
_US_MOVES_CACHE_LOCK = threading.Lock()  # Thread-safe lock for TTLCache access
_SINA_CONSECUTIVE_EMPTY = {"us_api": 0, "concept": 0}
_SINA_CONSECUTIVE_FAILURES = {"concept": 0}
_SINA_EMPTY_THRESHOLD = 3
_SINA_FAILURE_ERROR_INTERVAL = 3
_HOT_CONCEPTS_TIMEOUT_SECONDS = 15.0

# F5-P1: 保护 _SINA_CONSECUTIVE_EMPTY 和 _SINA_CONSECUTIVE_FAILURES 的并发读写。
# us_api 在 IO 线程池中访问，concept 在事件循环线程中访问，共享 dict 必须 同锁保护。
# 临界区无 IO（logger 调用移出锁外），无需超时。
_SINA_STATE_LOCK = threading.Lock()

# Lock for thread-safe mutation of pd.options.mode.string_storage
_pd_options_lock = threading.Lock()

# CLS circuit breaker state (separate from Sina counters above)
_CLS_CONSECUTIVE_FAILURES = 0
_CLS_FAILURE_THRESHOLD = 3
_CLS_CIRCUIT_OPENED_AT = 0.0
_CLS_CIRCUIT_COOLDOWN_SECONDS = 60.0

# F5-P1: 保护 _CLS_CONSECUTIVE_FAILURES 和 _CLS_CIRCUIT_OPENED_AT 的并发读写。
# 多个 get_latest_global_news 并发调用时，await 切换点间存在竞态，需锁保护
# 熔断器状态一致性（计数达阈值时熔断时间必须已设置）。临界区无 IO，无需超时。
_CLS_STATE_LOCK = threading.Lock()


def _run_with_python_string_storage(fetcher):
    """Run AKShare calls under a single critical section for global pandas option safety."""
    # 限制锁获取最大超时时间为 10.0 秒，防止连环死锁
    acquired = _pd_options_lock.acquire(timeout=10.0)
    if not acquired:
        raise TimeoutError("[NewsFetcher] Could not acquire _pd_options_lock within 10s")
    try:
        old_storage = pd.options.mode.string_storage
        pd.options.mode.string_storage = "python"
        try:
            return fetcher()
        finally:
            pd.options.mode.string_storage = old_storage
    finally:
        _pd_options_lock.release()


def _ensure_dataframe(result, source: str = "") -> pd.DataFrame | None:
    """Normalize akshare return value to DataFrame or None.
    Some akshare APIs return list instead of DataFrame on certain versions/error paths.
    """
    if result is None:
        return None
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, list):
        if not result:
            return pd.DataFrame()
        try:
            return pd.DataFrame(result)
        except Exception as e:
            _log_with_severity(e, "[NewsFetcher] Failed to convert list to DataFrame from %s", source)
            return None
    logger.warning("[NewsFetcher] Unexpected return type from %s: %s", source, type(result).__name__)
    return None


def _parse_news_time(raw: str | None, *, day_only: bool = False) -> datetime.datetime | None:
    """把公历时间字符串解析为存库格式：CST 本地化后转 UTC tz-naive（参考 CLS 时间戳/DB 存储口径）。

    - day_only=True：仅日期（公告），统一补 00:00:00；已含时间的输入原样解析，不再追加
      （review08-D1 复核护栏：防上游 pandas 格式化行为漂移导致长度校验失败、时间全丢）。
    - 解析失败返回 None（调用方按缺失时间处理，不影响其余文档）。
    """
    raw_text = (raw or "").strip()
    if not raw_text:
        return None
    text = f"{raw_text} 00:00:00" if day_only and len(raw_text) <= 10 else raw_text
    try:
        if len(text) >= 19 and len(text) <= 23:
            parsed = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007  东财/巨潮发布时间为 CST 本地时间文本，无时区字面量；下方 to_utc_for_db 按 CST 归属
        else:
            return None
    except ValueError:
        return None
    # 无 tz 的东财/巨潮时间为 CST 本地时间 → 本地化后转 UTC naive 存库
    return to_utc_for_db(parsed)


def _fetch_stock_news_core(
    symbol: str,
    ts_code: str,
    *,
    log_prefix: str = "",
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """一次抓取并归一化 CNINFO 公告 + EM 新闻（review08-D1 共享内核）。

    返回 ``(announcement_docs, news_docs, coverage)``：
    - 内部 doc 结构：``{"title", "publish_time"(UTC tz-naive datetime|None), "url", "content", "source_label"}``；
    - 不做 limit / 窗口过滤 / 空 title 过滤（由公开方法整形决定）；
    - 单层失败返回该层空列表 + coverage 对应 ``"fail"``；日志文本经 ``log_prefix``
      区分两个公开方法（``get_stock_news`` 传 ``""``，``get_stock_news_documents`` 传 ``"documents "``，
      与各自历史日志逐字一致）。
    """
    announcement_docs: list[dict] = []
    news_docs: list[dict] = []
    coverage = {"announcement": "fail", "news": "fail"}

    # B3：巨潮公告接口当前仅支持固定 market 值"沪深京"
    market = "沪深京"

    # Layer 1: 巨潮公告（announcement）
    try:
        # review08-B4: 所有 akshare 出站调用经模块级共享限速器（1 QPS / burst 2）。
        # 本内核在 IO 线程池线程执行，consume() 同步阻塞不阻塞事件循环（R16）。
        get_akshare_rate_limiter().consume(1)
        # Get last 6 months to ensure we find *something* (e.g. quarterly reports)
        end_date = get_now().strftime("%Y%m%d")
        start_date = (get_now() - timedelta(days=180)).strftime("%Y%m%d")

        df_cninfo = _ensure_dataframe(
            ak.stock_zh_a_disclosure_report_cninfo(
                symbol=symbol,
                market=market,
                start_date=start_date,
                end_date=end_date,
            ),
            source="stock_zh_a_disclosure_report_cninfo",
        )

        if df_cninfo is not None and not df_cninfo.empty:
            # Column names may vary by akshare version or encoding.
            # Known structure: [代码, 简称, 公告标题, 公告时间, 公告链接]
            # We use name-based lookup with positional fallback.
            cols = list(df_cninfo.columns)
            title_col = "公告标题" if "公告标题" in cols else (cols[2] if len(cols) > 2 else None)
            time_col = "公告时间" if "公告时间" in cols else (cols[3] if len(cols) > 3 else None)
            url_col = "公告链接" if "公告链接" in cols else None
            if title_col:
                for _, row in df_cninfo.iterrows():
                    title = str(row.get(title_col, "") or "").strip()
                    raw_date = str(row.get(time_col, "")) if time_col else ""
                    announcement_docs.append(
                        {
                            "title": title,
                            # 仅日期 → 补 00:00:00（CST）后转 UTC naive（统一时间口径，review08-D1）
                            "publish_time": _parse_news_time(raw_date, day_only=True),
                            "url": str(row.get(url_col, "")) if url_col else "",
                            "content": "",
                            "source_label": "巨潮公告",
                        }
                    )
                coverage["announcement"] = "ok"
    except Exception as e:
        _log_with_severity(
            e,
            f"[News] {log_prefix}CNINFO disclosure failed for %s: %s",
            ts_code,
            DataSanitizer.sanitize_error(e),
        )

    # Layer 2: 东财新闻搜索（news）
    try:
        # review08-B4: 每层各消耗 1 token；单次抓取 burst=2（与共享桶容量一致）。
        get_akshare_rate_limiter().consume(1)
        df_em = _ensure_dataframe(ak.stock_news_em(symbol=symbol), source="stock_news_em")

        if df_em is not None and not df_em.empty:
            # EastMoney returns '新闻内容' as title, '新闻链接', '发布时间', etc.
            for _, row in df_em.iterrows():
                title = str(row.get("新闻标题", row.get("新闻内容", "")) or "").strip()
                # review08-D1 复核修复：真实列名为 '发布时间'（akshare 1.18.97 实测），
                # 原主键 '新闻时间' 在生产返回中不存在、仅靠 fallback 侥幸生效。
                raw_time = row.get("发布时间", "")
                url = str(row.get("新闻链接", "") or "") if "新闻链接" in df_em.columns else ""
                content = str(row.get("新闻内容", "") or "").strip()
                news_docs.append(
                    {
                        "title": title,
                        "publish_time": _parse_news_time(str(raw_time)),
                        "url": url,
                        "content": content,
                        "source_label": str(row.get("文章来源", "东财新闻")),
                    }
                )
            coverage["news"] = "ok"
    except Exception as e:
        _log_with_severity(
            e,
            f"[News] {log_prefix}EM search failed for %s: %s",
            ts_code,
            DataSanitizer.sanitize_error(e),
        )

    return announcement_docs, news_docs, coverage


class NewsFetcher:
    """
    Fetches news data using AKShare and direct robust Sina/THS clients.
    Replaces blocked EastMoney interfaces.
    """

    @staticmethod
    @log_async_operation(
        operation_name="news_get_stock_news",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_stock_news(ts_code: str | None, limit: int | None = 5, as_of: date | None = None):
        """
        Fetch specific stock news using a dual-layer strategy:
        1. 巨潮公告 (stock_zh_a_disclosure_report_cninfo) - Official exchange filings (Highest quality)
        2. 东财搜索 (stock_news_em) - Fallback to keyword search (Lower quality, more noise)

        Both use a pyarrow string_storage workaround for pandas compatibility.

        as_of: When set to a historical date, returns empty list to prevent
        look-ahead bias in backtesting / AI context construction.
        """
        if as_of is not None and as_of != get_now().date():
            return []

        if not ts_code:
            return []

        # Extract symbol without suffix suffix for standard AKShare calls
        symbol = ts_code.split(".")[0]

        # Run the IO bound akshare calls in the thread pool
        def _fetch():
            def _shape(doc: dict) -> dict:
                return {
                    "title": doc["title"],
                    "publish_time": doc["publish_time"],
                    "source": doc["source_label"],
                }

            try:
                announcement_docs, news_docs, _coverage = _run_with_python_string_storage(
                    lambda: _fetch_stock_news_core(symbol, ts_code)
                )
            except Exception as outer_e:
                _log_with_severity(
                    outer_e,
                    "[News] Fatal error fetching stock news for %s: %s",
                    ts_code,
                    DataSanitizer.sanitize_error(outer_e),
                )
                return []

            # 公告优先，EM 回退（与重构前一致：公告层有行即返回，否则用 EM）
            source_docs = announcement_docs if announcement_docs else news_docs
            return [_shape(d) for d in source_docs[:limit]]

        try:
            # We use the IO Thread Pool with a 15-second timeout via asyncio.wait_for
            # to prevent hanging the AI pipeline if the APIs are slow/dead.
            future = ThreadPoolManager().run_async(TaskType.IO, _fetch)
            return await asyncio.wait_for(future, timeout=15.0)
        except TimeoutError as e:
            _log_with_severity(e, "[News] Timeout fetching news for %s", ts_code)
            # 监控告警：asyncio.wait_for 超时仅取消 asyncio.Future 的 await，
            # 底层 ThreadPoolManager IO 线程中的 _fetch（含 akshare 同步调用）
            # 无法被强制取消，会持续占用 IO 池槽位直至 _fetch 自然返回。
            # 多次超时累积可能耗尽 IO 线程池 max_workers，需关注线程池占用。
            logger.warning(
                "[News] Background IO task for %s may still be running after "
                "15s timeout (uncancelable in Python thread pool).",
                ts_code,
            )
            return []
        except Exception as e:
            _log_with_severity(
                e,
                "[News] Error dispatching news fetch task for %s: %s",
                ts_code,
                DataSanitizer.sanitize_error(e),
            )
            return []

    @staticmethod
    @log_async_operation(
        operation_name="news_get_stock_news_documents",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_stock_news_documents(
        ts_code: str,
        window_days: int = 30,
        limit: int = 50,
    ) -> dict:
        """合并个股新闻：巨潮公告 + 东财新闻（非二选一回退）。

        返回 ``{"docs": [...], "coverage": {"announcement": "ok"/"fail", "news": "ok"/"fail"}}``。
        每个 doc 含：ts_code / source_kind（announcement|news）/ title / publish_time（UTC tz-naive datetime，
        存库格式；公告仅日期已补 00:00:00）/ url（无则 None）/ content（可用正文或空串）。

        解析顺序不依赖 ``head()``：收集两源 → 按 publish_time 降序 → 严格过滤到 window_days 窗口 → 再限量。
        单源失败仍返回另一源结果 + coverage 状态；asyncio.CancelledError 必须传播（R2）。
        as_of 历史回放（look-ahead 防护）由时间窗口本身承担，本接口不做单独 as_of 判断。
        """
        if not ts_code:
            return {"docs": [], "coverage": {}}

        symbol = ts_code.split(".")[0]

        def _fetch():
            docs: list[dict] = []
            coverage = {"announcement": "fail", "news": "fail"}

            try:
                announcement_docs, news_docs, coverage = _run_with_python_string_storage(
                    lambda: _fetch_stock_news_core(symbol, ts_code, log_prefix="documents ")
                )
            except Exception as outer_e:
                _log_with_severity(
                    outer_e,
                    "[News] Fatal error fetching stock news documents for %s: %s",
                    ts_code,
                    DataSanitizer.sanitize_error(outer_e),
                )
                # 外层异常（如 string_storage 锁超时）降级为空结果，与重构前闭包初始值一致
                announcement_docs, news_docs = [], []

            # 合并两源（公告在前、新闻在后，排序稳定性与重构前一致）；空 title 文档剔除（documents 口径）
            for src_kind, layer_docs in (("announcement", announcement_docs), ("news", news_docs)):
                for d in layer_docs:
                    title = d["title"]
                    if not title:
                        continue
                    docs.append(
                        {
                            "ts_code": ts_code,
                            "source_kind": src_kind,
                            "title": title,
                            "publish_time": d["publish_time"],
                            "url": d["url"] or None,
                            "content": d["content"],
                        }
                    )

            # 收集两源 → 时间降序 → 窗口过滤 → 限量
            docs.sort(key=lambda d: d["publish_time"] or datetime.datetime.min, reverse=True)  # noqa: DTZ901  # 哨兵边界（None 置尾），排序键不参与时区运算，与 naive publish_time 同口径
            now_utc = get_now().astimezone(datetime.UTC).replace(tzinfo=None)
            cutoff = now_utc - timedelta(days=window_days)
            windowed = [d for d in docs if d["publish_time"] is None or d["publish_time"] >= cutoff]
            return windowed[:limit], coverage

        try:
            future = ThreadPoolManager().run_async(TaskType.IO, _fetch)
            docs, coverage = await asyncio.wait_for(future, timeout=15.0)
            return {"docs": docs, "coverage": coverage}
        except TimeoutError as e:
            _log_with_severity(e, "[News] documents fetch timeout for %s", ts_code)
            logger.warning(
                "[News] Background IO task for %s may still be running after "
                "15s timeout (uncancelable in Python thread pool).",
                ts_code,
            )
            return {"docs": [], "coverage": {}}
        except Exception as e:
            _log_with_severity(
                e,
                "[News] Error dispatching news documents task for %s: %s",
                ts_code,
                DataSanitizer.sanitize_error(e),
            )
            return {"docs": [], "coverage": {}}

    @staticmethod
    @log_async_operation(
        operation_name="news_get_latest_global_news",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_latest_global_news(limit: int | None = 20, as_of: date | None = None):
        """
        Get major financial news (CCTV / Major Portals)

        as_of: When set to a historical date, returns empty list to prevent
        look-ahead bias in backtesting / AI context construction.
        """
        # 保留原有的 look-ahead bias 保护逻辑
        if as_of is not None and as_of != get_now().date():
            return []

        global _CLS_CONSECUTIVE_FAILURES, _CLS_CIRCUIT_OPENED_AT
        now_ts = get_now().timestamp()  # 遵循系统统一时间获取规范

        # 1. 熔断判定（F5-P1: 锁内读取 failures 与 opened_at 保证一致性，锁外判断+记日志）
        with _CLS_STATE_LOCK:
            cls_failures = _CLS_CONSECUTIVE_FAILURES
            cls_opened_at = _CLS_CIRCUIT_OPENED_AT

        if cls_failures >= _CLS_FAILURE_THRESHOLD:
            # 熔断开启期间（冷却窗口内），直接快速失败
            if now_ts - cls_opened_at < _CLS_CIRCUIT_COOLDOWN_SECONDS:
                logger.warning("[NewsFetcher] CLS circuit breaker is OPEN. Fast failing request.")
                return []
            else:
                logger.info("[NewsFetcher] CLS circuit breaker is HALF-OPEN. Attempting probe request.")

        # 2. 直连请求函数 (移出全局锁，避免 akshare 挂起时死锁)
        async def _fetch_cls():
            url = "https://www.cls.cn/api/cache?name=telegraph"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.cls.cn/telegraph",
            }
            # B16: requests → httpx.AsyncClient（async-native IO，可被外层 wait_for 取消）
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            # B16: httpx async-native，直接 await（无需线程池提交）
            data = await _fetch_cls()

            # 请求成功，闭合熔断器并清空计数（F5-P1: 锁内重置，锁外记日志）
            with _CLS_STATE_LOCK:
                was_open = _CLS_CONSECUTIVE_FAILURES >= _CLS_FAILURE_THRESHOLD
                _CLS_CONSECUTIVE_FAILURES = 0

            if was_open:
                logger.info("[NewsFetcher] CLS circuit breaker CLOSED (recovered).")

            # 3. 健壮解析 JSON
            if not data or not isinstance(data, dict) or "data" not in data or "roll_data" not in data["data"]:
                logger.warning("[NewsFetcher] CLS API response missing expected structure")
                return []

            roll_data = data["data"]["roll_data"]
            if not isinstance(roll_data, list) or not roll_data:
                return []

            news_list = []

            for item in roll_data[:limit] if limit is not None else roll_data:
                if not isinstance(item, dict):
                    continue

                # 获取标题与内容（如果不存在 title 则回退至 content 截断）
                # data 层不感知 locale，title 为空时返回业务 code "no_title"，
                # 由 VM/View 层维护 code → i18n key 映射表在渲染时翻译。
                title = item.get("title") or item.get("content", "")
                title_str = str(title).strip()
                title_code = "no_title" if not title_str else ""

                content_str = str(item.get("content") or "").strip()

                # ctime 时间戳转换：自动检测毫秒/秒级
                ctime = item.get("ctime")
                if ctime:
                    try:
                        ctime_val = float(ctime)
                        # 毫秒级时间戳检测：大于 1e12 视为毫秒
                        if ctime_val > 1e12:
                            ctime_val = ctime_val / 1000.0
                        dt = datetime.datetime.fromtimestamp(ctime_val, tz=CST_TZ)
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception as conversion_err:
                        _log_with_severity(
                            conversion_err,
                            "[NewsFetcher] Timestamp conversion fallback: %s",
                            DataSanitizer.sanitize_error(conversion_err),
                        )
                        time_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    time_str = get_now().strftime("%Y-%m-%d %H:%M:%S")

                news_list.append(
                    {"title": title_str, "title_code": title_code, "content": content_str, "time": time_str}
                )

            # 按时间降序排列
            news_list.sort(key=lambda x: x["time"], reverse=True)
            return news_list

        except RuntimeError as infra_err:
            # 基础设施错误（如线程池未初始化）不应计入 CLS API 连续失败计数
            _log_with_severity(
                infra_err,
                "[NewsFetcher] CLS fetch skipped due to infrastructure error: %s",
                DataSanitizer.sanitize_error(infra_err),
            )
            return []
        except Exception as e:
            # 异常处理，递增连续失败计数并视情况触发熔断
            # F5-P1: 锁内完成 read-modify-write（递增+阈值判断+熔断时间设置），锁外记日志
            with _CLS_STATE_LOCK:
                _CLS_CONSECUTIVE_FAILURES += 1
                count = _CLS_CONSECUTIVE_FAILURES
                if count >= _CLS_FAILURE_THRESHOLD:
                    _CLS_CIRCUIT_OPENED_AT = get_now().timestamp()
                    is_threshold = count == _CLS_FAILURE_THRESHOLD
                else:
                    is_threshold = None  # 未达阈值标志

            if is_threshold is True:
                _log_with_severity(
                    e,
                    "[NewsFetcher] CLS API failed 3 consecutive times. Circuit breaker OPENED. Error: %s",
                    DataSanitizer.sanitize_error(e),
                )
            elif is_threshold is False:
                # 半开探活失败：重置冷却计时器，使下一个 60s 窗口从此刻重新计时
                _log_with_severity(
                    e,
                    "[NewsFetcher] CLS API failed in HALF-OPEN state. Cooldown reset. Error: %s",
                    DataSanitizer.sanitize_error(e),
                )
            else:
                _log_with_severity(
                    e,
                    "[NewsFetcher] CLS API request failed (%d/%d): %s",
                    count,
                    _CLS_FAILURE_THRESHOLD,
                    DataSanitizer.sanitize_error(e),
                )
            return []

    @staticmethod
    @log_async_operation(
        operation_name="news_get_us_major_moves",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_us_major_moves(as_of: date | None = None):
        """
        Fetch major US Tech giants performance (NVDA, TSLA, AAPL, MSFT, GOOGL, AMZN, META).
        Uses Sina Finance Custom Sort API (Verified Working).
        Cached for 5 minutes to avoid repeated external API calls.
        S2-4 fix: Added retry logic with exponential backoff.

        P0-4 fix: as_of parameter prevents look-ahead bias. When as_of is not
        today's date (i.e. historical replay), returns empty string instead of
        injecting real-time data into a past context.
        """
        if as_of is not None:
            if isinstance(as_of, datetime.datetime):
                as_of = as_of.date()
            if as_of != get_now().date():
                logger.debug(
                    "[News] Skipping US major moves for historical date %s (look-ahead guard)",
                    as_of,
                )
                return ""

        with _US_MOVES_CACHE_LOCK:
            cached = _US_MOVES_CACHE.get("result")
            if cached is not None:
                return cached

        MAX_RETRIES = 3
        RETRY_DELAY = 1.0

        async def _fetch():
            # Direct call to Sina US API
            # SEC-006: Use HTTPS to prevent MITM tampering on the wire.
            url = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/IO/US_CategoryService.getList"
            params = {
                "page": "1",
                "num": "100",
                "sort": "mktcap",  # Sort by market cap to get giants
                "asc": "0",
                "market": "",
                "id": "",
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            }

            from utils.proxy_manager import ProxyManager

            # B16: httpx 使用 get_httpx_proxy_config（proxy 映射格式兼容）
            proxy_cfg = ProxyManager.get_httpx_proxy_config()
            async with httpx.AsyncClient(timeout=10, **proxy_cfg) as client:
                resp = await client.get(url, params=params, headers=headers)
                content = resp.text

                start = content.find("(")
                end = content.rfind(")")
                if start != -1 and end != -1 and start < end:
                    json_str = content[start + 1 : end]
                    try:
                        data = json.loads(json_str)
                        # SEC-006: schema validation — reject unexpected structures
                        # that could result from MITM tampering on legacy HTTP.
                        if not isinstance(data, dict):
                            logger.warning("[News] Sina US API returned non-dict JSON, skipping")
                            # F5-P1: 锁内递增，锁外无 IO
                            with _SINA_STATE_LOCK:
                                _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                            return []
                        result = data.get("data", [])
                        if not isinstance(result, list):
                            logger.warning("[News] Sina US API 'data' field is not a list, skipping")
                            # F5-P1: 锁内递增
                            with _SINA_STATE_LOCK:
                                _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                            return []
                        if result:
                            # F5-P1: 锁内重置
                            with _SINA_STATE_LOCK:
                                _SINA_CONSECUTIVE_EMPTY["us_api"] = 0
                        else:
                            # F5-P1: read-modify-write 同临界区，logger 移出锁外
                            with _SINA_STATE_LOCK:
                                _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                                count = _SINA_CONSECUTIVE_EMPTY["us_api"]
                            if count >= _SINA_EMPTY_THRESHOLD:
                                logger.warning(
                                    "[News] Sina US API returned empty data %d consecutive times. Data source may be degraded.",
                                    count,
                                )
                            else:
                                logger.warning("[News] Sina US API returned empty data (consecutive: %d)", count)
                        return result
                    except json.JSONDecodeError as e:
                        # F5-P1: read-modify-write 同临界区，logger 移出锁外
                        with _SINA_STATE_LOCK:
                            _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                            count = _SINA_CONSECUTIVE_EMPTY["us_api"]
                        _log_with_severity(
                            e,
                            "[News] Failed to decode JSON from Sina US API (consecutive: %d)",
                            count,
                        )
                        return []
            # F5-P1: read-modify-write 同临界区，logger 移出锁外
            with _SINA_STATE_LOCK:
                _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                count = _SINA_CONSECUTIVE_EMPTY["us_api"]
            log_fn = logger.error if count >= _SINA_EMPTY_THRESHOLD else logger.warning
            log_fn("[News] Sina US API JSONP structure invalid (consecutive: %d)", count)
            return []

        data_list = None
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                data_list = await _fetch()  # B16: httpx async-native，直接 await
                if data_list is not None:
                    break
            except Exception as e:
                last_error = e
                _log_with_severity(
                    e,
                    "[News] US API attempt %d/%d failed: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    DataSanitizer.sanitize_error(e),
                )
                if attempt < MAX_RETRIES - 1:
                    import asyncio

                    await asyncio.sleep(RETRY_DELAY * (2**attempt))

        if not data_list:
            error_msg = f"Global data unavailable after {MAX_RETRIES} retries"
            if last_error:
                error_msg += f": {last_error}"
            return error_msg

        try:
            # Key mappings (Sina Names -> Ticker/English)
            key_tickers = [
                "NVDA",
                "TSLA",
                "AAPL",
                "MSFT",
                "GOOGL",
                "AMZN",
                "META",
                "AMD",
            ]

            summary = []
            for item in data_list:
                # Sina structure: {name: "NVDA", cname: "英伟达", price: "135.2", diff: "3.2", chg: "2.45", ...}
                ticker = item.get("name", "")
                cname = item.get("cname", "")

                matched = False
                if ticker in key_tickers:
                    matched = True

                # Safe float conversion
                try:
                    pct = float(item.get("chg", 0))
                except (ValueError, TypeError):
                    pct = 0.0

                if matched or abs(pct) > 3.0:
                    display_name = ticker if ticker else cname
                    summary.append(f"{display_name}: {pct}%")

            # If no giants found (unlikely with mktcap sort), take top 3 movers
            if not summary and data_list:
                for item in data_list[:5]:
                    name = item.get("name", "Unknown")
                    chg = item.get("chg", "0")
                    summary.append(f"{name}: {chg}%")

            result = ", ".join(summary)
            with _US_MOVES_CACHE_LOCK:
                _US_MOVES_CACHE["result"] = result
            return result

        except Exception as e:
            _log_with_severity(
                e,
                "[News] Error fetching US moves: %s",
                DataSanitizer.sanitize_error(e),
            )
            return "Global data error."

    @staticmethod
    @log_async_operation(
        operation_name="news_get_hot_concepts",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def get_hot_concepts(limit: int | None = 8):
        """
        Get top performing concept boards.
        Uses Sina Finance (verified working, not blocked).
        B2: 直连 HTTPS 端点（httpx async-native），消除 akshare 内部明文 HTTP 与无 timeout
        问题（SEC-006：HTTPS 防 MITM 篡改，概念热度进入 AI 上下文）。解析与 akshare
        stock_sector_spot 内部一致：JSONP → dict → 逐条 value.split(",")，仅消费「板块」/
        「涨跌幅」两列；列序漂移由本项目独立承担（新浪端点结构变更时需同步跟进）。

        Returns:
            list[dict] on success. Empty list on failure or when source returns
            no data, so callers can safely use the result without try/except.
            Only asyncio.CancelledError is propagated for graceful shutdown.
        """

        async def _fetch() -> list[tuple[str, float]]:
            # B16/B2: httpx async-native IO（可被外层 wait_for 取消）；HTTPS 防 MITM
            from utils.proxy_manager import ProxyManager

            proxy_cfg = ProxyManager.get_httpx_proxy_config()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            async with httpx.AsyncClient(timeout=_HOT_CONCEPTS_TIMEOUT_SECONDS, **proxy_cfg) as client:
                resp = await client.get(
                    "https://money.finance.sina.com.cn/q/view/newFLJK.php",
                    params={"param": "class"},
                    headers=headers,
                )
                resp.raise_for_status()
                text = resp.text

            start = text.find("{")
            if start == -1:
                raise ValueError("[News] Hot concepts JSONP response missing '{' prefix")
            json_data = json.loads(text[start:])
            if not isinstance(json_data, dict):
                # 响应结构异常（含 JSON null / 非对象）视为数据源故障
                raise ValueError("[News] Hot concepts response is not a JSON object")
            rows: list[tuple[str, float]] = []
            for value in json_data.values():
                fields = value.split(",")
                # 列序: label,板块,公司家数,平均价格,涨跌额,涨跌幅,总成交量,总成交额,
                # 股票代码,个股-涨跌幅,个股-当前价,个股-涨跌额,股票名称
                if len(fields) < 6:
                    continue  # 新浪列序漂移防御：列数不足时跳过，不产出错误数据
                name = fields[1].strip()
                if not name:
                    continue
                try:
                    change_val = float(fields[5])
                except (ValueError, TypeError):
                    change_val = 0.0
                if math.isnan(change_val):
                    change_val = 0.0
                rows.append((name, change_val))
            return rows

        try:
            rows = await asyncio.wait_for(_fetch(), timeout=_HOT_CONCEPTS_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            logger.warning("[News] Hot concepts fetch cancelled during shutdown.")
            raise
        except TimeoutError as e:
            # F5-P1: read-modify-write 同临界区，logger 移出锁外
            with _SINA_STATE_LOCK:
                _SINA_CONSECUTIVE_FAILURES["concept"] += 1
                count = _SINA_CONSECUTIVE_FAILURES["concept"]
            if count % _SINA_FAILURE_ERROR_INTERVAL == 0:
                _log_with_severity(
                    e,
                    "[News] Hot concepts fetch timed out (%.0fs). Consecutive failures: %d. Data source may be degraded.",
                    _HOT_CONCEPTS_TIMEOUT_SECONDS,
                    count,
                )
            else:
                _log_with_severity(
                    e,
                    "[News] Hot concepts fetch timed out (%.0fs). Consecutive failures: %d.",
                    _HOT_CONCEPTS_TIMEOUT_SECONDS,
                    count,
                )
            return []
        except Exception as e:
            # F5-P1: read-modify-write 同临界区，logger 移出锁外
            with _SINA_STATE_LOCK:
                _SINA_CONSECUTIVE_FAILURES["concept"] += 1
                count = _SINA_CONSECUTIVE_FAILURES["concept"]
            if count % _SINA_FAILURE_ERROR_INTERVAL == 0:
                _log_with_severity(
                    e,
                    "[News] Hot concepts fetch failed (%d consecutive). Error: %s",
                    count,
                    DataSanitizer.sanitize_error(e),
                )
            else:
                _log_with_severity(
                    e,
                    "[News] Hot concepts fetch failed (%d consecutive). Error: %s",
                    count,
                    DataSanitizer.sanitize_error(e),
                )
            return []

        if not rows:
            # 解析成功但无有效条目 — treat as empty (与既有 df.empty 语义一致)
            # F5-P1: empty 递增 + failures 重置需同临界区（避免 failures 未重置时 empty 计数已递增）
            with _SINA_STATE_LOCK:
                _SINA_CONSECUTIVE_EMPTY["concept"] += 1
                _SINA_CONSECUTIVE_FAILURES["concept"] = 0
                count = _SINA_CONSECUTIVE_EMPTY["concept"]
            if count >= _SINA_EMPTY_THRESHOLD:
                logger.warning(
                    "[News] Concept boards data empty %d consecutive times. Data source may be degraded.",
                    count,
                )
            return []

        # F5-P1: 成功路径，两个计数器重置需同临界区
        with _SINA_STATE_LOCK:
            _SINA_CONSECUTIVE_EMPTY["concept"] = 0
            _SINA_CONSECUTIVE_FAILURES["concept"] = 0

        # 涨跌幅降序取 top-N（与既有 pandas sort_values(ascending=False) 语义一致）
        rows.sort(key=lambda item: item[1], reverse=True)

        results = []
        for name, change_val in rows[: limit if limit is not None else len(rows)]:
            change_str = f"{change_val:.2f}%"

            # Color: red=up, green=down, gray=flat
            if change_val > 0:
                color = "red"
            elif change_val < 0:
                color = "green"
            else:
                color = "grey"

            results.append({"name": name, "change": change_str, "color": color})

        return results
