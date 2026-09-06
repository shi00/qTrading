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

- **所有交互控件必须有可读 label**：按钮文本、输入框 label、Dropdown label 不为空。
- **Icon-only 按钮**：必须设置 `tooltip` 属性，提供文字说明。
- **表单字段**：`ft.TextField` 必须设置 `label=` 参数，禁止仅依赖 placeholder（placeholder 不被屏幕阅读器视为标签）。
- **例外登记**：无法满足本条款的存量控件（如无 `label` 的数值输入框/Checkbox）统一登记于 §4 例外清单，不在此条款内豁免。

### 2.2 Dialog 可访问性

- **AlertDialog 必须有 title**：`ft.AlertDialog(title=...)` 不为空。
- **Dialog 操作按钮**：必须使用 `ft.TextButton` / `ft.Button` 文本按钮，不使用 Icon-only 按钮作为唯一操作入口（除非带 `tooltip`）。
- **关闭路径**：Dialog 必须提供「取消」或「关闭」按钮，不依赖 Esc 键作为唯一关闭路径。
- **Dialog 内表单**：字段顺序与视觉顺序一致（声明式组件按控件树顺序渲染，天然满足）。

### 2.3 错误状态可读性

- **错误消息**：`ft.TextField(error=...)` 必须设置非空错误消息（`error` 接受字符串或 `ft.Text(...)` 插槽——Flet 0.86 的 `TextField` 无 `error_text`；`ft.Dropdown(error_text=...)`），不依赖颜色变化作为唯一错误提示。
- **Toast 反馈**：操作成功/失败必须通过 `ToastManager.show()`（`ui/components/toast_manager.py`）反馈，不依赖控制台日志。Toast 无障碍要求：
  - 普通 toast duration 不得低于 10s（`show()` 强制下限，低于 10 自动提升到 10）；操作型 toast（含 action 按钮）自动 30s。
  - Toast 必须可手动关闭（`ToastCard` 提供关闭按钮），禁止依赖自动消失作为唯一关闭路径。
  - 依据：10s 低于常见 toast 显示时长参考 20s，但 Toast 支持手动关闭 + hover/展开暂停倒计时（桌面鼠标场景）+ 操作型 30s，整体可辩护。
  - 存量过渡：`ui/components/_markdown_safe.py`、`ui/components/config_panels/backup_restore_panel.py` 仍走 `page.show_toast(...)`（`app/application.py` 启动时对 `page.show_toast` 动态挂载，路径真实可达），应迁移到 `ToastManager.show()`；迁移跟踪见后续 M12 / UIX-13 批次。
- **表单校验**：必填字段未填时必须显示明确错误消息，禁止静默忽略提交。

### 2.4 键盘路径

- **Tab 顺序**：控件树顺序与视觉顺序一致（声明式渲染天然保证）。
- **Enter 提交**：表单提交按钮必须可由 Enter 键触发（`ft.TextField(on_submit=...)` 链接到提交逻辑）。
- **Esc 关闭 Dialog**：Dialog 必须支持 Esc 键关闭（Flet V1 `ft.use_dialog` 默认支持，禁止禁用）。
- **焦点可见**：禁止全局禁用焦点边框（如 `ft.TextField(focused_border_color=ft.Colors.TRANSPARENT)`）。

### 2.5 响应式不隐藏操作入口

- **响应式布局**：窄布局下不得通过 `visible=False` 隐藏操作入口（如「运行」「保存」按钮）。
- **替代方案**：若空间不足，操作入口可折叠到菜单（`ft.PopupMenuItem`）或图标按钮（带 `tooltip`），但不得完全隐藏。
- **响应式栅格**：视图栅格**推荐使用** [`ui/theme.py`](../../ui/theme.py) `AppStyles.COL_*` 预置配置（`COL_FULL`/`COL_HALF`/`COL_THIRD`/`COL_QUARTER`/`COL_TWO_THIRDS`），**新增布局优先使用 `COL_*`**；其断点键沿用 Flet `ResponsiveRow` 默认档位（xs/sm/md/lg）；桌面端窗口最小宽度 1280（`app/window_lifecycle.py`），布局设计以该宽度为下限。
  - **现状**：`AppStyles.COL_*` 字面引用仅 3 处/2 文件（其中 `backtest_result_panel.py` 经 `_COL_QUARTER` 别名间接使用 8 处），`ResponsiveRow` 提及 19 次（实测构造 14 处/10 文件），16/19 未使用规定栅格配置（报告口径）；存量迁移跟踪见 UIX-13 结构性批次（响应式断点模型）。

## 3. 审查清单（PR 评审用）

新增/修改 UI 控件时，评审者按以下清单核查：

- [ ] 所有交互控件有 label 或 tooltip
- [ ] Dialog 有 title + 操作按钮 + Esc 关闭路径
- [ ] 错误状态有可读错误消息（非颜色变化）
- [ ] 键盘路径完整（Tab / Enter / Esc）
- [ ] 响应式布局不隐藏操作入口
- [ ] 控件树顺序与视觉顺序一致

## 4. 例外清单

> 若某控件因业务原因无法满足上述标准，需在此登记例外并说明理由。例外统一登记于此（§4 是唯一注册表），各条款不另立例外机制；登记时标注对应条款号与复查触发条件。

| 例外 | 对应条款 | 现状说明 | 修复方向 / 复查触发条件 |
|------|---------|---------|------------------------|
| 表格键盘遍历降级（`ui/components/virtual_table.py`） | §2.4 键盘路径 | Flet 无表格 focus/grid 键盘遍历（无 Focus 控件、DataTable 无 on_key、KeyboardListener 无 focus 遍历），键盘用户无方向键导航 | 复查触发：Flet 升级提供表格 focus/grid 能力，或 KeyboardListener 组合方案经 E2E 验证可行（与 `virtual_table.py` 的 `# NOTE(lazy:)` 语义一致） |
| 排序指示语义（`ui/components/virtual_table.py` 排序指示） | §2.1 Label 关联 | 用 `↑`/`↓` 文本符号作排序指示，依赖字体支持，屏幕阅读器朗读结果不确定 | 修复方向：替换为 `ft.Icon(ft.Icons.ARROW_UPWARD)` / `ft.Icon(ft.Icons.ARROW_DOWNWARD)`，同步迁移 E2E `test_screener_sort_by_column` 的 `"pct_chg (涨跌幅) ↑"` 朗读锚点；升级触发：virtual_table 重构或可访问性 audit 时（债表 M12-020） |
| 数值输入框无 `label`（`ui/components/slider_input.py` TextField） | §2.1 Label 关联 | `TextField` 无 `label=`；视觉标签由外部布局提供（Row 同行标题或组件顶部 Text），TextField 自身读屏语义未关联（Flet TextField 无语义字段，需 `ft.Semantics` 包装或升级后复查） | 修复方向：组件内部补 `label=`；升级触发：slider_input 重构或可访问性 audit 时 |
| 确认 Checkbox 无 `label`（`ui/components/config_panels/llm_config_panel.py` Checkbox） | §2.1 Label 关联 | `ai_acknowledgment_checkbox` 无 `label=`/`tooltip=`，读屏聚焦时无语义名；配套 `ft.GestureDetector(content=ft.Text(...))` 默认不可 Tab 聚焦，键盘用户只能聚焦无名 Checkbox 按空格切换 | 修复方向：给 Checkbox 补 `label=`（长文本换行问题需处理）；升级触发：llm_config_panel 重构或可访问性 audit 时 |
| 搜索框无 `label`（`ui/components/watchlist_add_dialog.py` search_field） | §2.1 Label 关联 | `TextField` 仅 `hint_text=` 无 `label=`，读屏无语义名 | 修复方向：补 `label=`；升级触发：watchlist_add_dialog 重构或可访问性 audit 时 |
| 无代理输入无 `label`（`ui/views/settings_tabs/system_tab.py` no_proxy_input） | §2.1 Label 关联 | `TextField` 仅 `hint_text=` 无 `label=`（同文件其余 6 个输入框均有 `label=`），读屏无语义名 | 修复方向：补 `label=`；升级触发：system_tab 重构或可访问性 audit 时 |

## 5. 引用关系

- [v1-api-constraints.md §V1 声明式 UI 开发规范](./v1-api-constraints.md#v1-声明式-ui-开发规范): 声明式组件实现细则
- [project-differences.md §3](./project-differences.md#3-相对官方默认的项目分叉): 响应式断点项目分叉
- [CLAUDE.md §3.1 R16](../../CLAUDE.md#31--绝对禁止): UI 阻塞红线（不直接关联可访问性，但涉及事件处理器实现）
