# ADR-0005: 落地 3c enforcement 字段映射检查

> Status: Accepted
> Date: 2026-07-17
> Owner: 架构维护者
> Partial Supersedes: ADR-0003 (3c portion only; 3b portion remains valid)

## Context

ADR-0003 决策「3b 落地 + 3c 不做」，3c 不做的理由为：

1. 强制状态字段语义复杂，机器校验需大量特例
2. 当前强制状态字段未发生漂移事故
3. 收益成本比不及 3b

本次设计评审对 3c 重新评估，发现 ADR-0003 拒绝理由可化解：

1. 语义复杂 → 提取 9 个高价值不变量（N1~N9），不追求全量精确映射；R16 单点特例集中管理
2. 决策触发 → 架构维护者主动要求重新评估 3c；未宣称已发生漂移事故
3. 收益成本比 → 3b 已落地（`redlines.yml` schema 稳定），3c 是 3b 的自然延伸（共享 yml 解析、模块级路径常量、测试模式），边际成本低

### 触发来源扩展

技术债表 P3 原触发条件「强制状态字段发生漂移事故或红线违规频发」不足以覆盖本次主动触发场景。本次触发来源扩展如下：

> 除原「漂移事故 / 违规频发」事件驱动触发外，新增「架构维护者主动触发」作为合法触发源。本次 3c 落地即由架构维护者主动要求重新评估，非已发生漂移事故。技术债表 P3 的 upgrade 触发条件同步更新为「强制状态字段发生漂移事故、红线违规频发、或架构维护者主动重新评估时」。

### 同日推翻说明

ADR-0005 Date 与 ADR-0003 同为 2026-07-17。同日推翻理由：

> ADR-0003 落地 3b 后，架构维护者立即对 3c 重新评估。本次推翻基于 3b 落地后的即时评估，认为 3c 是 3b 的自然延伸（共享 yml 解析、模块级路径常量、测试模式），边际成本低。虽与 ADR-0003 同日，但本次推翻非草率决策，而是 3b 落地验证后的连续治理动作。ADR append-only 不可变性：ADR-0003 保留作为历史记录不修改，仅由 ADR-0005 标注 Partial Supersedes 关系。

## Decision

1. **部分推翻 ADR-0003 的 3c 决策**：3c 从「不做」改为「落地」。
   - 扩展 `scripts/check_docs_consistency.py` 新增 `check_enforcement_mapping()` 校验 8 个不变量（N1~N8；原 N9 在实施后检视中删除——与 N6 触发条件等价）
   - 新增 `EnforcementEnvironment` frozen dataclass 作为配置快照，保证核心不变量校验可纯函数测试（无文件 IO）
   - 新增 `tests/unit/test_docs_consistency.py::TestEnforcementMapping` 类（TDD，覆盖 N1~N8 正反例 + 特例 + 假阳性 + 4 种 YAML 风格 + 漂移检测）
2. **维持 ADR-0003 的 3b 决策**：3b（红线 R1~R18 编号 append-only 检查）不变。
3. **已知漏检场景登记**：R3 enforcement 不精确、meta 悖论（meta redlines 自身的 enforcement）、R2/R7/R8 弱校验、hook `files` 收窄、CI `if` 禁用均在设计文档与代码注释中登记。

### 8 个不变量概览

| 不变量 | 校验对象 | 检测的漂移场景 |
|--------|---------|---------------|
| N1 | `check_redlines.py` 关键词 | `redline-check` hook 被删除 / entry 被篡改 / 脚本文件丢失 |
| N2 | `import-linter` + 契约数量 | `lint-imports` hook 被删除 / 契约数量漂移 |
| N3 | `ruff` 关键词（word boundary） | `ruff-check` hook 被删除 / entry 被篡改 |
| N4 | `安全扫描` + Gitleaks 配置 | Gitleaks workflow 被删除 / `.gitleaks.toml` 丢失 |
| N5 | `CI-test` + pytest 命令 | CI workflow 中 pytest 命令被删除 |
| N6 | `仅人工评审` → `human_review_required: true` | yml 字段与 enforcement 文本不一致（正向） |
| N7 | `待实现` / `暂缓` → `human_review_required: false` | R16 误标 `human_review_required: true`（N8 的 R16 特化版，触发时 N8 也会触发，但 N7 报错更精确） |
| N8 | `human_review_required: true` → enforcement 含 `仅人工评审` | yml 字段与 enforcement 文本不一致（反向） |

> **N6 + N8 双向一致性**：共同构成 `human_review_required == true ⇔ enforcement 含「仅人工评审」` 的双向守护。
> **N7 与 N8 关系**：N7 是 N8 的 R16 特化版（含 pending 关键词时 N8 也会触发），保留 N7 因其报错更精确指向 R16 误标场景。
> **原 N9 已删除**：实施后检视发现 N9（`human_review_required == false ⇒ enforcement 不含「仅人工评审」）与 N6 触发条件完全等价（仅操作数顺序不同），重复报错，故删除 N9 保留 N6。

## Consequences

- **正向**：enforcement 字段声称的守护机制从纯人工评审升级为机器守护 + 人工兜底；hook 删除 / entry 篡改 / 契约数量漂移 / Gitleaks 删除 / CI 测试命令删除等漂移场景可被检测。
- **负向**：新增 9 个不变量的维护负担；enforcement 关键词变更时需同步不变量常量。
- **缓解**：关键词集合集中在模块顶部常量，维护成本低；不变量失败时提供精确报错（含 R 编号 + 不变量编号 + 具体漂移描述）。

### 跟进任务（不在本次 3c 范围内）

R3 enforcement = `"pre-commit"`（无具体 hook 名）是 yml schema 不精确问题，非 3c 范围限制。跟进任务：

> R3 enforcement 文本精确化为 `"pre-commit（type-ignore-reason）"`，然后扩展 N1 或新增 N10 守护 `type-ignore-reason` hook 存在。此任务属 yml schema 精确化，由独立 PR 处理，不阻塞本次 3c 落地。

## Alternatives

- **维持 3c 不做**：拒绝。架构维护者已主动要求重新评估；在 3b 已落地后继续靠人工评审守护 enforcement 漂移风险不可控。
- **全量精确映射**：拒绝。18 套签名（每条红线一个独立校验函数）维护成本高，违反 YAGNI；签名变更频繁。
- **粗粒度类别映射**（仅校验 enforcement 含 "pre-commit" / "CI-test" / "仅人工评审" 三类）：拒绝。无法检测「删除 `redline-check` hook 而保留 `ruff-check` hook」这类具体漂移。

## Errata / 勘误

> 2026-07-19 追加

Context「触发来源扩展」段落（L24）中"技术债表 P3 原触发条件「强制状态字段发生漂移事故或红线违规频发」"与 Decision 同段引用（L26）"技术债表 P3 的 upgrade 触发条件同步更新为「强制状态字段发生漂移事故、红线违规频发、或架构维护者主动重新评估时」"两处引用的目标条目（doc-lint 自动化第二阶段 P3 条目）已于 2026-07-19 从 `docs/debt/known-technical-debt.md` 清理。

清理原因：3a/3b/3c 全部落地完成（3a 由 Phase 5 落地、3b 由 ADR-0003 落地、3c 由本 ADR 落地），符合 CLAUDE.md §3.3「已解决事项不再列入本表」原则（设计 spec 未纳入版本控制）。

因此上述"同步更新"动作实际未生效（目标条目已不存在，无 upgrade 触发条件可更新）。读者如需理解本 ADR 触发条件的演进逻辑，请直接阅读本 ADR Context 与 ADR-0003 Decision 3 的历史记录，无需在 `known-technical-debt.md` 中查找。

本勘误仅说明引用失效与动作未生效原因，不修改原文（遵守 ADR append-only 不可变性原则）。
