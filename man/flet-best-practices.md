# Flet 0.86.0 应用开发最佳实践手册

> 面向工程团队的架构与工程实践指南
> 适用版本：**Flet 0.86.0**（属于 Flet 1.0 Beta 系列，即 `v1` 现代架构）
> 最后更新：2026-07

> ## ⚠️ 项目集成说明（AStockScreener 项目开发者必读）
>
> 本手册是 **通用 Flet v1 开发参考**，覆盖 Web/移动/桌面全平台。在 AStockScreener 项目中使用时，必须遵守以下**优先级**（后者被前者覆盖）：
>
> 1. **[CLAUDE.md](../CLAUDE.md)** — 项目宪法（红线 R1~R18、架构边界、交互准则）
> 2. **[CONTRIBUTING.md](../CONTRIBUTING.md)** — 项目实现规范（含「Flet 0.86.0 (V1) API 关键约束」21 行 breaking changes 表、「V1 声明式 UI 开发规范」）
> 3. **本手册** — 通用 Flet v1 知识补充（路由、Services、存储、构建打包等项目规范未展开的主题）
>
> **项目专属约束覆盖本手册的 8 处分叉**（遇到冲突以项目规范为准）：
>
> | 维度 | 本手册（通用） | 项目规范（优先） |
> |------|--------------|----------------|
> | 适用范围 | Web/移动/桌面通用 | **仅桌面端**（`page.window.min_width=1280`，见 CONTRIBUTING.md「V1 声明式 UI 开发规范」响应式小节） |
> | UI 模型 | 裸 `use_state`/`use_effect` 组件（§4~5） | **MVVM + `use_viewmodel` hook**（CLAUDE.md §3.2 强制；`use_viewmodel` 已实现，见 [ui/hooks.py](./ui/hooks.py)） |
> | 异步线程 | `asyncio.to_thread` / `page.run_thread`（§6.3） | **`ThreadPoolManager.run_async(TaskType.IO/CPU)`**（R16 红线，禁止在事件处理器同步阻塞） |
> | API 约束 | §17 迁移表（通用） | **21 行 breaking changes 表**（CONTRIBUTING.md，含检测方式，实测对齐 0.86.0） |
> | 版本锁定 | `flet==0.86.0` + charts 解析版本（§1.1） | **flet / flet-desktop / flet-charts 三包全锁 `==0.86.0`**（pyproject.toml） |
> | 响应式断点 | xs/sm/md/lg/xl/xxl 576~1400（§11.1） | **compact/standard/ultra_wide 1200/1600/2400**（桌面端，CONTRIBUTING.md「V1 声明式 UI 开发规范」响应式断点小节） |
> | 桌面打包 | `flet pack`（§13.5） | **PyInstaller**（`AStockScreener.spec`，见 CONTRIBUTING.md「PyInstaller 打包」） |
> | Dialog 管理 | `ft.use_dialog()` Hook（§10.1，声明式唯一推荐） | **`ft.use_dialog()` Hook**（声明式重写已收官） |
>
> 本手册中 Web/移动专属内容（WASM/CDN、APK/IPA 构建、SafeArea、Cupertino `adaptive`、移动端 NavigationBar 等）**项目桌面端不适用**，仅作背景知识。本手册 API 声明已对 `flet==0.86.0` 实测核实（核实方法：`python -c "import flet as ft; hasattr(ft, '...')"` + `inspect.getsource()` + 官方发布公告交叉验证）。

---

## 0. 版本背景与读者须知

Flet 是一个用纯 Python 构建 **Web / 桌面 / 移动** 跨平台应用的框架，底层由 Flutter 渲染。

**极其重要的前提**：`0.86.0` 属于 Flet 从零重写的 **v1 架构**（1.0 Beta，起始于 `0.70`/`0.80`），与旧版 `0.28.x`（v0）**不兼容**。网上大量教程、旧博客、旧代码仍基于 v0，直接套用会报错。本手册所有实践均以 v1 为准。

> **范式约定（务必先读）**：Flet 0.86.0 主推 **声明式（Declarative / Reactive）** 开发方式（`@ft.component` + Hooks）。为保证一致性、可测试性与可维护性，**本手册只讲声明式，不涉及命令式写法**。请将"改状态、让组件重渲染"作为唯一心智模型，不要手动持有控件引用再修改其属性。

判断你看到的资料是否适用 v1 的快速信号：

| 特征 | v0（旧，≤0.28.x） | v1（本手册，≥0.70，含 0.86.0） |
| --- | --- | --- |
| 启动函数 | `ft.app(target=main)` | `ft.run(main)` |
| 更新 UI | 到处手动 `control.update()` | 事件处理器结束后**自动更新** |
| 编程范式 | 仅命令式 | **声明式 / 响应式**（`@ft.component` + Hooks，本手册唯一推荐） |
| 控件实现 | 自定义类 + 手动序列化 | Python **dataclass**，强类型 |
| 通信协议 | JSON（图片需 base64） | **MessagePack** 二进制协议 |
| 并发模型 | 多线程 | **单线程 async**（类似 JS/Flutter） |
| 非可视功能 | 普通控件 | **Service（服务）**，需加入 `page.services` |
| 对话框 | `page.open()` / `page.close()` | `page.show_dialog()` / `page.pop_dialog()` |

---

## 1. 环境与依赖管理

### 1.1 固定版本，避免"隐式升级"

v1 与 v0 存在破坏性差异，扩展包（`flet-audio`、`flet-video` 等）在 v1 下版本号为 `0.2.x+`，v0 下为 `0.1.x`。**务必精确 pin 版本**，否则 `flet build` 会拉错版本。

推荐使用 `pyproject.toml` 管理：

```toml
[project]
name = "my-flet-app"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "flet==0.86.0",
    # 图表已拆分为独立包（v1 破坏性变更）；扩展包在 v1 下为 0.2.x 系列，
    # 具体版本以 `uv add` / `pip install` 实际解析到的为准，并写回此处 pin 死。
    # "flet-charts==<解析到的版本>",
    # "flet-audio==<解析到的版本>",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "ruff>=0.6",
    "mypy>=1.11",
]
```

安装（推荐 `uv`，也可用 `pip`）：

```bash
uv venv
uv add "flet==0.86.0"
# 或
pip install "flet==0.86.0"
```

### 1.2 每个项目独立虚拟环境

v1 与 v0 不能共存于同一环境。务必用独立 venv，避免全局污染。

### 1.3 桌面客户端二进制

自 `0.83` 起，桌面客户端二进制从 PyPI wheel 迁移到 GitHub Releases，由 `flet-desktop` 统一管理。首次 `flet run` 会自动下载，CI/离线环境需预置缓存目录 `~/.flet/cache/`。

---

## 2. 推荐项目结构

对中大型应用，采用"按职责分层 + 按功能分模块"的结构。小型应用可从单文件起步再演进。

```text
my-flet-app/
├── pyproject.toml
├── README.md
├── assets/                 # 静态资源：图片、字体、图标、i18n 文件
│   ├── icon.png
│   └── fonts/
├── src/
│   └── app/
│       ├── main.py         # 入口：ft.run(main, before_main=config)
│       ├── config.py       # 环境/常量/主题配置
│       ├── router.py       # ft.Router 路由定义
│       ├── state/          # 全局状态（observable / context）
│       │   └── app_state.py
│       ├── services/       # 数据/IO/业务服务（HTTP、DB、缓存）
│       │   ├── api_client.py
│       │   └── repository.py
│       ├── components/      # 可复用的 @ft.component 组件
│       │   ├── buttons.py
│       │   └── cards.py
│       ├── views/          # 页面级组件（每个路由一个）
│       │   ├── home.py
│       │   └── settings.py
│       └── theme/          # 主题、颜色、排版
│           └── theme.py
└── tests/
    ├── test_state.py
    └── test_services.py
```

**分层原则**：

- `views` / `components` 只负责渲染与事件绑定，不写业务逻辑；
- `services` 负责一切副作用（网络、文件、数据库），可脱离 UI 单独测试；
- `state` 负责应用状态，UI 通过 Hooks / context 订阅；
- 依赖方向单向：`views → components → state/services`，禁止反向。

---

## 3. 应用入口与生命周期

### 3.1 使用 `ft.run` 与 `before_main`

`ft.run()` 取代旧的 `ft.app()`（**API 整体替换**，不是参数更名：旧 API 第一个位置参数 `target` 对应新 API 第一个位置参数 `main`，签名与语义均已重写；`target` 作为向后兼容别名仍存在于 `ft.run` 签名末尾，默认 `None`，新代码不应使用）。用 `before_main` 在 Flutter 客户端开始发送事件**之前**可靠地注册页面级事件处理器，避免竞态。

```python
import flet as ft


@ft.component
def App():
    return ft.Text("Hello, Flet v1!")


def config(page: ft.Page):
    # 在 main 之前配置页面级属性/事件：确保早期事件（resize、路由）不丢失
    page.title = "My App"
    page.theme_mode = ft.ThemeMode.SYSTEM


def main(page: ft.Page):
    page.add(App())          # 声明式：挂载根组件，而非手动拼装控件


ft.run(main, before_main=config)
```

> `page.add(RootComponent())` 是"挂载入口"，之后一切 UI 都由组件按状态渲染。启用路由时改用 `ft.run(lambda page: page.render(App))` 渲染根路由组件（见第 7 章）。
>
> **两种挂载方式差异**：`page.add(App())` 把组件作为子控件挂到页面控件树（无路由）；`page.render(App)` 则把组件作为**根路由组件**渲染，配合 `ft.Router` 使用。无路由应用用前者，有路由应用用后者，不可混用。详见 §4.4「v1 内部命令式 vs 声明式 API 选用」。

### 3.2 随处获取当前 Page

v1 支持在程序任意位置通过 `ft.context.page` 拿到当前 `Page` 实例，无需层层透传。**但**：不要滥用它来做全局可变状态，仅用于确实需要页面引用的工具函数。

### 3.3 `before_event` 钩子

`Control.before_event(e)` 在任何事件处理器之前被调用，返回 `False` 可取消该事件。适合做统一鉴权、路由守卫、埋点。**源码依据**：`flet/controls/base_control.py` 的 `Control._trigger_event()` 方法在调用 `on_<event>` 处理器前先调用 `self.before_event(e)`，返回值为 `False` 时跳过事件处理器（0.86.0 实测核实）。

---

## 4. 声明式编程模型（唯一推荐范式）

Flet 0.86.0 的现代主线是**声明式 / 响应式**。核心思想：用 `@ft.component` 定义函数组件，组件根据当前**状态**返回一棵 UI 树；状态变化时组件自动重渲染，框架做高效差量更新。你只需描述"状态 → UI 的映射"，永远不手动增删或修改控件对象。

### 4.1 一个最小组件

```python
import flet as ft


@ft.component
def Counter():
    count, set_count = ft.use_state(0)

    return ft.Column([
        ft.Text(f"计数：{count}", size=32),
        ft.FilledButton("加一", on_click=lambda e: set_count(count + 1)),
    ])


def main(page: ft.Page):
    page.add(Counter())


ft.run(main)
```

三条核心心智：

- 组件是**纯函数**：输入状态/属性，输出控件树，本身不产生副作用（副作用交给 `use_effect`）；
- **不要**持有控件引用再改它的属性；改**状态**，让组件重渲染；
- 事件处理器结束后**自动更新**，无需调用 `update()`。

### 4.2 组件组合

把界面拆成小而可复用的组件，像积木一样嵌套：

```python
@ft.component
def Avatar(url: str):
    return ft.Image(src=url, width=40, height=40)


@ft.component
def UserCard(name: str, avatar: str):
    return ft.Card(
        content=ft.Row([Avatar(avatar), ft.Text(name)]),
    )
```

### 4.3 组件设计原则

- **单一职责**：一个组件只做一件事，过大就拆分；
- **状态就近**：状态放在真正需要它的最近公共祖先，避免无谓的顶层大状态；
- **数据向下，事件向上**：父组件用参数把数据传给子组件，子组件通过回调参数通知父组件；
- **展示与逻辑分离**：数据获取放 `services` + `use_effect`，组件只负责根据状态渲染。

### 4.4 v1 内部命令式 vs 声明式 API 选用

v1 同时支持命令式（`main(page)` + `page.add()` + `control.update()`）和声明式（`@ft.component` + Hooks）两套 API。**本手册唯一推荐声明式**，但部分 API 有命令式/声明式两套入口，选用时须按范式匹配，不可混用：

| 能力 | 命令式 API（非 `@ft.component`） | 声明式 API（`@ft.component` 内） | 详见 |
|------|--------------------------------|-------------------------------|------|
| Dialog 管理 | `page.show_dialog()` / `page.pop_dialog()` | `ft.use_dialog()` Hook | §10.1 |
| 根组件挂载 | `page.add(App())` | `page.render(App)` | §3.1 / §7.1 |
| UI 更新 | `control.update()` | 状态 setter 自动触发重渲染 | §4.1 |
| 状态持有 | 控件实例属性 | `use_state` / `use_ref` / `use_effect` | §5 |

**核心原则**：在 `@ft.component` 函数内**禁止**调用命令式 API（`page.show_dialog`、`control.update()` 等），否则会绕过框架的状态驱动渲染，导致 UI 与状态不同步。命令式 API 仅用于非声明式的 `main(page)` 入口写法。

---

## 5. 状态管理（Hooks）

Hooks 是 v1 声明式的核心，遵循 React 风格。

### 5.1 Hooks 三大铁律

1. **只在顶层调用**：不要在循环、条件、嵌套函数里调用 Hook；
2. **只在组件里调用**：必须在 `@ft.component` 装饰的函数内；
3. **调用顺序固定**：每次渲染 Hook 的调用顺序必须一致。

违反会导致状态错乱（因为 Hook 按位置存储在组件的 `_state.hooks` 列表里）。

### 5.2 常用 Hooks 一览

| Hook | 作用 | 类比 React |
| --- | --- | --- |
| `use_state(initial)` | 组件局部状态，返回 `(value, setter)` | `useState` |
| `use_effect(fn, deps)` | 副作用：IO/定时器/订阅，支持依赖与清理 | `useEffect` |
| `use_ref(initial)` | 保存可变值但**不触发重渲染** | `useRef` |
| `create_context()` / `use_context()` | 跨组件树注入状态/服务 | `createContext`/`useContext` |
| `use_dialog()` | 声明式管理对话框（`@ft.component` 中唯一推荐；详见 §10.1） | 自定义 hook |

### 5.3 `use_state`：局部状态

```python
@ft.component
def Counter():
    count, set_count = ft.use_state(0)

    # 函数式更新：确保拿到最新值（避免闭包陈旧值）
    def increment():
        set_count(lambda prev: prev + 1)

    return ft.FilledButton(f"{count}", on_click=lambda e: increment())
```

**最佳实践**：

- 涉及"基于旧值计算新值"时，**永远用函数式更新** `set_count(lambda prev: ...)`，避免闭包捕获的旧值造成竞态；
- 初始值代价高时用惰性初始化：`use_state(lambda: expensive())`，只在首次渲染计算一次；
- 一个状态一个 `use_state`，不要把不相关的值塞进同一个字典。

### 5.4 `use_effect`：副作用与生命周期

用于挂载时加载数据、订阅、定时器，并在依赖变化或卸载时清理。

```python
import asyncio
import flet as ft


@ft.component
def Clock():
    now, set_now = ft.use_state("")

    def start():
        task = asyncio.create_task(_tick(set_now))
        # 返回清理函数：组件卸载或依赖变化时调用
        return lambda: task.cancel()

    ft.use_effect(start, [])  # 空依赖 = 仅挂载时执行一次

    return ft.Text(now)


async def _tick(set_now):
    import time
    while True:
        set_now(time.strftime("%H:%M:%S"))
        await asyncio.sleep(1)
```

**要点**：`use_effect` 的第二个参数是依赖数组；`[]` 表示只在挂载执行；返回值作为清理函数，务必用它取消任务/取消订阅，防止内存泄漏。

### 5.5 组件间共享状态：`create_context` / observable

- 小范围共享：把 `(value, setter)` 通过组件参数逐层传递；
- 跨层共享：用 `create_context()` + `use_context()` 注入全局状态/服务（如登录用户、主题、API 客户端），避免"props 透传地狱"；
- 大型应用：可在 context 之上实现 **Redux 风格**（`action → reducer(state, action) → new_state`）。`use_state` 的 setter 支持"prev → next"函数，天然适配 reducer 模式。

**团队规范（中大型项目必读）**：

- **禁止逐层透传全局 state**：登录态、主题、locale、API 客户端等跨多组件树共享的状态，必须用 `create_context()` + `use_context()` 注入，禁止通过组件参数层层透传（"props 透传地狱"反模式）；
- **props 传递仅限真正局部**：仅当状态真正只被一个子树（≤2 层）使用时，才允许用组件参数传递；
- **context 分领域拆分**：不要把所有全局状态塞进单个 context，按领域拆分（如 `AuthContext`、`ThemeContext`、`LocaleContext`），避免无关状态变化触发大范围重渲染；
- **服务实例走 context**：HTTP 客户端、数据库连接等服务实例通过 context 注入，便于测试时替换为 mock。

### 5.6 状态管理反模式

- ❌ 在模块级用全局可变变量替代 `use_state`（无法触发重渲染、难测试）；
- ❌ 在渲染函数体内做网络请求（应放进 `use_effect`）；
- ❌ 用 `use_ref` 存本应触发 UI 更新的状态（`use_ref` 不重渲染）。

---

## 6. 并发与异步（v1 最容易踩坑的地方）

### 6.1 单线程 async 模型

v1 采用**单线程异步** UI 模型（类似 JavaScript/Flutter）。这意味着：

- **任何阻塞调用都会冻结 UI**，包括 `time.sleep()`、同步的 `requests.get()`、重计算循环；
- 事件处理器优先写成 `async def`；
- 延时用 `await asyncio.sleep()`，**不要**用 `time.sleep()`。

### 6.2 正确的异步事件处理

```python
import asyncio
import flet as ft


@ft.component
def Loader():
    status, set_status = ft.use_state("空闲")

    async def run(e):
        set_status("开始…")
        await asyncio.sleep(3)      # 非阻塞
        set_status("完成")

    return ft.Column([
        ft.Text(status),
        ft.FilledButton("执行", on_click=run),
    ])
```

### 6.3 CPU 密集任务：交给线程

CPU 密集型工作（图像处理、加密、解析大文件）会卡死单线程事件循环，必须卸载到线程：

```python
async def heavy(e):
    result = await asyncio.to_thread(cpu_bound_function, arg)
    set_result(result)
```

> Flet 也提供 `page.run_thread(fn)`（后台线程跑同步函数）与 `page.run_task(coro)`（调度协程）两个便捷方法，可按需替代裸 `asyncio` 调用。

### 6.4 长任务的中间进度反馈（`yield`）

在 async 事件处理器里，若要在等待期间**先把中间状态渲染出来**，可在更新状态后 `yield` 一次，让框架立即刷新界面。

**机制说明（v1 专属）**：v1 事件处理器默认**批量更新**——状态 setter 不会立即触发渲染，而是等到事件处理器返回时一次性刷新。`yield` 的作用是**主动让出事件循环**，触发一次中间帧渲染，是 v1 框架内置支持的长任务 UI 刷新方案。**源码依据**：`flet/controls/base_control.py` 的 `Control._trigger_event()` 方法用 `inspect.isasyncgenfunction` / `isgeneratorfunction` 检测事件处理器是否为生成器函数，若是，则在每次 `yield` 后调用 `session.after_event()` 触发一次中间帧渲染（0.86.0 实测核实）。适用场景：长任务需要在等待期间先把"处理中"等中间状态显示给用户，否则用户会看到界面"卡住"直到任务完成。

```python
@ft.component
def Task():
    status, set_status = ft.use_state("空闲")

    async def run(e):
        set_status("处理中…")
        yield                      # 主动让出事件循环，触发一次中间帧渲染
        await asyncio.sleep(3)
        set_status("完成")

    return ft.Column([
        ft.Text(status),
        ft.FilledButton("执行", on_click=run),
    ])
```

### 6.5 网络请求

优先使用异步 HTTP 客户端 `httpx.AsyncClient`（而非同步的 `requests`），并设置超时：

```python
async def load_user(set_name, set_error):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://api.example.com/user/1")
            r.raise_for_status()
            set_name(r.json()["name"])
    except Exception as ex:
        set_error(str(ex))
```

---

## 7. 路由与导航

### 7.1 声明式 `ft.Router`（0.85 引入）

v1 提供声明式 `ft.Router`，支持嵌套路由、布局路由（outlet）、动态段、可选段、splat、正则约束、数据加载器（loader）、激活链接检测、鉴权模式，以及 `manage_views=True` 的视图栈导航（移动端支持滑动返回与 AppBar 返回按钮）。

`ft.Router` 是一个组件，通常由根组件 `App` 返回；路由用 `ft.Route` 定义：`index=True` 表示根路由，`path="..."` 是**相对段（无前导斜杠）**，`component=` 指定渲染的组件。用 `ft.run(lambda page: page.render(App))` 挂载。

```python
import flet as ft


@ft.component
def App():
    return ft.Router(
        [
            ft.Route(index=True, component=Home),           # "/"
            ft.Route(path="settings", component=Settings),  # "/settings"
            ft.Route(path="users/:id", component=UserDetail),  # 动态段
        ],
        manage_views=True,   # 启用视图栈 + 移动端滑动返回
    )


ft.run(lambda page: page.render(App))
```

**嵌套布局（outlet）**：父路由带 `children=` 与 `outlet=True`，其组件内用 `ft.use_route_outlet()` 放置子路由：

```python
@ft.component
def AppLayout():
    outlet = ft.use_route_outlet()
    return ft.Column([ft.AppBar(title=ft.Text("My App")), outlet])


@ft.component
def App():
    return ft.Router([
        ft.Route(component=AppLayout, children=[
            ft.Route(index=True, component=Home),
            ft.Route(path="about", component=About),
        ]),
    ])
```

**导航与路由 Hooks**：

- 导航：`ft.context.page.navigate("/about")`（同步回调里也可用）；
- 路由 Hooks：`use_route_params`、`use_route_location`、`use_route_outlet`、`use_route_loader_data`、`is_route_active`；
- `Route(modal=True)`（0.85.2）：全屏对话框式模态，关闭时不重建底层视图栈；
- `Route(recursive=True)`（0.85.2）：路由可匹配自身为后代，适合树形无限深度 URL（如 `/folder/a/b/c`）。

### 7.2 视图栈操作

- `page.pop_views_until()`：弹出多个视图并向目标视图返回结果；
- `page.navigate(path)`：从同步回调触发导航（`page.push_route()` 的同步包装）。

### 7.3 路由守卫

在数据加载器（loader）或布局组件中判断登录态，未登录时 `ft.context.page.navigate("/login")` 重定向，是集中式鉴权的推荐做法。

---

## 8. 服务（Services）

### 8.1 概念

Service 是**非可视的服务型控件**，用于封装非 UI 能力。许多原本是控件的能力在 v1 中被重写为 Service，如 `FilePicker`、`Clipboard`、`SharedPreferences`、`StoragePaths`、`SecureStorage`、`HapticFeedback`、`ShakeDetector`，以及扩展中的 `Audio`、`AudioRecorder`、`Geolocator` 等。

Service 的方法都是**可 await 的异步方法**（v1 已统一移除 `_async` 后缀，直接 `await service.method()`）。

### 8.2 两种使用方式

**（1）一次性调用：直接实例化并 `await`**——用于无需持久化、无事件回调的场景（拿路径、读写剪贴板/偏好、选文件）：

```python
@ft.component
def FilePickerButton():
    async def pick(e):
        files = await ft.FilePicker().pick_files(allow_multiple=True)
        for f in files:
            print(f.name)

    return ft.FilledButton("选择文件", on_click=pick)
```

**（2）持久 + 事件型服务：必须加入 `page.services`**——服务需要"存活"并触发事件回调时（如 `Audio` 播放、`FilePicker` 上传进度 `on_upload`、`Geolocator` 位置流），实例必须加入 `page.services`，否则事件不生效：

```python
import flet as ft
import flet_audio as fta


def main(page: ft.Page):
    audio = fta.Audio(src="song.mp3", on_state_change=lambda e: print(e.state))
    page.services.append(audio)          # 事件型服务必须注册
    page.add(App(audio))


ft.run(main)
```

> 跨多层复用同一服务实例时，用 `create_context()` / `use_context()` 注入，避免逐层透传（见 5.5）。

### 8.3 FilePicker 方法（async 直接返回结果）

v1 的 FilePicker 不再用 "result 事件回调"，而是 async 方法直接返回结果：

```python
files = await ft.FilePicker().pick_files(allow_multiple=True)
file_name = await ft.FilePicker().save_file(file_name="out.txt")
dir_name = await ft.FilePicker().get_directory_path()
```

---

## 9. 数据存储

### 9.1 SharedPreferences（原 client storage）

v0 的"client storage"在 v1 成为 `SharedPreferences` 服务。方法有 `get` / `set` / `get_keys` / `contains_key` / `remove` / `clear`，支持 `int`、`float`、`bool`、`list[str]` 等值。适合存储轻量键值（用户偏好、令牌、开关）：

```python
await ft.SharedPreferences().set("theme", "dark")
theme = await ft.SharedPreferences().get("theme")
```

> 敏感数据（令牌、密钥）改用 `SecureStorage` 服务，底层走各平台原生安全存储（iOS/macOS Keychain、Windows Credential Manager、Linux libsecret、Android Keystore）。

### 9.2 剪贴板（Clipboard）

`Clipboard` 服务：文本用 `set()` / `get()`，图片用 `set_image()` / `get_image()`，文件引用用 `set_files()` / `get_files()`：

```python
await ft.Clipboard().set("copied text")
text = await ft.Clipboard().get()
```

### 9.3 文件系统路径（StoragePaths）

用 `StoragePaths` 服务跨平台获取常用目录（基于 `path_provider`）。**注意：这些方法在 Web 模式不支持，会抛 `FletUnsupportedPlatformException`**：

```python
sp = ft.StoragePaths()
docs_dir = await sp.get_application_documents_directory()
cache_dir = await sp.get_application_cache_directory()
tmp_dir = await sp.get_temporary_directory()
```

完整方法清单（0.86.0 实测）：`get_application_documents_directory` / `get_application_cache_directory` / `get_application_support_directory` / `get_temporary_directory` / `get_downloads_directory` / `get_library_directory` / `get_external_storage_directory` / `get_external_storage_directories` / `get_external_cache_directories` / `get_console_log_filename`（控制台日志文件路径，见 §16）。

**不要**硬编码平台路径；桌面/移动/Web 差异应交给此 API 处理。

### 9.4 持久化数据库

需要结构化持久化时，在 `services` 层封装（如 SQLite/`aiosqlite`），所有读写走 async，路径用 `StoragePaths` 获取（非 Web）。UI 层不直接触碰数据库。

---

## 10. UI、主题与样式

### 10.1 对话框 / 弹层

v1 对对话框管理提供**两套 API**，按编程范式选用：

| 范式 | API | 适用场景 |
|------|-----|----------|
| **声明式（推荐）** | `ft.use_dialog()` Hook | `@ft.component` 组件 |
| 命令式 | `page.show_dialog()` / `page.pop_dialog()` | 非 `@ft.component` 的旧式 `main(page)` 写法 |

**声明式应用（本手册唯一推荐范式）必须用 `ft.use_dialog()`**。`page.show_dialog/pop_dialog` 是命令式 API，**不适合 `@ft.component` 组件**——在声明式组件中调用会绕过框架的状态驱动渲染，导致 dialog 状态与组件状态不同步。官方在 0.85 发布公告中明确："That model doesn't fit declarative apps, where the UI is supposed to be a function of state."

**`use_dialog()` 接受所有 `DialogControl` 子类**（源码核验 `flet/controls/dialog_control.py`）：

| 控件 | 用途 |
|------|------|
| `ft.AlertDialog` | 模态对话框（确认/表单/详情） |
| `ft.DatePicker` | 日期选择器 |
| `ft.TimePicker` | 时间选择器 |
| `ft.SnackBar` | 底部临时消息 |
| `ft.Banner` | 顶部横幅 |
| `ft.BottomSheet` | 底部抽屉 |

以上 6 类控件在声明式组件中**一律用 `ft.use_dialog()`**，不用 `page.show_dialog()`。`FilePicker` 不是 `DialogControl`（是 `Service`），见 §8.3。

#### `ft.use_dialog()` 用法

每次渲染调用一次，传 `DialogControl` 显示，传 `None` 隐藏：

```python
@ft.component
def DeleteButton():
    show, set_show = ft.use_state(False)

    ft.use_dialog(
        ft.AlertDialog(
            title=ft.Text("确认"),
            content=ft.Text("确定要删除吗？"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: set_show(False)),
                ft.FilledButton("删除", on_click=lambda e: set_show(False)),
            ],
        )
        if show
        else None
    )

    return ft.FilledButton("删除", on_click=lambda e: set_show(True))
```

**机制说明（frozen-diff）**：组件重渲染时若传入新的 `DialogControl` 实例，hook 会逐字段 diff 前后实例，只发出实际变更的字段增量（而非整体替换）。这意味着 `AlertDialog` 内的 `TextField` 能跨重渲染保持光标、焦点、选区——即使 Python 侧每次都构造全新控件对象。同一组件可多次调用 `use_dialog()` 管理独立对话框（如重命名 + 删除两个 dialog）。

> **项目集成提示**：AStockScreener 项目当前 dialog 方案见 [CONTRIBUTING.md](../CONTRIBUTING.md) 与本手册 §0 分叉表第 8 行；项目规范覆盖本手册通用推荐。

### 10.2 抽屉（Drawer）

V1 仍保留 `page.drawer` / `page.end_drawer` 属性（用于挂载 `NavigationDrawer` 实例），但**展示/关闭改为方法调用**：

- 展示：`page.show_drawer()` / `page.show_end_drawer()`；
- 关闭：`page.close_drawer()` / `page.close_end_drawer()`。

```python
page.drawer = ft.NavigationDrawer(controls=[...])
page.show_drawer()      # 展示
page.close_drawer()     # 关闭
```

> 不要再用 V0 的 `page.open(drawer)` / `page.close(drawer)`（已移除，抛 `AttributeError`）；统一用上面的 `show_drawer` / `close_drawer`。对话框/Banner/SnackBar 等其它弹层：声明式组件用 `ft.use_dialog()` Hook（见 §10.1），命令式 `main(page)` 写法用 `page.show_dialog()` / `page.pop_dialog()`。

### 10.3 按钮：用 `content` 而非 `text`

**破坏性变更**：所有按钮不再有 `text` 属性。简单文本仍可作为第一个位置参数传入，复杂内容用 `content`：

```python
ft.FilledButton("保存")                       # 简单文本
ft.FilledButton(content=ft.Row([ft.Icon(ft.Icons.SAVE), ft.Text("保存")]))
```

### 10.4 间距/边框：用类方法，别用模块级函数

**破坏性变更**：模块级 `ft.padding.all()`、`ft.margin.symmetric()`、`ft.border_radius.only()` 等已移除。改用对应类的类方法：

```python
ft.Container(
    padding=ft.Padding.all(16),
    margin=ft.Margin.symmetric(vertical=8),
    border_radius=ft.BorderRadius.all(12),
)
```

### 10.5 主题

#### 10.5.1 Material 3 为默认设计语言

Flet 0.86.0 底层 Flutter 3.16+，**Material 3（Material You）是默认且唯一推荐的设计语言**。`ft.Theme.use_material3` 属性默认 `None`（透传给 Flutter，等同 `True`）。

**关键事实**（源码核验 `flet/controls/theme.py`）：

- `use_material3: Optional[bool] = None` — 注释明确为 "opt-out flag"，即默认启用 M3
- 仅当显式设置 `use_material3=False` 时才回退 Material 2 行为
- M3 影响多个组件的默认行为：`AlertDialog.icon_color`、`ProgressBar` track gap、`Tabs` indicator 样式、`IconButton` tooltip 展示、`Switch` thumb 形状等

**项目约定**（AStockScreener）：不设置 `use_material3`，保持 M3 默认。`theme.py` 构建 `ft.Theme(color_scheme=...)` 隐式运行在 M3 模式。若需 opt-out 某组件的 M3 行为，查阅 [Flet 源码](https://github.com/flet-dev/flet) 中该组件对 `use_material3` 的分支判断。

#### 10.5.2 明暗主题与颜色

- 用 `page.theme` / `page.dark_theme` + `page.theme_mode` 支持明暗；
- `page.theme_animation_style`（0.85）可自定义明暗切换的时长与曲线，或用 `AnimationStyle.no_animation()` 关闭；
- 颜色用 `ft.Colors.*`、图标用 `ft.Icons.*`（枚举，享受 IDE 补全与类型检查）；
- 注意：3/4 位十六进制短色（如 `#c00`）在旧版本曾渲染异常，0.85 已修复，但建议统一用 6/8 位完整写法。

### 10.6 布局与响应式

布局与自适应窗口是独立主题，详见 **第 11 章「自适应与响应式窗口布局」**。此处仅记两条速用规则：

- 用 `ResponsiveRow` + `col` 做栅格响应式；`col=0` 表示隐藏；
- 开启 `auto_scroll` 时必须同时显式设置 `scroll`，否则不生效。

### 10.7 表单输入组件

项目大量使用 `Dropdown` / `TextField` / `Slider` / `Switch` / `Checkbox`，统一规则如下。

#### Dropdown

```python
ft.Dropdown(
    label="策略",
    options=[ft.DropdownOption(key="macd", text="MACD")],
    value="macd",
    on_change=_on_change,
)
```

- **M3 默认**：`ft.Dropdown` 是 Material 3 风格；若需 M2 风格用 `ft.DropdownM2`（源码 `flet/controls/material/dropdown.py`）
- **V1 破坏性变更**：`options` 接受 `ft.DropdownOption` 列表（非旧 `ft.dropdown.Option`）
- 声明式组件中 `value` 应由 `use_state` 驱动，`on_change` 调 `set_state(e.control.value)`
- 大量选项时用 `ft.DropdownOption` 的 `content` 属性自定义行渲染

#### TextField

```python
ft.TextField(
    label="API Key",
    password=True,           # 密码输入（can_reveal_password=True 显示切换按钮）
    value=api_key,
    on_change=lambda e: set_api_key(e.control.value),
)
```

- 声明式中 `value` 必须受控（由 `use_state` 驱动），否则重渲染会丢失输入
- 密码/密钥字段用 `password=True` + `can_reveal_password=True`
- 多行文本用 `multiline=True` + `min_lines` / `max_lines`
- `ft.use_dialog()` 包裹的 `AlertDialog` 内的 `TextField` 能跨重渲染保持焦点（frozen-diff 机制，见 §10.1）

#### Slider / Switch / Checkbox

```python
ft.Slider(min=0, max=100, divisions=10, value=50, on_change=_on_change)
ft.Switch(label="启用", value=True, on_change=_on_change)
ft.Checkbox(label="创建数据库", value=False, on_change=_on_change)
```

- 声明式中 `value` 受控于 `use_state`
- `Slider` 的 `divisions` 设置后为离散刻度（M3 风格）
- `Switch` 在 M3 下默认有 thumb icon，`Switch.thumb_icon` 可自定义

### 10.8 选择器组件

#### DatePicker

```python
@ft.component
def DateRangePicker():
    show, set_show = ft.use_state(False)
    picked, set_picked = ft.use_state("")

    ft.use_dialog(
        ft.DatePicker(
            first_date=date(2020, 1, 1),
            last_date=date.today(),
            value=date.today(),
            on_change=lambda e: set_picked(str(e.control.value)),
            on_dismiss=lambda e: set_show(False),
        )
        if show
        else None
    )

    return ft.OutlinedButton(str(picked or "选择日期"), on_click=lambda e: set_show(True))
```

- `ft.DatePicker` 是 `DialogControl` 子类（源码核验 MRO：`DatePicker → DialogControl`），**完全支持 `ft.use_dialog()` 声明式管理**
- 声明式中 `show` 状态驱动 `use_dialog(date_picker if show else None)`，`on_dismiss` 调 `set_show(False)` 关闭
- 全局样式用 `ft.Theme.date_picker_theme`（`DatePickerTheme`）配置

#### SegmentedButton

```python
ft.SegmentedButton(
    selected={"realtime"},
    segments=[
        ft.Segment(key="realtime", icon=ft.Icons.SPEED, label="实时"),
        ft.Segment(key="history", icon=ft.Icons.HISTORY, label="历史"),
    ],
    on_change=_on_change,
)
```

- M3 风格分段控件，`selected` 是 `set[str]`（多选）或单值（单选）
- 声明式中 `selected` 由 `use_state` 驱动
- 适用于 2-5 个互斥选项的场景，比 `Dropdown` 更直观

### 10.9 导航组件

#### NavigationRail

```python
ft.NavigationRail(
    selected_index=0,
    destinations=[
        ft.NavigationRailDestination(icon=ft.Icons.HOME, label="首页"),
        ft.NavigationRailDestination(icon=ft.Icons.SEARCH, label="选股"),
    ],
    on_change=_on_change,
    extended=False,                          # 折叠时仅图标
    label_type=ft.NavigationRailLabelType.ALL,
)
```

- 桌面应用主导航首选，配合 `VerticalDivider` 分隔内容区
- `selected_index` 由 `use_state` 驱动，`on_change` 调 `set_state(e.control.selected_index)`
- `extended=True` 时展开显示标签，`False` 时仅图标（配合折叠按钮）
- M3 下 `label_type` 控制标签显示策略：`ALL` / `SELECTED` / `NONE`

### 10.10 列表与展开组件

#### ListView

```python
ft.ListView(
    controls=[...],
    expand=True,
    spacing=4,
    auto_scroll=True,    # 新内容自动滚动到底部
    padding=ft.Padding.all(8),
)
```

- 长列表首选（虚拟滚动），比 `Column` + `scroll=True` 性能更好
- `auto_scroll=True` 时必须设置 `scroll` 属性（V1 要求）
- 流式更新场景用 `use_ref` 缓冲 + 节流 `set_state`（见项目 `screener_view.py` AI 日志流）

#### ExpansionTile / ListTile

```python
ft.ExpansionTile(
    title=ft.Text("高级设置"),
    controls=[...],
    initially_expanded=False,
    on_change=_on_change,
)

ft.ListTile(
    leading=ft.Icon(ft.Icons.STOCKS),
    title=ft.Text("贵州茅台"),
    subtitle=ft.Text("600519.SH"),
    trailing=ft.Text("+2.3%"),
    on_click=_on_click,
)
```

- `ExpansionTile` 用于可折叠分组，`controls` 为展开内容
- `ListTile` 用于单行列表项，`leading` / `title` / `subtitle` / `trailing` 四槽位
- M3 下 `ListTile` 默认有触控波纹，`on_click=None` 时无波纹
- 声明式中 `initially_expanded` 应由 `use_state` 驱动的 `expanded` 属性替代

### 10.11 数据展示组件

#### DataTable

```python
ft.DataTable(
    columns=[ft.DataColumn(ft.Text("代码"), numeric=False)],
    rows=[
        ft.DataRow(
            cells=[ft.DataCell(ft.Text("600519.SH"))],
            on_select_changed=_on_select,
        ),
    ],
    heading_row_color=ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE),
    data_row_color={ft.ControlState.HOVERED: ft.Colors.with_opacity(0.04, ft.Colors.ON_SURFACE)},
)
```

- 大数据量（>100 行）用 `ListView` + 自定义行（项目 `virtual_table.py` 模式），`DataTable` 性能有限
- `numeric=True` 的列右对齐
- 全局样式用 `ft.Theme.data_table_theme`（`DataTableTheme`）配置
- 声明式中 `rows` 应由 state 驱动重新构造，不建议 `use_ref` 缓存行对象

#### ProgressBar / ProgressRing

```python
ft.ProgressBar(value=0.5, visible=is_loading, color=AppColors.PRIMARY)  # 确定性进度
ft.ProgressRing(visible=is_loading)                                      # 不确定等待
ft.ProgressBar(visible=is_loading)                                       # 不确定等待（线性）
```

- **确定性进度**（已知百分比）用 `value=0.0~1.0`
- **不确定性等待**（未知百分比）省略 `value` 或设为 `None`
- `ProgressRing` 圆形 / `ProgressBar` 线性，按场景选用：短操作用 Ring，长任务用 Bar
- M3 下 `ProgressBar` 有 track gap，`year_2023=True` 可关闭（旧版样式）

### 10.12 文本与内容组件

#### Markdown

```python
ft.Markdown(
    value=md_content,
    selectable=True,
    on_tap_link=safe_open_url,    # 必须用安全回调（见下）
    extension_set=ft.MarkdownExtensionSet.GITHUB,
    code_theme="atom-one-dark",
)
```

- **安全红线**：`on_tap_link` 必须用安全回调（项目 `_markdown_safe.py` 的 `safe_open_url`），白名单域名才放行，防止钓鱼/恶意站点
- LLM 生成内容可能含恶意链接，禁止直接 `webbrowser.open(url)`
- `extension_set` 支持 GitHub Flavored Markdown（表格/删除线/任务列表）
- `code_theme` 代码高亮主题：`atom-one-dark` / `github` / `vs` 等

#### Tooltip

```python
ft.IconButton(
    icon=ft.Icons.REFRESH,
    tooltip=I18n.get("refresh"),   # i18n key，locale 变化自动重渲染
    on_click=_on_click,
)
```

- 所有可交互控件支持 `tooltip` 属性（M3 风格自动展示）
- i18n 场景 `tooltip` 用 `I18n.get(key)`，声明式组件 locale 变化时自动重渲染
- 长 tooltip 用 `ft.Tooltip(message=..., text_style=...)` 独立控件自定义样式

#### Badge（M3 新增）

```python
ft.Badge(
    content=ft.Icon(ft.Icons.NOTIFICATIONS),
    label=ft.Text("3"),
    offset=ft.Offset(2, -2),
)
```

- M3 角标控件，用于通知计数
- `label` 为 `None` 时显示小圆点（无数字）

### 10.13 菜单与交互组件

#### PopupMenuButton

```python
ft.PopupMenuButton(
    icon=ft.Icons.MORE_VERT,
    items=[
        ft.PopupMenuItem(text="导出 CSV", on_click=_export),
        ft.PopupMenuItem(text="刷新", on_click=_refresh),
        ft.PopupMenuItem(),  # 分隔线
        ft.PopupMenuItem(text="删除", checked=False, on_click=_delete),
    ],
)
```

- 声明式中 `items` 由 state 驱动构造
- 空构造 `ft.PopupMenuItem()` 为分隔线
- M3 下 `checked` 属性支持勾选态

#### GestureDetector

```python
ft.GestureDetector(
    content=ft.Container(width=4, bgcolor=AppColors.BORDER),
    on_horizontal_drag_update=_on_drag,
    on_horizontal_drag_start=_on_drag_start,
    on_hover=_on_hover,
    mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
)
```

- 自定义手势识别（拖拽/悬停/长按等）
- 拖拽场景用 `use_ref` 缓存即时坐标 + 节流 `set_state`（见项目 `resizable_splitter.py`）
- `mouse_cursor` 设置鼠标悬停光标样式
- 性能要求：拖拽 <16ms/帧（项目约定），用 `use_ref` 避免 `use_state` 触发 re-render

---

## 11. 自适应与响应式窗口布局（Adaptive & Responsive）

这是跨平台应用最容易被忽视、却直接决定"能否一套代码同时在手机 / 平板 / 桌面窗口 / Web 各种尺寸下都好用"的关键。Flet 里要区分两个概念：

- **Responsive（响应式）**：同一套控件，根据**可用尺寸**改变布局（列数、排布、显示/隐藏）。
- **Adaptive（自适应平台）**：根据**运行平台**切换视觉风格（Material vs Cupertino）与交互习惯。

两者应结合使用：响应式解决"大小"，自适应解决"平台"。

### 11.1 断点体系（Breakpoints）

`ResponsiveRow` 把每一行划分为 **12 个虚拟列**，子控件通过 `col` 声明在不同断点下占几列。Flet 标准断点（以像素为界）：

| 断点 | 触发宽度 | 典型设备 |
| --- | --- | --- |
| `xs` | < 576px | 手机竖屏 |
| `sm` | ≥ 576px | 手机横屏 / 小平板 |
| `md` | ≥ 768px | 平板 |
| `lg` | ≥ 992px | 小桌面窗口 |
| `xl` | ≥ 1200px | 桌面 |
| `xxl` | ≥ 1400px | 大屏 |

`col` 可传：

- 单个数字：所有断点统一（如 `col=6` 恒占半行）；
- 字典：按断点分别指定（如 `col={"xs": 12, "md": 6, "xl": 4}`）。

> 约定：始终为最小断点 `xs` 提供兜底值（通常 `12`），确保窄屏不塌陷。

### 11.2 用 `ResponsiveRow` 做栅格布局

```python
import flet as ft


@ft.component
def CardGrid():
    # 手机整行，平板半行，桌面三分之一
    return ft.ResponsiveRow(
        controls=[
            ft.Container(
                ft.Text("卡片 A"), padding=10, bgcolor=ft.Colors.BLUE_100,
                col={"xs": 12, "md": 6, "lg": 4},
            ),
            ft.Container(
                ft.Text("卡片 B"), padding=10, bgcolor=ft.Colors.GREEN_100,
                col={"xs": 12, "md": 6, "lg": 4},
            ),
            ft.Container(
                ft.Text("卡片 C"), padding=10, bgcolor=ft.Colors.AMBER_100,
                col={"xs": 12, "md": 12, "lg": 4},
            ),
        ],
        run_spacing={"xs": 10},   # 换行间距也可按断点配置
        spacing=10,
    )
```

**要点与坑**：

- `ResponsiveRow` 需要**有界宽度**；放进无界宽度的容器会显式报错（应让其父级 `expand` 或有明确宽度）；
- 表单场景直接给 `TextField` 等设 `col`，即可实现"桌面并排、手机堆叠"；
- 内容可能超高时，为 `ResponsiveRow` 或页面设置 `scroll`。

### 11.3 尺寸判定：`page.width` 与断点纯函数

需要"按当前尺寸切换整块布局"（而非仅栅格重排）时，用 `page.width` / `page.height` 判定当前形态。窗口变化通过 `page.on_resize` 事件感知。

> 版本提示：v1 中事件名为 **`page.on_resize`**（v0 曾一度叫 `on_resized`，v1 又改回 `on_resize`）。

**最佳实践**：把"断点判定"抽成一个纯函数，组件只依据其返回值渲染，便于测试与复用：

```python
def form_factor(width: float) -> str:
    if width >= 992:
        return "desktop"
    if width >= 768:
        return "tablet"
    return "mobile"
```

声明式的完整实现见 11.4。

### 11.4 声明式自适应外壳（推荐实现）

在 `@ft.component` 中，用 `use_state` 保存当前宽度，在 `use_effect` 里订阅 `page.on_resize` 更新它，组件即自动按尺寸重渲染：

```python
import flet as ft


@ft.component
def AdaptiveShell(content):
    width, set_width = ft.use_state(lambda: ft.context.page.width or 0)

    def subscribe():
        page = ft.context.page
        page.on_resize = lambda e: set_width(page.width or 0)
        return lambda: setattr(page, "on_resize", None)  # 卸载时清理

    ft.use_effect(subscribe, [])

    is_wide = width >= 768
    if is_wide:
        return ft.Row([NavRail(), content], expand=True)
    return ft.Column([content, NavBar()], expand=True)
```

### 11.5 局部尺寸感知：`on_size_change`

当只关心**某个容器**的实际尺寸（而非整页），用布局控件的 `LayoutControl.on_size_change`（0.81 引入）。适合做"容器查询"式的局部自适应，比监听全页更精准、开销更低。

### 11.6 导航模式随尺寸切换（关键落地模式）

跨端应用最常见的自适应需求是导航形态：

- **移动端（窄）**：`NavigationBar`（底部）或 `NavigationDrawer`（抽屉）；
- **桌面 / 平板（宽）**：`NavigationRail`（侧栏），宽屏可 `extended=True` 展开标签。

按 `page.width` 判定形态选择对应控件即可（结合 11.3 / 11.4）。`NavigationRail`（0.85）支持 `scrollable` 与顶部/底部固定项，长菜单也能容纳。

### 11.7 自适应平台风格：`adaptive=True`

设 `page.adaptive = True`，Flet 会在 iOS/macOS 上渲染 Cupertino 风格、在 Android/Web 上渲染 Material 风格。`adaptive` 是**可递归**属性，容器类控件会向子级传递；也可在单个控件（如 `AppBar`、`Slider`、按钮）上单独设 `adaptive=True` 强制平台化。

```python
page.adaptive = True   # 整个应用按平台自适应 Material / Cupertino
```

> 注意：v1 已移除旧的 `page.design`，统一用 `page.adaptive`。

判断具体平台可读 `page.platform`（`ft.PagePlatform.IOS/ANDROID/WINDOWS/...`），或用便捷方法 `page.platform.is_mobile()` / `page.platform.is_desktop()`；判断是否 Web 用 `page.web`。据此做更细的差异化（如手势、间距、Web 专属逻辑）。

### 11.8 安全区与桌面窗口

- **移动端刘海/圆角**：用 `ft.SafeArea` 包裹根内容，避开状态栏与手势条；
- **桌面窗口控制**：通过 `page.window` 管理窗口尺寸与行为，常用：
  - `page.window.width` / `height` / `min_width` / `min_height`：设定初始与最小尺寸，防止窗口被拉到布局崩溃；
  - `page.window.maximized` / `resizable` / `center()`：窗口状态与居中；
  - 需要启动即隐藏窗口时用 `ft.AppView.FLET_APP_HIDDEN` + `page.window.visible`（0.86.0 修复了 Windows 启动闪窗问题）。

```python
def config(page: ft.Page):
    page.window.min_width = 400
    page.window.min_height = 600
    page.window.width = 1024
    page.window.height = 720
```

### 11.9 自适应布局 Checklist

- [ ] 每个 `col` 都提供 `xs` 兜底（通常 12）；
- [ ] 断点判定抽成纯函数，UI 只消费其结果；
- [ ] 用 `page.on_resize` + `page.width` 切换"整体布局形态"，用 `ResponsiveRow`/`col` 处理"栅格重排"，用 `on_size_change` 处理"局部容器"；
- [ ] 导航随尺寸切换：窄屏 `NavigationBar`/`Drawer`，宽屏 `NavigationRail`；
- [ ] 跨平台风格用 `page.adaptive = True`，必要时用 `page.platform` 做细化；
- [ ] 移动端根内容用 `SafeArea` 包裹；
- [ ] 桌面设置 `page.window.min_width/min_height`，防止窗口过小导致布局崩溃；
- [ ] 声明式下把宽度存入 `use_state`，让组件自动随尺寸重渲染，并在 `use_effect` 清理 `on_resize`。

---

## 12. 资源（Assets）管理

- 所有静态资源放在 `assets/` 目录，运行/构建时通过 `assets_dir` 指定；
- 引用用相对路径 `Image(src="images/logo.png")`；Web 部署在非根路径时，绝对路径 `src="/images/..."` 在 0.85 已修复，但**优先用相对路径**更稳妥；
- 字体放 `assets/fonts/`，通过 `page.fonts` 注册后在主题中引用；
- 得益于 MessagePack 二进制协议，图片等二进制数据**无需再 base64 编码**传输。

---

## 13. 构建、打包与部署

### 13.1 `flet run`（开发）

```bash
flet run                    # 桌面窗口运行
flet run --web              # 浏览器运行
flet run -d                 # 开启热重载（watch 文件变化）
```

### 13.2 `flet build`（生产多平台）

```bash
flet build web              # 产出静态 Web（默认 SKWASM 优先，回退 CanvasKit）
flet build apk              # Android
flet build ipa              # iOS
flet build windows / macos / linux
```

**关键实践**：`flet build` 依赖 `pyproject.toml` 的 `dependencies` 精确解析，务必 pin 好 `flet` 与所有扩展版本，否则会拉到不匹配的版本导致构建/运行失败。

### 13.3 Web 部署要点

- v1 默认在受支持浏览器优先用 **SKWASM**（WebAssembly 渲染器），不支持时自动回退到 **CanvasKit**（`WebRenderer` 枚举仅 `AUTO`/`CANVAS_KIT`/`SKWASM` 三成员，已无独立 Dart2JS 目标）；
- **离线/无 CDN 模式**：`ft.run(main, no_cdn=True)` 或环境变量 `FLET_WEB_NO_CDN=1`，或构建时 `flet build web --no-cdn`，把 CanvasKit/SkWASM/Pyodide/字体打包进应用，适合内网/隔离环境；
- 可将 Flet Web 应用**嵌入**现有网页的某个 HTML 元素，甚至在同页渲染多个视图。

### 13.4 构建缓存与提速

0.86.0 会把 `flet-build-template.zip` 按版本缓存到 `$FLET_CACHE_DIR/build-template/v<version>/`（默认 `~/.flet/cache/`），并把 `FLET_CACHE_DIR` 透传给 Gradle，显著减少 Android 每次构建的"Creating app shell"耗时。CI 中应**缓存 `~/.flet/cache/` 目录**以复用。

### 13.5 桌面打包 `flet pack`

`flet pack` 用于桌面单文件打包；注意 Windows/Linux 打包在旧版本曾有客户端归档缺失问题，0.85 已修复，建议基于 0.86.0 打包。

---

## 14. 测试策略

单线程 async + 声明式组件让测试更可行：

- **服务层单测**：`services/` 内的纯逻辑与 IO（用 `pytest-asyncio` 测 async 方法，网络用 mock/httpx 的 transport）——这是投资回报最高的测试；
- **状态/reducer 单测**：把状态转换写成纯函数（reducer 模式），可脱离 UI 直接断言；
- **组件测试**：`@ft.component` 是普通函数，可对给定状态断言其返回的控件树结构；
- **集成测试**：官方在 0.80 起提供集成测试能力，端到端验证关键路径。

示例（async 服务测试）：

```python
import pytest


@pytest.mark.asyncio
async def test_fetch_user(monkeypatch):
    repo = UserRepository(client=FakeAsyncClient())
    user = await repo.get(1)
    assert user.name == "Alice"
```

---

## 15. 性能优化

- **保持事件循环畅通**：绝不在处理器里做阻塞调用；CPU 密集用 `asyncio.to_thread`；
- **精简重渲染**：声明式下把状态就近放置，避免顶层大组件因小状态整棵重渲染；用 `use_ref` 存不影响 UI 的可变值；
- **列表用稳定 identity**：`ReorderableListView`/长列表项提供稳定 key，帮助差量算法复用节点；
- **依赖自动更新**：声明式下用状态 setter 触发重渲染即可，无需手动 `update()`，也不要持有控件引用改属性；
- **图片/二进制**：依赖 MessagePack 直接传二进制，避免自行 base64；
- **Web 首屏**：需要更快下载与运行时优先 WASM；内网用 no-CDN 模式减少外部请求；
- **内存**：为定时器/订阅在 `use_effect` 返回清理函数；频繁增删的重型控件（如视频）注意及时移除。

---

## 16. 错误处理与可观测性

- 事件处理器中的 async 代码务必 `try/except`，把错误反馈到 UI 状态（如 `set_error`），而非静默吞掉；
- Web 端可用 `FletApp.app_error_message` 定制加载/错误页文案（另有 `app_startup_screen_message`）；
- 桌面/移动可通过控制台日志文件排查：`await ft.StoragePaths().get_console_log_filename()`；
- 关键业务在 `services` 层统一记录日志（`logging`），UI 层只展示用户可读信息。

---

## 17. 迁移提示：从 v0（0.28.x）到 v1（0.86.0）

若维护旧应用，注意这是**手动迁移**、非平滑升级。高频改动清单：

| v0 写法 | v1 写法 |
| --- | --- |
| `ft.app(target=main)` | `ft.run(main)` |
| 到处 `control.update()` | 依赖自动更新，仅长任务用 `yield` |
| `page.open(dlg)` / `page.close(dlg)` | `page.show_dialog(dlg)` / `page.pop_dialog()` |
| `page.client_storage` | `ft.SharedPreferences()` 服务（`get`/`set`，无 `_async`） |
| `page.set_clipboard()` / `get_clipboard()` | `ft.Clipboard().set()` / `.get()` |
| `xxx_async()` 方法 | 去掉 `_async` 后缀，直接 `await xxx()` |
| `ft.padding.all(8)` | `ft.Padding.all(8)`（`Margin`/`Border`/`BorderRadius` 同理） |
| `Button(text="OK")` | `Button("OK")` 或 `content=...` |
| `FilePicker` 作为控件 + result 事件 | `FilePicker` 作为 Service，加入 `page.services`，async 直接返回 |
| `page.open(drawer)` / `page.close(drawer)` | `page.show_drawer()` / `page.close_drawer()`（`page.drawer`/`page.end_drawer` 属性保留） |
| `page.on_resized` | `page.on_resize`（v1 改回此名） |
| `ft.padding.symmetric()` 等模块级布局函数 | 对应类方法 `ft.Padding.symmetric()`（见 10.4） |
| 图表在 core 里 | 独立包 `flet-charts` |
| 多线程 + `time.sleep()` | 单线程 async + `await asyncio.sleep()` |
| `e.target` 是字符串 | `e.target` 是整数 |

同时把扩展包升到 `0.2.x+` 并 pin 版本。官方在 GitHub 维护"已知破坏性变更"清单，迁移时对照排查。

---

## 18. 团队约定 Checklist（可直接采纳）

- [ ] `pyproject.toml` 中精确 pin `flet==0.86.0` 及所有扩展版本；
- [ ] 每项目独立虚拟环境，禁止 v0/v1 混装；
- [ ] 统一采用声明式 `@ft.component` + Hooks（本手册唯一推荐范式），不写命令式 UI 构建，不手动改控件属性；
- [ ] 所有事件处理器写成 `async def`，禁用 `time.sleep()`/同步阻塞 IO；
- [ ] CPU 密集任务一律 `asyncio.to_thread`；
- [ ] 业务/IO 全部下沉到 `services/`，UI 层不写业务逻辑；
- [ ] `use_state` 基于旧值更新时用函数式 setter；`use_effect` 必写清理函数；
- [ ] Service 实例记得加入 `page.services`；
- [ ] 文件路径统一走 `ft.StoragePaths()`（非 Web），资源走相对路径；
- [ ] 弹层：声明式组件用 `ft.use_dialog()` Hook（§10.1），命令式写法用 `show_dialog`/`pop_dialog`；间距用 `Padding/Margin/BorderRadius` 类方法；
- [ ] 主题：保持 Material 3 默认（不设 `use_material3`），明暗用 `page.theme`/`page.theme_mode`（§10.5）；
- [ ] 表单组件：`Dropdown`/`TextField`/`Slider`/`Switch`/`Checkbox` 的 `value` 受控于 `use_state`（§10.7）；
- [ ] 所有 `DialogControl` 子类（`AlertDialog`/`DatePicker`/`TimePicker`/`SnackBar`/`Banner`/`BottomSheet`）一律用 `ft.use_dialog()`，无例外（§10.1、§10.8）；
- [ ] `Markdown.on_tap_link` 必须用安全回调（`_markdown_safe.py`），禁止直接 `webbrowser.open`（§10.12）；
- [ ] 长列表用 `ListView`（虚拟滚动），大数据表用 `ListView`+自定义行（§10.10、§10.11）；
- [ ] 自适应布局：`ResponsiveRow`+`col`（带 `xs` 兜底）做栅格，`page.on_resize`/`page.width` 切换形态，`page.adaptive=True` 适配平台，桌面设 `window.min_width/min_height`；
- [ ] CI 缓存 `~/.flet/cache/` 加速构建；
- [ ] 为 services 与状态逻辑编写单测（`pytest-asyncio`）。

---

## 参考来源

- Flet 官方 API 参考（从源码生成，本手册据此逐条核验）：<https://docs.flet.dev/>
  - 服务：SharedPreferences / Clipboard / FilePicker / StoragePaths / Audio
  - Router 控件与路由 Hooks、events-and-state 概念页
- Flet 官方仓库与 CHANGELOG：<https://github.com/flet-dev/flet/blob/main/CHANGELOG.md>
- Flet 1.0 Alpha 公告（架构与破坏性变更）：<https://flet.dev/blog/introducing-flet-1-0-alpha>
- Flet 0.85 发布公告（Router / dialogs）：<https://flet.dev/blog/flet-v-0-85-release-announcement/>
- PyPI 版本历史：<https://pypi.org/project/flet/>

> 说明：Flet v1 仍处于 Beta（API 约 99% 稳定），个别细节可能随 `0.86+` 微调。落地前建议对照对应版本的官方 CHANGELOG 复核。