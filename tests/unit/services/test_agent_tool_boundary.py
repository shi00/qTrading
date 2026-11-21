"""I43 研究边界派发端口：来源事实摘要与禁用阻断。"""

from __future__ import annotations

import pytest

from services.agent_service.tool_boundary import (
    BLOCKED_REASON_SOURCE_DISABLED,
    DaoToolBoundaryPort,
    boundary_sources_digest,
)

pytestmark = pytest.mark.unit

_SCOPE = ("user-1", "execution-1")


class _Reader:
    def __init__(
        self,
        active: frozenset[tuple[str, str]] = frozenset(),
        disabled: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self.active = active
        self.disabled = disabled
        self.calls: list[tuple[str, str]] = []

    async def read_boundary_source_keys(
        self, *, user_scope: str, execution_id: str
    ) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
        self.calls.append((user_scope, execution_id))
        return self.active, self.disabled


def _port(reader: _Reader) -> DaoToolBoundaryPort:
    return DaoToolBoundaryPort(dao=reader, user_scope=_SCOPE[0], execution_id=_SCOPE[1])


async def test_no_sources_is_not_blocked_and_reads_run_scope() -> None:
    reader = _Reader()
    attestation = await _port(reader).attest()
    assert attestation.blocked_reason is None
    assert attestation.digest == boundary_sources_digest(frozenset(), frozenset())
    assert reader.calls == [_SCOPE]


async def test_active_source_with_disabled_version_blocks() -> None:
    key = ("news-1", "v1")
    attestation = await _port(_Reader(frozenset({key}), frozenset({key}))).attest()
    assert attestation.blocked_reason == BLOCKED_REASON_SOURCE_DISABLED


async def test_disabled_source_not_registered_in_execution_does_not_block() -> None:
    """只有「已登记来源 ∩ 已禁用」才阻断；与能力层同一判据。"""

    attestation = await _port(_Reader(frozenset({("news-1", "v1")}), frozenset({("news-2", "v1")}))).attest()
    assert attestation.blocked_reason is None


async def test_digest_is_order_insensitive_and_changes_with_facts() -> None:
    first = boundary_sources_digest(frozenset({("a", "1"), ("b", "2")}), frozenset())
    second = boundary_sources_digest(frozenset({("b", "2"), ("a", "1")}), frozenset())
    changed = boundary_sources_digest(frozenset({("a", "1"), ("b", "2")}), frozenset({("b", "2")}))
    assert first == second
    assert first != changed
