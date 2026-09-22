# AI/LLM 服务（ai-service）canonical 正本

> **主题**：`ai-service`（CLAUDE.md §1.8 决策树与 canonical-topics.yml 登记的 AI/LLM 服务入口）
> **路由**：新增/修改 AI 服务相关代码（`services/ai_service/`、`utils/prompt_guard.py`、`utils/egress_audit.py`、策略 AI mixin）时必读本文档，再按下方「必读 / 条件触发 / 完成判定」定位相关联正本。
> **性质**：本文档为**引用为主的汇编正本**，AI 子系统的权威内容按主题分散在既有正本（技术债表、检视 Profile、ADR、红线），此处只做索引与判定边界，**不复写权威细节，避免第二正本**。

## 必读（改动前通读）

1. **不给 AI 执行权**：[ADR-0008](../adr/0008-no-ai-execution.md)。AI 输出仅用于评分与文本展示，禁止触发交易/外部工具/代码执行/改核心配置；所有 AI 交互须经 `PromptGuard` 与 `DataSanitizer`。
2. **缺失值伪装红线**：[project-profile.md 的 R21 自查段](../reviews/review-profiles/project-profile.md)。业务语义字段（`score`/`ai_score`/`confidence`）缺失必须用 `None`/哨兵（如 `suspend_data_absent`）表示，禁止填 `0`/`50` 等业务合法值；**已修复缺陷（DATA-03→`suspend_data_absent`）不得再判为伪装**。
3. **敏感信息脱敏与硬编码密钥**：CLAUDE.md §3.1 R9 / R10。日志与异常经 `DataSanitizer` 脱敏；API Key 从 keyring/环境变量读取，不硬编码。

## 条件触发（按改动类型定位）

### 1. 缺失/低置信度的表示法（两端表达）

- 正本：`project-profile.md` R21 自查段（上述必读 2）。
- **跨层传递**：缺失语义沿 `ai_service` 产出 → ViewModel state → UI 渲染链路保持一致，缺失一律用 `None`/哨兵表达，**不得**在任一层回填业务值；UI 对 `None`/哨兵渲染为「未知」，不渲染为数值。
- **实现模板**：`strategies/ai_mixin.py` 与 `strategies/prompt_validator.py`（见 [ai-strategy-mixin.md](./ai-strategy-mixin.md)）是 AI 输出进策略的落点，改这里须对照 R21 自查。

### 2. 成本与配额的单一事实源

- `services/ai_service/pricing.py`：`estimate_cost(effective_model, input_tokens, output_tokens) -> float | None`。全部依托 `litellm.cost_per_token` 计价（**无自维护 `MODEL_PRICING` 手写价格表**）；**未知/不可计价模型返回 `None` 不猜价**，免费模型返回 `0.0`（以 `cost is not None` 区分「计量为 0」与「不可计量」）；金额以**元**返回，由调用方换算为**整数分**。
- `services/ai_service/token_budget.py`：token 预算 / 上下文窗口裁剪（`DEFAULT_CONTEXT_WINDOW` / `CONTEXT_RESERVE_TOKENS` / `OUTPUT_RESERVE_TOKENS`），未知/自定义模型用保守回退值。
- `services/ai_service/usage_tracker.py`：`AIUsageTracker` 单例按月（分）原子累加，跨月轮换；未注入 engine 时 no-op 降级不阻断流程。
- **完整方案（未实施）**：P3-AI03-CostVisible-Full 目标为价格映射 + 累计持久化 + 三处 UI + 月度上限，见 [known-technical-debt.md](../debt/known-technical-debt.md) §P3-AI03。改动价格/成本前先对齐该债目标与 `token_budget`/`pricing` 的单位约定，避免重新设计一遍。

### 3. 输出契约与校验边界

- LLM 结构化输出的 schema 定义与校验器位置：`strategies/prompt_validator.py`（策略侧）与 `services/ai_service/stock_analysis.py` / `news_classifier.py` / `output.py`（分析侧）。
- **已知缺口**：review06 的 F13 指出 LLM 输出影响选股排序，**仅校验部分字段，缺完整输出 schema 校验与 AI 免责声明**（见 [reviews/README.md](../reviews/README.md) 未关闭发现）。改动输出域时把模型输出视为**不可信输入**（对应 [ai-ml-llm.md](../reviews/review-profiles/ai-ml-llm.md) 检视要点），补齐 schema/边界/确定性校验，不得只校验展示字段。

### 4. 外发边界与审计

- `utils/egress_audit.py`：`EgressAudit` 单例记录 LLM 云端外发的**元数据**（调用次数/状态），**不记录 prompt 内容本身**，避免二次泄露；审计文件 `USER_DATA_ROOT/logs/egress_audit.jsonl`。
- 映射：`egress_audit` 支撑 UN-07「可控的 AI 使用范围」与 ADR-0008；R9 脱敏适用于审计日志内容（不留明文密钥）。
- **新增 AI 外发调用点时**：复用 `EgressAudit.record`，不得绕过审计单例直接外发。

### 5. Prompt 注入防御分层

- `utils/prompt_guard.py` 为**正则黑名单式**防御，能力有限：能拦截已知指令形态，**挡不住新闻正文等外部文本中的植入指令**（review06 的 F12，见 [reviews/README.md](../reviews/README.md)）。
- **黑名单不是完整防御**：不要把 `prompt_guard` 当作充分防护；任何 AI 功能还须叠加 ADR-0008 的执行权边界（即使注入成功后果也被限制在错误建议范围）+ 输出侧结构化校验。新增 AI 功能在设计文档中须明确其输出仅用于展示，并通过测试验证无执行路径。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」对应最小子集；AI 服务逻辑改动后须 `redline-check` + 相关单测 + `python scripts/check_docs_consistency.py`。

- 缺失语义全链路用 `None`/哨兵，无 `0`/`50` 回填；
- 成本/配额改动对齐 `pricing`（分）/`token_budget`（token）/`usage_tracker`（分）单位与 P3-AI03 债目标；
- 输出经完整 schema/边界校验，模型输出按不可信输入处理（F13 缺口不扩散）；
- 外发必经 `EgressAudit` 审计，日志经 `DataSanitizer` 脱敏，无明文密钥；
- prompt 注入依赖明确分层（guard 只是第一层），新功能声明仅用于展示并经测试验证无执行路径；
- 门禁：`redline-check`、`ruff`、相关单测通过。