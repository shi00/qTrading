# Flet 1.1.0 升级与新能力利用方案

> Owner: UI 维护者
> 创建日期: 2026-11-06 · 修订日期: 2026-11-06 · 状态: 修订稿，实施前待复核
> 关联正本: [../flet/upgrade-checklist.md](../flet/upgrade-checklist.md)（升级检查清单）、[../flet/v1-api-constraints.md](../flet/v1-api-constraints.md)、[../flet/mcp-usage.md](../flet/mcp-usage.md)、[ci-cd.md](./ci-cd.md)
>
> **文档职责**：本文是一次特定版本升级的施工与验收方案，保留具体版本以便复现；按流程指南归入 `docs/guides/`，由 [../README.md](../README.md) 的目录索引覆盖。GDR-06 的扫描范围不含该目录，但这不是新增规则豁免，也不改变 `docs/flet/` 各专题的权威性。
>
> **目标**：同时完成 A「可靠升级」与 B「能力优化闭环」；高价值能力须经项目试点形成可验证收益，不能以升级成功或列出候选任务代替优化交付。
> **架构**：维持 PyInstaller + Inno Setup、MVVM + 声明式 UI、既有任务与数据同步链路；基础升级、必验优化与可选增强分 PR，整体完成标准不因拆 PR 而降低。
> **技术栈**：Python 3.13+、Flet、Polars、asyncio、Playwright；具体依赖以 [pyproject.toml](../../pyproject.toml) 为准。
> **执行边界**：当前仅交付方案，不代表已升级、已通过运行验证或已批准安装依赖、下载资产、访问数据库及发布制品。后文复选框均为未来实施项，须分别留存证据。

---

## 0. 结论摘要（TL;DR）

- **不得预判为低风险**：除中性主题与 Web 引擎资源变化，还必须覆盖 Windows 原生客户端默认改用 **Impeller**、`use_effect` 清理/取消行为修复、组件身份保持和迟到事件处理。PyInstaller 只打包 Python 后端，不会使预编译 Flutter 客户端的变化失效。
- **先保兼容，再利用能力**：订阅退订、Toast 计时任务、导出文件、AI 流式滚动、四主题和原生扩展必须回归；源码核验不等于这些运行场景已通过。
- **高价值项必须验证**：统一输入主题、客户端 min/max 尺寸约束、已使用控件的旧规避/重复配置清理属于 B 阶段必验项，必须提交试点与收益/保留证据；自选股重新加载、进度可访问性等增强按明确处置交付，不能无限停在候选清单。
- **不扩大刷新含义**：自选股首版只重读已同步的本地数据；选股重跑保持显式操作，不默认绑定下拉刷新；系统通知须先证明发行客户端支持。
- **实施策略**：A 阶段通过 G5 可独立合并，但只能标记“可靠升级完成”；B 阶段通过 G6 后才可标记整个升级与优化任务完成。FastAPI 先保留已验证约束，再做双环境对照；来源不明的资产和未经验证的真实安装包不能以 CI 绿灯代替验收。

---

## 1. 背景与现状

### 1.1 版本事实（2026-11-06 核实）

| 项 | 值 | 来源 |
|----|----|------|
| 当前锁定 | flet 全家桶 `==1.0.5`（2026-10-21 发布） | pyproject.toml `[project.dependencies]` |
| 锁定包集合 | `flet` / `flet-desktop` / `flet-charts` / `flet-code-editor`（主依赖）+ `flet-mcp`（dev extras，与主包 `==` 锁步） | pyproject.toml |
| 运行时自动安装 | `flet-web`（与主包同版本发布，CI 中 5 处硬编码预装 pin） | upgrade-checklist §1.1 |
| 目标版本 | `1.1.0`（2026-11-05 发布，PyPI 已确认六包——含 flet-mcp、flet-web——均有 1.1.0） | PyPI JSON API |
| fastapi 上界 | `<0.145`（D6.2 临时约束，pyproject.toml 与 CI 5 处同款；解除条件：E2E 全链路在 0.145+ 全绿） | pyproject.toml 注释 |

### 1.2 升级驱动

1. 跟踪上游 minor 版本，避免长期滞留旧版导致未来跨多版本升级的复合风险；
2. 验证并利用 hooks 生命周期、布局、进度语义、Markdown 主题和流式滚动等修复；
3. 评估集中输入样式、客户端尺寸约束等能减少重复逻辑的能力，而非为使用新控件而改造业务；
4. 区分上游工具改善与本项目收益：当前 E2E 为 pytest + Playwright，应用启动器直接运行 Python，不调用 `flet test`，因此其启动错误上报不能算作当前 nightly 的自动收益。

### 1.3 证据与执行状态

- 上游事实依据固定 [v1.1.0 CHANGELOG](https://github.com/flet-dev/flet/blob/v1.1.0/CHANGELOG.md) 和该 tag 源码，不以持续变化的 `main` 或在线最新文档代替目标版本。
- 当前项目事实来自调用链与测试源码；目标版原生客户端、GPU、浏览器 DOM、安装包恢复、通知权限及性能尚待运行验证。
- 每个执行项记录提交 SHA、解析后包版本、环境、命令、真实退出码和脱敏证据；未运行填写原因，不勾选通过。

---

## 2. 升级影响面盘点（项目侧触点清单）

先读 [CLAUDE.md](../../CLAUDE.md) §3/§4、[ci-cd.md](./ci-cd.md)、[dependency-management.md](./dependency-management.md)、[testing.md](./testing.md)；UI/VM 改动再按 [Flet 入口](../flet/README.md) 与 [MVVM 正本](../patterns/mvvm.md) 展开。下表区分必改与条件改动，不把“需验证”误写成“必定修改”：

| # | 位置 | 内容 | 同步方式 |
|---|------|------|---------|
| 1 | `pyproject.toml` 主依赖 | `flet` / `flet-desktop` / `flet-charts` / `flet-code-editor` 四条 `==` pin | 手工编辑 |
| 2 | `pyproject.toml` dev extras | `flet-mcp==` pin（api.json 版本专属，必须与主包对齐） | 手工编辑 |
| 3 | `pyproject.toml` fastapi 上界注释块 | 默认保留 `<0.145`；D-1 对照验证成立后才同 PR 放宽/移除 | 条件改动，记录最终选择 |
| 4 | `requirements.txt` / `requirements-dev.txt` / `requirements-optional.txt` | 编译产物 | **禁止手改**，由 pre-commit 的 `uv pip compile` 三个 hook 自动再生成 |
| 5 | `.github/workflows/ci_cd.yml` | 当前 4 处预装 pin 与同事务 FastAPI 约束 | 全量搜索后同步，不依赖固定数量代替检查 |
| 6 | `.github/workflows/flet-nightly.yml` | 1 处同款预装命令 | 手工编辑 |
| 7 | `tests/e2e/mock_assets/`（fonts / canvaskit / rive） | 目标包的实际资源、配套版本与来源记录 | 先取得来源批准，再按目标集合同步；必须比较 engineRevision，不凭版本号猜测 |
| 8 | `AStockScreener.spec` / 原生客户端 | Python 包数据、图表/编辑器客户端能力、实际加载客户端 | 对比并验证；只有收集规则变化才改 spec |
| 9 | `docs/flet/api-verification-template.md` | 本次升级的 API 核验记录 | 升级验证完成后登记 |
| 10 | `docs/flet/project-differences.md` | 「最后验证日期」 | 升级验证完成后更新 |
| 11 | `ui/hooks.py` / `ui/components/toast_manager.py` 及相应单测 | 生命周期行为与旧规避说明 | 必须回归；仅按目标实测修订说明/实现，不预先删除显式 cleanup |
| 12 | `tests/e2e/_font_urls.py` / `tests/e2e/conftest.py` / `scripts/sync_e2e_fonts.py` | 来源 manifest 校验、revision/摘要错配 fail-fast | 本次资产替换的配套任务，复用现有解析与拦截入口 |
| 13 | 本地验证环境 | 三份 requirements 后额外安装匹配的 `flet-web` | 必须补齐步骤与包路径断言，不依赖运行时自愈 |

> flet-nightly 的补充说明：该 workflow 是**针对当前锁定版本**的每日金丝雀（从 pyproject.toml 动态读取版本号写 Issue 正文），不会提前发现 1.1.0 的兼容性问题。升级合并后它将自动转为对 1.1.0 的每日回归守护。「让 flet-nightly 增加一路对 PyPI 最新版的探测 job」记录为后续独立改进项（见 §8 风险 R-8），不在本方案范围。

---

## 3. v1.1.0 变更逐项影响评估

依据 GitHub Release v1.1.0 正式版发布说明逐项判定（完整清单链接见附录 B）。

### 3.1 破坏性变更

| # | 上游变更 | 对本项目影响 | 判定 |
|---|---------|-------------|------|
| B1 | `flet build macos` .app 按 product name 命名 | 项目用 PyInstaller + Inno Setup 打包，不使用 `flet build`（pyproject.toml 无 `[tool.flet]`） | **N/A** |
| B2 | Material/Cupertino 改由 `material_ui` / `cupertino_ui` Dart 包提供 | 项目无 Flutter/Dart 扩展（纯 Python 侧） | **N/A** |
| B3 | **种子色主题（含默认主题）surface 中性化**：亮色变白、暗色变 `#121212` 近黑，容器/描边/表面文字用中性灰；种子色仅驱动强调色 | 项目 [ui/theme.py](../../ui/theme.py) `THEME_COLOR_SCHEMES` 为四套**显式** `ft.ColorScheme(...)`（DARK/LIGHT/NAVY/DRACULA），非种子色构建 → 直接冲击有界；但显式 scheme 未覆盖字段的控件回退默认值可能漂移 | **需视觉回归**（§4 阶段 3、§5.9） |
| B4 | Web 应用暗色 splash/加载屏默认色 `#222222` → `#121212` | 纯外观；未发现测试断言加载屏颜色 | **低**（E2E 冒烟覆盖） |
| B5 | 自定义 `flet build` 模板须读 `cookiecutter._options` / `_pyproject` | 不使用自定义构建模板 | **N/A** |
| B6 | iOS export options 改读 `cookiecutter._options.ios_export_options` | 同上 | **N/A** |
| B7 | macOS/Windows 构建 copyright 生成规则变化 | 不使用 `flet build` | **N/A** |
| B8 | `flet build windows` 自行转义 company/product 名 | 同上 | **N/A** |
| B9 | 桌面客户端不再为 Debian 10 ARM64 构建（glibc<2.31 报错） | 目标平台 Windows；CI 为 ubuntu-24.04 / Windows | **N/A** |
| B10 | flet-audio：Android 音频焦点默认行为变化 | 项目无音频功能（无 `ft.Audio` / flet-audio 依赖） | **N/A** |

以上只分类官方“Breaking changes”栏目，不能据此推导全部升级风险。官方“Improvements”中 Flutter 从 3.44.8 升至 3.47.8，并明确桌面默认渲染器由 Skia 转为 Impeller；这是本项目必查的兼容性变化（§5.11）。

### 3.2 与项目相关的修复（fixes）

| # | 上游修复 | 项目触点 | 处置 |
|---|---------|---------|------|
| F1 | `use_state` / `use_ref` callable 初始化器推断修复（#7014） | [ui/hooks.py](../../ui/hooks.py) 的两种初始化模式 | 上游明确修复 mypy/ty；本项目仍以 pyright 实测判断，不承诺一定产生类型告警差异（§5.1） |
| F2 | `ResponsiveRow` col 更新、零 spacing、baseline 对齐修复（#7027） | 参数面板、市场卡片、回测和设置布局 | 双向复核；负 spacing 的拒绝也需检查；正常 Container 包装不等同于旧 bug 规避（§5.2） |
| F3 | 进度控件 `semantics_value` 字符串序列化 | 任务中心与数据源同步进度 | 必须核验真实 DOM 与终态；不能推导“无回归风险”或必有 100% 帧（§5.6） |
| F4 | Flutter 升级；`flet test` 启动等待与错误上报（#7023） | 原生客户端与 Web 资源均受影响；现有测试不走 `flet test` | 前者必须回归；后者不算现有 E2E 自动收益 |
| F5 | Tab / PopupMenuItem 按 key 查找修复（#6976） | E2E 定位走 `Semantics.identifier` 体系而非 key | N/A |
| F6 | BottomAppBar notch 位置修复（#7024） | 未使用 BottomAppBar | N/A |
| F7 | Web client 文件 gzip 压缩下发（#7026） | E2E 拦截器只拦截外部 CDN，本地服务文件不经 `intercept_external` | 零改动受益（加载更快）；E2E 冒烟确认 |
| F8 | Linux 桌面 flavor/缓存修复；`flet pack` 扩展缺失告警 | 当前 Linux Web E2E 与直接 PyInstaller 构建不等同于这些入口 | 不计为当前链路自动收益；记录适用边界 |
| F9 | `use_effect` 返回 cleanup 丢失、双 cleanup、异步 cleanup、卸载取消修复 | `use_viewmodel` 与 ToastCard | 核心订阅/销毁路径必须补回归与版本说明（§5.12） |
| F10 | 组件跨父节点移动、opacity/交互包装变化时保持身份；迟到结果/事件不再中断会话 | 声明式导航、切页与异步控件调用 | 复用导航和生命周期测试，验证不丢状态、不重复挂载、不停止后续事件（§5.12） |
| F11 | `FilePicker.save_file()` 在桌面返回前创建目标文件；对话框长期开启超时修复 | 数据导出、选股导出 | 必查覆盖/取消/写入失败，不能把路径或空文件存在视为导出成功（§5.15） |
| F12 | Markdown 链接/引用块默认取主题色；auto_scroll 尾部收缩后继续跟随 | 选股 AI 文本与流式结果区 | 四主题与手动滚动/继续跟随回归；不移除用户 follow_latest 控制（§5.15） |
| F13 | 预编译 Windows `flet.exe` 与 DLL 代码签名 | 官方默认桌面客户端 | 候选制品验证签名与启动；不等于 Python 产品 EXE/安装器获得签名 |

### 3.3 新功能（利用评估详见 §5）

| # | 新能力 | 初步判定 |
|---|--------|---------|
| N1 | `RefreshIndicator` 下拉/主动刷新（桌面 + Web）（#6997） | 自选股本地重新加载试点；不默认绑定策略重跑（§5.3） |
| N2 | `Page.get_package_info()` 运行时包信息（#6950） | 已知来源为 Flutter 客户端；单独标注身份后评估诊断收益（§5.5） |
| N3 | `TextField` / `CupertinoTextField` `text_input_action`（#7021） | 低优先级可选 §5.4 |
| N4 | `flet-notifications` 扩展：全桌面平台本地通知（#6948） | 可选增强，独立 PR §5.7 |
| N5 | `flet-shadcn-ui` 扩展（#6918） | **不采纳** §5.8 |
| N6 | `InteractiveViewer.get_scale()` / `get_transform()` / `on_transform_changed`（#6938） | 当前未使用 InteractiveViewer，记录备用 |
| N7 | `flet build` 系列新配置（权限包 / Android queries / 深链 / iOS entitlements） | N/A（不使用 `flet build`） |
| N8 | `LayoutControl.min_width/max_width/min_height/max_height` | 客户端边界约束试点，不盲目替代测量或持久化逻辑（§5.13） |
| N9 | `Theme.input_decoration_theme` / `Theme.text_field_theme` | 优先评估输入样式收敛（§5.14） |
| N10 | `Dropdown.height` 紧凑高度、tap target 与 Markdown 全局主题 | 条件评估；不得为密度牺牲无障碍或覆盖业务语义色（§5.15） |

---

## 4. 实施计划（分阶段）

> R18：本轮只修订单文件文档，命中文档豁免；当前归档解压环境**无版本控制兜底**。实施跨文件修改时，有有效 clone 则创建隔离 worktree（建议分支 `feat/flet-1.1.0-upgrade`）；不可恢复版本控制时按宪法决策树声明限制并逐文件交付，禁止自行 `git init` 冒充历史。
>
> 每一步涉及不熟悉的 1.1.0 API 时，先经目标版 flet-mcp `get_api` 或目标源码核验（CLAUDE.md §1.10）；MCP 必须在阶段 1 锁步安装后才能提供目标版证据，当前文档修订不表示已经安装。

### 阶段 0 — 保存旧版基线与授权前置（G0）

- [ ] 确认实施授权与隔离方式；安装、下载外部资产、运行带数据库副作用的测试和发布分别遵守项目授权边界。
- [ ] 保留 1.0.5 独立 venv/解析文件、包文件清单、pyright 输出、客户端路径/摘要，以及最后已知正常安装包与 SHA-256。不要先销毁旧环境。
- [ ] 用固定数据、同一 locale、窗口尺寸和 DPI，保存四主题成功截图；覆盖选股、数据源、自选股、图表、CodeEditor 和 Markdown。记录当前支持的最小/默认/宽屏视口，升级后沿用。
- [ ] 在旧版同一环境记录 `flet-web` 版本、engineRevision、渲染资源集合；在本地受控目录保存证据，日志经 DataSanitizer 脱敏，不提交用户数据。
- [ ] 近期 nightly 仅作为旧版健康证据补充；若已红，先定因区分基础设施与产品失败，不以失败截图代替成功视觉基线。
- [ ] 承接 [known-technical-debt.md](../debt/known-technical-debt.md) 的 `P3-E2E-MockAssets-Provenance-Blocked`：取得批准来源或可信离线包后才进入资源替换；未获批准保持 blocked。

**出口**：旧版证据可重放、最后正常制品可取回、资源来源权限明确。缺任一项不进入破坏旧环境或签署回归结论的步骤。

### 阶段 1 — 依赖升级（最小升级 PR 的核心）

- [ ] 编辑 pyproject.toml 的四条主依赖 pin 与 dev 中 `flet-mcp`，均改为 `1.1.0`；**先保留** FastAPI 上界与 0.144.0 对照组合。
- [ ] 用现有 pre-commit 三个 pip-compile hook 生成 requirements；禁止手改。审查解析差异，非必要依赖更新不混入；若出现非预期旧 pin，查明解析约束而非绕过。
- [ ] 新建目标 venv，按 CI 顺序安装三份 requirements，再在同一安装事务预装 `flet-web==1.1.0` 与 `fastapi==0.144.0`（附录 A）；禁止靠首次运行自愈或 user-site 的旧包。
- [ ] 启动前断言六个包均为目标版本、`flet_web.__file__` 位于当前 venv，运行 `uv pip check`；记录完整安装版本。
- [ ] 同步 CI 的全部 flet-web pin，暂保留同事务 `"fastapi<0.145"`；数量当前为 4+1，仍须全量搜索。

**出口 G1**：首次应用启动前已有匹配的 Web 包，目标环境依赖检查通过，旧环境仍保留。

### 阶段 2 — 静态与单元验证

- [ ] 按 §6 运行一次合并选择器的 hooks/Toast/Flet 兼容专项；文件名中的 `0_86` 是历史命名，测试导入当前安装包。
- [ ] 对 §5.12 的生命周期行为先补回归，验证当前失败或明确记录旧版不能覆盖的路径，再做必要适配；真实调度契约不能只靠替身手动执行 cleanup 证明。
- [ ] 对比新旧 pyright 输出；`use_state/use_ref` 类型修复以实际结果为准，只有确认冗余才删除 cast/注解。
- [ ] 用目标 `flet-mcp` 核对使用中的 API，完成一次 get_api 请求；启动 MCP 的检查需结束测试进程，不能把常驻等待视为命令通过。

**出口 G2**：无新类型错误；内部 VM 只销毁一次、外部 VM 不被销毁、Toast 异步清理完成。完整提交门禁在阶段 5 按宪法顺序执行。

### 阶段 3 — 资源来源、Web 基线与 FastAPI 对照（G3）

**3.1 来源与配套**：按 upgrade-checklist §3.1–§3.9 执行，但补齐以下不能被“文件存在”替代的验收：

- [ ] 从两个独立 venv 的实际包读取 engineRevision；比较目标资源清单与文件摘要，不声称只要 minor 升级就一定换 hash。
- [ ] 在批准来源下同步目标 `canvaskit/` 的全部子目录及 `skwasm`/`wimp` 配套文件，核查 Rive JS/WASM 与 symbols；不得递归覆盖后留下未审查的混合版本。
- [ ] 同一目标 venv 运行 `scripts\sync_e2e_fonts.py`，核对字体集合与摘要。现有脚本跳过非空文件并不证明内容匹配；摘要不符时才在获准来源下重取。
- [ ] 计划新增 `tests/e2e/mock_assets/manifest.json` 与必要 LICENSE/NOTICE，逐资产记录相对路径、来源 URL、包版本/包摘要、engineRevision（适用时）、文件 SHA-256、许可路径及下载/变换方法；不适用字段显式说明原因。
- [ ] 在现有 `_font_urls.py`、`_setup_canvaskit_intercept` 与同步脚本中复用解析入口加入 manifest 校验：版本、revision、摘要错配或条目缺失均明确失败，不继续 fulfill 旧文件。保留 host 精确匹配与路径穿越保护。
- [ ] 扩展 `tests/unit/test_e2e_font_urls.py`，必要时新增 `tests/unit/test_e2e_asset_manifest.py`，覆盖有效集合、revision 错配、摘要损坏、缺文件和路径穿越；许可证/来源人工核对不伪装成单测可自动证明。
- [ ] 保持既有资产债务 blocked，直到其正本全部解除条件满足；本次目标集合未闭合时 G3 不通过，也不扩大声明为历史资产已经合规。

**3.2 固定组合的 Web 基线**：

- [ ] 先固定 `flet-web==1.1.0 + fastapi==0.144.0`；验证 COEP/PNA、CDN 拦截、字体、语义树 identifier、Dropdown、导航及真实导出交互。
- [ ] 运行 anchor smoke 后再跑全量 E2E；验证普通事件、控件移除后的迟到事件/结果仍不妨碍下一次操作。浏览器测试不能代替原生客户端测试。
- [ ] 对 G0 四主题基线做相同条件的截图比较，运行对比度检查；差异按 §5.9 处理。

**3.3 D-1：解除 FastAPI 上界的受控实验**：

1. 只有 3.1/3.2 通过后才开始。以暂时放宽上界的同一候选代码和同一资源集合，为 A/B 两腿分别创建独立环境与包管理器生成的临时解析产物；其他输入固定，不复用被覆盖的环境。
2. A 明确固定 `fastapi==0.144.0`，B 首个候选明确固定 `fastapi==0.145.0`；两腿的 Flet-Web 均为 `1.1.0`。记录所有传递依赖差异，每腿启动前用元数据断言实际版本并执行 `uv pip check`。
3. 两腿运行相同全量 E2E，必要时再测实施当日拟采用的新候选版本并记录精确值。失败必须区分资源、应用、浏览器、基础设施与 FastAPI 原因；不能把任何失败都归因于 FastAPI。
4. B 通过且目标组合完整证据齐备，才在升级同一 PR 同步放宽/移除 pyproject 与 CI 所有上界，并经 pre-commit 再生成正式锁文件。`uv pip compile` 可能复用旧 pin，必须明确升级候选包、检查最终锁值，重装最终锁定组合并重跑验收。
5. 未验证或失败未归因则保留上界，更新 D6.2 注释，升级不以解除上界为必达条件。

**出口 G3**：来源与版本配套闭合、实际固定组合全量 E2E 有证据；D-1 的通过/保留选择明确。

### 阶段 4 — 原生客户端与真实候选制品（G4）

- [ ] 对比两版本的包数据清单、客户端 flavor、实际 exe/DLL 路径和摘要；仅必要时改 spec，证明图表与 CodeEditor 的 Python 包和客户端实现均配套。
- [ ] 为本 PR 的候选 SHA 构建真实 Windows 程序及安装器。当前真实构建有 tag/手动触发限制；PR installer 检查包含空 EXE stub，脚本语法通过也不等于候选程序已构建或运行。按现有受控构建流程获准触发，或本地隔离构建并保存同等证据。
- [ ] 在干净 Windows VM 安装实际拟发行变体，记录安装包 SHA-256、应用版本、客户端摘要、Windows/GPU/驱动、DPI 与 locale；不同变体至少分别验证其独有能力，不能用一个变体代表所有。
- [ ] 用默认 Impeller 完成四主题关键页、图标、图表、CodeEditor、中文文字/输入、Dialog、Dropdown、窗口缩放、最小化恢复和关闭；再用 §5.11 的 Skia 回退验证关键路径，截图与 G0 对照。
- [ ] 核验官方客户端签名及启动；不把客户端签名当作本项目 EXE/安装器已签名。无 unknown control、缺图标/字体/资源或渲染异常才算通过。
- [ ] 在隔离 VM 实际运行 `installer_smoke_upgrade.ps1`、`installer_smoke_running_app.ps1`、`installer_smoke_crash_recovery.ps1`，使用脚本真实参数并记录退出码；它们的 sidecar/数据断言与上述 UI 检查互补。
- [ ] 按 §7 完成“旧包→候选包→恢复包”，确认配置、已提交数据哨兵与恢复后实际依赖版本；只验证正向升级不算回滚测试。

**出口 G4**：合并前已有对应 SHA 的真实制品、原生 UI、两种渲染后端与恢复证据；拿不到运行环境或构建权限时阻断待验证，不能改记“CI 已覆盖”。

### 阶段 5 — 文档登记与交付

- [ ] `docs/flet/api-verification-template.md` 登记 1.1.0 核验记录（changelog 要点 + 逐项验证结果）
- [ ] `docs/flet/project-differences.md` 更新「最后验证日期」
- [ ] 旧版 `use_effect` 规避说明按目标源码与运行结果修订；项目 hooks 的显式 cleanup 与订阅后补快照不能因为上游修复就删除。
- [ ] 提交前按顺序运行 `ruff check .` → `ruff format --check .` → `pre-commit run --all-files` → `pyright` → `python -m pytest tests\unit\ -v --tb=short`；pre-commit 生成文件变化后须重新检查。
- [ ] upgrade-checklist §1–§5 全项勾验
- [ ] PR 使用仓库模板；描述中记录 engineRevision 是否变化、D-1 处置结果、视觉回归结论
- [ ] `python scripts/check_docs_consistency.py` + `python -m pytest tests/unit/test_docs_consistency.py` 通过
- [ ] 审核 G0–G4 证据与 CI 全量结果后才允许合并；这只关闭 A 阶段，不关闭 B 阶段或整个任务。合并后首轮 nightly 属观察期，不能反过来抵扣合并前缺失的门禁。

### 阶段 6 — 能力优化闭环（B 阶段，独立 PR，G6）

**前置**：A 阶段 G5 通过并合并；阶段 6 不能阻塞 A 的独立安全交付，但也不能因 A 合并而被跳过。实施时先执行 §10 的 opt-01–opt-03 必验任务，增强项不必全部开发。

- [ ] opt-01：按 §5.14 在 system_tab 完成统一输入主题试点，记录默认配置迁移前后计数和状态矩阵；通过才保留变更，失败保存证据后回退，不能仅写“以后评估”。
- [ ] opt-02：按 §5.13 完成客户端尺寸约束试点，比较同样视口/数据下的边界行为与纯布局逻辑，记录哪些计算可消除、哪些仍必要；不能只在方案中描述新属性。
- [ ] opt-03：逐项核对 §5.1、§5.2、§5.9、§5.12、§5.15 涉及的既有类型/布局/生命周期/主题/滚动规避，形成“保留/替换/删除”的理由与回归证据；必要适配已在 A 完成的不重复实现或重复计算收益。
- [ ] 为每个必验项给出终局结论：采用并验证，或试验证明本项目不宜采用。缺少环境、权限、时间或运行证据只记“未完成/阻塞”，不能冒充无收益结论。
- [ ] 对自选股重新加载、进度语义、诊断、通知、键盘/密度及明确不采用的能力，按 §10 记录“本轮采用并完成 / 明确排除本轮及依据”；不得只留“可选”二字。
- [ ] 按 §10 汇总收益与必要保留项，配套对应组件/VM/真实交互测试和单独回退；通过 G6 后才关闭整体任务。

推荐先完成输入主题试点；尺寸试点与独立增强之间没有因排列顺序产生的隐含依赖。B 阶段不是追求新 API 使用数量，不为删除行数而移除业务校验、取消传播、持久化或正常布局包装。

---

## 5. 新功能 / 修复利用设计

> 总则：利用项**不混入升级 PR**；每项独立评审。所有新 UI 代码遵守 MVVM 契约（View = `@ft.component`，命令走 ViewModel）、R16（事件处理器禁同步阻塞）、R23（颜色/字号只用 `AppColors` / `AppStyles` token）、i18n 走 key。

### 5.1 【直接相关】use_state/use_ref 初始化器推断（F1）

- **现状**：[ui/hooks.py](../../ui/hooks.py) `use_viewmodel` 以 `ft.use_state(lambda: resolved_vm.state)` 初始化快照（callable 形式，仅首次渲染求值），这是项目 MVVM 桥接的核心路径；全项目 45 个文件存在 use_state 调用。
- **适用边界**：上游明确修复 callable 初始化器在 mypy/ty 中的推断；本项目使用 pyright，不能据此推定现有 pyright 一定误报，也不额外引入类型检查工具。
- **行动**：
  1. 升级后对 `ui/hooks.py` 及 use_state 密集文件（app_layout、各 view、对话框组件）跑 pyright，确认推断改善；
  2. 若存在为绕开误推断而写的显式注解 / `cast`，评估回退（逐处核对，不贪多）；
  3. `tests/unit/ui/` 中 hooks 相关用例复跑确认行为不变。
- **验收/退出**：复用 [test_hooks.py](../../tests/unit/ui/test_hooks.py)，确认工厂只初始化一次、state 类型与运行结果一致；没有冗余告警就不做额外清理。生命周期修复的运行时测试另见 §5.12。

### 5.2 【双向复核】ResponsiveRow 修复（F2）

- **上游修复内容**：col 属性更新不生效、spacing=0 不生效、baseline 对齐错误（#7027）。
- **项目触点**：[screener_view.py](../../ui/views/screener_view.py) 参数面板、[market_dashboard.py](../../ui/components/market_dashboard.py) 指数/概念卡片行、回测配置/结果面板、settings 各 tab。
- **风险与机会**：
  - 机会：只有能以旧版失败、目标版通过的用例证明是同一上游 bug 的绕路，才候选简化；
  - 风险：spacing=0 现在真正生效、col 动态更新行为修正，可能**反向改变**既有页面布局。
- **必须保留的边界**：选股参数面板的 Container 让声明式 Component 拥有可下发布局属性；默认 col=12、正常断点栅格和 Wrap 类型规避不能混同为本次修复。无对应证据不删除包装。
- **行动**：
  1. 逐文件审查 ResponsiveRow，用“正常布局 / 依赖旧行为 / 已证实可简化”分类，同时检查负 spacing；
  2. 视觉回归（阶段 3 截图对比）重点覆盖：选股页参数面板、市场仪表盘、回测面板、设置-数据源 tab；
  3. 必要适配随升级 PR 合入；纯简化延后独立 PR。验收含 col 动态变更、spacing=0、baseline 及连续缩放，不能只比较初始截图。

### 5.3 【限定试点】RefreshIndicator 与业务操作边界（N1）

**已核实 API**：`RefreshIndicator` 支持继承的 `tooltip`、零参数 async `on_refresh`；`await show()` 会触发刷新，不只是动画，但其返回不是业务完成屏障。客户端支持空/短内容的可滚动物理行为，仍须在应用实际布局里测试。

#### 自选股：只重新加载已同步数据

- **改动面**：[watchlist_view.py](../../ui/views/watchlist_view.py)、[watchlist_view_model.py](../../ui/viewmodels/watchlist_view_model.py)、双语资源，以及现有 [View 测试](../../tests/unit/ui/views/test_watchlist_view.py) / [VM 测试](../../tests/unit/ui/viewmodels/test_watchlist_view_model.py)。
- **复用接口**：`WatchlistViewModel.load_watchlist() -> bool`、`state.is_loading`、既有错误与缺失行情表示；不新增同义的 `refresh_quotes`/`refreshing`。该方法查询本地列表与 `daily_quotes`，不获取实时行情。
- **新增入口**：正常工具区新增“重新加载”按钮及无障碍标签，错误重试入口保留；按钮和手势接到同一刷新命令。按钮 `await` 当前已挂载 indicator 的 `show()`，不另行重复调用 VM；VM 不持有 Flet 控件。
- **状态流**：回调先检查 VM 的最新 `is_loading`；空闲才 `await load_watchlist()`。成功返回 True 才反馈重新加载完成；False 可能是已分类失败或旧代次被取代，不能统一报成功；以当前 VM 快照决定错误呈现，不覆盖新代次。
- **并发/取消**：继续使用现有请求代次隔离；加载中禁用按钮并在回调入口复核，不新增竞争中的加载。`CancelledError` 必须传播；View 卸载释放 UI 引用，不缓存捕获旧 VM 的回调，不绕开既有 dispose/失效处理。
- **文案/数据质量**：价格未更新不得提示“已获取最新行情”；行情降级保留“—”，不能填 0。网络取新行情如有需求，应走独立数据同步用例和产品确认，不藏进本控件。
- **验收**：按钮/键盘与手势均只发起一次重载；空列表、短列表、加载失败、行情降级、极快完成、连续点击、切页和 mutation 后迟到响应均正确；失败有反馈，取消不伪装失败或成功。

#### 选股：不默认把重跑绑定为刷新

[AIStreamMixin.run_strategy](../../ui/viewmodels/ai_stream_mixin.py) 清空结果、提交 TaskManager 后返回，默认会保存新 run_id；这既不是结果重载，也不是“await 返回就运行完成”。

- **首版决策 D-2**：仅实施自选股试点；选股页保留现有显式运行/取消入口，不新增下拉触发策略、AI 外发或历史写入。
- **未来若单独批准重跑入口**：复用现有参数校验、配置冻结、活动运行守卫和取消通道；仅 REALTIME 可显式重跑，HISTORY 不隐式启动新运行；文案必须说明这是新任务。
- **完成契约**：指示器绑定本次 task_id 的排队→执行→成功/失败/取消终态，而非 `run_strategy()` 或 `show()` 返回；其他任务的终态不能解除它。沿用现有后台任务切页归属，不因指示器卸载误取消业务。
- **独立验收**：排队期间持续忙态、重复触发无新增任务、参数错误不提交、历史模式不重跑、失败/取消/切页归属正确。没有此终态桥接与用例时不实施该扩展。

### 5.4 【低优先级可选】TextField.text_input_action（N3）

- **能力**：`TextInputAction.NEXT` 让确认键跳转下一可聚焦控件。
- **项目形态**：Windows 桌面为主，无屏幕键盘场景；收益局限于表单 Enter 键导航体验。
- **候选项**：设置页多字段表单可使用 NEXT；[watchlist_add_dialog.py](../../ui/components/watchlist_add_dialog.py) 搜索框保留提交搜索的预期，不机械改成 NEXT。
- **验收**：NEXT 的焦点顺序、Enter 与既有 `on_submit` 的组合、中文输入法确认、最后字段提交及对话框关闭后焦点恢复均符合原行为。未发现明确收益则不修改，不作为升级门禁。

### 5.5 【评估项】Page.get_package_info()（N2）

- **能力与已知来源**：返回 Flutter 客户端的包信息，底层为 `PackageInfo.fromPlatform()`，不是 Python 产品包的元数据。官方预编译客户端的非空 version 也不能认定为 AStockScreener 产品版本。
- **项目契合点**：[utils/diagnostics.py](../../utils/diagnostics.py) 诊断包收集环境信息，可增补包信息辅助用户报障。
- **方案**：UI/app 层异步采集后，以可选的 `flutter_client_package_info` 快照传给诊断导出；保留现有独立产品版本来源。不得让 [utils/diagnostics.py](../../utils/diagnostics.py) 的无页面调用方必须持有 Page 或回调 UI。
- **核验/退出**：在开发环境与冻结产物各采集一次，和实际客户端 exe 元数据对照；测试字段缺失、调用失败及无页面导出，失败明确标记 unavailable 并按标准分类脱敏记录，不填业务产品版本伪装客户端数据。只有诊断收益明确才接入。

### 5.6 【机会项】进度控件语义化强化（F3）

- **改动面**：[task_center_view.py](../../ui/views/task_center_view.py)、[data_source_tab.py](../../ui/views/settings_tabs/data_source_tab.py)、[e2e_ids.py](../../ui/testing/e2e_ids.py)、既有组件/VM 测试与 E2E 定位辅助。
- **确定进度**：在 identifier 下定位实际 `progressbar` 语义节点，对 `aria-valuenow` 作数值比较，不以字符串 `"100"` 与 `"100.0"` 的差异判失败；仅显式提供百分比文案时断言 `aria-valuetext`。
- **不确定进度**：`value=None` 时验证 loading 状态与可读标签，不推断百分比；正常状态缺失不得伪装 0%。
- **终态独立**：[DataSourceViewModel](../../ui/viewmodels/data_source_view_model.py) 结束时清零并隐藏进度条，因此成功断言用本次任务的持久终态或完成消息，不要求浏览器捕获瞬态 100%。任务中心若保留完成条，可另行检查其 `value=1.0`。
- **验收**：组件样例覆盖 `value=0.6/1.0`、自定义 `"60%"` 与不确定进度，并检查真实 DOM 属性；业务用例覆盖极快成功、失败、取消与控件移除。不延迟隐藏控件、不延长 sleep、不把 100% 当成功证据来迎合测试。
- **门禁**：复用 AnchorKind 与现有定位辅助；新增 identifier 通过 `check_e2e_anchors.py`，屏幕阅读器文案与双语状态同时验证。

### 5.7 【可选增强，独立评审】flet-notifications 系统通知（N4）

- **能力**：跨平台（含 Windows）本地通知服务：展示/定时/取消、权限请求、动作回调。
- **项目场景**：长耗时任务完成提醒——数据同步（sync 完成）、回测跑完、夜间预测（`services/scheduled_jobs/nightly_prediction.py`）产出名单。当前为应用内提示（任务中心 / SnackBar），窗口最小化时用户无感知。
- **前置门槛**：先在实际 Windows 客户端 flavor/冻结产物中证明该插件有原生实现；只有 Python 包能 import 或加了 hiddenimports 不够。若必须定制 Flutter 客户端，另行审批构建、签名、分发及维护成本；本升级不默认迁移为 `flet build`。
- **复用与边界**：app/UI 桥接消费 [TaskManager.subscribe](../../services/task_manager.py) 的终态事件，复用 `core.i18n.Message` 与既有消息语义；services 不 import Flet/通知包。确需注入时把协议放中立层，由 app 注入，不另造第二套任务完成事件。
- **生命周期**：默认关闭的设置经现有配置链路保存；首次用户启用才按平台请求权限。订阅随应用生命周期退订，按 task_id/终态去重，重连/重放不重复通知；关闭期间不补发历史积压。
- **错误/隐私**：权限拒绝或插件不可用要明确反馈；通知失败经分类脱敏记录，但不改变原任务成功/失败终态。系统锁屏可见内容仅用通用任务类型与状态，不带 Token、个人信息或选股明细。
- **验收/退出**：干净 Windows 安装产物在最小化后能收到一次通知，点击行为符合平台能力；拒绝权限、关闭开关、重复事件、退出期间事件均不损害业务。能力或维护成本不满足时保留应用内提示并停止本项，不阻塞基础升级。

### 5.8 【不采纳】flet-shadcn-ui（N5）

- **当前不采用的理由**：尚无必须借该组件集实现的产品需求；引入会增加客户端能力验证、依赖、主题适配与回归成本。本次优先复用现有组件和 tokens。
- **不是治理禁止**：R23 约束裸颜色/字号，并不自动禁止第三方组件、图标集或主题映射；四主题能否完整适配尚未验证，不能写成“必然冲突”。未来有明确场景时按 token 映射、原生能力与四主题证据重新评估。

### 5.9 【决策点】默认主题中性化（B3）的处置策略

视觉回归（阶段 3）若发现漂移，二选一：

- **(a) 显式补齐 `ColorScheme` 字段**（推荐）：把漂移控件的 surface/outline 色显式写进 `THEME_COLOR_SCHEMES`，保持「所见即声明」，符合 token 体系精神；
- **(b) `ft.Theme(surfaces=ft.ThemeSurfaces.TONAL)`**：一键保留旧版种子色调观感（官方提供的迁移逃生门）。

先以 G0 基线确认漂移是否真实，再选择。保留完整现有主题配置，不以新建一个仅含 surfaces 的 Theme 覆盖 scrollbar/DataTable 等样式；亮暗两侧一致处理并测试切换。若采用临时兼容策略，按项目要求记录上限与升级触发条件，不把工期紧张本身作为跳过视觉验证的理由。

### 5.10 【按实际入口判定】其余工具改善

gzip 对实际经 Flet Web 服务下发的文件适用，需验证资源与缓存可正常加载；不承诺项目中未测得的速度收益。当前不使用的 `flet test`、`flet pack`、Linux 原生客户端及移动构建改进，不列为本项目已获得的功能收益，也不为此更换现有测试/打包链路。

### 5.11 【升级必查】Windows Impeller 与 Skia 回退

- **事实/范围**：目标版 Windows `client/windows/runner/main.cpp` 默认采用 Impeller，并读取 `FLET_NO_IMPELLER`。官方默认桌面客户端、未由 `FLET_VIEW_PATH` 或自定义构建覆盖的 PyInstaller 运行路径适用；实际客户端仍需核查。Web CanvasKit/Skwasm 与该原生后端是两条独立验证链。
- **操作约束**：Windows 接受大小写不敏感的 `1`、`true`、`yes`；建议启动前设 `FLET_NO_IMPELLER=true`。`on`、`false` 或仅存在空变量不等于启用回退。测试完成恢复原环境，不提交用户机器全局配置。
- **改动面**：优先保持代码不变，在 G4 验证默认/回退启动与真实客户端身份；只有证明需要正式回退入口时，才在 app 启动边界实现并配套日志/配置规范，不把回退当已证明更优的新默认。
- **验收**：默认后端跑四主题关键场景；Skia 重跑图表、CodeEditor、中文输入、弹窗、缩放与恢复。记录 GPU/驱动、原生/远程环境、客户端摘要与差异；无特定 GPU 失败证据时只登记风险，不宣称已发生故障。
- **恢复路径**：若默认后端存在已复现故障而 Skia 可用，可采用明确告知的临时回退；两者都失败则停止发行并执行制品恢复，不以浏览器通过替代。

### 5.12 【升级必查】生命周期、组件身份与迟到回调

**直接命中**：[ui/hooks.py](../../ui/hooks.py) 的旧版说明明确涉及返回 cleanup 被重渲染清空；`use_viewmodel` 显式 cleanup 负责退订和内部 VM dispose；[ToastCard](../../ui/components/toast_manager.py) 有等待计时任务退出的 async cleanup。这些不是仅类型推断改善。

**实施顺序**：

1. 在 [test_hooks.py](../../tests/unit/ui/test_hooks.py) 与 [test_toast_manager.py](../../tests/unit/ui/test_toast_manager.py) 扩展实际组件生命周期用例，复用渲染辅助但补足真实目标版调度验证，不只直接调用测试替身清理函数。
2. 验证无关重渲染不增加订阅；外部 VM 换实例退订旧对象、订阅新对象且不 dispose 外部对象；内部 VM 卸载只 dispose 一次；快速挂载/卸载无残留订阅。
3. Toast 卸载后计时任务停止且不再回调；异步 cleanup 完成后再重新 setup；测试返回 cleanup 与显式 cleanup 各自只运行一次，依赖变化与运行中异步 setup 卸载取消顺序正确。测试异步 setup 不意味着当前 hooks 的同步 setup 也是该模式。
4. 导航与响应布局切换时组件身份/state 保持；卸载后的迟到方法结果/事件不会中断后续事件循环；opacity/交互开关变化不导致 VM 意外重建。
5. 确认目标行为后修订 hooks 与 Flet 专题中的旧版机制说明；必要实现修复配 R19 单测。**不默认删除显式 cleanup、订阅后补快照、请求代次隔离或业务取消传播**，这些仍有独立用途。

**退出条件**：生命周期计数、后台任务终止和随后一次正常交互均有可核对证据；出现取消吞没、双 dispose 或会话停止则阻断升级。

### 5.13 【opt-02 必验试点】客户端 min/max 尺寸约束

- **能力**：`LayoutControl` 的四个 min/max 宽高属性由客户端每次布局执行，适合表达边界，不能保证替代文本测量、拖拽状态或持久化逻辑。
- **改动面/现状**：[AppStyles.calc_dropdown_width](../../ui/theme.py) 按字符视觉长度估算再 clamp；[resizable_splitter.py](../../ui/components/resizable_splitter.py) 加载、拖拽、保存均约束宽度。这是两个可测候选，不推断它们是旧版 bug。
- **试点设计**：先在设置页语言/主题 Dropdown 外围布局与可拖拽分栏内容容器验证目标属性，边界复用既有 AppStyles/组件参数；保留显式宽度估算、状态与持久化 clamp，防止客户端视觉值与保存值分离。分栏整体空间不足时沿用既有折叠/断点策略，不能用 min_width 强行撑破父容器。
- **推广门槛**：只有证明客户端约束消除了具体越界/延迟，或某段旧 Python 逻辑纯粹重复布局边界，才删除该段；不能删除正常 Container 或引回全局 `page.on_resize` 订阅。
- **验证文件**：扩展 [test_resizable_splitter.py](../../tests/unit/ui/test_resizable_splitter.py)、[test_resizable_splitter_body.py](../../tests/unit/ui/test_resizable_splitter_body.py)、[test_theme.py](../../tests/unit/ui/test_theme.py) 与相关设置页契约测试。
- **验收/回退**：最小/默认/宽屏连续缩放、长中英文选项、DPI、拖拽上下限、折叠恢复及保存值一致；视图无新增溢出。没有可验证收益就不推广；回退仅撤销试点布局属性，不迁移或重置用户保存宽度。
- **B 阶段交付**：试点不可省略；给出同条件边界用例的前后结果、可删除的纯边界逻辑清单与必要保留清单。未改善边界、未减少冗余且只增加一层约束时，按实验证据拒绝采纳，不写成已完成布局优化。

### 5.14 【opt-01 必验试点】统一输入主题

- **能力边界**：`Theme.input_decoration_theme` 使用 `ft.InputDecorationTheme` 统一 TextField/Dropdown 的边框、填充、padding 与提示/错误文案装饰；`Theme.text_field_theme` 使用 `ft.TextFieldTheme.text_style` 统一 TextField 编辑文字，不能推断它也覆盖 Dropdown 选项文字。
- **现状/接入点**：[_build_theme](../../ui/theme.py) 已集中管理 ColorScheme 与控件主题；[system_tab.py](../../ui/views/settings_tabs/system_tab.py) 的语言/主题 Dropdown、并发数 TextField 重复配置 padding、圆角、字号与 `INPUT_BG/INPUT_TEXT/INPUT_BORDER`。
- **试点步骤**：先以现有默认视觉写主题/组件断言；在 `_build_theme` 中以 AppColors/AppStyles 构造共享输入主题，只迁移 system_tab 里确实等价的默认属性。状态边框覆盖与局部属性优先级按目标 API 核验，未指定状态保留框架默认，不无意抹平 hover/focus/error。
- **保留范围**：字段宽度、校验、错误数据、suffix、敏感输入策略和事件仍在各控件；Dropdown 的专属文字/选项样式不因 TextFieldTheme 而删除。不迁移业务涨跌色。
- **配套验证**：扩展 [test_theme.py](../../tests/unit/ui/test_theme.py)、[test_system_tab_contract.py](../../tests/unit/ui/test_system_tab_contract.py) 与 [设置页测试](../../tests/unit/ui/views/settings_tabs/test_system_tab.py)，先红后绿；原生与 Web 验证四主题下普通、hover、focus、error、disabled、局部覆盖和中英文布局。
- **采纳/回退**：只有默认效果与可访问性不退化、重复样式确有减少才推广；回退共享主题时同时恢复局部默认值，不能留下依赖主题的半迁移控件。此项不阻塞 A 阶段，但必须在 B 阶段试点、判定和验收。
- **收益证据**：按同一组默认属性，统计试点前后在控件构造处重复出现的赋值条目数，并列出迁入共享主题的属性与控件。采用时重复默认配置须净减少；不以新增主题对象、删除行数或截图相似本身宣称实现优化。统计范围与排除的字段专属属性同时记录。

### 5.15 【条件利用】导出、Markdown、流式滚动与密度

| 变化与项目触点 | 设计/验证要求 | 交付边界 |
|---|---|---|
| FilePicker 桌面预创建文件；[data_view.py](../../ui/views/data_view.py) 与 [screener_view.py](../../ui/views/screener_view.py) 的导出 | 选择路径后可能已产生空文件；只有实际写入完成才报成功。验证取消、覆盖既有文件、写权限失败、写入中取消、中文路径与打开文件夹；Web 下载路径单独测。失败必须明确反馈，不能按“文件存在”验收 | 必须回归；发现导出成功判据错误即在基础升级内修复。清理仅限能证明由本次操作创建的临时产物，不删除用户原文件 |
| Markdown 默认链接/引用块随主题；选股 AI 文本区 | 四主题下验证默认与局部 Markdown 样式、链接、引用和代码块对比度；确属旧版硬编码规避的局部样式才可简化 | 回归必做，样式去重单独交付 |
| auto_scroll 尾部内容收缩后继续跟随；选股流式结果 | 验证占位替换为较短内容后仍跟随；用户主动上滚后新 token 不抢回视口；恢复跟随、结束、失败和切页正确。保留现有 `follow_latest` 业务控制 | 回归必做，不能把修复当删除用户跟随开关的理由 |
| Dropdown 紧凑高度、MaterialTapTargetSize、PopupMenu 圆角 | 仅有明确密度需求时在 token/主题层试点；键盘焦点、指针点击与无障碍命中范围不退化，不全局启用 SHRINK_WRAP | 可选，不新增一套尺寸常量 |
| `page.run_task()` 返回值可 await | 现有导航/关闭等不少调用本意是 fire-and-forget；不批量改为 await，更不能据此推导 TaskManager 的业务任务已完成 | 记录能力；只有确需等待该 handler 时按调用语义单独改动 |

---

## 6. 验证矩阵

先运行覆盖变更的合并专项选择器；提交/跨层修改再执行全量门禁，不无差别增加构建工具。下列是实施验收要求，**并非本次文档修订已运行的结果**。

| 门禁/验证项 | 方法与证据 | 通过条件 |
|---|---|---|
| G0 旧版基线 | 依赖/客户端清单、pyright、四主题成功截图、正常安装包与摘要 | 在升级前取得，条件可重放，旧环境未覆盖 |
| G1 依赖一致性 | 六包版本、包路径断言、`uv pip check` | 同一目标 venv；无 user-site 混入；Web 包在首次启动前存在 |
| G2 hooks/兼容 | 附录 A 合并专项；hooks/Toast 真实调度补测 | 生命周期计数、async 清理、取消与组件身份保持正确 |
| G3 资产来源 | manifest 与许可证人工核对；revision/摘要/缺文件/路径测试 | 不是仅文件存在；错配 fail-fast；来源批准与版本对应 |
| G3 Web 功能 | anchor smoke、导航后全量 E2E，固定真实安装版本 | 无缺资源、unknown control 或事件链停止，交互与导出正常 |
| D-1 双腿试验 | 同候选代码/资源，FastAPI 0.144.0 对照与明确候选版本，记录依赖差异 | 每腿版本断言及全量结果；最终正式锁组合再次验证 |
| G4 原生/视觉 | 真安装产物；默认 Impeller 四主题、Skia 关键路径；相同 DPI/locale/视口对比 | 图标、中文、图表、编辑器、Markdown、Dialog/Dropdown、缩放恢复正常 |
| G4 安装/恢复 | 实际运行三类安装冒烟与旧→新→恢复路径 | 真实制品、脚本退出码、配置/数据保留、UI 与恢复后版本均验证 |
| 对比度 | `python scripts\check_theme_contrast.py` | 项目现有对比度门槛通过；不代替实际截图/读屏 |
| 提交全量门禁 | ruff → format → pre-commit → pyright → 全量 unit；CI 既有全量检查 | 真实退出码通过，无新告警或违规；生成物已复核 |
| 文档 | 一致性脚本与 `test_docs_consistency.py` | 相对链接/锚点/治理门禁通过；无法运行项如实登记 |
| G6（B 阶段）优化闭环 | opt-01–opt-03 必验任务的试点、收益/保留证据与所有增强项的终局处置；采用项跑对应测试 | 未完成试点不能以“可选”关闭；整体结论与实际收益一致，不用新 API 数量代替 |

**阶段划分**：G5 只验收本表 A 阶段的升级、兼容、制品与恢复项；G6 验收 B 阶段，不能反向成为 A 合并前置，也不能被 G5 通过替代。B 的独立 PR 仍按实际改动范围运行项目规定门禁。

**测试分工**：单元测试验证状态/计数/错误；真实组件调度验证框架生命周期；Web DOM 验证语义属性；原生客户端验证 GPU/扩展。任何一层不能作为另一层的替代证明。MCP 仅核 API，不证明运行正确；合并后 nightly 仅作观察期。

## 7. 回滚方案

### 7.1 触发与处置顺序

原生渲染异常、核心控件/扩展缺失、生命周期错误、数据/配置异常、持续启动失败均停止发行；隔离资源、环境与应用原因，不自动把任何失败归因于 FastAPI。涉及数据恢复先确认应用及子进程完全退出，按 [how-to.md](./how-to.md) 的离线维护流程执行。

1. 仅渲染后端故障且 Skia 验证可用：可临时采用 §5.11 回退并记录适用环境、责任人、退出条件；不能用它掩盖其他失败。
2. 回退不能恢复核心功能：选择 G0 留存的正常制品或基于旧依赖重新构建的恢复版本，不继续向用户分发候选包。
3. 业务数据若受影响：停止自动重试/覆盖，保留诊断与备份，按既有维护流程处理；不能把重装程序当数据库恢复。

### 7.2 源码与开发环境

- 基础升级为一个必要变更 PR；在有效 Git 环境中用新 revert 提交恢复依赖、CI pin、资源/manifest 与必要适配，不重写历史。归档环境依赖 G0 保存的文件清单与备份，不能假装可以 git revert。
- 还原的 requirements 是受控生成物；若 pyproject 再调整，仍经既有 hook 生成，禁止手改。恢复后用旧解析集合**重新创建/同步 venv**并验证实际包版本，Git 不会自动卸载已安装的新包。
- 重跑旧组合的启动、核心功能和依赖检查；nightly 的锁定版也需实际恢复后运行，不能仅凭源码版本号宣称服务已恢复。

### 7.3 用户已安装制品

- 安装器维护者保存最后正常版本的各发行变体、安装包 SHA-256、产品版本、客户端摘要与配置/schema 兼容记录。
- 优先发布**更高产品版本号、恢复旧 Flet 依赖**的恢复包，沿用现有 AppId 与正常发布/签名流程；这需要重新构建、验证与发布，源码 revert 本身不能更新用户 EXE。
- 直接重装旧包仅在旧/新配置与数据库兼容、安装器允许且隔离 VM 已验证时使用；先退出应用并按既有流程备份，保留用户数据目录，不以卸载清库“恢复干净”。
- 必须在隔离 VM 验证“正常旧包→本次候选包→恢复包”，核对实际恢复版本、原生 UI、配置及已提交数据库哨兵。未测逆向路径则该恢复路径不可宣称可用。

### 7.4 独立优化

每项优化 PR 单独回退。输入主题回退同时恢复局部默认值；尺寸试点不重置用户宽度；关闭通知只停止订阅/外发，不改变业务任务；自选股重新加载撤销入口但保留原错误重试。

## 8. 风险登记册

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| R-1 | 默认 surface、Markdown 与局部覆盖出现视觉变化 | 待测 | 中 | UI 维护者负责 G0 基线、四主题比较与 §5.9/§5.14 验收 |
| R-2 | Web revision/字体/JS/WASM 混用或缺失 | 待测 | 高 | 测试维护者负责目标包清单、manifest、摘要与错配失败测试 |
| R-3 | ResponsiveRow/尺寸约束改变已有布局 | 待测 | 中 | UI 维护者区分正常包装与真实绕路，验证缩放/持久化 |
| R-4 | FastAPI 未真正升到候选版本，或错误归因 | 待测 | 中 | 依赖维护者负责 A/B 精确版本与完整解析差异，保留失败证据 |
| R-5 | Python 包可 import，但冻结客户端缺扩展/资源 | 待测 | 高 | 打包维护者负责候选 SHA 真制品与原生 UI，不接受 stub 代替 |
| R-6 | MCP 清单与实际 API/行为不一致 | 待测 | 中 | UI 维护者对照目标源码与运行，MCP 不作行为门禁 |
| R-7 | gzip/缓存/COEP/PNA 与离线拦截交互异常 | 待测 | 中 | 测试维护者检查实际头与资源，不把测试放宽项带入生产 |
| R-8 | nightly 仅监视锁定版 | 已知边界 | 低 | 升级后观察；latest 探测另行评估，不扩大本升级范围 |
| R-9 | 默认 Impeller 在特定 Windows/GPU 环境不兼容 | 待测，未观察到故障 | 高 | 打包/UI 维护者负责原生双后端验证、环境记录与制品恢复 |
| R-10 | cleanup/组件身份修复激活原先未执行的清理路径 | 待测 | 高 | UI 维护者负责 §5.12 的真实调度与取消/销毁计数 |
| R-11 | 重载被误当取新行情、重跑被误当刷新 | 已明确设计边界 | 中 | §5.3 限定首版；未来重跑须独立批准与 task_id 终态桥接 |
| R-12 | 已安装用户未随源码恢复，或恢复覆盖数据 | 待测 | 高 | 安装器维护者负责 §7 逆向验证与备份，不靠 git revert 完成恢复 |
| R-13 | 基础升级已合并，但优化全部停在候选或未记录收益 | 需执行约束 | 中 | UI/架构维护者以 G6 验收 opt-01–opt-03 和终局处置；整体状态不得仅随 G5 变为完成 |

## 9. 治理合规自查

| 项 | 结论 |
|----|------|
| 授权与 R18 | 本轮只改一份文档；无版本控制兜底，未初始化 Git。未来跨文件实施按 R18 决策树，安装/下载/数据库/发布另获授权 |
| R19 配套测试 | 依赖升级运行相应门禁；行为变化/优化必须配套单测。B 阶段采用项须有对应测试与收益证据；不采用须有试点依据，不能把未运行改记无收益 |
| GDR-06 | 本文为一次性流程方案，不新增豁免；受约束专题使用相对版本，具体历史核验只登记到既有豁免模板 |
| §1.10 反幻觉 | 固定 tag 源码是 API 证据，运行验证仍待执行；未把 API 存在、PyPI 发布或 MCP 请求等同应用通过 |
| R2/R7/R16/MVVM | 取消传播、测试隔离、异步 IO/线程池及纯 VM 边界不变；cleanup/通知/控件引用由合适生命周期持有 |
| R9/R10/R23 | 日志/工件脱敏、无明文密钥；颜色/字号/共享样式用现有 tokens，第三方组件不是自动违规 |
| R5/R17 | 当前文档修订不新增 DAO/维护实现或表列；未来若实际触及，重新按正本自查，不能沿用本次不适用结论 |
| R20/R21/R24 | 不改变策略算法/票池/时点；重新加载与通知不补假值、伪造终态或丢失可信度。未来改变业务结论时重新路由并独立复核 |
| PR 模板 | 创建 PR 使用仓库模板，禁止手写简化 body |
| 资产债务 | 承接已登记 blocked 条目，不把许可证/来源问题当不存在，不未经授权宣称闭合 |

## 10. 交付物与 DoD

### 10.1 A：可靠升级交付（G5）

**基础升级 PR（单个）**：
- pyproject.toml **五个** Flet 家族 pin（四主依赖 + flet-mcp），另有 CI/本地 flet-web pin；D-1 的 FastAPI 最终选择及注释。
- requirements 自动生成与实际安装版本证明；必要的 hooks/布局/导出适配与对应测试。
- 目标 E2E 资源及来源 manifest、许可证/NOTICE、配套校验与失败用例。
- 对应 SHA 的真实 Windows 产物、客户端身份、Impeller/Skia 与四主题证据、安装及恢复结果。
- API 历史核验登记、项目差异验证日期、旧规避说明与升级清单同步；不复制本次过程性评审报告进仓库。

**A 阶段合并前 DoD（G5）**：G0–G4 完成；§6 的 A 阶段必需项与升级清单全部有证据；D-1 通过或有理由保留；真实制品与恢复验证通过；CI 既有全量门禁通过。任何“未运行/无权限/来源 blocked”不得标为通过。

**合并后观察**：首轮锁定版 nightly 与用户可控渠道反馈确认无新回归；发现问题按 §7 处置。这是观察期，不补办合并前缺失的门禁。此时最多标记“A：可靠升级完成”，不能宣布整个升级与优化任务完成。

### 10.2 B：优化任务分级与依赖

这里的 A/B 是交付阶段，不是 D-1 的 FastAPI 对照环境；`opt-01` 等小写编号仅是本方案局部任务标识，不是治理 ID，也不复用 §3 的破坏性变更编号。G5 合并是下表共同前置，行顺序不是额外依赖。所有实施状态初始为“未执行”，下表定义未来任务而非声明已采纳。

| ID / 任务 | 级别与额外依赖 | 独立产物/完成条件 |
|---|---|---|
| opt-01 输入主题试点 | 必须试点并形成终局结论 | §5.14 的 theme + system_tab 实际试验；迁移范围、重复默认配置前后计数、四主题状态矩阵与采用/拒绝证据 |
| opt-02 尺寸边界试点 | 必须试点并形成终局结论 | §5.13 的同视口对照；越界/缩放问题前后结果、纯边界逻辑与必要测量/持久化逻辑清单，说明净收益而非仅新增属性 |
| opt-03 旧规避与重复实现清理 | 必须逐项核查，删除须证明等价 | 已使用的类型、ResponsiveRow、hooks、主题、Markdown、流式滚动规避清单；每条保留/替换/删除与测试；不为凑数量强删 |
| opt-04 自选股重新加载 | 优先增强，必须明确本轮处置 | 采用则 §5.3 的真实命令接线、双语/键盘与并发/取消/降级测试完成；排除则说明用户价值或交互证据 |
| opt-05 进度语义增强 | 优先增强，必须明确本轮处置 | 采用则 §5.6 的实际 DOM 与持久终态测试完成；排除须说明现有语义能力和收益边界，不能回避 A 阶段的兼容验证 |
| opt-06 客户端诊断信息 | 条件增强，依赖来源核验 | 采用则可选快照、客户端/产品身份区分及无页面导出测试完成；排除则说明诊断收益或运行能力不足 |
| opt-07 系统通知 | 条件增强，依赖产品批准与原生能力 | 采用则 §5.7 权限/去重/生命周期/隐私与失败隔离通过；不具备前置时明确排除本轮，不把未获批准视为插件已不支持 |
| opt-08 其他新增能力 | 必须记录适用性，不要求全部使用 | NEXT、密度、Markdown 全局主题、`page.run_task` 等按现有调用语义处置；shadcn/InteractiveViewer/移动构建等无当前收益者可明确排除并说明原因 |

纯布局/Markdown 去重纳入 opt-03，不重复另算收益。选股重跑入口仍明确排除首版，不能以“利用新能力”为由扩大业务副作用。

### 10.3 收益证据与不采纳规则

每项 B 任务由 UI 维护者负责，涉及任务/资源/打包边界时由相应维护者共同核验；证据附在对应任务/PR 的受控记录中，不另建重复治理正本或把评审报告复制入库。

**每项证据必须包含**：目标 API/修复、本项目调用点、试验前基线、实际改动或受控试验、相同条件下的试验后结果、保留/删除清单、测试命令与真实退出码、最终处置。当前未测的指标不得预填提升比例。

| 收益类别 | 可核对指标/判据 | 不能代替收益的内容 |
|---|---|---|
| 输入样式去重 | 同一属性集合/控件范围内重复默认赋值条目净减少，共享入口及覆盖控件可定位；四主题/局部覆盖行为不退化 | 只新增主题对象、任意删行或仅普通态截图相似 |
| 布局实现改善 | 相同视口/数据的具体越界或滞后案例修正，或纯布局边界重复计算减少；必要测量、持久化、折叠不变 | 给原容器再加一层约束却没有行为或维护收益 |
| 旧规避清理/修复利用 | 每条规避对应旧/新契约，撤除后回归通过；保留者有仍必要的依据；框架修复收益与项目代码简化分别记录 | 默认把上游所有修复都算本项目收益，或把 A 阶段测试通过重复计为 B 优化 |
| 交互/可访问性增强 | 明确新增入口、可读语义或终态断言及对应用户/DOM/状态用例；分别覆盖异常与取消 | 仅有新控件、瞬态 100% 或无测试的体验描述 |

**终局处置只有三类**：

1. **采用并完成**：实现/配置变更已合入，收益及回归证据成立；尚在试验分支不能标为已落地。
2. **验证后不采用**：opt-01/opt-02 必须有实际对照试点；opt-03 有逐条契约/调用链依据；增强项有明确产品或技术取舍。记录无收益、不兼容、维护成本或明确排除本轮的理由，不把未运行说成无收益。
3. **未完成/阻塞**：缺权限、环境、证据或仍待决定；说明阻塞点但保持未完成，不能以“以后评估”关闭必验任务。

不强制为了完成任务而采用无收益能力；不采用也须可复核。若 opt-01–opt-03 全部证明不宜改动且没有实现改善落地，应如实标记“评估已完成、优化目标未兑现”，由用户确认是否调整整体目标，不能自行宣布充分利用或为了数量制造改动。

### 10.4 整体完成门禁（G6）

- [ ] A 阶段 G5 已通过，观察期发现的相关回归已处理，不把未解决退化带入优化验收。
- [ ] opt-01、opt-02 已完成实际试点，opt-03 的已使用规避清单逐项核对；每项均有可复核的采用/拒绝结论，而非候选描述。
- [ ] opt-04–opt-08 均已明确“采用并完成”或“明确排除本轮及依据”；凡选择采用而尚未合入/验证者仍未完成。
- [ ] 已采用项有当前实现的实际收益与对应回归测试；高价值项不能用其他小功能代替核验，新增 API 数量或单纯删行不构成充分利用。
- [ ] 收益汇总区分上游自然修复、项目实现简化、交互增强与保留约束，未重复计数；无指标数据时不宣称性能提升。
- [ ] 必验任务不存在悬空阻塞；仅评估完成而优化目标未兑现时，未擅自降低目标或标记整体通过。

**状态表达**：A 通过但 G6 未通过＝“可靠升级完成，优化未完成”；A 与 G6 均通过＝“升级与能力优化整体完成”。分 PR、单独回退与“充分利用”目标同时成立，不要求使用全部新 API。

---

## 附录 A：关键命令速查

以下为 **Windows PowerShell**，从项目根目录执行。除纯文档检查外，其余均是未来实施命令，不在本次修订中执行。安装段须已获得授权并激活**新建目标 venv**，旧环境已按 G0 留存；不要把代码块当作可跳过各阶段前置条件的一键脚本。

### A.1 pin 与生成物

```powershell
Select-String -Path .\pyproject.toml, .\.github\workflows\ci_cd.yml, .\.github\workflows\flet-nightly.yml -Pattern 'flet.*==|fastapi<'
pre-commit run pip-compile-core --files pyproject.toml
pre-commit run pip-compile-dev --files pyproject.toml
pre-commit run pip-compile-optional --files pyproject.toml
```

三个 hook 分别检查日志；生成文件变化导致首次非零时先审查差异，再复跑至通过，不把解析失败也当作正常变化。D-1 最终解锁如需 `uv pip compile --upgrade-package fastapi`，沿用三个 hook 的 universal/extra 参数由工具生成，再运行 hook 核对；禁止直接编辑输出 pin。

### A.2 安装并断言初始对照环境

```powershell
uv pip install --python (Get-Command python).Source -r .\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Core dependency installation failed' }
uv pip install --python (Get-Command python).Source -r .\requirements-optional.txt
if ($LASTEXITCODE -ne 0) { throw 'Optional dependency installation failed' }
uv pip install --python (Get-Command python).Source -r .\requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Development dependency installation failed' }
uv pip install --python (Get-Command python).Source 'flet-web==1.1.0' 'fastapi==0.144.0'
if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed' }
@'
from importlib.metadata import version
from pathlib import Path
import sys
import flet_web

names = ('flet', 'flet-desktop', 'flet-charts', 'flet-code-editor', 'flet-mcp', 'flet-web')
versions = {name: version(name) for name in names}
print(versions)
assert sys.prefix != sys.base_prefix, 'Activate the isolated target venv'
assert set(versions.values()) == {'1.1.0'}, versions
assert Path(flet_web.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert version('fastapi') == '0.144.0'
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Version or environment assertion failed' }
uv pip check --python (Get-Command python).Source
if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies are inconsistent' }
```

A/B 实验与最终组合分别使用各自明确的 FastAPI 断言，不能套用上述 A 腿断言后省略 B 腿；`uv pip freeze` 的完整输出留作依赖证据并脱敏。

### A.3 读取实际 engineRevision

旧/新两个 venv 各执行并保留输出；不会修改资源：

```powershell
@'
from pathlib import Path
import re
import flet_web

bootstrap = Path(flet_web.__file__).parent / 'web' / 'flutter_bootstrap.js'
match = re.search(r'"engineRevision"\s*:\s*"([^"]+)"', bootstrap.read_text(encoding='utf-8'))
assert match is not None, 'engineRevision not found; inspect the target bootstrap format'
print(match.group(1))
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine engineRevision' }
```

资源下载/复制仍按阶段 3 的来源、目标清单与摘要批准，不提供覆盖仓库的递归通配符命令。字体脚本只在获准来源下运行，结束后仍检查 manifest，而非仅凭退出码判断来源合规。

### A.4 一次运行关联专项

```powershell
$compat = @(Get-ChildItem -LiteralPath .\tests\unit\ui -Filter 'test_flet_0_86_*.py' |
    Sort-Object Name | ForEach-Object { $_.FullName })
if ($compat.Count -eq 0) { throw 'No Flet compatibility tests found' }
$focused = @(
    '.\tests\unit\ui\test_hooks.py'
    '.\tests\unit\ui\test_toast_manager.py'
    '.\tests\unit\ui\test_flet_test_page_helpers.py'
)
$selected = $compat + $focused
python -m pytest @selected -v --tb=short
if ($LASTEXITCODE -ne 0) { throw 'Flet lifecycle or compatibility tests failed' }
```

不把含 `*` 的字符串直接交给 pytest；PowerShell 不为原生程序展开该 glob。资产测试在计划文件实际创建后运行；E2E 的 sidecar/测试数据库准备按 [testing.md](./testing.md)，不连接生产数据库。

### A.5 提交前门禁与纯文档最小子集

```powershell
ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff check failed' }
ruff format --check .
if ($LASTEXITCODE -ne 0) { throw 'Ruff format check failed' }
pre-commit run --all-files
if ($LASTEXITCODE -ne 0) { throw 'Pre-commit failed; inspect generated changes or errors' }
pyright
if ($LASTEXITCODE -ne 0) { throw 'Pyright failed' }
python -m pytest .\tests\unit\ -v --tb=short
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
```

仅本方案文档修订时，不运行上述业务全量门禁，使用正本规定的最小子集：

```powershell
python .\scripts\check_docs_consistency.py
if ($LASTEXITCODE -ne 0) { throw 'Documentation consistency check failed' }
python -m pytest .\tests\unit\test_docs_consistency.py -n 0 -q --tb=short
if ($LASTEXITCODE -ne 0) { throw 'Documentation contract tests failed or were blocked' }
```

### A.6 开发环境临时 Skia 对照

此段只验证开发入口；G4 仍需对清单登记的真实安装产物启动验证，不用 `main.py` 代替冻结产物。

```powershell
$hadRendererOverride = Test-Path Env:\FLET_NO_IMPELLER
$previousRendererOverride = $env:FLET_NO_IMPELLER
try {
    $env:FLET_NO_IMPELLER = 'true'
    python .\main.py
    if ($LASTEXITCODE -ne 0) { throw 'Skia comparison process failed' }
}
finally {
    if ($hadRendererOverride) {
        $env:FLET_NO_IMPELLER = $previousRendererOverride
    }
    else {
        Remove-Item Env:\FLET_NO_IMPELLER -ErrorAction SilentlyContinue
    }
}
```

## 附录 B：参考链接

- Flet v1.1.0 Release Notes：<[https://github.com/flet-dev/flet/releases/tag/v1.1.0](https://github.com/flet-dev/flet/releases/tag/v1.1.0)>
- 固定版本完整 Changelog（含 Improvements / Bug fixes）：<[https://github.com/flet-dev/flet/blob/v1.1.0/CHANGELOG.md](https://github.com/flet-dev/flet/blob/v1.1.0/CHANGELOG.md)>
- 默认主题中性化迁移指南：<[https://flet.dev/docs/updates/breaking-changes/v1-1-0/neutral-default-surfaces](https://flet.dev/docs/updates/breaking-changes/v1-1-0/neutral-default-surfaces)>
- RefreshIndicator 目标源码（含 show/刷新完成契约）：<[https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/material/refresh_indicator.py](https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/material/refresh_indicator.py)>
- 目标版 use_effect：<[https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/components/hooks/use_effect.py](https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/components/hooks/use_effect.py)>
- Windows 默认渲染器与回退入口：<[https://github.com/flet-dev/flet/blob/v1.1.0/client/windows/runner/main.cpp](https://github.com/flet-dev/flet/blob/v1.1.0/client/windows/runner/main.cpp)>
- Windows 环境变量真值解析：<[https://github.com/flet-dev/flet/blob/v1.1.0/client/windows/runner/utils.cpp](https://github.com/flet-dev/flet/blob/v1.1.0/client/windows/runner/utils.cpp)>
- LayoutControl 尺寸约束：<[https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/layout_control.py](https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/layout_control.py)>
- 输入主题与 ThemeSurfaces：<[https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/theme.py](https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/theme.py)>
- PackageInfo 来源说明：<[https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/package_info.py](https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/package_info.py)>
- 进度控件语义契约：<[https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/material/progress_bar.py](https://github.com/flet-dev/flet/blob/v1.1.0/sdk/python/packages/flet/src/flet/controls/material/progress_bar.py)>
- 项目升级流程正本：[../flet/upgrade-checklist.md](../flet/upgrade-checklist.md)
- 项目任务生命周期：[../patterns/task-manager.md](../patterns/task-manager.md)
- 已登记资产来源边界：[../debt/known-technical-debt.md](../debt/known-technical-debt.md)