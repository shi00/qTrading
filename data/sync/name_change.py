"""股票名称变更历史同步（DATA-04 L3）。

通过 Tushare ``namechange`` 接口同步历次名称变更（含 ST/*ST 状态）到
``stock_name_history`` 表，供 as-of 还原历史时点名称/ST 状态，消除回测引用
当前名称（``stock_basic.name``）引入的前视偏差。

注意（tushare issue #1858）：``namechange`` 入参 start_date/end_date 被映射到
输出 ``ann_date`` 并触发隐式 NOT NULL 过滤，早期记录（ann_date 为 NULL）会静默
丢弃。故本策略**全量拉取**（不传日期区间），避免 ST 历史缺失导致生存偏差。

不加入 ``TABLE_TO_API_MAP``（非交易日快照数据，不参与交易日快照权限裁剪），
由 ``DataProcessor`` 在初始化时显式调用 ``self.strategies["name_change"].run()``。
"""

import asyncio
import logging

from data.constants import SYNC_RESULT_SKIPPED_PERMISSION
from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.sw_industry_dao import StockNameHistoryDao
from utils.error_classifier import classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now

from .base import ISyncStrategy, SyncResult, SyncStatus, safe_error

logger = logging.getLogger(__name__)


class NameChangeSyncStrategy(ISyncStrategy):
    """股票名称变更历史同步策略（DATA-04 L3，全量拉取）。

    数据生产方（API → DB），同 sw_industry 不适用 PolarsBaseStrategy 的
    ``required_quality_tier`` 模式（CLAUDE.md §3.2 限定该模式仅用于
    PolarsBaseStrategy）；质量门控由消费方声明。
    """

    def __init__(self, context):
        super().__init__(context)
        self.dao = StockNameHistoryDao(context.cache.engine)

    @log_async_operation(
        operation_name="NameChangeSyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def _run_impl(self, **kwargs) -> SyncResult:
        result = SyncResult()
        self._cancelled = False

        try:
            if self._check_cancelled(result):
                return result

            df = await self.context.api.get_namechange()
            if self._check_cancelled(result):
                return result

            if df is None or df.empty:
                logger.info("[NameChangeSync] No name change data returned")
                return result

            df = self._clean_null_values(df)
            # 主键 (ts_code, start_date)，全量 UPSERT 天然幂等
            count = await self.dao.save_stock_name_history(df)
            result.added += count or 0
            await self.context.cache.sync_dao.update_sync_status(
                "stock_name_history",
                get_now().date(),
                count or 0,
            )

            if self._cancelled and result.status not in ("failed", "cancelled"):
                result.status = "cancelled"

            logger.info(
                "[NameChangeSync] ✅ Complete. Added=%s, Errors=%s",
                result.added,
                len(result.errors),
            )
        except EngineDisposedError:
            logger.warning("[NameChangeSync] Engine disposed, stopping sync.")
            result.errors.append("engine disposed")
            return result
        except asyncio.CancelledError:
            raise
        except TushareAPIPermissionError:
            logger.warning("[NameChangeSync] ⛔ Permission denied for namechange")
            result.errors.append("NameChange: permission denied")
            await self._record_skipped_permission("stock_name_history")
            return result
        except Exception as e:
            severity = classify_severity(e, context="general")
            log_classified(
                logger,
                e,
                "general",
                "[NameChangeSync] failed (%s): %s",
                exc_info=True,
            )
            result.errors.append(f"NameChange: {safe_error(e)}")
            if severity == "system":
                raise
            result.status = SyncStatus.FAILED.value

        return result

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _record_skipped_permission(self, table_name: str) -> None:
        """记录 skipped_permission 状态到 sync_status 表（仿 sw_industry）。"""
        try:
            await self.context.cache.sync_dao.update_sync_status(
                table_name,
                get_now().date(),
                0,
                status="skipped_permission",
                last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
            )
        except EngineDisposedError:  # pragma: no cover - 防御性守卫，EngineDisposedError 从 API 层抛出概率极低
            raise
        except Exception as e:
            severity = classify_severity(e, context="general")
            log_classified(
                logger,
                e,
                "general",
                "[NameChangeSync] Failed to record skipped_permission status (%s): %s (table=%s)",
                table_name,
                exc_info=True,
            )
            if severity == "system":
                raise
