# 依赖管理与 PyInstaller 打包

> 来源：从 CONTRIBUTING.md 迁移

### 依赖管理

- `flet` / `flet-desktop` / `flet-charts` / `flet-code-editor` 四个独立包，版本以 `==` 精确锁定（锁定值见 [`pyproject.toml`](../../pyproject.toml) 的 `dependencies` 中 `flet` / `flet-desktop` / `flet-charts` / `flet-code-editor` 四项）
- `flet-charts` 是 V1 拆分出的图表控件独立包，新增图表控件必须 `import flet_charts as fch`
- 版本锁定策略：`==` 精确锁定，避免 minor 版本间的 API 漂移（V1 已稳定发布，仍须精确锁定以规避 breaking changes）
- 升级 Flet 版本时，四个包必须同步升级

### 操作步骤

依赖增删/升级的完整流程为「只改 `pyproject.toml` → pre-commit 自动 compile `requirements*.txt` → 本地验证」，具体步骤见 [how-to.md「6. 新增与升级依赖」](../guides/how-to.md#6-新增与升级依赖)，此处摘要关键边界：

1. **只编辑 `pyproject.toml`**：运行时依赖加到 `[project] dependencies`，开发依赖加到 `[project.optional-dependencies] dev`，可选依赖加到 `[project.optional-dependencies] optional`。**禁止手改 `requirements*.txt`**。
2. **`requirements*.txt` 由 pre-commit 自动重新编译**：提交时本地 pre-commit 的三个 hook（`pip-compile-core` / `pip-compile-dev` / `pip-compile-optional`）会用 `uv pip compile` 同步生成 `requirements.txt` / `requirements-dev.txt` / `requirements-optional.txt`。需要提交前本地立即生效时可手动编译（见 how-to.md 对应小节，命令形如 `uv pip compile --universal --no-emit-index-url [--extra dev] pyproject.toml -o <output.txt>`）。
3. **本地验证**：在已激活的 `.venv` 内 `uv pip install -r requirements.txt -r requirements-dev.txt` 同步安装，随后跑 `ruff check .` + pyright + 相关测试；依赖安全由 CI 的 `scripts/run_pip_audit.py`（pip-audit）扫描三个 `requirements*.txt` 并按 `.security/audit-allowlist.yml` 白名单判定（详见 [ci-cd.md](./ci-cd.md) 的 Security Audit job）。

### PyInstaller 打包

[`AStockScreener.spec`](../../AStockScreener.spec) 的 `hiddenimports` 列表必须含 `flet` / `flet_desktop` / `flet_charts` / `flet_code_editor` 四项：

- `flet_charts` 是 V1 新增的独立模块，遗漏会导致打包产物 `import flet_charts` 报 ImportError
- `flet_core` / `flet_desktop` 在 V1 已合并入 `flet`，但保守保留 `flet_desktop` 以兼容桌面打包路径
- 新增 flet 相关 import 时，同步检查 spec 文件的 `hiddenimports` 是否覆盖

---

## 完成判定（canonical 入口）

- PyInstaller 打包按「PyInstaller 打包」章节执行，产物在目标平台启动验证通过
- 依赖增删已同步 `requirements*.txt`（pre-commit 自动）并通过依赖审计

_最小验证命令：_ 依赖变更 → 编辑 `pyproject.toml` → pre-commit 自动同步；打包 → 构建后运行 `main.py` 产物自检。
