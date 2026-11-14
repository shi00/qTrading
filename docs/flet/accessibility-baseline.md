# UI 可访问性最低标准

> 来源：P2-4 整改新增章节。本文定义 AStockScreener UI 可访问性最低标准，所有新增 UI 控件必须满足。
>
> 文档入口：[Flet 开发文档入口](./README.md)
> 本文是 UI 无障碍可执行条款的唯一事实源；一般 UX 设计原则见 [ui-ux-best-practices.md](./ui-ux-best-practices.md)。

> Owner: UI 维护者
> 复核触发器: 新增交互控件 / Dialog / 表单 / 响应式断点调整 / 键盘路径相关变更

## 1. 适用范围

本标准适用于所有 `@ft.component` 声明式组件。涉及交互控件（按钮、输入框、Dropdown、Dialog 等）的组件必须满足以下基线；纯展示组件（如 `ft.Text`、`ft.Icon`）若无交互则豁免。

## 2. 最低标准清单

### 2.1 Label 关联

- **所有交互控件必须有可读语义名称（accessible name）**：按钮以可见文本承载名称；输入框以持久 label 承载名称；Dropdown label 不为空。语义名称是读屏朗读该控件的依据。
- **Icon-only 按钮**：必须设置 `tooltip` 属性提供**语义名称**（即读屏朗读的名称），不允许空 tooltip 或仅靠视觉图形传达含义。
- **表单字段**：`ft.TextField` 必须设置**持久** `label=` 参数（label 常驻显示、不随输入消失），禁止仅依赖 placeholder（placeholder 不被屏幕阅读器视为标签，且输入后不可见）。
- **例外登记**：无法满足本条款的存量控件（如无 `label` 的数值输入框/Checkbox）统一登记于 §4 例外清单，不在此条款内豁免。
- **结构命名与真实读屏证据分开**：本节名称要求由结构契约测试（如 `test_accessibility_contract.py` 的 TextField/Checkbox 扫描）守护，属**结构命名证据**；真实读屏（Narrator/NVDA）实测是另一层验证，二者不互替（现状见 §4 注）。

### 2.2 Dialog 可访问性

- **AlertDialog 必须有 title**：`ft.AlertDialog(title=...)` 不为空。
- **Dialog 操作按钮**：必须使用 `ft.TextButton` / `ft.Button` 文本按钮，不使用 Icon-only 按钮作为唯一操作入口（除非带 `tooltip`）。
- **关闭路径**：Dialog 必须提供「取消」或「关闭」按钮，不依赖 Esc 键作为唯一关闭路径。
- **Dialog 内表单**：字段顺序与视觉顺序一致（声明式组件按控件树顺序渲染，天然满足）。

### 2.3 错误状态可读性

- **错误消息**：`ft.TextField(error=...)` 必须设置非空错误消息（`error` 接受字符串或 `ft.Text(...)` 插槽——Flet V1 的 `TextField` 无 `error_text`；`ft.Dropdown(error_text=...)`），不依赖颜色变化作为唯一错误提示。
- **Toast 反馈**：操作成功/失败必须通过 `ToastManager.show()`（`ui/components/toast_manager.py`）反馈，不依赖控制台日志。Toast 无障碍要求：
  - 普通 toast duration 不得低于 10s（`show()` 强制下限，低于 10 自动提升到 10）；操作型 toast（含 action 按钮）自动 30s。
  - Toast 必须可手动关闭（`ToastCard` 提供关闭按钮），禁止依赖自动消失作为唯一关闭路径。
  - 依据：10s 低于常见 toast 显示时长参考 20s，但 Toast 支持手动关闭 + hover/展开暂停倒计时（桌面鼠标场景）+ 操作型 30s，整体可辩护。
- **表单校验**：必填字段未填时必须显示明确错误消息，禁止静默忽略提交。

### 2.4 键盘路径

- **Tab 顺序**：控件树顺序与视觉顺序一致（声明式渲染天然保证）。
- **Enter 提交**：表单提交按钮必须可由 Enter 键触发（`ft.TextField(on_submit=...)` 链接到提交逻辑）。
- **Esc 关闭 Dialog**：Dialog 必须支持 Esc 键关闭（Flet V1 `ft.use_dialog` 默认支持，禁止禁用）。
- **焦点可见**：禁止全局禁用焦点边框（如 `ft.TextField(border={ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=ft.Colors.TRANSPARENT))})`）。

### 2.5 响应式不隐藏操作入口

- **响应式布局**：窄布局下不得通过 `visible=False` 隐藏操作入口（如「运行」「保存」按钮）。
- **替代方案**：若空间不足，操作入口可折叠到菜单（`ft.PopupMenuItem`）或图标按钮（带 `tooltip`），但不得完全隐藏。
- **响应式栅格**：视图栅格**推荐使用** [`ui/theme.py`](../../ui/theme.py) `AppStyles.COL_*` 预置配置（`COL_FULL`/`COL_HALF`/`COL_THIRD`/`COL_QUARTER`/`COL_TWO_THIRDS`），**新增布局优先使用 `COL_*`**；其断点键沿用 Flet `ResponsiveRow` 默认六档（xs/sm/md/lg/xl/xxl，最小宽度 0/576/768/992/1200/1400），但 `COL_*` 预置配置实际仅覆盖前四档（xs/sm/md/lg），xl/xxl 档需自行提供 `col` 值；桌面端窗口最小宽度 1024（`app/window_lifecycle.py`），布局设计以该宽度为下限。
  - **现状**：`ResponsiveRow` 构造 15 处/9 文件；其中 5 个构造内行内子控件 `col=` 使用 `AppStyles.COL_*` 预置配置共 13 处（`backtest_result_panel.py` 3 个构造内经 `_COL_QUARTER` 别名 11 处、`market_dashboard.py` 2 个构造内 2 处），其余 10 个构造未使用（统计基准：2026-10-31）；存量迁移跟踪见 UIX-13 结构性批次（响应式断点模型）。

### 2.6 对比度

- **阈值（数值引用自 [`scripts/check_theme_contrast.py`](../../scripts/check_theme_contrast.py) 既有常量，不另立数值正本）**：
  - 正文文本 ≥ **4.5**（脚本 `_THRESHOLD_TEXT`）：业务状态/涨跌文字（SUCCESS/WARNING/INFO/UP_RED/DOWN_GREEN 等）实际用于 13/14px 正文（Toast、状态文字、行情单元格等），非 WCAG 大字号例外。
  - 图标/图形/错误态强调与禁用/装饰内容 ≥ **3.0**（脚本 `_THRESHOLD_LARGE`；WCAG §1.4.3 对禁用控件豁免，保留可读下限）。
- **覆盖边界（如实披露，不扩大为整应用 WCAG AA 认证）**：该脚本仅验收 `_CONTRAST_PAIRS` 登记的关键色对 × 4 主题（dark/light/navy/dracula），经 pre-commit `theme-contrast-check` hook 强制；其中 **ERROR 按图标档 3.0 验收**（其正文场景因与 danger_button/on_error 反色用途联动，超出脚本范围）；watchlist 列表行的 Layer 1 派生色（运行时由 Flet/Flutter 派生）不在可验收集合内。

## 3. 审查清单（PR 评审用）

新增/修改 UI 控件时，评审者按以下清单核查：

- [ ] 所有交互控件有可读语义名称：按钮可见文本；Icon-only 按钮 `tooltip` 语义名称（不允许空 tooltip）；`ft.TextField` 持久 `label=`（禁止仅 placeholder）；Dropdown label 非空（对应 §2.1）
- [ ] Dialog 有 title + 操作按钮 + Esc 关闭路径
- [ ] 错误状态有可读错误消息（非颜色变化）
- [ ] 键盘路径完整（Tab / Enter / Esc）
- [ ] 响应式布局不隐藏操作入口
- [ ] 控件树顺序与视觉顺序一致
- [ ] 涉及颜色/主题改动时，关键色对过 `theme-contrast-check`（正文 4.5 / 图标与禁用 3.0；覆盖边界见 §2.6）

## 4. 例外清单

> 若某控件因业务原因无法满足上述标准，需在此登记例外并说明理由。例外统一登记于此（§4 是唯一注册表），各条款不另立例外机制；登记时标注对应条款号与复查触发条件。

| 例外 | 对应条款 | 现状说明 | 修复方向 / 复查触发条件 |
|------|---------|---------|------------------------|
| 表格键盘遍历降级（`ui/components/virtual_table.py`） | §2.4 键盘路径 | Flet 无表格 focus/grid 键盘遍历（无 Focus 控件、DataTable 无 on_key、KeyboardListener 无 focus 遍历），键盘用户无方向键导航 | 复查触发：Flet 升级提供表格 focus/grid 能力，或 KeyboardListener 组合方案经 E2E 验证可行（与 `virtual_table.py` 的 `# NOTE(lazy:)` 语义一致） |
| 排序指示语义（`ui/components/virtual_table.py` 排序指示） | §2.1 Label 关联 | 已修复（债表 M12-020）：可见排序指示改用 `ft.Icon(ft.Icons.ARROW_UPWARD)` / `ft.Icon(ft.Icons.ARROW_DOWNWARD)`，不再依赖字体字形；读屏语义由 `Text.semantics_label` 的文字文案（`table_sort_asc` / `table_sort_desc`）承载，E2E 朗读锚点已迁移为语义文案断言 | 已落地/复核通过（由 `tests/unit/ui/test_virtual_table_body.py` 结构契约守护）；复查触发：排序指示再次变更或可访问性 audit 时 |

> 已修复项（检视报告 F08，2026-10）：`slider_input.py` 数值 TextField / `watchlist_add_dialog.py` 搜索框 / `system_tab.py` no_proxy 输入框 / 已移除的 `llm_config_panel.py` 确认 Checkbox 曾在本表登记，现已分别补语义名称（`tooltip` / 持久 `label=` / `semantics_label=`）移出例外；`test_accessibility_contract.py` 的 TextField/Checkbox 扫描已常态化守护，无例外条目。真实读屏（Narrator/NVDA）实测尚未执行，控件名称语义以结构契约测试为准。

## 5. 引用关系

- [v1-api-constraints.md §V1 声明式 UI 开发规范](./v1-api-constraints.md#v1-声明式-ui-开发规范): 声明式组件实现细则
- [project-differences.md §3](./project-differences.md#3-相对官方默认的项目分叉): 响应式断点项目分叉
- [CLAUDE.md §3.1 R16](../../CLAUDE.md#31--绝对禁止): UI 阻塞红线（不直接关联可访问性，但涉及事件处理器实现）
