import asyncio
import datetime
import json
import logging
import threading
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests
from cachetools import TTLCache

from core.i18n import I18n
from utils.sanitizers import DataSanitizer
from utils.log_decorators import log_async_operation, PerfThreshold
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import CST_TZ, get_now

logger = logging.getLogger(__name__)

_US_MOVES_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)
_US_MOVES_CACHE_LOCK = threading.Lock()  # Thread-safe lock for TTLCache access
_SINA_CONSECUTIVE_EMPTY = {"us_api": 0, "concept": 0}
_SINA_CONSECUTIVE_FAILURES = {"concept": 0}
_SINA_EMPTY_THRESHOLD = 3
_SINA_FAILURE_ERROR_INTERVAL = 3
_HOT_CONCEPTS_TIMEOUT_SECONDS = 15.0

# Lock for thread-safe mutation of pd.options.mode.string_storage
_pd_options_lock = threading.Lock()

# CLS circuit breaker state (separate from Sina counters above)
_CLS_CONSECUTIVE_FAILURES = 0
_CLS_FAILURE_THRESHOLD = 3
_CLS_CIRCUIT_OPENED_AT = 0.0
_CLS_CIRCUIT_COOLDOWN_SECONDS = 60.0


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
        except Exception:
            logger.warning("[NewsFetcher] Failed to convert list to DataFrame from %s", source)
            return None
    logger.warning("[NewsFetcher] Unexpected return type from %s: %s", source, type(result).__name__)
    return None


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

        # dynamic market key resolution:
        # Instead of guessing "上交所"/"深交所"/"北交所" and hitting KeyErrors in akshare,
        # akshare actually consolidates all of them under a single key in its column_map.
        # We dynamically fetch the exact default string from its signature definition
        # to perfectly bypass any GBK/UTF-8 mojibake issues on Windows.
        market = ""
        try:
            import akshare.stock_feature.stock_disclosure_cninfo as mod

            market = mod.stock_zh_a_disclosure_report_cninfo.__defaults__[1]  # type: ignore[misc]
        except (ImportError, AttributeError, IndexError, TypeError) as exc:
            logger.debug("[NewsFetcher] Failed to read akshare default market: %s", DataSanitizer.sanitize_error(exc))
            market = "沪深京"  # Fallback to standard standard UTF-8 key

        # Run the IO bound akshare calls in the thread pool
        def _fetch():
            def _fetch_locked():
                # -------------------------------------------------------------
                # Layer 1: 巨潮资讯公告 (CNINFO Official Filings)
                # -------------------------------------------------------------
                try:
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

                        if title_col:
                            news_list = []
                            for _, row in df_cninfo.head(limit if limit is not None else len(df_cninfo)).iterrows():
                                title = str(row.get(title_col, "")).strip()
                                pub_date = str(row.get(time_col, "")) if time_col else ""
                                pub_time = f"{pub_date} 00:00:00" if pub_date else ""

                                news_list.append(
                                    {
                                        "title": title,
                                        "publish_time": pub_time,
                                        "source": "巨潮公告",
                                    },
                                )

                            if news_list:
                                return news_list
                except Exception as e:
                    logger.warning(
                        "[News] CNINFO disclosure failed for %s: %s",
                        ts_code,
                        e,
                        exc_info=True,
                    )

                # -------------------------------------------------------------
                # Layer 2: 东财新闻搜索 (EastMoney News Search) - Fallback
                # -------------------------------------------------------------
                try:
                    df_em = _ensure_dataframe(ak.stock_news_em(symbol=symbol), source="stock_news_em")

                    if df_em is not None and not df_em.empty:
                        news_list = []
                        # EastMoney returns '新闻内容' as title, '新闻链接', '新闻时间', etc.
                        for _, row in df_em.head(limit if limit is not None else len(df_em)).iterrows():
                            title = row.get("新闻标题", row.get("新闻内容", ""))
                            pub_time = row.get("新闻时间", row.get("发布时间", ""))
                            source = row.get("文章来源", "东财新闻")

                            # Clean up title: 東方财富 often adds "[XXX]" prefixes or suffixes
                            title_str = str(title).strip()

                            news_list.append(
                                {
                                    "title": title_str,
                                    "publish_time": str(pub_time),
                                    "source": str(source),
                                },
                            )

                        return news_list
                except Exception as e:
                    logger.warning("[News] EM search failed for %s: %s", ts_code, e)

                return []

            try:
                return _run_with_python_string_storage(_fetch_locked)
            except Exception as outer_e:
                logger.error(
                    "[News] Fatal error fetching stock news for %s: %s",
                    ts_code,
                    DataSanitizer.sanitize_error(outer_e),
                )
                return []

        try:
            # We use the IO Thread Pool with a 15-second timeout via asyncio.wait_for
            # to prevent hanging the AI pipeline if the APIs are slow/dead.
            future = ThreadPoolManager().run_async(TaskType.IO, _fetch)
            return await asyncio.wait_for(future, timeout=15.0)
        except TimeoutError:
            logger.warning("[News] Timeout fetching news for %s", ts_code)
            return []
        except Exception as e:
            logger.error(
                "[News] Error dispatching news fetch task for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
            )
            return []

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

        # 1. 熔断判定
        if _CLS_CONSECUTIVE_FAILURES >= _CLS_FAILURE_THRESHOLD:
            # 熔断开启期间（冷却窗口内），直接快速失败
            if now_ts - _CLS_CIRCUIT_OPENED_AT < _CLS_CIRCUIT_COOLDOWN_SECONDS:
                logger.warning("[NewsFetcher] CLS circuit breaker is OPEN. Fast failing request.")
                return []
            else:
                logger.info("[NewsFetcher] CLS circuit breaker is HALF-OPEN. Attempting probe request.")

        # 2. 直连请求函数 (移出全局锁，避免 akshare 挂起时死锁)
        def _fetch_cls():
            url = "https://www.cls.cn/api/cache?name=telegraph"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.cls.cn/telegraph",
            }
            with requests.get(url, headers=headers, timeout=5.0) as resp:
                resp.raise_for_status()
                return resp.json()

        try:
            # 使用全局 IO 线程池异步执行请求，防止阻塞 asyncio 事件循环
            data = await ThreadPoolManager().run_async(TaskType.IO, _fetch_cls)

            # 请求成功，闭合熔断器并清空计数
            if _CLS_CONSECUTIVE_FAILURES >= _CLS_FAILURE_THRESHOLD:
                logger.info("[NewsFetcher] CLS circuit breaker CLOSED (recovered).")
            _CLS_CONSECUTIVE_FAILURES = 0

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
                title = item.get("title") or item.get("content", "")
                title_str = str(title).strip()
                if not title_str:
                    title_str = I18n.get("news_no_title")

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
                        logger.debug(
                            "[NewsFetcher] Timestamp conversion fallback: %s",
                            DataSanitizer.sanitize_error(conversion_err),
                        )
                        time_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
                else:
                    time_str = get_now().strftime("%Y-%m-%d %H:%M:%S")

                news_list.append({"title": title_str, "content": content_str, "time": time_str})

            # 按时间降序排列
            news_list.sort(key=lambda x: x["time"], reverse=True)
            return news_list

        except RuntimeError as infra_err:
            # 基础设施错误（如线程池未初始化）不应计入 CLS API 连续失败计数
            logger.warning(
                "[NewsFetcher] CLS fetch skipped due to infrastructure error: %s",
                DataSanitizer.sanitize_error(infra_err),
            )
            return []
        except Exception as e:
            # 异常处理，递增连续失败计数并视情况触发熔断
            _CLS_CONSECUTIVE_FAILURES += 1
            if _CLS_CONSECUTIVE_FAILURES >= _CLS_FAILURE_THRESHOLD:
                if _CLS_CONSECUTIVE_FAILURES == _CLS_FAILURE_THRESHOLD:
                    _CLS_CIRCUIT_OPENED_AT = get_now().timestamp()
                    logger.error(
                        "[NewsFetcher] CLS API failed 3 consecutive times. Circuit breaker OPENED. Error: %s",
                        DataSanitizer.sanitize_error(e),
                    )
                else:
                    # 半开探活失败：重置冷却计时器，使下一个 60s 窗口从此刻重新计时
                    _CLS_CIRCUIT_OPENED_AT = get_now().timestamp()
                    logger.error(
                        "[NewsFetcher] CLS API failed in HALF-OPEN state. Cooldown reset. Error: %s",
                        DataSanitizer.sanitize_error(e),
                    )
            else:
                logger.warning(
                    "[NewsFetcher] CLS API request failed (%d/%d): %s",
                    _CLS_CONSECUTIVE_FAILURES,
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

        def _fetch():
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

            proxy_config = ProxyManager.get_requests_proxy_config()
            with requests.get(url, params=params, headers=headers, timeout=10, **(proxy_config or {})) as resp:  # type: ignore[arg-type]
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
                            _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                            return []
                        result = data.get("data", [])
                        if not isinstance(result, list):
                            logger.warning("[News] Sina US API 'data' field is not a list, skipping")
                            _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                            return []
                        if result:
                            _SINA_CONSECUTIVE_EMPTY["us_api"] = 0
                        else:
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
                    except json.JSONDecodeError:
                        _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
                        count = _SINA_CONSECUTIVE_EMPTY["us_api"]
                        log_fn = logger.error if count >= _SINA_EMPTY_THRESHOLD else logger.warning
                        log_fn("[News] Failed to decode JSON from Sina US API (consecutive: %d)", count)
                        return []
            _SINA_CONSECUTIVE_EMPTY["us_api"] += 1
            count = _SINA_CONSECUTIVE_EMPTY["us_api"]
            log_fn = logger.error if count >= _SINA_EMPTY_THRESHOLD else logger.warning
            log_fn("[News] Sina US API JSONP structure invalid (consecutive: %d)", count)
            return []

        data_list = None
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                data_list = await ThreadPoolManager().run_async(TaskType.IO, _fetch)
                if data_list is not None:
                    break
            except Exception as e:
                last_error = e
                logger.warning(
                    "[News] US API attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, DataSanitizer.sanitize_error(e)
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
            logger.error("[News] Error fetching US moves: %s", DataSanitizer.sanitize_error(e))
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

        Returns:
            list[dict] on success. Empty list on failure or when source returns
            no data, so callers can safely use the result without try/except.
            Only asyncio.CancelledError is propagated for graceful shutdown.
        """

        def _fetch():
            # Sina Finance - Concept Boards
            # ProxyManager already whitelists domestic domains via NO_PROXY at startup
            return _ensure_dataframe(
                _run_with_python_string_storage(lambda: ak.stock_sector_spot(indicator="概念")),
                source="stock_sector_spot",
            )

        try:
            # Use Global IO Pool with timeout to prevent hanging on unresponsive API
            df = await asyncio.wait_for(
                ThreadPoolManager().run_async(TaskType.IO, _fetch),
                timeout=_HOT_CONCEPTS_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            logger.warning("[News] Hot concepts fetch cancelled during shutdown.")
            raise
        except TimeoutError:
            _SINA_CONSECUTIVE_FAILURES["concept"] += 1
            count = _SINA_CONSECUTIVE_FAILURES["concept"]
            if count % _SINA_FAILURE_ERROR_INTERVAL == 0:
                logger.error(
                    "[News] Hot concepts fetch timed out (%.0fs). Consecutive failures: %d. Data source may be degraded.",
                    _HOT_CONCEPTS_TIMEOUT_SECONDS,
                    count,
                )
            else:
                logger.warning(
                    "[News] Hot concepts fetch timed out (%.0fs). Consecutive failures: %d.",
                    _HOT_CONCEPTS_TIMEOUT_SECONDS,
                    count,
                )
            return []
        except Exception as e:
            _SINA_CONSECUTIVE_FAILURES["concept"] += 1
            count = _SINA_CONSECUTIVE_FAILURES["concept"]
            if count % _SINA_FAILURE_ERROR_INTERVAL == 0:
                logger.error(
                    "[News] Hot concepts fetch failed (%d consecutive). Error: %s",
                    count,
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
            else:
                logger.warning(
                    "[News] Hot concepts fetch failed (%d consecutive). Error: %s",
                    count,
                    DataSanitizer.sanitize_error(e),
                )
            return []

        if df is None:
            # None means the underlying call returned nothing usable — treat as failure
            _SINA_CONSECUTIVE_FAILURES["concept"] += 1
            count = _SINA_CONSECUTIVE_FAILURES["concept"]
            logger.warning(
                "[News] Hot concepts fetch returned None. Consecutive failures: %d.",
                count,
            )
            if count % _SINA_FAILURE_ERROR_INTERVAL == 0:
                logger.error(
                    "[News] Hot concepts fetch returned None %d consecutive times. Data source may be degraded.",
                    count,
                )
            return []

        if df.empty:
            _SINA_CONSECUTIVE_EMPTY["concept"] += 1
            _SINA_CONSECUTIVE_FAILURES["concept"] = 0
            count = _SINA_CONSECUTIVE_EMPTY["concept"]
            if count >= _SINA_EMPTY_THRESHOLD:
                logger.warning(
                    "[News] Concept boards data empty %d consecutive times. Data source may be degraded.",
                    count,
                )
            return []

        _SINA_CONSECUTIVE_EMPTY["concept"] = 0
        _SINA_CONSECUTIVE_FAILURES["concept"] = 0

        # Sina returns: 板块, 涨跌幅
        if "涨跌幅" in df.columns:
            df = df.sort_values("涨跌幅", ascending=False)

        results = []
        for _, row in df.head(limit if limit is not None else len(df)).iterrows():
            name = row.get("板块", "")
            if not name:
                continue

            try:
                raw_val = row.get("涨跌幅", 0)
                # Handle NaN from pandas
                if pd.isna(raw_val):  # type: ignore[union-attr]
                    change_val = 0.0
                else:
                    change_val = float(raw_val)  # type: ignore[arg-type]
                change_str = f"{change_val:.2f}%"
            except (ValueError, TypeError):
                change_str = "0.00%"
                change_val = 0.0

            # Color: red=up, green=down, gray=flat
            if change_val > 0:
                color = "red"
            elif change_val < 0:
                color = "green"
            else:
                color = "grey"

            results.append({"name": name, "change": change_str, "color": color})

        return results
