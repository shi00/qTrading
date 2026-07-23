# ADR-0001: Record Architecture Decisions

> Status: Accepted
> Date: 2026-07-17
> Owner: 架构维护者

## Context

AStockScreener 项目随演进积累了一系列架构决策（文档体系分层、Flet V1 迁移、红线自动化范围、单例生命周期等），但这些决策分散在 CLAUDE.md / CONTRIBUTING.md / 技术债表 / PR 描述 / 历史讨论中，缺乏统一追溯入口。新贡献者（含 AI 助手）在面对「为什么这样而不是那样」的问题时，需要反复 grep 历史记录或追问原作者。

文档体系检视报告（P2-2）建议引入极简 ADR 目录，沉淀不可逆或对项目走向有持续影响的架构决策。

## Decision

采用轻量级 ADR（Architecture Decision Record）机制：

1. **存储位置**：`docs/adr/` 下，文件名 `NNNN-kebab-case-title.md`，编号从 0001 起，单调递增不复用。
2. **格式**：每个 ADR 含五段——`Status / Context / Decision / Consequences / Alternatives`。无额外模板开销。
3. **生命周期**：
   - `Accepted`：决策已生效，作为当前规范依据
   - `Superseded by ADR-NNNN`：被后续 ADR 推翻，原文件保留以供追溯
   - 不引入 `Proposed` / `Rejected` 中间状态（YAGNI，决策在 PR 评审中确定是否落地）
4. **触发条件**：满足以下任一即应新建 ADR——
   - 推翻既有架构决策（如 ADR-0003 推翻 CONTRIBUTING.md 中「3b/3c 不做」决策）
   - 引入新的架构层 / 重大约束 / 跨模块范式
   - 在多个等效方案中选择且选择影响 product behavior
   - 解决需要长期追溯依据的争议
5. **不引入 ADR 的场景**：typo / 单文件重构 / 行数优化 / 测试补全 / 局部 bug 修复。

## Consequences

- **正向**：架构决策有可追溯证据，新贡献者（含 AI 助手）可快速理解项目走向；推翻决策时必须新写 ADR，强制暴露权衡。
- **负向**：每个不可逆决策增加一份文档维护负担；ADR 与 CLAUDE.md / CONTRIBUTING.md / docs/debt/ 存在职责边界重叠风险。
- **缓解**：ADR 只记录「为什么这么决定」，不重复 CLAUDE.md 的「红线 / 架构边界」或 CONTRIBUTING.md 的「实现细则」；技术债表与 ADR 互补——前者记录「推迟的优化」，后者记录「不可逆的决策」。

## Alternatives

- **不引入 ADR，继续靠 PR 描述 + 技术债表沉淀决策**：拒绝。PR 描述易随 squash merge 丢失上下文；技术债表只记「推迟的优化」，无法承载「为什么这么做」的决策依据。
- **引入重型 ADR 工具链（如 madr / adr-tools）**：拒绝。项目规模与决策频率不需要工具链开销，YAGNI。
- **将 ADR 合并到 CLAUDE.md / CONTRIBUTING.md**：拒绝。CLAUDE.md 是 AI 自动加载文件，不应承载历史决策追溯；CONTRIBUTING.md 是入口索引，不应承载决策细节。

## Errata

> 2026-07-23 补充

ADR-0001 与 ADR-0003 同日决策（2026-07-17），ADR-0003 推翻 CONTRIBUTING.md 历史「3b 不做」决策。ADR-0001 作为 ADR 机制首条记录，与 ADR-0003 同批次编排落地。
