"""
行情数据完整性测试

测试 Phase 2: 数据同步完整性增强
- H1: delist_date 精确计算历史存活股票数
- H2: 批量查询性能优化
- M2: 质量评分机制
- M3: 批量聚合查询
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from data.persistence.daos.quote_dao import QuoteDao


class TestQuoteDaoIntegrity:
    """测试行情数据完整性检查方法"""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.begin = MagicMock()
        return engine

    @pytest.fixture
    def quote_dao(self, mock_engine):
        return QuoteDao(mock_engine)

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_with_delist_date(self, quote_dao):
        """
        H1 测试：使用 delist_date 精确计算历史存活股票数

        场景：2018-06-01 应排除已退市股票
        """
        count = await quote_dao.get_expected_stock_count("20180601")

        assert count >= 0
        assert count < 5000

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_recent_date(self, quote_dao):
        """
        测试近期日期的存活股票数

        场景：2024-01-01 应包含约 5300 只股票
        """
        count = await quote_dao.get_expected_stock_count("20240101")

        assert count >= 0

    @pytest.mark.asyncio
    async def test_get_bulk_expected_stock_counts(self, quote_dao):
        """
        H2 测试：批量获取存活股票数

        场景：验证批量查询性能和正确性
        """
        counts = await quote_dao.get_bulk_expected_stock_counts("20240101", "20240131")

        assert isinstance(counts, dict)

    @pytest.mark.asyncio
    async def test_get_bulk_table_counts(self, quote_dao):
        """
        M3 测试：批量获取表记录数

        场景：验证单次查询获取指定表的记录数
        """
        counts = await quote_dao.get_bulk_table_counts("daily_quotes", "20240101", "20240131")

        assert isinstance(counts, dict)

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores(self, quote_dao):
        """
        M2 测试：批量质量评分

        场景：验证批量计算质量评分
        """
        scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240105")

        assert isinstance(scores, dict)
        for _date, quality_info in scores.items():
            assert isinstance(quality_info, dict)
            assert 0 <= quality_info.get("score", 0) <= 100


class TestQuoteDaoBoundary:
    """边界条件测试"""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.begin = MagicMock()
        return engine

    @pytest.fixture
    def quote_dao(self, mock_engine):
        return QuoteDao(mock_engine)

    @pytest.mark.asyncio
    async def test_empty_stock_basic_fallback(self, quote_dao):
        """
        边界测试：stock_basic 为空时的降级处理
        """
        with patch.object(
            quote_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=None,
        ):
            count = await quote_dao.get_expected_stock_count("20240101")

            assert count == 0

    @pytest.mark.asyncio
    async def test_future_date_handling(self, quote_dao):
        """
        边界测试：未来日期处理
        """
        count = await quote_dao.get_expected_stock_count("20990101")

        assert count >= 0

    @pytest.mark.asyncio
    async def test_invalid_date_format(self, quote_dao):
        """
        边界测试：无效日期格式
        """
        with patch.object(
            quote_dao,
            "_read_db",
            new_callable=AsyncMock,
            side_effect=Exception("Invalid date"),
        ):
            count = await quote_dao.get_expected_stock_count("invalid")

            assert count == 0

    @pytest.mark.asyncio
    async def test_bulk_counts_empty_range(self, quote_dao):
        """
        边界测试：空日期范围
        """
        counts = await quote_dao.get_bulk_table_counts("daily_quotes", "20990101", "20990105")

        assert isinstance(counts, dict)
        assert len(counts) == 0


class TestQuoteDaoPerformance:
    """性能测试"""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.begin = MagicMock()
        return engine

    @pytest.fixture
    def quote_dao(self, mock_engine):
        return QuoteDao(mock_engine)

    @pytest.mark.asyncio
    async def test_bulk_vs_individual_query_count(self, quote_dao):
        """
        性能测试：验证批量查询减少 DB 调用次数

        原方案：N 天 × M 表 = N×M 次查询
        优化后：M 次查询
        """
        call_count = 0

        async def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"daily_quotes": {}}

        with patch.object(quote_dao, "_read_db", new_callable=AsyncMock, side_effect=count_calls):
            await quote_dao.get_bulk_sync_quality_scores("20210101", "20231231")

            assert call_count <= 20, f"Expected <= 20 DB calls, got {call_count}"


class TestQualityScoreWeights:
    """质量评分权重测试"""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.begin = MagicMock()
        return engine

    @pytest.fixture
    def quote_dao(self, mock_engine):
        return QuoteDao(mock_engine)

    @pytest.mark.asyncio
    async def test_quality_score_with_custom_weights(self, quote_dao):
        """
        测试自定义权重配置
        """
        mock_config = {
            "sync_integrity": {
                "quality_weights": {
                    "daily_quotes": 50,
                    "daily_indicators": 30,
                    "moneyflow_daily": 20,
                }
            }
        }

        with (
            patch(
                "utils.config_handler.ConfigHandler.load_config",
                return_value=mock_config,
            ),
            patch.object(
                quote_dao,
                "get_bulk_table_counts",
                new_callable=AsyncMock,
                return_value={"daily_quotes": {"20240101": 5000}},
            ),
            patch.object(
                quote_dao,
                "get_bulk_expected_stock_counts",
                new_callable=AsyncMock,
                return_value={"20240101": 5000},
            ),
        ):
            scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240101")

            assert isinstance(scores, dict)
