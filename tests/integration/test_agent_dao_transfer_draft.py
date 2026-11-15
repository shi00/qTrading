"""AgentDao.transfer_draft / read_draft_transfer 集成测试（F-03-3）。

真实 embedded PG 上验证 §4.2 DraftTransfer 协议（R03）：

- 会话稿移入直达 completed：目标安装、源清空并留不可发送恢复记录、
  ``transfer_source_*`` claim 与唯一可发送归属落库；
- 重放查回原回执（不二次移动）、同键异输入 INPUT_CONFLICT；
- 双方 CAS 冲突零新事实；目标非空/归档拒绝；调用方 payload 必须与源稿一致；
- 同源版并发/跨目标争抢单胜者；业务准备稿仅 claim 仲裁；
- 目标草稿来源边写入、源边清空；
- 读回执：原样、非转移命令/未知命令 INPUT_INVALID、坏回执 OPERATION_UNKNOWN；
- 新草稿保存清空源转移 fence。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from alembic import command
from sqlalchemy import insert, select, text, update

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryState,
    DraftPayload,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferPhase,
    DraftTransferReceipt,
    DraftTransferSourceKind,
    Mode,
    SendableOwner,
    SourceKind,
    SourceVersionRef,
    derive_draft_transfer_id,
    draft_transfer_payload_digest,
    encode_draft_payload,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentCommand, AgentSession, AgentSourceLink
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u1"
_DIGEST = "a" * 64


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


async def _seed_session(
    dao: AgentDao, *, user_scope: str = _USER, session_id: str = "s1", archived: bool = False
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置会话
        await conn.execute(
            insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title=f"session {session_id}",
                archived=archived,
            )
        )


async def _seed_asset(dao: AgentDao, asset_id: str = "src-1", version: str = "1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试夹具
        await conn.execute(
            insert(AgentAssetVersion).values(
                user_scope=_USER,
                asset_id=asset_id,
                asset_version=version,
                asset_kind="source",
                display_name=f"{asset_id}@{version}",
                digest=_DIGEST,
                read_state="ready",
                verification_state="unverified",
            )
        )


def _payload(**overrides: Any) -> DraftPayload:
    values: dict[str, Any] = {
        "schema_version": 1,
        "text": "研究三家公司",
        "mode": Mode.DISCUSS,
        "model_selection": json_doc({"scenario_id": "agent_research"}),
        "source_refs": (),
        "skill_versions": (),
        "mcp_selection": (),
        "purpose": None,
        "cutoff": None,
        "send_target": None,
        "object_context": json_doc({"source_module": "ui"}),
        "role_prompt_selection": None,
        "customized_fields": (),
        "defaults_baseline_digest": None,
    }
    values.update(overrides)
    return DraftPayload(**values)


async def _save_source_draft(dao: AgentDao, payload: DraftPayload, *, command_id: str = "cmd-save-1") -> int:
    receipt = await dao.save_draft(
        DraftSaveCommand(
            command=CommandRef(
                user_scope=_USER,
                source_module="ui",
                business_request_id="req-save",
                command_id=command_id,
                expected_revision=1,
            ),
            session_id="s1",
            expected_draft_revision=1,
            payload=payload,
        )
    )
    return receipt.draft_revision


async def _save_target_draft(dao: AgentDao, payload: DraftPayload, *, command_id: str = "cmd-target-1") -> int:
    receipt = await dao.save_draft(
        DraftSaveCommand(
            command=CommandRef(
                user_scope=_USER,
                source_module="ui",
                business_request_id="req-save",
                command_id=command_id,
                expected_revision=1,
            ),
            session_id="s2",
            expected_draft_revision=1,
            payload=payload,
        )
    )
    return receipt.draft_revision


def _transfer(
    *,
    payload: DraftPayload,
    expected_source: int = 2,
    expected_target: int = 1,
    command_id: str = "cmd-t1",
    target: str = "s2",
    source_kind: DraftTransferSourceKind = DraftTransferSourceKind.SESSION_DRAFT,
    source_id: str = "s1",
    source_session_id: str | None = "auto",
    user_scope: str = _USER,
) -> DraftTransferCommand:
    if source_session_id == "auto":
        source_session_id = source_id if source_kind is DraftTransferSourceKind.SESSION_DRAFT else None
    values: dict[str, Any] = {
        "command": CommandRef(
            user_scope=user_scope,
            source_module="ui",
            business_request_id="req-transfer",
            command_id=command_id,
            expected_revision=expected_target,
        ),
        "transfer_id": "transfer-pending",
        "source_kind": source_kind,
        "source_id": source_id,
        "source_session_id": source_session_id,
        "expected_source_draft_revision": expected_source,
        "target_session_id": target,
        "expected_target_draft_revision": expected_target,
        "payload": payload,
        "payload_digest": draft_transfer_payload_digest(payload),
        "confirmed": True,
    }
    command = DraftTransferCommand(**values)
    return replace(command, transfer_id=derive_draft_transfer_id(command))


def _ref(command_id: str = "cmd-t1", expected: int = 1) -> CommandRef:
    return CommandRef(
        user_scope=_USER,
        source_module="ui",
        business_request_id="req-transfer",
        command_id=command_id,
        expected_revision=expected,
    )


def _expect_error(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    err = excinfo.value
    assert isinstance(err, AgentServiceError)
    assert err.code == code, f"expect {code.value}, got {err.code.value}: {err.sanitized_message}"


async def _session_row(url: str, session_id: str = "s1") -> Any:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    select(
                        AgentSession.draft_revision,
                        AgentSession.draft_payload,
                        AgentSession.revision,
                        AgentSession.updated_at,
                        AgentSession.draft_transfer_ref,
                        AgentSession.archived,
                    ).where(
                        AgentSession.user_scope == _USER,
                        AgentSession.session_id == session_id,
                    )
                )
            ).first()
    finally:
        await engine.dispose()


async def _command_rows(url: str) -> list[Any]:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                (
                    await conn.execute(
                        select(
                            AgentCommand.command_record_id,
                            AgentCommand.command_kind,
                            AgentCommand.target_id,
                            AgentCommand.expected_revision,
                            AgentCommand.input_revision,
                            AgentCommand.input_digest,
                            AgentCommand.commit_state,
                            AgentCommand.delivery_state,
                            AgentCommand.consumption_state,
                            AgentCommand.receipt_payload,
                            AgentCommand.request_payload,
                            AgentCommand.transfer_id,
                            AgentCommand.transfer_phase,
                            AgentCommand.sendable_owner,
                            AgentCommand.transfer_source_kind,
                            AgentCommand.transfer_source_id,
                            AgentCommand.transfer_source_revision,
                            AgentCommand.transfer_source_claimed,
                        ).where(
                            AgentCommand.user_scope == _USER,
                            AgentCommand.command_kind == "draft_transfer",
                        )
                    )
                )
                .all()
                .copy()
            )
    finally:
        await engine.dispose()


async def _draft_links(url: str) -> list[Any]:
    engine = create_test_engine(url)
    try:
        async with engine.connect() as conn:
            return (
                (
                    await conn.execute(
                        select(
                            AgentSourceLink.source_id,
                            AgentSourceLink.consumer_kind,
                            AgentSourceLink.consumer_id,
                            AgentSourceLink.consumer_version,
                            AgentSourceLink.session_id,
                        )
                        .where(AgentSourceLink.user_scope == _USER)
                        .order_by(AgentSourceLink.session_id, AgentSourceLink.source_id)
                    )
                )
                .all()
                .copy()
            )
    finally:
        await engine.dispose()


class TestTransferDraftSessionDraft:
    async def test_completes_and_moves_sendable_ownership(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        assert await _save_source_draft(dao, payload) == 2
        source_before = await _session_row(real_embedded_pg.url, "s1")
        target_before = await _session_row(real_embedded_pg.url, "s2")

        receipt = await dao.transfer_draft(_transfer(payload=payload))

        assert receipt.phase is DraftTransferPhase.COMPLETED
        assert receipt.sendable_owner is SendableOwner.TARGET
        assert receipt.commit_state is CommitState.COMMITTED
        assert receipt.source_revision == 3
        assert receipt.target_revision == 2
        assert receipt.expected_source_draft_revision == 2
        assert receipt.expected_target_draft_revision == 1
        assert receipt.payload_digest == draft_transfer_payload_digest(payload)
        assert receipt.next_action is None

        source_after = await _session_row(real_embedded_pg.url, "s1")
        assert source_after.draft_payload is None
        assert source_after.draft_revision == 3
        assert source_after.draft_transfer_ref == receipt.command_record_id
        assert source_after.revision == source_before.revision + 1

        target_after = await _session_row(real_embedded_pg.url, "s2")
        assert target_after.draft_payload == encode_draft_payload(payload)
        assert target_after.draft_revision == 2
        assert target_after.draft_transfer_ref is None
        assert target_after.revision == target_before.revision + 1

        rows = await _command_rows(real_embedded_pg.url)
        assert len(rows) == 1
        stored = rows[0]
        assert stored.command_kind == "draft_transfer"
        assert stored.target_id == "s2"
        assert stored.expected_revision == 1
        assert stored.input_revision == 2
        assert stored.transfer_id == receipt.transfer_id
        assert stored.transfer_phase == DraftTransferPhase.COMPLETED.value
        assert stored.sendable_owner == SendableOwner.TARGET.value
        assert stored.transfer_source_kind == "session_draft"
        assert stored.transfer_source_id == "s1"
        assert stored.transfer_source_revision == 2
        assert stored.transfer_source_claimed is True
        assert stored.commit_state == CommitState.COMMITTED.value
        assert stored.delivery_state == DeliveryState.NOT_DELIVERED.value
        assert stored.consumption_state == ConsumptionState.NOT_CONSUMED.value
        assert stored.request_payload["transfer_id"] == receipt.transfer_id
        assert stored.request_payload["payload"]["text"] == "研究三家公司"
        assert stored.receipt_payload["phase"] == "completed"

    async def test_replay_returns_original_receipt_without_moving_again(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        command = _transfer(payload=payload)

        first = await dao.transfer_draft(command)
        second = await dao.transfer_draft(command)

        assert second == first
        assert (await _session_row(real_embedded_pg.url, "s1")).draft_revision == 3
        assert (await _session_row(real_embedded_pg.url, "s2")).draft_revision == 2
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_same_key_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        await dao.transfer_draft(_transfer(payload=payload))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=_payload(text="异输入重放")))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

        assert (await _session_row(real_embedded_pg.url, "s1")).draft_revision == 3
        assert (await _session_row(real_embedded_pg.url, "s2")).draft_revision == 2
        assert len(await _command_rows(real_embedded_pg.url)) == 1

    async def test_stale_source_revision_conflicts_with_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        source_before = await _session_row(real_embedded_pg.url, "s1")
        target_before = await _session_row(real_embedded_pg.url, "s2")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=payload, expected_source=1))
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)

        source_after = await _session_row(real_embedded_pg.url, "s1")
        target_after = await _session_row(real_embedded_pg.url, "s2")
        assert source_after.draft_revision == source_before.draft_revision
        assert target_after.draft_revision == target_before.draft_revision
        assert target_after.draft_payload is None
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_stale_target_revision_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        await _save_target_draft(dao, _payload(text="目标已有草稿"))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=payload, expected_target=1))
        _expect_error(excinfo, AgentErrorCode.REVISION_CONFLICT)
        assert (await _session_row(real_embedded_pg.url, "s1")).draft_revision == 2
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_non_empty_target_rejected_with_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        assert await _save_target_draft(dao, _payload(text="目标正文")) == 2

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=payload, expected_target=2))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

        assert (await _session_row(real_embedded_pg.url, "s1")).draft_revision == 2
        target = await _session_row(real_embedded_pg.url, "s2")
        assert target.draft_revision == 2
        assert target.draft_payload["text"] == "目标正文"
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_empty_text_with_unsent_selection_is_not_empty_target(self, real_embedded_pg: ConnectionInfo) -> None:
        """正文空但有未发送选择不是空位（§4.2 L413）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        await _save_target_draft(
            dao,
            _payload(
                text="",
                model_selection=None,
                object_context=None,
                mode=Mode.EXECUTE,
                send_target=json_doc({"kind": "new_question"}),
            ),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=payload, expected_target=2))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_payload_must_match_source_draft(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        await _save_source_draft(dao, _payload())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=_payload(text="调用方自述正文")))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

        assert (await _session_row(real_embedded_pg.url, "s1")).draft_revision == 2
        assert (await _session_row(real_embedded_pg.url, "s2")).draft_payload is None
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_empty_source_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        empty = _payload(text="", model_selection=None, object_context=None, mode=Mode.EXECUTE)
        await _save_source_draft(dao, empty)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=empty))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_archived_target_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2", archived=True)
        payload = _payload()
        await _save_source_draft(dao, payload)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=payload))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_other_user_scope_source_not_visible(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, user_scope="u2", session_id="s1")
        await _seed_session(dao, session_id="s2")
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 另一用户域的源草稿
            await conn.execute(
                update(AgentSession)
                .where(AgentSession.user_scope == "u2", AgentSession.session_id == "s1")
                .values(draft_revision=2, draft_payload=encode_draft_payload(_payload()))
            )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(_transfer(payload=_payload(), expected_source=2))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
        assert await _command_rows(real_embedded_pg.url) == []

    async def test_source_and_target_links_swap_ownership(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        await _seed_asset(dao, "src-1")
        payload = _payload(
            source_refs=(
                SourceVersionRef(
                    source_kind=SourceKind.USER_MESSAGE, source_id="src-1", source_version="1", source_digest=_DIGEST
                ),
            )
        )
        await _save_source_draft(dao, payload)
        assert [(row.session_id, row.consumer_version) for row in await _draft_links(real_embedded_pg.url)] == [
            ("s1", "2")
        ]

        await dao.transfer_draft(_transfer(payload=payload))

        links = await _draft_links(real_embedded_pg.url)
        assert [(row.session_id, row.consumer_id, row.consumer_version) for row in links] == [("s2", "s2", "2")]

    async def test_new_source_draft_clears_transfer_fence(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        await dao.transfer_draft(_transfer(payload=payload))
        assert (await _session_row(real_embedded_pg.url, "s1")).draft_transfer_ref is not None

        await dao.save_draft(
            DraftSaveCommand(
                command=CommandRef(
                    user_scope=_USER,
                    source_module="ui",
                    business_request_id="req-save",
                    command_id="cmd-save-2",
                    expected_revision=3,
                ),
                session_id="s1",
                expected_draft_revision=3,
                payload=_payload(text="移入之后的新草稿"),
            )
        )
        source = await _session_row(real_embedded_pg.url, "s1")
        assert source.draft_transfer_ref is None
        assert source.draft_payload["text"] == "移入之后的新草稿"

    async def test_concurrent_same_source_revision_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        await _seed_session(dao, session_id="s3")
        payload = _payload()
        await _save_source_draft(dao, payload)

        results = await asyncio.gather(
            dao.transfer_draft(_transfer(payload=payload, command_id="cmd-a", target="s2")),
            dao.transfer_draft(_transfer(payload=payload, command_id="cmd-b", target="s3")),
            return_exceptions=True,
        )
        errors = [item for item in results if isinstance(item, AgentServiceError)]
        receipts = [item for item in results if isinstance(item, DraftTransferReceipt)]
        assert len(receipts) == 1, results
        assert len(errors) == 1
        assert errors[0].code is AgentErrorCode.REVISION_CONFLICT

        rows = await _command_rows(real_embedded_pg.url)
        assert len(rows) == 1
        assert rows[0].transfer_source_claimed is True
        assert (await _session_row(real_embedded_pg.url, "s1")).draft_payload is None
        winner_target = receipts[0].target_session_id
        loser_target = "s3" if winner_target == "s2" else "s2"
        assert (await _session_row(real_embedded_pg.url, winner_target)).draft_payload is not None
        assert (await _session_row(real_embedded_pg.url, loser_target)).draft_payload is None


class TestTransferDraftBusinessPreparation:
    async def test_claim_only_transfer_installs_target(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s2")
        payload = _payload()

        receipt = await dao.transfer_draft(
            _transfer(
                payload=payload,
                source_kind=DraftTransferSourceKind.BUSINESS_PREPARATION,
                source_id="prep-1",
                expected_source=1,
            )
        )

        assert receipt.phase is DraftTransferPhase.COMPLETED
        assert receipt.sendable_owner is SendableOwner.TARGET
        # 本库无业务准备稿源行：源 revision 不伪造可靠值。
        assert receipt.source_revision is None
        assert receipt.target_revision == 2
        target = await _session_row(real_embedded_pg.url, "s2")
        assert target.draft_payload == encode_draft_payload(payload)
        rows = await _command_rows(real_embedded_pg.url)
        assert rows[0].transfer_source_kind == "business_preparation"
        assert rows[0].transfer_source_id == "prep-1"
        assert rows[0].transfer_source_claimed is True

    async def test_second_claim_of_same_source_revision_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s2")
        await _seed_session(dao, session_id="s3")
        payload = _payload()
        await dao.transfer_draft(
            _transfer(
                payload=payload,
                source_kind=DraftTransferSourceKind.BUSINESS_PREPARATION,
                source_id="prep-1",
                expected_source=1,
            )
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.transfer_draft(
                _transfer(
                    payload=payload,
                    command_id="cmd-t2",
                    target="s3",
                    source_kind=DraftTransferSourceKind.BUSINESS_PREPARATION,
                    source_id="prep-1",
                    expected_source=1,
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)
        assert (await _session_row(real_embedded_pg.url, "s3")).draft_payload is None
        assert len(await _command_rows(real_embedded_pg.url)) == 1


class TestReadDraftTransfer:
    async def test_returns_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        receipt = await dao.transfer_draft(_transfer(payload=payload))

        assert await dao.read_draft_transfer(_ref(), user_scope=_USER) == receipt

    async def test_unknown_command_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_draft_transfer(_ref("missing"), user_scope=_USER)
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_non_transfer_command_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _save_source_draft(dao, _payload(), command_id="cmd-save-1")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_draft_transfer(_ref("cmd-save-1", expected=1), user_scope=_USER)
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_other_user_scope_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_draft_transfer(_ref(), user_scope="u2")
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_corrupt_receipt_reads_as_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, session_id="s1")
        await _seed_session(dao, session_id="s2")
        payload = _payload()
        await _save_source_draft(dao, payload)
        await dao.transfer_draft(_transfer(payload=payload))

        async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 人为破坏回执
            await conn.execute(
                update(AgentCommand)
                .where(AgentCommand.user_scope == _USER, AgentCommand.command_kind == "draft_transfer")
                .values(receipt_payload={"phase": "completed"})
            )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_draft_transfer(_ref(), user_scope=_USER)
        _expect_error(excinfo, AgentErrorCode.OPERATION_UNKNOWN)
