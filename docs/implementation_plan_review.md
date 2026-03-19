# implementation_plan.md 架构深度审视报告 (Code-Review Audit)

作为资深架构师，我深入比对了 `docs/implementation_plan.md` (以下简称《计划》) 与系统当前的真实源码 (`data/cache_manager.py`, `data/daos/*`, `data/sync_strategies/*`, `services/task_manager.py` 等)。

以下是针对该方案的深度审视、当前落实现状以及后续优化的专业评估：

## 💡 一、 整体战略评估：拨乱反正的架构回归

《计划》对当前系统中的 `asyncpg` 类型报错进行了极为深刻的剖析，并作出了一个至关重要的架构决断——**废弃 Phase 1 (DAO层正则类型护栏)，转而直击病灶在 Phase 3 (服务层清理 `strftime`) 修复强类型契约**。

*   **架构师视角的赞赏**：在底层 DAO 增加隐式的正则探测和转换（曾提议的 Phase 1）是典型的高成本、高风险“技术债掩盖”手段，尤其在处理海量量化数据的高吞吐量 `_read_db` 路径中会成为性能黑洞。当前《计划》选择走向“强契约（Contract-First）”，要求上游业务层必须传递原生 `datetime.date`/`datetime.datetime`，这是**唯一正确的企业级处理方式**。它不仅解决了报错，更杜绝了 `code_review_guidelines.md` (防区七) 中指出的 `date vs str 差集失效` 等深层隐患。

## 🔬 二、 源码实况核对与落地偏差预警

经过逐行代码扫描，当前系统**尚未实施**《计划》中的任何阶段代码。所有被指出的类型债依旧潜伏在代码库中。以下是对各个 Phase 在未来落地时的“排雷指南”：

### 1. Phase 0: `cache_manager.py` 职责下沉与“死锁危局”
*   **当前代码现状**：源码 `cache_manager.py` 第 464 行附近，依然原封不动地在使用 `await conn.exec_driver_sql("... >= $1 ...", (str(g_min), str(g_max)))` 进行裸查询和类型破坏。对应的 `quote_dao.py` 与 `stock_dao.py` 尚未被注入新方法。
*   **💡 架构级核心警告 (连接池嵌套锁死)**：
    《计划》中 10.1.3.2 节提到的“连接上下文重构”极其敏锐。在 `asyncpg` 配合 SQLAlchemy 的环境中，如果在 `async with self.engine.connect() as conn:` 语句块**内部**调用 `await self.quote_dao.get_date_range()`，因为 DAO 自身封装也会去获取由 SQLAlchemy 管理的新连接，单线程将瞬间锁住 2 个甚至更多连接。
    **指导意见**：实施时，**绝对严格遵守**计划中所写的：必须将所有的 DAO 读取动作放置于开启任何长连接上下文（`async with conn`）的**外部**，获取完内存变量后再进入后续流程。

### 2. Phase 2: `task_manager.py` 的 SQL 纯化
*   **当前代码现状**：`task_manager.py` 第 464 行依旧是直接拼装函数：`DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')`。
*   **💡 架构级评估**：
    该处的修复方案（在 Python 中计算好确切的 `cutoff_date` 然后传入 `$1`）不仅解决了由于没有参数强类型绑定导致的 `text` 强转错误，更重要的是将“时间计算权”收归回了受控的本地上下文，避免了数据库服务器时间和 Python 宿主机器时间的微小飘移，杜绝了时区撕裂。这是极佳的企业级重构。

### 3. Phase 3: 服务层 28 处 `strftime` 清理 (核心战场)
*   **当前代码现状**：如 `historical.py` 的第 88、483、503 行依然充斥着 `get_now().strftime("%Y%m%d")`，其余数据服务层的污染调用也一个都没少。
*   **💡 架构级指导**：
    这是本次计划工作量最大、也最引发回归缺陷防线的防区。
    *   **建议增补防线**：在全面替换这些代码前，需要特别关注 `TushareClient` 的调用。UI 层和请求 Tushare 等外部接口时依然需要 `str` 格式。在重构时，必须在 DAO 层的大门和外部网络请求的出口划出清晰的分水岭：**向系统内部 DB 的游走数据对象皆用 `date`/`datetime`，流向外部 API 的调用皆显式降级为 `str`。**

## 🚀 三、 架构师最终决断与下一步行动指令

这份 `implementation_plan.md` 逻辑缜密，不仅找到了 `asyncpg` 类型报出错误的真因，风险应对（见《计划》第 12 节）也设计得当，与团队现行的最高准则 `code_review_guidelines.md` 完全贴合（尤见指南的 45-51 条）。

**签发意见 (Sign-off)**：
**针对该实施计划，给予完全架构放行批准 (Approved for Execution)。** 

**实施节奏与建议步骤**：
为了保证核心金融数据的稳定，我强烈建议将此浩大的重构任务划分为三个独立安全的“PR/Commit 战役”推进：
1.  **初战告捷**：先单独执行无破坏性的 isolated **Phase 2** (`task_manager.py` 安全修复)，验证底层的 `$1` 参数化 `datetime` 传递机制畅通无阻，建立信心。
2.  **雷区排解**：执行最高危险系数的 **Phase 0**，重构并下沉 `cache_manager.py` 这个高频数据枢纽。需在此次修改中严格打灭掉任何微小的并发连接“幽灵锁”嵌套现象。
3.  **最终横扫**：集中性扫荡 **Phase 3** 中指出的 `historical.py` 等十余个文件，大规模拔除 `strftime` 毒瘤，完成数据流契约的终极纯化。

如果您已准备好开始实施，请下达口令，我将严格依据此节奏协助您启动代码库的实质性重构与回归验证。
