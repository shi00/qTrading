"""ModelConfigurationService R04 资料许可四方法端到端集成测试（C-02-4b，§10.2 L1773/L1774）。

真实 embedded PG + 隔离配置文件：owner 状态机（建行/批准/撤销）→ DAO 持久事务 →
authorize_data_attempt 新鲜核权（重算摘要 + authorize_attempt + 绑定一致核 + claim
行锁单胜者）→ 回执 allow/deny/unknown 分列。

复用 :mod:`tests.integration.test_agent_dao_data_consent` 的种子与构造 helper
（同一事实链语义）；服务与 DAO 共享同一 engine。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from unittest import mock

import pytest

import utils.config_handler as cfg_mod
from core.agent_types import (
    DataAttemptOutcome,
    DataConsentBinding,
    DataConsentState,
)
from services.model_configuration_service import ModelConfigurationService
from tests.integration.test_agent_dao_data_consent import (
    USER_SCOPE,
    _authorization,
    _claim,
    _revoke_command,
    _save_command,
    _seed_operation,
    _seed_research_chain,
    _setup,
)

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


@pytest.fixture()
def isolated_owner_config(tmp_path):
    """独立配置文件与干净缓存（同 test_model_configuration_service.isolated_config 语义）。"""
    config_file = tmp_path / "user_settings.json"
    with (
        mock.patch.object(cfg_mod, "CONFIG_FILE", str(config_file)),
        mock.patch.object(cfg_mod.ConfigHandler, "_config_cache", None),
    ):
        cfg_mod.ConfigHandler.ensure_defaults()
        # 本地 gguf 连接（资料许可绑定引用的连接身份；本地免外发路径）
        raw = json.loads(config_file.read_text(encoding="utf-8"))
        mm = dict(raw["model_management"])
        mm["connections"] = [
            {
                "connection_id": "synthetic-local-A",
                "name": "本地测试模型",
                "adapter_kind": "gguf_chat",
                "provider": "local",
                "model": "local-model",
                "api_base": None,
                "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
            }
        ]
        raw["model_management"] = mm
        config_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        yield config_file


async def _approved_consent(
    dao, service: ModelConfigurationService, session_id: str, execution_id: str, round_id: str
) -> DataConsentBinding:
    """owner 建行（pending）→ 批准（approved）两步真实事实链。"""
    from tests.integration.test_agent_dao_data_consent import _binding

    await service.save_data_consent(
        _save_command(
            _binding(state="pending", execution_id=execution_id, round_id=round_id),
            session_id=session_id,
            execution_id=execution_id,
            round_id=round_id,
            expected_revision=0,
            client_command_id="consent-cmd-1",
        )
    )
    return await service.save_data_consent(
        _save_command(
            _binding(execution_id=execution_id, round_id=round_id, consent_revision=2),
            session_id=session_id,
            execution_id=execution_id,
            round_id=round_id,
            expected_revision=1,
            client_command_id="consent-cmd-2",
        )
    )


class TestOwnerDataConsentEndToEnd:
    async def test_approve_then_authorize_allows_and_claims(
        self, real_embedded_pg, isolated_owner_config, monkeypatch
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        service = ModelConfigurationService(agent_dao=dao)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        binding = await _approved_consent(dao, service, session_id, execution_id, round_id)

        # owner 读面：user_scope 校验 + 绑定返回
        read = await service.read_data_consent("dec-consent-1", user_scope=USER_SCOPE)
        assert read.state is DataConsentState.APPROVED
        assert read.consent_revision == 2

        await _seed_operation(dao)
        receipt = await service.authorize_data_attempt(_authorization(consent_revision=2))
        assert receipt.outcome is DataAttemptOutcome.ALLOW
        assert receipt.consent_revision == 2
        assert receipt.reason is None
        assert receipt.binding is not None
        # claim 已持久化（真实 PG）：operation.data_consent_claims 一条 allow
        import sqlalchemy as sa

        from data.persistence.models import AgentOperation

        async with dao.engine.connect() as conn:
            op = (
                await conn.execute(
                    sa.select(AgentOperation.data_consent_claims).where(
                        AgentOperation.user_scope == USER_SCOPE,
                        AgentOperation.operation_id == "op-1",
                    )
                )
            ).first()
        assert op is not None
        assert op.data_consent_claims is not None and len(op.data_consent_claims) == 1
        assert op.data_consent_claims[0]["outcome"] == "allow"
        assert binding is not None

    async def test_revoke_then_authorize_denies(self, real_embedded_pg, isolated_owner_config) -> None:
        dao = await _setup(real_embedded_pg.url)
        service = ModelConfigurationService(agent_dao=dao)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await _approved_consent(dao, service, session_id, execution_id, round_id)
        receipt_cmd = await service.revoke_data_consent(_revoke_command(2))
        assert receipt_cmd.current_revision == 3

        await _seed_operation(dao)
        receipt = await service.authorize_data_attempt(_authorization(consent_revision=2))
        assert receipt.outcome is DataAttemptOutcome.DENY
        assert receipt.reason == "state_not_approved"
        assert receipt.binding is not None
        assert receipt.binding.state is DataConsentState.REVOKED

    async def test_update_non_pending_owner_state_machine_denies(self, real_embedded_pg, isolated_owner_config) -> None:
        dao = await _setup(real_embedded_pg.url)
        service = ModelConfigurationService(agent_dao=dao)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await _approved_consent(dao, service, session_id, execution_id, round_id)
        # 已 approved：再走更新路径（pending→approved 状态机不允许）→ POLICY_DENIED
        from core.agent_types import AgentErrorCode, AgentServiceError
        from tests.integration.test_agent_dao_data_consent import _binding

        with pytest.raises(AgentServiceError) as exc:
            await service.save_data_consent(
                _save_command(
                    _binding(execution_id=execution_id, round_id=round_id, consent_revision=3),
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    expected_revision=2,
                    client_command_id="consent-cmd-3",
                )
            )
        assert exc.value.code == AgentErrorCode.POLICY_DENIED

    async def test_operation_missing_propagates(self, real_embedded_pg, isolated_owner_config) -> None:
        dao = await _setup(real_embedded_pg.url)
        service = ModelConfigurationService(agent_dao=dao)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await _approved_consent(dao, service, session_id, execution_id, round_id)
        # 未先备 operation → claim INPUT_INVALID 原样抛出（先备后派发契约）
        from core.agent_types import AgentErrorCode, AgentServiceError

        with pytest.raises(AgentServiceError) as exc:
            await service.authorize_data_attempt(_authorization(consent_revision=2))
        assert exc.value.code == AgentErrorCode.INPUT_INVALID
