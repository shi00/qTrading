# AStockScreener (QTrading) 代码检视 (Code Review) 终极指南

这份指南是完全基于 **AStockScreener 智能选股系统**当前的真实技术栈（`Flet` + `Polars` + `PostgreSQL` + `llama.cpp/OpenAI`）以及业务域（离线数据落盘 + 定量粗筛 + LLM 定性精审）量身定制的架构师级 CR 规范。

在处理这套系统的 Pull Request 时，审查者 (Reviewer) 需要严格按照以下 **6 个核心防区、43 条致命检查项**进行逐行排雷。

---

## 🛡️ 防区一：策略引擎与数据流转换 (Strategy & Data Flow)
*目标：杜绝未来函数，保证上下游契约与数据纯洁度。*
**重点文件**: `strategies/`, `viewmodels/screener_view_model.py`, `quality_gate.py`

1. **[关键] 数据质量门控遗漏 (Quality Gate Bypass)**
   - **审查点**：开发者在新增或重构策略类的 `filter()` 或 `_filter_logic()` 时，是否遗忘了添加 `@require_quality(QualityTier.BRONZE / SILVER)` 装饰器？AStockScreener 依赖此注解在选股前自动校验底层数据的新鲜度，缺失它会导致在残存数据上强行跑出无效结果。
2. **[关键] ViewModel ↔ Strategy 隐式契约断裂 (Contract Breach)**
   - **审查点**：在策略模块 `fundamental.py` 中如果新增加了对 `context.get('block_trade')` 的依赖查询，审查者必须溯源到 `ScreenerViewModel.run_strategy()`，确认调用方是否真正**装载并传入了**这个对应的数据块！如果上游漏传，运行时不会报错但返回全空。
3. **[关键] 策略注册与装载断链 (Orphan Strategy)**
   - **审查点**：虽然有 `@register_strategy` 装饰器，但如果新增的策略文件没有在 `strategies/all_strategies.py` 中被显式 `import`，它就不会被 `StrategyManager` 感知，也不会出现在 UI 的下拉框中！必须查验引用的闭环。
4. **绝对禁忌：未来函数穿越 (Look-ahead Bias)**
   - **审查点**：获取财报指标时，SQL 连表必须严格按照 `ann_date`（实际公告日）而非 `end_date`（报告期末）进行。不能让系统在 9 月 30 日用到了 10 月 28 日才发布的 Q3 财报。
5. **警惕 Join 操作导致的笛卡尔爆破**
   - **审查点**：当把行情与异动表拼接时，连接键是否严格覆盖了 `(ts_code, trade_date)` 联合主键？缺少日期列会导致同一股票的历史被重复交叉放大，瞬间撑爆内存。
6. **NaN / Null 在过滤中的隐式穿透**
   - **审查点**：执行形如 `.filter(pl.col('pe') < 20)` 时，由于 Polars 处理 `Null` 的特性，需明确 `pe` 为空的股票是被保留还是丢弃？应配合 `.drop_nulls()` 使用。
7. **价格复权对齐 (Price Adjustment)**
   - **审查点**：任何基于历史 K 线计算形态的策略，其依赖的列必须是**复权后的价格列（前复权）**，绝不允许直接使用原始收盘价 `close` 以防除权暴跌导致虚假信号。

---

## 🛡️ 防区二：本地存储与字典抽象 (Storage & Schema)
*目标：保证底层极速读写与展示侧定义的三位一体。*
**重点文件**: `daos/`, `cache_manager.py`, `data_dictionary.py`, `database_manager.py`, `data/schema.sql`

8. **无脑全量更新的抗拒 (Upsert Principle)**
   - **审查点**：大表落盘必须强制使用 `base_dao.py` 中的 `_save_upsert` 接口（`ON CONFLICT DO UPDATE`），并确保明确声明了联合唯一主键，严禁先 `DELETE` 再全量 `INSERT`。
9. **异步并发下的 DB 锁死**
   - **审查点**：后台自动拉取与前台界面并线操作时，严禁在未包裹 `async with self.engine.begin()` 的情况下执行长耗时写盘。
10. **危操作的维护锁屏蔽 (Maintenance Mode)**
    - **审查点**：执行 `clear_all_cache` 或 `init_db` 这类破坏性删表时，是否触发了 `self._maintenance_event.clear()` 来物理挂起所有的并行读取线程？否则报 `no such table`。
11. **表结构升级的静默失败 (Schema Migration)**
    - **审查点**：在 DAO 建表语句中新增了一列时，**极其重要：必须**同步在 `CacheManager._check_and_update_schema` 中添加对应的 `ALTER TABLE ADD COLUMN` 补丁，否则旧用户现存库会因缺少列直接崩溃。
12. **数据字典的缺位 (Data Dictionary Orphan)**
    - **审查点**：在 SQL 大宽表中每 `SELECT` 出一个新的基础字段，审查者必须追查：开发者是否在 `data_dictionary.py` 中同步为其注册了中文解释与单位格式（如 `"circ_mv: 流通市值 (亿元)"`）？否则 UI 表头将裸露英文代码。
13. **量纲与单位换算陷阱 (Unit Scaling)**
    - **审查点**：Tushare 返回的市值单位往往是万元，而 UI 给用户看的是亿元。在 SQL 落盘前或过滤策略中，是否明确执行了 `/ 10000` 的拦截？这是数级差异最大的元凶。
14. **索引缺位与全表扫描 (Schema Index Mismatch)**
    - **审查点**：任何在 DAO 或 Strategy 中新加的查询条件（例如 `WHERE ts_code = ? AND list_date > ?`），审查者必须强制要求开发者去到建表核心处（如 `data/schema.sql` 或 `CacheManager`）比对：**是否为这些高频查询列创建了正确的联合索引（INDEX）？** 针对千万级的历史行情库，哪怕缺少一个索引也会让单次跨表回测慢上 50 倍，这是量化提速的命根子。

---

## 🛡️ 防区三：Flet 大前端工程与生命周期 (Frontend Engineering)
*目标：根治主线程挂起、内存泄漏与残缺排版。*
**重点文件**: `views/`, `components/virtual_table.py`, `ui/i18n.py`

15. **PubSub 订阅的幽灵泄漏 (Ghost Event Listeners)**
    - **审查点**：Flet 在路由切换后，旧对象的事件侦听并未销毁。在 `_on_mount` 中执行了 `pubsub.subscribe()`，**绝对必须**在紧接着的 `_on_unmount()` 中执行 `pubsub.unsubscribe_topic()`。
16. **滥用异步重绘导致的 AssertionError**
    - **审查点**：在异步方法（如 `await asyncio.sleep` 或网络请求结束）后执行重绘时，如果用户在此期间切走了页面会触发底层红屏断言！必须添加组件依然挂载的安全校验逻辑：`if self.page:` 再执行 `.update()`。
17. **路由栈的迷失与 views.clear() (Routing Sprawl)**
    - **审查点**：0.28+ 侧边栏切换顶级菜单时，切忌持续往 `page.views.append()` 里压入同级页面！必须查验真正的平级切换路由是否执行了 `page.views.clear()`。
18. **生命周期分离界限：__init__ 滥用**
    - **审查点**：严禁在 `Container` 的 `__init__` 函数里获取屏幕尺寸 `self.page.window_width` 或执行耗时逻辑，因为此时 `self.page` 为空，必须全部迁移到 `_on_mount(e)`中。
19. **UI 主线程的重度堵塞 (UI Thread Blocking)**
    - **审查点**：超过 50ms 的 IO 与 CPU 计算，**不允许**直写在点击事件函数中。必须使用 `page.run_task()` 委托后台执行。
20. **高频重绘与双生循环 (Double Update Race)**
    - **审查点**：在 `virtual_table` 或长列表循环构建组件时，逻辑有没有先在内存中聚合完所有的 `control`，再在最后一次性触发单一的 `.update()` 控制频次？
21. **巨量 DOM 渲染击穿 (DOM Explosion)**
    - **审查点**：严防开发者使用 `ListView` 或 `Column` 一次性循环塞入超过 200 个带图表的 `ListTile`。对于量化展示结果，必须强制使用 `VirtualTable`（`ui/components/virtual_table.py`）做可见区域切割（Windowing），只渲染视口内可见行。
22. **多语言闭环：消灭硬编码中文 (Hardcoded Strings)**
    - **审查点**：在前端视图中查杀所有形如 `ft.Text("选股器")` 的硬汉字直写，必须强制包裹为 `I18n.get("screener")`。
23. **双语词典对齐 (Dictionary Symmetry)**
    - **审查点**：在向 `ui/i18n.py` 补充新的 i18n 键时，是否同时在 `en` 和 `zh` 两个字典块中都添加了映射？如果发生落单漏写，用户切换语言时会触发 `KeyError` 导致界面白屏崩溃。
24. **多语言字典冗余 (i18n Duplication)**
    - **审查点**：在国际化资源字典（如 `ui/i18n.py`）中，严禁出现多个不同的 Key 指向完全相同的翻译文本（例如 `"btn_ok": "确定"` 与 `"dialog_confirm": "确定"` 同时存在）。这会造成翻译文件体积膨胀与跨语言维护灾难。必须复用基础语义词条！

---

## 🛡️ 防区四：AI 混合调用边界 (AI Integration)
*目标：控制不可控的大模型引擎，防御 Token 与接口爆破。*
**重点文件**: `services/ai_service.py`, `services/local_model_manager.py`, `strategies/ai_strategy.py`, `strategies/ai_mixin.py`

25. **漏斗筛选口径过大 (Funnel Leak)**
    - **审查点**：批量送入大模型的前提是定量初筛已被压到极小范围。如果放宽阈值导致 1000 只股票被并发打满调用 OpenAI API，会被秒封。必须在送往 AI 之前设置 `head(50)` 最大阀值控制。
26. **幻觉结构化容错 (JSON Fallback)**
    - **审查点**：大模型极易输出被 `\`\`\`json` 定界符包裹或带幻觉格式的结构。解析代码是否被安全的 `try json.loads` 包裹，并在解析失败时给予默认打分 0 的保底降级？绝不能因一只股票的 JSON 残损直接让批处理崩溃。
27. **Prompt 注入防范 (Context Injection)**
    - **审查点**：如果向 Prompt 喂入外部新闻标题，是否使用了坚固的 `<news>` 等分隔符圈养上下文，防御越权命令干扰 AI 既定逻辑。

---

## 🛡️ 防区五：全局系统韧性与容灾 (Global Resilience)
*目标：在崩溃中断后维持数据完整与网络免疫。*
**重点文件**: `main.py`, `data/tushare_client.py`, `data/database_manager.py`, `utils/`

28. **[关键] 危险的单例生命周期 (Singleton Dirty State)**
    - **审查点**：本项目依赖 8 大核心单例对象（如 `DataProcessor`、`CacheManager`、`AIService` 等）。在修改它们的 `__init__` 函数时，有没有遗漏 `if self._initialized: return` 的防重入墙？否则会导致核心组件被多次隐式构造产生连接池分裂。
29. **[关键] 优雅关闭与资源回收 (Graceful Shutdown)**
    - **审查点**：在新增轮询任务、子进程、或后台队列时，有没有在系统退出回调中同步注册销毁指令？例如有没有调用 `scheduler_service.stop()` 等待 IO 退潮？不闭合的文件句柄会锁死 SQLite 的物理数据库文件长达几个小时。
30. **[关键] 配置文件的原子写入 (Atomic Config Write)**
    - **审查点**：所有针对 `user_settings.json` 的修改，**只有一条路可走**：调用 `ConfigHandler._save_json_atomically()` 方法（先写临时文件，然后利用 OS 原子更名 rename），绝对禁止使用原生 `open(path, "w").write()` 导致断电时文件变成空壳烂尾。
31. **[关键] 配置结构与句柄断层 (Config Structure Mismatch)**
    - **审查点**：任何在 `config_handler.py` 的默认配置结构 (`DEFAULT_CONFIG`) 或数据类中新增、修改或删除的字段，**必须**同步核对实际产出的 `user_settings.json` 文件结构以及相关的 UI 设置页绑定。严禁出现代码中取不到配置抛 `KeyError`，或是配置文件里的旧字段成为僵尸节点的情况！
32. **防网络封禁的断点续传与自愈 (Rate Limit & Healing)**
    - **审查点**：查验 Tushare API 调用处是否配置了 `ConfigHandler.get_sync_retry_count()` 对应的重试次数以及 `get_sync_request_delay()` 对应的请求间隔？一次拉取 5 年 K 线时是否按日期切片执行？这样才不用惧怕第 1000 天挂掉而必须从头再来。
33. **安全秘钥的防泄露 (Credential Leakage)**
    - **审查点**：绝不准许在 Exception traceback 以及日终报告中明文打印出含有 Tushare 官方 Token 或是第三方 LLM Proxy Base_URL 的请求原文内容！项目已使用 `keyring` 与 `SecurityManager` 加密存储凭证，审查新代码时必须确认没有绕过这些安全层。
34. **隐式时区与时间转换 (Timezone Consistency)**
    - **审查点**：强制代码中使用并且只使用 `Asia/Shanghai` 时区进行交易日比对与本地时间戳生成。所有获取当前时间的调用，**严禁使用原生的 `datetime.datetime.now()`**，必须统一使用项目中封装的 `utils.time_utils.get_now()`，防止在海外云服务器上出现时钟偏移。导出或缓存文件命名时，强烈建议时间戳精确到秒 `%Y%m%d_%H%M%S` 以防同日高频覆盖。
35. **[关键] 彻底的文件名安全与防路径穿越 (Path Traversal & Sanitization)**
    - **审查点**：在动态生成本地文件路径（如导出 CSV、日志转储或 prompt 记录）时，绝不允许未经清洗直接凭借外部动态变量（如股票代码、策略输入名）拼接路径。必须强制使用正则表达式 `re.sub(r'[<>:"/\\|?*]', '_', name)` 过滤 Windows 预留特殊字符，彻底防御高级路径穿越（Path Traversal）漏洞与写入溢出带来的静默崩溃。
36. **不可预见的系统兜底 (Global Exception Catching)**
    - **审查点**：后台 Thread/Task 如果直接触发 Crash，是否向 UI 发送了更新消息？UI 如果一直等不到执行完的 Future，进度条动画将会死死卡在永久等待状态。
37. **日志规范与纪律 (Log Discipline)**
    - **审查点**：① 日志级别不得滥用——`WARNING` 只用于可自愈的降级，`ERROR` 只用于需人工介入的故障，严禁把常规分支写成 WARNING 刷屏。② 关键操作日志必须携带上下文参数（如 `ts_code`, `trade_date`），否则日志回查时无法定位到具体股票。③ 严禁在大循环内逐条调用 `logger.info()`，应在循环外一次性汇总输出，防止日志文件被撑爆。
38. **并发安全与协程隔离 (Concurrency Safety)**
    - **审查点**：① 使用 `asyncio.gather()` 时必须检查 `return_exceptions=True`，否则单个子任务异常会导致其余所有任务的结果被丢弃。② 多协程/多线程同时操作共享的 `dict`、`list` 或 Polars DataFrame 时，必须引入锁（`asyncio.Lock` 或 `threading.Lock`）保护临界区。③ 回调函数中不得直接修改 ViewModel 的共享状态而不经过事件总线。

---

## 🛡️ 防区六：量化系统代码坏味道 (Code Smells in Quant)
*目标：维持核心链路极致精简，拒绝技术债堆积如山。*
**重点文件**: `strategies/*.py`, `data/daos/`, `data/sync_strategies/`, `ui/views/`

39. **策略魔术数字硬编码 (Magic Numbers in Alpha)**
    - **审查点**：在选股逻辑 `.filter(pl.col('amount') > 10000000)` 中凭空冒出来的标尺，一律退回。必须被提取到类常量或是注入到随 UI 连动的 `context.get("params")` 定义中，确保策略调整有溯源。
40. **神级函数肥胖症 (God Function)**
    - **审查点**：一个包含"拉数据"、"洗量纲"、"入库"、"UI 反馈进度条"的巨兽方法（常常超过 80 行）极难测试且会阻塞单元测试，必须勒令将其拆分退化为单一职责。
41. **强塞在 UI 代码里的原生计算 (Business Logic Leak)**
    - **审查点**：Flet 前端按钮的回调函数里，坚决不能出现关于 DataFrame 合并、排序以及 SQLite 查询的计算逻辑。所有的繁重计算职责必须推迟并委托给 `View` 背后的 `ViewModel`，保证 V 和 VM 之间只有极其弱的信使通信。
42. **安静吞咽的异常 (Silenced Exceptions)**
    - **审查点**：捕获所有的错误却写下一个干秃秃的 `pass` 是量化体系中最具毁灭性的恶心代码。如果确实打算吞并降级，必须 `logger.warning('Reason')` 并附加详细的入参 `Context`，不然就是任凭残缺数据污染全链路选股池！
43. **重复代码与复制粘贴 (DRY Principle Violation)**
    - **审查点**：如果在两个策略或 DAO 中发现了超过 10 行以上结构高度雷同的逻辑（如几乎一样的 DataFrame 转换、指标计算），必须将其重构并上卷到基类或专属工具方法中。拒绝复制粘贴编程。
44. **僵尸代码死海 (Dead Code & YAGNI)**
    - **审查点**：任何不再被使用的废弃函数、未引用的 Import、或是为了"未来大干一场"而提前写下的复杂且未激活的架构代码（YAGNI），必须无情剔除，严禁将代码库当作历史垃圾桶。
