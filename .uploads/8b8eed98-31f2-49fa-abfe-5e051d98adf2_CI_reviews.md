# CI 工程检视报告

## 1. 结论摘要

**检视结论：REQUEST_CHANGES——建议先修复发布链路与门禁衔接问题，再继续提速。**

| 项目 | 结论 |
|---|---|
| 检视日期 / 产品版本 | 2026-11-06 / 0.11.4 |
| 模式 | CI 工程专项现状检视，不是增量 PR review，也不是完整安全审计 |
| 范围 | 13 个 GitHub Actions workflow，共 5,451 行、36 个 job 定义；关联门禁配置、脚本及定向契约测试 |
| 问题分类 | 5 项确定缺陷（CI-F01/02/03/04/06：P1 × 1、P2 × 3、P3 × 1）；1 项条件性门禁风险（CI-F05）；1 项实测运行期问题（CI-F07） |
| 报告复核意见 | APPROVE，附非阻断修订建议；仅评价报告真实性和方案合理性，不代表 CI 实现已通过或授权执行全部方案 |
| 本地验证 | 78 项现有配置契约测试通过；覆盖率最小复现确认 JSON/XML 陈旧；完成 YAML、依赖图、分类及路径探针 |
| 补充实测 | run #5941 为 schedule：整体约 95 分 22 秒，常规检查尾部 8 分 44 秒；CUDA 预热编译超时且未发布 wheel，详见 §5.4–5.7 |
| 第二次对照 | 同一提交的 run #5937 为 workflow_dispatch：整体约 136 分 59 秒；两条 CUDA 构建腿约 120 分钟后取消，预热也再次超时，详见 §5.8–5.9 |
| 最高风险 | sidecar 发布使用了错误的资产上传 API 主机，合法发布路径无法按 REST 契约完成上传 |
| 交付边界 | 仅新增并按复核意见修订本报告；未修改 workflow、业务代码、依赖或仓库设置，未执行发布、迁移或安装 |

现有工程基础较完整：稳定聚合门禁、静态/数据库/测试并行、Windows/Linux 验证、真实 sidecar 集成、覆盖率阈值、SHA 固定的 Action 引用、draft-first 发布及服务端摘要复核均已建立。主要问题不是“缺少检查”，而是**检查结果、事件分支、报告文件与外部 API 契约之间仍有断点**。两次同提交实测进一步区分了两类长尾：**schedule 的 95 分钟主要是夜间维护，dispatch 的 136 分钟主要是 CUDA 构建；均不能归因于 PR 主门禁。CUDA 预热的绿色结论也不代表有效产出。**

本报告中的 `REQUEST_CHANGES` 是针对现有 CI 工程的改进建议，不是对某个 PR 或线上流水线状态的自动判定，与“报告本身可采纳”的复核意见不冲突。P1 建议作为 sidecar 发布前置修复；确定缺陷按严重度和触发场景排期。CI-F05 须先核验远端策略，CI-F07 按冷构建有效性与维护成本评估；二者不计入五项确定缺陷，其 P2 标注用于风险/改进排期，不等于已证实产品功能错误。

## 2. 范围、基线与方法

### 2.1 证据边界

- 当前目录为无 Git 元数据的本地源码快照，无法取得提交 SHA、merge-base、历史差异或 staged/unstaged 状态。以下发现均为“快照中既存相关问题，具体引入时间无法判断”，不归因于某次提交或作者。
- 全量解析了全部 workflow 的 YAML 结构，逐项检查触发器、job 依赖、权限及矩阵；重点逐读主门禁、发布、自动依赖更新、覆盖率和工件上传路径。未对所有训练脚本、应用源码及第三方 Action 实现做全量审计。
- 初版仅依据本地快照。用户补充运行链接后，读取了指定 run #5941 和 #5937 的元数据、全部 job/step 时间、关键 CUDA/flaky 日志，以及共同运行提交的主 workflow。仍未访问 ruleset、环境保护或 secrets 设置；两次不同事件的定向对照不等于代表性统计，不能据此判断“最近 CI 全绿”“远端可直接合并”或 P95 耗时。
- 本地执行环境为 Windows、Python 3.13.13，并非主运行矩阵的 Python 3.14。沿用现成工具，未安装或升级依赖。
- 根目录未发现 `spec.md` 或 `Plans*.md`；本次以 [CI/CD 规范](../docs/guides/ci-cd.md)、[贡献流程](../CONTRIBUTING.md) 和实际配置建立契约，不假造产品规格或计划对齐结论。
- 已查阅 [技术债登记](../docs/debt/known-technical-debt.md)。Windows 单测超时余量、Rust 覆盖率欠债和 E2E 离线资产来源未闭合属于已有登记，不重复算作本次新增发现。

### 2.2 文件覆盖清单

下表 13 个 workflow 均成功读取并完成结构解析；“排除范围”指未执行的动态行为，而非文件读取失败。

| Workflow | 已检查内容 | 动态验证边界 |
|---|---|---|
| [ci_cd.yml](../.github/workflows/ci_cd.yml) | 20 个 job、PR/merge queue/main/tag/dispatch/schedule 路由、聚合门禁、构建发布和覆盖率 | 未执行完整 Python、数据库、浏览器、CUDA、安装包链路 |
| [docs-ci.yml](../.github/workflows/docs-ci.yml) | required check 常驻触发、依赖安装与文档契约步骤 | 未执行完整文档测试环境 |
| [precommit-config-ci.yml](../.github/workflows/precommit-config-ci.yml) | 配置专用触发、真实 hooks 验证、与主门禁的衔接 | 未运行会修改文件、解析网络依赖的 hooks |
| [sidecar.yml](../.github/workflows/sidecar.yml) | 四平台矩阵、Rust 质量门禁、覆盖率上传、draft 发布与 API 调用 | 未构建 Rust 或发起真实 Release 写请求 |
| [finbert-train.yml](../.github/workflows/finbert-train.yml) | 手动输入、指纹路由、训练/发布权限拆分、效果 Gate、工件及发布状态转换 | 未训练模型，也未复核模型数值正确性 |
| [flet-nightly.yml](../.github/workflows/flet-nightly.yml) | 固定依赖兼容检查、诊断脱敏、失败 Issue 去重 | 未启动 Flet/浏览器或创建 Issue |
| [release-please.yml](../.github/workflows/release-please.yml) | PAT 触发设计、仅创建 Release PR 的模式 | 未校验 token 权限或生成 PR |
| [release-tag-guard.yml](../.github/workflows/release-tag-guard.yml) | 版本/tag 三态、失败告警、分页和 Issue 去重 | 未查询仓库 tag 或发送通知 |
| [renovate.yml](../.github/workflows/renovate.yml) | 触发、鉴权变量、运行参数 | 未请求真实 token 鉴权或运行依赖机器人 |
| [cache-prune.yml](../.github/workflows/cache-prune.yml) | 仅清理非 main 且闲置缓存的条件 | 未删除任何缓存 |
| [codeql.yml](../.github/workflows/codeql.yml) | 触发、语言、配置入口与权限 | 未执行 CodeQL 扫描 |
| [gitleaks.yml](../.github/workflows/gitleaks.yml) | 全历史 checkout、扫描入口与权限 | 未执行 secrets 扫描 |
| [scorecard.yml](../.github/workflows/scorecard.yml) | 触发、凭据持久化设置、SARIF 输出 | 未执行 Scorecard |

关联检查包括 [.pre-commit-config.yaml](../.pre-commit-config.yaml)、[pyproject.toml](../pyproject.toml)、[路径分类器](../scripts/classify_changed_paths.py)、[diff coverage](../scripts/check_diff_coverage.py)、[per-file coverage](../scripts/check_per_file_coverage.py)、[flaky 探测](../scripts/detect_flaky.py)、[Dependabot 配置](../.github/dependabot.yml)、[Renovate 配置](../.github/renovate.json)及第 6 节列出的契约测试。

### 2.3 检视过程

1. **覆盖建模**：建立事件 → 分类 → job → Gate / artifact / Release 的路径。
2. **风险深挖**：优先检查条件跳过、改名、外部 API、覆盖率文件生命周期及副作用恢复。
3. **反证验证**：核对同类发布实现、锁定 Action 的上游源码和官方 API；运行不加载业务 fixture 的定向测试与隔离探针。

采用单会话手工独立视角检查：契约一致性、故障路径、回归风险和反证。没有委派子代理，也没有把“测试已有断言”直接当作实现正确的证明。

## 3. 当前链路与有效保护

### 3.1 主路径

| 场景 | 当前路径与信号 |
|---|---|
| 普通代码 PR | `Changes` → Linux static / DB / tests、Windows unit、5 组 Windows E2E、2 平台 embedded；`CI Gate` 聚合结果；文档检查独立运行 |
| 非代码类 PR | `Changes`、两组 fast lint 和 `CI Gate`；重型 job 按分类跳过；文档 required check 仍运行 |
| fork / Dependabot PR | Linux static / DB / tests 显式跳过并由 Gate 允许；Windows、E2E、embedded 保留；属于当前文档化策略 |
| merge queue | 主 workflow 和文档 workflow 支持 `merge_group`；分类按代码类处理 |
| 应用 tag 发布 | 测试与迁移 → 4 个 Windows 变体 → 本地工件校验 → draft → 12 资产服务端摘要校验 → publish；另等待 release-only sidecar smoke |
| sidecar tag 发布 | 四平台 lint/test → 四平台构建，结合 audit → 单一发布者 → draft / 校验 / publish |
| 夜间 | 完整主 CI 路径之外，额外执行 flaky/pollution 探测和 CUDA wheel 预热；另有独立 Flet、tag、缓存维护任务 |

### 3.2 应保留的设计

- `CI Gate` 使用 `always()` 收集失败，逐项区分 success、允许的 skipped 和意外 skipped，避免简单依赖链的假绿。
- `build-windows` 已等待 Linux 测试、static、DB、Windows unit、E2E 和 embedded；`release-windows` 另等待真实 sidecar smoke。不存在“完全不等测试就发布”的缺口。
- 13 个 workflow 中的 137 处外部 Action 引用均以 40 位 SHA 固定；这证明引用形式，不代表已审计所有上游实现或传递下载内容。
- Windows E2E 当前列举的 18 个测试文件与实际 18 个测试文件完全一致，没有现存漏列。
- 覆盖率的整体 85%、默认单文件 80%、strict diff 80% 与分层 advisory 有明确区分；不应把 advisory 误报为失效的强制门禁。
- 原子发布、已发布版本拒绝覆盖、sidecar 提交绑定与日志脱敏是有效控制，不应为提速而移除。

## 4. 发现与修复建议

### 4.1 汇总

| ID | 严重度 | 类别 | 问题 | 置信度 |
|---|---|---|---|---|
| CI-F01 | P1 严重 | 确定缺陷 | sidecar 资产上传请求发往 API 主机而非上传主机 | 高：完整发布路径 + 官方契约 |
| CI-F02 | P2 重要 | 确定缺陷 | 改名分类只看新路径，可能把生产代码移除判为非代码变更 | 高：分类执行 + 入口数据提取 |
| CI-F03 | P2 重要 | 确定缺陷 | 集成覆盖率已追加，但 per-file/diff/Codecov 消费陈旧 JSON/XML | 高：隔离动态复现 |
| CI-F04 | P2 重要 | 确定缺陷 | PR 漂移修复在 detached HEAD 下调用自动 PR Action，却未提供 base | 高：可达条件 + 锁定 Action 源码 |
| CI-F05 | P2 重要 | 待验证风险 | 独立 sidecar / pre-commit 检查未进入已声明的 required 闭环 | 本地配置事实高；实际合并约束未验证 |
| CI-F06 | P3 一般 | 确定缺陷 | Rust lcov 生产路径与 artifact 上传路径不一致 | 高：路径语义可直接定位 |
| CI-F07 | P2 重要 | 实测运行期问题 | CUDA 冷构建超时无产出；产出可观测性属于非阻断改进 | 超时/无产出事实高；内部编译瓶颈未定位 |

### CI-F01：sidecar 发布资产使用了错误的上传端点

**位置**：[sidecar.yml](../.github/workflows/sidecar.yml)，`jobs.publish-release` 的 `Create or reuse draft Release and upload 12 assets`；上传命令位于第 469 行。

- **触发条件**：推送 sidecar tag，四平台构建、audit 和本地资产校验成功，进入 draft 创建/上传阶段。
- **当前行为**：执行 `gh api --method POST ... "repos/${GH_REPO}/releases/${RELEASE_ID}/assets"`。相对 REST 路径使用普通 API 主机；上传步骤没有读取 `upload_url`，也没有指定 `uploads.github.com`。
- **预期行为**：GitHub 资产上传接口要求使用 Release 返回的 `upload_url`；官方示例主机为 `[https://uploads.github.com](https://uploads.github.com)`。普通 API 的资产列表路径不能替代上传 URL。
- **影响**：发布无法完成合法资产上传；可能留下 draft，但后续校验/正式发布不会执行。应用的 embedded 发行又依赖稳定 sidecar，因此会影响后续交付。
- **反证**：另外两条发布链使用专门的 Release Action 或 `gh release upload`，不能为该原始 REST 调用提供自动主机改写。现有 sidecar 契约测试仅断言路径片段和 `--input` 存在，未检查请求主机。
- **最小建议**：取得并验证 Release 的 `upload_url`，剥离 URI template 后向该完整 URL 上传二进制；也可改用 `gh release upload`。两种路径均须保留单一 publisher、draft-first、已发布版本拒绝覆盖、资产集与 digest 复验，不使用 `--clobber` 改写正式资产。
- **配套测试前提**：[test_sidecar_release_workflow.py](../tests/unit/test_sidecar_release_workflow.py) 的 `test_no_matrix_release_creation_and_no_clobber_anywhere` 当前对整份 workflow 禁用 `gh release upload`。若选择 CLI 方案，必须同步将该断言改为验证“仅单一 publisher 可上传、四平台构建不执行发布写操作”，保留其余不可变性检查；不能只改 workflow，也不能直接删除该测试。
- **验收**：用本地假 HTTP 服务/传输层 stub 断言实际上传主机、`name` 查询参数和原始二进制 body；CLI 方案还须验证调用仅发生于 publisher。注入任一资产上传失败，断言不发生 `draft=false`。最终由授权的发布冒烟验证真实 API。

**证据边界**：已确认静态契约错误；没有对真实仓库发起 POST，也不声称取得了线上 404/405 日志。

官方依据：[GitHub 上传 Release 资产](https://docs.github.com/en/rest/releases/assets?apiVersion=2022-11-28#upload-a-release-asset)、[gh api](https://cli.github.com/manual/gh_api)。

### CI-F02：重命名事件丢失旧路径，破坏分类的保守性

**位置**：[ci_cd.yml](../.github/workflows/ci_cd.yml) 的 `changes / Classify changed paths`，第 79 行只提取 `.[].filename`；[classify_changed_paths.py](../scripts/classify_changed_paths.py) 的 `CODE_PATTERNS`、`classify`。

- **触发条件**：GitHub 将一个代码文件识别为重命名，旧路径命中代码规则，新路径不命中；例如生产模块从 `services/example.py` 移到 `docs/example.txt`。
- **当前行为**：分类输入仅保留新路径，丢失 `previous_filename`；新路径被判为 `code=false`，Linux/Windows 业务测试、迁移及 E2E 均可能跳过。
- **影响**：删除有效生产模块这类实质变更被当成文档类变更，聚合门禁按设计接受重型 job 的 skipped。fast lint 不验证运行期导入，文档测试也不等价于业务测试。
- **执行证据**：实际分类器对 `main.py` 返回 True，对 `docs/renamed-main.md` 返回 False；workflow 的 jq 表达式确定不会将旧路径传入。若 GitHub 将操作拆成 delete/add，旧代码路径会被捕获，因此问题限定于 `status=renamed`，不是所有移动必然发生。
- **最小建议**：同时提取 `filename` 与存在的 `previous_filename`，对两者取并集再分类；保留 API 故障与分页上限的失败关闭语义。
- **验收**：补充代码→文档、文档→代码、代码→代码、文档→文档四类 renamed API fixture；前三类凡任一路径命中即 `code=true`，最后一类仍可轻量运行。

### CI-F03：最终覆盖率与下游报告不是同一份数据

**位置**：[ci_cd.yml](../.github/workflows/ci_cd.yml)，`ci-checks` 的 `Run Unit Tests`、`Run Integration Tests` 及后续 coverage 步骤；消费者为 [check_per_file_coverage.py](../scripts/check_per_file_coverage.py) 和 [check_diff_coverage.py](../scripts/check_diff_coverage.py)。

- **当前行为**：单测生成 XML 和 JSON；集成测试通过 `--cov-append` 更新底层覆盖率数据，但只指定 `--cov-report=term-missing`，未重新生成 XML/JSON。
- **影响**：终端整体覆盖率可能反映追加结果，而 per-file、diff coverage、Codecov 和上传 artifact 仍反映单测阶段。集成测试独有覆盖被遗漏，可造成误阻断及报告口径不一致；本次不据此推断会放过低覆盖变更。
- **最小复现**：两函数模块中，单测只调用第一个函数，集成测试调用第二个函数。第一次运行显示 75% 并写 JSON/XML；第二次追加后终端显示 100%，`.coverage` 已含新增执行行，但 JSON/XML 的 SHA-256 均未变化。

```text
unit phase:                    75%
integration phase, appended:  100%
JSON executed lines:          [1, 2, 5]
appended data lines:          [1, 2, 5, 6]
coverage.json unchanged:      True
coverage.xml unchanged:       True
```

- **最小建议**：最小改动是在集成命令中补充 `--cov-report=xml --cov-report=json`，让 per-file、diff、Codecov 和 artifact 消费最终综合数据；也可在两个阶段完成后集中 report/export，但不得关闭原有阶段门禁。默认保留“单测自身整体覆盖率 ≥85%”及综合整体 ≥85%、默认单文件 ≥80%、strict diff ≥80% 的现有要求，分层 advisory 仍为 advisory；若要调整这些策略，须另行确认。
- **验收**：只由集成测试覆盖的变更行必须进入最终 JSON/XML；per-file、diff、集成后的终端报告和 Codecov 输入一致。分别验证单测自身低于 85%、最终综合覆盖率不达标及 per-file/diff 不达标时仍失败，不能以导出修复为由弱化既有门禁。

### CI-F04：PR 场景的依赖漂移自动修复缺少 base

**位置**：[ci_cd.yml](../.github/workflows/ci_cd.yml)，`requirements-drift` → `update-requirements` → `Create PR for requirements update`。

- **触发条件**：同仓 PR 被分类为代码变更，requirements 重编译有差异，非 Dependabot，所需 PAT 已配置。
- **当前行为**：`update-requirements` 条件允许 PR 事件；checkout 未提供 ref，PR 默认检出合并提交；随后调用 `create-pull-request`，但输入没有 `base`。
- **预期/证据**：当前锁定 Action 源码对 `WorkingBaseType.Commit && !inputs.base` 直接抛错；这是 detached HEAD 场景的明确输入要求。
- **影响**：本应创建的漂移修复 PR 失败；该 job 又不在 `CI Gate` 中，因此可能出现聚合 required check 通过、旁路修复失败的状态。另有固定共享分支在多 PR 场景的归属问题，但本次不把未复现的竞态单独定为缺陷。
- **最小建议**：优先让 PR 只检测漂移，把自动修复限制到 main push 或 main 上手动运行，符合现有文档的主干同步模式。若确需 PR 分支修复，必须明确 base/ref 以及各 PR 的独立分支命名策略。
- **行为调整前提**：main-only 写回会停止 PR 阶段主动创建同步 PR，是有意的行为调整，不是无行为变化的配置修补。实施前须确认维护策略，并同步 [CI/CD 规范](../docs/guides/ci-cd.md)。若保留 PR 写回，应使 checkout、目标 base 和独立 bot 分支对应当前 PR 的修复意图；不能机械补上 `base=main`，把尚未合并的 PR 内容带入共享主干同步分支。
- **验收**：覆盖 main、有漂移的同仓 PR、fork、Dependabot、非 main dispatch；每种场景断言检测、是否允许写操作、目标 base 和分支名，不需要真实创建 PR。选择 main-only 时，必须验证 PR 与非 main dispatch 均只检测、不写回。

上游依据：[锁定 Action 源码](https://github.com/peter-evans/create-pull-request/blob/5f6978faf089d4d20b00c7766989d076bb2fc7f1/src/create-pull-request.ts)、[https://github.com/peter-evans/create-pull-request/blob/v8/docs/concepts-guidelines.md#events-which-checkout-a-commit](https://github.com/peter-evans/create-pull-request/blob/v8/docs/concepts-guidelines.md#events-which-checkout-a-commit)。

### CI-F05：独立补偿检查的 required 闭环风险（待远端核验）

**位置**：[路径分类器](../scripts/classify_changed_paths.py)、[主 CI 聚合 Gate](../.github/workflows/ci_cd.yml)、[sidecar workflow](../.github/workflows/sidecar.yml)、[pre-commit 配置 workflow](../.github/workflows/precommit-config-ci.yml)；required checks 的声明见 [CI/CD 规范](../docs/guides/ci-cd.md)。

- **已确认事实**：Rust 源码、sidecar workflow 和 pre-commit 配置不命中主分类器；sidecar/pre-commit 各有独立 PR 补偿 workflow，但均不支持 `merge_group`。`CI Gate` 不收集其结果，文档只声明 `CI Gate` 与 `Docs Consistency Check` 为 required。
- **遗漏场景**：只改 Rust 或 hooks 时，独立检查失败，两个已声明 required check 却可能通过；merge queue 的合成提交也没有这两类独立补偿验证。
- **影响边界**：这是本地配置与已声明策略间的缺口，不等于已经证明线上可以绕过合并保护。远端可能存在额外 ruleset、组织策略或人工阻断；当前未取得这些证据。
- **最小建议**：先核对远端 ruleset、额外 required workflow/check 及 merge queue 实际策略；若已有等效强制约束，不重复增设 gate。仅在确认缺少保护后，为两类检查建立 PR/merge_group 上稳定出现的 gate，内部按改动决定执行或有依据地跳过，并由授权维护者同步 required 设置。可通过同一 workflow 内路由或 reusable workflow 汇总，**不能直接用 `needs` 引用另一 workflow 的 job**。
- **验收**：先取得远端约束证据再决定是否修改。若新增 gate，分别构造“只改 Rust”“只改 pre-commit”“普通文档”三类变更；对适用检查注入失败/取消，required gate 必须红；无关变更允许有解释的 skip；merge_group 重复验证同样语义。

**分类**：条件性门禁风险，不计为确定缺陷；已确认的是本地配置与文档声明间的衔接缺口，实际合并保护仍待远端证据，不作“已发生绕过”结论。

### CI-F06：Rust 覆盖率 artifact 指向错误目录

**位置**：[sidecar.yml](../.github/workflows/sidecar.yml)，`lint-test.defaults.run.working-directory`、`cargo llvm-cov` 和 `Upload coverage report (Linux only)`。

- **当前行为**：run 默认目录为 `sidecars/qtrading-pg-sidecar`，`--output-path lcov.info` 将报告写到该子目录；`upload-artifact` 的 `path: lcov.info` 却相对工作区根解析。
- **影响**：门禁本身仍可执行，但报告不能按当前路径上传；未设置 `if-no-files-found: error` 时，缺报告通常只告警，容易留下“检查成功却没有覆盖率证据”的状态。
- **最小建议**：上传路径补足仓库相对子目录，并为这一必需报告设置 `if-no-files-found: error`；不要仅依赖 run 的默认工作目录。
- **验收**：生成与消费路径解析后必须相等；删除报告的测试场景应使上传校验失败。无需为此运行全平台 Rust 构建。

### CI-F07：CUDA 预热超时无产出（实测运行期问题）

**位置**：[ci_cd.yml](../.github/workflows/ci_cd.yml) 的 `build-llama-cuda-wheel`；直接证据为 [run #5941 的 CUDA job](https://github.com/shi00/qTrading/actions/runs/41995891233/job/127138822560)。

- **实际触发**：schedule 执行预热；日志显示 `release not found`，未能读取 wheel bank，进入冷编译。目标为 llama-cpp-python 0.3.36、CUDA 12.4.1、Python 3.14，包含既定六项 CUDA arch 配置。
- **实际结果**：安装 Toolkit 用时 4 分 51 秒，编译步骤执行 90 分 02 秒后触发 90 分钟超时；日志明确报告 canceled/timeout。后续 CUDA 校验和发布 bank 两步均为 skipped，因此**本次没有通过该 job 发布可复用 wheel**。
- **第二次证据**：[手动运行 #5937 的预热 job](https://github.com/shi00/qTrading/actions/runs/41962808967/job/127035430343) 同样在编译 90 分 02 秒后明确超时，job 仍为 success，校验和发布仍跳过；两条应用 CUDA 构建腿也未完成依赖安装阶段，约 120 分钟后取消。取消原因的证据边界见 §5.8，不把它们未经确认地全部称为 step timeout。
- **状态解释与改进机会**：编译步骤设有 `continue-on-error`；API 中步骤和 job 的 conclusion 均为 success，这是加速项允许失败的既定设计，不是绿色 job 本身构成缺陷。后续用 `steps.build-wheel.outcome == 'success'` 阻止坏产物发布是正确保护。可增加独立的 `cache_hit / built / published / timeout` 结果契约，便于监测实际产出；不能从绿色确认“已预热”。
- **影响**：本次消耗 95 分 18 秒 Windows job 时间，约占全部原始 job 秒数的 50.14%，却未建立预期的加速资产；它也是本次 workflow 整体完成时间的决定项。如果后续仍未命中 bank，会再次尝试冷编译，但本次不推断每天都重现。
- **定级边界**：已证实的是两次预热冷路径未在预算内完成、没有经该 job 发布 wheel，属于运行期有效性与维护成本问题，不计入五项确定缺陷。结构化产出信号是非阻断改进建议，不能据此认定现有非阻断设计违反契约。未从缓冲的 pip 日志确定 NVCC/C++ 内部瓶颈，不能断言编译死锁或直接要求降低 GPU 覆盖范围。
- **最小建议**：首先增加最终 summary/结构化结果，区分命中、成功构建并发布、超时和其他失败；将预热与主质量检查的反馈视图解耦。下一次受控冷构建采集编译详细日志、并行度、资源使用和阶段时间，再决定构建预算或构建方式；不要只反复提高 timeout。
- **验收**：注入编译超时，质量/发布阻断语义保持原样，但预热结果必须明确为 timeout、published=false；冷路径须有实际成功上传且通过校验的证据；热路径须证明读取并使用了同一 key 的有效资产，而不是仅观察 job success。

## 5. 优化建议与实施顺序

### 5.1 建议分批推进

以下是建议排期，不是已执行变更，也不是工时承诺。

| 顺序 | 内容 | 目标 / 验收 | 建议责任角色 |
|---|---|---|---|
| A：发布正确性 | CI-F01；同时修复 CI-F06 | 正确上传资产；失败不 publish；覆盖率工件必达 | 发布 / Rust 维护者 |
| B：门禁可信度 | 修复 CI-F02；先核验 CI-F05 | 改名不能漏分类；取得远端规则证据，仅在缺少等效保护时补 gate | CI / 仓库管理员 |
| C：数据与自动化 | CI-F03、CI-F04 | 最终报告一致；依赖修复路径对各事件可预测 | 测试 / 依赖维护者 |
| C：夜间维护有效性 | CI-F07，非阻断改进 | 保留加速项失败不阻断发布；预热是否实际建立 bank 有独立证据 | 构建 / CI 维护者 |
| D：有测量的提速 | 下列 O01–O07 | 以真实 run 的 P50/P95、runner 分钟和失败率验收 | CI 维护者 |

### 5.2 非阻断优化

| ID | 建议 | 原因与约束 |
|---|---|---|
| O01 | 将不需要 secrets 的 static 门禁从 fork/Dependabot 跳过策略中分离 | `ci-checks-static` 不使用 PG service，却继承整套跳过条件。可先恢复类型、架构、弱断言及静态依赖审计信号；不要给 fork 暴露 secrets，也不要改用不安全的特权 PR 执行方式 |
| O02 | 为文档检查建立最小依赖环境 | 现在 required 文档 job 安装 core/optional/dev 全量依赖，包含原生构建和无关浏览器栈。单元测试 autouse fixture 会导入 UI/数据层等业务模块，须先隔离业务 conftest 耦合、确认文档脚本及契约测试真实依赖，再缩减环境；不能只删除依赖或跳过必要 fixture 后声称等价 |
| O03 | 统一 lint 工具基线，并用真实解析/运行测试补充最低版本承诺 | 两个 fast lint 都调用同一版本 Ruff、同一个 `target-version=py313`；更换宿主 Python 不会使 Ruff 自动按不同语法目标工作。不要仅删掉 3.13 门禁，应替换为 3.13 编译/最小导入或契约测试。pre-commit 的 Ruff 独立环境仍使用浮动下界，未被现有 pin 一致性测试覆盖 |
| O04 | 增加轻量 workflow 专用验证 | actionlint、必要的 shell/Pwsh 语法检查，以及 HTTP/工件行为契约测试应覆盖所有 workflow 改动。保留现有静态字符串断言，但不要让其替代对上传地址、参数和事件状态的行为验证 |
| O05 | 收敛依赖安装与工具版本来源 | 复核确认三份锁文件在 Windows/Linux、Python 3.14 条件下无冲突 pin，具备评估联合安装的前提；但这不证明实际依赖 metadata 或原生 ABI 兼容。实施时仍须验证联合安装及 `uv pip check`，统一辅助工具版本来源，不顺带升级无关包。本次普通依赖安装不是主要瓶颈，优先级低于 CUDA 冷构建 |
| O06 | 让 advisory 结果可持续观察 | flaky job 只保留 advisory 结论时，建议输出结构化报告、summary，并按连续失败阈值通知。仍保留与 PR 主路径隔离，不直接恢复每 PR 十轮重跑 |
| O07 | 明确多产品发布和恢复操作手册 | sidecar 发布也设置 `make_latest=true`，模型发布亦需明确 Latest 策略。Latest 归属属于产品决策，须由维护者先确认；如果允许多产品轮换，不因此修复为“仅应用可成为 Latest”，也不把未定义策略当作故障。应用 tag 构建要求同提交 sidecar，手册仍应明确先发布同提交 sidecar、再发应用 tag |

补充观察：

- 自动 requirements PR 的正文未使用 [仓库 PR 模板](../.github/PULL_REQUEST_TEMPLATE.md) 的完整结构，与贡献流程存在漂移；修复 CI-F04 时一并对齐，避免再维护简化模板。
- [pre-commit 配置](../.pre-commit-config.yaml) 使用 `ruff>=0.16.7`，而 fast lint 有精确 pin。独立 hook 环境不会因主环境已装固定版本而自动对齐，建议纳入一致性检查。
- E2E 文件清单目前完整，建议新增集合相等的自动契约，防止以后新增文件忘记进入 matrix；无需改变目前保留文件顺序的分组设计。
- 不建议为缓存命中率盲目统一 Ubuntu 24.04 与滚动 latest 的原生构建缓存；键设计应同时考虑 Python ABI、runner/系统库兼容和 CPU/CUDA 后端。
- 现有单文件分层阈值是明确 advisory，Rust 覆盖率欠债也已有登记。优先关闭行为断点，不用机械抬高阈值代替正确性改进。

### 5.3 性能验收口径

现已取得同一提交的两次实际运行，事件分别为 schedule 和 workflow_dispatch，详见下节。可用于定位各次关键路径和比较触发面，不足以给出长期节省比例、成功率或 P95。后续基线仍应按事件、runner、冷热缓存及是否依赖变更分组，收集最近一段有效 run：

1. PR 到 required checks 完成的 P50/P95；
2. 各 job 的排队时间、依赖安装、测试、post-job 缓存上传时间；
3. runner 分钟与取消后浪费；
4. 缓存命中率、体积及冷构建频率；
5. 失败类别、基础设施误报率、flaky 率及恢复耗时。

优化只有在这些指标改善、且适用检查集合与失败阻断语义不变时才验收。不建议先大规模拆 reusable workflow 或扩矩阵。

### 5.4 用户补充的实际运行：身份与可比性

证据入口：[AStockScreener CI/CD · run 41995891233](https://github.com/shi00/qTrading/actions/runs/41995891233)。

| 字段 | API 结果 |
|---|---|
| run number / attempt | #5941 / 1 |
| 事件 / 分支 | `schedule` / `main`，**不是 PR，也不是发布构建** |
| 被测提交 | `fb381fa4ecab5b9071704f6ea5dec5e9c44613dd` |
| 运行日期 | 2026-11-05 |
| 开始 / 最终更新时间（UTC） | 08:37:18 / 10:12:40 |
| 对应北京时间 | 16:37:18 / 18:12:40 |
| 整体时间口径 | 上述时间差 95 分 22 秒；最后一个 job 于 10:12:39 结束，与最终更新时间相差 1 秒 |
| job 返回数量 | 25；16 个执行，9 个 skipped；API 对执行 job 均返回 success，但 CUDA 日志存在被吸收的编译超时 |
| 未执行的关键信号 | `CI Gate`、requirements 漂移、安装包构建/发布等按事件条件跳过，不能据此认定 PR required checks 或发布链成功 |

取回了[该提交的主 workflow](https://github.com/shi00/qTrading/blob/fb381fa4ecab5b9071704f6ea5dec5e9c44613dd/.github/workflows/ci_cd.yml)，与本地主 workflow 做 YAML 结构比较：唯一结构差异是 embedded Linux runner 从该提交的 `ubuntu-latest` 改为本地的 `ubuntu-24.04`。CUDA、flaky、Linux unit 等 job 定义没有结构差异；但 runner 镜像、缓存和依赖环境仍会变化，不能把本次耗时当作以后每次运行的固定值。

### 5.5 job 与步骤耗时

以下为 `completed_at - started_at`，没有相加并行 job 来冒充整体墙钟；skipped 的占位时间戳不参与求和。

| 执行 job | job 时长 | 主要步骤 / 说明 |
|---|---:|---|
| [CUDA wheel 预热](https://github.com/shi00/qTrading/actions/runs/41995891233/job/127138822560) | **95:18** | 编译 90:02，Toolkit 4:51；超时，无校验/发布 |
| [Flaky detection](https://github.com/shi00/qTrading/actions/runs/41995891233/job/127138822839) | **43:49** | 十轮探测 38:59，装依赖 3:14，pollution 1:19 |
| [Linux unit + integration](https://github.com/shi00/qTrading/actions/runs/41995891233/job/127138924528) | **8:26** | 单测 5:35，集成 1:40，依赖安装 0:02 |
| [Windows unit](https://github.com/shi00/qTrading/actions/runs/41995891233/job/127138924558) | 6:00 | 单测 4:47，依赖安装 0:35 |
| [Windows E2E core-a](https://github.com/shi00/qTrading/actions/runs/41995891233/job/127138924536) | 5:52 | E2E 4:21，依赖安装 0:42 |
| Windows E2E settings | 5:14 | E2E 3:35，依赖安装 0:43 |
| Windows E2E onboarding | 5:13 | E2E 3:35，依赖安装 0:40 |
| Windows E2E scenarios | 4:27 | E2E 3:07，依赖安装 0:35 |
| Linux static | 3:52 | Pyright 1:26，pip-audit 1:05，pre-commit 0:37 |
| Embedded Linux | 3:47 | E2E smoke 1:20、真实 onboarding 0:45、集成 0:32、Rust 构建 0:23 |
| Windows E2E core-b | 3:14 | E2E 2:02，依赖安装 0:30 |
| Embedded Windows | 2:58 | 本次不是常规检查尾部 |
| DB migration | 1:19 | 本次不是常规检查尾部 |
| Fast lint 3.13 | 0:15 | 轻量检查 |
| Fast lint 3.14 | 0:12 | 轻量检查 |
| Changes | 0:07 | 非 PR 按代码类处理 |

flaky 日志确认：**10 轮均 `returncode=0, collected=19024`，最终未检测到 flaky**。这不是探测挂死；38 分 59 秒主要是在重复全套单测，是否每晚都做十轮属于成本/检出能力的策略取舍。

### 5.6 三种时间必须分开

| 观察对象 | 本次结果 | 含义 |
|---|---:|---|
| 整个 schedule workflow | 约 **95:22** | 被 CUDA 预热拖尾 |
| 排除 CUDA 后，其余执行 job 的最晚结束点 | 开始后 **43:52** | 仍被 flaky 拖尾，不能只拆 CUDA 就声称反馈变为 9 分钟 |
| 同时排除 CUDA 与 flaky 后，常规检查最晚结束点 | 开始后 **8:44** | Linux unit/integration 于 08:46:02 UTC 结束，是本次常规检查尾部 |
| 16 个执行 job 的时长总和 | **190:03 原始 job 分钟** | 并行执行的资源占用合计，不是墙钟，也不是按 OS 倍率折算后的账单 |
| CUDA + flaky 的时长总和 | **139:07，约 73.20%** | 原始 job 时长的主要成本；CUDA 50.14%，flaky 23.06% |
| 其他 14 个 job 的时长总和 | **50:56** | 本次常规检查的原始 job 消耗 |

如果仅把这两个维护任务移到独立 workflow，在**任务耗时与调度不变**的回放假设下，常规质量 workflow 的可见完成点可从约 95 分钟回到约 8 分 44 秒。**这只是反馈视图/生命周期解耦，不会自动节省 139 分钟计算资源**；任务仍执行就仍有成本，也不能把此结果当成 PR 的 P95，因为本次没有执行 PR Gate 和独立文档 workflow。

### 5.7 基于 schedule 样本调整的优化重点

1. **优先处理 CUDA 预热的有效产出，而不是继续优化 fast lint。** 先落地 CI-F07 的产出状态，再定位冷编译；本次 timeout 没有建立 bank，不能用绿色 run 推断以后已进入热路径。保留现有 GPU arch 覆盖要求，不凭一次超时缩减产品兼容面。
2. **解耦夜间维护与常规质量反馈。** 可分别承载 CUDA 预热和 flaky 探测；复用时保留 main/ref 限制和最小写权限。拆分本身改善反馈解释性，资源节省需另外证明。
3. **按检出目标调整 flaky 策略，而不是只减轮数。** 可评估每日核心/变更相关集合、周期性全量十轮；须用历史 flaky 发现和遗漏情况验收。该次 19,024 用例 × 10 轮提供的信号应明确保留在哪里，不能把降采样包装成零损失提速。
4. **常规检查若还要提速，先看 Linux 单测 5:35 和集成 1:40。** 单测已启用 xdist，不建议盲目再加 worker；先读慢用例和 fixture 分布。若拆分测试，最终 coverage 必须正确合并，并同时修复 CI-F03 的报告同源问题。
5. **这次不支持把依赖安装认定为主瓶颈。** Linux 测试 job 安装只用 2 秒，Windows E2E 为 30–43 秒；文档最小环境仍有工程价值，但该独立 workflow 没在此 run 中出现，不能从这份样本给它估算收益。

正确性优先级仍维持 CI-F01 等发布/门禁修复在前；在“耗时优化”这一单独维度，CUDA 无产出与 flaky 全量重跑应优先于 lint 微调。

### 5.8 第二次运行：同一提交、不同触发面

证据入口：[run 41962808967 / #5937](https://github.com/shi00/qTrading/actions/runs/41962808967)。两次 `head_sha` 均为 `fb381fa4ecab5b9071704f6ea5dec5e9c44613dd`，无需用代码改动来解释两次触发 job 集合的差异。

| 指标 | #5937，41962808967 | #5941，41995891233 |
|---|---|---|
| 事件 / 分支 | workflow_dispatch / main | schedule / main |
| run 结论 | cancelled | success，但预热编译失败被吸收 |
| 开始 / 最终更新时间（UTC） | 03:37:57 / 05:54:56 | 08:37:18 / 10:12:40 |
| 整体时间差 | **136:59** | **95:22** |
| job 返回与结论 | 30：22 success、3 cancelled、5 skipped | 25：16 success、9 skipped |
| 首批 job 启动前区间 | **8:00** | 约 0:03 |
| 常规 14 项检查尾部，从首个 job 开始计 | **8:38** | **8:41** |
| 常规检查完成点，从 run 开始计 | 16:38 | 8:44 |
| Linux unit/integration job | 7:12 | 8:26 |
| Windows unit job | 8:08 | 6:00 |
| CUDA 预热 job | 97:05；编译 90:02 后超时，无发布 | 95:18；编译 90:02 后超时，无发布 |
| Flaky job | 45:29，job cancelled；探测十轮已完成 | 43:49，success |
| Windows skip revalidation | 3 个分组全部执行 | 不触发 |
| Windows 应用构建 | 4 个变体均启动，2 个 CUDA cancelled | 不触发 |
| 正式 Release | 不触发 | 不触发 |
| 原始执行 job 时长合计 | **485:27** | **190:03** |

**注意起点差异**：#5937 于 03:37:57 开始记录，首批 job 直到 03:45:55 才创建、03:45:57 才启动。前 7 分 58 秒发生在 job 创建前，不能把完整 8 分钟称作 runner 分配耗时；具体属于工作流并发等待还是平台调度，尚未取得等待原因。对比常规测试速度时，应使用首个 job 起点：两次常规检查尾部仅相差 3 秒。

#### 手动运行的构建长尾

| job | job 时长 | 关键阶段 | 结果 |
|---|---:|---|---|
| [CUDA 应用包](https://github.com/shi00/qTrading/actions/runs/41962808967/job/127038217325) | **120:16** | Toolkit 6:16；依赖安装 113:18 | cancelled，未进入 PyInstaller |
| [embedded-CUDA 应用包](https://github.com/shi00/qTrading/actions/runs/41962808967/job/127038217408) | **120:17** | Toolkit 21:24；依赖安装 97:43 | cancelled，未进入 PyInstaller |
| [CPU 应用包](https://github.com/shi00/qTrading/actions/runs/41962808967/job/127038217292) | 7:02 | 无 CUDA 工具链编译 | success，非 tag artifact |
| [embedded-CPU 应用包](https://github.com/shi00/qTrading/actions/runs/41962808967/job/127038217355) | 7:47 | PyInstaller 2:23、ISCC 3:16 | success，非 tag artifact |

CUDA 包日志明确显示 `release not found` → 回退内联源码编译 → `Building llama-cpp-python==0.3.36`，下载阶段数秒内完成，随后长时间停留于原生构建。取消时清理了仍存活的 NVCC/PTX 编译进程。**“Install dependencies”这个步骤名隐藏了原生 CUDA 编译，不能把其 113 分钟解释成包下载慢。**

两条构建腿都在 120 分钟预算边缘取消，符合当前 `build-windows.timeout-minutes=120` 的边界；但已取日志只给出 `The operation was canceled`，没有取得服务端取消注释，因此**不排除人工取消，不能将“取消原因=超时”写成已证事实**。它们没有在现有运行窗口内产出 CUDA 包则已确定。相比之下，两次预热日志均明确写有 `has timed out after 90 minutes`，该超时原因可以确定。

预热 job 不在应用构建 `needs` 中：#5937 里，预热和两条 CUDA 腿均在 bank 不可用时尝试冷构建，形成三个同版本/同 key 方向的编译任务。三者合计占用 **337 分 38 秒原始 job 时间，占该 run 69.55%**，却没有建立可用 bank 或产出 CUDA 应用包。这是后续优先处理冷路径的直接依据，而不是继续扩大 matrix 的依据。

#### 额外复验与 flaky 的成本

- 三条 Windows skip revalidation 共用 **33 分 14 秒** job 时间；其中依赖安装合计 **24 分 28 秒**，约占 73.62%。这部分与普通 Windows E2E 的 30–43 秒安装不同，已有主矩阵缓存优化并未同样覆盖该复验路径。只有在复验被频繁使用时，才值得优先为其补齐受控缓存；常规 PR 没有这笔成本。
- [#5937 的 flaky job](https://github.com/shi00/qTrading/actions/runs/41962808967/job/127035430421) 探测步骤耗时 **40 分 32 秒**；日志确认十轮全部 `returncode=0, collected=19024`，最终无 flaky，但整个 job 的 API 结论仍为 cancelled，部分 post 步骤 skipped。
- 探测之外还有安装依赖 3:22、pollution 1:24 和启动/收尾；当前总预算 45 分钟几乎没有余量。应区分“十轮探测完成”“job 完整结束”“后处理被取消”，不能把此次 cancelled 当成发现 flaky，也不能只看探测 step success 宣称 job 完整成功。取消来源同样未取得独立注释，保留原因判断。

### 5.9 两次证据合并后的优先级

1. **先验证 CUDA 冷构建能真正完成并建立 bank，再谈热路径收益。** 两次预热均明确超时，手动运行两条 CUDA 腿也未完成。下一步应在授权的受控构建中采集原生编译阶段、并行度和资源证据；不把取消简单归因于网络、PyInstaller 或安装器。
2. **避免 bank 缺失时三个 job 重复尝试同类编译。** 可先完成一次独立、受控的 bank 构建，再保持现有消费者的校验与回退；若进一步改成单 producer 分发给两条 CUDA 腿，需要显式评估阻断关系和失败回退。默认不得直接把非阻断预热 job 加入发布构建的必成功 `needs`，否则可能在预热失败或跳过时阻断原本可回退的构建，改变既定契约。
3. **按目的拆分质量、应用构建、预热与复验入口。** 当前 main 的 push/schedule/dispatch 还共享同一 workflow concurrency group，非 PR 不取消进行中的运行；长维护项会形成阻塞后续 main 运行的可达路径。但不能据此断定 #5937 的 8 分钟等待一定由并发锁导致。拆分时同时校验 group、权限、ref 限制和复用关系。
4. **给 flaky 完整生命周期留出可观测的预算。** 本次并非十轮测试没跑完；预算覆盖依赖、pollution、十轮与清理才有意义。优先记录探测结果和 job 生命周期状态，再用更多样本决定轮数、集合和预算，不依赖 job 级 `continue-on-error` 掩盖取消。
5. **常规质量链路目前约 8–9 分钟，先不要为它承担重大架构改造。** 两次从首个 job 计算的尾部几乎相同，但具体最慢 job 变化：一次 Linux 单测/集成，一次 Windows 单测。优化应看多样本慢用例和 fixture 分布，而不是只按单次最慢项机械拆分。

两次事件覆盖面不同，不宜把 136:59 与 95:22 的差额解释成代码性能回归，也不能从这两个样本计算有代表性的平均值或 P95。真实 PR required checks 和正式 tag 发布仍需各自样本。

## 6. 验证证据与局限

### 6.1 已运行

| 检查 | 结果 | 证明范围 |
|---|---|---|
| 13 个 workflow YAML 解析与 `needs` 引用检查 | 退出码 0；36 个 job 定义，依赖目标均存在 | 结构可解析、引用存在；不等价于 Actions schema 或运行语义全部正确 |
| 5 组现有 CI 契约测试 | 退出码 0；78 passed，3 warnings，3.04s | Action pin、Ruff pin、3.13 Gate 接线、现有分类及 sidecar 静态发布契约 |
| 报告复核时重跑同一契约子集 | 退出码 0；78 passed，3 warnings，2.53s | 证明现有契约断言通过，不等于上述缺陷已修复或未来方案已通过验收 |
| 三份 requirements 锁文件 pin 复核 | Windows/Linux、Python 3.14 各 202 个有效 pin 包，冲突数均为 0 | 仅确认锁文件条件约束一致；未执行联合安装或 `uv pip check`，不证明运行期 metadata/ABI 兼容 |
| 覆盖率追加最小复现 | 两阶段各 1 passed；探针退出码 0，确认陈旧报告 | CI-F03 的文件生命周期，不代表业务覆盖率为 75% 或 100% |
| 分类器执行与改名入口核对 | 退出码 0；新路径非代码、旧代码路径 True | CI-F02 的当前分类行为；未创建真实 GitHub rename PR |
| sidecar 上传与 lcov 路径探针 | 退出码 0；上传主机契约不符、报告路径不一致 | CI-F01、CI-F06 的静态契约 |
| E2E matrix 集合核对 | 18/18；missing=[]，unknown=[] | 当前文件级列举完整，不证明用例没有 skip |
| 自动 PR 输入与上游源码核对 | 默认 checkout、缺 base；锁定源码存在明确报错分支 | CI-F04 的可达失败路径 |
| 指定 run 的元数据、25 个 job 和全部 step 时间 | 16 执行、9 skipped；原始 job 秒数 11,403 | 本次事件与关键路径，不代表多次运行统计 |
| CUDA 与 flaky 日志核对 | CUDA 90 分钟超时、校验和发布 skipped；flaky 十轮均成功 | CI-F07 和实际维护任务成本 |
| 运行提交与本地主 workflow 结构对比 | 仅 embedded Linux runner 的 matrix 字段不同 | 限定耗时结论的配置可比性 |
| 第二次 run 的元数据、30 个 job 与全部 step 时间 | 22 success、3 cancelled、5 skipped；原始 job 秒数 29,127 | 同提交 workflow_dispatch 的额外构建/复验与生命周期 |
| 第二次 CUDA 构建、预热及 flaky 日志 | 预热明确 90 分钟超时；CUDA 包取消；flaky 十轮完成但 job cancelled | 区分 step、job、产出和取消原因，不把它们视为同一种失败 |

定向测试命令如下，执行时设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 和 `PYTHONDONTWRITEBYTECODE=1`，避免加载应用 fixture、外部插件或写入 pyc：

```powershell
python -B -m pytest --noconftest -p no:cacheprovider -o addopts= `
  tests\unit\test_classify_changed_paths.py `
  tests\unit\test_ci_gate_python313_lint.py `
  tests\unit\test_ci_ruff_pin_consistency.py `
  tests\unit\test_workflow_action_pins.py `
  tests\unit\test_sidecar_release_workflow.py -q --tb=short
```

三条 warning 均是关闭插件自动加载后，pytest 不识别现有 asyncio 配置项；不是测试失败，也不据此报告项目 asyncio 配置错误。

覆盖率探针在会话专用目录中创建两函数模块和两个单测，先使用 `--cov-report=term-missing --cov-report=xml --cov-report=json`，再使用 `--cov-report=term-missing --cov-append`；比较报告摘要与 `CoverageData.lines()`。探针未导入应用、未访问数据库或网络，临时文件在交付前清理。

### 6.2 已排除的候选误报

| 候选判断 | 不采纳原因 |
|---|---|
| Renovate 的 Authorization 被硬编码为星号 | 工具展示做了脱敏；本地布尔检查确认真实文件使用 token 变量，星号不是源码事实 |
| Windows 发布不等待 Linux static/DB | 实际 `build-windows.needs` 已包含这两项及 Linux 测试 |
| Windows E2E 已漏列测试文件 | 文件集合与 matrix 精确相等 |
| 分层覆盖率必须已强制 90%/85% | 当前配置明确为 advisory，文档也作此区分 |
| fork 跳过 Linux job 是无意 bug | 当前是文档化无 secrets 策略；本报告只建议拆出可安全运行的静态部分 |

### 6.3 未运行 / 未取得

- 未运行完整 Ruff、pyright、全量单测、集成、迁移、E2E、Rust、CUDA 或打包；本次没有修改这些实现，也不具备执行真实外部副作用的授权。
- 未执行 pre-commit：其中存在自动修复和网络重编译步骤，不符合本次只读检视边界。
- 未运行 actionlint：当前环境未提供该工具，未为检视安装工具。
- 未执行仓库文档一致性全套：仅新增本地忽略的报告，没有修订治理契约；[检视协议](../docs/reviews/ai-review.md) 亦区分只读检视与协议变更。
- 未调用真实发布上传、创建 PR/Issue、读取 secrets 或修改 ruleset；新增证据来自只读 API 和既有日志。
- 未验证服务端上传 HTTP 失败状态、远端 required checks 实际清单、组织策略，以及代表性样本的成功率、耗时分布和实际账单。两次指定 run 的时间与关键日志已补充；CUDA 包/flaky 的服务端取消注释未取得，不能确定取消发起者或原因。

**因此：78 项契约测试通过不代表项目 CI 全部通过；报告中没有完成修复，也没有发布验证通过的结论。**

## 7. 交付与后续复核

本次唯一持久工作区新增或修订文件为本报告，本轮仅按复核意见修订其内容。项目配置与代码保持原状；无 Git 快照下不作“工作区干净”承诺。单文件报告修订不触发跨文件实现隔离要求；会话目录中的复现文件不是项目改动。报告复核可采纳不构成执行代码修改、改变写回策略或修改远端 ruleset 的授权。

复核建议：

- CI-F01 修复须连同上传策略对应的契约测试调整，先做假服务行为验证，再由维护者执行受控发布冒烟；CI-F06 同时验证必需报告存在性。
- CI-F02 覆盖改名方向及聚合门禁分支；CI-F05 先取得远端规则证据，若确需补 gate，再覆盖失败、取消、意外 skip、普通文档及 merge_group。
- CI-F03 对最终 JSON/XML、阈值计算及 Codecov 输入做同源校验，并保持单测自身 85% 等原有门槛。
- CI-F04 明确 PR 只检测还是自动修复的边界，记录 main-only 写回属于行为调整，避免直接“补一个 base”掩盖分支归属问题。
- CI-F07 保留加速项不阻断的设计，但独立验收冷构建有效产出、热路径命中和超时结果；将夜间总时长与 PR 反馈、计算成本分开跟踪。
- 远端规则、环境保护及真实运行证据未补齐前，对实际合并/发布控制保留判断。

自查：未新增 DAO/表/业务缺失值赋值，R5/R17/R21 的代码变更检查不适用；没有修改选股或回测结论相关规则，无需对 R20/R21/R24 的实现作独立会话通过判定。TDD 与回归实现验收不适用；本次验证针对现状取证，不声称完成任何红绿修复周期。