"""S2 单连接执行 litellm 侧测试（ADR-0014 §6.1/§6.2）。

覆盖三块契约：
- ``normalize_litellm_usage``：MM-08 五字段用量归一化（OpenAI prompt_tokens 已含
  cached_tokens 不重复相加；Anthropic 协议 cache_creation/read 存在非 None 时 input 加回；
  缺失不默认 0；非负整数或 None）；
- ``resolve_credential``：credential_ref 三形态（keyring:/env:/None）→ 实际秘密，
  R9（异常消息不含秘密）与显式失败（未知 scheme / 缺失项）；
- ``LiteLLMClient.invoke_single_connection``：单连接 HTTP 调用、``num_retries=0``、
  无 Router、deadline 单调时钟预算、usage 归一化、CancelledError 传播（R2），
  以及受控端点断言实际出站次数（真实锁定 litellm，非 MagicMock）证明无 SDK 隐藏重试。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.ai_service.litellm_client import LiteLLMClient
from core.model_management_types import (
    AdapterResponse,
    AttemptContext,
    InferenceRequest,
    NormalizedLLMUsage,
)
from services.ai_service.litellm_client import normalize_litellm_usage, resolve_credential
from utils.egress_audit import EgressAudit


@pytest.fixture(autouse=True)
def _ack_egress_and_isolate_audit(tmp_path):
    """SEC-01 门控（R06-D3）：本文件验证单连接 HTTP 行为，非门控本身，默认视为已确认
    （与 test_ai_egress_audit_integration.py 的 _ack_cloud_egress 同模式）。门控拒绝
    语义由 test_r06_d23_unified_chain_egress.py 专项覆盖。
    同时把 EgressAudit 落盘隔离到临时目录（R06-D2：统一链出口现已接审计账本，
    防止测试 dispatch 写入真实用户数据目录的 egress_audit.jsonl）。
    """
    EgressAudit._reset_singleton()
    EgressAudit._configure_for_tests(str(tmp_path / "egress_audit.jsonl"))
    with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True):
        yield


# ---------------------------------------------------------------------------
# 测试构造 helper
# ---------------------------------------------------------------------------
def _attempt_context() -> AttemptContext:
    return AttemptContext(
        owner_session_id="sess-1",
        owner_epoch=1,
        invocation_id="inv-1",
        attempt_id="att-1",
    )


def _connection(**overrides):
    from core.model_management_types import ResolvedConnection

    base = {
        "connection_id": "conn-1",
        "connection_revision": 1,
        "adapter_kind": "litellm_chat",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_base": "https://api.deepseek.com",
        "adapter_options": {},
        "context_window": None,
        "concurrency_limit": 1,
        "credential_ref": None,
        "credential_version": None,
    }
    base.update(overrides)
    return ResolvedConnection(**base)


def _request(payload: dict | None = None, deadline: float | None = None) -> InferenceRequest:
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + 60.0
    return InferenceRequest(
        scenario_id="stock_analysis",
        task_id="task-1",
        invocation_id="inv-1",
        payload=payload if payload is not None else {"prompt": "hello", "max_tokens": 128, "temperature": 0.2},
        deadline=deadline,
        run_id=None,
    )


def _fake_response(content: str = "ok", model: str = "deepseek/deepseek-chat", usage=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.model = model
    resp.usage = usage
    return resp


@pytest.fixture()
def real_litellm():
    """受控端点测试需要真实锁定 litellm（判定证据要求，非 MagicMock）。

    conftest 在 pytest_configure 阶段把 ``sys.modules["litellm"]`` 替换为 mock，
    并经会话级 fixture 把 ``services.ai_service.litellm/acompletion`` 指向 mock；
    本 fixture 临时还原真实模块属性与 sys.modules 项，测试后恢复 mock 状态。
    """
    import importlib

    import services.ai_service as ai_service

    saved_attrs = (ai_service.litellm, ai_service.acompletion, ai_service.LITELLM_AVAILABLE)
    saved_module = sys.modules.get("litellm")

    mocked = sys.modules.pop("litellm", None)
    try:
        real = importlib.import_module("litellm")
        ai_service.litellm = real
        ai_service.acompletion = real.acompletion
        ai_service.LITELLM_AVAILABLE = True
        yield real
    finally:
        if mocked is not None:
            sys.modules["litellm"] = mocked
        elif saved_module is not None:
            sys.modules["litellm"] = saved_module
        ai_service.litellm, ai_service.acompletion, ai_service.LITELLM_AVAILABLE = saved_attrs


# ---------------------------------------------------------------------------
# normalize_litellm_usage（MM-08）
# ---------------------------------------------------------------------------
class TestNormalizeLitellmUsage:
    def test_none_returns_none(self):
        assert normalize_litellm_usage(None) is None

    def test_openai_plain(self):
        usage = normalize_litellm_usage({"prompt_tokens": 100, "completion_tokens": 20})
        assert usage == NormalizedLLMUsage(
            input_total_tokens=100,
            uncached_input_tokens=100,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=20,
        )

    def test_openai_with_cached_tokens_not_double_added(self):
        """OpenAI 协议 prompt_tokens 已含 cached_tokens → 不重复相加（MM-08）。"""
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 30},
            }
        )
        assert usage is not None
        assert usage.input_total_tokens == 100
        assert usage.uncached_input_tokens == 70
        assert usage.cache_read_tokens == 30
        assert usage.cache_write_tokens is None
        assert usage.output_tokens == 20

    def test_anthropic_cache_fields_added_back(self):
        """Anthropic 协议：input_tokens 不含缓存 token → 归一化时加回（MM-08）。"""
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 40,
            }
        )
        assert usage is not None
        assert usage.input_total_tokens == 100
        assert usage.uncached_input_tokens == 50
        assert usage.cache_read_tokens == 40
        assert usage.cache_write_tokens == 10
        assert usage.output_tokens == 5

    def test_no_usable_fields_returns_none(self):
        """全部业务字段缺失/无效 → None（缺失不默认 0，R21 同源语义）。"""
        assert normalize_litellm_usage({"foo": 1}) is None
        assert normalize_litellm_usage({"prompt_tokens": True}) is None  # bool 排除
        assert normalize_litellm_usage({"prompt_tokens": 100.5}) is None  # 非整数拒绝

    def test_partial_fields_kept(self):
        usage = normalize_litellm_usage({"prompt_tokens": 100})
        assert usage is not None
        assert usage.input_total_tokens == 100
        assert usage.uncached_input_tokens == 100
        assert usage.cache_read_tokens is None
        assert usage.output_tokens is None

    def test_negative_clamped_to_zero(self):
        usage = normalize_litellm_usage({"prompt_tokens": -5, "completion_tokens": 3})
        assert usage is not None
        assert usage.input_total_tokens == 0
        assert usage.uncached_input_tokens == 0
        assert usage.output_tokens == 3

    def test_attribute_access_carrier(self):
        usage = normalize_litellm_usage(
            SimpleNamespace(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=None)
        )
        assert usage is not None
        assert usage.input_total_tokens == 100
        assert usage.output_tokens == 20


# ---------------------------------------------------------------------------
# resolve_credential（credential_ref 三形态）
# ---------------------------------------------------------------------------
class TestResolveCredential:
    def test_none_ref_returns_none(self):
        assert resolve_credential(None) is None

    def test_env_ref_resolved(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("QTR_TEST_CRED", "sk-test-env")
        assert resolve_credential("env:QTR_TEST_CRED") == "sk-test-env"

    def test_env_missing_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("QTR_TEST_MISSING", raising=False)
        with pytest.raises(ValueError, match="QTR_TEST_MISSING"):
            resolve_credential("env:QTR_TEST_MISSING")

    def test_keyring_ref_resolved(self, monkeypatch: pytest.MonkeyPatch):
        import utils.config_handler as cfg

        captured: dict = {}

        def fake_get_password(service: str, item: str):
            captured["service"], captured["item"] = service, item
            return "sk-from-keyring"

        monkeypatch.setattr(cfg.keyring, "get_password", fake_get_password)
        assert resolve_credential("keyring:model-connection-abc") == "sk-from-keyring"
        assert captured["service"] == cfg.KEYRING_SERVICE_NAME
        assert captured["item"] == "model-connection-abc"

    def test_keyring_missing_raises(self, monkeypatch: pytest.MonkeyPatch):
        import utils.config_handler as cfg

        monkeypatch.setattr(cfg.keyring, "get_password", lambda service, item: None)
        with pytest.raises(ValueError, match="model-connection-none"):
            resolve_credential("keyring:model-connection-none")

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="scheme"):
            resolve_credential("file:///etc/passwd")

    def test_malformed_ref_raises(self):
        with pytest.raises(ValueError, match="empty variable name"):
            resolve_credential("env:")


# ---------------------------------------------------------------------------
# invoke_single_connection（单连接契约）
# ---------------------------------------------------------------------------
class TestInvokeSingleConnection:
    def _invoke(self, client: LiteLLMClient, request: InferenceRequest, connection):
        return client.invoke_single_connection(request, connection, request.deadline, _attempt_context())

    async def test_rejects_non_litellm_adapter_kind(self):
        client = LiteLLMClient(MagicMock())
        with patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac:
            with pytest.raises(ValueError, match="adapter_kind"):
                await self._invoke(client, _request(), _connection(adapter_kind="gguf_sentiment"))
        mock_ac.assert_not_awaited()

    async def test_litellm_unavailable_raises(self):
        client = LiteLLMClient(MagicMock())
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=False),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            with pytest.raises(RuntimeError, match="LiteLLM"):
                await self._invoke(client, _request(), _connection())
        mock_ac.assert_not_awaited()

    async def test_deadline_exhausted_zero_outbound(self):
        client = LiteLLMClient(MagicMock())
        loop = asyncio.get_running_loop()
        request = _request(deadline=loop.time() - 1.0)
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            with pytest.raises(TimeoutError) as exc_info:
                await client.invoke_single_connection(request, _connection(), request.deadline, _attempt_context())
        assert type(exc_info.value) is TimeoutError
        mock_ac.assert_not_awaited()

    async def test_num_retries_zero_and_timeout_budget(self):
        """S2 核心契约：num_retries=0（无 SDK 隐藏重试）；timeout 受 deadline 预算裁剪。"""
        client = LiteLLMClient(MagicMock())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 45.0
        request = _request(deadline=deadline)
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await client.invoke_single_connection(request, _connection(), deadline, _attempt_context())

        kwargs = mock_ac.await_args.kwargs
        assert kwargs["num_retries"] == 0
        timeout = kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        # 预算 = min(DEFAULT_CLOUD_TIMEOUT=30, deadline−now≈45) → 30
        assert timeout.read is not None and timeout.read <= 30.0
        assert timeout.connect == 5.0

    async def test_remaining_budget_clamps_timeout(self):
        client = LiteLLMClient(MagicMock())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.8
        request = _request(deadline=deadline)
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await client.invoke_single_connection(request, _connection(), deadline, _attempt_context())

        timeout = mock_ac.await_args.kwargs["timeout"]
        assert timeout.read <= 0.8

    async def test_model_prefix_and_api_base(self):
        client = LiteLLMClient(MagicMock())
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await self._invoke(client, _request(), _connection())

        kwargs = mock_ac.await_args.kwargs
        assert kwargs["model"] == "deepseek/deepseek-chat"
        assert kwargs["api_base"] == "https://api.deepseek.com"

    async def test_unknown_provider_falls_back_openai_prefix(self):
        client = LiteLLMClient(MagicMock())
        conn = _connection(provider="custom-self-hosted", model="research-model", api_base="http://127.0.0.1:8000/v1")
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await self._invoke(client, _request(), conn)

        kwargs = mock_ac.await_args.kwargs
        assert kwargs["model"] == "openai/research-model"
        assert kwargs["api_base"] == "http://127.0.0.1:8000/v1"

    async def test_payload_keys_built_into_messages_and_params(self):
        client = LiteLLMClient(MagicMock())
        payload = {"prompt": "user text", "max_tokens": 128, "temperature": 0.2, "system_prompt": "be terse"}
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await self._invoke(client, _request(payload=payload), _connection())

        kwargs = mock_ac.await_args.kwargs
        assert kwargs["messages"] == [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "user text"},
        ]
        assert kwargs["max_tokens"] == 128
        assert kwargs["temperature"] == 0.2

    async def test_content_usage_actual_model(self):
        client = LiteLLMClient(MagicMock())
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response(content="answer", model="deepseek/deepseek-chat", usage=usage)
            response = await self._invoke(client, _request(), _connection())

        assert isinstance(response, AdapterResponse)
        assert response.content == "answer"
        assert response.actual_model == "deepseek/deepseek-chat"
        assert response.usage is not None
        assert response.usage.input_total_tokens == 100
        assert response.usage.output_tokens == 20

    async def test_credential_env_resolved_into_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("QTR_INVOKE_KEY", "sk-invoke")
        client = LiteLLMClient(MagicMock())
        # env: 形态凭据无版本化租约语义（F06：version 必须为 None）
        conn = _connection(credential_ref="env:QTR_INVOKE_KEY", credential_version=None)
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await self._invoke(client, _request(), conn)

        assert mock_ac.await_args.kwargs["api_key"] == "sk-invoke"

    async def test_credential_none_omits_api_key(self):
        client = LiteLLMClient(MagicMock())
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await self._invoke(client, _request(), _connection(credential_ref=None))

        assert "api_key" not in mock_ac.await_args.kwargs

    async def test_cancelled_error_propagates(self):
        """R2：acompletion 抛 CancelledError 必须传播，不得包装。"""
        client = LiteLLMClient(MagicMock())
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await self._invoke(client, _request(), _connection())
        assert type(exc_info.value) is asyncio.CancelledError  # R2 传播纯度：非子类包装


# ---------------------------------------------------------------------------
# 受控端点：真实锁定 litellm 断言实际出站次数（判定证据）
# ---------------------------------------------------------------------------
class TestSingleConnectionNoHiddenRetry:
    """500 端点 + 真实 litellm：断言恰好 1 次出站（num_retries=0 无隐藏重试/备用）。"""

    async def test_server_error_outbound_once(self, monkeypatch: pytest.MonkeyPatch, real_litellm):
        pytest.importorskip("litellm")
        monkeypatch.setenv("QTR_NO_RETRY_KEY", "sk-test-local-endpoint")
        # 代理环境隔离：受控端点必须直连（litellm_env_context 亦会清代理，双保险）
        for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(var, raising=False)

        request_count = 0

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            nonlocal request_count
            request_count += 1
            await reader.read(65536)
            body = json.dumps({"error": {"message": "boom", "type": "server_error", "code": None}}).encode()
            writer.write(
                b"HTTP/1.1 500 Internal Server Error\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        conn = _connection(
            provider="openai",
            model="research-model",
            api_base=f"http://127.0.0.1:{port}/v1",
            credential_ref="env:QTR_NO_RETRY_KEY",
        )
        client = LiteLLMClient(MagicMock())
        request = _request(deadline=asyncio.get_running_loop().time() + 30.0)
        try:
            with pytest.raises(Exception) as excinfo:
                await asyncio.wait_for(
                    client.invoke_single_connection(request, conn, request.deadline, _attempt_context()),
                    timeout=25.0,
                )
            # CancelledError 之外的异常即服务端 500 传播（openai SDK APIStatusError 系）
            assert not isinstance(excinfo.value, asyncio.CancelledError)
        finally:
            server.close()
            await server.wait_closed()

        assert request_count == 1, f"expected exactly 1 outbound request, got {request_count}"

    async def test_success_response_returns_content(self, monkeypatch: pytest.MonkeyPatch, real_litellm):
        """受控端点 200 正常路径：content/usage/actual_model 全链路提取。"""
        pytest.importorskip("litellm")
        monkeypatch.setenv("QTR_NO_RETRY_KEY", "sk-test-local-endpoint")
        for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(var, raising=False)

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            await reader.read(65536)
            body = json.dumps(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "research-model",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        conn = _connection(
            provider="openai",
            model="research-model",
            api_base=f"http://127.0.0.1:{port}/v1",
            credential_ref="env:QTR_NO_RETRY_KEY",
        )
        client = LiteLLMClient(MagicMock())
        request = _request(deadline=asyncio.get_running_loop().time() + 30.0)
        try:
            response = await asyncio.wait_for(
                client.invoke_single_connection(request, conn, request.deadline, _attempt_context()),
                timeout=25.0,
            )
        finally:
            server.close()
            await server.wait_closed()

        assert response.content == "pong"
        assert response.usage is not None
        assert response.usage.input_total_tokens == 11
        assert response.usage.output_tokens == 1

    def test_no_proxy_env_leak_of_secret_in_errors(self, monkeypatch: pytest.MonkeyPatch):
        """R9：keyring 解析失败的异常消息不得含秘密本身（仅含项名）。"""
        import utils.config_handler as cfg

        def exploding_get_password(service: str, item: str):
            raise RuntimeError("keyring backend locked (secret=sk-super-secret-value)")

        monkeypatch.setattr(cfg.keyring, "get_password", exploding_get_password)
        with pytest.raises(RuntimeError) as excinfo:
            resolve_credential("keyring:model-connection-leak")
        assert "sk-super-secret-value" not in str(excinfo.value)
        assert os.environ.get("QTR_NO_RETRY_KEY") != "sk-test-local-endpoint"


# ---------------------------------------------------------------------------
# invoke_single_connection 解析租约一致性（F06）
# ---------------------------------------------------------------------------
class TestInvokeCredentialLease:
    """F06：版本化凭据的 credential_ref 必须由 credential_version 派生，否则零出站拒绝。

    闭环「许可校验的版本 == 实际解析目标」：许可侧绑定实际执行版本（F06），
    出站侧校验 ref 与该版本同源（keyring:model-connection-{version}，
    与 ModelConfigurationService.prepare_credential_version 生成形态一致）。
    """

    async def test_lease_mismatch_refuses_outbound(self):
        """ref 与 version 不同源（数据损坏 / ref 被偷换）→ ValueError，零 keyring 读、零出站。"""
        client = LiteLLMClient(MagicMock())
        conn = _connection(credential_ref="keyring:item", credential_version="v1")
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            with pytest.raises(ValueError) as exc_info:
                await client.invoke_single_connection(_request(), conn, _request().deadline, _attempt_context())
        assert "Credential ref does not match versioned lease" in str(exc_info.value)
        assert "v1" in str(exc_info.value)
        mock_ac.assert_not_awaited()

    async def test_lease_match_resolves_and_dispatches(self):
        """ref 与 version 同源（keyring:model-connection-{version}）→ 正常解析出站。"""
        import utils.config_handler as cfg

        cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "model-connection-v1", "sk-lease-test")
        client = LiteLLMClient(MagicMock())
        conn = _connection(credential_ref="keyring:model-connection-v1", credential_version="v1")
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            response = await client.invoke_single_connection(_request(), conn, _request().deadline, _attempt_context())

        assert response.content == "ok"
        assert mock_ac.await_args.kwargs["api_key"] == "sk-lease-test"

    async def test_version_none_skips_lease_check(self):
        """version=None（ref 缺失 / env: / 旧手工项）无租约语义，不触发一致性校验。"""
        import utils.config_handler as cfg

        cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "legacy-item", "sk-legacy-test")
        client = LiteLLMClient(MagicMock())
        conn = _connection(credential_ref="keyring:legacy-item", credential_version=None)
        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await client.invoke_single_connection(_request(), conn, _request().deadline, _attempt_context())

        assert mock_ac.await_args.kwargs["api_key"] == "sk-legacy-test"


# ---------------------------------------------------------------------------
# F29：凭据解析（同步 keyring offload）后的出站前 deadline 重核
# ---------------------------------------------------------------------------
class TestF29DeadlineRecheckAfterCredential:
    """凭据解析耗时不受 deadline 预算约束——出站前重核剩余预算并重新裁剪 httpx 超时。

    回归证据：慢凭据（解析耗时超过剩余预算）不得触发新出站（零出站）；
    凭据解析真实消耗预算后，httpx 总超时须按解析后剩余重新裁剪（禁止沿用
    解析前的 stale 预算）。
    """

    def _mock_thread_pool(self, resolve_side_effect):
        """替身 ThreadPoolManager：run_async 直接执行慢凭据解析协程。"""
        mock_tpm = MagicMock()

        async def _run_async(_task_type, fn, *args, **kwargs):
            await resolve_side_effect()
            return fn(*args, **kwargs)

        mock_tpm.return_value.run_async = AsyncMock(side_effect=_run_async)
        return mock_tpm

    async def test_credential_resolution_exhausts_deadline_zero_outbound(self):
        """慢凭据耗尽剩余预算 → TimeoutError，零出站（零审计，与 dispatch 前检查一致）。"""
        client = LiteLLMClient(MagicMock())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.05
        request = _request(deadline=deadline)

        async def _slow_keyring():
            await asyncio.sleep(0.2)  # 解析耗时超过剩余预算（0.2 > 0.05）

        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch("services.ai_service.litellm_client.ThreadPoolManager", new=self._mock_thread_pool(_slow_keyring)),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            with pytest.raises(TimeoutError) as exc_info:
                await client.invoke_single_connection(request, _connection(), request.deadline, _attempt_context())
        assert type(exc_info.value) is TimeoutError
        mock_ac.assert_not_awaited()

    async def test_credential_resolution_reclamps_http_timeout(self):
        """凭据解析真实消耗预算后，httpx 总超时按解析后剩余重新裁剪。"""
        client = LiteLLMClient(MagicMock())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.6
        request = _request(deadline=deadline)

        async def _keyring_with_cost():
            await asyncio.sleep(0.2)  # 解析真实消耗 0.2s 预算

        with (
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
            patch(
                "services.ai_service.litellm_client.ThreadPoolManager", new=self._mock_thread_pool(_keyring_with_cost)
            ),
            patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_ac,
        ):
            mock_ac.return_value = _fake_response()
            await client.invoke_single_connection(request, _connection(), request.deadline, _attempt_context())

        timeout = mock_ac.await_args.kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        # 解析前 stale 预算 ≈ 0.6；解析消耗 0.2s 后重裁剪 → read ≤ 0.4
        assert timeout.read is not None and timeout.read <= 0.4
