# test_settings_log_level_switch Windows CI 卡死根因分析

> **归档时间**：2026-07-25
> **分析对象**：`tests/e2e/test_settings_flow.py::test_settings_log_level_switch`
> **触发场景**：P3-WinE2E-Skip 复验（`--run-windows-skip` 临时取消 skipif markers）
> **CI run**：30138544395（commit af98983，settings-slow matrix group 未运行；历史 run 29736885686 记录 34 分钟卡死）
> **失败模式**：34 分钟卡死超时（worker crash + 主进程 hang）

## 1. 用例代码逻辑

**文件**：`tests/e2e/test_settings_flow.py` L86-126

```python
async def test_settings_log_level_switch(e2e_page):
    await e2e_page.click_text(I18n.get("nav_settings"), timeout_ms=15000)
    await e2e_page.expect_text(I18n.get("settings_title"), timeout_ms=10000)
    await e2e_page.click_text(I18n.get("settings_tab_system"), timeout_ms=8000)
    log_level_label = I18n.get("settings_log_level")
    await e2e_page.expect_text(log_level_label, timeout_ms=10000)
    # 准备 i18n 字符串
    log_level_debug = I18n.get("sys_opt_debug")  # "DEBUG"
    log_level_info = I18n.get("sys_opt_info")  # "INFO"
    log_level_error = I18n.get("sys_opt_error")  # "ERROR"
    snack_prefix = I18n.get("sys_log_label")  # "控制系统日志详细程度"
    try:
        # 先切到 ERROR（确定状态），再切到 DEBUG（目标状态）
        await e2e_page.select_dropdown(log_level_label, log_level_error, timeout_ms=10000)
        await e2e_page.select_dropdown(log_level_label, log_level_debug, timeout_ms=10000)
        # 验证 snackbar 出现
        await e2e_page.expect_text(f"{snack_prefix}: DEBUG", timeout_ms=5000)
    finally:
        try:
            await e2e_page.select_dropdown(log_level_label, log_level_info, timeout_ms=10000)
        except Exception as e:
            logger.warning("[settings_flow] restore log level to INFO failed: %s", e, exc_info=True)
```

**关键特征**：
- 共 3 次 `select_dropdown` 调用（ERROR→DEBUG→INFO），每次 `timeout_ms=10000`
- 1 次 `expect_text` 验证 snackbar，`timeout_ms=5000`
- finally 块吞没异常（仅 warning），不掩盖原始失败

## 2. select_dropdown 方法实现分析

方法位置：`tests/e2e/helpers/flet_page.py:219-538`

### 2.1 触发器定位策略（4 层级 + 暴力搜索）

| 优先级 | 策略 | 机制 |
|--------|------|------|
| 0（最高） | JS 精确定位 | `document.querySelectorAll('flt-semantics[role="button"][aria-expanded]')` + `closest('group')` 的 `aria-label === targetLabel \|\| startsWith(targetLabel)` |
| 1 | group 子串匹配 | `flt-semantics[role="group"][aria-label*="<key>" i]` 内嵌 `flt-semantics[role="button"][aria-expanded]` |
| 2 | 触发器文本匹配 | `flt-semantics[role="button"][aria-expanded]` + `has_text=<key>` |
| 3 | 兜底 | `flt-semantics[role="button"][aria-expanded]` 的 `.first` |
| 兜底扩展 | 旧兼容 | `input[aria-label*=<key>]` / `[role="combobox"]` / `[role="button"][aria-label*=<key>]` / `get_by_text` |
| 暴力搜索 | 最后兜底 | 依次点击每个 `aria-expanded` 触发器，检查弹出的选项是否匹配 |

### 2.2 match_keys 扩展分支（关键差异点）

```python
if "语言" in norm_label or "language" in norm_label or "locale" in norm_label:
    match_keys.extend(["language", "语言", "locale", "简体中文", "english", "chinese", ...])
elif "主题" in norm_label or "theme" in norm_label:
    match_keys.extend(["theme", "主题", "浅色", "深色", "light", "dark", ...])
# ⚠️ 没有 "日志" / "log" 分支！
```

**对于 `log_level_label="日志级别"`**：
- norm_label = "日志级别"
- 不命中任何分支，`match_keys = ["日志级别"]`（仅原值 + lower 去重）

**对于 `lang_label="语言"`**：
- 命中 "语言" 分支，`match_keys` 扩展为 8+ 个变体

### 2.3 选项点击策略（15 个 locator 候选）

`get_option_locators()` 返回 15 个候选 locator，按优先级：
1. `flt-semantics[role="button"]:not([aria-expanded])` + `has_text=option_text`（V1 M3 主策略）
2. `[role="option"][aria-label*=...]`（4 个变体）
3. `[role="menuitem"]`（4 个变体）
4. `[aria-label="..."]`（2 个变体）
5. `[role="button"]:not([aria-expanded])`（4 个变体）

### 2.4 等待机制（无显式总 timeout）

**主轮询循环**：
```python
wait_cycles = max(1, self._tm(timeout_ms) // 200)  # timeout_ms=10000 → 50 cycles
for i in range(wait_cycles):
    if await check_option_visible():
        option_ready = True
        break
    if not initial_visible and i > 0 and i % 10 == 0 and last_clicked_target:
        await last_clicked_target.click(timeout=self._tm(3000), force=True)  # 重试点击触发器
    await self.page.wait_for_timeout(200)
```

**暴力搜索（主轮询失败后）**：
```python
brute_force_deadline_s = time.monotonic() + self._tm(15000) / 1000  # 15s × multiplier
all_triggers = self.page.locator('flt-semantics[role="button"][aria-expanded]')
max_triggers = min(trigger_count, 4)
for idx in range(max_triggers):
    if time.monotonic() > brute_force_deadline_s:
        break
    await trigger.click(timeout=self._tm(3000), force=True)
    for _ in range(4):
        await self.page.wait_for_timeout(300)
        if await check_option_visible():
            option_ready = True
            break
    if option_ready:
        break
    await self.page.keyboard.press("Escape")
    await self.page.wait_for_timeout(400)
```

**关键问题**：暴力搜索的注释明确记录了已知的 worker crash：
> 原 30s deadline 在 CI 高负载下导致 Chromium 渲染线程死锁、xdist worker crash  
> (run 29736885686: worker gw0 down after 暴力搜索遍历 3 个 Dropdown 触发器，  
> 每次点击触发 vm.set_table → DB query → CanvasKit 重渲染 → 资源累积 → crash)

### 2.5 单次 select_dropdown 理论耗时上限

| 阶段 | 耗时（multiplier=1） | 耗时（CI multiplier=2） |
|------|---------------------|----------------------|
| 主轮询 50 cycles × 200ms | 10s | 20s |
| 每 10 cycles 重试点击（5 次 × 3s） | 15s | 30s |
| 暴力搜索 deadline | 15s | 30s |
| **单次理论上限** | ~40s | ~80s |
| **3 次累计** | ~120s（2 分钟） | ~240s（4 分钟） |

**结论**：单纯按代码逻辑计算，3 次 select_dropdown 最多 4 分钟，**无法解释 34 分钟卡死**。卡死必然来自 Playwright/Chromium 层的不可恢复状态。

## 3. 卡死根因清单（按可能性排序）

### 根因 1（最高可能）：暴力搜索引发 Chromium 渲染线程死锁，Playwright 调用永久不返回

**证据链**：
- 代码注释明确记录 run 29736885686 的 worker crash 机制
- `log_level_label="日志级别"` 的 `match_keys` 仅 1 个变体（无 "日志/log" 扩展分支），JS 策略 0 + 策略 1-3 命中率显著低于 `language_switch`
- System tab 上可能存在多个 `aria-expanded` 触发器（语言、日志级别、主题等 dropdown 合并到同一 group），暴力搜索会点击错误触发器
- 每次错误点击触发 CanvasKit 重渲染 + vm.set_table + DB query，资源累积
- Chromium 渲染线程死锁后，`await self.page.wait_for_timeout(300)` 永远不返回
- `check_option_visible()` 内 15 个 locator 的 `.count()` / `.is_visible()` 调用永久阻塞
- 没有 deadline 检查能终止已阻塞的 Playwright 调用

**34 分钟机制**：CI 强制超时终止，而非代码逻辑自然退出。

### 根因 2（高可能）：snackbar 在 CanvasKit 下渲染机制导致 expect_text 15 locator 全部不命中

**机制**：
- snackbar 是 Flet 浮层控件，a11y 语义可能是 `role="alert"` / `role="status"` / `aria-live`
- CanvasKit 下 snackbar 可能：(a) 延迟渲染到语义树；(b) 渲染后 4s 自动关闭；(c) 文本合并到其他 group 的 aria-label
- `expect_text` 的 fallback 轮询只检查 `<input>` 元素，snackbar 不是 input
- 5000ms timeout × multiplier 后抛 TimeoutError

**影响**：单次 5-10s 超时，不直接导致 34 分钟。但若 snackbar 断言失败后 finally 块的 select_dropdown 又触发根因 1，则累计耗时激增。

### 根因 3（中可能）：测试间状态污染（locale 未恢复）

**机制**：
- `test_settings_language_switch` 是 `test_settings_log_level_switch` 的前序测试（按文件顺序）
- language_switch 的 finally 块若恢复失败，flet_app 内存 locale 仍是 en_US
- `_ensure_locale_zh` 安全网在 e2e_page teardown 时检查，但安全网本身依赖 UI 操作（`click_text` + `select_dropdown`），若 UI 已卡死则安全网也卡死
- log_level_switch 的 `I18n.get("nav_settings")` 返回中文 "设置"，但 UI 显示英文 "Settings"
- 所有 `click_text` / `expect_text` 超时

**反证**：`_ensure_locale_zh` 安全网的 `has_text` 检查是轻量级（只检查 count），若失败只 warning 不阻塞。此根因可能但非主因。

### 根因 4（中可能）：flet_app 子进程崩溃但 fixture 未检测

**机制**：
- `app.assert_alive()` 仅在 `_make_page` 开头检查一次
- 测试中途 flet_app 崩溃（OOM、CanvasKit 渲染线程死锁），fixture 不感知
- Playwright 继续与已死页面通信，所有操作 30s 超时
- 3 次 select_dropdown × 多次操作 × 30s = 累计数分钟

### 根因 5（低可能）：check_option_visible 在 Windows 下极慢

**机制**：
- 15 个 locator 候选，每个 `.count()` + `.is_visible()` 在 CanvasKit 下数百毫秒
- 主轮询 50 次 × 15 locator × 数百毫秒 = 数分钟
- 暴力搜索 4 触发器 × 4 wait × 15 locator × 数百毫秒 = 数分钟
- 3 次 select_dropdown 累计 10-30 分钟

**反证**：此根因是"慢"而非"卡死"，通常会在 30 分钟内自然退出。34 分钟超时更可能是根因 1 的永久阻塞。

## 4. 与 test_settings_language_switch 对比

| 维度 | language_switch | log_level_switch |
|------|----------------|------------------|
| **select_dropdown 调用次数** | 2 次（切英文 + 切回中文） | **3 次**（ERROR→DEBUG→INFO） |
| **match_keys 扩展** | 命中 "语言" 分支，扩展为 8+ 变体 | **不命中任何分支，仅 1 个变体**（"日志级别"） |
| **JS 策略 0 命中率** | 高（多变体匹配 group aria-label） | **低**（仅 "日志级别" 精确/前缀匹配） |
| **断言类型** | `expect_text("Screener")` + `expect_text("Theme")`（普通 UI 文本） | **`expect_text("控制系统日志详细程度: DEBUG")`**（snackbar 浮层文本） |
| **snackbar 风险** | 无 snackbar 断言 | **snackbar 在 CanvasKit 下渲染机制不明**，可能不在语义树 |
| **PITFALL 注释** | 显式规避 CanvasKit 含斜杠文本问题 | 无 PITFALL 注释 |
| **finally 块** | `select_dropdown` + 25 次 `has_text` 轮询（轻量级） | **仅 `select_dropdown`**（无轻量级轮询兜底） |
| **i18n 文案** | 简单（"简体中文" / "English"） | 英文单词（"ERROR" / "DEBUG" / "INFO"），CanvasKit 下可能被识别为 button 而非 option |
| **skipif 状态** | 无 skipif，在所有平台运行 | **skipif win32** |

**核心差异**：
1. `log_level_switch` 的 `match_keys` 容错性显著低于 `language_switch`（1 个 vs 8+ 个变体）
2. `log_level_switch` 独有 snackbar 断言，snackbar 在 CanvasKit 下渲染机制是未知风险
3. `log_level_switch` 多 1 次 select_dropdown 调用，触发根因 1 的概率更高

## 5. skipif reason 更新判定

**当前 reason**：
> "Windows Flet/Playwright snackbar 时序问题 + select_dropdown 性能问题导致 30+ 分钟耗时 (P3-WinE2E-Skip)"

**判定**：reason **基本准确但不够精确**，建议更新为：

> "Windows Flet/Playwright CanvasKit 下 select_dropdown 暴力搜索引发 Chromium 渲染线程死锁（match_keys 缺少 '日志/log' 扩展分支致触发器定位失败）+ snackbar 浮层文本在 CanvasKit 下渲染机制不明致 expect_text 不命中，单次测试 30+ 分钟耗时 (P3-WinE2E-Skip)"

**更新依据**：
- "snackbar 时序问题" → 更精确为 "snackbar 浮层文本在 CanvasKit 下渲染机制不明"
- "select_dropdown 性能问题" → 更精确为 "暴力搜索引发 Chromium 渲染线程死锁" + 根因（match_keys 缺少扩展分支）
- 保留 "30+ 分钟耗时" 准确描述

## 6. 建议的修复方案

### 方案 A（推荐：保持 skipif，记录改进点）

**理由**：
- 债表 P3-WinE2E-Skip 已明确"复验方案：Windows 实机分级复验，8 用例逐个独立判定"
- 替代覆盖已存在：`tests/unit/ui/views/settings_tabs/test_system_tab.py:953` 起 `TestDoLogLevelChange` 覆盖 `_do_log_level_change` 成功/异常/CancelledError + state 验证
- 强行修复 select_dropdown 在 Windows 下的稳定性 scope 远超技术债处理边界

**action**：仅更新 skipif reason 文本（见第 5 节），不动代码。

### 方案 B（缓解性修复：为 log_level_label 添加 match_keys 扩展）

在 `select_dropdown` 中添加 "日志/log" 分支：

```python
elif "日志" in norm_label or "log" in norm_label:
    match_keys.extend([
        "log", "日志", "log level", "日志级别",
        "debug", "info", "warning", "error",
        "DEBUG", "INFO", "WARNING", "ERROR",
    ])
```

**收益**：提升 JS 策略 0 + 策略 1-3 的命中率，降低进入暴力搜索的概率。

**局限**：不解决 snackbar 断言问题，不解决 Chromium 渲染线程死锁的根本问题。仅作为未来复验时的改进点。

### 方案 C（根本性修复：替换 snackbar 断言）

不验证 snackbar 文本，改为验证更稳定的信号：
- 验证 dropdown 当前选中值（通过 `flt-semantics[role="button"][aria-expanded]` 的文本内容）
- 或通过 flet_app 暴露的状态检查接口验证 logger.level

**收益**：消除 snackbar 在 CanvasKit 下渲染不明的风险。

**局限**：需要修改被测代码（暴露状态检查接口）或调整断言策略，scope 较大。

### 方案 D（防御性修复：为 select_dropdown 添加硬性总 deadline）

```python
async def select_dropdown(self, current_or_label, option_text, timeout_ms=TIMEOUTS.INTERACTION):
    overall_deadline = time.monotonic() + self._tm(timeout_ms) / 1000 + self._tm(15000) / 1000
    # 所有循环检查 overall_deadline
    if time.monotonic() > overall_deadline:
        raise RuntimeError(f"select_dropdown overall deadline exceeded for '{option_text}'")
```

**收益**：防止暴力搜索 + Playwright 阻塞导致的永久卡死，fail-fast 而非 34 分钟超时。

**局限**：若 Playwright 调用本身永久阻塞（根因 1），deadline 检查无法执行。

### 综合建议

**短期**：方案 A（保持 skipif + 更新 reason），与债表 P3-WinE2E-Skip 的复验计划对齐。

**中期（Windows 复验时）**：方案 B + 方案 D，提升 select_dropdown 的健壮性。

**长期（若复验仍失败）**：方案 C，从根本上消除对 snackbar 渲染的依赖。

## 7. 关键文件路径

- 测试文件：`tests/e2e/test_settings_flow.py`
- conftest：`tests/e2e/conftest.py`
- FletPage：`tests/e2e/helpers/flet_page.py`
- 超时常量：`tests/e2e/timeouts.py`
- Windows skip 策略：`tests/e2e/_windows_skip.py`
- 技术债：`docs/debt/known-technical-debt.md`（P3-WinE2E-Skip 条目）
- 上游调研：`docs/debt/win-e2e-skip-revalidation/2026-07-25-upstream-research.md`
