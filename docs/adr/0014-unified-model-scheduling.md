# ADR-0014: 统一模型管理的场景调度契约（ScenarioExecutor 独占候选调度）

> Status: Accepted（替代 ADR-0011 Router 职责的批准范围已经用户于 PR #1389 合并批准，见 Decision §3）
> Date: 2026-10-14
> Owner: 架构维护者
> Supersedes: ADR-0011（部分替代：litellm.Router 承担的候选选择 / 重试 / 冷却职责；保留语义见 Decision §3）

## Context

[ADR-0011](./0011-litellm-router-failover.md) 将手写 failover 循环迁移至 `litellm.Router`，
由 Router 承担同供应商瞬时重试（num_retries=2）、失败冷却（cooldown_time=30s）、健康路由
与跨供应商 fallback 候选选择。该职责分配建立在「候选由配置层静态声明（primary +
fallbacks）、候选内部选择细节对本层不可见」的前提上。

统一模型管理改造（D04 固定决策）将候选语义升级为用户显式契约：

1. **用户顺序即业务契约**：用户为新闻标注、股票综合分析、个股新闻风险解读三个场景分别
   定义有序候选连接；「不按位置、价格、品牌、名称或推测质量重排」（D03）。Router 按部署
   池权重 / 健康度自动选择，无法表达「用户序号优先」语义。
2. **逐次授权与计量是执行边界**：每次实际尝试须经 `authorize_attempt` 新鲜核对目标授权、
   凭据撤销与禁止外发范围（D07 / §4.3）；费用型调用在派发前须原子预算预留并绑定
   attempt_id 线性化点（D09 / D11）。Router 内部尝试对本层不可见（ADR-0011 已登记的
   双层审计让渡），无法承载逐次许可、逐次记录与预算预留。
3. **取消与 deadline 需全链贯通**：请求总时限约束排队、加载、重试、备用与解析全程；
   Router 内部重试不受本层剩余 deadline 约束，取消传播无法覆盖其内部行为。

因此候选调度职责必须上收至本层执行器（ScenarioExecutor），LiteLLM 退化为单连接适配层。
本 ADR 记录该职责迁移的契约与替代范围提案；技术实施细节（配置域 schema、账本表、
worker 生命周期）随 S1~S3 落各技术正本，不在本 ADR 展开。

## Decision

### 1. ScenarioExecutor 独占候选调度

- 执行链固定为：业务模块 → ScenarioExecutor → 单连接适配器；Executor 拥有候选选择、
  瞬态重试、冷却、并发控制与逐次记录（candidate_decisions + ai_attempt），适配器只执行
  指定连接，不再有第二层候选逻辑。
- 一次请求的固定执行流程（冻结快照 → 候选资格检查 → 并发 / 预算预留与 attempt_id 派发
  绑定 → 单连接执行与逐次授权 → 错误收敛 → 终态落账）按设计 §6.2 六步实施（S3 工作包）。
- 同候选瞬态重试使用 Tenacity `AsyncRetrying`（直接依赖 `tenacity>=9,<10`）：首次执行外
  默认再试 1 次、上限 2 次；退避 1s / 2s，合法 Retry-After 与退避取较大值，并受剩余
  deadline 限制。重试参数为本 ADR 固定契约，具体 API 用法实施时以锁定版本源码核验。
- 冷却固定 30 秒，键为 `connection_id + connection_revision`，归当前事件循环的执行器。
- 失败行为按表格化契约执行：缺文件 / 依赖 / 凭据、未授权、预算限制 → 条件跳过并记录
  决策；连接失败 / 限流 / 瞬时服务错误 / 单次超时 → 同候选瞬态重试后按用户序切换；输出
  校验非法 → 不做同候选瞬态重试、可切换候选并保留已知用量；内容安全拒绝 → 结束请求不换
  模型规避；用户取消 / 安全撤销 → 传播取消或策略终态；总 deadline 耗尽 → 结束请求。

### 2. LiteLLM 单连接化

- `litellm` 调用固定 `num_retries=0`；移除 Router fallback 与隐藏重试，适配器返回内容、
  程序用量与实际模型。
- 「无 SDK 隐藏重试 / 备用」以真实锁定库 + 受控 HTTP 端点断言实际出站次数证明（S2 DoD），
  不以全局 MagicMock 证明 SDK 行为。

### 3. 替代 ADR-0011 的批准范围（已经用户批准）

- **拟替代**：ADR-0011 Decision §1/§2 中 Router 承担的同供应商瞬时重试、失败冷却、健康
  路由与跨供应商候选切换。
- **语义保留（迁入新链，非删除）**：SEC-01 外发知情门控、SEC-03 外发审计（且由双层审计
  增强为逐次尝试审计，补回 ADR-0011「负向」中让渡的中间尝试可见性）、JSON 解析与计量
  元数据回填。
- **不影响**：[ADR-0012](./0012-news-sentiment-inference-paradigm.md) 与
  [ADR-0013](./0013-runtime-selection-final-gguf.md) 的运行时决策（GGUF 路线、外部分词
  兜底、独立 worker、仅展示边界）保留不变，不标被替代、不重写历史证据。
- **批准记录**：替代范围经用户于 PR #1389 合并批准；本 ADR Status 已转 Accepted，
  ADR-0011 Status 已更新为 Superseded（注明部分替代范围与保留语义），两 ADR 双向链接互指。

### 4. 类型与错误契约（S1~S3 实施的公共契约，先于此落定）

- 执行 / 发布类型统一定义于 `services/ai_service/model_management_types.py`（S1 交付）：
  ModelManagementDraft、ModelManagementSnapshot、ExecutionSnapshot、InferenceRequest、
  ResolvedConnection、AttemptContext、AttemptPermit、AdapterResponse、
  ValidatedBusinessOutput、ScenarioExecutionResult、ScoredText、ScoringOutcome、
  NormalizedLLMUsage、CandidateDecision、BudgetReservation、AttemptRecord、
  PersistenceOutcome、SyntheticTestContext / SyntheticTestConsent；类型为 frozen
  dataclass + slots + tuple，不发布可变 dict / list。
- **ConfigurationSaveOutcome 三态**：committed / not_committed / commit_unknown。替换前
  确定失败为 not_committed；提交后回执丢失、取消或缓存更新失败不得据异常推断未提交，按
  相同锁顺序重读磁盘凭回执（operation_id / 指纹）确认或保持 commit_unknown；结果明确前
  不自动重试、不轮换凭据、不回滚。
- **错误契约**：`asyncio.CancelledError` 始终传播，不包装成业务失败或 unavailable；安全
  状态读取 / 校验失败禁止发放许可，不用旧缓存、默认值或冻结快照放行；凭据 / 授权撤销
  阻止后续新许可，已取得许可的在途调用仅尽力取消。

## Consequences

- **正向**：用户候选顺序成为可执行契约；逐次授权、逐次审计、逐次计量与预算预留有了
  明确的执行归属；取消与 deadline 全链贯通；Router 内部不可见性问题随 Router 移除消失。
- **负向**：ADR-0011 由 Router 免费获得的健康路由 / 自动冷却需由本层显式实现（30s 冷却
  契约）；重试逻辑自担（Tenacity 成熟实现兜底，避免 ADR-0011 Alternatives 1 否决过的
  自研漂移回归）。
- **迁移期**：S0~S5 期间旧 Router 路径仍在生产服务，新执行链不同请求并存；同请求禁止
  新旧双跑，整体切换与旧账封口按迁移设计执行（S6），不在本 ADR 展开。

## Alternatives

1. **保留 Router、以部署池映射用户候选**：部署池粒度无法表达连接身份（逐连接计量、
   授权目标、冷却键、attempt 绑定均以连接为锚），且内部选择仍对本层不可见，否决。
2. **继续手写重试循环**：ADR-0011 已论证等价于重新实现 Router 子集、自研漂移风险高；
   本决策以 Tenacity 声明式重试承接同候选瞬态重试，跨候选切换由 Executor 显式编排，
   不回到手写全量循环，否决。
