"""来源读取成员校验契约测试（检视 D12）。

执行上下文的源边 ``consumer_version`` 承载源所属输入版（检视 D13），读取必须按
本次查询的准确输入版核成员：同输入版可查，旧版源集不得借给新版查询。输入版
按指定轮次核定；未指定轮次时取执行当前轮/权威受理 manifest（与 D30 同口径）。

真实 PG 约束与端到端读取由 ``tests/integration/test_agent_dao_read_source.py``
覆盖；本文件用 capture conn 断言成员条件与拒绝分支。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    Selection,
    SourceKind,
    SourceLocator,
    SourceReadQuery,
    SourceVersionRef,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_DIGEST = "a" * 64


def _locator() -> SourceLocator:
    return SourceLocator(
        locator_kind="source_range",
        origin_ref=SourceVersionRef(
            source_kind=SourceKind.ATTACHMENT,
            source_id="src-1",
            source_version="v1",
            source_digest=_DIGEST,
        ),
        selection=Selection(kind="all"),
    )


def _query(
    *,
    execution_only: bool = False,
    round_id: str = "round-1",
    input_revision: int = 2,
) -> SourceReadQuery:
    return SourceReadQuery(
        source_id="src-1",
        version="v1",
        digest=_DIGEST,
        locator=_locator(),
        draft_revision=None,
        execution_id="exec-1",
        round_id=None if execution_only else round_id,
        input_revision=None if execution_only else input_revision,
    )


def _requested_versions(statement: Any) -> set[str]:
    """取语句绑定的 ``consumer_version`` 值（成员过滤条件的实证）。"""

    compile_fn = getattr(statement, "compile", None)
    if compile_fn is None:
        return set()
    return {str(value) for key, value in compile_fn().params.items() if "consumer_version" in key}


class _SourceMembershipConn:
    """按 SQL 目标表返回最小行事实，并记录源边查询请求的输入版。"""

    def __init__(
        self,
        *,
        registered: set[str],
        round_row: Any = None,
        execution_row: Any = None,
        command_row: Any = None,
    ) -> None:
        self.registered = registered
        self.round_row = round_row
        self.execution_row = execution_row
        self.command_row = command_row
        self.link_versions: list[set[str]] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        result = MagicMock()
        result.first.return_value = None
        if "FROM agent_source_link" in sql:
            requested = _requested_versions(statement)
            self.link_versions.append(requested)
            if requested & self.registered:
                result.first.return_value = SimpleNamespace(link_id="link-1")
        elif "FROM agent_round" in sql:
            result.first.return_value = self.round_row
        elif "FROM agent_execution" in sql:
            result.first.return_value = self.execution_row
        elif "FROM agent_command" in sql:
            result.first.return_value = self.command_row
        return result


def _round(round_id: str = "round-1", *, execution_id: str = "exec-1", input_revision: int = 2) -> Any:
    return SimpleNamespace(round_id=round_id, execution_id=execution_id, input_revision=input_revision)


async def _assert_registered(conn: Any, query: SourceReadQuery) -> None:
    await AgentDao(None)._assert_source_registered(conn, query=query, user_scope="u1", identity="u1/src-1@v1")


class TestExecutionInputVersionMembership:
    """D12：按本次查询输入版核源边成员，旧新源集不交叉借用。"""

    async def test_same_input_version_source_readable(self) -> None:
        conn = _SourceMembershipConn(registered={"2"}, round_row=_round(input_revision=2))

        await _assert_registered(conn, _query(input_revision=2))

        assert conn.link_versions == [{"2"}]

    async def test_source_from_other_input_version_rejected(self) -> None:
        conn = _SourceMembershipConn(registered={"1"}, round_row=_round(input_revision=2))

        with pytest.raises(AgentServiceError) as excinfo:
            await _assert_registered(conn, _query(input_revision=2))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "not registered" in excinfo.value.sanitized_message
        assert conn.link_versions == [{"2"}]

    async def test_execution_only_query_uses_current_round_input_version(self) -> None:
        conn = _SourceMembershipConn(
            registered={"3"},
            execution_row=SimpleNamespace(current_round_id="round-2"),
            round_row=_round("round-2", input_revision=3),
        )

        await _assert_registered(conn, _query(execution_only=True))

        assert conn.link_versions == [{"3"}]

    async def test_execution_only_queued_uses_authoritative_manifest(self) -> None:
        conn = _SourceMembershipConn(
            registered={"5"},
            execution_row=SimpleNamespace(current_round_id=None),
            command_row=SimpleNamespace(input_revision=5, request_payload=None),
        )

        await _assert_registered(conn, _query(execution_only=True))

        assert conn.link_versions == [{"5"}]

    async def test_execution_only_unknown_execution_rejected(self) -> None:
        conn = _SourceMembershipConn(registered={"2"})

        with pytest.raises(AgentServiceError) as excinfo:
            await _assert_registered(conn, _query(execution_only=True))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.link_versions == []

    async def test_round_of_other_execution_rejected(self) -> None:
        conn = _SourceMembershipConn(registered={"2"}, round_row=_round(execution_id="exec-2"))

        with pytest.raises(AgentServiceError) as excinfo:
            await _assert_registered(conn, _query(input_revision=2))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "round does not belong" in excinfo.value.sanitized_message
        assert conn.link_versions == []

    async def test_declared_input_revision_mismatch_rejected(self) -> None:
        conn = _SourceMembershipConn(registered={"2"}, round_row=_round(input_revision=1))

        with pytest.raises(AgentServiceError) as excinfo:
            await _assert_registered(conn, _query(input_revision=2))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "input_revision does not match" in excinfo.value.sanitized_message
        assert conn.link_versions == []
