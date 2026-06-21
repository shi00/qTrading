"""
行情数据完整性测试

测试 Phase 2: 数据同步完整性增强
- H1: delist_date 精确计算历史存活股票数
- H2: 批量查询性能优化
- M2: 质量评分机制
- M3: 批量聚合查询

P2-3-a fix: TestQuoteDaoIntegrity 改为使用真实数据库 + 精确断言，
不再使用 MagicMock engine 配合 `count >= 0` 这类无效断言。
"""

import contextlib
import datetime

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from unittest.mock import AsyncMock, patch

from data.persistence.daos.quote_dao import QuoteDao
from tests.builders.stock_data import (
    make_daily_quote_row,
    make_stock_basic_row,
    make_trade_cal_rows,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_engine: AsyncEngine):
    """每个测试前清理相关表，避免数据残留干扰断言。"""
    tables = [
        "daily_quotes",
        "stock_basic",
        "trade_cal",
        "daily_indicators",
        "financial_reports",
    ]
    async with test_engine.begin() as conn:
        for table in tables:
            with contextlib.suppress(Exception):
                await conn.execute(text(f"DELETE FROM {table}"))
    yield


@pytest_asyncio.fixture
async def quote_dao(test_engine: AsyncEngine):
    return QuoteDao(test_engine)


class TestQuoteDaoIntegrity:
    """测试行情数据完整性检查方法（真实数据库 + 精确断言）。

    P2-3-a: 替换原 MagicMock engine + `count >= 0` 弱断言，
    改为插入已知测试数据后断言精确数值。
    """

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_with_delist_date(self, test_engine: AsyncEngine, quote_dao: QuoteDao):
        """
        H1 测试：使用 delist_date 精确计算历史存活股票数

        场景：2018-06-01 应排除已退市股票
        - 000001.SZ: L 状态，list_date=2010-01-01，存活
        - 000002.SZ: D 状态，delist_date=2015-01-01，已退市，应排除
        """
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO stock_basic (ts_code, symbol, name, list_status, list_date, delist_date) "
                    "VALUES (:ts_code, :symbol, :name, :list_status, :list_date, :delist_date)"
                ),
                make_stock_basic_row(
                    ts_code="000001.SZ",
                    name="平安银行",
                    list_status="L",
                    list_date=datetime.date(2010, 1, 1),
                    delist_date=None,
                ),
            )
            await conn.execute(
                text(
                    "INSERT INTO stock_basic (ts_code, symbol, name, list_status, list_date, delist_date) "
                    "VALUES (:ts_code, :symbol, :name, :list_status, :list_date, :delist_date)"
                ),
                make_stock_basic_row(
                    ts_code="000002.SZ",
                    name="已退市股票",
                    list_status="D",
                    list_date=datetime.date(2010, 1, 1),
                    delist_date=datetime.date(2015, 1, 1),
                ),
            )
            await conn.execute(
                text(
                    "INSERT INTO trade_cal (cal_date, exchange, is_open, pretrade_date) "
                    "VALUES (:cal_date, :exchange, :is_open, :pretrade_date)"
                ),
                make_trade_cal_rows([datetime.date(2018, 6, 1)])[0],
            )

        count = await quote_dao.get_expected_stock_count("20180601")

        assert count == 1, f"应仅 1 只存活股票（排除已退市），实际 {count}"

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_recent_date(self, test_engine: AsyncEngine, quote_dao: QuoteDao):
        """
        测试近期日期的存活股票数

        场景：2024-01-01 应包含 2 只 L 状态股票
        """
        async with test_engine.begin() as conn:
            for ts_code, name in [("000001.SZ", "平安银行"), ("000002.SZ", "万科A")]:
                await conn.execute(
                    text(
                        "INSERT INTO stock_basic (ts_code, symbol, name, list_status, list_date) "
                        "VALUES (:ts_code, :symbol, :name, :list_status, :list_date)"
                    ),
                    make_stock_basic_row(ts_code=ts_code, name=name, list_status="L"),
                )
            await conn.execute(
                text(
                    "INSERT INTO trade_cal (cal_date, exchange, is_open, pretrade_date) "
                    "VALUES (:cal_date, :exchange, :is_open, :pretrade_date)"
                ),
                make_trade_cal_rows([datetime.date(2024, 1, 1)])[0],
            )

        count = await quote_dao.get_expected_stock_count("20240101")

        assert count == 2, f"应有 2 只存活股票，实际 {count}"

    @pytest.mark.asyncio
    async def test_get_expected_stock_count_non_trading_day(self, test_engine: AsyncEngine, quote_dao: QuoteDao):
        """
        边界测试：非交易日应返回 0

        场景：trade_cal 中 is_open=0 的日期应返回 0
        """
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO stock_basic (ts_code, symbol, name, list_status, list_date) "
                    "VALUES (:ts_code, :symbol, :name, :list_status, :list_date)"
                ),
                make_stock_basic_row(list_status="L"),
            )
            await conn.execute(
                text(
                    "INSERT INTO trade_cal (cal_date, exchange, is_open, pretrade_date) "
                    "VALUES (:cal_date, :exchange, :is_open, :pretrade_date)"
                ),
                make_trade_cal_rows([datetime.date(2024, 1, 6)], is_open=False)[0],  # 周六
            )

        count = await quote_dao.get_expected_stock_count("20240106")

        assert count == 0, f"非交易日应返回 0，实际 {count}"

    @pytest.mark.asyncio
    async def test_get_bulk_expected_stock_counts(self, test_engine: AsyncEngine, quote_dao: QuoteDao):
        """
        H2 测试：批量获取存活股票数

        场景：2024-01-01 至 2024-01-05 共 5 个交易日，每天应有 2 只存活股票
        """
        dates = [datetime.date(2024, 1, d) for d in range(1, 6)]
        async with test_engine.begin() as conn:
            for ts_code, name in [("000001.SZ", "平安银行"), ("000002.SZ", "万科A")]:
                await conn.execute(
                    text(
                        "INSERT INTO stock_basic (ts_code, symbol, name, list_status, list_date) "
                        "VALUES (:ts_code, :symbol, :name, :list_status, :list_date)"
                    ),
                    make_stock_basic_row(ts_code=ts_code, name=name, list_status="L"),
                )
            for row in make_trade_cal_rows(dates):
                await conn.execute(
                    text(
                        "INSERT INTO trade_cal (cal_date, exchange, is_open, pretrade_date) "
                        "VALUES (:cal_date, :exchange, :is_open, :pretrade_date)"
                    ),
                    row,
                )

        counts = await quote_dao.get_bulk_expected_stock_counts("20240101", "20240105")

        assert set(counts.keys()) == set(dates), f"应返回 5 个交易日，实际 {set(counts.keys())}"
        for d in dates:
            assert counts[d] == 2, f"{d} 应有 2 只存活股票，实际 {counts[d]}"

    @pytest.mark.asyncio
    async def test_get_bulk_table_counts(self, test_engine: AsyncEngine, quote_dao: QuoteDao):
        """
        M3 测试：批量获取表记录数

        场景：daily_quotes 表 2024-01-01 有 2 条记录，2024-01-02 有 1 条记录
        """
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO daily_quotes (ts_code, trade_date, close, pct_chg, vol, amount) "
                    "VALUES (:ts_code, :trade_date, :close, :pct_chg, :vol, :amount)"
                ),
                make_daily_quote_row(ts_code="000001.SZ", trade_date=datetime.date(2024, 1, 1)),
            )
            await conn.execute(
                text(
                    "INSERT INTO daily_quotes (ts_code, trade_date, close, pct_chg, vol, amount) "
                    "VALUES (:ts_code, :trade_date, :close, :pct_chg, :vol, :amount)"
                ),
                make_daily_quote_row(ts_code="000002.SZ", trade_date=datetime.date(2024, 1, 1)),
            )
            await conn.execute(
                text(
                    "INSERT INTO daily_quotes (ts_code, trade_date, close, pct_chg, vol, amount) "
                    "VALUES (:ts_code, :trade_date, :close, :pct_chg, :vol, :amount)"
                ),
                make_daily_quote_row(ts_code="000001.SZ", trade_date=datetime.date(2024, 1, 2)),
            )

        counts = await quote_dao.get_bulk_table_counts(
            "daily_quotes", datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)
        )

        assert counts == {
            datetime.date(2024, 1, 1): 2,
            datetime.date(2024, 1, 2): 1,
        }, f"应返回精确记录数，实际 {counts}"

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores(self, test_engine: AsyncEngine, quote_dao: QuoteDao):
        """
        M2 测试：批量质量评分

        场景：2024-01-01 有 2 只存活股票，daily_quotes 有 2 条记录，
        ratio=1.0 >= quotes_tolerance_ratio(0.95)，score 应为 100。
        显式传入 tables=["daily_quotes"] 避免依赖 TushareClient 能力缓存。
        """
        async with test_engine.begin() as conn:
            for ts_code, name in [("000001.SZ", "平安银行"), ("000002.SZ", "万科A")]:
                await conn.execute(
                    text(
                        "INSERT INTO stock_basic (ts_code, symbol, name, list_status, list_date) "
                        "VALUES (:ts_code, :symbol, :name, :list_status, :list_date)"
                    ),
                    make_stock_basic_row(ts_code=ts_code, name=name, list_status="L"),
                )
            await conn.execute(
                text(
                    "INSERT INTO trade_cal (cal_date, exchange, is_open, pretrade_date) "
                    "VALUES (:cal_date, :exchange, :is_open, :pretrade_date)"
                ),
                make_trade_cal_rows([datetime.date(2024, 1, 1)])[0],
            )
            for ts_code in ["000001.SZ", "000002.SZ"]:
                await conn.execute(
                    text(
                        "INSERT INTO daily_quotes (ts_code, trade_date, close, pct_chg, vol, amount) "
                        "VALUES (:ts_code, :trade_date, :close, :pct_chg, :vol, :amount)"
                    ),
                    make_daily_quote_row(ts_code=ts_code, trade_date=datetime.date(2024, 1, 1)),
                )

        scores = await quote_dao.get_bulk_sync_quality_scores(
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 1),
            tables=["daily_quotes"],
        )

        assert datetime.date(2024, 1, 1) in scores, "应返回 2024-01-01 的质量评分"
        info = scores[datetime.date(2024, 1, 1)]
        assert info["expected_base"] == 2, f"理论股票数应为 2，实际 {info['expected_base']}"
        assert info["tables"]["daily_quotes"]["count"] == 2
        assert info["tables"]["daily_quotes"]["ratio"] == 1.0
        assert info["tables"]["daily_quotes"]["passed"] is True
        assert info["score"] == 100, f"数据完整时 score 应为 100，实际 {info['score']}"
        assert info["issues"] == [], "数据完整时不应有 issues"


class TestQuoteDaoBoundary:
    """边界条件测试"""

    @pytest.fixture
    def mock_engine(self):
        from unittest.mock import MagicMock

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
        边界测试：未来日期处理（trade_cal 无对应记录，is_trade_day=0，返回 0）
        """
        with patch.object(
            quote_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame({"is_trade_day": [0], "cnt": [0]}),
        ):
            count = await quote_dao.get_expected_stock_count("20990101")

            assert count == 0

    @pytest.mark.asyncio
    async def test_invalid_date_format(self, quote_dao):
        """
        边界测试：无效日期格式（_read_db 抛异常，应捕获并返回 0）
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
        边界测试：空日期范围（无数据时返回空 dict）
        """
        with patch.object(
            quote_dao,
            "_read_db_select",
            new_callable=AsyncMock,
            return_value=pd.DataFrame({"trade_date": [], "cnt": []}),
        ):
            counts = await quote_dao.get_bulk_table_counts("daily_quotes", "20990101", "20990105")

            assert counts == {}


class TestQuoteDaoPerformance:
    """性能测试"""

    @pytest.fixture
    def mock_engine(self):
        from unittest.mock import MagicMock

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
        from unittest.mock import MagicMock

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

        P2-3-a fix: 加强断言，验证 score 精确值而非仅 `isinstance(scores, dict)`。
        场景：daily_quotes ratio=1.0，权重 50；daily_indicators ratio=1.0，权重 30；
        moneyflow_daily ratio=1.0，权重 20。加权 score = 100。
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
                return_value={datetime.date(2024, 1, 1): 5000},
            ),
            patch.object(
                quote_dao,
                "get_bulk_expected_stock_counts",
                new_callable=AsyncMock,
                return_value={datetime.date(2024, 1, 1): 5000},
            ),
            patch.object(
                quote_dao,
                "get_field_completeness",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            scores = await quote_dao.get_bulk_sync_quality_scores(
                "20240101",
                "20240101",
                tables=["daily_quotes", "daily_indicators", "moneyflow_daily"],
            )

            assert datetime.date(2024, 1, 1) in scores, "应返回 2024-01-01 的评分"
            info = scores[datetime.date(2024, 1, 1)]
            assert info["score"] == 100, f"全部 ratio=1.0 时 score 应为 100，实际 {info['score']}"
            assert info["issues"] == [], "全部通过时不应有 issues"
            # 验证每个表的 ratio 和 passed
            for table in ["daily_quotes", "daily_indicators", "moneyflow_daily"]:
                assert info["tables"][table]["ratio"] == 1.0
                assert info["tables"][table]["passed"] is True
