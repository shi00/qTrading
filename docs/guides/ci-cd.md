# CI/CD 流水线与门禁

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.2（pre-commit、Alembic 迁移、质量门控强制）；实现细则以本节为准。

### 三层门禁区分

修改代码后按下表选择对应门禁层级，避免全量跑浪费时间或漏跑：

| 层级 | 触发场景 | 命令/Job |
|------|---------|----------|
| **本地最小门禁** | 每次小改动后自检 | `ruff check .` + `ruff format --check .` + 变更相关测试 |
| **变更相关门禁** | 提交前按变更范围自检 | 按 [变更类型 → 最小验证子集](../../CONTRIBUTING.md#变更类型--最小验证子集) 选择 |
| **CI 全量门禁** | 推送 / PR / 跨层修改 | `ruff`（前置 `lint-fast` / `lint-fast-313` job）→ `pre-commit` → 版本一致 → 安全扫描（pip-audit）→ `pyright` → weak assertions → 迁移一致性（`upgrade head`/`check`/`downgrade base`/`upgrade head`）→ ORM 一致性 → 单测 → 集成 → per-file 覆盖率（≥80% 强制 + 分层 advisory）→ diff coverage（strict ≥80%）→ Windows e2e |

### CI Job 矩阵

GitHub Actions 双平台验证 (`.github/workflows/ci_cd.yml`)，PR/主干质量门禁包括：

> **PR 触发范围与 required 门禁**：`ci_cd.yml` 的 `pull_request` 不再设置 `paths` 白名单。main 分支的 required status check 是聚合 job `CI Gate`（另有 `docs-ci.yml` 的 `Docs Consistency Check`），而 workflow 未触发时该 check 会永久停在 Pending 并阻塞合并。变更是否属于代码类改由 `Changes` job 在运行时判定（`scripts/classify_changed_paths.py`，规则与迁移前的 `pull_request.paths` 白名单一致）：非代码类 PR 只跑 `Changes`、`lint-fast` 与 `lint-fast-313`，重型 job 依 `needs.changes.outputs.code` 跳过。`CI Gate` 在 `if: always()` 下用 `needs.*.result` 显式校验（`pull_request` 与 `merge_group` 事件均运行，后者为 merge queue 预留），只放行明确允许的跳过（非代码类 PR 的重型 job、dependabot/fork PR 的 Linux 三分体 `ci-checks` / `ci-checks-static` / `ci-checks-db`、代码类 PR 的 `installer-smoke`——其 job 内以变更检测 fail-closed 兜底），上游失败导致的 skipped 一律判不通过，避免「上游失败 → 下游跳过 → 门禁绿灯」。分类所用 GitHub API 失败、fork PR 或变更文件触达 3000 上限时一律按代码类处理（fail-closed）。另有独立补偿门禁 `ci-aux-gate.yml` 的聚合 job `CI Aux Gate`：`pull_request` 与 `merge_group` 均不设 paths 过滤而稳定出现，内部 `detect` 按变更路径决定是否经 `workflow_call` 调用 `sidecar.yml` / `precommit-config-ci.yml`，聚合判定只放行「需要 → success」「不需要 → skipped」两种组合。该 check 的 required 注册由授权维护者在 ruleset `main-required-checks` 执行，仓库内变更不修改远端 ruleset。
>
> **main 分支合并策略（PR-only）**：main 由规则集 `main-required-checks` 保护——当前远端 required checks 为 `CI Gate` 与 `Docs Consistency Check`（新增的 `CI Aux Gate` 需由授权维护者在同一 ruleset 注册后才进入强制集合），并启用 `pull_request` 规则（required approving review 数为 0，即必须经 PR 合并）；直推 main 被禁止，管理员通过 `bypass_mode: pull_request` 获得的是「PR 合并时可绕过 checks」的能力，不是直推许可。

1. **Fast Ruff Check & Format**：`lint-fast` job 跑 Python `3.14`（默认运行时），`lint-fast-313` job 跑 Python `3.13`（`requires-python>=3.13` 声明下界的静态解析守卫）。两者均仅跑 `ruff check` + `ruff format --check`，不安装项目依赖，且均已移除 `continue-on-error`（结果真实上报）。聚合门禁 `CI Gate` 分别校验 `needs.lint-fast.result` 与 `needs.lint-fast-313.result`，两者均不允许 skipped。3.13 job 只证明源码在 ruff（`target-version=py313`）规则/语法层面兼容，**不覆盖 3.13 运行时业务兼容**（运行时测试矩阵与发布产物仍为 3.14）。拆成独立 job 而非同一 matrix，是为了让聚合门禁逐项判定，避免 matrix 合并状态带来的歧义
2. **Pre-commit Hooks** (`ci-checks-static` job：Ruff、格式化、裸 `type: ignore`、requirements 同步、版本一致性、文档一致性、红线自动化、import-linter 架构守护；hook 数量见 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml))
3. **Security Audit** (`ci-checks-static` job，`scripts/run_pip_audit.py`，扫描 `requirements.txt`、`requirements-optional.txt`、`requirements-dev.txt`，使用 `.security/audit-allowlist.yml`)
4. **Pyright Type Check** (`ci-checks-static` job，版本见 `ci_cd.yml`，`continue-on-error: false`)。CI 的 `pre-commit run --all-files` 以 `SKIP=pyright-changed,weak-assertion-changed` 跳过两个增量 hook：`--all-files` 会把全仓文件传给 `pass_filenames` hook，使其退化为全量扫描，与本项独立 `pyright` step 及 weak assertions step 完全重复（实测分别约 74.5s / 7.6s）；本地 `pre-commit` 仍保留增量秒级阻断，CI 门禁强度不变
5. **Alembic Migration** (`ci-checks-db` job：`upgrade head` → `alembic check` → `downgrade base` → `upgrade head`，其后跑 ORM-迁移 schema 一致性测试)
6. **Unit & Integration Tests** (Linux/Windows unit，Linux integration；完整测试矩阵为 Python `3.14`)。Linux 侧拆为三个并行 job：`ci-checks-static`（第 2~4 项 + weak assertions 扫描）、`ci-checks-db`（第 5 项，含 postgres service）与 `ci-checks`（单测 + 集成 + 覆盖率 + SBOM）；三者均以 `needs.lint-fast` 为前置且由 `CI Gate` 逐项校验，取代原先单 job 串行执行——拆分只改并行度、不减少任何门禁。**覆盖率只采集一次**（`Run Unit Tests` 与 `Run Integration Tests` 两阶段共用同一 `.coverage` 数据源，集成阶段 `--cov-append` 后以同名 `coverage.xml` / `coverage.json` 重新生成最终报告，下游 per-file / diff / Codecov / artifact 消费的是合并后的数据）：`ci-checks`（Linux）负责第 8 项的 per-file / diff / Codecov 门禁，`ci-checks-windows` 的 `Run Unit Tests` 不传 `--cov*`，只保留「同一批 `tests/unit/` 用例在 Windows 环境可跑通」这一独有信号（Windows 采集覆盖率曾使该 job 的 Run Unit Tests 达 7m26，成为全流水线最长分支）。**污染探测不进 PR 重型路径**：`test_no_cross_test_pollution_in_core_subset`（3 次串行乱序重跑，合计约 2 分钟）由 Linux/Windows 单测与 nightly `flaky-detection` 的 10 轮循环以 `--deselect` 排除，只在 nightly job 中（flaky 检测之前）的专用 step 执行一次（PR 仅保留其秒级的文件存在性检查）——xdist 多进程会掩盖跨测试污染，且每 PR 重复付出该成本无增量信号
7. **Windows E2E Tests** (`tests/e2e/`，Chromium + PostgreSQL)。5 个矩阵腿各自构建 sidecar，故 sidecar 二进制按**源码哈希**缓存（键含 `Cargo.lock` / `src/**` / `build.rs` / `.cargo/config.toml` + rust 版本，仅 main 保存）；`Swatinem/rust-cache` 的 target 缓存无法省掉这一步——checkout 重写源文件 mtime 后 cargo 指纹失效仍会全量重编译
8. **Per-File (≥ 80%) + Diff Coverage (≥ 80%)（CI 强制）**；分层单文件阈值 services/strategies/data ≥ 90%、ui ≥ 85%（advisory 报告模式，不阻断，见 [testing.md](./testing.md)）；**Overall ≥ 85% 同样在 CI 路径强制**（`pyproject.toml` `[tool.coverage.report]` `fail_under=85`：CI 的 `pytest --cov` 未显式传 `--cov-fail-under`，pytest-cov 继承 coverage 配置的 `fail_under`，整体覆盖率不足时该 job 失败）(覆盖率阈值见 [`pyproject.toml`](../../pyproject.toml))
9. **requirements*.txt 漂移处理** (`requirements-drift` job 在代码类 PR、push 到 main、任意 `workflow_dispatch` 上检测漂移；写回与创建同步 PR 仅限 push 到 main 或 main 上的 `workflow_dispatch`，详见下方「requirements 漂移的检测与写回」)
10. **Installer ISCC Smoke** (`installer-smoke` job，仅 PR)：`Detect installer changes` 步骤用 `gh api --paginate` 分页读取全部 PR 变更文件，仅当变更含 `installer.iss` 或 `scripts/*installer*` 时执行 ISCC 编译校验；API 失败、token 缺失或触达 3000 文件上限时 fail-closed 报错，不会静默跳过

> **fork PR 的 secretless 路径**：`pull_request` 事件来自 fork 时 GitHub 不注入仓库 secrets（仅只读 `GITHUB_TOKEN`）。`ci-checks` 与 `ci-checks-db` 依赖 `CI_PG_PASSWORD`（postgres service 容器 + integration 测试）与 `CODECOV_TOKEN`，secret 为空会使 service 容器在初始化阶段失败且不可提前拦截；`ci-checks-static` / `ci-checks-db` / `ci-checks` 的跳过条件保持一致（同一 job 模板的三分体，任一分体在 fork PR 与 dependabot PR 下均跳过）；`update-requirements` 依赖 `PAT_FOR_CI` 与 `contents: write` 回推分支，且仅 push 到 main / main 上的 `workflow_dispatch` 运行（见下方「requirements 漂移的检测与写回」），对任何 PR（含 fork / Dependabot）均不写回。故 `ci-checks` 等依赖 secret 的 job 对 fork PR 显式跳过（状态为 skipped，不代表通过），其验证由维护者审阅后以在库分支重跑再合并；fork PR 的 requirements 漂移仍由 `requirements-drift` 检测并展示。其余 PR 触发 job（`ci-checks-windows` / `e2e-tests-windows` / `embedded-tests` / `installer-smoke` / `requirements-drift`）不依赖仓库 secret，保持对 fork PR 运行。

> **requirements 漂移的检测与写回（CI-F04）**：`requirements-drift` 在所有适用事件继续检测——代码类 PR、push 到 `main`、任意 ref 的 `workflow_dispatch`。写回 `requirements*.txt` 并创建 `chore/auto-requirements-sync` → `main` 的 PR，仅限 `push` 到 `main` 或 `main` 上的 `workflow_dispatch`，且要求 `drift=true`、非 Dependabot、`PAT_FOR_CI` 可用；`update-requirements` 保留 `contents: write` / `pull-requests: write`，但 PR 与非 main ref 的 `workflow_dispatch` 不运行该 job。该行为是有意调整：PR 阶段不再主动创建同步 PR，避免在合并提交（detached HEAD、缺 base）上写回，也避免把未合并 PR 内容带入共享分支；PR 的漂移只作为检测信号，由维护者按主干同步流程处理。
>
> **requirements 机器人协作规则（依赖升级期间）**：main 上 `requirements-drift` 检出漂移后由 `update-requirements` 创建 `chore/auto-requirements-sync` PR（PR 阶段只检测、不创建）。依赖全量升级的各 Phase PR 在途期间，该 bot PR 只读不改：若其 diff 与本 Phase 目标版本一致，先合入 bot PR 再 rebase 本 PR；否则关闭该 bot PR，待 Phase 合并后由 bot 重新生成。任何 Phase PR 合并后须复跑 `requirements-drift`（上游当日发版会使 main 变红），变红时按同一规则处理，不得在业务 PR 中顺带升级无关包。

> **Python 运行时矩阵**：完整测试矩阵（Code Quality & Tests、Windows Unit、Windows E2E、Windows Build 等）与发布产物统一使用 Python `3.14`；`pyproject.toml` 的 `requires-python` 仍为 `>=3.13`（源码安装下界），由 `lint-fast-313` job 守护（`CI Gate` 校验其 `needs.lint-fast-313.result`，不允许 skipped）；该 job 仅做 ruff 静态解析检查，不代表 3.13 运行时业务支持。编译型依赖已取证：`psycopg2-binary` 有 cp314 Windows 轮子；`llama-cpp-python` PyPI 仅发源码包（上游已声明 Python 3.14 支持），CI 在 windows-latest / ubuntu-latest 以 MSVC / CMake 源码构建。不升 Python 3.15（`litellm` 声明 `Requires-Python <3.15`）。

### 版本发布流程与 Release 管理

发布流程（人工推 tag，release-please 只出 Release PR，不建 tag/Release）:

1. 合并 release-please 生成的 Release PR：bump 版本号、更新 `CHANGELOG.md` 与 `.release-please-manifest.json`。**合并前须在同一 PR 分支手改 `SECURITY.md` 的 Supported Versions 表**（如 `0.11.x` / `< 0.11`）：release-please 的通用 updater 无法改写通配行，而 `Docs Consistency Check` 是 main 的 required check，不改则该 check 在 Release PR 上必然失败，只能靠 admin bypass 合并；若后续 release-please 重跑覆盖了这次手改，补一次即可；
2. **在该合并提交上立即推送同名 tag**：`git tag -a v<版本> -m "v<版本>" && git push origin v<版本>`（形如 `v0.11.0`，须与 `.release-please-manifest.json` 的版本一致，与 `release-please-config.json` 的 `include-v-in-tag: true` / `include-component-in-tag: false` 对齐）；
3. **把已合并 Release PR 的 `autorelease: pending` 标签改为 `autorelease: tagged`**：`gh pr edit <PR> --remove-label "autorelease: pending" --add-label "autorelease: tagged"`。`skip-github-release: true` 下 release-please 不会自行改这个标签，而它下一轮运行以该标签识别「已合并但未打 tag 的 Release PR」；残留 `pending` 时 release-please 报 `There are untagged, merged release PRs outstanding - aborting` 并停止产出后续 Release PR（`v0.11.0` 之后的 Release PR 即因漏此步而未能自动生成）；
4. tag 推送触发 `ci_cd.yml`（`push.tags: v*.*.*`）→ `build-windows` job → PyInstaller 打包 4 个变体（`cpu` / `cuda` / `embedded-cpu` / `embedded-cuda`，后两者内置 sidecar PostgreSQL）→ smoke test → Inno Setup 制作安装包 → 安装包冒烟测试（`installer_smoke_crash_recovery.ps1` + `installer_smoke_running_app.ps1`，两步调用均校验退出码；运行中拒绝场景另须以 `/LOG` 受控日志行 `qtrading.setup-guard.refused reason=...` 认证拒绝原因——非 0 退出码与磁盘/权限等安装内部故障无法区分，不足以判通过，任一失败即中断发布）；同时触发 release-only 的 `e2e-sidecar-real-smoke` job（真实 sidecar ready → migration → 主界面路径）→ `build-windows` 与 `e2e-sidecar-real-smoke` 均成功后才由 `release-windows` 原子发布（任一失败即不发布；手动 `workflow_dispatch` 非 tag ref 仅构建产物，不创建 Release）。两条 cuda 变体所需 `llama-cpp-python` CUDA wheel 由独立 workflow `.github/workflows/cuda-wheel-warmup.yml` 预编译（与本 workflow 完全解耦——冷构建预算已放宽到 210 分钟，若留在 `ci_cd.yml` 内会与 push 到 main 的 run 共享 workflow 级并发组（同 ref、`cancel-in-progress: false`），让后续所有合并的整条 CI 排队等待最长数小时；独立文件 + 独立并发组消除该队头阻塞。触发面为 **push 到 main**（`paths` 过滤 `requirements.txt` / 本 workflow / 共用 action / 定义键后缀的 `ci_cd.yml`）/ **main 上的** `workflow_dispatch` / 夜间 `schedule`，**不含 tag**——tag 发布时预热与 `build-windows` 并发启动，同 run 产物必然来不及取用，2026-11-07 夜间 run `42353067654` 实测此前的 job 在 90 分钟步超时被杀、从未发布成功，每次 tag 都等同白烧一次编译。`workflow_dispatch` 会执行所选 ref 上的 workflow 定义并授予 job 级 `contents: write`，故 job `if` 显式限定 `github.ref == 'refs/heads/main'`，避免从任意分支触发写入；同一时刻只允许一个预热构建（workflow 级 `concurrency: cuda-wheel-warmup`、`cancel-in-progress: false`），避免并发重编同一 key 互相 `--clobber`）：产物不进 `actions/cache`，而是发布为**独立 tag `ci-wheels` 的 Release 资产**（资产名 `<key>.zip`，内含 wheel 本体与 `SHA256SUMS.txt`）。该仓库不受每仓库 10 GB 缓存上限与 7 天未访问淘汰约束，也不受 tag 作用域限制——任意 tag / 分支 / `workflow_dispatch` 均可读取，且不占用 PR 缓存预算。该 Release 以 `--prerelease` 创建，不会被 GitHub 标记为仓库 Latest（`make_latest` 默认行为），也不进入「正式版本」视图；上传前会读取 `isDraft` 并拒绝向 draft 状态写入（draft 对外不可见，写入会变成「上传成功但下游永远取不到」的假成功）。`<key>` 覆盖 llama-cpp-python 版本 / CUDA 版本 / Python 版本 / CUDA arch 列表 / runner 镜像标签（`win2022`），版本单源于 `requirements.txt`，可变后缀单源于 workflow env `LLAMA_CUDA_WHEEL_KEY_SUFFIX`，末尾带 `-rN` 修订段：产物异常（如镜像内 MSVC 补丁变化）时递增该段即可强制重编，旧 `-rN` 资产不会自动清理，确认无回滚需求后可手工删除对应 `<key>.zip`。两条 cuda 腿与预热 workflow 共用 composite action `.github/actions/restore-cuda-wheel`（`gh release download ci-wheels --pattern <key>.zip` + sha256 / 文件名校验 + 3 次指数退避重试，单源避免三处副本漂移；重试用于避免一次网络抖动把发布腿推回源码编译）；命中后每条腿省去约 37 分钟以上的源码编译（37 分钟取自旧 arch 集合实测；6 档 arch 的冷构建实测 >90 分钟，见下），CUDA Toolkit 安装保留（保证打包期 PyInstaller 的 DLL 依赖解析与改动前一致）。预热 workflow **独立于 `ci_cd.yml`、不在 `build-windows` 的 `needs` 内**（失败或超时不得阻断发布），重型步骤另设 `continue-on-error` 与单步超时；Release / 资产不存在、下载或校验失败时，两腿自动回退到内联源码编译，行为与改动前等价，所有既有断言（`Verify CUDA Toolkit`、`Verify llama-cpp-python CUDA backend DLLs`、安装包冒烟）照常逐腿执行；热路径在下载侧额外做 sha256 与文件名校验、安装后做 CUDA 后端证据复核（`lib/*cuda*.dll` 存在，或 `llama.dll` 链接 `cudart64_12` / `cublas64_12` / `cublasLt64_12`），证据不足即卸载回退源码编译——校验强度较改动前的 `actions/cache` 更高（旧实现无任何摘要校验）。**不使用同 run artifact 交付**：两个 job 在时间上重叠，实测 `build-windows` 在 run 开始 8.4 分钟即启动，而冷路径编译需 90 分钟以上，同 run 产出的 artifact 必然来不及被取用，故只用 Release 资产做跨 run 交付。冷路径（bank 未命中）墙钟与改动前持平；因 tag 已不在触发面，冷路径不再额外多花一次预编译，预编译成本只发生在 `schedule` / push 到 main。夜间 `schedule` 与 push 到 main 在命中时数分钟结束（命中时跳过 Toolkit 安装，只做下载与复核）、未命中才重编发布，保证 tag 发布前 bank 已预热；也可在 main 上手动跑一次 `workflow_dispatch` 补建。单步预算单源于 workflow env `LLAMA_CUDA_BUILD_TIMEOUT_MIN`（当前 210 分钟，`Build CUDA wheel` 的 `timeout-minutes` 与 summary 判定共同引用），job 预算 300 分钟——Build 之前的 Toolkit 安装实测可达 26 分钟，余量必须覆盖 job 内前置步骤，否则 job 级超时会先于步超时触发、令 `always()` 的 summary 与守卫都不执行（job 变灰而非变红）；实测 90 分钟不足以完成 6 档 arch 冷构建。该 workflow 的唯一职责就是补建 bank，故终态不是 `cache_hit` 或 `built_published` 时，末尾守卫步骤 `Fail warmup when bank stays cold` 会 `exit 1` 让整个 workflow 变红（历史实现因所有重步骤 `continue-on-error` 且 outputs 无人消费而恒绿，超时无人发现）；它独立于 `ci_cd.yml`，不在任何 `needs` 链内，变红不阻断 PR 门禁与发布链。下载未命中时共用 action 会打 `::warning::` 注解，故 tag 发布腿回退源码编译同样可见。推 tag 前应确认 bank 已含当前 key 资产（`gh release view ci-wheels --json assets`），缺失则先在 main 上 dispatch 预热。`ci-wheels` tag 不匹配 `push.tags: v*.*.*`，不会触发发版链；`release-tag-guard` 只检查 `.release-please-manifest.json` 声明的 `v<版本>` tag，亦不受影响。`cuda-wheel-warmup.yml` 的 job 末尾固定执行 `if: always()` 的结构化 summary：读取 restore / build / verify / publish 的 outcome 与起始时间，输出稳定结果枚举（`cache_hit` / `built_published` / `build_timeout` / `build_failed` / `verify_failed` / `publish_failed` / `cancelled` / `unexpected`）、`published` 标志与 Markdown 表格，并映射为 job outputs `warmup_result` / `warmup_published`；该 summary 自身 `continue-on-error`，不改变 job 结论，build outcome=failure 且耗时 ≥ 预算-1 分钟（预算取 workflow env `LLAMA_CUDA_BUILD_TIMEOUT_MIN`，当前 210）判为 `build_timeout`。CUDA arch 固定为 `75-real;80-real;86-real;89-real;90-real;90-virtual`（Turing / Ampere / Ada / Hopper + PTX 前向兼容），调整时须同步 `cuda-wheel-warmup.yml` 的 CMAKE_ARGS、cuda 腿的 CMAKE_ARGS 与 workflow env `LLAMA_CUDA_WHEEL_KEY_SUFFIX` 三处；CUDA 版本升级另须同步两处 `cuda:` 输入、`$cudaBin` 路径（`v12.4`）与 key 后缀中的 `cu12.4.1`。

   `release-windows` 的原子发布分四步，全部通过才对外可见：① 本地校验下载到的 4 个变体资产（安装包 / `SHA256SUMS-<variant>.txt` / `sbom-<variant>.cdx.json`）齐全，且每个变体恰好 3 个文件，并以 `sha256sum` 复验安装包与清单中的摘要一致（`SHA256SUMS` 由 PowerShell `Get-FileHash | Format-List` 生成，不是 `sha256sum -c` 可解析的格式，故按 64 位十六进制摘要提取比对）；② 以 `draft: true` 创建（或复用同 tag 的）draft Release 并上传 12 个资产；③ 经 GitHub API 复验 draft 状态、`tag_name` 与触发 tag 一致、资产名单与 12 个预期文件完全一致，且每个资产的服务端 `digest`（`sha256:<hex>`）与本地文件实测摘要一致；④ 全部通过后才 `gh release edit <tag> --draft=false --latest` 转正式。本地校验失败时尚未创建 Release 对象；上传或复验失败时残留的只是 draft（仅写权限可见、不能被标记 latest），外部不会把它当作正式版本。

> **Release PR 的 CI 触发**：`release-please.yml` 通过 `token: ${{ secrets.RELEASE_PLEASE_TOKEN }}`（fine-grained PAT，仓库权限 `Contents` / `Pull requests` / `Issues` 均为读写）创建 Release PR。默认 `GITHUB_TOKEN` 创建的 PR 不触发 `pull_request` workflow，一旦 main 启用 required status checks，Release PR 的 check 会永久停在 Pending；改用 PAT 后 Release PR 正常进入 CI。secret 过期会导致 Release PR 停更，须按 PAT 有效期轮换并同步更新仓库 secret。

**为什么必须人工推 tag**：`release-please.yml` 以 `skip-github-release: true` 运行，只产出 Release PR。release-please 默认用 `GITHUB_TOKEN` 创建 tag/Release，而 GitHub 规定由 `GITHUB_TOKEN` 触发的事件（`workflow_dispatch` / `repository_dispatch` 除外）不会再触发新的 workflow run，故 bot 创建的 tag 不会触发 `ci_cd.yml`，只会留下「Release 已存在但资产为 0」的空壳（历史 `v0.10.0` / `v0.10.1` 即此形态）。要改成自动发版须先在仓库侧配置 PAT/GitHub App token 并同步修订本文件，不得只改 workflow。

- **tag 归属**：只有人工推送的 tag 会触发构建；若某版本 tag 已存在，禁止 `-f` 强推或删除重推（会改写既有 Release 的 tag 来源），改为提升版本号重走 Release PR。
- **失败重跑与幂等**：同一 tag 重跑时，若已存在 draft 则复用并覆盖同名资产（`overwrite_files: true`），重新校验后再发布；若已存在正式 Release 则直接失败，不覆盖、不重发（已发布资产的摘要可能已被下载方或校验记录引用，静默替换会造成不一致），需人工评估后显式删除再重跑。不采用「先 `gh release delete` 再重建」：该做法会删除既有 Release 记录及其 URL，对已发布版本更等同于未经授权的破坏性操作。
- **latest 归属**：publish 时显式 `--latest`，保证正式版本被标记为 latest；GitHub API 规定 draft 与 prerelease 不能标记 latest，故 latest 只在发布动作内生效。
- **Release 正文**：由 `softprops/action-gh-release` 的 `generate_release_notes`（GitHub 自动变更摘要）生成；`CHANGELOG.md` 正文由 release-please 在 Release PR 中生成。两者不互相同步，属预期行为。正文在 draft 创建时生成，publish 步骤只改 `draft` / `make_latest`、请求不含 body，故不会重写正文。
- **sidecar 同源绑定（D18-R04）**：`build-windows` 的 embedded 变体打包 `sidecar-v*` Release 的二进制，下载前校验其与本次发版源码同源——commit 精确相等，或 `sidecars/qtrading-pg-sidecar` 子树 tree SHA 相等（sidecar 是同仓库内的独立 Rust 组件，子树相等即源码逐字节相同）；子树不同即 fail-fast，避免安装包内置另一份 sidecar 源码所编的二进制（`sidecar-v0.1.0` 停留在 2026-08，导致 `v0.11.5` 构建被阻断即此形态）。补救方式是在**该发版 commit 上**推送新的 `sidecar-v<版本>` tag（`git tag sidecar-v<版本> <commit> && git push origin sidecar-v<版本>`），待 `sidecar.yml` 四平台发布完成后重跑失败的 `build-windows` job；`workflow_dispatch` 到分支的手动复验允许差异，但会以 warning + Step Summary 显式标注且不发布。
- **切换后首个版本须验证**：合并下一个 Release PR → 推 tag → 确认新 Release 资产非空，且其后 release-please 仍能基于同名 tag 产出增量 changelog（条目不得重复或缺失）。
- **历史空 Release 不自动补救**：`v0.10.0` / `v0.10.1` 等既有空 Release 保持原样，如需补资产另行人工评估。

**其他 workflow**（`.github/workflows/` 下除 `ci_cd.yml` 主矩阵外的兜底流水线，由 `check_workflow_enum()` 门禁守护无遗漏）:
- 前端流水线：文档 CI (`docs-ci.yml`，对 PR 与 `merge_group` 事件运行、不设 paths 过滤（检视 §5.4：必要验证 job 每次事件均运行，纯 Markdown PR 天然覆盖），三 job 结构——`docs-static`（Windows runner：原位更新保护依赖 Windows 独占句柄，POSIX 按合同拒绝原位更新，生成/更新类用例须在支持平台真实执行；Python 3.13 只装 `requirements-docs.txt`，跑 `check_docs_consistency.py` 全量静态检查 + `tests/docs` 独立轻量套件）、`docs-application-contracts`（应用环境跑符号导入 / Flet API 字段合同 + 旧入口兼容转发）、聚合 job `Docs Consistency Check`（`if: always()` 显式汇总，失败/取消/skipped 均不通过）是 main 的 required check；缺少 `merge_group` 触发时，启用 merge queue 后队列永远拿不到该 check，而未命中 paths 时 PR 上的 check 会停在 Pending)、Flet 前瞻验证 (`flet-nightly.yml`)、PostgreSQL sidecar Release (`sidecar.yml`，其 `pull_request` 触发已迁移到补偿门禁的 `workflow_call`，仅保留 push / tag / dispatch / schedule 语义)
- 补偿检查门禁：`ci-aux-gate.yml` 的聚合 job `CI Aux Gate`，对 PR / `merge_group` 稳定出现（不使用 paths 过滤），按变更路径决定是否调用 `sidecar.yml` / `precommit-config-ci.yml`；另有独立必达 job `ci-meta-contract`（`scripts/check_ci_meta_contract.py`）在每次事件解析真实 workflow，校验 `docs-ci.yml` 的 job 图、验证 job 实际选中的检查脚本/测试文件（删除必需文件即失败）、执行条件与错误传播（`run` 被替换为 `echo`、禁用必要 job、`|| true` / `continue-on-error` 吞失败均失败），聚合判定要求其结果为 `success`；`merge_group` 只做测试与审计、不产生发布产物。该 check 需由授权维护者在 ruleset `main-required-checks` 注册为 required，仓库内变更不修改远端 ruleset
- 配置流水线：pre-commit 配置 PR 冒烟 (`precommit-config-ci.yml`，其 `pull_request` 触发已迁移到补偿门禁的 `workflow_call`，仅保留 `workflow_dispatch`)，由 gate 在 PR 修改 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)（或 `sidecar.yml` / `precommit-config-ci.yml` / `ci-aux-gate.yml` 自身）时执行 `pre-commit validate-config` + `pre-commit run --all-files`，不引入 PG service / 迁移 / 测试
- 安全流水线：CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)
- 缓存治理：缓存预算日清理 (`cache-prune.yml`)，每日 04:17 UTC（北京时间 12:17，错开上述日检）+ 手动触发；只删除「非 `refs/heads/main` 且 3 天内未被访问」的缓存条目（活跃 PR 每次推送都会访问自身缓存故不误删，`refs/heads/main` 永不删除），守护 GitHub Actions 每仓库 10 GB 缓存上限——超限后 LRU 会挤掉 main 的 uv / cargo 条目，实测曾使 Windows job 的 `Install dependencies` 由 0.7 分钟回退到 5.5~8.0 分钟；写入侧由 `ci_cd.yml` 的 `save-if`（cargo 缓存仅 main 保存）与各 job 的 `actions/cache` 显式 `UV_CACHE_DIR` 缓存（key 不含 ref，命中即不再写入副本；`setup-uv` 自带缓存一律 `enable-cache: false`）配合
- CUDA wheel 预热：`cuda-wheel-warmup.yml`（夜间 02:00 UTC + push 到 main 的 paths 过滤（`requirements.txt` / 本 workflow / 共用 action / 定义键后缀的 `ci_cd.yml`）+ main 上的 `workflow_dispatch`），把 llama-cpp-python CUDA wheel 预编译并发布到 `ci-wheels` Release 资产库，供 `ci_cd.yml` 的两条 cuda 腿下载复用；独立 workflow 与独立并发组 `cuda-wheel-warmup`（`cancel-in-progress: false`）是硬约束——冷路径预算 210 分钟，若并入主 CI 会以同一 `refs/heads/main` 并发组阻塞后续所有合并的整条 CI；终态非 `cache_hit` / `built_published` 时守卫步骤让 workflow 变红，机制细节见下文「Windows 构建」的 CUDA wheel 缓存段落
- 发版提醒：发版 tag 缺失日检 (`release-tag-guard.yml`)，每日 01:00 UTC（北京时间 09:00，错开主 CI flaky 检测 02:00 UTC 与 Flet 日检 03:00 UTC）+ 手动触发；读取 `.release-please-manifest.json` 的声明版本，用 `git ls-remote --tags --refs origin refs/tags/v<版本>` 判定 tag 是否缺失，缺失时以稳定标题 `Release tag missing: v<版本>` 分页检索 open Issue（排除 PR）→ 命中追加评论、未命中新建（Issue 被关闭后再次缺失按新建处理）；去重键只用标题（仓库无专用 label）；manifest 版本缺失/非法或 `git ls-remote` 失败只记 `core.warning` 并跳过，建单失败同样只 warning，不改变日检结论；**只提醒，不创建/推送 tag**（`GITHUB_TOKEN` 创建的 tag 不会触发 `ci_cd.yml`，强推/删除重推会改写既有 Release 的 tag 来源）；不挂 `pull_request`（fork PR 无 issues 写权限），不进主 CI 必跑路径
- 依赖与发布：依赖更新机器人（自托管 Renovate workflow (`renovate.yml`)，每周日 18:00 UTC（北京时间周一 02:00）定时 + 手动触发，运行时刻落在 `renovate.json` 允许更新窗口内，配置见 [`.github/renovate.json`](../../.github/renovate.json)；Python/JS 依赖由其接管，GitHub Actions 生态由 [`.github/dependabot.yml`](../../.github/dependabot.yml) 承接，分工在两侧配置注释中声明。Python 漏洞另由 CI `run_pip_audit.py` 扫描）、自动化 Release PR (`release-please.yml`)
> **Flet 日检失败建单去重（`flet-nightly.yml`）**：失败时以稳定标题 `Flet nightly compatibility check failed` 分页检索 open Issue（兼容历史 `... (<runId>)` 旧标题、排除 PR），命中则把本次 run 追加为评论，未命中则新建；Issue 被人工关闭后再次失败按新建处理（保留人工关闭语义，不自动重开）。该 workflow 只有一个失败信号（单 job `failure()`），故单一稳定标题即去重键；将来若拆出多个失败类别，须把类别并入标题。去重键只用标题（不叠加 label，避免未预置 label 导致键静默失效）。建单/追加失败或查询失败仅记 `core.warning` 并跳过建单（失败 run 本身在 Actions 页面可见），不改变 workflow 结论。
- 模型产线：FinBERT GGUF 训练/导出/Gate/发布流水线 (`finbert-train.yml`)，仅 `workflow_dispatch` 手动触发（输入 `model_version` 可选（留空自动取最新 Release minor+1 并探测 tag 空位）+ `corpus_tag`（默认 `corpus-v1`）+ `force_retrain`（默认 false））——指纹路由（ADR-0015 内容寻址去重：装依赖前对语料 sums + 产线脚本白名单 + workflow 全文 + 基座 revision + llama.cpp commit 计算 canonical 指纹，与最新 `finbert-gguf-v*` Release 随附 `fingerprint.json` 对比，输入无变化即拒绝重训）→ 语料下载校验（SHA256 全量 + manifest 校验（FT-07：行数/digest 期望值取自 corpus Release 随附 manifest，替代硬编码；legacy corpus-v1 经显式登记兜底并声明未做 manifest 校验）+ F2 语料锚断言（ADR-0015：实际下载的 SHA256SUMS.txt 必须与指纹锚一致，错位即 fail-fast））→ 固定 seed 训练 → GGUF 导出（llama.cpp 工具链 v0.3.0）→ 一致性 Gate（N0-3 四判据）→ 通过后发布 Release `finbert-gguf-v{model_version}`（note 含训练语料 lineage 与训练指纹（ADR-0015）；`fingerprint.json` 随 Release 分发，供下轮训练作去重基线）；训练依赖（torch CPU / transformers）临时安装在 job 内、不进 pyproject（ADR-0012 修订经 ADR-0013 承载）。与主 CI 隔离：concurrency 分组 `finbert-train`（`cancel-in-progress: false`），发布 tag `finbert-gguf-v*` 不匹配主 CI `v*.*.*` tag 过滤；产线机制总览正本见 [finbert-pipeline.md](./finbert-pipeline.md)

### 依赖升级

依赖升级 / 新增 / 移除的操作步骤以 [how-to.md「6. 新增与升级依赖」](./how-to.md#6-新增与升级依赖) 为唯一正本（本节只做路由，不复述步骤）；依赖管理总览与 PyInstaller 打包约束见 [dependency-management.md](./dependency-management.md)。

常规更新由 Renovate / Dependabot 自动发起、来源漏洞由 CI Security Audit（`run_pip_audit.py`）扫描（分工见上「其他 workflow」与「CI Job 矩阵」）。

### Pre-commit Hooks

本项目使用 pre-commit hooks，定义在 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)，本地 `repo: local` 提供以下 17 个 hook（提交前必须全部通过）：

- 代码质量：`ruff-check` · `ruff-format` · `type-ignore-reason` · `no-isolated-asyncio-testcase` · `no-temp-files`
- 增量检查：`pyright-changed` · `weak-assertion-changed` · `e2e-anchor-check` · `theme-contrast-check` · `lint-imports`
- 依赖与一致性：`pip-compile-core` · `pip-compile-dev` · `pip-compile-optional` · `pip-compile-docs` · `verify-versions` · `docs-consistency` · `redline-check`

> hook 数量与名称以 `.pre-commit-config.yaml` 为唯一正本，本清单由 `check_precommit_hook_names()` 门禁守护名称级一致（配置新增/删除 hook 时须同步本枚举）。

> **配置改动的合并前验证**：`.pre-commit-config.yaml` 不属于代码类变更（`Changes` job 分类为 `code=false`），仅改该文件的 PR 主 CI 只跑变更分类与快速 lint（均为轻量 job），不会拉起重型流水线，hook entry / 独立环境依赖 / 文件选择规则的缺陷会延迟到 main push 才暴露。该文件因此由独立轻量 workflow `precommit-config-ci.yml` 承接（`pre-commit validate-config` + `pre-commit run --all-files` 实际执行全部 hook，不含 PG service / 迁移 / 单测）；该 workflow 不再保留直接 `pull_request` 触发，改由 `ci-aux-gate.yml` 在变更命中时经 `workflow_call` 调用，保证同一 PR 只执行一次；不把该文件计入代码类变更，是为避免单文件配置 PR 拉起整套主流水线（其测试覆盖对配置改动零增量）。`docs-ci.yml` 也会运行（`Docs Consistency Check` 为 required check），其 docs-static job 在纯 docs 依赖环境跑文档静态检查与 `tests/docs` 套件、docs-application-contracts job 在应用环境跑文档契约测试，均不执行 hook 本体。配置与业务代码同时变更的 PR 会由主 CI 与本 workflow 各跑一次 hooks，属已知的有限重复。

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
| `scripts/classify_changed_paths.py` | PR 变更文件代码类分类（`Changes` job 据此决定重型 job 是否执行；规则与迁移前的 `pull_request.paths` 白名单一致，重命名文件同时对 `filename` 与 `previous_filename` 分类，API 失败/fork PR 由 workflow 按 fail-closed 兜底） | CI `Changes` job | 阻断（分类结果决定 `CI Gate` 判定） |
| `scripts/check_ci_meta_contract.py` | CI 选择与门禁结构元检查（检视 D22）：解析真实 `docs-ci.yml` / `ci-aux-gate.yml`，校验验证 job 图、实际选中的检查脚本与测试文件（必需文件缺失即失败）、执行条件与错误传播（`run` 被替换为 `echo`、禁用必要 job、`|| true` / `continue-on-error` 吞失败均失败），并断言本检查自身接入辅助门必达聚合 | CI `ci-aux-gate.yml` 的 `ci-meta-contract` job（每次 PR / merge_group） | 阻断 |
| `scripts/detect_flaky.py` | 重复运行 pytest 定位 flaky 测试 nodeid | CI（报告）+ 手动 | 不阻断 |
| `scripts/generate_sidecar_manifest.py` | qtrading-pg-sidecar Release manifest 生成 | CI `sidecar.yml` | 阻断（release 时） |
| `scripts/prototype_business_redlines.py` | R20/R21/R22 AST 可行性原型（人工评审辅助） | 手动 | 不阻断 |
| `scripts/verify_embedded_pg_connect.py` | embedded PG sidecar 连通性验证（一次性 spike；复用运行时密码解码，支持 Windows DPAPI `PGPW2:` / Unix 明文） | 手动/维护 | 不阻断 |
| `scripts/check_doctor_schema.py` | Doctor JSON 字段契约（显式必需字段 + 允许忽略表；未登记的新 Rust 字段/缺失必需字段失败）；另有 `tests/unit/test_verify_versions.py` 对真实源断言 | 手动/维护 | 不阻断 |
| `scripts/check_failure_injection_coverage.py` | 故障注入覆盖率交叉校验 | 手动/报告 | 不阻断 |
| `scripts/sync_e2e_fonts.py` | E2E 缓存字体同步（资产准备） | 手动 | 不阻断 |
| `scripts/safe_cleanup_branches.py` | 分支安全清理 | 手动 | 不阻断 |
| `scripts/sanitize_artifacts.py` | 上传前对文本工件做 R9 凭证脱敏（复用 `DataSanitizer.sanitize_error`，glob 未匹配即跳过） | CI `embedded-tests` 日志上传前 | 阻断（脱敏失败则 step 失败） |
| `scripts/migrate_strategy_name_to_i18n_key.py` | 历史 strategy_name 迁移为 i18n key（一次性 DB 迁移；仅 revision ≥ 0033 可 `--execute`，默认 dry-run 预检 revision 与归一化冲突，写入包单事务） | 手动 | 不阻断 |
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
| `scripts/upgrade_golden_snapshot.py` | 依赖升级金标准快照（D0.2）：用完全离线的合成冻结快照（freeze_date=2025-12-31）重跑 5 个策略场景 + 1 次回测，比对/覆写 `tests/fixtures/upgrade_golden/expected`；`--check` 一致退出 0、差异退出 1、输入异常退出 2；`--update` 重建 expected；`--generate-inputs` 确定性重建 inputs | 手动（依赖全量升级前后各跑一次） | 不阻断 |
| `scripts/g4_theme_screenshots.py` | G4-202 四主题截图判读采集（Flet 升级验收）：四主题 × Impeller/Skia 双后端；Win32 窗口定位、窄/宽屏缩放、最小化恢复、关闭确认弹窗；截图回退（PrintWindow → 屏幕抓取）、主色核验主题是否真正生效、与 G0 基线像素差分；退出码 0=采集完成/差分成功、1=主题未生效或空白截图、2=环境参数错误 | 手动（Flet 升级验收，候选构建实跑） | 不阻断 |
| `scripts/docs_markdown.py` | 文档 Markdown 统一解析视图（检视 5.1 工作包 D3）：CommonMark + table/strikethrough 一次解析形成标题/锚点、标准与引用式链接、HTML ID、代码块/行号的不可变视图，供 `docs_generated.py`（D4）与 `check_docs_consistency.py`（D5）复用；测试见 `tests/docs/` 独立环境（docs 组） | 手动 / 被 D5 门禁调用（接线后随 `docs-consistency` 触发） | 不阻断 |
| `scripts/docs_generated.py` | 受控生成器（检视 5.2/5.2.1 工作包 D4）：读 §4.1 作者源按封闭目标清单渲染派生块（缺块/重复/嵌套/越界/非法源显式拒绝），更新编排覆盖读快照→写入保护→核对→渲染→逐文件提交→更新后 all 检查全窗口；由 `check_docs_consistency.py --check/--update` 调用；测试见 `tests/docs/` 独立环境 | 手动 / 被 `check_docs_consistency.py --scope document` 生成输出检查调用（D6/D9 接线后随 `docs-consistency` 触发） | 不阻断 |
| [docs/governance/checker-migration-map.md](../governance/checker-migration-map.md) | 42 项文档检查迁移映射表（工作包 D5）：旧函数名 → 处置（保留 / 共享解析替代 / 迁移出口）→ 输入 → 对应测试 → 执行入口的责任映射正本（底稿 JSON 未恢复，人工核对口径）；承接 `check_docs_consistency.py` 的 42 项责任映射维护 | 随 `check_docs_consistency.py` 检查清单调整时同步维护 | 不阻断 |

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
