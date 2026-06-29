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
    """Task 1.1: 三个前缀常量定义"""

    def test_ai_concept_prefix(self):
        assert StockDao.AI_CONCEPT_PREFIX == "AI_LLM_"

    def test_em_concept_prefix(self):
        assert StockDao.EM_CONCEPT_PREFIX == "EM_"

    def test_limit_concept_prefix(self):
        assert StockDao.LIMIT_CONCEPT_PREFIX == "LIMIT_"


class TestOverwriteConceptsDeleteScope:
    """Task 1.1: 修复 overwrite_concepts 全表 DELETE 问题"""

    @pytest.mark.asyncio
    async def test_delete_only_em_prefix_concepts(self):
        """DELETE 语句必须含 WHERE concept_id LIKE 'EM_%'，不得全表删除"""
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["EM_C1"], "concept_name": ["概念1"]})
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
        assert delete_calls[0].strip().upper() != "DELETE FROM STOCK_CONCEPTS"
        # 验证参数正确传入（R4 参数化）
        delete_call = next(
            c for c in mock_conn.exec_driver_sql.call_args_list if c.args[0].strip().upper().startswith("DELETE")
        )
        assert delete_call.args[1] == [f"{StockDao.EM_CONCEPT_PREFIX}%"]

    @pytest.mark.asyncio
    async def test_does_not_delete_ai_llm_concepts(self):
        """DELETE 语句不得触及 AI_LLM_ 前缀概念"""
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "concept_id": ["EM_C1"], "concept_name": ["概念1"]})
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
        assert "AI_LLM_" not in delete_calls[0]
        assert "AI_DOUBAO_" not in delete_calls[0]


class TestClearAllAiLlmConcepts:
    """Task 1.1: 重命名 clear_all_doubao_concepts → clear_all_ai_llm_concepts"""

    @pytest.mark.asyncio
    async def test_method_exists_and_deletes_ai_llm_prefix(self):
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=10)
        result = await dao.clear_all_ai_llm_concepts()
        assert result == 10
        sql_arg = dao._write_db.call_args.args[0]
        assert "concept_id LIKE $1" in sql_arg
        assert "AI_LLM_" not in sql_arg  # SQL 不应含字面量（R4 参数化）
        assert "AI_DOUBAO_" not in sql_arg
        # 验证参数正确传入（R4 参数化）
        params_arg = dao._write_db.call_args.args[1]
        assert params_arg == [f"{StockDao.AI_CONCEPT_PREFIX}%"]

    def test_old_method_removed(self):
        """旧的 clear_all_doubao_concepts 方法不应再存在"""
        assert not hasattr(StockDao, "clear_all_doubao_concepts")


class TestUpsertAiConceptsPrefixMigration:
    """Task 1.1: upsert_ai_concepts 内部前缀迁移 AI_DOUBAO_ → AI_LLM_"""

    @pytest.mark.asyncio
    async def test_uses_ai_llm_prefix(self):
        dao = _make_dao()
        entries = [{"ts_code": "000001.SZ", "concepts": ["概念1"]}]
        await dao.upsert_ai_concepts(entries)
        dao._save_upsert.assert_called_once()
        df_arg = dao._save_upsert.call_args.args[0]
        concept_ids = df_arg["concept_id"].tolist()
        assert all(cid.startswith("AI_LLM_") for cid in concept_ids), (
            f"concept_id 应以 AI_LLM_ 开头，实际: {concept_ids}"
        )
        assert not any(cid.startswith("AI_DOUBAO_") for cid in concept_ids)

    @pytest.mark.asyncio
    async def test_dummy_id_uses_ai_llm_prefix(self):
        """无概念时生成的 dummy_id 也应使用 AI_LLM_ 前缀"""
        dao = _make_dao()
        entries = [{"ts_code": "000001.SZ", "concepts": []}]
        await dao.upsert_ai_concepts(entries)
        dao._save_upsert.assert_called_once()
        df_arg = dao._save_upsert.call_args.args[0]
        concept_ids = df_arg["concept_id"].tolist()
        assert len(concept_ids) == 1
        assert concept_ids[0].startswith("AI_LLM_")


class TestGetStocksWithoutAiConceptsPrefixMigration:
    """Task 1.1: get_stocks_without_ai_concepts 内部前缀迁移"""

    @pytest.mark.asyncio
    async def test_sql_uses_ai_llm_prefix(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]}))
        await dao.get_stocks_without_ai_concepts(batch_size=10)
        sql_arg = dao._read_db.call_args.args[0]
        assert "concept_id LIKE $1" in sql_arg
        assert "AI_LLM_" not in sql_arg  # SQL 不应含字面量（R4 参数化）
        assert "AI_DOUBAO_" not in sql_arg
        # 验证参数正确传入（R4 参数化）
        params_arg = dao._read_db.call_args.args[1]
        assert params_arg == [f"{StockDao.AI_CONCEPT_PREFIX}%"]


class TestUpsertEmConcepts:
    """Task 1.2: upsert_em_concepts 东财概念入库"""

    @pytest.mark.asyncio
    async def test_upsert_em_concepts_calls_save_upsert(self):
        """验证调用 _save_upsert，table_name='stock_concepts'"""
        dao = _make_dao()
        records = [
            {"ts_code": "000001.SZ", "concept_id": "EM_C1", "concept_name": "概念1"},
            {"ts_code": "000002.SZ", "concept_id": "EM_C2", "concept_name": "概念2"},
        ]
        result = await dao.upsert_em_concepts(records)
        assert result == 5  # mock _save_upsert return_value=5
        dao._save_upsert.assert_called_once()
        args = dao._save_upsert.call_args.args
        # args[0]=df, args[1]=table_name, args[2]=cols, args[3]=pk_columns
        assert args[1] == "stock_concepts"
        # 验证传入的 DataFrame 包含全部 records
        df_arg = args[0]
        assert len(df_arg) == 2
        assert set(df_arg["concept_id"]) == {"EM_C1", "EM_C2"}

    @pytest.mark.asyncio
    async def test_upsert_em_concepts_empty_records_returns_zero(self):
        """空列表返回 0，不调用 _save_upsert"""
        dao = _make_dao()
        result = await dao.upsert_em_concepts([])
        assert result == 0
        dao._save_upsert.assert_not_called()


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


class TestClearTodayLimitConcepts:
    """Task 1.3: clear_today_limit_concepts 清空 LIMIT_% 概念"""

    @pytest.mark.asyncio
    async def test_clear_today_limit_concepts_deletes_limit_prefix(self):
        """验证 SQL 含 WHERE concept_id LIKE $1 参数化（R4）"""
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.clear_today_limit_concepts()
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


class TestAIConceptFailureConstants:
    """错题本：默认重试上限与冷却期常量"""

    def test_max_retry_constant(self):
        assert StockDao.AI_CONCEPT_FAILURE_MAX_RETRY == 3

    def test_cooldown_seconds_constant(self):
        assert StockDao.AI_CONCEPT_FAILURE_COOLDOWN_SECONDS == 24 * 3600


class TestUpsertAIConceptFailure:
    """错题本：upsert_ai_concept_failure"""

    @pytest.mark.asyncio
    async def test_upsert_calls_guarded_begin_with_correct_sql(self):
        """验证使用 _guarded_begin 事务保护，SQL 含 ON CONFLICT upsert"""
        dao = _make_dao()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await dao.upsert_ai_concept_failure("000001.SZ", "平安银行", "LLM timeout")
        assert result == 1
        mock_conn.exec_driver_sql.assert_called_once()
        sql_arg = mock_conn.exec_driver_sql.call_args.args[0]
        assert "INSERT INTO ai_concept_failures" in sql_arg
        assert "ON CONFLICT (ts_code) DO UPDATE" in sql_arg
        assert "retry_count = ai_concept_failures.retry_count + 1" in sql_arg
        params = mock_conn.exec_driver_sql.call_args.args[1]
        assert params[0] == "000001.SZ"
        assert params[1] == "平安银行"
        assert params[2] == "LLM timeout"

    @pytest.mark.asyncio
    async def test_upsert_propagates_cancelled_error(self):
        """CancelledError 必须传播（R2）"""
        dao = _make_dao()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(
            side_effect=asyncio.CancelledError(),
        )
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(asyncio.CancelledError):
            await dao.upsert_ai_concept_failure("000001.SZ", "test", "err")

    @pytest.mark.asyncio
    async def test_upsert_propagates_engine_disposed(self):
        """EngineDisposedError 必须传播（R5）"""
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(
            side_effect=EngineDisposedError(),
        )
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(EngineDisposedError):
            await dao.upsert_ai_concept_failure("000001.SZ", "test", "err")

    @pytest.mark.asyncio
    async def test_upsert_custom_cooldown_overrides_default(self):
        """显式传入 cooldown_seconds 时使用自定义值

        T4 fix: next_retry_at 现以 UTC tz-naive 存储（S1-6 fix 模式），
        测试边界也需用 to_utc_for_db 转换以保持时区一致。
        """
        import datetime as dt

        from utils.time_utils import get_now, to_utc_for_db

        dao = _make_dao()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        before = to_utc_for_db(get_now())
        assert before is not None  # get_now() 永不为 None，收窄类型供 Pyright
        await dao.upsert_ai_concept_failure("000001.SZ", "test", "err", cooldown_seconds=60)
        after = to_utc_for_db(get_now())
        assert after is not None
        params = mock_conn.exec_driver_sql.call_args.args[1]
        # params[4] = next_retry_at（UTC tz-naive）
        next_retry: dt.datetime = params[4]
        # 应在 [before+60s, after+60s] 范围内
        assert before + dt.timedelta(seconds=60) <= next_retry <= after + dt.timedelta(seconds=60)

    @pytest.mark.asyncio
    async def test_upsert_writes_utc_not_cst_naive(self):
        """T4 fix: 验证写入的 last_attempt_at 是 UTC tz-naive，不是 CST tz-naive。

        若仍写 CST tz-naive，与 DB `now()` 比较时会有 8 小时偏差。
        """
        import datetime as dt

        from utils.time_utils import CST_TZ, get_now, to_utc_for_db

        dao = _make_dao()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql = AsyncMock()
        dao._guarded_begin = MagicMock()
        dao._guarded_begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        dao._guarded_begin.return_value.__aexit__ = AsyncMock(return_value=False)

        before_utc_naive = to_utc_for_db(get_now())
        assert before_utc_naive is not None  # get_now() 永不为 None，收窄类型供 Pyright
        await dao.upsert_ai_concept_failure("000001.SZ", "test", "err", cooldown_seconds=0)
        params = mock_conn.exec_driver_sql.call_args.args[1]
        # params[3] = last_attempt_at, params[4] = next_retry_at
        last_attempt: dt.datetime = params[3]
        # UTC tz-naive 应当比 before_utc_naive 晚（或相等），不应早 8 小时
        assert last_attempt >= before_utc_naive
        # 若写的是 CST tz-naive，last_attempt 会比 UTC 早 8 小时
        cst_naive = get_now().astimezone(CST_TZ).replace(tzinfo=None)
        cst_as_utc = to_utc_for_db(cst_naive)
        assert cst_as_utc is not None
        assert abs((last_attempt - cst_as_utc).total_seconds()) < 5  # 应接近 UTC，不是 CST


class TestGetAIConceptFailuresForRetry:
    """错题本：get_ai_concept_failures_for_retry"""

    @pytest.mark.asyncio
    async def test_returns_list_of_tuples(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {"ts_code": ["000001.SZ", "600000.SH"], "name": ["平安银行", "浦发银行"]},
            ),
        )
        result = await dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert result == [("000001.SZ", "平安银行"), ("600000.SH", "浦发银行")]
        sql_arg = dao._read_db.call_args.args[0]
        assert "retry_count < $1" in sql_arg
        assert "next_retry_at IS NULL OR next_retry_at <= now()" in sql_arg
        assert "ORDER BY last_attempt_at ASC" in sql_arg
        assert "LIMIT $2" in sql_arg
        # 默认 max_retry=3
        params = dao._read_db.call_args.args[1]
        assert params == (3, 10)

    @pytest.mark.asyncio
    async def test_custom_max_retry_overrides_default(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.get_ai_concept_failures_for_retry(batch_size=5, max_retry=10)
        params = dao._read_db.call_args.args[1]
        assert params == (10, 5)

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_ai_concept_failures_for_retry(batch_size=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_propagates_engine_disposed(self):
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=EngineDisposedError())
        with pytest.raises(EngineDisposedError):
            await dao.get_ai_concept_failures_for_retry(batch_size=10)


class TestClearAIConceptFailure:
    """错题本：clear_ai_concept_failure"""

    @pytest.mark.asyncio
    async def test_clear_calls_write_db_with_param(self):
        """成功打标后从错题本删除，使用参数化 SQL（R4）"""
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.clear_ai_concept_failure("000001.SZ")
        assert result == 1
        sql_arg = dao._write_db.call_args.args[0]
        assert "DELETE FROM ai_concept_failures WHERE ts_code = $1" in sql_arg
        params = dao._write_db.call_args.args[1]
        assert params == ("000001.SZ",)

    @pytest.mark.asyncio
    async def test_clear_propagates_cancelled(self):
        dao = _make_dao()
        dao._write_db = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await dao.clear_ai_concept_failure("000001.SZ")

    @pytest.mark.asyncio
    async def test_clear_propagates_engine_disposed(self):
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._write_db = AsyncMock(side_effect=EngineDisposedError())
        with pytest.raises(EngineDisposedError):
            await dao.clear_ai_concept_failure("000001.SZ")


class TestCountAIConceptFailures:
    """错题本：count_ai_concept_failures"""

    @pytest.mark.asyncio
    async def test_returns_count(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame({"cnt": [5]}),
        )
        result = await dao.count_ai_concept_failures()
        assert result == 5

    @pytest.mark.asyncio
    async def test_returns_zero_on_empty(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.count_ai_concept_failures()
        assert result == 0

    @pytest.mark.asyncio
    async def test_swallows_operational_errors_returns_zero(self):
        """通用异常降级为 0，不传播（诊断用途）"""
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=RuntimeError("connect fail"))
        result = await dao.count_ai_concept_failures()
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_propagates_engine_disposed(self):
        """EngineDisposedError 必须传播（R5），不可被降级为 0"""
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=EngineDisposedError())
        with pytest.raises(EngineDisposedError):
            await dao.count_ai_concept_failures()


class TestDeleteExpiredFailures:
    """T5 fix: 错题本清理 — delete_expired_failures"""

    @pytest.mark.asyncio
    async def test_deletes_records_with_retry_count_ge_max(self):
        """正常路径：删除 retry_count >= max_retry 的记录"""
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=2)
        result = await dao.delete_expired_failures()
        assert result == 2
        dao._write_db.assert_called_once()
        sql_arg = dao._write_db.call_args.args[0]
        params = dao._write_db.call_args.args[1]
        assert "DELETE FROM ai_concept_failures WHERE retry_count >= $1" in sql_arg
        assert params == (StockDao.AI_CONCEPT_FAILURE_MAX_RETRY,)

    @pytest.mark.asyncio
    async def test_custom_max_retry_overrides_default(self):
        """显式传入 max_retry 时使用自定义值"""
        dao = _make_dao()
        dao._write_db = AsyncMock(return_value=5)
        result = await dao.delete_expired_failures(max_retry=10)
        assert result == 5
        params = dao._write_db.call_args.args[1]
        assert params == (10,)

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self):
        """CancelledError 必须传播（R2）"""
        dao = _make_dao()
        dao._write_db = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await dao.delete_expired_failures()

    @pytest.mark.asyncio
    async def test_propagates_engine_disposed(self):
        """EngineDisposedError 必须传播（R5）"""
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao._write_db = AsyncMock(side_effect=EngineDisposedError())
        with pytest.raises(EngineDisposedError):
            await dao.delete_expired_failures()

    @pytest.mark.asyncio
    async def test_propagates_operational_errors(self):
        """通用异常必须传播（与 count_ai_concept_failures 的降级语义不同：
        清理是写操作，错误应让调用方感知而非静默）"""
        dao = _make_dao()
        dao._write_db = AsyncMock(side_effect=RuntimeError("connect fail"))
        with pytest.raises(RuntimeError):
            await dao.delete_expired_failures()
