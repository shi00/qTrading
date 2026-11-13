"""E-02 接收/解码/深度/目录/schema/单结果/留存预算与 deadline 单元测试。

设计（§10.4 / HANDOFF §9）要求上述预算在大量分配之前冻结并执行，恰等通过、
超一拒绝；本测试只验证计量与早拒绝合同，不声明任何产品数值已批准。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from core.agent_types import MCPBudgetProfile
from services.agent_service.mcp import MCPServiceIdentity
from services.agent_service.mcp_budget import (
    MCPBudgetExceeded,
    MCPBudgetGuard,
    MCPDeadline,
    MCPProgressGuard,
    bounded_json_loads,
    read_bounded_stream,
)

_IDENTITY = MCPServiceIdentity(service_id="svc-local-fs", config_version=1)


def _profile(**overrides: int | None) -> MCPBudgetProfile:
    values: dict[str, int | None] = {
        "receive_bytes": 100,
        "decoded_bytes": 200,
        "structure_depth": 4,
        "catalog_items": 3,
        "catalog_bytes": 100,
        "schema_bytes": 100,
        "single_result_bytes": 50,
        "retention_bytes": 80,
    }
    values.update(overrides)
    return MCPBudgetProfile(**values)


def _guard(**overrides: int | None) -> MCPBudgetGuard:
    return MCPBudgetGuard(_profile(**overrides), identity="svc-local-fs@1")


async def _chunks(count: int, size: int = 10) -> AsyncIterator[bytes]:
    for _ in range(count):
        yield b"x" * size


class TestBudgetAdmission:
    def test_unfrozen_profile_cannot_construct_guard(self) -> None:
        from core.agent_types import AgentErrorCode, AgentServiceError

        with pytest.raises(AgentServiceError) as exc:
            MCPBudgetGuard(MCPBudgetProfile(), identity="svc@1")
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "receive_bytes" in exc.value.sanitized_message


class TestReceiveBytes:
    @pytest.mark.asyncio
    async def test_exact_receive_limit_passes(self) -> None:
        data = await read_bounded_stream(_chunks(10), _guard(receive_bytes=100))
        assert len(data) == 100

    @pytest.mark.asyncio
    async def test_one_byte_over_is_rejected_before_reading_more_chunks(self) -> None:
        consumed = 0

        async def source() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(5):
                consumed += 1
                yield b"x" * 10

        with pytest.raises(MCPBudgetExceeded) as exc:
            await read_bounded_stream(source(), _guard(receive_bytes=25))
        assert exc.value.family == "receive_bytes"
        # 第 3 块即 30 字节 > 25：早拒绝后不再继续拉取剩余块。
        assert consumed == 3


class TestDecodedBytes:
    def test_compression_amplification_is_measured_after_decode(self) -> None:
        guard = _guard(decoded_bytes=10)
        guard.decoded_bytes(10)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.decoded_bytes(1)
        assert exc.value.family == "decoded_bytes"


class TestStructureDepth:
    def test_depth_exactly_at_limit_passes(self) -> None:
        text = '{"a":{"b":{"c":{"d":1}}}}'
        assert bounded_json_loads(text, _guard(structure_depth=4)) == {"a": {"b": {"c": {"d": 1}}}}

    def test_depth_over_limit_rejected_before_parse(self) -> None:
        text = '{"a":{"b":{"c":{"d":{"e":1}}}}}'
        with pytest.raises(MCPBudgetExceeded) as exc:
            bounded_json_loads(text, _guard(structure_depth=4))
        assert exc.value.family == "structure_depth"

    def test_braces_inside_strings_do_not_count_as_depth(self) -> None:
        text = '{"a":"{{{{{{"}'
        assert bounded_json_loads(text, _guard(structure_depth=1)) == {"a": "{{{{{{"}


class TestCatalogAndSchema:
    def test_catalog_items_exact_and_plus_one(self) -> None:
        guard = _guard(catalog_items=2)
        guard.catalog_item()
        guard.catalog_item()
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.catalog_item()
        assert exc.value.family == "catalog_items"

    def test_catalog_bytes_exact_and_plus_one(self) -> None:
        guard = _guard(catalog_bytes=20)
        guard.catalog_bytes(20)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.catalog_bytes(1)
        assert exc.value.family == "catalog_bytes"

    def test_schema_bytes_accumulate_across_tools(self) -> None:
        guard = _guard(schema_bytes=100)
        guard.schema_bytes(60)
        guard.schema_bytes(40)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.schema_bytes(1)
        assert exc.value.family == "schema_bytes"


class TestSingleResultAndRetention:
    def test_single_result_exact_and_plus_one(self) -> None:
        guard = _guard(single_result_bytes=50)
        guard.single_result_bytes(50)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.single_result_bytes(1)
        assert exc.value.family == "single_result_bytes"

    def test_retention_accumulates_across_operations(self) -> None:
        guard = _guard(retention_bytes=80)
        guard.retention_bytes(50)
        guard.retention_bytes(30)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.retention_bytes(1)
        assert exc.value.family == "retention_bytes"


class TestProgressAndDeadline:
    def test_consecutive_progress_over_limit_is_rejected(self) -> None:
        guard = _guard()
        progress = MCPProgressGuard(guard, max_consecutive=3)
        for _ in range(3):
            progress.observe()
        with pytest.raises(MCPBudgetExceeded) as exc:
            progress.observe()
        assert exc.value.family == "progress_burst"
        progress.completed()
        progress.observe()  # 完成后重新计数

    def test_progress_does_not_extend_deadline(self) -> None:
        now = [1000.0]
        deadline = MCPDeadline(seconds=30, monotonic=lambda: now[0])
        assert deadline.remaining() == 30

        now[0] += 10
        deadline.observe_progress()
        assert deadline.remaining() == 20
        assert deadline.expired() is False

        now[0] += 20
        assert deadline.expired() is True


@pytest.fixture(autouse=True)
def _no_side_effects() -> None:
    """本模块不触达真实 SDK/网络/数据库。"""
