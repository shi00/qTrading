# Flet V1 项目差异与高风险 API

> 来源：从 man/flet-best-practices.md 迁移（原文件改为薄 stub 指向 docs/flet/）

> 面向 AStockScreener 工程团队的项目 Flet 集成差异指南

> Owner: UI 维护者
> 复核触发器: Flet 依赖版本变化（pyproject.toml）、关键 API 变化、架构红线/边界变化或 ADR 决策（见 [../adr/](../adr/)）
> 最后验证日期: 2026-07-16

---

## 0. 文档定位

本文件是 **项目 Flet 差异与高风险 API 清单**，仅记录 AStockScreener 相对 Flet 官方默认的**分叉点**、**项目验证过的高风险 API** 与 **R16 UI 阻塞红线**。通用 Flet 教程（路由、Services、存储、构建打包、移动/Web 适配、响应式布局、控件清单等）请直接查阅 [Flet 官方文档](https://docs.flet.dev/)，本文件不再复制，避免与上游漂移。

API 约束表、声明式组件契约、V1 声明式 UI 开发规范见 [v1-api-constraints.md](./v1-api-constraints.md)；升级时的验证步骤见 [upgrade-checklist.md](./upgrade-checklist.md)。

**优先级**（后者被前者覆盖）：

1. [CLAUDE.md](../../CLAUDE.md) — 项目宪法（红线 R1~R18、架构边界、交互准则）
2. [CONTRIBUTING.md](../../CONTRIBUTING.md) — 项目实现规范入口索引
3. [v1-api-constraints.md](./v1-api-constraints.md) — Flet V1 API 关键约束
4. **本文件** — 项目差异与高风险 API

---

## 1. 当前锁定版本

适用版本：**Flet V1**（版本号从 [`pyproject.toml`](../../pyproject.toml) 读取）。

项目锁定三个包：`flet` / `flet-desktop` / `flet-charts`，具体版本以 `pyproject.toml` 为准。本文件不写补丁版本号，避免与 `pyproject.toml` 漂移；升级时同步更新本文件「最后验证日期」与 [upgrade-checklist.md](./upgrade-checklist.md)。

---

## 2. 项目 MVVM + use_viewmodel 契约

项目采用 **MVVM + 声明式渲染** 复合范式。View = `@ft.component` 声明式组件，经项目统一的 `use_viewmodel` hook 消费 ViewModel。实现见 [`ui/hooks.py`](../../ui/hooks.py)。

### 2.1 use_viewmodel 双模式（互斥）

| 模式 | 签名 | 适用场景 |
|------|------|---------|
| **内部模式** | `use_viewmodel(factory=...)` | hook 实例化 VM，卸载时退订 + dispose（`dispose_on_unmount=True` 时） |
| **外部模式** | `use_viewmodel(vm=...)` | VM 由消费方持有，hook 仅订阅 state，**永远不 dispose** |

**契约**：`factory=` 与 `vm=` 互斥（同时传或都不传抛 `ValueError`）。

### 2.2 ViewModel 契约

VM 须满足 `_ViewModelProtocol`（结构性类型，见 [`ui/hooks.py`](../../ui/hooks.py)）：

- `state` 属性：返回**不可变 snapshot**（frozen dataclass），View 据此渲染
- `subscribe(callback) -> unsub`：注册 state 变化回调，返回退订函数
- `dispose()`：释放资源（订阅、定时器、任务等）

**禁止**：VM 内 import flet / 持有 Flet 控件 / 调 `page.update()` 或 `control.update()` / 感知 locale。

### 2.3 i18n 契约

- VM 只产出 **i18n key** 或 `Message(key, params)` 对象
- View 按当前 locale 渲染（i18n locale 由独立状态源驱动）
- **禁止** VM 内调用 `I18n.get()`（VM 须保持 locale-agnostic）
- **禁止** View 手动 subscribe locale 变化（应通过 state 驱动）

---

## 3. 相对官方默认的项目分叉

| 维度 | Flet 官方默认 | 项目规范（优先） | 依据 |
|------|-------------|----------------|------|
| UI 模型 | 裸 `use_state`/`use_effect` 组件 | **MVVM + `use_viewmodel` hook** | [CLAUDE.md](../../CLAUDE.md) §3.2 |
| 适用范围 | Web/移动/桌面通用 | **仅桌面端**（`page.window.min_width=1280`） | [CONTRIBUTING.md](../../CONTRIBUTING.md) 响应式小节 |
| 声明式 Dialog | `ft.use_dialog()` Hook | **`ft.use_dialog()` Hook**（声明式组件内唯一契约） | [v1-api-constraints.md §声明式组件内 API 契约](./v1-api-constraints.md#声明式组件内-api-契约) |
| Dropdown 事件 | `on_change` | **`on_select`** | [v1-api-constraints.md §V0→V1 迁移 API 表](./v1-api-constraints.md#v0v1-迁移-api-表) 第 13 项 |
| `use_effect` cleanup | setup 返回 cleanup 函数 | **显式 `cleanup=` 参数传入** | [v1-api-constraints.md §声明式组件内 API 契约](./v1-api-constraints.md#声明式组件内-api-契约) |
| 异步阻塞段 | `asyncio.to_thread` / `page.run_thread` | **`ThreadPoolManager.run_async(TaskType.IO/CPU)`** | R16 红线（见 §5） |
| 响应式断点 | xs/sm/md/lg/xl/xxl 576~1400 | **compact/standard/ultra_wide 1200/1600/2400** | [`ui/theme.py`](../../ui/theme.py) `AppStyles` 断点常量 |
| 桌面打包 | `flet pack` | **PyInstaller**（[`AStockScreener.spec`](../../AStockScreener.spec)） | [依赖管理](../guides/dependency-management.md) |

---

## 4. 项目验证过的高风险 API

以下 API 是项目踩坑后验证的契约，**升级 Flet 时必须重新验证**。

### 4.1 `ft.use_dialog()`（声明式组件内唯一 Dialog 契约）

声明式 `@ft.component` 内一律用 `ft.use_dialog()`，**禁止** `page.show_dialog()` / `page.pop_dialog()`（命令式 API，会绕过状态驱动渲染）。

适用所有 `DialogControl` 子类：`AlertDialog` / `DatePicker` / `TimePicker` / `SnackBar` / `Banner` / `BottomSheet`。

```python
@ft.component
def DeleteButton():
    show, set_show = ft.use_state(False)
    ft.use_dialog(
        ft.AlertDialog(
            title=ft.Text("确认"),
            content=ft.Text("删除?"),
            actions=[ft.TextButton("取消", on_click=lambda e: set_show(False))],
        )
        if show
        else None
    )
    return ft.FilledButton("删除", on_click=lambda e: set_show(True))
```

机制：组件重渲染时 hook 逐字段 diff 前后 `DialogControl` 实例，只发增量；dialog 内 `TextField` 能跨重渲染保持焦点/选区。

### 4.2 Dropdown `on_select`（非 `on_change`）

项目 `ft.Dropdown` 事件统一用 `on_select`：

```python
ft.Dropdown(
    label="策略",
    options=[ft.DropdownOption(key="macd", text="MACD")],
    value="macd",
    on_select=_on_select,
)
```

### 4.3 `use_effect` cleanup 显式参数

`use_effect` 的 cleanup 通过**显式 `cleanup=` 关键字参数**传入，**不**通过 setup 返回值：

```python
def setup() -> None:
    unsub_ref.current = vm.subscribe(lambda s: set_state(s))

def cleanup() -> None:
    if unsub_ref.current is not None:
        unsub_ref.current()
        unsub_ref.current = None

ft.use_effect(setup, dependencies=[], cleanup=cleanup)
```

### 4.4 `use_viewmodel` 双模式

见 §2.1。`factory=` 与 `vm=` 互斥。

---

## 5. R16 UI 阻塞红线

**R16**：Flet 事件处理器中**同步阻塞段**（同步 HTTP、文件 IO、CPU 密集计算）必须 `await ThreadPoolManager.run_async()` 提交到线程池，禁止同步阻塞主循环。

**澄清**：本条针对同步阻塞段。async-native IO（`httpx.AsyncClient`、SQLAlchemy async、asyncpg）按原生 `await` 模型执行，不额外包线程池。
