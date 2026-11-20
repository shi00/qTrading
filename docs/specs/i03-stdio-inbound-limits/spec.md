# I03 规格：stdio MCP 的解析前入站限额与准入放开

> 状态：已批准、已实现（验证结果见 tasks.md）
> 工作树：`.worktrees/agent-prod-wiring`（分支 `fix/agent-prod-wiring`，满足 R18 隔离要求）
> 目标分支：`feat/agent-runtime-v2`（仅允许 `git push origin HEAD:feat/agent-runtime-v2`）

## 1. 问题

`services/agent_service/mcp_budget.py` 的 `MCP_INBOUND_LIMIT_BINDINGS[STDIO]` 为空元组，`require_pre_parse_bindings` 因此在保存/启连时对 stdio 抛 `CAPABILITY_UNVERIFIED`：任何带 `receive_bytes` / `structure_depth` 的本地 stdio 服务都无法准入。

根因：锁定版 SDK（fastmcp 4.1.6 / mcp SDK）的 `StdioTransport` 内部走 `mcp.client.stdio.stdio_client`，逐行读取子进程 stdout 并在库内完成 JSON 解析，调用方拿不到原始字节，也没有帧长参数。因此不能只删限额族放开准入（那等于把「有限额」伪装成「已限流」，违反 R21 的同类语义）。

## 2. 方案（已定，无需再选型）

进程内受控 stdio 传输：自管子进程 + 在 SDK 解析前按原始字节计量，不用独立中继进程。

### 2.1 新增模块 `services/agent_service/mcp_stdio_transport.py`

包含两个公开符号：

**A. `_BudgetedLineReader`**（可单测的限额核心）

```
__init__(guard: MCPBudgetGuard, *, encoding="utf-8", errors="strict")
feed(chunk: bytes) -> list[SessionMessage | Exception]
```

`feed` 的执行顺序（顺序即合同）：

1. `guard.receive_bytes(len(chunk))` —— 超限立刻抛 `MCPBudgetExceeded("receive_bytes")`，该块**不进入缓冲区**；
2. `guard.structure_depth(scanner.feed(chunk))` —— 超限立刻抛 `MCPBudgetExceeded("structure_depth")`，同样不进入缓冲区；
3. 通过后才把块并入内部 `_buffer`，按 `b"\n"` 切出完整行；
4. 每行用配置编码解码（默认 utf-8 / strict），调用 `mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)` 解析，成功返回 `SessionMessage(message)`，失败返回异常值（与 SDK `_parse_line` 语义一致）；
5. 不完整的尾行留在缓冲区等下一块。

性质（测试将断言）：
- 限额判定严格先于切行与解析，超限帧的完整消息对象从未被构造；
- 缓冲区上界由 `receive_bytes` 保证（超限的块在入缓冲区之前就被拒）；
- 深度扫描器跨块连续，字符串内括号与转义不计（复用 `JsonDepthScanner`），按 `max_depth` 判定，不重复解析。

**B. `BudgetedStdioTransport(ClientTransport)`**

```
__init__(command, args, *, env, cwd, guard, errlog,
         on_exit_status: Callable[[int | None], None] | None = None,
         on_limit_exceeded: Callable[[str], None] | None = None)
connect_session(*, transport_options=None, **session_kwargs)  # 唯一抽象方法
close()  # no-op：进程生命周期由 connect_session 的结构化并发持有
```

`connect_session` 完整复刻锁定版 SDK `mcp.client.stdio.stdio_client` 的进程与收尾语义，只替换读路径：

- 进程创建：`await _get_executable_command(command)` → `await _create_platform_compatible_process(command, args, env, errlog, cwd)`（Windows Job Object / POSIX `start_new_session`、`CREATE_NO_WINDOW`）；
- 读任务 `stdout_reader`：`process.stdout.receive()` 拿字节块 → `_BudgetedLineReader.feed(chunk)` → `read_stream_writer.send(...)`；循环结束（含超限）后 `finally: await _drain_stdout(process)`；
- 写任务 `stdin_writer`：与 SDK 相同（`model_dump_json(by_alias=True, exclude_unset=True)` + `"\n"`，写 `process.stdin`）；
- 握手/会话：`transport_options.session_class(read_stream, write_stream, **session_kwargs)`，在 `connect_session` 内部进入其上下文，再 `yield` 给 fastmcp；
- 收尾：照抄 SDK `shutdown()` 顺序 —— 关 `read_stream`/`write_stream` → 有界等待写任务 flush → `await _stop_server_process(process)`（关 stdin → 2s 宽限 → kill 进程树 → `close_process_job`）→ 关闭全部流 → `checkpoint`；整个收尾在 `anyio.CancelScope(shield=True)` 内；退出后 `on_exit_status(process.returncode)`。

失败语义（fail-closed，明确写入 docstring）：
- `MCPBudgetExceeded` 在 `stdout_reader` 内抛出，经 task group 传播，连接被拆除；读流随之关闭，在途请求立即失败而不是挂起；
- 同时在抛出前调用 `on_limit_exceeded(family)`，由上层写入 `MCPSessionDiagnostics.record(f"inbound_limit:{family}")`；
- **不承诺**以 `MCPBudgetExceeded` 原类型抵达调用方：`ClientSession` 对读流中的 `Exception` 值走 `message_handler`，fastmcp 默认处理器会吞掉，故不采用「送 Exception 值」作为唯一手段；
- `asyncio.CancelledError` 原样传播，不吞（R2）。

依赖说明：复用 `mcp.client.stdio` 的私有辅助（`_get_executable_command` / `_create_platform_compatible_process` / `_stop_server_process` / `_drain_stdout`）以保持停止与回收语义一致；版本由 `pyproject.toml` 锁定，**升级 fastmcp/mcp 时必须复核这些符号仍存在且语义未变**，复核结论写入模块 docstring。

### 2.2 接线 `services/agent_service/mcp_fastmcp.py`

- `FastMCPClientFactory._transport` 的 `MCPTransportKind.STDIO` 分支：由 `StdioTransport(...)` 改为 `BudgetedStdioTransport(command, list(args), env=stdio_env.env, cwd=transport.cwd, guard=inbound, errlog=cast("TextIO", drain), on_exit_status=diagnostics.set_exit_status, on_limit_exceeded=lambda family: diagnostics.record(f"inbound_limit:{family}"))`；
- `MTPSessionDiagnostics` 增加 `set_exit_status(self, code: int | None) -> None`（仅在 `code is not None` 时写入，保持「不可观测即 None，不伪造 0」）；
- `keep_alive=False` 的等价行为由 `connect_session` 退出即停进程保证，不再传该参数；
- 模块 docstring：删除「stdio 无前置插入点/已被准入拒绝」段落，改为描述本插入点；「仍属残留」中删除「stdio 入站无插入点」，保留「限额族不保证原异常类型抵达调用方」这一条。

### 2.3 准入登记 `services/agent_service/mcp_budget.py`

- 新增 `_STDIO_INBOUND_BINDINGS`，两条记录：
  - `family="receive_bytes"`，`insertion_point="services.agent_service.mcp_stdio_transport._BudgetedLineReader.feed"`，`source` 写明「自管子进程 stdout 原始字节块，先于 SDK 逐行解析计入」；
  - `family="structure_depth"`，同插入点，`source` 写明「同一插入点以 JsonDepthScanner 逐字节判定嵌套深度，发生在 SDK JSON 解析之前」；
- `MCP_INBOUND_LIMIT_BINDINGS[STDIO]` 指向该元组，删除「stdio 保持禁用」注释；
- `_HTTP_INBOUND_BINDINGS` 上方注释删除「stdio 无任何前置插入点」的表述，改为仅说明 HTTP 插入点。

## 3. 测试与验收

### 3.1 单元测试

新增 `tests/unit/services/test_agent_mcp_stdio_transport.py`（不依赖子进程）：

| 用例 | 断言 |
|------|------|
| 大帧：单块超过 `receive_bytes` | `feed` 抛 `MCPBudgetExceeded`，`family == "receive_bytes"`，返回值为空（未构造消息） |
| 深 JSON：多块累计超 `structure_depth` | 抛 `MCPBudgetExceeded`，`family == "structure_depth"`，且触发块未进入缓冲区 |
| 跨块行边界 | 同一 JSON 分成多块喂入，只在行完整时产出 1 条 `SessionMessage` |
| 非法 JSON | 返回 `Exception` 值而非抛出；后续行仍可正常解析 |
| 限额先于解析 | 超限块内即使含完整合法 JSON，也不产出任何消息 |
| 超限非取消 | `MCPBudgetExceeded` 是 `AgentServiceError`，不是 `CancelledError`（R2 语义边界；传输层取消传播由收尾 shield 保证，集成用例断言超限不以取消形式呈现） |

更新 `tests/unit/test_agent_mcp_inbound_limits.py`：

- `test_stdio_has_no_pre_parse_insertion_point` 改为 `test_stdio_binds_both_pre_parse_families`：`missing_pre_parse_bindings(STDIO, _budget()) == ()`，两条 binding 的 `insertion_point` / `source` 非空；
- `test_transport_without_insertion_point_is_refused_at_save` / `..._at_connect` 改为 stdio 准入通过（`save_config` 落库、`connect` 调 `factory.open` 各一次）；
- 文件头 docstring 第 5 条同步改写。

### 3.2 集成测试（真实子进程）

在 `tests/integration/test_agent_mcp_fastmcp_transport.py` 增加：

1. `list_tools` / `call_tool` 端到端仍通过（既有用例保持）；
2. 超限大帧：最小 stdio 服务在校验期后主动输出一条超过 `receive_bytes` 的响应，调用方得到异常且 `session.diagnostics.last_error` 含 `inbound_limit:receive_bytes`；不出现挂起（用 `asyncio.timeout` 包裹断言）；
3. 深 JSON 超限同理（`structure_depth`）；
4. `session.diagnostics.exit_status` 在 `aclose()` 后为 `int`（自管进程可观测）。

### 3.3 其他受影响测试

grep `CAPABILITY_UNVERIFIED` + `STDIO`、`exit_status`，逐条确认：仅上述两文件需要改；其余 stdio 配置用例（codec/probe/service/env/stderr/deadline）不受准入变化影响，若断言被连带打破则按新合同修正，不得删除断言。

### 3.4 验证命令（顺序即门禁）

```
d:/workspace/qTrading/.venv/Scripts/python.exe -m ruff check .
d:/workspace/qTrading/.venv/Scripts/python.exe -m ruff format --check .
d:/workspace/qTrading/.venv/Scripts/python.exe -m pyright --pythonpath d:/workspace/qTrading/.venv/Scripts/python.exe
d:/workspace/qTrading/.venv/Scripts/python.exe scripts/check_redlines.py
d:/workspace/qTrading/.venv/Scripts/python.exe -m pytest tests/unit/services tests/unit/test_agent_mcp_inbound_limits.py -q
d:/workspace/qTrading/.venv/Scripts/python.exe -m pytest tests/integration/test_agent_mcp_fastmcp_transport.py -q
```

无法运行的项必须如实说明，不得声称通过。

### 3.5 文档同步

- `mcp_fastmcp.py` / `mcp_budget.py` 模块 docstring（见 2.2 / 2.3）；
- `docs/debt/known-technical-debt.md` 的 `P2-AgentMCP-Residual-Risks`：删除「stdio 入站族无计量入口」「exit_status 恒 None」两项已收敛描述，保留并改写剩余残余（错误分类、单一账本、retention 释放等）。

## 4. 验收标准

1. 已冻结 `receive_bytes` + `structure_depth` 的 stdio 配置可 `save_config` 并可 `connect`；
2. 真实子进程可完成 `list_tools` / `call_tool`；
3. 超限帧/深 JSON 在解析前被拒，连接被拆除，`diagnostics` 记录 `inbound_limit:<family>`，调用不悬挂；
4. `exit_status` 在 stdio 关闭后可观测，HTTP 仍为 `None`（不伪造 0）；
5. 取消原样传播（R2），无新增 `type: ignore`（R3），SQL 无改动（R4），业务逻辑变更配套单测（R19）；
6. 3.4 全部命令通过。

## 5. 明确不做

- 不实现自动重连、不合并会话内 guard 与连接管理器 guard 的账本（属既有 P2 残余，另行处理）；
- 不改 HTTP/SSE 路径；
- 不为 stdio 增加额外的帧长参数或协议扩展；
- 不新增预算数值或降低限额族。
