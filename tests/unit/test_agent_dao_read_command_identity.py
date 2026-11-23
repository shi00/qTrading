"""read_command / read_draft_transfer 准确 target 身份（检视 D20/T-07）。

缺陷（T-07）：两个读取面把 target 当作可选的「缩小条件」，不带准确目标时只按
命令键 + 预期版 + 意图键过滤，同域另一 target 的同键命令会冒认原命令回执。存储
PK 是 ``stable_id("command",[user_scope, target_id, client_command_id])``，同一命令
键在不同合法 target 下本就并存，因此准确 target 必须是必需身份。本文件用内存行
事实（按语句绑定参数过滤）验证：

- 准确 target 条件确实进入 SQL（首选候选），并按该 target 的记录查回；
- 该 target 下无该命令 → not found，绝不回退同域另一 target 的同键记录；
- 跨用户域、空 target、非转移命令、预期版不符的诊断语义不变；重启仍可查回。

真实 PG 链路由 ``tests/integration/test_agent_dao_read_command.py`` 与
``tests/integration/test_agent_dao_transfer_draft.py`` 覆盖。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryState,
    DraftTransferPhase,
    DraftTransferReceipt,
    DraftTransferSourceKind,
    SendableOwner,
    encode_draft_transfer_receipt,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_DT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = [] if rows is None else list(rows)

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _CommandStore:
    """内存命令行事实：执行语句时按绑定参数过滤，暴露 DAO 实际下推的身份条件。"""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement))
        bound = statement.compile().params
        return _Result(rows=[row for row in self.rows if _row_matches(row, bound)])


def _row_matches(row: SimpleNamespace, bound: dict[str, Any]) -> bool:
    for key, value in bound.items():
        column = key.rsplit("_", 1)[0]
        if column == "user_scope" and row.user_scope != value:
            return False
        if column == "client_command_id" and row.client_command_id != value:
            return False
        if column == "target_id" and row.target_id != value:
            return False
        if column == "expected_revision" and row.expected_revision != value:
            return False
        if column == "command_kind" and row.command_kind != value:
            return False
        if column == "source_module" and row.intent_source_module is not None:
            if row.intent_source_module != value:
                return False
            if row.intent_business_request_id != bound.get("business_request_id_1"):
                return False
    return True


def _dao(conn: _CommandStore) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=cm)
    return AgentDao(engine)


def _command_row(
    *,
    user_scope: str = "u1",
    client_command_id: str = "c-1",
    target_id: str = "s1",
    expected_revision: int = 0,
    intent_source_module: str | None = "ui",
    intent_business_request_id: str | None = "req-1",
    command_kind: str = "submit",
    receipt_payload: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_scope=user_scope,
        client_command_id=client_command_id,
        target_id=target_id,
        command_kind=command_kind,
        expected_revision=expected_revision,
        revision=1,
        commit_state=CommitState.COMMITTED.value,
        delivery_state=DeliveryState.NOT_DELIVERED.value,
        consumption_state=ConsumptionState.NOT_CONSUMED.value,
        updated_at=_DT,
        error_code=None,
        receipt_payload=receipt_payload,
        intent_source_module=intent_source_module,
        intent_business_request_id=intent_business_request_id,
    )


def _ref(
    *,
    command_id: str = "c-1",
    source_module: str = "ui",
    business_request_id: str = "req-1",
    expected_revision: int = 0,
    user_scope: str = "u1",
) -> CommandRef:
    return CommandRef(
        user_scope=user_scope,
        source_module=source_module,
        business_request_id=business_request_id,
        command_id=command_id,
        expected_revision=expected_revision,
    )


def _transfer_receipt(expected_target: int = 1, *, target: str = "s2") -> dict[str, Any]:
    return encode_draft_transfer_receipt(
        DraftTransferReceipt(
            command_record_id="cmd-rec-1",
            transfer_id="transfer-1",
            source_kind=DraftTransferSourceKind.SESSION_DRAFT,
            source_id="s1",
            source_session_id="s1",
            target_session_id=target,
            expected_source_draft_revision=2,
            expected_target_draft_revision=expected_target,
            payload_digest="a" * 64,
            commit_state=CommitState.COMMITTED,
            phase=DraftTransferPhase.COMPLETED,
            sendable_owner=SendableOwner.TARGET,
            source_revision=3,
            target_revision=expected_target + 1,
            verified_at="2026-06-01T12:00:00.000000Z",
            next_action=None,
        )
    )


class TestReadCommandIdentityPreFilter:
    async def test_same_key_across_targets_resolved_by_exact_target(self) -> None:
        store = _CommandStore(
            [
                _command_row(target_id="s1", intent_business_request_id="req-1"),
                _command_row(target_id="s2", intent_business_request_id="req-2"),
            ]
        )

        receipt = await _dao(store).read_command(_ref(), user_scope="u1", target_id="s1")

        assert receipt.target_id == "s1"
        assert "agent_command.target_id = " in store.statements[0]
        assert "agent_command.expected_revision = " in store.statements[0]
        assert "agent_execution.source_module = " in store.statements[0]

    async def test_leaf_execution_without_intent_key_still_readable(self) -> None:
        store = _CommandStore([_command_row(intent_source_module=None, intent_business_request_id=None)])

        receipt = await _dao(store).read_command(
            _ref(source_module="api", business_request_id="other"), user_scope="u1", target_id="s1"
        )

        assert receipt.target_id == "s1"

    async def test_other_target_with_same_identity_is_not_returned(self) -> None:
        store = _CommandStore(
            [
                _command_row(target_id="s1"),
                _command_row(target_id="s2"),
            ]
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_command(_ref(), user_scope="u1", target_id="s3")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not found in user scope" in excinfo.value.sanitized_message

    async def test_exact_target_selects_that_targets_record(self) -> None:
        store = _CommandStore(
            [
                _command_row(target_id="s1"),
                _command_row(target_id="s2"),
            ]
        )

        receipt = await _dao(store).read_command(_ref(), user_scope="u1", target_id="s2")

        assert receipt.target_id == "s2"
        assert "agent_command.target_id = " in store.statements[0]

    async def test_restart_new_dao_and_connection_still_reads_back(self) -> None:
        store = _CommandStore([_command_row(target_id="s1")])

        first = await _dao(store).read_command(_ref(), user_scope="u1", target_id="s1")
        restarted = await _dao(store).read_command(_ref(), user_scope="u1", target_id="s1")

        assert first == restarted

    async def test_identity_mismatch_reported_without_echoing_states(self) -> None:
        store = _CommandStore([_command_row(intent_source_module="ui")])

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_command(_ref(source_module="api"), user_scope="u1", target_id="s1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "does not match stored command" in excinfo.value.sanitized_message
        assert "source_module" in excinfo.value.sanitized_message
        # 诊断路径不携带身份条件（回看该 target 全部候选以区分「不存在」与「不符」）。
        assert "agent_execution.source_module = " not in store.statements[-1]

    async def test_unknown_command_reports_not_found(self) -> None:
        store = _CommandStore([])

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_command(_ref(), user_scope="u1", target_id="s1")
        assert "not found in user scope" in excinfo.value.sanitized_message

    async def test_cross_user_scope_rejected_before_lookup(self) -> None:
        store = _CommandStore([_command_row()])

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_command(_ref(), user_scope="u2", target_id="s1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert store.statements == []

    async def test_empty_target_rejected(self) -> None:
        store = _CommandStore([_command_row()])

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_command(_ref(), user_scope="u1", target_id="")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert store.statements == []


class TestReadDraftTransferIdentityPreFilter:
    async def test_exact_target_and_revision_prefiltered(self) -> None:
        store = _CommandStore(
            [
                _command_row(
                    target_id="s1",
                    command_kind="draft_transfer",
                    expected_revision=5,
                    receipt_payload=_transfer_receipt(5, target="s1"),
                ),
                _command_row(
                    target_id="s2",
                    command_kind="draft_transfer",
                    expected_revision=1,
                    receipt_payload=_transfer_receipt(1, target="s2"),
                ),
            ]
        )

        receipt = await _dao(store).read_draft_transfer(
            _ref(expected_revision=1), user_scope="u1", target_session_id="s2"
        )

        assert receipt.target_session_id == "s2"
        assert "agent_command.target_id = " in store.statements[0]
        assert "agent_command.command_kind = " in store.statements[0]
        assert "agent_command.expected_revision = " in store.statements[0]

    async def test_other_target_with_same_key_is_not_returned(self) -> None:
        store = _CommandStore(
            [
                _command_row(
                    target_id="s1",
                    command_kind="draft_transfer",
                    expected_revision=1,
                    receipt_payload=_transfer_receipt(),
                ),
                _command_row(
                    target_id="s2",
                    command_kind="draft_transfer",
                    expected_revision=1,
                    receipt_payload=_transfer_receipt(),
                ),
            ]
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_draft_transfer(_ref(expected_revision=1), user_scope="u1", target_session_id="s3")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not found in user scope" in excinfo.value.sanitized_message

    async def test_exact_target_selects_that_targets_record(self) -> None:
        store = _CommandStore(
            [
                _command_row(
                    target_id="s1",
                    command_kind="draft_transfer",
                    expected_revision=1,
                    receipt_payload=_transfer_receipt(),
                ),
                _command_row(
                    target_id="s2",
                    command_kind="draft_transfer",
                    expected_revision=1,
                    receipt_payload=_transfer_receipt(),
                ),
            ]
        )

        receipt = await _dao(store).read_draft_transfer(
            _ref(expected_revision=1), user_scope="u1", target_session_id="s2"
        )

        assert receipt.target_session_id == "s2"
        assert "agent_command.target_id = " in store.statements[0]

    async def test_repeat_read_after_restart_returns_original(self) -> None:
        store = _CommandStore(
            [
                _command_row(
                    target_id="s2",
                    command_kind="draft_transfer",
                    expected_revision=1,
                    receipt_payload=_transfer_receipt(),
                )
            ]
        )

        first = await _dao(store).read_draft_transfer(
            _ref(expected_revision=1), user_scope="u1", target_session_id="s2"
        )
        restarted = await _dao(store).read_draft_transfer(
            _ref(expected_revision=1), user_scope="u1", target_session_id="s2"
        )

        assert first == restarted

    async def test_non_transfer_command_diagnosed(self) -> None:
        store = _CommandStore([_command_row(target_id="s1", command_kind="save_draft", expected_revision=1)])

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_draft_transfer(_ref(expected_revision=1), user_scope="u1", target_session_id="s1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not a draft transfer" in excinfo.value.sanitized_message

    async def test_revision_mismatch_diagnosed(self) -> None:
        store = _CommandStore(
            [
                _command_row(
                    target_id="s1",
                    command_kind="draft_transfer",
                    expected_revision=2,
                    receipt_payload=_transfer_receipt(2),
                )
            ]
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_draft_transfer(_ref(expected_revision=1), user_scope="u1", target_session_id="s1")
        assert "expected_revision" in excinfo.value.sanitized_message

    async def test_missing_receipt_is_unknown(self) -> None:
        store = _CommandStore(
            [_command_row(target_id="s1", command_kind="draft_transfer", expected_revision=1, receipt_payload=None)]
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_draft_transfer(_ref(expected_revision=1), user_scope="u1", target_session_id="s1")
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN

    async def test_empty_target_session_rejected(self) -> None:
        store = _CommandStore([_command_row(command_kind="draft_transfer", expected_revision=1)])

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(store).read_draft_transfer(_ref(expected_revision=1), user_scope="u1", target_session_id="")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert store.statements == []
