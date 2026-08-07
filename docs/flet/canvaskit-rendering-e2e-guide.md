# Flutter Web CanvasKit 渲染行为与 E2E 定位指南

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
Flet Web 依赖 Flutter Web Engine 运行时。Flet 版本由 [`pyproject.toml`](../../pyproject.toml) 锁定（`flet` / `flet-desktop` / `flet-charts` 三包，版本见该文件），`flet-web` 作为 `flet` 的 transitive dependency 同版本发布。
- **动态加载机制**：Flet Web 启动时，`flutter_bootstrap.js` 会从谷歌 CDN 动态下载 CanvasKit 二进制：
  `https://www.gstatic.com/flutter-canvaskit/<engineRevision>/chromium/canvaskit.wasm`
- **本地 Mock 缓存**：E2E 测试通过 `tests/e2e/conftest.py::_setup_canvaskit_intercept` 离线化拦截。`mock_assets/canvaskit/` 保存了匹配 `engineRevision` 的 `canvaskit.js` / `canvaskit.wasm` 和 CJK 字体分片。
- **升级注意事项**：升级 Flet 版本时，若 `engineRevision` 发生变化，必须从 `site-packages/flet_web/web/canvaskit/` 重新复制对应 WASM / JS 文件，并同步更新 `tests/e2e/_font_urls.py`（见 [Flet 升级检查清单](./upgrade-checklist.md)）。

---

## 2. CanvasKit 双轨 HTML 语义树 (`<flt-semantics>`) 映射机制

CanvasKit 并非渲染 HTML 原生控件，而是在 `<canvas>` 上绘图，并在上层维护一层 HTML 语义 DOM 树（`<flt-semantics>`）供无障碍辅助技术和 Web 自动化定位。

### 2.1 双轨映射规则

```
                      ┌─────────────────────────────────────────┐
                      │ ft.Semantics(container=True, label=EID) │
                      └────────────────────┬────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    ▼                                             ▼
       【轨 1: INTERACTIVE / INPUT】                 【轨 2: LABEL / COMPLEX】
     • ft.Button 系列 (button=True)                 • ft.Text / ft.Dropdown /
     • ft.TextField / ft.TextArea                  • GestureDetector / Container(on_click)
                    │                                             │
                    ▼                                             ▼
┌───────────────────────────────────────┐     ┌───────────────────────────────────────┐
│ DOM 生成独立 aria-label 节点:          │     │ DOM 无 aria-label 独立节点，EID 落入  │
│ <flt-semantics aria-label="EID">      │     │ textContent 文本合并节点:             │
│   <flt-semantics flt-tappable />     │     │ <flt-semantics>                       │
│ </flt-semantics>                      │     │   EID\n显示文案                       │
└───────────────────────────────────────┘     │ </flt-semantics>                      │
                                              └───────────────────────────────────────┘
```

#### 轨 1：INTERACTIVE / INPUT 轨（独立 `aria-label` 节点）
- **触发条件**：Flet 原生 Button 系列（`ft.Button` / `ft.IconButton` / `ft.FilledButton` 等）、`ft.TextField`，或显式传入 `button=True` 的 Semantics 包装。
- **DOM 结构**：
  ```html
  <flt-semantics aria-label="e2e.screener.run_button">
    <flt-semantics flt-tappable role="button"></flt-semantics>
  </flt-semantics>
  ```
- **定位策略**：`AnchorPage` 通过 `flt-semantics[aria-label$="EID"]` 精确匹配。

#### 轨 2：LABEL / COMPLEX 轨（`textContent` 换行合并节点）
- **触发条件**：`ft.Text`（纯展示）、`ft.Dropdown`、`ft.Container(on_click=...)`、`ft.GestureDetector(on_tap=...)`。
- **DOM 结构**：CanvasKit **不会**为此类控件生成 `aria-label="EID"` 节点（即使传了 `button=True`，Flutter 引擎对 GestureDetector 场景也会忽略）。
- **文本合并格式**：Semantics `label`（EID）与子节点的展示文本合并到同一 `<flt-semantics>` 的 `textContent` 中，以 `\n` 分隔：
  $$\text{textContent} = \text{EID} + \text{"\n"} + \text{Display\_Text}$$
  *示例*：
  - 导航标签 `EIDS.NAV.SETTINGS`：`textContent` 值为 `"e2e.nav.settings\n设置"`
  - 选股列头 `EIDS.SCREENER.column_header("pct_chg")`：`textContent` 值为 `"e2e.screener.column_header.pct_chg\n涨跌幅"`
- **定位策略**：`AnchorPage` 通过 `_locate_by_text` 在 JavaScript 侧扫描 `textContent` 前缀。

---

## 3. E2E 测试定位与交互坑点及防护规程

### 坑点 1：`textContent` 判定不可使用 `t === label` 严格全匹配（PR 479 修复）
- **根因**：`LABEL` 锚点包裹 `ft.Text` 时，节点 `textContent` 为 `"EID\n显示文本"`。若判断逻辑要求 `(textContent).trim() === EID`，全匹配必定返回 `false`，导致超时失败。
- **规程**：`exact=True` 前缀匹配必须允许 `\n` 换行边界：
  ```javascript
  if (exact) return t === label || t.startsWith(label + '\n');
  ```
  使用 `\n` 分隔符既能精确匹配合并节点（如 `"e2e.nav.settings\n设置"`），又不会误命中同前缀的子命名空间（如 `e2e.nav.settings.tab`）。

### 坑点 2：Playwright DOM 合成 click 事件失效
- **根因**：CanvasKit 的 `<flt-semantics>` 节点不响应 Playwright `locator.click()` 合成事件或 `element.dispatchEvent(...)`。
- **规程**：一律获取节点 bounding box，使用真实物理鼠标坐标点击：
  ```python
  box = await self._locate_inner_tappable_bbox(eid_str)
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

---

## 4. E2E 锚点 (EIDS + AnchorKind) 分类速查表

| AnchorKind | 适用控件类型 | DOM 表现形态 | 定位与点击策略 |
| :--- | :--- | :--- | :--- |
| **`INTERACTIVE`** | `ft.Button`, `ft.IconButton`, `ft.FilledButton` | `[aria-label="EID"]` 独立节点 (`button=True`) + 内层 `[flt-tappable]` | 按 `aria-label$="EID"` 后缀定位内层 `flt-tappable` bbox，物理鼠标点击 |
| **`INPUT`** | `ft.TextField`, `ft.TextArea` | `[aria-label="EID"]` + 内层 `<input>` / `<textarea>` | 按 `aria-label$="EID"` 定位 `<input>` bbox，`mouse.click` + `keyboard.type` |
| **`COMPLEX`** | `ft.Dropdown`, `ft.PopupMenuButton`, `ft.GestureDetector`, `ft.Container(on_click=...)` | `role="button"`，EID 落 `textContent` 形态 `"EID\n<text>"` | 按 `textContent` 前缀 (`.` / `\n`) + `role="button"` 过滤，物理鼠标点击 |
| **`LABEL`** | `ft.Text` (纯展示, 无点击) | 无 `aria-label` 独立节点，EID 落 `textContent` 形态 `"EID\n<text>"` | 按 `textContent` 精确匹配 (`t === EID` 或 `t.startsWith(EID + "\n")`)，纯展示/断言 |
