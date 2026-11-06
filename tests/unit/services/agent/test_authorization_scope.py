"""M03 授权范围扩展（设计正本 §9.7 / §12.5 M03）。

先建立行为反例：旧公开 grant 不得扩大为私人/研究范围；agent_research 用途与
data_scope_digest 必须写入原安全 schema 并被 authorize_attempt 严格匹配；
撤销与派发双顺序；grant 已提交但应用关联失败只查原 operation，不二次建 grant。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import utils.config_handler as cfg_mod
from services.agent.service import AgentService
from services.model_configuration_service import ModelConfigurationService

pytestmark = pytest.mark.unit

CONN = "33333333-3333-4333-8333-333333333333"
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    config_file = tmp_path / "user_settings.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", str(config_file))
    monkeypatch.setattr(cfg_mod.ConfigHandler, "_config_cache", None)
    yield config_file


@pytest.fixture()
def service(isolated_config):
    cfg_mod.ConfigHandler.ensure_defaults()
    return ModelConfigurationService()


def _revision() -> int:
    return int(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]["config_revision"])


def _load_mm() -> dict:
    return dict(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"])


async def _seed_connection(service: ModelConfigurationService) -> None:
    mm = _load_mm()
    mm.pop("migration_state", None)
    mm.pop("migration_transition", None)
    connections = [c for c in mm["connections"] if c["connection_id"] != CONN]
    connections.append(
        {
            "connection_id": CONN,
            "name": "研究模型",
            "adapter_kind": "litellm_chat",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_base": "https://api.example.com",
            "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
        }
    )
    mm["connections"] = connections
    outcome = await service.save_draft(mm, _revision(), "op-m03-seed")
    assert outcome.status == "committed"


async def _grant(
    service: ModelConfigurationService,
    *,
    purpose: str = "agent_research",
    data_scope_digest: str | None = DIGEST,
    operation_id: str = "op-m03-grant",
):
    return await service.grant_egress(
        connection_id=CONN,
        target_fingerprint="fp-research",
        purpose=purpose,
        scope_version=1,
        expected_revision=_revision(),
        operation_id=operation_id,
        data_scope_digest=data_scope_digest,
    )


async def _agent(service: ModelConfigurationService) -> AgentService:
    return AgentService(domain="test_domain", dao=MagicMock(), model_config_service=service)


def _grants() -> list[dict]:
    return [
        dict(g)
        for g in cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]["security_state"]["grants"]
    ]


# ---------------------------------------------------------------------------
# schema / 写入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_research_purpose_and_scope_digest_persist_in_original_schema(service) -> None:
    await _seed_connection(service)
    outcome = await _grant(service)
    assert outcome.status == "committed"
    grants = _grants()
    assert len(grants) == 1
    assert grants[0]["purpose"] == "agent_research"
    assert grants[0]["data_scope_digest"] == DIGEST
    snapshot = await service.get_snapshot()
    assert snapshot.security_grants[0]["data_scope_digest"] == DIGEST


@pytest.mark.asyncio
async def test_unknown_purpose_is_rejected(service) -> None:
    await _seed_connection(service)
    with pytest.raises(ValueError):
        await _grant(service, purpose="private_agent_shortcut")


# ---------------------------------------------------------------------------
# 旧 grant 不扩范围 / 精确 digest 匹配
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_public_grant_does_not_authorize_private_scope(service) -> None:
    await _seed_connection(service)
    outcome = await _grant(service, data_scope_digest=None)
    assert outcome.status == "committed"
    with pytest.raises(PermissionError):
        await service.authorize_attempt(
            CONN,
            "fp-research",
            "agent_research",
            1,
            None,
            "attempt-private",
            data_scope_digest=DIGEST,
        )


@pytest.mark.asyncio
async def test_matching_scope_digest_authorizes_and_mismatch_is_rejected(service) -> None:
    await _seed_connection(service)
    await _grant(service, data_scope_digest=DIGEST)
    permit = await service.authorize_attempt(
        CONN, "fp-research", "agent_research", 1, None, "attempt-ok", data_scope_digest=DIGEST
    )
    assert permit.connection_id == CONN
    with pytest.raises(PermissionError):
        await service.authorize_attempt(
            CONN, "fp-research", "agent_research", 1, None, "attempt-other", data_scope_digest=OTHER_DIGEST
        )


# ---------------------------------------------------------------------------
# 撤销 / 派发双顺序
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_then_dispatch_rejects_and_dispatch_then_revoke_keeps_issued_permit(service) -> None:
    await _seed_connection(service)
    await _grant(service)
    permit = await service.authorize_attempt(
        CONN, "fp-research", "agent_research", 1, None, "attempt-before-revoke", data_scope_digest=DIGEST
    )
    grant_id = _grants()[0]["grant_id"]
    outcome = await service.revoke_egress(grant_id, _revision(), "op-m03-revoke")
    assert outcome.status == "committed"
    assert permit.security_revision >= 1
    with pytest.raises(PermissionError):
        await service.authorize_attempt(
            CONN, "fp-research", "agent_research", 1, None, "attempt-after-revoke", data_scope_digest=DIGEST
        )


@pytest.mark.asyncio
async def test_grant_unknown_is_resolved_by_original_operation_without_second_grant(service) -> None:
    await _seed_connection(service)
    outcome = await _grant(service, operation_id="op-m03-grant-once")
    assert outcome.status == "committed"
    resolved = await service.resolve_save_outcome("op-m03-grant-once")
    assert resolved.status == "committed"
    assert resolved.last_config_commit is not None
    assert len(_grants()) == 1


# ---------------------------------------------------------------------------
# AgentService 读 / 撤（复用原服务，不建第二账本）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_service_reads_grants_with_scope_digest(service) -> None:
    await _seed_connection(service)
    await _grant(service)
    agent = await _agent(service)
    result = await agent.read_authorizations()
    assert result["config_revision"] >= 1
    assert result["scope_version"] == 1
    assert result["grants"][0]["purpose"] == "agent_research"
    assert result["grants"][0]["data_scope_digest"] == DIGEST
    assert result["agent_scopes"] == []


@pytest.mark.asyncio
async def test_agent_service_read_failure_is_not_an_empty_authorization_list(service) -> None:
    agent = await _agent(service)
    service.get_snapshot = MagicMock(side_effect=RuntimeError("profile unreadable"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await agent.read_authorizations()


@pytest.mark.asyncio
async def test_agent_service_revokes_through_original_service(service) -> None:
    await _seed_connection(service)
    await _grant(service)
    grant_id = _grants()[0]["grant_id"]
    agent = await _agent(service)
    outcome = await agent.revoke_authorization(grant_id, _revision(), "op-m03-agent-revoke")
    assert outcome.status == "committed"
    raw = json.loads(json.dumps(_grants()))
    assert raw[0]["revoked_at"] is not None
