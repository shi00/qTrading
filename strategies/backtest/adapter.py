"""策略适配层

将现有 BaseStrategy.filter(context) 输出规范化为回测信号。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
import polars as pl

from utils.log_decorators import PerfThreshold, log_async_operation

if TYPE_CHECKING:
    from strategies.base_strategy import BaseStrategy
    from strategies.utils import StrategyContext

logger = logging.getLogger(__name__)


class BacktestRequiredDataError(RuntimeError):
    """回测必需数据取数失败/缺失（D12-001）。

    ``fail_fast=False`` 时由 engine 记入 ``BacktestResult.failed_signal_dates``（既有失败日
    通道），``fail_fast=True`` 时直接中止——不得折叠成「正常无匹配」空信号（R21）。
    """


class BacktestStrategyAdapter:
    """
    将现有 BaseStrategy.filter(context) 输出规范化为回测信号。

    关键设计：
    1. 调用 strategy.check_dependencies(context) 检查依赖
    2. 处理 unready/degraded 状态
    3. 规范化输出为标准信号 schema
    """

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def generate_signal(
        self,
        strategy: BaseStrategy,
        context: StrategyContext,
        signal_date: date,
        execution_date: date,
    ) -> pl.DataFrame:
        """
        生成回测信号。

        步骤：
        1. 检查策略依赖：dep_result = strategy.check_dependencies(context)
        2. 必需数据取数失败（diagnostics.required_failures / 必需辅助表 failed）→ 抛
           BacktestRequiredDataError（D12-001，进入 engine 失败日通道）
        3. 如果 unready：记录警告，返回空 DataFrame
        4. 如果 degraded：记录信息，继续执行（合法稀疏空日不升级）
        5. 调用 strategy.filter(context)
        6. 空结果且 _empty_reason 指示必需数据缺失 → 抛 BacktestRequiredDataError
        7. 规范化输出为标准信号 schema

        Args:
            strategy: 策略实例
            context: 策略上下文
            signal_date: 信号日期
            execution_date: 执行日期（T+1）

        Returns:
            标准信号 DataFrame，包含以下列：
            - signal_date: date
            - execution_date: date
            - ts_code: str
            - score: float | None
            - rank: int
            - reason: str | None
        """
        dep_result = strategy.check_dependencies(context)

        # D12-001 / R21：必需数据取数失败必须显式报错，不得退化成空信号。
        # 合法稀疏空日（如当日龙虎榜无数据）不进入 diagnostics["required_failures"]，
        # 仍按 degraded（仅日志）继续执行，不升级为质量告警。
        required_failure = self._collect_required_data_failure(strategy, context)
        if required_failure is not None:
            message = (
                f"[BacktestAdapter] Required data unavailable for {strategy.name} on {signal_date}: {required_failure}"
            )
            logger.error(message)
            raise BacktestRequiredDataError(message)

        if dep_result["status"] == "unready":
            logger.warning(
                "[BacktestAdapter] Strategy %s dependencies unready: missing_keys=%s, missing_tables=%s",
                strategy.name,
                dep_result["missing_keys"],
                dep_result["missing_tables"],
            )
            return pl.DataFrame()

        if dep_result["status"] == "degraded":
            logger.info(
                "[BacktestAdapter] Strategy %s running in degraded mode: empty_keys=%s",
                strategy.name,
                dep_result["empty_keys"],
            )

        context["_dependency_status"] = dep_result

        result_df = await strategy.filter(context)

        # D12-001：策略因「必需数据缺失」而空返回（如财报低覆盖守卫）时，
        # `_empty_reason` 与 warnings 已给出原因；该空结果不是「无匹配」，
        # 必须走失败日通道，避免 hold/清仓被当成正常空信号。
        missing_reason = self._empty_result_missing_reason(context, result_df)
        if missing_reason is not None:
            warning_keys = [getattr(w, "key", str(w)) for w in (context.get("warnings") or [])]
            message = (
                f"[BacktestAdapter] Strategy {strategy.name} returned empty on {signal_date} "
                f"because required data is missing: reason={missing_reason}, warnings={warning_keys}"
            )
            logger.error(message)
            raise BacktestRequiredDataError(message)

        return self._normalize_signal_output(
            result_df,
            signal_date,
            execution_date,
        )

    @staticmethod
    def _collect_required_data_failure(strategy: BaseStrategy, context: StrategyContext) -> str | None:
        """D12-001：从 provider 的逐表失败身份中筛出「本策略必需」的失败。

        仅当失败表属于策略声明依赖（``required_context_keys`` / ``required_tables``，
        或基本面策略的 ``requires_fundamental_coverage``）时才升级为显式错误；
        策略未声明的可选表失败只由 provider 记为降级告警，不阻断当日信号。
        """
        diagnostics = context.get("_diagnostics") or {}
        table_map = getattr(strategy, "CONTEXT_KEY_TABLE_MAP", {}) or {}
        required_keys = tuple(getattr(strategy, "required_context_keys", ()) or ())
        required_tables = tuple(getattr(strategy, "required_tables", ()) or ())
        requires_fundamental = bool(getattr(strategy, "requires_fundamental_coverage", False))

        def _is_required(key: str) -> bool:
            if key in required_keys:
                return True
            table = table_map.get(key)
            if table is not None and table in required_tables:
                return True
            return key == "fundamental_screening_data" and requires_fundamental

        def _describe(key: str, error: str) -> str:
            return f"{key} ({table_map.get(key) or 'unknown table'}): {error}"

        for key, error in (diagnostics.get("required_failures") or {}).items():
            if _is_required(key):
                return _describe(key, str(error))

        for key, status in (diagnostics.get("table_status") or {}).items():
            if isinstance(status, dict) and status.get("failed") and _is_required(key):
                return _describe(key, str(status.get("error") or "fetch failed"))
        return None

    @staticmethod
    def _empty_result_missing_reason(context: StrategyContext, result_df) -> str | None:
        """空结果是否由必需数据缺失导致（而非合法「无匹配」）。

        仅认 ``_empty_reason == "fundamental_data_missing"``（PolarsBase 覆盖率守卫与
        空表守卫的既有原因通道）；``no_match`` 等正常空结果保持原样。
        """
        if result_df is None:
            is_empty = True
        elif isinstance(result_df, pl.DataFrame):
            is_empty = result_df.is_empty()
        else:
            is_empty = bool(getattr(result_df, "empty", False))
        if not is_empty:
            return None
        reason = context.get("_empty_reason")
        return reason if reason == "fundamental_data_missing" else None

    def _normalize_signal_output(
        self,
        result_df,
        signal_date: date,
        execution_date: date,
    ) -> pl.DataFrame:
        """
        规范化策略输出为标准信号 schema。

        标准信号输出 schema：
        - signal_date: date
        - execution_date: date
        - ts_code: str
        - score: float | None
        - rank: int
        - reason: str | None
        """
        if result_df is None or (hasattr(result_df, "empty") and result_df.empty):
            return pl.DataFrame()

        if isinstance(result_df, pd.DataFrame):
            if "ts_code" not in result_df.columns:
                logger.warning("[BacktestAdapter] Strategy result missing 'ts_code' column, returning empty DataFrame")
                return pl.DataFrame()

            df = pl.from_pandas(result_df)
        elif isinstance(result_df, pl.DataFrame):
            df = result_df
            if "ts_code" not in df.columns:
                logger.warning("[BacktestAdapter] Strategy result missing 'ts_code' column, returning empty DataFrame")
                return pl.DataFrame()
        else:
            logger.warning(
                "[BacktestAdapter] Strategy returned unexpected type: %s, returning empty DataFrame",
                type(result_df),
            )
            return pl.DataFrame()

        num_rows = len(df)

        # M10-001/M10-002 修复：统一 signal_rank 语义为 "rank 大 = 信号强"。
        # 若存在 score 列，按 score 降序排序后赋 rank，消除"策略返回 DataFrame 已排序"的隐式假设。
        score_col_name = None
        for score_name in ["score", "signal_score", "rank_score", "ai_score"]:
            if score_name in df.columns:
                score_col_name = score_name
                break

        if score_col_name is not None:
            # D1-C2：空分数 = 该股未产出有效信号强度（AI 失败/未评分），不得参与排名。
            # Polars sort 默认 nulls_last=False 会把 null 顶到最强位，必须显式 nulls_last=True；
            # 同时直接剔除空分行，避免"最弱信号"仍被建仓。
            df = df.filter(pl.col(score_col_name).is_not_null()).sort(score_col_name, descending=True, nulls_last=True)
            num_rows = len(df)
            if num_rows == 0:
                return pl.DataFrame()

        ts_codes = df["ts_code"].to_list()
        score_col = df[score_col_name].to_list() if score_col_name else None

        # BT-01: has_real_score 指示信号是否来自独立打分。存在打分列时为 True；
        # 否则 score 为 None，signal_rank 仅表达策略自身的排序偏好（如按股息率/ROE/市值排序），
        # 不代表信号强度 —— 此时 IC 应被解读为「排序 IC」而非独立打分的信息系数。
        has_real_score = score_col_name is not None

        reason_col = None
        for reason_name in ["reason", "signal_reason", "note"]:
            if reason_name in df.columns:
                reason_col = df[reason_name].to_list()
                break

        # rank 语义：rank 大 = 信号强（第一行 rank=N 最大，按 score 降序后即最强信号）
        ranks = list(range(num_rows, 0, -1))

        signal_data = {
            "signal_date": [signal_date] * num_rows,
            "execution_date": [execution_date] * num_rows,
            "ts_code": ts_codes,
            "score": score_col if score_col else [None] * num_rows,
            "signal_rank": ranks,
            "has_real_score": [has_real_score] * num_rows,
            "reason": reason_col if reason_col else [None] * num_rows,
        }

        # D1-M2：透传信号日（T）快照的 total_mv，供市值加权 sizer 使用，
        # 避免其回退到执行日(T+1)收盘后才确定的市值（前视）。缺失时不参与。
        if "total_mv" in df.columns:
            signal_data["total_mv"] = df["total_mv"].to_list()

        return pl.DataFrame(signal_data)
