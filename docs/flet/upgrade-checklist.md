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
- [ ] `flet-mcp` 可用性验证（`venv/Scripts/python.exe -c "from flet_mcp import mcp; mcp.run()"` 能启动，见 [mcp-usage.md](./mcp-usage.md)）；flet-mcp 版本与 flet 主包版本对齐

## 2. 兼容性测试与文档检查

```bash
# Flet 兼容性测试（从项目根目录运行）
python -m pytest tests/unit/ui/test_flet_0_86_*.py -v

# 文档一致性检查
python scripts/check_docs_consistency.py
```

## 3. E2E 离线资源检查

Flet 升级时 `flet_web/web/main.dart.js` 中硬编码的字体 URL 可能随之变化，需验证 `tests/e2e/mock_assets/fonts/` 本地缓存是否继续有效。未验证会导致 E2E 测试确定性失败（字体分片不匹配 → CJK 文本节点不生成 → Playwright 等待中文文本超时 → 整批 E2E 失败）。

**背景**：`main.dart.js` 中硬编码了所有 Noto Sans 变体的字体注册（`A.m("FontName", "relpath")`），其中 `notosanssc` 有 ~100 个分片（按当前 flet 版本统计，跨版本可能变化，按 Unicode 范围切分），CanvasKit 按需加载应用实际渲染字符对应的分片。应用 locale=zh 只触发 `notosanssc`，但不同 UI 路径渲染不同字符 → 触发不同分片子集 → 缓存不完整时 CI 偶发失败。

### 3.1 字体缓存完整性验证（一键命令）

```bash
# 从项目根目录运行（需在 venv 中，且 flet_web 已安装）
python scripts/sync_e2e_fonts.py

# 强制重新下载所有字体分片（即使本地已存在），用于：
#   - 怀疑本地缓存文件损坏（如 git LFS/CRLF 误处理、磁盘错误）
#   - main.dart.js 中字体 hash 变化但旧文件名巧合相同（极罕见）
python scripts/sync_e2e_fonts.py --force
```

脚本行为：
1. 定位 `flet_web/web/main.dart.js`（自动搜索 venv site-packages 与 user site-packages）
2. 解析其中所有 `notosanssc` + `roboto` + `notosans` 字体分片相对路径
3. 批量下载缺失分片到 `tests/e2e/mock_assets/fonts/`（幂等：已存在跳过，`--force` 例外）
4. 原子写入：通过临时文件 + `os.replace` 保证 dest 不会半写入
5. 校验完整性：本地缓存文件名集合必须覆盖解析的全部 URL

判定：
- 输出 `[OK] 字体缓存完整` → 缓存有效，无需额外操作
- 输出 `[ERROR] 仍有 N 个字体分片缺失` → 网络问题导致部分下载失败，重试脚本
- 脚本异常报 `flet_web 包未安装` → 先运行任意 E2E 用例触发 flet 自动安装，或手动 `pip install flet-web==<flet-version>`

### 3.2 启动期断言（自动生效）

`tests/e2e/conftest.py` 的 `e2e_browser` fixture 在 session 启动时自动调用 `tests/e2e/_font_urls.py::find_missing_fonts()` 校验缓存完整性。若缺失任何分片，fixture fail-fast 并提示运行 `sync_e2e_fonts.py`，避免 E2E 测试因字体未命中而偶发超时。

### 3.3 何时需要重新同步字体

- **flet 主版本/次版本升级**（如 0.86.x → 0.87.x）：`main.dart.js` 中字体 URL hash 或版本号可能变化 → 必须重新运行 `sync_e2e_fonts.py`
- **flet patch 版本升级**（如 0.86.x → 0.86.x+1）：通常 `main.dart.js` 不变，但建议运行脚本验证（幂等，已存在文件跳过）
- **应用新增 locale 支持**（如增加日文/韩文）：需在 `tests/e2e/_font_urls.py::REQUIRED_FONT_FAMILIES` 中补充对应字体族（如 `notosansjp`），再运行 `python scripts/sync_e2e_fonts.py` 同步新字体分片，将 `tests/e2e/mock_assets/fonts/` 下新增文件提交后推送

> 注意：sync 脚本必须在 E2E 运行的同一 venv 中执行，确保解析到的 `flet_web` 与 E2E 实际使用的版本一致。

### 3.4 CanvasKit 版本验证

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

