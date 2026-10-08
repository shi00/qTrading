"""回测服务入口

提供统一的回测编排，集成引擎运行与持久化。
位于 services 层，负责策略查找、引擎实例化和结果持久化。
UI / 任务系统通过此服务调用回测，不直接实例化引擎。

依赖注入契约：
- `engine_factory` 与 `strategy_lookup` 由调用方（如 ui 层 viewmodel）注入，
  避免 services 层运行时依赖 strategies 层（CLAUDE.md §3.1 R1 红线）。
- `get_result` / `list_results` / `delete_result` / `_persist_result` 不需要工厂注入。
- `run_backtest` 需要 `engine_factory` + `strategy_lookup`。
- `run_backtest_with_strategy` 仅需要 `engine_factory`。
- 若调用方未注入所需工厂，对应方法会 `raise RuntimeError`（fail-late）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from core.i18n import Message
from data.cache.cache_manager import CacheManager
from data.persistence.daos.base_dao import EngineDisposedError

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestConfig, BacktestResult
    from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# BT-03 MINOR-03: 数据指纹参与比对的受控字段集（与 QuoteDao.get_data_fingerprint 输出对齐）。
_FINGERPRINT_FIELDS: tuple[str, ...] = (
    "daily_quotes_row_count",
    "daily_quotes_max_updated_at",
    "financial_reports_max_ann_date",
)


def diff_data_fingerprints(prev: dict | None, curr: dict | None) -> list[str]:
    """比对两次运行的数据指纹，返回发生变化的字段名列表（BT-03 MINOR-03）。

    仅在前后均为有效指纹时才构成可比基线；任一为 ``None``（无基线 / 指纹不可得）
    时返回 ``[]``，调用方据此**不写入比较结论**（R21：未知不伪装为「数据未变」）。
    仅比对受控的 ``_FINGERPRINT_FIELDS`` 字段，字段缺失按 ``None`` 处理。
    """
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return []
    return [field for field in _FINGERPRINT_FIELDS if prev.get(field) != curr.get(field)]


class BacktestService:
    """
    回测服务入口。

    功能：
    1. 按策略 key 查找并实例化策略（通过 `strategy_lookup` 注入）
    2. 运行回测引擎（通过 `engine_factory` 注入）
    3. 持久化回测结果
    """

    def __init__(
        self,
        cache: CacheManager,
        data_processor: Any = None,
        engine_factory: Callable[[CacheManager, BacktestConfig, Any | None], Any] | None = None,
        strategy_lookup: Callable[[str], type | None] | None = None,
    ):
        if cache is None:
            raise ValueError("BacktestService requires a CacheManager instance (dependency injection).")
        self.cache = cache
        self.data_processor = data_processor
        self._engine_factory = engine_factory
        self._strategy_lookup = strategy_lookup

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def run_backtest(
        self,
        strategy_key: str,
        config: BacktestConfig,
        params: dict | None = None,
        progress_callback: Callable[[float, Message], None] | None = None,
        persist: bool = True,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        strategy = self._get_strategy(strategy_key)
        if strategy is None:
            raise ValueError(f"Strategy not found: {strategy_key}")

        engine = self._create_engine(config)

        result = await engine.run(
            strategy=strategy,
            params=params,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

        if persist:
            result = await self._persist_result(result)

        return result

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def run_backtest_with_strategy(
        self,
        strategy: BaseStrategy,
        config: BacktestConfig,
        params: dict | None = None,
        progress_callback: Callable[[float, Message], None] | None = None,
        persist: bool = True,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        engine = self._create_engine(config)

        result = await engine.run(
            strategy=strategy,
            params=params,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

        if persist:
            result = await self._persist_result(result)

        return result

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _persist_result(self, result: BacktestResult) -> BacktestResult:
        try:
            result_dict = result.to_persist_dict()
            result_dict["app_version"] = self._get_app_version()

            # BT-03 MINOR-03: 注入数据版本指纹与重跑比对结论（best-effort，不阻断持久化）。
            await self._augment_data_fingerprint(result_dict, result)

            await self.cache.backtest_dao.save_result(result_dict)
            logger.info(
                "[BacktestService] Saved backtest result: run_id=%s, strategy=%s",
                result.run_id,
                result.strategy_name,
            )
            return result
        except Exception as e:
            sanitized = DataSanitizer.sanitize_error(e)
            log_classified(
                logger,
                e,
                "general",
                "[BacktestService] Failed to persist backtest result (%s): %s",
                exc_info=True,
            )
            new_warnings = [*list(result.data_warnings), f"persist_failed: {sanitized}"]
            return result.with_warnings(new_warnings)

    async def _augment_data_fingerprint(self, result_dict: dict, result: BacktestResult) -> None:
        """为 ``quality_json`` 注入数据版本指纹与重跑比对结论（BT-03 MINOR-03，best-effort）。

        指纹为辅助诊断信息，其任何失败都不得阻断结果持久化：本方法吞掉非取消 / 非引擎
        释放异常并记录日志，保证 ``_persist_result`` 的核心职责（保存结果）不受影响。

        写入约定（R21：不以「缺失」冒充「无变化」）：
        - ``data_fingerprint``：仅当指纹查询成功时写入；查询失败则不写键（未知）；
        - ``data_version_changes``：仅当存在可比基线时写入——``[]`` 表示「已比对且未变化」，
          非空列表表示「已比对且这些字段发生变化」；无基线时**不写键**（不做未变化结论）。
        """
        quality = result_dict.get("quality_json")
        if not isinstance(quality, dict):
            return
        try:
            config = result.config
            fingerprint = await self.cache.quote_dao.get_data_fingerprint(config.start_date, config.end_date)
            if fingerprint is None:
                # 指纹不可得：不写入键（未知），不得伪装为「数据未变」（R21）。
                return
            quality["data_fingerprint"] = fingerprint

            baseline = await self.cache.backtest_dao.get_latest_data_fingerprint_for_range(
                result.strategy_name,
                config.start_date,
                config.end_date,
                exclude_run_id=result.run_id,
            )
            if baseline is None:
                # 无上次运行 / 上次运行无指纹（存量旧记录）→ 不可比对，不写比较结论。
                return
            changes = diff_data_fingerprints(baseline, fingerprint)
            quality["data_version_changes"] = changes  # [] = 已比对且未变化
            if changes:
                logger.warning(
                    "[BacktestService] Data version changed for strategy=%s range=%s..%s (run_id=%s): fields=%s",
                    result.strategy_name,
                    config.start_date,
                    config.end_date,
                    result.run_id,
                    changes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[BacktestService] data fingerprint augmentation failed (run_id=%s): %s",
                result.run_id,
                DataSanitizer.sanitize_error(e),
            )

    def _create_engine(self, config: BacktestConfig) -> Any:
        """通过注入的 engine_factory 创建引擎实例。

        Raises:
            RuntimeError: 若未注入 engine_factory。
        """
        if self._engine_factory is None:
            raise RuntimeError("BacktestService requires 'engine_factory' to be injected to run backtest.")
        return self._engine_factory(self.cache, config, self.data_processor)

    def _get_strategy(self, strategy_key: str) -> BaseStrategy | None:
        """按 key 查找策略类并实例化。

        - 实例化职责保留在 services 层（编排逻辑）。
        - 仅 "查 registry" 委托给注入的 strategy_lookup。

        Raises:
            RuntimeError: 若未注入 strategy_lookup。
        """
        if self._strategy_lookup is None:
            raise RuntimeError(
                "BacktestService requires 'strategy_lookup' to be injected to resolve strategy for run_backtest."
            )
        strategy_class = self._strategy_lookup(strategy_key)
        if strategy_class is None:
            return None

        try:
            instance: BaseStrategy = strategy_class()  # type: ignore[call-arg]  # 子类 __init__ 签名各异
            instance.key = strategy_key  # type: ignore[attr-defined]  # 动态打标，pre-existing 行为
            return instance
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[BacktestService] Failed to instantiate strategy (%s): %s (strategy=%s)",
                strategy_key,
                exc_info=True,
            )
            return None

    @staticmethod
    def _get_app_version() -> str:
        try:
            from importlib.metadata import version

            return version("astock-screener")
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[BacktestService] Failed to get app version (%s): %s",
                exc_info=True,
            )
            return "dev"

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_result(self, run_id: str) -> dict | None:
        return await self.cache.backtest_dao.get_result(run_id)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def list_results(
        self,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.cache.backtest_dao.list_results(
            strategy_name=strategy_name,
            limit=limit,
        )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def delete_result(self, run_id: str) -> bool:
        return await self.cache.backtest_dao.delete_result(run_id)
