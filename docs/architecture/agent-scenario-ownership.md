# 智能体场景归属与调用方契约（W01-B 已批准）

> **Status:** Approved（已批准场景归属；当前运行路径未改变）
> **Date:** 2026-11-01
> **Owner:** 架构维护者
> **Task:** W01-B
> **Inputs:** 内部需求基线（本地存档，不入库）
> - [ADR-0008](../adr/0008-no-ai-execution.md)
> - [ADR-0014](../adr/0014-unified-model-scheduling.md)
> - [ADR-0016](../adr/0016-agent-permission-exception.md)
> - [agent-contract.md](./agent-contract.md)
> **Approval:** Approved 2026-11-01（见文末「审批回执」）

## 1. 用途与效力

本文件逐项登记**实际业务/后台/诊断模型入口**的拟议归属：直接场景推理、Agent 执行、无模型读取，或保持现状待定。

- 本文件不是 ADR，不改变现有调用链、权限、工具门控、依赖或数据库。
- 未获批准的条目保持**现有行为**；本文件不能成为迁移依据。
- 归属只决定“哪个执行路径负责该场景”，不改变既有数据准备、PIT/质量、数学筛选、回测、业务保存或新闻语义。
- 静态索引不是完整动态调用图；本文件不声称已完成全仓审计或覆盖所有动态配置分支。

## 2. 场景归属表

| ID | 入口/调用链 | 拟议归属 | 输出与落点 | 权限/治理边界 | 迁移任务 |
|---|---|---|---|---|---|
| S01 交互式股票/批策略 | [AIStreamMixin.run_strategy](../../ui/viewmodels/ai_stream_mixin.py) → [AIStrategyMixin.run_ai_analysis](../../strategies/ai_mixin.py) → `_mixin_analyze_single` → [AIService.analyze_stock](../../services/ai_service/__init__.py) → [StockAnalysisService.analyze_stock](../../services/ai_service/stock_analysis.py) | **Agent**（T16 后唯一路径） | 原股票结构化结果 + 业务行/筛选链；程序构造 Agent execution/轮次/身份引用 | E1/E3；PIT/质量/数学筛选不变；业务 owner/slot | T16/W16-A—C |
| S02 其他策略 AI 分支 | [PolarsBaseStrategy.filter](../../strategies/polars_base.py)、[OversoldStrategy.filter](../../strategies/oversold_strategy.py)、[AISelectionStrategy.filter](../../strategies/ai_strategy.py) 均进入同一 `run_ai_analysis` | **Agent**（与 S01 同一入口） | 同 S01 | 非 AI/禁用 AI 的数学分支保持原样 | T16 |
| S03 单股重试 | [AIStreamMixin.retry_single_stock](../../ui/viewmodels/ai_stream_mixin.py) → [AIStrategyMixin.retry_single](../../strategies/ai_mixin.py) → `_mixin_analyze_single` | **Agent 重试命令**（新命令身份关联原轮次） | 原结果消费者；旧轮次记录保留 | 不复用提交键；不重复未知非幂等动作 | T16/W16-C |
| S04 既有夜间预测 | [app/bootstrap.py](../../app/bootstrap.py) 注入 → [nightly_prediction._prediction_logic](../../services/scheduled_jobs/nightly_prediction.py) → 同股票链 → [ReviewManager.save_results](../../data/persistence/review_manager.py) | **Agent 推理 + 既有业务触发**（不新增 Agent 自主定时器） | 原夜间业务保存；Agent 历史可查 | D04；无 UI 决定保持等待；TaskManager 只管原业务协程 | T16/W16-B/C |
| S05 新闻主题/情绪 | [NewsSubscriptionService._processing_loop](../../services/news_subscription_service.py) → [AIService.classify_news](../../services/ai_service/__init__.py) → [NewsClassifier](../../services/ai_service/news_classifier.py) bundled/dedicated → `market_news` 保存/通知 | **直接场景推理**（不启动 Agent/聊天/规划） | 原 `market_news` 数据与通知 | 现有场景候选/授权/预算；不生成聊天 | 无迁移；T01 保持 |
| S06 专用 FinBERT 情绪编码 | [gguf_sentiment_adapter.invoke](../../services/ai_service/gguf_sentiment_adapter.py) → [NewsSentimentScorer.score_for_connection](../../services/news_sentiment_scorer.py)；旧消费者可调 `score_texts` | **直接专用编码模型** | 原情绪评分/结构化结果 | 不包装成聊天/工具模型；不额外再评分 | 无迁移；T01 保持 |
| S07 个股新闻风险 | [NewsInsightViewModel._analyze_task](../../ui/viewmodels/news_insight_view_model.py) → [NewsInsightService.analyze](../../services/news_insight_service.py) → `AIService.analyze_news_risk` → [NewsRiskAnalyzer](../../services/ai_service/news_risk.py) | **直接场景推理**（本轮不迁 Agent） | 原缓存/成功快照/evidence_only/failed 语义 | 保留证据裁剪、指纹、冻结轨道；不因名称含“解读”迁移 | 无迁移；T01 保持 |
| S08 新连接合成测试 | [ModelConnectionsViewModel.test](../../ui/viewmodels/model_connections_view_model.py) → `begin_synthetic_test` → 一次性 consent → 固定合成请求 → 适配器 `invoke` | **直接测试许可通道**（不启动 Agent） | 连接测试结果；不保存业务 grant | 不借测试许可作聊天/股票/摘要授权 | 无迁移；T01 保持 |
| S09 旧连接/本地模型验证 | [OnboardingViewModel.test_llm_connection](../../ui/viewmodels/onboarding_view_model.py) → [AIService.test_connection](../../services/ai_service/__init__.py) → [LiteLLMClient.test_connection](../../services/ai_service/litellm_client.py)；本地 `load_model(..., is_verification=True)` | **直接诊断**（不启动 Agent） | 诊断结果 | 不得因“统一入口”获得业务工具或权限 | 无迁移；T01 保持 |
| S10 新聊天/协作/摘要 | 目标：`AgentService` → 选定 Agno 容器 → Python 模型桥 → 同一网关 | **Agent** | 主/子公开历史；摘要内部上下文版本 | 主/子/摘要均授权/计量；纯历史不启动模型 | T05、T06、T10、T11 |
| S11 Agent 压缩/摘要 | Agno 原生 Compaction/SessionSummaryManager → 同模型网关 | **Agent 内部**（不新增用户任务） | 上下文版本 + 统一用量；原文仍可查 | 摘要模型走授权系统连接；失败不切换新上下文 | T02、T11 |
| S12 纯历史浏览/搜索/复制 | `AgentHistoryService`/DAO（未来 T06/T14） | **无模型** | 原历史/索引副本 | 不触发推理、MCP 或摘要；索引滞后可见 | T06、T14 |
| S13 数学筛选/数据同步/回测/业务保存 | 现有 Polars/DAO/ReviewManager/回测链 | **无模型**（保持原样） | 原业务数据/回测结果 | 不因 Agent 迁移改变算法、单位、时点或保存语义 | 无迁移 |

## 3. 调用方契约

1. **唯一 Agent 入口**：UI、股票业务、其他合法上层模块均通过注入的 `AgentService` 调用 Agent；UI 只是消费者之一，不依赖 Flet/Page/ViewModel，不操纵 Agno 内部对象。
2. **来源可信**：`source_module` 由注入的调用方上下文验证，不信任请求正文自报“管理员”或“系统”。
3. **股票身份**：交互式/夜间/单股重试在首次提交前持久化 `BusinessOperationRef` 与 `StockAgentCall`；两层 `analyze_stock` 以 keyword-only `agent_call` 透传；无身份的旧兼容调用明确“一次调用一个新意图”，不以内部临时 UUID 冒充幂等。
4. **不双链**：T16 启用后，股票分析不再在 Agent 失败时静默回旧 `_chat_completion` 或旧股票场景分支；旧公共接口最多保留薄兼容转发。
5. **TaskManager 边界**：TaskManager 继续管理原筛选/夜间业务协程，不接受 Agent 叶任务；Agent 任务不使用任务中心作为必需入口。
6. **直模不变**：S05—S09 继续经既有场景服务/`ScenarioExecutor` 或专用测试/诊断许可通道；不因“统一 UI”迁 Agent，不新增第二供应商/Router/账本。
7. **观察隔离**：订阅断开、切页、等待超时只释放观察资源，不自动取消 Agent；取消/终止必须显式控制命令；`asyncio.CancelledError` 清理后继续抛出。
8. **无 UI**：必要的结构化决定保持等待，不自动批准；夜间/API 场景不因无人值守而放行。
9. **结果归属**：直接推理结果落原业务域，不强制生成聊天；只有真正 Agent 运行生成主/子公开历史；历史摘要不删原文。

## 4. 未决与风险

| 事项 | 现状 | 处理 |
|---|---|---|
| 全量动态调用图 | 已做静态 AST 索引（204/294 文件、29 个模型相关调用/转发点），非全仓动态审计 | 迁移前逐入口核对；未核对动态配置的入口不宣称已审计 |
| 新闻风险等“解读”入口 | 名称含“解读”但当前不迁 Agent | 保持现状；若未来改归属，须显式修订本文件与 AC |
| 夜间业务范围 | 保留既有触发，不新增 Agent 定时器 | 须审批确认；若改为禁止定时触发 Agent，须显式修订 |
| 连接诊断/合成测试 | 专用许可通道，不是业务 Executor 授权 | 不得借作聊天/股票/摘要的授权或真实资料发送通道 |
| 其他策略 AI 分支 | 共享 `run_ai_analysis`，但各策略数学语义不同 | 只迁分析入口，不改数学/同步/回测；逐策略回归 |
| 旧兼容调用 | 无 `agent_call` 的旧调用 | 明确一调用一新意图；需要幂等的调用方必须使用新参数或 F33 API |

## 5. 审批回执

> 本回执由有权维护者于 2026-11-01 确认；不改变当前运行路径。

- 决定：`[x] 批准` / `[ ] 退回修改` / `[ ] 拒绝`
- 批准人：产品负责人（有权维护者）
- 日期：2026-11-01
- 批准范围：`[x] S01—S04 股票/夜间 Agent` `[x] S05—S09 直接场景保持` `[x] S10—S11 Agent/摘要` `[x] S12—S13 无模型/原样`
- 未决入口处理：`[x] 保持现状`
- 修订说明：无，按本文件 §2 归属表批准
- 生效条件：与 ADR-0016 及 G02/G03/G04/G05 门禁共同生效；本文件不改变运行路径
- 备注：批准全部归属；不启用 Agent 工具，不迁移新闻/FinBERT/风险/诊断

---

**相关文档：** [agent-contract.md](./agent-contract.md)、[ADR-0016](../adr/0016-agent-permission-exception.md)
