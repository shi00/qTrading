# UX-05 复盘聚合统计视图 — 设计方案（v5，已吸收四轮检视修订：一审 4 视角 + 二审 Skeptic + 三审对抗性 harness-review + 四审对抗性实证 M1）

> **v5 关键修订（四审对抗性 harness-review，实证缺陷）**：
> - **M1（实证）**：存储层 `alpha` 实为 **T+1 超额收益**（`review_manager` 中 `alpha = t1_pct - index_pct`，WIN/LOSS 判定亦基于此）；T+5 回填不写 `index_pct`/`benchmark_code`，**不存在 T+5 alpha**。主指标由「T+5 Alpha」**改为「T+1 Alpha」**（已与用户确认采用推荐口径），零 schema 改动。
> - **M2**：胜率（逐股二项判定，样本单位=股票行）与均值 CI（样本单位=交易日）**样本单位不同**，主行单 N 与单角标会错配 → **胜率独立 N + 独立 30/100 分级**，UI 分行呈现。
> - **M3**：日组合均值**等权**，组合规模骤变时 1 股高波动日稀释 CI → 保留等权（避免加权引入新口径分歧），**附加披露「日均纳入股票数」**提醒规模波动。
> - **L1**：`DISTINCT ON` 补 `ORDER BY run_id DESC` 保证快照确定性（迁移前旧键 `(run_id,ts_code)` 窗口的去重结果不确定）。
> - **L2**：scipy 未入 pyproject → **t 分布内插表明确覆盖 df=1..179** 并承诺 30/100 边界精度，配套固定表单测。
> - **L3**：NULL 基准组与 alpha 可比组**强视觉分隔 + 文案隔离**，防误读。

## 目标
满足 UN-04「可信地验证自己的研究方法」（review05 UX-05）：在当前**单股维度**复盘之外，新增**按策略汇总**的复盘聚合统计视图——展示每个策略的有效样本量、日组合收益均值、标准差、置信区间，以及 WIN/LOSS 胜率。当样本量不足时显式提示，防止用户基于少量样本得出「策略有效」的错误结论。

## 需求收敛（已与用户确认，v5 定稿）
| 决策点 | 结论 |
|--------|------|
| 功能广度 | **按策略汇总子视图**：按 `strategy_name + benchmark_code` 聚合跨多日复盘明细，可下钻单股 |
| **样本单位** | **日组合收益序列**（三审 M1 定稿）：先按 `trade_date` 聚类，每天取组合内股票收益均值得日序列，对日序列算 mean/std，CI 用 t 分布。N=交易日数（≈180 天内），**不用股票行计数作独立样本**——消除横截面相关/虚高统计效力 |
| 样本量阈值 | 沿用检视报告：日样本 N<30「无参考意义」；30≤N<100「仅供参考」；≥100 正常 |
| **指标排布** | **行内主指标 + 折叠**（三审 M3 + 四审 M2 定稿）：单行只放主指标（**T+1 Alpha** / CI / 日序列 N / **胜率独立 N 角标**）；T+5 等下钻折叠（T+5 仅收益均值，标注「无超额基准」，四审 M1），避免信息过载 |
| **N 口径** | **指标独立 N + NULL 组**（三审 M4 + 四审 M1/M2 定稿）：均值与 CI 用**日序列 N**（各指标非 NULL 独立日序列），样本单位=交易日；胜率用**股票行 N**（逐股二项判定），独立 30/100 分级、UI 分行呈现；`benchmark_code IS NULL` 历史行单列为「基准未知」组，与 alpha 可比组强视觉分隔（四审 L3） |
| 统计口径 | 主指标含 **T+1 Alpha**（= `t1_pct − index_pct`，与存储层/胜率判定一致，四审 M1）的日序列均值/CI/分级 + WIN/LOSS 胜率（独立 N）；T+5 仅展示收益均值（无超额基准）。**不含** UN-05 样本演变趋势（本轮不做） |

## 架构基线

### 1. 数据层（data/persistence/daos/screener_dao.py）
新增聚合 DAO 方法 `get_strategy_review_stats()`：
- **SQL 预聚合**（对齐 `get_history_tree` 风格，三审 m1/M2 定稿）：按 `(benchmark_group, strategy_name, trade_date, metric)` 返回每组的**日级聚合值**（每日组合内均值、当日有效样本数），窗口近 180 天。
- **覆盖语义处理（三审 M2 + 四审 L1 定稿）**：同日同策略同股票为覆盖语义（唯一键 `(trade_date,strategy,ts_code)`，被淘汰股票行残留），聚合**不能按股票行直接计数**。SQL 内按 `(trade_date, strategy_name, ts_code)` 去重并取该股票最新快照（`DISTINCT ON (trade_date,strategy_name,ts_code) ORDER BY trade_date, strategy_name, ts_code, run_id DESC`）后，再按 `trade_date` 聚类求日组合均值 → 每日一个收益点，消除多运行日加权放大。补 `ORDER BY run_id DESC` 保证快照确定性（迁移前旧键 `(run_id,ts_code)` 窗口去重结果不确定，四审 L1）。
- **指标独立 N（三审 M4 定稿）**：t1_pct / t5_pct / **alpha（= T+1 超额收益，四审 M1）** 各自以**非 NULL 行**聚类成独立日序列，每指标有独立 N（交易日数）。
- **N 口径**：t 分布 CI 的样本量 = 存活日序列的交易日数；每日内多个股票收益均值为该日组合点。
- 复用现有 SQLAlchemy Core / asyncpg `$1`，不引入 SQL 注入（R4）。

### 1.1 常量与窗口（三审 m1/m2 定稿）
- screener_dao 定义**模块常量 `REVIEW_STATS_WINDOW_DAYS = 180`**，并让 `get_history_tree` 的 `'180 days'` 与 `get_strategy_review_stats` 引用同一常量（消除魔术字符串漂移）。
- **过滤一致性**：聚合与 `get_history_tree` 展示的「策略集」共用同一 `strategy_name` 过滤条件，避免两侧 N 不一致。
- **点击下钻**复用 `get_history_records`（返回明细含全部 `_base_cols`）；置信区间由消费端基于日序列 N/均值/标准差计算。

### 2. 统计计算（数据层 `data/domain_services/`）
**架构（一检 Arch Major#1 + 二审 落点 + 三审 口径）**：统计计算**不得** import `strategies/backtest/metrics.py`（触发越界单测并违反 MVVM）。复盘统计为**数据聚合 + 阈值判定**业务，承载于 `data/domain_services/`（同层既有 `trade_calendar_service.py`/`market_data_service.py` 先例；业务逻辑放 `data/domain_services/` 符合约定）：
- `review_stats_service.py`：输入 DAO 日级聚合（各 `trade_date` 的组合内均值、当日有效样本数、胜负数），对**日序列**计算统计，输出 `StrategyStatRow` 列表（各指标 N/均值/标准差/置信区间/胜率/分级）。
- **置信区间（三审 M1 + 四审 L2 定稿，t 分布）**：按日序列样本量 `n=n_dates`，使用 **t 分布**临界值（`mean ± t_{0.975, n-1} * std / sqrt(n)`），不用正态 1.96——小样本日数下更诚实；**n<30 时置 None**并提示「无参考意义」，**n<3 时 std 为 NaN/0 置 None**。t 分布用**固定内插表**（scipy 未入 pyproject，四审 L2）：覆盖 **df=1..179** 的 97.5 分位临界值，明确定界 30/100 边界（df=29/30/99/100），配套固定表单测断言。
- **样本量分级常量**：均值/CI 用日序列 N 分级（`REVIEW_MIN_SAMPLE=30` / `REVIEW_ADEGUATE_SAMPLE=100`）；胜率用股票行 N **独立分级**（四审 M2），两套分级共用同一常量但独立判定、UI 分行呈现。硬编码模块常量（二审 Skeptic 定稿：不引 ConfigHandler，走 YAGNI——CLAUDE.md §1.3 极简顺序）。
- **指标独立 N（三审 M4 + 四审 M1/M2 定稿）**：主指标 T+1 alpha 与 t1/t5 各自由非 NULL 日序列算独立 N/均值/标准差/CI；**胜率沿用 `alpha_win_threshold`/`alpha_loss_threshold` 判据（`review_manager` 判据，同为 T+1 alpha 口径，四审 M1）**，但胜率分母用股票行 N 独立计算。
- **等权与规模披露（四审 M3 定稿）**：日组合均值采用等权（不引入组合规模加权，避免新口径分歧）；DAO 返回「当日有效样本数（纳入股票数）」，**统计服务额外输出「日均纳入股票数」**供 UI 披露，提醒组合规模骤变导致的 CI 稀释。
- **稳健性**：N=0 或该策略无任何复盘记录 → 跳过不产出；存活者偏差文案「统计基于已通过筛选股票」。

### 2.1 分组与 NULL 基准（一检 M2 + 三审 M4 定稿）
- 聚合按 `(strategy_name, benchmark_group)` 分组，组内基准一致；`benchmark_code IS NULL` 历史行归入**「基准未知」组**，alpha 才有的指标在其下不展示可比区间，仅给 T+1/T+5 独立均值/N。
- 不同历史基准自然拆为多组，各自独立 N 与口径，避免跨基准 alpha 误比。
- **视觉与语义隔离（四审 L3 定稿）**：NULL「基准未知」组始终排在 alpha 可比组**之后**，用**分隔线 + 灰色弱化 + 独立组标题**呈现，组内显示「基准未知，无超额基准」引导文案，防止与 alpha 可比组并排时被误读为可比较。

### 3. ViewModel（ui/viewmodels/history_mode_mixin.py）
- 新增聚合 state：`strategy_stats: tuple[StrategyStatRow, ...]`。
- `load_strategy_stats()` 拉取聚合数据；viewmodel 只产出 i18n key，不感知 locale（§3.2 MVVM）。

### 4. View（ui/views/screener_view.py，三审 M3 + 四审 M1/M2/M3 定稿：主指标+折叠）
- HISTORY 模式新增「策略汇总」展开区：每策略/基准组一行，**行内只放主指标**（**T+1 Alpha** / 置信区间 / 日序列 N + **胜率独立 N 角标** + **日均纳入股票数披露**）；**T+5 收益均值为折叠内容**，标注「无超额基准」（四审 M1）；T+1 均值、标准差等下钻折叠显示。
- **胜率独立角标（四审 M2）**：均值用日序列 N 分级（<30/30-100/≥100），胜率用股票行 N **独立分级独立角标**，两角标分行/分标签呈现，避免单一 N 错配两套样本单位。
- 点击下钻到 `get_history_records(trade_date, strategy_name)` 单股明细。
- 纯声明式组件，不持业务状态。
- 样本量提示角标：日序列 n<30「样本量不足，无参考意义」；30≤n<100「样本量有限，仅供参考」；≥100 正常（同样规则独立应用于胜率股票行 N）。

### 5. i18n（locales/zh_CN|en_US/strings.json）
- 新增：聚合标题、N/均值/标准差/置信区间/胜率列名、「样本量不足/有限」提示文案、**胜率独立 N 列名与独立角标文案（四审 M2）**、**「日均纳入股票数」披露标签（四审 M3）**、**「T+5 无超额基准」折叠标注（四审 M1）**、**「基准未知，无超额基准」隔离引导文案（四审 L3）**、「基准未知」组标签、存活者偏差说明、下钻折叠标题。

### 6. 测试（R19 配套，三检汇总 + 四审修订）
- 聚合 DAO `get_strategy_review_stats`：日级聚类正确性（**DISTINCT ON + ORDER BY run_id DESC 快照确定性（四审 L1）**、按 trade_date 求日组合均值、指标独立 N、窗口过滤、NULL benchmark 分组、**alpha=T+1 口径断言（四审 M1）**、**日均纳入股票数输出（四审 M3）**）。
- 统计计算 `review_stats_service`：t 分布 CI（n 分级边界：n=0 跳过、n=1/2 为无法计算、n=29/30/99/100）、**t 分布内插表固定表断言（df=29/30/99/100 精确匹配，四审 L2）**、胜率（WIN/LOSS 占比 + **股票行 N 独立于日序列 N 的用例（四审 M2）**）、`t1_pct/alpha` NULL 不计入分母。
- **边界用例**：组合内仅 1 只股票（日组合均值=该股）、空策略安全降级、显式断言语义（均值/分类枚举，非弱断言）。
- VM state：`load_strategy_stats()` 解析与容错（空/缺列不 raise）。
- 回归：UX-04 归因列 / 复盘列 / history_tree / pagination 不动（改动的 history_mode 路径不破坏既有单测）。
- 复用 `tests/unit/` 既有 mock（mvd_data fixture / override_db_url），统计计算为纯函数可无 DB 单测，t 分布临界值用固定表断言。

## 交付链路（分支 A，v5）
1. `data/persistence/daos/screener_dao.py`：定义 `REVIEW_STATS_WINDOW_DAYS` 常量并入 `get_history_tree`；新增 `get_strategy_review_stats()`（DISTINCT ON + ORDER BY run_id DESC + 日级预聚合 + 日均纳入股票数输出）。
2. `data/domain_services/review_stats_service.py`：日序列统计计算（t 分布内插表 + 胜率独立 N + 等权均值 + 分级）+ 明文。
3. `ui/viewmodels/history_mode_mixin.py`：`strategy_stats` state + `load_strategy_stats()`。
4. `ui/views/screener_view.py`：history 侧栏「策略汇总」展开区（T+1 Alpha 主指标 + 胜率独立角标 + 折叠 T+5）+ 下钻。
5. `locales/{zh_CN,en_US}/strings.json`：聚合标题/列名/提示文案/隔离文案。
6. 单测 + 回归，pre-commit 全门禁，现有测试不降级。

## 明确不做的（YAGNI / 边界）
- UN-05 样本演变趋势（近 N 次 vs 全部）：登记为后续增强，不阻塞本轮。
- 不做跨量纲指标（均值/标准差跨不同收益口径不可直接比较之外，无损导入）。
- **不改 schema**（ScreeningHistory 既有字段已满足）：采纳 v5 的 T+1 Alpha 主指标准入，依赖既有 `alpha`（= t1_pct − index_pct）、`t1_pct`/`t5_pct`/`benchmark_code` 列，无新增列/迁移（四审 M1）。