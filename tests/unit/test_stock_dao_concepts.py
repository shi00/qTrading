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
        assert "concept_id LIKE 'EM_%'" in delete_calls[0], f"DELETE 应限定 EM_ 前缀，实际: {delete_calls[0]}"
        assert delete_calls[0].strip().upper() != "DELETE FROM STOCK_CONCEPTS"

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
        assert "concept_id LIKE 'AI_LLM_%'" in sql_arg
        assert "AI_DOUBAO_" not in sql_arg

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
        assert "AI_LLM_%" in sql_arg
        assert "AI_DOUBAO_" not in sql_arg
