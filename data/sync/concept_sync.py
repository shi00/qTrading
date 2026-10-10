"""Concept sync strategies.

Two strategies for syncing stock-concept mappings from different sources:
1. AKShareConceptSyncStrategy — AKShare East-Money concept boards (3 concurrent, 3 retry).
2. LimitListSyncStrategy — Tushare limit_list (涨跌停) sync; 停写状态（review08-D3，见类 docstring）。

All strategies inherit ISyncStrategy and obey the standard SyncContext/SyncResult
contract. CancelledError is always propagated (R2). External IO methods are
decorated with @log_async_operation (§3.2). Error classification uses
classify_severity + log_classified (§5.7).
"""

import asyncio
import logging
import time
import typing
from collections.abc import Mapping

from data.external.akshare_concept_client import AkshareConceptClient
from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.stock_dao import StockDao
from data.sync.base import ISyncStrategy, SyncResult, SyncStatus, safe_error
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.error_classifier import classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation

logger = logging.getLogger(__name__)

# Concurrency / retry tuning for AKShare concept sync.
_AKSHARE_CONCURRENCY = 3
_AKSHARE_MAX_RETRIES = 3
_AKSHARE_RETRY_BASE_DELAY = 1.0  # seconds; exponential backoff: 1, 2, 4

# S7: AKShare 循环体内取消检查的时间间隔（秒）。
# 项目内存约束："长运行操作必须每 2 秒检查 cancel_event"。旧实现每 200 条
# board 检查一次，单 board 最坏 7s+，最坏 1000s 才响应取消，远超 2s 红线。
_AKSHARE_CANCEL_CHECK_INTERVAL = 2.0


def _to_ts_code(code: str, code_map: Mapping[str, str]) -> str | None:
    """Convert an AKShare 6-digit code to the authoritative Tushare ts_code.

    经 stocks 表权威映射（code_map: symbol -> ts_code）解析，取代基于前缀的
    交易所猜测（review08 D2）。前缀猜测在遇未来新代码段时会静默产生错误
    ts_code 污染概念表，与 R21「缺失值伪装」精神相悖。

    - 合法 6 位数字代码且映射命中 → 返回权威 ts_code
    - 非法输入（空/长度不符/含非数字）或映射未知 → 返回 None（调用方跳过并
      计入 warnings，显式标记而非猜测 fallback）
    """
    if not code or len(code) != 6 or not code.isdigit():
        return None
    return code_map.get(code)


class AKShareConceptSyncStrategy(ISyncStrategy):
    """Sync East-Money concept boards and constituents via AKShare.

    Fetches the concept board list, then concurrently fetches constituents for
    each board (3 concurrent, 3 retries with exponential backoff). Results are
    persisted via ``StockDao.overwrite_em_concepts``: for each board whose
    constituents were fetched **and fully resolved** to authoritative ts_codes,
    its existing ``EM_`` rows are deleted and rebuilt within one transaction, so
    constituents removed from a board no longer linger. Boards that failed, or
    whose constituents were only partially resolved, are upsert-only (no deletion).
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

            # review08 D2: 预载 stocks 表权威 code→ts_code 映射，成分股循环直接查
            # 内存 dict，避免每次循环 N 次 DB 往返。这是"快照语义"——映射固化在
            # 本次同步启动时刻；概念同步与 stock_basic 批量更新天然低时并发，
            # 可接受。映射预载失败直接走外层 except（FAILED / R5 传播），不降级为
            # 空映射静默空跑（那会伪装成"无可疑"）。
            code_map = await self.context.cache.stock_dao.get_ts_code_map()

            semaphore = asyncio.Semaphore(_AKSHARE_CONCURRENCY)
            records: list[dict] = []
            failed_boards: list[str] = []
            unresolved: set[str] = set()
            # 允许「删除-重建」的板块代码：仅当该板块成分成功抓取且全部解析为权威
            # ts_code 时才登记，避免把抓取失败/部分解析板块的有效旧成分误删。
            replace_board_codes: list[str] = []
            # NOTE(lazy): 仅对本次成功抓取且成分完全解析的板块做删除-重建，不扫描
            # 「已从板块列表消失」的板块. ceiling: 板块停用/更名后其历史 EM_ 行不会被清理.
            # upgrade: 需要全量对账（库内 EM_ 板块集合 vs 当前板块列表 diff）时.

            async def sync_one_board(board_name: str, board_code: str) -> None:
                if self._cancelled:
                    return
                async with semaphore:
                    if self._cancelled:
                        return
                    for attempt in range(_AKSHARE_MAX_RETRIES):
                        try:
                            df_cons = await client.get_concept_constituents(board_name)
                            if df_cons is None or df_cons.empty:
                                # 空响应（软失败）：不替换该板块，保留既有 EM_ 行（R21）
                                logger.debug(
                                    "[AKShareConceptSync] Board %s returned no constituents, skip replacement.",
                                    board_name,
                                )
                                return
                            concept_id = f"{StockDao.EM_CONCEPT_PREFIX}{board_code}"
                            board_records: list[dict] = []
                            board_unresolved = 0
                            for code in df_cons["代码"].astype(str):
                                ts_code = _to_ts_code(code, code_map)
                                if ts_code is None:
                                    # R21: 未知/非法代码显式标记并跳过，不猜测交易所后缀
                                    unresolved.add(code)
                                    board_unresolved += 1
                                    continue
                                board_records.append(
                                    {
                                        "ts_code": ts_code,
                                        "concept_id": concept_id,
                                        "concept_name": board_name,
                                    }
                                )
                            if not board_records:
                                # 全部代码均未解析：不替换，避免清空该板块既有 EM_ 行（R21）
                                logger.warning(
                                    "[AKShareConceptSync] Board %s: all %d code(s) unresolved, skip replacement.",
                                    board_name,
                                    board_unresolved,
                                )
                                return
                            records.extend(board_records)
                            if board_unresolved:
                                # 部分未解析：只 upsert，不删除该板块旧行，避免把「映射临时
                                # 缺失」的有效成分一并删除（R21 精神）。
                                logger.warning(
                                    "[AKShareConceptSync] Board %s: %d of %d code(s) unresolved, upsert-only (no replacement).",
                                    board_name,
                                    board_unresolved,
                                    len(board_records) + board_unresolved,
                                )
                            else:
                                replace_board_codes.append(board_code)
                            return
                        except asyncio.CancelledError:
                            raise
                        except EngineDisposedError:
                            raise
                        except Exception as e:
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
                                log_classified(
                                    logger,
                                    e,
                                    "general",
                                    "[AKShareConceptSync] Retry (%s): %s (board=%s, attempt=%d)",
                                    board_name,
                                    attempt + 1,
                                    exc_info=True,
                                )
                                await asyncio.sleep(delay)
                            else:
                                failed_boards.append(f"{board_name}: {safe_error(e)}")
                                log_classified(
                                    logger,
                                    e,
                                    "general",
                                    "[AKShareConceptSync] Failed board (%s): %s (board=%s, retries=%d)",
                                    board_name,
                                    _AKSHARE_MAX_RETRIES,
                                    exc_info=True,
                                )

            # Phase 2F + S7: 循环体按时间维度（每 2 秒）检查 _check_cancelled。
            # 旧实现每 200 条 board 检查一次，单 board 最坏 7s+，最坏 1000s 才响应取消，远超 2s 红线。
            # M7.5: 改用 time.monotonic() 替代 get_now()，避免 NTP 时钟回退导致
            # 取消检查永久失效（wall clock 可回退，monotonic clock 单调递增）。
            tasks: list = []
            last_cancel_check = time.monotonic()
            for _, row in df_boards.iterrows():
                now = time.monotonic()
                if now - last_cancel_check >= _AKSHARE_CANCEL_CHECK_INTERVAL:
                    last_cancel_check = now
                    if self._check_cancelled(result):
                        return result
                tasks.append(sync_one_board(str(row["板块名称"]), str(row["板块代码"])))
            await gather_return_exceptions_propagating_cancel(*tasks)

            if self._check_cancelled(result):
                return result

            if unresolved:
                # R21: 显式标记未知/非法代码的跳过（聚合为一条，避免 warnings 膨胀）。
                _unresolved_list = sorted(unresolved)
                result.warnings.append(
                    f"[AKShareConceptSync] skipped {len(_unresolved_list)} unknown/invalid stock code(s) "
                    f"not in stock_basic: {_unresolved_list[:5]}"
                )
                logger.warning(
                    "[AKShareConceptSync] %d unknown code(s) skipped: %s",
                    len(_unresolved_list),
                    _unresolved_list[:5],
                )
                result.skipped += len(_unresolved_list)

            # replace_board_codes 非空 ⟹ records 非空（见 sync_one_board 构造），
            # 故此处以 records 判定是否落库即可。
            if records:
                saved = await self.context.cache.stock_dao.overwrite_em_concepts(
                    records,
                    replace_board_codes=replace_board_codes,
                )
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
            severity = classify_severity(e, context="general")
            error_info = log_classified(
                logger,
                e,
                "general",
                "[AKShareConceptSync] Run | failed (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
            result.status = SyncStatus.FAILED.value
            result.errors.append(error_info["message_key"])

        return result


class LimitListSyncStrategy(ISyncStrategy):
    """Tushare limit_list (涨跌停) 同步策略——review08-D3 后停写股票名概念。

    review08-D3（P0）：原实现把「股票名」当作「概念名」写入
    ``LIMIT_{ts_code}`` 伪概念，并经无前缀过滤的 ``get_concepts`` 泄漏给所有
    概念消费方。本轮按产品决策「修正语义」**停写**：无论 fetch 成功 / 权限
    不足 / 空数据，均清空既有 ``LIMIT_%`` 存量（幂等清理历史污染）并记录
    warning。涨停原因聚合需新数据源（本轮范围外单独排期），数据源接入前
    不再写入任何 LIMIT_ 概念。CancelledError / EngineDisposedError 传播
    （R2/R5）；TushareAPIPermissionError 降级语义保留。
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

            # review08-D3 停写：任何路径都不再构造 LIMIT_ 股票名概念，统一清空存量。
            cleared = await stock_dao.clear_all_limit_concepts()

            try:
                df = await self.context.api.get_limit_list(trade_date=trade_date)
            except TushareAPIPermissionError as e:
                # 权限不足：清空后 warning（旧污染不残留，P0 修订）
                logger.warning(
                    "[LimitListSync] Permission denied for limit_list (积分不足), "
                    "LIMIT_ concepts cleared (review08-D3): %s",
                    e.api_name,
                )
                result.warnings.append(
                    f"Tushare limit_list permission denied ({e.api_name}); "
                    "LIMIT_ concepts stopped and cleared (review08-D3)",
                )
                result.skipped += 1
                return result

            if df is None or df.empty:
                logger.debug(
                    "[LimitListSync] Empty limit_list for trade_date=%s (LIMIT_ concepts stopped, cleared=%s)",
                    trade_date,
                    cleared,
                )
                result.warnings.append(
                    "Empty limit_list; LIMIT_ concepts stopped and cleared (review08-D3)",
                )
                return result

            logger.info(
                "[LimitListSync] LIMIT_ concept sync stopped (review08-D3); cleared %s stale record(s), trade_date=%s",
                cleared,
                trade_date,
            )
            result.warnings.append(
                "LIMIT_ concept sync stopped (review08-D3): 涨停原因聚合需新数据源，接入前不再写入",
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
            severity = classify_severity(e, context="general")
            error_info = log_classified(
                logger,
                e,
                "general",
                "[LimitListSync] Run | failed (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
            result.status = SyncStatus.FAILED.value
            result.errors.append(error_info["message_key"])
            # review08-D3 停写语义：清空与 fetch 同在外层 try，清空自身失败也会落到此处，
            # 此时存量未必已清除，故只声明"不再写入"而不声称清除状态（避免文案失实）。
            result.warnings.append("LIMIT_ concepts are no longer written (review08-D3); cleanup status uncertain")

        return result
