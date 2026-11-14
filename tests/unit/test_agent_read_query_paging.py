"""成果/操作读取查询与正文分页合同（DD §4.2/§9.10.1，F-02-2/F-02-3）。

覆盖：``limit`` 1—50、游标语法（``b:``/``o:`` 前缀、1 基、越界失效）、码点分页
不截断、服务门面委托与关闭后 fail-closed。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ArtifactReadQuery,
    OperationReadQuery,
    READ_LIMIT_MAX,
    slice_body_page,
    validate_artifact_read_query,
    validate_operation_read_query,
)
from services.agent_service.service import AgentService


def _artifact_query(**overrides: Any) -> ArtifactReadQuery:
    values: dict[str, Any] = {
        "artifact_id": "art-1",
        "artifact_version": 1,
        "execution_id": "exec-1",
        "round_id": "round-1",
    }
    values.update(overrides)
    return ArtifactReadQuery(**values)


def _operation_query(**overrides: Any) -> OperationReadQuery:
    values: dict[str, Any] = {"operation_id": "op-1"}
    values.update(overrides)
    return OperationReadQuery(**values)


class TestArtifactReadQuery:
    def test_defaults_to_max_limit(self) -> None:
        assert validate_artifact_read_query(_artifact_query()).limit == READ_LIMIT_MAX

    @pytest.mark.parametrize("limit", [0, READ_LIMIT_MAX + 1])
    def test_limit_out_of_range_rejected(self, limit: int) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            validate_artifact_read_query(_artifact_query(limit=limit))

        assert excinfo.value.code == "invalid_range"

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            validate_artifact_read_query(_artifact_query(artifact_version=0))

        assert excinfo.value.code == "invalid_range"

    def test_cursor_prefix_is_frozen(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            validate_artifact_read_query(_artifact_query(body_cursor="o:3"))

        assert excinfo.value.code == "invalid_cursor"

    def test_cursor_must_be_one_based(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            validate_artifact_read_query(_artifact_query(body_cursor="b:0"))

        assert excinfo.value.code == "invalid_cursor"


class TestOperationReadQuery:
    def test_round_requires_execution(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            validate_operation_read_query(_operation_query(round_id="round-1"))

        assert excinfo.value.code == "identity_mismatch"

    def test_execution_and_tool_call_optional(self) -> None:
        validated = validate_operation_read_query(_operation_query(execution_id="exec-1", tool_call_id="call-1"))

        assert validated.execution_id == "exec-1"
        assert validated.tool_call_id == "call-1"
        assert validated.limit == READ_LIMIT_MAX


class TestSliceBodyPage:
    def test_default_limit_returns_whole_short_body(self) -> None:
        chunk, next_cursor = slice_body_page("短正文", cursor=None, prefix="b", limit=READ_LIMIT_MAX)

        assert chunk == "短正文"
        assert next_cursor is None

    def test_pages_by_codepoints(self) -> None:
        body = "字" * 5000

        first, cursor = slice_body_page(body, cursor=None, prefix="b", limit=1)
        assert len(first) == 2000
        assert cursor == "b:2001"

        second, cursor = slice_body_page(body, cursor=cursor, prefix="b", limit=1)
        assert (first + second) == body[:4000]
        assert cursor == "b:4001"

        third, cursor = slice_body_page(body, cursor=cursor, prefix="b", limit=2)
        assert third == body[4000:]
        assert cursor is None

    def test_cursor_exactly_past_end_is_empty_page(self) -> None:
        chunk, next_cursor = slice_body_page("abc", cursor="b:4", prefix="b", limit=1)

        assert chunk == ""
        assert next_cursor is None

    def test_cursor_beyond_end_is_invalid(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            slice_body_page("abc", cursor="b:5", prefix="b", limit=1)

        assert excinfo.value.code == "invalid_cursor"


class _RecordingDao:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, str]] = []

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> Any:
        self.calls.append(("artifact", query.artifact_id, user_scope))
        return "artifact-sentinel"

    async def read_operation(self, query: OperationReadQuery, *, user_scope: str) -> Any:
        self.calls.append(("operation", query.operation_id, user_scope))
        return "operation-sentinel"


class TestAgentServiceReadArtifact:
    @pytest.mark.asyncio
    async def test_read_artifact_delegates(self) -> None:
        dao = _RecordingDao()
        service = AgentService(dao)  # type: ignore[arg-type]

        assert await service.read_artifact(_artifact_query(), user_scope="u1") == "artifact-sentinel"
        assert dao.calls == [("artifact", "art-1", "u1")]

    @pytest.mark.asyncio
    async def test_closed_service_rejects(self) -> None:
        service = AgentService(_RecordingDao())  # type: ignore[arg-type]
        await service.aclose()

        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_artifact(_artifact_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


class TestAgentServiceReadOperation:
    @pytest.mark.asyncio
    async def test_read_operation_delegates(self) -> None:
        dao = _RecordingDao()
        service = AgentService(dao)  # type: ignore[arg-type]

        assert await service.read_operation(_operation_query(), user_scope="u1") == "operation-sentinel"
        assert dao.calls == [("operation", "op-1", "u1")]

    @pytest.mark.asyncio
    async def test_closed_service_rejects(self) -> None:
        service = AgentService(_RecordingDao())  # type: ignore[arg-type]
        await service.aclose()

        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_operation(_operation_query(), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
