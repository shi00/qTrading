"""NewsInsightService 单元测试（新闻风险解读第一期 Phase C，R19）。

覆盖：纯工具（_window / _analysis_profile / _build_input_hash / _row_to_result /
_sort_pub / _fill_time_range）、证据加载（_load_evidence 各来源/降级/取消分支）、
持久化（_persist 成功/失败/降级不持久化）、preview 标签、主流程 analyze
（no_evidence / cache_hit / subset / 无缓存 AI 编排 / regenerate 跳过缓存）。

isolate：注入 mock economy DAO / AI service，monkeypatch NewsFetcher 静态方法、
dedupe_documents / match_news_to_stock / get_now / ConfigHandler，避免真实网络/时间。
"""

from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock

import services.news_insight_service as module
from data.persistence.daos.base_dao import EngineDisposedError
from services.news_insight_models import (
    NEWS_RISK_PROMPT_VERSION,
    EvidenceDocument,
    NewsInsightResult,
)
from services.news_insight_service import NewsInsightService, NewsInsightSourceDbError
from utils.config_handler import ConfigHandler

pytestmark = pytest.mark.unit

CST = ZoneInfo("Asia/Shanghai")


def _make_dao() -> MagicMock:
    dao = MagicMock()
    dao.save_market_news_batch = AsyncMock()
    dao.get_market_news_documents = AsyncMock(return_value=pd.DataFrame())
    dao.get_telegraph_news_for_stocks = AsyncMock(return_value=pd.DataFrame())
    dao.save_news_risk_brief = AsyncMock()
    dao.get_news_risk_brief = AsyncMock(return_value=None)
    dao.get_latest_success_brief = AsyncMock(return_value=None)
    return dao


def _make_ai() -> MagicMock:
    ai = MagicMock()
    ai.analyze_news_risk = AsyncMock(
        return_value=NewsInsightResult(
            analysis_status="analyzed_with_events",
            risk_level="high",
            confidence=80,
            summary="s",
            events=[{"severity": "high"}],
            evidence_news_ids=[1],
            coverage={},
            model_id="local",
            analysis_profile="",
            prompt_version=NEWS_RISK_PROMPT_VERSION,
        )
    )
    return ai


# --- _window ---


def _validating_log_classified(*args, **kwargs):
    """校验 log_classified 占位符数与实参匹配（红线回归）。

    utils.error_classifier.log_classified(logger, exc, context, msg, *fmt_args)
    内部固定填充 code + sanitize_error 两个 %s，再透传 *fmt_args：
    因此 ``msg.count('%s') == 2 + len(fmt_args)``，否则 logging 阶段抛
    TypeError 掩盖原始异常。测试设为 no-op 时无法暴露该 bug，改用 spy 断言。
    """
    assert len(args) >= 4, f"log_classified 缺少 msg: {args!r}"
    _logger, _exc, _context, msg = args[:4]
    fmt_args = args[4:]
    assert msg.count("%s") == 2 + len(fmt_args), f"log_classified 占位符/实参不匹配: msg={msg!r}, fmt_args={fmt_args!r}"
    return None


class TestWindow:
    def test_window_shape_and_bounds(self, monkeypatch):
        fixed = datetime.datetime(2026, 9, 16, 12, 30, 0).replace(tzinfo=CST)
        monkeypatch.setattr(module, "get_now", lambda: fixed)
        start_utc, end_utc, start_date, end_date = NewsInsightService._window()
        assert isinstance(start_utc, datetime.datetime)
        assert isinstance(end_utc, datetime.datetime)
        assert start_date < end_date
        assert end_utc > start_utc
        # 窗口覆盖 30 个自然日
        assert (end_date - start_date).days == _WINDOW_DAYS - 1

    def test_window_start_is_midnight_prev_day_utc(self, monkeypatch):
        fixed = datetime.datetime(2026, 9, 16, 12, 30, 0).replace(tzinfo=CST)
        monkeypatch.setattr(module, "get_now", lambda: fixed)
        start_utc, _end_utc, start_date, end_date = NewsInsightService._window()
        assert start_date == datetime.date(2026, 8, 18)
        assert end_date == datetime.date(2026, 9, 16)
        # start_midnight CST = 前一日 16:00 UTC naive
        assert start_utc == datetime.datetime(2026, 8, 17, 16, 0, 0)

    def test_window_end_is_235959_cst(self, monkeypatch):
        fixed = datetime.datetime(2026, 9, 16, 12, 30, 0).replace(tzinfo=CST)
        monkeypatch.setattr(module, "get_now", lambda: fixed)
        _start_utc, end_utc, _sd, _ed = NewsInsightService._window()
        # 23:59:59 CST = 15:59:59 UTC naive
        assert end_utc == datetime.datetime(2026, 9, 16, 15, 59, 59)


# 最大可读天数常量（从源码对齐）
_WINDOW_DAYS = 30


# --- pure utils ---


class TestAnalysisProfile:
    def test_normal_path_cloud_and_local(self, monkeypatch):
        monkeypatch.setattr(ConfigHandler, "get_llm_config", lambda: {"provider": "deepseek", "model": "chat"})
        monkeypatch.setattr(ConfigHandler, "get_setting", lambda key, default=None: "/opt/models/llm.bin")
        assert NewsInsightService._analysis_profile() == "cloud:deepseek/chat,local:llm.bin"

    def test_no_provider_model_cloud_none(self, monkeypatch):
        monkeypatch.setattr(ConfigHandler, "get_llm_config", lambda: {})
        monkeypatch.setattr(ConfigHandler, "get_setting", lambda key, default=None: None)
        assert NewsInsightService._analysis_profile() == "cloud:none"

    def test_llm_config_error_yields_cloud_none(self, monkeypatch):
        def boom():
            raise RuntimeError("cfg")

        monkeypatch.setattr(ConfigHandler, "get_llm_config", boom)
        monkeypatch.setattr(ConfigHandler, "get_setting", lambda key, default=None: None)
        # 隔离日志边界：异常路径触发 log_classified，其消息模板/参数透传由生产代码决定，
        # 测试不依赖其格式化实现，仅验证业务分支（异常 → cloud:none）。
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        assert NewsInsightService._analysis_profile() == "cloud:none"

    def test_local_path_missing_omits_local(self, monkeypatch):
        monkeypatch.setattr(ConfigHandler, "get_llm_config", lambda: {"provider": "deepseek", "model": "chat"})
        monkeypatch.setattr(ConfigHandler, "get_setting", lambda key, default=None: "")
        assert NewsInsightService._analysis_profile() == "cloud:deepseek/chat"

    def test_local_get_setting_error_still_cloud(self, monkeypatch):
        def boom(key, default=None):
            raise RuntimeError("path")

        monkeypatch.setattr(ConfigHandler, "get_llm_config", lambda: {"provider": "deepseek", "model": "chat"})
        monkeypatch.setattr(ConfigHandler, "get_setting", boom)
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        assert NewsInsightService._analysis_profile() == "cloud:deepseek/chat"


class TestBuildInputHash:
    def test_deterministic_and_sorted(self):
        h1 = NewsInsightService._build_input_hash(
            "000001.SZ", datetime.date(2026, 8, 18), datetime.date(2026, 9, 16), ["b", "a"], "profile"
        )
        h2 = NewsInsightService._build_input_hash(
            "000001.SZ", datetime.date(2026, 8, 18), datetime.date(2026, 9, 16), ["a", "b"], "profile"
        )
        h3 = NewsInsightService._build_input_hash(
            "000001.SZ", datetime.date(2026, 8, 18), datetime.date(2026, 9, 16), ["a", "b"], "profile2"
        )
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # sha256 hexdigest

    def test_filters_empty_hashes(self):
        h = NewsInsightService._build_input_hash(
            "000001.SZ", datetime.date(2026, 8, 18), datetime.date(2026, 9, 16), ["", "a", None], "p"
        )
        assert len(h) == 64


class TestRowToResult:
    def test_missing_fields_defaults(self):
        r = NewsInsightService._row_to_result({}, {})
        assert r.analysis_status == "failed"
        assert r.risk_level is None
        assert r.confidence is None
        assert r.summary is None
        assert r.events == []
        assert r.evidence_news_ids == []
        assert r.coverage == {}
        assert r.model_id is None
        assert r.analysis_profile == ""
        assert r.prompt_version == NEWS_RISK_PROMPT_VERSION

    def test_full_row_passed_through(self):
        row = {
            "analysis_status": "analyzed_with_events",
            "risk_level": "high",
            "confidence": 75,
            "summary": "x",
            "events": [{"severity": "high"}],
            "evidence_news_ids": [1, 2],
            "coverage": {"announcement": {"status": "ok"}},
            "model_id": "m",
            "analysis_profile": "p",
        }
        r = NewsInsightService._row_to_result(row, {})
        assert r.analysis_status == "analyzed_with_events"
        assert r.risk_level == "high"
        assert r.confidence == 75
        assert r.evidence_news_ids == [1, 2]
        assert r.model_id == "m"
        assert r.coverage == {"announcement": {"status": "ok"}}

    def test_coverage_param_priority(self):
        r = NewsInsightService._row_to_result({"coverage": {"announcement": {"x": 1}}}, {"news": {"y": 2}})
        assert r.coverage == {"news": {"y": 2}}


class TestModulePureFunctions:
    def test_sort_pub_none_last(self):
        ts = datetime.datetime(2026, 9, 1, 12, 0, 0)
        assert module._sort_pub({"publish_time": ts}) == ts
        assert module._sort_pub({}) == datetime.datetime.max

    def test_fill_time_range(self):
        src = {"earliest": None, "latest": None}
        d1 = datetime.datetime(2026, 9, 1, 12, 0, 0)
        d2 = datetime.datetime(2026, 9, 5, 8, 0, 0)
        module._fill_time_range(src, [{"publish_time": d2}, {"publish_time": d1}, {"publish_time": None}])
        assert src["earliest"] == d1.isoformat()
        assert src["latest"] == d2.isoformat()

    def test_fill_time_range_no_times(self):
        src = {"earliest": None, "latest": None}
        module._fill_time_range(src, [{"publish_time": None}])
        assert src["earliest"] is None
        assert src["latest"] is None


# --- _load_evidence ---


def _candidate_df(rows):
    cols = [
        "id",
        "source_kind",
        "source",
        "title",
        "content",
        "publish_time",
        "url",
        "content_hash",
        "sentiment",
    ]
    data = {c: [r.get(c) for r in rows] for c in cols}
    return pd.DataFrame(data)


class TestLoadEvidence:
    def test_full_success(self, monkeypatch):
        dao = _make_dao()
        pub = datetime.datetime(2026, 9, 10, 8, 0, 0)
        monkeypatch.setattr(
            module.NewsFetcher,
            "get_stock_news_documents",
            AsyncMock(
                return_value={
                    "docs": [
                        {"source_kind": "news", "title": "n1"},
                        {"source_kind": "news", "title": "n2"},
                    ],
                    "coverage": {"announcement": "ok", "news": "fail"},
                }
            ),
        )
        dao.get_market_news_documents = AsyncMock(
            return_value=_candidate_df(
                [
                    {
                        "id": 1,
                        "source_kind": "announcement",
                        "title": "公告",
                        "content": "正文",
                        "publish_time": pub,
                        "content_hash": "h1",
                    },
                    {
                        "id": 2,
                        "source_kind": "news",
                        "title": "新闻",
                        "content": "内容",
                        "publish_time": pub,
                        "content_hash": "h2",
                    },
                ]
            )
        )
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())

        evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", "平安银行", pub, pub, None))

        assert len(evidence) == 2
        assert evidence[0].source_kind == "announcement"
        assert evidence[0].quoteable_text  # neutralize 生成摘录
        assert evidence[1].source_kind == "news"
        # coverage: announcement status 回填自 fetched_coverage；adopted 计算
        assert coverage["announcement"]["status"] == "ok"
        assert coverage["announcement"]["adopted"] == 1
        assert coverage["news"]["adopted"] == 1
        assert coverage["announcement"]["fetched"] == 0
        assert coverage["news"]["fetched"] == 2
        assert coverage["announcement"]["earliest"] == pub.isoformat()
        dao.save_market_news_batch.assert_awaited_once()
        dao.get_market_news_documents.assert_awaited_once()
        dao.get_telegraph_news_for_stocks.assert_awaited_once()
        # evidence 顺序：公告优先（announcement 在前）
        assert evidence[0].source_kind == "announcement"

    def test_top12_truncation(self, monkeypatch):
        dao = _make_dao()
        rows = []
        for i in range(13):
            rows.append(
                {
                    "id": i,
                    "source_kind": "news",
                    "title": f"n{i}",
                    "content": "c",
                    "publish_time": datetime.datetime(2026, 9, 10, 8, 0, 0) + datetime.timedelta(minutes=i),
                    "content_hash": f"h{i}",
                }
            )
        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_market_news_documents = AsyncMock(return_value=_candidate_df(rows))
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        pub = datetime.datetime(2026, 9, 10, 8, 0, 0)

        evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))
        assert len(evidence) == 12
        assert coverage["news"]["adopted"] == 12

    def test_telegraph_matched(self, monkeypatch):
        dao = _make_dao()
        pub = datetime.datetime(2026, 9, 10, 8, 0, 0)
        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_telegraph_news_for_stocks = AsyncMock(
            return_value=_candidate_df(
                [
                    {
                        "id": 99,
                        "title": "000001.SZ 重大事项",
                        "source_kind": "telegraph",
                        "publish_time": pub,
                    }
                ]
            )
        )
        # 命中：文本含完整代码
        monkeypatch.setattr(module, "match_news_to_stock", lambda text, pool: ["000001.SZ"] if "000001" in text else [])
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())

        evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))
        assert any(ev.source_kind == "telegraph" for ev in evidence)
        assert coverage["telegraph"]["status"] == "ok"
        assert coverage["telegraph"]["adopted"] == 1
        # §8.1 telegraph 按自然日条数分布（publish_time 归一化）
        assert coverage["telegraph"]["daily_counts"]  # 非空

    def test_telegraph_daily_counts_exposes_hole(self, monkeypatch):
        """§15.1 覆盖统计：本地快讯存在整段空洞时，按日分布必须暴露空洞。

        窗口内 Day1 与 Day3 各有 telegraph，Day2 无条目 → daily_counts keys 不得被
        填充为横跨首尾的覆盖（Day2 必须缺位，暴露该空洞）。
        """
        dao = _make_dao()
        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        day1 = datetime.datetime(2026, 9, 10, 9, 0, 0)
        day3 = datetime.datetime(2026, 9, 12, 9, 0, 0)
        dao.get_telegraph_news_for_stocks = AsyncMock(
            return_value=_candidate_df(
                [
                    {"id": 1, "title": "000001.SZ 一", "source_kind": "telegraph", "publish_time": day1},
                    {"id": 2, "title": "000001.SZ 二", "source_kind": "telegraph", "publish_time": day3},
                ]
            )
        )
        monkeypatch.setattr(module, "match_news_to_stock", lambda text, pool: ["000001.SZ"] if "000001" in text else [])
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())

        window_start = datetime.datetime(2026, 9, 10, 0, 0, 0)
        window_end = datetime.datetime(2026, 9, 12, 23, 59, 59)
        _evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", None, window_start, window_end, None))

        daily = coverage["telegraph"]["daily_counts"]
        # 空洞暴露：Day2 不存在条目，必须缺位而非被填充成覆盖值
        assert daily.get("2026-09-10") == 1
        assert daily.get("2026-09-12") == 1
        assert "2026-09-11" not in daily

    def test_fetch_failure_still_reads_db(self, monkeypatch):
        dao = _make_dao()

        async def boom(*a, **k):
            raise RuntimeError("net")

        monkeypatch.setattr(module.NewsFetcher, "get_stock_news_documents", boom)
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        dao.get_market_news_documents = AsyncMock(
            return_value=_candidate_df(
                [
                    {
                        "id": 1,
                        "source_kind": "news",
                        "title": "n",
                        "content": "c",
                        "publish_time": datetime.datetime(2026, 9, 10),
                    }
                ]
            )
        )
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        pub = datetime.datetime(2026, 9, 10)

        evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))
        assert len(evidence) == 1
        assert coverage["announcement"]["status"] == "fail"
        assert coverage["news"]["status"] == "fail"

    def test_save_batch_failure_continues(self, monkeypatch):
        dao = _make_dao()
        pub = datetime.datetime(2026, 9, 10)

        async def boom(*a, **k):
            raise RuntimeError("save")

        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.save_market_news_batch = AsyncMock(side_effect=boom)
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        dao.get_market_news_documents = AsyncMock(
            return_value=_candidate_df(
                [{"id": 1, "source_kind": "news", "title": "n", "content": "c", "publish_time": pub}]
            )
        )
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())

        evidence, _ = asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))
        assert len(evidence) == 1

    def test_telegraph_db_error_marks_db_error(self, monkeypatch):
        """对抗性检视 Major①：telegraph 快讯 DB 读取失败标记 db_error（非实时抓取 fail）。"""
        dao = _make_dao()

        async def boom(*a, **k):
            raise RuntimeError("telegraph")

        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_telegraph_news_for_stocks = AsyncMock(side_effect=boom)
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        pub = datetime.datetime(2026, 9, 10)

        evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))
        assert evidence == []
        assert coverage["telegraph"]["status"] == "db_error"

    def test_documents_db_error_marks_db_error(self, monkeypatch):
        """对抗性检视 Major①：get_market_news_documents DB 故障不再被吞成空证据。

        _load_evidence 须捕获读异常并把对应来源 coverage status 置为 ``db_error``，
        不得落入 ``fail``（fail 语义是"实时抓取失败"，db_error 语义是"数据库读取失败"）。
        """
        dao = _make_dao()

        async def boom(*a, **k):
            raise RuntimeError("db-down")

        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_market_news_documents = AsyncMock(side_effect=boom)
        dao.get_telegraph_news_for_stocks = AsyncMock(return_value=pd.DataFrame())
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        pub = datetime.datetime(2026, 9, 10)

        evidence, coverage = asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))
        assert evidence == []
        assert coverage["announcement"]["status"] == "db_error"
        assert coverage["news"]["status"] == "db_error"

    def test_all_sources_db_error_raises(self, monkeypatch):
        """对抗性检视 Major① + 用户决策：全部证据来源均 db_error 时抛可识别异常。"""
        dao = _make_dao()

        async def boom(*a, **k):
            raise RuntimeError("db-down")

        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_market_news_documents = AsyncMock(side_effect=boom)
        dao.get_telegraph_news_for_stocks = AsyncMock(side_effect=boom)
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        pub = datetime.datetime(2026, 9, 10)

        with pytest.raises(NewsInsightSourceDbError):  # noqa: weak-assertion 全来源 DB 故障须暴露可识别异常，异常类型即测试目标
            asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))

    def test_engine_disposed_propagates_from_documents(self, monkeypatch):
        """R5：get_market_news_documents 抛 EngineDisposedError 时须传播，不得吞成 db_error。"""
        dao = _make_dao()

        async def boom(*a, **k):
            raise EngineDisposedError("Engine disposed")

        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_market_news_documents = AsyncMock(side_effect=boom)
        dao.get_telegraph_news_for_stocks = AsyncMock(return_value=pd.DataFrame())
        monkeypatch.setattr(module, "log_classified", _validating_log_classified)
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        pub = datetime.datetime(2026, 9, 10)

        with pytest.raises(EngineDisposedError):  # noqa: weak-assertion R5 僵尸引擎错误须显式传播，异常类型即测试目标
            asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, None))

    def test_cancel_propagates(self, monkeypatch):
        dao = _make_dao()
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        ev = asyncio.Event()
        ev.set()
        pub = datetime.datetime(2026, 9, 10)

        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 守卫：验证 _maybe_cancel 截断协同传播 CancelledError，异常类型传播即测试目标
            asyncio.run(svc._load_evidence("000001.SZ", None, pub, pub, ev))


# --- _persist ---


class TestPersist:
    def test_success_saved(self):
        dao = _make_dao()
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        result = _result("analyzed_with_events")
        asyncio.run(
            svc._persist("000001.SZ", "h", datetime.datetime(2026, 9, 1), datetime.datetime(2026, 9, 10), result)
        )
        dao.save_news_risk_brief.assert_awaited_once()
        dao.get_news_risk_brief.assert_not_awaited()

    def test_failed_no_existing_saved(self):
        dao = _make_dao()
        dao.get_news_risk_brief = AsyncMock(return_value=None)
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        result = _result("failed")
        asyncio.run(
            svc._persist("000001.SZ", "h", datetime.datetime(2026, 9, 1), datetime.datetime(2026, 9, 10), result)
        )
        dao.get_news_risk_brief.assert_awaited_once()
        dao.save_news_risk_brief.assert_awaited_once()

    def test_failed_with_existing_success_not_saved(self, caplog):
        dao = _make_dao()
        dao.get_news_risk_brief = AsyncMock(
            return_value={"analysis_status": "analyzed_with_events", "evidence_news_ids": [1]}
        )
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        result = _result("failed")
        asyncio.run(
            svc._persist("000001.SZ", "h", datetime.datetime(2026, 9, 1), datetime.datetime(2026, 9, 10), result)
        )
        dao.save_news_risk_brief.assert_not_awaited()
        assert any("failed analysis not persisted" in r.message for r in caplog.records)

    def test_evidence_only_not_persisted(self):
        dao = _make_dao()
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        result = _result("evidence_only")
        asyncio.run(
            svc._persist("000001.SZ", "h", datetime.datetime(2026, 9, 1), datetime.datetime(2026, 9, 10), result)
        )
        dao.save_news_risk_brief.assert_not_awaited()

    def test_no_evidence_not_persisted(self):
        dao = _make_dao()
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        result = _result("no_evidence")
        asyncio.run(
            svc._persist("000001.SZ", "h", datetime.datetime(2026, 9, 1), datetime.datetime(2026, 9, 10), result)
        )
        dao.save_news_risk_brief.assert_not_awaited()


def _result(status: str, evidence_ids=None) -> NewsInsightResult:
    return NewsInsightResult(
        analysis_status=status,
        risk_level="high" if status in ("analyzed_with_events", "analyzed_no_event") else None,
        confidence=80,
        summary="s",
        events=[{"severity": "high"}] if status == "analyzed_with_events" else [],
        evidence_news_ids=evidence_ids or [1],
        coverage={},
        model_id="m",
        analysis_profile="p",
        prompt_version=NEWS_RISK_PROMPT_VERSION,
    )


# --- load_evidence_preview / label ---


class TestPreview:
    def test_missing_code_raises(self):
        svc = NewsInsightService(market_dao=_make_dao(), ai_service=_make_ai())
        with pytest.raises(ValueError):  # noqa: weak-assertion 空 ts_code 输入校验守卫，异常类型即测试目标
            asyncio.run(svc.load_evidence_preview("", None))

    def test_normal(self, monkeypatch):
        dao = _make_dao()
        monkeypatch.setattr(
            module.NewsFetcher, "get_stock_news_documents", AsyncMock(return_value={"docs": [], "coverage": {}})
        )
        dao.get_market_news_documents = AsyncMock(
            return_value=_candidate_df(
                [
                    {
                        "id": 1,
                        "source_kind": "news",
                        "title": "n",
                        "content": "c",
                        "publish_time": datetime.datetime(2026, 9, 10),
                    }
                ]
            )
        )
        svc = NewsInsightService(market_dao=dao, ai_service=_make_ai())
        evidence, coverage = asyncio.run(svc.load_evidence_preview("000001.SZ", "平安银行"))
        assert len(evidence) == 1
        assert isinstance(coverage, dict)

    def test_window_label(self, monkeypatch):
        fixed = datetime.datetime(2026, 9, 16, 12, 30, 0).replace(tzinfo=CST)
        monkeypatch.setattr(module, "get_now", lambda: fixed)
        svc = NewsInsightService(market_dao=_make_dao(), ai_service=_make_ai())
        assert svc.analysis_window_label() == "2026-08-18 ~ 2026-09-16"


# --- analyze ---


def _make_svc_with_fake_load(monkeypatch, dao=None, ai=None, *, evidence, coverage=None):
    dao = dao or _make_dao()
    ai = ai or _make_ai()
    svc = NewsInsightService(market_dao=dao, ai_service=ai)
    _coverage = coverage if coverage is not None else {"announcement": {"status": "ok"}}

    async def fake_load(ts, name, ws, we, ce):
        return (list(evidence), _coverage)

    monkeypatch.setattr(svc, "_load_evidence", fake_load)
    return svc, dao, ai


class TestAnalyze:
    def test_missing_code_raises(self):
        svc = NewsInsightService(market_dao=_make_dao(), ai_service=_make_ai())
        with pytest.raises(ValueError):  # noqa: weak-assertion 空 ts_code 输入校验守卫，异常类型即测试目标
            asyncio.run(svc.analyze(""))

    def test_no_evidence(self, monkeypatch):
        svc, dao, ai = _make_svc_with_fake_load(monkeypatch, evidence=[])
        outcome = asyncio.run(svc.analyze("000001.SZ"))
        assert outcome.result.analysis_status == "no_evidence"
        assert outcome.reused is False
        ai.analyze_news_risk.assert_not_awaited()

    def test_empty_evidence_with_db_error_raises(self, monkeypatch):
        """对抗性检视 Major①：空证据 + 任一来源 db_error 时，不得落入 no_evidence。

        §8.1 不得把"未观测数据"表述为"不存在"；§10.3 数据库缓存读取失败仍可尝试即时生成。
        DB 读取故障应以可识别异常向上暴露（VM 映射 error 可重试），而非伪装 no_evidence。
        """
        svc, _dao, ai = _make_svc_with_fake_load(
            monkeypatch,
            evidence=[],
            coverage={"announcement": {"status": "db_error"}},
        )
        with pytest.raises(NewsInsightSourceDbError):  # noqa: weak-assertion db_error 须显式失败并携带故障语义，异常类型即测试目标
            asyncio.run(svc.analyze("000001.SZ"))
        ai.analyze_news_risk.assert_not_awaited()

    def test_empty_evidence_with_partial_db_error_raises(self, monkeypatch):
        """空证据但仅部分来源 db_error：仍不得伪装 no_evidence（§8.1 不得把未观测数据表述为不存在）。"""
        svc, _dao, ai = _make_svc_with_fake_load(
            monkeypatch,
            evidence=[],
            coverage={
                "announcement": {"status": "fail"},
                "news": {"status": "fail"},
                "telegraph": {"status": "db_error"},
            },
        )
        with pytest.raises(NewsInsightSourceDbError):  # noqa: weak-assertion 与 test_empty_evidence_with_db_error_raises 同理
            asyncio.run(svc.analyze("000001.SZ"))
        ai.analyze_news_risk.assert_not_awaited()

    def test_empty_evidence_without_db_error_stays_no_evidence(self, monkeypatch):
        """回归守卫：无 db_error 时空证据仍按既有契约走 no_evidence（§10.3 正常无证据）。"""
        svc, dao, ai = _make_svc_with_fake_load(
            monkeypatch,
            evidence=[],
            coverage={"announcement": {"status": "fail"}, "news": {"status": "fail"}, "telegraph": {"status": "fail"}},
        )
        outcome = asyncio.run(svc.analyze("000001.SZ"))
        assert outcome.result.analysis_status == "no_evidence"
        ai.analyze_news_risk.assert_not_awaited()

    def test_cache_hit(self, monkeypatch):
        hit = {
            "analysis_status": "analyzed_with_events",
            "risk_level": "high",
            "confidence": 70,
            "summary": "x",
            "events": [{"severity": "high"}],
            "evidence_news_ids": [1],
            "model_id": "local",
        }
        dao = _make_dao()
        dao.get_news_risk_brief = AsyncMock(return_value=hit)
        svc, _, ai = _make_svc_with_fake_load(
            monkeypatch, dao=dao, evidence=[EvidenceDocument(news_id=1, content_hash="h")]
        )
        outcome = asyncio.run(svc.analyze("000001.SZ"))
        assert outcome.reused is True
        assert outcome.reuse_type == "cache_hit"
        assert outcome.result.analysis_status == "analyzed_with_events"
        assert outcome.result.risk_level == "high"
        ai.analyze_news_risk.assert_not_awaited()
        # cache_hit 不重复持久化
        dao.save_news_risk_brief.assert_not_awaited()

    def test_subset_reuse(self, monkeypatch):
        latest = {
            "analysis_status": "analyzed_with_events",
            "risk_level": "medium",
            "confidence": 60,
            "summary": "更完整",
            "events": [],
            "evidence_news_ids": [1, 2, 3],
            "model_id": "local",
        }
        dao = _make_dao()
        dao.get_news_risk_brief = AsyncMock(return_value=None)
        dao.get_latest_success_brief = AsyncMock(return_value=latest)
        svc, _, ai = _make_svc_with_fake_load(
            monkeypatch,
            dao=dao,
            evidence=[EvidenceDocument(news_id=1, content_hash="h")],
            coverage={"announcement": {"status": "db_error"}, "news": {"status": "ok"}, "telegraph": {"status": "ok"}},
        )
        outcome = asyncio.run(svc.analyze("000001.SZ"))
        assert outcome.reused is True
        assert outcome.reuse_type == "subset"
        assert outcome.result.summary == "更完整"
        ai.analyze_news_risk.assert_not_awaited()

    def test_no_subset_without_db_error(self, monkeypatch):
        """Minor 假子集：无 db_error 时证据集为既有快照真子集不得误报 reuse_type='subset'。

        去重/top-12 截取导致的真子集不是"部分来源失败"，应继续走 AI 分析而非复用历史快照。
        """
        latest = {
            "analysis_status": "analyzed_with_events",
            "risk_level": "medium",
            "confidence": 60,
            "summary": "更完整",
            "events": [],
            "evidence_news_ids": [1, 2, 3],
            "model_id": "local",
        }
        dao = _make_dao()
        dao.get_news_risk_brief = AsyncMock(return_value=None)
        dao.get_latest_success_brief = AsyncMock(return_value=latest)
        svc, _, ai = _make_svc_with_fake_load(
            monkeypatch,
            dao=dao,
            evidence=[EvidenceDocument(news_id=1, content_hash="h")],
            coverage={"announcement": {"status": "ok"}, "news": {"status": "ok"}, "telegraph": {"status": "ok"}},
        )
        outcome = asyncio.run(svc.analyze("000001.SZ"))
        assert outcome.reused is False
        assert outcome.reuse_type == "none"
        ai.analyze_news_risk.assert_awaited_once()

    def test_no_cache_calls_ai_and_persists(self, monkeypatch):
        dao = _make_dao()
        svc, _, ai = _make_svc_with_fake_load(
            monkeypatch, dao=dao, evidence=[EvidenceDocument(news_id=1, content_hash="h")]
        )
        result = asyncio.run(svc.analyze("000001.SZ"))
        ai.analyze_news_risk.assert_awaited_once()
        assert result.reused is False
        assert result.result.analysis_status == "analyzed_with_events"
        dao.save_news_risk_brief.assert_awaited_once()

    def test_regenerate_skips_cache(self, monkeypatch):
        hit = {
            "analysis_status": "analyzed_with_events",
            "risk_level": "high",
            "evidence_news_ids": [1],
        }
        dao = _make_dao()
        dao.get_news_risk_brief = AsyncMock(return_value=hit)
        svc, _, ai = _make_svc_with_fake_load(
            monkeypatch, dao=dao, evidence=[EvidenceDocument(news_id=1, content_hash="h")]
        )
        asyncio.run(svc.analyze("000001.SZ", regenerate=True))
        ai.analyze_news_risk.assert_awaited_once()
