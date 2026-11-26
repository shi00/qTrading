"""会话模型选择存储：键格式、缺失/损坏语义与端口取消传播。"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, PlanNodeSpec
from core.model_management_types import ExecutionSnapshot
from services.agent_service.leaf_runtime import resolve_agent_model_binding
from services.agent_service.session_model import SessionModelStore
from services.agent_service.workflows import LeafExecutionContext
from services.ai_service.scenario_dispatch import ScenarioDispatch

pytestmark = pytest.mark.unit


@pytest.fixture
def store() -> tuple[SessionModelStore, dict[str, str]]:
    data: dict[str, str] = {}

    async def read(key: str) -> str | None:
        return data.get(key)

    async def write(key: str, value: str) -> None:
        data[key] = value

    async def delete(key: str) -> bool:
        return data.pop(key, None) is not None

    return SessionModelStore(read=read, write=write, delete=delete), data


def test_key_format_is_session_scoped() -> None:
    assert SessionModelStore.key_for("session-1") == "agent.session.model.session-1"


@pytest.mark.asyncio
async def test_missing_value_reads_as_none(store: tuple[SessionModelStore, dict[str, str]]) -> None:
    catalog, _ = store

    assert await catalog.read("session-1") is None


@pytest.mark.asyncio
async def test_round_trip_and_clear(store: tuple[SessionModelStore, dict[str, str]]) -> None:
    catalog, data = store

    await catalog.write("session-1", "conn-local")

    assert data["agent.session.model.session-1"] == "conn-local"
    assert await catalog.read("session-1") == "conn-local"
    assert await catalog.clear("session-1") is True
    assert await catalog.read("session-1") is None
    assert await catalog.clear("session-1") is False


@pytest.mark.asyncio
async def test_read_strips_surrounding_whitespace(store: tuple[SessionModelStore, dict[str, str]]) -> None:
    """历史值带前后空白时归一后再返回（空白本身仍视为损坏）。"""

    catalog, data = store
    data["agent.session.model.session-1"] = "  conn-local\t"

    assert await catalog.read("session-1") == "conn-local"


@pytest.mark.asyncio
async def test_blank_stored_value_is_corruption(store: tuple[SessionModelStore, dict[str, str]]) -> None:
    """空串/仅空白不得伪装成「未配置」（R21）。"""

    catalog, data = store
    for corrupted in ("", "   ", "\t\n"):
        data["agent.session.model.session-1"] = corrupted

        with pytest.raises(AgentServiceError) as excinfo:
            await catalog.read("session-1")

        assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert excinfo.value.sanitized_message == "session model selection is not decodable"
        assert excinfo.value.identity == "session-1"


@pytest.mark.asyncio
async def test_blank_connection_id_is_rejected(store: tuple[SessionModelStore, dict[str, str]]) -> None:
    catalog, data = store
    for invalid in ("", "   ", 123, None):
        with pytest.raises(AgentServiceError) as excinfo:
            await catalog.write("session-1", cast(Any, invalid))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert excinfo.value.sanitized_message == "session model connection id must be non-empty text"

    assert data == {}


@pytest.mark.asyncio
async def test_read_port_cancellation_propagates() -> None:
    async def read(key: str) -> str | None:
        raise asyncio.CancelledError()

    async def write(key: str, value: str) -> None:
        raise AssertionError("must not write")

    async def delete(key: str) -> bool:
        return False

    catalog = SessionModelStore(read=read, write=write, delete=delete)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await catalog.read("session-1")

    assert isinstance(excinfo.value, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_write_port_cancellation_propagates() -> None:
    async def read(key: str) -> str | None:
        return None

    async def write(key: str, value: str) -> None:
        raise asyncio.CancelledError()

    async def delete(key: str) -> bool:
        return False

    catalog = SessionModelStore(read=read, write=write, delete=delete)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await catalog.write("session-1", "conn-local")

    assert isinstance(excinfo.value, asyncio.CancelledError)


# ---------------------------------------------------------------------------
# 会话模型选择透传叶执行链（resolve_agent_model_binding）
# ---------------------------------------------------------------------------

_SNAPSHOT = ExecutionSnapshot(
    snapshot_id="snap-1",
    run_id=None,
    config_revision=1,
    routes=(),
    connections=(),
    business_contract_versions={},
    security_scope_version=1,
    created_at="2026-11-18T00:00:00.000000Z",
)


def _leaf_context() -> LeafExecutionContext:
    return LeafExecutionContext(
        execution_id="execution-1",
        round_id="round-1",
        plan_digest="b" * 64,
        node=PlanNodeSpec(node_id="n1", target="梳理公司业务", role_key="analyst"),
    )


@pytest.mark.asyncio
async def test_binding_threads_preferred_connection_to_route(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    async def _resolve(scenario_id: str, *, preferred_connection_id: str | None = None) -> ScenarioDispatch | None:
        captured.append(f"{scenario_id}|{preferred_connection_id}")
        return ScenarioDispatch(
            executor=object(),
            snapshot=_SNAPSHOT,
            route_revision=1,
            total_timeout_seconds=30.0,
            task_routes={},
        )

    monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)

    await resolve_agent_model_binding(_leaf_context(), preferred_connection_id="conn-local")

    assert captured == ["agent_research|conn-local"]
