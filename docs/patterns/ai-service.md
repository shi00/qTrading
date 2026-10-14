# AI/LLM 服务（ai-service）canonical 正本

> **主题**：`ai-service`（CLAUDE.md §1.8 决策树与 canonical-topics.yml 登记的 AI/LLM 服务入口）
> **路由**：新增/修改 AI 服务相关代码（`services/ai_service/`、`utils/prompt_guard.py`、`utils/egress_audit.py`、策略 AI mixin）时必读本文档，再按下方「必读 / 条件触发 / 完成判定」定位相关联正本。
> **性质**：本文档为**引用为主的汇编正本**，AI 子系统的权威内容按主题分散在既有正本（技术债表、检视 Profile、ADR、红线），此处只做索引与判定边界，**不复写权威细节，避免第二正本**。

## 必读（改动前通读）

1. **不给 AI 执行权**：[ADR-0008](../adr/0008-no-ai-execution.md)。AI 输出仅用于评分与文本展示，禁止触发交易/外部工具/代码执行/改核心配置；所有 AI 交互须经 `utils/prompt_guard.py` 的净化函数（`validate_prompt` / `sanitize_prompt` / `neutralize_external_text`，**无 `PromptGuard` 类**）与 `DataSanitizer`。调用示例见 `strategies/ai_mixin.py`。
2. **缺失值伪装红线**：[project-profile.md 的 R21 自查段](../reviews/review-profiles/project-profile.md#r21-缺失值伪装)。业务语义字段（`score`/`ai_score`/`confidence`）缺失必须用 `None`/哨兵（如 `suspend_data_absent`）表示，禁止填 `0`/`50` 等业务合法值；**已修复缺陷（DATA-03→`suspend_data_absent`）不得再判为伪装**。
3. **敏感信息脱敏与硬编码密钥**：CLAUDE.md §3.1 R9 / R10。日志与异常经 `DataSanitizer` 脱敏；API Key 从 keyring/环境变量读取，不硬编码。

## 条件触发（按改动类型定位）

### 1. 缺失/低置信度的表示法（两端表达）

- 正本：`project-profile.md` R21 自查段（上述必读 2）。
- **跨层传递**：缺失语义沿 `ai_service` 产出 → ViewModel state → UI 渲染链路保持一致，缺失一律用 `None`/哨兵表达，**不得**在任一层回填业务值；UI 对 `None`/哨兵渲染为「未知」，不渲染为数值。
- **实现模板**：`services/ai_service/output.py::validate_ai_analysis_response`（服务侧输出校验与置信度归一化）与 `strategies/ai_mixin.py::AIStrategyMixin._build_result_row`（策略侧结果行组装与纵深防御，见 [ai-strategy-mixin.md](./ai-strategy-mixin.md)）是 AI 输出进策略的落点，改这里须对照 R21 自查（`strategies/prompt_validator.py` 仅校验 Prompt 声明的数据源是否真实可用，不承担 LLM 响应校验）。

### 2. 成本与配额的单一事实源

- `services/ai_service/pricing.py`：`estimate_cost(effective_model, input_tokens, output_tokens) -> float | None`。全部依托 `litellm.cost_per_token` 计价（**无自维护 `MODEL_PRICING` 手写价格表**）；**未知/不可计价模型返回 `None` 不猜价**，免费模型返回 `0.0`（以 `cost is not None` 区分「计量为 0」与「不可计量」）；金额以**元**返回，由调用方换算为**整数分**。
- `services/ai_service/token_budget.py`：token 预算 / 上下文窗口裁剪（`DEFAULT_CONTEXT_WINDOW` / `CONTEXT_RESERVE_TOKENS` / `OUTPUT_RESERVE_TOKENS`），未知/自定义模型用保守回退值。
- `services/ai_service/usage_tracker.py`：`AIUsageTracker` 单例按月（分）原子累加，跨月轮换；未注入 engine 时 no-op 降级不阻断流程。
- `services/ai_service/pricing.py` 与 `usage_tracker.py` 即为 AI-03「完整成本计量」的现行实现（对应原 P3-AI03 债目，已闭环删除）；改动价格/成本前先按 `pricing`（元）/`usage_tracker`（分）的单位约定对齐，避免单位错配。

### 3. 输出契约与校验边界

- **输入声明 vs 输出校验职责边界**：
  - **Prompt 输入数据声明校验（策略/测试侧）**：`strategies/prompt_validator.py::validate_prompt_declarations`（配合 `DataDeclaration` / `get_declarations`）——仅校验 System Prompt 声明的数据源在当前库中是否真实可用，**不校验 LLM 响应内容**；坏数据声明用例归口该输入声明边界（如 `tests/unit/test_prompt_validator.py`），不得以输入声明校验替代输出响应校验。
  - **LLM 输出 schema 与归一化所有者（服务/策略侧）**：个股分析结论枚举与字段契约定义于 `core/prompt_base.py`（`CONCLUSION_LABEL_FIELD` / `CONCLUSION_LABELS` / `RECOMMENDATION_TO_CONCLUSION_LABEL`），响应边界校验、置信度归一化与自由文本清洗位于 `services/ai_service/output.py::validate_ai_analysis_response`（含 `VALID_RECOMMENDATIONS` 读兼容与 `_sanitize_free_text`），由 `services/ai_service/stock_analysis.py::StockAnalysisService.analyze_stock` 调用，并在策略侧经 `strategies/ai_mixin.py::AIStrategyMixin._build_result_row` 完成结果行组装与二次兜底；新闻分类输出解析、白名单校验（对照 `utils/config_models.py::NEWS_CATEGORY_MAP`）与层级兜底位于 `services/ai_service/news_classifier.py::NewsClassifier._parse_news_result`。坏模型响应用例须落在上述输出消费边界验证。
- **已知缺口**：review06 的 F13 指出 LLM 输出影响选股排序，**仅校验部分字段，缺完整输出 schema 校验与 AI 免责声明**（见 [reviews/README.md](../reviews/README.md) 未关闭发现）。改动输出域时把模型输出视为**不可信输入**（对应 [ai-ml-llm.md](../reviews/review-profiles/ai-ml-llm.md) 检视要点），补齐 schema/边界/确定性校验，不得只校验展示字段。

### 4. 外发边界与审计

- `utils/egress_audit.py`：`EgressAudit` 单例记录 LLM 云端外发的**元数据**（调用次数/状态），**不记录 prompt 内容本身**，避免二次泄露；审计文件 `USER_DATA_ROOT/logs/egress_audit.jsonl`。
- 映射：`egress_audit` 支撑 UN-07「可控的 AI 使用范围」与 ADR-0008；R9 脱敏适用于审计日志内容（不留明文密钥）。
- **新增 AI 外发调用点时**：复用 `await EgressAudit().record(...)`（`record` 为 **`async def`，须在 async 方法内调用并 await**，否则得到未 await 的 coroutine、审计静默失效），不得绕过审计单例直接外发。
- **failover 双层审计（L4/ADR-0011）**：`_router_failover` 入口按 primary 意图记录一次；响应后按 `result["model"]`（流式经 chunk.model 动态收集）与 primary 不一致时**补记真实目的地**——Router 内部 fallback 后的实际生效供应商对审计可见（SEC-03 语义）。

### 4a. 多供应商 failover（litellm.Router）

- **正本**：[ADR-0011](../adr/0011-litellm-router-failover.md)。
- `_router_failover`（services/ai_service/litellm_client.py）以 `litellm.Router` 承担同供应商瞬时重试（`num_retries=2`）、失败冷却（`cooldown_time=30s`）、健康路由与跨供应商切换；本层只保留入口门控、SEC-01/03 审计、流式解析与错误收敛。
- **门控链**：cloud 可用性 → primary 存在 → `_ensure_litellm_loaded` → `_ensure_router_loaded`（Router 惰性构造，失败降级为 `AIServiceUnavailableError`，绝不静默回退非 Router 路径）。
- **错误收敛**：非瞬态（Auth/ContentPolicy 等）直抛原异常（保持旧语义）；瞬态经 Router 重试+fallback 仍失败 → `AIServiceUnavailableError`（Tried 从配置构造）。
- **配置热生效**：`reload_config` 失效已构造 Router，下次调用重建。
- **已知行为增强**：ContentPolicyViolationError 未配置专属 content_policy_fallbacks 时 Router 默认会尝试普通 fallback（旧循环直抛语义不再保留），见 ADR-0011 Consequences。

### 5. Prompt 注入防御分层

- `utils/prompt_guard.py` 为**正则黑名单式**防御，能力有限：能拦截已知指令形态，**挡不住新闻正文等外部文本中的植入指令**（review06 的 F12，见 [reviews/README.md](../reviews/README.md)）。
- **黑名单不是完整防御**：不要把 `prompt_guard` 当作充分防护；任何 AI 功能还须叠加 ADR-0008 的执行权边界（即使注入成功后果也被限制在错误建议范围）+ 输出侧结构化校验。新增 AI 功能在设计文档中须明确其输出仅用于展示，并通过测试验证无执行路径。

### 6. 本地新闻情绪打分（FinBERT 子进程，N3-2）

- **正本**：[ADR-0012](../adr/0012-news-sentiment-inference-paradigm.md)（编码器 + CLS 池化 embedding + numpy 分类头，不用 RANK 模式）；设计细节见 `reviews/新闻达标模型微调.md` §8/§9.2。
- **实现位置**：`services/news_sentiment_scorer.py::NewsSentimentScorer`（注册单例）；编码器运行在**独立常驻子进程**（与 `LocalModelManager` 的对话子进程分离），分词必用 HF `tokenizers`（`Tokenizer.from_file`），**禁用 `Llama.embed(text)` 内部分词**（N0-4 实测与 HF 不一致）。
- **仅展示边界**：打分输出（`label`/`confidence`/`score`/`probs`）仅用于新闻流着色与展示面板（ADR-0008），**选股/策略模块不得读取**（N3-7 展示边界测试守护）。
- **缺失语义（R21）**：`score_texts` 服务不可用（未启用/未配置/加载失败/超时/worker 错误）整体返回 `None`，条目 NaN/Inf 返回 `None`，**不回填中性、不回填 0 分**；`head.npz` 形状/标签序/有限性校验不符时拒绝加载，不静默降级。
- **配置键**：`news_sentiment_enabled`（默认 `False`）/ `news_sentiment_model_path` / `news_sentiment_head_path` / `news_sentiment_tokenizer_path` / `news_sentiment_n_threads` / `news_sentiment_max_length` / `news_sentiment_timeout_s`（设置页接入见 N3-6）。
- **依赖**：`llama-cpp-python` / `tokenizers` 为可选依赖（`importlib` 延迟导入，缺失时打分降级 `None`，不影响 App 启动）；`head.npz` 与 GGUF 产物不进仓库。

### 6.1 模型产物分发与归档（N5-4；CG-3 CI 微调产线后更新）

- **产物清单与分发**：微调产物**不进仓库、不进安装包**（`AStockScreener.spec` / `installer.iss` 未引用，无需打包配置）：
  - **CI 产线产物**（`finbert-train.yml`，ADR-0013）发布于 GitHub Release `finbert-gguf-v<model_version>`：`finbert2-news-f16.gguf`（f16 编码器，发布产物；Q8_0 量化因概率差判据必 FAIL 已于 2026-10-14 移除，定因见 `scripts/export_finbert_gguf.py` docstring）+ `head.npz`（分类头：`mode`/`labels`/`cw`/`cb`[/`pw`/`pb`]，格式正本 `load_head_npz`）+ `SHA256SUMS.txt`（前两者自校验凭证）；获取步骤见 [how-to.md「13. 获取新闻情绪模型产物」](../guides/how-to.md)。
  - **`tokenizer.json` 不随 Release 分发**：从 HF 基座仓库 `valuesimplex-ai-lab/FinBERT2-base`（固定 revision，见 Release note lineage 段）获取——微调不改变词表，基座分词器即最终分词器（N0-4：必须用此外部分词器文件，禁用 GGUF 内部分词）。
  - 本地离线重训产物仍归档 `ai_models/`（已被 `.gitignore` 忽略）。
- **校验门禁**：CI 产线发布前自动运行 `scripts/verify_finbert_gguf.py --head`（N0-3 四判据：分词一致 / CLS 余弦 / 概率差<0.02 / 标签一致率≥99%；Gate 不过不发布），验证报告进 workflow artifact（schema `finbert-gguf-verification.v1`）；线下重训发布前手动运行同一脚本，报告留存 `ai_models/*.verify.json`（工程脚本清单见 `docs/guides/ci-cd.md`）。App 侧保存路径时再做轻量校验（GGUF 魔数 + `head.npz` 结构 + 分词器存在性，`AIBrainSettingsViewModel.save_ai_settings`）。
- **接入方式（首次使用由用户提供文件）**：设置页「AI 大脑」情绪卡片三路径支持手输或文件选择器（GGUF/`.npz`/`.json`），保存即生效（`NewsSentimentScorer` 每次打分重读配置，stat 签名变化自动重载）；产物不随 App 下载/打包。
- **来源边界**：模型文件来源与合规由用户承担（`tokenizer.json` 直连 HF 基座；Release 产物已过 CI 一致性 Gate，App 侧仅做魔数/结构轻量校验即使用，不构成对文件内容的信任）（已登记 [known-technical-debt.md](../debt/known-technical-debt.md)）。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」对应最小子集；AI 服务逻辑改动后须 `redline-check` + 相关单测 + `python scripts/check_docs_consistency.py`。

- 缺失语义全链路用 `None`/哨兵，无 `0`/`50` 回填；
- 成本/配额改动对齐 `pricing`（元→分）/`token_budget`（token）/`usage_tracker`（分）单位约定；
- 输出经完整 schema/边界校验，模型输出按不可信输入处理（F13 缺口不扩散）；
- 外发必经 `EgressAudit` 审计，日志经 `DataSanitizer` 脱敏，无明文密钥；
- prompt 注入依赖明确分层（guard 只是第一层），新功能声明仅用于展示并经测试验证无执行路径；
- 门禁：`redline-check`、`ruff`、相关单测通过。