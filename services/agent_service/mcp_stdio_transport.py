"""受控 stdio 传输：在 SDK 解析前执行接收字节与结构深度限额（I03）。

为什么需要本模块
----------------
锁定版 fastmcp 的 ``StdioTransport`` 内部使用 ``mcp.client.stdio.stdio_client``：
它逐行读取子进程 stdout 并在库内完成 JSON 解析，调用方拿不到原始字节，也没有
帧长参数。故 stdio 在准入时被拒（``CAPABILITY_UNVERIFIED``），而不是按「预算有值」
放行。本模块以**进程内受控传输**替代该路径：自管子进程，在 SDK 解析前按原始字节
计量，从而为 ``receive_bytes`` / ``structure_depth`` 提供真实插入点。

限额插入点（顺序即合同）
------------------------
:meth:`_BudgetedLineReader.feed` 对每个到达的字节块依次执行：

1. ``guard.receive_bytes(len(chunk))`` —— 超限立即抛 ``MCPBudgetExceeded``，该块不进入缓冲区；
2. ``guard.structure_depth(scanner.feed(chunk))`` —— 超限同样立即抛出；
3. 通过后才并入缓冲区、按 ``\n`` 切行、解码并解析。

因此超限帧的完整消息对象从未被构造，缓冲区上界也由 ``receive_bytes`` 保证。
深度扫描器复用 :class:`JsonDepthScanner`（增量、字符串内括号与转义不计），跨块连续。

超限后的失败语义（fail-closed，不得宣称以原异常类型抵达调用方）
----------------------------------------------------------------
超限时读任务把 ``MCPBudgetExceeded`` 作为 ``Exception`` 值送入读流并停止转发，
随后关闭读流。``ClientSession`` 对读流中的 ``Exception`` 值走 ``message_handler``
（fastmcp 默认处理器不做抛错），故**不以该异常类型抵达调用方**；在途请求由分发器
``_fan_out_closed`` 以 ``CONNECTION_CLOSED`` 立即失败，不会挂起。超限原因经
``on_limit_exceeded(family)`` 回调写入会话诊断（``inbound_limit:<family>``）。

停止与回收语义
--------------
进程创建与停止复用锁定版 SDK ``mcp.client.stdio`` 的私有辅助
（``_get_executable_command`` / ``_create_platform_compatible_process`` /
``_drain_stdout`` / ``_stop_server_process`` / ``_aclose_all``），以保持
Windows Job Object、POSIX 进程组、关闭 stdin → 宽限 → 杀进程树的顺序一致。
版本由 ``pyproject.toml`` 锁定；**升级 fastmcp / mcp 时必须复核这些符号仍存在且
语义未变**，复核结论更新在本段。收尾在 ``CancelScope(shield=True)`` 内完成，
``CancelledError`` 原样传播（R2）。

退出码
------
子进程由本模块自管，``_stop_server_process`` 之后 ``process.returncode`` 可读，
经 ``on_exit_status`` 回调写入诊断；仍未观测到则保持 ``None``（不伪造 0）。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import TextIO, Unpack

import anyio
import anyio.lowlevel
import mcp_types as types
from fastmcp.client.transports.base import ClientTransport, SessionKwargs, TransportOptions
from mcp import ClientSession
from mcp.client.stdio import (
    _WRITER_FLUSH_TIMEOUT,
    _aclose_all,
    _create_platform_compatible_process,
    _drain_stdout,
    _get_executable_command,
    _stop_server_process,
)
from mcp.os.win32.utilities import ServerProcess
from mcp.shared.message import SessionMessage

from services.agent_service.mcp_budget import JsonDepthScanner, MCPBudgetExceeded, MCPBudgetGuard

logger = logging.getLogger(__name__)


class _BudgetedLineReader:
    """字节块 → 消息：限额判定严格先于切行与解析。

    非线程安全，仅在读任务内使用；``feed`` 抛 ``MCPBudgetExceeded`` 后调用方不得
    继续喂入（连接已按 fail-closed 拆除）。
    """

    def __init__(self, guard: MCPBudgetGuard, *, encoding: str = "utf-8", errors: str = "strict") -> None:
        self._guard = guard
        self._encoding = encoding
        self._errors = errors
        self._scanner = JsonDepthScanner()
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[SessionMessage | Exception]:
        """计入限额后返回本块切出的完整行消息（不完整尾行留在缓冲区）。"""

        # 1/2：限额先于切行与解析——超限块不进入缓冲区，消息对象从未被构造。
        self._guard.receive_bytes(len(chunk))
        self._guard.structure_depth(self._scanner.feed(chunk))
        self._buffer += chunk
        messages: list[SessionMessage | Exception] = []
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                return messages
            line = self._buffer[:index]
            self._buffer = self._buffer[index + 1 :]
            messages.append(self._parse_line(line))

    def _parse_line(self, line: bytes) -> SessionMessage | Exception:
        """与 SDK ``_parse_line`` 同语义：解析失败以异常值返回，不抛出。"""

        try:
            text = line.decode(self._encoding, errors=self._errors)
        except UnicodeDecodeError as exc:
            return exc
        try:
            message = types.jsonrpc_message_adapter.validate_json(text, by_name=False)
        except ValueError as exc:
            # 只记类型，不记正文（R9）。
            logger.warning("Failed to parse JSONRPC message from MCP server: %s", type(exc).__name__)
            return exc
        return SessionMessage(message)


class BudgetedStdioTransport(ClientTransport):
    """自管 stdio 子进程 + 解析前入站限额的 ``ClientTransport`` 实现。

    ``connect_session`` 复刻 SDK ``stdio_client`` 的进程与收尾语义，只在读路径前
    插入 :class:`_BudgetedLineReader`；连接上下文退出即停进程（等价 ``keep_alive=False``），
    ``close`` 无跨连接状态可关。
    """

    def __init__(
        self,
        command: str,
        args: list[str],
        *,
        guard: MCPBudgetGuard,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        errlog: TextIO | None = None,
        encoding: str = "utf-8",
        encoding_error_handler: str = "strict",
        on_exit_status: Callable[[int | None], None] | None = None,
        on_limit_exceeded: Callable[[str], None] | None = None,
    ) -> None:
        self._command = command
        self._args = args
        self._guard = guard
        self._env = env
        self._cwd = cwd
        self._errlog = errlog if errlog is not None else sys.stderr
        self._encoding = encoding
        self._encoding_error_handler = encoding_error_handler
        self._on_exit_status = on_exit_status
        self._on_limit_exceeded = on_limit_exceeded

    @asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncGenerator[ClientSession]:
        options = transport_options or TransportOptions()
        command = await _get_executable_command(self._command)
        process: ServerProcess = await _create_platform_compatible_process(
            command=command,
            args=list(self._args),
            env=self._env,
            errlog=self._errlog,
            cwd=self._cwd,
        )

        read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)
        reader = _BudgetedLineReader(self._guard, encoding=self._encoding, errors=self._encoding_error_handler)
        shutting_down = False
        writer_done = anyio.Event()

        async def stdout_reader() -> None:
            assert process.stdout, "Opened process is missing stdout"
            try:
                async with read_stream_writer:
                    try:
                        while True:
                            try:
                                chunk = await process.stdout.receive()
                            except anyio.EndOfStream:
                                return
                            try:
                                messages = reader.feed(chunk)
                            except MCPBudgetExceeded as exc:
                                # fail-closed：记录原因 → 把错误作为值交给会话 → 停止转发并关闭读流。
                                self._report_limit(exc.family)
                                await read_stream_writer.send(exc)
                                return
                            for message in messages:
                                await read_stream_writer.send(message)
                    finally:
                        # 限额超限后的 drain 由上下文收尾关闭管道后结束（与 SDK 同路径）。
                        await _drain_stdout(process)
            except anyio.ClosedResourceError:
                pass  # 自身收尾在读之下关闭了流
            except (anyio.BrokenResourceError, ConnectionError):
                if not shutting_down:
                    logger.warning("Reading from the MCP server's stdout failed mid-session")

        async def stdin_writer() -> None:
            assert process.stdin, "Opened process is missing stdin"
            try:
                async with write_stream_reader:
                    async for session_message in write_stream_reader:
                        payload = session_message.message.model_dump_json(by_alias=True, exclude_unset=True)
                        data = (payload + "\n").encode(self._encoding, errors=self._encoding_error_handler)
                        await process.stdin.send(data)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
                # 服务端可能仍存活：关闭读流让会话看到连接结束，而不是让请求悬挂。
                await read_stream_writer.aclose()
            finally:
                writer_done.set()

        async def shutdown() -> None:
            """停止流量 → 有界 flush → 停进程树 → 释放流 → 回收退出码。"""

            read_stream.close()
            write_stream.close()
            with anyio.move_on_after(_WRITER_FLUSH_TIMEOUT) as flush_scope:
                await writer_done.wait()
            if flush_scope.cancelled_caught:
                await anyio.lowlevel.cancel_shielded_checkpoint()
            await _stop_server_process(process)
            await _aclose_all(read_stream, write_stream, read_stream_writer, write_stream_reader)
            await anyio.lowlevel.checkpoint()
            self._report_exit_status(process.returncode)

        async with anyio.create_task_group() as tg:
            tg.start_soon(stdout_reader)
            tg.start_soon(stdin_writer)
            try:
                async with options.session_class(read_stream, write_stream, **session_kwargs) as session:
                    yield session
            finally:
                shutting_down = True
                # 收尾必须在调用方取消下也完成，否则子进程泄漏；内部每个等待都有界。
                with anyio.CancelScope(shield=True):
                    await shutdown()
                tg.cancel_scope.cancel()
        # 取消经 throw() 落地；让出一次以完成 3.11 的覆盖率同步（同 SDK）。
        await anyio.lowlevel.cancel_shielded_checkpoint()

    async def close(self) -> None:
        """无跨连接状态可关：进程生命周期由 :meth:`connect_session` 持有。"""

    def __repr__(self) -> str:
        return f"<BudgetedStdioTransport(command={self._command!r}, args={self._args!r})>"

    # -- 回调（缺省即无操作） ---------------------------------------------

    def _report_limit(self, family: str) -> None:
        if self._on_limit_exceeded is not None:
            self._on_limit_exceeded(family)

    def _report_exit_status(self, code: int | None) -> None:
        if self._on_exit_status is not None:
            self._on_exit_status(code)
