# pyright: reportArgumentType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false
# 本文件含测试替身/mock 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）、
# Optional 成员访问（mock 返回 None）、Optional 下标访问。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""R06-D2/D3 统一链云出口 SEC-01 门控 + SEC-03 审计回归。

统一链四场景（stock_analysis / news_annotation / news_risk / synthetic_test）全部
经 ``ScenarioExecutor → _LiteLLMChatAdapter → LiteLLMClient.invoke_single_connection``
出站，本文件验证该唯一云出口的既有安全机制接线：

1. SEC-01（R06-D3）：``is_egress_acknowledged()`` 未确认 → 抛
   ``AIPolicyNotAcknowledgedError``（与旧链门控同异常同 i18n key），``acompletion``
   零调用（零出站）、``EgressAudit.record`` 零调用（未外发即无外发记录）；
2. SEC-01：已确认 → 正常 dispatch，不抛策略异常；
3. SEC-03（R06-D2）：dispatch 前接既有 ``EgressAudit`` 账本（真实单例落盘断言，
   证明接入的是审计状态栏消费的同一 JSONL 事实源，非平行账本）——destination 用
   真实发送的 litellm_model（含 provider 前缀），category 用场景 id；
4. SEC-03：审计降级（record 普通异常）不阻断 dispatch；
5. R2：``EgressAudit.record`` 抛 ``CancelledError`` 必须传播（不吞没）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.errors import AIPolicyNotAcknowledgedError
from core.model_management_types import InferenceRequest, ResolvedConnection
from services.ai_service.litellm_client import LiteLLMClient
from utils.egress_audit import EgressAudit

pytestmark = pytest.mark.unit

_CATEGORY = "news_annotation"


@pytest.fixture(autouse=True)
def _tmp_egress_path(tmp_path):
    """注入 EgressAudit 落盘路径到临时目录（reset 后、首次 record 前，R7 单例隔离）。"""
    EgressAudit._reset_singleton()
    EgressAudit._configure_for_tests(str(tmp_path / "egress_audit.jsonl"))
    yield tmp_path


def _read_records(path) -> list[dict]:
    """读取 JSONL 审计文件（唯一事实源）为记录列表；无文件返回空列表。"""
    file = path / "egress_audit.jsonl"
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _connection() -> ResolvedConnection:
    return ResolvedConnection(
        connection_id="conn-1",
        connection_revision=1,
        adapter_kind="litellm_chat",
        provider="openai",
        model="gpt-test",
        api_base="https://api.test.com",
        adapter_options={},
        context_window=None,
        concurrency_limit=1,
        credential_ref=None,
        credential_version=None,
    )


def _request(scenario_id: str = _CATEGORY) -> InferenceRequest:
    # deadline 为单调时钟截止值：运行 loop.time() 起恒有余量
    return InferenceRequest(
        scenario_id=scenario_id,
        task_id="t1",
        invocation_id="inv-r06",
        payload={"system_prompt": "sys prompt", "prompt": "user content"},
        deadline=asyncio.get_running_loop().time() + 30.0,
    )


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        model="gpt-test",
        usage=None,
    )


async def _invoke(request: InferenceRequest | None = None):
    """统一调用入口：打桩 litellm 加载与 acompletion（零真实网络），真实 EgressAudit。"""
    request = request or _request()
    client = LiteLLMClient(MagicMock())
    acompletion = AsyncMock(return_value=_ok_response())
    with (
        patch("services.ai_service._ensure_litellm_loaded", return_value=True),
        patch("services.ai_service.acompletion", acompletion),
    ):
        response = await client.invoke_single_connection(request, _connection(), request.deadline, MagicMock())
    return response, acompletion


class TestSec01EgressGate:
    """SEC-01（R06-D3）：统一链出口知情确认门控。"""

    @pytest.mark.asyncio
    async def test_unacknowledged_refuses_without_outbound(self, _tmp_egress_path) -> None:
        """未确认 → 抛策略异常，acompletion 零调用（零出站）、EgressAudit 零记录。"""
        acompletion = AsyncMock()
        record = AsyncMock()
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=False),
            patch("services.ai_service.acompletion", acompletion),
            patch("utils.egress_audit.EgressAudit.record", record),
        ):
            request = _request()
            with pytest.raises(AIPolicyNotAcknowledgedError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_single_connection(
                    request, _connection(), request.deadline, MagicMock()
                )

        # 精确异常属性断言（非弱断言）：类型 + i18n key 与旧链门控同源
        assert excinfo.type is AIPolicyNotAcknowledgedError
        assert excinfo.value.message.key == "ai_external_acknowledgment_prompt"
        acompletion.assert_not_awaited()  # 零出站
        record.assert_not_awaited()  # 未外发即无外发记录
        assert _read_records(_tmp_egress_path) == []

    @pytest.mark.asyncio
    async def test_acknowledged_dispatches(self, _tmp_egress_path) -> None:
        """已确认 → 正常 dispatch，不抛策略异常。"""
        with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True):
            response, _ = await _invoke()

        assert response.content == "ok"


class TestSec03EgressAudit:
    """SEC-03（R06-D2）：统一链出口接既有 EgressAudit 账本。"""

    @pytest.mark.asyncio
    async def test_dispatch_records_to_shared_audit_ledger(self, _tmp_egress_path) -> None:
        """dispatch 后既有账本恰一条记录：destination=真实发送 model、category=场景 id。"""
        with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True):
            await _invoke()

        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["destination"] == "llm:openai/gpt-test"
        assert rec["category"] == _CATEGORY
        assert rec["item_count"] == 2
        assert rec["payload_size_bytes"] > 0
        assert rec["status"] == "sent"
        # 元数据审计不记录 prompt 内容本身（防二次泄露）
        assert "user content" not in json.dumps(rec, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_audit_failure_degrades_dispatch(self, _tmp_egress_path) -> None:
        """审计 record 抛普通异常 → 降级不阻断，dispatch 仍成功返回。"""
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch("utils.egress_audit.EgressAudit.record", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            response, _ = await _invoke()

        assert response.content == "ok"
        assert _read_records(_tmp_egress_path) == []

    @pytest.mark.asyncio
    async def test_audit_cancelled_error_propagates(self, _tmp_egress_path) -> None:
        """审计 record 抛 CancelledError 必须传播（R2：不吞没、优雅停机）。"""
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch(
                "utils.egress_audit.EgressAudit.record",
                AsyncMock(side_effect=asyncio.CancelledError()),
            ),
        ):
            with pytest.raises(asyncio.CancelledError) as excinfo:
                await _invoke()

        assert excinfo.type is asyncio.CancelledError  # R2: 取消必须传播而非吞没


class TestNullContentJsonConvergence:
    """content=None（tool_calls/reasoning 响应合法形态）→ 统一 ValueError 收敛。

    修复前：json.loads(None) 抛 TypeError 绕过 "Invalid JSON response" 收敛——
    ``_chat_completion`` 直接冒泡未分类异常；``_router_failover`` 落入外层
    except Exception 经 classify_error 误分类（可能收敛为"所有供应商失败"并
    触发无意义重试）。
    """

    @pytest.mark.asyncio
    async def test_chat_completion_json_mode_none_content_raises_value_error(self) -> None:
        """_chat_completion（cloud + json_mode）：content=None → ValueError 而非 TypeError。"""
        svc = MagicMock()
        svc._chat_completion_litellm = AsyncMock(return_value={"content": None})
        client = LiteLLMClient(svc)

        with pytest.raises(ValueError, match="Invalid JSON response"):
            await client._chat_completion(messages=[{"role": "user", "content": "hi"}], json_mode=True)

    @pytest.mark.asyncio
    async def test_router_failover_none_content_raises_value_error(self) -> None:
        """_router_failover：Router 返回 content=None → ValueError 收敛，
        不经外层 except 被误分类为供应商失败（should_retry=False 原样上抛）。"""
        svc = MagicMock()
        svc._litellm_config = {"model": "gpt-test"}
        client = LiteLLMClient(svc)
        router = MagicMock()
        router.acompletion = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
                model="gpt-test",
                usage=None,
            )
        )
        with (
            patch(
                "utils.config_handler.ConfigHandler.get_failover_config",
                return_value={"primary": "openai/gpt-test", "fallbacks": []},
            ),
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service._ensure_router_loaded", return_value=True),
            patch("services.ai_service._litellm_router", router),
            patch("services.ai_service.litellm_client._estimate_tokens", return_value=1),
        ):
            with pytest.raises(ValueError, match="Invalid JSON response"):
                await client._router_failover(messages=[{"role": "user", "content": "hi"}])
