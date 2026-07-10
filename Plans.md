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
| D.5 | [review-gate] Phase D review gate | - | 检视记录；`grep "PageRefMixin" ui/components/`=0；4 文件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿 | D.1-D.4 | cc:TODO |

---

## Phase E: 高复杂度视图

> 依赖 Phase A/D 的高复杂度视图。
> **并行可能**：E.1/E.2/E.3 互不依赖（各自依赖已完成的 Phase A/D）

| Task | 文件 | VM 策略 | 内容 | DoD | Depends | Status |
|------|------|---------|------|-----|---------|--------|
| E.1 | `ui/views/settings_tabs/ai_brain_tab.py` (768行) | use_state（三阶段保存） | `@ft.component` + `use_state`；消费已声明式 LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel；三阶段保存流程用 state 驱动；移除 did_mount/will_unmount/_on_locale_change/_save_ai_settings 命令式 | grep 命令式=0；三阶段保存验证；测试通过 | D.1 | cc:完了 [768→675行, 38 契约测试, _SAVE_IDLE/_SAVE_SAVING/_SAVE_SUCCESS/_SAVE_ERROR 状态机, 3 子 VM use_viewmodel 内部模式, R2 合规, test_onboarding_api_contracts 修复] |
| E.2 | `ui/views/settings_tabs/data_source_tab.py` (972行) | use_viewmodel(DataSourceViewModel) | `@ft.component` + `use_viewmodel`；消费已声明式 TushareConfigPanel/MetricCard/ActionChip/HealthReportDialog；9 个 state diff dispatch 移除（VM subscribe 自动重渲染）；AlertDialog 用条件渲染；移除 did_mount/will_unmount/refresh_locale | grep 命令式=0；9 个 _on_vm_* 方法移除验证；测试通过 | A.1 | cc:完了 [40 契约测试, use_viewmodel(DataSourceViewModel) 内部模式, 11 _on_vm_* 方法移除, _HEALTH_STATUS_VISUALIALS 类型修复, pyright 0 errors, _get_page/_build_history_years_options/_render_message/_resolve_snack_color/_build_health_summary_content 纯函数] |
| E.3 | `ui/components/health_report_dialog.py` (844行) 完整化 | use_state（HealthScanDialog）+ 纯函数子组件 | HealthScanDialog 命令式 class 重写为 `@ft.component` + `use_state`；4 个命令式子组件 class（HealthScoreCard/MetricTile/KeyMetricsGrid/CoverageDetailTable）重写为模块级纯函数；跨线程 future 管理改 `use_effect` + R2 CancelledError；HealthReportDialog 已声明式保留 | `grep "class.*ft\.\(Container\|Column\|AlertDialog\)" ui/components/health_report_dialog.py`=0；跨线程取消验证；测试通过 | A.1 | cc:完了 [844→900行, 62 契约测试, 4 class→纯函数, HealthScanDialog @ft.component, futures_ref use_ref + use_effect cleanup, R2 CancelledError raise, data_source_tab 消费方适配] |
| E.4 | [review-gate] Phase E review gate | - | 检视记录；3 文件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿 | E.1-E.3 | cc:TODO |

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
| F.5 | [review-gate] Phase F review gate | - | 检视记录；4 文件形态契约一致；`grep "PageRefMixin" ui/`=0；流式/拖拽性能达标；`pytest tests/unit/ -m "not slow"` 全绿 | F.1-F.4 | cc:TODO |

---

## Phase G: 入口 + 特殊控件 + 清理

> main.py 入口改造 + v1_compat.py 删除 + FilePicker 声明式挂载。
> **grep 验收**：`grep -rn "v1_compat\|PageRefMixin\|_page_ref" --include=*.py .` = 0

| Task | 文件 | 内容 | DoD | Depends | Status |
|------|------|------|-----|---------|--------|
| G.1 | `main.py` | 入口改造：`close_confirm_dialog.update()` 改 state 驱动；`StartupViewRenderer` 改 `@ft.component`；移除 `_on_resize`（AppLayout use_effect 自管）；`startup_views.py` 改 `page.add(AppLayout())` | `grep "\.update()" main.py`=0；`grep "_on_resize" main.py`=0；ruff/pyright 通过 | Phase F | cc:TODO |
| G.2 | FilePicker 声明式挂载（3 处） | `data_view.py`/`screener_view.py`/`local_model_config_panel.py` 的 FilePicker：`use_ref` + `use_effect` 挂载到 `page.services` + state 驱动 pick 结果（Phase F.2/F.3 已处理 data_view/screener_view；本 Task 补 local_model_config_panel 验证） | 3 处 FilePicker `grep "page.services.append\|page.overlay.append"` 用 `use_effect` 包装；`on_result` 回调改 command；ruff/pyright 通过 | Phase F | cc:TODO |
| G.3 | `ui/v1_compat.py` 删除 | 删除 `v1_compat.py` + `test_v1_compat.py`；`test_mock_flet_contract.py` 重写为 V1 原生 mock 契约 | `grep "v1_compat" .`=0（含文件删除）；`pytest tests/unit/ui/test_mock_flet_contract.py` 通过 | G.1/G.2 | cc:TODO |
| G.4 | [review-gate] Phase G review gate | 检视记录；v1_compat 无残留；FilePicker/AlertDialog 形态契约一致；grep+静态全绿 | G.1-G.3 | cc:TODO |

---

## Phase G2: 统一测试修复（全量验收）

> Phase A-F 改造期混合态失败随消费方声明式化已消解，本 Phase 统一修复剩余测试问题。
> **硬约束**：本 Phase 完成后 `pytest tests/unit/ -m "not slow"` 必须全绿。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| G2.1 | 单元测试统一修复：修复所有混合态遗留测试失败（命令式消费方测试 mock 调整、契约守护测试补全、PaginatedTable 测试修复等） | `pytest tests/unit/ -m "not slow"` 全绿（0 failed）；失败数从 86 降到 0 | G.4 | cc:TODO |
| G2.2 | 集成测试统一修复：`pytest tests/integration/` 全绿 | `pytest tests/integration/` 全绿 | G2.1 | cc:TODO |
| G2.3 | 全量门禁回归：`ruff check .` + `ruff format --check .` + `pyright` + `pytest tests/unit/ -m "not slow"` + `pytest tests/integration/` + `pre-commit run --all-files` 全绿；`grep -rn "v1_compat\|PageRefMixin\|_page_ref\|did_mount\|will_unmount" --include=*.py ui/ main.py`=0 | 6 项门禁全绿；grep 全部=0 | G2.1/G2.2 | cc:TODO |
| G2.4 | [review-gate] Phase G2 review gate | 检视记录；单元+集成测试全绿；门禁全绿 | G2.3 | cc:TODO |

---

## Phase H: E2E + 文档同步

> E2E xfail 消除（用户硬约束）+ 文档同步。
> **用户硬约束**：E2E test cases must all pass, no xFail cases allowed

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| H.1 | E2E 完整回归（11 文件）+ xfail 消除：移除 `@pytest.mark.xfail`；E2E 选择器同步调整（不依赖具体 Flet 控件类名）；视口策略验证 | `pytest tests/e2e/ -v` 全绿（**0 xFail**）；E2E 选择器对声明式改造透明 | Phase G | cc:TODO |
| H.2 | 22 项 grep 验收 + 9 类混合态清零验证 | 22 项 grep 全部达标；9 类混合态全部清零 | G.4 | cc:TODO |
| H.3 | CONTRIBUTING.md 同步：技术债标记"已偿还"；四项强制约束沉淀到 V1 声明式 UI 开发规范；删除命令式存量附录；版本号与 CLAUDE.md 同步 | `grep "refresh_locale\|handle_resize\|self\.update()" CONTRIBUTING.md` 仅历史引用；命令式附录已删除；版本号一致 | H.1/H.2 | cc:TODO |
| H.4 | CLAUDE.md §3.3 已知技术债标记"已偿还"/"已实现"；版本号与 CONTRIBUTING.md 一致 | CLAUDE.md §3.3 两个技术债条目标记完成；版本号一致 | H.3 | cc:TODO |
| H.5 | [review-gate] Phase H review gate | 检视记录；E2E 0 xFail；22 项 grep + 9 类混合态清零；文档版本号一致；`pytest tests/unit/ tests/integration/ tests/e2e/` 全绿 | H.1-H.4 | cc:TODO |

---

## 事前確認（plan 承認時に一括確認）

以下操作在 plan 承認時已一括確認，breezing 実行中不再因宣言済み事項出 AskUserQuestion：

- 事項: destructive — 删除 `ui/v1_compat.py` + `tests/unit/ui/test_v1_compat.py`（Phase G.3）
- 事項: destructive — 删除 CONTRIBUTING.md 命令式存量附录（Phase H.3）
- 事項: external-send — `git push origin feature/flet-v1-declarative` + 最终 PR（Phase H.5 后）

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
```

**总计**：8 Phase × (3-5 Task + 1 gate) ≈ 35 Task；20 个文件重写 + 入口/清理/文档
