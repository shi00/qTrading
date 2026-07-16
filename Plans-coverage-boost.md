# AStockScreener 测试覆盖率提升计划

作成日: 2026-07-15

---

## 背景与目标

当前 18 个源文件覆盖率低于项目阈值（per-file ≥80%, overall ≥85%，定义于 `pyproject.toml`）。本计划补齐测试用例，使所有源文件达标且整体覆盖率达标，不修改任何源码（仅新增/补充测试）。

**基线数据**（来自 `logs/converage.txt`）:

| 文件 | Cov% | Stmts | Miss |
|------|------|-------|------|
| ui/views/settings_tabs/automation_tab.py | 13.8% | 206 | 171 |
| ui/components/config_panels/llm_config_panel.py | 17.1% | 141 | 113 |
| ui/views/backtest_view.py | 19.7% | 56 | 43 |
| ui/components/config_panels/local_model_config_panel.py | 20.8% | 109 | 85 |
| ui/views/settings_tabs/system_tab.py | 21.7% | 292 | 207 |
| ui/views/settings_tabs/ai_brain_tab.py | 24.3% | 213 | 156 |
| ui/components/config_panels/database_config_panel.py | 28.0% | 65 | 46 |
| ui/components/backtest/backtest_config_panel.py | 28.6% | 81 | 57 |
| ui/components/config_panels/tushare_config_panel.py | 30.0% | 74 | 49 |
| ui/app_layout.py | 32.7% | 93 | 58 |
| ui/views/screener_view.py | 36.2% | 534 | 321 |
| ui/components/toast_manager.py | 56.1% | 157 | 67 |
| app/bootstrap.py | 62.5% | 166 | 53 |
| ui/components/health_report_dialog.py | 66.1% | 220 | 73 |
| data/sync/holder.py | 76.1% | 465 | 103 |
| ui/views/home_view.py | 76.5% | 77 | 17 |
| utils/security_utils.py | 75.6% | 299 | 57 |
| data/sync/financial.py | 79.9% | 518 | 96 |

**Spec skip reason**: 本次为补测任务，不改 product behavior/API/data model/权限/课金/外部连携。覆盖率阈值已由 `pyproject.toml` [tool.coverage.report] fail_under=85 + [tool.custom_coverage] per_file_minimum=80 定义（配置而非 spec）。项目宪法 `CLAUDE.md` §3 + `CONTRIBUTING.md`「测试规范」已充分定义测试规范。无需创建/更新 spec.md。

**team_validation_mode**: subagent（已通过 search subagent 完成 18 文件源码与现有测试调研）

**unknown_data**:
- 部分现有测试文件（`test_config_panels.py` / `test_backtest_config_panel.py` / `test_health_report_dialog_contract.py` / `test_screener_view_contract.py`）未读取完整内容，实现时需先核对已覆盖路径，避免重复测试
- `component_renderer` 的具体 API（`make_component`/`render_once`/`run_mount_effects`/`run_unmount_effects`/`run_render_effects`）需在实现时核对 `tests/unit/ui/component_renderer.py`

**测试范式（统一遵循）**:
- UI 层（14 文件）: 契约守护（grep 模式检查禁止的命令式 API）+ 模块级纯函数测试 + 组件运行时测试（`component_renderer` + FakeVM，参考 `tests/unit/ui/test_home_view.py::TestHomeViewRuntime`）
- 非 UI 层（4 文件）: 常规单测，mock DB engine / TushareClient / AIService / LocalModelManager，用 `_reset_all_singletons` autouse fixture 隔离单例（R7）
- 所有 async handler 测试: 验证 R2 `CancelledError` raise 语义
- 所有 UI 测试: 验证 R16 `page.run_task` 调度（不直接 await 阻塞）
- 所有外部 IO 测试: mock，不真实访问 DB/网络

---

## Stage 1: 检証・調査（已完成）

- 18 个低覆盖率文件源码结构与现有测试调研完成（见上方背景）
- 测试范式参考: `tests/unit/ui/test_app_layout_contract.py`（契约守护）+ `tests/unit/ui/test_home_view.py::TestHomeViewRuntime`（组件运行时）
- 测试基础设施确认: `tests/unit/ui/component_renderer.py` / `tests/unit/ui/conftest.py`（mock_i18n/mock_app_colors_state/mock_config_handler）/ `tests/unit/conftest.py`（_reset_all_singletons/_reset_i18n_state autouse）/ `tests/conftest.py`（keyring/litellm 全局 mock）

---

## Phase 0: worktree 隔离与基线（R18 红线）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 0.1 | [lane:gate] [tdd:skip:test-only] 创建 worktree 分支 `test/coverage-boost`，运行基线覆盖率快照并存档到 `logs/coverage_baseline_20260715.txt` | `git worktree list` 显示新 worktree；基线快照文件存在且包含 18 个低覆盖率文件 | - | cc:完了 |

---

## Phase 1: UI settings_tabs 三件套补测（高优先级，534 miss）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | [lane:gate] [tdd:skip:test-only] 补 `ui/views/settings_tabs/system_tab.py` 组件运行时测试: 9 个 async handler（`_do_language_change`/`_do_theme_change`/`_do_log_level_change`/`_do_save_concurrency`/`_do_save_db_pool`/`_do_save_thread_pool`/`_do_save_no_proxy`/`_do_export_diagnostics`）各自的成功/越界/异常路径 + 9 个 event handler 的 `page` 可用/None 早返回 + `diagnostics_exporting` 状态切换。用 `component_renderer` + FakeSystemViewModel（mock `ConfigHandler`/`ThreadPoolManager`/`ProxyManager`/`SystemDiagnosticsCollector`/`I18n`）。每 handler 1-3 个测试 | `pytest tests/unit/ui/views/settings_tabs/test_system_tab.py -v` 通过；system_tab.py 覆盖率 ≥80%；R2 守卫（9 个 async handler `CancelledError` raise）；R16 守卫（同步 handler 用 `page.run_task`） | 0.1 | cc:完了 |
| 1.2 | [lane:gate] [tdd:skip:test-only] 补 `ui/views/settings_tabs/automation_tab.py` 组件运行时测试: `AutomationTab` 5 个 async handler（`_do_schedule_toggle`/`_do_schedule_time_change`/`_do_ai_concept_toggle`/`_do_ai_concept_time_change`/`_do_ai_concept_engine_change`）成功/异常回滚/异常 toast + `NotificationsTab` 2 个 async handler（`_do_news_toggle`/`_do_interval_change`）成功/`ValueError`/异常 + 7 个 event handler `page` 可用/None + `auto_enabled`/`news_enabled` 切换 `disabled` 状态。用 `component_renderer` + mock `ConfigHandler`/`ThreadPoolManager` | `pytest tests/unit/ui/test_automation_tab.py -v` 通过；automation_tab.py 覆盖率 ≥80%；R2 守卫 | 0.1 | cc:完了 |
| 1.3 | [lane:gate] [tdd:skip:test-only] 补 `ui/views/settings_tabs/ai_brain_tab.py` 组件运行时测试: `_do_save_ai_settings` 四阶段（验证/提取/保存/重载）各分支 — 验证失败（max_cand/min_turn/concurrency/news_concurrency 越界 + ai_prompt/news_prompt 验证失败）+ 保存失败（llm_vm.save_config / ConfigHandler.save_local_ai_config / save_config / save_ai_system_prompt / set_ai_news_prompt / LocalModelManager.commit_verification_if_active 失败）+ 重载分支（local_path 存在性 / loaded_md5 vs new_md5 / ai_model_file_not_found / ai_local_model_changed toast）+ 异常路径（classify_error/classify_severity + system 级 logger.critical + settings_snack_ai_error toast）+ `_on_llm_test_connection`/`_on_reload_ai_service`/`_on_verify_local_model` 3 个 async helper + `_on_save_ai`/`_on_reset_ai_prompt`/`_on_reset_news_prompt` event handler。用 `component_renderer` + FakeLLMConfigPanelVM + FakeLocalModelConfigPanelVM + FakeFailoverConfigPanelVM（mock `AIService`/`LocalModelManager`/`ConfigHandler`）。每阶段 2-4 个测试 | `pytest tests/unit/ui/test_ai_brain_tab_contract.py -v` 通过；ai_brain_tab.py 覆盖率 ≥80%；R2 守卫；R9 守卫（_render_message 不含 api_key） | 0.1 | cc:完了 |

---

## Phase 2: UI 复杂视图补测（高优先级，364 miss）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | [lane:gate] [tdd:skip:test-only] 补 `ui/views/screener_view.py` 组件运行时测试: 14 个 handler — `_on_strategy_change`（含 `ai_system_prompt` 特殊处理 + 参数默认值初始化）+ `_on_run_click`（无策略早返回 + `set_run_disabled(True)` + `vm.run_strategy` 调用 + 异常 `logger.error`）+ `_on_sort`/`_on_page_size_change`（`ValueError` 容错）/`_on_prev_page`/`_on_next_page`/`_on_mode_change`（HISTORY/REALTIME 切换 + 同 mode 早返回）+ `_on_export_click`（无数据 toast + `file_picker.save_file` + filepath 空早返回 + 成功/失败/异常 toast）+ `_load_history_tree`（空数据/append/`len>=5` 切换 load_more_visible/异常 toast）+ `_load_history_for_date`（run_id 优先 + strategy_name fallback + `vm.set_history_viewing_status`/`load_history_data` 异常）+ `_on_tree_item_click`（`page.run_task`）+ `_on_row_click`（ts_code 提取 + `_raw_row_lookup` + `set_detail_dialog_data`）+ `_on_detail_close` + `_update_param`/`_on_slider_change`（`vm.update_strategy_desc`）+ `_do_restore_default_async`/`_do_save_prompt_async`（validate_prompt 失败/成功/异常 toast）。补充 `_build_param_control`（slider/number/dropdown/textarea 四类型）/`_build_params_panel`（default/advanced ExpansionTile/custom_groups）/`_build_log_card`（is_analyzing ProgressRing/reasoning+content）/`_build_history_tree`（空/非空/first_expand/load_more_btn）派生渲染验证。补充深度链接 `_execute_pending_strategy`（pending_strategy None 早返回 + 策略不存在 warning + 自动执行）+ FilePicker/PubSub `use_effect` cleanup。用 `component_renderer` + FakeScreenerViewModel（参考 `test_home_view.py::_FakeHomeViewModel`）+ mock `ConfigHandler`/`ThreadPoolManager`。先核对 `test_screener_view.py` 与 `test_screener_view_contract.py` 已覆盖路径避免重复 | `pytest tests/unit/ui/test_screener_view.py tests/unit/ui/test_screener_view_contract.py tests/unit/ui/test_screener_view_spike.py -v` 通过；screener_view.py 覆盖率 ≥80%；R2 守卫（8 个 async handler）；R16 守卫；R9 守卫（DataSanitizer.sanitize_error 调用验证） | 0.1 | cc:完了 |
| 2.2 | [lane:gate] [tdd:skip:test-only] 补 `ui/views/backtest_view.py` 组件运行时测试: `_on_strategy_change`（`set_selected_strategy` + `set_no_strategy_error(False)`）+ `_on_run_backtest`（无策略 `set_no_strategy_error(True)` 早返回 + `vm.create_config` 参数正确性 + `page` None RuntimeError 守卫 + `page.run_task(vm.run_backtest)` 调用）+ `_on_cancel_backtest`（`vm.cancel_backtest`）+ 状态渲染（`no_strategy_error and not is_running` → "backtest_no_strategy" / `status_message` 翻译 / `progress_message` 翻译 / `is_running` 切换 `progress_bar.visible`/`cancel_button.visible` / `progress` 数值绑定 / `result` 传递 `BacktestResultPanel` / `status_color` 映射 `_STATUS_COLOR_MAP` / `strategies` 空 `selected_strategy=None`）。用 `component_renderer` + FakeBacktestViewModel。先核对 `test_backtest_view.py` 已覆盖路径 | `pytest tests/unit/ui/test_backtest_view.py -v` 通过；backtest_view.py 覆盖率 ≥80%；R16 守卫 | 0.1 | cc:完了 |

---

## Phase 3: UI config_panels 补测（中优先级，350 miss）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/config_panels/llm_config_panel.py` 测试: 模块级纯函数 — `_get_provider_name`（zh_CN 用 name / 非 zh_CN 用 name_en fallback name / 缺失字段 fallback provider_id）+ `_build_provider_options`（国内/国际/自定义分组 + disabled 分组标题 + 缺失 provider 跳过）+ `_build_model_options`（tag 空/tag 翻译/缺失 models/model 缺失 name fallback id）+ `_build_links_row`（三 url 都存在/部分缺失/compact 与非 compact 按钮样式）+ `_on_test_click_factory`/`_on_save_click_factory`/`_on_refresh_click_factory`/`_on_provider_change_factory` 4 个工厂函数（page 可用/None/RuntimeError 守卫）。组件运行时 — `compact=True/False` + `show_save_button` + `show_register_link` + `is_azure=True` 显示 azure 字段（azure_resource/azure_deployment/azure_version）+ `show_custom_model_input=True` + `base_url_read_only=True` + provider 切换时 model_options 重算。验证 `MODELS_API_COMPATIBLE` 常量成员。先核对 `test_config_panels.py` 已覆盖路径 | `pytest tests/unit/ui/test_config_panels.py -v` 通过；llm_config_panel.py 覆盖率 ≥80%；R9 守卫（_render_message 不含 api_key） | 0.1 | cc:完了 |
| 3.2 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/config_panels/local_model_config_panel.py` 测试: 模块级 — `_select_file`（正常选文件返回 path / 用户取消返回 None / `pick_files` 抛异常 logger.error 不抛出 / `result.files` 为空）+ `_on_verify_click_factory`/`_on_save_click_factory`/`_on_select_file_click_factory` 3 个工厂函数（page 可用/None/RuntimeError 守卫）。组件运行时 — `_setup_file_picker`（page.services 不含 picker append / 已含跳过 / page None 容错）+ `_cleanup_file_picker`（page.services 含 picker remove / 不含跳过 / `LocalModelManager.cancel_verification_if_active()` 调用 / `cancel_verification_if_active` 抛异常 logger.debug）+ `compact=True/False` + `show_save_button` + `show_internal_loading` + `is_gpu_auto=True` 显示 gpu_auto_switch 隐藏 gpu_layers_input + `is_verifying=True` 显示 ProgressRing + `gpu_layers_display` 计算（is_gpu_auto=True→0 / False→state.n_gpu_layers）+ 表单控件 on_change 触发 VM update_*。用 `component_renderer` + FakeLocalModelConfigPanelVM + mock `LocalModelManager`。先核对 `test_config_panels.py` 已覆盖路径 | `pytest tests/unit/ui/test_config_panels.py -v` 通过；local_model_config_panel.py 覆盖率 ≥80%；R7 守卫（LocalModelManager 单例 mock） | 0.1 | cc:完了 |
| 3.3 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/config_panels/tushare_config_panel.py` 测试: 模块级 — `_build_tier_options`（5 档选项 + `TUSHARE_POINT_TIERS` 常量正确性）+ `_on_verify_click_factory`（page 可用/None/RuntimeError 守卫）+ `_on_save_click_factory`（`vm.save()` 同步调用，不通过 run_task）+ `_on_tier_change_factory`（new_tier 空早返回 + page 可用/None/RuntimeError 守卫）+ `_on_register_click`（mock `webbrowser.open_new_tab`）。组件运行时 — `compact=True` 独立布局 / `compact=False` 标准布局 + `show_save_button` + `show_register_link` + `is_verifying=True` 按钮禁用 + `status_text` 空 `status_icon` 隐藏 + `token_input` password 类型 + `can_reveal_password`。先核对 `test_config_panels.py` 已覆盖路径 | `pytest tests/unit/ui/test_config_panels.py -v` 通过；tushare_config_panel.py 覆盖率 ≥80%；R9 守卫（token 不打印） | 0.1 | cc:完了 |
| 3.4 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/config_panels/database_config_panel.py` 测试: 模块级 — `_render_message`（msg=None→"" / 正常翻译 / params 替换）+ `_on_test_click_factory`/`_on_save_click_factory`（page 可用/None/RuntimeError 守卫 + `run_task(vm.test_connection)`/`run_task(vm.save_config)` 调用验证）。组件运行时 — `show_header=True/False` + `compact=True/False` + `show_save_button=True/False` 三 flag 组合 + `status` 不同类型（success/error/warning/info）的图标/颜色映射（`_STATUS_ICON_MAP`/`_STATUS_COLOR_MAP`）+ `db_info` 字段渲染（host/port/database/user）+ 表单控件 on_change 触发 VM update_*。用 `component_renderer` + FakeDatabaseConfigPanelVM。先核对 `test_config_panels.py` 已覆盖路径 | `pytest tests/unit/ui/test_config_panels.py -v` 通过；database_config_panel.py 覆盖率 ≥80% | 0.1 | cc:完了 |
| 3.5 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/backtest/backtest_config_panel.py` 测试: 模块级 — `_get_config_from_state`（stamp_duty_auto=True→None / stamp_duty_auto=False→/1000 / commission/10000 单位转换 / slippage 直接传 / 各字段默认值边界）+ `_make_date_picker`（label/initial_value/on_change 配置正确性）。组件运行时 — `_on_run_click`（调 `_get_config_from_state` + 触发 `on_run_backtest`）+ `_on_stamp_duty_auto_change`（stamp_duty_auto 切换时 stamp_duty_rate 显隐）+ `DatePicker` 选择日期后 start_date/end_date 状态更新 + 9 个表单控件 on_change 触发 set_* + `initial_capital` 空/非数字容错 + `max_positions` 边界（0/负数/超大值）。用 `component_renderer`。先核对 `test_backtest_config_panel.py` 已覆盖路径 | `pytest tests/unit/ui/test_backtest_config_panel.py -v` 通过；backtest_config_panel.py 覆盖率 ≥80% | 0.1 | cc:完了 |

---

## Phase 4: UI 其他组件补测（中优先级，215 miss）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | [lane:gate] [tdd:skip:test-only] 补 `ui/app_layout.py` 组件运行时测试: `_do_tab_switch`（防抖期间二次切换取消首个任务 / 防抖完成后真正切换 tab / R2 CancelledError raise）+ `_on_nav_change`（page None 容错 / page.run_task 调用 / `selected == int(current_tab)` 早返回）+ `_toggle_nav`（nav_collapsed 状态切换）+ `_setup_resize`（page.on_resize 注册 / page None 早返回）+ resize 防抖（多次 resize 取消旧任务 / `debounce_task_ref` 取消 + 置 None）+ `_cleanup_resize`（取消 pending debounce_task + 置 None / page.on_resize 置 None）。用 `component_renderer` + `attach_fake_page`。先核对 `test_app_layout_contract.py` 已覆盖路径（契约守护已较完整，本任务聚焦行为覆盖） | `pytest tests/unit/ui/test_app_layout_contract.py -v` 通过；app_layout.py 覆盖率 ≥80%；R2 守卫；R16 守卫（_on_nav_change 用 page.run_task） | 0.1 | cc:完了 |
| 4.2 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/toast_manager.py` 组件运行时测试: `ToastCard` — `setup`（page None 早返回 / page.run_task(_run_timer) 启动 timer / `_register_task` 调用）+ `_run_timer`（hover 暂停倒计时 / expand 暂停 / 倒计时完成 → set_is_dismissing(True) → on_dismiss / R2 CancelledError raise / 其他异常 logger.debug）+ `cleanup`（task None 早返回 / task.done() 跳过 cancel / `gather_for_shutdown_cleanup` 调用）+ `_on_hover`（e.data=="true" 切换 is_hovered）+ `_on_dismiss_click`（is_dismissing=True 早返回 / 否则 set_is_dismissing(True) + on_dismiss）+ `is_long_text`（>80 字符显示展开按钮 / ≤80 不显示）+ `is_expanded`（切换 max_lines / expand_icon / expand_tooltip）。`ToastManagerView` — 渲染空 state / 渲染多个 toast / `_on_dismiss` 移除指定 toast。补 `_resolve_color_icon`（4 种 type / 未知 type fallback info）。用 `component_renderer` + `_reset_state_for_test` fixture。先核对 `test_toast_manager.py` 已覆盖路径（契约守护 + R2 守卫已完整，本任务聚焦 ToastCard 运行时） | `pytest tests/unit/ui/test_toast_manager.py -v` 通过；toast_manager.py 覆盖率 ≥80%；R2 守卫；R7 守卫（_reset_state_for_test 调用验证） | 0.1 | cc:完了 |
| 4.3 | [lane:gate] [tdd:skip:test-only] 补 `ui/components/health_report_dialog.py` 测试: 模块级纯函数 — `_build_metric_tile`（sub_text None / 非空）+ `_build_section_header`（i18n key 渲染）+ `_build_depth_breadth_items`（depth_ratio None / breadth_ratio None / 两者都 None / 阈值边界）+ `_create_coverage_row`（global 类型 / stock 类型 / ratio 阈值边界 `>=EXCELLENT`/`>=COVERAGE`/<COVERAGE / fresh_ratio 渲染 / covered 计数徽标 / health_global_no_data 文案）+ `_build_coverage_detail_table`（仅 global / 仅 stock / 混合 / 不在 HEALTH_REPORT_ORDER 中的表 / 排序正确性）+ `_build_scan_result`（score 阈值 >80/>50/其他 / avg_fundamental 阈值 / fin_recency_ok True/False / tier 渲染）+ `_build_scan_content`（scan_state="done"+result / "idle" / "scanning" / "error"）+ `_scan_dialog_size`（无 page / 大窗口 / 小窗口）。组件运行时 — `HealthScanDialog` `_start_scan_effect`（启动扫描 / `data_processor=None` 错误状态 / `run_quality_scan` 抛异常 → set_scan_state("error") / R2 CancelledError raise）+ `on_progress` 跨线程回调（`run_coroutine_threadsafe` 调度回主 loop）+ `_cleanup_scan`（取消 pending futures / R2 兼容不重新抛出）。用 `component_renderer` + mock `data_processor.run_quality_scan`。先核对 `test_health_report_dialog.py`/`test_health_report_dialog_contract.py` 已覆盖路径 | `pytest tests/unit/ui/test_health_report_dialog.py tests/unit/ui/test_health_report_dialog_contract.py -v` 通过；health_report_dialog.py 覆盖率 ≥80%；R2 守卫；R11 守卫（_update_progress 跨线程 run_coroutine_threadsafe） | 0.1 | cc:完了 |
| 4.4 | [lane:gate] [tdd:skip:test-only] 补 `ui/views/home_view.py` 异常路径测试: `_on_load_more_click` 异常路径（`vm.load_next_page` 抛异常 → `logger.error` + `DataSanitizer.sanitize_error` 调用验证）+ `_init_and_load` 异常路径（`vm.init` 抛异常不传播 / `vm.init_data` 抛异常不传播）+ `state.market_hot_concepts` 渲染 + `state.market_hsgt` 渲染。先核对 `test_home_view.py` 已覆盖路径（18 契约守护 + 2 R2 守卫 + 12 运行时已较完整，本任务仅补 17 miss） | `pytest tests/unit/ui/test_home_view.py -v` 通过；home_view.py 覆盖率 ≥80%；R2 守卫；R9 守卫（DataSanitizer.sanitize_error） | 0.1 | cc:完了 |

---

## Phase 5: 非 UI 层补测（mock engine 为主，309 miss）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 5.1 | [lane:gate] [tdd:skip:test-only] 补 `app/bootstrap.py` 测试: `_warmup_tushare_capabilities`（warmup 成功 / 网络异常 / 超时）+ `_validate_failover_credentials`（凭据缺失 / 凭据无效 / 多供应商混合）+ `_validate_strategy_tier_coverage`（策略未覆盖 / 全部覆盖 / 部分覆盖）+ `initialize_services` 异步启动路径（`SchedulerService.start` 异常 / `NewsSubscriptionService.start` 异常 / `MarketDataService.start` 异常）+ `CancelledError` 在 `initialize_services` 内部 `gather` 传播路径 + `E2E_TESTING` 短路分支 + `mask_sensitive` 边界（空字符串 / 无敏感字段 / 嵌套字典 / 列表内字典）。用 `_reset_all_singletons` autouse 隔离 `SchedulerService`/`TaskManager`/`CacheManager`/`AIService`/`TushareClient`/`MarketDataService`/`NewsSubscriptionService`（R7）+ mock `TushareClient`/`AIService` 外部 API。先核对 `test_bootstrap.py` 已覆盖路径（TestMaskSensitive/TestCheckOnboardingNeeded/TestInitializeServices/TestMaybeAutoProbeOnStartup 已有） | `pytest tests/unit/test_bootstrap.py -v` 通过；bootstrap.py 覆盖率 ≥80%；R2 守卫；R7 守卫；R9 守卫（mask_sensitive 脱敏 Token/API Key） | 0.1 | cc:完了 |
| 5.2 | [lane:gate] [tdd:skip:test-only] 补 `utils/security_utils.py` 测试: `_get_machine_fingerprint`（mock 不同平台 Windows/Linux/Mac / 失败 fallback）+ `_derive_key_from_machine`（salt + fingerprint 组合 / PBKDF2 迭代次数）+ `_hide_file_windows`（Windows `SetFileAttributes` 调用 / 非 Windows 跳过 / 调用失败容错）+ `SecurityManager.get_key`（并发调用时 `_key_lock` 保护 / 多次调用缓存）+ `_get_key_inner`（KEY_FILE 不存在但 KEY_FILE_BAK 存在 → 从备份恢复 / 两者都存在但内容一致 / 两者内容不一致）+ `migrate_to_derived_key`（已有 legacy 数据时迁移 / 无 legacy 数据时跳过 / 迁移失败回滚）+ `_get_or_create_salt`（`_MACHINE_SALT_FILE` 存在读取 / 不存在创建 / 文件损坏重新创建）+ `_load_key_file`（文件不存在 / 文件存在但损坏 / 文件存在且有效）+ `_save_key`（保存到 KEY_FILE / 同步备份到 KEY_FILE_BAK / 保存失败抛出）+ `_copy_file`（源文件不存在 / 目标目录不存在 / 复制失败）+ `encrypt_data`/`decrypt_data`（data=None / data="" / 大数据分块 / 密钥不匹配 / 数据损坏 / 加密失败抛 EncryptionError / 解密失败抛 DecryptionError）往返测试 + `has_legacy_encrypted_data`（`_LEGACY_MARKER` 存在 / 不存在 / 文件读取异常）+ `_ensure_legacy_marker`（创建 marker / 已存在跳过 / 创建失败）。用 `tmp_path` fixture + mock `keyring`（全局已 mock）+ mock `_get_machine_fingerprint` 返回稳定值 + `SecurityManager` 类级 `_key` 缓存测试间重置。先核对 `test_security_utils.py` 已覆盖路径（18 测试类已有） | `pytest tests/unit/utils/test_security_utils.py -v` 通过；security_utils.py 覆盖率 ≥80%；R9/R10 守卫（密钥不日志打印 / 不硬编码 / KEY_FILE 路径从配置读取） | 0.1 | cc:完了 |
| 5.3 | [lane:gate] [tdd:skip:test-only] 补 `data/sync/financial.py` 测试（mock engine 为主）: `_run_full_sync`（peak 披露季分批大小调整 / 进度回调频率边界 / 中途部分股票失败继续）+ `_run_incremental_sync`（增量边界日期与全量切换 / 增量无新数据早返回）+ `_fetch_comprehensive_financial_data`（单表降级时仍返回其余表 / 全部表降级返回空）+ `_sync_corporate_actions_by_date`（日期范围跨年 / 多公司同日 / 无新数据）+ `repair_financial_data`（最新一季度数据缺失修复 / 历史数据修复）+ `_dedup_financial_df`（ann_date 缺失场景 / update_flag 全为 None）+ `gather_return_exceptions_propagating_cancel`（部分任务失败 + 部分被取消的组合）。用 mock DB engine + mock `TushareClient` 返回 Polars DataFrame + `_reset_all_singletons` 隔离 `CacheManager`/`TushareClient`（R7）+ `EngineDisposedError` mock 验证 R5 传播。仅对 `_dedup_financial_df` 去重逻辑补 1-2 个集成测试（用 `mvd_data` fixture + `override_db_url`）。先核对 `test_financial_sync.py` 已覆盖路径（19 测试类已有） | `pytest tests/unit/test_financial_sync.py -v` 通过；financial.py 覆盖率 ≥80%；R2 守卫；R5 守卫（EngineDisposedError raise）；R7 守卫；R11 守卫（_counter_lock loop-local） | 0.1 | cc:完了 |
| 5.4 | [lane:gate] [tdd:skip:test-only] 补 `data/sync/holder.py` 测试（mock engine 为主）: `_sync_one_table` `consecutive_errors` 达到 `_MAX_ERRORS=5` 后 abort 路径 + `_sync_top10_holders`（checkpoint resume 中断后继续 / 跨多季度数据合并 / 大批量分块边界）+ `_sync_pledge_stat`（数据空 / 部分公司无质押）+ `_sync_share_float`（解禁数据为空 / 多次解禁同一股票）+ `_sync_stk_holdertrade`（大股东增减持多记录 / 异常日期）+ `_get_recent_quarter_ends`（跨年边界 / 当前季度末未来日期）+ `rate_limit_hits` 触发重试逻辑 + `_log_sync_error` 不同错误分类对应的日志级别。用 mock DB engine + mock `TushareClient` 返回不同响应（空/部分/全量）+ `_reset_all_singletons` 隔离 `CacheManager`/`TushareClient`（R7）+ `EngineDisposedError` mock 验证 R5 传播。仅对 `_save_top10_checkpoint` resume 逻辑补 1-2 个集成测试（用 `mvd_data` fixture + `override_db_url`，DAO 断言 `sort_values("end_date", ascending=True)`）。验证 `_get_existing_top10_ts_codes` 用 `DISTINCT ON (ts_code, end_date)`。先核对 `test_holder_sync.py` 已覆盖路径（22 测试类已有） | `pytest tests/unit/test_holder_sync.py -v` 通过；holder.py 覆盖率 ≥80%；R2 守卫；R5 守卫；R7 守卫；长任务 cancel_event 2 秒检查一次（项目内存约束） | 0.1 | cc:完了 |

---

## Phase 6: 覆盖率验证与 PR closeout（lane:release）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 6.1 | [lane:gate] [tdd:skip:test-only] 运行完整覆盖率检查: `python -m pytest tests/ --cov --cov-report=term-missing --cov-report=json` + `python scripts/check_per_file_coverage.py`。验证所有 18 个源文件覆盖率 ≥80% 且整体 ≥85%。若有未达标文件，回填测试直到达标 | `coverage.json` 生成；`check_per_file_coverage.py` 退出码 0；`fail_under=85` 通过 | 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 5.4 | cc:TODO |
| 6.2 | [lane:gate] [tdd:skip:test-only] 运行完整门禁: `ruff check .` → `ruff format --check .` → `pyright` → `python -m pytest tests/unit/ -v -m "not slow"` → `pre-commit run --all-files`。修复 lint/type 错误（仅测试代码） | 所有命令退出码 0；无新增 pyright error；无裸 `# type: ignore` | 6.1 | cc:TODO |
| 6.3 | [lane:release] [tdd:skip:test-only] PR closeout: 整理 evidence pack（基线覆盖率 vs 最终覆盖率对比表 + 18 文件覆盖率提升明细）→ 填写 PR body（遵循项目 PR 模板）→ `git push origin test/coverage-boost` → `gh pr create`。PR body 包含: 背景/变更范围/覆盖率对比/测试范式说明/R2/R7/R9 守卫验证/无源码变更声明 | PR 创建成功；CI 全绿；PR body 含覆盖率对比表 | 6.2 | cc:TODO |

---

## 事前確認

- 事項: `git worktree add` 创建隔离工作区
  理由: R18 红线要求跨多文件修改任务在 worktree 中隔离，避免污染主工作区
  scope: Phase 0 / Task 0.1

- 事項: `git push origin test/coverage-boost` 推送补测分支到远程
  理由: PR closeout 需要远程分支触发 CI 验证
  scope: Phase 6 / Task 6.3

- 事項: `gh pr create` 创建 Pull Request
  理由: 通过 PR 流程合并补测代码，触发完整 CI 门禁验证
  scope: Phase 6 / Task 6.3

注: 本次补测不涉及 secret-read（全部 mock keyring/litellm/TushareClient）、不涉及 destructive 操作（无 rm -rf / 无 migration / 无 force push）、不涉及真实外部 API 调用（全部 mock）。

---

## 验收标准

1. 所有 18 个低覆盖率源文件覆盖率 ≥80%（由 `scripts/check_per_file_coverage.py` 强制）
2. 整体覆盖率 ≥85%（由 `pyproject.toml` `fail_under=85` 强制）
3. 所有新增测试通过 `pytest tests/unit/ -v -m "not slow"`
4. `ruff check .` + `ruff format --check .` + `pyright` + `pre-commit run --all-files` 全部通过
5. 无源码变更（仅新增/补充测试文件）
6. R2 `CancelledError` raise 守卫覆盖所有 async handler
7. R7 单例隔离守卫（`_reset_all_singletons` autouse）
8. R9/R10 守卫（不打印/硬编码敏感信息）
9. R16 守卫（UI 同步 handler 用 `page.run_task` 调度）
10. 无 flaky 测试（不依赖真实 sleep/网络/DB）

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| `component_renderer` async handler 测试可能 flaky | 遵循 `test_home_view.py::TestHomeViewRuntime` 范本；用 `asyncio.sleep(0)` 让出控制权而非真实 sleep；mock `page.run_task` 让 handler 同步执行 |
| FakeVM 与真实 VM 行为偏差 | FakeVM 仅模拟必要接口（state snapshot + commands）；参考 `test_home_view.py::_FakeHomeViewModel` 范本；不模拟内部实现细节 |
| 过度测试导致维护负担 | 每 handler 1-3 个测试（成功/异常/边界）；不为每条分支写独立测试；用参数化测试合并相似场景 |
| 现有测试已覆盖部分路径导致重复 | 每个 task 实现前先核对现有测试文件已覆盖路径（DoD 中已标注"先核对"） |
| `data/sync/financial.py`/`holder.py` mock engine 路径与真实 DB 行为偏差 | 关键 DB 写入路径（去重/checkpoint resume）补 1-2 个集成测试验证（用 `mvd_data` fixture） |
| 测试发现源码 bug | 不在本次任务中修复源码（仅新增测试）；发现的 bug 记录为独立 issue 延后处理；测试中用 `pytest.xfail` 或 skip 标注，并在 PR body 中列出 |

---

## 新しいセッションの起動コマンド: `claude`
## 起動後の最初の入力: `/harness-work 1.1`
## 向いている場面: Phase 0 已完成（worktree + 基线快照），Phase 1 (1.1/1.2/1.3) が次の依存元、逐次または並列で進められるため
