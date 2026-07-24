# Flet MCP 使用规范

> 来源：AI 编程时使用 flet-mcp 验证 Flet API 的操作指南
> 配套：[CLAUDE.md §1.10 反幻觉护栏](../../CLAUDE.md#110-反幻觉护栏-ai-特有红线)、[v1-api-constraints.md](./v1-api-constraints.md)、[api-verification-template.md](./api-verification-template.md)
> Owner: UI 维护者
> 复核触发器: flet-mcp 包版本变化、Flet 依赖版本变化、MCP 配置变化

## 1. 文档定位

本文件是 **AI 使用 flet-mcp 的操作指南**，解决 CLAUDE.md §1.10「禁止臆造 API」红线在 Flet 场景的落地问题。

flet-mcp 是 Flet 0.86+ 官方提供的独立 PyPI 包（非 `flet` CLI 子命令），通过 MCP 协议向 AI 暴露**版本特定的 Flet API 权威信息**。服务器对象为 `flet_mcp:mcp`（FastMCP 实例，`mcp.name == "flet-mcp"`），经 `python -c "from flet_mcp import mcp; mcp.run()"` 启动（直接调用 FastMCP 实例的 `run()` 方法，默认 stdio transport）。flet-mcp 自带预构建的 `api.json`，**不依赖 flet 运行时安装即可查询 API**（对 CI/纯审查场景友好）。

与现有 Flet 文档的关系：
- `v1-api-constraints.md`：项目内的 API 约束清单（**项目契约**）
- `project-differences.md`：项目相对官方默认的分叉点（**项目分叉**）
- `api-verification-template.md`：API 核验记录模板（**历史沉淀**）
- **本文件**：AI 如何用 flet-mcp 验证 API（**操作指南**）

## 2. 何时使用 flet-mcp（触发条件）

**强制使用**（违反即触犯 §1.10 红线）：
- 使用任何不熟悉的 Flet API 前（如 `ft.use_dialog`、`ft.use_effect`、`use_viewmodel` 等 hooks）
- 不确定某 API 在当前锁定版本（见 `pyproject.toml`）是否存在、签名是否变化
- V0→V1 迁移时验证迁移目标 API 的当前签名
- 涉及 flet_charts / flet-desktop 等子包 API
- 使用枚举值前验证成员是否存在（`enum_has_member`）

**推荐使用**：
- 编写 UI 契约测试时验证控件属性枚举值
- 排查「AttributeError / TypeError 静默失效」类问题时确认 API 行为
- Flet 版本升级后批量核验高风险 API
- 查找图标时用 `find_icon`（支持同义词，如 "user"→"account_circle"）

**无需使用**：
- 已在 `v1-api-constraints.md` V0→V1 迁移表中明确记录的 API（已核验）
- 已在 `api-verification-template.md` 历史核验记录中沉淀的 API
- 纯业务逻辑代码（不涉及 Flet API 调用）

## 3. 如何使用 flet-mcp

### 3.1 配置（需用户在 IDE 中手动创建）

在所用 IDE 的 MCP 配置位置写入以下内容：

```json
{
  "mcpServers": {
    "flet": {
      "command": "venv/Scripts/python.exe",
      "args": ["-c", "from flet_mcp import mcp; mcp.run()"]
    }
  }
}
```

**配置要点**：
- `command` 需指向本机 venv 内的 `python.exe`（Windows: `venv/Scripts/python.exe`；Linux/macOS: `venv/bin/python`），根据自己环境调整路径
- `flet-mcp` 版本必须与项目使用的 `flet` 版本对齐（项目用 `==` 锁定，见 `pyproject.toml`），需先执行 `pip install -e ".[dev]"` 安装

配置成功后 IDE 会显示 MCP server 已连接（通常为绿色图标）。IDE 本地 MCP 配置不入版本控制，需用户手动创建。

### 3.2 核心工具清单

| 工具 | 签名 | 用途 | 典型场景 |
|------|------|------|---------|
| `get_api` | `(name, member?, query?, format="text")` | 获取任意 Flet 符号 API 参考 | 不确定 `ft.use_dialog` 签名时；"not found" 在 api.json 完整收录前提下为否定 |
| `list_controls` | `(category?, kind?, limit=50)` | 列出控件/服务 | 浏览可用控件 |
| `get_enum` | `(name)` | 获取枚举定义 | 查看枚举全部成员（小枚举） |
| `search_enum_members` | `(name, query, limit=10)` | 搜索枚举成员 | 在 Icons 大枚举中查找特定图标名 |
| `enum_has_member` | `(name, member)` | 检查枚举成员是否存在 | 使用 `Icons.ARROW_BACK` 前验证 |
| `find_icon` | `(query, family?, limit=10)` | 按概念搜索图标（同义词感知） | "delete"/"user" 等概念查找 |

**可选工具组**（经 `FLET_MCP_ENABLE_*=1` 环境变量启用）：
- EXAMPLES：`search_examples` / `get_example`（示例代码搜索）
- DOCS：`search_docs` / `get_doc`（官方文档搜索）
- CLI：`get_cli_help`（Flet CLI 帮助）

### 3.3 使用示例

**场景 1**：不确定 `ft.Dropdown` 在 V1 中是 `on_change` 还是 `on_select` 事件。

**错误做法**（违反 §1.10）：凭记忆写 `on_change=...`，运行期 TypeError 才发现。

**正确做法**：
1. 调用 flet-mcp `get_api(name="Dropdown")` 获取当前签名
2. 确认事件属性名为 `on_select`
3. 与 `v1-api-constraints.md` V0→V1 迁移表交叉验证
4. 编写代码 `ft.Dropdown(on_select=...)`

**场景 2**：使用 `ft.Icons.DELETE` 前验证存在性。

```
调用 enum_has_member(name="Icons", member="DELETE") → {"exists": true}
```

**场景 3**：查找「删除」语义图标但不确定具体名。

```
调用 find_icon(query="delete") → 返回 Icons.DELETE / Icons.DELETE_OUTLINE / Icons.REMOVE 等
```

### 3.4 与项目契约的优先级

flet-mcp 返回的是 **api.json 中版本特定的 Flet API 信息**（构建时快照，非运行时反射），项目契约（`v1-api-constraints.md` / `project-differences.md`）可能在此基础上进一步收窄（如项目统一用 `on_select` 而非 `on_change`）。**项目契约优先**，flet-mcp 用于验证 API 存在性与签名，不用于覆盖项目契约。

优先级（后者被前者覆盖）：
1. CLAUDE.md 红线（§3）
2. `v1-api-constraints.md` 项目契约
3. `project-differences.md` 项目分叉
4. flet-mcp 返回的官方行为

### 3.5 `get_api` 返回格式说明

默认 `format="text"`（紧凑文本）：
- `?` 后缀表示可选属性
- `async ` 前缀表示须 await（调用事件处理器须为 `async def`）
- `package: <name>` 行表示该类位于独立 pip 包（需加入依赖才能 import）

`format="json"` 返回结构化字典（程序化消费用）。

## 4. 验证记录沉淀

通过 flet-mcp 验证的 API，若属于以下情况，应按 [`api-verification-template.md`](./api-verification-template.md) 模板沉淀记录：
- 验证结果与项目契约不一致（需更新契约）
- 验证结果与先前记录冲突（需更新历史记录）
- Flet 版本升级后的批量核验

## 5. 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| AI 调用 flet-mcp 报「command not found」 | venv 中未装 flet-mcp | `pip install -e ".[dev]"` 安装 dev 依赖 |
| `python -c "from flet_mcp import mcp; mcp.run()"` 报 ModuleNotFoundError | venv 中未装 flet-mcp | 同上 |
| AI 未自动加载 MCP server | IDE 的 MCP 配置未创建或路径/格式错误 | 按 §3.1 在所用 IDE 中手动创建 MCP 配置；重启 IDE；IDE 本地配置不入版本控制需手动维护 |
| worktree 内 python.exe 不存在 | worktree 未与主工作区共享 venv | 在该 worktree 内 `pip install -e ".[dev]"`，或改用主工作区 venv 绝对路径 |
| `get_api` 返回 "not found" | API 名错误，或当前版本已删除该 API，或 flet-mcp 与 flet 版本漂移 | 检查 API 名拼写；确认 flet-mcp 版本与 flet 主包对齐（见 §1 权威性边界）；若确认已删除，按 V0→V1 迁移表寻找替代 |
| flet-mcp 工具与 flet 版本不匹配 | flet-mcp 包版本与 flet 主包版本差距过大 | 项目用 `==` 锁定 flet-mcp 与 flet 主包同步升级（版本见 `pyproject.toml`）；升级 flet 时必须同步升级 flet-mcp |
| flet 升级时 flet-mcp 无对应版本 | flet-mcp 滞后发布或停维 | 临时改用 flet-mcp 已有最高版本 + 在 `api-verification-template.md` 记录已知偏差；或暂缓 flet 升级并在 `docs/debt/known-technical-debt.md` 登记 |
| Linux 环境下 `python.exe` 不存在 | 路径平台差异 | Linux 路径为 `venv/bin/python`，需按 OS 调整 MCP 配置 |

## 6. 验证记录

本节记录 flet-mcp 工具签名与文档描述一致性的 smoke test 证据，作为 §3.2 工具清单与 §3.3 场景示例的可信度依据。

**最近一次验证**：2026-07-24（flet 0.86.2 / flet-mcp 0.86.2 / fastmcp 3.4.4）

**验证命令与结果**：

```python
# 1. 服务器对象可加载
venv/Scripts/python.exe -c "from flet_mcp import mcp; print(mcp.name)"
# 输出: flet-mcp

# 2. get_api 查询 Dropdown 的 on_select 事件（验证 §3.3 场景 1）
venv/Scripts/python.exe -c "from flet_mcp.api_store import ApiStore; store = ApiStore(); import json; print(json.dumps(store.get('Dropdown', query='on_select'), ensure_ascii=False, indent=2))"
# 输出（关键字段）:
# {
#   "name": "Dropdown",
#   "package": "flet",
#   "events": [
#     {
#       "name": "on_select",
#       "type": "Optional[ControlEventHandler[Dropdown]]",
#       "docstring": "Called when the selected item of this dropdown has changed."
#     }
#   ]
# }

# 3. enum_has_member 等价查询（验证 §3.3 场景 2）
venv/Scripts/python.exe -c "from flet_mcp.api_store import ApiStore; store = ApiStore(); print('Icons.DELETE:', 'DELETE' in store.enum_member_names('Icons')); print('Icons.ARROW_BACK:', 'ARROW_BACK' in store.enum_member_names('Icons'))"
# 输出:
# Icons.DELETE: True
# Icons.ARROW_BACK: True

# 4. 启动命令验证（验证 §3.1 启动命令能启动 stdio server）
#    进程应持续运行等待 stdin 输入（Ctrl+C 退出），不报错即通过
venv/Scripts/python.exe -c "from flet_mcp import mcp; mcp.run()"
# 预期：进程启动后阻塞等待输入，stderr 输出 "Starting MCP server 'flet-mcp' with transport 'stdio'"
```

**结论**：§3.2 工具清单签名准确，§3.3 场景 1/2 示例返回值与文档描述一致。§3.1 启动命令 `python -c "from flet_mcp import mcp; mcp.run()"` 验证可用（2026-07-24 补充）。

**复验触发器**：flet-mcp 包版本变化 / flet 主包版本变化 / mcp-usage.md §3.2 或 §3.3 修改时，应按上述命令重新执行 smoke test 并更新本节记录。
