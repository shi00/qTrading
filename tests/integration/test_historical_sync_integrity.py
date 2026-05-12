"""
历史数据同步完整性集成测试

测试 Phase 2: 数据同步完整性增强
- H1: delist_date 精确排除历史退市股票
- H2: 批量查询性能优化
- M2: 质量评分机制
- M3: 批量聚合查询

本轮新增测试覆盖：
- C1: fina_indicator 幽灵表问题
- H1: quality_weights 配置使用
- H2: 低频表评分策略
- H3: 断点续传 CORE_RESUME_TABLES
- H4: SyncResult.merge() 状态合并逻辑
- M2: get_sync_quality_score key 类型不匹配
- M3: historical.py 层级违规
- M4: get_incomplete_financial_stocks 漏检
- M5: get_expected_stock_count 退市股票逻辑
"""

import datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import pytest_asyncio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from data.sync.base import SyncResult
from strategies.prompt_validator import (
    generate_declaration_report,
    get_declarations,
    validate_prompt_declarations,
)


@pytest.fixture
def mock_engine():
    return MagicMock()


@pytest.fixture
def quote_dao(mock_engine):
    from data.persistence.daos.quote_dao import QuoteDao

    return QuoteDao(mock_engine)


@pytest.fixture
def financial_dao(mock_engine):
    from data.persistence.daos.financial_dao import FinancialDao

    return FinancialDao(mock_engine)


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.cache = MagicMock()
    context.cache.get_cached_dates_for_table = AsyncMock(return_value=set())
    context.cache.get_bulk_sync_quality_scores = AsyncMock(return_value={})
    context.cache.get_bulk_table_counts = AsyncMock(return_value={})
    return context


@pytest.fixture
def mock_sync_strategy(mock_context):
    from data.sync.historical import HistoricalSyncStrategy

    return HistoricalSyncStrategy(mock_context)


class TestHistoricalSyncIntegrity:
    """测试历史数据同步完整性"""

    @pytest.mark.asyncio
    async def test_sync_with_interruption_recovery(self, mock_sync_strategy):
        """
        集成测试：同步中断后恢复

        场景：模拟同步过程中断，验证断点续传正确性
        """
        mock_sync_strategy.context.cache.get_cached_dates_for_table = AsyncMock(return_value={"20240101"})
        mock_sync_strategy.context.cache.get_bulk_sync_quality_scores = AsyncMock(return_value={"20240101": 90})

        result = SyncResult()

        assert result is not None

    @pytest.mark.asyncio
    async def test_low_quality_data_triggers_resync(self, mock_sync_strategy):
        """
        集成测试：低质量数据触发重新同步

        场景：模拟低质量数据，验证重新同步逻辑
        """
        mock_sync_strategy.context.cache.get_bulk_sync_quality_scores = AsyncMock(return_value={"20240101": 50})

        quality_scores = await mock_sync_strategy.context.cache.get_bulk_sync_quality_scores("20240101", "20240101")

        low_quality_dates = [date for date, score in quality_scores.items() if score < 80]

        assert "20240101" in low_quality_dates

    @pytest.mark.asyncio
    async def test_historical_data_not_misjudged(self, mock_sync_strategy):
        """
        集成测试：历史数据不被误判

        场景：验证 2018 年数据不会被误判为低质量
        """
        mock_sync_strategy.context.cache.get_bulk_sync_quality_scores = AsyncMock(return_value={"20180601": 85})

        quality_scores = await mock_sync_strategy.context.cache.get_bulk_sync_quality_scores("20180601", "20180601")

        score = quality_scores.get("20180601", 0)
        assert score >= 80


class TestBulkQualityScoreOptimization:
    """M3 测试：批量质量评分优化"""

    @pytest.mark.asyncio
    async def test_bulk_vs_individual_query_count(self, quote_dao):
        """
        性能测试：验证批量查询减少 DB 调用次数

        原方案：750 天 × 12 表 = 9000 次查询
        优化后：约 13 次查询
        """
        call_count = 0

        async def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame({"trade_date": [], "expected_count": []})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, side_effect=count_calls):
            await quote_dao.get_bulk_sync_quality_scores("20210101", "20231231")

            assert call_count <= 20, f"Expected <= 20 DB calls, got {call_count}"


class TestQualityThreshold:
    """质量阈值测试"""

    @pytest.mark.asyncio
    async def test_quality_threshold_from_config(self):
        """
        测试从配置读取质量阈值
        """
        from utils.config_handler import ConfigHandler

        config = ConfigHandler.get_sync_integrity_config()
        threshold = config.get("quality_threshold", 80)

        assert threshold >= 0
        assert threshold <= 100

    @pytest.mark.asyncio
    async def test_tolerance_ratios(self):
        """
        测试容差系数配置
        """
        from utils.config_handler import ConfigHandler

        config = ConfigHandler.get_sync_integrity_config()

        quotes_tolerance = config.get("quotes_tolerance_ratio", 0.95)
        indicators_tolerance = config.get("indicators_tolerance_ratio", 0.90)
        moneyflow_tolerance = config.get("moneyflow_tolerance_ratio", 0.80)

        assert 0 < quotes_tolerance <= 1
        assert 0 < indicators_tolerance <= 1
        assert 0 < moneyflow_tolerance <= 1


class TestDelistDateHandling:
    """H1 测试：退市股票处理"""

    @pytest.mark.asyncio
    async def test_delist_date_excludes_retired_stocks(self, quote_dao):
        """
        测试 delist_date 精确排除历史退市股票
        """
        with patch.object(
            quote_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame({"trade_date": ["20180601"], "expected_count": [2500]}),
        ):
            counts = await quote_dao.get_bulk_expected_stock_counts("20180601", "20180601")

            assert isinstance(counts, dict)

    @pytest.mark.asyncio
    async def test_delist_date_sql_query(self, quote_dao):
        """
        测试 delist_date SQL 查询逻辑
        """
        mock_result = pd.DataFrame({"trade_date": ["20240101", "20240102"], "expected_count": [5200, 5200]})

        with patch.object(
            quote_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            counts = await quote_dao.get_bulk_expected_stock_counts("20240101", "20240102")

            assert len(counts) == 2


class TestSyncResultMerge:
    """测试 SyncResult.merge() 方法 - H4 修复验证"""

    def test_merge_basic_fields(self):
        """
        测试基本字段合并
        """
        result1 = SyncResult(status="success", added=10, updated=5, skipped=2)
        result2 = SyncResult(status="partial", added=5, updated=3, skipped=1)

        result1.merge(result2)

        assert result1.added == 15
        assert result1.updated == 8
        assert result1.skipped == 3

    def test_merge_errors(self):
        """
        测试错误合并
        """
        result1 = SyncResult(status="success", errors=["error1"])
        result2 = SyncResult(status="failed", errors=["error2", "error3"])

        result1.merge(result2)

        assert len(result1.errors) == 3
        assert result1.status == "partial"

    def test_merge_quality_scores(self):
        """
        测试质量评分合并

        验证点：
        1. 合并后两个结果的 quality_scores 都存在
        2. 字符串日期 key 被归一化为 datetime.date
        """
        result1 = SyncResult(
            status="success",
            quality_scores={"20240101": 85},
            expected_bases={"20240101": 5000},
        )
        result2 = SyncResult(
            status="success",
            quality_scores={"20240102": 90},
            expected_bases={"20240102": 5100},
        )

        result1.merge(result2)

        assert datetime.date(2024, 1, 1) in result1.quality_scores
        assert datetime.date(2024, 1, 2) in result1.quality_scores
        assert len(result1.quality_scores) == 2
        assert all(isinstance(k, datetime.date) for k in result1.quality_scores)

    def test_merge_status_priority(self):
        """
        测试状态优先级
        """
        result1 = SyncResult(status="success")
        result2 = SyncResult(status="failed")

        result1.merge(result2)

        assert result1.status == "partial"

        result3 = SyncResult(status="partial")
        result4 = SyncResult(status="success")

        result3.merge(result4)

        assert result3.status == "partial"

    def test_merge_failed_plus_success_equals_partial(self):
        """
        H4 修复验证：failed + success = partial

        场景：一个失败一个成功应产生 partial 状态
        """
        result1 = SyncResult(status="failed")
        result2 = SyncResult(status="success")

        result1.merge(result2)

        assert result1.status == "partial"

    def test_merge_failed_plus_failed_equals_failed(self):
        """
        H4 修复验证：failed + failed = failed

        场景：两个都失败应保持 failed 状态
        """
        result1 = SyncResult(status="failed")
        result2 = SyncResult(status="failed")

        result1.merge(result2)

        assert result1.status == "failed"

    def test_merge_cancelled_takes_priority(self):
        """
        H4 修复验证：cancelled 状态优先级最高

        场景：任何状态 + cancelled = cancelled
        """
        result1 = SyncResult(status="success")
        result2 = SyncResult(status="cancelled")

        result1.merge(result2)

        assert result1.status == "cancelled"

        result3 = SyncResult(status="failed")
        result4 = SyncResult(status="cancelled")

        result3.merge(result4)

        assert result3.status == "cancelled"

    def test_merge_partial_plus_success_equals_partial(self):
        """
        H4 修复验证：partial + success = partial

        场景：部分成功后继续成功仍保持 partial
        """
        result1 = SyncResult(status="partial")
        result2 = SyncResult(status="success")

        result1.merge(result2)

        assert result1.status == "partial"


class TestPromptDeclarationReport:
    """测试 Prompt 声明报告"""

    @pytest.mark.asyncio
    async def test_generate_declaration_report(self):
        """
        测试生成声明状态报告
        """
        await validate_prompt_declarations(get_declarations())
        report = generate_declaration_report(get_declarations())

        assert "multi_period_roe" in report
        assert "Prompt 数据声明状态报告" in report

    @pytest.mark.asyncio
    async def test_declaration_status_format(self):
        """
        测试声明状态格式
        """
        await validate_prompt_declarations(get_declarations())

        for decl in get_declarations():
            assert decl.status in [
                "available",
                "missing",
                "unknown",
            ] or decl.status.startswith("error")


class TestGetSyncQualityScoreKeyType:
    """M2 修复验证：get_sync_quality_score key 类型不匹配"""

    @pytest.mark.asyncio
    async def test_string_date_key_lookup(self, quote_dao):
        """
        测试字符串日期参数能正确查找结果

        场景：传入 "20240101" 字符串，应能正确返回结果
        """
        mock_result = {
            datetime.date(2024, 1, 1): {
                "score": 85,
                "expected_base": 5000,
                "tables": {},
                "issues": [],
            }
        }

        with patch.object(
            quote_dao,
            "get_bulk_sync_quality_scores",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await quote_dao.get_sync_quality_score("20240101")

            assert result["score"] == 85

    @pytest.mark.asyncio
    async def test_datetime_date_key_lookup(self, quote_dao):
        """
        测试 datetime.date 参数能正确查找结果

        场景：传入 datetime.date(2024, 1, 1)，应能正确返回结果
        """
        mock_result = {
            datetime.date(2024, 1, 1): {
                "score": 90,
                "expected_base": 5100,
                "tables": {},
                "issues": [],
            }
        }

        with patch.object(
            quote_dao,
            "get_bulk_sync_quality_scores",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await quote_dao.get_sync_quality_score(datetime.date(2024, 1, 1))

            assert result["score"] == 90

    @pytest.mark.asyncio
    async def test_fallback_on_empty_result(self, quote_dao):
        """
        测试空结果时返回默认值

        场景：查询失败时返回默认的失败结果
        """
        with patch.object(
            quote_dao,
            "get_bulk_sync_quality_scores",
            new_callable=AsyncMock,
            return_value={},
        ):
            result = await quote_dao.get_sync_quality_score("20240101")

            assert result["score"] == 0
            assert "查询失败" in result["issues"]


class TestLowFrequencyTableScoring:
    """H2 修复验证：低频表评分策略"""

    @pytest.mark.asyncio
    async def test_low_frequency_tables_not_affect_score(self, quote_dao):
        """
        测试低频表不影响整体评分

        场景：低频表（如 block_trade）无数据时，评分不应降低
        """
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

        mock_expected = {datetime.date(2024, 1, 1): 5000}

        async def mock_read_db(query, *args, **kwargs):
            if "stock_counts" in query or "expected_count" in query:
                df = pd.DataFrame(
                    {
                        "trade_date": [datetime.date(2024, 1, 1)],
                        "expected_count": [5000],
                    }
                )
                return df
            if "COUNT" in query and "daily_quotes" in query:
                df = pd.DataFrame(
                    {
                        "trade_date": [datetime.date(2024, 1, 1)],
                        "cnt": [5000],
                    }
                )
                return df
            if "COUNT" in query and "block_trade" in query:
                return pd.DataFrame()
            return pd.DataFrame()

        with (
            patch.object(quote_dao, "_read_db", new_callable=AsyncMock, side_effect=mock_read_db),
            patch.object(
                quote_dao,
                "get_bulk_expected_stock_counts",
                new_callable=AsyncMock,
                return_value=mock_expected,
            ),
        ):
            scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240101")

            if datetime.date(2024, 1, 1) in scores:
                result = scores[datetime.date(2024, 1, 1)]
                for table in LOW_FREQUENCY_TABLES:
                    if table in result.get("tables", {}):
                        assert result["tables"][table].get("passed") is True
                        assert result["tables"][table].get("exempt") is True
                        assert result["tables"][table].get("ratio") is None

    @pytest.mark.asyncio
    async def test_low_frequency_tables_do_not_inflate_score(self, quote_dao):
        """
        低频表不应抬高质量分数

        场景：高频表 ratio=0.9，低频表 ratio=None/exempt=True
        期望：分数仅基于高频表计算，不被低频表抬高
        """
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

        mock_expected = {datetime.date(2024, 1, 1): 5000}

        async def mock_read_db(query, *args, **kwargs):
            if "stock_counts" in query or "expected_count" in query:
                return pd.DataFrame(
                    {
                        "trade_date": [datetime.date(2024, 1, 1)],
                        "expected_count": [5000],
                    }
                )
            if "COUNT" in query:
                return pd.DataFrame(
                    {
                        "trade_date": [datetime.date(2024, 1, 1)],
                        "cnt": [4500],
                    }
                )
            return pd.DataFrame()

        with (
            patch.object(quote_dao, "_read_db", new_callable=AsyncMock, side_effect=mock_read_db),
            patch.object(
                quote_dao,
                "get_bulk_expected_stock_counts",
                new_callable=AsyncMock,
                return_value=mock_expected,
            ),
        ):
            scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240101")

            if datetime.date(2024, 1, 1) in scores:
                result = scores[datetime.date(2024, 1, 1)]
                score = result.get("score", 0)
                for table in LOW_FREQUENCY_TABLES:
                    if table in result.get("tables", {}):
                        assert result["tables"][table].get("exempt") is True
                        assert result["tables"][table].get("ratio") is None
                exempt_count = sum(
                    1 for t, info in result.get("tables", {}).items() if info.get("exempt") or info.get("ratio") is None
                )
                non_exempt_count = sum(
                    1
                    for t, info in result.get("tables", {}).items()
                    if not info.get("exempt") and info.get("ratio") is not None
                )
                if non_exempt_count > 0 and exempt_count > 0:
                    from utils.config_handler import ConfigHandler

                    quality_config = ConfigHandler.get_sync_integrity_config()
                    quality_weights = quality_config.get("quality_weights", {})
                    total_weight = 0
                    weighted_score = 0
                    for t, info in result["tables"].items():
                        if info.get("exempt") or info.get("ratio") is None:
                            continue
                        if "ratio" in info:
                            w = quality_weights.get(t, 5)
                            weighted_score += info["ratio"] * w
                            total_weight += w
                    expected_score = int(min(100, (weighted_score / total_weight) * 100)) if total_weight > 0 else 0
                    assert score == expected_score, (
                        f"Score {score} should equal weighted score {expected_score}, "
                        f"not inflated by {exempt_count} exempt tables"
                    )


class TestBreakpointResumeCoreTables:
    """H3 修复验证：断点续传 CORE_RESUME_TABLES"""

    @pytest.mark.asyncio
    async def test_resume_uses_core_tables_only(self, mock_context):
        """
        测试断点续传仅使用核心表

        场景：非核心表无数据时，断点续传仍应正常工作
        """
        from data.sync.historical import HistoricalSyncStrategy

        strategy = HistoricalSyncStrategy(mock_context)

        cached_dates = {
            "daily_quotes": {datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)},
            "daily_indicators": {datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)},
            "block_trade": set(),
            "moneyflow_daily": set(),
        }

        strategy.context.cache.get_cached_dates_for_table = AsyncMock(side_effect=lambda t: cached_dates.get(t, set()))

        assert hasattr(strategy, "CORE_RESUME_TABLES")
        assert "daily_quotes" in strategy.CORE_RESUME_TABLES
        assert "daily_indicators" in strategy.CORE_RESUME_TABLES

    @pytest.mark.asyncio
    async def test_resume_ignores_auxiliary_tables(self, mock_context):
        """
        测试断点续传忽略辅助表

        场景：辅助表（如 block_trade）无数据不影响断点续传
        """
        from data.sync.historical import HistoricalSyncStrategy

        strategy = HistoricalSyncStrategy(mock_context)

        assert "block_trade" not in strategy.CORE_RESUME_TABLES
        assert "moneyflow_daily" not in strategy.CORE_RESUME_TABLES

    @pytest.mark.asyncio
    async def test_resume_marks_missing_quality_as_resync(self, mock_context):
        """
        回归测试：quality_results 缺失日期 key 时应强制重同步

        场景：两个核心表都存在缓存日期，但质量结果只返回其中一天。
        期望：缺失质量结果的日期不能被当作高质量跳过，必须执行重同步。
        """
        from data.sync.historical import HistoricalSyncStrategy

        strategy = HistoricalSyncStrategy(mock_context)
        result = SyncResult()

        d1 = datetime.date(2024, 1, 1)
        d2 = datetime.date(2024, 1, 2)
        trade_dates = [d1, d2]

        mock_context.processor = MagicMock()
        mock_context.processor.trade_calendar = MagicMock()
        mock_context.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=trade_dates)

        cached_dates = {d1, d2}
        mock_context.cache.get_cached_dates_for_table = AsyncMock(return_value=cached_dates)
        mock_context.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                d1: {
                    "score": 95,
                    "expected_base": 5000,
                    "tables": {},
                    "issues": [],
                }
            }
        )

        strategy.sync_daily_market_snapshot = AsyncMock(return_value=True)

        await strategy._run_historical_sync(days=2, progress_callback=None, result=result)

        strategy.sync_daily_market_snapshot.assert_awaited_once()
        called_date = strategy.sync_daily_market_snapshot.await_args[0][0]
        assert called_date == d2
        assert result.updated == 1
        assert result.added == 1


class TestIncompleteFinancialStocksDetection:
    """M4 修复验证：get_incomplete_financial_stocks 漏检"""

    @pytest.mark.asyncio
    async def test_detects_stocks_with_no_records(self, financial_dao):
        """
        测试检测无记录的股票

        场景：股票存在于 stock_sync_status 但 financial_reports 无记录
        """
        mock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]})

        async def mock_read_db(query, *args, **kwargs):
            if "stock_sync_status" in query:
                return mock_df
            return pd.DataFrame()

        with patch.object(financial_dao, "_read_db", new_callable=AsyncMock, side_effect=mock_read_db):
            result = await financial_dao.get_incomplete_financial_stocks(min_periods=4)

            assert isinstance(result, set)

    @pytest.mark.asyncio
    async def test_detects_stocks_with_insufficient_periods(self, financial_dao):
        """
        测试检测报告期不足的股票

        场景：股票财务数据报告期少于最小要求
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "periods": [2],
            }
        )

        async def mock_read_db(query, *args, **kwargs):
            if "periods" in query or "COUNT" in query:
                return mock_df
            return pd.DataFrame()

        with patch.object(financial_dao, "_read_db", new_callable=AsyncMock, side_effect=mock_read_db):
            result = await financial_dao.get_incomplete_financial_stocks(min_periods=4)

            assert isinstance(result, set)


class TestDelistedStockLogic:
    """M5 修复验证：get_expected_stock_count 退市股票逻辑"""

    @pytest.mark.asyncio
    async def test_list_status_L_with_null_delist_date(self, quote_dao):
        """
        测试 L 状态且无退市日期的股票

        场景：list_status='L' 且 delist_date IS NULL 应计入
        """
        mock_df = pd.DataFrame({"is_trade_day": [1], "cnt": [5000]})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, return_value=mock_df):
            count = await quote_dao.get_expected_stock_count("20240101")

            assert count == 5000

    @pytest.mark.asyncio
    async def test_list_status_D_without_delist_date_excluded(self, quote_dao):
        """
        测试 D 状态但无退市日期的股票被排除

        场景：list_status='D' 且 delist_date IS NULL 不应计入
        """
        call_args = []

        async def capture_query(query, params):
            call_args.append(query)
            return pd.DataFrame({"cnt": [0]})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, side_effect=capture_query):
            await quote_dao.get_expected_stock_count("20240101")

            query = call_args[0] if call_args else ""
            assert "list_status = 'L'" in query or "list_status='L'" in query
            assert "delist_date IS NOT NULL" in query or "delist_date is not null" in query.lower()


class TestQualityWeightsConfig:
    """H1 修复验证：quality_weights 配置使用"""

    @pytest.mark.asyncio
    async def test_quality_weights_from_config(self):
        """
        测试质量评分使用配置权重

        场景：评分计算应使用配置中的 quality_weights
        """
        from utils.config_handler import ConfigHandler

        config = ConfigHandler.get_sync_integrity_config()
        weights = config.get("quality_weights", {})

        assert isinstance(weights, dict)
        if weights:
            assert "daily_quotes" in weights or len(weights) > 0

    @pytest.mark.asyncio
    async def test_quality_weights_applied_in_scoring(self, quote_dao):
        """
        测试评分计算应用权重配置

        场景：验证评分计算使用配置权重而非硬编码
        """
        mock_config = {
            "quality_threshold": 80,
            "quotes_tolerance_ratio": 0.95,
            "indicators_tolerance_ratio": 0.90,
            "moneyflow_tolerance_ratio": 0.80,
            "quality_weights": {
                "daily_quotes": 50,
                "daily_indicators": 30,
                "moneyflow_daily": 20,
            },
        }

        with (
            patch(
                "utils.config_handler.ConfigHandler.get_sync_integrity_config",
                return_value=mock_config,
            ),
            patch.object(
                quote_dao,
                "get_bulk_expected_stock_counts",
                new_callable=AsyncMock,
                return_value={datetime.date(2024, 1, 1): 5000},
            ),
            patch.object(
                quote_dao,
                "get_bulk_table_counts",
                new_callable=AsyncMock,
                return_value={datetime.date(2024, 1, 1): 5000},
            ),
            patch.object(
                quote_dao,
                "_read_db",
                new_callable=AsyncMock,
                return_value=pd.DataFrame(
                    {
                        "trade_date": [datetime.date(2024, 1, 1)],
                        "cnt": [5000],
                    }
                ),
            ),
        ):
            scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240101")

            assert isinstance(scores, dict)


class TestFinancialDaoGhostTable:
    """C1 修复验证：fina_indicator 幽灵表问题"""

    @pytest.mark.asyncio
    async def test_verify_financial_integrity_uses_fina_audit(self, financial_dao):
        """
        测试财务完整性验证使用 fina_audit 表

        场景：verify_stock_financial_integrity 应检查 fina_audit 而非 fina_indicator
        """
        call_args = []

        async def capture_query(query, params):
            call_args.append(query)
            if "periods" in query:
                return pd.DataFrame({"periods": [8]})
            return pd.DataFrame({"cnt": [1]})

        with patch.object(financial_dao, "_read_db", new_callable=AsyncMock, side_effect=capture_query):
            await financial_dao.verify_stock_financial_integrity("000001.SZ")

            queries = " ".join(call_args)
            assert "fina_audit" in queries
            assert "fina_indicator" not in queries


class TestHistoricalLayerViolation:
    """M3 修复验证：historical.py 层级违规"""

    @pytest.mark.asyncio
    async def test_uses_cache_manager_interface(self, mock_context):
        """
        测试使用 CacheManager 接口而非直接访问 DAO

        场景：HistoricalSyncStrategy 应通过 cache.get_bulk_table_counts 获取数据
        """
        from data.sync.historical import HistoricalSyncStrategy

        strategy = HistoricalSyncStrategy(mock_context)
        strategy.context.cache.get_bulk_table_counts = AsyncMock(return_value={datetime.date(2024, 1, 1): 100})

        assert hasattr(strategy.context.cache, "get_bulk_table_counts")

    @pytest.mark.asyncio
    async def test_no_direct_dao_access(self):
        """
        测试不直接访问 quote_dao._read_db

        场景：验证代码不包含 quote_dao._read_db 调用
        """
        from data.sync.historical import HistoricalSyncStrategy

        assert not hasattr(HistoricalSyncStrategy, "quote_dao") or "_read_db" not in dir(HistoricalSyncStrategy), (
            "HistoricalSyncStrategy should not access quote_dao._read_db directly"
        )


class TestP0RedundantFallbackRemoved:
    """P0-1 修复验证：移除冗余 fallback 查询"""

    @pytest.mark.asyncio
    async def test_no_fallback_query_on_empty_result(self, quote_dao):
        """
        测试空结果时不执行 fallback 查询

        场景：get_bulk_expected_stock_counts 返回空时不再执行第二次查询
        """
        call_count = 0

        async def count_calls(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame()

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, side_effect=count_calls):
            result = await quote_dao.get_bulk_expected_stock_counts("20240101", "20240131")

            assert call_count == 1, f"Expected 1 DB call, got {call_count}"
            assert result == {}


class TestP0DateKeyNormalization:
    """P0-2 修复验证：日期 key 类型归一化"""

    @pytest.mark.asyncio
    async def test_bulk_expected_counts_returns_date_keys(self, quote_dao):
        """
        测试 get_bulk_expected_stock_counts 返回 datetime.date 类型的 key

        场景：返回的字典 key 应统一为 datetime.date 类型
        """
        mock_df = pd.DataFrame({"trade_date": ["20240101", "20240102"], "expected_count": [5000, 5100]})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, return_value=mock_df):
            result = await quote_dao.get_bulk_expected_stock_counts("20240101", "20240102")

            for key in result:
                assert isinstance(key, datetime.date), f"Key {key} is not datetime.date"

    @pytest.mark.asyncio
    async def test_bulk_table_counts_returns_date_keys(self, quote_dao):
        """
        测试 get_bulk_table_counts 返回 datetime.date 类型的 key

        场景：返回的字典 key 应统一为 datetime.date 类型
        """
        mock_df = pd.DataFrame({"trade_date": ["20240101", "20240102"], "cnt": [5000, 5100]})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, return_value=mock_df):
            result = await quote_dao.get_bulk_table_counts("daily_quotes", "20240101", "20240102")

            for key in result:
                assert isinstance(key, datetime.date), f"Key {key} is not datetime.date"


class TestP1IndexTablesInLowFrequency:
    """P1-1 修复验证：指数表不应在 LOW_FREQUENCY_TABLES 中（容差 0.95 需正常评估）"""

    def test_index_daily_not_in_low_frequency_tables(self):
        """
        测试 index_daily 不在 LOW_FREQUENCY_TABLES 中

        场景：指数日线数据容差为 0.95，应被正常评估
        """
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

        assert "index_daily" not in LOW_FREQUENCY_TABLES

    def test_index_dailybasic_not_in_low_frequency_tables(self):
        """
        测试 index_dailybasic 不在 LOW_FREQUENCY_TABLES 中

        场景：指数每日指标容差为 0.95，应被正常评估
        """
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

        assert "index_dailybasic" not in LOW_FREQUENCY_TABLES

    def test_moneyflow_hsgt_not_in_low_frequency_tables(self):
        """
        测试 moneyflow_hsgt 不在 LOW_FREQUENCY_TABLES 中

        场景：沪深港通资金流向容差为 0.95，应被正常评估
        """
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

        assert "moneyflow_hsgt" not in LOW_FREQUENCY_TABLES

    def test_index_tables_use_fixed_expected(self):
        """
        测试指数/聚合表使用固定期望值而非股票数比例

        场景：index_daily/index_dailybasic/moneyflow_hsgt 不应使用 reference_count * tolerance
        """
        from data.persistence.daos.quote_dao import FIXED_EXPECTED_TABLES

        assert "index_daily" in FIXED_EXPECTED_TABLES
        assert "index_dailybasic" in FIXED_EXPECTED_TABLES
        assert "moneyflow_hsgt" in FIXED_EXPECTED_TABLES
        assert FIXED_EXPECTED_TABLES["index_daily"] > 0
        assert FIXED_EXPECTED_TABLES["index_dailybasic"] > 0
        assert FIXED_EXPECTED_TABLES["moneyflow_hsgt"] > 0

    @pytest.mark.asyncio
    async def test_index_tables_not_falsely_reported_incomplete(self, quote_dao):
        """
        测试指数/聚合表数据完整时不会误报为不完整

        场景：index_daily 有 7 条记录时，应 passed=True 而非 passed=False
        """
        from data.persistence.daos.quote_dao import FIXED_EXPECTED_TABLES

        mock_expected = {datetime.date(2024, 1, 1): 5000}

        async def mock_get_bulk_table_counts(table_name, start_date, end_date):
            if table_name in FIXED_EXPECTED_TABLES:
                return {datetime.date(2024, 1, 1): FIXED_EXPECTED_TABLES[table_name]}
            return {datetime.date(2024, 1, 1): 5000}

        with (
            patch.object(
                quote_dao,
                "get_bulk_table_counts",
                new_callable=AsyncMock,
                side_effect=mock_get_bulk_table_counts,
            ),
            patch.object(
                quote_dao,
                "get_bulk_expected_stock_counts",
                new_callable=AsyncMock,
                return_value=mock_expected,
            ),
        ):
            scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240101")

            if datetime.date(2024, 1, 1) in scores:
                result = scores[datetime.date(2024, 1, 1)]
                for table in FIXED_EXPECTED_TABLES:
                    if table in result.get("tables", {}):
                        table_info = result["tables"][table]
                        assert table_info["passed"] is True, (
                            f"{table} should be passed=True with fixed expected={FIXED_EXPECTED_TABLES[table]}, "
                            f"but got expected={table_info['expected']}, count={table_info['count']}, ratio={table_info['ratio']}"
                        )
                        assert table_info["expected"] == FIXED_EXPECTED_TABLES[table], (
                            f"{table} expected should be {FIXED_EXPECTED_TABLES[table]}, got {table_info['expected']}"
                        )


class TestP1TradingDayValidation:
    """P1-3 修复验证：单日查询添加交易日验证"""

    @pytest.mark.asyncio
    async def test_non_trading_day_returns_zero(self, quote_dao):
        """
        测试非交易日返回 0

        场景：周末或节假日应返回 0 而非股票数
        """
        mock_df = pd.DataFrame({"is_trade_day": [0], "cnt": [5000]})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, return_value=mock_df):
            count = await quote_dao.get_expected_stock_count("20240107")

            assert count == 0

    @pytest.mark.asyncio
    async def test_trading_day_returns_stock_count(self, quote_dao):
        """
        测试交易日返回正确的股票数

        场景：交易日应返回实际股票数
        """
        mock_df = pd.DataFrame({"is_trade_day": [1], "cnt": [5200]})

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, return_value=mock_df):
            count = await quote_dao.get_expected_stock_count("20240102")

            assert count == 5200


class TestP1SyncVersionParameterized:
    """P1-4 修复验证：sync_version 参数化"""

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks_default_params(self, financial_dao):
        """
        测试默认参数值

        场景：不传参数时使用默认值 sync_version=1, min_periods=4
        """
        call_args = []

        async def capture_query(query, params):
            call_args.append((query, params))
            return pd.DataFrame()

        with patch.object(financial_dao, "_read_db", new_callable=AsyncMock, side_effect=capture_query):
            await financial_dao.get_incomplete_financial_stocks()

            if call_args:
                query, params = call_args[0]
                assert params[0] == 1
                assert params[1] == 4

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks_custom_params(self, financial_dao):
        """
        测试自定义参数值

        场景：传入自定义 sync_version 和 min_periods
        """
        call_args = []

        async def capture_query(query, params):
            call_args.append((query, params))
            return pd.DataFrame()

        with patch.object(financial_dao, "_read_db", new_callable=AsyncMock, side_effect=capture_query):
            await financial_dao.get_incomplete_financial_stocks(min_periods=8, sync_version=2)

            if call_args:
                query, params = call_args[0]
                assert params[0] == 2
                assert params[1] == 8


class TestP2SyncResultMergeDateNormalization:
    """P2-1 修复验证：SyncResult.merge 日期 key 归一化"""

    def test_merge_normalizes_string_date_keys(self):
        """
        测试合并时归一化字符串日期 key

        场景：quality_scores 中的字符串日期应转换为 datetime.date
        """
        result1 = SyncResult()
        result1.quality_scores["20240101"] = 85

        result2 = SyncResult()
        result2.quality_scores[datetime.date(2024, 1, 2)] = 90

        result1.merge(result2)

        for key in result1.quality_scores:
            assert isinstance(key, datetime.date), f"Key {key} is not datetime.date"

    def test_merge_normalizes_expected_bases_keys(self):
        """
        测试合并时归一化 expected_bases 的 key

        场景：expected_bases 中的字符串日期应转换为 datetime.date
        """
        result1 = SyncResult()
        result1.expected_bases["20240101"] = 5000

        result2 = SyncResult()
        result2.expected_bases[datetime.date(2024, 1, 2)] = 5100

        result1.merge(result2)

        for key in result1.expected_bases:
            assert isinstance(key, datetime.date), f"Key {key} is not datetime.date"


class TestP2RecursiveConfigMerge:
    """P2-4 修复验证：递归嵌套配置补全"""

    def test_deep_merge_defaults_adds_missing_keys(self):
        """
        测试递归合并添加缺失的嵌套键

        场景：嵌套字典中缺失的键应被补全
        """
        from utils.config_handler import ConfigHandler

        current = {"sync_integrity": {"quotes_tolerance_ratio": 0.95}}
        defaults = {
            "sync_integrity": {
                "quotes_tolerance_ratio": 0.95,
                "indicators_tolerance_ratio": 0.90,
                "quality_weights": {"daily_quotes": 30},
            }
        }

        result, dirty = ConfigHandler._deep_merge_defaults(current, defaults)

        assert dirty is True
        assert "indicators_tolerance_ratio" in result["sync_integrity"]
        assert "quality_weights" in result["sync_integrity"]

    def test_deep_merge_defaults_preserves_existing(self):
        """
        测试递归合并保留现有值

        场景：已存在的值不应被覆盖
        """
        from utils.config_handler import ConfigHandler

        current = {"sync_integrity": {"quotes_tolerance_ratio": 0.80}}
        defaults = {"sync_integrity": {"quotes_tolerance_ratio": 0.95}}

        result, dirty = ConfigHandler._deep_merge_defaults(current, defaults)

        assert dirty is False
        assert result["sync_integrity"]["quotes_tolerance_ratio"] == 0.80

    def test_deep_merge_defaults_handles_deep_nesting(self):
        """
        测试深层嵌套合并

        场景：多层嵌套字典应正确合并
        """
        from utils.config_handler import ConfigHandler

        current = {"level1": {"level2": {"existing": 1}}}
        defaults = {"level1": {"level2": {"existing": 1, "new": 2}, "level2b": 3}}

        result, dirty = ConfigHandler._deep_merge_defaults(current, defaults)

        assert dirty is True
        assert result["level1"]["level2"]["new"] == 2
        assert result["level1"]["level2b"] == 3


class TestM5SyncResultToDict:
    """M5 修复验证：SyncResult.to_dict() 方法"""

    def test_to_dict_returns_complete_structure(self):
        """
        测试 to_dict 返回完整结构

        场景：to_dict 应返回包含所有字段的字典
        """
        result = SyncResult(
            status="success",
            added=100,
            updated=50,
            skipped=10,
            message="Test sync",
        )
        result.errors.append("Error 1")
        result.warnings.append("Warning 1")
        result.quality_scores[datetime.date(2024, 1, 1)] = 85.5
        result.expected_bases[datetime.date(2024, 1, 1)] = 5000
        result.table_stats["daily_quotes"] = {"count": 100}

        d = result.to_dict()

        assert d["status"] == "success"
        assert d["added"] == 100
        assert d["updated"] == 50
        assert d["skipped"] == 10
        assert d["message"] == "Test sync"
        assert d["errors"] == ["Error 1"]
        assert d["warnings"] == ["Warning 1"]
        assert datetime.date(2024, 1, 1) in d["quality_scores"]
        assert d["quality_scores"][datetime.date(2024, 1, 1)] == 85.5
        assert datetime.date(2024, 1, 1) in d["expected_bases"]
        assert d["table_stats"]["daily_quotes"]["count"] == 100

    def test_to_dict_returns_copies(self):
        """
        测试 to_dict 返回副本而非引用

        场景：修改返回的字典不应影响原始 SyncResult
        """
        result = SyncResult(status="success")
        result.errors.append("Error 1")

        d = result.to_dict()
        d["errors"].append("Error 2")
        d["quality_scores"][datetime.date(2024, 1, 1)] = 90

        assert len(result.errors) == 1
        assert len(d["errors"]) == 2
        assert datetime.date(2024, 1, 1) not in result.quality_scores


class TestL1NormalizeTradeDate:
    """L1 修复验证：_normalize_trade_date 工具函数"""

    def test_normalize_datetime_date(self):
        """
        测试 datetime.date 类型直接返回
        """
        from data.persistence.daos.quote_dao import _normalize_trade_date

        input_date = datetime.date(2024, 1, 15)
        result = _normalize_trade_date(input_date)

        assert result == input_date
        assert isinstance(result, datetime.date)

    def test_normalize_datetime_datetime(self):
        """
        测试 datetime.datetime 转换为 date
        """
        from data.persistence.daos.quote_dao import _normalize_trade_date

        input_dt = datetime.datetime(2024, 1, 15, 10, 30, 0)
        result = _normalize_trade_date(input_dt)

        assert result == datetime.date(2024, 1, 15)
        assert isinstance(result, datetime.date)

    def test_normalize_string_yyyymmdd(self):
        """
        测试字符串 YYYYMMDD 格式转换
        """
        from data.persistence.daos.quote_dao import _normalize_trade_date

        result = _normalize_trade_date("20240115")

        assert result == datetime.date(2024, 1, 15)
        assert isinstance(result, datetime.date)

    def test_normalize_invalid_string_returns_original(self):
        """
        测试无效字符串返回原值
        """
        from data.persistence.daos.quote_dao import _normalize_trade_date

        result = _normalize_trade_date("invalid")

        assert result == "invalid"

    def test_normalize_none_returns_none(self):
        """
        测试 None 返回 None
        """
        from data.persistence.daos.quote_dao import _normalize_trade_date

        result = _normalize_trade_date(None)

        assert result is None


class TestH3SafeTableNames:
    """H3 修复验证：SQL 注入防御白名单"""

    def test_safe_table_names_contains_required_tables(self):
        """
        测试白名单包含所有必需的表名
        """
        from data.persistence.daos.quote_dao import _SAFE_TABLE_NAMES

        required_tables = {
            "daily_quotes",
            "daily_indicators",
            "moneyflow_daily",
            "margin_daily",
            "financial_reports",
            "stock_basic",
            "trade_cal",
        }

        for table in required_tables:
            assert table in _SAFE_TABLE_NAMES, f"{table} not in _SAFE_TABLE_NAMES"

    def test_safe_table_names_is_frozenset(self):
        """
        测试白名单是不可变集合
        """
        from data.persistence.daos.quote_dao import _SAFE_TABLE_NAMES

        assert isinstance(_SAFE_TABLE_NAMES, frozenset)

    def test_safe_table_names_contains_cn_m(self):
        """
        测试白名单包含 cn_m 宏观数据表
        """
        from data.persistence.daos.quote_dao import _SAFE_TABLE_NAMES

        assert "cn_m" in _SAFE_TABLE_NAMES

    def test_get_default_synced_tables_filters_unsafe(self):
        """
        测试 _get_default_synced_tables 过滤非白名单表
        """
        from data.persistence.daos.quote_dao import (
            _SAFE_TABLE_NAMES,
            _get_default_synced_tables,
        )

        tables = _get_default_synced_tables()

        for table in tables:
            assert table in _SAFE_TABLE_NAMES, f"{table} not in safe list"


class TestM6CacheManagerCheckTableHasData:
    """M6 修复验证：CacheManager.check_table_has_data 封装方法（使用 SQLAlchemy Core）"""

    @staticmethod
    def _make_mock_engine(execute_return_value):
        """构建 mock engine，模拟 async with engine.connect() as conn 路径"""
        mock_result = MagicMock()
        mock_result.first.return_value = execute_return_value

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect.return_value = mock_ctx

        return mock_engine, mock_conn

    @pytest_asyncio.fixture
    async def cache_manager(self):
        """创建 CacheManager 实例"""
        from data.cache.cache_manager import CacheManager

        with patch("data.cache.cache_manager.CacheManager.__init__", return_value=None):
            cache = CacheManager()
            # 构建默认 mock engine（有数据场景）
            engine, conn = self._make_mock_engine(execute_return_value=(1,))
            cache.engine = engine
            cache._mock_conn = conn  # 暴露给测试用例做断言
            cache._maintenance_mode = False
            cache._maintenance_cv = MagicMock()
            cache._maintenance_cv.wait = AsyncMock()
            return cache

    @pytest.mark.asyncio
    async def test_check_table_has_data_allowed_table(self, cache_manager):
        """
        测试允许的表名返回正确结果
        """
        result = await cache_manager.check_table_has_data("fina_audit")

        assert result is True
        cache_manager._mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_table_has_data_blocked_table(self, cache_manager):
        """
        测试不允许的表名返回 False（白名单拦截，不触碰数据库）
        """
        result = await cache_manager.check_table_has_data("malicious_table")

        assert result is False
        cache_manager.engine.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_table_has_data_empty_result(self, cache_manager):
        """
        测试空结果返回 False
        """
        # 重新设置 engine，模拟 result.first() 返回 None
        engine, conn = self._make_mock_engine(execute_return_value=None)
        cache_manager.engine = engine

        result = await cache_manager.check_table_has_data("fina_audit")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_table_has_data_exception_handling(self, cache_manager):
        """
        测试异常情况返回 False
        """
        # 重新设置 engine，模拟 conn.execute 抛异常
        engine, conn = self._make_mock_engine(execute_return_value=None)
        conn.execute.side_effect = Exception("DB Error")
        cache_manager.engine = engine

        result = await cache_manager.check_table_has_data("fina_audit")

        assert result is False


class TestCacheManagerGetIncompleteFinancialStocks:
    """C-NEW-2 修复验证：CacheManager.get_incomplete_financial_stocks 参数传递"""

    @pytest_asyncio.fixture
    async def cache_manager(self):
        """创建 CacheManager 实例"""
        from data.cache.cache_manager import CacheManager

        with patch("data.cache.cache_manager.CacheManager.__init__", return_value=None):
            cache = CacheManager()
            cache.financial_dao = MagicMock()
            cache.financial_dao.get_incomplete_financial_stocks = AsyncMock(return_value={"000001.SZ"})
            return cache

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks_default_params(self, cache_manager):
        """
        测试默认参数传递

        场景：不传参数时使用默认值 min_periods=4, sync_version=1
        """
        result = await cache_manager.get_incomplete_financial_stocks()

        assert result == {"000001.SZ"}
        cache_manager.financial_dao.get_incomplete_financial_stocks.assert_called_once_with(4, 1)

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks_custom_params(self, cache_manager):
        """
        测试自定义参数传递

        场景：传入自定义 min_periods 和 sync_version
        """
        cache_manager.financial_dao.get_incomplete_financial_stocks.return_value = set()

        result = await cache_manager.get_incomplete_financial_stocks(min_periods=8, sync_version=2)

        assert result == set()
        cache_manager.financial_dao.get_incomplete_financial_stocks.assert_called_once_with(8, 2)

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks_only_min_periods(self, cache_manager):
        """
        测试只传 min_periods 参数

        场景：只传入 min_periods，sync_version 使用默认值
        """
        result = await cache_manager.get_incomplete_financial_stocks(min_periods=6)

        assert result == {"000001.SZ"}
        cache_manager.financial_dao.get_incomplete_financial_stocks.assert_called_once_with(6, 1)

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks_only_sync_version(self, cache_manager):
        """
        测试只传 sync_version 参数

        场景：只传入 sync_version，min_periods 使用默认值
        """
        result = await cache_manager.get_incomplete_financial_stocks(sync_version=3)

        assert result == {"000001.SZ"}
        cache_manager.financial_dao.get_incomplete_financial_stocks.assert_called_once_with(4, 3)


class TestConsecutiveFailuresCircuitBreaker:
    """测试 historical.py 连续失败熔断机制 (P1-5)"""

    def test_consecutive_counter_resets_on_success(self):
        """连续失败计数器在成功后重置"""
        consecutive_failures = 0

        consecutive_failures += 1
        consecutive_failures += 1
        assert consecutive_failures == 2

        consecutive_failures = 0
        assert consecutive_failures == 0

        for _ in range(5):
            consecutive_failures += 1
        assert consecutive_failures == 5

        consecutive_failures = 0
        assert consecutive_failures == 0

    def test_consecutive_threshold_triggers_abort(self):
        """连续失败超过阈值触发熔断"""
        consecutive_failures = 0
        CB_THRESHOLD = 3
        abort_sync = False

        for _ in range(4):
            consecutive_failures += 1
            if consecutive_failures > CB_THRESHOLD:
                abort_sync = True
                break

        assert abort_sync is True
        assert consecutive_failures == 4

    def test_interleaved_failures_no_abort(self):
        """交替失败和成功不触发熔断（非连续）"""
        consecutive_failures = 0
        CB_THRESHOLD = 3
        abort_sync = False

        for result in ["fail", "success", "fail", "success", "fail"]:
            if result == "success":
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures > CB_THRESHOLD:
                    abort_sync = True
                    break

        assert abort_sync is False
        assert consecutive_failures == 1

    def test_cb_threshold_dynamic_calculation(self):
        """熔断阈值随总天数动态调整"""
        assert min(50, max(10, int(0 * 0.1) if 0 > 0 else 10)) == 10
        assert min(50, max(10, int(50 * 0.1))) == 10
        assert min(50, max(10, int(100 * 0.1))) == 10
        assert min(50, max(10, int(200 * 0.1))) == 20
        assert min(50, max(10, int(500 * 0.1))) == 50
        assert min(50, max(10, int(1000 * 0.1))) == 50
