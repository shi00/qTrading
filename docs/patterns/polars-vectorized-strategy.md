# Polars 向量化策略基类

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.2（数据质量门控强制）；实现模板见本节。

继承 `PolarsBaseStrategy` 使用 Polars LazyFrame 进行高性能向量化计算。
`PolarsBaseStrategy` 同时继承了 `AIStrategyMixin`，Polars 过滤后自动进入 AI 分析阶段（可通过 `enable_ai_analysis = False` 关闭）：

```python
import polars as pl

from data.persistence.quality_gate import QualityTier
from strategies.base_strategy import register_strategy
from strategies.polars_base import PolarsBaseStrategy
from strategies.utils import StrategyContext


@register_strategy("my_polars_strategy")
class MyPolarsStrategy(PolarsBaseStrategy):
    # 注：Polars 子类用类属性覆盖默认质量等级（required_quality_tier），不在方法上加 @require_quality 装饰器
    required_quality_tier = QualityTier.SILVER

    def __init__(self):
        super().__init__(name_key="strategy_my_polars", desc_key="strategy_my_polars_desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: StrategyContext) -> pl.LazyFrame:
        return lf.filter(pl.col("pct_chg") > 5.0)
```

> 注：上述类属性模式适用于 `PolarsBaseStrategy` 子类。非 `PolarsBaseStrategy` 子类（如 `OversoldStrategy` 继承 `BaseStrategy` + `AIStrategyMixin`）可使用 `@require_quality` 装饰器。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」；把模板落成临时 `.py` 文件跑 `ruff check` + `pyright`，确认可 import 且 `@register_strategy` 注册生效（R14）。

- 模板含 `@register_strategy("...")` 装饰器与 `__init__` 调 `super().__init__(name_key, desc_key)`（可 import、不触发 R14）；
- Polars 子类数据质量门控用类属性 `required_quality_tier` 覆盖默认等级（§3.2），不在方法上挂 `@require_quality`；
- 门禁：`redline-check`（R14）、`ruff`、相关单测通过。
