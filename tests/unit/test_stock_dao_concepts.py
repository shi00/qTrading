# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.persistence.daos.stock_dao import StockDao

pytestmark = pytest.mark.unit


def _make_dao():
    dao = StockDao(MagicMock())
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=None)
    dao._write_db = AsyncMock(return_value=0)
    dao._get_maintenance_event = MagicMock()
    dao._get_maintenance_event.return_value.wait = AsyncMock()
    dao.engine = MagicMock()
    dao._prepare_data_params = MagicMock(return_value=[["val1"]])
    dao._quote_columns = MagicMock(return_value="ts_code, concept_id, concept_name, updated_at")
    return dao


class TestConceptPrefixConstants:
    """Task 1.1: 来源前缀常量定义（review09-24 MAJOR-06 新增 TS_）"""

    def test_em_concept_prefix(self):
        assert StockDao.EM_CONCEPT_PREFIX == "EM_"

    def test_limit_concept_prefix(self):
        assert StockDao.LIMIT_CONCEPT_PREFIX == "LIMIT_"

    def test_ts_concept_prefix(self):
        """Tushare 概念同步自有行前缀，与 EM_/LIMIT_ 隔离"""
        assert StockDao.TS_CONCEPT_PREFIX == "TS_"


class TestOverwriteConceptsDeleteScope:
    """review09-24 MAJOR-06: overwrite_concepts 仅清理 TS_ 自有行，不再误删他源行"""

    @pytest.mark.asyncio
    async def test_delete_only_ts_prefix_concepts(self):
        """DELETE 必须限定 TS_ 前缀（下划线转义 + ESCAPE），不得全表删除"""
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["TS_C1"], "concept_name": ["概念1"]})
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql = AsyncMock()
        dao.engine.begin = MagicMock()
        dao.engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao.engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value=[["val1"]])
            await dao.overwrite_concepts(df)

        sql_calls = [call.args[0] for call in mock_conn.exec_driver_sql.call_args_list]
        delete_calls = [s for s in sql_calls if s.strip().upper().startswith("DELETE")]
        assert len(delete_calls) == 1, f"应只有一条 DELETE 语句，实际: {delete_calls}"
        assert "concept_id LIKE $1" in delete_calls[0], f"DELETE 应使用参数化 LIKE $1，实际: {delete_calls[0]}"
        assert "ESCAPE" in delete_calls[0], f"DELETE 需显式 ESCAPE 以还原下划线字面量，实际: {delete_calls[0]}"
        assert delete_calls[0].strip().upper() != "DELETE FROM STOCK_CONCEPTS"
        # 验证参数正确传入（R4 参数化）：TS_ 的下划线须转义为字面量
        delete_call = next(
            c for c in mock_conn.exec_driver_sql.call_args_list if c.args[0].strip().upper().startswith("DELETE")
        )
        expected_pattern = StockDao.TS_CONCEPT_PREFIX.replace("_", "\\_") + "%"
        assert delete_call.args[1] == [expected_pattern]

    @pytest.mark.asyncio
    async def test_does_not_delete_other_source_concepts(self):
        """DELETE 语句不得触及 EM_ / LIMIT_ 其他来源行"""
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["TS_C1"], "concept_name": ["概念1"]})
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql = AsyncMock()
        dao.engine.begin = MagicMock()
        dao.engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao.engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value=[["val1"]])
            await dao.overwrite_concepts(df)

        sql_calls = [call.args[0] for call in mock_conn.exec_driver_sql.call_args_list]
        delete_calls = [s for s in sql_calls if s.strip().upper().startswith("DELETE")]
        assert len(delete_calls) == 1
        for other_prefix in (StockDao.EM_CONCEPT_PREFIX, StockDao.LIMIT_CONCEPT_PREFIX):
            assert other_prefix not in delete_calls[0]
        # 参数仅含 TS_ 前缀，不放行他源
        delete_call = next(
            c for c in mock_conn.exec_driver_sql.call_args_list if c.args[0].strip().upper().startswith("DELETE")
        )
        assert delete_call.args[1] == [StockDao.TS_CONCEPT_PREFIX.replace("_", "\\_") + "%"]


class TestOverwriteEmConcepts:
    """review09-24 MAJOR-06: overwrite_em_concepts 按板块「删除-重建」东财概念"""

    @pytest.mark.asyncio
    async def test_deletes_replace_boards_then_upserts(self):
        """仅删除 replace_board_codes 对应 EM_ 行（= ANY($1) 参数化），再 upsert 全部记录"""
        dao = _make_dao()
        records = [
            {"ts_code": "000001.SZ", "concept_id": "EM_BK0123", "concept_name": "概念1"},
            {"ts_code": "000002.SZ", "concept_id": "EM_BK0123", "concept_name": "概念1"},
        ]
        result = await dao.overwrite_em_concepts(records, replace_board_codes=["BK0123"])
        assert result == 5  # mock _save_upsert return_value=5

        dao._write_db.assert_called_once()
        sql_arg = dao._write_db.call_args.args[0]
        assert "DELETE FROM stock_concepts" in sql_arg
        assert "concept_id = ANY($1)" in sql_arg
        assert dao._write_db.call_args.args[1] == [["EM_BK0123"]]
        # 删除与写入共享同一事务 conn
        assert dao._write_db.call_args.kwargs.get("conn") is dao._save_upsert.call_args.kwargs.get("conn")

        dao._save_upsert.assert_called_once()
        args = dao._save_upsert.call_args.args
        assert args[1] == "stock_concepts"
        df_arg = args[0]
        assert len(df_arg) == 2
        assert set(df_arg["concept_id"]) == {"EM_BK0123"}

    @pytest.mark.asyncio
    async def test_no_replace_boards_upserts_without_delete(self):
        """replace_board_codes 为空时不做删除，仅 upsert（保留旧行）"""
        dao = _make_dao()
        records = [{"ts_code": "000001.SZ", "concept_id": "EM_BK0999", "concept_name": "概念9"}]
        result = await dao.overwrite_em_concepts(records, replace_board_codes=[])
        assert result == 5
        dao._write_db.assert_not_called()
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_records_with_replace_boards_only_deletes(self):
        """记录为空但需替换板块时，仍删除对应旧行（清理已清空板块），不调用 upsert"""
        dao = _make_dao()
        result = await dao.overwrite_em_concepts([], replace_board_codes=["BK0123"])
        assert result == 0
        dao._write_db.assert_called_once()
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_both_empty_returns_zero_without_db(self):
        """记录与替换板块均为空时直接返回 0，不触达 DB"""
        dao = _make_dao()
        result = await dao.overwrite_em_concepts([], replace_board_codes=[])
        assert result == 0
        dao._write_db.assert_not_called()
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self):
        """CancelledError 必须传播（R2）"""
        dao = _make_dao()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(side_effect=asyncio.CancelledError())
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(asyncio.CancelledError):
            await dao.overwrite_em_concepts(
                [{"ts_code": "000001.SZ", "concept_id": "EM_C1", "concept_name": "概念1"}],
                replace_board_codes=[],
            )

    @pytest.mark.asyncio
    async def test_propagates_engine_disposed(self):
        """EngineDisposedError 必须传播（R5）"""
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(side_effect=EngineDisposedError())
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(EngineDisposedError):
            await dao.overwrite_em_concepts(
                [{"ts_code": "000001.SZ", "concept_id": "EM_C1", "concept_name": "概念1"}],
                replace_board_codes=[],
            )

    def test_old_upsert_em_concepts_removed(self):
        """旧的 upsert_em_concepts（无删除语义）不应再存在"""
        assert not hasattr(StockDao, "upsert_em_concepts")


class TestUpsertLimitConcepts:
    """Task 1.3: upsert_limit_concepts 涨停原因概念入库"""

    @pytest.mark.asyncio
    async def test_upsert_limit_concepts_calls_save_upsert(self):
        """验证调用 _save_upsert，table_name='stock_concepts'"""
        dao = _make_dao()
        records = [
            {"ts_code": "000001.SZ", "concept_id": "LIMIT_C1", "concept_name": "涨停原因1"},
        ]
        result = await dao.upsert_limit_concepts(records)
        assert result == 5
        dao._save_upsert.assert_called_once()
        args = dao._save_upsert.call_args.args
        assert args[1] == "stock_concepts"
        df_arg = args[0]
        assert len(df_arg) == 1
        assert df_arg["concept_id"].iloc[0] == "LIMIT_C1"

    @pytest.mark.asyncio
    async def test_upsert_limit_concepts_empty_records_returns_zero(self):
        """空列表返回 0，不调用 _save_upsert"""
        dao = _make_dao()
        result = await dao.upsert_limit_concepts([])
        assert result == 0
        dao._save_upsert.assert_not_called()


class TestClearAllLimitConcepts:
    """review08-D3: clear_all_limit_concepts 清空全部 LIMIT_% 概念（原名 clear_today_limit_concepts 无日期条件，重命名）"""

    @pytest.mark.asyncio
    async def test_clear_all_limit_concepts_deletes_limit_prefix(self):
        """验证 SQL 含 WHERE concept_id LIKE $1 参数化（R4）"""
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.clear_all_limit_concepts()
        assert result == 1
        dao._write_db.assert_called_once()
        sql_arg = dao._write_db.call_args.args[0]
        assert "concept_id LIKE $1" in sql_arg
        assert "LIMIT_" not in sql_arg  # SQL 不应含字面量（R4 参数化）
        assert "DELETE FROM stock_concepts" in sql_arg
        # 验证参数正确传入（R4 参数化）
        params_arg = dao._write_db.call_args.args[1]
        assert params_arg == [f"{StockDao.LIMIT_CONCEPT_PREFIX}%"]


class TestGetConceptsByPrefix:
    """Task 1.3: get_concepts_by_prefix 按 concept_id 前缀查询概念"""

    @pytest.mark.asyncio
    async def test_get_concepts_by_prefix_returns_list(self):
        """验证返回 list[dict]，SQL 含 LIKE $1 参数化"""
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "concept_id": ["EM_C1"],
                    "concept_name": ["概念1"],
                }
            )
        )
        result = await dao.get_concepts_by_prefix("EM_")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["ts_code"] == "000001.SZ"
        assert result[0]["concept_id"] == "EM_C1"
        # 验证 SQL 含参数化 LIKE $1（R4 合规）
        sql_arg = dao._read_db.call_args.args[0]
        assert "concept_id LIKE $1" in sql_arg
        # 验证参数含 EM_%
        params_arg = dao._read_db.call_args.args[1]
        assert params_arg == ["EM_%"]

    @pytest.mark.asyncio
    async def test_get_concepts_by_prefix_with_ts_codes(self):
        """验证带 ts_codes 参数的查询，IN 子句使用 $2, $3 占位符"""
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "concept_id": ["EM_C1"],
                    "concept_name": ["概念1"],
                }
            )
        )
        result = await dao.get_concepts_by_prefix("EM_", ts_codes=["000001.SZ", "000002.SZ"])
        assert isinstance(result, list)
        assert len(result) == 1
        sql_arg = dao._read_db.call_args.args[0]
        assert "concept_id LIKE $1" in sql_arg
        assert "ts_code IN ($2,$3)" in sql_arg
        params_arg = dao._read_db.call_args.args[1]
        assert params_arg == ["EM_%", "000001.SZ", "000002.SZ"]

    @pytest.mark.asyncio
    async def test_get_concepts_by_prefix_empty_returns_empty_list(self):
        """空结果返回 []"""
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_concepts_by_prefix("EM_")
        assert result == []


class TestOverwriteLimitConcepts:
    """P0-2: overwrite_limit_concepts 事务原子性测试。

    验证 clear_all_limit_concepts + upsert_limit_concepts 在同一事务（同一 conn）内执行，
    避免 clear 成功 upsert 失败导致当日数据丢失。
    """

    @pytest.mark.asyncio
    async def test_records_passed_clear_and_upsert_share_same_conn(self):
        """有记录时 clear 与 upsert 必须在同一 conn 上执行（事务原子性）"""
        dao = _make_dao()
        mock_conn = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        # 真实调用 clear_all_limit_concepts / upsert_limit_concepts，验证 conn 透传
        dao._write_db = AsyncMock(return_value=3)
        dao._save_upsert = AsyncMock(return_value=5)

        records = [
            {"ts_code": "000001.SZ", "concept_id": "LIMIT_000001.SZ", "concept_name": "涨停原因1"},
        ]
        result = await dao.overwrite_limit_concepts(records)

        assert result == 5
        # clear_all_limit_concepts 传入 conn=mock_conn
        dao._write_db.assert_called_once()
        assert dao._write_db.call_args.kwargs.get("conn") is mock_conn
        # upsert_limit_concepts 传入 conn=mock_conn
        dao._save_upsert.assert_called_once()
        assert dao._save_upsert.call_args.kwargs.get("conn") is mock_conn

    @pytest.mark.asyncio
    async def test_empty_records_only_clears_no_upsert(self):
        """空记录时只 clear 不 upsert，返回 0（保留 clear 以重置全部 LIMIT_ 数据）"""
        dao = _make_dao()
        mock_conn = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        dao._write_db = AsyncMock(return_value=2)
        dao._save_upsert = AsyncMock(return_value=99)

        result = await dao.overwrite_limit_concepts([])

        assert result == 0
        dao._write_db.assert_called_once()
        assert dao._write_db.call_args.kwargs.get("conn") is mock_conn
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self):
        """CancelledError 必须传播（R2），不得被外层 except Exception 吞"""
        dao = _make_dao()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(
            side_effect=asyncio.CancelledError(),
        )
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(asyncio.CancelledError):
            await dao.overwrite_limit_concepts([{"ts_code": "000001.SZ"}])

    @pytest.mark.asyncio
    async def test_propagates_engine_disposed(self):
        """EngineDisposedError 必须传播（R5）"""
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(
            side_effect=EngineDisposedError(),
        )
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(EngineDisposedError):
            await dao.overwrite_limit_concepts([{"ts_code": "000001.SZ"}])

    @pytest.mark.asyncio
    async def test_propagates_upsert_failure(self):
        """upsert 失败时异常必须传播，由 _guarded_begin 触发事务回滚（clear 不应提交）"""
        dao = _make_dao()
        mock_conn = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        dao._write_db = AsyncMock(return_value=3)
        # upsert 抛通用异常 → 必须传播，事务回滚
        dao._save_upsert = AsyncMock(side_effect=RuntimeError("upsert boom"))

        with pytest.raises(RuntimeError, match="upsert boom"):
            await dao.overwrite_limit_concepts([{"ts_code": "000001.SZ"}])

        # clear 已调用，但事务因 upsert 失败回滚
        dao._write_db.assert_called_once()
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_propagates_clear_failure(self):
        """clear 失败时异常必须传播，由 _guarded_begin 触发事务回滚（不调用 upsert）"""
        dao = _make_dao()
        mock_conn = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        dao._write_db = AsyncMock(side_effect=RuntimeError("clear boom"))
        dao._save_upsert = AsyncMock(return_value=99)

        with pytest.raises(RuntimeError, match="clear boom"):
            await dao.overwrite_limit_concepts([{"ts_code": "000001.SZ"}])

        dao._write_db.assert_called_once()
        # clear 失败 → upsert 不应被调用
        dao._save_upsert.assert_not_called()
