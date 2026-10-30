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
5. R2：``EgressAudit.record`` 抛 ``CancelledError`` 必须传播（不吞没）；
6. F27：非流式响应的内容安全拒绝信号（``message.refusal`` /
   ``finish_reason == "content_filter"``）提炼为 ``AIContentRefusedError``
   策略终态（Executor 据此终止候选遍历，禁止重试与换候选绕过）；
7. UIR-F23-003 外发结果回写：单次尝试只落 **一条结果记录**（意图与结果合并，不双记），
   ``status`` 真实反映 sent / failed / cancelled，``error_category`` 记录分类码，
   ``destination_host`` 记录出口主机（host[:port]，不含凭据/路径）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.errors import AIContentRefusedError, AIPolicyNotAcknowledgedError
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


def _connection(api_base: str | None = "https://api.test.com", provider: str = "openai") -> ResolvedConnection:
    return ResolvedConnection(
        connection_id="conn-1",
        connection_revision=1,
        adapter_kind="litellm_chat",
        provider=provider,
        model="gpt-test",
        api_base=api_base,
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


async def _invoke_with(
    acompletion: AsyncMock,
    *,
    request: InferenceRequest | None = None,
    connection: ResolvedConnection | None = None,
):
    """按给定 acompletion 行为执行单连接调用（知情确认已通过，零真实网络）。"""
    request = request or _request()
    client = LiteLLMClient(MagicMock())
    with (
        patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
        patch("services.ai_service._ensure_litellm_loaded", return_value=True),
        patch("services.ai_service.acompletion", acompletion),
    ):
        return await client.invoke_single_connection(
            request, connection or _connection(), request.deadline, MagicMock()
        )


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
        assert rec["destination_host"] == "api.test.com"
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


class TestEgressOutcomeWriteback:
    """UIR-F23-003：外发结果回写（单条结果记录，意图与结果合并，不双记）。

    审计需回答「是否真的外发成功、发往哪」：`status` 由 dispatch 结果决定
    （sent / failed / cancelled），`destination_host` 记录出口主机，`error_category`
    记录分类码；失败/取消不伪装成 `sent`，未知主机记 None（R21 不伪装）。
    """

    @pytest.mark.asyncio
    async def test_success_single_outcome_record(self, _tmp_egress_path) -> None:
        """成功 → 恰一条 sent 记录（无意图+结果双记），error_category 为空。"""
        await _invoke_with(AsyncMock(return_value=_ok_response()))

        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["status"] == "sent"
        assert rec["error_category"] is None
        assert rec["destination_host"] == "api.test.com"

    @pytest.mark.asyncio
    async def test_dispatch_failure_records_failed_and_reraises(self, _tmp_egress_path) -> None:
        """dispatch 抛超时 → 回写 failed/timeout（含主机）后原异常继续传播。"""
        with pytest.raises(TimeoutError) as excinfo:
            await _invoke_with(AsyncMock(side_effect=TimeoutError("Request timed out")))

        assert excinfo.type is TimeoutError  # 原始异常未被审计路径改写
        assert str(excinfo.value) == "Request timed out"
        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["status"] == "failed"
        assert rec["error_category"] == "timeout"
        assert rec["destination_host"] == "api.test.com"
        # 错误消息正文不入审计（只记分类码）
        assert "timed out" not in json.dumps(rec, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_content_refusal_records_failed(self, _tmp_egress_path) -> None:
        """内容安全拒绝 → 回写 failed/content_policy，仍抛 AIContentRefusedError。"""
        refusal = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, refusal="该内容违反使用政策。"),
                    finish_reason="stop",
                )
            ],
            model="gpt-test",
            usage=None,
        )
        with pytest.raises(AIContentRefusedError) as excinfo:
            await _invoke_with(AsyncMock(return_value=refusal))

        assert excinfo.type is AIContentRefusedError  # 策略终态信号未被改写
        assert excinfo.value.message.key == "llm_err_content_policy"
        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert records[0]["error_category"] == "content_policy"

    @pytest.mark.asyncio
    async def test_cancelled_dispatch_records_cancelled_and_propagates(self, _tmp_egress_path) -> None:
        """dispatch 被取消 → 回写 cancelled 后 CancelledError 继续传播（R2）。"""
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await _invoke_with(AsyncMock(side_effect=asyncio.CancelledError()))

        assert excinfo.type is asyncio.CancelledError  # R2: 取消必须传播而非吞没
        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["status"] == "cancelled"
        assert rec["error_category"] == "cancelled"
        assert rec["destination_host"] == "api.test.com"

    @pytest.mark.asyncio
    async def test_audit_write_failure_does_not_mask_dispatch_error(self, _tmp_egress_path) -> None:
        """失败路径上审计写失败仅降级：原始 dispatch 异常照常传播，无记录。"""
        with patch("utils.egress_audit.EgressAudit.record", AsyncMock(side_effect=RuntimeError("audit boom"))):
            with pytest.raises(TimeoutError) as excinfo:
                await _invoke_with(AsyncMock(side_effect=TimeoutError("Request timed out")))

        assert excinfo.type is TimeoutError  # 审计异常未掩盖原始 dispatch 异常
        assert "audit boom" not in str(excinfo.value)
        assert _read_records(_tmp_egress_path) == []

    @pytest.mark.asyncio
    async def test_retry_attempts_each_record_once(self, _tmp_egress_path) -> None:
        """Executor 重试语义：每次尝试恰一条结果记录（先 failed 后 sent，无多余意图记录）。"""
        request = _request()
        client = LiteLLMClient(MagicMock())
        acompletion = AsyncMock(side_effect=[TimeoutError("Request timed out"), _ok_response()])
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", acompletion),
        ):
            with pytest.raises(TimeoutError) as excinfo:
                await client.invoke_single_connection(request, _connection(), request.deadline, MagicMock())
            assert excinfo.type is TimeoutError
            await client.invoke_single_connection(request, _connection(), request.deadline, MagicMock())

        assert acompletion.await_count == 2
        records = _read_records(_tmp_egress_path)
        assert [r["status"] for r in records] == ["failed", "sent"]

    @pytest.mark.asyncio
    async def test_host_strips_credentials_and_path(self, _tmp_egress_path) -> None:
        """自定义 api_base 带凭据/端口/路径 → 只记 host[:port]，凭据与路径不入审计（R9）。"""
        connection = _connection(api_base="https://user:secret@api.example.com:8443/v1/proxy")
        await _invoke_with(AsyncMock(return_value=_ok_response()), connection=connection)

        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["destination_host"] == "api.example.com:8443"
        dumped = json.dumps(rec, ensure_ascii=False)
        assert "secret" not in dumped
        assert "/v1/proxy" not in dumped

    @pytest.mark.asyncio
    async def test_host_none_when_api_base_absent(self, _tmp_egress_path) -> None:
        """连接未声明 api_base → destination_host 记 None（R21 不伪装具体主机）。"""
        connection = _connection(api_base=None)
        await _invoke_with(AsyncMock(return_value=_ok_response()), connection=connection)

        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        assert records[0]["destination_host"] is None
        assert records[0]["status"] == "sent"


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


class TestF27ContentRefusalExtraction:
    """F27：非流式响应的内容安全拒绝信号提炼为策略终态异常（AIContentRefusedError）。

    只读 content 会把 refusal / content_filter 伪装成「无输出」，Executor 将按
    输出非法类切换候选绕过安全拒绝；本类验证拒绝信号在统一链出口即被提炼为终止
    语义。覆盖边界（R21 诚实声明）：OpenAI 兼容响应模式（``message.refusal`` /
    ``finish_reason == "content_filter"``）已核验；Anthropic / Gemini 原生拒绝
    块等格式未核验、不伪装已覆盖（SDK 层 ContentPolicyViolationError 由 Executor
    异常分类承载）。
    """

    @staticmethod
    def _refusal_response(
        *, refusal: str | None = "抱歉，该内容违反使用政策。", finish_reason: str | None = "stop"
    ) -> SimpleNamespace:
        message = SimpleNamespace(content=None, refusal=refusal)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
            model="gpt-test",
            usage=None,
        )

    @pytest.mark.asyncio
    async def test_message_refusal_raises_terminal_refusal(self, _tmp_egress_path) -> None:
        """message.refusal 非空白 → AIContentRefusedError（i18n key 与既有错误文案同源）。"""
        request = _request()
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", AsyncMock(return_value=self._refusal_response())),
        ):
            with pytest.raises(AIContentRefusedError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_single_connection(
                    request, _connection(), request.deadline, MagicMock()
                )
        assert excinfo.type is AIContentRefusedError
        assert excinfo.value.message.key == "llm_err_content_policy"

    @pytest.mark.asyncio
    async def test_content_filter_finish_reason_raises_terminal_refusal(self, _tmp_egress_path) -> None:
        """finish_reason == "content_filter"（refusal 字段缺失的 provider 形态）→ 同样终止。"""
        request = _request()
        response = self._refusal_response(refusal=None, finish_reason="content_filter")
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", AsyncMock(return_value=response)),
        ):
            with pytest.raises(AIContentRefusedError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_single_connection(
                    request, _connection(), request.deadline, MagicMock()
                )
        assert excinfo.value.message.key == "llm_err_content_policy"

    @pytest.mark.asyncio
    async def test_blank_refusal_does_not_raise(self, _tmp_egress_path) -> None:
        """空白 refusal（非真实拒绝）→ 不误判，走正常 content 提取路径。"""
        request = _request()
        response = self._refusal_response(refusal="   ", finish_reason="stop")
        response.choices[0].message.content = "ok"
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", AsyncMock(return_value=response)),
        ):
            result = await LiteLLMClient(MagicMock()).invoke_single_connection(
                request, _connection(), request.deadline, MagicMock()
            )
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_response_without_refusal_field_regression(self, _tmp_egress_path) -> None:
        """回归：无 refusal 属性的历史响应形态不受影响（getattr 缺省 None 不触发）。"""
        with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True):
            response, _ = await _invoke()
        assert response.content == "ok"
