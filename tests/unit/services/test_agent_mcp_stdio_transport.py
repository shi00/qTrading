"""I03：stdio 入站限额的解析前插入点（``_BudgetedLineReader``）单测。

不依赖子进程：直接按字节块驱动读入器，证明限额判定严格先于切行/解码/解析，
且超限块不进入缓冲区、不产出任何消息。
"""

from __future__ import annotations

import asyncio

import pytest

from core.agent_types import AgentServiceError, MCPBudgetProfile
from mcp.shared.message import SessionMessage
from services.agent_service.mcp_budget import MCPBudgetExceeded, MCPBudgetGuard
from services.agent_service.mcp_stdio_transport import _BudgetedLineReader

_IDENTITY = "svc-stdio@1"
_ECHO_RESPONSE = b'{"jsonrpc":"2.0","id":1,"result":{}}\n'


def _budget(**overrides: int | None) -> MCPBudgetProfile:
    values: dict[str, int | None] = {
        "receive_bytes": 1000,
        "decoded_bytes": 2000,
        "structure_depth": 8,
        "catalog_items": 10,
        "catalog_bytes": 10_000,
        "schema_bytes": 10_000,
        "single_result_bytes": 10_000,
        "retention_bytes": 20_000,
    }
    values.update(overrides)
    return MCPBudgetProfile(**values)


def _reader(**overrides: int | None) -> tuple[_BudgetedLineReader, MCPBudgetGuard]:
    guard = MCPBudgetGuard(_budget(**overrides), identity=_IDENTITY)
    return _BudgetedLineReader(guard), guard


class TestReceiveBytes:
    def test_single_frame_over_budget_is_refused_before_buffer(self) -> None:
        reader, guard = _reader(receive_bytes=10)

        with pytest.raises(MCPBudgetExceeded) as exc:
            reader.feed(_ECHO_RESPONSE)

        assert exc.value.family == "receive_bytes"
        # 拒绝发生在计数之前：超限块既未计入也未进入缓冲区，消息对象从未构造。
        assert guard.used("receive_bytes") == 0

    def test_oversized_chunk_is_refused_again(self) -> None:
        """被拒的块不计数也不进缓冲区：同样超限的块再次喂入仍在同一族被拒。"""

        reader, _guard = _reader(receive_bytes=10)
        with pytest.raises(MCPBudgetExceeded):
            reader.feed(_ECHO_RESPONSE)
        with pytest.raises(MCPBudgetExceeded) as exc:
            reader.feed(_ECHO_RESPONSE)
        assert exc.value.family == "receive_bytes"


class TestStructureDepth:
    def test_deep_json_is_refused_before_the_frame_completes(self) -> None:
        reader, _guard = _reader(receive_bytes=1000, structure_depth=3)

        assert reader.feed(b'{"a":') == []
        assert reader.feed(b'{"b":') == []
        with pytest.raises(MCPBudgetExceeded) as exc:
            reader.feed(b'{"c":{"d":1}')

        assert exc.value.family == "structure_depth"
        # 触发块未进入缓冲区（消息从未成形）；扫描器已记录超限深度，后续喂入继续被拒——
        # 调用方必须拆除连接，不得继续读取。
        with pytest.raises(MCPBudgetExceeded) as later:
            reader.feed(b"}}\n")
        assert later.value.family == "structure_depth"

    def test_brackets_inside_strings_do_not_count(self) -> None:
        reader, _guard = _reader(receive_bytes=1000, structure_depth=2)
        line = b'{"jsonrpc":"2.0","id":1,"result":{"text":"[[[[{{{{"}}\n'
        messages = reader.feed(line)

        assert len(messages) == 1
        assert isinstance(messages[0], SessionMessage)


class TestLineAssembly:
    def test_split_frame_is_emitted_only_when_line_completes(self) -> None:
        reader, _guard = _reader()

        assert reader.feed(b'{"jsonrpc":"2.0","id":1,') == []
        messages = reader.feed(b'"result":{}}\n')

        assert len(messages) == 1
        assert isinstance(messages[0], SessionMessage)

    def test_invalid_json_is_returned_as_value_and_later_lines_still_parse(self) -> None:
        reader, _guard = _reader()

        messages = reader.feed(b"not-json\n" + _ECHO_RESPONSE)

        assert len(messages) == 2
        assert isinstance(messages[0], Exception)
        assert isinstance(messages[1], SessionMessage)


class TestFailureSemantics:
    def test_exceed_is_not_a_cancellation(self) -> None:
        """超限是预算错误而非取消：不得以 ``CancelledError`` 形式呈现（R2 语义边界）。"""

        reader, _guard = _reader(receive_bytes=10)
        with pytest.raises(MCPBudgetExceeded) as exc:
            reader.feed(_ECHO_RESPONSE)

        assert isinstance(exc.value, AgentServiceError)
        assert not isinstance(exc.value, asyncio.CancelledError)
