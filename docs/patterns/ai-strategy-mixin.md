# AI 策略混入

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.1 R9/R10（敏感信息与硬编码密钥红线）；实现模板见本节。

`strategies/ai_mixin.py` 的 `AIStrategyMixin` 类提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：

- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量、Prompt 安全防护 (`utils/prompt_guard.py`)
- Prompt 模板集中在 `strategies/strategy_prompts.py`，响应校验在 `strategies/prompt_validator.py`

### 最小可提交骨架

混入类 `AIStrategyMixin`（定义于 `strategies/ai_mixin.py`）通过继承挂到策略类上，策略只需覆盖少量钩子即可获得 AI 增强能力。最小骨架：

```python
import pandas as pd

from data.persistence.quality_gate import QualityTier, require_quality
from strategies.base_strategy import BaseStrategy, register_strategy
from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext
from strategies.utils import StrategyContext


@register_strategy("my_ai_strategy")
class MyAIStrategy(BaseStrategy, AIStrategyMixin):
    required_context_keys: tuple[str, ...] = ("screening_data",)
    required_tables: tuple[str, ...] = ("daily_quotes",)

    # 类属性（与 strategies/ai_mixin.py 中 AIStrategyMixin 定义保持一致）
    enable_ai_analysis: bool = True  # False 跳过 Phase 2 AI 分析
    ai_risk_check_in_prompt: bool = False  # True 时承载定性风险检查职责

    @require_quality(QualityTier.SILVER, require_continuous_window=True)
    async def filter(self, context: StrategyContext) -> pd.DataFrame:
        # Phase 1: 数学筛选出候选集 candidates_df
        candidates_df = self._math_filter(context)  # 策略自身的数学筛选
        if candidates_df.empty:
            return pd.DataFrame()
        # Phase 2: AI 增强（真实完整示例见 strategies/oversold_strategy.py::OversoldStrategy）
        return await self.run_ai_analysis(candidates_df, context)

    def get_ai_context(self, row: dict) -> str:
        """Override: 注入策略特定上下文，告诉 LLM 该股为何被选中（防 context vacuum）。"""
        return ""

    async def _prefetch_strategy_specific(
        self, candidates_df: pd.DataFrame, context: dict, prefetched: PreFetchedContext
    ) -> PreFetchedContext:
        """Override(可选): 预取策略特定数据，返回值需原样/增强返回。"""
        return prefetched
```

需要 override 的钩子清单（均见 `strategies/ai_mixin.py::AIStrategyMixin`）：

| 钩子 | 默认 | 说明 |
|------|------|------|
| `get_ai_context(row) -> str` | 返回 `""` | **必覆**：单股策略上下文，注入后进入 LLM prompt |
| `_prefetch_strategy_specific(...) -> PreFetchedContext` | 原样返回 | 可选：批量预取策略特定数据 |
| `should_include_global_context() -> bool` | `True` | 是否注入共享大盘/全局上下文 |
| `should_include_learning_context() -> bool` | `True` | 是否注入跨运行历史学习上下文 |
| `_sort_for_ai(df) -> df` | 保持输入序 | 可选：AI 分析前按业务意图排序截断 |
| `register_context_builder(name, fn)` | — | 在 `__init__` 注册自定义上下文块构造器 |

进入 AI 分析统一经 `await self.run_ai_analysis(candidates_df, context)`（候选截断、云端可用性、外发确认、月度预算护栏、缺失语义 R21 均由混入内部处理），单股重试经 `retry_single`。双模式（云端 LiteLLM / 本地 llama-cpp-python）由 `AIService` 统一封装，策略无需感知。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」；AI 策略混入逻辑改动后须 `redline-check` + 相关单测 + `python scripts/check_docs_consistency.py`。

- 混入类结构与 `strategies/ai_mixin.py` 契约一致（类属性 / 需 override 的钩子），可 import、不新增无谓抽象；
- AI 执行权遵守 [ADR-0008](../adr/0008-no-ai-execution.md)：输出仅评分/展示，无交易/工具/代码执行路径；
- AI 输出缺失语义沿链路用 `None`/哨兵表达（R21），不填充 `0`/`50` 等业务合法值；
- 外发经 `EgressAudit` 审计、prompt 经 `utils/prompt_guard.py` 净化、日志经 `DataSanitizer` 脱敏（R9）；
- 门禁：`redline-check`、`ruff`、相关单测通过。
