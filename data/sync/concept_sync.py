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
from data.sync.base import ISyncStrategy, SyncResult, SyncStatus
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation

logger = logging.getLogger(__name__)

# Concurrency / retry tuning for AKShare concept sync.
_AKSHARE_CONCURRENCY = 3
_AKSHARE_MAX_RETRIES = 3
_AKSHARE_RETRY_BASE_DELAY = 1.0  # seconds; exponential backoff: 1, 2, 4

# Default batch size for AI concept tagging.
_AI_TAG_DEFAULT_BATCH = 50


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
                            if attempt < _AKSHARE_MAX_RETRIES - 1:
                                delay = _AKSHARE_RETRY_BASE_DELAY * (2**attempt)
                                logger.debug(
                                    "[AKShareConceptSync] Retry %s attempt %d: %s",
                                    board_name,
                                    attempt + 1,
                                    e,
                                )
                                await asyncio.sleep(delay)
                            else:
                                failed_boards.append(f"{board_name}: {e}")
                                logger.warning(
                                    "[AKShareConceptSync] Failed board %s after %d retries: %s",
                                    board_name,
                                    _AKSHARE_MAX_RETRIES,
                                    e,
                                )

            tasks = [sync_one_board(str(row["板块名称"]), str(row["板块代码"])) for _, row in df_boards.iterrows()]
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
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(f"[AKShareConceptSync] SYSTEM-LEVEL failure: {e}", exc_info=True)
                raise
            logger.error(f"[AKShareConceptSync] Operational error: {e}", exc_info=True)
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
            await stock_dao.clear_today_limit_concepts()

            if self._check_cancelled(result):
                return result

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
            for _, row in df.iterrows():
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
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(f"[LimitListSync] SYSTEM-LEVEL failure: {e}", exc_info=True)
                raise
            logger.error(f"[LimitListSync] Operational error: {e}", exc_info=True)
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
            pending = await stock_dao.get_stocks_without_ai_concepts(batch_size, [])

            if not pending:
                logger.debug("[AIConceptTagSync] No pending stocks for AI concept tagging.")
                return result

            entries: list[dict] = []
            failed: list[str] = []

            for ts_code, name in pending:
                if self._cancelled:
                    result.status = SyncStatus.CANCELLED.value
                    return result

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
                    resp = await ai_service.chat_with_web_search(
                        messages,
                        temperature=0.3,
                        timeout=60.0,
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
                except asyncio.CancelledError:
                    raise
                except EngineDisposedError:
                    raise
                except Exception as e:
                    failed.append(f"{ts_code}: {e}")
                    logger.warning("[AIConceptTagSync] Failed for %s: %s", ts_code, e)

            if self._check_cancelled(result):
                return result

            if entries:
                saved = await stock_dao.upsert_ai_concepts(entries)
                result.added = saved or 0

            if failed:
                result.status = SyncStatus.PARTIAL.value
                result.errors.extend(failed)

            logger.info(
                "[AIConceptTagSync] Done | added=%d, failed=%d",
                result.added,
                len(failed),
            )
        except asyncio.CancelledError:
            result.status = SyncStatus.CANCELLED.value
            raise
        except EngineDisposedError:
            logger.warning("[AIConceptTagSync] Engine disposed, stopping.")
            result.status = SyncStatus.FAILED.value
            result.errors.append("Engine disposed during sync")
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(f"[AIConceptTagSync] SYSTEM-LEVEL failure: {e}", exc_info=True)
                raise
            logger.error(f"[AIConceptTagSync] Operational error: {e}", exc_info=True)
            result.status = SyncStatus.FAILED.value
            result.errors.append(error_info["message_key"])

        return result
