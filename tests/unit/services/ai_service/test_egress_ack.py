"""D13-002：外发确认许可绑定本次 attempt / 连接 / 目标 / 有效期。

``utils/egress_ack.py`` 的 ``bind_egress_ack_permit`` + ``verify_egress_ack_permit``
把 SEC-01 从「进程级当前全局主 provider 是否已确认」改为「本次尝试许可是否有效」：

- 对象：许可 provider == 实际 ``ResolvedConnection.provider``，且按该 provider 现场复查
  用户确认（不放宽既有二次确认、撤销即时生效）；
- 目标：连接 id / 模型 / 规范化外发目标（scheme+host+port+base path）一致；
- 有效期：``issued_at <= now <= expires_at`` 且 scope_version 未轮转；
- 缺许可 / 跨 attempt / 跨会话 / 换目标一律 fail-closed（零出站）。

本文件只测纯函数层（不触达网络 / litellm）；统一链出口接线由
``test_r06_d23_unified_chain_egress.py`` 覆盖。
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from core.errors import AIPolicyNotAcknowledgedError
from core.model_management_types import (
    AttemptContext,
    ResolvedConnection,
    normalize_egress_target,
)
from utils.egress_ack import (
    EGRESS_ACK_PERMIT_TTL_S,
    bind_egress_ack_permit,
    verify_egress_ack_permit,
)
from utils.config_models import AI_EGRESS_SCOPE_VERSION

pytestmark = pytest.mark.unit

_NOW = 1_000.0
_DEADLINE = _NOW + 30.0


def _context(**overrides) -> AttemptContext:
    base = {
        "owner_session_id": "sess-1",
        "owner_epoch": 3,
        "invocation_id": "inv-1",
        "attempt_id": "att-1",
    }
    base.update(overrides)
    return AttemptContext(**base)


def _connection(**overrides) -> ResolvedConnection:
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


def _permit(context: AttemptContext | None = None, connection: ResolvedConnection | None = None, **kwargs):
    context = context or _context()
    connection = connection or _connection()
    params = {"attempt_context": context, "resolved_connection": connection, "now": _NOW, "deadline": _DEADLINE}
    params.update(kwargs)
    return bind_egress_ack_permit(**params)


def _verify(permit, *, context=None, connection=None, invocation_id="inv-1", now=_NOW, acknowledged=True):
    with patch("utils.egress_ack.ConfigHandler.is_ai_external_acknowledged", return_value=acknowledged):
        verify_egress_ack_permit(
            permit,
            attempt_context=context or _context(),
            resolved_connection=connection or _connection(),
            invocation_id=invocation_id,
            now=now,
        )


class TestBindEgressAckPermit:
    """许可签发：结构化绑定 + 有效期，缺上下文拒绝签发。"""

    def test_binds_identity_target_and_validity(self) -> None:
        permit = _permit()

        assert permit.attempt_id == "att-1"
        assert permit.invocation_id == "inv-1"
        assert permit.owner_session_id == "sess-1"
        assert permit.owner_epoch == 3
        assert permit.connection_id == "conn-1"
        assert permit.provider == "deepseek"
        assert permit.model == "deepseek-chat"
        assert permit.target_fingerprint == normalize_egress_target("https://api.deepseek.com")
        assert permit.scope_version == AI_EGRESS_SCOPE_VERSION
        assert permit.issued_at == _NOW
        assert permit.expires_at == _NOW + min(EGRESS_ACK_PERMIT_TTL_S, _DEADLINE - _NOW)

    def test_validity_clamped_by_deadline(self) -> None:
        permit = _permit(deadline=_NOW + 5.0)
        assert permit.expires_at == _NOW + 5.0

    @pytest.mark.parametrize("field_name", ["attempt_id", "invocation_id", "owner_session_id"])
    def test_missing_attempt_context_rejected(self, field_name: str) -> None:
        """缺 attempt / invocation / owner 上下文 → 拒绝签发（fail-closed）。"""
        context = _context(**{field_name: ""})
        with pytest.raises(ValueError, match=field_name):
            _permit(context=context)

    def test_missing_connection_provider_rejected(self) -> None:
        with pytest.raises(ValueError, match="provider"):
            _permit(connection=_connection(provider=""))

    def test_non_integer_owner_epoch_rejected(self) -> None:
        with pytest.raises(ValueError, match="owner_epoch"):
            _permit(context=_context(owner_epoch=object()))  # type: ignore[arg-type]


class TestVerifyEgressAckPermit:
    """许可校验：对象 / 目标 / 有效期 / 二次确认，任一不符 fail-closed。"""

    def test_valid_permit_allows_egress(self) -> None:
        _verify(_permit())

    def test_missing_permit_rejected(self) -> None:
        with pytest.raises(AIPolicyNotAcknowledgedError, match="missing or invalid permit"):
            _verify(None)

    def test_mock_permit_rejected(self) -> None:
        """非 ``EgressAckPermit`` 替身（如 MagicMock）不得被误当有效许可。"""
        with patch("utils.egress_ack.ConfigHandler.is_ai_external_acknowledged", return_value=True):
            with pytest.raises(AIPolicyNotAcknowledgedError, match="missing or invalid permit"):
                verify_egress_ack_permit(
                    MagicMock(),  # type: ignore[arg-type]
                    attempt_context=_context(),
                    resolved_connection=_connection(),
                    invocation_id="inv-1",
                    now=_NOW,
                )

    def test_cross_attempt_reuse_rejected(self) -> None:
        """A attempt 的许可不能用于 B attempt（跨 attempt 不串味）。"""
        permit = _permit()
        with pytest.raises(AIPolicyNotAcknowledgedError, match="attempt id mismatch"):
            _verify(permit, context=_context(attempt_id="att-2"))

    def test_cross_invocation_reuse_rejected(self) -> None:
        permit = _permit()
        with pytest.raises(AIPolicyNotAcknowledgedError, match="invocation id mismatch"):
            _verify(permit, context=_context(invocation_id="inv-2"), invocation_id="inv-2")
        with pytest.raises(AIPolicyNotAcknowledgedError, match="invocation id mismatch"):
            _verify(permit, invocation_id="inv-2")

    def test_cross_session_reuse_rejected(self) -> None:
        """跨会话（owner session / epoch 变更）复用 → 拒绝。"""
        permit = _permit()
        with pytest.raises(AIPolicyNotAcknowledgedError, match="owner session mismatch"):
            _verify(permit, context=_context(owner_session_id="sess-2"))
        with pytest.raises(AIPolicyNotAcknowledgedError, match="owner epoch mismatch"):
            _verify(permit, context=_context(owner_epoch=4))

    def test_connection_provider_model_target_mismatch_rejected(self) -> None:
        permit = _permit()
        for connection in (
            _connection(connection_id="conn-2"),
            _connection(provider="openai"),
            _connection(model="deepseek-reasoner"),
            _connection(api_base="https://api.other.com"),
        ):
            with pytest.raises(AIPolicyNotAcknowledgedError, match="mismatch"):
                _verify(permit, connection=connection)

    def test_expired_permit_rejected(self) -> None:
        permit = _permit(deadline=_NOW + 1.0)
        with pytest.raises(AIPolicyNotAcknowledgedError, match="permit expired"):
            _verify(permit, now=_NOW + 2.0)

    def test_future_issued_permit_rejected(self) -> None:
        permit = replace(_permit(), issued_at=_NOW + 10.0)
        with pytest.raises(AIPolicyNotAcknowledgedError, match="issued in the future"):
            _verify(permit)

    def test_scope_version_rotation_expires_permit(self) -> None:
        permit = replace(_permit(), scope_version=AI_EGRESS_SCOPE_VERSION + 1)
        with pytest.raises(AIPolicyNotAcknowledgedError, match="scope version expired"):
            _verify(permit)

    def test_non_finite_validity_rejected(self) -> None:
        """NaN / 反向时间窗不得让比较恒为 False 绕过有效期校验。"""
        for broken in (float("nan"), float("inf")):
            with pytest.raises(AIPolicyNotAcknowledgedError, match="invalid validity window"):
                _verify(replace(_permit(), expires_at=broken))
        with pytest.raises(AIPolicyNotAcknowledgedError, match="invalid validity window"):
            _verify(replace(_permit(), issued_at=_NOW + 5.0, expires_at=_NOW))

    def test_boolean_owner_epoch_in_permit_rejected(self) -> None:
        """``True == 1`` 不得被当作合法 owner epoch（布尔绕过）。"""
        with pytest.raises(AIPolicyNotAcknowledgedError, match="owner epoch mismatch"):
            _verify(replace(_permit(), owner_epoch=True))  # type: ignore[arg-type]

    def test_provider_not_acknowledged_rejected(self) -> None:
        """用户对实际 provider 的确认缺失 → 拒绝（既有二次确认不放宽）。"""
        with pytest.raises(AIPolicyNotAcknowledgedError, match="not acknowledged"):
            _verify(_permit(), acknowledged=False)

    def test_ack_lookup_uses_actual_provider_not_global_primary(self) -> None:
        """确认判定按实际 provider（不读全局主 provider，避免跨会话串味）。"""
        calls: list[str | None] = []

        def _ack(*, provider=None, **_kwargs):
            calls.append(provider)
            return provider == "deepseek"

        with (
            patch("utils.egress_ack.ConfigHandler.is_ai_external_acknowledged", side_effect=_ack),
            patch(
                "utils.egress_ack.ConfigHandler.get_llm_provider",
                side_effect=AssertionError("verify must not read process-global primary provider"),
            ),
        ):
            verify_egress_ack_permit(
                _permit(),
                attempt_context=_context(),
                resolved_connection=_connection(),
                invocation_id="inv-1",
                now=_NOW,
            )

        assert calls == ["deepseek"]
