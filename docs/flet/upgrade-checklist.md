# Flet V1 升级检查清单

> 来源：从 man/flet-best-practices.md 迁移

> Owner: UI 维护者
> 复核触发器: Flet 依赖版本变化（pyproject.toml）、关键 API 变化、架构红线/边界变化或 ADR 决策（见 [../adr/](../adr/)）

Flet 版本升级时，按以下清单逐项验证。每项验证结果建议记录到 [api-verification-template.md](./api-verification-template.md) 模板中，作为升级证据沉淀。

## 1. API 兼容性检查

- [ ] `ft.use_dialog()` 签名与行为（声明式 Dialog 唯一契约，见 [project-differences.md §4.1](./project-differences.md#41-ftuse_dialog声明式组件内唯一-dialog-契约)）
- [ ] `ft.Dropdown.on_select` 事件名（项目统一用 `on_select`，非 `on_change`，见 [project-differences.md §4.2](./project-differences.md#42-dropdown-on_select非-on_change)）
- [ ] `use_effect` 的 `cleanup=` 关键字参数（非 setup 返回值，见 [project-differences.md §4.3](./project-differences.md#43-use_effect-cleanup-显式参数)）
- [ ] `use_viewmodel(factory=)` / `use_viewmodel(vm=)` 双模式互斥（见 [`ui/hooks.py`](../../ui/hooks.py)）
- [ ] `ft.Router` / `ft.Route` 声明式路由
- [ ] `DialogControl` 子类清单（`AlertDialog`/`DatePicker`/`TimePicker`/`SnackBar`/`Banner`/`BottomSheet`）
- [ ] V0→V1 迁移 API 表全部 21 项（见 [v1-api-constraints.md §V0→V1 迁移 API 表](./v1-api-constraints.md#v0v1-迁移-api-表)）
- [ ] `flet-mcp` 可用性验证（`venv\Scripts\fastmcp.exe run flet_mcp:mcp` 能启动，见 [mcp-usage.md](./mcp-usage.md)）；flet-mcp 版本与 flet 主包版本对齐

## 2. 兼容性测试与文档检查

```bash
# Flet 兼容性测试（从项目根目录运行）
python -m pytest tests/unit/ui/test_flet_0_86_*.py -v

# 文档一致性检查
python scripts/check_docs_consistency.py
```

## 3. E2E 离线资源检查

Flet 升级时 `flet_web/web/main.dart.js` 中硬编码的字体 URL 可能随之变化，需验证 `tests/e2e/mock_assets/fonts/` 本地缓存是否继续有效。未验证会导致 E2E 测试确定性失败（字体分片不匹配 → 表格布局塌陷 → 行语义节点缺失 → 断言超时）。

### 3.1 字体 URL 版本验证

```powershell
# <site-packages> 替换为实际 Python 环境 site-packages 路径
Select-String -Path "<site-packages>/flet_web/web/main.dart.js" `
  -Pattern "notosanssc/v\d+/" -AllMatches |
  ForEach-Object { $_.Matches } | Select-Object -ExpandProperty Value -Unique
```

判定：
- 输出 `notosanssc/v37/` → 本地缓存继续有效，无需重下
- 输出其他版本号 → URL 变了，按 3.2 重新捕获并下载

### 3.2 字体分片重新下载（仅 URL 变化时执行）

```bash
# 1. 捕获实际请求的字体 URL（PowerShell；从 flet_web 包内 main.dart.js 提取）
Select-String -Path "<site-packages>/flet_web/web/main.dart.js" `
  -Pattern "notosanssc/v\d+/" -AllMatches |
  ForEach-Object { $_.Matches } | Select-Object -ExpandProperty Value -Unique

# 2. 按捕获的 URL 下载 woff2 分片到 tests/e2e/mock_assets/fonts/
#    （Noto Sans SC 通常 7 个分片 + Roboto 1 个，约 278KB）

# 3. 重新验证 E2E 表格依赖测试
python -m pytest tests/e2e/test_screener_flow.py -v -n 1 --timeout=180
```

### 3.3 CanvasKit 版本验证

> 背景：Flet web app 启动时从 `https://www.gstatic.com/flutter-canvaskit/<engineRevision>/` 加载 `canvaskit.js` 与 `canvaskit.wasm`。E2E 测试通过 [tests/e2e/conftest.py](../../tests/e2e/conftest.py) 的 `intercept_external` 拦截该请求并从 `tests/e2e/mock_assets/canvaskit/` 提供本地文件。canvaskit 版本由 Flutter `engineRevision` 决定（见 `site-packages/flet_web/web/flutter_bootstrap.js` 的 `_flutter.buildConfig`），与 Flet Python 层版本无直接关系——同 minor 版本的不同 patch 可能共用相同 engineRevision（如 0.86.0 与 0.86.1），跨 minor 版本通常不同（如 0.85.3 → 0.86.0）。

- [ ] **比较 engineRevision**：对比升级前后的 `flutter_bootstrap.js` 中 `_flutter.buildConfig.engineRevision` 字段
  - 升级前：从当前 `pyproject.toml` 锁定版本的 `flet_web` wheel 中读取
  - 升级后：从新安装的 `site-packages/flet_web/web/flutter_bootstrap.js` 读取
  - 命令示例：`python -c "import re,pathlib; p=pathlib.Path(__import__('flet_web').__file__).parent/'web/flutter_bootstrap.js'; print(re.findall(r'engineRevision\\":\\"([^"]+)\\"', p.read_text(encoding='utf-8')))"`
- [ ] **若 engineRevision 变化**：从新版本 `site-packages/flet_web/web/canvaskit/` 复制 `canvaskit.js` 和 `canvaskit.wasm` 到 `tests/e2e/mock_assets/canvaskit/`
  - 复制命令：`cp <site-packages>/flet_web/web/canvaskit/canvaskit.{js,wasm} tests/e2e/mock_assets/canvaskit/`
  - 验证文件大小变化（确认复制成功）
- [ ] **若 engineRevision 未变化**：跳过文件复制，但在升级 PR 描述中记录"engineRevision 未变，canvaskit 资源无需更新"
- [ ] **运行 E2E 冒烟测试**（若本地环境支持）：验证 canvaskit 加载无回归

## 4. 项目验证步骤

- [ ] 运行 `ruff check .` → `ruff format --check .` → `pyright`
- [ ] 运行 `python -m pytest tests/unit/ -v --tb=short`
- [ ] 启动应用，验证 Dialog / Dropdown / use_effect / use_viewmodel 关键路径
- [ ] 更新 [project-differences.md](./project-differences.md) 「最后验证日期」
- [ ] 在 [api-verification-template.md](./api-verification-template.md) 中登记本次升级的核验记录

## 5. 文档同步

- [ ] 检查 [CLAUDE.md](../../CLAUDE.md) 中 Flet 版本引用
- [ ] 检查 [CONTRIBUTING.md](../../CONTRIBUTING.md) Flet V1 章节入口索引
- [ ] 检查 [v1-api-constraints.md](./v1-api-constraints.md) 中 API 约束
- [ ] 检查 [project-differences.md](./project-differences.md) 中项目分叉与高风险 API
- [ ] 更新 [project-differences.md](./project-differences.md) 「最后验证日期」

## 6. 官方文档链接

- Flet 官方文档：<https://docs.flet.dev/>
- Flet Changelog：<https://github.com/flet-dev/flet/blob/main/CHANGELOG.md>
- Flet GitHub 仓库：<https://github.com/flet-dev/flet>

> 通用 Flet v1 教程（路由、Services、存储、构建打包、移动/Web 适配、响应式布局、控件清单等）请直接查阅官方文档，本文件不再复制，避免与上游漂移。

