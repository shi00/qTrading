# ADR-0006: AGENTS.md 从纯指针改为最小安全集 + 指针

> Status: Accepted
> Date: 2026-09-03
> Owner: 架构维护者
> Supersedes: ADR-0002「文档架构分层决策」中 AGENTS.md 的纯指针定位

## Context

`AGENTS.md` 定位为「跨工具自动加载的极薄规则入口」，全文约 9 行，仅声明规则正本为 `CLAUDE.md` 并请读者先阅读（检视报告 DOC-08）。

该定位对**会自动加载 `CLAUDE.md` 的工具**（如本会话）完全正确——宪法已在上下文中，`AGENTS.md` 只需消除歧义。但对**只自动加载 `AGENTS.md` 的工具链**（Codex CLI 及多数遵循 AGENTS.md 约定的 agent），实际效果是：18 条红线、架构边界、反幻觉护栏能否生效，取决于模型是否主动多打开一个文件——这是概率而非工程保障。

其中至少 6 条红线的违反后果不可逆或高成本：R2（吞没 `CancelledError` → 停机挂死）、R4（SQL 注入）、R10（硬编码密钥入库）、R16（阻塞 UI 主循环）、R18（未隔离开发污染主工作区）、R1（架构越界，会被 CI 拒绝但已浪费整轮工作）。

「不承载规则内容避免多源漂移」这一理由成立，但它在**漂移风险**与**红线整体失效风险**之间做了错误的权衡：前者可用门禁消除，后者不能。

另见检视报告 DOC-13：`AGENTS.md` 是体系中唯一的孤儿治理文件——无 owner / review_triggers / canonical_for 元数据，不在任何索引，不在 `verify-versions` 白名单。

## Decision

将 `AGENTS.md` 从「纯指针」改为「**最小安全集 + 指针**」，且最小安全集**由 `redlines.yml` 生成而非手工复制**（与 `redlines.yml` 镜像 `CLAUDE.md` §3.1 红线表是同一套已验证的做法，见 ADR-0003 / ADR-0004）：

1. `AGENTS.md` 扩展为约 30 行：
   - 元数据块（P2-07 格式，owner / review_triggers / canonical_for）；canonical_for 标注为「跨工具红线最小安全集（导出镜像，非语义正本）」；
   - 强制读取指令与语言/只读默认（对应 CLAUDE.md §1.0 / §1.1）；
   - 自动生成区块，含 `rule_type: INVARIANT` 的红线（R2/R3/R4/R5/R7/R9/R10，共 7 条）id + title 一行摘要，以及 R18（worktree 隔离，属 WORKFLOW 但影响工作区整洁）；
   - 收尾指向 `CLAUDE.md` 与 `CONTRIBUTING.md`。
2. 生成区块用 `<!-- generated:redlines-invariant -->` / `<!-- /generated -->` 包裹；新增 `check_agents_md_sync()` 从 `redlines.yml` 重新渲染并断言一致，不一致则 fail。这样「多源」存在，但「漂移」不可能存在。
3. `AGENTS.md` 的语义正本仍是 `CLAUDE.md` §3.1 / `redlines.yml`；`AGENTS.md` 仅承载受门禁保障的导出镜像，不引入第二个语义正本。
4. 随本决策一并办理 DOC-13：补 owner / review_triggers 元数据、加入 `CLAUDE.md` §5 与 `CONTRIBUTING.md` 索引、加入 `verify-versions` 的 `files` 白名单。

## Consequences

- **正向**：只自动加载 `AGENTS.md` 的工具在进入首条被拦截前的成本，从「是否愿意打开 CLAUDE.md」降为「是否遵守明文的不可豁免红线」；门禁从机制上防止生成区块与 `redlines.yml` 漂移。
- **代价**：`AGENTS.md` 行数从 9 行增至约 30 行；新增一个 `check_agents_md_sync()` 检查函数接入 `docs-consistency` hook。
- **维护契约**：新增 `INVARIANT` 红线时，必须同时更新 `AGENTS.md` 生成区块（由 `docs-consistency` hook 兜底）；变更 `redlines.yml` 时不得手工修改生成区块，应改正本后同步。
- **边界**：`AGENTS.md` 不承载 `rule_type` 为 `DEFAULT` / `NEW_CODE` / `MIGRATION_TARGET` / `EXCEPTIONABLE` 的其他红线，避免入口膨胀；这些规则仍由 `CLAUDE.md` / `redlines.yml` 承载。
