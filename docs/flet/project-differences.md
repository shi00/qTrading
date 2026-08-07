# Flet V1 项目差异与高风险 API

> 来源：从 man/flet-best-practices.md 迁移（原文件改为薄 stub 指向 docs/flet/）

> 面向 AStockScreener 工程团队的项目 Flet 集成差异指南

> Owner: UI 维护者
> 复核触发器: Flet 依赖版本变化（pyproject.toml）、关键 API 变化、架构红线/边界变化或 ADR 决策（见 [../adr/](../adr/)）
> 最后验证日期: 2026-07-27

---

## 0. 文档定位

本文件是 **项目 Flet 差异与高风险 API 清单**，仅记录 AStockScreener 相对 Flet 官方默认的**分叉点**、**项目验证过的高风险 API** 与 **R16 UI 阻塞红线**。通用 Flet 教程（路由、Services、存储、构建打包、移动/Web 适配、响应式布局、控件清单等）请直接查阅 [Flet 官方文档](https://docs.flet.dev/)，本文件不再复制，避免与上游漂移。

API 约束表、声明式组件契约、V1 声明式 UI 开发规范见 [v1-api-constraints.md](./v1-api-constraints.md)；Flutter Web CanvasKit 渲染行为与 E2E 避坑指南见 [canvaskit-rendering-e2e-guide.md](./canvaskit-rendering-e2e-guide.md)；升级时的验证步骤见 [upgrade-checklist.md](./upgrade-checklist.md)。

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
| 响应式断点 | xs/sm/md/lg/xl/xxl 576~1400 | **沿用 Flet 默认断点**，视图栅格经 `AppStyles.COL_*` 预置配置统一消费 | [`ui/theme.py`](../../ui/theme.py) `AppStyles` |
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

### 4.5 ListView 视口高度为 0 时不生成子控件语义节点（E2E 杀手）

**背景**：PR #392 修复 `PaginatedTable` E2E 测试失败时定位的根因。

**现象**：`ft.ListView(build_controls_on_demand=False)` 在视口高度为 0（E2E 环境中父容器布局尚未稳定）时，Flutter 引擎仍可能跳过子控件语义节点生成，导致 Playwright `get_by_text` 找不到行内文本、`click_row_by_text` 全策略失败。`build_controls_on_demand=True` 时更严重（视口为 0 时 Flutter 不构建任何子控件）。

**根因**：Flutter ListView 的虚拟化机制依赖视口高度计算可见区域，视口高度为 0 时 `build_sliver_list` 不构建任何 child，语义节点（`flt-semantics`）也不生成。

**解决方案**：单页 ≤100 行规模下，改用 `ft.Column(scroll=ft.ScrollMode.ALWAYS)` 替代 `ListView`。Column 不做窗口化，所有行立即参与布局并生成 `flt-semantics` 节点。

**适用场景**：单页行数有明确上限（如 `page_size=100`、`MAX_ROWS_UI=100`）的表格。无上限场景仍需用 `ListView` 虚拟化，但必须确保父容器有稳定非零高度。

**升级触发条件**：单页行数 ≥500 或构建耗时 > 50ms 时，评估切换回 `ListView(build_controls_on_demand=True)` 并解决 E2E 视口高度为 0 时的子控件不构建问题（可能需要 `page.on_resize` 等待布局稳定）。

### 4.6 CanvasKit 不响应合成 DOM 事件，需用真实鼠标事件

**背景**：PR #392 修复行点击未触发 `on_click` 回调时定位的根因。

**现象**：Playwright `click(force=True)`（合成 DOM `el.click()` 事件）对 Flet CanvasKit 渲染的控件不可靠 — `force=True` 报告 SUCCESS 但 `on_click`/`on_tap` 回调未触发。

**根因**：Flutter CanvasKit 在 canvas 层面处理 hit-testing，不响应浏览器合成的 DOM 事件；只响应真实鼠标事件（`mousedown` + `mouseup`）。

**解决方案**：E2E 测试中优先使用 `page.mouse.click(cx, cy)`（真实鼠标事件）而非 `locator.click(force=True)`（合成 DOM 事件）。`cx, cy` 通过 `locator.bounding_box()` 中心坐标计算。

```python
# 推荐：真实鼠标事件触发 Flutter hit-testing
box = await locator.bounding_box()
cx = box["x"] + box["width"] / 2
cy = box["y"] + box["height"] / 2
await page.mouse.click(cx, cy)

# 降级：合成 DOM 事件（对 CanvasKit 不可靠）
await locator.click(force=True)
```

**降级策略**：真实鼠标事件失败时，再降级到 `force=True` 合成事件（对部分 Flet 控件如导航按钮仍有效）。

### 4.7 Container.on_click 不生成 flt-tappable，GestureDetector.on_tap 才生成

**背景**：PR #392 修复行点击语义属性缺失时定位的根因。

**现象**：`ft.Container(on_click=handler, ink=True)` 生成 `flt-semantics[role="button"]` 但不生成 `flt-tappable` 语义属性，导致 Playwright 无法通过 `flt-tappable` 选择器定位可点击元素。

**根因**：Flet 的 `flt-tappable` 语义属性由 `GestureDetector` 生成，`Container.on_click` 内部虽用 `InkWell` 但不暴露 `flt-tappable`。

**解决方案**：需要 E2E 点击的行容器用 `ft.GestureDetector(content=Container, on_tap=handler)` 包裹，而非直接用 `Container(on_click=handler)`。

```python
# 推荐：GestureDetector 生成 flt-tappable
inner = ft.Container(
    height=ROW_HEIGHT,
    ink=True,
    bgcolor=...,
    content=ft.Row(cells, spacing=0),
    on_hover=on_hover,  # hover 仍挂 Container
)
return ft.GestureDetector(
    content=inner,
    on_tap=on_row_click_handler if on_row_click is not None else None,
)

# 不推荐：Container.on_click 不生成 flt-tappable
return ft.Container(
    on_click=on_row_click_handler,
    ink=True,
    ...
)
```

**E2E 点击策略优先级**（见 `tests/e2e/pages.py:click_row_by_text`）：
1. `flt-semantics[flt-tappable]` bounding_box 中心 → `page.mouse.click`（真实鼠标事件）
2. `flt-semantics[role="button"]` bounding_box 中心 → `page.mouse.click`
3. 文本 bounding_box 中心 → `page.mouse.click`
4. `flt-semantics[flt-tappable]` → `click(force=True)`（合成事件降级）
5. `flt-semantics[role="button"]` → `click(force=True)`
6. 文本 → `click(force=True)`

### 4.8 Flet 布局嵌套中 expand=True 的传递性陷阱

**背景**：PR #392 修复表格区域被压扁、只有表头可见时定位的根因。

**现象**：`Column > Row(STRETCH) > inner_column(无 expand=True) > rows_clip_container(expand=True)` 嵌套中，`rows_clip_container.expand=True` 无效（父级 `inner_column` 无固定高度），行区域按内容高度撑开（100*30=3000px），超出视口被裁剪，只有表头可见。

**根因**：Flet 的 `expand=True` 只在直接父级有约束高度时生效。若父级是 `Column`/`Row` 但无 `expand=True`，父级本身按内容高度撑开，子级的 `expand=True` 无意义。

**解决方案**：扁平化布局，减少嵌套层级。直接用 `Column(controls=[header, rows_clip_container], expand=True)`，让 `rows_clip_container.expand=True` 在有 `expand=True` 的父级 `Column` 中生效。

```python
# 推荐：扁平化布局，expand 链路清晰
return ft.Column(
    controls=[header_container, rows_clip_container],
    expand=True,  # 父级 expand=True 占满可用高度
    spacing=0,
    width=total_w,
)

# 不推荐：嵌套过深，expand 传递断裂
return ft.Column(
    controls=[
        ft.Row(
            controls=[inner_column],  # inner_column 无 expand=True
            expand=True,
            scroll=ft.ScrollMode.ALWAYS,
        )
    ],
    expand=True,
)
```

**调试技巧**：E2E 失败时截图若显示表格只有表头无数据行，优先检查 `expand=True` 链路是否断裂。

### 4.9 增量变更触发边缘状态视口塌陷（PR #373）

**背景**：PR #373 修复 9 个 E2E 测试失败（`get_by_text("平安银行")` 超时）时定位的根因；前 7 次试错均误判为"语义树问题"，实际是布局视口塌陷。

**根因（边缘状态 + 增量传导）**：main 基线表体视口仅 **6px**（边缘状态，未塌陷）→ Task 8.3 新增 `backtest_btn` → `right_controls +109px` → `left_controls(expand=True)` 压缩 → 参数面板 `Row(wrap=True)` 换行 +78px → `table_card` 视口压到 **0px** → Column 行节点 `flt-semantics` 存在但 `text=''`（Column 在视口 0px 时节点存在但文本不渲染，与 §4.5 的 ListView 节点不生成机制不同）→ `get_by_text` 超时。

**修复**：`backtest_btn` 移入独立右对齐行 + `table_card expand=2`（行高 6px → 37px）。

**诊断要点**：7 次试错均使用了 DOM dump 但误读 `text=''` 为语义合并缺陷，实际是 0px 视口导致文本不渲染；应用日志"策略执行成功"是误导性证据（UI 渲染与业务逻辑独立）。该试错模式违反 [core-protocol.md §5](../bug-fix/core-protocol.md#5-反模式快速自查)「试错式补丁」反模式。同类边缘状态风险排查见 [known-technical-debt.md](../debt/known-technical-debt.md) `P3-PR373-Viewport-Collapse-Audit`。

---

## 5. R16 UI 阻塞红线

**R16**：Flet 事件处理器中**同步阻塞段**（同步 HTTP、文件 IO、CPU 密集计算）必须 `await ThreadPoolManager.run_async()` 提交到线程池，禁止同步阻塞主循环。

**澄清**：本条针对同步阻塞段。async-native IO（`httpx.AsyncClient`、SQLAlchemy async、asyncpg）按原生 `await` 模型执行，不额外包线程池。
