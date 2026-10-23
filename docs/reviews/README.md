# 检视方法论索引 (Review Index)

> **目的**：让检视方法论对新会话一跳可达，避免重复检视、重复给出已存在的结论（沿 [CLAUDE.md](../../CLAUDE.md) §1.8「AI 代码检视 / PR review」→ [ai-review.md](./ai-review.md) → 本文件）。
>
> **目录职责边界**：`docs/reviews/` 仅承载检视**方法论**资产（ai-review.md / appendix.md / quality-dimensions.md / scenario-completeness.md / review-policy.yaml / review-result.schema.json / review-profiles/ / evals/）。检视**报告产物与结论**（正文 + 长证据 + 结论登记）均不入库，报告正文一律落根目录 `reviews/`（gitignored，仅本地工作区可读）；报告正文与结论 **SHALL NOT** 落 `docs/reviews/`。原检视**结论结构化登记**机制（`docs/reviews/findings/`，GOV-04）已废弃，未关闭发现统一迁入 [known-technical-debt.md](../debt/known-technical-debt.md) 跟进。决策正本见 [ADR-0002](../adr/0002-document-layering.md) Errata。

## 方法论文档

检视方法论资产登记于此（新增顶层方法论文档须同时在本文件登记，见 `check_reviews_index_completeness` 门禁）：

- [ai-review.md](./ai-review.md) — AI 代码检视指南（核心协议 + 稳定规则 ID + 专项 Profile + schema/policy 分离 + evals 评测集）
- [appendix.md](./appendix.md) — 检视附录（模板 / 术语 / 深度阅读）
- [quality-dimensions.md](./quality-dimensions.md) — 检视质量维度定义
- [scenario-completeness.md](./scenario-completeness.md) — Evals 场景完整性方法论
- [review-policy.yaml](./review-policy.yaml) — 检视策略机器可读定义（字段语义与 ai-review.md 对齐）
- [review-result.schema.json](./review-result.schema.json) — 检视结论机器可读 JSON Schema（本地报告产出机器可读结论时遵循）

## 相关入口

- [known-technical-debt.md](../debt/known-technical-debt.md) — 未关闭发现与技术债的唯一登记处（含历次检视迁入项）
- [governance-ids.md](../governance/governance-ids.md) — 治理 ID 对照表（检视发现 ID → 一句话含义溯源）
