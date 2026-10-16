"""S3 模型兼容性服务单测（MM-06：参数 clamp 与能力校验）。

覆盖：
- 本地 GGUF：max_length clamp 至 n_ctx - 8（防越界，模型统一.md §6.2）；
- 超时下限 clamp 至 1.0s；
- n_ctx 不足以承载推理时判不兼容（缺失不伪装为可用，R21 邻域）；
- 远程 litellm 连接原样透传，不引入本地专属参数。
"""

import pytest

from services.ai_service.model_compatibility import ModelCompatibilityService

pytestmark = pytest.mark.unit


def _local_conn(**overrides):
    conn = {
        "connection_id": "conn-local",
        "connection_revision": 1,
        "adapter_kind": "gguf_chat",
        "provider": "local",
        "model": "models/finbert2-base-f16.gguf",
        "api_base": None,
        "adapter_options": {"n_ctx": 512, "max_length": 600, "timeout": 0.2, "n_threads": 4},
        "context_window": None,
        "concurrency_limit": 1,
        "credential_ref": None,
        "credential_version": None,
    }
    conn.update(overrides)
    return conn


def _remote_conn(**overrides):
    conn = {
        "connection_id": "conn-remote",
        "connection_revision": 3,
        "adapter_kind": "litellm_chat",
        "provider": "zhipu",
        "model": "glm-4-air",
        "api_base": None,
        "adapter_options": {"temperature": 0.2},
        "context_window": 8192,
        "concurrency_limit": 4,
        "credential_ref": "keyring:item",
        "credential_version": "v1",
    }
    conn.update(overrides)
    return conn


class TestLocalClamp:
    def setup_method(self):
        self.svc = ModelCompatibilityService()

    def test_max_length_clamped_to_n_ctx_minus_8(self):
        decision = self.svc.resolve_execution_params(_local_conn())
        assert decision.compatible is True
        assert decision.resolved_options["max_length"] == 504  # 512 - 8

    def test_max_length_within_budget_unchanged(self):
        conn = _local_conn(adapter_options={"n_ctx": 512, "max_length": 128, "timeout": 2.0})
        decision = self.svc.resolve_execution_params(conn)
        assert decision.resolved_options["max_length"] == 128

    def test_timeout_clamped_to_minimum_1s(self):
        decision = self.svc.resolve_execution_params(_local_conn())
        assert decision.resolved_options["timeout"] == 1.0

    def test_timeout_above_minimum_unchanged(self):
        conn = _local_conn(adapter_options={"n_ctx": 512, "max_length": 128, "timeout": 3.5})
        decision = self.svc.resolve_execution_params(conn)
        assert decision.resolved_options["timeout"] == 3.5

    def test_n_ctx_too_small_marked_incompatible(self):
        conn = _local_conn(adapter_options={"n_ctx": 8, "max_length": 8, "timeout": 2.0})
        decision = self.svc.resolve_execution_params(conn)
        assert decision.compatible is False
        # n_ctx=8 == 保留值下限，必须给出精确归因（含实际值）而非泛化拒绝
        assert decision.reason is not None and "n_ctx" in decision.reason and "8" in decision.reason

    def test_missing_n_ctx_marked_incompatible(self):
        conn = _local_conn(adapter_options={"max_length": 128})
        decision = self.svc.resolve_execution_params(conn)
        assert decision.compatible is False


class TestRemotePassthrough:
    def setup_method(self):
        self.svc = ModelCompatibilityService()

    def test_remote_options_passthrough_unchanged(self):
        decision = self.svc.resolve_execution_params(_remote_conn())
        assert decision.compatible is True
        assert decision.resolved_options == {"temperature": 0.2}

    def test_resolved_options_are_copies_not_aliases(self):
        conn = _local_conn()
        decision = self.svc.resolve_execution_params(conn)
        decision.resolved_options["max_length"] = 999
        assert conn["adapter_options"]["max_length"] == 600
