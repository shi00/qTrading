# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import re

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd

from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.stock_dao import stock_alive_condition
from data.constants import REVIEW_STATUS_COMPLETED

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


class TestScreenerDaoGetScreeningHistory:
    @pytest.mark.asyncio
    async def test_with_strategy(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_screening_history("test_strategy", limit=10)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_without_strategy(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_screening_history(None, limit=10)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1


class TestScreenerDaoGetHistoryTree:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "run_id": ["r1"],
                    "trade_date": ["20240615"],
                    "strategy_name": ["test"],
                    "cnt": [5],
                }
            )
        )
        result = await dao.get_history_tree(offset=0, limit=30)
        assert isinstance(result, pd.DataFrame)
        assert "run_id" in result.columns
        assert len(result) == 1


class TestScreenerDaoGetHistoryRecords:
    @pytest.mark.asyncio
    async def test_with_run_id(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date=None, run_id="r1")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_strategy_name(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_history_records(trade_date="20240615", strategy_name="test")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1


class TestScreenerDaoGetPendingReviews:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        result = await dao.get_pending_reviews()
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_pending_reviews()
        assert result == []


class TestScreenerDaoGetLearningExamples:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [0.5],
                }
            )
        )
        wins, losses = await dao.get_learning_examples(limit=3)
        assert isinstance(wins, pd.DataFrame)
        assert isinstance(losses, pd.DataFrame)


class TestScreenerDaoGetScreeningData:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_screening_data(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_without_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            side_effect=[
                pd.DataFrame({"max_td": ["20240615"]}),
                pd.DataFrame({"ts_code": ["000001.SZ"]}),
            ]
        )
        result = await dao.get_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_range_query_passes_max_rows_guardrail(self):
        """DAT-10: 两个区间预载查询都必须携带 max_rows 护栏，防止 OOM。"""
        import data.persistence.daos.screener_dao as screener_mod

        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.5]}))
        result = await dao.get_screening_data_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert dao._read_db.call_args.kwargs.get("max_rows") == screener_mod._MAX_SCREENING_RANGE_ROWS

        result_f = await dao.get_fundamental_screening_data_range("20240601", "20240615")
        assert isinstance(result_f, pd.DataFrame)
        assert dao._read_db.call_args.kwargs.get("max_rows") == screener_mod._MAX_SCREENING_RANGE_ROWS
        assert screener_mod._MAX_SCREENING_RANGE_ROWS == 1_500_000


class TestScreenerDaoGetPendingPredictions:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "trade_date": ["20240615"],
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        result = await dao.get_pending_predictions("20240601")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_pending_predictions("20240601")
        assert isinstance(result, pd.DataFrame)


class TestScreenerDaoGetLearningContext:
    @pytest.mark.asyncio
    async def test_win(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [0.5],
                }
            )
        )
        result = await dao.get_learning_context(limit=3, is_win=True)
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_loss(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "alpha": [-0.5],
                }
            )
        )
        result = await dao.get_learning_context(limit=3, is_win=False)
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_as_of_adds_date_filter(self):
        import datetime

        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        as_of_date = datetime.date(2024, 6, 1)
        await dao.get_learning_context(limit=3, is_win=True, as_of=as_of_date)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "trade_date <" in sql
        compiled = stmt.compile()
        assert compiled.params["prediction_result_1"] == "WIN"
        assert compiled.params["review_status_1"] == REVIEW_STATUS_COMPLETED
        assert compiled.params["trade_date_1"] == as_of_date
        assert "LIMIT :PARAM_1" in sql.upper() or "LIMIT 3" in sql.upper()
        assert compiled.params.get("param_1") == 3 or compiled.params.get("limit_1") == 3

    @pytest.mark.asyncio
    async def test_no_as_of_no_date_filter(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "trade_date <" not in sql

    @pytest.mark.asyncio
    async def test_sql_includes_t5_pct_filter(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "t5_pct IS NOT NULL" in sql

    @pytest.mark.asyncio
    async def test_sql_includes_review_status_filter(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "review_status" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_COMPLETED in compiled.params.values()

    @pytest.mark.asyncio
    async def test_as_of_sql_includes_t5_pct_and_review_status(self):
        import datetime

        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        as_of_date = datetime.date(2024, 6, 1)
        await dao.get_learning_context(limit=3, is_win=True, as_of=as_of_date)
        call_args = dao._read_db_select.call_args
        stmt = call_args[0][0]
        sql = str(stmt)
        assert "t5_pct IS NOT NULL" in sql
        assert "review_status" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_COMPLETED in compiled.params.values()


class TestScreenerDaoUpdatePredictionResult:
    @pytest.mark.asyncio
    async def test_basic(self):
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        dao._guarded_begin = mock_guarded_begin

        await dao.update_prediction_result(
            record_id=1,
            pct=5.0,
            label="WIN",
            t1_price=10.0,
            t5_pct=3.0,
            t5_price=10.3,
            index_pct=1.0,
            alpha=4.0,
        )
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_review_status(self):
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        dao._guarded_begin = mock_guarded_begin

        await dao.update_prediction_result(
            record_id=1,
            pct=5.0,
            label="WIN",
            review_status="completed",
        )
        mock_conn.execute.assert_called_once()


class TestScreenerDaoSaveScreeningResults:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=0)
        await dao.save_screening_results([])
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=0)
        await dao.save_screening_results(None)
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_dict_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [
            {
                "run_id": "r1",
                "ts_code": "000001.SZ",
                "name": "Test",
                "trade_date": "20240615",
            }
        ]
        await dao.save_screening_results(records)
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_thinking(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        dao._save_thinking = AsyncMock()
        records = [
            {
                "run_id": "r1",
                "ts_code": "000001.SZ",
                "name": "Test",
                "trade_date": "20240615",
                "thinking": "AI analysis",
            }
        ]
        await dao.save_screening_results(records)
        dao._save_thinking.assert_called_once()


class TestScreenerDaoBuildScreeningSql:
    def test_build_sql_with_close_requirement(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql(require_close=True)
        assert "q.close IS NOT NULL" in sql
        # DAT-01: PIT 存活判定（含 list_status='D' 但 delist_date 晚于 as_of 的分支）
        assert "list_status = 'D' AND b.delist_date IS NOT NULL" in sql

    def test_build_sql_without_close_requirement(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql(require_close=False)
        assert "q.close IS NOT NULL" not in sql
        # DAT-01: PIT 存活判定（含 list_status='D' 但 delist_date 晚于 as_of 的分支）
        assert "list_status = 'D' AND b.delist_date IS NOT NULL" in sql

    def test_build_sql_contains_all_joins(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "LEFT JOIN daily_quotes q" in sql
        assert "LEFT JOIN daily_indicators i" in sql
        assert "LEFT JOIN suspend_d s" in sql
        assert "financial_reports" in sql

    def test_build_sql_contains_is_tradable(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "is_tradable" in sql

    def test_build_sql_contains_financial_subquery(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "ROW_NUMBER() OVER" in sql
        assert "PARTITION BY ts_code" in sql

    def test_build_sql_financial_ordering_by_end_date(self):
        """DAT-03: 最新一期财报口径 end_date DESC, ann_date DESC，禁止回退到 ann_date DESC 优先。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "ORDER BY end_date DESC, ann_date DESC" in sql
        assert "ORDER BY ann_date DESC, end_date DESC" not in sql
        sql_range = dao._build_screening_sql_range()
        assert "ORDER BY f_inner.end_date DESC, f_inner.ann_date DESC" in sql_range

    def test_build_sql_template_placeholder_replaced(self):
        """review03-C7: __CLOSE_COND__ 模板占位符必须被 require_close 完全替换，无残留。"""
        dao = ScreenerDao(MagicMock())
        sql_true = dao._build_screening_sql(require_close=True)
        sql_false = dao._build_screening_sql(require_close=False)
        assert "__CLOSE_COND__" not in sql_true
        assert "__CLOSE_COND__" not in sql_false
        assert "WHERE q.close IS NOT NULL" in sql_true
        # DAT-01: PIT 存活判定（含 list_status='D' 但 delist_date 晚于 as_of 的分支）
        assert "list_status = 'D' AND b.delist_date IS NOT NULL" in sql_false

    def test_build_sql_includes_ann_date_not_null(self):
        """DAT-06: 财务子查询必须显式 ann_date IS NOT NULL，防回退。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "ann_date IS NOT NULL AND ann_date <=" in sql


class TestScreenerDaoSwIndustryJoin:
    """DAT-08③：验证 screener_dao SQL 使用 LEFT JOIN sw_industry_member 拆分为两列。

    拆列语义：申万二级行业经 LATERAL join 计算为 industry_sw_l2（无映射为 NULL）；
    stock_basic.industry 保留 Tushare 原始值输出为 industry_tushare；不再 COALESCE 混列。
    """

    def test_screener_sql_uses_sw_industry(self):
        """_build_screening_sql 必须包含 sw_industry_member JOIN 与双列拆分。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "sw_industry_member" in sql
        assert "AS industry_sw_l2" in sql
        assert "AS industry_tushare" in sql
        assert "COALESCE(m.sw_l2_name, b.industry)" not in sql
        assert "LEFT JOIN LATERAL" in sql

    def test_screener_sql_range_uses_sw_industry(self):
        """_build_screening_sql_range 必须包含 sw_industry_member JOIN 与双列拆分。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "sw_industry_member" in sql
        assert "AS industry_sw_l2" in sql
        assert "AS industry_tushare" in sql
        assert "COALESCE(m.sw_l2_name, b.industry)" not in sql
        assert "LEFT JOIN LATERAL" in sql

    def test_industry_split_no_coalesce(self):
        """双列拆分契约：单日/区间模板均输出 industry_sw_l2 与 industry_tushare 两列，
        且不再存在 COALESCE(m.sw_l2_name, b.industry) 单列混合（评审 m1：含
        fundamental 模板，经 require_close 参数复用的同一静态模板）。"""
        dao = ScreenerDao(MagicMock())
        for sql in (
            dao._build_screening_sql(),
            dao._build_screening_sql_range(),
            dao._build_screening_sql(require_close=False),
            dao._build_screening_sql_range(require_close=False),
        ):
            assert "m.sw_l2_name AS industry_sw_l2" in sql, f"缺少 industry_sw_l2:\n{sql}"
            assert "b.industry AS industry_tushare" in sql, f"缺少 industry_tushare:\n{sql}"
            assert "COALESCE(m.sw_l2_name, b.industry) AS industry" not in sql, f"残留 COALESCE 混列:\n{sql}"

    @staticmethod
    def _extract_sw_industry_lateral(sql: str) -> str:
        """提取 sw_industry_member 的 LATERAL 子查询片段（含 LIMIT 1）。

        以 LEFT JOIN LATERAL (SELECT sw_l2_name FROM sw_industry_member 起头、
        ") m ON TRUE" 收尾，因此只捕获行业子查询，不会误捕财务子查询。
        """
        m = re.search(
            r"LEFT JOIN LATERAL \(\s*SELECT sw_l2_name\s*FROM sw_industry_member.*?LIMIT 1\s*\) m ON TRUE",
            sql,
            re.S,
        )
        assert m is not None, f"SQL 中未找到 sw_industry_member LATERAL 子查询:\n{sql}"
        return m.group(0)

    def test_industry_lateral_deterministic_order(self):
        """DAT-08①: 行业 LATERAL 子查询 LIMIT 1 前必须有 ORDER BY index_code。

        sw_industry_member 主键为 (ts_code, index_code)，同 ts_code 可有多行；
        无 ORDER BY 的 LIMIT 1 返回行随执行计划（VACUUM/ANALYZE/并行度）漂移，
        导致同一股票行业归属在两次运行间变化。单日/区间两模板都必须满足。
        """
        dao = ScreenerDao(MagicMock())
        for sql in (dao._build_screening_sql(), dao._build_screening_sql_range()):
            lateral = self._extract_sw_industry_lateral(sql)
            assert "ORDER BY index_code" in lateral, f"行业 LATERAL 缺少 ORDER BY:\n{lateral}"
            assert "LIMIT 1" in lateral
            assert lateral.index("ORDER BY index_code") < lateral.index("LIMIT 1")


class TestScreenerDaoGetLatestClosedTradeDate:
    @pytest.mark.asyncio
    async def test_returns_date_string(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": ["20240615"]}))
        result = await dao._get_latest_closed_trade_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao._get_latest_closed_trade_date()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_nan(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [float("nan")]}))
        result = await dao._get_latest_closed_trade_date()
        assert result is None


class TestScreenerDaoGetScreeningDataNoTradeDate:
    @pytest.mark.asyncio
    async def test_no_trade_date_and_db_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerDaoGetFundamentalScreeningData:
    @pytest.mark.asyncio
    async def test_with_trade_date(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_fundamental_screening_data(trade_date="20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_without_trade_date_auto_resolve(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            side_effect=[
                pd.DataFrame({"max_td": ["20240615"]}),
                pd.DataFrame({"ts_code": ["000001.SZ"]}),
            ]
        )
        result = await dao.get_fundamental_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns

    @pytest.mark.asyncio
    async def test_no_trade_date_and_db_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_td": [None]}))
        result = await dao.get_fundamental_screening_data()
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerDaoUpdatePredictionResultEdgeCases:
    @pytest.mark.asyncio
    async def test_table_not_in_metadata(self):
        dao = ScreenerDao(MagicMock())
        dao._check_engine = MagicMock()
        with patch("data.persistence.daos.screener_dao.Base") as mock_base:
            mock_base.metadata.tables.get.return_value = None
            await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN")
        mock_base.metadata.tables.get.assert_called_once_with("screening_history")

    @pytest.mark.asyncio
    async def test_engine_not_initialized(self):
        dao = ScreenerDao(MagicMock())
        dao.engine = None
        with patch("data.persistence.daos.screener_dao.sa.update") as mock_update:
            mock_update.return_value.where.return_value.values.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="Engine not initialized"):
                await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN")

    @pytest.mark.asyncio
    async def test_default_status_t1_done_when_no_t5(self):
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        dao._guarded_begin = mock_guarded_begin

        with patch("data.persistence.daos.screener_dao.sa.update") as mock_update:
            mock_update.return_value.where.return_value.values.return_value = MagicMock()
            await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN")
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_prediction_result_conn_path_rechecks_engine_after_wait(self):
        """DAT-01: conn 路径维护事件放行后若引擎已释放，须抛 EngineDisposedError 而非执行 conn。

        覆盖 update_prediction_result 的 conn 直执行分支：入口 _check_engine 通过 →
        阻塞于维护事件 → 期间 mark_disposed(True) → 放行后 _wait_maintenance_guard
        复查必须抛出 EngineDisposedError，且不得调用 conn.execute。
        """
        from data.persistence import engine_provider
        from data.persistence.daos.base_dao import EngineDisposedError

        engine_provider.reset_engine_provider()
        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        engine_provider.set_engine(mock_engine)

        evt = asyncio.Event()
        evt.clear()
        dao._get_maintenance_event = MagicMock(return_value=evt)

        mock_conn = AsyncMock()
        with (
            patch(
                "data.persistence.daos.screener_dao.Base.metadata.tables",
                new=MagicMock(get=MagicMock(return_value=MagicMock())),
            ),
            patch("data.persistence.daos.screener_dao.sa.update") as mock_update,
        ):
            mock_update.return_value.where.return_value.values.return_value = MagicMock()
            task = asyncio.create_task(dao.update_prediction_result(record_id=1, pct=5.0, label="WIN", conn=mock_conn))
            # 让任务停留在维护事件等待处
            await asyncio.sleep(0.05)
            assert not task.done()
            engine_provider.mark_disposed(True)
            evt.set()

        with pytest.raises(EngineDisposedError, match="post-maintenance"):
            await task
        mock_conn.execute.assert_not_called()
        engine_provider.reset_engine_provider()


class TestScreenerDaoSaveScreeningResultsTuple:
    @pytest.mark.asyncio
    async def test_with_tuple_records(self):
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        with patch(
            "data.persistence.daos.screener_dao.get_model_columns",
            return_value=["run_id", "ts_code", "name", "trade_date"],
        ):
            records = [("r1", "000001.SZ", "Test", "20240615")]
            await dao.save_screening_results(records)
            dao._save_upsert.assert_called_once()


class TestScreenerDaoSaveThinking:
    @pytest.mark.asyncio
    async def test_save_thinking_with_matching_ids(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1], "run_id": ["r1"], "ts_code": ["000001.SZ"]}))
        dao._save_upsert = AsyncMock(return_value=1)
        thinking_records = [{"run_id": "r1", "ts_code": "000001.SZ", "thinking": "analysis"}]
        await dao._save_thinking(thinking_records)
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_thinking_no_matching_ids(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1], "run_id": ["r2"], "ts_code": ["000002.SZ"]}))
        dao._save_upsert = AsyncMock(return_value=0)
        thinking_records = [{"run_id": "r1", "ts_code": "000001.SZ", "thinking": "analysis"}]
        await dao._save_thinking(thinking_records)
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_thinking_empty_read(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        dao._save_upsert = AsyncMock(return_value=0)
        thinking_records = [{"run_id": "r1", "ts_code": "000001.SZ", "thinking": "analysis"}]
        await dao._save_thinking(thinking_records)
        dao._save_upsert.assert_not_called()


class TestScreenerDaoBuildScreeningSqlRange:
    def test_build_sql_with_close_requirement(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range(require_close=True)
        assert "q.close IS NOT NULL" in sql
        # DAT-01: PIT 存活判定（含 list_status='D' 但 delist_date 晚于 as_of 的分支）
        assert "list_status = 'D' AND b.delist_date IS NOT NULL" in sql
        assert "trade_cal" in sql

    def test_build_sql_without_close_requirement(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range(require_close=False)
        assert "q.close IS NOT NULL" not in sql
        # DAT-01: PIT 存活判定（含 list_status='D' 但 delist_date 晚于 as_of 的分支）
        assert "list_status = 'D' AND b.delist_date IS NOT NULL" in sql

    def test_build_sql_contains_lateral_join(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "LATERAL" in sql

    def test_build_sql_contains_date_range_params(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "cal_date >= $1" in sql
        assert "cal_date <= $2" in sql

    def test_build_sql_range_includes_ann_date_not_null(self):
        """DAT-06: 区间模板财务子查询必须显式 ann_date IS NOT NULL，防回退。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "f_inner.ann_date IS NOT NULL" in sql

    def test_build_sql_range_template_placeholder_replaced(self):
        """review03-C7: __CLOSE_COND__ 模板占位符必须被 require_close 完全替换，无残留。"""
        dao = ScreenerDao(MagicMock())
        sql_true = dao._build_screening_sql_range(require_close=True)
        sql_false = dao._build_screening_sql_range(require_close=False)
        assert "__CLOSE_COND__" not in sql_true
        assert "__CLOSE_COND__" not in sql_false
        assert "WHERE q.close IS NOT NULL" in sql_true
        # DAT-01: PIT 存活判定（含 list_status='D' 但 delist_date 晚于 as_of 的分支）
        assert "list_status = 'D' AND b.delist_date IS NOT NULL" in sql_false


class TestScreenerDaoPitCondition:
    """DAT-01: 选股主路径 PIT 存活判定（生存者偏差修复）。"""

    @staticmethod
    def _normalize(sql: str) -> str:
        return "".join(sql.split())

    @staticmethod
    def _extract_pit_fragment(sql: str) -> str:
        """提取 SQL 中的 PIT 存活条件片段（((...)) 括号对），供全等断言。"""
        m = re.search(r"\(\(.*?list_status = 'D'.*?\)\)", sql, re.S)
        assert m is not None, f"SQL 中未找到 PIT 存活条件片段:\n{sql}"
        return m.group(0)

    def test_screening_sql_includes_pit_condition(self):
        """两模板渲染后必须包含 list_status='D' 分支（退市后仍有数据）与 delist_date 比较。"""
        dao = ScreenerDao(MagicMock())
        sql_single = dao._build_screening_sql(require_close=True)
        sql_range = dao._build_screening_sql_range(require_close=True)
        assert "list_status = 'D'" in sql_single
        assert "delist_date > $5" in sql_single
        assert "list_status = 'D'" in sql_range
        assert "delist_date > cal.cal_date" in sql_range

    def test_build_sql_no_stock_alive_placeholder_residue(self):
        """DAT-01: __STOCK_ALIVE_CONDITION__ 占位符必须被完全替换，无残留。"""
        dao = ScreenerDao(MagicMock())
        sqls = (
            dao._build_screening_sql(require_close=True),
            dao._build_screening_sql(require_close=False),
            dao._build_screening_sql_range(require_close=True),
            dao._build_screening_sql_range(require_close=False),
        )
        for sql in sqls:
            assert "__STOCK_ALIVE_CONDITION__" not in sql
            assert "__CLOSE_COND__" not in sql

    @pytest.mark.asyncio
    async def test_all_stock_pool_sqls_reference_shared_condition(self):
        """DAT-01 (M2): 四个股票池 SQL 的 PIT 片段必须与 stock_alive_condition() 唯一正本全等。

        逐态断言（alias/as_of 两态 × 单日/区间），规范化空白后全等，防止任何调用方
        内联复制或改写条件（含 quote_dao 原"保持同步"docstring 约定已被证伪的情况）。
        """
        screener = ScreenerDao(MagicMock())
        quote = QuoteDao(MagicMock())

        # screener 单日模板 → alias="b.", as_of="$5"
        single = screener._build_screening_sql(require_close=False)
        assert self._normalize(self._extract_pit_fragment(single)) == self._normalize(
            stock_alive_condition(alias="b.", as_of="$5")
        )

        # screener 区间模板 → alias="b.", as_of="cal.cal_date"
        rng = screener._build_screening_sql_range(require_close=False)
        assert self._normalize(self._extract_pit_fragment(rng)) == self._normalize(
            stock_alive_condition(alias="b.", as_of="cal.cal_date")
        )

        # quote 单日 → alias="", as_of="$1"
        quote._read_db = AsyncMock(return_value=pd.DataFrame({"is_trade_day": [1], "cnt": [10]}))
        await quote.get_expected_stock_count("20240101")
        q_single_sql = quote._read_db.call_args[0][0]
        assert self._normalize(self._extract_pit_fragment(q_single_sql)) == self._normalize(
            stock_alive_condition(alias="", as_of="$1")
        )

        # quote 区间 → alias="", as_of="$1"
        quote._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240102"], "expected_count": [1]}))
        await quote.get_bulk_expected_stock_counts("20240101", "20240105")
        q_rng_sql = quote._read_db.call_args[0][0]
        assert self._normalize(self._extract_pit_fragment(q_rng_sql)) == self._normalize(
            stock_alive_condition(alias="", as_of="$1")
        )


class TestScreenerDaoGetScreeningDataRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_screening_data_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_screening_data_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerDaoGetFundamentalScreeningDataRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_fundamental_screening_data_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "ts_code" in result.columns
        dao._read_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_fundamental_screening_data_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerDaoGetPendingReviewsNone:
    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_pending_reviews()
        assert result == []


class TestScreenerDaoGetHistoryTreeNoneLimit:
    @pytest.mark.asyncio
    async def test_none_limit_defaults_to_30(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "run_id": ["r1"],
                    "trade_date": ["20240615"],
                    "strategy_name": ["test"],
                    "cnt": [5],
                }
            )
        )
        result = await dao.get_history_tree(offset=0, limit=None)
        assert isinstance(result, pd.DataFrame)
        call_args = dao._read_db.call_args
        params = call_args[0][1]
        assert params[0] == 30  # effective_limit defaults to 30 when limit is None
