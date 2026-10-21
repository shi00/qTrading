"""services.ai_service.news_risk 单元测试（新闻风险解读第一期 Phase C/R19）。

覆盖纯函数（normalize_quote_text / validate_events / aggregate_risk_level）、
prompt 构造（_build_system_prompt / _build_user_prompt）、_cloud_model_id，
以及核心 analyze_news_risk 的 local/cloud/降级/校验失败分支。

isolate：将 service 替身的 ``_chat_completion`` mock 为 AsyncMock（经组合根
self._service 访问）；monkeypatch ``services.ai_service.ConfigHandler`` 控云端配置。
"""

from __future__ import annotations

import asyncio
import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.errors import AIPolicyNotAcknowledgedError
from core.i18n import Message
from core.model_management_types import LegacyDispatchBlockedError
from services.ai_service import AIService
from services.ai_service.news_risk import (
    RiskEvent,
    RiskQuote,
    NewsRiskAnalyzer,
    aggregate_risk_level,
    normalize_quote_text,
    validate_events,
)
from services.news_insight_models import EvidenceDocument, NewsInsightRequest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _legacy_admission_allowed(monkeypatch):
    """旧链测试前提显式化（F05 fail-closed）：分派/准入读取必须成功且为 allowed
    （pending 未开启迁移）才走旧链——读取异常现在阻断旧链，不再 fail-open。"""
    from services.ai_service import scenario_dispatch

    config_service = MagicMock()
    config_service.get_snapshot = AsyncMock(
        return_value=MagicMock(migration_state="pending", migration_transition=None, scenario_routes=())
    )
    monkeypatch.setattr(scenario_dispatch, "_new_config_service", lambda: config_service)


def _evidence(news_id=1, text="公司发布公告称经营正常") -> EvidenceDocument:
    return EvidenceDocument(news_id=news_id, quoteable_text=text, source_kind="announcement")


def _request(evidence=None) -> NewsInsightRequest:
    return NewsInsightRequest(
        ts_code="000001.SZ",
        stock_name="平安银行",
        window_start=datetime.datetime(2026, 8, 17, 16, 0, 0),
        window_end=datetime.datetime(2026, 9, 16, 15, 59, 59),
        evidence=evidence or [_evidence()],
        coverage={"announcement": {"status": "ok"}},
        analysis_profile="cloud:deepseek/chat",
    )


def _event(quote: str = "公告", news_id: int = 1) -> RiskEvent:
    return RiskEvent(
        event_type="litigation",
        severity="high",
        company_role="direct",
        event_status="ongoing",
        impact_horizon="short_term",
        fact="被诉讼",
        impact_reasoning="影响声誉",
        uncertainty="",
        confidence=85,
        evidence_quotes=[RiskQuote(news_id=news_id, quote=quote)],
    )


def _valid_raw():
    return {
        "summary": "风险提示",
        "events": [
            {
                "event_type": "litigation",
                "severity": "high",
                "company_role": "direct",
                "event_status": "ongoing",
                "impact_horizon": "short_term",
                "fact": "被诉讼",
                "impact_reasoning": "影响声誉",
                "uncertainty": "",
                "confidence": 85,
                "evidence_quotes": [{"news_id": 1, "quote": "公告"}],
            }
        ],
    }


def _analyzer(svc=None):
    if svc is None:
        svc = _service_stub()
    return NewsRiskAnalyzer(svc)


def _service_stub():
    svc = AIService.__new__(AIService)  # 绕过 __init__，仅需 _chat_completion
    svc._chat_completion = AsyncMock(return_value=_valid_raw())
    return svc


# --- normalize_quote_text ---


class TestNormalizeQuoteText:
    def test_none_and_empty(self):
        assert normalize_quote_text(None) == ""
        assert normalize_quote_text("") == ""

    def test_fullwidth_to_halfwidth(self):
        assert normalize_quote_text("ＦＤＡ　ＬＬＣ") == "FDA LLC"

    def test_collapse_whitespace(self):
        assert normalize_quote_text("  风险   提示  ") == "风险 提示"

    def test_preserves_ellipsis(self):
        """对抗性检视 Minor（省略号）：归一化保留省略号标记，不得先删后匹配。

        NFKC 会把全角省略号 ``…`` 归一化为半角 ``...``，标记本身仍保留（未剔除）。
        """
        assert normalize_quote_text("风险…提示...") == "风险...提示..."

    def test_strips_edges(self):
        assert normalize_quote_text("  正文  ") == "正文"


# --- validate_events ---


class TestValidateEvents:
    def test_valid_quote_substring(self):
        ev = _event(quote="公告")
        valid, dropped = validate_events([ev], [_evidence(news_id=1, text="公司发布公告称经营正常")])
        assert len(valid) == 1
        assert valid[0]["event_type"] == "litigation"
        assert valid[0]["severity"] == "high"
        assert valid[0]["evidence_quotes"][0]["news_id"] == 1
        assert dropped == []

    def test_quote_cannot_span_ellipsis_gap(self):
        """对抗性检视 Minor（省略号）：quote 不得跨越原文省略号间隙仍判为连续子串。

        原文含省略号（``公告…称``），quote ``公告称`` 无省略号标记——即使剔除省略号后
        是子串，也应判定为跨间隙引用而被剔除（§9.4 连续子串约束收紧）。
        """
        ev = _event(quote="公告称")
        valid, dropped = validate_events([ev], [_evidence(news_id=1, text="公司发布公告…称经营正常")])
        assert valid == []
        assert dropped[0]["drop_reason"] == "no_valid_evidence_quote"

    def test_quote_with_matching_ellipsis_valid(self):
        """quote 显式保留与原文一致的省略号标记 → 逐字对应，仍为合法引用。"""
        ev = _event(quote="公告…称")
        valid, dropped = validate_events([ev], [_evidence(news_id=1, text="公司发布公告…称经营正常")])
        assert len(valid) == 1
        assert valid[0]["evidence_quotes"][0]["quote"] == "公告…称"
        assert dropped == []

    def test_news_id_not_in_evidence_dropped(self):
        ev = _event(news_id=99, quote="公告")
        valid, dropped = validate_events([ev], [_evidence()])
        assert valid == []
        assert dropped[0]["drop_reason"] == "no_valid_evidence_quote"

    def test_empty_quote_dropped(self):
        ev = _event(quote="   ")
        valid, dropped = validate_events([ev], [_evidence()])
        assert dropped[0]["drop_reason"] == "no_valid_evidence_quote"

    def test_non_substring_dropped(self):
        ev = _event(quote="完全不存在的摘录")
        valid, dropped = validate_events([ev], [_evidence()])
        assert dropped[0]["drop_reason"] == "no_valid_evidence_quote"

    def test_mixed_keeps_only_valid(self):
        good = _event(quote="公告")
        bad = RiskEvent(
            event_type="other",
            severity="low",
            company_role="indirect",
            event_status="proposed",
            impact_horizon="long_term",
            fact="x",
            impact_reasoning="y",
            confidence=50,
            evidence_quotes=[RiskQuote(news_id=50, quote="不在")],
        )
        valid, dropped = validate_events([good, bad], [_evidence()])
        assert len(valid) == 1
        assert valid[0]["event_type"] == "litigation"
        assert len(dropped) == 1


# --- aggregate_risk_level ---


class TestAggregateRiskLevel:
    def test_empty_none(self):
        assert aggregate_risk_level([]) is None

    def test_highest_wins(self):
        assert aggregate_risk_level([{"severity": "low"}, {"severity": "high"}]) == "high"
        assert aggregate_risk_level([{"severity": "critical"}]) == "critical"
        assert aggregate_risk_level([{"severity": "medium"}]) == "medium"

    def test_severity_order_reversal(self):
        # SEVERITY_ORDER = [low, medium, high, critical]；返回最高
        assert aggregate_risk_level([{"severity": "critical"}, {"severity": "medium"}]) == "critical"
        assert aggregate_risk_level([{"severity": "low"}, {"severity": "critical"}, {"severity": "high"}]) == "critical"


# --- prompt 构造 ---


class TestBuildSystemPrompt:
    def test_contains_key_phrases(self):
        p = NewsRiskAnalyzer._build_system_prompt()
        assert "不可信文本" in p
        assert "证据" in p
        assert "连续子串" in p
        assert "JSON" in p


class TestBuildUserPrompt:
    def test_contains_stock_and_evidence_blocks(self):
        req = _request(evidence=[_evidence(news_id=1, text="正文")])
        p = NewsRiskAnalyzer._build_user_prompt(req)
        assert "股票：平安银行（000001.SZ）" in p
        assert "id=1" in p
        assert "quoteable_text: 正文" in p
        assert "股票：" in p

    def test_body_cap_truncates(self):
        big = "x" * 7000  # 单条超 body 上限：累计 >= 6000 后 break，第二条不进 prompt
        req = _request(evidence=[_evidence(news_id=1, text=big), _evidence(news_id=2, text="第二")])
        p = NewsRiskAnalyzer._build_user_prompt(req)
        assert "id=2" not in p
        assert "id=1" in p

    def test_no_publish_time_branch(self):
        ev = EvidenceDocument(news_id=1, quoteable_text="正文", source_kind="news", publish_time=None)
        req = _request(evidence=[ev])
        p = NewsRiskAnalyzer._build_user_prompt(req)
        assert "time=" in p


# --- _cloud_model_id ---


class TestCloudModelId:
    def test_normal(self, monkeypatch):
        monkeypatch.setattr(
            "services.ai_service.ConfigHandler.get_llm_config", lambda: {"provider": "deepseek", "model": "chat"}
        )
        assert _analyzer()._cloud_model_id() == "deepseek/chat"

    def test_incomplete_returns_none(self, monkeypatch):
        monkeypatch.setattr("services.ai_service.ConfigHandler.get_llm_config", lambda: {"provider": "deepseek"})
        assert _analyzer()._cloud_model_id() is None

    def test_error_returns_none(self, monkeypatch):
        def boom():
            raise RuntimeError("cfg")

        monkeypatch.setattr("services.ai_service.ConfigHandler.get_llm_config", boom)
        assert _analyzer()._cloud_model_id() is None


# --- analyze_news_risk ---


class TestAnalyzeNewsRisk:
    async def test_local_success(self):
        svc = _service_stub()
        analyzer = _analyzer(svc)
        result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "analyzed_with_events"
        assert result.risk_level == "high"
        assert result.model_id == "local"
        assert result.risk_level == "high"
        kwargs = svc._chat_completion.await_args.kwargs
        assert kwargs["provider"] == "local"
        assert kwargs["purpose"] == "news"
        assert kwargs["json_mode"] is True
        assert kwargs["local_max_tokens"] == 2048

    async def test_local_fails_cloud_with_ack(self, monkeypatch):
        monkeypatch.setattr(
            "services.ai_service.ConfigHandler.get_llm_config", lambda: {"provider": "pw", "model": "md"}
        )
        svc = AIService.__new__(AIService)

        async def chat(messages, **kwargs):
            if kwargs.get("provider") == "local":
                raise RuntimeError("local down")
            return _valid_raw()

        svc._chat_completion = AsyncMock(side_effect=chat)
        analyzer = _analyzer(svc)
        result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "analyzed_with_events"
        assert result.model_id == "pw/md"
        # 云端 provider 调用发生
        providers = [c.kwargs.get("provider") for c in svc._chat_completion.await_args_list]
        assert providers == ["local", "cloud"]

    async def test_both_providers_fail_evidence_only(self):
        svc = AIService.__new__(AIService)

        async def chat(messages, **kwargs):
            raise RuntimeError("all down")

        svc._chat_completion = AsyncMock(side_effect=chat)
        analyzer = _analyzer(svc)
        result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "evidence_only"
        assert result.risk_level is None
        assert result.evidence_news_ids == [1]

    async def test_cloud_policy_not_acknowledged(self):
        svc = AIService.__new__(AIService)

        async def chat(messages, **kwargs):
            raise AIPolicyNotAcknowledgedError(Message("ai_external_acknowledgment_prompt"))

        svc._chat_completion = AsyncMock(side_effect=chat)
        analyzer = _analyzer(svc)
        result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "evidence_only"

    async def test_local_fails_cloud_policy_not_acknowledged(self):
        svc = AIService.__new__(AIService)

        async def chat(messages, **kwargs):
            if kwargs.get("provider") == "local":
                raise RuntimeError("local down")
            raise AIPolicyNotAcknowledgedError(Message("ai_external_acknowledgment_prompt"))

        svc._chat_completion = AsyncMock(side_effect=chat)
        analyzer = _analyzer(svc)
        result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "evidence_only"


# --- _process_raw ---


class TestProcessRaw:
    def test_valid_with_events(self):
        req = _request(evidence=[_evidence(news_id=1, text="公告")])
        result = _analyzer()._process_raw(_valid_raw(), req, "local")
        assert result.analysis_status == "analyzed_with_events"
        assert result.risk_level == "high"
        assert result.confidence == 85
        assert len(result.events) == 1
        assert result.model_id == "local"
        assert result.evidence_news_ids == [1]

    def test_summary_only_no_event(self):
        req = _request(evidence=[_evidence()])
        raw = {"summary": "无重大风险", "events": []}
        result = _analyzer()._process_raw(raw, req, "local")
        assert result.analysis_status == "analyzed_no_event"
        assert result.summary == "无重大风险"
        assert result.risk_level is None

    def test_no_summary_fallback(self):
        req = _request(evidence=[_evidence()])
        raw = {"summary": None, "events": []}
        result = _analyzer()._process_raw(raw, req, "local")
        assert result.analysis_status == "analyzed_no_event"
        assert result.summary == "在本次实际覆盖范围内未识别到重大风险"

    def test_invalid_enum_raises_validation_error(self):
        """_process_raw 本身不吞校验错误：RiskOutput.model_validate 抛出 ValidationError，
        由 analyze_news_risk 外层 try 捕获 → failed（见 test_direct_invalid_enum）。"""
        req = _request(evidence=[_evidence()])
        raw = {
            "summary": "x",
            "events": [_valid_raw()["events"][0] | {"severity": "INVALID_SEVERITY"}],
        }
        from pydantic import ValidationError

        with pytest.raises(ValidationError):  # noqa: weak-assertion 事件 severity 枚举校验守卫，异常类型即测试目标
            _analyzer()._process_raw(raw, req, "local")

    @pytest.mark.asyncio
    async def test_direct_invalid_enum(self):
        """end-to-end：_chat_completion 返回非法枚举 → analyze_news_risk 判 failed。"""
        svc = AIService.__new__(AIService)

        async def chat(messages, **kwargs):
            return {"summary": "x", "events": [_valid_raw()["events"][0] | {"severity": "INVALID"}]}

        svc._chat_completion = AsyncMock(side_effect=chat)
        analyzer = _analyzer(svc)
        result = await analyzer.analyze_news_risk(_request(evidence=[_evidence(news_id=1, text="公告")]))
        assert result.analysis_status == "failed"
        assert result.evidence_news_ids == [1]


class TestAnalyzeNewsRiskScenarioChain:
    """S4 §8.3 news_risk 新链接线（s4_plan §4.1）。

    订阅判定：patch news_risk 模块级 ``_resolve_news_risk_dispatch`` 注入
    ScenarioDispatch（mock executor），不真走 Executor——分派/计量由
    scenario_dispatch / executor 层测试承载。
    """

    @staticmethod
    def _make_dispatch(exec_result):
        from services.ai_service.scenario_dispatch import ScenarioDispatch

        executor = MagicMock()
        executor.execute = AsyncMock(return_value=exec_result)
        snapshot = MagicMock()
        snapshot.config_revision = 7
        return ScenarioDispatch(
            executor=executor,
            snapshot=snapshot,
            route_revision=3,
            total_timeout_seconds=60.0,
            task_routes={},
        )

    @staticmethod
    def _exec_result(status, output=None, decisions=None, actual_usage=()):
        from core.model_management_types import CandidateDecision, ScenarioExecutionResult
        from services.ai_service.scenario_executor import ScenarioOutput

        outputs = ()
        if output is not None:
            outputs = (
                # F03：预制形态与 Executor 真实成功出口一致（ScenarioOutput，非裸对象）
                ScenarioOutput(
                    output=output,
                    metadata={"actual_model": "m-1", "connection_revision": 5},
                ),
            )
        if decisions is None:
            decisions = (
                CandidateDecision(
                    task_id="risk",
                    candidate_index=0,
                    connection_id="conn-1",
                    decision="succeeded" if status == "succeeded" else "failed",
                    reason="ok" if status == "succeeded" else "deadline exhausted",
                ),
            )
        return ScenarioExecutionResult(
            scenario_id="news_risk",
            invocation_id="inv-r",
            status=status,
            outputs=outputs,
            candidate_decisions=decisions,
            actual_usage=actual_usage,
        )

    @staticmethod
    def _validated_output():
        """Executor validate_output 产出形态：_parse_and_validate 之后的中间结构。"""
        return {
            "summary": "风险提示",
            "events": [
                {
                    "event_type": "litigation",
                    "severity": "high",
                    "company_role": "direct",
                    "event_status": "ongoing",
                    "impact_horizon": "short_term",
                    "fact": "被诉讼",
                    "impact_reasoning": "影响声誉",
                    "uncertainty": "",
                    "confidence": 85,
                    "evidence_quotes": [{"news_id": 1, "quote": "公告"}],
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_dispatch_precedes_legacy_chain(self):
        """新链命中 → executor.execute 被调用、旧链 _chat_completion 不被调用。"""
        dispatch = self._make_dispatch(self._exec_result("succeeded", output=self._validated_output()))
        svc = _service_stub()
        analyzer = _analyzer(svc)
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        svc._chat_completion.assert_not_awaited()
        dispatch.executor.execute.assert_awaited_once()
        assert result.analysis_status == "analyzed_with_events"
        assert result.risk_level == "high"
        assert result.execution_metadata["ai_status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_scenario_payload_shape(self):
        """payload 契约：单 system_prompt + 单 prompt + json_mode + max_tokens；task_id=risk。"""
        dispatch = self._make_dispatch(self._exec_result("succeeded", output=self._validated_output()))
        analyzer = _analyzer()
        req = _request()
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            await analyzer.analyze_news_risk(req)
        request = dispatch.executor.execute.await_args.args[0]
        assert request.scenario_id == "news_risk"
        assert request.task_id == "risk"
        assert request.payload["json_mode"] is True
        assert "只能引用你收到的证据" in request.payload["system_prompt"]
        assert "000001.SZ" in request.payload["prompt"]
        assert isinstance(request.payload["max_tokens"], int)

    @pytest.mark.asyncio
    async def test_scenario_success_metadata(self):
        """成功 → execution_metadata 8 键（程序构造，config_revision 来自 snapshot）。"""
        dispatch = self._make_dispatch(self._exec_result("succeeded", output=self._validated_output()))
        analyzer = _analyzer()
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        em = result.execution_metadata
        assert em["ai_status"] == "succeeded"
        assert em["route_revision"] == 3
        assert em["config_revision"] == 7
        assert em["connection_id"] == "conn-1"
        assert em["connection_revision"] == 5
        assert em["actual_model"] == "m-1"
        assert em["usage_status"] == "unavailable"
        assert em["invocation_id"]
        # model_id 兼容显示：actual_model 优先
        assert result.model_id == "m-1"

    @pytest.mark.asyncio
    async def test_scenario_success_no_event(self):
        """无有效事件 → analyzed_no_event（合法成功，risk_level None，R21）。"""
        dispatch = self._make_dispatch(self._exec_result("succeeded", output={"summary": "无重大风险", "events": []}))
        analyzer = _analyzer()
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "analyzed_no_event"
        assert result.risk_level is None
        assert result.confidence is None
        assert result.execution_metadata["ai_status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_scenario_all_unavailable_maps_evidence_only(self):
        """全候选不可用（deadline/cooldown 等）→ evidence_only，不伪装无风险（R21）。"""
        dispatch = self._make_dispatch(self._exec_result("failed"))
        analyzer = _analyzer()
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "evidence_only"
        assert result.risk_level is None
        assert result.execution_metadata["ai_status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_scenario_validation_failure_maps_failed(self):
        """输出全校验失败（reason 含 output validation failed）→ failed（§10.3 不保存结论）。"""
        dispatch = self._make_dispatch(
            self._exec_result(
                "failed",
                decisions=(
                    MagicMock(
                        task_id="risk",
                        candidate_index=0,
                        connection_id="c1",
                        decision="failed",
                        reason="output validation failed: ModelOutputValidationError",
                    ),
                ),
            )
        )
        analyzer = _analyzer()
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "failed"
        assert result.execution_metadata["ai_status"] == "failed"

    @pytest.mark.asyncio
    async def test_scenario_executor_exception_maps_evidence_only(self):
        """executor 抛异常（非取消）→ evidence_only + 明确 ai_status，不伪装成功（R21）。"""
        dispatch = self._make_dispatch(None)
        dispatch.executor.execute = AsyncMock(side_effect=RuntimeError("boom-secret"))
        analyzer = _analyzer()
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        assert result.analysis_status == "evidence_only"
        assert result.events == []
        assert result.execution_metadata["ai_status"] == "failed"

    @pytest.mark.asyncio
    async def test_dispatch_error_blocks_legacy_fail_closed(self):
        """F05 fail-closed：分派过程异常（如配置损坏）→ read_failed 阻断传出，
        零旧链传输（_chat_completion 不被调），不再降级旧链（R21 不伪装成功）。"""
        svc = _service_stub()
        analyzer = _analyzer(svc)
        with (
            patch(
                "services.ai_service.scenario_dispatch.resolve_executor",
                AsyncMock(side_effect=RuntimeError("config corrupt")),
            ),
            pytest.raises(LegacyDispatchBlockedError) as excinfo,
        ):
            await analyzer.analyze_news_risk(_request())
        assert excinfo.value.admission == "read_failed"
        svc._chat_completion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_none_falls_back_to_legacy(self):
        """分派返回 None（pending/未注册）→ 旧链行为逐字节保留。"""
        svc = _service_stub()
        analyzer = _analyzer(svc)
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=None),
        ):
            result = await analyzer.analyze_news_risk(_request())
        svc._chat_completion.assert_awaited_once()
        assert result.analysis_status == "analyzed_with_events"
        assert result.execution_metadata is None

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """executor 抛 CancelledError → 裸传播（R2，不包装成 evidence_only）。"""
        dispatch = self._make_dispatch(None)
        dispatch.executor.execute = AsyncMock(side_effect=asyncio.CancelledError())
        analyzer = _analyzer()
        with (
            patch(
                "services.ai_service.news_risk._resolve_news_risk_dispatch",
                AsyncMock(return_value=dispatch),
            ),
            pytest.raises(asyncio.CancelledError) as excinfo,
        ):
            await analyzer.analyze_news_risk(_request())
        # R2 强断言：CancelledError 类型原样传播，未被包装/替换（excinfo.type 身份校验）。
        assert excinfo.type is asyncio.CancelledError


class TestAnalyzeNewsRiskFrozenDispatch:
    """F39：冻结分派载荷消费（编排层单点冻结随 request 携带，执行侧不再自行分派读盘）。"""

    @pytest.mark.asyncio
    async def test_frozen_dispatch_executes_scenario_directly(self):
        """冻结新链 dispatch → 直接走 Executor，不再调用 _resolve_news_risk_dispatch。"""
        dispatch = TestAnalyzeNewsRiskScenarioChain._make_dispatch(
            TestAnalyzeNewsRiskScenarioChain._exec_result(
                "succeeded", output=TestAnalyzeNewsRiskScenarioChain._validated_output()
            )
        )
        svc = _service_stub()
        analyzer = _analyzer(svc)
        request = _request()
        request.dispatch_frozen = True
        request.frozen_dispatch = dispatch
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(side_effect=AssertionError("frozen path must not self-resolve dispatch")),
        ) as resolve_mock:
            result = await analyzer.analyze_news_risk(request)
        resolve_mock.assert_not_awaited()
        dispatch.executor.execute.assert_awaited_once()
        svc._chat_completion.assert_not_awaited()
        assert result.analysis_status == "analyzed_with_events"

    @pytest.mark.asyncio
    async def test_frozen_legacy_dispatch_runs_fresh_admission(self):
        """冻结旧链域（frozen_dispatch=None）→ 仅准入新鲜判定，不再调 resolve_executor。"""
        svc = _service_stub()
        analyzer = _analyzer(svc)
        request = _request()
        request.dispatch_frozen = True
        request.frozen_dispatch = None
        with (
            patch(
                "services.ai_service.scenario_dispatch.evaluate_legacy_admission",
                AsyncMock(return_value="allowed"),
            ),
            patch(
                "services.ai_service.scenario_dispatch.resolve_executor",
                AsyncMock(side_effect=AssertionError("frozen legacy must not re-resolve dispatch")),
            ),
        ):
            result = await analyzer.analyze_news_risk(request)
        svc._chat_completion.assert_awaited_once()
        assert result.analysis_status == "analyzed_with_events"
        assert result.execution_metadata is None

    @pytest.mark.asyncio
    async def test_frozen_legacy_admission_blocked_zero_egress(self):
        """冻结旧链 + 准入 draining_stop → LegacyDispatchBlockedError，零旧链传输（R21）。"""
        svc = _service_stub()
        analyzer = _analyzer(svc)
        request = _request()
        request.dispatch_frozen = True
        request.frozen_dispatch = None
        with (
            patch(
                "services.ai_service.scenario_dispatch.evaluate_legacy_admission",
                AsyncMock(return_value="draining_stop"),
            ),
            pytest.raises(LegacyDispatchBlockedError) as excinfo,
        ):
            await analyzer.analyze_news_risk(request)
        assert excinfo.value.admission == "draining_stop"
        svc._chat_completion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_frozen_legacy_admission_read_failed_fail_closed(self):
        """F05 fail-closed 保留：冻结旧链准入读取异常 → read_failed 阻断传出。"""
        svc = _service_stub()
        analyzer = _analyzer(svc)
        request = _request()
        request.dispatch_frozen = True
        request.frozen_dispatch = None
        with (
            patch(
                "services.ai_service.scenario_dispatch.evaluate_legacy_admission",
                AsyncMock(side_effect=RuntimeError("io")),
            ),
            pytest.raises(LegacyDispatchBlockedError) as excinfo,
        ):
            await analyzer.analyze_news_risk(request)
        assert excinfo.value.admission == "read_failed"
        svc._chat_completion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unfrozen_request_keeps_self_dispatch(self):
        """dispatch_frozen=False（E2E fake / 旧调用默认）→ 既有自行分派路径不变。"""
        dispatch = TestAnalyzeNewsRiskScenarioChain._make_dispatch(
            TestAnalyzeNewsRiskScenarioChain._exec_result(
                "succeeded", output=TestAnalyzeNewsRiskScenarioChain._validated_output()
            )
        )
        svc = _service_stub()
        analyzer = _analyzer(svc)
        with patch(
            "services.ai_service.news_risk._resolve_news_risk_dispatch",
            AsyncMock(return_value=dispatch),
        ):
            result = await analyzer.analyze_news_risk(_request())
        dispatch.executor.execute.assert_awaited_once()
        assert result.analysis_status == "analyzed_with_events"
