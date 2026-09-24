# AStockScreener 项目 Profile

> 加载方式：检视 AStockScreener 项目代码时必须读取。包含项目覆盖规则与 reviewProfile 结构。

## 项目规则优先级

项目特定规则优先于 [ai-review.md](../ai-review.md) 通用建议。冲突时以 [CLAUDE.md](../../../CLAUDE.md) §3 红线 / §4 架构边界为准。

## 红线索引（R1~R24）

检视时须对照正本逐条核对 R1~R24 的违反；全部 R1~R24 的编号、标题与定义以 [CLAUDE.md §3.1](../../../CLAUDE.md#31--绝对禁止) 为唯一正本（机器可读镜像见 [docs/governance/redlines.yml](../../governance/redlines.yml)）。本 profile **不复述**红线定义（避免与正本措辞分化），只补充正本未承载的增量：无完整自动门禁红线的人工追查步骤（见下节）。

## 必检维度（本项目）

除 [ai-review.md](../ai-review.md) 的通用必检维度外，本项目将 [quality-dimensions.md](../quality-dimensions.md) 第 11 维「量化结论可信度」列为**必检**：检视策略 / 回测 / 选股结论类变更时，必须回答「取数时点是否前视」「票池是否含幸存者偏差」「复权口径是否一致」「结论是否受财报修订 / 质量缺口影响」（正本见 [backtest-correctness.md](../../patterns/backtest-correctness.md)）。

## 红线自查步骤

对 `automation_coverage: none` 的 3 条红线（R5 / R17 / R18）与 `partial` 自动门禁的 R20 / R21 / R22 / R24（R20 / R21 / R24 由 `check_R20` / `check_R21` / `check_R24` 报告模式提示 warning、不阻断退出码；R22 由 `check_R22` 水位线静态检测、pre-commit 拦截）以及高风险的 `partial` 维度（R16 事件处理器维度 / R11 缓存点与跨循环使用维度）给出可执行自查入口：每条给出**搜索式**（明确 grep/模式）、**调用链追溯**（明确入口与定义符号）或**声明核对**（无工作区访问权时的等价入口，如 R18 的 PR 描述声明）。R5 / R17 / R21 的判据正本同时以 `redlines.yml` 的 `self_check` 字段承载，并渲染进 [CLAUDE.md §3.1](../../../CLAUDE.md#31--绝对禁止) 的「人工评审红线可执行自查清单」生成区块（交付时逐条回答）。上述红线无完整自动门禁或自动检测未覆盖全部场景，检视时须主动按此追查。完整语义见 [redlines.yml](../../governance/redlines.yml)，正常本见 [CLAUDE.md §3.1](../../../CLAUDE.md#31--绝对禁止)。

### 独立会话复核（无自动拦截且影响产品结论的红线）

**无自动拦截**（`automation_coverage: none`，或自动化仅报告模式 warning、不阻断退出码）**且影响产品结论**的红线（R21、R24（R24 见 [ruleset-changelog.md](../../governance/ruleset-changelog.md)，由并行单元新增）），除按上述判据自查外，**须经独立会话复核**：新开一个上下文，只提供 diff 与判据（`redlines.yml` 的 `self_check` / 上方追查步骤），**不提供实现理由与自评结论**，由该会话独立判定是否命中。这是 AI 工作流中唯一近似「第二双眼睛」的可行机制（被约束者自评等于无约束），规则正本与体例见 [ai-review.md](../ai-review.md) 的 ROUND3-05。

### R5 僵尸引擎操作
1. 调用链追溯：对改动新增/触及的 DAO 或维护方法，从方法入口追到首次碰 `self.engine` 的读/写点，确认其先经 `BaseDao._check_engine()`（`data/persistence/daos/base_dao.py:116`）检查——该方法调 `engine_provider.is_disposed()`（`data/persistence/engine_provider.py:70`）判定，disposed 时抛 `EngineDisposedError`（`base_dao.py:29`）。
2. 搜索式：在改动 DAO 文件内查是否有绕过保护方法的直达 `self.engine.execute/_read/_write` 等结构（`grep -n "self\.engine\.\|is_disposed\|_check_engine" <改动文件>`），绕过检查点即违规。
3. 豁免：应用服务层轮询循环的优雅停止（既有偏离）经 `docs/governance/exceptions.yml` 登记 —— 评到该类代码先查是否已登记，避免重复判定。

### R11 跨循环复用同步原语（缓存点 / 跨循环使用维度）
1. 类/实例属性直接绑定 `asyncio.Event/Lock/Semaphore` 的构造点已由 `tests/unit/test_no_class_attr_asyncio_primitives.py` AST 覆盖；人工补查的是**缓存点**（把原语缓存进字典/实例后复用）与**跨循环使用**（在另一 loop 上 `await`/`acquire`）。
2. 搜索式：`grep -rn "asyncio\.Event\|asyncio\.Lock\|asyncio\.Condition\|asyncio\.Semaphore" core data services strategies utils`，核对每个拿到原语的位置是否经 `get_loop_local()`（`utils/loop_local.py:22`）绑定当前循环获取而非直接构造缓存。
3. 调用链追溯：对每个 `get_loop_local` 消费方，确认其 `await` 所在协程与命中点同属一个事件循环；跨不同 `asyncio.run`/驱动下的循环共享同一原语即违规。

### R16 UI 阻塞主循环（事件处理器维度）
1. Flet 事件回调（`@ft.component` 的 `on_*` 处理器及 VM 异步 command 内）做同步阻塞 IO/CPU 即违规。改到事件回调时确认耗时位置改为 `await ThreadPoolManager.run_async(task_type, func, ...)`（`utils/thread_pool.py:268`）提交线程池。
2. 搜索式：对改动 diff 中所有 `on_*` 事件处理器及其同步调用体，`grep -n "time\.sleep\|requests\.\|open(\|\.read()\|asyncio\.run"` 命中同步阻塞调用且外层非 `run_async` 的即 R16 候选。
3. 调用链追溯：`@ft.component` 的 on_* → VM command → 是否 `await run_async(...)`；同步段被事件回调直接顶层调用未排队即阻塞主循环。CPU 密集（Polars 等释放 GIL 库）例外见红线描述，但纯 Python 计算须向量化。

### R17 保留字作字段
1. 新增/修改 ORM 模型（`data/models.py` 的 `Table`/列定义）或数据字典（`data/data_dictionary.py:148` 的 `TABLE_DEFINITIONS`）时，检查表名/列名是否数字开头、含特殊字符（`.`/`-`/空格）、或 SQL 保留字（如 `order`/`group`/`select`/`desc`/`from`）。
2. 搜索式：对新增列名做保留字碰撞 `grep -rnE "\b(order|group|select|desc|asc|from|where)\b" data/models.py`；命中保留字/特殊字须用 ORM `name=` 属性映射（Python 属性名 ≠ SQL 列名）。
3. 规避核查：`grep -rn "text(f" data/ services/`，确认没有对保留字列名的 f-string 拼接裸查询（该列名只能经 `name=` 映射，不得出现在裸 SQL 字面量）。

### R18 未隔离开发
1. 检视侧（PR/diff 场景 AI 通常看不到作者工作区，无法执行 `git worktree list` / `git status --porcelain`）：核对 **PR 描述是否声明 worktree 隔离**（给出分支名或 `.worktrees/<name>` 路径）；改动跨多文件（新特性/重构）而未声明隔离即判 R18 候选，要求作者补充声明或说明豁免。
2. 作者侧（AI 自身执行隔离时）：任务开始前 `git worktree list` 确认改动在独立 worktree 进行，`git status --porcelain` 确认主工作区干净、无未隔离源码改动。
3. 命中豁免项（单文件文档纯改、单行修复、bug 复现脚本、`.worktrees/` 内已有隔离）在评审结论注明豁免；无法恢复版本控制（ZIP/只读挂载/IDE 映射目录）时声明「无版本控制兜底」，交付时逐文件列改动清单 —— 见 CLAUDE.md §3.1 R18 执行决策树。

### R20 单位未核对的量纲比较
1. 调用链追溯：对策略/回测改动涉及金额/数量列（`north_money` / `net_amount` / `amount` / `total_mv` / `circ_mv` / `vol`）的数值比较，确认调用链先经 `threshold_in_data_unit()`（`strategies/utils.py:41`）或 `get_column_unit()` / `get_column_unit_source()` 显式取单位（单位声明见 `data/constants.py` 的 `HSGT_COLUMN_UNITS` / `TOP_LIST_COLUMN_UNITS`）。
2. 运行 AST 原型复核：`python scripts/prototype_business_redlines.py`（ROOT 缺省为仓库根）。`UnitCompareVisitor` 检测已知单位列进入大小比较且无换算调用，输出 `R20` 命中；`unit_compare`（`north_money`/`net_amount` 直判）须逐条核，`unit_compare_lowconf`（`amount`/`total_mv`/`circ_mv`/`vol`）误报高、仅作提示。
3. 误报排除：`lowconf` 命中多为回测撮合中单位一致的合法比较（同列同单位），人工读上下文确认两端单位一致后放行；`HIGH` 列直判命中若无 `threshold_in_data_unit()` 不豁免。

### R21 缺失值伪装
1. 业务语义字段（`score` / `ai_score` / `confidence`）缺失必须用 `None`/哨兵（如 `suspend_data_absent`）表示，禁止填 `0` / `50` 等业务合法值。改动中对其赋常量或 `fillna(0)` / `fill(0)` 即违规。
2. 运行自动检测（报告模式）：`python scripts/check_redlines.py`，`check_R21` 复用 `scripts/prototype_business_redlines.py` 的 `MissingMaskingVisitor` 检测 `masking_assign`（赋值 0/50）与 `masking_fillna_lowconf`（fillna/fill 0/50），以 warning 输出 `R21 缺失值伪装`（不阻断退出码，须逐条人工确认）；`score=0`、`confidence=50`、空表当「无限制」均为候选。可行性原型 `python scripts/prototype_business_redlines.py` 仍可运行以查看同源命中的 `defect_id` 标注。
3. 误报排除：合法 `score==0` 语义（AI-01 已修默认 `None`）与 `suspend_data_absent`（DATA-03 已修）不在伪装之列；原型 `defect_id` 标 `clean` 的命中（对已修复缺陷不再报警）无需整改，确认命中的真实缺陷才处理。影响产品结论的变更还须按上方「独立会话复核」执行。

### R22 水位线单调性
1. checkpoint/高水位持久化写入必须单调：优先 `set_app_state_max()`（`data/persistence/app_state_service.py:43`，SQL 层 GREATEST 保护）；禁止用无条件 `set_app_state()`（`app_state_service.py:27`，最后写入者获胜）写水位 key（含 `attempted`/`watermark`/`checkpoint`/`upto`/`last_sync`/`resume` 语义）。
2. 运行 AST 原型：`python scripts/prototype_business_redlines.py`。`WatermarkVisitor` 检测非 `*_max` 的 `set_app_state` 写水位 key，输出 `R22` 命中（`watermark_unmonotone`，SYNC-01 同类）。
3. 调用链追溯：对命中点追读侧消费者 —— 高水位/断点续传读侧若依赖「读到的值即已处理到的最大位置」且写入侧有乱序覆盖即违规；并核对是否配套乱序写入单测（断言最终值取最大值），缺失则该单测为整改必补项。

### R24 时点正确性
1. 运行 AST 原型复核：`python scripts/check_redlines.py`（报告模式），查看 `R24` 命中——扫 `strategies/` 下对无生效日期维度表（`sw_industry_member` / `sw_industry_classify` / `index_member_all`）、其 ORM 类、派生列键（`industry_sw_l2` / `sw_industry`）的引用；命中为候选，逐条确认是否参与跨期历史区间计算。
2. 调用链追溯：对回测/选股结论涉及的维度数据（行业分类、指数成分、股票池、财报最新值），确认其取数时点不晚于被决策交易日——快照类维度须带生效日期，或显式声明「当期近似」并在结果中标注。
3. 人工补查范围（自动检测未覆盖）：`data/` 层已知的跨期前视点（`screener_dao` 行业 `LATERAL` 子查询，属 DAT-08② 已文档化的「已知限制」）与结论可信度边界，正本见 [backtest-correctness.md](../../patterns/backtest-correctness.md)。

## reviewProfile 结构

项目可定义覆盖规则（收紧或豁免通用要求）：

```yaml
reviewProfile:
  scopeRules: []
  architectureRules: []
  requiredDimensions: []
  optionalDimensions: []
  excludedPaths:
    - pattern: "<glob>"
      reason: "<why exclusion is safe>"
      authority: "<approver or governing rule>"
      expiresAt: "<RFC 3339 timestamp or null>"
  generatedPaths:
    - pattern: "<glob>"
      source: "<generator and source inputs>"
      verification: "<reproducibility or artifact check>"
  requiredChecks: []
  severityMapping: {}
  blockingPolicy:
    policyId: "<stable id>"
    policyVersion: "<immutable version>"
  documentationRequirements: []
```

项目规则可以收紧通用要求，也可以显式关闭不适用项，但必须说明理由、批准依据和有效期。法律、安全和基本正确性维度不得被普通路径排除关闭。
