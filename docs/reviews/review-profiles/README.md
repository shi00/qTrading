# AI 代码检视专项 Profile 索引

> **加载方式**：由 [ai-review.md §6 风险信号触发表](../ai-review.md#6-风险信号--专项-profile-触发表) 按需加载。每个 Profile 只补充通用维度的增量风险，不重复核心协议。
>
> **与项目宪法的关系**：项目特定规则（CLAUDE.md §3 红线 / §4 架构边界）优先于本目录通用建议。

## 专项 Profile 清单

| Profile | 适用场景 | 风险信号 |
|---------|---------|---------|
| [web-api.md](./web-api.md) | Web/API 路由、参数、上传下载 | HTTP 路由/参数绑定/内容类型/限流 |
| [frontend-mobile.md](./frontend-mobile.md) | 前端/移动端 UI 交互 | 状态同步/竞态/弱网/可访问性 |
| [messaging-data-pipeline.md](./messaging-data-pipeline.md) | 消息队列、任务、数据管道 | 幂等/ack 顺序/重试/死信/分区 |
| [database-migration.md](./database-migration.md) | 数据库 schema、迁移、回填 | DDL 锁/兼容窗口/回填/约束/回滚 |
| [cli-iac.md](./cli-iac.md) | CLI、脚本、基础设施即代码 | 退出码/路径兼容/dry-run/命令注入 |
| [library-sdk-plugin.md](./library-sdk-plugin.md) | 库、SDK、插件公共 API | API 稳定性/线程安全/默认配置/弃用 |
| [third-party-rpa.md](./third-party-rpa.md) | 第三方集成、浏览器自动化 | 稳定契约/定位稳健/会话过期/幂等 |
| [ai-ml-llm.md](./ai-ml-llm.md) | AI/ML/LLM、非确定性输出 | 不可信输出/注入/泄露/工具校验/评测 |
| [project-profile.md](./project-profile.md) | AStockScreener 项目特定规则 | 红线 R1-R18/架构边界/reviewProfile |

## 加载规则

- 出现 ai-review.md §6 触发表中的风险信号时，必须加载对应 Profile。
- 多个信号同时出现时组合加载，不得只选最熟悉的一项。
- project-profile.md 在每次检视 AStockScreener 项目代码时都应读取，包含项目覆盖规则。
