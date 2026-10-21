"""S4 news_classifier 新链分支单测（模型统一.md §8.1，s4_plan.md §2.1/§6.2）。

覆盖（bundled / dedicated / 旧链回退 / 失败语义）：
- 分派不可用 → 旧链（local/cloud failover 逐字节保留）；
- bundled 一次联合请求：主题+情绪均有效 / 主题有效情绪缺失（unavailable）/
  非法情绪值不转换（R21）/ 全候选失败（category=unknown 兼容字段）；
- validate_output 分别校验：主题无效抛 ModelOutputValidationError（Executor 切换）；
- dedicated：主题与情绪独立调用、主题失败不丢已成功情绪、情绪失败不重跑主题；
- 计量一次（bundled 单次 execute）；
- deadline = loop.time() + total_timeout_seconds。
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import (
    AdapterResponse,
    CandidateDecision,
    ModelOutputValidationError,
    NormalizedLLMUsage,
    ScenarioExecutionResult,
)
from services.ai_service import scenario_dispatch
from services.ai_service.news_classifier import (
    NEWS_TEXT_MAX_LEN,
    NewsClassifier,
    _validate_bundled_output,
)
from services.ai_service.scenario_executor import ScenarioOutput

pytestmark = pytest.mark.unit

_SCENARIO_ID = "news_annotation"


@pytest.fixture(autouse=True)
def _reset_scheduling_provider():
    """测试隔离（R7 精神）：清理模块级调度服务 provider，避免跨文件状态泄漏。"""
    yield
    scenario_dispatch.set_scheduling_services_provider(None)


def _adapter_response(content: str | None) -> AdapterResponse:
    return AdapterResponse(
        content=content,
        usage=NormalizedLLMUsage(
            input_total_tokens=10,
            uncached_input_tokens=10,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=5,
        ),
        actual_model="glm-4-air",
        metadata={"connection_id": "conn-1", "connection_revision": 2},
    )


def _bundled_json(l1: str = "finance", l2: str = "precious_metals", sentiment: str | None = "Positive") -> str:
    obj: dict = {"category_L1": l1, "category_L2": l2}
    if sentiment is not None:
        obj["sentiment"] = sentiment
    return json.dumps(obj)


def _succeeded_result(content: str) -> ScenarioExecutionResult:
    return ScenarioExecutionResult(
        scenario_id=_SCENARIO_ID,
        invocation_id="inv-1",
        status="succeeded",
        outputs=(
            # F03：预制形态与 Executor 真实成功出口一致（ScenarioOutput，非裸对象）
            ScenarioOutput(
                output=json.loads(content),
                metadata={"actual_model": "glm-4-air", "connection_id": "conn-1", "connection_revision": 2},
            ),
        ),
        candidate_decisions=(
            CandidateDecision(
                task_id="topic",
                candidate_index=0,
                connection_id="conn-1",
                decision="succeeded",
                reason="ok",
            ),
        ),
        actual_usage=(
            NormalizedLLMUsage(
                input_total_tokens=10,
                uncached_input_tokens=10,
                cache_read_tokens=None,
                cache_write_tokens=None,
                output_tokens=5,
            ),
        ),
    )


def _failed_result() -> ScenarioExecutionResult:
    return ScenarioExecutionResult(
        scenario_id=_SCENARIO_ID,
        invocation_id="inv-1",
        status="failed",
        outputs=(),
        candidate_decisions=(
            CandidateDecision(
                task_id="topic",
                candidate_index=0,
                connection_id="conn-1",
                decision="failed",
                reason="upstream 5xx",
            ),
        ),
        actual_usage=(),
    )


def _dispatch(mode: str = "bundled", sentiment_candidates: list[str] | None = None) -> MagicMock:
    dispatch = MagicMock()
    dispatch.executor = MagicMock()
    dispatch.route_revision = 3
    dispatch.total_timeout_seconds = 45.0
    dispatch.task_routes = {
        "sentiment": {
            "mode": mode,
            "candidates": sentiment_candidates or ([] if mode == "bundled" else ["conn-9"]),
            "reuse_bundled_sentiment": True,
        }
    }
    return dispatch


def _patch_env(monkeypatch, dispatch: MagicMock | None):
    """patch 分派环境：provider + config service。"""

    svc = {"executor": dispatch.executor} if dispatch is not None else None
    scenario_dispatch.set_scheduling_services_provider(lambda: svc)
    config_service = MagicMock()
    # S6 D6：dispatch=None 分支会经 evaluate_legacy_admission 读同一快照，
    # 该分支语义是「调度服务未组装 + 迁移未开启 → 降级旧链」，故 pending；
    # dispatch 非 None 分支的快照由 resolve_executor 消费，须 active。
    config_service.get_snapshot = AsyncMock(
        return_value=MagicMock(
            snapshot_id="snap-1",
            config_revision=7,
            migration_state="active" if dispatch is not None else "pending",
            migration_transition=None,
            connections=({"connection_id": "conn-1"},),
            scenario_routes=(
                {
                    "scenario_id": _SCENARIO_ID,
                    "enabled": True,
                    "candidates": ["conn-1"],
                    "route_revision": 3,
                    "total_timeout_seconds": 45,
                    "task_routes": dispatch.task_routes if dispatch else {},
                },
            ),
            usage_policy={},
            security_revision=5,
            created_at="2026-10-16T00:00:00Z",
        )
    )
    monkeypatch.setattr(scenario_dispatch, "_new_config_service", lambda: config_service)
    monkeypatch.setattr(
        "services.ai_service.ConfigHandler.get_ai_news_prompt",
        staticmethod(lambda: "TOPIC_PROMPT"),
    )
    return dispatch


def _service() -> tuple[NewsClassifier, MagicMock]:
    svc = MagicMock()
    svc._chat_completion = AsyncMock(return_value={"category_L1": "finance", "category_L2": "monetary"})
    return NewsClassifier(svc), svc


# ---------------------------------------------------------------------------
# validate_output 分别校验（纯函数）
# ---------------------------------------------------------------------------


def test_validate_output_both_valid():
    out = _validate_bundled_output(_adapter_response(_bundled_json()))
    assert out.output["category_L1"] == "finance"
    assert out.output["sentiment"] == "Positive"
    assert out.metadata["actual_model"] == "glm-4-air"


def test_validate_output_topic_valid_sentiment_missing_kept():
    """主题有效 + 情绪缺失 → 保留主题输出，sentiment 规范化为 None（unavailable 不在 validate 层抛）。"""
    out = _validate_bundled_output(_adapter_response(_bundled_json(sentiment=None)))
    assert out.output["category_L1"] == "finance"
    assert out.output["sentiment"] is None


def test_validate_output_invalid_sentiment_normalized_none():
    """非法情绪值（小写 neutral 存量失败标记）不转换成合法 Neutral，归 None（R21）。"""
    out = _validate_bundled_output(_adapter_response(_bundled_json(sentiment="neutral")))
    assert out.output["sentiment"] is None


@pytest.mark.parametrize("content", [None, "not-json", '{"category_L1": "bogus_l1", "category_L2": "x"}'])
def test_validate_output_topic_invalid_raises(content):
    """主题 JSON 坏 / L1 白名单外 → ModelOutputValidationError（Executor 按失败切换）。"""
    with pytest.raises(ModelOutputValidationError) as excinfo:
        _validate_bundled_output(_adapter_response(content))
    # 强断言：三类拒绝（empty/invalid json/L1 白名单外）均以 "bundled output:" 前缀承载原因。
    assert str(excinfo.value).startswith("bundled output:")


# ---------------------------------------------------------------------------
# classify_news 新链分支
# ---------------------------------------------------------------------------


async def test_dispatch_unavailable_falls_back_to_legacy(monkeypatch):
    _patch_env(monkeypatch, None)
    classifier, svc = _service()

    result = await classifier.classify_news("央行宣布降准")

    svc._chat_completion.assert_awaited_once()
    assert result["category_L1"] == "finance"
    # 旧链返回不带新链字段
    assert "sentiment_status" not in result


async def test_bundled_success_single_request(monkeypatch):
    dispatch = _patch_env(monkeypatch, _dispatch())
    classifier, svc = _service()
    dispatch.executor.execute = AsyncMock(return_value=_succeeded_result(_bundled_json()))

    result = await classifier.classify_news("央行宣布降准")

    # 计量一次：仅一次统一请求，旧链不触发
    dispatch.executor.execute.assert_awaited_once()
    svc._chat_completion.assert_not_awaited()

    request = dispatch.executor.execute.await_args.args[0]
    assert request.scenario_id == _SCENARIO_ID
    assert request.task_id == "topic"
    assert request.payload["json_mode"] is True
    assert "TOPIC_PROMPT" in request.payload["system_prompt"]
    assert request.payload["prompt"].startswith("央行宣布降准")
    # deadline = loop.time() + total_timeout_seconds
    assert request.deadline > asyncio.get_running_loop().time()

    assert result["category_L1"] == "finance"
    assert result["sentiment"] == "Positive"
    assert result["sentiment_status"] == "success"
    assert result["sentiment_source"] == "bundled"
    meta = result["execution_metadata"]
    assert meta["invocation_id"]
    assert meta["route_revision"] == 3
    assert meta["connection_id"] == "conn-1"
    assert meta["actual_model"] == "glm-4-air"
    assert meta["usage_status"] == "reported"


async def test_bundled_topic_ok_sentiment_unavailable(monkeypatch):
    dispatch = _patch_env(monkeypatch, _dispatch())
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(return_value=_succeeded_result(_bundled_json(sentiment=None)))

    result = await classifier.classify_news("央行宣布降准")

    assert result["category_L1"] == "finance"
    assert result["sentiment"] is None
    assert result["sentiment_status"] == "unavailable"
    assert result["sentiment_source"] == "bundled"


async def test_bundled_all_candidates_failed(monkeypatch):
    dispatch = _patch_env(monkeypatch, _dispatch())
    classifier, svc = _service()
    dispatch.executor.execute = AsyncMock(return_value=_failed_result())

    result = await classifier.classify_news("央行宣布降准")

    assert result["category"] == "unknown" or result.get("category_L1") is None
    assert result["sentiment"] is None
    assert result["sentiment_status"] == "unavailable"
    assert "error" in result


async def test_text_truncated_before_dispatch(monkeypatch):
    dispatch = _patch_env(monkeypatch, _dispatch())
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(return_value=_succeeded_result(_bundled_json()))

    await classifier.classify_news("x" * (NEWS_TEXT_MAX_LEN + 100))

    request = dispatch.executor.execute.await_args.args[0]
    assert len(request.payload["prompt"]) == NEWS_TEXT_MAX_LEN


# ---------------------------------------------------------------------------
# dedicated 模式
# ---------------------------------------------------------------------------


async def test_dedicated_topic_and_sentiment_both_success(monkeypatch):
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()

    results = [
        _succeeded_result(_bundled_json(sentiment=None)),  # topic 请求（纯主题 prompt）
        ScenarioExecutionResult(
            scenario_id=_SCENARIO_ID,
            invocation_id="inv-2",
            status="succeeded",
            outputs=(ScenarioOutput(output={"sentiment": "Negative"}),),
            candidate_decisions=(
                CandidateDecision(
                    task_id="sentiment",
                    candidate_index=0,
                    connection_id="conn-9",
                    decision="succeeded",
                    reason="ok",
                ),
            ),
            actual_usage=(),
        ),
    ]
    dispatch.executor.execute = AsyncMock(side_effect=results)

    result = await classifier.classify_news("央行宣布降准")

    assert dispatch.executor.execute.await_count == 2
    topic_req, sentiment_req = [call.args[0] for call in dispatch.executor.execute.await_args_list]
    assert topic_req.task_id == "topic"
    assert sentiment_req.task_id == "sentiment"
    assert result["category_L1"] == "finance"
    assert result["sentiment"] == "Negative"
    assert result["sentiment_status"] == "success"
    assert result["sentiment_source"] == "dedicated:llm"
    assert result["execution_metadata"]["invocation_id"]


async def test_dedicated_topic_fail_sentiment_kept(monkeypatch):
    """主题失败不丢掉已成功情绪（§8.1 dedicated 独立语义）。"""
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()

    dispatch.executor.execute = AsyncMock(
        side_effect=[
            _failed_result(),
            ScenarioExecutionResult(
                scenario_id=_SCENARIO_ID,
                invocation_id="inv-2",
                status="succeeded",
                outputs=(ScenarioOutput(output={"sentiment": "Neutral"}),),
                candidate_decisions=(),
                actual_usage=(),
            ),
        ]
    )

    result = await classifier.classify_news("央行宣布降准")

    assert result["sentiment"] == "Neutral"
    assert result["sentiment_status"] == "success"
    assert result["sentiment_source"] == "dedicated:llm"
    assert result.get("category") == "unknown"


async def test_dedicated_sentiment_fail_topic_kept(monkeypatch):
    """情绪失败不重跑主题；主题结果保留，sentiment 缺失（R21）。"""
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()

    dispatch.executor.execute = AsyncMock(
        side_effect=[
            _succeeded_result(_bundled_json(sentiment=None)),
            _failed_result(),
        ]
    )

    result = await classifier.classify_news("央行宣布降准")

    assert dispatch.executor.execute.await_count == 2
    assert result["category_L1"] == "finance"
    assert result["sentiment"] is None
    assert result["sentiment_status"] == "unavailable"


async def test_bundled_sentiment_uuid_invocation_unique(monkeypatch):
    dispatch = _patch_env(monkeypatch, _dispatch())
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(return_value=_succeeded_result(_bundled_json()))

    r1 = await classifier.classify_news("第一条")
    r2 = await classifier.classify_news("第二条")

    assert r1["execution_metadata"]["invocation_id"] != r2["execution_metadata"]["invocation_id"]
    assert uuid.UUID(r1["execution_metadata"]["invocation_id"])  # hex 形态合法


# ---------------------------------------------------------------------------
# dedicated 修复回归（检视 D1/D2/D3）
# ---------------------------------------------------------------------------


async def test_dedicated_sentiment_uses_subroute_candidates(monkeypatch):
    """D1：sentiment 请求使用 task_routes.sentiment.candidates 独立子路由快照。"""
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()

    results = [
        _succeeded_result(_bundled_json(sentiment=None)),
        ScenarioExecutionResult(
            scenario_id=_SCENARIO_ID,
            invocation_id="inv-2",
            status="succeeded",
            outputs=(ScenarioOutput(output={"sentiment": "Negative"}),),
            candidate_decisions=(),
            actual_usage=(),
        ),
    ]
    dispatch.executor.execute = AsyncMock(side_effect=results)

    await classifier.classify_news("央行宣布降准")

    topic_call, sentiment_call = dispatch.executor.execute.await_args_list
    sentiment_req, sentiment_snap = sentiment_call.args[0], sentiment_call.args[1]
    assert sentiment_req.task_id == "sentiment"
    route = sentiment_snap.routes[0]
    assert route["scenario_id"] == _SCENARIO_ID
    assert [c["connection_id"] for c in route["candidates"]] == ["conn-9"]
    assert route["candidates"][0]["task_id"] == "sentiment"
    # 子路由快照仅替换 routes，connections 与分派快照共享
    assert sentiment_snap.connections == topic_call.args[1].connections
    # 主题请求仍走分派快照原路由
    assert [c["connection_id"] for c in topic_call.args[1].routes[0]["candidates"]] == ["conn-1"]


async def test_no_sentiment_key_defaults_to_dedicated(monkeypatch):
    """D2：无 sentiment 键 → dedicated（方案 §2.1：bundled iff mode=="bundled"）。"""
    dispatch = _dispatch()
    dispatch.task_routes.clear()  # 原地清空：_patch_env 捕获的 route 字典与分派快照同源
    _patch_env(monkeypatch, dispatch)
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(
        side_effect=[_succeeded_result(_bundled_json(sentiment=None)), _failed_result()]
    )

    result = await classifier.classify_news("央行宣布降准")

    assert dispatch.executor.execute.await_count == 2
    assert result["category_L1"] == "finance"
    assert result["sentiment"] is None
    assert result["sentiment_status"] == "unavailable"


async def test_dedicated_reuse_bundled_on_chain_unavailable(monkeypatch):
    """D3：专用链整体不可用 + 主题响应携带合法联合情绪 → reuse_bundled 显式降级。"""
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(
        side_effect=[
            _succeeded_result(_bundled_json(sentiment="Positive")),  # 主题响应携带合法联合情绪
            _failed_result(),  # sentiment 链整体不可用（upstream 5xx）
        ]
    )

    result = await classifier.classify_news("央行宣布降准")

    assert result["sentiment"] == "Positive"
    assert result["sentiment_status"] == "success"
    assert result["sentiment_source"] == "reuse_bundled"


async def test_dedicated_invalid_output_exhausted_no_reuse(monkeypatch):
    """D3：invalid_output 耗尽 → 保持缺失不回填（R21），即使主题响应有合法联合情绪。"""
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(
        side_effect=[
            _succeeded_result(_bundled_json(sentiment="Positive")),
            ScenarioExecutionResult(
                scenario_id=_SCENARIO_ID,
                invocation_id="inv-2",
                status="failed",
                outputs=(),
                candidate_decisions=(
                    CandidateDecision(
                        task_id="sentiment",
                        candidate_index=0,
                        connection_id="conn-9",
                        decision="switched",
                        reason="output validation failed: ModelOutputValidationError",
                    ),
                ),
                actual_usage=(),
            ),
        ]
    )

    result = await classifier.classify_news("央行宣布降准")

    assert result["sentiment"] is None
    assert result["sentiment_status"] == "unavailable"
    assert result["sentiment_source"] == "dedicated:llm"


async def test_dedicated_invalid_joint_sentiment_not_reused(monkeypatch):
    """R21：主题响应的非法情绪值（存量失败标记 neutral）不作复用来源。"""
    dispatch = _patch_env(monkeypatch, _dispatch(mode="dedicated"))
    classifier, _svc = _service()
    dispatch.executor.execute = AsyncMock(
        side_effect=[
            _succeeded_result(_bundled_json(sentiment="neutral")),  # 非法值 → validator 归 None
            _failed_result(),
        ]
    )

    result = await classifier.classify_news("央行宣布降准")

    assert result["sentiment"] is None
    assert result["sentiment_status"] == "unavailable"
