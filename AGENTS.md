# AGENTS.md — AStockScreener 跨工具规则入口

> **对应版本**：0.9.0（产品版本，与 pyproject.toml 一致）
> **元数据**（P2-07 统一格式，规则集版本与产品版本分离，与 CLAUDE.md 一致）：
> - owner: 架构维护者
> - ruleset_version: 1.3.0（规则集版本，与 CLAUDE.md 同步，规则变更时递增）
> - review_triggers: 红线新增/变更、Flet 升级、检视报告发布时
> - canonical_for: 跨工具红线最小安全集（导出镜像，非语义正本）

本文件为跨工具自动加载的规则入口（P3-03）。**修改任何代码前必须先完整阅读 [CLAUDE.md](./CLAUDE.md)（项目宪法，红线/架构边界/AI 行为准则的正本）；未读取前只做只读调查。**

- **语言约定**：始终使用简体中文回复。
- **只读默认**：回答/诊断默认只读，文件或外部状态修改须用户明确授权（对应 CLAUDE.md §1.0 / §1.1）。

下方为不可豁免红线（`rule_type: INVARIANT`）的最小安全集，由 [redlines.yml](./docs/governance/redlines.yml) 生成的导出镜像（由 `check_agents_md_sync` 门禁守护，见 CONTRIBUTING.md「文档一致性校验」）。正本以 CLAUDE.md §3.1 / redlines.yml 为准，本区块 **SHALL NOT 手工修改**，如需变更请改正本后同步：

<!-- generated:redlines-invariant -->
- R2：异常吞没
- R3：模糊压制
- R4：SQL 注入
- R5：僵尸引擎操作
- R7：测试状态污染
- R9：敏感信息泄露
- R10：硬编码密钥
- R18：未隔离开发
<!-- /generated -->

> **入选规则（治理决策）**：本区块组成规则 = [redlines.yml](./docs/governance/redlines.yml) 中全部 `rule_type: INVARIANT` 红线，另加 R18（`rule_type: WORKFLOW`，守护工作区整洁，跨工具一致适用）；不含其余 WORKFLOW / `仅人工评审` 红线（如 R11/R17，其为 `NEW_CODE` 类，不构成跨工具无条件不变量）。区块内容以本文件上方生成的 `<!-- generated:redlines-invariant -->` 区块为唯一数据源（由 `check_agents_md_sync` 门禁守护，禁止手工改）；红线上调为 INVARIANT 或新增守护工作区整洁的红线时，仅需改正本 [redlines.yml](./docs/governance/redlines.yml) 再同步生成区块，本段为规则描述、不随清单逐条复制。

实现规范、代码模板与工作流步骤见 [CONTRIBUTING.md](./CONTRIBUTING.md)。
