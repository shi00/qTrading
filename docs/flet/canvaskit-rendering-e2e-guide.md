# Flutter Web CanvasKit 渲染行为与 E2E 定位指南

> 文档入口：[Flet 开发文档入口](./README.md)
> **核心地位**：记录本项目绑定的 Flutter Web CanvasKit 渲染引擎特性、HTML 语义树生成规则、版本依赖锁定机制以及 E2E 测试定位与交互坑点。
> **适用版本**：Flet V1（版本以 [`pyproject.toml`](../../pyproject.toml) 锁定为准；Flutter Web Engine Revision 从 `site-packages/flet_web/web/flutter_bootstrap.js` 的 `_flutter.buildConfig.engineRevision` 读取，查询命令见 [upgrade-checklist.md §3.4](./upgrade-checklist.md#34-canvaskit-版本验证)）。
> **相关文档**：
> - [Flet V1 API 关键约束](./v1-api-constraints.md)
> - [项目 Flet 差异与高风险 API](./project-differences.md)
> - [Flet 升级检查清单](./upgrade-checklist.md)
> - [测试规范与指南](../guides/testing.md)

---

## 1. CanvasKit 引擎版本锁定与资源拦截

### 1.1 引擎版本与 `engineRevision` 依赖
Flet Web 依赖 Flutter Web Engine 运行时。Flet 版本由 [`pyproject.toml`](../../pyproject.toml) 锁定（`flet` / `flet-desktop` / `flet-charts` / `flet-code-editor` 四包，版本见该文件），`flet-web` 作为 `flet` 的 transitive dependency 同版本发布。
- **动态加载机制**：Flet Web 启动时，`flutter_bootstrap.js` 会从谷歌 CDN 动态下载 CanvasKit 二进制：
  `https://www.gstatic.com/flutter-canvaskit/<engineRevision>/chromium/canvaskit.wasm`
- **本地 Mock 缓存**：E2E 测试通过 `tests/e2e/conftest.py::_setup_canvaskit_intercept` 离线化拦截。`mock_assets/canvaskit/` 保存了匹配 `engineRevision` 的 `canvaskit.js` / `canvaskit.wasm` 和 CJK 字体分片。
- **升级注意事项**：升级 Flet 版本时，若 `engineRevision` 发生变化，必须从 `site-packages/flet_web/web/canvaskit/` 重新复制对应 WASM / JS 文件，并同步更新 `tests/e2e/_font_urls.py`（见 [Flet 升级检查清单](./upgrade-checklist.md)）。

---

## 2. `Semantics.identifier` → `flt-semantics-identifier` 精确定位机制

CanvasKit 并非渲染 HTML 原生控件，而是在 `<canvas>` 上绘图，并在上层维护一层 HTML 语义 DOM 树（`<flt-semantics>`）供无障碍辅助技术和 Web 自动化定位。

### 2.1 单一通道：`identifier` 属性

E2E 锚点包装器 `anchored()`（`ui/testing/anchor.py`）在 E2E 模式（`E2E_TESTING=true`）下注入 `ft.Semantics(container=True, label=EID, identifier=EID)`。其中 `identifier=EID` 是**唯一**定位通道：CanvasKit web 将其落为 DOM 属性 `flt-semantics-identifier`。

```
                      ┌───────────────────────────────────────────────┐
                      │ ft.Semantics(container=True,                  │
                      │              label=EID, identifier=EID)        │
                      └───────────────────────┬───────────────────────┘
                                              │
                                              ▼
                        DOM 生成带 identifier 属性的语义节点：
            <flt-semantics flt-semantics-identifier="<EID>" ...>
                      （四类 AnchorKind 共用同一节点形态）
```

- **选择器**：`AnchorPage` 一律用
  `flt-semantics[flt-semantics-identifier="<EID>"]` 精确定位（`_locator_by_identifier`）：
  无 kind 分派、无前缀/后缀匹配、无 `role` 过滤，也无启发式回退。
- **节点存在性与 `AnchorKind` 无关**（PoC P0-2 矩阵）：Button 系列 / Text / Dropdown 顶层 /
  ListView 行 / Dialog 内节点均在同一节点形态上暴露 `identifier`，故四类 kind 共用同一选择器。
- **`label=EID` 仍然注入**：作为无障碍 label 通道保留（供
  `tests/e2e/test_screener_anchor_smoke.py` 的 `container=True` 独立性守护断言使用），
  但**不再是定位通道**。

### 2.2 四类 AnchorKind 的差异（仅影响 bbox 解析）

四类 `AnchorKind` 共用同一 identifier 选择器，差异只体现在「取哪个 bbox 做交互」：

| AnchorKind | bbox 解析 | 说明 |
| :--- | :--- | :--- |
| `INTERACTIVE` | identifier 节点自身 bbox | 节点自身即交互面 |
| `COMPLEX` | identifier 节点自身 bbox | identifier 路径下与 INTERACTIVE 行为一致 |
| `INPUT` | identifier 节点**后代** `input` / `textarea` bbox | 节点 bbox（如 200×48）与真实 `input` bbox（如 208×54）不一致，需下潜取真实输入面 |
| `LABEL` | identifier 节点自身 bbox | 纯展示，`click` / `scroll_into_view` 显式拒绝 |

- **INPUT 需下潜**：`ft.TextField` 的 identifier 节点 bbox 含边框/内边距，与真实输入面不一致。
- **LABEL 为 display-only**：`click` / `scroll_into_view` 会显式抛 `RuntimeError`；断言用
  `expect_visible` / `click_label`（后者依赖事件冒泡到可点击父容器）。
- **选项面板无 identifier 覆盖**：Dropdown 打开后的选项节点由 Flet 动态生成、无 anchor
  覆盖，仍需文本匹配（`_find_option_element`：候选组优先级 + 匹配优先级）。
- **ListView 视口外行需先滚入**：视口外的行尚未进入语义树构建窗口，identifier 节点可能
  count=0 / 无 bbox；须先经 `scroll_into_view` 滚入视口再定位点击。

### 2.3 P0-1 PoC 证据（identifier 通道落地结论）

PoC 在锁定 Flet 版本 + CanvasKit 上实测 `Semantics(identifier=…)` 的 web DOM 行为，结论：

- **属性名确认**：`Semantics.identifier` 在 CanvasKit web 上落为 DOM 属性
  `flt-semantics-identifier`（Android 为 `resource-id`、iOS 为 `accessibilityIdentifier`；
  本项目只依赖 web 形态）。
- **`container=True` 非必需**：identifier 属性的生成不依赖 `container=True`；项目仍保留
  `container=True`，以便 `label` 不被父容器合并（供 `container=True` 独立性守护断言使用）。
- **bbox 与控件一致**：identifier 节点 bbox 与真实控件/可点击节点一致（Text 类节点自身即
  交互面，`pointer-events: auto`），故可直接取节点 bbox 中心做物理点击。
- **未与祖先节点合并**：identifier 节点未被父/祖先语义节点合并，`count()` 全局唯一
  （四类 kind 同形；offstage 控件为预期排除项，见坑点 8）。

> 说明：本节不登记具体补丁版本号，Flet 版本以 [`pyproject.toml`](../../pyproject.toml) 锁定为准；
> 引擎 revision 与资源层校验见 [Flet 升级检查清单 §3.4](./upgrade-checklist.md#34-canvaskit-版本验证)。

---

## 3. E2E 测试定位与交互坑点及防护规程

### 坑点 1（历史说明）：`textContent` 严格全匹配曾导致 LABEL 定位失败（PR 479 修复）
> **已随 identifier 路径废弃**，保留作历史记录与迁移参照。
- **根因（历史）**：早期 legacy 定位路径按 `textContent` 前缀匹配 EID。`LABEL` 锚点包裹
  `ft.Text` 时，合并节点 `textContent` 为 `"EID\n显示文本"`，若判定逻辑要求
  `(textContent).trim() === EID` 则全匹配恒为 `false`，导致超时失败；当年修复为允许
  `\n` 换行边界（`t === label || t.startsWith(label + '\n')`）。
- **现状**：定位已改为 identifier 精确选择器（§2），不再有 `textContent` 前缀匹配，
  该问题不再适用于 anchor 定位。仍按文本匹配的只有 Dropdown 选项面板（坑点 3），
  其匹配入口 `_find_option_element` 自带匹配优先级，不受本坑点影响。

### 坑点 2：Playwright DOM 合成 click 事件失效
- **根因**：CanvasKit 的 `<flt-semantics>` 节点不响应 Playwright `locator.click()` 合成事件或 `element.dispatchEvent(...)`。
- **规程**：一律获取节点 bounding box，使用真实物理鼠标坐标点击：
  ```python
  box = await self._identifier_box(eid)  # identifier 节点自身 bbox（INPUT 下潜后代 input）
  await self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
  ```

### 坑点 3：Dropdown 下拉框残留状态与 Actionability 检查不稳定（PR 478 修复）
- **根因**：
  1. 选项面板由 Flet 动态生成，不在初始 anchor 覆盖范围内；选项 `flt-semantics` 节点上的 Playwright actionability check 容易因 CanvasKit 帧重绘失败。
  2. 若前一次点击留下了 `aria-expanded="true"`，再次点击 Dropdown 会被 Material 3 识别为"关闭"而非"展开"。
- **规程**（`AnchorPage.select_option` 实施避坑 4 要素）：
  1. **展开前清场**：若已有 `aria-expanded="true"`，先按 `Escape` 键收合。
  2. **展开校验**：点击 Dropdown 后，显式 poll `aria-expanded="true"`，未展开立即抛错，避免盲目等待 20s。
  3. **选项提取**：在 JavaScript DOM 侧按精确文本 / 别名格式（如 `key (alias)`） / 换行前缀提取 ElementHandle。
  4. **强力物理点击**：使用 `option_element.click(force=True)` 绕开 actionability 检查。

### 坑点 4：Python 字符串转义在 `page.evaluate` 中破坏 JS 语法
- **根因**：在 `page.evaluate("""... label + '\\n' ...""")` 中使用普通 Python 三引号字符串时，`\\n` 会被 Python 解释为实际换行符 `\n`。传递给 Chrome V8 后，JS 代码在单引号字符串内出现换行，抛出 `SyntaxError: Invalid or unexpected token`。
- **规程**：所有包含 `\n` 转义的 JS 评估字符串必须使用 Python 原始字符串（Raw String）：
  ```python
  await self.page.evaluate(r"""(args) => { ... t.startsWith(label + '\n'); ... }""")
  ```

### 坑点 5：Windows 上多 Worker 并行 (`pytest-xdist`) 资源与进程冲突
- **根因**：Windows 上并行 4-8 个 xdist worker 会同时试图 `pip install flet-web` 到 site-packages 产生 WinError 32 锁死，或者同时启动 8 个 Chrome + Flet 服务导致 CPU/内存与端口抢占 crash (`node down: Not properly terminated`)。
- **规程**：在 CI 或本地 Windows 环境下运行 E2E 测试时，清空 addopts 禁用并行：
  ```bash
  pytest tests/e2e/ -o addopts="" -p no:xdist -p no:randomly
  ```

### 坑点 6：CI 高负载下交互时序脆弱 → 步骤级"确认触发 + 重试"（PR 500 修复）
- **根因**：CI 共享 runner 高负载/性能波动 → headless Chromium 无 GPU、CanvasKit 走 SwiftShader 软件渲染 → 帧率不稳（日志 `GL Driver Message (...) GPU stall due to ReadPixels`）→ 偶发时刻物理鼠标点击落在渲染中间帧被吞（交互未触发），或 `<flt-semantics>` 更新延迟导致断言超时。锚点（anchor）只能解决"定位"，不能解决"CI 时序抖动吞事件"，故需在交互层做健壮化。
- **规程**：步骤级"确认触发 + N=3 重试"（间隔 500ms），以可靠"确认指标"判断交互是否真正触发，确认不到才重试，3 次仍失败抛真错误（不静默吞）。通用实现 `tests/e2e/helpers/anchor_page.py::retry_until_triggered(interact, confirm, attempts, interval_ms)`；`confirm` 为可注入 async 谓词，便于单测注入模拟"前 N-1 次失败"。
  - **确认指标必须是"短轮询等待状态迁移"而非"点击后立即查询"**：`ScreenerPage.run` 点击 RUN_BUTTON 后，异步 loading 状态可能延迟数帧才反映到 DOM，立即 `count==0` 会误判"未触发"→ 重复点击已消失按钮 → 等满超时误报失败（恰与抗抖动目标相悖）。应改为 `expect_hidden(RUN_BUTTON, timeout_ms=2000)` 短轮询，区分"点击被吞（按钮持续可见→重试）"与"渲染延迟（按钮数帧内消失→成功）"。
  - **重试粒度**：步骤级（交互方法内部），优于用例级 flaky（flaky 重跑整个用例含 DB seeding/页面加载，CI 成本高且不精准）。`DataPage.select_table` 整体重试（含 `select_option` + `TABLE_READY` 先 hidden 再 visible）。
  - 本地无法复现 CI 偶发，最终验收依赖 CI 连续多次运行时验证。

### 坑点 7：语义树启用 ≠ 应用内容渲染完成（P0-1 PoC 实证）
- **根因**：CanvasKit 的 `<flt-semantics>` 语义树被启用/挂载，与应用内容（控件）渲染到
  语义 DOM 是**两件事**。`flt-semantics` 元素出现时其内部节点可能尚未构建完成。
- **后果**：若以「语义节点数 > 0」作为内容已就绪的提前退出条件，会在内容渲染前就发起
  定位查询，命中 0 个 identifier 节点，产生**假阴性**（看似控件不存在，实则未渲染完）。
- **规程**：等待条件必须锚定「应用内容已渲染」的**可观测事实**，而非语义节点数量。
  可靠做法：轮询 `document.body.innerText` 是否已包含应用文案（如页面标题/按钮文案），
  确认内容可见后再查询 identifier 节点；随后仍以 identifier 节点的
  `wait_for(state="visible")` 收敛。

### 坑点 8：offstage / 视口外控件的 identifier 节点 count=0 或无 bbox
- **根因**：尚未进入语义树构建窗口的控件（offstage 控件、`ListView` 视口外的行）不会
  生成 identifier 语义节点，或生成了但无有效 bounding box。
- **后果**：直接 `count()` 得 0、直接取 bbox 抛错，被误判为「锚点缺失」。
- **规程**：视口外控件须先经 `scroll_into_view`（按 `flt-semantics-identifier` 执行 JS
  滚入）进入视口再定位；断言/点击前用 `expect_visible`（identifier 节点 `visible` 等待）
  收敛。`offstage` 控件为**预期排除项**，不应在定位前置等待其出现。

---

## 4. E2E 锚点 (EIDS + AnchorKind) 分类速查表

| AnchorKind | 适用控件类型 | DOM 表现形态 | 定位与点击策略 |
| :--- | :--- | :--- | :--- |
| **`INTERACTIVE`** | `ft.Button`, `ft.IconButton`, `ft.FilledButton` | `flt-semantics[flt-semantics-identifier="EID"]` 独立节点（`label="EID"` 另作无障碍通道，`button=True`） | 取 identifier 节点自身 bbox 中心物理鼠标点击（不下潜 `flt-tappable`） |
| **`INPUT`** | `ft.TextField`, `ft.TextArea` | identifier 节点 + 内层 `<input>` / `<textarea>` | 下潜后代 `input, textarea` 取真实输入面 bbox，`mouse.click` + `keyboard.type` |
| **`COMPLEX`** | `ft.Dropdown`, `ft.PopupMenuButton`, `ft.GestureDetector`, `ft.Container(on_click=...)` | identifier 节点自身（与 INTERACTIVE 同形） | 取 identifier 节点自身 bbox 中心物理鼠标点击 |
| **`LABEL`** | `ft.Text` (纯展示, 无点击) | identifier 节点自身 | 取 identifier 节点自身 bbox，纯展示/断言；`click` / `scroll_into_view` 显式拒绝 |
