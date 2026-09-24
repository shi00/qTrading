# CI/CD 流水线与门禁

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.2（pre-commit、Alembic 迁移、质量门控强制）；实现细则以本节为准。

### 三层门禁区分

修改代码后按下表选择对应门禁层级，避免全量跑浪费时间或漏跑：

| 层级 | 触发场景 | 命令/Job |
|------|---------|----------|
| **本地最小门禁** | 每次小改动后自检 | `ruff check .` + `ruff format --check .` + 变更相关测试 |
| **变更相关门禁** | 提交前按变更范围自检 | 按 [变更类型 → 最小验证子集](../../CONTRIBUTING.md#变更类型--最小验证子集) 选择 |
| **CI 全量门禁** | 推送 / PR / 跨层修改 | `ruff`（前置 `lint-fast` job）→ `pre-commit` → 版本一致 → 安全扫描（pip-audit）→ `pyright` → weak assertions → 迁移一致性（`upgrade head`/`check`/`downgrade base`/`upgrade head`）→ ORM 一致性 → 单测 → 集成 → per-file 覆盖率（≥80% 强制 + 分层 advisory）→ diff coverage（strict ≥80%）→ Windows e2e |

### CI Job 矩阵

GitHub Actions 双平台验证 (`.github/workflows/ci_cd.yml`)，PR/主干质量门禁包括：

1. **Fast Ruff Check & Format** (`lint-fast` job)：matrix 含 Python `3.13` 与 `3.14`，其中 `3.14` 标记 `experimental: true` 并 `continue-on-error`（**Python 3.14 仅在 `lint-fast` job 中作为 experimental 矩阵项运行**，仅跑 `ruff check` + `ruff format --check`，不安装项目依赖）
2. **Pre-commit Hooks** (Ruff、格式化、裸 `type: ignore`、requirements 同步、版本一致性、文档一致性、红线自动化、import-linter 架构守护；hook 数量见 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml))
3. **Security Audit** (`scripts/run_pip_audit.py`，扫描 `requirements.txt`、`requirements-optional.txt`、`requirements-dev.txt`，使用 `.security/audit-allowlist.yml`)
4. **Pyright Type Check** (版本见 `ci_cd.yml`，`continue-on-error: false`)
5. **Alembic Migration** (`upgrade head` → `alembic check` → `downgrade base` → `upgrade head`)
6. **Unit & Integration Tests** (Linux/Windows unit，Linux integration；完整测试矩阵仅 Python `3.13`)
7. **Windows E2E Tests** (`tests/e2e/`，Chromium + PostgreSQL)
8. **Per-File (≥ 80%) + Diff Coverage (≥ 80%)（CI 强制）**；分层单文件阈值 services/strategies/data ≥ 90%、ui ≥ 85%（advisory 报告模式，不阻断，见 [testing.md](./testing.md)）；**Overall ≥ 85% 为本地目标**（`pyproject.toml` `fail_under=85` 未在 CI 路径启用）(覆盖率阈值见 [`pyproject.toml`](../../pyproject.toml))
9. **requirements*.txt 漂移处理** (`requirements-drift` job 检测到 main 分支漂移时，由 `update-requirements` job 创建同步 PR)

> **Python 3.14 状态说明**：完整测试矩阵（Code Quality & Tests、Windows E2E、Windows Build 等）仅运行 Python `3.13`（稳定基线）。Python `3.14` 当前依赖已支持（`litellm>=1.101.0` 的 `Requires-Python` 为 `<3.15, >=3.10`），`lint-fast` job 将 `3.14` 作为 experimental 矩阵项前瞻验证（仅跑 `ruff check` + `ruff format --check`，不安装项目依赖；矩阵配置见 [已知架构技术债](../debt/known-technical-debt.md) 相关条目）。

### 版本发布流程与 Release 管理

发布流程: 打 `v*.*.*` tag → 触发 `build-windows` job → PyInstaller 打包 CPU/CUDA 两个变体 → smoke test → Inno Setup 制作安装包 → GitHub Release 发布。

**其他 workflow**（`.github/workflows/` 下除 `ci_cd.yml` 主矩阵外的兜底流水线，由 `check_workflow_enum()` 门禁守护无遗漏）:
- 前端流水线：文档 CI (`docs-ci.yml`)、Flet 前瞻验证 (`flet-nightly.yml`)、PostgreSQL sidecar Release (`sidecar.yml`)
- 安全流水线：CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)
- 依赖与发布：依赖更新机器人（自托管 Renovate workflow (`renovate.yml`)，每周一 02:00 UTC 定时 + 手动触发，配置见 [`.github/renovate.json`](../../.github/renovate.json)；Python/JS 依赖由其接管，GitHub Actions 生态由 [`.github/dependabot.yml`](../../.github/dependabot.yml) 承接，分工在两侧配置注释中声明。Python 漏洞另由 CI `run_pip_audit.py` 扫描）、自动化 Release PR (`release-please.yml`)

### Pre-commit Hooks

本项目使用 pre-commit hooks，定义在 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)，本地 `repo: local` 提供以下 16 个 hook（提交前必须全部通过）：

- 代码质量：`ruff-check` · `ruff-format` · `type-ignore-reason` · `no-isolated-asyncio-testcase` · `no-temp-files`
- 增量检查：`pyright-changed` · `weak-assertion-changed` · `e2e-anchor-check` · `theme-contrast-check` · `lint-imports`
- 依赖与一致性：`pip-compile-core` · `pip-compile-dev` · `pip-compile-optional` · `verify-versions` · `docs-consistency` · `redline-check`

> hook 数量与名称以 `.pre-commit-config.yaml` 为唯一正本，本清单由 `check_precommit_hook_names()` 门禁守护名称级一致（配置新增/删除 hook 时须同步本枚举）。

### 工程脚本清单

`scripts/*.py` 门禁/工具脚本总览（新增脚本须在本表登记，由 `check_scripts_index_completeness()` 门禁守护）：

| 脚本 | 用途 | 触发时机 | 是否阻断 |
|------|------|---------|---------|
| `scripts/check_docs_consistency.py` | 文档一致性门禁（锚点/相对链接/版本/规则集/ADR/脚本索引等 30+ 项） | pre-commit `docs-consistency` + CI | 阻断 |
| `scripts/check_redlines.py` | 红线自动化（R4/R12/R13/R14/R15/R16+R20/R21/R22+UI 裸色+token 脱敏；R20/R21 报告模式 warning 不阻断） | pre-commit `redline-check` | 阻断 |
| `scripts/check_type_ignore_reason.py` | R3 裸 `type: ignore` 原因检查 | pre-commit `type-ignore-reason` | 阻断 |
| `scripts/check_no_isolated_asyncio.py` | 禁止 `unittest.IsolatedAsyncioTestCase` | pre-commit `no-isolated-asyncio-testcase` | 阻断 |
| `scripts/check_no_temp_files.py` | 拦截编译/二进制/归档临时文件入库 | pre-commit `no-temp-files` | 阻断 |
| `scripts/check_staged_weak_assertions.py` | staged 测试文件弱断言增量阻断 | pre-commit `weak-assertion-changed` | 阻断 |
| `scripts/check_e2e_anchors.py` | E2E anchor ID 引用合法性 | pre-commit `e2e-anchor-check` | 阻断 |
| `scripts/check_theme_contrast.py` | WCAG 2.1 §1.4.3 对比度门禁 | pre-commit `theme-contrast-check` | 阻断 |
| `scripts/run_pyright_changed.py` | staged `.py` 跑 pyright（error 阻断） | pre-commit `pyright-changed` | 阻断 |
| `scripts/verify_versions.py` | 项目版本一致性校验 | pre-commit `verify-versions` + CI | 阻断 |
| `scripts/run_pip_audit.py` | 依赖安全审计（pip-audit + allowlist） | CI Security Audit | 阻断 |
| `scripts/scan_weak_assertions.py` | CI 弱断言全量扫描 | CI weak assertions step | 阻断 |
| `scripts/check_diff_coverage.py` | PR 变更行覆盖率（R19 diff-coverage） | CI `check_diff_coverage`（strict 80%） | 阻断 |
| `scripts/check_per_file_coverage.py` | 单文件覆盖率（分层阈值 D37） | CI coverage step（`--report` 为 advisory） | 阻断（`--report` 不阻断） |
| `scripts/detect_flaky.py` | 重复运行 pytest 定位 flaky 测试 nodeid | CI（报告）+ 手动 | 不阻断 |
| `scripts/generate_sidecar_manifest.py` | qtrading-pg-sidecar Release manifest 生成 | CI `sidecar.yml` | 阻断（release 时） |
| `scripts/prototype_business_redlines.py` | R20/R21/R22 AST 可行性原型（人工评审辅助） | 手动 | 不阻断 |
| `scripts/verify_embedded_pg_connect.py` | embedded PG sidecar 连通性验证（一次性 spike） | 手动/维护 | 不阻断 |
| `scripts/check_doctor_schema.py` | Doctor JSON schema 与 Rust sidecar/Python 服务一致性 | 手动/维护 | 不阻断 |
| `scripts/check_failure_injection_coverage.py` | 故障注入覆盖率交叉校验 | 手动/报告 | 不阻断 |
| `scripts/sync_e2e_fonts.py` | E2E 缓存字体同步（资产准备） | 手动 | 不阻断 |
| `scripts/safe_cleanup_branches.py` | 分支安全清理 | 手动 | 不阻断 |
| `scripts/migrate_strategy_name_to_i18n_key.py` | 历史 strategy_name 迁移为 i18n key（一次性 DB 迁移） | 手动 | 不阻断 |

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链

---

## 完成判定（canonical 入口）

- CI Job / pre-commit hook 增删已回填本文件与 `.pre-commit-config.yaml` / workflow 清单，名称级一致（避免枚举漂移）
- 版本发布流程按「版本发布流程与 Release 管理」执行，release 产物验证通过

_最小验证命令：_ CI/依赖变更 → 编辑 `pyproject.toml`（pre-commit 自动同步 requirements）或 workflow → 由 CI 验证；
        文档改动 → `python scripts/check_docs_consistency.py`。
