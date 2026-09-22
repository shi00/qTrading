# 策略模式实现模板

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.1（R14 红线）、§3.2（强制要求）；实现模板见本节。

```python
import pandas as pd

from data.persistence.quality_gate import QualityTier, require_quality
from strategies.base_strategy import BaseStrategy, register_strategy
from strategies.utils import StrategyContext


@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):
    required_context_keys: tuple[str, ...] = ("screening_data",)
    required_tables: tuple[str, ...] = ("daily_quotes",)
    required_history_days = 60

    def __init__(self):
        super().__init__(name_key="strategy_my", desc_key="strategy_my_desc")

    @require_quality(QualityTier.SILVER)
    async def filter(self, context: StrategyContext) -> pd.DataFrame:
        # 策略逻辑：返回过滤后的 DataFrame（完整真实示例见 strategies/oversold_strategy.py）
        df = context.get("screening_data")
        if df is None:
            return pd.DataFrame()
        return df[df["pct_chg"] > 5.0]
```

> 注：数据质量门控默认挂 `@require_quality(QualityTier.SILVER)`（可叠加 `require_continuous_window=True` 做区间完整性门控，见 `oversold_strategy.py`）；**PolarsBaseStrategy 子类改用类属性 `required_quality_tier` 覆盖质量等级**，不在方法上挂装饰器（见 [polars-vectorized-strategy.md](./polars-vectorized-strategy.md)）。

- **策略入口**: `strategies/all_strategies.py` 通过导入触发 `@register_strategy`，由 `_STRATEGY_REGISTRY` 统一暴露。
- **策略 API**: 依赖声明 (`required_context_keys`/`required_tables`/`required_history_days`/`required_apis`)、动态参数 (`get_parameters()`)、动态描述 (`get_dynamic_description()`)、依赖检查 (`check_dependencies()`) — 详见 `strategies/base_strategy.py`。
- **新增策略流程**: 见 [标准开发工作流](../guides/how-to.md#3-新增一个策略)。

## 路由（必读 / 条件触发 / 完成判定）

> 本文件是被 [CLAUDE.md §1.8](../../CLAUDE.md#18-任务类型--必读文件-决策树) 路由的「新增/修改策略」canonical 入口。判断信息见下，具体步骤以 [docs/guides/how-to.md](../guides/how-to.md)「3. 新增一个策略」为准，本文件不复制步骤正文。

### 必读

新增或修改策略任务，至少阅读：

1. [CLAUDE.md](../../CLAUDE.md) §3.1 R14（未注册策略红线）与 §4.1（strategies 分层边界）
2. [docs/guides/how-to.md](../guides/how-to.md)「3. 新增一个策略」（流程正本）
3. 本文件上方的代码模板与 `strategies/base_strategy.py` 的 API 声明

### 条件触发

- 新增策略：按 [how-to.md](../guides/how-to.md#3-新增一个策略) 全流程，并在 `strategies/all_strategies.py` 的 `_import_all_strategies()` 导入以触发自动注册
- 向量化策略：继承 `PolarsBaseStrategy`，且必须用类属性 `required_quality_tier` 覆盖数据质量等级（CLAUDE.md §3.2）
- 访问 LLM：混入 `AIStrategyMixin`，Prompt 写入 `strategies/strategy_prompts.py`
- 动态参数 / 描述：实现 `get_parameters()` / `get_dynamic_description()` / `check_dependencies()`（见 `strategies/base_strategy.py`）
- 新增策略未用 `@register_strategy("key")` 装饰器 → 触发 R14（pre-commit `redline-check` 守护）
- 涉及金额/数量列（`north_money` / `net_amount` / `amount` / `total_mv` / `circ_mv` / `vol`）的数值比较：必须经 `threshold_in_data_unit()` 统一入口换算后再比较（`strategies/utils.py`）；单位声明见 `data/constants.py` 的 `HSGT_COLUMN_UNITS` / `TOP_LIST_COLUMN_UNITS`。违反触发 R20（见 [CLAUDE.md §3.1](../../CLAUDE.md#31--绝对禁止)）

### 完成判定

- 策略经 `@register_strategy` 注册，并在 `strategies/all_strategies.py` 的 `_import_all_strategies()` 触发
- 数据质量门控：普通策略挂 `@require_quality`；`PolarsBaseStrategy` 通过 `required_quality_tier` 属性覆盖默认等级（CLAUDE.md §3.2）
- i18n：`locales/` 补齐 `strategy_xxx` / `strategy_xxx_desc` 等 key
- 单测：`tests/unit/` 下覆盖（按 [docs/guides/testing.md](../guides/testing.md#模板-4策略单测polars-夹具--依赖声明)「模板 4：策略单测」模板编写）
- 门禁：`redline-check`（R14）、`pyright`、相关单测通过
- 最小验证命令：对照 [CONTRIBUTING.md](../../CONTRIBUTING.md)「变更类型 → 最小验证子集」该改动范围对应最小子集
