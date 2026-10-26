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
8. **Per-File (≥ 80%) + Diff Coverage (≥ 80%)（CI 强制）**；分层单文件阈值 services/strategies/data ≥ 90%、ui ≥ 85%（advisory 报告模式，不阻断，见 [testing.md](./testing.md)）；**Overall ≥ 85% 同样在 CI 路径强制**（`pyproject.toml` `[tool.coverage.report]` `fail_under=85`：CI 的 `pytest --cov` 未显式传 `--cov-fail-under`，pytest-cov 继承 coverage 配置的 `fail_under`，整体覆盖率不足时该 job 失败）(覆盖率阈值见 [`pyproject.toml`](../../pyproject.toml))
9. **requirements*.txt 漂移处理** (`requirements-drift` job 检测到 main 分支漂移时，由 `update-requirements` job 创建同步 PR)

> **Python 3.14 状态说明**：完整测试矩阵（Code Quality & Tests、Windows E2E、Windows Build 等）仅运行 Python `3.13`（稳定基线）。Python `3.14` 当前依赖已支持（`litellm>=1.101.0` 的 `Requires-Python` 为 `<3.15, >=3.10`），`lint-fast` job 将 `3.14` 作为 experimental 矩阵项前瞻验证（仅跑 `ruff check` + `ruff format --check`，不安装项目依赖；矩阵配置见 [已知架构技术债](../debt/known-technical-debt.md) 相关条目）。

### 版本发布流程与 Release 管理

发布流程: 打 `v*.*.*` tag → 触发 `build-windows` job → PyInstaller 打包 CPU/CUDA 两个变体 → smoke test → Inno Setup 制作安装包 → GitHub Release 发布。

**其他 workflow**（`.github/workflows/` 下除 `ci_cd.yml` 主矩阵外的兜底流水线，由 `check_workflow_enum()` 门禁守护无遗漏）:
- 前端流水线：文档 CI (`docs-ci.yml`)、Flet 前瞻验证 (`flet-nightly.yml`)、PostgreSQL sidecar Release (`sidecar.yml`)
- 安全流水线：CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)
- 依赖与发布：依赖更新机器人（自托管 Renovate workflow (`renovate.yml`)，每周一 02:00 UTC 定时 + 手动触发，配置见 [`.github/renovate.json`](../../.github/renovate.json)；Python/JS 依赖由其接管，GitHub Actions 生态由 [`.github/dependabot.yml`](../../.github/dependabot.yml) 承接，分工在两侧配置注释中声明。Python 漏洞另由 CI `run_pip_audit.py` 扫描）、自动化 Release PR (`release-please.yml`)
- 模型产线：FinBERT GGUF 训练/导出/Gate/发布流水线 (`finbert-train.yml`)，仅 `workflow_dispatch` 手动触发（输入 `model_version` 可选（留空自动取最新 Release minor+1 并探测 tag 空位）+ `corpus_tag`（默认 `corpus-v1`）+ `force_retrain`（默认 false））——指纹路由（ADR-0015 内容寻址去重：装依赖前对语料 sums + 产线脚本白名单 + workflow 全文 + 基座 revision + llama.cpp commit 计算 canonical 指纹，与最新 `finbert-gguf-v*` Release 随附 `fingerprint.json` 对比，输入无变化即拒绝重训）→ 语料下载校验（SHA256 全量 + manifest 校验（FT-07：行数/digest 期望值取自 corpus Release 随附 manifest，替代硬编码；legacy corpus-v1 经显式登记兜底并声明未做 manifest 校验）+ F2 语料锚断言（ADR-0015：实际下载的 SHA256SUMS.txt 必须与指纹锚一致，错位即 fail-fast））→ 固定 seed 训练 → GGUF 导出（llama.cpp 工具链 v0.3.0）→ 一致性 Gate（N0-3 四判据）→ 通过后发布 Release `finbert-gguf-v{model_version}`（note 含训练语料 lineage 与训练指纹（ADR-0015）；`fingerprint.json` 随 Release 分发，供下轮训练作去重基线）；训练依赖（torch CPU / transformers）临时安装在 job 内、不进 pyproject（ADR-0012 修订经 ADR-0013 承载）。与主 CI 隔离：concurrency 分组 `finbert-train`（`cancel-in-progress: false`），发布 tag `finbert-gguf-v*` 不匹配主 CI `v*.*.*` tag 过滤；产线机制总览正本见 [finbert-pipeline.md](./finbert-pipeline.md)

### 依赖升级

依赖升级 / 新增 / 移除的操作步骤以 [how-to.md「6. 新增与升级依赖」](./how-to.md#6-新增与升级依赖) 为唯一正本（本节只做路由，不复述步骤）；依赖管理总览与 PyInstaller 打包约束见 [dependency-management.md](./dependency-management.md)。

常规更新由 Renovate / Dependabot 自动发起、来源漏洞由 CI Security Audit（`run_pip_audit.py`）扫描（分工见上「其他 workflow」与「CI Job 矩阵」）。

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
| `scripts/check_docs_consistency.py` | 文档一致性门禁（锚点/相对链接/版本/规则集/ADR/脚本索引等一致性项，检查项清单见脚本 docstring） | pre-commit `docs-consistency` + CI | 阻断 |
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
| `scripts/verify_finbert_gguf.py` | FinBERT2-base GGUF（f16 发布主体；Q8_0 可选对照）与 PyTorch 参考编码器一致性门禁（N0-3；分词/CLS/概率/维度，需 finbert 环境） | 手动 / CI（`finbert-train.yml` Gate）/ 发布；Gate FAIL 阻断 Release | 不阻断 CI，阻断 Release |
| `scripts/verify_finbert_effect_gate.py` | FinBERT 模型效果硬门槛（FT-02；需 gh 凭据）：新模型 dev/test macro-F1 须 ≥ 最近一次已发布 Release（tag `finbert-gguf-v*`）note 中同名字段 - 0.03；锚点运行期从 Release note 解析（不硬编码），锚点不可解析即 fail-closed | 手动 / CI（`finbert-train.yml` Effect gate）/ 发布；FAIL 阻断 Release | 不阻断 CI，阻断 Release |
| `scripts/corpus_manifest.py` | FinBERT 训练语料 manifest 校验 / 生成（FT-07）：manifest（schema `finbert-corpus-manifest.v1`，含 corpus_tag/版本 + 三 split 非空行数/SHA256）为行数/digest 期望值唯一权威来源，替代原硬编码 8813/1102/1102；`build` 由语料实测生成 manifest，`verify` 校验文件与 manifest/`SHA256SUMS.txt` 交叉一致（防双事实源漂移）；legacy 语料（corpus-v1，无 manifest）经显式登记表 `LEGACY_CORPUS_REGISTRY` 兜底并在报告/Release note 声明「未做 manifest 校验」，未登记 tag 缺 manifest 即 fail-closed（不静默回退硬编码） | 手动（`build`，语料 Release 创建）/ CI（`finbert-train.yml`，`verify`）/ 维护 | 不阻断 CI，阻断 Release |
| `scripts/collect_sina_7x24.py` | 新浪财经 7x24 快讯离线采集器（N1-1；录制原始 JSON + 打印解析摘要；支持 `--parse` 离线复解析） | 手动/维护 | 不阻断 |
| `scripts/collect_sina_stock.py` | 新浪个股新闻离线采集器（N1-2；录制 GB18030 HTML + 打印解析摘要；支持 `--parse` 离线复解析） | 手动/维护 | 不阻断 |
| `scripts/collect_cninfo.py` | 巨潮资讯公告离线采集器（N1-3；录制原始 JSON + 打印解析摘要；支持 `--parse` 离线复解析） | 手动/维护 | 不阻断 |
| `scripts/collect_corpus.py` | 多源语料库采集编排器（N1-4；按源插件化聚合三源 → 清洗去重 → 写 SQLite 语料库；含单源熔断与限速） | 手动/维护 | 不阻断 |
| `scripts/laya_annotator.py` | 本地 laya-multilingual 零样本新闻情绪标注器 + 人工抽检 Gate（N2-1；choice 三分类 / 不可信输出丢弃计数 / 按源分层抽样 / 一致率 ≥85% 门禁；需 finbert 离线环境，数据不出本机） | 手动/维护 | 不阻断 |
| `scripts/train_finbert.py` | FinBERT 新闻情绪三分类阶段 B 训练 driver（CG-1；固化 reviews §5.2 超参 / 固定 seed / 语料完整性校验 / `--dry-run` 计划；需 finbert 离线环境） | 手动 / CI（`finbert-train.yml`）/ 维护 | 不阻断 |
| `scripts/export_finbert_gguf.py` | FinBERT GGUF 导出流水线（CG-1；分类头 → head.npz + 编码器 f16（发布产物，Q8_0 量化已移除）；llama.cpp 对齐 tag v0.3.0 见脚本 docstring；`--dry-run` 预检；需 finbert 离线环境） | 手动 / CI（`finbert-train.yml`）/ 维护 | 不阻断 |
| `scripts/patch_llama_cpp_pre_registry.py` | llama.cpp pre-tokenizer 登记补丁（FinBERT2-base chkhsh 上游全版本未登记；幂等注入已识别的 bert-bge-large 登记项——运行时强制外部分词，pre 名仅元数据；锚点缺失/重复注入拒绝） | CI（`finbert-train.yml`）/ 手动 | 不阻断 |
| `scripts/compute_training_fingerprint.py` | FinBERT 训练指纹计算与指纹驱动发布路由（ADR-0015 内容寻址去重；stdlib 运行，F3 复用链当前 stdlib-only）：对语料 sums + 产线脚本白名单（显式枚举，含自举）+ workflow 全文 + 基座 revision + llama.cpp commit 计算 canonical 指纹，与最新 `finbert-gguf-v*` Release 随附 `fingerprint.json` 对比，输入无变化即拒绝重训（省 1~3h）；`route` 于装依赖前 fail-fast，`verify-corpus-anchor` 作 F2 语料锚断言（实际下载的 SHA256SUMS.txt 与指纹锚一致）；退出码 0=放行/1=输入无变化/2=错误（fail-closed） | CI（`finbert-train.yml` 指纹路由 + 语料锚断言）/ 手动 | 阻断 Release（skip 拒绝重复训练 / fail-closed 阻断训练） |

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链

---

## 完成判定（canonical 入口）

- CI Job / pre-commit hook 增删已回填本文件与 `.pre-commit-config.yaml` / workflow 清单，名称级一致（避免枚举漂移）
- 版本发布流程按「版本发布流程与 Release 管理」执行，release 产物验证通过
- 依赖升级按「依赖升级」小节路由至 [how-to.md「6. 新增与升级依赖」](./how-to.md#6-新增与升级依赖)，`pyproject.toml` 与 `requirements*.txt` 保持一致

_最小验证命令：_ CI/依赖变更 → 编辑 `pyproject.toml`（pre-commit 自动同步 requirements）或 workflow → 由 CI 验证；
        文档改动 → `python scripts/check_docs_consistency.py`。
