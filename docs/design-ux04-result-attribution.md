# UX-04 选股结果完整归因 — 设计方案（v2，已吸收两轮检视修订）

## 目标
满足 UN-03「用户能保留判断，需要知道系统是怎么判断的」——纯数学筛选策略提供结构化归因：每只通过的股票展示**每个筛选条件的实际值 vs 阈值**，以及**排序依据 + 候选池排名位置**。在股票详情弹窗新增「筛选条件归因」卡片。

## 设计取向（先决条件）
- **opt-in、零侵入**：`attribution_enabled` 默认 False，仅目标策略实现。未启用策略行为完全不变。
- **只讲「为什么被留下」**：归因只作用于筛选幸存行，因此每条条件在该行均通过。不展示「被排除的股票/失败条件」——UI 明确标注「本列表为已通过股票，以下为其通过条件」，避免用户误以为能看到排除原因。
- **不做跨量纲「最接近临界」**：不同条件量纲（PE 几十、股息率 %、PB 倍数）的临界距离不可比，标注会误导（二次检视 M2）。改为每条件独立展示实际值/阈值，不加跨条件标记。
- **归因不持久化、不入库**：仅存于单次筛选结果内存，无 schema 变更。

## 1. 策略层数据结构（strategy 内新增，不跨层）

```python
# strategies/attribution.py


@dataclass(frozen=True)
class FilterCondition:
    """单个筛选条件的展示化描述。仅在筛选通过的行上生成，故 is_passed 恒真，用运算符表达。"""

    label_key: str  # 列别名 i18n key（如 "column_dv_ttm"，见 §3 复用列别名）
    operator: str  # "gt" | "lt" | "between" | "geq" | "leq"
    threshold: float | tuple[float, float]  # 数据单位的阈值（金额类经 threshold_in_data_unit 换算）
    actual: float | None  # 该股票实际值（数据单位；NaN→None 归一）


@dataclass(frozen=True)
class RankAttribution:
    """排序归因。rank 口径恒为「在 strategy 输出的候选池（AI 截断前）内按 rank_field 排序」。"""

    field: str  # 排序列名
    label_key: str  # 排序字段 i18n key（View 复用）
    ascending: bool = False  # 排序方向（默认降序）
    value: float | None = None
    position: int | None = None  # 1-based，在 total 中排名
    total: int | None = None  # 候选池（AI 截断前）总数


@dataclass(frozen=True)
class FilterAttribution:
    conditions: tuple[FilterCondition, ...]
    rank: RankAttribution
```

序列化：结果 DataFrame 新增列 `_filter_attribution`，值为 `json.dumps(asdict(...))`。理由：伴随筛选结果同行流动，无需额外索引；`asdict` 保证可 round-trip。

## 2. BaseStrategy 钩子（strategies/base_strategy.py）

```python
class BaseStrategy:
    attribution_enabled: bool = False
    """True 表示子类实现 build_attribution，在选股详情弹窗展示筛选归因。"""

    def build_attribution(
        self,
        row: dict,  # pandas DataFrame 单行（collect().to_pandas() 之后，见 m1 修订）
        total_candidates: int,  # AI 截断前候选池总数（见 Ma1/B3 修订）
        params: dict,
    ) -> FilterAttribution | None:
        return None
```
- `row` 为 **pandas Series→dict**，与 `polars_base.py:102` 的 `to_pandas()` 数据流一致（二次检视 m1）。
- 金额/数量类条件，`build_attribution` 内必须用 `threshold_in_data_unit()` 把参数阈值换算到数据单位后再写入 `FilterCondition.threshold`（符合 R20）。

## 3. 执行流（strategies/polars_base.py）

关键修正（一次检视 Major#1 + 二次检视 B2/B3/Ma4）：**归因必须在 `run_ai_analysis` 之前、且覆盖所有分支（含 `enable_ai_analysis=False` 早退）生成**。

```python
def filter(self, context):
    # ... 现有 _convert_and_filter → candidates_df ...
    if candidates_df is None or candidates_df.empty:
        return pd.DataFrame()

    # UX-04：在 AI 分支前生成归因，total=AI 截断前真实候选池
    if self.attribution_enabled:
        total = len(candidates_df)
        candidates_df = await ThreadPoolManager().run_async(
            TaskType.CPU,
            lambda: _build_attributions(self, candidates_df, total, context),
        )

    if not self.enable_ai_analysis:
        return candidates_df  # 早退分支也已带归因（B2 修复）

    candidates_df = self._sort_for_ai(candidates_df)
    return await self.run_ai_analysis(candidates_df, context)


def _build_attributions(strategy, df, total, context):
    values = []
    for _, row in df.iterrows():
        attr = strategy.build_attribution(row.to_dict(), total, context)
        values.append(json.dumps(asdict(attr)) if attr else None)
    df["_filter_attribution"] = values
    return df
```
- 归因在 AI 排序/截断前完成，`rank.total` 为真实通过候选数（`ai_mixin.py` 的 `head(cap)` 不再影响语义）。
- `_filter_attribution` 列会随 `candidates_df` 流入 AI 路径并最终进入 `_full_results`；NaN 已归一为 None，不影响 `ScreenerRow.__eq__`。

## 4. 目标策略实现（5 个）

| 策略 | 条件列 | 排序 | AI? |
|------|-------|------|-----|
| `DividendStrategy` | `dv_ttm > dv_min` | dv_ttm desc | AI 启用 |
| `ValueStrategy` | pe_ttm∈(pe_min,pe_max), pb∈(0,pb_max), dv_ttm>dv_min | dv_ttm desc | AI |
| `GrowthStrategy` | or_yoy>rev, netprofit_yoy>profit, roe>roe_min | roe desc | AI |
| `CashFlowStrategy` | debt_to_assets<debt_max, roe>roe_min, … | 见源码 | AI |
| `VolumeBreakoutStrategy` | pct_chg∈(chg_min,chg_max), turnover_rate>turnover | pct_chg desc | **False（早退路径，验证 B2 修复）** |

每策略：
- 覆盖 `build_attribution`，从 params 读阈值 → `FilterCondition`，`threshold` 与列同单位（以上列均为 %/倍数，无需换算；金额策略若引入须经 `threshold_in_data_unit`）。
- **列别名复用**：`label_key` 复用现有 `get_column_alias(column)` 的 i18n key（如 `column_dv_ttm` 已存在，View 渲染「股息率(TTM)」），不再占用参数 label_key（避免「股息率下限=3.2」名实不符，二次检视 m3）。运算符符号 `≥/≤/∈` 用新增 `filter_op_*` i18n。
- **VolumeBreakout 动态阈值**：`_filter_logic` 在 `chg_min>=chg_max` 自动调整时，把生效值存入 `context["_attribution_thresholds"]`；`build_attribution` 优先读该生效值，与展示一致（二次检视 Ma4）。不改 params 本身。

## 5. VM 层（ui/viewmodels/pagination_sorting_mixin.py）

`_build_current_page_rows`：当 `df` 含 `_filter_attribution` 列时，对每行 `json.loads` → 放入 `values["_filter_attribution"]`（dict 结构）。列缺失 / 值 None / 非法 JSON 均安全跳过（不 raise）。`ScreenerRow.values` 是动态 map，无需改 dataclass。

## 6. View 层

- `_HIDDEN_COLS`（`ui/views/screener_view.py`）**新增 `_filter_attribution`**，避免 JSON 裸列上表（二次检视 B1）。
- `stock_detail_dialog.py`：当行数据含 `_filter_attribution` 且非 None → 在 AI 分析下方插入「筛选条件归因」卡片，渲染：
  ```
  筛选条件归因
  ⓘ 本列表为已通过筛选的股票，以下为其通过条件；排名按 {rank_label}({升/降}序) 在 {total} 只候选中计算
  股息率(TTM) = 3.2% ≥ 2.0% ✓
  PE(TTM) = 12.4 ∈ (5, 20) ✓
  PB = 1.3 ≤ 3.0 ✓
  排名：47 / 312（按股息率(TTM)，降序）
  ```
- 运算符 `= × ×` 用 i18n `filter_op_*` 渲染（中英齐备），数值经单元格式化（复用现有 `fmt_val`/`format` 逻辑）。
- 无障碍：✓ 为文本+颜色叠加，不依赖纯颜色。

## 7. i18n
新增（zh_CN + en_US 对称）：
- `filter_attribution_title`：筛选条件归因
- `filter_attribution_note`：本列表为已通过筛选的股票，以下为其通过条件
- `filter_attribution_rank`：排名：{position} / {total}（按 {field}，{order}）
- `filter_op_ge/g/le/l/between`：≥ / ≥ / ≤ / ≤ / ∈（或等文字等价）
- 排序方向 `filter_order_asc/desc`：升序/降序
其余列别名（`column_*`）与参数 label_key 均复用既有资源。

## 8. 架构/红线与边界
- R1：`strategies/attribution.py` 只依赖 dataclass/json，不导入 ui/services/app ✅
- R16：归因生成经 `ThreadPoolManager.run_async(TaskType.CPU)` ✅
- R11：frozen dataclass，无 asyncio 原语类属性 ✅
- R20：金额/数量类条件必须经 `threshold_in_data_unit` 换算（文档强制 + 目标策略当前同单位）✅
- MVVM：VM 只 `json.loads`（不调 I18n.get），View 渲染期翻译 ✅；View 纯声明式、不持状态 ✅
- R19：业务逻辑变更配套单测（见 §9）

## 9. DoD（可验证）
1. `strategies/attribution.py`：`FilterCondition`/`RankAttribution`/`FilterAttribution`（frozen dataclass）
2. `base_strategy.py`：`attribution_enabled` + `build_attribution` 默认实现
3. `polars_base.py`：归因在 AI 分支前生成，`total`=AI 截断前真实候选池；覆盖早退路径
4. 5 个策略实现 `build_attribution`
5. `stock_detail_dialog.py` 渲染归因卡片；`_HIDDEN_COLS` 含 `_filter_attribution`
6. `pagination_sorting_mixin.py` 反序列化，列缺失/None/非法 JSON 安全跳过
7. i18n 中英新增项齐全
8. 单测：`strategies/attribution.py` 序列化 round-trip；`DividendStrategy`/`ValueStrategy`/`VolumeBreakoutStrategy` 的 `build_attribution` 与 `_filter_logic` 阈值一致性；`_build_current_page_rows` 解析与容错；弹窗渲染（含 HISTORY 无列不渲染守卫）；rank position 在 AI 截断下仍正确
9. pre-commit 全门禁通过，现有测试不降级

## 10. 后续增强（登记技术债，不阻塞本轮）
- `closest_to_critical` 跨量纲临界标记
- join/gating 复杂策略（NorthboundFlow / NorthboundHolding / Oversold 动态列）归因