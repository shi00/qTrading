# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import re

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd

from data.persistence.daos.screener_dao import ScreenerDao, _derive_screening_from_fundamental
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.stock_dao import stock_alive_condition
from data.constants import REVIEW_STATUS_COMPLETED, REVIEW_STATUS_PENDING, REVIEW_STATUS_T1_DONE

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

    @pytest.mark.asyncio
    async def test_groups_by_trade_date_strategy_and_run(self):
        """RV-03: 历史树按 (trade_date, strategy_name, run_id) 聚合——同 (date, strategy)
        的多次运行分别成行（「今天跑了 3 次」分别可见），cnt 为该 run 股票数。"""
        dao = ScreenerDao(MagicMock())
        df = pd.DataFrame(
            {
                "trade_date": ["20240615", "20240615", "20240615"],
                "strategy_name": ["strat_a", "strat_a", "strat_b"],
                "cnt": [3, 5, 7],
                "run_id": ["r1", "r2", "r3"],
            }
        )
        dao._read_db = AsyncMock(return_value=df)
        result = await dao.get_history_tree(offset=0, limit=30)
        assert len(result) == 3  # strat_a 两个 run 分别成行
        assert set(result["strategy_name"]) == {"strat_a", "strat_b"}

    @pytest.mark.asyncio
    async def test_window_interval_renders_constant(self):
        """UX-05 回归：窗口 INTERVAL 必须插值为受控常量，而非字面花括号占位。

        窗口天数由 REVIEW_STATS_WINDOW_DAYS 模块常量经 f-string 插值到 SQL；
        若漏加 f 前缀，'{REVIEW_STATS_WINDOW_DAYS}' 以字面量进入 pg 报非法 interval，
        导致 get_history_tree 返回空表（本用例在修复前必失败）。
        """
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_history_tree(offset=0, limit=30)
        sql = dao._read_db.call_args.args[0]
        assert "REVIEW_STATS_WINDOW_DAYS}" not in sql  # 占位符必须已插值
        assert "INTERVAL '180 days'" in sql


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

    @pytest.mark.asyncio
    async def test_ai_score_desc_nulls_last(self):
        """BIZ-01: 历史记录按 ai_score DESC 排序，无 AI 记录（NULL）靠后（Postgres 默认 NULLS FIRST 会顶到最前）。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_history_records(trade_date="20240615")
        sql = str(dao._read_db_select.call_args[0][0])
        assert "NULLS LAST" in sql.upper()


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

    @pytest.mark.asyncio
    async def test_sql_no_ai_score_filter(self):
        """BIZ-01: 复盘池查询不再以 ``ai_score > 0`` 过滤，纯数学/无 AI 记录可进入。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_pending_predictions("20240601")
        sql = str(dao._read_db.call_args[0][0])
        assert "ai_score > 0" not in sql
        assert "review_status" in sql
        assert "$1" in sql or "$2" in sql


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
    async def test_sql_includes_ai_score_not_null(self):
        """BIZ-01: 学习样例过滤 ``ai_score IS NOT NULL``，避免非 AI 记录污染 few-shot。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        sql = str(dao._read_db_select.call_args[0][0])
        assert "ai_score IS NOT NULL" in sql

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

    @pytest.mark.asyncio
    async def test_strategy_name_filter_added(self):
        """D4-M3: 传入 strategy_name 时 WHERE 增加同策略过滤。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True, strategy_name="strategy_oversold")
        stmt = dao._read_db_select.call_args[0][0]
        compiled = stmt.compile()
        assert "strategy_oversold" in compiled.params.values()

    @pytest.mark.asyncio
    async def test_no_strategy_name_no_filter(self):
        """D4-M3: strategy_name 为 None 时不追加策略过滤（向后兼容）。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        sql = str(dao._read_db_select.call_args[0][0])
        # 全表 SQL 不应包含 strategy_name 相等比较
        compiled = dao._read_db_select.call_args[0][0].compile()
        assert "strategy_name =" not in sql and not any(
            isinstance(v, str) and "strategy" in str(v).lower() for v in compiled.params.values()
        )

    @pytest.mark.asyncio
    async def test_latest_run_dedup_distinct_on_id_desc(self):
        """RV-03: 学习样本按 (trade_date, strategy_name, ts_code) 去重取最近运行（id DESC），
        避免 append-only 下同日同股多 run 重复取样挤占 few-shot 多样性。"""
        from sqlalchemy.dialects import postgresql

        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_learning_context(limit=3, is_win=True)
        stmt = dao._read_db_select.call_args[0][0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        # 子查询（latest_learning）含 DISTINCT ON + id DESC（最近一次运行去重）
        assert "DISTINCT ON" in sql
        assert "screening_history.id DESC" in sql
        assert "screening_history.run_id DESC" not in sql

    @pytest.mark.asyncio
    async def test_learning_context_stats_aggregates(self):
        """D4-M3: stats 聚合返回总数/win/loss/中位数。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "total": [100],
                    "win_cnt": [30],
                    "loss_cnt": [20],
                    "alpha_mean": [0.5],
                    "alpha_median": [0.2],
                }
            )
        )
        result = await dao.get_learning_context_stats(strategy_name="strategy_oversold")
        assert result is not None
        assert result["total"] == 100
        assert result["win_cnt"] == 30
        assert result["loss_cnt"] == 20
        assert result["alpha_median"] == 0.2

    @pytest.mark.asyncio
    async def test_learning_context_stats_empty_returns_none(self):
        """D4-M3: 无样本时 stats 返回 None 而非异常。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_learning_context_stats()
        assert result is None


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

    @pytest.mark.asyncio
    async def test_benchmark_code_written_when_provided(self):
        """D2-5：传入 benchmark_code 时，UPDATE 应包含 benchmark_code 列。"""
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
            benchmark_code="000985.CSI",
        )
        stmt = mock_conn.execute.call_args.args[0]
        assert "benchmark_code" in str(stmt)

    @pytest.mark.asyncio
    async def test_benchmark_code_not_overwritten_when_absent(self):
        """D2-5：未传入 benchmark_code（如 T+5 回填路径）时不应触碰该列，避免用 NULL 覆写已落库基准。"""
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

        await dao.update_prediction_result(record_id=1, pct=5.0, label="WIN", t5_pct=3.0)
        stmt = mock_conn.execute.call_args.args[0]
        assert "benchmark_code" not in str(stmt)


class TestScreenerDaoRv04LabelDecouple:
    """RV-04: 数值/标签解耦的 DAO 侧契约——finalize 幂等守卫、backfill 状态语义、A/B 分池。"""

    @staticmethod
    def _make_dao_with_conn():
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
        return dao, mock_conn

    @pytest.mark.asyncio
    async def test_finalize_prediction_label_has_alpha_null_guard(self):
        """T3: finalize 的 UPDATE WHERE 带 alpha IS NULL 幂等守卫——已定稿标签不被覆盖。"""
        dao, mock_conn = self._make_dao_with_conn()
        await dao.finalize_prediction_label(
            record_id=1, label="WIN", index_pct=1.0, benchmark_code="000300.SH", alpha=4.0
        )
        mock_conn.execute.assert_called_once()
        sql = str(mock_conn.execute.call_args.args[0])
        assert "alpha IS NULL" in sql

    @pytest.mark.asyncio
    async def test_finalize_prediction_label_writes_completed(self):
        """T3: finalize 推进 review_status 为 COMPLETED，并写 label/index_pct/benchmark_code/alpha。"""
        dao, mock_conn = self._make_dao_with_conn()
        await dao.finalize_prediction_label(
            record_id=1, label="WIN", index_pct=1.0, benchmark_code="000300.SH", alpha=4.0
        )
        compiled = mock_conn.execute.call_args.args[0].compile()
        assert REVIEW_STATUS_COMPLETED in compiled.params.values()
        assert "prediction_result" in str(mock_conn.execute.call_args.args[0])

    @pytest.mark.asyncio
    async def test_backfill_t5_label_none_keeps_t1_done(self):
        """T5: 数值-only 回填（label=None，基准缺失解耦写入）status 停留 T1_DONE，
        不误判 COMPLETED——否则记录脱离 pending 池与补标签池，标签永久无人定稿。"""
        dao, mock_conn = self._make_dao_with_conn()
        await dao.backfill_t5_prediction(record_id=1, t5_pct=5.0, t5_price=10.5, label=None)
        mock_conn.execute.assert_called_once()
        compiled = mock_conn.execute.call_args.args[0].compile()
        assert REVIEW_STATUS_T1_DONE in compiled.params.values()
        assert REVIEW_STATUS_COMPLETED not in compiled.params.values()
        # label=None 不写 prediction_result（不在写入集，保持既有占位不覆盖）
        assert "prediction_result" not in str(mock_conn.execute.call_args.args[0])

    @pytest.mark.asyncio
    async def test_backfill_t5_label_present_writes_completed(self):
        """T5: label 非 None（同步定稿）→ status COMPLETED + prediction_result 写入。"""
        dao, mock_conn = self._make_dao_with_conn()
        await dao.backfill_t5_prediction(record_id=1, t5_pct=5.0, t5_price=10.5, label="WIN", index_pct=1.0, alpha=4.0)
        mock_conn.execute.assert_called_once()
        compiled = mock_conn.execute.call_args.args[0].compile()
        assert REVIEW_STATUS_COMPLETED in compiled.params.values()
        assert "prediction_result" in str(mock_conn.execute.call_args.args[0])

    @pytest.mark.asyncio
    async def test_unlabeled_predictions_sql_contract(self):
        """T7: B 类池（数值已齐、标签未定稿）过滤契约——T1_DONE + t5_pct IS NOT NULL +
        alpha IS NULL，独立 LIMIT 不与 A 类池挤占。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_unlabeled_predictions(limit=500)
        stmt = dao._read_db_select.call_args[0][0]
        sql = str(stmt)
        assert "t5_pct IS NOT NULL" in sql
        assert "alpha IS NULL" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_T1_DONE in compiled.params.values()

    @pytest.mark.asyncio
    async def test_unfilled_horizon_predictions_sql_contract(self):
        """T7: A 类池过滤契约——T1_DONE + t5_pct IS NULL，与 B 类池互补不重叠。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_unfilled_horizon_predictions(limit=500)
        stmt = dao._read_db_select.call_args[0][0]
        sql = str(stmt)
        assert "t5_pct IS NULL" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_T1_DONE in compiled.params.values()

    @pytest.mark.asyncio
    async def test_finalize_prediction_label_with_conn_executes_on_conn(self):
        """批量定稿路径（conn 由调用方事务持有）：语句直接在传入 conn 上执行，
        不再自建事务（与 _batch_finalize_labels 的单事务语义配套）。"""
        dao, mock_conn = self._make_dao_with_conn()
        await dao.finalize_prediction_label(
            record_id=7, label="WIN", index_pct=1.0, benchmark_code="000300.SH", alpha=4.0, conn=mock_conn
        )
        mock_conn.execute.assert_called_once()
        sql = str(mock_conn.execute.call_args.args[0])
        assert "alpha IS NULL" in sql

    @pytest.mark.asyncio
    async def test_finalize_prediction_label_tx_failure_logs_not_raises(self):
        """自建事务失败（非 disposed）→ 记 warning 不上抛（补标签通道次日重试兜底）。"""
        from contextlib import asynccontextmanager

        dao, _ = self._make_dao_with_conn()

        @asynccontextmanager
        async def failing_begin(conn=None):
            raise RuntimeError("tx failed")
            yield

        dao._guarded_begin = failing_begin
        # 不抛：单条失败由次日 backfill job 兜底重试
        await dao.finalize_prediction_label(
            record_id=1, label="WIN", index_pct=1.0, benchmark_code="000300.SH", alpha=4.0
        )

    @pytest.mark.asyncio
    async def test_finalize_prediction_label_disposed_raises(self):
        """R5: 自建事务遇 EngineDisposedError 上抛（disposed 引擎上不再静默吞没）。"""
        from contextlib import asynccontextmanager

        from data.persistence.daos.base_dao import EngineDisposedError

        dao, mock_conn = self._make_dao_with_conn()

        @asynccontextmanager
        async def disposed_begin(conn=None):
            raise EngineDisposedError("engine disposed")
            yield

        dao._guarded_begin = disposed_begin
        with pytest.raises(EngineDisposedError, match="engine disposed"):
            await dao.finalize_prediction_label(
                record_id=1, label="WIN", index_pct=1.0, benchmark_code="000300.SH", alpha=4.0
            )
        # disposed 引擎上 UPDATE 语句未执行
        mock_conn.execute.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_uses_three_field_pk_columns(self):
        """RV-03: save_screening_results 应以 (trade_date, strategy_name, ts_code, run_id) 为主键传参。"""
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [
            {
                "run_id": "r1",
                "strategy_name": "strategy_ai_active_name",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "name": "Test",
            }
        ]
        await dao.save_screening_results(records)
        dao._save_upsert.assert_called_once()
        call_kwargs = dao._save_upsert.call_args.kwargs
        assert call_kwargs["pk_columns"] == ["trade_date", "strategy_name", "ts_code", "run_id"]

    @pytest.mark.asyncio
    async def test_batch_internal_duplicate_overwrite_keeps_last(self):
        """RV-03: append-only——批内同 (date,strategy,code,run_id) 预去重保留最新；
        不同 run_id 各自保留（不再覆盖）。"""
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [
            {
                "run_id": "r1",
                "strategy_name": "strategy_ai_active_name",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "name": "First",
            },
            {
                "run_id": "r2",
                "strategy_name": "strategy_ai_active_name",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "name": "Latest",
            },
            # 同 (4 键) 重复行：预去重保留最新
            {
                "run_id": "r2",
                "strategy_name": "strategy_ai_active_name",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "name": "Latest2",
            },
        ]
        await dao.save_screening_results(records)
        df = dao._save_upsert.call_args.kwargs["df"]
        # 不同 run_id 独立保留（2 行），同 4 键去重（r2 保留 Latest2）
        assert len(df) == 2
        by_run = {row.run_id: row.name for row in df.itertuples()}
        assert by_run == {"r1": "First", "r2": "Latest2"}


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

    def test_build_sql_contains_sc03_columns(self):
        """SC-03: 模板必须提供 n_income（绝对盈利下限）与 gpm_prev（上一报告期毛利率，增长质量判据）。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "n_income" in sql
        assert "gpm_prev" in sql
        sql_range = dao._build_screening_sql_range()
        assert "n_income" in sql_range
        assert "gpm_prev" in sql_range


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
        assert "COALESCE(m.l2_name, b.industry)" not in sql
        assert "LEFT JOIN LATERAL" in sql

    def test_screener_sql_range_uses_sw_industry(self):
        """_build_screening_sql_range 必须包含 sw_industry_member JOIN 与双列拆分。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "sw_industry_member" in sql
        assert "AS industry_sw_l2" in sql
        assert "AS industry_tushare" in sql
        assert "COALESCE(m.l2_name, b.industry)" not in sql
        assert "LEFT JOIN LATERAL" in sql

    def test_industry_split_no_coalesce(self):
        """双列拆分契约：单日/区间模板均输出 industry_sw_l2 与 industry_tushare 两列，
        且不再存在 COALESCE(m.l2_name, b.industry) 单列混合（评审 m1：含
        fundamental 模板，经 require_close 参数复用的同一静态模板）。"""
        dao = ScreenerDao(MagicMock())
        for sql in (
            dao._build_screening_sql(),
            dao._build_screening_sql_range(),
            dao._build_screening_sql(require_close=False),
            dao._build_screening_sql_range(require_close=False),
        ):
            assert "m.l2_name AS industry_sw_l2" in sql, f"缺少 industry_sw_l2:\n{sql}"
            assert "b.industry AS industry_tushare" in sql, f"缺少 industry_tushare:\n{sql}"
            assert "COALESCE(m.l2_name, b.industry) AS industry" not in sql, f"残留 COALESCE 混列:\n{sql}"

    @staticmethod
    def _extract_sw_industry_lateral(sql: str) -> str:
        """提取 sw_industry_member 的 LATERAL 子查询片段（含 LIMIT 1）。

        以 LEFT JOIN LATERAL (SELECT l2_name FROM sw_industry_member 起头、
        ") m ON TRUE" 收尾，因此只捕获行业子查询，不会误捕财务子查询。
        """
        m = re.search(
            r"LEFT JOIN LATERAL \(\s*SELECT l2_name\s*FROM sw_industry_member.*?LIMIT 1\s*\) m ON TRUE",
            sql,
            re.S,
        )
        assert m is not None, f"SQL 中未找到 sw_industry_member LATERAL 子查询:\n{sql}"
        return m.group(0)

    def test_industry_lateral_deterministic_order(self):
        """DAT-08① + DATA-04 L2: 行业 LATERAL 子查询 LIMIT 1 前必须有 ORDER BY l2_code。

        sw_industry_member 主键为 (ts_code, l3_code, in_date)，同 ts_code 当前有效行
        (out_date IS NULL) 由 LATERAL 的 WHERE 限定为唯一归属；ORDER BY l2_code
        保证无 ORDER BY 时不随执行计划漂移。单日/区间两模板都必须满足。
        """
        dao = ScreenerDao(MagicMock())
        for sql in (dao._build_screening_sql(), dao._build_screening_sql_range()):
            lateral = self._extract_sw_industry_lateral(sql)
            assert "ORDER BY l2_code" in lateral, f"行业 LATERAL 缺少 ORDER BY:\n{lateral}"
            assert "LIMIT 1" in lateral
            assert lateral.index("ORDER BY l2_code") < lateral.index("LIMIT 1")

    def test_industry_lateral_asof_condition(self):
        """DATA-04 L2: 行业 LATERAL 子查询必须按 as-of 时点过滤，消除回测行业前视偏差。

        单日模板 as-of 为 $5（trade_date，与 stock_alive_condition 复用同一参数）；
        区间模板 as-of 为 cal.cal_date（逐交易日，与财务子查询 ann_date <= cal.cal_date
        同模式）。as-of 语义为「纳入日 <= as_of 且（未剔除或剔除日 > as_of）」，
        回测历史时点取当时生效的行业归属，而非当前快照（裸 out_date IS NULL）。
        """
        dao = ScreenerDao(MagicMock())
        sql_daily = dao._build_screening_sql()
        lateral_daily = self._extract_sw_industry_lateral(sql_daily)
        assert "in_date <= $5" in lateral_daily, f"单日模板缺少 in_date as-of 条件:\n{lateral_daily}"
        assert "(out_date IS NULL OR out_date > $5)" in lateral_daily, (
            f"单日模板缺少 out_date as-of 条件:\n{lateral_daily}"
        )

        sql_range = dao._build_screening_sql_range()
        lateral_range = self._extract_sw_industry_lateral(sql_range)
        assert "in_date <= cal.cal_date" in lateral_range, f"区间模板缺少 in_date as-of 条件:\n{lateral_range}"
        assert "(out_date IS NULL OR out_date > cal.cal_date)" in lateral_range, (
            f"区间模板缺少 out_date as-of 条件:\n{lateral_range}"
        )

        # 行业子查询必须保留 out_date 的双态判断（IS NULL 或 > as_of），不得退回裸当前快照
        for lateral in (lateral_daily, lateral_range):
            assert lateral.count("out_date") == 2, f"行业 LATERAL 应含 2 处 out_date 判断:\n{lateral}"
            assert "AND out_date IS NULL" not in lateral, f"行业 LATERAL 不得退回裸当前快照:\n{lateral}"


class TestScreenerDaoStockNameHistory:
    """DS-02（ST 时点还原链路）：name-history LATERAL JOIN + is_st 派生列。

    - name 列经 COALESCE(nh.name, b.name) 按 as-of 时点还原历史名称，无历史回退当前名称；
    - is_st 派生列（UPPER(name) LIKE '%ST%'，覆盖 *ST/S*ST）供数据层行过滤/as-of 涨跌停判定；
    - ST 排除不做在 DAO 层（SQL 无 WHERE 过滤，留存完整数据供回测 data_provider 直读）。
    """

    def test_daily_sql_uses_stock_name_history(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "stock_name_history" in sql
        assert "COALESCE(nh.name, b.name) AS name" in sql
        assert "AS is_st" in sql
        assert "UPPER(COALESCE(nh.name, b.name)) LIKE '%ST%'" in sql

    def test_daily_name_lateral_asof_uses_param5(self):
        """单日版 name-history LATERAL 以 $5 为 as-of，且先于 suspend_d JOIN。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        assert "start_date <= $5" in sql
        assert "(end_date IS NULL OR end_date > $5)" in sql
        assert sql.index("LEFT JOIN LATERAL (") < sql.index("LEFT JOIN suspend_d s")

    def test_range_sql_uses_stock_name_history(self):
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "stock_name_history" in sql
        assert "COALESCE(nh.name, b.name) AS name" in sql
        assert "AS is_st" in sql
        assert "UPPER(COALESCE(nh.name, b.name)) LIKE '%ST%'" in sql

    def test_range_name_lateral_asof_uses_cal_date(self):
        """区间版 name-history LATERAL 按 cal.cal_date 逐交易日 as-of，先于 suspend_d JOIN。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        assert "start_date <= cal.cal_date" in sql
        assert "(end_date IS NULL OR end_date > cal.cal_date)" in sql
        assert sql.index("LEFT JOIN LATERAL (") < sql.index("LEFT JOIN suspend_d s")

    def test_st_exclude_not_in_sql(self):
        """DS-02: ST 排除在数据层行过滤而非 SQL WHERE，两份模板不留 ST 过滤条件。"""
        dao = ScreenerDao(MagicMock())
        for sql in (dao._build_screening_sql(), dao._build_screening_sql_range()):
            assert "WHERE is_st" not in sql
            assert "__CLOSE_COND__" not in sql  # 模板占位符已被替换

    def test_parameter_count_unchanged(self):
        """DS-02: 两份模板参数位不变（单日仍 $1-$6，区间仍 $1-$2）。"""
        daily_sql = ScreenerDao(MagicMock())._build_screening_sql()
        range_sql = ScreenerDao(MagicMock())._build_screening_sql_range()
        assert daily_sql.count("$1") and max(int(x) for x in re.findall(r"\$(\d)", daily_sql)) == 6
        assert max(int(x) for x in re.findall(r"\$(\d)", range_sql)) == 2


class TestScreenerDaoExcludeSt:
    """SC-01: 筛选 SQL 层 as-of ST 判定（stock_name_history LATERAL + is_st 派生列）。

    合并 DS-02 后，单日版 as-of 统一以 $5（恒等于 trade_date，与 m 行业子查询及
    stock_alive_condition 的 as_of 自洽）；is_st 派生列采用 UPPER(COALESCE(...)) 写法。
    """

    @staticmethod
    def _extract_name_lateral(sql: str) -> str:
        """提取 stock_name_history 的 LATERAL 子查询片段（含 LIMIT 1）。"""
        m = re.search(
            r"LEFT JOIN LATERAL \(\s*SELECT name\s*FROM stock_name_history.*?LIMIT 1\s*\) nh ON TRUE",
            sql,
            re.S,
        )
        assert m is not None, f"SQL 中未找到 stock_name_history LATERAL 子查询:\n{sql}"
        return m.group(0)

    def test_single_day_template_has_asof_name_lateral(self):
        """SC-01: 单日模板必须经 stock_name_history 做 as-of 名称还原（as-of 为 $5）。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql()
        lateral = self._extract_name_lateral(sql)
        assert "start_date <= $5" in lateral, f"单日模板缺少 start_date as-of 条件:\n{lateral}"
        assert "(end_date IS NULL OR end_date > $5)" in lateral, f"单日模板缺少 end_date as-of 条件:\n{lateral}"

    def test_range_template_has_asof_name_lateral(self):
        """SC-01: 区间模板必须逐交易日做 as-of 名称还原（as-of 为 cal.cal_date）。"""
        dao = ScreenerDao(MagicMock())
        sql = dao._build_screening_sql_range()
        lateral = self._extract_name_lateral(sql)
        assert "start_date <= cal.cal_date" in lateral, f"区间模板缺少 start_date as-of 条件:\n{lateral}"
        assert "(end_date IS NULL OR end_date > cal.cal_date)" in lateral, (
            f"区间模板缺少 end_date as-of 条件:\n{lateral}"
        )

    def test_name_lateral_deterministic_order(self):
        """SC-01: LATERAL LIMIT 1 前必须有 ORDER BY start_date DESC（防执行计划漂移）。"""
        dao = ScreenerDao(MagicMock())
        for sql in (dao._build_screening_sql(), dao._build_screening_sql_range()):
            lateral = self._extract_name_lateral(sql)
            assert "ORDER BY start_date DESC" in lateral, f"名称 LATERAL 缺少 ORDER BY:\n{lateral}"
            assert "LIMIT 1" in lateral
            assert lateral.index("ORDER BY start_date DESC") < lateral.index("LIMIT 1")

    def test_name_column_coalesce_asof_fallback(self):
        """SC-01: name 列 COALESCE(nh.name, b.name)——名称表空/无记录时回退当前名称。"""
        dao = ScreenerDao(MagicMock())
        for sql in (dao._build_screening_sql(), dao._build_screening_sql_range()):
            assert "COALESCE(nh.name, b.name) AS name" in sql

    def test_is_st_upper_derived_column(self):
        """is_st 派生列采用 UPPER(COALESCE(nh.name, b.name)) LIKE '%ST%'。

        UPPER 覆盖 *ST/S*ST，与 _get_limit_pct 语义一致；COALESCE 双回退：有 as-of 记录
        用历史名判定，无记录回退当前名称，空表不误判全市场非 ST。
        """
        dao = ScreenerDao(MagicMock())
        for sql in (dao._build_screening_sql(), dao._build_screening_sql_range()):
            assert "CASE WHEN UPPER(COALESCE(nh.name, b.name)) LIKE '%ST%' THEN TRUE ELSE FALSE END AS is_st" in sql


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
            return_value=["run_id", "ts_code", "name", "trade_date", "strategy_name"],
        ):
            # LIFE-03: tuple 形式记录须包含新主键列 strategy_name（drop_duplicates 依赖三列）。
            records = [("r1", "000001.SZ", "Test", "20240615", "test_strategy")]
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


class TestScreenerDaoGetUnfilledHorizonPredictions:
    """D2-4: 回填候选查询 —— 只含 T1_DONE 且 t5_pct 为 NULL，升序 + LIMIT 封顶。"""

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1, 2],
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240601", "20240610"],
                }
            )
        )
        result = await dao.get_unfilled_horizon_predictions()
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_unfilled_horizon_predictions()
        assert result == []

    @pytest.mark.asyncio
    async def test_sql_encoding(self):
        """SQL 必须限定 review_status='T1_DONE' + t5_pct IS NULL，且升序、LIMIT 封顶。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_unfilled_horizon_predictions(limit=2000)
        stmt = dao._read_db_select.call_args[0][0]
        sql = str(stmt)
        assert "review_status" in sql
        assert "t5_pct IS NULL" in sql
        assert "ORDER BY" in sql
        assert "trade_date" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_T1_DONE in compiled.params.values()
        assert compiled.params.get("param_1") == 2000 or compiled.params.get("limit_1") == 2000


class TestScreenerDaoGetUnfilledT1Predictions:
    """BIZ-03: T+1 回填候选查询 —— 只含 PENDING/NULL 且 t1_pct IS NULL，升序 + LIMIT 封顶。"""

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1, 2],
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240601", "20240610"],
                }
            )
        )
        result = await dao.get_unfilled_t1_predictions()
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_unfilled_t1_predictions()
        assert result == []

    @pytest.mark.asyncio
    async def test_sql_encoding(self):
        """SQL 必须限定 review_status IN (PENDING, NULL) + t1_pct IS NULL，且升序、LIMIT 封顶。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_unfilled_t1_predictions(limit=2000)
        stmt = dao._read_db_select.call_args[0][0]
        sql = str(stmt)
        assert "review_status" in sql
        assert "t1_pct IS NULL" in sql
        assert "ORDER BY" in sql
        assert "trade_date" in sql
        compiled = stmt.compile()
        assert REVIEW_STATUS_PENDING in compiled.params.values()
        assert compiled.params.get("param_1") == 2000 or compiled.params.get("limit_1") == 2000


class TestScreenerDaoBackfillT5Prediction:
    """D2-4: 幂等回填 —— WHERE 带 t5_pct IS NULL；RV-04 起 status 按标签成熟度推进。"""

    @staticmethod
    def _make_dao_with_conn():
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
        return dao, mock_conn

    @pytest.mark.asyncio
    async def test_basic_update(self):
        dao, mock_conn = self._make_dao_with_conn()
        await dao.backfill_t5_prediction(1, 3.0, 10.3)
        stmt = mock_conn.execute.call_args.args[0]
        sql = str(stmt)
        assert "t5_pct" in sql
        assert "t5_price" in sql
        assert "t5_pct IS NULL" in sql
        compiled = stmt.compile()
        # RV-04: label=None（数值-only 解耦写入）→ 停留 T1_DONE 留在补标签通道，
        # 不再无条件 COMPLETED（否则记录脱离补标签池，标签永久无人定稿）。
        assert compiled.params.get("review_status") == REVIEW_STATUS_T1_DONE
        assert compiled.params.get("t5_pct") == 3.0

    @pytest.mark.asyncio
    async def test_in_conn_batch(self):
        """支持在外部 conn（engine.begin() 事务）中批量执行，不自行开新事务。"""
        dao, mock_conn = self._make_dao_with_conn()
        await dao.backfill_t5_prediction(1, 3.0, 10.3, conn=mock_conn)
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_table_not_in_metadata(self):
        dao, _ = self._make_dao_with_conn()
        with patch("data.persistence.daos.screener_dao.Base") as mock_base:
            mock_base.metadata.tables.get.return_value = None
            await dao.backfill_t5_prediction(1, 3.0, 10.3)
        mock_base.metadata.tables.get.assert_called_once_with("screening_history")
        dao._check_engine.assert_called_once()  # noqa: weak-assertion 无参调用，仅确认引擎前置检查已执行

    @pytest.mark.asyncio
    async def test_engine_disposed_raised(self):
        """_guarded_begin 抛 EngineDisposedError → 必须上抛（R5），不可被 except Exception 吞没。"""
        from data.persistence.daos.base_dao import EngineDisposedError as EDE
        from contextlib import asynccontextmanager

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        @asynccontextmanager
        async def raise_on_begin(conn=None):
            raise EDE("engine disposed")
            yield None  # pragma: no cover 确保除非被异常跳过否则不产生额外分支

        dao._guarded_begin = raise_on_begin
        with pytest.raises(EDE, match="engine disposed"):
            await dao.backfill_t5_prediction(1, 3.0, 10.3)

    @pytest.mark.asyncio
    async def test_other_exception_warned_not_raised(self, caplog):
        """_guarded_begin 抛普通异常 → 记 WARNING 降级，不向上抛。"""
        from contextlib import asynccontextmanager
        import logging

        mock_engine = MagicMock()
        dao = ScreenerDao(mock_engine)
        dao._check_engine = MagicMock()
        dao._get_maintenance_event = MagicMock(return_value=MagicMock(wait=AsyncMock()))

        @asynccontextmanager
        async def raise_on_begin(conn=None):
            raise RuntimeError("db down")
            yield None  # pragma: no cover 确保除非被异常跳过否则不产生额外分支

        dao._guarded_begin = raise_on_begin
        with caplog.at_level(logging.WARNING, logger="data.persistence.daos.screener_dao"):
            await dao.backfill_t5_prediction(1, 3.0, 10.3)
        assert any("Failed to backfill T+5" in r.message for r in caplog.records)


class TestScreenerDaoGetStrategyReviewStats:
    """UX-05: get_strategy_review_stats 覆盖语义去重 SQL 结构化断言。

    schema 唯一键 (trade_date, strategy_name, ts_code) 禁止新数据同 key 多快照共存，
    DISTINCT ON ... ORDER BY run_id DESC 取最新快照仅为迁移 0024 前旧唯一键
    (run_id, ts_code) 遗留数据的防御逻辑（生命周期：当前 schema 覆盖语义下不可经 INSERT
    造出多快照，须以结构化单测锁定该防御 SQL，防止回归，四审 L1）。
    """

    @pytest.mark.asyncio
    async def test_latest_snapshot_dedup_distinct_on_and_run_id_desc(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_strategy_review_stats()
        stmt = dao._read_db_select.call_args.args[0]
        from sqlalchemy.dialects import postgresql

        sql = str(stmt.compile(dialect=postgresql.dialect()))
        # RV-03: DISTINCT ON 三列（(trade_date, strategy_name, ts_code) 最新运行口径）
        assert "DISTINCT ON" in sql
        assert ("screening_history.trade_date, screening_history.strategy_name, screening_history.ts_code") in sql
        # RV-03: 快照确定性改按 id DESC（id 单调 = 最近一次运行；run_id 随机 hex 非时间序）
        assert "screening_history.id DESC" in sql
        assert "screening_history.run_id DESC" not in sql

    @pytest.mark.asyncio
    async def test_windows_days_bindparam_and_grouping(self):
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_strategy_review_stats()
        stmt = dao._read_db_select.call_args.args[0]
        from sqlalchemy.dialects import postgresql

        sql = str(stmt.compile(dialect=postgresql.dialect()))
        # 窗口走 bindparam 而非字符串拼接常量（R4 参数化）
        assert "window_days" in sql
        assert "GROUP BY" in sql
        assert "screening_history.benchmark_code" in sql


class TestScreenerDaoGetAiAttributionStats:
    """BIZ-04 第二层: get_ai_attribution_stats 聚合 SQL 构建 (mock 读通道)。"""

    @pytest.mark.asyncio
    async def test_builds_ai_attribution_aggregation_sql(self) -> None:
        """构建 DISTINCT ON 最新快照 + ai_score 判定 has_ai + WIN/LOSS 计数聚合。"""
        dao = ScreenerDao(MagicMock())
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
        await dao.get_ai_attribution_stats()

        stmt = dao._read_db_select.call_args[0][0]
        sql = str(stmt)
        # DISTINCT ON 最新快照覆盖语义在 latest 子查询内 (外层 str 不内联), 其正确性由集成测试对真实库验证。
        assert "window_days" in sql  # R4 参数化绑定, 无拼接注入
        assert "win_cnt" in sql
        assert "loss_cnt" in sql
        assert "group by" in sql.lower()
        dao._read_db_select.assert_awaited_once()


class TestScreenerDaoScreeningDerivation:
    """DS-05: screening_data 由 fundamental_screening_data 派生（同模板唯差 close 条件）。

    语义：SQL ``q.close IS NOT NULL`` ↔ pandas ``close.notna()``；缺 close 列/None/空表
    原样返回（防御路径，真实模板恒含 close 列）。
    """

    def _fund_df(self, closes):
        return pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1, len(closes) + 1)],
                "close": list(closes),
            }
        )

    def test_all_close_non_null_returns_all(self):
        """close 全非 NULL → 派生结果与输入行集相同。"""
        fund = self._fund_df([10.0, 20.0, 30.0])
        out = _derive_screening_from_fundamental(fund)
        assert len(out) == 3
        assert list(out["ts_code"]) == ["000001.SZ", "000002.SZ", "000003.SZ"]

    def test_null_close_rows_removed_and_index_reset(self):
        """部分 close 为 NULL（无报价/停牌）→ 剔除该行且 index 连续。"""
        fund = self._fund_df([10.0, None, 30.0])
        out = _derive_screening_from_fundamental(fund)
        assert list(out["ts_code"]) == ["000001.SZ", "000003.SZ"]
        assert list(out.index) == [0, 1]  # reset_index(drop=True)

    def test_all_null_close_returns_empty(self):
        fund = self._fund_df([None, None])
        out = _derive_screening_from_fundamental(fund)
        assert out.empty

    def test_missing_close_column_returns_original(self):
        """缺 close 列（防御路径）→ 原样返回，不做不可预期裁剪。"""
        fund = pd.DataFrame({"ts_code": ["000001.SZ"]})
        out = _derive_screening_from_fundamental(fund)
        assert out is fund

    def test_none_and_empty_passthrough(self):
        assert _derive_screening_from_fundamental(None) is None
        empty = pd.DataFrame()
        assert _derive_screening_from_fundamental(empty) is empty


class TestRv03AppendOnlySemantics:
    """RV-03: 研究记录 append-only 语义——同日多次运行互不覆盖、统计取最新运行。"""

    @pytest.mark.asyncio
    async def test_same_day_two_runs_both_kept(self):
        """同日不同 run_id 的两批写入各自落库（不再互斥覆盖）。"""
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [
            {
                "run_id": "run_a",
                "strategy_name": "strat",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "params_snapshot": {"pe": 20},
                "name": "A",
            },
            {
                "run_id": "run_b",
                "strategy_name": "strat",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "params_snapshot": {"pe": 15},
                "name": "B",
            },
        ]
        await dao.save_screening_results(records)
        df = dao._save_upsert.call_args.kwargs["df"]
        pk = dao._save_upsert.call_args.kwargs["pk_columns"]
        # 两 run 各自独立成行（append-only）
        assert len(df) == 2
        assert set(df["run_id"]) == {"run_a", "run_b"}
        # 唯一键含 run_id
        assert pk == ["trade_date", "strategy_name", "ts_code", "run_id"]

    @pytest.mark.asyncio
    async def test_thread_pending_labels_not_reset(self):
        """append-only：不再为覆盖语义显式重置既有行——review_status 仅对新行置 PENDING。"""
        dao = ScreenerDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        records = [
            {
                "run_id": "run_c",
                "strategy_name": "strat",
                "ts_code": "000001.SZ",
                "trade_date": "20240615",
                "name": "C",
            }
        ]
        await dao.save_screening_results(records)
        df = dao._save_upsert.call_args.kwargs["df"]
        assert (df["review_status"] == REVIEW_STATUS_PENDING).all()  # 新行置 PENDING 正常
