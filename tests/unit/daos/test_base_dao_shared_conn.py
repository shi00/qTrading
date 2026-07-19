"""P1-4 回归测试：共享事务连接场景下 _chunked_execute 必须串行执行分块。

背景：
    HolderDao._calculate_holder_changes 在 ``async with self._guarded_begin() as tx_conn:``
    内调用 ``self.chunked_in_write(self._write_db, sql_template, ts_codes, conn=tx_conn)``。
    原实现 ``_chunked_execute`` 不区分共享 conn 场景，统一走 ``Semaphore(8)+asyncio.gather``
    并发分支，违反 asyncpg「单连接不可并发执行语句」语义，触发
    ``InterfaceError: another operation is in progress``，异常被吞为 warning，
    导致 ``holder_num_change``/``holder_num_ratio`` 永久静默不更新。

修复：
    ``_chunked_execute`` 新增 ``conn: typing.Any = None`` 显式形参，
    ``conn is not None`` 时强制串行 for 循环执行（与 ``_save_upsert`` conn 分支同型）。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
