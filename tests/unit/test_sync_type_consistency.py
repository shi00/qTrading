"""
Sync Type Consistency Tests

Tests for ensuring type consistency in data synchronization:
- Date types: datetime.date vs string
- Schema completeness: required columns exist
- Cache method coverage: all critical tables supported
- Breakpoint resume: core tables configuration

These tests prevent regression of known type-related issues.

Run: pytest tests/test_sync_type_consistency.py -v
"""

import inspect

from data.constants import FINANCIAL_REPORT_SCHEMA_COLS
from data.persistence.daos.quote_dao import QuoteDao
from data.sync.historical import HistoricalSyncStrategy
from data.sync.financial import FinancialSyncStrategy
import pytest


pytestmark = pytest.mark.unit


class TestSyncedTableRegistryConsistency:
    """SYNCED_TABLES 与 quote_dao 各「表名登记表」必须同步。

    review09-24 dim05 MAJOR-01：新增同步表若漏登记，该表会对完整性检查、质量评分、
    断点续跑支撑静默失效（清单分散于多个模块，无一致性约束）。
    """

    def test_synced_tables_all_in_safe_whitelist(self):
        """SYNCED_TABLES 每张表都必须登记进 _SAFE_TABLE_NAMES（否则被白名单过滤而隐形）。"""
        from data.persistence.daos.quote_dao import _SAFE_TABLE_NAMES

        missing = sorted(set(HistoricalSyncStrategy.SYNCED_TABLES) - _SAFE_TABLE_NAMES)
        assert not missing, f"SYNCED_TABLES 中未登记进 _SAFE_TABLE_NAMES 的表: {missing}"

    def test_synced_tables_all_registered_in_quality_scoring(self):
        """SYNCED_TABLES 每张表都必须显式登记容忍系数 / 低频豁免 / 固定期望行数之一。"""
        from data.persistence.daos.quote_dao import (
            FIXED_EXPECTED_TABLES,
            LOW_FREQUENCY_TABLES,
            _build_table_tolerance_map,
        )

        from utils.config_models import SyncIntegrityConfig

        # 用真实配置模型派生 config（而非硬编码 stub），使容忍系数键名漂移能被本用例捕获。
        config = SyncIntegrityConfig().model_dump()
        registered = set(_build_table_tolerance_map(config)) | LOW_FREQUENCY_TABLES | set(FIXED_EXPECTED_TABLES)
        unregistered = sorted(set(HistoricalSyncStrategy.SYNCED_TABLES) - registered)
        assert not unregistered, (
            f"SYNCED_TABLES 中未在质量评分侧登记（容忍系数/低频豁免/固定期望行数）的表: {unregistered}"
        )

    def test_synced_tables_all_supported_by_cached_dates(self):
        """SYNCED_TABLES 每张表都必须登记进 _TABLE_DATE_COLUMN_MAP（否则断点续跑查询被拒并刷假告警）。"""
        from data.persistence.daos.quote_dao import _TABLE_DATE_COLUMN_MAP

        missing = sorted(set(HistoricalSyncStrategy.SYNCED_TABLES) - set(_TABLE_DATE_COLUMN_MAP))
        assert not missing, f"SYNCED_TABLES 中未登记进 _TABLE_DATE_COLUMN_MAP 的表: {missing}"

    def test_density_checked_tables_subset_of_synced(self):
        """_DENSITY_CHECKED_TABLES 必须 ⊆ SYNCED_TABLES（防止登记已下线/拼写错误的表名而成为死登记）。"""
        from data.persistence.daos.quote_dao import _DENSITY_CHECKED_TABLES

        extra = sorted(set(_DENSITY_CHECKED_TABLES) - set(HistoricalSyncStrategy.SYNCED_TABLES))
        assert not extra, f"_DENSITY_CHECKED_TABLES 中不在 SYNCED_TABLES 的表: {extra}"

    def test_dense_tables_covered_by_density_checked(self):
        """_DENSE_TABLES 必须 ⊆ _DENSITY_CHECKED_TABLES。

        漏登记会让 check_data_exists 对该 dense 表退化为「≥1 行」存在性判定，截断写入被判为
        完整、该日被永久跳过（review09-24 dim05 CRITICAL-01/MAJOR-02 的同类失效路径）。
        """
        from data.persistence.daos.quote_dao import _DENSITY_CHECKED_TABLES, _DENSE_TABLES

        uncovered = sorted(set(_DENSE_TABLES) - set(_DENSITY_CHECKED_TABLES))
        assert not uncovered, f"dense 表未纳入行数密度检查: {uncovered}"

    def test_dense_table_registries_consistent(self):
        """quote_dao 与 historical 各自维护的 _DENSE_TABLES 必须一致且 ⊆ SYNCED_TABLES。

        两处副本语义相同（每交易日必有数据的表）；漂移会让断点续传完成度判定与
        sync_empty_days 豁免口径不一致。
        """
        from data.persistence.daos.quote_dao import _DENSE_TABLES as quote_dense
        from data.sync.historical import _DENSE_TABLES as historical_dense

        assert quote_dense == historical_dense, (
            f"两处 _DENSE_TABLES 漂移: quote_dao={sorted(quote_dense)} historical={sorted(historical_dense)}"
        )
        extra = sorted(set(quote_dense) - set(HistoricalSyncStrategy.SYNCED_TABLES))
        assert not extra, f"_DENSE_TABLES 中不在 SYNCED_TABLES 的表: {extra}"

    def test_density_checked_tables_not_low_frequency_exempt(self):
        """行数密度检查表不得被 LOW_FREQUENCY_TABLES 豁免。

        否则其缺口会被静默移出加权评分；dense 表混入低频豁免是同一失效路径的变体。
        """
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES, _DENSITY_CHECKED_TABLES

        overlap = sorted(set(_DENSITY_CHECKED_TABLES) & LOW_FREQUENCY_TABLES)
        assert not overlap, f"行数密度检查表被误判为低频豁免: {overlap}"

    def test_quality_weights_keys_subset_of_synced(self):
        """quality_weights 的键必须 ⊆ SYNCED_TABLES。

        拼写错误或已下线的键不会报错而是被静默忽略（真实表权重回落默认 5），
        导致评分语义被无声改变。
        """
        from utils.config_models import SyncIntegrityConfig

        extra = sorted(set(SyncIntegrityConfig().quality_weights) - set(HistoricalSyncStrategy.SYNCED_TABLES))
        assert not extra, f"quality_weights 中不在 SYNCED_TABLES 的键: {extra}"


class TestSyncTypeConsistency:
    """Test cases for type consistency in data synchronization."""

    def test_financial_report_schema_cols_matches_orm(self):
        """FINANCIAL_REPORT_SCHEMA_COLS 必须与 ORM 模型列完全一致（排除时间戳）"""
        from data.persistence.models import FinancialReports, get_model_columns

        orm_cols = set(get_model_columns(FinancialReports))
        schema_cols = set(FINANCIAL_REPORT_SCHEMA_COLS)
        missing_in_schema = orm_cols - schema_cols
        extra_in_schema = schema_cols - orm_cols
        assert not missing_in_schema, f"FINANCIAL_REPORT_SCHEMA_COLS 缺少 ORM 字段: {missing_in_schema}"
        assert not extra_in_schema, f"FINANCIAL_REPORT_SCHEMA_COLS 多出 ORM 不存在的字段: {extra_in_schema}"

    def test_get_cached_dates_for_table_has_all_critical_tables(self):
        critical_tables = [
            "daily_quotes",
            "daily_indicators",
            "moneyflow_daily",
            "limit_list",
            "suspend_d",
            "margin_daily",
            "northbound_holding",
            "moneyflow_hsgt",
            "top_list",
            "block_trade",
            "index_daily",
            "index_dailybasic",
        ]

        synced = set(HistoricalSyncStrategy.SYNCED_TABLES)
        missing = [t for t in critical_tables if t not in synced]
        assert not missing, f"HistoricalSyncStrategy.SYNCED_TABLES missing tables: {missing}"

    def test_holder_sync_uses_get_now(self):
        import data.sync.holder as holder_mod

        assert hasattr(holder_mod, "get_now"), "HolderSyncStrategy module should import get_now"

    def test_historical_sync_uses_get_now_instead_of_date_today(self):
        import data.sync.historical as hist_mod

        assert hasattr(hist_mod, "get_now"), "HistoricalSyncStrategy module should import get_now"

    def test_historical_sync_moneyflow_accepts_datetime_date(self):
        sig = inspect.signature(HistoricalSyncStrategy.sync_moneyflow)
        trade_date_param = sig.parameters.get("trade_date")
        assert trade_date_param is not None, "sync_moneyflow should have trade_date parameter"
        annotation = trade_date_param.annotation
        assert annotation != inspect.Parameter.empty, "sync_moneyflow trade_date should have type annotation"
        ann_str = str(annotation)
        assert "date" in ann_str, f"sync_moneyflow trade_date annotation should reference date type, got: {ann_str}"

    def test_historical_sync_northbound_accepts_datetime_date(self):
        assert hasattr(HistoricalSyncStrategy, "sync_northbound"), (
            "HistoricalSyncStrategy should have sync_northbound method"
        )

    def test_historical_sync_uses_core_tables_for_resume(self):
        assert hasattr(HistoricalSyncStrategy, "CORE_RESUME_TABLES"), (
            "HistoricalSyncStrategy should define CORE_RESUME_TABLES"
        )
        core_tables = HistoricalSyncStrategy.CORE_RESUME_TABLES
        assert "daily_quotes" in core_tables, "CORE_RESUME_TABLES should include daily_quotes"
        assert "daily_indicators" in core_tables, "CORE_RESUME_TABLES should include daily_indicators"
        assert set(core_tables) == set(HistoricalSyncStrategy.SYNCED_TABLES), (
            "CORE_RESUME_TABLES should equal SYNCED_TABLES for semantic consistency"
        )

    def test_get_cached_dates_returns_datetime_date(self):
        sig = inspect.signature(QuoteDao.get_cached_dates_for_table)
        return_annotation = sig.return_annotation
        assert return_annotation != inspect.Signature.empty, (
            "get_cached_dates_for_table should have return type annotation"
        )

    def test_get_cached_trade_dates_returns_datetime_date(self):
        sig = inspect.signature(QuoteDao.get_cached_trade_dates)
        return_annotation = sig.return_annotation
        assert return_annotation != inspect.Signature.empty, "get_cached_trade_dates should have return type annotation"

    def test_sync_daily_market_snapshot_accepts_datetime_date(self):
        sig = inspect.signature(HistoricalSyncStrategy.sync_daily_market_snapshot)
        trade_date_param = sig.parameters.get("trade_date")
        assert trade_date_param is not None, "sync_daily_market_snapshot should have trade_date parameter"
        annotation = trade_date_param.annotation
        ann_str = str(annotation)
        assert "date" in ann_str, (
            f"sync_daily_market_snapshot trade_date should reference datetime.date, got: {ann_str}"
        )

    def test_sync_one_day_uses_datetime_date(self):
        assert hasattr(HistoricalSyncStrategy, "_run_historical_sync"), (
            "HistoricalSyncStrategy should have _run_historical_sync method"
        )
        sig = inspect.signature(HistoricalSyncStrategy._run_historical_sync)
        assert len(sig.parameters) >= 3, "_run_historical_sync should accept days, progress_callback, result"

    def test_financial_sync_uses_trade_calendar_not_deprecated_api(self):
        import data.sync.financial as fin_mod

        assert hasattr(fin_mod, "FinancialSyncStrategy")
        assert hasattr(FinancialSyncStrategy, "_get_effective_trade_date"), (
            "FinancialSyncStrategy should have _get_effective_trade_date method"
        )
        sig = inspect.signature(FinancialSyncStrategy._get_effective_trade_date)
        assert sig.return_annotation != inspect.Signature.empty, (
            "FinancialSyncStrategy._get_effective_trade_date should have return type annotation"
        )
        ann_str = str(sig.return_annotation)
        assert "date" in ann_str, (
            f"FinancialSyncStrategy._get_effective_trade_date should return datetime.date, got: {ann_str}"
        )
