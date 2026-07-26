# P3-WinE2E-Skip 上游修复调研报告

> **调研时间**: 2026-07-25
> **调研者**: Harness (harness-plan)
> **关联债务**: P3-WinE2E-Skip（`docs/debt/known-technical-debt.md` 第 23 行）
> **关联 Phase**: `Plans.md` Phase 2 Task 2.2
> **调研目的**: 确认 8 个 Windows skipif 用例的根因（CanvasKit 渲染 / snackbar 时序 / select_dropdown 性能 / 向导状态隔离）是否在上游（Flet / Playwright / Flutter）已修复，作为 Phase 2 Windows 实机复验（Task 2.5-2.7）的决策依据

---

## 1. 调研背景

技术债 P3-WinE2E-Skip 登记了 8 个 Windows 平台 E2E skipif 用例（4 文件），根因涉及：

- Flet/Playwright CanvasKit textbox 渲染问题（Windows 平台）
- Playwright snackbar 时序问题
- Flet select_dropdown 性能问题（30+ 分钟耗时）
- 向导状态隔离问题

债务表原登记 2 用例 + Flet 0.86.0，实际代码 8 用例 + Flet 0.86.2（`pyproject.toml` 锁定）。Task 2.1 已修正债务表事实性错误。本报告归档上游修复调研结论，确认是否进入 Windows 实机复验（Task 2.5-2.7）。

---

## 2. 调研方法

| 证据源 | 检索方式 | 关键词 | 检索时间 |
|--------|----------|--------|----------|
| Flet 版本元数据 | `pyproject.toml` + Flet release notes | `flet==0.86.2` / `engineRevision` | 2026-07-25 |
| Flet GitHub issue tracker | https://github.com/flet-dev/flet/issues 搜索 | `CanvasKit textbox Windows` / `textbox rendering Windows` | 2026-07-25 |
| Playwright release notes | https://github.com/microsoft/playwright/releases | `1.60 snackbar timing` | 2026-07-25 |
| Flutter CanvasKit A11y | Flutter release notes + Flutter issue tracker | `CanvasKit accessibility semantic tree` | 2026-07-25 |

---

## 3. 调研结论

### 3.1 Flet 0.86.0 → 0.86.2 升级未触及 CanvasKit 本身

**证据**:

- `pyproject.toml` 锁定 `flet==0.86.2` / `flet-desktop==0.86.2` / `flet-charts==0.86.2`，对应 Flutter 3.44.7
- Flet 0.86.0 → 0.86.2 升级未改变 `engineRevision`（仍为 `a10d8ac38de835021c8d2f920dbf50a920ccc030`）
- `engineRevision` 是 Flutter engine 的 commit hash，CanvasKit 作为 Flutter Web 渲染器随 engine 分发
- `engineRevision` 不变 → CanvasKit 二进制不变 → CanvasKit 行为预期不变

**结论**: Flet 0.86.2 下的 CanvasKit 行为与 0.86.0 相同，textbox 渲染问题预期仍存在。

### 3.2 Flet GitHub issue tracker 无 CanvasKit Windows textbox 相关 issue

**证据**:

- 搜索 `CanvasKit textbox Windows`：0 结果
- 搜索 `textbox rendering Windows`：0 结果
- 搜索 `Windows Playwright textbox`：0 结果

**结论**: 上游社区未报告与本项目相同的 CanvasKit textbox 渲染问题。可能原因：

- 该问题仅在 Flet + Playwright E2E 测试场景下触发（非典型 Flet 使用方式）
- Flet 用户中通过 Playwright 跑 E2E 的比例极低
- 问题被归类为 Playwright/Flutter 上游而非 Flet 本身

### 3.3 Playwright 1.60（2026-05）无 snackbar 时序修复

**证据**:

- Playwright 1.60 release notes（2026-05 发布）：无 snackbar / toast / notification 时序相关修复
- Playwright issue tracker 搜索 `snackbar timing`：无 1.60 范围内的修复

**结论**: Playwright snackbar 时序问题（#8 `test_settings_log_level_switch` 根因之一）上游未修复。

### 3.4 Flutter 3.44.7 无 CanvasKit A11y 语义树修复

**证据**:

- Flutter 3.44.7 release notes：无 CanvasKit accessibility / semantic tree 相关修复
- Flutter issue tracker 搜索 `CanvasKit accessibility semantic tree Windows`：无 3.44.7 范围内的修复

**结论**: CanvasKit A11y 语义树问题（Playwright 与 Flet 控件交互的前提）上游未修复。

### 3.5 综合证据表

| 上游组件 | 版本 | 是否修复 | 证据 |
|----------|------|----------|------|
| Flet | 0.86.2 | 否（`engineRevision` 未变） | `engineRevision` `a10d8ac38de835021c8d2f920dbf50a920ccc030` 与 0.86.0 相同 |
| Flutter | 3.44.7 | 否 | 3.44.7 release notes 无 CanvasKit A11y 修复 |
| Playwright | 1.60 | 否 | 1.60 release notes 无 snackbar 时序修复 |
| Flet issue tracker | - | 0 issue | 搜索 `CanvasKit textbox Windows` 0 结果 |

**核心结论**: 上游未修复 8 个 skipif 用例的任何根因。Flet 0.86.2 升级未触及 CanvasKit 二进制，行为预期与 0.86.0 相同。需 Windows 实机复验确认 0.86.2 下是否仍触发不稳定（Phase 2 Task 2.5-2.7）。

---

## 4. 8 用例根因分类

| # | 用例 | 文件 | fixture | 根因 | 上游修复状态 |
|---|------|------|---------|------|--------------|
| 1 | `test_embedded_onboarding_zero_config_first_launch` | `tests/e2e/test_onboarding_embedded.py` | `embedded_wizard_page`（fake sidecar） | CanvasKit textbox 渲染 | 未修复（§3.1） |
| 2 | `test_embedded_wizard_forward_then_back` | `tests/e2e/test_onboarding_embedded.py` | `embedded_wizard_page`（fake sidecar） | CanvasKit textbox 渲染 | 未修复（§3.1） |
| 3 | `test_embedded_db_info_message_displayed` | `tests/e2e/test_onboarding_embedded.py` | `embedded_wizard_page`（fake sidecar） | CanvasKit textbox 渲染 | 未修复（§3.1） |
| 4 | `test_embedded_real_onboarding_zero_config_first_launch` | `tests/e2e/test_onboarding_embedded_real.py` | `embedded_real_wizard_page`（real sidecar） | CanvasKit textbox 渲染 | 未修复（§3.1） |
| 5 | `test_embedded_real_wizard_forward_then_back` | `tests/e2e/test_onboarding_embedded_real.py` | `embedded_real_wizard_page`（real sidecar） | CanvasKit textbox 渲染 | 未修复（§3.1） |
| 6 | `test_embedded_real_db_info_message_displayed` | `tests/e2e/test_onboarding_embedded_real.py` | `embedded_real_wizard_page`（real sidecar） | CanvasKit textbox 渲染 | 未修复（§3.1） |
| 7 | `test_wizard_db_validation_success` | `tests/e2e/test_onboarding_wizard.py` | `wizard_page` | 向导状态隔离 + CanvasKit textbox 渲染 | 未修复（§3.1 + 项目内状态隔离问题） |
| 8 | `test_settings_log_level_switch` | `tests/e2e/test_settings_flow.py` | `e2e_page` | snackbar 时序 + select_dropdown 性能 | 未修复（§3.3 + 项目内 select_dropdown 性能问题） |

### 4.1 根因分布（按用例数去重）

- CanvasKit textbox 渲染：**7 用例**（#1-#7，其中 #7 兼有向导状态隔离）
- snackbar 时序 + select_dropdown 性能：**1 用例**（#8，双根因）

### 4.2 根因分布（按根因实例）

- CanvasKit textbox 渲染：7 实例（#1-#7）
- 向导状态隔离：1 实例（#7）
- snackbar 时序：1 实例（#8）
- select_dropdown 性能：1 实例（#8）
- 总计：10 实例 / 8 用例

> **与 Plans.md Task 2.2 DoD 的差异说明**: Plans.md Task 2.2 DoD 写"CanvasKit 渲染 6 / snackbar 时序 1 / select_dropdown 性能 1"（6+1+1=8），将 #8 拆成 2 个根因实例但 #7 漏算。实际 #7 根因标签为"向导状态隔离 + CanvasKit textbox 渲染"（双根因），应单独列出。本报告按"用例数去重"和"根因实例"两种方式分类，更精确反映 8 用例的真实根因分布。

---

## 5. 多视角评审关键采纳

Phase 2 plan-time 多视角评审（Product / Architecture / Security / QA / Skeptic 5 perspective）对调研结论的关键修正：

- **Product**: 要求 plan 必须明确复验失败的 fallback 策略（保留替代覆盖 + 关联 P3-E2E-Sidecar-Ready-Path）；优先复验 `test_settings_log_level_switch`（根因可能不同，select_dropdown 性能可能已改善）；分阶段复验（fake sidecar 先于 real sidecar）。已采纳。
- **Architecture**: 修正事实性错误（8 用例非 7）；明确切割 P3-E2E-Sidecar-Ready-Path（目标正交）。已采纳。
- **Security**: 修正 secret 名称为 `CI_PG_PASSWORD`（非 `embedded_pg_password`）；真实 sidecar E2E 用 sidecar binary 自管密码，不依赖 `CI_PG_PASSWORD`。已采纳。
- **QA**: 复验方案必须明确"如何实际运行被 skipif 跳过的用例"（`pytest_collection_modifyitems` hook 临时取消 skipif）；复验必须在真实 `windows-latest` runner；8 用例逐个独立判定；复验判定标准量化（连续 N≥3 次成功 + 单用例 ≤10 分钟）。已采纳。
- **Skeptic**: 质疑"全面处理"是否过度工程化；要求先 Windows 最小复验确认问题仍存在再决定范围（YAGNI）；8 用例 × 30 分钟 = 4 小时+ 超过 GitHub Actions 单 job 6 小时上限，必须拆 job。已采纳——本 Phase 范围收窄为"分级复验 + 文档修正"，非"全面重写"。

---

## 6. 复验决策依据

基于本调研结论：

1. 上游未修复任何根因 → 不能直接移除 skipif（需实机证据）
2. Flet 0.86.2 `engineRevision` 未变 → 行为预期与 0.86.0 相同，但需实机确认（预期"仍触发"是大概率）
3. `select_dropdown` 性能可能因 Flet/Playwright 微优化而改善 → 优先复验 #8（Plans.md Task 2.5）
4. fake sidecar 用例不依赖真实 sidecar binary，可能通过率更高 → 先复验 fake sidecar（Plans.md Task 2.6）

---

## 7. 下一步行动

| Task | 内容 | 依据 |
|------|------|------|
| 2.4 | 搭建 Windows 复验 CI job（`windows-skip-revalidation`） | 复验需在真实 `windows-latest` runner 执行 |
| 2.5 | 优先复验 `test_settings_log_level_switch`（#8） | `select_dropdown` 性能可能已改善（根因可能不同） |
| 2.6 | 分级复验 6 个 onboarding 用例（fake sidecar 先于 real sidecar） | fake sidecar 通过率预期更高 |
| 2.7 | 复验 `test_wizard_db_validation_success`（#7） | 双根因（向导状态隔离 + CanvasKit），可能需额外排查 |
| 2.8 | 基于复验结果逐用例决策（移除/保留 skipif） | 8 用例独立判定，非"全有或全无" |

---

## 8. 参考资料

- `docs/debt/known-technical-debt.md` P3-WinE2E-Skip 条目（第 23 行）
- `pyproject.toml`（Flet 版本锁定）
- `Plans.md` Phase 2 Task 2.2 DoD
- Flet release notes: https://github.com/flet-dev/flet/releases
- Playwright release notes: https://github.com/microsoft/playwright/releases
- Flutter release notes: https://docs.flutter.dev/release/release-notes
