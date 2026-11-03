"""D05-T02：事务/取消/幂等/部分批写边界证据（单元级）。

本文件只提供**可离线验证**的契约证据：
- ``set_app_state_max``：单语句原子 UPSERT + ``WHERE config_value < 新值``，
  无 Python 侧读-改-写（并发语义仍需真库，见 tests/integration/test_embedded_pg_dao_rw.py）。
- ``mark_empty_days``：复合主键 ``(table_name, trade_date)`` + ``DO NOTHING``，
  重复日期在入库前去重（真实主键冲突行为需真库）。
- ``_save_upsert`` 共享连接：写库异常（含 ``suppress_errors=True``）必须传播，
  且中止后续分块；事务回滚本身由调用方 ``_guarded_begin`` / ``engine.begin`` 负责。
- ``_save_upsert`` 非共享连接：小批量单事务、超阈值每块独立事务（部分批写契约）。
- 非共享长批量中的取消：``CancelledError`` 直接传播（R2），仅已提交分块保留。

上述断言是调用边界/SQL 结构证据，**不冒充真实 PostgreSQL 回滚或并发证据**；
需要真库的项在 integration 用例中通过 ``real_embedded_pg`` fixture 执行，缺失
sidecar 时诚实 skip。

补充契约：``set_app_state_max`` 的数值单调**不等于**最大值之前没有未解决洞——
质量评分豁免只读 ``sync_empty_days`` 的 (表, 日) 精确登记，不读旧单点水位；
既有 ``tests/unit/test_quote_dao.py`` 的 other-date/dense/positive-count 用例与
本文件 integration 的 stale-marker 用例共同守护该缺口。
"""

# pyright: reportPrivateUsage=false
# 测试需访问 DAO 私有方法（_save_upsert/_guarded_begin）与模块级分块常量

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import data.persistence.daos.base_dao as base_dao_module
from data.persistence.app_state_service import set_app_state_max
from data.persistence.daos.sync_dao import SyncDao
from data.persistence.models import SyncEmptyDay, get_model_columns, get_model_pk_columns

pytestmark = pytest.mark.unit

_EMPTY_DAY_COLUMNS = get_model_columns(SyncEmptyDay)
_EMPTY_DAY_PK_COLUMNS = get_model_pk_columns(SyncEmptyDay)


def _make_begin_engine(conn: MagicMock) -> MagicMock:
    """构造只走 ``engine.begin()`` 的假引擎（不提供 ``engine.connect()``）。"""
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=conn)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    return engine


@pytest.fixture
def inline_thread_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 CPU 转换线程池替换为同步内联执行，保留真实记录归一逻辑。"""

    fake_pool = MagicMock()
    fake_pool.run_async = AsyncMock(side_effect=lambda _task_type, fn, frame: fn(frame))
    monkeypatch.setattr(base_dao_module, "ThreadPoolManager", lambda: fake_pool)


def _empty_day_frame(count: int, *, start_day: int = 1) -> pd.DataFrame:
    dates = [datetime.date(2024, 1, start_day + offset) for offset in range(count)]
    return pd.DataFrame([{"table_name": "moneyflow_daily", "trade_date": d} for d in dates])


class TestSetAppStateMaxAtomicity:
    @pytest.mark.asyncio
    async def test_compiles_single_where_guarded_upsert_without_read_back(self) -> None:
        """单调写必须是单条 UPSERT（无 SELECT 预读），WHERE 比较现有值与新值。

        说明：本用例只证明语句结构与调用边界；真实并发覆盖/最终值由 integration
        真库用例守护（本沙箱 skip）。
        """
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)

        await set_app_state_max(engine, "sync_attempted_upto:moneyflow_daily", "20250910")

        # 无 Python 侧读-改-写：只开一个写事务、只执行一条语句
        engine.connect.assert_not_called()
        assert engine.begin.call_count == 1
        assert mock_conn.execute.await_count == 1

        executed_stmt = mock_conn.execute.await_args.args[0]
        sql = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert sql.count("INSERT INTO app_state") == 1
        assert "ON CONFLICT (config_key) DO UPDATE" in sql
        # 严格大于守卫：仅当现有值更小才覆盖，较小值写入为零行更新
        assert "WHERE app_state.config_value < '20250910'" in sql
        assert "SELECT" not in sql


class TestMarkEmptyDaysIdempotencyContract:
    @pytest.mark.asyncio
    async def test_duplicate_dates_collapse_and_compile_do_nothing_on_composite_pk(
        self,
        inline_thread_pool: None,
    ) -> None:
        """重复 (table, date) 先按主键去重，SQL 走 ON CONFLICT DO NOTHING。"""
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)
        dao = SyncDao(engine)

        trade_date = datetime.date(2024, 9, 10)
        written = await dao.mark_empty_days("moneyflow_daily", [trade_date, trade_date])

        assert written == 1, "同键去重后仅登记 1 行，返回值应为 1"
        assert mock_conn.execute.await_count == 1
        executed_stmt, records = mock_conn.execute.await_args.args
        sql = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ON CONFLICT (table_name, trade_date) DO NOTHING" in sql
        assert len(records) == 1
        assert records[0]["table_name"] == "moneyflow_daily"
        assert records[0]["trade_date"] == trade_date


class TestSaveUpsertTransactionBoundaries:
    @pytest.mark.asyncio
    async def test_shared_conn_propagates_error_even_with_suppress_errors_and_stops_chunks(
        self,
        inline_thread_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """共享事务连接下异常必须传播（P1-3），且失败分块后不再执行后续分块。

        这证明调用方事务不会被“吞错后继续写”污染；真正的回滚由事务持有者完成，
        由 integration ``test_shared_conn_upsert_failure_rolls_back_whole_transaction``
        在真库验证（本沙箱 skip）。
        """
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)
        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=[None, RuntimeError("chunk 2 failed")])
        dao = SyncDao(MagicMock())

        with pytest.raises(RuntimeError, match="chunk 2 failed"):
            await dao._save_upsert(
                _empty_day_frame(5),
                "sync_empty_days",
                _EMPTY_DAY_COLUMNS,
                _EMPTY_DAY_PK_COLUMNS,
                suppress_errors=True,
                conn=conn,
            )

        # 只尝试前两块；第三块不再执行（避免在已损坏事务上继续写）
        assert conn.execute.await_count == 2
        first_chunk = conn.execute.await_args_list[0].args[1]
        second_chunk = conn.execute.await_args_list[1].args[1]
        assert [record["trade_date"] for record in first_chunk] == [
            datetime.date(2024, 1, 1),
            datetime.date(2024, 1, 2),
        ]
        assert [record["trade_date"] for record in second_chunk] == [
            datetime.date(2024, 1, 3),
            datetime.date(2024, 1, 4),
        ]
        assert dao.engine.begin.call_count == 0, "共享连接不得自行开启事务"

    @pytest.mark.asyncio
    async def test_long_batch_opens_one_transaction_per_chunk(
        self,
        inline_thread_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超过长事务阈值时每块独立事务（部分批写契约，依赖 UPSERT 幂等重跑）。"""
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)
        monkeypatch.setattr(base_dao_module, "_LONG_TX_ROW_THRESHOLD", 2)
        conn = MagicMock()
        conn.execute = AsyncMock()
        engine = _make_begin_engine(conn)
        dao = SyncDao(engine)

        written = await dao._save_upsert(
            _empty_day_frame(5),
            "sync_empty_days",
            _EMPTY_DAY_COLUMNS,
            _EMPTY_DAY_PK_COLUMNS,
        )

        assert written == 5
        # 5 行 / 每块 2 行 => 3 个独立事务
        assert engine.begin.call_count == 3
        assert conn.execute.await_count == 3
        assert [len(call.args[1]) for call in conn.execute.await_args_list] == [2, 2, 1]

    @pytest.mark.asyncio
    async def test_long_batch_mid_failure_keeps_only_committed_chunks(
        self,
        inline_thread_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """长批量中段失败：前一块已提交，异常传播、后续块不执行。"""
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)
        monkeypatch.setattr(base_dao_module, "_LONG_TX_ROW_THRESHOLD", 2)
        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=[None, RuntimeError("chunk 2 failed")])
        engine = _make_begin_engine(conn)
        dao = SyncDao(engine)

        with pytest.raises(RuntimeError, match="chunk 2 failed"):
            await dao._save_upsert(
                _empty_day_frame(5),
                "sync_empty_days",
                _EMPTY_DAY_COLUMNS,
                _EMPTY_DAY_PK_COLUMNS,
            )

        assert engine.begin.call_count == 2
        assert conn.execute.await_count == 2
        assert len(conn.execute.await_args_list[0].args[1]) == 2
        assert len(conn.execute.await_args_list[1].args[1]) == 2

    @pytest.mark.asyncio
    async def test_cancellation_in_long_batch_propagates_and_stops_chunks(
        self,
        inline_thread_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """取消信号（R2）不得被吞没：已提交块保留，后续块不执行。"""
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)
        monkeypatch.setattr(base_dao_module, "_LONG_TX_ROW_THRESHOLD", 2)
        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        engine = _make_begin_engine(conn)
        dao = SyncDao(engine)

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await dao._save_upsert(
                _empty_day_frame(5),
                "sync_empty_days",
                _EMPTY_DAY_COLUMNS,
                _EMPTY_DAY_PK_COLUMNS,
            )
        assert isinstance(exc_info.value, asyncio.CancelledError)

        # 第一块独立事务完成；第二块触发取消；第三块未开始
        assert engine.begin.call_count == 2
        assert conn.execute.await_count == 2
        assert len(conn.execute.await_args_list[0].args[1]) == 2

    @pytest.mark.asyncio
    async def test_small_batch_uses_single_transaction(
        self,
        inline_thread_pool: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未超阈值的小批量在单个事务内落库（避免部分可见）。"""
        monkeypatch.setattr(base_dao_module, "_UPSERT_CHUNK_SIZE", 2)
        conn = MagicMock()
        conn.execute = AsyncMock()
        engine = _make_begin_engine(conn)
        dao = SyncDao(engine)

        written = await dao._save_upsert(
            _empty_day_frame(2),
            "sync_empty_days",
            _EMPTY_DAY_COLUMNS,
            _EMPTY_DAY_PK_COLUMNS,
        )

        assert written == 2
        assert engine.begin.call_count == 1
        assert conn.execute.await_count == 1
        assert len(conn.execute.await_args_list[0].args[1]) == 2
