# 技术债集中清理 Plans.md

作成日: 2026-07-15

---

## 执行环境（独立分支 + worktree 隔离）

> 对应 [CLAUDE.md §3.1 R18](./CLAUDE.md#31--绝对禁止) + [CONTRIBUTING.md「Git 工作流与分支策略」](./CONTRIBUTING.md#git-工作流与分支策略)。本计划属于"重构任务 + AI 助手驱动的多步骤实现任务"，必须 worktree 隔离。

| 项 | 值 |
|---|---|
| 主工作区分支 | `fix/audit6-issues`（当前）→ 合并后回到 `main` |
| 计划执行分支 | `chore/tech-debt-cleanup`（承载 Phase 1-6 实现） |
| 计划 worktree 路径 | `.worktrees/chore-tech-debt-cleanup/` |
| 分支命名规范来源 | CONTRIBUTING.md L145-163（`<type>/<scope>-<short-desc>`，`chore/` 用于构建/工具/依赖，全小写，≤ 50 字符） |
| worktree 标准工作流来源 | CONTRIBUTING.md L179-212 |

### 分支隔离原则

1. **本计划全程在 worktree 内执行**：Phase 1-6 的所有代码修改、commit、push 均在 `.worktrees/chore-tech-debt-cleanup/` 内进行
2. **R18 豁免项**：Phase 3.1（Phase R 正本恢复属 git 只读操作）、Phase 5.1（doc-lint 调研属 docs-only）可在主工作区执行
3. **Squash Merge 原则**：每个 Phase 完成后通过 PR Squash Merge 回 `main`，保持 main 历史干净。Phase 间存在依赖时，后续 Phase 的 worktree 应从已合并的前序 Phase 的 main 创建，而非从前序 Phase 的分支创建
4. **PR 策略**：6 个 Phase 串行 PR（Phase 1 → merge → Phase 2 → merge → ...），避免累积 PR 难以 review。若某 Phase 内有多个独立 task，可在同一 PR 内多个原子 commit

### worktree 创建/清理标准流程

```bash
# 创建计划 worktree（Phase 0 执行）
# 注意：必须从 main 创建，避免继承 fix/audit6-issues 未合并的改动
git worktree add .worktrees/chore-tech-debt-cleanup -b chore/tech-debt-cleanup main
cd .worktrees/chore-tech-debt-cleanup
uv sync
pre-commit install

# 基线测试验证起点干净
ruff check . && ruff format --check . && pyright
python -m pytest tests/unit/ -v -m "not slow"
# 注意：若基线测试因 P1-2 事件循环泄露出现 flaky，重跑一次确认；
# 若仍失败，登记为 baseline flaky 不阻塞计划推进（因 P1-2 本就是待解决技术债）

# ... 开发（遵循 TDD + 原子提交）...

# PR 合并后清理（回主工作区执行）
cd ../..
git worktree remove .worktrees/chore-tech-debt-cleanup
git worktree prune
git branch -d chore/tech-debt-cleanup
```

---

## 评估结论与决策

- **评估日期**: 2026-07-15
- **范围**: CONTRIBUTING.md「已知架构技术债」全部 6 项 + 测试覆盖率 + Phase R 收尾 + 路由方案合规性核查
- **五视角评审结论**: Product 6/10 + Architecture 7/10 + Security 5/10 + QA 6/10 + Skeptic 5/10
- **决策**: 按 ROI 分批推进，优先高价值红线修复与架构守护，暂缓低价值与过度工程项

### Spec skip reason

本次技术债清理不引入新功能、不改变 UI 行为（除 TD4a R9 修复影响错误信息显示）、不修改数据模型。主要是内部债务清理与自动化守护加固。项目从未有过 root `spec.md`，CONTRIBUTING.md 与 CLAUDE.md 已承载 product contract。技术债清理完成后更新 CONTRIBUTING.md「已知架构技术债」表与 CLAUDE.md §3.3 已知技术债条目，不创建独立 spec.md。

理由：
1. 本次任务为"债务清理 + 自动化守护"，不引入新功能、不改变 UI 行为（除 R9 错误信息脱敏）
2. 项目从未有过 root `spec.md`，CONTRIBUTING.md 与 CLAUDE.md 已承载 product contract
3. 技术债状态变更记录在 CONTRIBUTING.md 已知技术债表与本 Plans.md metadata
4. TD4a R9 修复属于红线合规，不是产品行为变更

### team_validation_mode

`subagent` — 已通过 1 个 Task subagent 完成 Product / Architecture / Security / QA / Skeptic 五视角独立评审。关键发现已纳入方案修订：
- TD4a R9 修复范围扩展为全局扫描 `str(e)` 入业务数据结构
- TD2 暂缓（Flet 0.85.3 Container 不支持 max_width，4K 真需求存疑）
- TD8 先恢复 Phase R 正本（位于 `origin/feature/flet-v1-declarative` 分支）
- TD1 先跑性能基准再决定降级
- TD5 依赖 TD3a（doc-lint NOTE 检查）先落地

---

## Phase 0: worktree 隔离创建 [lane:fast]

> 目的：满足 R18 红线，为本计划全程提供隔离执行环境。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 0.1 | [tdd:skip:setup] [lane:fast] 创建计划执行 worktree：`git worktree add .worktrees/chore-tech-debt-cleanup -b chore/tech-debt-cleanup` → `cd .worktrees/chore-tech-debt-cleanup` → `uv sync` → `pre-commit install` → 跑基线测试验证起点干净 | worktree 创建成功；基线 `ruff check . && ruff format --check . && pyright` 通过；`pytest tests/unit/ -v -m "not slow"` 通过 | - | cc:完了 |

---

## Phase 1: 高优先级红线修复与架构守护 [lane:gate]

> 目的：修复 R9 红线违规（敏感信息泄露）+ 落地 R1 红线自动化（架构守护）。这两项是五视角评审共识的最高 ROI 任务。**执行环境**：Phase 0.1 创建的 `.worktrees/chore-tech-debt-cleanup/` worktree。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 1.1 | [tdd:required] [lane:gate] **R9 全局排查**：扫描 `strategies/` + `services/` 中所有 `str(e)` / `str(exc)` / `f"{e}"` / `format(e)` / `repr(e)` 进入业务数据结构（dict/list/返回值/数据库字段）的位置，输出泄露点清单到 `docs/r9-leak-audit.md`。已知泄露点：`strategies/backtest/engine.py:476` 的 `failed_signal_dates.append({"date": signal_date, "error": str(e)})`。初步扫描结果：strategies/ 4 处（engine.py 3 + data_provider.py 1）+ services/ 1 处（ai_service.py 1），共 5 处候选 | 排查报告完成；`grep -rn "str(e)\|str(exc)\|f\"{e}\"\|format(e)\|repr(e)" strategies/ services/` 输出的每处位置都已归类为"业务数据结构"或"仅日志/安全"；清单覆盖所有候选位置；每处标注文件:行号 + 上下文 + 分类 | 0.1 | cc:完了 |
| 1.2 | [tdd:required] [lane:gate] **R9 批量修复**：对 Task 1.1 清单中归类为"业务数据结构"的每处位置，替换为 `DataSanitizer.sanitize_error(e)`。同步修复 `strategies/backtest/engine.py:474` 的 `logger.warning(... e)` 未 sanitize 隐患。注意：`DataSanitizer` 位于 `utils/` 层（横切关注点），strategies/services 导入 utils 不违反 R1 分层架构 | 所有"业务数据结构"泄露点修复；`grep -rn "str(e)\|str(exc)\|f\"{e}\"\|format(e)\|repr(e)" strategies/ services/` 输出的"业务数据结构"分类命中为 0；`pytest tests/unit/strategies/ tests/unit/services/ -v` 通过；新增 R9 守护测试（对每处修复点验证 `error` 字段值不匹配常见敏感模式正则：token/api_key/password/secret 的 16+ 字符连续字符串） | 1.1 | cc:完了 |
| 1.3 | [tdd:required] [lane:gate] **R1 import-linter 落地**：在 `pyproject.toml` 新增 `[tool.importlinter]` 段，声明 §4.1 分层架构禁止方向。**禁止方向**（对应 R1）：`core` 禁止导入 `data/services/strategies/ui/utils/app`；`data` 禁止导入 `services/strategies/ui`；`services` 禁止导入 `strategies/ui`；`strategies` 禁止导入 `ui`。**例外**（§4.1 明确）：`utils/` 可被任意层引用（横切关注点，不禁止任何层导入 utils）；`app/` 编排所有层（不禁止 app 导入任何层）；`ui/i18n.py` 是 UI 层对 `core.i18n` 的薄封装（允许 ui 导入 core）。新增 `lint-imports` 到 `.pre-commit-config.yaml` | `lint-imports` 命令通过；pre-commit hook 接入；`tests/unit/test_import_linter_config.py` 守护配置不被破坏（验证 `lint-imports` 命令在干净代码上返回 0）；在 worktree 内临时创建反向依赖文件（如 `core/test_reverse.py` import data）验证能被拦截后删除该临时文件，不留痕 | 0.1 | cc:完了 |

---

## Phase 2: 测试基础设施评估与条件性降级 [lane:gate]

> 目的：解决 P1-2 Windows 测试事件循环泄露。五视角评审要求先跑性能基准再决定降级，避免盲目降级引入性能回归。**执行环境**：Phase 0.1 创建的 `.worktrees/chore-tech-debt-cleanup/` worktree。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | [tdd:skip:research-only] [lane:gate] **性能基准对比**：在 worktree 内跑 `pytest tests/unit/ --durations=20 -m "not slow"` 记录 session scope 基线总耗时 + Top20 慢测试。用 `git stash` 暂存当前改动后临时改 `pyproject.toml` 为 `asyncio_default_test_loop_scope = "function"` 跑同样命令记录 function scope 耗时，跑完后 `git checkout pyproject.toml` 恢复（或 `git stash pop`）。同时验证 integration/e2e fixture 显式声明 `loop_scope="session"` override 在 `asyncio_default_test_loop_scope=function` 下是否生效（跑 1-2 个 integration 测试确认无 loop scope 冲突警告）。输出对比报告到 `docs/loop-scope-perf-baseline.md` | 对比报告完成；session vs function 总耗时差异数字明确；Top20 慢测试无显著回归（< 30%）则可降级，否则登记为 P1-2 阻塞项暂缓；integration fixture loop_scope override 行为已验证；`pyproject.toml` 已恢复为 session | 0.1 | cc:完了 |
| 2.2 | [tdd:required] [lane:gate] **条件性降级**：仅当 Task 2.1 性能回归 < 30% 时执行。**前置排查**：`grep -rn "clear_all_loop_locals\|reset_loop_local_cache" tests/ utils/ services/ data/` 确认除 `tests/conftest.py:223-234` 与 `tests/unit/test_infra_loop_isolation.py` 外无其他调用方。改 `pyproject.toml` 的 `asyncio_default_test_loop_scope` 从 `session` 为 `function`。integration/e2e fixture 显式声明 `loop_scope="session"` override 保持不变。删除 `tests/conftest.py:223-234` 的 `reset_loop_local_cache` autouse fixture。删除 `tests/unit/test_infra_loop_isolation.py`（4 个文档性测试，替换为新的 function scope 守护测试） | `pyproject.toml` 改为 function；前置排查无其他调用方；`reset_loop_local_cache` fixture 已删除；`test_infra_loop_isolation.py` 已删除；`pytest tests/unit/ -v -m "not slow"` 通过；新增 function scope 无泄漏守护测试（验证 loop-local 缓存在 function scope 下不跨测试残留，区别于旧文档性测试：旧测试验证 reset fixture 有效，新测试验证 function scope 下无需 reset 即无残留） | 2.1 | cc:完了 |
| 2.3 | [tdd:skip:docs-only] [lane:fast] **文档同步**：更新 `CONTRIBUTING.md`「已知架构技术债」P1-2 条目为"已解决（unit 降 function / integration-e2e 保留 session）"。更新 `CLAUDE.md` §3.3 P1-2 描述。若 Task 2.1 性能回归 >= 30% 则改为"暂缓，待 pytest-asyncio 性能优化"。同步更新 `CLAUDE.md` 顶部版本号（CONTRIBUTING.md L338 自检条目要求） | 文档更新通过 `docs-consistency` pre-commit hook；CLAUDE.md 版本号同步 | 2.2 | cc:TODO |

---

## Phase 3: Phase R 正本恢复与核对 [lane:gate]

> 目的：解决 Phase R 正本 unknown 问题。五视角评审发现 Phase R 正本位于 `origin/feature/flet-v1-declarative` 分支的 Plans.md（已验证分支存在：`remotes/origin/feature/flet-v1-declarative`），需先恢复核对 17 任务真实状态再决定推进。**执行环境**：Task 3.1 属 git 只读操作可在主工作区执行（无 Phase 0 依赖）；Task 3.2-3.4 在 Phase 0.1 worktree 内。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | [tdd:skip:setup] [lane:fast] **Phase R 正本恢复**：`git fetch origin feature/flet-v1-declarative` → `git show origin/feature/flet-v1-declarative:Plans.md > .tmp/phase-r-plans.md`（Windows 兼容路径，创建 `.tmp/` 目录并加入 `.gitignore`）→ 读取并提取 Phase R 章节（R.1.1-R.6.3 全部任务）→ 与主工作区 `Plans.md` 的 Phase 3.1 引用（17 个未完成任务编号）核对 | Phase R 全部任务清单提取完成；17 个未完成任务编号（R.1.5/R.2.3-R.2.4/R.3.1-R.3.4/R.4.1-R.4.3/R.5.1-R.5.4/R.6.1-R.6.3）的标题与 DoD 已明确；R.4.1 状态核查（CHANGELOG 显示已删除 refresh_dropdown_options，核对是否真完成） | - | cc:TODO |
| 3.2 | [tdd:skip:research-only] [lane:gate] **17 任务真实状态核对**：对 Task 3.1 提取的 17 个任务，逐个核对当前代码状态（grep 关键符号 + 读取相关文件），标注每个任务的"真完成 / 未完成 / 部分完成"状态。输出核对报告到 `docs/phase-r-status-audit.md` | 17 任务状态全部明确；报告含每任务的"文件:行号证据 + 当前状态 + 缺口"；R.4.1 状态明确（CHANGELOG 与代码是否一致） | 3.1, 0.1 | cc:TODO |
| 3.3 | [tdd:required] [lane:gate] **未完成任务推进**：对 Task 3.2 确认的"未完成"任务，按 Task 3.1 恢复的原 Phase R DoD 逐个推进。每完成一个任务更新核对报告状态。若某任务的 DoD 涉及 UI 或集成层，补充运行 `pytest tests/integration/ -v` 相关用例 | 所有"未完成"任务达到 Task 3.1 恢复的原 Phase R DoD；`pytest tests/unit/ -v -m "not slow"` 通过；核对报告全部标注"真完成" | 3.2 | cc:TODO |
| 3.4 | [tdd:skip:docs-only] [lane:fast] **Phase R 收尾文档**：更新 `Plans.md`（Flet 升级准备）的 Phase 3.1 引用，将"17 个未完成任务"改为"已完成"。更新 `CONTRIBUTING.md` 若有 Phase R 相关注册条目。清理 `.tmp/phase-r-plans.md` 临时文件 | 文档更新通过 `docs-consistency` pre-commit hook；临时文件已清理 | 3.3 | cc:TODO |

---

## Phase 4: 测试覆盖率补齐 [lane:gate]

> 目的：补齐测试覆盖率至 85% 阈值 + 28 个 ui/ 文件 >= 80%。五视角评审要求先列清单再分批，警惕数字游戏。**执行环境**：Phase 0.1 创建的 `.worktrees/chore-tech-debt-cleanup/` worktree。**跨 Phase 依赖**：必须在 Phase 2 之后执行（Phase 2 删除 test_infra_loop_isolation.py 会降低覆盖率）。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | [tdd:skip:research-only] [lane:gate] **覆盖率缺口清单**：跑 `pytest tests/unit/ --cov --cov-report=json -m "not slow"` → 用 `scripts/check_per_file_coverage.py` 输出所有 < 80% 的文件清单 → 对覆盖率不足的 ui/ 文件逐个标注当前覆盖率与缺口行数 → 输出到 `docs/coverage-gap-audit.md`，按"缺口 < 5% / 5-15% / > 15%"分批 | 缺口清单完成；所有 < 80% 的 ui/ 文件每个有当前覆盖率 + 缺口行数 + 分批标签；文件总数明确（实际数量以脚本输出为准，不预设 28） | 0.1, Phase 2 | cc:TODO |
| 4.2 | [tdd:required] [lane:gate] **高缺口批补齐（缺口 > 15%）**：对 Task 4.1 标注"缺口 > 15%"的 ui/ 文件，补齐 ViewModel 契约测试 + View 渲染测试至 >= 80%。遵循 §3.2 MVVM 范式（View = f(ViewModel.state)），不补低价值断言测试。**低价值测试反例**（禁止）：`assert view is not None`、`assert True`、`assert len(state) >= 0` 等不验证业务逻辑的断言 | 高缺口文件全部 >= 80%；`scripts/check_per_file_coverage.py` 对该批文件通过；无低价值测试（Code review 确认每条断言验证具体业务逻辑） | 4.1 | cc:TODO |
| 4.3 | [tdd:required] [lane:gate] **中低缺口批补齐（缺口 5-15% / < 5%）**：对 Task 4.1 标注"中低缺口"的文件补齐至 >= 80%。优先补关键路径（事件处理 / state 转换 / 命令执行），非关键路径可不补 | 中低缺口文件全部 >= 80%；`scripts/check_per_file_coverage.py` 全过 | 4.2 | cc:TODO |
| 4.4 | [tdd:required] [lane:gate] **整体覆盖率验证**：跑 `pytest tests/unit/ --cov --cov-fail-under=85 -m "not slow"` 验证整体 >= 85%。若仍未达标，回 Task 4.2/4.3 补齐剩余缺口 | 整体覆盖率 >= 85%；`fail_under=85` 通过；无新增 flaky 测试 | 4.3 | cc:TODO |

---

## Phase 5: doc-lint 第二阶段（仅 3a）+ utils NOTE 标记 [lane:gate]

> 目的：落地 doc-lint 3a（NOTE(lazy) 三要素检查）作为 TD5 的守护，再补 utils/ 39 处 NOTE 标记。五视角评审判定 3b/3c 过度工程，仅做 3a。**执行环境**：Phase 0.1 创建的 `.worktrees/chore-tech-debt-cleanup/` worktree。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 5.1 | [tdd:required] [lane:gate] **doc-lint 3a 落地**：扩展 `scripts/check_docs_consistency.py` 新增 `check_note_lazy_format()` 函数，正则匹配 `# NOTE(lazy):` 后校验同行或多行块是否含 `ceiling:` 与 `upgrade:` 两个关键字。复用 `check_anchor_dead_links` 的 fenced code block 跳过逻辑。区分 NOTE(lazy) 与 `# TODO:`（后者不触发检查） | 新增检查函数；`tests/unit/test_check_docs_consistency.py` 守护测试（含故意违规用例确认能拦截）；`pre-commit run docs-consistency` 通过 | 0.1 | cc:TODO |
| 5.2 | [tdd:required] [lane:gate] **utils/ 39 处 NOTE(lazy) 标记**：对 `utils/config_handler.py`（28 处）、`utils/exception_hooks.py`（3 处）、`utils/logger.py`（3 处）、`utils/singleton_registry.py`（3 处）、`utils/diagnostics.py`（2 处）补 `# NOTE(lazy):` 标记，三要素齐全（简化内容 + ceiling + upgrade）。按调查报告分组模板：keyring fallback / encrypt-decrypt / 文件 IO / 基础设施兜底 / 业务降级。注：`utils/time_utils.py:80` 的 1 处不属于此 39 处范围（无业务消费方语义，调查时已剔除），不标记 | 39 处全部标记（28+3+3+3+2=39）；`pre-commit run docs-consistency` 通过（Task 5.1 守护）；`ruff check .` 通过 | 5.1 | cc:TODO |
| 5.3 | [tdd:skip:docs-only] [lane:fast] **utils P3 条目文档同步**：更新 `CONTRIBUTING.md`「已知架构技术债」utils/ 层 P3 条目措辞为"待统一标记 NOTE(lazy)（已标记 39 处，time_utils.py:80 已剔除）"。说明 40 处中 0 处适合走 classify_error（无业务消费方） | 文档更新通过 `docs-consistency` pre-commit hook | 5.2 | cc:TODO |

---

## Phase 6: strategies/ 层异常处理优化 [lane:gate]

> 目的：优化 strategies/ 层异常处理。五视角评审要求 TD4b 警惕过度抽象（§1.3 单调用的层），需先评估杠杆点再决定是否引入统一入口。**执行环境**：Phase 0.1 创建的 `.worktrees/chore-tech-debt-cleanup/` worktree。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 6.1 | [tdd:skip:research-only] [lane:gate] **统一入口杠杆评估**：评估在 `strategies/polars_base.py` 引入 `_handle_strategy_exception(e, context)` 统一入口的杠杆点。核查 38 处 NOTE(lazy) 位置的异常处理语义差异（fail_fast / 记录 failed_signal_dates / 仅日志 / 降级返回空）。判断统一入口是否能保留每处差异化行为，还是会导致过度抽象。输出评估报告到 `docs/strategy-exception-leverage.md`。**依赖说明**：依赖 1.2 是因 R9 修复会改变 strategies/ 异常处理代码现状（如 `engine.py:476` 的 `str(e)` → `sanitize_error(e)`），杠杆评估必须基于 R9 修复后的代码现状，否则评估结果失真 | 评估报告完成；明确"引入统一入口"或"保持现状仅改 ceiling"的决策；若引入，明确放置层（strategies/ 内，不可放 utils/） | 0.1, 1.2 | cc:TODO |
| 6.2 | [tdd:required] [lane:gate] **条件性引入统一入口**：仅当 Task 6.1 评估为"引入"时执行。在 `strategies/polars_base.py` 或 `strategies/base_strategy.py` 引入 `_handle_strategy_exception(e, context)` 方法，参数化 severity 判定 + sanitize + 降级策略。所有子类策略自动获益。每处改造点保留原语义的回归测试 | 统一入口实现；38 处中可统一的位置已迁移；`pytest tests/unit/strategies/ -v` 通过；每处改造点有回归测试证明行为等价 | 6.1 | cc:TODO |
| 6.3 | [tdd:required] [lane:gate] **NOTE(lazy) ceiling 动态化**：对 strategies/ 38 处 NOTE(lazy) 标记，将硬编码"ceiling: 38处策略层异常"改为动态描述（如"ceiling: 剩余 prefetch 类异常"或"ceiling: 该 try 块抛出 transient 异常"）。解决 ceiling 数值同步隐患。**依赖说明**：依赖 Task 5.1（doc-lint 3a）而非 6.2——ceiling 动态化修改 NOTE(lazy) 标记后需 doc-lint 3a 验证格式合规，与统一入口引入无因果依赖，避免 6.1→6.2→6.3 串行死锁 | 38 处 ceiling 全部动态化；`pre-commit run docs-consistency` 通过；`grep -rn "ceiling: 38处" strategies/` 无命中 | 5.1 | cc:TODO |
| 6.4 | [tdd:skip:docs-only] [lane:fast] **strategies P3 条目文档同步**：更新 `CONTRIBUTING.md`「已知架构技术债」strategies/ 层 P3 条目，反映实际数量（43 处 = 5 处已 classify + 38 处 NOTE）与改造后状态（统一入口 + ceiling 动态化） | 文档更新通过 `docs-consistency` pre-commit hook | 6.2, 6.3 | cc:TODO |

---

## 暂缓/拒绝清单

以下技术债经五视角评审判定为暂缓或拒绝，不纳入本计划：

| 技术债 | 判定 | 理由 |
|--------|------|------|
| **TD2 MAX_CONTENT_WIDTH 未实现** | 暂缓 | Flet 0.85.3 Container 实测不支持 max_width 属性，方案需返工重设计（用 Row+alignment 或 ResponsiveRow）。Skeptic 质疑 4K 居中是伪需求（A 股量化用户主流 1080p/2K）。待用户确认 4K 真实需求后再启动 |
| **TD3b doc-lint R1-R18 append-only 检查** | 暂缓 | 需维护 baseline 文件 `.baseline/redline_ids.txt`，baseline 漂移后误报。ROI 低，待红线变更频发时再实现 |
| **TD3c doc-lint 强制状态映射检查** | 暂缓 | 跨文件语义比对难度高，产出可能是"为了检查而检查"。待 doc-lint 工具链成熟后再实现 |
| **TD6b R16 AST 钩子** | 暂缓 | 实现难度最高（需维护白名单，误报风险高）。先做基线扫描调研违规规模，待 R1 import-linter 稳定后再启动 |
| **TD6c R4/R12/R13/R14/R15 自定义检查** | 暂缓 | 优先级低于 R1。R12/R15 已有运行时部分覆盖。待红线违规频发时再实现 |
| **TD9 路由方案重构** | 拒绝 | 五视角评审一致拒绝。当前 NavigationRail + use_state + ft.Stack+visible 是 Flet 0.85.3 (V1) 桌面应用主流范式，无 V0 路由 API 残留，完全合规 |
| **AI 调用 LiteLLM 模板统一（5 处跨层重复）** | 暂缓 | `prompt_validator.py:47` / `ai_mixin.py:1006` / `market.py:136,278,343` 存在重复的 LiteLLM 调用模板。跨 strategies/ + services/ 两层、5 处 > 3 文件，按 §1.7 举一反三原则须记录为独立重构任务延后处理，不纳入本计划 Phase 6（Phase 6 聚焦 strategies/ 层异常处理，不跨层）。待本计划完成后作为独立重构任务评估 |

---

## 风险登记（来自五视角评审）

| 风险点 | 等级 | 缓解策略 |
|--------|------|---------|
| TD1 删除 reset_loop_local_cache 后 integration/e2e session fixture 内 loop-local 缓存跨测试累积 | 中 | Task 2.1 性能基准 + Task 2.2 条件性降级（仅回归 < 30% 才执行） |
| TD1 删除 test_infra_loop_isolation.py 降覆盖率与 TD7 升 85% 冲突 | 中 | Task 2.1 先量化覆盖率下降幅度，Task 4.2/4.3 补齐时优先补该部分 |
| TD4a R9 修复后 DataSanitizer 循环依赖 backtest 模块 | 低 | Task 1.2 验证 DataSanitizer 在 utils/ 层无反向依赖 strategies/ |
| TD4b 统一入口违反 §1.3 单调用的层禁令 | 中 | Task 6.1 杠杆评估先行，明确"引入"或"保持现状"决策 |
| TD8 Phase R 正本恢复后 17 任务状态与 Plans.md 引用不一致 | 中 | Task 3.2 逐个核对真实状态，Task 3.4 同步文档 |
| TD7 覆盖率 85% 数字游戏（补低价值测试达成） | 中 | Task 4.2/4.3 明确"不补 assert True 类低价值测试"，优先关键路径 |
| pytest-asyncio 1.4.0 混合 loop_scope 行为未知 | 中 | Task 2.1 性能基准同时验证混合 scope 行为 |
| import-linter 在 Python 3.13 + 项目分层模型下兼容性 | 低 | Task 1.3 先在 worktree 验证配置可行性 |

---

## unknown_data 清单（not_observed != absent）

- **ui/ 文件覆盖率 < 80% 的具体清单与数量**：`coverage.json` 单行 2272KB 超过工具读取限制无法提取，数量未知（不预设 28）。Task 4.1 将通过 `scripts/check_per_file_coverage.py` 重新生成清单
- **Phase R 17 个任务的标题与 DoD**：主工作区 Plans.md 不含 Phase R 字样，正本位于 `origin/feature/flet-v1-declarative` 分支。Task 3.1 将恢复正本提取
- **R.4.1 真实状态**：CHANGELOG 显示 R.4.1 已完成（删除 refresh_dropdown_options），但主 Plans.md 仍列为未完成，信息不一致。Task 3.2 将核对
- **TD1 降级 function scope 后的实测性能影响**：无基线数据。Task 2.1 将实测对比
- **Flet 0.85.3 Container 是否支持 max_width**：已实测**不支持**（五视角评审验证），TD2 方案需返工，已暂缓
- **pytest-asyncio 1.4.0 混合 loop_scope 下 event_loop_policy fixture（session）与 asyncio_default_test_loop_scope=function 的交互行为**：未验证。Task 2.1 将实测
- **strategies/ + services/ 中 `str(e)` 入业务数据结构的完整清单**：仅已知 engine.py:476。Task 1.1 将全局扫描
- **utils/loop_local.py 的 clear_all_loop_locals 是否被其他地方调用**：未排查。Task 2.2 删除前将排查
- **import-linter 配置在项目分层模型（utils 横切 + app 编排）下的精细调整**：未验证。Task 1.3 将实测

---

## 事前確認（plan-time pre-approval）

本计划涉及以下外部操作，需在计划批准时一次性确认。批准后记录到 `.claude/state/plan-preapprovals.json`，`harness-work` / `breezing` 执行期间不再就这些事项重复询问。

### external-send（外部发送）

- **事項**: `git push -u origin chore/tech-debt-cleanup` + `gh pr create`
  理由: Phase 1-6 完成后推送计划分支并发起 PR 至 `main`
  scope: Phase 1-6 / Task 合并后

- **事項**: `git fetch origin feature/flet-v1-declarative`
  理由: Phase 3.1 恢复 Phase R 正本需从远程分支获取
  scope: Phase 3 / Task 3.1

### destructive（破坏性操作）

- **事項**: `git worktree remove .worktrees/chore-tech-debt-cleanup` + `git branch -d chore/tech-debt-cleanup`
  理由: Phase 1-6 PR Squash Merge 后清理计划 worktree 与分支
  scope: Phase 1-6 / 合并后

- **事項**: 删除 `tests/conftest.py:223-234` 的 `reset_loop_local_cache` autouse fixture + 删除 `tests/unit/test_infra_loop_isolation.py`
  理由: Phase 2.2 条件性降级 unit 测试到 function scope 后，loop-local 隔离 fixture 与文档性测试不再需要
  scope: Phase 2 / Task 2.2

- **事項**: 临时改 `pyproject.toml` 的 `asyncio_default_test_loop_scope` 为 `function`（仅 Task 2.1 性能基准用，Task 2.1 完成后恢复）
  理由: Phase 2.1 性能基准对比需临时切换 loop scope 跑测试
  scope: Phase 2 / Task 2.1

### secret-read

本计划不涉及 secret-read 操作（不读取 `.env*` / `secrets/**` / `*.pem` / `*.key` / `.ssh/**` / `.aws/**` / `credentials`）。

---

## 会话启动指引（create 完成时）

```text
新的会话的启动命令: claude
起動後の最初の入力: /harness-work 0.1
向いている場面: Phase 0 的 worktree 创建是整个计划的前置依赖，从 0.1 开始最自然
```

替代方案（若希望并行推进 Phase 1 高 ROI 任务）：

```text
新的会话的启动命令: claude
起動後の最初の入力: /breezing all
向いている場面: Phase 0 完成后，Phase 1 的 3 个 task（R9 排查 + R9 修复 + R1 import-linter）是五视角评审共识的最高 ROI，可并行推进
```

长时间运行方案（若 Phase 3-4 推进耗时较长）：

```text
新的会话的启动命令: ENABLE_PROMPT_CACHING_1H=1 claude
起動後の最初の入力: /harness-loop all
向いている場面: Phase 3 Phase R 核对 + Phase 4 覆盖率补齐可能跨多 session，长运行模式适合
```
