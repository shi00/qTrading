# AI-01 修复设计方案 · 不可计价模型成本预算失效

> 检视报告：`reviews/09-20/REVIEW-04-AI决策链可信度.md` → AI-01 (Critical)
> 分支：`fix/ai01-unpriced-cost-budget`（隔离 worktree，R18）
> 状态：方案待检视（两轮：宪法合规 + 对抗性）

## 一、问题定位（已确证）

成本追踪链路对「不在 `MODEL_PRICING` 价格表内的模型」（即非 DeepSeek 的 OpenAI/Claude/通义/Kimi 等
及自定义模型）完全失效：

1. `pricing.estimate_cost()` 对未知模型返回 `None`（设计正确）。
2. `ai_mixin.run_ai_analysis()` 累加处（`ai_usage_cluster`）用
   `isinstance(cost, (int, float)) and cost > 0` 判断，`None` 被静默跳过，**无 unpriced 计数**。
3. 批次末 `_track_cost` 只持久化 `cost_cny`；0 元不写库。
4. `_ai_budget_exhausted()` 读取的月成本恒 0 → `0 >= round(limit*100)` 恒假 → 预算护栏永不触发。
5. UI「本月累计」恒显示 0.00（`ai_brain_settings_view_model.py`），或批次汇总只显示 cost_cny。

**根因**：`None`（不可计量）从 `estimate_cost` 出来后任一层都没有把「未知」状态向下传递，
最终被 `if cost > 0` 悄悄吞成 0 —— 违反 **R21「缺失值伪装」**：把「无法计量」呈现为「计量结果为零」。

已确认 `res["cost"]` 直接来自 `litellm_client.py` 的 `estimate_cost(...)` 返回（None），
`res["usage"].total_tokens` 总是存在 → 「有 usage 但 cost 为 None」即可判定为不可计价调用
（免费模型 cost==0.0 属已计价，不算不可计价，见 3.2）。

## 二、修复范围（用户已拍板）

- **核心正确性修复**（本分支必须）：不可计价调用计数 + 诚实展示（消除 R21）。
- **预算护栏策略**：用户已选「保守 —— 提示确认」。
- **计价统一 litellm 化**：彻底删除自维护 `MODEL_PRICING` 手写价格表与手写公式，全部成本估算
  依托 `litellm` 能力（`cost_per_token` + 官方模型目录），不造轮子。
- **模型清单全面对齐 litellm 官方 key**：模型 id / context 以 litellm 为权威源，项目仅保留供应商
  展示层元数据（icon/name/key_prefix/urls/tag/custom/azure/PROVIDER_CATEGORIES）。
- **预算护栏持久化**：复用项目现有 app_state（`AIUsageTracker`），不引 litellm Proxy（过度工程）、
  不用 `BudgetManager` 文件持久化。

## 三、设计方案

### 3.1 数据侧：AIUsageTracker 持久化不可计价量

`services/ai_service/usage_tracker.py` 已实现按月持久化不可计价调用次数/tokens（`_AI_UNPRICED_CALLS /
_AI_UNPRICED_TOKENS`、`month_unpriced_calls_key/tokens_key`、`get_month_unpriced()->(int,int)`、
`add_unpriced(calls,tokens,when)` 单事务原子 upsert）。保留复用，本设计不再重复实现。

### 3.1b 成本估算来源：全 litellm 计价（用户拍板重写 2026-09-20）

**决策**：删除自维护 `MODEL_PRICING` 手写价格表与手写公式，全部成本估算依托 `litellm`
内置 `cost_per_token`，全程不自己写计价公式、不维护手写价目表。**模型 id 统一对齐 litellm
官方 key**（供应商清单半年前已过时，模型管理全权交给 litellm 专业组件），因此 **无需
`register_model` 注入私有价**——官方 key 在 litellm 价格表天然有价（已实测 deepseek-chat /
gpt-5.4-mini / claude-sonnet-4-6 / gemini-2.0-flash 均可计价）。
`litellm_client.py` 的 `estimate_cost(...)` 调用**不改**（接口不变），只改 `pricing.estimate_cost` 内部。

**litellm 能力（已对锁定版本核实）**：
- `litellm.cost_per_token(model, prompt_tokens=, completion_tokens=)` → `tuple[float,float]`
  每 token **美元**；模型不在价格表时**爬异常**（非返 0）；model 支持 `provider/model` 前缀逐级剥匹配。
- `litellm.models_by_provider: dict[provider -> set[str]]`：内置按供应商分组的官方模型目录。
- `litellm.model_cost: dict[model -> {...}]`：每模型含 `provider`/`max_tokens`/`max_input_tokens`/
  `max_output_tokens`（context 权威源）。

**方案（`services/ai_service/pricing.py` 重构）**：

1. **删除** `MODEL_PRICING` 手写价格表与 `estimate_cost` 的手写公式。
2. 新增汇率常量（`# NOTE(lazy)` 注明 ceiling 与 upgrade 触发条件）：
   ```python
   _USD_TO_CNY_RATE = 7.2  # NOTE(lazy): 简化，固定汇率不实时拉取. ceiling: 波动<±10%. upgrade: 需精确对账或引入实时汇率时
   ```
3. `estimate_cost(effective_model, input_tokens, output_tokens)` 重构为：
   ```python
   if input_tokens < 0 or output_tokens < 0:
       return None
   try:
       in_usd, out_usd = litellm.cost_per_token(
           effective_model, prompt_tokens=input_tokens, completion_tokens=output_tokens
       )
   except Exception:
       return None  # 不可计价 → 上层 unpriced 兜底（不把「不可计量」伪装成 0，R21）
   cost = (in_usd + out_usd) * _USD_TO_CNY_RATE
   return round(cost, 4)
   ```
   - **免费模型语义（对齐 litellm）**：litellm 对已定价免费模型返回 `(0.0, 0.0)`（非抛异常），
     → `cost == 0.0`（float 非 None）。上层判定须用 **`cost is not None`**（而非 `>0`）区分
     「计量为 0（免费）」与「不可计量（None）」，避免免费模型被误计为 unpriced 骚扰用户。
   - 不自行按 provider 剥前缀：`cost_per_token` 原生支持含 provider 前缀的 `effective_model`
     （逐级剥前缀匹配），与项目 `f"{provider}/{model}"` 的既有形态兼容。
   - `try/except` 仅拦截「未知模型/缺价」爬异常 → None（控制流分支，用 `except Exception`）。
4. **litellm 升级韧性**（用户要求考虑后续升级）：对 litellm 仅依赖其**稳定公开语义**
   （`cost_per_token` 返回美元元组、未知模型抛异常、`models_by_provider` 是 dict、`model_cost` 是 dict），
   不依赖内部私有字段/Type；对 `model_cost` 的取值统一经 `dict.get()` 防御，避免字段增删导致
   `KeyError`；升级后回归测试（见第四节）锁定行为。**不擅自改 pyproject 依赖**。
5. **不引 litellm Proxy / BudgetManager**（过度工程，YAGNI）：只用 litellm 单一函数族完成
   「计价」，不做服务端代理、不做预算文件持久化。

> 已知局限（写入风险段）：极少数国产/边缘官方 key 在 litellm 价格表可能缺价，此时回退
> unpriced——有意的诚实兜底。因模型 id 已全面对齐官方 key，覆盖面 ≥ 现状。

### 3.1c 模型供应商预制清单：全面对齐 litellm 官方目录（用户拍板 2026-09-20 方案B · 修正重写 2026-09-21）

**决策**：供应商/模型清单以 **litellm 官方目录为唯一权威源**。**移除**一切项目预置的商业/主观展示：
"推荐/主流"标签、注册/控制台/定价/模型链接等 URL 字段全部删除。用户从 litellm 列表**自行选择**
使用的供应商与模型，不显示任何注册引导，不做任何商业推荐。

**UI 快速定位交互（用户拍板 2026-09-21 收敛）**：
1. **搜索框 + 实时结果列表**：模型选择采用一个可输入搜索框 + 随输入实时渲染的结果列表面板，
   替代原普通 `Dropdown`（对齐"模型大目录很快、普通下拉装不下"的事实）。
2. **跨供应商一键搜索**：搜索范围是**全部投影供应商**，命中一行同时设定「供应商+模型」；
   不止在当前供应商内过滤。
3. **最近/常用回记置顶**：本地记录最近选用的供应商+模型，结果列表顶部置顶复用。
4. **供应商分组折叠（浏览/空搜索态）**：无关键词时按供应商分组、可折叠展开浏览。
交互形态 = **搜索(一键定供应商+模型) + 分组浏览 + 回记置顶** 组合。

**关键事实（已对锁定版本 litellm 实测，2026-09-21）**：
- litellm 官方目录（`models_by_provider` 各 key 含模型数，1.100.1 实机）：`openai`=231、`gemini`=94、
  `mistral`=94、`dashscope`(通义)=47、`moonshot`=24、`zai`(智谱 Z.ai)=16、`anthropic`=28、
  `minimax`=10、`deepseek`=16、`azure`=（部署制，无型号目录）。
  （review-pr1073 A2 修正：早期文档的 `qwen_ai_platform`=47 / `qwencloud`=45 为不存在之 key——该版本
  litellm 的 `models_by_provider` 无此二键，仅 `dashscope` 承载 qwen 模型；相关去重表述已删除）
- **dashscope 目录含第三方托管模型**（`deepseek-v4-flash`/`glm-5.1`/`kimi-k2.7-code` 等，探针实证）。
  投影到 qwen 供应商须按**品牌前缀过滤**（`catalog_brand_prefixes=("qwen",)`），避免跨牌混配（review-pr1073 D1）。
- **运行时路由（`litellm_client._build_litellm_params`）用 `litellm_prefix` 拼 `<prefix>/<model>`**；
  qwen / zhipu / moonshot / minimax 的 `litellm_prefix` 均为 `"openai"`（走 OpenAI 兼容端点）。
  因此**「UI 按 `litellm_prefix` 枚举投影」是错误设计**——会把这几家全挤进 openai 目录、混入
  GPT 模型。早期方案第 2 点已废弃。须为「UI 枚举」与「运行路由」**解耦**。
- **zhipu（智谱）在 litellm 的官方目录键为 `zai`**（智谱国际品牌 Z.ai，含 16 个 GLM 模型，均有计价；
  国内 bigmodel 品牌键 zhipu/bigmodel 查无）→ 与 qwen→dashscope 同范式：`litellm_catalog_key: "zai"`
  使 zhipu 可枚举官方模型且可计价（review-pr1073 A1 修正：早期「zhipu 无目录」断言为认知幻觉）。
- **cloud 计价的 provider 键名 == 枚举目录键名**（deepseek/anthropic/gemini/mistral/openai/moonshot/
  minimax），与路由前缀不一致的有 dashscope 系（qwen）与 zai（zhipu）、gemini（google）→ 统一
  经 `litellm_catalog_key` 字段映射（review-pr1073 A4 补充：google 亦须映射，实现已覆盖）。

**目标文件**：`utils/llm_providers.py`（数据+纯函数）+ 新共享组件
`ui/components/model_picker.py` + 两处消费点（LLMConfigPanel / FailoverConfigPanel）。

**A. `LLM_PROVIDERS` 字段瘦身（删/留/增）**：
- **删**：`models`（硬编码型号数组）、`console_url`/`signup_url`/`pricing_url`/`models_url`
  （全部 URL 字段）、`tag`（及 `is_recommended_model`/`get_display_tag` 两个 tag 函数）。
- **留**：`name`/`name_en`/`icon`/`key_prefix`/`custom`/`azure`/`PROVIDER_CATEGORIES`/
  `base_url`（**功能性 API 端点，非"注册/推广"信息，必需保留**）/`litellm_prefix`（**运行路由必需，保留**）。
- **增**：`litellm_catalog_key`（UI 枚举专用：litellm 官方目录 key）。缺省回退**自身 `provider_id`**
  （**不得**回退 `litellm_prefix`——openai 撞车；见 §3.1c-review #9）。
  取值：deepseek→`deepseek`、openai→`openai`、anthropic→`anthropic`、google→`gemini`、
  mistral→`mistral`、moonshot→`moonshot`、minimax→`minimax`、qwen→`dashscope`、
  zhipu→`zai`（review-pr1073 A1）、azure→（部署制，无目录）、custom→（自由文本，无目录）。

**B. 运行路由不受影响**：`_build_litellm_params` 仍读 `litellm_prefix`；模型清单只决定「可选的
model id 字符串」，不改变路由、不改变存储 model 格式 → 无行为变更、无存量配置迁移、不影响
failover 前缀判定（`model.split("/")[0]`）。

**C. 新增 `utils/llm_providers.py` 纯函数（函数体内惰性 import litellm，带 `# lazy-import:` 注释并
登记 pyproject 契约 5 ignore_imports 白名单）**：
1. `get_litellm_models_by_provider() -> dict[provider_id -> list[dict]]`：
   - 按每个 provider 的 `litellm_catalog_key`（回退 `litellm_prefix`）投影 `litellm.models_by_provider`，
     **只投影项目支持的供应商**，不把 litellm 全部 98 个供应商全量塞入。
   - 每模型 `{id, context}`；context 取 `litellm.model_cost.get(id, {})` 的
     `max_input_tokens`/`max_tokens`/`max_output_tokens`（首非零），全缺退 0 前先回退
     `litellm.get_model_info(id)` 第二通道（review-pr1073 A3/C2：model_cost 的 max_* 对
     openai 一批模型为 None，而 get_model_info 可补齐；无法补齐才 0，`dict.get` 防御）。
   - 同一 catalog_key 被多个项目 provider 复用时按 model id 去重；模型 id 可能是
     `provider/model` 形态，按 `/` 右侧 id 归一。（review-pr1073 A2 修正：「qwen 系三个 key」
     去重意图已落空——`qwen_ai_platform`/`qwencloud` 在 1.100.1 不存在，qwen 组=dashscope；
     附加**品牌前缀过滤**避免跨牌混配，见上文 D1）
   - **模块级缓存**投影结果（避免每次渲染/搜索重投影 98 供应商）；缓存键含 litellm 版本号，
     升级后失效重算。
   - 升级韧性：`dict.get`/try-except 防御 key 增减，某 key 缺失只令该 provider 空列表，不崩 UI。
   - 品牌过滤（review-pr1073 D1）：provider 配置 `catalog_brand_prefixes` 时，仅投影以任一
     前缀开头的模型 id（对去重后 id 判定）；未配置即全部投影（默认供应商目录同品牌无需过滤）。
2. `search_models(keyword) -> list[dict]`：在投影结果上按「provider 名 + provider_id + model id +
   key_prefix」做大小写不敏感子串匹配；返回拍平列表，每项含
   `{provider_id, provider_name, icon, model_id, context}`；排序按（PROVIDER_CATEGORIES 分类顺序,
   供应商名, 模型名）稳定排序防抖动；空 keyword 返回空列表（UI 转浏览模式）。
   - **性能（R16）**：预构建小写归一化索引（model id + provider 名小写 → 反查行），单次过滤 O(n)
   命中集合较小；openai 231 项规模下按需惰性排序。若事件处理器内过滤整体仍超阈值，经
   `ThreadPoolManager.run_async(TaskType.CPU, ...)` offload（实现时按 config-quality-perf 决策）。
3. 最近回记：`get_recent_selections() -> list[dict]` / `record_selection(provider_id, model_id)`。
   复用项目现有 app_state / settings 持久化（不引新存储介质；实施时选最小实现），格式
   有序去重 `[{"provider", "model"}]`，上限（如 8 条，取最近选用的）。
4. `get_model_info(provider_id, model_id)`：对外契约**不变**（返回 `{id, name, context}`）；
   context 解析= litellm `model_cost`（→ 项目 `llm_custom_model_contexts` 运行时覆盖）→ `0`。
   `token_budget.py` 消费点（`get_model_info(...).get("context", 0)`）不受影响。
5. 删除 `is_recommended_model` / `get_display_tag` 及全部调用点。

**D. 新共享组件 `ModelPicker`（`@ft.component` 声明式）+ VM**：
- 位于 `ui/components/model_picker.py`（与 config_panels 平级共享组件）+ 配套
  `ui/viewmodels/model_picker_view_model.py`。
- 视图结构：
  - 顶部 `TextField`（占位「搜索模型或供应商」）：`on_change → vm.update_model_query`；
    已选态回显 `供应商名 / 模型 id`，focus/点击进入选择态。
  - 下方结果面板（`ft.Column`，随 query 与打开态渲染）：
    - **空 query（浏览模式）**：`最近使用` 置顶分组 + `供应商分组折叠`（每组首行 = provider
      icon+name+模型数，可点展开 → 该 provider 的模型行）。
    - **有 query（搜索模式）**：`search_models` 拍平列表，每行
      `[icon] 供应商名 · 模型id (context)`；点击一行为**一键设定 provider + model**。
- 交互：跨供应商搜索命中即同时设定 provider+model；VM 同步保持 LLMConfigPanel 的 provider
  下拉（provider 仍是稳定锚点，承载 base_url/凭证/azure/custom 专用逻辑）。
- Azure（部署名，无目录）、custom（自由文本）不经 ModelPicker 模型清单（保留既有输入）。
- R16：litellm 目录读取在函数体内惰性 import；渲染层无 logger/print 副作用。

**E. 消费点改造（删字段波及面，逐一列全）**：
- `ui/components/config_panels/llm_config_panel.py`：删 `_build_model_options`（tag）/
  `_build_links_row`（URL / `get_display_tag`）；模型选择区改为 `<ModelPicker>`；删 `show_register_link`
  链接行；provider 下拉保留。
- `ui/viewmodels/llm_config_panel_view_model.py`：删 `models`/`is_recommended_model` 自动推荐默认模型
  逻辑（`update_provider`/`_load_config_to_state`）；`base_url` 仍 fallback `LLM_PROVIDERS.base_url`；
  模型值改由 `<ModelPicker>` 回写；当前已存 model 若不在新目录中仍保留为值（不回退、不误删）。
- `ui/components/config_panels/failover_config_panel.py` + `failover_config_panel_view_model.py`：
  `ProviderCredentialDialog` 模型选择改用 `<ModelPicker>`（删 `_build_model_options`/URL 行/`get_display_tag`）。
- `services/ai_service/token_budget.py`：消费 `get_model_info` 契约，**不改**。
- 测试：`tests/unit/test_llm_providers.py`（重写 get_model_info context、删 tag 相关、新增 catalog/
  search/recent/cache）、`tests/unit/ui/components/config_panels/test_llm_config_panel.py`（删 tag 相关）、
  failover 相关测试、新增 ModelPicker VM 测试（R19）。

**F. 风险与边界**：
- **zhipu**：litellm 官方目录键 `zai`（review-pr1073 A1）→ 可枚举 16 个官方 GLM 模型并有计价；
  若未来 litellm 升级致目录键调整，回落「无目录供应商自定义输入兜底」（§3.1c-F 通用条款）。
- **azure**：部署制，不枚举 litellm 目录，保留原有部署名输入。
- **custom**：自由文本，保留。
- **provider 元数据函数**（`get_provider_icon*`/`get_provider_by_id`/`get_all_providers`/
  `get_providers_by_category`）保持签名一致。
- R16 惰性 import + 契约 5 白名单；R19 配套测试；不擅自改 pyproject 依赖（litellm 已依赖）。

> 展示层仅保留供应商识别元数据（icon/name/key_prefix/custom/azure/分类）；**无 URL、无推荐标签**。
> 模型型号/context 权威交给 litellm；**UI 枚举(litellm_catalog_key)与运行路由(litellm_prefix)解耦**；
> zhipu 等无目录供应商诚实降级到自定义输入——项目不替用户做商业判断、不伪造清单。

### 3.1c-review 对抗性检视修正（2026-09-21 采纳）

> 立项前对 §3.1c 的对抗性检视结论为 REQUEST_CHANGES。以下为采纳的修正，覆盖阻断项与关键建议项，
> 均已并入 §A-E 的实施意涵，实现时按此执行。

1. **计价链对齐（阻断，AI-01 主目标）**：运行时计价 `_resolve_effective_model` 按 provider id 拼
   `<provider>/<model>`（如 `qwen/qwen-max`），`litellm.cost_per_token` 需对该前缀剥匹配成功才会计价。
   **实现第一步**先跑探针 `litellm.cost_per_token("qwen/<id>", prompt_tokens=, completion_tokens=)`
   确认前缀剥匹配行为：
   - 若剥匹配成功 → 计价正常，无需处理。
   - 若抛异常/不匹配 → 为 dashscope 系(qwen)显式提供「计价键映射」：以 `litellm_catalog_key`(=dashscope)
     覆盖计价 effective_model 前缀为 `dashscope/<id>`，再交 `cost_per_token`；映射完成后回归锁定
     qwen 可计价（配合 AI-01 的 unpriced 诚实兜底，若仍不可计价则该供应商归属 unpriced 提示，
     但**不得**在未探测时默认"覆盖面≥现状"——该断言对 qwen 不成立）。
   即：`litellm_catalog_key` 不仅用于 UI 枚举，也作为**计价回溯键**，统一解析到 litellm 官方 key。
2. **`_check_reasoning_support` 兜底删除（阻断）**：litellm_client.py 目前遍历
   `LLM_PROVIDERS[provider]["models"]` 的 tag 做 reasoning 兜底；删 models/tag 后该分支恒 False 且会
   静默丢失 reasoning 流。**删除该 LLM_PROVIDERS 兜底分支**（litellm 已加载时本就优先 `supports_*`），
   并确认调用方对不可判定 reasoning 的既有回退行为。E 节消费点清单补充该文件。
3. **custom 历史判定改用选中态**：`llm_config_panel_view_model._build_custom_models_update` 现用
   `.models` 判定是否记入 custom 历史；删 models 后改为「该 model 是否来自 ModelPicker 已选清单」判定，
   避免目录模型被误记入 custom_model 历史。
4. **`refresh_models` 去留**：现 httpx 拉 `/models` 与新 litellm 目录海报选重叠 → **移除 refresh 按钮/命令**
   （目录即权威源，无需二次拉取），`MODELS_API_COMPATIBLE`/`_REFRESH_TIMEOUT` 相关一并清理。
5. **failover 对话框供应商同步**：`failover_config_panel_view_model` 以 `f"{dialog_provider}/{model}"`
   拼条目；ModelPicker 跨供应商搜索命中时**必须同步 dialog_provider = 命中结果 provider**，并以结果
   provider 为准写回条目，避免条目前缀错位（`split("/")[0]`）与 credential 归属错误。
6. **R16 首次载入 offload 导入本身**：litellm 惰性 import 单次可达十秒级。目录首次构建（
   `get_litellm_models_by_provider` 首次调用）在 VM 命令内包 `ThreadPoolManager.run_async(TaskType.CPU/IO)`
   offload，避免首次击键阻塞主循环；其结果带加载态（is_loading）渲染，完成后刷新。
7. **context 缺失呈现（R21）**：context=0 代表"litellm 未声明/无法计量"，**UI 搜索/浏览行隐藏
   `(context)` 后缀**，不得显示 0 误导；tooltip/详情可给"未知"语义。token_budget 对 0 回退既有 128k 逻辑不变。
8. **字段命名核对**：文档"留 azure 字段"实为 `azure_config`（`custom: True` 同理）；azures 判定以
   `provider=="azure"`、`azure_config` 校验为准，删 models 不影响该判定。
9. **`litellm_catalog_key` 缺省回退改为 provider_id（雷区）**：不得回退 `litellm_prefix`（openai 撞车）。
   未显式配置 catalog_key 的 provider 回退其自身 provider_id；并对"该项目支持的 provider 但未配置
   catalog_key 且非 azure/custom"做启动期断言，漏配即报错（防漏配混入 GPT 目录）。
10. **类型红线**：`utils` 引 litellm 不被 import-linter 拦，但须遵循 R3 对 untyped 依赖加
    `# type: ignore[import-untyped]  # 原因`；"契约5 白名单登记"非必需，以 R3 为准。

### 3.2 累加侧：共享累加辅助方法（修复批量 + 重试两处同类隐患）

`strategies/ai_mixin.py`：

1. 新增静态方法 `_accumulate_usage(ai_usage_cluster: dict, res: dict) -> None`，统一累加分枝：
   - `usage` 存在时无条件 `calls++`、`tokens += total_tokens`；
   - `cost` 为 `int/float`（含 0.0，即 **`cost is not None`**）→ `cost_cny += cost`；
     —— 免费模型 cost==0.0 走成本记账归零，不误计 unpriced（对齐 litellm 语义，防骚扰）。
   - `cost` 为 `None`（不可计量，R21）→ `unpriced_calls += 1`、`unpriced_tokens += total_tokens`，
     注释标注 SEC/R21（不可计价必须计数，否则「不可计量」被呈现为「零成本」）。
2. `ai_usage_cluster` 初始化（L778）扩展：
   ```python
   ai_usage_cluster = {"calls": 0, "tokens": 0, "cost_cny": 0.0, "unpriced_calls": 0, "unpriced_tokens": 0}
   ```
3. **批量路径** `run_ai_analysis`（L850-859）：内联累加分枝替换为 `self._accumulate_usage(ai_usage_cluster, res)`。
4. **重试路径** `retry_single`（L1104-1118）：**修正漏记账**——构造临时 cluster，
   `self._accumulate_usage(cluster, res)` 后经扩展的 `_track_cost` 一并持久化成本与不可计价量。
   （检视轮 2 阻断项：重试路径原只记 cost>0、从不记账 unpriced，会导致月度不可计价量被低估、
   保守提示漏触发。）
5. 批次末（L964-971）：`_ai_usage_summary` 增加 `unpriced_calls`/`unpriced_tokens`；
   `_track_cost` 同时持久化成本与不可计价量。

### 3.3 持久化侧：_track_cost 改造

`ai_mixin._track_cost(cost_cny, unpriced_calls=0, unpriced_tokens=0)`：
- `cost_cny is not None`（含 0.0 免费）→ `AIUsageTracker().add_cost_cny(...)`。
- `unpriced_calls > 0` → `AIUsageTracker().add_unpriced(calls, tokens)`。
- 不可计价量即使 cost 为 0 也会持久化（修正原「0 元不写库」导致的记账缺失）。

### 3.4 预算护栏（保守：提示确认）

`_ai_budget_exhausted` + 前置检查（`_preflight_cloud_call`）：

- 新增判定 `_should_prompt_unpriced()`：`预算已设置（ai_cost_limit_cny>0）` 且
  `本月存在不可计价调用（get_month_unpriced().calls > 0）`。
- 当 `_ai_budget_exhausted()` 为假但 `_should_prompt_unpriced()` 为真时，前置返回新 blocker key
  `ai_budget_unpriced_prompt`（保守提示），不再静默放行。
- **确认作用域 = 进程生命周期（一次）**：`AIStrategyMixin` 增加类属性
  `_ai_unpriced_acknowledged = False`。用户确认后置 True，**本进程内后续所有批次/重试不再重复弹**
  （消解检视轮 2「每批重弹」UX 摩擦）。重启应用后重置（每月至少校验/提示一次，符合"保守"）。
- 批量与重试路径共用 `_preflight_cloud_call`，确认语义一致。
- 本地模型已确证不进入云路径（`run_ai_analysis`/`retry_single` 均先 `is_cloud_available()`），
  不会因「无法计价」误伤本地/免费模型；免费模型（cost==0.0）也因 3.2 的 `cost is not None`
  判定不进入 unpriced。

### 3.5 UI 诚实展示

- `ui/viewmodels/ai_stream_mixin.py`：`_ai_usage_summary` 元组扩展为
  `(calls, tokens, cost_cny, unpriced_calls, unpriced_tokens)`。
- `ui/views/screener_view.py` 批次汇总行：当 `unpriced_calls > 0` 时追加
  「（另有 N 次调用无法计价，约 M tokens）」。
- `ui/viewmodels/ai_brain_settings_view_model.py`：新增 `month_unpriced` 状态（读
  `get_month_unpriced`），UI 设置页本月累计文案在存在不可计价量时追加说明。
- `ui/views/settings_tabs/ai_brain_tab.py`：渲染不可计价说明。
- i18n：新增英文/简体中文字符串（设置页 + 批次汇总 + 保守提示确认框）。

## 四、测试（配套，R19）

- `services/ai_service` 单测：
  - `AIUsageTracker.get_month_unpriced/add_unpriced` 读写与 no-op 行为（engine 未注入）。
  - **并发不丢数**：对 `add_unpriced` 多次累加断言最终值为各 delta 之和（对应检视轮 1 覆盖缺陷）。
  - `_accumulate_usage` 分枝：cost 非 None（含 0.0）→ cost_cny；cost=None → unpriced_calls/tokens
    递增；usage 缺失 → 完全不计。
- `strategies` 单测：
  - **核心断言（报告 AI-01 建议 5）**：显式 mock `res={"cost": None, "usage": {"total_tokens": N},
    "ai_status": "analyzed"}` 跑一次分析后，`_ai_usage_summary.unpriced_calls > 0`、
    `cost_cny == 0`、`calls/tokens` 正常。
    （检视轮 2 修正：LLM client 是 mock 的，必须显式传 `cost=None` 而非仅「用非 DeepSeek 模型」，
    否则不会触发 unpriced 分支、测试假绿。）
  - **免费模型不误计**：mock `res={"cost": 0.0, ...}` → unpriced_calls 不增、cost_cny==0（3.2 判定回归）。
  - `retry_single` 对不可计价调用同样累计 unpriced（检视轮 2 缺陷回归）。
  - `_should_prompt_unpriced`：设预算+本月有不可计价 → 真；无预算 / 无不可计价 → 假。
  - `_ai_unpriced_acknowledged` 进程级确认：确认后重复调用不返回 `ai_budget_unpriced_prompt`。
- UI 单测：`ai_brain_settings_view_model` 加载不可计价状态、`ai_stream_mixin` 元组 3→5 展开后
  解包路径正确（防 ValueError）。
- `pricing` 单测（3.1b）：
  - **全 litellm 计价**：mock `litellm.cost_per_token` 返回 `(in,out)` → 断言 `estimate_cost` 返回
    `(in+out)*汇率` 且四舍五入 4 位；**删 MODEL_PRICING**，不再有手写公式分支。
  - **免费模型**：mock `cost_per_token` 返回 `(0.0,0.0)` → `estimate_cost` 返回 `0.0`（非 None）。
  - **不可计价兜底**：mock `cost_per_token` 抛异常 → `estimate_cost` 返回 `None`（R21 诚实兜底）；
    负 token → `None`。
  - **litellm 升级韧性**：`model_cost`/`models_by_provider` 用 `dict.get` 防御，字段缺失不抛
    KeyError（结构断言）。
  - **汇率 `# NOTE(lazy)`**：断言常量存在且含三要素描述（结构校验）。
- `llm_providers` 单测（3.1c）：
  - 合并规则：litellm 官方目录作权威源；某 provider 目录为空时回退静态
    `LLM_PROVIDERS[provider].models`。
  - `get_model_info` context 优先级（litellm > 项目 > 0）与对外契约不变。
  - `get_litellm_models_by_provider` 只投影项目支持的 provider_id；惰性 import 不破坏顶层 import。
  - **权威可计价**：项目支持的每供应商至少投影出一个可被 `cost_per_token` 计价的官方模型（防升级回归）。

## 五、验证

- `ruff check .` / `ruff format --check .` / `pyright`。
- `pre-commit run --all-files`（含 redline-check：新代码不得引入 R21 伪装；AIUsageTracker 新增
  AppState key 非表列、不触发 R12）。
- `python -m pytest tests/unit/ -v -m "not slow"`（相关子集的完整回归）。
- 最小覆盖：新增/修改文件满足 `scripts/check_per_file_coverage.py` 阈值。

## 六、风险与边界

- 不可计价计数为「有 usage 但 cost 为 None（不可计量）」；免费模型（cost==0.0）走成本记账归零，
  不误计 unpriced（3.2 用 `cost is not None` 判定，对齐 litellm 语义）；本地模型不进入云路径，不受影响。
- **模型对齐影响（3.1c）**：用户可见模型 id 全面改为 litellm 官方 key，项目静态 `LLM_PROVIDERS`
  原自定义型号不再作为权威清单。**存量用户已配置的旧自定义模型 id 将无法计价**（回退 unpriced 诚实
  展示），UI 供应商/模型下拉来源变化需回归既有显示（icon/tag 由项目层提供，不影响）。
- **计价回归风险（3.1b）**：删除 `MODEL_PRICING` 后默认展示模型必须由 litellm 官方 key 承载价格；
  若某供应商官方 key 缺价则回退 unpriced（诚实兜底，非伪装零成本）。已用单测锁定每供应商至少一个
  可计价官方模型。
- **litellm 版本升级风险**：pyproject 现为 `litellm>=1.101.0`（非 `==` 锁定），
  `cost_per_token`/`models_by_provider`/`model_cost` 语义随升级可能变化；本方案仅依赖其稳定公开
  语义、对 `model_cost` 取值经 `dict.get` 防御，落地与未来依赖升级时须回归第四节相关测试。
  实施时**不擅自改 pyproject**（依赖调整走独立流程）。
- failover 到备用 provider 的计费失真（报告提及）被统一计入 unpriced，账目不再静默失真。
- 保守确认的「进程级一次」为本次实现粒度；跨重启的按月持久化 ack 为可后续增强项（YAGNI）。