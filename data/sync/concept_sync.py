"""Concept sync strategies.

Three strategies for syncing stock-concept mappings from different sources:
1. AKShareConceptSyncStrategy — AKShare East-Money concept boards (3 concurrent, 3 retry).
2. LimitListSyncStrategy — Tushare limit_list (涨跌停) daily rebuild.
3. AIConceptTagSyncStrategy — LLM-driven concept tagging fallback (manual trigger only).

All strategies inherit ISyncStrategy and obey the standard SyncContext/SyncResult
contract. CancelledError is always propagated (R2). External IO methods are
decorated with @log_async_operation (§3.2). Error classification uses
classify_error + classify_severity (§5.7).
"""

import asyncio
import json
import logging
import typing

from data.external.akshare_concept_client import AkshareConceptClient
from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.stock_dao import StockDao
from data.sync.base import ISyncStrategy, SyncResult, SyncStatus, safe_error
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

# Concurrency / retry tuning for AKShare concept sync.
_AKSHARE_CONCURRENCY = 3
_AKSHARE_MAX_RETRIES = 3
_AKSHARE_RETRY_BASE_DELAY = 1.0  # seconds; exponential backoff: 1, 2, 4

# S7: AKShare 循环体内取消检查的时间间隔（秒）。
# 项目内存约束："长运行操作必须每 2 秒检查 cancel_event"。旧实现每 200 条
# board 检查一次，单 board 最坏 7s+，最坏 1000s 才响应取消，远超 2s 红线。
_AKSHARE_CANCEL_CHECK_INTERVAL = 2.0

# Default batch size for AI concept tagging.
_AI_TAG_DEFAULT_BATCH = 50

# Polling interval (seconds) for cancel-aware LLM calls. Project memory hard
# constraint: long-running operations must check cancel_event every 2 seconds.
_AI_TAG_CANCEL_POLL_INTERVAL = 2.0


def _to_ts_code(code: str) -> str:
    """Convert a 6-digit AKShare code to Tushare ts_code format.

    Rules (simplified, covers A-share main boards):
    - 60/68/90 prefix → .SH (Shanghai)
    - 00/30/20 prefix → .SZ (Shenzhen)
    - 43/83/87/92 prefix → .BJ (Beijing)
    - fallback → .SZ
    """
    if not code or len(code) != 6 or not code.isdigit():
        return code
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "20")):
        return f"{code}.SZ"
    if code.startswith(("43", "83", "87", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


class AKShareConceptSyncStrategy(ISyncStrategy):
    """Sync East-Money concept boards and constituents via AKShare.

    Fetches the concept board list, then concurrently fetches constituents for
    each board (3 concurrent, 3 retries with exponential backoff). Results are
    upserted via ``StockDao.upsert_em_concepts``.
    """

    @log_async_operation(
        operation_name="AKShareConceptSyncStrategy.run",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def _run_impl(self, **kwargs: typing.Any) -> SyncResult:
        result = SyncResult()
        try:
            if self._check_cancelled(result):
                return result

            client = AkshareConceptClient()
            df_boards = await client.get_concept_list()

            if df_boards is None or df_boards.empty:
                logger.debug("[AKShareConceptSync] Empty concept board list, nothing to sync.")
                return result

            if self._check_cancelled(result):
                return result

            semaphore = asyncio.Semaphore(_AKSHARE_CONCURRENCY)
            records: list[dict] = []
            failed_boards: list[str] = []

            async def sync_one_board(board_name: str, board_code: str) -> None:
                if self._cancelled:
                    return
                async with semaphore:
                    if self._cancelled:
                        return
                    for attempt in range(_AKSHARE_MAX_RETRIES):
                        try:
                            df_cons = await client.get_concept_constituents(board_name)
                            if df_cons is not None and not df_cons.empty:
                                concept_id = f"{StockDao.EM_CONCEPT_PREFIX}{board_code}"
                                for code in df_cons["代码"].astype(str):
                                    records.append(
                                        {
                                            "ts_code": _to_ts_code(code),
                                            "concept_id": concept_id,
                                            "concept_name": board_name,
                                        }
                                    )
                            return
                        except asyncio.CancelledError:
                            raise
                        except EngineDisposedError:
                            raise
                        except Exception as e:
                            error_info = classify_error(e, context="general")
                            severity = classify_severity(e, context="general")
                            if severity == "system":
                                logger.critical(
                                    "[AKShareConceptSync] SYSTEM-LEVEL failure for board %s: %s",
                                    board_name,
                                    safe_error(e),
                                    exc_info=True,
                                )
                                raise
                            if attempt < _AKSHARE_MAX_RETRIES - 1:
                                delay = _AKSHARE_RETRY_BASE_DELAY * (2**attempt)
                                if severity == "recoverable":
                                    logger.warning(
                                        "[AKShareConceptSync] Retry %s attempt %d (%s): %s",
                                        board_name,
                                        attempt + 1,
                                        error_info["code"],
                                        safe_error(e),
                                        exc_info=True,
                                    )
                                else:
                                    logger.error(
                                        "[AKShareConceptSync] Retry %s attempt %d (%s): %s",
                                        board_name,
                                        attempt + 1,
                                        error_info["code"],
                                        safe_error(e),
                                        exc_info=True,
                                    )
                                await asyncio.sleep(delay)
                            else:
                                failed_boards.append(f"{board_name}: {e}")
                                if severity == "recoverable":
                                    logger.warning(
                                        "[AKShareConceptSync] Failed board %s after %d retries (%s): %s",
                                        board_name,
                                        _AKSHARE_MAX_RETRIES,
                                        error_info["code"],
                                        safe_error(e),
                                        exc_info=True,
                                    )
                                else:
                                    logger.error(
                                        "[AKShareConceptSync] Failed board %s after %d retries (%s): %s",
                                        board_name,
                                        _AKSHARE_MAX_RETRIES,
                                        error_info["code"],
                                        safe_error(e),
                                        exc_info=True,
                                    )

            # Phase 2F + S7: 循环体按时间维度（每 2 秒）检查 _check_cancelled。
            # 旧实现每 200 条 board 检查一次，单 board 最坏 7s+，最坏 1000s 才响应取消，远超 2s 红线。
            tasks: list = []
            last_cancel_check = get_now()
            for _, row in df_boards.iterrows():
                now = get_now()
                if (now - last_cancel_check).total_seconds() >= _AKSHARE_CANCEL_CHECK_INTERVAL:
                    last_cancel_check = now
                    if self._check_cancelled(result):
                        return result
                tasks.append(sync_one_board(str(row["板块名称"]), str(row["板块代码"])))
            await gather_return_exceptions_propagating_cancel(*tasks)

            if self._check_cancelled(result):
                return result

            if records:
                saved = await self.context.cache.stock_dao.upsert_em_concepts(records)
                result.added = saved or 0

            if failed_boards:
                result.status = SyncStatus.PARTIAL.value
                result.errors.extend(failed_boards)

            logger.info(
                "[AKShareConceptSync] Done | added=%d, failed_boards=%d",
                result.added,
                len(failed_boards),
            )
        except asyncio.CancelledError:
            result.status = SyncStatus.CANCELLED.value
            raise
        except EngineDisposedError:
            logger.warning("[AKShareConceptSync] Engine disposed, stopping.")
            result.status = SyncStatus.FAILED.value
            result.errors.append("Engine disposed during sync")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[AKShareConceptSync] SYSTEM-LEVEL failure: %s", safe_error(e), exc_info=True)
                raise
            logger.error("[AKShareConceptSync] Operational error: %s", safe_error(e), exc_info=True)
            result.status = SyncStatus.FAILED.value
            result.errors.append(error_info["message_key"])

        return result


class LimitListSyncStrategy(ISyncStrategy):
    """Sync Tushare limit_list (涨跌停统计) as daily limit-reason concepts.

    Clears yesterday's LIMIT_ prefixed concepts, then fetches today's limit_list
    and upserts via ``StockDao.upsert_limit_concepts``. TushareAPIPermissionError
    degrades gracefully to SUCCESS + warning (积分不足降级).
    """

    @log_async_operation(
        operation_name="LimitListSyncStrategy.run",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def _run_impl(
        self,
        trade_date: str | None = None,
        **kwargs: typing.Any,
    ) -> SyncResult:
        result = SyncResult()
        try:
            if self._check_cancelled(result):
                return result

            stock_dao = self.context.cache.stock_dao

            try:
                df = await self.context.api.get_limit_list(trade_date=trade_date)
            except TushareAPIPermissionError as e:
                logger.warning(
                    "[LimitListSync] Permission denied for limit_list (积分不足), skipping: %s",
                    e.api_name,
                )
                result.warnings.append(
                    f"Tushare limit_list permission denied ({e.api_name}), skipped",
                )
                return result

            if df is None or df.empty:
                logger.debug("[LimitListSync] Empty limit_list for trade_date=%s", trade_date)
                return result

            records: list[dict] = []
            # Phase 2F: 循环体每 200 条检查 _check_cancelled，响应取消信号
            for i, (_, row) in enumerate(df.iterrows()):
                if i > 0 and i % 200 == 0 and self._check_cancelled(result):
                    return result
                ts_code = row.get("ts_code")
                if not ts_code:
                    continue
                name = str(row.get("name", "")) or ""
                records.append(
                    {
                        "ts_code": str(ts_code),
                        "concept_id": f"{StockDao.LIMIT_CONCEPT_PREFIX}{ts_code}",
                        "concept_name": name,
                    }
                )

            if self._check_cancelled(result):
                return result

            # S11 fix: fetch 成功后再 clear+upsert，避免 clear 后 fetch 失败导致旧数据丢失
            await stock_dao.clear_today_limit_concepts()
            if records:
                saved = await stock_dao.upsert_limit_concepts(records)
                result.added = saved or 0

            logger.info(
                "[LimitListSync] Done | trade_date=%s, added=%d",
                trade_date,
                result.added,
            )
        except asyncio.CancelledError:
            result.status = SyncStatus.CANCELLED.value
            raise
        except EngineDisposedError:
            logger.warning("[LimitListSync] Engine disposed, stopping.")
            result.status = SyncStatus.FAILED.value
            result.errors.append("Engine disposed during sync")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[LimitListSync] SYSTEM-LEVEL failure: %s", safe_error(e), exc_info=True)
                raise
            logger.error("[LimitListSync] Operational error: %s", safe_error(e), exc_info=True)
            result.status = SyncStatus.FAILED.value
            result.errors.append(error_info["message_key"])

        return result


class AIConceptTagSyncStrategy(ISyncStrategy):
    """LLM-driven concept tagging fallback for stocks without AI concepts.

    Only triggered manually (e.g. via scheduler). If no LLM is configured
    (``context.ai_service`` is None or ``is_cloud_available()`` returns False),
    the strategy skips with status=SUCCESS and skipped>0. Otherwise it fetches
    untagged stocks, asks the LLM to infer core concepts, and upserts via
    ``StockDao.upsert_ai_concepts``.

    错题本 (P1-6): run 开始时优先从 ``ai_concept_failures`` 表拉取可重试股票
    (retry_count < max_retry AND next_retry_at <= now)，再从
    ``get_stocks_without_ai_concepts`` 拉取补充到 batch_size。失败时 upsert 入
    错题本；成功后从错题本删除。

    取消粒度 (P0-2): LLM 单次调用通过 ``_cancellable_llm_call`` 包装，每
    ``_AI_TAG_CANCEL_POLL_INTERVAL`` (2s) 检查 ``context.cancel_event``，最长
    2 秒内响应取消。
    """

    @log_async_operation(
        operation_name="AIConceptTagSyncStrategy.run",
        threshold_ms=PerfThreshold.AI_INFERENCE,
    )
    async def _run_impl(
        self,
        batch_size: int = _AI_TAG_DEFAULT_BATCH,
        **kwargs: typing.Any,
    ) -> SyncResult:
        result = SyncResult()
        try:
            ai_service = self.context.ai_service
            if ai_service is None or not ai_service.is_cloud_available():
                logger.info("[AIConceptTagSync] LLM not configured, skipping AI concept tagging.")
                result.skipped = 1
                result.warnings.append("LLM not configured, AI concept tagging skipped")
                return result

            if self._check_cancelled(result):
                return result

            stock_dao = self.context.cache.stock_dao
            cancel_event = getattr(self.context, "cancel_event", None)
            # 配置在循环内不变，提到循环外读取一次即可（避免每个标的重复读 config）
            search_engine = self.context.config.get_ai_concept_search_engine()

            # 错题本优先重试：先从失败队列拉取（max_retry + cooldown 过滤）
            # EngineDisposedError 必须传播（R5），不可作为可恢复错误吞掉
            retry_pending: list[tuple[str, str]] = []
            try:
                retry_pending = await stock_dao.get_ai_concept_failures_for_retry(batch_size)
            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                raise
            except Exception as e:
                error_info = classify_error(e, context="general")
                severity = classify_severity(e, context="general")
                if severity == "system":
                    logger.critical(
                        "[AIConceptTagSync] SYSTEM-LEVEL failure while loading retry queue: %s",
                        safe_error(e),
                        exc_info=True,
                    )
                    raise
                elif severity == "recoverable":
                    logger.warning(
                        "[AIConceptTagSync] Failed to load retry queue, continuing without it (%s): %s",
                        error_info["code"],
                        safe_error(e),
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "[AIConceptTagSync] Failed to load retry queue, continuing without it (%s): %s",
                        error_info["code"],
                        safe_error(e),
                        exc_info=True,
                    )
                retry_pending = []

            retry_codes = {ts_code for ts_code, _ in retry_pending}

            # 补充未打标的新股票
            fresh_pending: list[tuple[str, str]] = []
            remaining = max(0, batch_size - len(retry_pending))
            if remaining > 0:
                try:
                    fresh_pending = await stock_dao.get_stocks_without_ai_concepts(remaining, [])
                except asyncio.CancelledError:
                    raise
                except EngineDisposedError:
                    raise
                except Exception as e:
                    error_info = classify_error(e, context="general")
                    severity = classify_severity(e, context="general")
                    if severity == "system":
                        logger.critical(
                            "[AIConceptTagSync] SYSTEM-LEVEL failure while loading fresh pending: %s",
                            safe_error(e),
                            exc_info=True,
                        )
                        raise
                    elif severity == "recoverable":
                        logger.warning(
                            "[AIConceptTagSync] Failed to load fresh pending (%s): %s",
                            error_info["code"],
                            safe_error(e),
                            exc_info=True,
                        )
                    else:
                        logger.error(
                            "[AIConceptTagSync] Failed to load fresh pending (%s): %s",
                            error_info["code"],
                            safe_error(e),
                            exc_info=True,
                        )
                    fresh_pending = []

            pending = retry_pending + fresh_pending

            if not pending:
                logger.debug("[AIConceptTagSync] No pending stocks for AI concept tagging.")
                return result

            if retry_pending:
                logger.info(
                    "[AIConceptTagSync] Pending: %d retry + %d fresh",
                    len(retry_pending),
                    len(fresh_pending),
                )

            entries: list[dict] = []
            failed: list[str] = []
            succeeded_codes: list[str] = []

            for ts_code, name in pending:
                if self._cancelled:
                    result.status = SyncStatus.CANCELLED.value
                    break
                if cancel_event is not None and hasattr(cancel_event, "is_set") and cancel_event.is_set():
                    result.status = SyncStatus.CANCELLED.value
                    break

                try:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "你是 A 股概念分析专家。根据股票代码和名称，推断该股票的核心概念板块。"
                                '返回 JSON 格式：{"concepts": ["概念1", "概念2", ...]}。'
                                "最多返回 5 个核心概念。如果无法确定，返回空列表。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"股票代码：{ts_code}\n股票名称：{name}",
                        },
                    ]
                    resp = await self._cancellable_llm_call(
                        ai_service,
                        messages,
                        temperature=0.3,
                        timeout=60.0,
                        cancel_event=cancel_event,
                        search_engine=search_engine,
                    )
                    concepts: list[str] = []
                    content = resp.get("content", "") if isinstance(resp, dict) else ""
                    parsed: typing.Any = None
                    if content:
                        try:
                            parsed = json.loads(content)
                        except json.JSONDecodeError:
                            start = content.find("{")
                            if start != -1:
                                try:
                                    parsed, _ = json.JSONDecoder().raw_decode(content[start:])
                                except json.JSONDecodeError:
                                    logger.warning("[AIConceptTagSync] JSON parse failed for %s", ts_code)
                    if isinstance(parsed, dict):
                        raw = parsed.get("concepts", [])
                        if isinstance(raw, list):
                            concepts = [str(c) for c in raw if c]
                    entries.append({"ts_code": ts_code, "concepts": concepts})
                    succeeded_codes.append(ts_code)
                except asyncio.CancelledError:
                    result.status = SyncStatus.CANCELLED.value
                    raise
                except EngineDisposedError:
                    raise
                except Exception as e:
                    failed.append(f"{ts_code}: {e}")
                    error_info = classify_error(e, context="general")
                    severity = classify_severity(e, context="general")
                    if severity == "system":
                        logger.critical(
                            "[AIConceptTagSync] SYSTEM-LEVEL failure for %s: %s",
                            ts_code,
                            DataSanitizer.sanitize_error(e),
                            exc_info=True,
                        )
                        raise
                    elif severity == "recoverable":
                        logger.warning(
                            "[AIConceptTagSync] Failed for %s (%s): %s",
                            ts_code,
                            error_info["code"],
                            DataSanitizer.sanitize_error(e),
                            exc_info=True,
                        )
                    else:
                        logger.error(
                            "[AIConceptTagSync] Failed for %s (%s): %s",
                            ts_code,
                            error_info["code"],
                            DataSanitizer.sanitize_error(e),
                            exc_info=True,
                        )
                    # 写入错题本（不影响主流程；CancelledError/EngineDisposedError 必须传播，R2/R5）
                    try:
                        await stock_dao.upsert_ai_concept_failure(ts_code, name, DataSanitizer.sanitize_error(e))
                    except asyncio.CancelledError:
                        raise
                    except EngineDisposedError:
                        raise
                    except Exception as fe:
                        fe_info = classify_error(fe, context="general")
                        fe_sev = classify_severity(fe, context="general")
                        if fe_sev == "system":
                            logger.critical(
                                "[AIConceptTagSync] SYSTEM-LEVEL failure while persisting failure for %s: %s",
                                ts_code,
                                DataSanitizer.sanitize_error(fe),
                                exc_info=True,
                            )
                            raise
                        elif fe_sev == "recoverable":
                            logger.warning(
                                "[AIConceptTagSync] Failed to persist failure for %s (%s): %s",
                                ts_code,
                                fe_info["code"],
                                DataSanitizer.sanitize_error(fe),
                                exc_info=True,
                            )
                        else:
                            logger.error(
                                "[AIConceptTagSync] Failed to persist failure for %s (%s): %s",
                                ts_code,
                                fe_info["code"],
                                DataSanitizer.sanitize_error(fe),
                                exc_info=True,
                            )

            if self._check_cancelled(result):
                return result

            if entries:
                saved = await stock_dao.upsert_ai_concepts(entries)
                result.added = saved or 0

            # 成功打标的股票：从错题本清除
            if succeeded_codes:
                for ts_code in succeeded_codes:
                    if ts_code in retry_codes:
                        try:
                            await stock_dao.clear_ai_concept_failure(ts_code)
                        except asyncio.CancelledError:
                            raise
                        except EngineDisposedError:
                            raise
                        except Exception as fe:
                            fe_info = classify_error(fe, context="general")
                            fe_sev = classify_severity(fe, context="general")
                            if fe_sev == "system":
                                logger.critical(
                                    "[AIConceptTagSync] SYSTEM-LEVEL failure while clearing failure record for %s: %s",
                                    ts_code,
                                    safe_error(fe),
                                    exc_info=True,
                                )
                                raise
                            elif fe_sev == "recoverable":
                                logger.warning(
                                    "[AIConceptTagSync] Failed to clear failure record for %s (%s): %s",
                                    ts_code,
                                    fe_info["code"],
                                    safe_error(fe),
                                    exc_info=True,
                                )
                            else:
                                logger.error(
                                    "[AIConceptTagSync] Failed to clear failure record for %s (%s): %s",
                                    ts_code,
                                    fe_info["code"],
                                    safe_error(fe),
                                    exc_info=True,
                                )

            if failed:
                result.status = SyncStatus.PARTIAL.value
                result.errors.extend(failed)

            # T5 fix: 清理已达 max_retry 的错题本记录，避免无限累积。
            # 放在主流程末尾、错题本写入/清除之后，确保本批次处理的记录状态先稳定。
            try:
                expired = await stock_dao.delete_expired_failures()
                if expired > 0:
                    logger.info("[AIConceptTagSync] Cleaned %d expired failure records", expired)
            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                raise
            except Exception as fe:
                fe_info = classify_error(fe, context="general")
                fe_sev = classify_severity(fe, context="general")
                if fe_sev == "system":
                    logger.critical(
                        "[AIConceptTagSync] SYSTEM-LEVEL failure while cleaning expired failures: %s",
                        safe_error(fe),
                        exc_info=True,
                    )
                    raise
                elif fe_sev == "recoverable":
                    logger.warning(
                        "[AIConceptTagSync] Failed to clean expired failures (%s): %s",
                        fe_info["code"],
                        safe_error(fe),
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "[AIConceptTagSync] Failed to clean expired failures (%s): %s",
                        fe_info["code"],
                        safe_error(fe),
                        exc_info=True,
                    )

            logger.info(
                "[AIConceptTagSync] Done | added=%d, failed=%d, retry_cleared=%d",
                result.added,
                len(failed),
                sum(1 for c in succeeded_codes if c in retry_codes),
            )
        except asyncio.CancelledError:
            result.status = SyncStatus.CANCELLED.value
            raise
        except EngineDisposedError:
            logger.warning("[AIConceptTagSync] Engine disposed, stopping.")
            result.status = SyncStatus.FAILED.value
            result.errors.append("Engine disposed during sync")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[AIConceptTagSync] SYSTEM-LEVEL failure: %s", safe_error(e), exc_info=True)
                raise
            logger.error("[AIConceptTagSync] Operational error: %s", safe_error(e), exc_info=True)
            result.status = SyncStatus.FAILED.value
            result.errors.append(error_info["message_key"])

        return result

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def _cancellable_llm_call(
        self,
        ai_service: typing.Any,
        messages: list[dict],
        *,
        temperature: float,
        timeout: float,
        cancel_event: typing.Any,
        search_engine: str = "search_std",
    ) -> dict:
        """LLM 调用包装：每 _AI_TAG_CANCEL_POLL_INTERVAL (2s) 检查 cancel_event。

        若取消信号到达，取消底层 LLM task 并 raise CancelledError；否则正常返回
        LLM 响应。底层 LLM 调用本身不受影响（asyncio.shield 保护），但本调用
        会主动 cancel 它以释放资源。
        """
        if cancel_event is None:
            return await ai_service.chat_with_web_search(
                messages,
                temperature=temperature,
                timeout=timeout,
                search_engine=search_engine,
            )

        llm_task = asyncio.create_task(
            ai_service.chat_with_web_search(
                messages,
                temperature=temperature,
                timeout=timeout,
                search_engine=search_engine,
            ),
        )
        try:
            while not llm_task.done():
                if hasattr(cancel_event, "is_set") and cancel_event.is_set():
                    llm_task.cancel()
                    # await 清理 llm_task 的异常/资源，suppress 二次异常，保留原始 CancelledError 传播
                    # T7 fix: 记录 llm_task 的原始异常（如有），便于调试；不改变 suppress 行为
                    # L4 fix: 补充 exc_info=True 保留完整 traceback
                    # L3 fix: 不在此处 raise，避免覆盖外层 CancelledError；异常链通过 logger.debug 记录
                    try:
                        await llm_task
                    except asyncio.CancelledError:
                        pass  # 内部取消被 suppress，外层 raise 传播外层 CancelledError（R2）
                    except Exception as llm_err:
                        # 不 raise：避免覆盖外层 CancelledError 传播（R2）。
                        # classify_error/classify_severity 仅用于记录分类，不改控制流。
                        llm_info = classify_error(llm_err, context="general")
                        llm_sev = classify_severity(llm_err, context="general")
                        logger.debug(
                            "[AIConceptTagSync] llm_task suppressed during cancel (%s/%s): %r",
                            llm_info["code"],
                            llm_sev,
                            DataSanitizer.sanitize_error(llm_err),
                            exc_info=True,
                        )
                    raise asyncio.CancelledError("task cancelled by user (cancel_event set in ai concept sync)")
                try:
                    # shield 防止外部 cancel 传播到 LLM task 内部前被 wait_for 吞掉
                    return await asyncio.wait_for(
                        asyncio.shield(llm_task),
                        timeout=_AI_TAG_CANCEL_POLL_INTERVAL,
                    )
                except TimeoutError:
                    continue
            # 走到这里说明 llm_task 已 done（极端边界：循环外完成）
            return await llm_task
        except asyncio.CancelledError:
            if not llm_task.done():
                llm_task.cancel()
                # await 清理 llm_task 的异常/资源，suppress 二次异常，保留原始 CancelledError 传播
                # T7 fix: 同上，记录原始异常便于调试
                # L3/L4 fix: 同上，保留 traceback；不覆盖外层 CancelledError
                try:
                    await llm_task
                except asyncio.CancelledError:
                    pass  # 内部取消被 suppress
                except Exception as llm_err:
                    # 不 raise：避免覆盖外层 CancelledError 传播（R2）。
                    # classify_error/classify_severity 仅用于记录分类，不改控制流。
                    llm_info = classify_error(llm_err, context="general")
                    llm_sev = classify_severity(llm_err, context="general")
                    logger.debug(
                        "[AIConceptTagSync] llm_task suppressed during outer cancel (%s/%s): %r",
                        llm_info["code"],
                        llm_sev,
                        DataSanitizer.sanitize_error(llm_err),
                        exc_info=True,
                    )
            raise
