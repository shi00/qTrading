"""受理/存稿写事务失败分列（N12）：确定未写 ≠ 提交结果未知。

``AgentDao._write_transaction`` 是受理（``accept_request``）与存稿（``save_draft``）
共用的写事务信封，判据：

- 事务体未走完（取连接/开事务/语句失败）或 COMMIT 返回服务端错误 → PostgreSQL 未
  提交 → ``PERSISTENCE_UNAVAILABLE``（同一命令键重试安全）；
- 事务体走完、COMMIT 已发出但确认中断（连接失效/超时）→ 服务端可能已提交 →
  ``OPERATION_UNKNOWN``（按原身份查回，不换键补写）；
- 领域错误与取消原样传播（R2）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    CommandRef,
    DraftPayload,
    DraftSaveCommand,
    Mode,
    Purpose,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.daos.base_dao import EngineDisposedError

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, first: Any = None, rows: list[Any] | None = None, scalar: int = 0) -> None:
        self._first = first
        self._rows = [] if rows is None else list(rows)
        self._scalar = scalar
        self.rowcount = 1

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _Result:
        return self

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._first


class _WriteConn:
    """最小行事实：让受理/存稿事务体走完（不校验 SQL 细节，只按表名分派）。"""

    def __init__(self, *, session_row: Any, command_row: Any = None, execution_row: Any = None) -> None:
        self.session_row = session_row
        self.command_row = command_row
        self.execution_row = execution_row
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=[])
        if "FROM agent_session" in sql:
            return _Result(first=self.session_row)
        if "FROM agent_command" in sql:
            return _Result(first=self.command_row)
        if "FROM agent_execution" in sql:
            return _Result(first=self.execution_row)
        if "FROM agent_message" in sql:
            return _Result(scalar=0)
        return _Result()


def _begin_cm(*, exit_error: BaseException | None = None, conn: Any = None) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn if conn is not None else SimpleNamespace())
    cm.__aexit__ = AsyncMock(return_value=False, side_effect=exit_error)
    return cm


def _dao(begin: Any) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    if isinstance(begin, BaseException):
        engine.begin = MagicMock(side_effect=begin)
    else:
        engine.begin = MagicMock(return_value=begin)
    return AgentDao(engine)


def _failing_conn(error: BaseException) -> SimpleNamespace:
    async def _execute(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return SimpleNamespace(execute=_execute)


def _payload() -> DraftPayload:
    return DraftPayload(
        schema_version=2,
        text="研究三家公司",
        mode=Mode.DISCUSS,
        model_selection=None,
        source_refs=(),
        skill_versions=(),
        mcp_selection=(),
        purpose=Purpose.CONCEPT,
        research_date=None,
        cutoff=None,
        send_target=None,
        object_context=None,
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def _draft_command() -> DraftSaveCommand:
    return DraftSaveCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="mod",
            business_request_id="req-1",
            command_id="cmd-1",
            expected_revision=1,
        ),
        session_id="s1",
        expected_draft_revision=1,
        payload=_payload(),
    )


def _accept_request() -> AcceptRequest:
    return AcceptRequest(
        user_scope="u1",
        session_id="s1",
        source_module="mod",
        business_request_id="req-1",
        client_command_id="cmd-1",
        expected_revision=0,
        node_id="root",
        workflow_id="wf-1",
        mode=Mode.DISCUSS,
        purpose=Purpose.CONCEPT,
        text="研究三家公司",
        manifest_payload=json_doc({"schema_version": 1, "source_module": "mod", "business_request_id": "req-1"}),
        source_display_name="src",
    )


def _draft_session_row() -> SimpleNamespace:
    return SimpleNamespace(draft_revision=1, event_seq=0)


def _accept_session_row() -> SimpleNamespace:
    return SimpleNamespace(session_id="s1", draft_revision=1, draft_payload=None, archived=False)


@pytest.fixture
def _no_side_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """旁路草稿谱系边与事件写入：本文件只验证写事务阶段分类。"""

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(AgentDao, "_sync_draft_source_links", _noop)
    monkeypatch.setattr(AgentDao, "_append_session_event", _noop)


class TestWriteTransactionEnvelope:
    async def test_connect_failure_is_definitely_not_committed(self) -> None:
        dao = _dao(ConnectionRefusedError("db down"))
        with pytest.raises(AgentServiceError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                raise AssertionError("unreachable")
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert exc.value.identity == "u1/s1/cmd-1"
        assert "retry with the same command id" in exc.value.sanitized_message

    async def test_statement_failure_is_wrapped_as_not_committed(self) -> None:
        cm = _begin_cm(conn=_failing_conn(ConnectionResetError("reset during body")))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1") as conn:  # pyright: ignore[reportPrivateUsage]
                await conn.execute(text("SELECT 1"))
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        cm.__aexit__.assert_awaited_once()

    async def test_commit_ack_lost_is_unknown(self) -> None:
        cm = _begin_cm(exit_error=ConnectionResetError("ack lost"))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                pass
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert "query the original identity" in exc.value.sanitized_message

    async def test_commit_timeout_is_unknown(self) -> None:
        cm = _begin_cm(exit_error=TimeoutError("commit timeout"))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                pass
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN

    async def test_commit_server_error_is_definitely_not_committed(self) -> None:
        # 服务端在 COMMIT 返回错误（约束/序列化等）表示已回滚，不得报「结果未知」。
        cm = _begin_cm(exit_error=IntegrityError("COMMIT", None, Exception("duplicate key")))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                pass
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    async def test_domain_error_propagates_unchanged(self) -> None:
        cm = _begin_cm()
        dao = _dao(cm)
        original = AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "conflict", "u1/s1/cmd-1")
        with pytest.raises(AgentServiceError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                raise original
        assert exc.value is original
        cm.__aexit__.assert_awaited_once()

    async def test_cancellation_propagates(self) -> None:
        cm = _begin_cm()
        dao = _dao(cm)
        cancelled = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as exc:
            async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                raise cancelled
        assert exc.value is cancelled
        cm.__aexit__.assert_awaited_once()

    async def test_engine_disposed_and_validation_errors_propagate(self) -> None:
        # R5 / 合同校验是明确领域信号，不得降级为「存储不可用」。
        for error in (EngineDisposedError("engine disposed"), AgentValidationError("invalid", "$.x")):
            dao = _dao(_begin_cm())
            with pytest.raises(type(error)) as exc:
                async with dao._write_transaction(identity="u1/s1/cmd-1"):  # pyright: ignore[reportPrivateUsage]
                    raise error
            assert exc.value is error


class TestAcceptRequestWriteStates:
    """受理入口：两种故障分别给出不同状态（不再裸抛驱动异常）。"""

    async def test_connect_failure_is_not_committed(self) -> None:
        dao = _dao(ConnectionRefusedError("db down"))
        with pytest.raises(AgentServiceError) as exc:
            await dao.accept_request(_accept_request())
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    async def test_statement_failure_is_not_committed(self) -> None:
        dao = _dao(_begin_cm(conn=_failing_conn(ConnectionResetError("reset"))))
        with pytest.raises(AgentServiceError) as exc:
            await dao.accept_request(_accept_request())
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    async def test_commit_ack_lost_is_unknown(self) -> None:
        cm = _begin_cm(conn=_WriteConn(session_row=_accept_session_row()), exit_error=ConnectionResetError("ack lost"))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            await dao.accept_request(_accept_request())
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert exc.value.identity == "u1/mod/req-1"

    async def test_domain_rejection_is_not_reported_as_unknown(self) -> None:
        # 会话不存在是领域拒绝（确定未写），不得与「提交结果未知」混淆。
        cm = _begin_cm(conn=_WriteConn(session_row=None))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            await dao.accept_request(_accept_request())
        assert exc.value.code is AgentErrorCode.INPUT_INVALID


class TestSaveDraftWriteStates:
    """存稿入口：两种故障分别给出不同状态。"""

    async def test_connect_failure_is_not_committed(self) -> None:
        dao = _dao(ConnectionRefusedError("db down"))
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_draft(_draft_command())
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    async def test_statement_failure_is_not_committed(self) -> None:
        dao = _dao(_begin_cm(conn=_failing_conn(ConnectionResetError("reset"))))
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_draft(_draft_command())
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    async def test_commit_ack_lost_is_unknown(self, _no_side_writes: None) -> None:
        cm = _begin_cm(conn=_WriteConn(session_row=_draft_session_row()), exit_error=ConnectionResetError("ack lost"))
        dao = _dao(cm)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_draft(_draft_command())
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert exc.value.identity == "u1/s1/cmd-1"

    async def test_revision_conflict_is_not_reported_as_unknown(self, _no_side_writes: None) -> None:
        conn = _WriteConn(session_row=SimpleNamespace(draft_revision=3, event_seq=0))
        dao = _dao(_begin_cm(conn=conn))
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_draft(_draft_command())
        assert exc.value.code is AgentErrorCode.REVISION_CONFLICT
