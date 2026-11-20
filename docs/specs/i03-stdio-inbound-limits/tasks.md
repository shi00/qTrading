# I03 任务清单

> 前置：`spec.md` 已获用户批准。按序执行，每项完成即更新状态。

## T1 新模块与限额核心

- [x] T1.1 新建 `services/agent_service/mcp_stdio_transport.py`：`_BudgetedLineReader`（见 spec §2.1 A）
- [x] T1.2 同文件实现 `BudgetedStdioTransport(ClientTransport)`（见 spec §2.1 B）
- [x] T1.3 模块 docstring 写明：插入点、复用 SDK 私有辅助的版本锁定前提、超限失败语义、`CancelledError` 不吞

验证：`ruff check` 通过；`pyright`（改动文件）0 errors

## T2 生产接线与准入登记

- [x] T2.1 `mcp_fastmcp.py`：`_transport` STDIO 分支改用 `BudgetedStdioTransport`
- [x] T2.2 `MCPSessionDiagnostics.set_exit_status`；stdio 退出码经回调写入
- [x] T2.3 `mcp_fastmcp.py` 模块 docstring 更新（删除 stdio 无插入点表述）
- [x] T2.4 `mcp_budget.py`：新增 `_STDIO_INBOUND_BINDINGS` 并登记到 `MCP_INBOUND_LIMIT_BINDINGS[STDIO]`，删除禁用注释，修正 HTTP 注释

验证：`missing_pre_parse_bindings(STDIO, 已冻结预算) == ()`（由单测覆盖）

## T3 单元测试

- [x] T3.1 新建 `tests/unit/services/test_agent_mcp_stdio_transport.py`（7 个用例）
- [x] T3.2 改写 `tests/unit/test_agent_mcp_inbound_limits.py` 的 stdio 准入三条用例与文件头 docstring
- [x] T3.3 grep `CAPABILITY_UNVERIFIED` + `STDIO` / `exit_status`：其余命中均为审核/预算未冻结等无关用例，无需改动

验证：`pytest tests/unit/services/test_agent_mcp_stdio_transport.py tests/unit/test_agent_mcp_inbound_limits.py -q` → 17 passed

## T4 真实子进程端到端

- [x] T4.1 既有 discover/call 用例保持通过（工具清单同步为 echo/big/deep）
- [x] T4.2 新增超限大帧拒绝用例（`asyncio.timeout(10)`，断言不挂起 + 诊断含 `inbound_limit:receive_bytes`）
- [x] T4.3 新增深 JSON 超限用例（`structure_depth`）
- [x] T4.4 新增 `exit_status` 可观测用例

验证：`pytest tests/integration/test_agent_mcp_fastmcp_transport.py -q` → 8 passed

## T5 文档

- [x] T5.1 `docs/debt/known-technical-debt.md` 的 `P2-AgentMCP-Residual-Risks`（索引行 + 条目）：删除已收敛项（stdio 接收计量、exit_status 不可观测），补 `retention_bytes` 无释放与「超限异常类型不保证原样抵达」残余

## T6 门禁与交付

- [x] T6.1 `ruff check .` / `ruff format --check .` / `python scripts/check_redlines.py` 全部通过（WARNING 为本仓既有报告模式项，改动文件无命中）
- [x] T6.2 `pytest tests/unit/services` + 全部 `test_agent_mcp_*` → 1533 passed, 12 skipped
- [x] T6.3 `pytest tests/integration/test_agent_mcp_fastmcp_transport.py -q` → 8 passed
- [x] T6.4 `pyright`：改动文件 0 errors；全仓 3 个**既有** error（`embedded_pg_maintenance_service.py` 的 POSIX-only `os.getpgid/killpg`、`test_agent_leaf_agent.py:156`），均在未改动文件，与本次变更无关
- [ ] T6.5 `git commit --no-verify` + `git push origin HEAD:feat/agent-runtime-v2`

## 完成定义

spec §4 的 6 条验收标准全部满足；无法运行的验证项在交付说明中逐条写明原因。
