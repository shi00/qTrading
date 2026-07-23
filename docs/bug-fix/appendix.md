# AI 问题修复附录

> **加载方式**：由 [core-protocol.md](./core-protocol.md) 按需引用。包含详细方法、模板、提示词与参考实践，不强制加载。
>
> **与项目宪法的关系**：本附录是方法论参考；项目特定规则（CLAUDE.md §3 红线 / §4 架构边界）优先于本文件。

---

## A. 详细方法

### A.1 假设账本模板

维护简洁的假设账本，避免过早锁定根因或试错式补丁：

| 假设 | 支持证据 | 反对证据 | 区分性实验 | 结果 |
|------|---------|---------|-----------|------|
| H1：缓存返回旧数据 | 仅重启后恢复 | 数据库值正确 | 禁用缓存后复现 | 排除/保留 |
| H2：… | … | … | … | … |

**假设排序考虑**：能解释多少已知现象、与最近变更和故障边界是否一致、出现概率和影响、验证成本与区分能力。优先使用能同时区分多个假设的实验。

### A.2 因果分析五要素

软件故障通常由多个技术、环境和流程因素共同造成。合格的因果结论应能解释全部关键症状，而非只解释报错位置。

| 要素 | 定义 |
|------|------|
| **症状** | 用户或监控看到的异常 |
| **直接原因** | 异常发生的最近机制 |
| **关键因果因素** | 使该机制成为可能的设计、实现、配置、环境或流程条件，可能不止一个 |
| **促成因素** | 放大概率或影响的条件 |
| **逃逸原因** | 为何测试、评审或监控未提前发现 |

**示例**：

```text
症状：订单接口偶发创建两条记录。
直接原因：客户端超时后再次调用创建接口。
关键因果因素：服务端创建操作没有稳定幂等键，且调用方把超时当作"未执行"。
促成因素：代理超时短于后端处理时间。
逃逸原因：测试未覆盖"服务端成功但响应丢失"。
```

**因果结论合格标准**：
1. 能解释全部关键症状，而非只解释报错位置；
2. 能说明触发条件和问题为何此前未出现；
3. 有代码、运行时或实验依据；
4. 问题可复现时，修正关键因果因素后原复现稳定转为通过；
5. 没有更简单、证据更强的替代解释。

不以"人为失误"作为分析终点。

### A.3 影响地图构建

围绕失败路径检查：

- 输入从哪里进入，经过哪些解析和校验；
- 哪些函数、服务、线程或任务参与；
- 状态在哪里读取、缓存、修改和持久化；
- 哪些权限和信任边界被跨越；
- 哪些外部调用和副作用可能已发生；
- 错误如何传播、转换、重试或被吞掉；
- 哪些调用方、消费者和版本可能受影响；
- 哪些日志、指标和测试可以观测该路径。

通用执行路径：

```text
输入/事件
  → 解析与校验
  → 身份与授权
  → 核心逻辑
  → 状态变更
  → 持久化/消息/外部副作用
  → 响应或回执
  → 日志、指标与审计
```

对于写操作，要从副作用反向追踪授权、幂等、事务、重试、核对、补偿和人工接管。

### A.4 假设排序与实验方法

优先使用能同时区分多个假设的实验。常用方法：

- **对比法**：正常与异常版本、配置、输入、环境；
- **二分法**：在调用链、提交区间或数据处理中逐步收窄；
- **单变量实验**：每次只改变一个因素；
- **跟踪法**：沿调用、数据、状态和错误传播路径追踪；
- **故障注入**：模拟超时、断连、重复、乱序和资源不足；
- **反事实验证**：移除假定根因后，问题是否消失且机制可解释。

### A.5 实验前声明模板

每次实验前先写明：

```text
要检验的假设：
操作：
预期支持结果：
预期反驳结果：
副作用与回退：
```

---

## B. 输入契约

### B.1 理想输入清单

AI 应尽可能收集：

- 问题描述、业务影响、严重度和首次发生时间；
- 预期行为、实际行为和验收标准；
- 最小复现步骤、失败频率和样例输入；
- 错误信息、调用栈、日志、指标、截图或转储；
- 受影响版本、环境、平台、配置及依赖版本；
- 最近相关变更、正常版本或对照环境；
- 代码范围、项目规则、测试命令和禁止事项；
- 已尝试措施及其结果。

信息不足不意味着必须立即提问。AI 应先用安全的只读方式检查仓库和现有证据；只有缺少的信息会实质改变修复方案、风险或权限边界时，才提出聚焦问题。

### B.2 标准问题陈述模板

调查开始时，将问题归一化为：

```text
问题：
预期行为：
实际行为：
影响范围与严重度：
触发条件：
受影响版本/环境：
已知正常版本/环境：
复现状态：
验收标准：
明确非目标：
权限与限制：
```

---

## C. 输出模板

### C.1 标准交付结果（最小）

完成时至少说明：

- 根因及支持证据；
- 修复内容和为何能消除根因；
- 变更文件和影响范围；
- 已运行的验证及结果；
- 未运行检查、残余风险和人工确认项；
- 若只完成缓解或诊断，明确说明尚未完成的工作。

### C.2 完整问题修复报告模板

```markdown
# 问题修复报告

## 结论
- 状态：已修复 / 部分修复 / 仅诊断 / 无法判断
- 严重度：
- 根因摘要：
- 验收结论：

## 问题与范围
- 预期行为：
- 实际行为：
- 触发条件：
- 受影响版本/环境：
- 修复范围：
- 非目标：

## 诊断证据
- 最小复现：
- 关键观测：
- 已排除假设：
- 根因与促成因素：

## 修复内容
- 变更：
- 修复机制：
- 兼容性与安全影响：

## 验证
- 修复前失败证据：
- 已运行检查及结果：
- 未运行检查及原因：

## 残余风险与后续动作
- 残余风险：
- 待人工确认：
- 建议的防复发措施：
```

---

## D. 可直接使用的 AI 执行提示词

```text
你是一名负责问题诊断和修复的资深软件工程师。请在用户授权和项目规则范围内完成任务。

原则：
1. 先建立预期/实际行为和环境基线，再修改；
2. 尽量稳定复现，并保存修复前失败证据；
3. 使用假设—实验方式定位根因，区分事实、推断和未知；
4. 修复根因，不通过吞错、放宽断言、跳过测试或关闭门禁制造假成功；
5. 采用最小但完整的改动，不顺手重构无关代码；
6. 保护用户已有修改、敏感信息和生产环境；
7. 实际运行风险相称的测试、构建、静态检查和目标环境验证；
8. 无法验证时明确披露，不得使用"应该可用"冒充证据。

执行：
- 读取项目规则、相关契约、实现、调用方和测试；
- 归一化问题描述，明确范围、验收标准和限制；
- 建立失败路径、数据、状态、权限和副作用地图；
- 提出并按信息增益验证多个假设；
- 确认根因、促成因素和问题逃逸原因；
- 设计并实施最小完整修复；
- 验证原复现、回归测试、邻近行为及必要质量门禁；
- 复核最终 diff，检查无关改动、安全、数据和兼容性风险。

输出：
- 先给状态和根因摘要；
- 说明修复内容及其机制；
- 列出实际运行的验证和结果；
- 分开列出未验证项、残余风险和人工待办。
```

---

## E. 参考实践

本附录是自包含的，执行时不依赖外部网页。以下资料仅用于说明方法来源和便于追溯。

链接已于 **2026-07-23** 在本文编写环境中核验为能够返回内容，这不代表所有企业或区域网络均可访问。官方站点仍可能因文档迁移、企业网络策略或区域网络限制而无法直接访问；遇到这种情况，可使用每项给出的"文献标识/检索词"在官方站点或组织认可的文档镜像中检索，不应改用来源不明的转载内容。

### E.1 通用工程与安全方法

1. [Google SRE Book — Chapter 12: Effective Troubleshooting](https://sre.google/sre-book/effective-troubleshooting/)
   - 检索词：`Google SRE Effective Troubleshooting Chapter 12`
   - 采用内容：假设—演绎式排障、观测与实验、问题定位中的 what/where/why。
2. [Google Cloud — Troubleshooting tips: Help your cloud provider help you](https://cloud.google.com/blog/products/gcp/troubleshooting-tips-help-your-cloud-provider-help-you)
   - 检索词：`Google Cloud Triage Examine Diagnose Test and Treat`
   - 采用内容：分流止损、收集观察、建立假设、测试处置和长期问题动态摘要。
3. [NIST SP 800-218 — Secure Software Development Framework (SSDF) Version 1.1](https://csrc.nist.gov/pubs/sp/800/218/final)
   - 稳定文献标识：`NIST SP 800-218`，DOI：`10.6028/NIST.SP.800-218`
   - [官方 PDF](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf)
   - 采用内容：漏洞确认、风险排序、根因分析、同类缺陷排查及开发流程改进。
4. [OWASP — Secure Code Review Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secure_Code_Review_Cheat_Sheet.html)
   - 检索词：`OWASP Cheat Sheet Series Secure Code Review`
   - 采用内容：入口、数据流、认证授权、业务逻辑、错误处理、配置部署和安全回归检查。

### E.2 AI 编程代理实践

以下资料用于交叉验证 AI 代理的任务描述、执行边界和结果验证方式，不代表本指南依赖某一厂商产品。

1. [Anthropic — Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)
   - 检索词：`Claude Code Best Practices Give Claude a way to verify its work`
   - 采用内容：给 AI 提供可执行的通过/失败标准，并要求用测试、构建结果或截图证明结果。
2. [GitHub — Best practices for using Copilot to work on tasks](https://docs.github.com/en/copilot/how-tos/agents/copilot-coding-agent/best-practices-for-using-copilot-to-work-on-tasks)
   - 检索词：`GitHub Docs Copilot coding agent well-scoped tasks acceptance criteria`
   - 采用内容：清晰问题描述、完整验收标准、相关文件提示和仓库级构建测试说明。
3. [GitHub — Responsible use of Copilot agents](https://docs.github.com/en/copilot/responsible-use/agents)
   - 检索词：`GitHub Docs responsible use Copilot agents human oversight`
   - 采用内容：AI 输出必须经过人工审查和测试，不能替代安全、业务和发布责任。
4. [Microsoft Learn — Debug with GitHub Copilot in Visual Studio](https://learn.microsoft.com/en-us/visualstudio/debugger/debug-with-copilot?view=visualstudio)
   - 检索词：`Microsoft Learn Debugger Agent reproduce instrument isolate validate fix`
   - 采用内容：使用真实运行时状态执行"复现—插桩—隔离—修复—验证"闭环。
5. [OpenAI Codex — Best Practices](https://developers.openai.com/codex/learn/best-practices)
   - 检索词：`OpenAI Codex Best Practices AGENTS.md verification`
   - 采用内容：用项目指令固化仓库结构、构建测试命令、工程约束和完成标准。
6. [OpenAI Codex — Agent approvals and security](https://developers.openai.com/codex/agent-approvals-security)
   - 检索词：`OpenAI Codex agent approvals security sandbox network access`
   - 采用内容：使用沙箱、审批策略和网络边界控制代理权限与副作用。

参考资料会持续演进。实际使用时，应以组织采用的标准版本、项目契约和可访问的官方最新文档为准。
