# ADR-0011: 手写 failover 循环迁移至 litellm.Router（L4）

> Status: Accepted
> Date: 2026-09-23
> Owner: 架构维护者
> Supersedes: 无（重构既有实现，无旧 ADR 对应）

## Context

`_chat_completion_with_failover`（services/ai_service/litellm_client.py）原为手写
126 行 failover 循环：依次尝试 primary + fallbacks，按 `classify_error` 的
`should_retry` 判定切卡或直抛。该实现存在检视报告 L4 列出的问题：

1. **重试/冷却语义缺位**：同供应商瞬时错误（429/5xx/超时）只有 fallback 时才会
   切下一家，没有原地重试与失败冷却——瞬时抖动直接消耗备选供应商配额。
2. **健康路由缺失**：无法感知供应商连续失败，请求会反复打到刚失败的供应商。
3. **流式 reasoning 静态探测**：入口以 `supports_reasoning(primary)` 静态判定后
   全程按此解析流式 chunk，fallback 模型与 primary 推理能力不同时 reasoning 内容
   被错误丢弃。
4. **双层审计缺口**：审计只在入口按 primary 意图记录，Router fallback 后的真实
   目的地不可见（SEC-03 语义缺口）。

## Decision

将手写循环整体替换为 `litellm.Router`（1.102.x 实测验证参数契约），语义分层：
同供应商瞬时重试/冷却/健康路由/跨供应商切换交由 Router 承担，本层只保留
入口门控、SEC-01/03 审计、流式解析与错误收敛。

### 1. Router 惰性构造（`_ensure_router_loaded`）

- 与 `_ensure_litellm_loaded` 同范式：模块级 `_litellm_router` /
  `_router_import_attempted` 哨兵，构造失败优雅降级（`AIServiceUnavailableError`
  承载），不向上传播配置缺陷。
- `model_list` 复用 `build_router_model_list`（M1）单点解析：跨供应商 fallback 缺
  专属 key 经 D8-1 校验后**排除**（不连坐 primary，不静默回退全局 key）。
- fallbacks / context_window_fallbacks 以 list-of-dicts 形式（litellm
  `validate_fallbacks` 逐项要求 dict），且只登记 model_list 中实际存在的
  model_name（excluded 项不可引用）。
- `RetryPolicy(AuthenticationErrorRetries=0)`：401 契约保险——源码默认已在
  `should_retry_this_error` 因同组部署数=1 直接 raise（不重试不 fallback），显式
  置 0 防版本演进悄悄改变语义。
- `reload_config` 失效已构造 Router，下次调用重建（配置热生效）。

### 2. 调用（`_router_failover`）

- 入口门控链：cloud 可用性 → primary 存在 → litellm 加载 → Router 构造可用。
  任一不满足显式失败，绝不静默回退非 Router 路径。
- SEC-01 外发知情确认门控（purpose="news" 未确认 → AIPolicyNotAcknowledgedError）。
- SEC-03 双层审计：入口按 primary 意图记录一次；响应后按 `result["model"]`（流式
  经 chunk.model 动态收集）与 primary 不一致时补记真实目的地。
- JSON 解析与 AI-01 计量元数据回填（与 `_chat_completion` 同源语义）。
- 错误收敛：非瞬态（Auth/ContentPolicy 等）保持原语义直抛原异常；瞬态经 Router
  内部重试+fallback 仍失败 → `AIServiceUnavailableError`（Tried 从配置构造）。

### 3. 流式解析（`_consume_router_stream`）

- reasoning_content 改为**动态字段检测**（每次 getattr），不依赖入口静态探测——
  fallback 模型推理能力不同时仍正确收 reasoning。
- chunk.model 动态收集实际生效模型（供补记审计与计价）。

## Consequences

**正向**
- 同供应商瞬时错误原地重试（num_retries=2，与原 LITELLM_MAX_RETRIES 对齐），
  失败冷却（cooldown_time=30s），健康路由避免请求打到冷却中的供应商。
- 流式 reasoning 跨供应商正确（动态检测）。
- 双层审计补齐真实目的地（SEC-03 增强）。
- 删除 126 行手写循环，语义由 litellm 维护（免自研漂移）。

**负向**
- **行为增强**：ContentPolicyViolationError 未配置专属 content_policy_fallbacks，
  Router 默认会尝试普通 fallback（旧循环直抛语义不再保留）——内容策略违规是
  提示词级问题，重试/切换无意义且浪费配额，但 Router 默认行为如此；如需恢复
  直抛须显式 `content_policy_fallbacks` 为空表或构造期拦截（登记为后续观察项）。
- **审计粒度变化**（对抗检视确认）：旧手写循环**每次尝试**（含失败的中间
  fallback）各记一条外发审计；Router 化后为**双层审计**——入口 primary 意图 +
  最终实际目的地（Router 内部中间供应商的失败尝试不再逐条可见）。外发计数
  语义从「尝试次数」变为「实际生效目的地」，SEC-03 面板展示按实际消费收敛
  （Router 内部失败尝试的真实外发是否发生由 litellm 决定，本层不可见）。
- **依赖面变化**：`_router_failover` 依赖 litellm.Router 构造参数契约
  （list-of-dicts fallbacks 等），litellm 升级时由 `_ensure_router_loaded` 的
  降级路径兜底（构造失败 → AIServiceUnavailableError，不崩溃）。
- 单测无法覆盖 Router 内部行为（conftest 以 MagicMock 替换 sys.modules["litellm"]），
  语义正确性由真实构造验证 + 源码契约测试保证。
- **观察项**：非流式 `response.model` / 流式 `chunk.model` 的返回格式（litellm
  Router 是否保留池内 model_name 完整前缀）依赖 litellm 实现，未经真实调用
  验证；若返回缺前缀的供应商名，补记审计 destination 格式可能漂移（SEC-03
  单点格式），登记为 litellm 升级回归观察项。

## Alternatives

1. **低成本局部修复手写循环**（补重试/冷却/健康路由）：等价于重新实现 Router 子集，
   自研漂移风险更高，且丢失 Router 生态测试覆盖，否决。
2. **保留旧循环 + 仅加健康状态表**：未解决流式 reasoning 跨供应商与双层审计，
   不完整，否决。
