# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.market_dao import MarketDao, _row_to_dict

pytestmark = pytest.mark.unit


class TestMarketDaoSaveMarketNews:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.save_market_news(
            {
                "content": "Test news",
                "tags": "finance",
                "publish_time": "2024-06-15 10:00:00",
                "source": "Sina",
            }
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_empty_content(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.save_market_news({"content": ""})
        assert result == 1

    @pytest.mark.asyncio
    async def test_sql_uses_composite_conflict_key(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        await dao.save_market_news(
            {
                "content": "Test",
                "tags": None,
                "publish_time": "2024-06-15 10:00:00",
                "source": "Sina",
            }
        )
        call_args = dao._write_db.call_args
        sql = call_args[0][0]
        assert 'ON CONFLICT("content_hash","publish_time")' in sql

    @pytest.mark.asyncio
    async def test_sql_updates_content_and_source_on_conflict(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        await dao.save_market_news(
            {
                "content": "Updated news",
                "tags": None,
                "publish_time": "2024-06-15 10:00:00",
                "source": "CLS",
            }
        )
        call_args = dao._write_db.call_args
        sql = call_args[0][0]
        assert '"content" = excluded."content"' in sql
        assert '"source" = excluded."source"' in sql


class TestMarketDaoGetMarketNews:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_market_news(limit=10)
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_min_time(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_market_news(limit=10, min_publish_time="2024-06-15")
        assert result is not None

    @pytest.mark.asyncio
    async def test_home_filter_default_applies_telegraph(self):
        """A2: 默认 home_filter=True 应过滤出 telegraph/存量(NULL) 行，剔除 announcement/news 文档。"""
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        await dao.get_market_news(limit=10)
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "source_kind = $1" in sql
        assert "source_kind IS NULL" in sql
        params = call_args[0][1]
        assert "telegraph" in params

    @pytest.mark.asyncio
    async def test_home_filter_false_omits_source_kind_clause(self):
        """A2: home_filter=False 时保持原查询不追加来源过滤。"""
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        await dao.get_market_news(limit=10, home_filter=False)
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "source_kind" not in sql


class TestMarketDaoSaveDailyIndicators:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_daily_indicators(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_daily_indicators(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=5)
        result = await dao.save_daily_indicators(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        assert result == 5


class TestMarketDaoGetDailyIndicators:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_dates(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators(ts_code="000001.SZ", start_date="20240101", end_date="20240630")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_limit(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators(limit=10)
        assert result is not None


class TestMarketDaoGetDailyIndicatorsBulk:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_daily_indicators_bulk([])
        assert result is not None

    @pytest.mark.asyncio
    async def test_small_list(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators_bulk(["000001.SZ", "000002.SZ"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_large_list_chunked(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        codes = [f"{i:06d}.SZ" for i in range(600)]
        result = await dao.get_daily_indicators_bulk(codes, start_date="20240101")
        assert result is not None


class TestMarketDaoSaveIndexWeights:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_index_weights(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_index_weights(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_index_weights(pd.DataFrame({"index_code": ["000300.SH"]}))
        assert result == 3


class TestMarketDaoGetIndexWeights:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"index_code": ["000300.SH"]}))
        result = await dao.get_index_weights("000300.SH", "20240615")
        assert result is not None


class TestMarketDaoGetLatestIndexWeightDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": ["20240615"]}))
        result = await dao.get_latest_index_weight_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": [None]}))
        result = await dao.get_latest_index_weight_date()
        assert result is None


class TestMarketDaoSaveMoneyflowHsgt:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_moneyflow_hsgt(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_moneyflow_hsgt(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_moneyflow_hsgt(pd.DataFrame({"trade_date": ["20240615"]}))
        assert result == 3


class TestMarketDaoGetMoneyflowHsgt:
    @pytest.mark.asyncio
    async def test_basic(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"], "north_money": [100.0]}))
        result = await dao.get_moneyflow_hsgt(trade_date="20240615")
        assert result is not None
        assert result.attrs["column_units"]["north_money"] == "million_cny"

    @pytest.mark.asyncio
    async def test_with_limit(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"trade_date": ["20240615"], "north_money": [100.0]}))
        result = await dao.get_moneyflow_hsgt(limit=10)
        assert result is not None
        assert result.attrs["column_units"]["north_money"] == "million_cny"


class TestMarketDaoGetMoneyflowHsgtRange:
    @pytest.mark.asyncio
    async def test_with_date_range(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame({"trade_date": ["20240615", "20240614"], "north_money": [100.0, 200.0]})
        )
        result = await dao.get_moneyflow_hsgt_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert "trade_date" in result.columns
        assert result.attrs["column_units"]["north_money"] == "million_cny"
        dao._read_db.assert_called_once()
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "trade_date >= $1" in sql
        assert "trade_date <= $2" in sql

    @pytest.mark.asyncio
    async def test_empty_result(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_moneyflow_hsgt_range("20240601", "20240615")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_moneyflow_hsgt_range("20240601", "20240615")
        assert result is None


class TestMarketDaoSaveMoneyflowHsgtStringCoercion:
    @pytest.mark.asyncio
    async def test_numeric_string_coercion(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=1)
        df = pd.DataFrame(
            {
                "trade_date": ["20240615"],
                "north_money": ["100.5"],
                "south_money": ["200.3"],
            }
        )
        result = await dao.save_moneyflow_hsgt(df)
        assert result == 1
        call_args = dao._save_upsert.call_args
        saved_df = call_args[0][0]
        assert saved_df["north_money"].dtype in ["float64", "float32", "int64"]


class TestMarketDaoGetDailyIndicatorsNoParams:
    @pytest.mark.asyncio
    async def test_no_params(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_daily_indicators()
        assert isinstance(result, pd.DataFrame)
        dao._read_db.assert_called_once()


class TestMarketDaoGetMarketNewsNoMinTime:
    @pytest.mark.asyncio
    async def test_no_min_time(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        result = await dao.get_market_news()
        assert result is not None
        dao._read_db.assert_called_once()


class TestMarketDaoGetLatestIndexWeightDateEmpty:
    @pytest.mark.asyncio
    async def test_empty_dataframe(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_latest_index_weight_date()
        assert result is None


class TestMarketDaoSaveMarketNewsNoneContent:
    @pytest.mark.asyncio
    async def test_none_content(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._write_db = AsyncMock(return_value=1)
        result = await dao.save_market_news({"content": None})
        assert result == 1


class TestRowToDict:
    def test_passthrough_container_and_none(self):
        events = [{"a": 1}]
        assert _row_to_dict({"events": events, "coverage": {"k": "v"}, "ts_code": "000001.SZ"}) == {
            "events": events,
            "coverage": {"k": "v"},
            "ts_code": "000001.SZ",
        }

    def test_float_nan_to_none(self):
        assert _row_to_dict({"score": float("nan")}) == {"score": None}

    def test_numpy_scalar_item(self):
        out = _row_to_dict({"val": __import__("numpy").int64(5)})
        assert out["val"] == 5

    def test_item_raises_falls_back_to_raw(self):
        class _BadScalar:
            def item(self):
                raise ValueError("boom")

        out = _row_to_dict({"val": _BadScalar()})
        assert isinstance(out["val"], _BadScalar)

    def test_plain_value_passthrough(self):
        out = _row_to_dict({"sentiment": "positive", "title": "x"})
        assert out == {"sentiment": "positive", "title": "x"}


class TestMarketDaoBuildContentHash:
    def test_announcement_uses_canonical_text(self):
        h = MarketDao.build_content_hash("announcement", "  body  ", "title", "000001.SZ")
        assert h == __import__("hashlib").sha256(b"000001.SZannouncementbody").hexdigest()

    def test_news_content_empty_uses_title(self):
        h = MarketDao.build_content_hash("news", None, "  mytitle ", "600519.SH")
        assert h == __import__("hashlib").sha256(b"600519.SHnewsmytitle").hexdigest()

    def test_canonical_both_empty_returns_none(self):
        assert MarketDao.build_content_hash("announcement", None, None, "600519.SH") is None

    def test_telegraph_uses_full_content(self):
        h = MarketDao.build_content_hash("telegraph", "raw content", "title", None)
        assert h == __import__("hashlib").sha256(b"raw content").hexdigest()

    def test_unknown_source_uses_full_content(self):
        h = MarketDao.build_content_hash(None, "plain", None, None)
        assert h == __import__("hashlib").sha256(b"plain").hexdigest()


class TestMarketDaoSaveMarketNewsBatch:
    @pytest.mark.asyncio
    async def test_empty_docs(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        assert await dao.save_market_news_batch([]) == []

    @pytest.mark.asyncio
    async def test_skips_docs_without_hash_material_or_publish_time(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        # announcement + no content/title -> no hash; telegraph + no publish_time -> skip
        result = await dao.save_market_news_batch(
            [
                {"source_kind": "announcement", "content": None, "title": None, "ts_code": "000001.SZ"},
                {"source_kind": "telegraph", "content": "x", "title": "t"},
            ]
        )
        assert result == []
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_and_lookup_ids(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock()
        import hashlib

        content_hash = hashlib.sha256(b"body").hexdigest()
        id_df = pd.DataFrame({"id": [101], "content_hash": [content_hash], "publish_time": ["2024-06-15 10:00:00"]})
        dao._read_db = AsyncMock(return_value=id_df)
        docs = [
            {
                "content": "body",
                "title": "t",
                "ts_code": "000001.SZ",
                "source_kind": "telegraph",
                "publish_time": "2024-06-15 10:00:00",
                "tags": "t1",
                "source": "CLS",
            }
        ]
        result = await dao.save_market_news_batch(docs)
        assert result == [101]
        dao._save_upsert.assert_called_once()  # noqa: weak-assertion upsert 走批量路径由下行 _read_db call_args 的 SQL 形状强断言
        call_args = dao._read_db.call_args
        assert "IN (($1,$2))" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_lookup_empty_returns_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.save_market_news_batch(
            [{"content": "b", "source_kind": "telegraph", "publish_time": "2024-06-15 10:00:00"}]
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_lookup_missing_some_ids(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock()
        import hashlib

        content_hash_a = hashlib.sha256(b"a").hexdigest()
        id_df = pd.DataFrame({"id": [101], "content_hash": [content_hash_a], "publish_time": ["p1"]})
        dao._read_db = AsyncMock(return_value=id_df)
        docs = [
            {"content": "a", "source_kind": "telegraph", "publish_time": "p1"},
            {"content": "b", "source_kind": "telegraph", "publish_time": "p2"},
        ]
        result = await dao.save_market_news_batch(docs)
        assert result == [101]


class TestMarketDaoGetTelegraphNewsForStocks:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.get_telegraph_news_for_stocks([], [])
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_codes_names_start_end(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        await dao.get_telegraph_news_for_stocks(["000001.SZ"], ["平安银行"], "2024-06-01", "2024-06-15", limit=50)
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "publish_time >= $2" in sql
        assert "publish_time <= $3" in sql
        assert "ILIKE" in sql
        assert params[0] == "telegraph"

    @pytest.mark.asyncio
    async def test_short_name_ignored(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        await dao.get_telegraph_news_for_stocks([], ["ab"])
        call_args = dao._read_db.call_args
        assert "ILIKE" not in call_args[0][0]

    @pytest.mark.asyncio
    async def test_read_db_not_suppressed(self):
        """对抗性检视 Major①：证据读取必须显式失败，不得被 _read_db 默认吞成空 DF。

        suppress_errors=False 保证 DB 故障向上抛异常，服务层据此映射 db_error，
        而非伪装成 no_evidence（§8.1 / §10.3）。
        """
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        await dao.get_telegraph_news_for_stocks(["000001.SZ"], ["平安银行"], "2024-06-01", "2024-06-15")
        assert dao._read_db.call_args.kwargs.get("suppress_errors") is False

    @pytest.mark.asyncio
    async def test_engine_error_propagates(self):
        """DB 引擎异常须原样传播（不被 suppress_errors 吞掉），供服务层捕获识别。"""
        dao = MarketDao(MagicMock(spec=AsyncEngine))

        async def boom(*a, **k):
            raise EngineDisposedError("engine disposed")

        dao._read_db = AsyncMock(side_effect=boom)
        with pytest.raises(EngineDisposedError):  # noqa: weak-assertion 引擎异常须显式传播，异常类型即测试目标
            await dao.get_telegraph_news_for_stocks(["000001.SZ"], ["平安银行"], "2024-06-01", "2024-06-15")


class TestMarketDaoGetMarketNewsDocuments:
    @pytest.mark.asyncio
    async def test_empty_ts_code_returns_empty(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        result = await dao.get_market_news_documents("")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    @pytest.mark.asyncio
    async def test_with_time_range(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"id": [1]}))
        await dao.get_market_news_documents("000001.SZ", "2024-06-01", "2024-06-15")
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "ts_code = $1" in sql
        assert "publish_time >= $2" in sql
        assert "publish_time <= $3" in sql
        assert call_args[0][1][0] == "000001.SZ"


class TestMarketDaoSaveNewsRiskBrief:
    @pytest.mark.asyncio
    async def test_empty_brief_returns(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock()
        await dao.save_news_risk_brief({})
        dao._save_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_upsert(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock()
        await dao.save_news_risk_brief({"ts_code": "000001.SZ", "input_hash": "h", "summary": "s"})
        dao._save_upsert.assert_called_once()  # noqa: weak-assertion 数据表名与 pk_columns 由下两行 call_args 强断言验证
        # 强断言：走 news_risk_brief 主键冲突路径（复合主键 ts_code + input_hash）
        table_arg = dao._save_upsert.call_args[0][1]
        assert table_arg == "news_risk_brief"
        assert dao._save_upsert.call_args.kwargs["pk_columns"] == ["ts_code", "input_hash"]


class TestMarketDaoGetNewsRiskBrief:
    @pytest.mark.asyncio
    async def test_empty_returns_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        assert await dao.get_news_risk_brief("000001.SZ", "h") is None

    @pytest.mark.asyncio
    async def test_none_returns_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=None)
        assert await dao.get_news_risk_brief("000001.SZ", "h") is None

    @pytest.mark.asyncio
    async def test_with_row(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "score": [3.0]}))
        result = await dao.get_news_risk_brief("000001.SZ", "h")
        assert result["ts_code"] == "000001.SZ"


class TestMarketDaoGetLatestSuccessBrief:
    @pytest.mark.asyncio
    async def test_empty_returns_none(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        assert await dao.get_latest_success_brief("000001.SZ", "w1", "w2") is None

    @pytest.mark.asyncio
    async def test_with_row_and_sql(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "id": [7]}))
        result = await dao.get_latest_success_brief("000001.SZ", "w1", "w2")
        assert result["id"] == 7
        call_args = dao._read_db.call_args
        sql = call_args[0][0]
        assert "analysis_status IN ($4, $5)" in sql
        params = call_args[0][1]
        assert params[3] == "analyzed_with_events"
        assert params[4] == "analyzed_no_event"
