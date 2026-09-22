# AI 策略混入

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.1 R9/R10（敏感信息与硬编码密钥红线）；实现模板见本节。

`strategies/ai_mixin.py` 的 `AIStrategyMixin` 类提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：

- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量、Prompt 安全防护 (`utils/prompt_guard.py`)
- Prompt 模板集中在 `strategies/strategy_prompts.py`，响应校验在 `strategies/prompt_validator.py`

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」；AI 策略混入逻辑改动后须 `redline-check` + 相关单测 + `python scripts/check_docs_consistency.py`。

- 混入类结构与 `strategies/ai_mixin.py` 契约一致（类属性 / 需 override 的钩子），可 import、不新增无谓抽象；
- AI 执行权遵守 [ADR-0008](../adr/0008-no-ai-execution.md)：输出仅评分/展示，无交易/工具/代码执行路径；
- AI 输出缺失语义沿链路用 `None`/哨兵表达（R21），不填充 `0`/`50` 等业务合法值；
- 外发经 `EgressAudit` 审计、prompt 经 `utils/prompt_guard.py` 净化、日志经 `DataSanitizer` 脱敏（R9）；
- 门禁：`redline-check`、`ruff`、相关单测通过。
