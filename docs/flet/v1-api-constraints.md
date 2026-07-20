# Flet V1 API 关键约束

> 来源：从 CONTRIBUTING.md §Flet V1 API 关键约束迁移

> 相关：[CLAUDE.md §2](../../CLAUDE.md#2-项目概览) 技术栈表、[CLAUDE.md §3.1 R16](../../CLAUDE.md#31--绝对禁止)（V1 单线程 async 模型对 UI 阻塞更敏感）。Flet 锁定版本见 [`pyproject.toml`](../../pyproject.toml)。

> 配套文档：
> - [项目差异与高风险 API](./project-differences.md) — 项目相对 Flet 官方默认的分叉点与项目验证过的高风险 API
> - [升级检查清单](./upgrade-checklist.md) — Flet 版本升级时的验证步骤与文档同步要求
> - [Flet API 核验记录模板](./api-verification-template.md) — 新增/变更 API 的核验记录模板
> - [可访问性最低标准](./accessibility-baseline.md) — UI 可访问性基线要求

## 演进方向

项目已从 Flet V0 升级到 V1（版本见 [`pyproject.toml`](../../pyproject.toml)）。**项目策略：全面拥抱 V1 声明式，新代码禁止命令式 UI 范式，不保留兼容垫片**。遵循以下原则：

- **新代码禁止引入任何 V0 兼容垫片**（如 `hasattr(page, "open")` 双路径、`getattr(e, "delta_x", 0)` 兼容取值等）。当前代码库已无残留（受 `tests/unit/ui/*_contract.py` 契约测试守护）；若新增依赖必须引入兼容垫片，需在本文末「例外清单」登记。
- **全面采用 V1 原生机制**：通过挂载到 `page.controls` 后由 `parent` 链访问 `page`，而非 `PageRefMixin` 覆写
- **全面使用 V1 API 形态**：`ft.Button` 而非 `ElevatedButton`；声明式组件内对话框统一用 `ft.use_dialog()`（V0→V1 迁移入口 `page.show_dialog()`/`page.pop_dialog()` 仅迁移旧代码参考，声明式组件内禁用，见 [V0→V1 迁移 API 表](#v0v1-迁移-api-表) 与 [声明式组件内 API 契约](#声明式组件内-api-契约)）
- **历史命令式代码已重写为声明式**：所有 `class X(ft.Container)` + `did_mount`/`will_unmount` + `self.update()` + `PageRefMixin` + `on_update`/`on_log` 回调注入的代码，已重写为 `@ft.component` + `use_viewmodel` 声明式范式。新代码禁止新增命令式控件（当前受契约测试守护，覆盖 `tests/unit/ui/test_data_source_tab_contract.py`、`test_data_view_contract.py`、`test_onboarding_wizard_contract.py`、`test_task_center_view.py` 等通过 `_ViewModelProtocol` 校验 VM 契约）；例外清单见本文末。
- **兼容垫片新代码禁止使用**：`PageRefMixin` 在依赖代码重写完成后已从生产代码删除，当前生产代码库无残留；`mock_flet` 测试桩仅保留在 `tests/unit/ui/mock_flet.py` 供 UI 单测使用，生产代码不得引入（见下文「兼容垫片使用规则」）；新代码禁止 reintroduce `PageRefMixin`，例外清单见本文末。

## V0→V1 迁移 API 表

V1 引入的 breaking changes 已通过 `pyright` 与运行期 TypeError/AttributeError 兜底，但部分项为**静默回归**（无异常），开发时必须主动遵守。本表为 V0→V1 迁移参考（迁移旧代码时使用），**不用于新代码**。声明式组件内 API 契约见 [下方「声明式组件内 API 契约」](#声明式组件内-api-契约)。

| # | 类别 | V0（禁止） | V1（必须） | 检测方式 |
|---|------|----------|----------|---------|
| 1 | 应用入口 | `ft.app(target=main)` | `ft.run(main=main)` | 运行期 |
| 2 | 窗口 resize | `page.on_resized = ...` | `page.on_resize = ...` | 运行期（静默失效） |
| 3 | 对话框显示（V0→V1 迁移入口） | `page.open(x)` / `page.dialog = x` | `page.show_dialog(x)`（仅 V0→V1 迁移参考，**声明式组件内禁用**，改用 `ft.use_dialog()`） | AttributeError |
| 4 | 对话框关闭（V0→V1 迁移入口） | `page.close(x)` | `page.pop_dialog()`（仅 V0→V1 迁移参考，**声明式组件内禁用**，改用 `ft.use_dialog()`） | AttributeError |
| 5 | FilePicker | `FilePicker(on_result=...)` + `overlay.append` | `page.services.append(picker)` + `await picker.pick_files()` | 运行期 |
| 6 | 图表控件 | `ft.LineChart(...)` | `import flet_charts as fch` → `fch.LineChart(...)` | ImportError |
| 7 | 图像 fit 枚举 | `ft.ImageFit.CONTAIN` | `ft.BoxFit.CONTAIN` | AttributeError |
| 8 | 图像 src | `ft.Image(src_base64=...)` | `ft.Image(src=b64_str)`（直接 base64） | TypeError |
| 9 | 按钮文本 | `Button(text="x")` / `btn.text = ...` | `Button(content="x")` / `btn.content = ...`（位置参数仍可） | TypeError |
| 10 | 弃用按钮 | `ft.ElevatedButton(...)` | `ft.Button(...)`（无警告但仍建议迁移） | 无（静默） |
| 11 | 滚动间隔 | `on_scroll_interval=100` | `scroll_interval=100` | 运行期（静默失效） |
| 12 | 样式 helper | `ft.padding.only(...)` / `ft.alignment.center` | `ft.Padding.only(...)` / `ft.Alignment.CENTER` | AttributeError |
| 13 | Dropdown 事件 | `Dropdown(on_change=...)` | `Dropdown(on_select=...)` | TypeError |
| 14 | TextField 字段 | `focus_border_color=...` | `focused_border_color=...` | TypeError |
| 15 | Tabs 构造 | `ft.Tabs(tabs=[ft.Tab(text=..., content=...)])` | `ft.Tabs(length=N, content=ft.Column([ft.TabBar(tabs=[ft.Tab(label=...)]), ft.TabBarView(controls=[...])]))` | TypeError |
| 16 | 拖拽增量 | `e.delta_x` | `e.primary_delta`（主路径），`e.local_delta.x`（回退） | **静默回归**（恒 0） |
| 17 | 窗口图标 | `page.window_icon` | `page.window.icon` | AttributeError |
| 18 | 控件 page 属性 | `self.page = page` 直接赋值 | 通过 `parent` 链访问；声明式组件内经 `ft.context.page` 或事件 `e.page` 获取（`PageRefMixin` 已删除，新代码禁用） | AttributeError |
| 19 | 本地存储 | `page.client_storage` | `page.shared_preferences` | AttributeError |
| 20 | 控件 update | 未挂载时 `control.update()` 静默返回 | 未挂载抛 `RuntimeError`（测试代码由 `conftest._v1_page_compat` fixture 兼容） | RuntimeError |
| 21 | 窗口方法 | `page.window.destroy()`（同步） | `await page.window.destroy()`（V1 协程） | 运行期（RuntimeWarning: coroutine never awaited） |

> **⚠️ 桌面关闭事件不可用 `page.on_close`**：`page.on_close` 在会话关闭/超时断开时触发，**非**用户点击窗口关闭按钮。桌面端关闭拦截必须用 `page.window.prevent_close = True` + `page.window.on_event`（监听 `ft.WindowEventType.CLOSE`），见 `main.py` 的窗口事件处理器。此为 V1 正确实现，非 V0 遗留。

> **来源说明**：第 8 项（`src_base64` → `src`）与第 16 项（`delta_x` → `primary_delta`）来自 Flet 官方 issue #5238（V1 breaking changes 汇总）。

> **第 3、4 项 Dialog 迁移入口**：`page.show_dialog()` / `page.pop_dialog()` 是 V0→V1 迁移入口，仅用于迁移命令式旧代码；声明式组件内统一使用 `ft.use_dialog()`（见下表）。

## 声明式组件内 API 契约

新代码（声明式 `@ft.component` 组件）内必须使用的 API。与上方 V0→V1 迁移表互为补充：声明式组件内 API 优先，迁移表仅在迁移旧代码时参考。

| 类别 | API | 说明 |
|------|-----|------|
| Dialog 管理 | `ft.use_dialog(dialog)` | 声明式组件内唯一契约；自动挂载/卸载到 page overlay（由 `use_state(open)` 控制显隐） |
| ViewModel 消费 | `use_viewmodel(factory=...)` 或 `use_viewmodel(vm=...)` | 唯一桥接 hook；`factory` 与 `vm` 互斥，详见 [MVVM 表现层](../patterns/mvvm.md) 与 [ui/hooks.py](../../ui/hooks.py) |
| Dropdown 事件 | `Dropdown(on_select=...)` | 声明式组件内事件契约（与 V0→V1 迁移表第 13 项一致） |
| use_effect cleanup | `ft.use_effect(setup, dependencies=[], cleanup=fn)` | cleanup 通过显式 `cleanup=` 参数传入，**不是 setup 返回值** |
| page 引用 | `ft.context.page` 或事件 `e.page` | 不直接赋值 `self.page = page`（`PageRefMixin` 已删除） |

## 兼容垫片使用规则

V0→V1 兼容垫片（`PageRefMixin` / 旧 mock 全局桩）**新代码禁止使用**；当前代码库已无残留（受 `tests/unit/ui/*_contract.py` 契约测试守护）。测试侧改用 `conftest._v1_page_compat` fixture 兼容未挂载控件的 `update()`/`page` 访问。例外清单见本文末。

> **`refresh_dropdown_options()` 状态**：已在 Phase R.4.1 删除。声明式 UI 下 options 由 state 派生，`use_state` 触发重建即自动绕过 V1 `Prop.__set__` 值相等优化，该函数不再需要。

## V1 声明式 UI 开发规范

> 宪法 [CLAUDE.md §3.2 UI 模型（强制）](../../CLAUDE.md#32--强制要求) 的唯一实现细则。
> 命令式存量（`class X(ft.Container)` + `did_mount`/`will_unmount` + 手动 `self.update()`）已重写为声明式范式（新代码禁止新增命令式控件，当前受契约测试守护，见 [MVVM 表现层](../patterns/mvvm.md) 与 `tests/unit/ui/*_contract.py`；下方「关注点对照」列出当前允许/禁止形态）。

切到 Flet V1 后，新增 View/Panel/Component 必须采用声明式 `@ft.component` + 官方 hooks 写法。API 签名见 [下方](#3-use_state--use_effect-api) 与 [项目差异与高风险 API](./project-differences.md)。

### 1. 关注点对照（命令式作废 → 声明式要求）

| 关注点 | 命令式旧写法（新代码禁止） | 声明式要求（宪法标准） |
|--------|------|------|
| 组件定义 | `class X(ft.Container): __init__/super()` | `@ft.component` 函数返回控件树 |
| 状态 | 实例属性 + 手动 `self.update()` | `use_state` 状态变更自动重渲染 |
| 生命周期/副作用 | `did_mount`/`will_unmount` | `use_effect(setup, dependencies, cleanup)` |
| i18n 热切换 | `I18n.subscribe`/`unsubscribe` + `refresh_locale` + 手动刷新 | locale 作为声明式状态源，切换自动重渲染（不再手动订阅/刷新） |
| 下拉刷新 | ~~`refresh_dropdown_options` 两步 update 绕过~~（已删除） | 状态驱动重建 options，自动绕过 |
| 响应式 | `handle_resize` 鸭子分发 + 断点手算 | 窗口尺寸作为 state/observable + `ResponsiveRow`，状态驱动布局 |
| page 引用 | `PageRefMixin` 覆写只读 `control.page` | 组件内经官方上下文机制或事件 `e.page` 获取，垫片已删除 |
| ViewModel 消费 | `on_update`/`on_log` 回调注入 + View 持有 VM | `use_viewmodel(factory) -> (state, commands)`，View 只读 state + 调 commands（见 [MVVM 表现层](../patterns/mvvm.md)） |

### 2. `@ft.component` 标准模板

```python
import flet as ft

@ft.component
def MetricCard(label: str):
    # 声明式状态：值变更自动重渲染，无需手动 update()
    value, set_value = ft.use_state(0)

    # 副作用：挂载/卸载/依赖变更时执行；cleanup 通过显式 cleanup= 参数传入
    def setup() -> None:
        set_value(42)  # 示例：挂载后初始化值

    def cleanup() -> None:
        pass  # 卸载或依赖变更时清理资源（如关闭句柄、退订外部源）

    ft.use_effect(setup, dependencies=[label], cleanup=cleanup)

    return ft.Container(
        content=ft.Column([
            ft.Text(label),
            ft.Text(str(value)),
        ]),
    )
```

> **i18n 不在此处手动订阅**：locale 由独立状态源驱动（见 [§4](#4-i18n--响应式声明式实现)），声明式组件内禁止调用 `I18n.subscribe()`。

### 3. `use_state` / `use_effect` API

- `ft.use_state(initial) -> (value, setter)`：类似 React `useState`。`setter` 接受新值，或接受接收前值返回新值的函数。
- `ft.use_effect(setup, dependencies=None, cleanup=None)`：
  - `setup` 为普通函数，**不通过返回值传递 cleanup**；cleanup 必须通过显式 `cleanup=` 参数传入（与 [声明式组件内 API 契约](#声明式组件内-api-契约) 一致）。
  - `dependencies` 缺省时只在初次渲染运行；指定时按依赖变化重跑；cleanup 在重跑前与卸载时执行。
  - hooks 必须在 `@ft.component` 渲染上下文内调用，独立调用抛 `RuntimeError: No current renderer`。
- `ft.component(fn)` 装饰器：把函数标记为组件，返回值即控件树根节点。

### 4. i18n / 响应式声明式实现

- **i18n（canonical 模式）**：
  - **不手动订阅**：声明式组件内禁止调用 `I18n.subscribe()` / `I18n.unsubscribe()` / `refresh_locale`。locale 由 View 层独立状态源（通常在根组件由 `use_state` 持有，通过 props/context 下发）驱动重渲染。
  - **VM 不感知 locale**：ViewModel state 不含 locale 字段；VM 只产出 i18n key 与 params（封装为 `Message(key, params)`），View 渲染时按当前 locale 解析：`I18n.get(msg.key, **msg.params)`。
  - **唯一 canonical 示例**见 [§5 ViewModel 消费](#5-viewmodel-消费mvvm-桥接) 中的 `ScreenerView`（`state.status` 为 `Message` 对象，View 渲染时调用 `I18n.get(state.status.key, **state.status.params)`）。
- **响应式**：窗口尺寸作为 `use_state`（由根组件订阅 `page.on_resize` 更新），通过 props 下发；视图内用 `ResponsiveRow` + `col` 配置，状态驱动布局。**不再**实现 `handle_resize` 鸭子分发。
- **下拉刷新**：options 由 state 派生，`use_state` 触发重建即自动绕过 V1 `Prop.__set__` 值相等优化。`refresh_dropdown_options()` 工具函数已在 Phase R.4.1 删除（声明式下不再需要）。

### 5. ViewModel 消费（MVVM 桥接）

View 消费 ViewModel 必须经 `use_viewmodel(factory) -> (state, commands)` hook，**不得**直接 `vm = SomeViewModel()` 实例化或注入回调。完整契约与形态见 [MVVM 表现层](../patterns/mvvm.md)。

```python
import flet as ft
from core.i18n import I18n
from ui.hooks import use_viewmodel          # 已实现，见 ui/hooks.py
from ui.viewmodels.screener_view_model import ScreenerViewModel

@ft.component
def ScreenerView():
    state, vm = use_viewmodel(ScreenerViewModel)   # state 不可变 snapshot；vm 即 commands

    async def on_run(e):
        await vm.run()    # command -> _notify -> state 更新 -> 自动重渲染

    # View 只做两件事：读 state 渲染、事件调 commands
    return ft.Column([
        ft.Text(I18n.get(state.status.key, **state.status.params)),  # Message 渲染
        ft.Button(I18n.get("run"), on_click=on_run),
    ])
```

要点：

- View 只做两件事：读 `state` 渲染控件树、事件调 `vm.command()`
- VM 不得出现在 View 的 `use_state`/`use_effect` 之外的任何地方；不持有 VM 引用做副作用
- `use_viewmodel` hook 已实现（见 [ui/hooks.py](../../ui/hooks.py)），新 UI 必须通过本 hook 消费 ViewModel
- 所有 ViewModel 必须满足 [`_ViewModelProtocol`](../../ui/hooks.py)（`state` / `subscribe` / `dispose` 三方法）；已知例外清单见 `ui/viewmodels/` 审查记录

### 6. 迁移约束

- **命令式 UI 代码新代码禁止**：所有 `class X(ft.Container)` + `did_mount`/`will_unmount` + `self.update()` + `PageRefMixin` + `on_update`/`on_log` 回调注入的代码，已重写为 `@ft.component` + `use_viewmodel` 声明式范式。新代码禁止新增命令式控件（契约测试守护 `tests/unit/ui/*_contract.py` 通过 `_ViewModelProtocol` 校验）；活动规范只允许声明式形态，例外清单见本文末。
- `ft.run(before_main=...)` 属可选优化，YAGNI，暂不强制。
- async 窗口/控件方法必须 `await`。
- 命令式 `@ft.control`/`@dataclass` + `did_mount`/`will_unmount` 写法新代码禁止（已重写为 `@ft.component` + `use_effect(setup, dependencies, cleanup)`，命令式控件已删除）；例外清单见本文末。

## 依赖管理

> 本节已迁移到 [../guides/dependency-management.md](../guides/dependency-management.md)。

## PyInstaller 打包

> 本节已迁移到 [../guides/dependency-management.md](../guides/dependency-management.md)。

## Flet 版本升级文档协同机制

- `CLAUDE.md` 不记录具体 Flet API 细节，只记录升级时必须遵守的验证原则、红线与架构边界。
- `CONTRIBUTING.md` 是 Flet API 约束、UI 开发范式、兼容垫片与测试模板的入口索引，必须随 `pyproject.toml` 中锁定的 Flet 版本同步更新。
- `docs/flet/` 下子文档是 Flet API 约束的详细实现细则源（v1-api-constraints.md / project-differences.md / upgrade-checklist.md / api-verification-template.md / accessibility-baseline.md）。
- 每次升级 Flet 小版本或大版本，必须完成：
  1. 核对官方 breaking changes / deprecations；
  2. 运行最小 UI 验证：启动、窗口关闭、dialog、resize、i18n 热重载、一个 V1 控件样例；
  3. 更新 `docs/flet/` 的 Flet 章节与对应验证清单；
  4. 仅当升级影响红线、架构边界或 AI 行为规则时，才同步修改 `CLAUDE.md`。
- 禁止在多份文档中重复维护同一 Flet API 细节；长期规范引用用符号锚点，不用硬编码行号。

## Flet V1 项目差异与升级清单

> 宪法依据：CLAUDE.md §5 索引指向 `docs/flet/`；本节不重复 API 细节，仅声明引用关系与优先级。

项目规范的 Flet 知识聚焦于**项目专属约束**（V0→V1 迁移 API 表、声明式组件内 API 表、V1 声明式 UI 规范、兼容垫片、依赖管理、PyInstaller、升级协同）。通用 Flet v1 概念（路由 `ft.Router`、Services 用法、`SharedPreferences`/`Clipboard`/`StoragePaths`/`FilePicker`、`use_state`/`use_effect`/`use_ref`/`use_dialog`/`create_context` 基础 Hooks、`yield` 中间进度反馈、资源管理、构建打包、性能与错误处理通用模式等）见 [Flet 官方文档](https://docs.flet.dev/)，本项目不再复制，避免与上游漂移。

**优先级（冲突时前者覆盖后者）**：

1. [CLAUDE.md](../../CLAUDE.md)（红线 R1~R18、架构边界、交互准则）
2. [CONTRIBUTING.md](../../CONTRIBUTING.md)（项目实现规范入口索引）
3. [`docs/flet/`](./) 子文档（项目 Flet 差异与升级清单详细实现）

**项目专属约束覆盖通用手册的 8 处分叉**（查阅通用手册时须以下表项目规范为准）：

| 维度 | 通用手册 | 项目规范（优先） |
|------|---------|----------------|
| 适用范围 | Web/移动/桌面通用 | 仅桌面端（`page.window.min_width=1280`） |
| UI 模型 | 裸 `use_state`/`use_effect` 组件 | MVVM + `use_viewmodel` hook（CLAUDE.md §3.2 强制；`use_viewmodel` 已实现，见 [ui/hooks.py](../../ui/hooks.py)） |
| 异步线程 | `asyncio.to_thread` / `page.run_thread` | `ThreadPoolManager.run_async(TaskType.IO/CPU)`（CLAUDE.md §3.1 R16 红线） |
| API 约束表 | 通用手册 §17 迁移表 | 本节 [V0→V1 迁移 API 表](#v0v1-迁移-api-表) + [声明式组件内 API 契约](#声明式组件内-api-契约)（含检测方式，与 [`pyproject.toml`](../../pyproject.toml) 锁定版本对齐） |
| 版本锁定 | 通用手册示例值 | `flet` / `flet-desktop` / `flet-charts` 三包均以 `==` 精确锁定（锁定值见 [`pyproject.toml`](../../pyproject.toml)，见 [依赖管理](../guides/dependency-management.md)） |
| 响应式断点 | xs/sm/md/lg/xl/xxl 576~1400 | compact/standard/ultra_wide 1200/1600/2400（见 [`ui/theme.py`](../../ui/theme.py) 的 `AppStyles` 断点常量） |
| 桌面打包 | `flet pack`（通用手册 §13.5） | PyInstaller（[`AStockScreener.spec`](../../AStockScreener.spec)，见 [PyInstaller 打包](../guides/dependency-management.md)） |
| Dialog 管理 | `ft.use_dialog()` Hook（通用手册 §10.1，声明式唯一推荐） | 项目规范一致：声明式组件内唯一契约为 `ft.use_dialog()`；`page.show_dialog()`/`page.pop_dialog()` 仅作为 V0→V1 迁移入口，声明式组件内禁用（见 [声明式组件内 API 契约](#声明式组件内-api-契约)） |

通用手册中 Web/移动专属内容（WASM/CDN、APK/IPA 构建、`SafeArea`、Cupertino `adaptive`、移动端 `NavigationBar` 等）项目桌面端不适用，仅作背景知识。

## 例外清单

> P1-3 绝对化表述分层：本节集中登记「新代码禁止 / 当前受契约测试守护」规则的例外情况。新增例外需在此登记并说明理由。

当前无例外。若新增依赖或重构必须引入兼容垫片、命令式控件、V0 API 形态，需在此处登记：

- 例外项: `<项名>`
  - 理由: `<为何无法遵守默认规则>`
  - 守护机制: `<对应的契约测试 / 手动验证步骤>`
  - 移除条件: `<何时可移除例外>`
