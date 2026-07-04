"""Phase 3F-1 §4.3.2：申万行业分类同步策略（全局快照，月度更新）。

通过 Tushare `index_classify` + `index_member_all` 接口同步申万行业分类数据：
- L1/L2/L3 三级行业分类落入 `sw_industry_classify` 表
- 各 index_code 的成分股映射落入 `sw_industry_member` 表

不加入 `TABLE_TO_API_MAP`（不参与交易日快照权限裁剪），由 `DataProcessor`
在初始化或月度刷新时显式调用 `self.strategies["sw_industry"].run()`。
"""

import asyncio
import logging
import typing

import pandas as pd

from data.constants import SYNC_RESULT_SKIPPED_PERMISSION
from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.sw_industry_dao import SwIndustryClassifyDao, SwIndustryMemberDao
from data.persistence.quality_gate import QualityTier
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now

from .base import ISyncStrategy, SyncResult

logger = logging.getLogger(__name__)

# 申万行业三级分类
_SW_LEVELS: tuple[str, ...] = ("L1", "L2", "L3")

# Phase 3F-1 §4.3.2：循环体每 200 个 index_code 检查一次取消信号
_CANCEL_CHECK_INTERVAL = 200


class SwIndustrySyncStrategy(ISyncStrategy):
    """申万行业分类同步策略（全局快照，月度更新）。

    Phase 3F-1 §4.3.2：申万行业是基础元数据，全局快照（非交易日快照），
    月度更新。声明 ``required_quality_tier = QualityTier.BRONZE``（仅需
    stock_basic 可用即可同步行业分类，不依赖日线/财务数据连续性）。
    """

    # Phase 3F-1 §4.3.2：声明数据质量要求（遵循 PolarsBaseStrategy 类属性模式）
    required_quality_tier = QualityTier.BRONZE

    def __init__(self, context: typing.Any):
        super().__init__(context)
        engine = context.cache.engine
        self.classify_dao = SwIndustryClassifyDao(engine)
        self.member_dao = SwIndustryMemberDao(engine)

    @log_async_operation(
        operation_name="SwIndustrySyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def _run_impl(self, **kwargs: typing.Any) -> SyncResult:
        result = SyncResult()
        self._cancelled = False

        try:
            # Step 1: 同步 L1/L2/L3 三级申万行业分类
            classify_df = await self._sync_classify(result)
            if self._check_cancelled(result):
                return result

            # Step 2: 按 index_code 循环同步成分股映射
            await self._sync_members(result, classify_df)

            if self._cancelled and result.status not in ("failed", "cancelled"):
                result.status = "cancelled"

            if result.status == "cancelled":
                logger.info(
                    "[SwIndustrySync] Run | ⚠️ Cancelled. Added=%s, Errors=%s",
                    result.added,
                    len(result.errors),
                )
            else:
                logger.info(
                    "[SwIndustrySync] Run | ✅ Complete. Added=%s, Errors=%s",
                    result.added,
                    len(result.errors),
                )
        except asyncio.CancelledError:
            result.status = "cancelled"
            raise
        except EngineDisposedError:
            logger.warning("[SwIndustrySync] Run | Engine disposed, stopping sync.")
            result.status = "failed"
            result.errors.append("Engine disposed during sync")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[SwIndustrySync] SYSTEM-LEVEL failure: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[SwIndustrySync] Recoverable error (%s): %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[SwIndustrySync] Operational error: %s", e, exc_info=True)
            result.status = "failed"
            result.errors.append(error_info["message_key"])

        return result

    async def _sync_classify(self, result: SyncResult) -> pd.DataFrame:
        """同步 L1/L2/L3 三级行业分类，返回拼接后的 DataFrame（供 _sync_members 复用 index_code 列表）。"""
        try:
            frames: list[pd.DataFrame] = []
            for level in _SW_LEVELS:
                if self._check_cancelled(result):
                    return pd.DataFrame()
                df = await self.context.api.get_index_classify(level=level, src="SW2021")
                if df is not None and not df.empty:
                    frames.append(df)

            if not frames:
                logger.warning("[SwIndustrySync] Classify | All levels returned empty")
                return pd.DataFrame()

            combined = pd.concat(frames, ignore_index=True)
            count = await self.classify_dao.save_sw_industry_classify(combined)
            result.added += count if count else 0
            await self.context.cache.update_sync_status(
                "sw_industry_classify",
                get_now().date(),
                count or 0,
            )
            logger.debug(
                "[SwIndustrySync] Classify | Saved %s classification records",
                count,
            )
            return combined
        except EngineDisposedError:
            raise
        except TushareAPIPermissionError:
            logger.warning("[SwIndustrySync] Classify | ⛔ Permission denied for index_classify")
            result.errors.append("SwIndustry Classify: permission denied")
            await self._record_skipped_permission("sw_industry_classify")
            return pd.DataFrame()
        except Exception as e:
            logger.warning("[SwIndustrySync] Classify | ⚠️ Error: %s", e, exc_info=True)
            result.errors.append(f"SwIndustry Classify: {e}")
            return pd.DataFrame()

    async def _sync_members(self, result: SyncResult, classify_df: pd.DataFrame) -> None:
        """按 index_code 循环同步成分股映射，每 200 个检查取消信号。"""
        if classify_df is None or classify_df.empty or "index_code" not in classify_df.columns:
            logger.debug("[SwIndustrySync] Members | No classify data, skipping member sync")
            return

        try:
            index_codes = classify_df["index_code"].astype(str).unique().tolist()
            total = len(index_codes)
            logger.info(
                "[SwIndustrySync] Members | Starting per-index sync: %s indices",
                total,
            )

            all_dfs: list[pd.DataFrame] = []
            total_rows = 0
            errors = 0

            for i, index_code in enumerate(index_codes):
                # Phase 3F-1 §4.3.2：循环体每 200 条检查 _check_cancelled
                if i > 0 and i % _CANCEL_CHECK_INTERVAL == 0 and self._check_cancelled(result):
                    logger.debug(
                        "[SwIndustrySync] Members | Cancelled at i=%s/%s",
                        i,
                        total,
                    )
                    return

                try:
                    df = await self.context.api.get_index_member_all(index_code=index_code)
                    if df is not None and not df.empty:
                        all_dfs.append(df)
                        total_rows += len(df)
                except EngineDisposedError:
                    raise
                except TushareAPIPermissionError:
                    logger.warning(
                        "[SwIndustrySync] Members | ⛔ Permission denied for index_member_all",
                    )
                    result.errors.append("SwIndustry Members: permission denied")
                    await self._record_skipped_permission("sw_industry_member")
                    return
                except Exception as e:
                    errors += 1
                    logger.debug(
                        "[SwIndustrySync] Members | Skip index_code=%s: %s",
                        index_code,
                        e,
                        exc_info=True,
                    )

            if not all_dfs:
                logger.warning(
                    "[SwIndustrySync] Members | No data fetched (errors=%s/%s)",
                    errors,
                    total,
                )
                return

            combined = pd.concat(all_dfs, ignore_index=True)
            # 去重：同一 ts_code+index_code 可能被多个级别重复返回
            if {"ts_code", "index_code"}.issubset(combined.columns):
                combined = combined.drop_duplicates(subset=["ts_code", "index_code"])
            count = await self.member_dao.save_sw_industry_member(combined)
            result.added += count if count else 0
            await self.context.cache.update_sync_status(
                "sw_industry_member",
                get_now().date(),
                count or 0,
            )
            logger.info(
                "[SwIndustrySync] Members | Saved %s member records (errors=%s/%s)",
                count,
                errors,
                total,
            )
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[SwIndustrySync] Members | ⚠️ Error: %s", e, exc_info=True)
            result.errors.append(f"SwIndustry Members: {e}")

    async def _record_skipped_permission(self, table_name: str) -> None:
        """记录 skipped_permission 状态到 sync_status 表，便于 UI 展示降级提示。"""
        try:
            await self.context.cache.update_sync_status(
                table_name,
                get_now().date(),
                0,
                status="skipped_permission",
                last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
            )
        except Exception as e:
            logger.debug(
                "[SwIndustrySync] Failed to record skipped_permission status for %s: %s",
                table_name,
                e,
                exc_info=True,
            )
