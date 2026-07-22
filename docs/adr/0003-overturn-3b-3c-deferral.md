# ADR-0003: 推翻「3b/3c 过度工程不做」决策

> Status: Partial Superseded by ADR-0005 (3c portion only; 3b portion remains valid)
> Date: 2026-07-17
> Owner: 架构维护者
> Supersedes: CONTRIBUTING.md 历史版本中「doc-lint 自动化第二阶段 3b/3c 判定为过度工程，不做」决策（3b 部分）

## Context

CONTRIBUTING.md 历史版本「已知架构技术债」表第一条 P3 记录了 doc-lint 自动化第二阶段评审结论：

> 五视角评审判定 3b（红线 R1~R18 编号 append-only 检查）与 3c（"强制状态"与实际 hook/CI job 映射检查）为过度工程，不做。

该决策的 upgrade 触发条件为「红线违规频发或 CI 自动化专项迭代时重新评估 3b/3c」。

本次文档体系检视整改（P1-2）属于 CI 自动化专项迭代，触发条件已满足。同时：

- 红线 R1~R18 是项目宪法核心，append-only 与不复用废弃编号的约束目前仅靠人工评审守护
- CLAUDE.md §3.1 红线表的「强制状态」字段（pre-commit / CI-test / 仅人工评审）与实际 hook/CI 配置存在漂移风险
- 引入 `docs/governance/redlines.yml` 机器可读映射后，可通过脚本 + 单元测试守护编号一致性

但 3c（"强制状态"与实际 hook/CI job 映射检查）仍判定为过度工程：
- 强制状态字段语义复杂（如 R1 由 import-linter 守护而非 check_redlines.py，R16 标注「可自动化待实现（暂缓）」），机器校验需大量特例
- 当前强制状态字段未发生漂移事故
- 收益成本比不及 3b

## Decision

1. **推翻 3b 决策**：3b（红线 R1~R18 编号 append-only 检查）从「不做」改为「落地」。
   - 新建 `docs/governance/redlines.yml` 含 R1~R18 全部红线（字段：`id / title / description / enforcement / human_review_required`）
   - 扩展 `scripts/check_docs_consistency.py` 新增 `check_redlines_yaml_consistency()` 校验 yml 中 R 编号与 CLAUDE.md §3.1 表格一致（append-only、不复用废弃编号、编号连续）
   - 新增 `tests/unit/test_docs_consistency.py::TestRedlinesYamlConsistency` 类（≥4 测试，TDD）
   - 移除 `scripts/check_docs_consistency.py` docstring 中「红线 R1~R18 编号 append-only 检查（未实现）」登记
2. **维持 3c 决策**：3c（"强制状态"与实际 hook/CI job 映射检查）仍不做。
3. **更新决策记录**：`docs/debt/known-technical-debt.md` 第一条 P3 条目更新为「3a 已落地，3b 已落地（见 ADR-0003），3c 仍不做」。

## Consequences

- **正向**：红线 R1~R18 编号 append-only 约束从人工评审升级为机器守护；后续新增 R19 等编号时强制同步 redlines.yml + CLAUDE.md，杜绝编号漂移。
- **负向**：新增一份 redlines.yml 维护负担；CLAUDE.md §3.1 表格行数变化时必须同步 yml，否则 check_docs_consistency.py 报错。
- **缓解**：redlines.yml 字段精简（5 个字段），维护成本低；check_redlines_yaml_consistency() 提供精确报错（缺哪个 R / 多哪个 R / 编号不连续），快速定位漂移。

## Alternatives

- **维持 3b/3c 都不做**：拒绝。upgrade 触发条件已满足（CI 自动化专项迭代）；继续靠人工评审守护红线编号漂移风险不可控。
- **3b/3c 都做**：拒绝。3c 强制状态字段语义复杂，机器校验需大量特例，收益成本比不及 3b；YAGNI 原则下推迟 3c。
- **只做 3c 不做 3b**：拒绝。3b（编号 append-only）比 3c（强制状态映射）更基础、收益更直接；优先级 3b > 3c。

## Errata / 勘误

> 2026-07-19 追加

Decision 3 中"docs/debt/known-technical-debt.md 第一条 P3 条目更新为「3a 已落地，3b 已落地（见 ADR-0003），3c 仍不做」"所指的"第一条 P3 条目"为决策时点（2026-07-17）的 doc-lint 自动化第二阶段条目。

该条目后因 3a/3b/3c 全部落地完成（3a 由 Phase 5 落地、3b 由本 ADR 落地、3c 由 ADR-0005 落地），已于 2026-07-19 从 `docs/debt/known-technical-debt.md` 清理（符合 CLAUDE.md §3.3「已解决事项不再列入本表」原则；设计 spec 未纳入版本控制）。

当前 `docs/debt/known-technical-debt.md` 第一条 P3 条目为"strategies/ 层 except Exception 已标记 NOTE(lazy)"，与本 ADR 决策无直接关联。读者如需查阅 doc-lint 历史决策记录，请直接阅读本 ADR 与 ADR-0005，无需在 `known-technical-debt.md` 中查找。

本勘误仅说明引用失效原因，不修改 Decision 3 原文（遵守 ADR append-only 不可变性原则）。
