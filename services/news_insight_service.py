"""新闻风险解读服务编排（新闻风险解读第一期 Phase C，设计方案 §6.1/§8/§9/§10/§11）。

``NewsInsightService`` 是 data 层之上的纯编排服务（不依赖 UI），承载：
- 证据加载：实时抓取巨潮公告 + 东财新闻落库取 id + 首页快讯直接关联
  （``NewsFetcher`` / ``MarketDao`` / ``data.news_match``）；
- 确定性去重（§8.4）、公告优先截取 top-12（§9.1）、逐条 ``neutralize_external_text``
  生成 ``quoteable_text``（§10.1）；
- 缓存与子集例外（§11）：成功快照命中 / 部分来源失败复用更完整历史快照；
- AI 编排与降级（§10.3）：无证据→``no_evidence``、AI 不可用→``evidence_only``；
- 快照持久化（§7.2）：仅成功态作可缓存快照；``failed`` 仅在无既有成功快照时落库。

设计约束：本服务为可注入的普通服务（不在单例注册表内，R7/§16 优先可注入服务），
构造注入 ``market_dao`` / ``ai_service`` 便于测试隔离。
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import os
from dataclasses import dataclass

from data.cache.cache_manager import CacheManager
from data.external.news_fetcher import NewsFetcher
from data.news_match import dedupe_documents, match_news_to_stock
from services.ai_service import AIService
from services.news_insight_models import (
    NEWS_RISK_PROMPT_VERSION,
    SUCCESS_STATUS,
    EvidenceDocument,
    NewsInsightRequest,
    NewsInsightResult,
)
from utils.config_handler import ConfigHandler
from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.prompt_guard import neutralize_external_text
from utils.time_utils import from_utc_to_cst, get_now, to_utc_for_db

logger = logging.getLogger(__name__)

# 分析窗口（§9.1 最近 30 个自然日，含首尾）
_WINDOW_DAYS = 30
# 进 Prompt 的最大证据条数（§9.1）
_EVIDENCE_LIMIT = 12
# 单条 quoteable_text 上限（§9.1）
_QUOTEABLE_MAX_LEN = 500


@dataclass
class NewsInsightOutcome:
    """服务编排结果：``result`` 为结构化风险简报；``reused`` 标记是否复用历史快照。

    ``reuse_type`` 取值：``none`` / ``cache_hit``（§11 相同输入命中）/ ``subset``
    （§11 子集例外，部分来源失败复用更完整历史快照）。
    """

    result: NewsInsightResult
    reused: bool
    reuse_type: str = "none"


class NewsInsightService:
    """按需新闻风险解读编排（可注入普通服务，非单例）。"""

    def __init__(self, market_dao=None, ai_service: AIService | None = None) -> None:
        self.dao = market_dao if market_dao is not None else CacheManager().market_dao
        self.ai = ai_service if ai_service is not None else AIService()

    # ------------------------------------------------------------------
    # 纯工具
    # ------------------------------------------------------------------

    @staticmethod
    def _analysis_profile() -> str:
        """不含凭据的 provider/model 配置指纹（§7.2 §11）。配置变更时使缓存失效。"""
        parts: list[str] = []
        try:
            llm = ConfigHandler.get_llm_config() or {}
            provider = llm.get("provider", "")
            model = llm.get("model", "")
            parts.append(f"cloud:{provider}/{model}" if provider and model else "cloud:none")
        except Exception as e:
            log_classified(logger, e, "general", "[NewsInsight] read llm config failed: %s")
            parts.append("cloud:none")
        try:
            local_path = ConfigHandler.get_setting("local_model_path")
            if local_path:
                parts.append(f"local:{os.path.basename(os.fspath(local_path))}")
        except Exception as e:
            log_classified(logger, e, "general", "[NewsInsight] read local model path failed: %s")
        return ",".join(sorted(parts))

    @staticmethod
    def _window() -> tuple[datetime.datetime, datetime.datetime, datetime.date, datetime.date]:
        """计算分析窗口（最近 30 个自然日，CST）。

        返回 ``(window_start_utc, window_end_utc, start_cst_date, end_cst_date)``：
        UTC naive 用于 DB 查询边界；CST 日期用于 input_hash（§11 按中国标准时间自然日归一化）。
        """
        now = get_now()
        end_cst = now.replace(hour=23, minute=59, second=59, microsecond=0)
        start_cst = (end_cst - datetime.timedelta(days=_WINDOW_DAYS - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        # start_cst/end_cst 为 tz-aware，to_utc_for_db 必返回非 None（仅 None 入参才返回 None）
        start_utc = to_utc_for_db(start_cst)
        end_utc = to_utc_for_db(end_cst)
        assert start_utc is not None and end_utc is not None
        return (
            start_utc,
            end_utc,
            start_cst.date(),
            end_cst.date(),
        )

    @staticmethod
    def _build_input_hash(
        ts_code: str,
        start_cst: datetime.date,
        end_cst: datetime.date,
        content_hashes: list[str],
        analysis_profile: str,
    ) -> str:
        """§11 input_hash：ts_code + CST 窗口日期 + 排序后证据内容哈希 + prompt 版本 + 配置指纹。"""
        parts = [ts_code, str(start_cst), str(end_cst), NEWS_RISK_PROMPT_VERSION, analysis_profile]
        parts += sorted(h for h in content_hashes if h)
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    async def _maybe_cancel(cancel_event: asyncio.Event | None) -> None:
        """在异步阶段边界检查取消（协同传播 CancelledError，R2）。"""
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()

    @staticmethod
    def _row_to_result(row: dict, coverage: dict) -> NewsInsightResult:
        """把持久化快照行还原为 NewsInsightResult（缓存/子集复用）。"""
        return NewsInsightResult(
            analysis_status=row.get("analysis_status") or "failed",
            risk_level=row.get("risk_level"),
            confidence=row.get("confidence"),
            summary=row.get("summary"),
            events=row.get("events") or [],
            evidence_news_ids=row.get("evidence_news_ids") or [],
            coverage=coverage if coverage else (row.get("coverage") or {}),
            model_id=row.get("model_id"),
            analysis_profile=row.get("analysis_profile") or "",
            prompt_version=row.get("prompt_version") or NEWS_RISK_PROMPT_VERSION,
        )

    # ------------------------------------------------------------------
    # 证据加载
    # ------------------------------------------------------------------

    async def _load_evidence(
        self,
        ts_code: str,
        stock_name: str | None,
        window_start_utc,
        window_end_utc,
        cancel_event: asyncio.Event | None,
    ) -> tuple[list[EvidenceDocument], dict]:
        """加载并组织证据（落库取 id → 去重 → 公告优先截取 top-12 → 生成 quoteable_text）。

        返回 ``(evidence, coverage)``；``coverage`` 记录各来源成功/失败、获取/采用数与时间范围，
        并对 telegraph 记录按自然日条数分布（§8.1，暴露时间空洞）。
        """
        candidates: list[dict] = []
        coverage: dict = {
            "announcement": {"status": "fail", "fetched": 0, "adopted": 0, "earliest": None, "latest": None},
            "news": {"status": "fail", "fetched": 0, "adopted": 0, "earliest": None, "latest": None},
            "telegraph": {"status": "fail", "adopted": 0, "daily_counts": {}},
        }

        await self._maybe_cancel(cancel_event)

        # 1) 实时抓取公告 + 新闻并落库（取 id 由 get_market_news_documents 反查）
        fetched: dict = {"docs": [], "coverage": {}}
        try:
            fetched = await NewsFetcher.get_stock_news_documents(ts_code, window_days=_WINDOW_DAYS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_classified(logger, e, "external", "[NewsInsight] fetch documents failed (%s): %s", ts_code)
        fetched_docs = fetched.get("docs") or []
        fetched_coverage = fetched.get("coverage") or {}
        for src in ("announcement", "news"):
            if src in fetched_coverage:
                coverage[src]["status"] = fetched_coverage.get(src, "fail") or "fail"
            coverage[src]["fetched"] = sum(1 for d in fetched_docs if d.get("source_kind") == src)
        if fetched_docs:
            try:
                await self.dao.save_market_news_batch(fetched_docs)
            except Exception as e:
                log_classified(logger, e, "db", "[NewsInsight] save fetched docs failed (%s): %s", ts_code)

        await self._maybe_cancel(cancel_event)

        # 2) 落库后的公告/新闻（覆盖实时抓取与历史已入库行）
        df = await self.dao.get_market_news_documents(ts_code, window_start_utc, window_end_utc)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                candidates.append(
                    {
                        "id": r["id"],
                        "source_kind": r.get("source_kind"),
                        "source": r.get("source"),
                        "title": r.get("title"),
                        "content": r.get("content"),
                        "publish_time": r.get("publish_time"),
                        "url": r.get("url"),
                        "content_hash": r.get("content_hash"),
                        "sentiment": r.get("sentiment"),
                    }
                )

        await self._maybe_cancel(cancel_event)

        # 3) 首页快讯直接关联（telegraph；ts_code 恒 NULL，需文本匹配）
        try:
            df_t = await self.dao.get_telegraph_news_for_stocks(
                [ts_code],
                [stock_name] if stock_name else [],
                window_start_utc,
                window_end_utc,
            )
            coverage["telegraph"]["status"] = "ok"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_classified(logger, e, "db", "[NewsInsight] fetch telegraph candidates failed (%s): %s", ts_code)
            df_t = None
        if df_t is not None and not df_t.empty:
            candidates_pool = [{"ts_code": ts_code, "name": stock_name or ""}]
            for _, r in df_t.iterrows():
                text = f"{r.get('title') or ''} {r.get('content') or ''}"
                if ts_code in match_news_to_stock(text, candidates_pool):
                    candidates.append(
                        {
                            "id": r["id"],
                            "source_kind": r.get("source_kind") or "telegraph",
                            "source": r.get("source"),
                            "title": r.get("title"),
                            "content": r.get("content"),
                            "publish_time": r.get("publish_time"),
                            "url": r.get("url"),
                            "content_hash": r.get("content_hash"),
                            "sentiment": r.get("sentiment"),
                        }
                    )

        await self._maybe_cancel(cancel_event)

        # 4) 确定性去重（§8.4，仅同 source_kind 内）
        kept, _dropped = dedupe_documents(candidates)

        # 5) 公告优先 + 时间降序截取 top-12（§9.1）
        ordered = sorted(kept, key=lambda d: (0 if d.get("source_kind") == "announcement" else 1, _sort_pub(d)))
        top = ordered[:_EVIDENCE_LIMIT]

        evidence: list[EvidenceDocument] = [
            EvidenceDocument(
                news_id=d["id"],
                source_kind=d.get("source_kind"),
                source=d.get("source"),
                title=d.get("title"),
                content=d.get("content"),
                publish_time=d.get("publish_time"),
                url=d.get("url"),
                content_hash=d.get("content_hash"),
                sentiment=d.get("sentiment"),
                quoteable_text=neutralize_external_text(
                    (d.get("title") or "") + "\n" + (d.get("content") or ""),
                    max_len=_QUOTEABLE_MAX_LEN,
                ),
            )
            for d in top
        ]

        # 6) coverage 采用数 / 时间范围 / telegraph 按日分布
        for src, key in (("announcement", "announcement"), ("news", "news"), ("telegraph", "telegraph")):
            src_docs = [d for d in top if (d.get("source_kind") or "telegraph") == src]
            coverage[key]["adopted"] = len(src_docs)
            _fill_time_range(coverage[key], src_docs)
        daily: dict[str, int] = {}
        for d in candidates:
            if (d.get("source_kind") or "telegraph") != "telegraph":
                continue
            if d.get("publish_time"):
                cst = from_utc_to_cst(d["publish_time"])
                day = cst.strftime("%Y-%m-%d") if cst else ""
                if day:
                    daily[day] = daily.get(day, 0) + 1
        coverage["telegraph"]["daily_counts"] = dict(sorted(daily.items()))

        return evidence, coverage

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    async def _persist(
        self,
        ts_code: str,
        input_hash: str,
        window_start_utc,
        window_end_utc,
        result: NewsInsightResult,
    ) -> None:
        """按 §7.2/§11 持久化：仅成功态作可缓存快照；``failed`` 仅在无既有成功快照时落库。"""
        brief = {
            "ts_code": ts_code,
            "input_hash": input_hash,
            "window_start": window_start_utc,
            "window_end": window_end_utc,
            "analysis_status": result.analysis_status,
            "risk_level": result.risk_level,
            "confidence": result.confidence,
            "summary": result.summary,
            "events": result.events,
            "evidence_news_ids": result.evidence_news_ids,
            "coverage": result.coverage,
            "model_id": result.model_id,
            "analysis_profile": result.analysis_profile,
            "prompt_version": result.prompt_version,
        }
        if result.analysis_status in SUCCESS_STATUS:
            await self.dao.save_news_risk_brief(brief)
            return
        if result.analysis_status == "failed":
            existing = await self.dao.get_news_risk_brief(ts_code, input_hash)
            if existing is None or existing.get("analysis_status") not in SUCCESS_STATUS:
                await self.dao.save_news_risk_brief(brief)
            else:
                logger.info("[NewsInsight] failed analysis not persisted; keep existing success snapshot (%s)", ts_code)
            return
        # evidence_only / no_evidence：确定性/降级结果，不作为可缓存快照持久化

    async def load_evidence_preview(
        self,
        ts_code: str,
        stock_name: str | None = None,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[list[EvidenceDocument], dict]:
        """仅加载并整理证据（UI 打开详情的 preview 阶段，不触发 AI，§12 第 2 步）。

        ``select_stock`` 阶段由 VM 调用以展示材料与覆盖状态；返回 ``(evidence, coverage)``，
        与 ``analyze`` 内部证据加载共用同一实现（``_window`` + ``_load_evidence``），
        避免 UI 侧复制证据整理逻辑。
        """
        if not ts_code:
            raise ValueError("ts_code is required")
        window_start_utc, window_end_utc, _s, _e = self._window()
        return await self._load_evidence(ts_code, stock_name, window_start_utc, window_end_utc, cancel_event)

    def analysis_window_label(self) -> str:
        """分析窗口的可展示标签（CST 自然日）``YYYY-MM-DD ~ YYYY-MM-DD``。"""
        _start_utc, _end_utc, start_cst, end_cst = self._window()
        return f"{start_cst.isoformat()} ~ {end_cst.isoformat()}"

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    @log_async_operation(operation_name="news_insight_analyze", threshold_ms=PerfThreshold.AI_INFERENCE)
    async def analyze(
        self,
        ts_code: str,
        stock_name: str | None = None,
        *,
        regenerate: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> NewsInsightOutcome:
        """对 tscode 执行新闻风险解读（缓存命中 / 子集复用 / 降级 / AI 编排 / 快照持久化）。"""
        if not ts_code:
            raise ValueError("ts_code is required")

        window_start_utc, window_end_utc, start_cst, end_cst = self._window()
        await self._maybe_cancel(cancel_event)

        evidence, coverage = await self._load_evidence(
            ts_code, stock_name, window_start_utc, window_end_utc, cancel_event
        )
        await self._maybe_cancel(cancel_event)

        content_hashes = [ev.content_hash or "" for ev in evidence]
        analysis_profile = self._analysis_profile()
        input_hash = self._build_input_hash(ts_code, start_cst, end_cst, content_hashes, analysis_profile)

        # 无有效证据 → no_evidence（§10.3），不持久化、不调用 AI
        if not evidence:
            return NewsInsightOutcome(
                result=NewsInsightResult(
                    analysis_status="no_evidence",
                    risk_level=None,
                    confidence=None,
                    summary=None,
                    events=[],
                    evidence_news_ids=[],
                    coverage=coverage,
                    model_id=None,
                    analysis_profile=analysis_profile,
                    prompt_version=NEWS_RISK_PROMPT_VERSION,
                ),
                reused=False,
            )

        # 缓存命中（§11）：相同输入命中成功快照直接返回
        if not regenerate:
            hit = await self.dao.get_news_risk_brief(ts_code, input_hash)
            if hit is not None and hit.get("analysis_status") in SUCCESS_STATUS:
                return NewsInsightOutcome(
                    result=self._row_to_result(hit, coverage),
                    reused=True,
                    reuse_type="cache_hit",
                )

        # 子集例外（§11）：本次来源失败导致证据集为既有成功快照真子集时，复用更完整快照
        if not regenerate:
            latest = await self.dao.get_latest_success_brief(ts_code, window_start_utc, window_end_utc)
            if latest is not None and latest.get("evidence_news_ids"):
                curr_ids = {ev.news_id for ev in evidence}
                snap_ids = set(latest.get("evidence_news_ids") or [])
                if curr_ids < snap_ids:
                    return NewsInsightOutcome(
                        result=self._row_to_result(latest, coverage),
                        reused=True,
                        reuse_type="subset",
                    )

        await self._maybe_cancel(cancel_event)

        request = NewsInsightRequest(
            ts_code=ts_code,
            stock_name=stock_name or "",
            window_start=window_start_utc,
            window_end=window_end_utc,
            evidence=evidence,
            coverage=coverage,
            analysis_profile=analysis_profile,
        )
        result = await self.ai.analyze_news_risk(request)  # CancelledError 由 _chat_completion 传播（R2）

        await self._persist(ts_code, input_hash, window_start_utc, window_end_utc, result)
        return NewsInsightOutcome(result=result, reused=False)


def _sort_pub(d: dict) -> datetime.datetime:
    """按发布时间排序的键；无发布时间的文档排最后。"""
    pub = d.get("publish_time")
    if pub is None:
        return datetime.datetime.max
    return pub


def _fill_time_range(src_item: dict, src_docs: list[dict]) -> None:
    """填充 coverage 该来源的 earliest / latest（取采用文档的发布区间）。"""
    times = [d["publish_time"] for d in src_docs if d.get("publish_time")]
    if times:
        src_item["earliest"] = min(times).isoformat()
        src_item["latest"] = max(times).isoformat()
