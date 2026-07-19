# CI/CD 流水线与门禁

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.2（pre-commit、Alembic 迁移、质量门控强制）；实现细则以本节为准。

### 三层门禁区分

修改代码后按下表选择对应门禁层级，避免全量跑浪费时间或漏跑：

| 层级 | 触发场景 | 命令/Job |
|------|---------|----------|
| **本地最小门禁** | 每次小改动后自检 | `ruff check .` + `ruff format --check .` + 变更相关测试 |
| **变更相关门禁** | 提交前按变更范围自检 | 按 [变更类型 → 最小验证子集](../../CONTRIBUTING.md#变更类型--最小验证子集) 选择 |
| **CI 全量门禁** | 推送 / PR / 跨层修改 | `ruff` → `format` → `pre-commit` → `pyright` → `pytest` → 安全扫描 → 迁移一致性 → integration/e2e |

### CI Job 矩阵

GitHub Actions 双平台验证 (`.github/workflows/ci_cd.yml`)，PR/主干质量门禁包括：

1. **Fast Ruff Check & Format** (`lint-fast` job)：matrix 含 Python `3.13` 与 `3.14`，其中 `3.14` 标记 `experimental: true` 并 `continue-on-error`（**Python 3.14 仅在 `lint-fast` job 中作为 experimental 矩阵项运行**，仅跑 `ruff check` + `ruff format --check`，不安装项目依赖）
2. **Pre-commit Hooks** (Ruff、格式化、裸 `type: ignore`、requirements 同步、版本一致性、文档一致性、红线自动化、import-linter 架构守护；hook 数量见 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml))
3. **Security Audit** (`scripts/run_pip_audit.py`，扫描 `requirements.txt`、`requirements-optional.txt`、`requirements-dev.txt`，使用 `.security/audit-allowlist.yml`)
4. **Pyright Type Check** (版本见 `ci_cd.yml`，`continue-on-error: false`)
5. **Alembic Migration** (`upgrade head` → `alembic check` → `downgrade base` → `upgrade head`)
6. **Unit & Integration Tests** (Linux/Windows unit，Linux integration；完整测试矩阵仅 Python `3.13`)
7. **Windows E2E Tests** (`tests/e2e/`，Chromium + PostgreSQL)
8. **Per-File (≥ 80%) & Overall Coverage (≥ 85%)** (覆盖率阈值见 [`pyproject.toml`](../../pyproject.toml))
9. **requirements*.txt 漂移处理** (`requirements-drift` job 检测到 main 分支漂移时，由 `update-requirements` job 创建同步 PR)

> **Python 3.14 状态说明**：完整测试矩阵（Code Quality & Tests、Windows E2E、Windows Build 等）仅运行 Python `3.13`。Python `3.14` 因上游 `litellm` 限制 `Requires-Python <3.14`，暂时无法安装项目依赖，故仅在 `lint-fast` job 中作为 experimental 矩阵项运行（详见 [已知架构技术债](../debt/known-technical-debt.md) 中的 litellm 限制条目）。

发布流程: 打 `v*.*.*` tag → 触发 `build-windows` job → PyInstaller 打包 CPU/CUDA 两个变体 → smoke test → Inno Setup 制作安装包 → GitHub Release 发布。

**其他 workflow**: CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、自动化 Release PR (`release-please.yml`)、依赖更新机器人 (`renovate.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)。

### Pre-commit Hooks

本项目使用 pre-commit hooks (定义在 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)，含 Ruff lint/format、裸 `type: ignore` 检测、禁止 `IsolatedAsyncioTestCase`、requirements 同步、版本一致性校验、文档一致性校验、红线自动化校验、import-linter 架构守护)。hook 数量见 `.pre-commit-config.yaml`，提交前必须全部通过。

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链
