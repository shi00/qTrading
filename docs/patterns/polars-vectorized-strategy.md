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

## 库边界约定

pandas 与 Polars 在项目中有明确的职责划分（对应 CLAUDE.md §4.1 分层 / 技术栈：Flet + Polars + asyncio）：

- **pandas 是跨层数据契约**：`data/ → services/ → ui/` 的经手数据以 `pd.DataFrame` 承载。DAO 读取路径统一产出 pandas（见 `base_dao.py` 的 `_build_df_normalized`，用 `pd.DataFrame(rows, columns=cols)` 构造并归一化 Decimal 列）。
- **Polars 是计算内核内部实现**：`PolarsBaseStrategy` 在 `_filter_logic` 内以 `pl.LazyFrame` 做向量化计算，属于策略计算内核，不直接对外暴露。

**转换（pandas ↔ polars）只允许发生在计算内核的出入口**：
- 入口：由 DAO 产出的 pandas 转入 polars 计算，仅在 `_filter_logic` 开头的接数处；
- 出口：计算完成转回 pandas（`.to_pandas()` / `pl.DataFrame.to_pandas()`）交给后续服务/UI。

**禁止在 Polars 计算内核之间用 pandas 中转**：一旦进入 polars 计算，不应为中间的某一步转回 pandas 处理、再转回 polars 继续算。pandas 透传会破坏向量化计算的单一数据形态（触发不必要的隐式转换与复制），并可能因 LazyFrame 求值时机造成`collect` 语义混乱。

## 指标语义单一事实源

同一技术指标若同时存在 pandas 与 Polars 实现，**以 Polars 表达式为唯一语义正本**，pandas 入口必须是**薄委托**（仅做格式转换与返回），不得是独立算法实现。

- **正本**：Polars 表达式工厂（如 `utils/technical_analysis.py` 的 `get_rsi_expr` / `get_macd_expr` / `get_kdj_expr`），是逻辑的唯一来源。
- **pandas 入口**：必须内部调用 Polars 表达式工厂、计算后转回 pandas。模式参照 `TechnicalAnalysis.calculate_rsi_pandas`——它不重复实现 RSI，而是调用 `get_rsi_expr` 计算后转回，预热期语义与 Polars 版本一致（D3-2/SC-05 合一）。

**禁止新增独立的 pandas 指标实现**：任何新的技术指标，只实现一份 Polars 表达式正本；若下游仍需 pandas 结果，提供薄委托入口，不得另行实现一套算法。同一指标双实现导致的语义漂移（如预热期、边界值差异）已由 `tests/unit/test_technical_analysis_equivalence.py` 专项守护，新增指标应同时补齐该等价性测试。

## 明令禁用：`pl.read_database` / `pl.read_database_uri`

**禁止在本项目代码中使用 Polars 的数据库读取 API**（`polars.read_database` / `polars.read_database_uri`，及 `pl.read_database` / `pl.read_database_uri` 等形态），无论数据源直连还是经 SQLAlchemy 连接。理由如下：

1. **事件循环污染（事件循环级风险，对应 R11 loop-local 约束）**：`read_database` + SQLAlchemy async 连接时，内部 `_run_async` 在存在 running loop 的场景下会对事件循环打**进程全局**猴补丁（nest_asyncio 风格，替换 `run_forever` / `run_until_complete` / `_run_once` / `_check_running` 四方法），污染 Flet UI 事件循环本身，波及 TaskManager / SchedulerService / litellm 等全部异步组件，与项目「把同步工作搬出事件循环（ThreadPoolManager）」的架构方向（CLAUDE.md §3.2）直接相反。
2. **连接池与并发上限失效**：`read_database_uri`（ConnectorX / ADBC）绕过 SQLAlchemy 连接池，`pool_size` / `max_overflow` / `pool_pre_ping` / `pool_recycle` 全部失效，且与 `base_dao.py` 的信号量并发上限脱钩，有打爆 PostgreSQL `max_connections` 的风险；同时 connectorx / adbc 依赖未安装（见 `pyproject.toml`）。
3. **项目读取路径不需要它们**：DAO 已自行取出 rows/cols，`_build_df_normalized` 用 `pd.DataFrame(rows, columns=cols)` 构造。若某处需要 Polars 结果，只需等价地 `pl.DataFrame(rows, schema=cols)` 构造，无需这些 DB 读取 API。

> 数据始终从项目 DAO 层注入策略；计算内核只负责在已注入的 DataFrame 上计算，不自行打开数据库连接。如需读取需补充的行情/快照数据，走 data 层同步/DAO 流程，不得在策略内核内直连数据库。

## 行序保证与流式语义（新增 lazy 管线强制约定）

Polars 2.x 的流式默认与语义严格化，把一部分问题从**抛异常**变成**静默的结果差异**：行序只在显式声明处才被保证（如 `join(..., maintain_order="left")`、`group_by(..., maintain_order=True)`），其余算子不承诺输出顺序。`PolarsBaseStrategy` 拿到 `_filter_logic` 返回的 LazyFrame 后直接 `collect().to_pandas()` 交给服务/UI，位次漂移不会报错，只会让 `head(n)` 截断、展示与导出顺序整体错位。

**强制要求（新增/改造 lazy 管线时逐点核对）**：

1. **末步显式 sort**：`_filter_logic` 及其他 lazy 管线最后一步必须显式 `.sort(...)`，不得以 `filter` / `join` / `group_by().agg()` / `select` 收尾而依赖输入行序。
2. **排序键可区分行**：sort 键须能唯一区分行（并列时追加唯一键，如 `["score", "ts_code"]`）；并列键之间的次序无保证，不得依赖。
3. **顺序可观测处按需 `maintain_order`**：展示、导出、Golden / 单测断言等顺序可观测路径，在 `join` / `group_by` 处按需显式声明 `maintain_order`；不全局开启（会使流式执行退化为有序收集，有性能代价）。
4. **不依赖输入行序**：`pl.from_pandas(df).lazy()` 的输入顺序不是契约；`head` / `slice` / `unique` 等结果取决于位置的算子必须置于明确 sort 之后。

**反例（判为不合规）**：

- 管线以 `lf.filter(...)` 收尾即返回：当前结果无异常，但上游一旦加入 `join` / `group_by`，返回顺序即静默漂移。
- `lf.join(dim, on="ts_code", how="left").sort("score", descending=True)`：仅按 `score` 排序，并列分之间的行序不可复现，Golden 快照 / 单测会偶发不一致。
- `lf.group_by("industry").agg(...).collect()` 直接用于导出，未声明 `maintain_order=True`。
- 单测用 `assert_frame_equal(result, expected)` 比较未显式 sort 的管线输出（该断言默认比较行序）。

**正例**：`strategies/fundamental.py` / `strategies/market.py` 的 `_filter_logic` 筛选管线以 `.sort("dv_ttm", descending=True)` / `.sort("pct_chg", descending=True)` 收尾，新增策略沿用该形态。

> 背景与升级触发条件见技术债 [P3-Polars-Streaming-Order-Semantics](../debt/known-technical-debt.md)；Polars 大版本升级前须按该条复核流式与行序语义。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」；把模板落成临时 `.py` 文件跑 `ruff check` + `pyright`，确认可 import 且 `@register_strategy` 注册生效（R14）。

- 模板含 `@register_strategy("...")` 装饰器与 `__init__` 调 `super().__init__(name_key, desc_key)`（可 import、不触发 R14）；
- Polars 子类数据质量门控用类属性 `required_quality_tier` 覆盖默认等级（§3.2），不在方法上挂 `@require_quality`；
- lazy 管线末步已显式 `.sort(...)`（排序键可区分行），顺序可观测路径已按需声明 `maintain_order`（见上文「行序保证与流式语义」）；
- 门禁：`redline-check`（R14）、`ruff`、相关单测通过。
