# UX-12（P2-05）回测图表语境修复方案

> 分支: `fix/ux-12-backtest-chart` | worktree: `.worktrees/fix-ux-12-backtest-chart`
> 状态: 方案 v1 — 待 3 轮检视
> 验收依据: `logs/UX检视报告.md` P2-05 + 验收清单第 7 条「回测图表有日期、单位、图例、基准与文本摘要」

## 1. 背景与目标

`logs/UX检视报告.md` P2-05 指出：
- 净值曲线和 IC 图的横轴使用序号，未展示日期。
- 图表没有轴标题、单位、图例、数据来源、时间范围和可访问文本摘要。
- 净值曲线未在同图突出基准，用户难以快速解释「为什么好/坏」。

**验收标准**（P2-05 建议 + 验收清单第 7 条）：
回测图表具备 ① 日期横轴 ② 单位/轴标题 ③ 图例 ④ 基准对比 ⑤ 可复制的文本摘要；并增强 hover 明细。

## 2. 现状与技术可行性（已核实）

### 2.1 数据流现状
- 引擎 `strategies/backtest/engine.py::run` 产出 `BacktestResult`（`config.py::BacktestResult` frozen dataclass）：
  - `nav_curve: pl.DataFrame{trade_date, nav}`（**日期天然可得**，engine.py L147-152）
  - `benchmark_returns: pl.Series`（日频基准收益，小数，engine.py `_calc_benchmark_returns`）
  - `ic_series: pl.Series`（**纯数值，无日期**，engine.py `_calc_ic_series`，仅在有有效信号对齐时采样，长度 ≠ trade_dates）
- VM `ui/viewmodels/backtest_view_model.py` 从 result 提取渲染字段：`nav_curve`（仅 nav 值）、`ic_series`（仅值），**丢弃了日期与基准**。
- UI `ui/components/backtest/backtest_result_panel.py`：
  - `_build_nav_chart(nav_curve)` 横轴 `x=i` 序号，无日期、无轴标题、无基准、无图例、无摘要。
  - `_build_ic_chart(ic_series)` 同缺。

### 2.2 flet_charts API（已读 .venv .py 源码验证，0.x 版）
| 能力 | 依据 |
|---|---|
| 轴标题 | `ChartAxis.title: ft.Control` + `title_size`（chart_axis.py L32-37）|
| 自定义横轴标签 | `ChartAxis.labels: list[ChartAxisLabel(value, label)]`（chart_axis.py L48-52；依赖 `label_spacing` 抽样）|
| 折线 hover 明细 | `LineChartDataPoint.tooltip`（str/`LineChartDataPointTooltip`）+ `show_tooltip`（line_chart_data_point.py L96-105）|
| 基准虚线 | `LineChartData.dash_pattern`（line_chart_data.py L58-64）|
| 多序列同图 | `LineChart.data_series: list[LineChartData]`（line_chart.py L215）|
| 柱状 hover 明细 | `BarChartRod.tooltip` + `show_tooltip`（bar_chart_rod.py L121-131）|
| 图例 | **无内置 name** → 需 UI 自绘（色块 + 文本 Row）|
| IC 日期对齐 | 需引擎增量返回 `ic_dates` |

### 2.3 持久化
`services/backtest_service.py::_persist_result` 单向 `save_result`，**不回读还原为 `BacktestResult` 供 UI**（VM 直接用 `engine.run` 返回的 result）。因此 **`ic_dates` 仅需内存传递，不落库**，持久化契约保持稳定。

## 3. 范围决策（默认已定，经 3 轮检视修订）
1. **IC 图一并做完整**（引擎增量返回 `ic_dates`）：完整覆盖 P2-05，但为跨层改动。
2. **文本摘要用可选中复制文本**（`ft.Text(selectable=True)`），不加剪贴板按钮（YAGNI、避免剪贴板权限/回调复杂度）。
3. **回撤不做图内标注**：最大回撤以指标卡 + 文本摘要呈现（P2-05 建议为"可选项"，图内峰谷高亮会引入额外 series/区间绘制，超出本次语境补齐核心 5 项验收）。此取舍在方案中显式声明。
4. **摘要含数据来源行**（策略名 + 基准名），落实 P2-05「数据来源」建议。

## 4. 设计方案

### 4.1 引擎层 — 增量返回 IC 日期（跨层主改动）
文件：`strategies/backtest/engine.py`、`strategies/backtest/config.py`

- `_calc_ic_series(...)` 返回类型改为 `tuple[pl.Series, list[date]]`（首个为 IC 值，次为对应 `signal_date`）。
  - 空信号时返回 `(pl.Series([], dtype=pl.Float64), [])`。
  - 采样循环内：append `ic` 时同步 append `signal_date`（即 `trade_dates[i]`）。
- `run` 内解包 `ic_values, ic_dates`，`BacktestResult` 新增字段 `ic_dates: pl.Series`（`pl.Series(ic_dates, dtype=pl.Date)`）。
- `config.py::BacktestResult`：
  - 新增 `ic_dates: pl.Series`，**带默认值并置于 dataclass 末尾**：`ic_dates: pl.Series = pl.Series(dtype=pl.Date)`。frozen dataclass 允许末尾字段带默认值，可避免破坏既有测试构造点（详见 §5 构造点清单）。
  - `with_warnings()` 追补 `ic_dates=self.ic_dates`（冻结重建需要）。
  - `to_persist_dict()` **不追加** `ic_dates`（非持久化字段，保持 DB 契约稳定；IC 图仅实时展示）。
- `services/backtest_service.py`：透传，无改动。

### 4.2 VM 层 — 透传日期与构造基准曲线
文件：`ui/viewmodels/backtest_view_model.py`

`BacktestState` 新增渲染字段：
- `nav_dates: tuple[str, ...]` — 从 `result.nav_curve["trade_date"]` 格式化为 `%Y-%m-%d`。
- `benchmark_curve: tuple[float, ...]` — 基准相对净值，**递推构造**（同起点、消除首日收益错位）：
  `bench[0] = nav[0]`（与策略净值首点显式对齐锚点）；
  `bench[i] = bench[i-1] * (1 + benchmark_returns[i])`（i≥1）。
  渲染/构造处须保证 `len(benchmark_curve) == len(nav_curve)`；若不等（数据异常），退化为不渲染基准系列。
- `ic_dates: tuple[str, ...]` — 从 `result.ic_dates` 格式化。

新增模块级纯函数（可单测，不依赖 UI）：
- `_to_date_strings(dates) -> tuple[str, ...]`：统一 `date`/`str`/`pl.Date` 为 `"%Y-%m-%d"` 字符串（`pl.Date.to_list()` 得 `datetime.date`，`strftime` 直接可用；str 输入透明透传）。
- `_build_benchmark_curve(nav0: float, returns: Sequence[float]) -> tuple[float, ...]`：按上述递推累计相对净值（全 0 收益 → 恒等曲线 = nav0 全程）。

成功终态 `_set_state` 补填三字段；**初始化/启动/失败/取消**路径全部 `self._set_state(..., nav_dates=(), benchmark_curve=(), ic_dates=(), ...)` 置空，避免上次结果残留：
- L338 run 启动重置；
- L415 失败路径；
- （取消路径经 `_CANCELLED`/except 分支一并处理）。
三新字段生命周期与既有 `nav_curve`/`ic_series` 完全一致。

### 4.3 UI 层 — 图表语境增强
文件：`ui/components/backtest/backtest_result_panel.py`

**`_build_nav_chart(nav_curve, nav_dates, benchmark_curve)`**（新参带默认空元组 `nav_dates=(), benchmark_curve=()`，避免破坏现有调用的位置签名）：
- 空态判断：`nav_curve` 或 `nav_dates` 为空 → 现有空态。
- 底部轴：`fch.ChartAxis(title=ft.Text(I18n.get("backtest_chart_axis_date")), labels=[ChartAxisLabel(value=i, label=d) for i, d in enumerate(nav_dates)], label_size=...`，配合 `label_spacing`（自动或显式抽样，见 §7）来避免数百 control 拥挤。
- 左轴：`fch.ChartAxis(title=ft.Text(I18n.get("backtest_chart_axis_nav_value")), label_size=...)`。
- 策略系列：`LineChartData(color=AppColors.PRIMARY, stroke_width=2)`，每点 `tooltip=f"{date} {I18n.get('backtest_chart_legend_strategy')}: {val:,.0f}"`、`show_tooltip=True`。
- 基准系列（`benchmark_curve` 非空 **且** `len == len(nav_curve)` 才加）：
  `LineChartData(color=AppColors.TEXT_SECONDARY, stroke_width=1.5, dash_pattern=[6, 4])`，tooltip 同构。
- 顶部/下方自绘图例：`ft.Row([色块 Container(w/h 12+bgcolor) + Text, ...])`（策略 PRIMARY、基准 TEXT_SECONDARY）。
- `tooltip=fch.LineChartTooltip(...)`、`interactive=True`（默认）。

**`_build_ic_chart(ic_series, ic_dates)`**（`ic_dates=()` 默认）：
- 左轴 title=`backtest_chart_axis_ic_value`。
- 底部轴 `labels` 映射 `ic_dates`。
- 每个 `BarChartRod` 加 `tooltip=f"{date} IC: {v:.4f}"`、`show_tooltip=True`。

**新增 `_build_chart_summary(strategy_name, benchmark_name, metrics, nav_dates, ...)` -> `ft.Text`**（可选中复制）：
- `selectable=True`；内容聚合：数据来源（策略名/基准名）、回测区间（首/末交易日）、期初/期末净值、总收益、最大回撤、与基准超额收益。
- 放于净值曲线 Tab 顶部；复用 `metrics` 已计算字段，不新增计算。

### 4.4 i18n（key 前缀统一 `backtest_chart_*`，与 `backtest_*` 命名空间一致）
`locales/zh_CN/strings.json` + `locales/en_US/strings.json`，zh=中文 / en=英文：
- `backtest_chart_axis_date`（日期 / Date）
- `backtest_chart_axis_nav_value`（净值 / Net Value）
- `backtest_chart_axis_ic_value`（IC值 / IC Value）
- `backtest_chart_legend_strategy`（策略 / Strategy）
- `backtest_chart_legend_benchmark`（基准 / Benchmark）
- 摘要 key（完整清单，zh/en 各自补齐，缺任一漏配会露出 raw key）：
  - `backtest_chart_summary_title`（回测摘要 / Backtest Summary）
  - `backtest_chart_summary_source`（策略/基准：{strategy}/{benchmark}）
  - `backtest_chart_summary_range`（区间：{start} ~ {end}）
  - `backtest_chart_summary_start_nav`（期初净值 / Starting NAV）
  - `backtest_chart_summary_end_nav`（期末净值 / Ending NAV）
  - `backtest_chart_summary_total_return`（总收益 / Total Return）
  - `backtest_chart_summary_max_dd`（最大回撤 / Max Drawdown）

## 5. 测试计划（TDD，先写失败用例）
**`BacktestResult` 构造点清单（新增 `ic_dates` 默认值后，仅需显式提供的为验证日期标签的 fixture；其余靠默认值）：**
- 生产：`engine.run` 显式传 `ic_dates`；`with_warnings` 追补 `ic_dates=self.ic_dates`。
- 测试：
  - `tests/unit/strategies/backtest/test_backtest_config.py::_make_result(defaults)`：`defaults` 显式补 `ic_dates`（缺会哈希不完整/类型不一致）。
  - `tests/unit/data/test_backtest_dao.py`、`tests/unit/strategies/backtest/test_report.py`、`tests/unit/services/test_backtest_service.py`、`tests/unit/strategies/backtest/test_backtest_config.py`：若断言涉及 `ic_dates` 则显式给；否则靠默认值不加（仅需确认依赖 `ic_dates` 的断言处补）。

- `tests/unit/ui/test_backtest_result_panel.py`：
  - 迁移现有 `_build_nav_chart`/`_build_ic_chart` 调用签名（现有单参调用靠新参默认空元组保持可跑；新用例显式传 `nav_dates`/`benchmark_curve`/`ic_dates`）。
  - 新增断言（**新 key 用真实 `I18n.get(key)` 同源比对，不 patch I18n.get 为 mock 文本**，否则退化为弱断言）：
    - nav 图：底部轴含日期 labels；含两条系列（策略+基准）当 `benchmark_curve` 非空且等长；仅一条当为空或不等长；左轴 title 存在；tooltip 格式串含策略/基准标题与千分位净值；图例含策略/基准两项。
    - ic 图：底部日期 labels、左轴 title、rod tooltip。
    - 摘要：`selectable=True`，内容含策略名/基准名/首末日期/期末净值/总收益/最大回撤（用 `I18n.get(key)` 同源）。
  - 「弱断言」比例控制：批量构造同源期望值，避免 `assert x is not None` 超过阈值。
- `tests/unit/ui/test_backtest_view_model.py`：
  - mock result 带 `nav_curve["trade_date"]`、`benchmark_returns`、`ic_dates`，断言 state 三字段正确提取。
  - `_build_benchmark_curve`：全 0 → 恒等 nav0 全程；递增序列数值正确；`_to_date_strings` 覆盖 `date`/`str`/`pl.Date` 三分支。
- `tests/unit/strategies/backtest/test_engine.py`：`_calc_ic_series` 返回 `(values, dates)`，日期与值一一对应且为 `signal_date`（非 `execution_date`）；同处同步 append + `len(values)==len(dates)` 断言；空信号返回 `([], [])`。

## 6. 验证门禁
- `ruff check .` → `ruff format --check .`
- `pyright`
- `python -m pytest tests/unit/ui/test_backtest_result_panel.py tests/unit/ui/test_backtest_view_model.py tests/unit/strategies/backtest/ tests/unit/data/test_backtest_dao.py tests/unit/services/test_backtest_service.py -v --tb=short`
- 覆盖率：改动单文件 ≥80%，总 ≥85%。
- E2E：`run_e2e_local.py` 相关回测用例；**必须实测**横轴 label 密度（长序列不拥挤）与摘要/图例 anchor 稳定性（参考 UX-11 的 Semantics/锚点经验，必要时给摘要/图例 `data-testid` 独立语义边界）。

## 7. 风险与注意事项
- **冻结 dataclass**：`ic_dates` 默认值置于末尾，`with_warnings` 追补；engine 显式传；测试 fixture 靠默认值 + 需断言处显式给（构造点清单见 §5）。
- **IC 图日期对齐**：`ic_values`/`ic_dates` 在**同一处**同步 append `signal_date=trade_dates[i]`（勿误用 `execution_date=i+1`），并断言长度一致。
- **基准曲线**：递推同起点锚定 `nav[0]`；渲染前断言长度与 `nav_curve` 一致，不一致则不渲染基准系列。
- **横轴标签密度**：`nav_dates` 可能数百，全量 `labels` 依赖 `label_spacing` 抽样；若实测仍拥挤，降级为显式抽样 `labels = [ChartAxisLabel(value, date) for ... in nav_dates[::step]]`（value 用步进后的真实序号），需 E2E/截图验证。
- **信号长度≠交易日**：`ic_series`/`ic_dates` 与 `trade_dates` 不等长（仅在有效信号采样），IC 图横轴独立映射，勿与 nav 图共用 x_labels。
- **E2E/anchor**：可选中摘要 `ft.Text(selectable=True)` 与自绘图例可能在语义树引入新边界，需实测 anchor 前缀匹配；必要时 `data-testid` + 独立 Container 隔离。
- **R1 分层**：ui→strategies 为允许方向；engine 改动不引入反向依赖；VM 提取日期属 data→ui 读取，无越界。
- **R18**：已在独立 worktree 隔离。

## 8. 三轮检视记录（已完成，方案定稿）
- **ROUND1 覆盖与规范**：覆盖 P2-05 全部 5 项验收 ✓；分层/MVVM/极简通过；3 处需改进（基准公式歧义、测试同源断言模式矛盾、既有 chart 测试签名迁移）→ 已修订 §4.2/§4.3/§5。
- **ROUND2 对抗/风险**：4 主要（frozen 构造点、基准曲线首日错位、VM 状态残留、测试签名）+ 若干次要 → 已修订 §4.1/§4.2/§4.3/§5/§7。
- **ROUND3 反证/验证**：核心技术假设全部成立（label 日期映射、str→Tooltip 转换、IC 逐点对齐 `signal_date`、基准同起点、`pl.Date` 直接格式化）；仅构造点覆盖缺口（中）→ 已修订为默认值方案。
- **总体裁决**：方案放行，进入 TDD 实施。