"""AgentDao C-02-4a 资料许可事务集成测试（§9.8/§10.2 R04/§5.4 单胜者）。

真实 embedded PG 上的单事务语义验证：

- ``save_data_consent_decision``：decision(kind='data_consent') 建行/CAS 更新 +
  save 命令行；同命令同载荷查回原结果绑定，异输入 INPUT_CONFLICT；
- ``revoke_data_consent_decision``：state→revoked CAS（保原批准主体/期限）+
  revoke 命令行（自足命令三态同事务落定）；已终态 POLICY_DENIED；
- ``read_data_consent_decision``：存在/缺失两路；
- ``claim_data_consent``：decision 行锁下单胜者复核（撤销先胜 POLICY_DENIED）+
  operation.data_consent_claims 追加；同 attempt 重放查回。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    BindActualInputRequest,
    CommandRef,
    ConnectionAuthorizationRef,
    DataAttemptAuthorization,
    DataAttemptOutcome,
    DataConsentBinding,
    DataConsentClaim,
    DataConsentCommand,
    DataConsentRevokeCommand,
    DataConsentState,
    EventKind,
    EventReadQuery,
    Mode,
    Purpose,
    decode_data_consent_binding,
    decode_data_consent_source_item,
    encode_data_consent_binding,
    encode_data_consent_claim,
    json_doc,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentCommand,
    AgentDecision,
    AgentExecution,
    AgentOperation,
    AgentSession,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "synthetic-user"
LOCAL_FP = "local-model-v1:cbd93ad05f977479f840e79852cd2bfb9b123c327ac6470ccd4cc5bd9f3d18ca"
SRC_DIGEST = sha256_hex("abc")
SELECTED_RANGE_DIGEST = "548d12d686601e50452f7cd140e44b26138e8c0f41dc52a68a32081cf8e34f31"
SCOPE_DIGEST = "29aa96ad9f423ef236d5e2b8b06f566cbc69ac5385ef10d6dcd6e36dc44f83fa"


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> AgentDao:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


async def _seed_research_chain(dao: AgentDao, user_scope: str = USER_SCOPE) -> tuple[str, str, str]:
    """受理 + 首派发绑定 → (session_id, execution_id, round_id) 真实事实链。"""

    session_id = "s-consent"
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title="consent test session",
            )
        )
    receipt = await dao.accept_request(
        AcceptRequest(
            user_scope=user_scope,
            session_id=session_id,
            source_module="ui",
            business_request_id="req-consent",
            client_command_id="client-consent",
            expected_revision=0,
            node_id="root",
            workflow_id="wf-consent-1",
            mode=Mode.EXECUTE,
            purpose=Purpose.END_OF_DAY_RESEARCH,
            text="研究这三家公司",
            manifest_payload=json_doc(
                {
                    "schema_version": 1,
                    "source_module": "ui",
                    "business_request_id": "req-consent",
                    "mode": "execute",
                    "purpose": "end_of_day_research",
                    "text": "研究这三家公司",
                }
            ),
            source_display_name="用户消息",
        )
    )
    bind_receipt = await dao.bind_actual_input(
        BindActualInputRequest(
            user_scope=user_scope,
            session_id=session_id,
            execution_id=receipt.execution_id,
            node_id="root",
            workflow_id=receipt.workflow_id,
            workflow_code_version="wcv-1",
            run_epoch=1,
            expected_input_revision=1,
            frozen_manifest=json_doc({"schema_version": 1, "kind": "frozen_manifest"}),
            actual_input=json_doc({"schema_version": 1, "kind": "actual_input"}),
        )
    )
    return session_id, receipt.execution_id, bind_receipt.round_id


def _source_item_wire() -> dict[str, Any]:
    return {
        "locator": {
            "locator_kind": "source_range",
            "origin_ref": {
                "source_digest": SRC_DIGEST,
                "source_id": "资料-A",
                "source_kind": "attachment",
                "source_version": "v1",
            },
            "selection": {"kind": "all"},
        },
        "provenance_refs": [],
        "representation_digest": SRC_DIGEST,
        "representation_version": "utf8-v1",
        "selected_range": {
            "accepted_limited_version": None,
            "limitations": [],
            "read_ranges": [{"kind": "all"}],
            "selection": {"kind": "all"},
            "stats": {"chars": 3, "pages": None, "rows": None, "sheets": None, "source_bytes": 3},
            "unread_ranges": [],
        },
        "selected_range_digest": SELECTED_RANGE_DIGEST,
        "source_digest": SRC_DIGEST,
        "source_id": "资料-A",
        "source_kind": "attachment",
        "source_version": "v1",
    }


def _binding(
    *,
    state: str = "approved",
    consent_revision: int = 1,
    execution_id: str = "exec",
    round_id: str = "round",
    expires_at: str | None = "2099-01-01T00:00:00.000000Z",
    valid_from: str = "2026-01-01T00:00:00.000000Z",
) -> DataConsentBinding:
    """合法 v1 绑定（本地免外发路径，AR-09 语义；state 矩阵由 core decode 核）。"""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "consent_id": "dec-consent-1",
        "consent_revision": consent_revision,
        "state": state,
        "approver_ref": None if state == "pending" else "user:local-owner",
        "user_scope": USER_SCOPE,
        "execution_id": execution_id,
        "round_id": round_id,
        "valid_from": None if state == "pending" else valid_from,
        "expires_at": expires_at,
        "requires_egress_grant": False,
        "connection_authorization_ref": {
            "connection_id": "synthetic-local-A",
            "target_fingerprint": LOCAL_FP,
            "purpose": "agent_research",
            "scope_version": 1,
            "verified_connection_grant_id": None,
        },
        "connection_revision": 2,
        "security_revision": 0,
        "credential_version": None,
        "target_fingerprint": LOCAL_FP,
        "purpose": "agent_research",
        "scope_version": 1,
        "source_selections": [_source_item_wire()],
        "source_scope_digest": SCOPE_DIGEST,
    }
    return decode_data_consent_binding(payload, "$")


def _save_command(
    binding: DataConsentBinding,
    *,
    session_id: str,
    execution_id: str,
    round_id: str,
    expected_revision: int,
    client_command_id: str = "consent-cmd-1",
) -> DataConsentCommand:
    return DataConsentCommand(
        command=CommandRef(
            user_scope=USER_SCOPE,
            source_module="agent_workflows",
            business_request_id="req-consent",
            command_id=client_command_id,
            expected_revision=expected_revision,
        ),
        consent_id="dec-consent-1",
        session_id=session_id,
        execution_id=execution_id,
        round_id=round_id,
        node_id="root",
        binding=binding,
    )


def _revoke_command(expected_revision: int, client_command_id: str = "revoke-cmd-1") -> DataConsentRevokeCommand:
    return DataConsentRevokeCommand(
        command=CommandRef(
            user_scope=USER_SCOPE,
            source_module="agent_decisions",
            business_request_id="req-consent",
            command_id=client_command_id,
            expected_revision=expected_revision,
        ),
        consent_id="dec-consent-1",
    )


def _authorization(*, consent_revision: int = 1, attempt_id: str = "attempt-1") -> DataAttemptAuthorization:
    item = decode_data_consent_source_item(_source_item_wire(), "$")
    return DataAttemptAuthorization(
        user_scope=USER_SCOPE,
        operation_id="op-1",
        attempt_id=attempt_id,
        consent_id="dec-consent-1",
        expected_consent_revision=consent_revision,
        connection_id="synthetic-local-A",
        connection_revision=2,
        security_revision=0,
        credential_version=None,
        purpose="agent_research",
        target_fingerprint=LOCAL_FP,
        scope_version=1,
        source_selections=(item,),
        source_scope_digest=SCOPE_DIGEST,
        owner_epoch=1,
        run_epoch=1,
    )


def _claim(consent_revision: int = 1, attempt_id: str = "attempt-1") -> DataConsentClaim:
    return DataConsentClaim(
        schema_version=1,
        consent_id="dec-consent-1",
        consent_revision=consent_revision,
        connection_authorization_ref=ConnectionAuthorizationRef(
            connection_id="synthetic-local-A",
            target_fingerprint=LOCAL_FP,
            purpose="agent_research",
            scope_version=1,
            verified_connection_grant_id=None,
        ),
        security_revision=0,
        credential_version=None,
        scope_version=1,
        source_scope_digest=SCOPE_DIGEST,
        purpose="agent_research",
        target_fingerprint=LOCAL_FP,
        attempt_id=attempt_id,
        operation_id="op-1",
        owner_epoch=1,
        run_epoch=1,
        authorized_at="2026-06-01T12:00:00.000000Z",
        outcome=DataAttemptOutcome.ALLOW,
    )


async def _seed_operation(
    dao: AgentDao,
    user_scope: str = USER_SCOPE,
    operation_id: str = "op-1",
    *,
    execution_id: str | None = None,
    round_id: str | None = None,
    owner_epoch: int = 1,
    run_epoch: int = 1,
) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentOperation).values(
                user_scope=user_scope,
                operation_id=operation_id,
                execution_id=execution_id,
                round_id=round_id,
                owner_epoch=owner_epoch,
                run_epoch=run_epoch,
                phase="prepared",
                effect_state="not_dispatched",
                io_state="not_started",
            )
        )


def _code_of(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> AgentErrorCode:
    assert isinstance(excinfo.value, AgentServiceError)
    return excinfo.value.code


class TestSaveDataConsentDecision:
    async def test_create_writes_decision_and_command(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        binding = _binding(execution_id=execution_id, round_id=round_id)
        result = await dao.save_data_consent_decision(
            _save_command(
                binding, session_id=session_id, execution_id=execution_id, round_id=round_id, expected_revision=0
            )
        )
        assert result is binding
        async with dao.engine.begin() as conn:
            decision = (
                await conn.execute(
                    sa.select(AgentDecision).where(
                        AgentDecision.user_scope == USER_SCOPE, AgentDecision.decision_id == "dec-consent-1"
                    )
                )
            ).first()
            assert decision is not None
            assert decision.decision_kind == "data_consent"
            assert decision.revision == 1
            assert decision.source_scope_digest == SCOPE_DIGEST
            assert json_doc(decision.data_consent_payload) == json_doc(encode_data_consent_binding(binding))
            command_row = (
                await conn.execute(
                    sa.select(AgentCommand).where(
                        AgentCommand.user_scope == USER_SCOPE, AgentCommand.command_kind == "data_consent_save"
                    )
                )
            ).first()
            assert command_row is not None
            assert command_row.target_id == "dec-consent-1"
            assert command_row.delivery_state == "not_delivered"
            assert command_row.consumption_state == "not_consumed"
            assert json_doc(command_row.receipt_payload) == json_doc(encode_data_consent_binding(binding))

    async def test_save_appends_command_changed_event_once(self, real_embedded_pg) -> None:
        """许可保存同事务追加 command_changed；同命令键重放不重复（R2-I21）。"""

        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        cmd = _save_command(
            _binding(execution_id=execution_id, round_id=round_id),
            session_id=session_id,
            execution_id=execution_id,
            round_id=round_id,
            expected_revision=0,
        )
        await dao.save_data_consent_decision(cmd)
        await dao.save_data_consent_decision(cmd)

        page = await dao.read_events(EventReadQuery(user_scope=USER_SCOPE, session_id=session_id, after_seq=0))
        events = [event for event in page.events if event.payload_kind == "data_consent_saved"]
        assert len(events) == 1
        assert events[0].event_kind is EventKind.COMMAND_CHANGED
        assert events[0].subject_id == "dec-consent-1"
        assert events[0].subject_revision == 1

    async def test_replay_same_payload_returns_original(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        cmd = _save_command(
            _binding(execution_id=execution_id, round_id=round_id),
            session_id=session_id,
            execution_id=execution_id,
            round_id=round_id,
            expected_revision=0,
        )
        first = await dao.save_data_consent_decision(cmd)
        second = await dao.save_data_consent_decision(cmd)
        assert json_doc(encode_data_consent_binding(second)) == json_doc(encode_data_consent_binding(first))
        async with dao.engine.begin() as conn:
            count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentCommand)
                    .where(AgentCommand.user_scope == USER_SCOPE, AgentCommand.command_kind == "data_consent_save")
                )
            ).scalar_one()
            assert count == 1

    async def test_same_key_different_input_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
            )
        )
        # 同命令键不同绑定（追加到期期限）→ 完整版本化输入不同 → 异输入冲突
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_data_consent_decision(
                _save_command(
                    _binding(
                        execution_id=execution_id,
                        round_id=round_id,
                        expires_at="2027-01-01T00:00:00.000000Z",
                    ),
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    expected_revision=0,
                )
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT

    async def test_create_when_decision_exists_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
            )
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_data_consent_decision(
                _save_command(
                    _binding(execution_id=execution_id, round_id=round_id),
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    expected_revision=0,
                    client_command_id="consent-cmd-2",
                )
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT

    async def test_update_revision_cas_appends(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                _binding(state="pending", execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
                client_command_id="consent-cmd-1",
            )
        )
        updated = _binding(execution_id=execution_id, round_id=round_id, consent_revision=2)
        result = await dao.save_data_consent_decision(
            _save_command(
                updated,
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=1,
                client_command_id="consent-cmd-2",
            )
        )
        assert result.state is DataConsentState.APPROVED
        async with dao.engine.begin() as conn:
            decision = (
                await conn.execute(
                    sa.select(AgentDecision).where(
                        AgentDecision.user_scope == USER_SCOPE, AgentDecision.decision_id == "dec-consent-1"
                    )
                )
            ).first()
            assert decision is not None
            assert decision.revision == 2
            payload = decode_data_consent_binding(decision.data_consent_payload, "$")
            assert payload.state is DataConsentState.APPROVED
            assert payload.consent_revision == 2

    async def test_update_stale_revision_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
            )
        )
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id, consent_revision=2),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=1,
                client_command_id="consent-cmd-2",
            )
        )
        # 决定行已到 revision 2：以旧基准 expected=1 的 CAS（consent_revision=2 合法）
        # 落到 DB 后单胜者失败 → REVISION_CONFLICT
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_data_consent_decision(
                _save_command(
                    _binding(execution_id=execution_id, round_id=round_id, consent_revision=2),
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    expected_revision=1,
                    client_command_id="consent-cmd-3",
                )
            )
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT

    async def test_update_missing_decision_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_data_consent_decision(
                _save_command(
                    _binding(execution_id=execution_id, round_id=round_id, consent_revision=2),
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    expected_revision=1,
                )
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestRevokeDataConsentDecision:
    async def _approved_consent(self, dao: AgentDao, session_id: str, execution_id: str, round_id: str) -> None:
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
                client_command_id="consent-cmd-1",
            )
        )

    async def test_revoke_from_approved(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        receipt = await dao.revoke_data_consent_decision(_revoke_command(1))
        assert receipt.current_revision == 2
        assert receipt.commit_state.value == "committed"
        assert receipt.delivery_state.value == "delivered"
        assert receipt.consumption_state.value == "consumed"
        async with dao.engine.begin() as conn:
            decision = (
                await conn.execute(
                    sa.select(AgentDecision).where(
                        AgentDecision.user_scope == USER_SCOPE, AgentDecision.decision_id == "dec-consent-1"
                    )
                )
            ).first()
            assert decision is not None
            assert decision.revision == 2
            payload = decode_data_consent_binding(decision.data_consent_payload, "$")
            # 撤销保原批准主体/生效时刻（§9.8 状态矩阵）
            assert payload.state is DataConsentState.REVOKED
            assert payload.approver_ref == "user:local-owner"
            assert payload.valid_from == "2026-01-01T00:00:00.000000Z"
            assert payload.consent_revision == 2
            cmd = (
                await conn.execute(
                    sa.select(AgentCommand).where(
                        AgentCommand.user_scope == USER_SCOPE, AgentCommand.command_kind == "data_consent_revoke"
                    )
                )
            ).first()
            assert cmd is not None
            assert cmd.receipt_payload is not None

    async def test_replay_returns_original_receipt(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        cmd = _revoke_command(1)
        first = await dao.revoke_data_consent_decision(cmd)
        second = await dao.revoke_data_consent_decision(cmd)
        assert second.current_revision == first.current_revision
        assert second.commit_state is first.commit_state
        async with dao.engine.begin() as conn:
            count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentCommand)
                    .where(AgentCommand.user_scope == USER_SCOPE, AgentCommand.command_kind == "data_consent_revoke")
                )
            ).scalar_one()
            assert count == 1

    async def test_same_key_different_input_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await dao.revoke_data_consent_decision(_revoke_command(1))
        with pytest.raises(AgentServiceError) as exc:
            await dao.revoke_data_consent_decision(_revoke_command(2))
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT

    async def test_already_revoked_policy_denied(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await dao.revoke_data_consent_decision(_revoke_command(1, client_command_id="revoke-cmd-1"))
        with pytest.raises(AgentServiceError) as exc:
            await dao.revoke_data_consent_decision(_revoke_command(2, client_command_id="revoke-cmd-2"))
        assert _code_of(exc) == AgentErrorCode.POLICY_DENIED

    async def test_stale_revision_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        with pytest.raises(AgentServiceError) as exc:
            await dao.revoke_data_consent_decision(_revoke_command(9, client_command_id="revoke-cmd-3"))
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT

    async def test_missing_decision_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_research_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.revoke_data_consent_decision(_revoke_command(1))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestReadDataConsentDecision:
    async def test_read_existing(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        binding = _binding(execution_id=execution_id, round_id=round_id)
        await dao.save_data_consent_decision(
            _save_command(
                binding, session_id=session_id, execution_id=execution_id, round_id=round_id, expected_revision=0
            )
        )
        read = await dao.read_data_consent_decision(USER_SCOPE, "dec-consent-1")
        assert read is not None
        assert read.consent_id == "dec-consent-1"
        assert read.state is DataConsentState.APPROVED
        assert read.consent_revision == 1
        assert json_doc(encode_data_consent_binding(read)) == json_doc(encode_data_consent_binding(binding))

    async def test_read_missing_returns_none(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_research_chain(dao)
        assert await dao.read_data_consent_decision(USER_SCOPE, "no-such-consent") is None

    async def test_read_wrong_scope_returns_none(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                binding=_binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
            )
        )
        assert await dao.read_data_consent_decision("other-scope", "dec-consent-1") is None

    async def test_read_save_replay_returns_original_binding(self, real_embedded_pg) -> None:
        """M15：服务层迁移预检前按原命令身份查回原回执（同键同输入）。"""
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        binding = _binding(execution_id=execution_id, round_id=round_id)
        cmd = _save_command(
            binding, session_id=session_id, execution_id=execution_id, round_id=round_id, expected_revision=0
        )
        await dao.save_data_consent_decision(cmd)
        replayed = await dao.read_data_consent_save_replay(cmd)
        assert isinstance(replayed, DataConsentBinding)
        assert json_doc(encode_data_consent_binding(replayed)) == json_doc(encode_data_consent_binding(binding))

    async def test_read_save_replay_missing_returns_none(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        cmd = _save_command(
            _binding(execution_id=execution_id, round_id=round_id),
            session_id=session_id,
            execution_id=execution_id,
            round_id=round_id,
            expected_revision=0,
        )
        assert await dao.read_data_consent_save_replay(cmd) is None

    async def test_read_save_replay_different_input_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        binding = _binding(execution_id=execution_id, round_id=round_id)
        cmd = _save_command(
            binding, session_id=session_id, execution_id=execution_id, round_id=round_id, expected_revision=0
        )
        await dao.save_data_consent_decision(cmd)
        other = _save_command(
            _binding(execution_id=execution_id, round_id=round_id, expires_at="2027-01-01T00:00:00.000000Z"),
            session_id=session_id,
            execution_id=execution_id,
            round_id=round_id,
            expected_revision=0,
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.read_data_consent_save_replay(other)
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT


class TestClaimDataConsent:
    async def _approved_consent(self, dao: AgentDao, session_id: str, execution_id: str, round_id: str) -> None:
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
                client_command_id="consent-cmd-1",
            )
        )

    async def test_claim_appends_to_operation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await _seed_operation(dao, execution_id=execution_id, round_id=round_id)
        fact = await dao.claim_data_consent(_authorization(), _claim())
        assert fact.replayed is False
        assert fact.claim.outcome is DataAttemptOutcome.ALLOW
        async with dao.engine.begin() as conn:
            op = (
                await conn.execute(
                    sa.select(AgentOperation).where(
                        AgentOperation.user_scope == USER_SCOPE, AgentOperation.operation_id == "op-1"
                    )
                )
            ).first()
            assert op is not None
            assert op.data_consent_claims is not None
            assert len(op.data_consent_claims) == 1
            assert json_doc(op.data_consent_claims[0]) == json_doc(encode_data_consent_claim(_claim()))

    async def test_claim_replay_same_attempt(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await _seed_operation(dao, execution_id=execution_id, round_id=round_id)
        first = await dao.claim_data_consent(_authorization(), _claim())
        second = await dao.claim_data_consent(_authorization(), _claim())
        assert first.replayed is False
        assert second.replayed is True
        async with dao.engine.begin() as conn:
            op = (
                await conn.execute(
                    sa.select(AgentOperation.data_consent_claims).where(
                        AgentOperation.user_scope == USER_SCOPE, AgentOperation.operation_id == "op-1"
                    )
                )
            ).first()
            assert op is not None
            assert len(op.data_consent_claims) == 1

    async def test_claim_same_attempt_different_input_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await _seed_operation(dao, execution_id=execution_id, round_id=round_id)
        await dao.claim_data_consent(_authorization(), _claim())
        with pytest.raises(AgentServiceError) as exc:
            await dao.claim_data_consent(
                _authorization(),
                replace(_claim(), authorized_at="2026-06-02T12:00:00.000000Z"),
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT

    async def test_claim_after_revoke_single_winner_denied(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await _seed_operation(dao, execution_id=execution_id, round_id=round_id)
        # 撤销先胜：claim 复核在 decision 行锁下，revoked 状态拒绝新 claim
        await dao.revoke_data_consent_decision(_revoke_command(1))
        with pytest.raises(AgentServiceError) as exc:
            await dao.claim_data_consent(_authorization(), _claim())
        assert _code_of(exc) == AgentErrorCode.POLICY_DENIED
        assert "state_not_approved" in str(exc.value)

    async def test_claim_stale_revision_denied(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        await _seed_operation(dao, execution_id=execution_id, round_id=round_id)
        # 绑定推进到 revision 2 后，旧版核权（expected=1）拒绝
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id, consent_revision=2),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=1,
                client_command_id="consent-cmd-2",
            )
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.claim_data_consent(_authorization(consent_revision=1), _claim(consent_revision=1))
        assert _code_of(exc) == AgentErrorCode.POLICY_DENIED

    async def test_claim_missing_operation_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await self._approved_consent(dao, session_id, execution_id, round_id)
        with pytest.raises(AgentServiceError) as exc:
            await dao.claim_data_consent(_authorization(), _claim())
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_claim_expired_binding_denied(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                _binding(
                    execution_id=execution_id,
                    round_id=round_id,
                    valid_from="2025-01-01T00:00:00.000000Z",
                    expires_at="2026-01-01T00:00:00.000000Z",
                ),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
            )
        )
        await _seed_operation(dao, execution_id=execution_id, round_id=round_id)
        with pytest.raises(AgentServiceError) as exc:
            await dao.claim_data_consent(_authorization(), _claim())
        assert _code_of(exc) == AgentErrorCode.POLICY_DENIED


class TestEnsureModelOperation:
    """I43：模型调用 operation 先备（幂等 + 冲突零新事实 + 撤销先胜）。"""

    async def test_ensure_is_idempotent_and_uses_db_epochs(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        _, execution_id, round_id = await _seed_research_chain(dao)
        first = await dao.ensure_model_operation(
            user_scope=USER_SCOPE,
            operation_id="op-model-1",
            execution_id=execution_id,
            round_id=round_id,
            node_id="root",
        )
        second = await dao.ensure_model_operation(
            user_scope=USER_SCOPE,
            operation_id="op-model-1",
            execution_id=execution_id,
            round_id=round_id,
            node_id="root",
        )
        assert first is True
        assert second is False
        async with dao.engine.begin() as conn:
            rows = (
                await conn.execute(
                    sa.select(AgentOperation).where(
                        AgentOperation.user_scope == USER_SCOPE,
                        AgentOperation.operation_id == "op-model-1",
                    )
                )
            ).all()
        assert len(rows) == 1
        op = rows[0]
        assert op.execution_id == execution_id
        assert op.round_id == round_id
        assert op.step_id == "root"
        assert op.owner_epoch == 1
        assert op.run_epoch == 1
        assert op.phase == "prepared"
        assert op.effect_state == "not_dispatched"
        assert op.io_state == "not_started"
        assert op.revision == 1

    async def test_conflicting_scope_raises_and_writes_nothing(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        _, execution_id, round_id = await _seed_research_chain(dao)
        await dao.ensure_model_operation(
            user_scope=USER_SCOPE,
            operation_id="op-model-1",
            execution_id=execution_id,
            round_id=round_id,
            node_id="root",
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.ensure_model_operation(
                user_scope=USER_SCOPE,
                operation_id="op-model-1",
                execution_id=execution_id,
                round_id=round_id,
                node_id="node-other",
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT
        async with dao.engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(AgentOperation.step_id, AgentOperation.revision).where(
                        AgentOperation.user_scope == USER_SCOPE,
                        AgentOperation.operation_id == "op-model-1",
                    )
                )
            ).first()
        assert row is not None
        assert row.step_id == "root"
        assert row.revision == 1

    async def test_revoke_first_wins_over_ensured_operation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_research_chain(dao)
        await dao.save_data_consent_decision(
            _save_command(
                _binding(execution_id=execution_id, round_id=round_id),
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                expected_revision=0,
            )
        )
        await dao.ensure_model_operation(
            user_scope=USER_SCOPE,
            operation_id="op-1",
            execution_id=execution_id,
            round_id=round_id,
            node_id="root",
        )
        await dao.revoke_data_consent_decision(_revoke_command(1))
        with pytest.raises(AgentServiceError) as exc:
            await dao.claim_data_consent(_authorization(), _claim())
        assert _code_of(exc) == AgentErrorCode.POLICY_DENIED
        assert "state_not_approved" in str(exc.value)


class TestAcceptIntentConcurrency:
    """检视 M-1 回归锚：跨 session 并发受理同一 intent 的唯一索引竞态必须
    归 INPUT_CONFLICT（§9.13 数据库失败明确分类；INV-01 一意图一受理身份），
    不允许裸 IntegrityError 逃逸。"""

    async def test_concurrent_same_intent_cross_session_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        async with dao.engine.begin() as conn:
            await conn.execute(
                insert(AgentSession).values(user_scope=USER_SCOPE, session_id="s-race-1", title="race 1")
            )
            await conn.execute(
                insert(AgentSession).values(user_scope=USER_SCOPE, session_id="s-race-2", title="race 2")
            )

        async def attempt(session_id: str) -> object:
            try:
                return await dao.accept_request(
                    AcceptRequest(
                        user_scope=USER_SCOPE,
                        session_id=session_id,
                        source_module="ui",
                        business_request_id="req-race",
                        client_command_id=f"client-{session_id}",
                        expected_revision=0,
                        node_id="root",
                        workflow_id=f"wf-{session_id}",
                        mode=Mode.EXECUTE,
                        purpose=Purpose.END_OF_DAY_RESEARCH,
                        text="并发受理同意图",
                        manifest_payload=json_doc(
                            {
                                "schema_version": 1,
                                "source_module": "ui",
                                "business_request_id": "req-race",
                                "mode": "execute",
                                "purpose": "end_of_day_research",
                                "text": "并发受理同意图",
                            }
                        ),
                        source_display_name="用户消息",
                    )
                )
            except AgentServiceError as exc:
                return exc

        results = await asyncio.gather(attempt("s-race-1"), attempt("s-race-2"))
        outcomes = [r.code if isinstance(r, AgentServiceError) else "receipt" for r in results]
        # 单胜者：一成功（或重放回执）+ 一 INPUT_CONFLICT；intent 恰好一条 execution。
        assert "receipt" in outcomes
        assert AgentErrorCode.INPUT_CONFLICT in outcomes
        async with dao.engine.connect() as conn:
            count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentExecution)
                    .where(
                        AgentExecution.user_scope == USER_SCOPE,
                        AgentExecution.source_module == "ui",
                        AgentExecution.business_request_id == "req-race",
                    )
                )
            ).scalar_one()
        assert count == 1
