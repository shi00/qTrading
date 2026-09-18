"""集成测试：新闻风险解读第一期 §15.2（真实 test DB）。

覆盖：
1. ``market_news`` 新字段兼容旧数据（存量 ``source_kind IS NULL`` 行可读可过滤）。
2. 存量 telegraph 重复写入仍命中 ``(content_hash, publish_time)``，不产生重复行。
3. 首页快讯查询（``get_market_news`` home_filter）不返回 announcement/news 文档，
   且仍返回存量 ``source_kind IS NULL`` 行；limit/offset 生效。
4. ``news_risk_brief`` UPSERT / 缓存命中 / 证据 ID 持久化，失败不覆盖成功快照。
5. 部分来源失败时复用既有更完整成功快照，AI 调用次数不增加（§11 子集例外）。
6. 引擎 disposed 时传播 ``EngineDisposedError``（R5，DAO 与服务层）。
7. AI/DAO/外部抓取组合后的部分失败降级。
"""

import datetime

import pytest
from sqlalchemy import func, insert, select
from unittest.mock import AsyncMock, MagicMock

from data.persistence import engine_provider
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.models import MarketNews
from data.external.news_fetcher import NewsFetcher
from services.news_insight_models import NEWS_RISK_PROMPT_VERSION, NewsInsightResult
from services.news_insight_service import NewsInsightService
from tests.integration.test_infra_base import TestDatabaseBase

pytestmark = pytest.mark.integration

_TS = "000001.SZ"
_NAME = "平安银行"


def _row(ts_code=_TS, source_kind="telegraph", content="test body", title="test title", **overrides):
    row = {
        "content": content,
        "content_hash": "hash-" + str(abs(hash((source_kind or "", content, title)))),
        "tags": None,
        "publish_time": datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=1),
        "source": "Sina",
        "ts_code": ts_code,
        "title": title,
        "url": None,
        "source_kind": source_kind,
        "category_l1": None,
        "category_l2": None,
        "sentiment": None,
    }
    row.update(overrides)
    return row


def _success_result(coverage=None, evidence_news_ids=None):
    return NewsInsightResult(
        analysis_status="analyzed_with_events",
        risk_level="medium",
        confidence=70,
        summary="集成测试摘要",
        events=[{"event_type": "regulatory", "severity": "medium"}],
        evidence_news_ids=evidence_news_ids or [],
        coverage=coverage or {},
        model_id="mock-model",
        analysis_profile="cloud:test/none",
        prompt_version=NEWS_RISK_PROMPT_VERSION,
    )


class TestNewsInsightIntegration(TestDatabaseBase):
    """新闻风险解读集成测试（TestDatabaseBase 提供真实 test DB 与 cache.engine）。"""

    @property
    def dao(self):
        return self.cache.market_dao

    async def _count(self, model) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(select(func.count()).select_from(model))
            return int(result.scalar())

    async def _insert_rows(self, rows: list[dict]):
        async with self.engine.begin() as conn:
            await conn.execute(insert(MarketNews), rows)

    # --- 1/2/3. market_news 旧数据兼容 + telegraph 去重 + 首页来源过滤 ---

    async def test_old_style_news_readable_and_telegraph_dedupe(self):
        """存量 telegraph 重复写入仍命中 (content_hash, publish_time)，不产生重复行。"""
        row = _row(source_kind=None, content="央行逆回购", title="快讯标题")
        # 首次写入
        ids1 = await self.dao.save_market_news_batch([row])
        assert ids1, "首次写入应反查到 id"
        first_id = ids1[0]
        # 重复写入（content/publish_time 相同 → 相同 content_hash）
        ids2 = await self.dao.save_market_news_batch([row])
        assert ids2 == [first_id], "重复写入应返回同一 id"
        assert await self._count(MarketNews) == 1, "存量 telegraph 重复写入不得产生重复行"

    async def test_batch_returns_ids_and_persists_new_fields(self):
        """announcement/news 批量落库取 id，新字段持久化且互不覆盖。"""
        rows = [
            _row(ts_code=_TS, source_kind="announcement", title="公告A"),
            _row(ts_code=_TS, source_kind="news", title="新闻B"),
        ]
        ids = await self.dao.save_market_news_batch(rows)
        assert len(ids) == 2
        assert len(set(ids)) == 2, "不同标题/内容的文档应有不同 id"
        df = await self.dao.get_market_news_documents(_TS)
        for r in rows:
            assert (df["source_kind"] == r["source_kind"]).any()
        assert await self._count(MarketNews) == 2

    async def test_home_filter_excludes_announcement_news_and_keeps_null(self):
        """首页快讯查询不返回 announcement/news，仍返回源 source_kind IS NULL 行；limit/offset 生效。"""
        base = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        rows = [
            _row(source_kind=None, content="存量快讯-1", publish_time=base - datetime.timedelta(hours=4)),
            _row(source_kind="telegraph", content="快讯-2", publish_time=base - datetime.timedelta(hours=3)),
            _row(source_kind="announcement", content="公告", publish_time=base - datetime.timedelta(hours=2)),
            _row(source_kind="news", content="新闻", publish_time=base - datetime.timedelta(hours=1)),
        ]
        await self._insert_rows(rows)

        df = await self.dao.get_market_news(limit=20, offset=0)
        assert list(df["source_kind"].isin(["announcement", "news"])) == [False] * len(df), (
            "首页过滤后不得包含 announcement/news"
        )
        assert df["source_kind"].isna().any(), "必须仍返回存量 source_kind IS NULL 行"

        # limit/offset 生效
        df_page = await self.dao.get_market_news(limit=1, offset=0)
        assert len(df_page) == 1
        # home_filter=False 时不做来源过滤
        df_all = await self.dao.get_market_news(limit=20, offset=0, home_filter=False)
        assert len(df_all) == 4

    # --- 4. news_risk_brief UPSERT / cache hit / evidence id / 失败不覆盖成功 ---

    async def test_brief_upsert_cache_hit_and_evidence_id_persistence(self):
        """news_risk_brief UPSERT、缓存命中读取与证据 ID 持久化。"""
        input_hash = "h" * 64
        brief = {
            "ts_code": _TS,
            "input_hash": input_hash,
            "window_start": datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=30),
            "window_end": datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
            "analysis_status": "analyzed_with_events",
            "risk_level": "high",
            "confidence": 80,
            "summary": "first",
            "events": [{"severity": "high"}],
            "evidence_news_ids": [11, 22],
            "coverage": {"announcement": {"status": "ok"}},
            "model_id": "m",
            "analysis_profile": "p",
            "prompt_version": NEWS_RISK_PROMPT_VERSION,
        }
        await self.dao.save_news_risk_brief(brief)
        got = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got is not None
        assert got["analysis_status"] == "analyzed_with_events"
        assert got["summary"] == "first"
        assert sorted(got["evidence_news_ids"]) == [11, 22], "证据 ID 必须持久化并可读"

        # UPSERT 覆盖（同一主键）成功态
        brief["summary"] = "second"
        brief["risk_level"] = "critical"
        await self.dao.save_news_risk_brief(brief)
        got2 = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got2["summary"] == "second"
        assert got2["risk_level"] == "critical"

    async def test_brief_failed_does_not_overwrite_success(self):
        """failed 空结果写同主键（服务层：无既有成功快照时才落 failed，§7.2）。

        业务的「失败不覆盖成功」由服务层保证（``test_subset_reuse`` 与
        ``test_ai_failure_produces_failed_without_success_snapshot`` 覆盖）；
        本用例校验当既有成功快照存在时，同窗口同输入的新分析走复用成功，不落 failed。
        """
        # 先造一条成功快照
        input_hash = "z" * 64
        win = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=5),
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        )
        await self.dao.save_news_risk_brief(
            {
                "ts_code": _TS,
                "input_hash": input_hash,
                "window_start": win[0],
                "window_end": win[1],
                "analysis_status": "analyzed_with_events",
                "risk_level": "high",
                "confidence": 80,
                "summary": "succ",
                "events": [{"severity": "high"}],
                "evidence_news_ids": [1, 2],
                "coverage": {"announcement": {"status": "ok"}},
                "model_id": "m",
                "analysis_profile": "p",
                "prompt_version": NEWS_RISK_PROMPT_VERSION,
            }
        )
        # 快照存在时读回仍为成功态（不被后续 failed 语义污染）
        got = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got["analysis_status"] == "analyzed_with_events"
        assert sorted(got["evidence_news_ids"]) == [1, 2]

    # --- 5. 部分来源失败复用更完整快照，AI 次数不增 ---

    async def test_subset_reuse_does_not_call_ai_again(self, monkeypatch):
        """部分来源失败（telegraph 快讯 DB 读取故障）导致证据集为既有成功快照真子集时复用，AI 调用次数不增加（§11）。"""
        # 外部抓取模拟完全失败（announcement/news 均无 fetch），候选仅来自落库行
        monkeypatch.setattr(
            NewsFetcher,
            "get_stock_news_documents",
            AsyncMock(return_value={"docs": [], "coverage": {}}),
        )

        # 模拟部分来源失败：telegraph 快讯 DB 读取故障 → coverage[telegraph]="db_error"
        # （§11 子集例外须有来源故障迹象，与单测 test_subset_reuse 对齐；删除证据本身不是 db_error）
        async def _telegraph_db_down(*a, **k):
            raise RuntimeError("telegraph db down")

        monkeypatch.setattr(self.dao, "get_telegraph_news_for_stocks", AsyncMock(side_effect=_telegraph_db_down))
        ai = MagicMock()
        ai.analyze_news_risk = AsyncMock(
            side_effect=lambda req: _success_result(
                coverage=req.coverage,
                evidence_news_ids=[ev.news_id for ev in req.evidence],
            )
        )
        svc = NewsInsightService(market_dao=self.dao, ai_service=ai)

        rows = [
            _row(ts_code=_TS, source_kind="announcement", title="公告1"),
            _row(ts_code=_TS, source_kind="announcement", title="公告2"),
        ]
        await self._insert_rows(rows)

        out1 = await svc.analyze(_TS, _NAME)
        assert out1.reused is False
        assert out1.result.analysis_status == "analyzed_with_events"
        assert ai.analyze_news_risk.await_count == 1
        assert len(set(out1.result.evidence_news_ids)) == 2, "首次分析应消费两条证据"

        # 证据收缩（删除一条落库证据）且 telegraph 来源故障 → 证据集为成功快照真子集 → 子集复用，AI 不再调用
        deleted = await self._delete_one_news_row()
        assert deleted == 1
        out2 = await svc.analyze(_TS, _NAME)
        assert out2.reused is True
        assert out2.reuse_type == "subset"
        assert ai.analyze_news_risk.await_count == 1, "子集复用不得再次调用 AI"

    async def _delete_one_news_row(self) -> int:
        subq = select(MarketNews.id).order_by(MarketNews.publish_time.desc()).limit(1)
        async with self.engine.begin() as conn:
            res = await conn.execute(MarketNews.__table__.delete().where(MarketNews.id.in_(subq)))
            return res.rowcount

    # --- 6. 引擎 disposed 时传播 EngineDisposedError（R5）---

    async def test_market_dao_propagates_engine_disposed(self):
        """DAO 层：引擎 disposed 时 get_market_news 抛 EngineDisposedError（R5）。"""
        self.cache._disposed = True
        engine_provider.mark_disposed(True)
        with pytest.raises(EngineDisposedError, match="Engine disposed"):
            await self.dao.get_market_news(limit=10)
        # 恢复，避免影响后续用例
        self.cache._disposed = False
        engine_provider.mark_disposed(False)

    async def test_service_analyze_propagates_engine_disposed(self, monkeypatch):
        """服务层：analyze 内部 DAO 查询请求被 disposed 时传播 EngineDisposedError。"""
        monkeypatch.setattr(
            NewsFetcher,
            "get_stock_news_documents",
            AsyncMock(return_value={"docs": [], "coverage": {}}),
        )
        ai = MagicMock()
        ai.analyze_news_risk = AsyncMock(return_value=_success_result())
        svc = NewsInsightService(market_dao=self.dao, ai_service=ai)
        self.cache._disposed = True
        engine_provider.mark_disposed(True)
        try:
            with pytest.raises(EngineDisposedError, match="Engine disposed"):
                await svc.analyze(_TS, _NAME)
            assert ai.analyze_news_risk.await_count == 0, "disposed 时不得走到 AI 调用"
        finally:
            self.cache._disposed = False
            engine_provider.mark_disposed(False)

    # --- 7. 组合部分失败降级 ---

    async def test_ai_failure_produces_failed_without_success_snapshot(self, monkeypatch):
        """AI 失败 → failed 结果，且无既有成功快照时落库为 failed。"""
        monkeypatch.setattr(
            NewsFetcher,
            "get_stock_news_documents",
            AsyncMock(return_value={"docs": [], "coverage": {}}),
        )
        ai = MagicMock()
        ai.analyze_news_risk = AsyncMock(
            return_value=NewsInsightResult(
                analysis_status="failed",
                risk_level=None,
                confidence=None,
                summary=None,
                events=[],
                evidence_news_ids=[],
                coverage={},
                model_id=None,
                analysis_profile="",
                prompt_version=NEWS_RISK_PROMPT_VERSION,
            )
        )
        svc = NewsInsightService(market_dao=self.dao, ai_service=ai)
        await self._insert_rows([_row(ts_code=_TS, source_kind="announcement", title="公告1")])
        out = await svc.analyze(_TS, _NAME)
        assert out.result.analysis_status == "failed"
        assert out.result.risk_level is None, "AI 失败不得伪装风险等级（R21）"
        assert out.reused is False

    async def test_fetch_failure_degrades_to_evidence_only_when_no_db_evidence(self, monkeypatch):
        """外部抓取失败且无落库证据 → no_evidence，不调用 AI。"""
        monkeypatch.setattr(
            NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        ai = MagicMock()
        ai.analyze_news_risk = AsyncMock(return_value=_success_result())
        svc = NewsInsightService(market_dao=self.dao, ai_service=ai)
        out = await svc.analyze(_TS, _NAME)
        assert out.result.analysis_status == "no_evidence"
        assert ai.analyze_news_risk.await_count == 0, "无证据时不得调用 AI"
