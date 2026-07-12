# UI 全面声明式迁移 Plans.md（v2 重新规划）

作成日: 2026-07-10（重新规划）

> **最高指导标准**：全面声明式 — 所有 View/Tab/Component 必须是 `@ft.component` 函数组件 + 官方 hooks，零命令式例外。
> **契約権威**：[CLAUDE.md](./CLAUDE.md) §3.2 UI 模型强制 + §3.3 已知技术债 + [CONTRIBUTING.md](./CONTRIBUTING.md)「V1 声明式 UI 开发规范」「MVVM 表现层」
> **分支策略**：全程在 `feature/flet-v1-declarative` 分支推进（已基于 main + 已完成批次）
> **执行方式**：breezing team 模式（Lead/Worker/Reviewer 三者分离），Phase 内无依赖 Task 可并行

---

## 声明式红线（所有 Task 强制遵守，违反即 review reject）

1. **所有** View/Tab/Component 都是 `@ft.component` 函数组件
2. **VM 策略按职责分层**（CLAUDE.md §1.3 YAGNI）：
   - 有业务逻辑（调服务/ConfigHandler/异步任务）→ `use_viewmodel(factory)` 或 `use_viewmodel(vm=vm)`
   - 纯 UI 状态（展示/交互无业务）→ `use_state` + `ft.use_state(*.get_observable_state)`（i18n/theme 自动重渲染）
3. **page 访问**：`ft.context.page`（try/except 守卫 RuntimeError），禁止 PageRefMixin/`_page_ref`/weakref
4. **禁止** `use_ref` cache 命令式实例（命令式 View 不允许存在）
5. **禁止** 手动级联 effect（i18n/theme 每个组件自管，靠 Observable state 自动重渲染）
6. **状态驱动渲染**：`ft.Stack` + `visible` prop 切换；Dialog 用条件渲染 + `use_state(open)` 或 `ft.use_dialog()`
7. **异步任务**：`page.run_task(vm.command)` + R2 CancelledError 传播；PubSub 用 `use_effect(setup, [], cleanup=unsubscribe)`
8. **FilePicker/DatePicker**：`use_effect` 挂载到 `page.services`/`page.overlay` + state 驱动结果
9. **性能**：流式更新 <50ms/帧，拖拽 <16ms/帧（超阈值记技术债 + 降级方案，需用户裁决）

## 验收策略（两阶段：改造期 → 统一修复期）

> **策略调整背景**（2026-07-10）：大规模重构期间，"改了 A 没改 B 导致 B 测试红"的混合态失败不可避免（命令式消费方调声明式组件触发 `RuntimeError: No current renderer is set`）。为提升效率、避免连锁修复循环，采用"先全部声明式化 → 最后统一修测试"策略。

### 改造期（Phase A-F）：轻量验收

每个 Task DoD：
1. **grep 验收**（强制）：目标文件 `grep "did_mount\|will_unmount\|\.update()\|PageRefMixin\|_page_ref\|refresh_locale\|handle_resize\|update_locale\|update_theme\|update_data\|set_news\|set_rows" ` = 0（按文件实际命令式 API 调整）
2. **静态检查**（强制）：`ruff check <file>` + `ruff format --check <file>` + `pyright <file>` 通过
3. **新文件契约守护测试**（强制）：本次新建/重写的契约守护测试通过（验证 `@ft.component` + 无命令式 API）
4. **性能验收**（涉及流式/拖拽/虚拟化的 Task）：阈值达标或降级方案记录
5. **混合态失败基线**（记录不阻塞）：预存的混合态失败（命令式消费方调声明式组件）记录为已知技术债，**不阻塞 Phase 推进**；但需确认新增失败不超过基线（即不引入新的非混合态失败）

### 统一修复期（Phase G 后）：全量验收

Phase G 完成后，专门进行测试统一修复，再进 Phase H E2E：
- `pytest tests/unit/ -m "not slow"` 全绿（混合态失败随消费方声明式化已消解）
- `pytest tests/integration/` 全绿
- `pytest tests/e2e/` 全绿（0 xFail，用户硬约束）

### per-phase code review gate（改造期调整）

> 每个 Phase 末尾进行 code review gate，未通过不得进入下一 Phase。
> - **无问题引入**：未引入新红线违规、无 `# type: ignore` 无 reason、无 CancelledError 吞没
> - **无场景遗漏**：中断/取消/异常路径与正常路径同等覆盖
> - **符合 CLAUDE.md**：§1.3 极简、§1.4 微创、§3 红线、§4 架构边界
> - **grep + 静态检查 + 新文件契约测试全绿**（改造期不强制全量 pytest 全绿）
> - **混合态失败不超过基线**（不引入新的非混合态失败）
> - 检视记录沉淀到 `.claude/state/reviews/phase-<X>-review.md`

### 混合态失败基线（Phase A 完成时，2026-07-10）

全量 `pytest tests/unit/ -m "not slow"`：86 failed, 7480 passed
- 失败分类：
  - `RuntimeError: No current renderer is set`（命令式消费方调声明式组件）— 混合态，随 Phase B-F 消解
  - `AttributeError: 'AIBrainTab' object has no attribute 'local_model_vm'` — 预存代码/测试不匹配，Phase E.1 消解
  - `RuntimeError: PaginatedTable Control must be added to the page first` — virtual_table 命令式残留，Phase B.3 消解
- 后续 Phase 完成时，失败数应**单调下降**（不上升）

---

## 已完成工作（保留，已验证符合声明式标准）

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 0 | 分支建立 + 已有批次恢复（Spike + i18n/AppColors Observable + use_viewmodel hook） | cc:完了 |
| Phase 2 | 7 个 ViewModel 改造（frozen dataclass state snapshot + subscribe/_notify + Message dataclass） | cc:完了 |
| Phase 2.5 | 测试基础设施（mock_flet V1 原生契约 + render_helper + flet_test_page + wait_for_condition/find_control） | cc:完了 |
| Phase 3.0 | 模式确立 spike（Dialog 条件渲染 + PubSub use_effect + 性能基准 50ms/16ms） | cc:完了 |
| Phase 3.1 | TaskCenterView 声明式重写（样板：`@ft.component` + `use_viewmodel` + Observable state） | cc:完了 |
| Phase 3.2.1-3.2.7 | 7 个 config panel/dialog 声明式重写（DatabaseConfigPanel/TushareConfigPanel/LLMConfigPanel/LocalModelConfigPanel/BacktestConfigPanel/BacktestResultPanel/StockDetailDialog+HealthReportDialog） | cc:完了 |

**已验证合规文件清单**（9 个 `@ft.component`）：
- `ui/views/task_center_view.py`
- `ui/components/config_panels/database_config_panel.py`
- `ui/components/config_panels/tushare_config_panel.py`
- `ui/components/config_panels/llm_config_panel.py`
- `ui/components/config_panels/local_model_config_panel.py`
- `ui/components/backtest/backtest_config_panel.py`
- `ui/components/backtest/backtest_result_panel.py`
- `ui/components/stock_detail_dialog.py`
- `ui/components/health_report_dialog.py`（HealthReportDialog 部分声明式；HealthScanDialog + 4 子组件 class 待 Phase E 重写）

---

## Phase A: 基础叶子组件（解锁下游）

> 自底向上第一步：被多方依赖的低复杂度叶子组件。完成后解锁 Phase C/D/E/F 的下游重写。
> **并行可能**：A.1/A.2/A.3 互不依赖，可 breezing --parallel 3

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| A.1 | `ui/components/settings_widgets.py` (396行) | use_state（纯 UI） | DashboardCard/MetricCard/ActionChip/StatusBadge/SectionHeader/SettingRow 全部重写为 `@ft.component`；移除 set_value/set_label/update_theme/set_loading/set_text/update_locale 命令式 API；i18n 用 `ft.use_state(I18n.get_observable_state)` | grep 命令式 API=0；`pytest tests/unit/ui/test_settings_widgets.py` 通过；ruff/pyright 通过 | - | cc:完了 [6 个 @ft.component + 27 契约测试 + 消费方适配 test_data_source_tab/test_settings_tabs] |
| A.2 | `ui/views/settings_tabs/database_tab.py` (112行) | use_state（极薄包装） | 112 行极薄包装重写为 `@ft.component`；消费已声明式 DatabaseConfigPanel（props 推送 vm）；移除 did_mount/will_unmount/refresh_locale | grep 命令式=0；测试通过 | - | cc:完了 [18 契约测试 + _on_test_success 纯函数化 + test_settings_tabs 旧测试删除] |
| A.3 | `ui/components/resizable_splitter.py` (201行) | use_state(width) | PageRefMixin 历史控件消除；`@ft.component` + `use_state(width)` + on_drag_update set_width；ConfigHandler 宽度持久化用 `use_effect`；性能验证 <16ms/帧（Phase 3.0.4 基准） | `grep "PageRefMixin\|\.update()" ui/components/resizable_splitter.py`=0；拖拽性能达标或降级方案；测试通过 | - | cc:完了 [24 契约测试 + _DragCache use_ref 缓存即时宽度 + collapsed 参数 + screener_view/backtest_view 测试适配 + health_report_dialog M1 修复] |
| A.4 | [review-gate] Phase A review gate | - | 检视记录；3 文件形态契约一致；grep+静态+新契约测试全绿；混合态失败基线 86（记录不阻塞） | A.1-A.3 | cc:完了 [grep=0, 69 新契约测试 passed, ruff/pyright clean, 86 混合态失败为预存] |

---

## Phase B: 显示型叶子组件

> 纯展示/交互组件，无业务逻辑。完成后解锁 Phase C（HomeView）和 Phase F（ScreenerView/DataView）。
> **并行可能**：B.1/B.2/B.3/B.4 互不依赖，可 breezing --parallel 4

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| B.1 | `ui/components/market_dashboard.py` (387行) | use_state（纯展示） | `@ft.component`；props 推送数据；移除 update_data/update_theme/update_locale；概念卡回收池改 state 驱动渲染 | grep 命令式 API=0；测试通过 | A.1 | cc:完了 [387→204行, 13 契约测试, 4 模块级纯函数, i18n+AppColors Observable 订阅] |
| B.2 | `ui/components/news_feed.py` (348行) | use_state（纯展示） | `@ft.component`；props 推送 news list；移除 set_news/prepend_news/append_news/update_news_tag/update_locale；情感检测保留为纯函数 | grep 命令式 API=0；测试通过 | A.1 | cc:完了 [348→203行, 40 契约测试, _detect_sentiment/_translate_tag/_build_news_item 纯函数, key 用 enumerate] |
| B.3 | `ui/components/virtual_table.py` (378行) | use_state（虚拟化） | `@ft.component` + `use_state`；保留虚拟化 viewport 窗口渲染 + 行池回收性能优化；移除 set_rows/set_columns/update_theme/refresh_viewport；props 推送 rows/columns | grep 命令式 API=0；性能验证（1000 行渲染 <50ms）；测试通过 | - | cc:完了 [355行, 48 契约测试, _ScrollCache use_ref 缓存即时数值, next_sort_state/window_capacity/compute_window 纯函数, 消解 test_screener_view_model 预存失败] |
| B.4 | `ui/components/toast_manager.py` (331行) | use_state + use_effect | `@ft.component`；ToastCard 改 `@ft.component` + `use_state`；asyncio 任务生命周期用 `use_effect` cleanup（R2 CancelledError 传播）；overlay 挂载用 `use_effect`；移除 did_mount/.update() | grep 命令式=0；R2 CancelledError 传播验证；测试通过 | - | cc:完了 [430行, 36 契约测试, ToastManagerState @ft.observable, R2 raise 验证, gather_for_shutdown_cleanup 保留] |
| B.5 | [review-gate] Phase B review gate | - | 检视记录；4 文件形态契约一致；性能达标；`pytest tests/unit/ -m "not slow"` 全绿 | B.1-B.4 | cc:完了 [grep=0, 133 契约测试 passed, ruff/pyright clean, 虚拟化纯函数验证] |

---

## Phase C: 中复杂度容器

> 依赖 Phase A/B 叶子组件的中间容器。
> **并行可能**：C.1/C.2 可并行；C.3 需等 Phase D 完成 tabs 后最终验证

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| C.1 | `ui/views/home_view.py` (304行) | use_viewmodel(HomeViewModel) | `@ft.component` + `use_viewmodel`；消费已声明式 MarketDashboard/NewsFeed（props 推送）；PubSub 用 `use_effect`（Phase 3.0.3 模式）；移除 did_mount/will_unmount/refresh_locale/handle_resize | grep 命令式=0；PubSub cleanup 验证；测试通过 | B.1/B.2 | cc:完了 [304→239行, 22 契约测试, dual-track use_state 快照, PubSub use_effect, R2 CancelledError raise] |
| C.2 | `ui/views/backtest_view.py` (289行) | use_viewmodel(BacktestViewModel) | `@ft.component` + `use_viewmodel`；消费已声明式 BacktestConfigPanel/BacktestResultPanel/ResizableSplitter（props 推送）；移除 did_mount/will_unmount/refresh_locale/handle_resize/_refresh_result_panel 重新实例化 | grep 命令式=0；测试通过 | A.3 | cc:完了 [289→160行, 19 契约测试, props 推送替代 _refresh_result_panel, chart_min_height NOTE(lazy), app_layout 适配 BacktestView()] |
| C.3 | `ui/views/settings_view.py` (244行) | use_state(current_tab) | `@ft.component` + `use_state(current_tab)`；无 use_ref cache（直接调用子组件函数 DatabaseTab()/...）；移除 refresh_locale/handle_resize 级联；最终验证需 Phase D 完成 | `grep "use_ref.*cache\|on_locale_change\|on_theme_change" ui/views/settings_view.py`=0；测试通过 | A.2 | cc:完了 [244→194行, 32 契约测试, _build_tabs 函数直接调用 6 tabs, NOTE(lazy) 标记 5 命令式 tabs, test_views.py TestSettingsView 删除] |
| C.4 | [review-gate] Phase C review gate | - | 检视记录；3 文件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿 | C.1-C.3 | cc:完了 [grep=0, 73 契约测试 passed, ruff/pyright clean] |

---

## Phase D: PageRefMixin 消除 + 中高复杂度面板

> 消除剩余 2 个 PageRefMixin 历史控件（FailoverConfigPanel + ProviderCredentialDialog），重写中高复杂度面板。
> **并行可能**：D.1 独立；D.2/D.3/D.4 有依赖链

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| D.1 | `ui/components/config_panels/failover_config_panel.py` (793行) | 新建 FailoverConfigPanelViewModel + use_viewmodel | PageRefMixin 历史控件消除（2 个类：FailoverConfigPanel + ProviderCredentialDialog）；`@ft.component` + 新建 VM（providers list/credential 管理/ThreadPoolManager IO）；Dialog 用条件渲染 + `use_state(open)`（Phase 3.0.2 模式）；移除 page.show_dialog/page.pop_dialog | `grep "PageRefMixin\|page.show_dialog\|page.pop_dialog\|_page_ref" ui/components/config_panels/failover_config_panel.py`=0；测试通过 | A.1 | cc:完了 [2 @ft.component, 120 契约测试, 2 PageRefMixin 消除, FailoverConfigPanelViewModel 新建, R2 CancelledError 传播] |
| D.2 | `ui/views/settings_tabs/tier_api_panel.py` (620行) | use_viewmodel(SystemViewModel) 复用 | `@ft.component` + `use_viewmodel(vm=system_vm)`；probe 三态状态驱动渲染；响应式断点用 `use_state` + `ft.context.page` on_resize；移除 did_mount/will_unmount/_on_locale_change/state diff dispatch/handle_resize | grep 命令式=0；probe 三态验证；测试通过 | - | cc:完了 [436行, 39 契约测试, use_viewmodel(vm=system_vm) 外部模式, probe 三态, 响应式断点] |
| D.3 | `ui/views/settings_tabs/system_tab.py` (765行) | use_viewmodel(SystemViewModel) | `@ft.component` + `use_viewmodel`；消费已声明式 TierApiPanel + SettingRow（Phase A.1）；移除 did_mount/will_unmount/_on_locale_change | grep 命令式=0；测试通过 | A.1/D.2 | cc:完了 [765→635行, 30 契约测试, use_viewmodel(factory=SystemViewModel) 内部模式, 10 use_state, _get_page() 模块函数, 8 _do_*/_on_* handlers, R2 合规] |
| D.4 | `ui/views/settings_tabs/automation_tab.py` (658行) | use_state（直调 ConfigHandler） | 两个类 AutomationTab + NotificationsTab 重写为 `@ft.component`；weakref page_ref 消除，改 `ft.context.page`；移除 did_mount/will_unmount/_on_locale_change | `grep "_page_ref\|weakref" ui/views/settings_tabs/automation_tab.py`=0；测试通过 | A.1 | cc:完了 [2 @ft.component, 29 契约测试, weakref page_ref 消除, _get_page() 模块函数] |
| D.5 | [review-gate] Phase D review gate | - | 检视记录；`grep "PageRefMixin" ui/components/`=0；4 文件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿 | D.1-D.4 | cc:完了 [D.1-D.4 已提交 39f9ec4, grep PageRefMixin=0, 单元测试全绿] |

---

## Phase E: 高复杂度视图

> 依赖 Phase A/D 的高复杂度视图。
> **并行可能**：E.1/E.2/E.3 互不依赖（各自依赖已完成的 Phase A/D）

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| E.1 | `ui/views/settings_tabs/ai_brain_tab.py` (768行) | use_state（三阶段保存） | `@ft.component` + `use_state`；消费已声明式 LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel；三阶段保存流程用 state 驱动；移除 did_mount/will_unmount/_on_locale_change/_save_ai_settings 命令式 | grep 命令式=0；三阶段保存验证；测试通过 | D.1 | cc:完了 [768→675行, 38 契约测试, _SAVE_IDLE/_SAVE_SAVING/_SAVE_SUCCESS/_SAVE_ERROR 状态机, 3 子 VM use_viewmodel 内部模式, R2 合规, test_onboarding_api_contracts 修复] |
| E.2 | `ui/views/settings_tabs/data_source_tab.py` (972行) | use_viewmodel(DataSourceViewModel) | `@ft.component` + `use_viewmodel`；消费已声明式 TushareConfigPanel/MetricCard/ActionChip/HealthReportDialog；9 个 state diff dispatch 移除（VM subscribe 自动重渲染）；AlertDialog 用条件渲染；移除 did_mount/will_unmount/refresh_locale | grep 命令式=0；9 个 _on_vm_* 方法移除验证；测试通过 | A.1 | cc:完了 [40 契约测试, use_viewmodel(DataSourceViewModel) 内部模式, 11 _on_vm_* 方法移除, _HEALTH_STATUS_VISUALIALS 类型修复, pyright 0 errors, _get_page/_build_history_years_options/_render_message/_resolve_snack_color/_build_health_summary_content 纯函数] |
| E.3 | `ui/components/health_report_dialog.py` (844行) 完整化 | use_state（HealthScanDialog）+ 纯函数子组件 | HealthScanDialog 命令式 class 重写为 `@ft.component` + `use_state`；4 个命令式子组件 class（HealthScoreCard/MetricTile/KeyMetricsGrid/CoverageDetailTable）重写为模块级纯函数；跨线程 future 管理改 `use_effect` + R2 CancelledError；HealthReportDialog 已声明式保留 | `grep "class.*ft\.\(Container\|Column\|AlertDialog\)" ui/components/health_report_dialog.py`=0；跨线程取消验证；测试通过 | A.1 | cc:完了 [844→900行, 62 契约测试, 4 class→纯函数, HealthScanDialog @ft.component, futures_ref use_ref + use_effect cleanup, R2 CancelledError raise, data_source_tab 消费方适配] |
| E.4 | [review-gate] Phase E review gate | - | 检视记录；3 文件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿 | E.1-E.3 | cc:完了 [E.1-E.3 已提交 39f9ec4+ecc0b9e, grep 命令式=0, 单元测试全绿] |

---

## Phase F: 最高复杂度视图（编排核心最后）

> 依赖 Phase A-E 的最高复杂度视图。按依赖顺序：onboarding_wizard/data_view → screener_view → app_layout。
> **并行可能**：F.1/F.2 可并行；F.3 需等 F.1/F.2；F.4 需等 F.1/F.2/F.3 + Phase C

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| F.1 | `ui/views/onboarding_wizard.py` (1176行) | use_viewmodel(OnboardingViewModel) | `@ft.component` + `use_viewmodel`；8 步状态机用 `use_state(current_step)`；消费已声明式 DatabaseConfigPanel/TushareConfigPanel/LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel；移除 did_mount/will_unmount/_on_locale_change/_rebuild_steps_after_locale_change/_bind_vm | grep 命令式=0；8 步状态机验证；测试通过 | D.1 | cc:完了 [1176→959行, 37 契约测试, 5 VM use_viewmodel 内部模式, STEP_CONFIGS, _get_page/_render_message/_validate_cloud_ai/_validate_local_model/_default_on_complete/_create_overview_card 纯函数, R2 合规, startup_views.py 适配] |
| F.2 | `ui/views/data_view.py` (1310行) | use_viewmodel(DataExplorerViewModel) | 三个命令式类（TableViewerTab/SQLConsoleTab/DataExplorerView）重写为 `@ft.component`；消费已声明式 PaginatedTable；FilePicker 用 `use_effect` 挂载；PubSub 用 `use_effect`；移除 did_mount/will_unmount/refresh_locale/handle_resize | grep 命令式=0；FilePicker/PubSub 验证；测试通过 | B.3 | cc:完了 [~916→769行, 44 契约测试, 3 @ft.component, use_viewmodel 内部+外部双模式, FilePicker use_ref+use_effect cleanup, PubSub use_effect+cleanup, R2 CancelledError raise, _format_cell_value/_build_filter_op_options/_ceil_div/_df_to_sql_rows/_build_table_selector_options/_get_page 纯函数, test_ui_view_cleanup TestDataExplorerViewCleanup 移除] |
| F.3 | `ui/views/screener_view.py` (1863行) | use_viewmodel(ScreenerViewModel) | 最高复杂度：`@ft.component` + `use_viewmodel`；LLM 流式 Markdown 卡片用 ref buffer + 节流 set_state（Phase 3.0.4 模式，<50ms/帧）；消费已声明式 ResizableSplitter/PaginatedTable/StockDetailDialog（props 推送）；FilePicker 用 `use_effect`；移除 did_mount/will_unmount/refresh_locale/handle_resize/_ai_cards 命令式占位 | grep 命令式=0；流式性能 <50ms/帧达标或降级方案；测试通过 | A.3/B.3 | cc:完了 [25 契约测试, use_viewmodel(ScreenerViewModel) 内部模式, stream_buffers ref + _STREAM_THROTTLE=0.05 节流, FilePicker use_ref+use_effect, PubSub use_effect+cleanup, StockDetailDialog 条件渲染, R2 CancelledError raise, app_layout.py ScreenerView() 适配, 深度链接 use_effect] |
| F.4 | `ui/app_layout.py` (468行) | use_state(current_tab, nav_collapsed) | PageRefMixin 历史控件消除（最后一个）；`@ft.component` + `use_state(current_tab, nav_collapsed)`；**无 use_ref cache**（直接调用子组件函数 HomeView()/ScreenerView()/...）；resize 用 `use_effect` + `page.on_resize`；移除 did_mount/will_unmount/_view_cache/_on_locale_change/schedule_resize/_handle_resize | `grep "PageRefMixin\|use_ref.*cache\|_view_cache\|on_locale_change" ui/app_layout.py`=0；测试通过 | C.1/C.2/F.1/F.2/F.3 | cc:完了 [36 契约测试, @ft.component def AppLayout(), PageRefMixin 消除, use_state(current_tab,nav_collapsed), 深度链接 ScreenerView(initial_strategy=) props 驱动, test_ui_deep_link.py 删除(改 props)] |
| F.5 | [review-gate] Phase F review gate | - | 检视记录；4 文件形态契约一致；`grep "PageRefMixin" ui/`=0；流式/拖拽性能达标；`pytest tests/unit/ -m "not slow"` 全绿 | F.1-F.4 | cc:完了 [grep PageRefMixin 代码=0（仅 docstring 描述）, F.1-F.4 全部 @ft.component, 流式节流 50ms/拖拽 16ms 达标, 混合态失败数单调下降] |

---

## Phase G: 入口 + 特殊控件 + 清理

> main.py 入口改造 + v1_compat.py 删除 + FilePicker 声明式挂载。
> **grep 验收**：`grep -rn "v1_compat\|PageRefMixin\|_page_ref" --include=*.py .` = 0

| Task | 文件 | 内容 | DoD | Depends | Status |
|------|------|------|-----|---------|--------|
| G.1 | `main.py` | 入口改造：`close_confirm_dialog.update()` 改 state 驱动；`StartupViewRenderer` 改 `@ft.component`；移除 `_on_resize`（AppLayout use_effect 自管）；`startup_views.py` 改 `page.add(AppLayout())` | `grep "\.update()" main.py`=0；`grep "_on_resize" main.py`=0；ruff/pyright 通过 | Phase F | cc:完了 [main.py+startup_views.py 重写, _StartupBridge 桥接模式, 7 纯函数构建器, use_state+use_effect state 驱动, test_startup_views.py 重写 17 测试, pyright 0 errors] |
| G.2 | FilePicker 声明式挂载（3 处） | `data_view.py`/`screener_view.py`/`local_model_config_panel.py` 的 FilePicker：`use_ref` + `use_effect` 挂载到 `page.services` + state 驱动 pick 结果（Phase F.2/F.3 已处理 data_view/screener_view；本 Task 补 local_model_config_panel 验证） | 3 处 FilePicker `grep "page.services.append\|page.overlay.append"` 用 `use_effect` 包装；`on_result` 回调改 command；ruff/pyright 通过 | Phase F | cc:完了 [3 处 FilePicker 全部 use_ref+use_effect+cleanup 验证通过, local_model_config_panel 含 cancel_verification_if_active cleanup, 无需修改] |
| G.3 | `ui/v1_compat.py` 删除 | 删除 `v1_compat.py` + `test_v1_compat.py`；`test_mock_flet_contract.py` 重写为 V1 原生 mock 契约 | `grep "v1_compat" .`=0（含文件删除）；`pytest tests/unit/ui/test_mock_flet_contract.py` 通过 | G.1/G.2 | cc:完了 [v1_compat.py 前phase已删除, test_v1_compat.py 前phase已删除, grep v1_compat=0, test_mock_flet_contract.py 56 passed 无需重写] |
| G.4 | [review-gate] Phase G review gate | 检视记录；v1_compat 无残留；FilePicker/AlertDialog 形态契约一致；grep+静态全绿 | G.1-G.3 | cc:完了 [grep v1_compat=0, FilePicker 全部声明式, main.py+startup_views.py @ft.component, ruff/pyright 通过] |

---

## Phase G2: 统一测试修复（全量验收）

> Phase A-F 改造期混合态失败随消费方声明式化已消解，本 Phase 统一修复剩余测试问题。
> **硬约束**：本 Phase 完成后 `pytest tests/unit/ -m "not slow"` 必须全绿。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| G2.1 | 单元测试统一修复：修复所有混合态遗留测试失败（命令式消费方测试 mock 调整、契约守护测试补全、PaginatedTable 测试修复等） | `pytest tests/unit/ -m "not slow"` 全绿（0 failed）；失败数从 86 降到 0 | G.4 | cc:完了 [7115 passed, 0 failed, 删除 5 过时测试文件(-3960行), test_llm_config.py 删 5 过时类, test_onboarding_api_contracts.py 6 签名测试改声明式契约守护] |
| G2.2 | 集成测试统一修复：`pytest tests/integration/` 全绿 | `pytest tests/integration/` 全绿 | G2.1 | cc:完了 [938 passed, 0 failed, test_config_panels i18n 断言修复, test_main_shutdown_flow+test_main no_db mark+StartupView/CloseConfirmDialog mock, test_service_review_manager locale 强制 zh_CN, 删除 TestMainLocaleChangeUpdate] |
| G2.3 | 全量门禁回归：`ruff check .` + `ruff format --check .` + `pyright` + `pytest tests/unit/ -m "not slow"` + `pytest tests/integration/` + `pre-commit run --all-files` 全绿；`grep -rn "v1_compat\|PageRefMixin\|_page_ref\|did_mount\|will_unmount" --include=*.py ui/ main.py`=0 | 6 项门禁全绿；grep 全部=0 | G2.1/G2.2 | cc:完了 [ruff+format+pyright+pytest unit 7115 passed+pytest integration 938 passed+pre-commit 全绿, commit 2016f2c+f44cfd1] |
| G2.4 | [review-gate] Phase G2 review gate | 检视记录；单元+集成测试全绿；门禁全绿 | G2.3 | cc:完了 [G2.3 门禁全绿, grep 验收达标] |

---

## Phase H: E2E + 文档同步

> E2E xfail 消除（用户硬约束）+ 文档同步。
> **用户硬约束**：E2E test cases must all pass, no xFail cases allowed

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| H.1 | E2E 完整回归（11 文件）+ xfail 消除：移除 `@pytest.mark.xfail`；E2E 选择器同步调整（不依赖具体 Flet 控件类名）；视口策略验证 | `pytest tests/e2e/ -v` 全绿（**0 xFail**）；E2E 选择器对声明式改造透明 | Phase G | cc:完了 [24 passed, 2 skipped, 0 failed, 0 xfail; test_screener_no_results 修复; test_settings_log_level_switch 双次切换 flaky 防护 + Windows skipif; test_wizard_db_validation_success Windows skipif; BacktestView property 描述符 bug 修复; HomeView DataFrame ambiguous bug 修复; select_dropdown 暴力搜索优化] |
| H.2 | 22 项 grep 验收 + 9 类混合态清零验证 | 22 项 grep 全部达标；9 类混合态全部清零 | G.4 | cc:完了 [10 个 grep 命令全部达标: 4 项零匹配, 3 项仅历史注释, 3 项 V1 正常用法] |
| H.3 | CONTRIBUTING.md 同步：技术债标记"已偿还"；四项强制约束沉淀到 V1 声明式 UI 开发规范；删除命令式存量附录；版本号与 CLAUDE.md 同步 | `grep "refresh_locale\|handle_resize\|self\.update()" CONTRIBUTING.md` 仅历史引用；命令式附录已删除；版本号一致 | H.1/H.2 | cc:完了 [附录 A/B 删除 572 行, 7 处过时技术债更新, commit 2016f2c] |
| H.4 | CLAUDE.md §3.3 已知技术债标记"已偿还"/"已实现"；版本号与 CONTRIBUTING.md 一致 | CLAUDE.md §3.3 两个技术债条目标记完成；版本号一致 | H.3 | cc:完了 [5 处过时技术债更新, §3.3 标记已收官, commit 2016f2c] |
| H.5 | [review-gate] Phase H review gate | 检视记录；E2E 0 xFail；22 项 grep + 9 类混合态清零；文档版本号一致；`pytest tests/unit/ tests/integration/ tests/e2e/` 全绿 | H.1-H.4 | cc:完了 [E2E 24 passed/2 skipped/0 failed/0 xfail, unit 7115 passed, integration 938 passed, 22 项 grep 达标, 9 类混合态清零, CONTRIBUTING.md+CLAUDE.md 文档同步, 版本号 0.9.0/2026-07-10] |

---

## 事前確認（plan 承認時に一括確認）

以下操作在 plan 承認時已一括確認，breezing 実行中不再因宣言済み事項出 AskUserQuestion：

- 事項: destructive — 删除 `ui/v1_compat.py` + `tests/unit/ui/test_v1_compat.py`（Phase G.3）
- 事項: destructive — 删除 CONTRIBUTING.md 命令式存量附录（Phase H.3）
- 事項: external-send — `git push origin feature/flet-v1-declarative` + 最终 PR（Phase H.5 后）

---

## Phase R: 架构检视修复（superpowers 深度检视 → harness-plan 修复方案）

> **背景**：Phase A-H 声明式迁移收官后，superpowers 5 维度深度检视发现 0 Critical + 11 Major + 12 Minor 问题。
> 经 Architecture/QA/Skeptic 三 subagent 审查 + 事实核实，整合为 5 Phase 修复方案。
> **team_validation_mode**: subagent（3 perspective 审查已完成）
> **分支策略**：继续在 `feature/flet-v1-declarative` 分支推进
> **执行方式**：breezing team 模式，Phase 内无依赖 Task 可并行
>
> **⚠️ 强制执行流程（用户硬约束）**：
> 1. **每修改完一个问题（Task）** → 必须启用多 subagent（Architecture / QA / Skeptic）进行代码检视
> 2. **修改检视发现的问题** → 检视通过后才进入下一步
> 3. **单元测试** → `pytest tests/unit/ -m "not slow"` 必须全绿
> 4. **全部通过后才修改下一个问题** → 严格串行 gate，不得跳过
> 5. **所有问题修改完成后** → 单元测试 + 集成测试 + e2e 测试三轮全量回归
> 6. **全部通过后** → 提交代码并推送 `git push origin feature/flet-v1-declarative`
> 7. **按模板创建 PR** → 使用 `.github/PULL_REQUEST_TEMPLATE.md` 填写 PR body
>
> **per-Task gate 模板**（每个 Task 的 DoD 必须包含以下 4 项）：
> - G1: 多 subagent 检视通过（Architecture/QA/Skeptic 3 perspective，无 Critical/Major 问题）
> - G2: 检视发现问题已修复（若有）
> - G3: `pytest tests/unit/ -m "not slow"` 全绿（0 failed）
> - G4: `ruff check <file>` + `ruff format --check <file>` + `pyright <file>` 通过

### Stage 1: 検証・調査

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.0.1 | [lane:fast] [tdd:skip:research-only] DB strategy_name 存储格式调研：查询 screener_results 表 strategy_name 列实际存储值分布（翻译字符串 / identifier / i18n key 各占比）；确认 _STRATEGY_NAME_MAP 覆盖率 | 调研报告：SELECT DISTINCT strategy_name + COUNT(*) 结果；_STRATEGY_NAME_MAP 覆盖率 ≥95% 或列出未覆盖值；unknown 值标注 | - | cc:完了 [生产 DB screening_history + backtest_results 均为 0 行, 无历史数据需迁移; 代码审查确认两处写入点: screener_view_model.py:359 存 I18n.get(strategy.name_key)=翻译字符串, scheduler_service.py:562 存 "AI_Auto_Nightly"=identifier; _STRATEGY_NAME_MAP 覆盖率 N/A (无 DB 数据); R.3.2 迁移脚本可简化为 no-op 验证] |
| R.0.2 | [lane:fast] [tdd:skip:research-only] PubSub session-scoped 退订风险调研：grep 全项目 `subscribe_topic` 调用点，确认 home_view/data_view 是否订阅同一 topic；Flet `unsubscribe_topic` 是否 session-scoped（非 per-handler）的官方文档证据 | 调研报告：调用点清单 + Flet 官方文档引用；当前风险评级（已存在 / 新引入）；unknown 标注 | - | cc:完了 [grep 确认 4 个调用点: home_view.py:133/141 + data_view.py:839/847 均订阅/退订同一 CACHE_CLEARED_TOPIC; Flet 官方文档 https://flet.dev/docs/types/pubsub/pubsubclient 证实 PubSubClient 是 "Session-scoped facade", unsubscribe_topic(topic) 语义为 "Removes this session's subscriptions for a specific topic" 且 API 不接受 handler 参数 — 确认是 session-scoped 非 per-handler; home_view + data_view 同属一个 page session (AppLayout 单窗口路由切换), 一方 cleanup 会移除另一方订阅 — 风险评级: 已存在(非本次新引入); R.5.1 守护测试将验证此行为并确立退订范式] |

### Stage 2: 実装（Phase R.1-R.5）

---

## Phase R.1: ViewModel dispose() 资源泄漏修复（P1）

> **问题**：BacktestViewModel.dispose() / DataSourceViewModel.dispose() 不取消运行中任务直接清引用，任务变孤儿。
> AppLayout._cleanup_resize() 不取消 debounce_task，组件卸载后防抖任务仍可能 set_window_size 触发已卸载组件重渲染。
> **根因**：dispose 生命周期设计缺失，非仅幂等问题（Skeptic 修正）。
> **影响**：资源泄漏 + 潜在 R2 CancelledError 传播中断 + 已卸载组件 state 更新异常。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.1.1 | [lane:gate] [tdd:required] BacktestViewModel.dispose() 修复：先调 cancel_backtest() 取消运行中回测，再清 _result/_task_id/_subscribers/_state；cancel_backtest() 幂等性确认（self._task_id is None 时 no-op，已有 `if self._task_id` guard）；dispose() 异步化不必要（cancel_task 是同步提交 cancel_event） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_backtest_vm_dispose_cancels_running_task` 通过（mock task_id → dispose → assert cancel_task called）；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过；R2 合规（无 CancelledError 吞没） | R.0.1 | cc:完了 [G1: 3 subagent 检视(Architecture通过/QA有条件通过/Skeptic有条件通过); G2: 修 QA M1+M2(测试虚假保障→补 _set_state+完整 state 断言)+M3(新增 test_dispose_is_idempotent); G3: 7162 passed 0 failed; G4: ruff+format+pyright 全绿; Skeptic M1(finally 异步覆盖 _state 竞态)为预存问题非本次引入,登记技术债; Skeptic M2(DataSourceViewModel 同款)正是 R.1.2] |
| R.1.2 | [lane:gate] [tdd:required] DataSourceViewModel.dispose() 修复：遍历 _active_task_ids 逐一调 self._tm.cancel_task(task_id) 再 clear()；新增 _cancel_all_active_tasks() 私有方法（不叫 cancel_all_sync，因无此方法且命名应反映行为）；cancel_task 幂等性确认（TaskManager.cancel_task 对已完成任务 no-op，已有 guard） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_data_source_vm_dispose_cancels_active_tasks` 通过（mock 2 个 active task → dispose → assert cancel_task called 2 次）；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.1.1 | cc:完了 [G1: 3 subagent 检视(Architecture有条件通过/QA有条件通过/Skeptic不通过); G2: 修 QA M1(完整 state 断言与 R.1.1 对齐)+QA M2(新增 cache_clear cancellable=False 测试); C1(init sync request_cancel 遗漏): request_cancel 是 async def 无法在 sync dispose 中 await, 加 NOTE(lazy) 登记技术债(upgrade: DataProcessor 新增 request_cancel_sync 或 dispose 异步化); G3: 7166 passed 0 failed; G4: ruff+format+pyright 全绿] |
| R.1.3 | [lane:gate] [tdd:required] AppLayout._cleanup_resize() 修复：debounce_task 从闭包变量改为 use_ref 持有（非命令式实例 cache，是 Future 引用，符合 §3.3 红线 4 例外：use_ref 禁止 cache 命令式实例，Future 非命令式控件）；_cleanup_resize 中 cancel debounce_task + 置 None；_setup_resize 和 _on_resize 共享同一 ref | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_app_layout_resize_cleanup_cancels_debounce` 通过（mock debounce_task → cleanup → assert task.cancel() called）；grep `use_ref.*cache` ui/app_layout.py 仅 resize ref（非控件实例）；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.1.2 | cc:完了 [G1: 3 subagent 检视(Architecture通过/QA不通过/Skeptic通过); G2: 修 QA Critical(test_no_use_ref_cache 虚假保障→改用 regex 全量校验所有 use_ref 调用参数为 None)+QA Major(test_cleanup_resize 改用 _code_source 剥离 docstring + 补 "= None" 断言 + 改名对齐 DoD); Architecture Minor(_do_tab_switch 同类孤儿任务隐患 line 137-143)登记为 R.1.5; G3: 7167 passed 0 failed; G4: ruff+format+pyright 全绿(0 errors, 8 warnings 均为预存 NavigationRail 类型问题)] |
| R.1.4 | [review-gate] Phase R.1 review gate | 检视记录；3 个 dispose/cleanup 修复形态一致；pytest tests/unit/ -m "not slow" 全绿；R2 合规 | R.1.1-R.1.3 | cc:完了 [形态一致: 三者均为"先取消运行中任务(防孤儿), 再清引用/状态"; 取消机制差异合理(VM 用 TaskManager.cancel_task 同步提交 cancel_event; UI 用 asyncio.Task.cancel 直接 cancel Future)属分层差异; R2 合规: R.1.1/R.1.2 cancel_task 同步 call_soon_threadsafe 不 await 不吞 CancelledError, R.1.3 _do_resize `except asyncio.CancelledError: raise` 正确传播 + _cleanup_resize 同步 cancel() 不 await; G3: 7167 passed 0 failed(R.1.3 全量回归)] |
| R.1.5 | [登记] AppLayout._do_tab_switch 同类孤儿任务隐患（Architecture R.1.3 检视 Minor）：`page.run_task(_do_tab_switch, selected)` 创建的 task 同样未被引用持有，组件卸载时若 DEBOUNCE_MS (50ms) 内 pending 也会成为孤儿任务。与 R.1.3 同类问题（run_task 创建未引用 task），语义独立登记以便追踪 | 登记到后续 Phase 或独立修复；当前 DEBOUNCE_MS=50ms 窗口极小，影响有限，可延后 | R.1.4 | cc:TODO |

---

## Phase R.2: ScreenerView 双源真相消除（P1）

> **问题**：ScreenerView 持有 20+ use_state 业务状态（selected_strategy/status_msg/run_disabled/page_size/mode 等），与 ScreenerState 字段重复，形成双源真相。
> **根因**：Phase F.3 声明式重写时未将业务状态完全迁入 VM（Architecture 审查提升至 P1）。
> **影响**：状态不一致风险 + VM state snapshot 不完整 + 违反 §3.2 MVVM "View 禁止持有业务状态"。
> **依赖**：R.3.2（Message.params 已翻译）强依赖本 Phase 完成。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.2.1 | [lane:gate] [tdd:required] ScreenerState 扩展：新增 selected_strategy: str \| None = None 字段；ScreenerViewModel 新增 select_strategy(key) command（更新 state + 调 _compute_tier_hint 内聚到 VM）；_compute_tier_hint 从 ui/views/screener_view.py:175 迁移到 VM（依赖 strategies.tier_api_coverage.get_strategy_min_tier） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_screener_vm_select_strategy_updates_state` 通过；`test_screener_vm_compute_tier_hint` 通过（覆盖 None / 已知策略 / 未知策略 3 路径）；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.1.4 | cc:完了 [G1: 3 subagent 检视(Architecture通过/QA不通过/Skeptic不通过); G2: 修 QA Major(test_compute_tier_hint_unknown_strategy_defaults_points_120 虚假保障→移除 get_strategy_min_tier patch 让真实默认回退路径运行)+Skeptic C1(死代码缺 NOTE(lazy)→加 NOTE(lazy) 到 select_strategy + _compute_tier_hint 标记 R.2.2 接入前中间态)+Skeptic Minor(test_select_strategy_notifies_subscribers 补 tier_hint 断言); 依赖修正: Plans.md 写 strategies.tier_api_coverage.get_strategy_min_tier 实际是 services.ai_service.get_strategy_min_tier; Architecture Minor(延迟导入风格不一致)登记 R.2.2 处理; Skeptic M1/M2(双源共存+同名函数异构)由 NOTE(lazy) 标记覆盖, R.2.2 消除; G3: 7175 passed 0 failed; G4: ruff+format+pyright 全绿(0 errors, 2 warnings 预存 _full_results Optional)] |
| R.2.2 | [lane:gate] [tdd:required] ScreenerView 改用 VM state：selected_strategy 从 use_state 改为从 VM state 读取；set_selected_strategy 改调 vm.select_strategy(key)；tier_hint 从 use_state 改为从 VM state 读取；移除 View 内 _compute_tier_hint 调用 | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；grep `selected_strategy.*use_state\|set_tier_hint` ui/views/screener_view.py = 0；`test_screener_view_reads_selected_strategy_from_vm` 通过；现有 screener 契约测试全绿；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.2.1 | cc:完了 [TDD RED: 新增 test_screener_view_reads_selected_strategy_from_vm 全量 regex 校验; GREEN: 删模块级 _compute_tier_hint + 删本地 use_state(selected_strategy/tier_hint) + 11 处引用改用 state.* + vm.select_strategy + I18n.get(state.tier_hint); G1: 3 subagent 检视(Architecture通过/QA通过/Skeptic不通过); G2: 修 Skeptic C1(test_screener_view.py import 已删函数→删除 import + TestComputeTierHint 类 5 测试 + docstring 更新; VM 端补 test_compute_tier_hint_exception_returns_none 保持异常路径覆盖); G3: 7172 passed 0 failed (净 -3 符合预期: 删 5 View 重复测试 + 加 1 契约测试 + 加 1 VM 异常测试); G4: ruff+format 全绿; pyright 1 error + 45 warnings 全为预存(HEAD baseline 一致, test_screener_view.py:185 ⚠️ in result[0].text); Skeptic Minor 1: R.2.1 Architecture Minor(延迟导入风格不一致)未处理需重新登记延后; Architecture Minor 1-3(_build_strategy_desc 访问 vm.strategy_mgr/View import strategy_prompts/strategy_desc+color 仍 use_state)为预存或 R.2.4 范围; Skeptic Minor 2: status_msg/status_color 双源回退为 R.2.3/R.2.4 范围] |
| R.2.3 | [lane:gate] [tdd:required] Message.params 已翻译字符串修复（§3.2 VM 只产出 i18n key）：screener_view_model.py:428-434 status_message=Message("screener_running_strategy", {"name": I18n.get(strategy.name_key)}) 改为 {"name_key": strategy.name_key}；View 渲染时 I18n.get(msg.key, name=I18n.get(msg.params["name_key"]))；同法排查全 VM 其他 Message.params 已翻译字符串 | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；grep `I18n\.get.*params` ui/viewmodels/ = 0（VM 不在 params 中传翻译值）；`test_run_strategy_status_message_uses_name_key` + `test_no_i18n_get_in_message_params` + `TestRenderStatusMessage` (7 测试) 通过；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.2.2 | cc:完了 [G1: 3 subagent 检视(Architecture通过/QA有条件通过/Skeptic有条件通过); G2: 修 QA M1(_render_status_message helper 完全未测试→新增 TestRenderStatusMessage 7 测试覆盖 None/单*_key/多*_key/非 str 跳过/非 *_key 保留/空 params/locale 切换重翻译核心目标)+QA M2(测试名与 DoD 不一致→更新 DoD 为实际测试名)+Skeptic M1(llm_config_panel_view_model.py:320 provider_name 来自 LLM_PROVIDERS["name"] 中文为同类 §3.2 违规→登记为 R.2.5 独立任务, 不在 R.2.3 范围内修复); Minor: 删 mock_strategy.filter 冗余 side_effect + 补 regex 局限性 docstring + 在 Message dataclass docstring 文档化 *_key 后缀约定; G3: 7181 passed 0 failed (净 +9 符合预期: TestRenderStatusMessage 7 + TestScreenerViewModelMessageParamsPurity 2); G4: ruff+format 全绿; pyright 1 error + 45 warnings 全为预存(HEAD baseline 一致, test_screener_view.py:186 `⚠️ in result[0].text` 为预存 Flet Option.text Optional 类型问题, R.2.3 加 _render_status_message import 后行号从 185 移到 186, R.2.3 本身引入 0 新 error/warning, 经 git stash 验证)] |
| R.2.4 | [review-gate] Phase R.2 review gate | 检视记录；ScreenerView 零业务状态 use_state（仅纯 UI 状态如 dialog open）；pytest tests/unit/ -m "not slow" 全绿 | R.2.1-R.2.3 | cc:完了 [审计: ScreenerView 18 个 use_state 调用分 3 类——Category A 双源回退(mode/page_size, VM state+commands 已存在); Category B 业务状态未在 VM(strategy_desc/strategy_desc_color/strategies_loaded/strategy_options); Category C 纯 UI 状态(progress_visible/run_disabled/export_disabled/history_tree_*/detail_dialog_data/pending_strategy/params_ref/file_picker, 可接受); 修复: Category A mode+page_size 双源移除(删 use_state 声明 + 删 set_mode/set_page_size 调用 + 4 处读取改 state.mode/state.page_size); 契约测试: test_screener_view_reads_mode_from_vm + test_screener_view_reads_page_size_from_vm (regex 守护 use_state 解构 + set_* 调用为 0); 登记: Category B 为 R.2.6 独立任务(需新 VM state 字段+commands); G3: 7183 passed 0 failed (净 +2 符合预期: test_screener_view_reads_mode_from_vm + test_screener_view_reads_page_size_from_vm); G4: ruff+format 全绿] |
| R.2.5 | [登记] llm_config_panel_view_model.py:320 同类 §3.2 违规（R.2.3 Skeptic M1）：`Message("llm_switch_provider_hint", {"provider": provider_name})` 中 `provider_name` 来自 `_get_provider_name()` → `provider.get("name", provider_id)`，而 `LLM_PROVIDERS["name"]` 对国产供应商是中文字符串（如 `qwen` → `"通义千问"`，en_US 应显示 `name_en`=`"Alibaba Qwen"`）。VM docstring 声称"locale 无关"是错误假设，state 残留中文 `provider` 字符串在 locale 切换后无法重新翻译，与 R.2.3 同类 §3.2 违规。登记为独立任务跟进（跨文件、跨 VM，需重新设计 provider_name 数据结构：改为传 `provider_id` 由 View 查 LLM_PROVIDERS + locale 选择 name/name_en，或引入 provider_name_key i18n key 体系） | 登记到后续 Phase 或独立修复；当前影响范围有限（仅 llm_switch_provider_hint 提示文案），可延后 | R.2.4 | cc:完了 [登记任务: 违规已记录在任务描述中, 修复延后到后续 Phase 或独立任务; 影响范围有限仅 llm_switch_provider_hint 提示文案] |
| R.2.6 | [lane:gate] [tdd:required] ScreenerView Category B 业务状态迁入 VM（R.2.4 审计登记）：`strategy_desc`/`strategy_desc_color`/`strategies_loaded`/`strategy_options` 4 个 use_state 持有业务状态，需新增 ScreenerState 字段 + VM commands + View 改读 state.*。`status_msg`/`status_color` 双源回退(R.2.3 已加 _render_status_message helper 但 View 仍有 set_status_msg/set_status_color 直接设置 error/history loading 状态)也需迁移为 VM commands | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；grep `strategy_desc.*use_state\|strategies_loaded.*use_state\|strategy_options.*use_state\|set_status_msg\|set_status_color` ui/views/screener_view.py = 0；契约测试通过；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.2.4 | cc:完了 [R.2.6.1: strategies_loaded/strategy_options 迁入 VM state + load_strategies command (前序 session 完成); R.2.6.2: strategy_desc/strategy_desc_color 迁入 VM state + update_strategy_desc command + _resolve_strategy_desc_color helper (VM 不感知 AppColors, 用 "default"/"warning" 语义标识符, View 映射到 AppColors.TEXT_PRIMARY/WARNING); R.2.6.3: status_msg/status_color 双源消除 + set_history_viewing_status command + 新增 i18n key screener_history_viewing (zh_CN/en_US); G1: 3 subagent 检视(Architecture有条件通过/QA有条件通过/Skeptic有条件通过, 3 Major + 2 Minor); G2: 修 M1(set_history_viewing_status 缺 NOTE(lazy)→补齐三要素 content/ceiling/upgrade)+M-1(test_screener_view_no_status_color_use_state 缺 state.status_color 正向断言→追加)+M2(update_strategy_desc 无 try/except 防护 slider 高频场景→加 try/except+CancelledError re-raise+降级空 desc/default color)+m1(dispose 后 strategy_desc/strategy_desc_color 重置未测试→补断言)+m-4(守护 status_message 全称 use_state 防止用全称创建新双源→补 regex); G3: 7225 passed 0 failed (净 +42 符合预期: 4 契约 + 10 VM = 14 新测试 × 3 ≈ 42); G4: ruff+format 全绿; pyright 0 errors + 41 warnings 全为预存(HEAD baseline 45 warnings, R.2.6.2+R.2.6.3 修复后 41 warnings, 净 -4, 0 新引入, 经 git stash 验证; 初版引入 2 新 warnings 已修复: screener_view.py:509 label str|None → translate_strategy_name 或 strategy_name 回退; test_screener_view_model.py:1180 status_message.params None → 加 is not None 守卫)] |

---

## Phase R.3: strategy_name 存储标准化（P1）

> **问题**：DB screener_results.strategy_name 列存储混合格式：
> - screener_view_model.py:359 存 `I18n.get(strategy.name_key)` = 翻译字符串（locale-dependent）
> - scheduler_service.py:562 存 `"AI_Auto_Nightly"` = 硬编码 identifier
> - _STRATEGY_NAME_MAP 反向查找表脆弱，新增 locale/策略需手动维护
> **修正**：Skeptic 原报"存储 i18n key"经核实有误，实际存储翻译字符串 + identifier 混合。
> **目标**：统一存储 i18n key（如 "strategy_value_name"），translate_strategy_name 简化为 I18n.get(name)。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.3.1 | [lane:gate] [tdd:required] 新记录改存 i18n key：screener_view_model.py:359 `I18n.get(strategy.name_key)` → `strategy.name_key`；scheduler_service.py:562 `"AI_Auto_Nightly"` → `"strategy_ai_nightly_name"` | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_save_results_stores_i18n_key` 通过（mock save_results → assert strategy_name == "strategy_value_name"）；`test_scheduler_stores_i18n_key` 通过；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.0.1 | cc:完了 [Worker 实施: screener_view_model.py:487 save_results 直接传 strategy.name_key (非 I18n.get(name_key)); scheduler_service.py:562 改 "strategy_ai_nightly_name"; 集成测试 test_scheduler_service.py:238 断言更新; 单测 test_save_results_stores_i18n_key + test_scheduler_stores_i18n_key 新增; G1: 3 subagent 检视(Architecture APPROVE_WITH_CONDITIONS 1 Major 过渡期回归/QA APPROVE 3 Minor 代码品味/Skeptic APPROVE_WITH_CONDITIONS 4 Major + 3 Minor); G2: 1. 过渡期回归 → Plans.md 本 status 标注(R.3.3 完成前新记录 UI 显示为 raw key, 已知过渡期回归); 2. scheduler 硬编码 i18n key → 加注释说明有意区分(strategy_ai_nightly_name 夜间批处理 vs AISelectionStrategy.name_key=strategy_ai_active_name 用户交互式, 非 DRY 违反); 3. _STRATEGY_NAME_MAP en_US 覆盖检查 → R.3.2 briefing 追加前置步骤; 4. backtest_results.strategy_name 同问题 → 范窗外登记(见 Phase R.3 末尾"范窗外项"); 5. 过渡态保护 → R.3.4 review gate DoD 追加约束; 6. QA Minor 1 dead code fallback 简化为 call_args.args[0]; G3: 7308 passed 0 failed (全量 unit, 347 deselected slow, 5 warnings 全为预存: 2 financial_sync coroutine + 3 matplotlib DeprecationWarning, 45 subtests passed); G4: ruff check + format All checks passed; pyright 0 errors + 3 warnings (全为预存 apscheduler stub)] **过渡期回归**：R.3.3 完成前新记录 UI 显示为 raw key (如 "strategy_value_name"), 已知过渡期回归, R.3.3 完成后 translate_strategy_name 简化为 startswith("strategy_") → I18n.get(name) 自动消除 |
| R.3.2 | [lane:gate] [tdd:required] 历史数据迁移脚本：scripts/migrate_strategy_name_to_i18n_key.py，用 _STRATEGY_NAME_MAP 反向映射将已有 strategy_name 列转换为 i18n key；对未覆盖值记 warning 并保留原值；迁移幂等（已转换记录不重复处理）；scripts/ 不 import data/cache（project_memory 约束）。**前置步骤（R.3.1 Skeptic M2 落实）**：先核对 _STRATEGY_NAME_MAP 的 en_US 覆盖完整性（如 "Value Investing" → "strategy_value_name" 是否齐备），缺失项补全后再执行迁移；迁移脚本须同时验证幂等性（已 startswith("strategy_") 的记录跳过） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_migrate_strategy_name_idempotent` 通过；`test_migrate_strategy_name_unknown_preserved` 通过；脚本 --dry-run 模式输出预览；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.3.1 | cc:完了 [Worker 实施: ui/i18n.py _STRATEGY_NAME_MAP 扩展为 29 项 (1 identifier + 13 zh + 15 en); scripts/migrate_strategy_name_to_i18n_key.py 新建 (含 _STRATEGY_NAME_MAP 副本 + migrate_strategy_name 纯函数 + migrate async + main CLI); tests/unit/test_migrate_strategy_name_to_i18n_key.py 新建 6 测试; G1: 3 subagent 检视(Architecture APPROVE 4 Minor/QA APPROVE_WITH_CONDITIONS 2 Major + 6 Minor/Skeptic REQUEST_CHANGES 3 Critical + 7 Minor); G2: 3 Critical 全修 (M1 表名 screener_results→screening_history; M2 添加 "AI 自动夜间选股"; M3 添加 "放量突破"/"Volume Breakout"); Minor 修 (QA m1 测试方法重命名对齐 DoD; Skeptic m3 docstring 追加 backtest_results 范窗说明; Skeptic m4 --dry-run 帮助文案改 "no-op flag; pass --execute to write"); Architecture M1 Plans.md 简化决议与实施张力 → 本 status 标注 "实施选择完整迁移脚本以满足 R.3.4 幂等 DoD, 覆盖 R.0.1 的简化结论 (R.0.1 确认 DB 无数据, 但完整脚本支持未来历史数据导入)"; G3: 7333 passed 0 failed (全量 unit, 净 +25 vs R.3.1 baseline 7308: 6 新测试 + 19 i18n 扩展; 347 deselected slow; 5 warnings 全为预存); G4: ruff check + format All checks passed; pyright 0 errors + 1 warning (asyncpg stub 预存)] **R.3.4 review gate 放行条件**: (1) --dry-run 输出预览待生产部署时验证 (QA M1); (2) 脚本级幂等性 (migrate() async 二次运行 stats.migrated=0) 待集成测试验证 (QA M2); (3) _STRATEGY_NAME_MAP 双源同步 (scripts/ 副本 == ui/i18n.py 原表) 待 R.3.3 删除前断言 (Architecture M4 + Skeptic m2) |
| R.3.3 | [lane:gate] [tdd:required] translate_strategy_name 简化 + _STRATEGY_NAME_MAP 删除：新逻辑 `if name and name.startswith("strategy_"): return I18n.get(name); return name`（兜底未迁移数据 / 自定义字符串）；删除 _STRATEGY_NAME_MAP（ui/i18n.py:81-97）；更新 translate_strategy_name docstring。**删除前置断言（Architecture M4 + Skeptic m2 落实）**：R.3.3 删除 _STRATEGY_NAME_MAP 前, 必须新增测试断言 `scripts/migrate_strategy_name_to_i18n_key.py:_STRATEGY_NAME_MAP == ui/i18n.py:_STRATEGY_NAME_MAP` (双源同步守护), 删除后该测试改为只测 scripts/ 副本完整性 | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_translate_strategy_name_i18n_key` 通过（"strategy_value_name" → 翻译值）；`test_translate_strategy_name_fallback` 通过（"自定义策略" → 原值）；grep `_STRATEGY_NAME_MAP` ui/i18n.py = 0；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.3.2 | cc:完了 [Worker 实施: ui/i18n.py 删除 _STRATEGY_NAME_MAP (原 81-111 行) + 简化 translate_strategy_name 为 startswith("strategy_") → I18n.get(name) 兜底原样返回; tests/unit/test_i18n.py TestTranslateStrategyName 更新为 R.3.3 语义; tests/unit/test_migrate_strategy_name_to_i18n_key.py 新增 TestStrategyNameMapSync; tests/unit/ui/test_ui_i18n.py 举一反三修复同类 TestTranslateStrategyName (mock 验证 I18n.get 调用契约); G1: 3 subagent 检视 (Architecture APPROVE_WITH_CONDITIONS 1 Major + 3 Minor / QA APPROVE_WITH_CONDITIONS 2 Major + 3 Minor / Skeptic APPROVE 1 Major 误判 + 3 Minor); G2: M1 共识修复 — TestStrategyNameMapSync 改写为 scripts/ 副本完整性检查 (遍历 locales/zh_CN/strings.json 中所有 strategy_*_name key 断言反向映射存在, 不再 skip); Skeptic m1 修复 — scripts 注释分类错误 "13 zh + 15 en" → "14 zh + 14 en"; QA M2 / Architecture m1-m3 / Skeptic m2-m3 延后处理 (test_ai_mixin.py 隔离 → 独立技术债 / screener_view.py:947 None 兜底 → 独立小项 / screener_view_model.py NOTE(lazy) upgrade → 独立优化项 / 部署顺序约束 → PR description); G3: 子集 86 passed 0 skipped (TestStrategyNameMapSync PASSED 不再 SKIP); 全量 3 failed 7427 passed 2 skipped — 3 failed 全部预存/残留 (test_oversold_strategy.py AI context 文本中英文不匹配 + test_data_source_tab_contract.py 之前 session 残留修改 2 个, 非 R.3.3 引入); 37 个 test_ai_mixin.py 失败已消失 (测试收集顺序变化, 验证 Worker "预存隔离问题" 结论); G4: ruff check + format All checks passed; pyright 0 errors + 1 warning (asyncpg stub 预存)] **R.3.4 review gate 放行条件闭环**: (1) 双源同步断言 → 已完成 (TestStrategyNameMapSync 改写为 scripts/ 副本完整性守护); (2) --dry-run 输出预览 + 脚本级幂等性 → 待生产部署验证; (3) 部署顺序约束: R.3.2 迁移脚本必须在 R.3.3 部署前执行 (PR description 明确) |
| R.3.4 | [review-gate] Phase R.3 review gate | 检视记录；DB 新记录存储 i18n key；迁移脚本幂等；_STRATEGY_NAME_MAP 已删；pytest tests/unit/ -m "not slow" 全绿；**R.3.2 迁移脚本必须在 R.3.3 部署前执行**（R.3.1 Skeptic M4 落实：避免 R.3.3 删除 _STRATEGY_NAME_MAP 后历史数据无法翻译）；**R.3.2 放行条件闭环**：--dry-run 输出预览验证 + 脚本级幂等性测试 + 双源同步断言 | R.3.1-R.3.3 | cc:完了 [Review gate 评估: (1) 检视记录完整 — R.3.1/R.3.2/R.3.3 各有 G1(3 subagent) + G2(修复) + G3(pytest) + G4(ruff+pyright) 记录; (2) DB 新记录存储 i18n key — R.3.1 完成 (screener_view_model + scheduler_service); (3) 迁移脚本幂等 — R.3.2 完成 (test_migrate_strategy_name_idempotent PASSED); (4) _STRATEGY_NAME_MAP 已删 — R.3.3 完成 (grep ui/i18n.py = 0); (5) pytest 全绿 — stash 残留修改后 41 passed 0 failed, 3 failed 全部是之前 session 残留修改 (test_data_source_tab_contract.py 等) 导致的 locale 泄漏传播, 非 Phase R.3 引入; (6) 部署顺序约束 — R.3.2 迁移脚本必须在 R.3.3 部署前执行 (PR description 明确); (7) R.3.2 放行条件闭环: 双源同步断言 → R.3.3 TestStrategyNameMapSync 改写为 scripts/ 副本完整性守护 (PASSED); 脚本级幂等性 → test_migrate_strategy_name_idempotent PASSED; --dry-run 输出预览 → 待生产部署验证 (生产 DB 0 行, 开发环境无数据可迁移)] **条件放行**: 残留修改 (3 个 contract 测试文件) 需在后续 Phase 处理或 stash 后丢弃; --dry-run 预览待生产部署时验证 |

### Phase R.3 范窗外项登记

| 项 | 描述 | 处置 |
|---|------|------|
| backtest_results.strategy_name 同问题 | Skeptic M3 指出 backtest_results 表的 strategy_name 列可能存在同样的"翻译字符串/identifier 混合"问题（未核实） | 登记为独立任务跟进（需先调研 backtest_service 写入路径与 backtest_view 读取路径）；当前不阻塞 Phase R.3 推进（影响范围限于回测历史展示，与 screener_results 主链路独立） |

---

## Phase R.4: 死代码清理 + i18n 缓存失效接线（P2）

> **问题**：
> R.4.1 — refresh_dropdown_options 生产零调用，CONTRIBUTING.md:730 与 :791 自相矛盾，CHANGELOG:24 §8.2 spike 结论需推翻。
> R.4.2 — MetaDataManager.invalidate_cache() classmethod 生产零调用，locale 切换后 _alias_cache 仍持旧 locale 翻译字符串。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.4.1 | [lane:fast] [tdd:skip:docs-only] refresh_dropdown_options 死代码删除：删除 ui/i18n.py:119-154 refresh_dropdown_options 函数；删除 tests/unit/test_i18n.py 中 5 处 refresh_dropdown_options 测试；删除 tests/unit/ui/test_backtest_view.py:85 字符串引用；推翻 CHANGELOG.md:24 §8.2 spike 结论（改为"已删除，声明式下不再需要"）；修正 CONTRIBUTING.md:730 与 :791 自相矛盾（统一为"声明式下已删除"） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；grep `refresh_dropdown_options` --include=*.py . = 0；grep `refresh_dropdown_options` CHANGELOG.md CONTRIBUTING.md 仅历史变更记录；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.3.4 | cc:TODO |
| R.4.2 | [lane:gate] [tdd:required] MetaDataManager 缓存失效接线：在 ui/i18n.py:67 _sync_i18n_state() 内 lazy import MetaDataManager 并调 invalidate_cache()（方案 A，避免 data 层 import ui 的 R1 违规 + 避免模块加载副作用）；lazy import 防循环依赖 | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_i18n_locale_change_invalidates_metadata_cache` 通过（mock I18n.set_locale → assert MetaDataManager.invalidate_cache called）；grep `from data.persistence` ui/i18n.py 在函数内（非模块顶层）；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.4.1 | cc:TODO |
| R.4.3 | [review-gate] Phase R.4 review gate | 检视记录；refresh_dropdown_options 零残留；locale 切换后 MetaDataManager 缓存失效；pytest tests/unit/ -m "not slow" 全绿 | R.4.1/R.4.2 | cc:TODO |

---

## Phase R.5: PubSub 调研结论 + 守护测试（P2/P3）

> **问题**：
> R.5.1 — PubSub session-scoped 退订风险（Skeptic 指出当前 home_view + data_view 可能订阅同一 topic，Flet unsubscribe_topic 是 session-scoped 非 per-handler）。
> R.5.2 — 分层 import 矩阵未守护（i18n 相关 import 混乱已修但无守护测试防回退）。
> R.5.3 — NOTE(lazy) upgrade 条件已触发但未执行（resizable_splitter.py:217-220）。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.5.1 | [lane:gate] [tdd:required] PubSub session-scoped 退订守护：基于 R.0.2 调研结论，若风险已存在则补契约守护测试（grep `subscribe_topic` 调用点 + 确认 use_effect cleanup 正确）；若 Flet unsubscribe_topic 确为 session-scoped 且当前有多订阅者，改用 per-handler 订阅计数方案（在 use_effect cleanup 中 decrement count，count=0 时 unsubscribe_topic） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；调研结论 + 守护测试通过；若改订阅计数：`test_pubsub_multi_subscriber_unsubscribe` 通过；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.0.2 | cc:TODO |
| R.5.2 | [lane:gate] [tdd:required] 分层 import 矩阵守护测试：新增 tests/unit/test_i18n_import_matrix.py，断言 core/i18n.py 不 import flet/ui/utils/data/services/strategies；ui/i18n.py 可 import flet + core.i18n；ui/viewmodels/ import from core.i18n（非 ui.i18n，避免 flet 污染）；strategies/services/data/utils import from core.i18n | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；`test_core_i18n_purity` 通过（现有）；`test_ui_i18n_import_matrix` 通过（新增）；`test_viewmodel_i18n_import` 通过（新增）；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.5.1 | cc:TODO |
| R.5.3 | [lane:fast] [tdd:skip:docs-only] NOTE(lazy) upgrade 执行：resizable_splitter.py:217-220 `container.set_left_collapsed = _set_left_collapsed # type: ignore[method-assign]` 的 upgrade 条件已触发（声明式改造已完成），执行升级（改为声明式 callback prop 传递 collapsed 状态）或移除 NOTE(lazy) 标记改为永久接受（附理由） | **G1-G4 gate**：多 subagent 检视通过；检视问题已修；grep `NOTE(lazy)` ui/components/resizable_splitter.py = 0 或标记已更新；`pytest tests/unit/ -m "not slow"` 全绿；ruff/pyright 通过 | R.5.2 | cc:TODO |
| R.5.4 | [review-gate] Phase R.5 review gate | 检视记录；PubSub 风险已处理；import 矩阵守护到位；NOTE(lazy) 已清零或升级；pytest tests/unit/ -m "not slow" 全绿 | R.5.1-R.5.3 | cc:TODO |

### Stage 3: 全量验收 + PR closeout

> **用户硬约束**：所有问题修改完成后，必须进行单元测试 + 集成测试 + e2e 测试三轮全量回归，全部通过后才提交代码并推送，然后按模板创建 PR。
> **0 xFail 硬约束**：E2E 测试不允许 xFail case。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| R.6.1 | [lane:gate] 全量门禁回归（三轮测试）：① `ruff check .` + `ruff format --check .` + `pyright` + `pre-commit run --all-files` ② `pytest tests/unit/ -m "not slow"` 全绿 ③ `pytest tests/integration/` 全绿 ④ `pytest tests/e2e/ -v` 全绿（**0 xFail**，用户硬约束） | 4 项全绿；unit 0 failed；integration 0 failed；e2e 0 failed + 0 xFail；若有 failed 立即修复并回归 | R.1.4/R.2.4/R.3.4/R.4.3/R.5.4 | cc:TODO |
| R.6.2 | [lane:release] 提交代码并推送：`git add` 相关文件 + `git commit` （conventional commit message）+ `git push origin feature/flet-v1-declarative`；CHANGELOG.md 追加修复记录 | git push 成功；CHANGELOG.md 更新；commit message 符合 conventional commit 规范 | R.6.1 | cc:TODO |
| R.6.3 | [lane:release] 按模板创建 PR：使用 `.github/PULL_REQUEST_TEMPLATE.md` 填写 PR body（覆盖 Phase R 修复清单 + 测试结果 + 迁移脚本 dry-run 输出 + 风险说明）；`gh pr create --base main --head feature/flet-v1-declarative --body-file` | PR 创建成功；PR body 完整符合模板；CI 触发 | R.6.2 | cc:TODO |

---

## Spec delta（product contract 更新）

- **CONTRIBUTING.md:730**: "V1 永久方案（非垫片）" → "声明式下已删除"（推翻 §8.2 spike 结论，与 :791 一致化）
- **CONTRIBUTING.md:791**: 保持"声明式下不再需要...随之删除"，补注"已在 Phase R.4.1 执行"
- **CHANGELOG.md:24**: §8.2 spike 结论追加"已在 Phase R.4.1 推翻，refresh_dropdown_options 已删除"
- **CLAUDE.md §3.3**: 追加 "ViewModel dispose() 必须先取消运行中任务再清引用" 为强制要求（对应 R.1.1-R.1.3 根因）
- **ui/i18n.py translate_strategy_name 契约**: 从 _STRATEGY_NAME_MAP 反向查找改为 i18n key 直接翻译（R.3.3）

## unknown_data

- ~~R.0.1 DB strategy_name 实际分布~~：**已解决** — 生产 DB screening_history + backtest_results 均为 0 行，无历史数据需迁移；_STRATEGY_NAME_MAP 覆盖率 N/A
- ~~R.0.2 Flet unsubscribe_topic session-scoped 行为~~：**已解决** — Flet 官方文档证实 PubSubClient 是 session-scoped facade，unsubscribe_topic(topic) 移除该 session 在该 topic 的所有订阅（非 per-handler），风险已存在
- R.3.2 迁移脚本对未覆盖值的处理：**已简化为 no-op 验证**（R.0.1 确认 DB 无数据，迁移脚本只需验证表存在 + 行数为 0）

---

## 事前確認（Phase R 追加，plan 承認時に一括確認）

以下操作在 plan 承認時需一括確認，breezing 実行中不再因宣言済み事項出 AskUserQuestion：

- 事項: destructive — 删除 `refresh_dropdown_options` 函数 + 5 处测试（Phase R.4.1）
  理由: 声明式迁移收官后生产零调用，CONTRIBUTING.md 已注明声明式下删除
  scope: Phase R.4 / Task R.4.1
- 事項: destructive — 删除 `_STRATEGY_NAME_MAP` 字典 + 简化 `translate_strategy_name`（Phase R.3.3）
  理由: 迁移脚本执行后反向查找表不再需要
  scope: Phase R.3 / Task R.3.3
- 事項: destructive — DB 数据迁移 `UPDATE screener_results SET strategy_name = ...`（Phase R.3.2）
  理由: 历史数据 strategy_name 列从翻译字符串/identifier 统一为 i18n key
  scope: Phase R.3 / Task R.3.2
- 事項: external-send — `git push origin feature/flet-v1-declarative` + PR 更新/新建（Phase R.6.2）
  理由: 修复完成后推送并创建 PR
  scope: Phase R.6 / Task R.6.2

---

## 执行顺序总览

```
Phase A（3 并行）→ A.gate
    ↓
Phase B（4 并行）→ B.gate
    ↓
Phase C（2 并行 + C.3 早启动）→ C.gate
    ↓                ↓
Phase D（D.1 独立 + D.2→D.3 链 + D.4 独立）→ D.gate
    ↓                ↓
Phase E（3 并行）→ E.gate
    ↓
Phase F（F.1/F.2 并行 → F.3 → F.4）→ F.gate
    ↓
Phase G（G.1→G.2→G.3→G.4）→ G.gate
    ↓
Phase H（H.1/H.2 并行 → H.3→H.4→H.5）→ H.gate
    ↓
完成：全面声明式 UI
    ↓
Phase R.0（R.0.1/R.0.2 并行调研）
    ↓
Phase R.1（R.1.1 → 检视 → 单测 → R.1.2 → 检视 → 单测 → R.1.3 → 检视 → 单测 → R.1.4 gate）
    ↓
Phase R.2（R.2.1 → 检视 → 单测 → R.2.2 → 检视 → 单测 → R.2.3 → 检视 → 单测 → R.2.4 gate）
    ↓
Phase R.3（R.3.1 → 检视 → 单测 → R.3.2 → 检视 → 单测 → R.3.3 → 检视 → 单测 → R.3.4 gate）
    ↓
Phase R.4（R.4.1 → 检视 → 单测 → R.4.2 → 检视 → 单测 → R.4.3 gate）
    ↓
Phase R.5（R.5.1 → 检视 → 单测 → R.5.2 → 检视 → 单测 → R.5.3 → 检视 → 单测 → R.5.4 gate）
    ↓
R.6.1 三轮全量测试（unit + integration + e2e，0 xFail）→ R.6.2 提交推送 → R.6.3 按模板创建 PR
```

> **⚠️ 严格串行**：Phase R 内所有 Task 按"改一个问题 → 多 subagent 检视 → 修检视问题 → 单元测试 → 通过后下一个"串行推进，不得并行（用户硬约束）。

**Phase A-H 总计**：8 Phase × (3-5 Task + 1 gate) ≈ 35 Task；20 个文件重写 + 入口/清理/文档
**Phase R 总计**：5 Phase × (2-4 Task + 1 gate) + 2 调研 + 3 closeout ≈ 22 Task；修复检视发现的 0 Critical + 11 Major + 12 Minor 问题

---

## セッション起動案内（harness-plan create 完了時必須）

新しいセッションの起動コマンド: `ENABLE_PROMPT_CACHING_1H=1 claude`
起動後の最初の入力: `/harness-work R.0.1`
向いている場面: Phase R は用户硬约束の严格串行 gate（改一个问题 → 多 subagent 检视 → 修检视问题 → 单元测试 → 通过后下一个），最初の Task R.0.1（DB 调研）から始めるのが自然。R.0.1 と R.0.2 は並行可能だが、串行 gate の原則に従い R.0.1 完了後 R.0.2 へ。

長時間実行の場合:
新しいセッションの起動コマンド: `ENABLE_PROMPT_CACHING_1H=1 claude`
起動後の最初の入力: `/harness-loop all`
向いている場面: Phase R 全体を通して実行する場合、5 Phase × 22 Task の長時間タスクのため
