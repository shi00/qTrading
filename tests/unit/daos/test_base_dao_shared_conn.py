"""共享事务连接场景的 DAO 回归测试。

P1-4 背景：
    HolderDao._calculate_holder_changes 在 ``async with self._guarded_begin() as tx_conn:``
    内调用 ``self.chunked_in_write(self._write_db, sql_template, ts_codes, conn=tx_conn)``。
    原实现 ``_chunked_execute`` 不区分共享 conn 场景，统一走 ``Semaphore(8)+asyncio.gather``
    并发分支，违反 asyncpg「单连接不可并发执行语句」语义，触发
    ``InterfaceError: another operation is in progress``，异常被吞为 warning，
    导致 ``holder_num_change``/``holder_num_ratio`` 永久静默不更新。

修复：
    ``_chunked_execute`` 新增 ``conn: typing.Any = None`` 显式形参，
    ``conn is not None`` 时强制串行 for 循环执行（与 ``_save_upsert`` conn 分支同型）。

D05-003 背景（锁环窗口）：
    已准入事务内若再等待维护事件，维护方的 DDL（DROP SCHEMA，需 ACCESS EXCLUSIVE）
    在等该事务结束、该事务在等 DDL 之后才 set 的事件 → 环。修复：事务内语句
    （``conn is not None``）不再等待维护事件（仅保留 R5 复查），维护侧新增有界排空屏障。

说明：本文件全部用例使用可控替身（MagicMock/AsyncMock），不连接真实数据库，
也不执行真实 DDL；DDL 权限降级路径由 ``metadata.drop_all`` 替身覆盖。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.cache.cache_manager import CacheManager
from data.persistence import engine_provider
from data.persistence.daos.base_dao import BaseDao, DatabaseQueryError, EngineDisposedError

pytestmark = pytest.mark.unit


class TestChunkedExecuteSharedConnSerial:
    """P1-4: 当 ``conn`` 显式传入 ``_chunked_execute`` 时，分块必须严格串行执行。

    asyncpg 禁止单连接并发执行语句——并发会触发
    ``InterfaceError: another operation is in progress``。
    串行分支与 ``_save_upsert`` conn 分支同型。
    """

    @pytest.mark.asyncio
    async def test_shared_conn_executes_chunks_serially(self):
        """conn 传入时，分块严格串行执行，无任何并发。"""
        active = 0
        max_active = 0
        lock = asyncio.Lock()
        events: list[tuple[str, int]] = []

        async def mock_db_fn(sql, params, **kwargs):
            nonlocal active, max_active
            chunk_idx = params[0]
            async with lock:
                active += 1
                max_active = max(max_active, active)
                events.append(("start", chunk_idx))
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
                events.append(("end", chunk_idx))
            return chunk_idx

        mock_conn = AsyncMock()
        results = await BaseDao._chunked_execute(
            mock_db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            [1, 2, 3],
            chunk_size=1,
            conn=mock_conn,
        )

        assert max_active == 1, f"检测到并发执行，max_active={max_active}"
        assert results == [1, 2, 3]
        # 严格串行：每块 start 前一块必须 end
        assert events == [
            ("start", 1),
            ("end", 1),
            ("start", 2),
            ("end", 2),
            ("start", 3),
            ("end", 3),
        ], f"分块未严格串行：{events}"

    @pytest.mark.asyncio
    async def test_shared_conn_forwards_conn_to_db_fn(self):
        """conn 显式传入时，转发到 db_fn 的 kwargs。"""
        captured_conn = None

        async def mock_db_fn(sql, params, **kwargs):
            nonlocal captured_conn
            captured_conn = kwargs.get("conn")
            return 1

        mock_conn = AsyncMock()
        await BaseDao._chunked_execute(
            mock_db_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders})",
            ["A"],
            conn=mock_conn,
        )
        assert captured_conn is mock_conn

    @pytest.mark.asyncio
    async def test_shared_conn_preserves_chunk_order(self):
        """串行路径下结果保持分块顺序（非执行顺序）。"""
        call_idx = 0

        async def mock_db_fn(sql, params, **kwargs):
            nonlocal call_idx
            call_idx += 1
            return (call_idx, params[0])

        mock_conn = AsyncMock()
        results = await BaseDao._chunked_execute(
            mock_db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            [1, 2, 3, 4, 5, 6],
            chunk_size=2,
            conn=mock_conn,
        )
        # 3 个分块，每块首个元素：1, 3, 5
        assert results == [(1, 1), (2, 3), (3, 5)]

    @pytest.mark.asyncio
    async def test_shared_conn_empty_values_returns_empty(self):
        """空 values 时返回 []，不调用 db_fn。"""
        mock_db_fn = AsyncMock()
        mock_conn = AsyncMock()
        results = await BaseDao._chunked_execute(
            mock_db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            [],
            conn=mock_conn,
        )
        assert results == []
        mock_db_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_shared_conn_extra_kwargs_forwarded(self):
        """串行路径下其它 kwargs（如 suppress_errors）正常转发到 db_fn。"""
        captured_kwargs = None

        async def mock_db_fn(sql, params, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return 1

        mock_conn = AsyncMock()
        await BaseDao._chunked_execute(
            mock_db_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders})",
            ["A"],
            conn=mock_conn,
            suppress_errors=True,
        )
        assert captured_kwargs is not None
        assert captured_kwargs.get("conn") is mock_conn
        assert captured_kwargs.get("suppress_errors") is True

    @pytest.mark.asyncio
    async def test_shared_conn_large_batch_stays_serial(self):
        """大批量分块（模拟 holder 全量同步 5500 codes / 11 块）保持串行。"""
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def mock_db_fn(sql, params, **kwargs):
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.005)
            async with lock:
                active -= 1
            return len(params)

        mock_conn = AsyncMock()
        # 5500 codes / 500 = 11 块——与 holder_dao 全量同步规模一致
        values = [f"{i:06d}.SH" for i in range(5500)]
        results = await BaseDao._chunked_execute(
            mock_db_fn,
            "UPDATE stk_holdernumber SET holder_num_change = 0 WHERE ts_code IN ({placeholders})",
            values,
            chunk_size=500,
            conn=mock_conn,
        )
        assert max_active == 1, (
            f"大批量共享 conn 检测到并发，max_active={max_active}——"
            "asyncpg 会触发 InterfaceError: another operation is in progress"
        )
        assert len(results) == 11
        assert sum(results) == 5500

    @pytest.mark.asyncio
    async def test_shared_conn_callable_template_with_start_idx(self):
        """串行路径下 callable sql_template 的 3 参重载（含 start_idx）正常工作。"""
        passed_start_idx_values: list[int] = []

        def sql_template_3(placeholders, chunk_len, start_idx):
            passed_start_idx_values.append(start_idx)
            return f"SELECT * FROM t WHERE id IN ({placeholders}) LIMIT ${start_idx + chunk_len}"

        async def mock_db_fn(sql, params, **kwargs):
            return sql

        mock_conn = AsyncMock()
        await BaseDao._chunked_execute(
            mock_db_fn,
            sql_template_3,
            ["A", "B", "C"],
            chunk_size=1,
            extra_params=["prefix1"],
            conn=mock_conn,
        )
        # 每个分块是独立查询，actual_start_idx 一次计算后所有分块共用
        # （与既有并发路径行为一致：每个分块占位符从 actual_start_idx 起算）
        assert passed_start_idx_values == [2, 2, 2]

    @pytest.mark.asyncio
    async def test_shared_conn_params_fn_assembled(self):
        """串行路径下 params_fn 追加参数到分块末尾。"""
        captured_params: list[list] = []

        async def mock_db_fn(sql, params, **kwargs):
            captured_params.append(params)
            return 1

        mock_conn = AsyncMock()

        def params_fn(chunk):
            return ["extra_suffix"]

        await BaseDao._chunked_execute(
            mock_db_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders})",
            ["A", "B"],
            chunk_size=1,
            params_fn=params_fn,
            conn=mock_conn,
        )
        assert captured_params == [["A", "extra_suffix"], ["B", "extra_suffix"]]


class TestChunkedExecuteNoConnStaysConcurrent:
    """P1-4 回归保护：无 conn 时仍走 Semaphore+gather 并发分支。"""

    @pytest.mark.asyncio
    async def test_no_conn_still_uses_concurrent_path(self):
        """无 conn 时仍走并发分支（回归保护，避免误把全部路径改串行）。"""
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def mock_db_fn(sql, params, **kwargs):
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return len(params)

        # pool_size=10 → max_concurrent = 8
        with patch(
            "utils.config_handler.ConfigHandler.get_db_connection_pool_size",
            return_value=10,
        ):
            values = list(range(20))
            await BaseDao._chunked_execute(
                mock_db_fn,
                "SELECT * FROM t WHERE id IN ({placeholders})",
                values,
                chunk_size=2,
            )
        assert max_active > 1, "无 conn 时未走并发分支——所有路径都被串行了"


class TestHolderDaoPatternViaChunkedInWrite:
    """端到端：模拟 HolderDao._calculate_holder_changes 调用链。

    ``chunked_in_write(write_db_fn, sql, values, conn=tx_conn)`` 必须串行
    调用 ``_write_db(..., conn=tx_conn)``，避免 asyncpg 单连接并发冲突。
    """

    @pytest.mark.asyncio
    async def test_holder_dao_pattern_executes_serially_on_shared_tx_conn(self):
        """模拟 HolderDao 全量同步路径，验证共享 tx_conn 上无并发。"""
        active = 0
        max_active = 0
        lock = asyncio.Lock()
        call_count = 0

        mock_tx_conn = AsyncMock()

        async def mock_exec_driver_sql(sql, params=None):
            nonlocal active, max_active, call_count
            call_count += 1
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            async with lock:
                active -= 1

        mock_tx_conn.exec_driver_sql = mock_exec_driver_sql
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            # 1100 codes / 500 = 3 块
            ts_codes = [f"{i:06d}.SH" for i in range(1100)]
            total = await dao.chunked_in_write(
                dao._write_db,
                "UPDATE stk_holdernumber SET holder_num_change = 0 WHERE ts_code IN ({placeholders})",
                ts_codes,
                conn=mock_tx_conn,
                chunk_size=500,
            )

        assert max_active == 1, (
            f"_write_db 在共享 tx_conn 上并发执行，max_active={max_active}——"
            "HolderDao._calculate_holder_changes 会触发 asyncpg InterfaceError"
        )
        # _write_db 每块返回 1，3 块 → total = 3
        assert total == 3
        # 验证 exec_driver_sql 被串行调用 3 次（3 个分块）
        assert call_count == 3


class TestChunkedExecuteSharedConnErrorPropagation:
    """P2-1: 共享 conn 串行路径下异常必须原样传播，不可被静默吞没。

    覆盖三类关键异常：
      - DatabaseQueryError：suppress_errors=False 抛出的预期异常（R5/R9 一致性）
      - EngineDisposedError：R5 僵尸引擎保护
      - asyncio.CancelledError：R2 取消传播红线回归保护
    """

    @pytest.mark.asyncio
    async def test_shared_conn_propagates_database_query_error(self):
        """DatabaseQueryError 必须原样传播，调用方需感知失败。"""

        async def mock_db_fn(sql, params, **kwargs):
            raise DatabaseQueryError("simulated write failure")

        mock_conn = AsyncMock()
        with pytest.raises(DatabaseQueryError):
            await BaseDao._chunked_execute(
                mock_db_fn,
                "UPDATE t SET x=1 WHERE id IN ({placeholders})",
                ["A", "B"],
                chunk_size=1,
                conn=mock_conn,
            )

    @pytest.mark.asyncio
    async def test_shared_conn_propagates_engine_disposed_error(self):
        """EngineDisposedError 必须原样传播（R5 僵尸引擎保护）。"""

        async def mock_db_fn(sql, params, **kwargs):
            raise EngineDisposedError("engine disposed")

        mock_conn = AsyncMock()
        with pytest.raises(EngineDisposedError):
            await BaseDao._chunked_execute(
                mock_db_fn,
                "UPDATE t SET x=1 WHERE id IN ({placeholders})",
                ["A", "B"],
                chunk_size=1,
                conn=mock_conn,
            )

    @pytest.mark.asyncio
    async def test_shared_conn_propagates_cancelled_error(self):
        """asyncio.CancelledError 必须原样传播（R2 红线回归保护）。"""

        async def mock_db_fn(sql, params, **kwargs):
            raise asyncio.CancelledError()

        mock_conn = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await BaseDao._chunked_execute(
                mock_db_fn,
                "UPDATE t SET x=1 WHERE id IN ({placeholders})",
                ["A", "B"],
                chunk_size=1,
                conn=mock_conn,
            )


_UPDATE_TEMPLATE = "UPDATE t SET x=1 WHERE ts_code IN ({placeholders})"


def _make_engine(conn):
    """构造 engine 替身：``engine.begin()`` 产出固定 conn（不连真实库）。"""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.sync_engine = MagicMock()
    engine.begin = MagicMock(return_value=ctx)
    return engine


class TestMaintenanceAdmissionBarrier:
    """D05-003：已准入事务内不得等待维护事件，维护侧用有界排空屏障取代隐式互等。"""

    @pytest.mark.asyncio
    async def test_multi_chunk_write_inside_admitted_transaction_completes(self):
        """首块已持锁 + 并发维护（事件已 clear、DDL 等事务结束）必须有界完成。

        生产形态：``_guarded_begin`` + ``chunked_in_write(conn=tx_conn)``（holder 全量
        计算，>500 只股票即多块）。未修复时第 2 块在事务内等维护事件，维护事件要等本
        事务结束才 set → 环；修复后事务内语句跳过事件等待，写入完成、维护放行。
        """
        maint_event = BaseDao._get_maintenance_event()
        writer_done = asyncio.Event()
        exec_count = 0
        conn = AsyncMock()

        async def _exec_driver_sql(sql, params=None):
            nonlocal exec_count
            exec_count += 1
            if exec_count == 1:
                # 首块 UPDATE 已持锁：此刻维护方 clear 事件并进入「等事务结束」阶段
                maint_event.clear()
                await asyncio.sleep(0.01)
            return None

        conn.exec_driver_sql = _exec_driver_sql
        dao = BaseDao(_make_engine(conn))

        async def _writer() -> None:
            async with dao._guarded_begin() as tx_conn:
                await dao.chunked_in_write(dao._write_db, _UPDATE_TEMPLATE, ["A", "B", "C"], conn=tx_conn, chunk_size=1)
            writer_done.set()

        async def _maintenance() -> None:
            # 模拟 clear_all_cache：事件已 clear，DROP SCHEMA 需等写事务释放锁
            await writer_done.wait()
            maint_event.set()

        maintenance_task = asyncio.create_task(_maintenance())
        try:
            await asyncio.wait_for(asyncio.gather(_writer(), maintenance_task), timeout=3)
        finally:
            maint_event.set()

        assert exec_count == 3
        assert writer_done.is_set() is True

    @pytest.mark.asyncio
    async def test_shared_conn_write_does_not_wait_event(self):
        """共享事务连接上的单条写入在事件 clear 时也必须立即完成（不等待）。"""
        maint_event = BaseDao._get_maintenance_event()
        conn = AsyncMock()
        dao = BaseDao(_make_engine(conn))
        maint_event.clear()
        try:
            await asyncio.wait_for(dao._write_db("SELECT 1", conn=conn), timeout=1)
        finally:
            maint_event.set()
        conn.exec_driver_sql.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_shared_write_still_waits_for_maintenance(self):
        """回归保护：无共享 conn 的写入仍必须在准入门（维护事件）上等待。"""
        maint_event = BaseDao._get_maintenance_event()
        conn = AsyncMock()
        dao = BaseDao(_make_engine(conn))
        maint_event.clear()
        try:
            write_task = asyncio.create_task(dao._write_db("SELECT 1"))
            await asyncio.sleep(0.01)
            assert write_task.done() is False, "无共享 conn 的写入未等待维护事件，准入门被绕过"
            maint_event.set()
            await asyncio.wait_for(write_task, timeout=1)
        finally:
            maint_event.set()
        conn.exec_driver_sql.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_admission_count_visible_to_drain(self):
        """准入事务未结束前 drain 返回 False；结束后返回 True。"""
        conn = AsyncMock()
        dao = BaseDao(_make_engine(conn))

        async with dao._guarded_begin():
            assert BaseDao.admitted_transaction_count() == 1
            assert await BaseDao.drain_admitted_transactions(timeout=0.01) is False

        assert BaseDao.admitted_transaction_count() == 0
        assert await BaseDao.drain_admitted_transactions(timeout=0.01) is True

    @pytest.mark.asyncio
    async def test_drain_returns_true_when_idle(self):
        """无已准入事务时 drain 立即返回 True（不消耗超时窗口）。"""
        assert await BaseDao.drain_admitted_transactions(timeout=0.01) is True

    @pytest.mark.asyncio
    async def test_cancelled_transaction_releases_admission(self):
        """事务内取消：CancelledError 传播（R2），且准入计数不泄漏。"""
        maint_event = BaseDao._get_maintenance_event()
        conn = AsyncMock()
        calls = 0

        async def _exec_driver_sql(sql, params=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError()
            return None

        conn.exec_driver_sql = _exec_driver_sql
        dao = BaseDao(_make_engine(conn))

        with pytest.raises(asyncio.CancelledError):
            async with dao._guarded_begin() as tx_conn:
                await dao.chunked_in_write(dao._write_db, _UPDATE_TEMPLATE, ["A", "B"], conn=tx_conn, chunk_size=1)

        assert maint_event.is_set() is True
        assert BaseDao.admitted_transaction_count() == 0
        assert await BaseDao.drain_admitted_transactions(timeout=0.01) is True

    @pytest.mark.asyncio
    async def test_shared_conn_write_rejects_disposed_engine_without_waiting(self):
        """R5 保留：已准入事务内引擎被释放时必须立即明确拒绝，而非等待维护事件。"""
        maint_event = BaseDao._get_maintenance_event()
        conn = AsyncMock()
        engine = _make_engine(conn)
        dao = BaseDao(engine)
        engine_provider.set_engine(engine)
        engine_provider.mark_disposed(True)
        maint_event.clear()
        try:
            with pytest.raises(EngineDisposedError):
                await asyncio.wait_for(dao._write_db("SELECT 1", conn=conn), timeout=1)
        finally:
            maint_event.set()
            engine_provider.reset_engine_provider()
        conn.exec_driver_sql.assert_not_awaited()


def _make_cache_manager(engine):
    """构造 CacheManager 替身（__new__ 绕过单例初始化，不连真实库）。"""
    mgr = CacheManager.__new__(CacheManager)
    mgr._initialized = True
    mgr._schema_initialized = False
    mgr._disposed = False
    mgr.engine = engine
    return mgr


class TestMaintenanceClearAdmission:
    """D05-003：clear/close/init 维护入口与准入屏障的交互（全部为可控替身）。"""

    @staticmethod
    def _make_ddl_conn(order: list[str], cascade_error: Exception | None = None):
        conn = AsyncMock()
        savepoint_ctx = AsyncMock()
        savepoint_ctx.__aexit__ = AsyncMock(return_value=None)
        conn.begin_nested = MagicMock(return_value=savepoint_ctx)
        calls = {"n": 0}

        async def _execute(stmt, params=None):
            order.append("ddl")
            calls["n"] += 1
            # 仅首条 DROP SCHEMA 失败，触发降级；降级路径的后续语句必须能继续执行
            if cascade_error is not None and calls["n"] == 1:
                raise cascade_error
            return None

        conn.execute = AsyncMock(side_effect=_execute)
        conn.run_sync = AsyncMock()
        return conn

    @pytest.mark.asyncio
    async def test_clear_all_cache_drains_admitted_transaction_before_ddl(self):
        """DDL 必须发生在已准入事务结束之后（排空屏障前置）。"""
        order: list[str] = []
        ddl_conn = self._make_ddl_conn(order)
        mgr = _make_cache_manager(_make_engine(ddl_conn))

        writer_conn = AsyncMock()
        dao = BaseDao(_make_engine(writer_conn))
        admitted = asyncio.Event()

        async def _writer() -> None:
            async with dao._guarded_begin():
                admitted.set()
                await asyncio.sleep(0.05)
            order.append("tx_closed")

        with patch.object(mgr, "init_db", new_callable=AsyncMock):
            writer_task = asyncio.create_task(_writer())
            await asyncio.wait_for(admitted.wait(), timeout=1)
            await asyncio.wait_for(asyncio.gather(writer_task, mgr.clear_all_cache()), timeout=3)

        assert order[0] == "tx_closed", f"DDL 先于已准入事务结束：{order}"
        assert "ddl" in order
        assert BaseDao.admitted_transaction_count() == 0

    @pytest.mark.asyncio
    async def test_clear_all_cache_proceeds_after_drain_timeout(self):
        """排空超时后维护仍有界继续（不悬挂），且不阻塞写事务自行结束。"""
        order: list[str] = []
        ddl_conn = self._make_ddl_conn(order)
        mgr = _make_cache_manager(_make_engine(ddl_conn))

        writer_conn = AsyncMock()
        dao = BaseDao(_make_engine(writer_conn))
        admitted = asyncio.Event()

        async def _writer() -> None:
            async with dao._guarded_begin():
                admitted.set()
                await asyncio.sleep(0.2)
            order.append("tx_closed")

        with (
            patch.object(mgr, "init_db", new_callable=AsyncMock),
            patch("data.persistence.daos.base_dao._MAINTENANCE_DRAIN_TIMEOUT_SEC", 0.02),
        ):
            writer_task = asyncio.create_task(_writer())
            await asyncio.wait_for(admitted.wait(), timeout=1)
            await asyncio.wait_for(asyncio.gather(writer_task, mgr.clear_all_cache()), timeout=3)

        # 超时窗口逼近后 DDL 先执行，写事务随后自然结束——两方都不悬挂
        assert order.index("ddl") < order.index("tx_closed")
        assert BaseDao.admitted_transaction_count() == 0

    @pytest.mark.asyncio
    async def test_clear_all_cache_ddl_privilege_downgrade_still_bounded(self):
        """DDL 权限降级（CASCADE 失败 → metadata.drop_all 替身）在排空后仍完成。"""
        order: list[str] = []
        ddl_conn = self._make_ddl_conn(order, cascade_error=RuntimeError("insufficient privilege"))
        mgr = _make_cache_manager(_make_engine(ddl_conn))

        writer_conn = AsyncMock()
        dao = BaseDao(_make_engine(writer_conn))
        admitted = asyncio.Event()

        async def _writer() -> None:
            async with dao._guarded_begin():
                admitted.set()
                await asyncio.sleep(0.02)
            order.append("tx_closed")

        with patch.object(mgr, "init_db", new_callable=AsyncMock):
            writer_task = asyncio.create_task(_writer())
            await asyncio.wait_for(admitted.wait(), timeout=1)
            await asyncio.wait_for(asyncio.gather(writer_task, mgr.clear_all_cache()), timeout=3)

        ddl_conn.run_sync.assert_awaited_once()  # 降级路径执行
        assert order[0] == "tx_closed"
        assert BaseDao.admitted_transaction_count() == 0

    @pytest.mark.asyncio
    async def test_close_then_shared_conn_write_rejects_without_waiting(self):
        """close 释放引擎后，事务内写入明确拒绝（R5），不等待维护事件。"""
        engine = _make_engine(AsyncMock())
        engine_provider.set_engine(engine)
        mgr = _make_cache_manager(engine)
        engine_mgr = MagicMock()
        engine_mgr.engine = engine

        async def _dispose() -> None:
            engine_mgr.engine = None

        engine_mgr.dispose = AsyncMock(side_effect=_dispose)
        mgr._engine_manager = engine_mgr
        mgr._dao_registry = MagicMock()

        dao = BaseDao(engine)
        try:
            await asyncio.wait_for(mgr.close(), timeout=2)
            assert mgr.engine is None
            with pytest.raises(EngineDisposedError):
                await asyncio.wait_for(dao._write_db("SELECT 1", conn=AsyncMock()), timeout=1)
            # close 的 finally 已放行维护事件，且准入计数无泄漏
            assert BaseDao._get_maintenance_event().is_set() is True
            assert BaseDao.admitted_transaction_count() == 0
        finally:
            engine_provider.reset_engine_provider()

    @pytest.mark.asyncio
    async def test_guarded_begin_works_after_init_cycle(self):
        """clear/init 之后新引擎上的事务仍可正常准入与排空（准入状态按 loop 隔离）。"""
        order: list[str] = []
        mgr = _make_cache_manager(_make_engine(self._make_ddl_conn(order)))
        tx_mock = AsyncMock()
        new_engine = _make_engine(tx_mock)

        async def _init_db(force=False, auto_migrate=None):
            mgr.engine = new_engine
            mgr._schema_initialized = True

        with patch.object(mgr, "init_db", new=_init_db):
            await asyncio.wait_for(mgr.clear_all_cache(), timeout=3)

        dao = BaseDao(new_engine)
        async with dao._guarded_begin() as tx_conn:
            assert tx_conn is tx_mock
            assert BaseDao.admitted_transaction_count() == 1
        assert BaseDao.admitted_transaction_count() == 0
        assert await BaseDao.drain_admitted_transactions(timeout=0.01) is True
