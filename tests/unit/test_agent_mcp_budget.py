"""E-02 接收/解码/深度/目录/schema/单结果/留存预算与 deadline 单元测试。

设计（§10.4 / HANDOFF §9）要求上述预算在大量分配之前冻结并执行，恰等通过、
超一拒绝；本测试只验证计量与早拒绝合同，不声明任何产品数值已批准。
入站字节/深度的真实插入点（HTTP 响应流）见 ``test_agent_mcp_inbound_limits.py``。
"""

from __future__ import annotations

import pytest

from core.agent_types import MCPBudgetProfile
from services.agent_service.mcp import MCPServiceIdentity
from services.agent_service.mcp_budget import (
    JsonDepthScanner,
    MCPBudgetExceeded,
    MCPBudgetGuard,
    MCPDeadline,
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


class TestBudgetAdmission:
    def test_unfrozen_profile_cannot_construct_guard(self) -> None:
        from core.agent_types import AgentErrorCode, AgentServiceError

        with pytest.raises(AgentServiceError) as exc:
            MCPBudgetGuard(MCPBudgetProfile(), identity="svc@1")
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "receive_bytes" in exc.value.sanitized_message


class TestReceiveBytes:
    def test_exact_receive_limit_passes(self) -> None:
        guard = _guard(receive_bytes=100)
        guard.receive_bytes(100)
        assert guard.used("receive_bytes") == 100

    def test_one_byte_over_is_rejected(self) -> None:
        guard = _guard(receive_bytes=25)
        guard.receive_bytes(20)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.receive_bytes(6)
        assert exc.value.family == "receive_bytes"
        # 超限不计入：账本保持最后一次成功计入的值。
        assert guard.used("receive_bytes") == 20


class TestDecodedBytes:
    def test_compression_amplification_is_measured_after_decode(self) -> None:
        guard = _guard(decoded_bytes=10)
        guard.decoded_bytes(10)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.decoded_bytes(1)
        assert exc.value.family == "decoded_bytes"


class TestStructureDepth:
    def test_depth_exactly_at_limit_passes(self) -> None:
        scanner = JsonDepthScanner()
        assert scanner.feed(b'{"a":{"b":{"c":{"d":1}}}}') == 4
        _guard(structure_depth=4).structure_depth(scanner.max_depth)

    def test_depth_over_limit_rejected(self) -> None:
        scanner = JsonDepthScanner()
        scanner.feed(b'{"a":{"b":{"c":{"d":{"e":1}}}}}')
        with pytest.raises(MCPBudgetExceeded) as exc:
            _guard(structure_depth=4).structure_depth(scanner.max_depth)
        assert exc.value.family == "structure_depth"

    def test_braces_inside_strings_do_not_count_as_depth(self) -> None:
        scanner = JsonDepthScanner()
        assert scanner.feed(b'{"a":"{{{{{{"}') == 1
        assert scanner.depth == 0

    def test_chunk_boundaries_do_not_change_depth(self) -> None:
        scanner = JsonDepthScanner()
        for chunk in (b'{"a":', b'{"b":', b"[1,2]", b"}}"):
            scanner.feed(chunk)
        assert scanner.max_depth == 3
        assert scanner.depth == 0


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
    def test_single_result_is_per_result_not_cumulative(self) -> None:
        """单条结果族按单条比较：两条各 50 字节的小结果都通过，超一才拒绝。"""

        guard = _guard(single_result_bytes=50)
        guard.single_result_bytes(50)
        guard.single_result_bytes(50)
        assert guard.used("single_result_bytes") == 50
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.single_result_bytes(51)
        assert exc.value.family == "single_result_bytes"

    def test_retention_accumulates_across_operations(self) -> None:
        guard = _guard(retention_bytes=80)
        guard.retention_bytes(50)
        guard.retention_bytes(30)
        with pytest.raises(MCPBudgetExceeded) as exc:
            guard.retention_bytes(1)
        assert exc.value.family == "retention_bytes"


class TestDeadline:
    def test_deadline_counts_down_from_construction_only(self) -> None:
        """单一总 deadline：不因 progress/分片续期，时间轴只前进。"""

        now = [1000.0]
        deadline = MCPDeadline(seconds=30, monotonic=lambda: now[0])
        assert deadline.remaining() == 30

        now[0] += 10
        assert deadline.remaining() == 20
        assert deadline.expired() is False

        now[0] += 20
        assert deadline.remaining() == 0
        assert deadline.expired() is True


@pytest.fixture(autouse=True)
def _no_side_effects() -> None:
    """本模块不触达真实 SDK/网络/数据库。"""
