# pyright: reportArgumentType=false
# 本文件含 mock/monkey-patch 模式，触发 参数类型不兼容 告警；pyright 无法验证
# 替身类与生产类型兼容性，统一在此文件局部禁用相关告警。

import datetime
import hashlib

import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.market_dao import MarketDao
from data.news_match import match_news_to_stock, dedupe_documents

pytestmark = pytest.mark.unit


# ---------- B2 build_content_hash 分口径 ----------
class TestBuildContentHash:
    def test_telegraph_uses_raw_content(self):
        """telegraph / None 口径：输入 = 入库前原始 content 全文（与 save_market_news 逐字节一致）。"""
        expected = hashlib.sha256(b"  hello  ").hexdigest()
        assert MarketDao.build_content_hash("telegraph", "  hello  ", None, "600519.SH") == expected
        assert MarketDao.build_content_hash(None, "  hello  ", None, "600519.SH") == expected

    def test_telegraph_empty_content_matches_save_market_news(self):
        """telegraph 空 content 哈希为空串文案（与现有 save_market_news 行为一致，非拒绝）。"""
        expected = hashlib.sha256(b"").hexdigest()
        assert MarketDao.build_content_hash("telegraph", "", None, None) == expected

    def test_announcement_uses_code_kind_canonical(self):
        """announcement 口径：ts_code + source_kind + canonical_text(content.strip())。"""
        content = "  半年度报告  "
        expected = hashlib.sha256("600519.SHannouncement半年度报告".encode()).hexdigest()
        assert MarketDao.build_content_hash("announcement", content, None, "600519.SH") == expected

    def test_news_uses_title_when_content_empty(self):
        """news 口径 content 为空 → canonical_text = title.strip()。"""
        expected = hashlib.sha256("000001.SZnews独家新闻".encode()).hexdigest()
        assert MarketDao.build_content_hash("news", "  ", " 独家新闻 ", "000001.SZ") == expected

    def test_announcement_both_empty_rejects(self):
        """announcement/content、title 皆空 → 拒绝（返回 None）。"""
        assert MarketDao.build_content_hash("announcement", None, None, "600519.SH") is None
        assert MarketDao.build_content_hash("news", "", "", None) is None


# ---------- B2 save_market_news_batch 批量落库 + 反查 id ----------
class TestSaveMarketNewsBatch:
    def _dao(self):
        dao = MarketDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=2)
        return dao

    @pytest.mark.asyncio
    async def test_writes_via_upsert_with_conflict_columns(self):
        dao = self._dao()
        docs = [
            {
                "ts_code": "600519.SH",
                "source_kind": "announcement",
                "title": "半年报",
                "publish_time": datetime.datetime(2024, 6, 15),
            },
            {
                "ts_code": "000001.SZ",
                "source_kind": "news",
                "title": "独家",
                "publish_time": datetime.datetime(2024, 6, 15),
            },
        ]
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [101, 102],
                    "content_hash": [marker for marker in ("", "")],
                    "publish_time": [datetime.datetime(2024, 6, 15), datetime.datetime(2024, 6, 15)],
                }
            )
        )
        ids = await dao.save_market_news_batch(docs)
        upsert_args = dao._save_upsert.call_args
        assert upsert_args.args[1] == "market_news"
        upsert_kwargs = upsert_args.kwargs
        assert upsert_kwargs["conflict_columns"] == ["content_hash", "publish_time"]
        assert upsert_kwargs["pk_columns"] == ["content_hash", "publish_time"]
        assert isinstance(ids, list)

    @pytest.mark.asyncio
    async def test_requery_sql_is_parameterized_pairwise(self):
        dao = self._dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        await dao.save_market_news_batch(
            [
                {
                    "ts_code": "600519.SH",
                    "source_kind": "news",
                    "title": "a",
                    "publish_time": datetime.datetime(2024, 6, 15),
                },
                {
                    "ts_code": "000001.SZ",
                    "source_kind": "news",
                    "title": "b",
                    "publish_time": datetime.datetime(2024, 6, 15),
                },
            ]
        )
        sql = dao._read_db.call_args[0][0]
        params = dao._read_db.call_args[0][1]
        # 复合冲突键 IN 条件：2 条 → 2 组 $N 占位符（R4 参数化，无字符串拼接用户输入）
        assert "WHERE (content_hash, publish_time) IN (($1,$2),($3,$4))" in sql
        # 平铺参数：hash/hash(2条) + 2× publish_time，均为绑定值（非拼入 SQL）
        assert len(params) == 4

    @pytest.mark.asyncio
    async def test_returns_ids_in_doc_order_from_requery(self):
        dao = self._dao()
        ts_a = datetime.datetime(2024, 6, 15)
        ts_b = datetime.datetime(2024, 6, 16)
        rows = [
            {"ts_code": "600519.SH", "source_kind": "news", "title": "a", "publish_time": ts_a},
            {"ts_code": "000001.SZ", "source_kind": "news", "title": "b", "publish_time": ts_b},
        ]
        content_hashes = [MarketDao.build_content_hash("news", "", r["title"], r["ts_code"]) for r in rows]
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [201, 202],
                    "content_hash": content_hashes,
                    "publish_time": [ts_a, ts_b],
                }
            )
        )
        ids = await dao.save_market_news_batch(rows)
        assert ids == [201, 202]

    @pytest.mark.asyncio
    async def test_empty_docs_returns_empty(self):
        dao = self._dao()
        assert await dao.save_market_news_batch([]) == []

    @pytest.mark.asyncio
    async def test_empty_hash_material_skipped(self):
        """announcement 且 content/title 皆空 → 该 doc 被拒绝，不进入反查。"""
        dao = self._dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.save_market_news_batch(
            [
                {
                    "ts_code": "600519.SH",
                    "source_kind": "announcement",
                    "title": None,
                    "publish_time": datetime.datetime(2024, 6, 15),
                },
                {
                    "ts_code": "000001.SZ",
                    "source_kind": "news",
                    "title": "ok",
                    "publish_time": datetime.datetime(2024, 6, 15),
                },
            ]
        )
        # 只有第二个 doc 进入反查 → 单组占位符
        sql = dao._read_db.call_args[0][0]
        assert "IN (($1,$2))" in sql
        assert result == []


# ---------- B4 match_news_to_stock 直接关联（§15.1 用例） ----------
def _cand(*entries):
    return [{"ts_code": c, "name": n} for c, n in entries]


class TestMatchNewsToStock:
    def test_full_code_sh_prefix(self):
        cands = _cand(("600519.SH", "贵州茅台"), ("000001.SZ", "平安银行"))
        assert match_news_to_stock("贵州茅台今日 SH600519 创历史新高", cands) == ["600519.SH"]

    def test_full_code_dot_suffix(self):
        cands = _cand(("600519.SH", "贵州茅台"))
        assert match_news_to_stock("关注 600519.SH 走势", cands) == ["600519.SH"]

    def test_bare_six_digit_in_pool(self):
        cands = _cand(("600519.SH", "贵州茅台"))
        assert match_news_to_stock("茅台 600519 涨停", cands) == ["600519.SH"]

    def test_bare_six_digit_not_in_pool_no_match(self):
        cands = _cand(("000001.SZ", "平安银行"))
        assert match_news_to_stock("白酒龙头 603555 异动", cands) == []

    def test_bare_date_segment_excluded(self):
        """19xx/20xx 日期段不误匹配。"""
        cands = _cand(("000001.SZ", "平安银行"))
        assert match_news_to_stock("资金面 202403 数据显示回暖", cands) == []

    def test_bare_money_amount_excluded(self):
        """大额金额（以 6 位连续数字形态）不误匹配候选代码。"""
        cands = _cand(("600519.SH", "贵州茅台"))
        assert match_news_to_stock("预计投入 888888 万元", cands) == []

    def test_consecutive_number_excluded(self):
        cands = _cand(("000001.SZ", "平安银行"))
        assert match_news_to_stock("测试序列 123456 结束", cands) == []

    def test_full_name_match(self):
        cands = _cand(("600519.SH", "贵州茅台酒股份有限公司"), ("000001.SZ", "平安银行"))
        assert match_news_to_stock("贵州茅台酒股份有限公司发布业绩预告", cands) == ["600519.SH"]

    def test_unique_short_name_match(self):
        """唯一且 >= 3 汉字的简称 → 直接匹配。"""
        cands = _cand(("600519.SH", "茅台酒"))
        assert match_news_to_stock("茅台酒涨价传闻", cands) == ["600519.SH"]

    def test_ambiguous_short_name_not_matched(self):
        """同名简称在候选池非唯一 → 歧义，不直接关联（>= 3 汉字仍不匹配）。"""
        cands = _cand(("000001.SZ", "茅台酒"), ("300001.SZ", "茅台酒"))
        assert match_news_to_stock("茅台酒发布重大事项", cands) == []

    def test_industry_theme_no_match(self):
        """行业/宏观推理（不含代码/名称）不关联。"""
        cands = _cand(("600519.SH", "贵州茅台"), ("000001.SZ", "平安银行"))
        assert match_news_to_stock("白酒板块今日资金净流入，食品饮料走强", cands) == []

    def test_short_two_hanzi_name_not_direct(self):
        """不足 3 汉字的简称不作为直接关联依据。"""
        cands = _cand(("600519.SH", "茅台"), ("000001.SZ", "银行"))  # 2 汉字简称应忽略
        # "银行"仅 2 汉字 → 不匹配；但 "茅台"2 汉字同样不匹配
        assert match_news_to_stock("银行板块拉升", cands) == []


# ---------- B4 dedupe_documents 确定性去重（§8.4） ----------
def _doc(source_kind, title, publish_time, url=None, content_hash=None):
    return {
        "source_kind": source_kind,
        "title": title,
        "publish_time": publish_time,
        "url": url,
        "content_hash": content_hash,
    }


_T = datetime.datetime(2024, 6, 15, 10, 0, 0)


class TestDedupeDocuments:
    def test_url_same_dropped(self):
        kept, dropped = dedupe_documents(
            [_doc("news", "A", _T, url="http://x/1"), _doc("news", "A复制", _T, url="http://x/1")]
        )
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "url_same"

    def test_hash_same_dropped(self):
        kept, dropped = dedupe_documents(
            [_doc("news", "A", _T, content_hash="h1"), _doc("news", "B", _T, content_hash="h1")]
        )
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "content_hash_same"

    def test_title_time_near_dropped_within_10min(self):
        kept, dropped = dedupe_documents(
            [_doc("news", "苹果  大跌", _T), _doc("news", "苹果 大跌", _T + datetime.timedelta(minutes=5))]
        )
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "title_time_similar"

    def test_announcement_same_day_counted_near(self):
        """公告同日算接近，即使无精确时间戳差异。"""
        kept, dropped = dedupe_documents(
            [
                _doc("announcement", "半年报", datetime.datetime(2024, 6, 15), content_hash="a"),
                _doc("announcement", "半年报", datetime.datetime(2024, 6, 15), content_hash="b"),
            ]
        )
        # content_hash 不同、无 url，但标题同 + 同日 → 去重
        assert len(kept) == 1
        assert dropped[0]["dedup_reason"] == "title_time_similar"

    def test_title_same_different_day_kept(self):
        kept, dropped = dedupe_documents(
            [_doc("news", "苹果大跌", _T), _doc("news", "苹果大跌", _T + datetime.timedelta(days=3))]
        )
        assert len(kept) == 2
        assert dropped == []

    def test_cross_source_kind_not_deduplicated(self):
        """跨 source_kind 不去重。"""
        kept, dropped = dedupe_documents(
            [_doc("announcement", "数月报告", _T, content_hash="h1"), _doc("news", "数月报告", _T, content_hash="h1")]
        )
        assert len(kept) == 2
        assert dropped == []
