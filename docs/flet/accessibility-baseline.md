# UI 可访问性最低标准

> 来源：P2-4 整改新增章节。本文定义 AStockScreener UI 可访问性最低标准，所有新增 UI 控件必须满足。

> Owner: UI 维护者
> 复核触发器: 新增交互控件 / Dialog / 表单 / 响应式断点调整 / 键盘路径相关变更

## 1. 适用范围

本标准适用于所有 `@ft.component` 声明式组件。涉及交互控件（按钮、输入框、Dropdown、Dialog 等）的组件必须满足以下基线；纯展示组件（如 `ft.Text`、`ft.Icon`）若无交互则豁免。

## 2. 最低标准清单

### 2.1 Label 关联

- **所有交互控件必须有可读 label**：按钮文本、输入框 label、Dropdown label 不为空。
- **Icon-only 按钮**：必须设置 `tooltip` 属性，提供文字说明。
- **表单字段**：`ft.TextField` 必须设置 `label=` 参数，禁止仅依赖 placeholder（placeholder 不被屏幕阅读器视为标签）。

### 2.2 Dialog 可访问性

- **AlertDialog 必须有 title**：`ft.AlertDialog(title=...)` 不为空。
- **Dialog 操作按钮**：必须使用 `ft.TextButton` / `ft.Button` 文本按钮，不使用 Icon-only 按钮作为唯一操作入口（除非带 `tooltip`）。
- **关闭路径**：Dialog 必须提供「取消」或「关闭」按钮，不依赖 Esc 键作为唯一关闭路径。
- **Dialog 内表单**：字段顺序与视觉顺序一致（声明式组件按控件树顺序渲染，天然满足）。

### 2.3 错误状态可读性

- **错误消息**：`ft.TextField(error_text=...)` 必须设置非空错误消息，不依赖颜色变化作为唯一错误提示。
- **SnackBar 反馈**：操作成功/失败必须通过 `ft.use_dialog(ft.SnackBar(...))` 反馈，不依赖控制台日志。
- **表单校验**：必填字段未填时必须显示明确错误消息，禁止静默忽略提交。

### 2.4 键盘路径

- **Tab 顺序**：控件树顺序与视觉顺序一致（声明式渲染天然保证）。
- **Enter 提交**：表单提交按钮必须可由 Enter 键触发（`ft.TextField(on_submit=...)` 链接到提交逻辑）。
- **Esc 关闭 Dialog**：Dialog 必须支持 Esc 键关闭（Flet V1 `ft.use_dialog` 默认支持，禁止禁用）。
- **焦点可见**：禁止全局禁用焦点边框（如 `ft.TextField(focused_border_color=ft.Colors.TRANSPARENT)`）。

### 2.5 响应式不隐藏操作入口

- **响应式布局**：`ResponsiveRow` 在 compact 断点（< 1200px）下不得 `visible=False` 隐藏操作入口（如「运行」「保存」按钮）。
- **替代方案**：若空间不足，操作入口可折叠到菜单（`ft.PopupMenuItem`）或图标按钮（带 `tooltip`），但不得完全隐藏。
- **响应式断点**：使用项目三档断点 compact/standard/ultra_wide（1200/1600/2400，见 [`ui/theme.py`](../../ui/theme.py) `AppStyles`），不使用 Flet 默认 xs/sm/md/lg/xl/xxl。

## 3. 审查清单（PR 评审用）

新增/修改 UI 控件时，评审者按以下清单核查：

- [ ] 所有交互控件有 label 或 tooltip
- [ ] Dialog 有 title + 操作按钮 + Esc 关闭路径
- [ ] 错误状态有可读错误消息（非颜色变化）
- [ ] 键盘路径完整（Tab / Enter / Esc）
- [ ] 响应式布局不隐藏操作入口
- [ ] 控件树顺序与视觉顺序一致

## 4. 例外清单

> 若某控件因业务原因无法满足上述标准，需在此登记例外并说明理由。

当前无例外。

## 5. 引用关系

- [v1-api-constraints.md §V1 声明式 UI 开发规范](./v1-api-constraints.md#v1-声明式-ui-开发规范): 声明式组件实现细则
- [project-differences.md §3](./project-differences.md#3-相对官方默认的项目分叉): 响应式断点项目分叉
- [CLAUDE.md §3.1 R16](../../CLAUDE.md#31--绝对禁止): UI 阻塞红线（不直接关联可访问性，但涉及事件处理器实现）
