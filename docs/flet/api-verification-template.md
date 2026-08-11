# Flet API 核验记录模板

> 用途：每次 Flet 版本升级或新增/变更 API 使用时，按本模板沉淀核验记录，作为升级证据。配合 [upgrade-checklist.md](./upgrade-checklist.md) 使用。
>
> 文档入口：[Flet 开发文档入口](./README.md)

> Owner: UI 维护者
> 复核触发器: Flet 版本升级、新增高风险 API 使用、API 行为异常排查

## 模板字段

每次核验按以下字段记录：

| 字段 | 说明 |
|------|------|
| **API** | 受核验的 Flet API（如 `ft.use_dialog`、`ft.Dropdown.on_select`、`use_viewmodel(factory=)`） |
| **锁定版本** | 核验时的 Flet 锁定版本（从 [`pyproject.toml`](../../pyproject.toml) 读取，不写补丁号漂移；写小版本+日期） |
| **核验来源** | 官方文档链接 / Flet issue / 项目运行期验证 / 项目单元测试 |
| **项目结论** | 在项目中的契约判定（继续使用 / 调整契约 / 暂禁用 / 待评估） |
| **需更新文件** | 核验结果波及的文件清单（`v1-api-constraints.md` / `project-differences.md` / `upgrade-checklist.md` / `ui/hooks.py` / `tests/unit/ui/*_contract.py` 等）。仅当新增、删除或改变专题职责时更新 [docs/flet/README.md](./README.md)。 |
| **核验日期** | YYYY-MM-DD |
| **核验人** | 核验执行者（GitHub 用户名或团队角色） |

## 核验记录

> 按「最新在上」顺序追加记录。每条记录使用以下子模板：

### 核验记录模板

```markdown
### <API 名> @ <锁定版本小版本> (<核验日期>)

- **API**: <API 签名/行为>
- **锁定版本**: Flet <major.minor>（pyproject.toml 实际锁定见文件）
- **核验来源**:
  - 官方文档: <URL>
  - Flet issue / PR: <URL 或 N/A>
  - 项目运行期验证: <验证步骤或测试名>
  - 项目单元测试: <测试文件::测试方法>
- **项目结论**: <继续使用 / 调整契约 / 暂禁用 / 待评估>
  - 理由: <为何得出此结论>
- **需更新文件**:
  - [ ] <文件路径>
  - [ ] <文件路径>
- **核验人**: <GitHub 用户名 / 团队角色>
```

## 历史核验记录

### engineRevision 修正 @ 0.86 最新补丁 (2026-08-07)

- **API**: CanvasKit engineRevision（非 Flet API，但影响 E2E 测试基础设施）
- **锁定版本**: Flet 0.86 系列最新补丁（pyproject.toml 实际锁定见文件）
- **核验来源**:
  - 项目运行期验证: 读取 `site-packages/flet_web/web/flutter_bootstrap.js` 的 `_flutter.buildConfig.engineRevision`，实际值为 `0cd610717bde95fd88343c64f81c11ba4e5c0010`
- **项目结论**: 需追加复验
  - 理由: 下方 2026-07-27 记录声称"engineRevision 未变化（`a10d8ac38de835021c8d2f920dbf50a920ccc030`）"，该值是 0.86.2 的 engineRevision。0.86.3 升级后 engineRevision 已变更为 `0cd610717bde95fd88343c64f81c11ba4e5c0010`，表明 0.86.2→0.86.3 跨越了一次 Flutter engine 升级。需按 [upgrade-checklist.md §3.9](./upgrade-checklist.md#39-canvaskit-语义树行为验证flet-升级必查) 验证 CanvasKit 语义树行为是否漂移。
- **需更新文件**:
  - [x] docs/flet/canvaskit-rendering-e2e-guide.md（去除版本硬编码，改为以 pyproject.toml 为准）
  - [x] docs/flet/upgrade-checklist.md（新增 §3.9 CanvasKit 语义树行为验证）
  - [x] docs/debt/known-technical-debt.md（P3-WinE2E-Skip 条目追加 engineRevision 变更说明）
- **核验人**: AI 助手 (GLM-5.2)

### Flet 0.86 升级核验 @ 0.86 最新补丁 (2026-07-27)

- **API**: 全量 V1 声明式 API + 私有 API + flet_charts API
- **锁定版本**: Flet 0.86 系列最新补丁（pyproject.toml 实际锁定见文件）
- **核验来源**:
  - 官方文档: https://github.com/flet-dev/flet/releases + PyPI JSON API（2026-07-26 发布最新补丁）
  - Flet issue / PR: #6709/#6710 (MatplotlibChart 切 tab 冻结修复，本项目未用 MatplotlibChart 故不相关)
  - 项目运行期验证: engineRevision 比对（前一补丁→最新补丁均为 `a10d8ac38de835021c8d2f920dbf50a920ccc030`，未变化）；`python scripts/sync_e2e_fonts.py` 输出 `[OK] 字体缓存完整`（103 字体，0 失败）；flet-mcp（与主包同步锁定版本）`get_api` 查询 `CandlestickChart`/`AlertDialog`/`use_dialog` 均返回完整 API
  - 项目单元测试:
    - tests/unit/ui/test_flet_0_86_v1_api_compat.py (42 tests passed in 20.25s)
    - tests/unit/ui/test_flet_0_86_private_api_compat.py
    - tests/unit/ui/test_flet_0_86_charts_compat.py
- **项目结论**: 继续使用
  - 理由: 0.86 系列内 patch 升级（无 breaking change，Flutter 引擎 3.44.7 未变，engineRevision 未变化），CanvasKit/字体资源无需更新；三套兼容性测试 42 项全部通过；flet-mcp 与主包同步发布，API 覆盖度验证通过；6 个 bug fix 中仅模态同帧关闭修复（#6 "setState() called during build"）与项目 AlertDialog+use_dialog 路径理论相关，E2E 冒烟待 CI 验证；ruff/pyright 0 errors
- **需更新文件**:
  - [x] pyproject.toml (四包版本号同步升级至 0.86 系列最新补丁: flet/flet-desktop/flet-charts/flet-mcp)
  - [x] requirements.txt / requirements-optional.txt / requirements-dev.txt (uv pip compile 重新生成)
  - [x] .github/workflows/ci_cd.yml (flet-web 同步升级至 0.86 系列最新补丁, 3 处)
  - [x] README.md (徽章同步更新)
  - [x] docs/flet/project-differences.md (最后验证日期 2026-07-23→2026-07-27)
  - [x] docs/flet/api-verification-template.md (本核验记录)
  - [x] docs/debt/known-technical-debt.md (engineRevision 未变，CanvasKit 行为预期不变，无需追加复验)
- **核验人**: AI 助手 (GLM-5.2)

### Flet 0.86 升级核验 @ 0.86 (2026-07-23)

- **API**: 全量 V1 声明式 API + 私有 API + flet_charts API
- **锁定版本**: Flet 0.86（pyproject.toml 实际锁定补丁号见文件）
- **核验来源**:
  - 官方文档: https://github.com/flet-dev/flet/blob/main/CHANGELOG.md
  - 项目运行期验证: engineRevision 比对（升级前后均为 `a10d8ac38de835021c8d2f920dbf50a920ccc030`，未变化）；字体 URL 版本 `notosanssc/v37/` 未变化
  - 项目单元测试:
    - tests/unit/ui/test_flet_0_86_v1_api_compat.py (42 tests passed)
    - tests/unit/ui/test_flet_0_86_private_api_compat.py
    - tests/unit/ui/test_flet_0_86_charts_compat.py
- **项目结论**: 继续使用
  - 理由: 本次为 patch 升级（Flutter bugfix），engineRevision 未变化，CanvasKit/字体资源无需更新；三套兼容性测试 42 项全部通过；无破坏性 API 变更
- **需更新文件**:
  - [x] pyproject.toml (三包版本号同步升级)
  - [x] requirements.txt / requirements-optional.txt / requirements-dev.txt (uv pip compile 重新生成)
  - [x] README.md (徽章同步更新)
  - [x] ui/views/screener_view.py (注释泛化为 0.86+)
  - [x] docs/flet/project-differences.md (最后验证日期 2026-07-20→2026-07-23)
  - [x] docs/flet/api-verification-template.md (本核验记录)
  - [x] docs/debt/known-technical-debt.md (3 项技术债复验状态更新)
- **核验人**: AI 助手 (GLM-5.2)

### Flet 0.86 升级核验 @ 0.86 (2026-07-20)

- **API**: 全量 V1 声明式 API + 私有 API + flet_charts API
- **锁定版本**: Flet 0.86（pyproject.toml 实际锁定补丁号见文件）
- **核验来源**:
  - 官方文档: https://github.com/flet-dev/flet/blob/main/CHANGELOG.md
  - Flet issue / PR: #6680, #6606, #6684, #6686
  - 项目运行期验证: 启动应用 + Dialog/Dropdown/use_effect/use_viewmodel 关键路径
  - 项目单元测试:
    - tests/unit/ui/test_flet_0_86_v1_api_compat.py
    - tests/unit/ui/test_flet_0_86_private_api_compat.py
    - tests/unit/ui/test_flet_0_86_charts_compat.py
- **项目结论**: 继续使用
  - 理由: 0.86 系列最新补丁版本是纯 bugfix，无破坏性 API 变更；三套兼容性测试全部通过；项目无 BasePage / allowed_devices 使用，bugfix 不影响现有代码
- **需更新文件**:
  - [x] pyproject.toml (三包版本号同步升级至最新补丁)
  - [x] requirements.txt / requirements-optional.txt / requirements-dev.txt (pre-commit 自动重新生成)
  - [x] docs/flet/project-differences.md (最后验证日期)
  - [x] docs/flet/api-verification-template.md (本核验记录)
- **核验人**: AI 助手 (GLM-5.2) + 项目维护者

## 引用关系

- [v1-api-constraints.md](./v1-api-constraints.md): 声明式组件内 API 契约与 V0→V1 迁移表（契约定义源）
- [project-differences.md](./project-differences.md): 项目验证过的高风险 API（历史验证结果沉淀）
- [upgrade-checklist.md](./upgrade-checklist.md): 升级时的验证步骤（本模板的触发场景）
- [CLAUDE.md §3.1 R16](../../CLAUDE.md#31--绝对禁止): UI 阻塞红线（涉及 async API 时必查）
