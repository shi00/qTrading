# D03 数据库模型、DAO、SQL 与事务检视

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责31文件：31完整语义阅读、0完成有限来源核对、0待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共6项；其中本次新增接受1项缺陷、0项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**结论：已核实局部问题，整体无法判断；任务 1.1 保持 blocked，不输出通过。**

- 主责31个文件、15,353行已全部直接完整阅读；续读补齐了此前4个文件的未读区间。历史阶段27完整/4部分与当前31完整/0部分分别保留在 [D03-evidence.json](D03-evidence.json)，不把续读结果追认成此前已读。
- 标准库 AST 对全部 31 文件建立 339 个函数/方法入口和 **50 张 ORM 表**的结构对照；50 张表均有数据字典登记，额外 `alembic_version` 为 Alembic 自管对象。结构解析不是完整语义审读，也不证明真实库一致。
- 下列五个问题有源码可达链、契约与反证；最高严重度 **P2**。运行环境未对齐且没有强隔离，不运行pytest、驱动连接、迁移或故障注入；不得把标准库反例称为数据库测试。

| 类别 | 数量 | 说明 |
| --- | --- | --- |
| 确定缺陷 | 5 | 宏观混频、URL编码、文本类型、学习快照口径、同run重放状态 |
| 场景遗漏 | 0 | 不为凑数重复登记缺陷 |
| 测试缺口 | 5 | 分别关联上述缺陷；仅说明实际读取到的断言不足，不声称全库无测试 |
| 待确认问题 | 1 | sync keys 与 ORM 键不一致的实际消费语义 |
| 待验证风险 | 3 | 真实事务/取消、统计与提交边界、账本恢复与结算锁序 |
| 范围外风险 | 0 | 没有展开其他维度正式检视 |
| 非阻断建议 | 2 | 写入返回计数、概念前缀匹配契约 |

不统计已登记技术债为新缺陷；没有专门可利用漏洞扫描或安全通过结论。本次只交付发现和方案，**没有实施修复**。

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D03-evidence.json](D03-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

### 2.1 主责及阅读边界

已完整直接阅读：

- [data_dictionary.py](../../data/data_dictionary.py)、[persistence 包入口](../../data/persistence/__init__.py)、[app_state_service.py](../../data/persistence/app_state_service.py)、[DAO 包入口](../../data/persistence/daos/__init__.py)；
- [BaseDao](../../data/persistence/daos/base_dao.py)、[BacktestDAO](../../data/persistence/daos/backtest_dao.py)、[ExpressDao](../../data/persistence/daos/express_dao.py)、[FinancialDao](../../data/persistence/daos/financial_dao.py)、[HolderDao](../../data/persistence/daos/holder_dao.py)、[MacroDao](../../data/persistence/daos/macro_dao.py)、[MarketDao](../../data/persistence/daos/market_dao.py)；
- [PledgeDetailDao](../../data/persistence/daos/pledge_detail_dao.py)、[ShareFloatDao](../../data/persistence/daos/share_float_dao.py)、[StkHoldertradeDao](../../data/persistence/daos/stk_holdertrade_dao.py)、[StkLimitDao](../../data/persistence/daos/stk_limit_dao.py)、[StockDao](../../data/persistence/daos/stock_dao.py)、[申万及名称历史 DAO](../../data/persistence/daos/sw_industry_dao.py)、[SyncDao](../../data/persistence/daos/sync_dao.py)、[TopInstDao](../../data/persistence/daos/top_inst_dao.py)、[WatchlistDao](../../data/persistence/daos/watchlist_dao.py)；
- [DataExplorerQueryClient](../../data/persistence/data_explorer_query_client.py)、[DatabaseConfigService](../../data/persistence/db_config_service.py)、[override_db_url](../../data/persistence/db_url_override.py)、[engine_provider](../../data/persistence/engine_provider.py)、[MetaDataManager](../../data/persistence/metadata_manager.py)、[models.py](../../data/persistence/models.py)、[db_utils.py](../../utils/db_utils.py)。
- 续读完整补齐 [AIUsageDao](../../data/persistence/daos/ai_usage_dao.py)、[QuoteDao](../../data/persistence/daos/quote_dao.py)、[ScreenerDao](../../data/persistence/daos/screener_dao.py)、[ReviewManager](../../data/persistence/review_manager.py)。

续读记录（原始已读段不丢失，当前合并后均覆盖1至EOF）：

| 文件 | 之前读取行段（1-based） | 本次直接补读行段 |
| --- | --- | --- |
| [ai_usage_dao.py](../../data/persistence/daos/ai_usage_dao.py) | 1–145 / 共2,098行 | 146–2,098；输出被截断的1280–1295另行补读 |
| [quote_dao.py](../../data/persistence/daos/quote_dao.py) | 1–145 / 共1,777行 | 146–1,777 |
| [screener_dao.py](../../data/persistence/daos/screener_dao.py) | 1,032–1,170 / 共1,170行 | 1–1,031；被截断的140–160、301–317另行补读 |
| [review_manager.py](../../data/persistence/review_manager.py) | 1–140 / 共1,901行 | 141–1,901；被截断的415–425、695–705另行补读 |

此前三个后台文件组调查未能取得可用返回，没有重启同目标代理，也没有把委派视为完成。后续均由主任务直接阅读补齐；没有用AST摘要代替源码阅读。

原始 0.1 清单另存 [scope-0.1.csv](scope-0.1.csv)，与冻结时 SHA-256 一致；[scope.csv](scope.csv) 只更新 D03 的阅读状态/依据，其他维度保持 pending。源文件未改变。

### 2.2 权威依据与非目标

- [DAO 正本](../../docs/patterns/dao-pattern.md)、[项目宪法](../../CLAUDE.md) 的 R2/R4/R5/R8/R12/R17/R21/R22；
- [检视协议](../../docs/reviews/ai-review.md)、[项目 Profile](../../docs/reviews/review-profiles/project-profile.md)、[数据库 Profile](../../docs/reviews/review-profiles/database-migration.md)；
- [历史债务](../../docs/debt/known-technical-debt.md)、[例外注册表](../../docs/governance/exceptions.yml)、[回测正确性](../../docs/patterns/backtest-correctness.md)；
- [验证准入矩阵](validation-matrix.md)：当前仅允许静态取证。未批准秘密实读、依赖安装、生产/测试库访问、外发或破坏性操作。

没有 Git 历史，所有问题的引入关系为 **unknown**，不归因到某个提交。D04 迁移、D06 全链时点/量纲、D22 独立盲复核不由本报告替代。

## 3. 模型与场景

### 3.1 表、键与方法对照

逐表模型/主键/业务唯一键/数据字典/sync keys/DAO 入口见 [D03-table-map.md](D03-table-map.md)，具体 upsert 参数及 conn/suppress_errors 传递见 [结构证据](D03-evidence.json) 的 `method_map.upsert_shapes`。

已核对的关键区别：

- 一般行情/财务表：`get_model_pk_columns` 派生冲突键，不复制另一份手写键。
- 自增主键不等于业务冲突键：[watchlist](../../data/persistence/daos/watchlist_dao.py) 用 ts_code 唯一键；[market_news](../../data/persistence/daos/market_dao.py) 用 content_hash+publish_time；[screening_history](../../data/persistence/daos/screener_dao.py) 用日期+策略+代码+run_id；thinking 用 history_id 唯一键；[backtest_results](../../data/persistence/daos/backtest_dao.py) 用 run_id 唯一键。不能只与 ORM 自增 id 不同就报冲突键错误。
- [get_model_columns](../../data/persistence/models.py) 合并排除时间戳和 computed 字段；已读代码中的部分旧注释与其现行行为不符，以实现为准。
- [JSONB 信任元数据](../../data/persistence/models.py) 的 `none_as_null=True` 与 `null_protected` coalesce 协作；SQL NULL、JSON null、空数组、合法零不能混算。

### 3.2 事务与 disposed 边界

| 路径 | 已观察行为 | 尚不能证明 |
| --- | --- | --- |
| BaseDao 单条读/写 | 入口 `_check_engine`，维护事件等待后复查；R2取消上抛 | 获取连接后与 dispose 竞争的驱动实况 |
| `_guarded_begin(conn)` | 显式conn复用调用者事务，否则 engine.begin；不自行提交共享conn | 调用者一定在正确生命周期内提交/回滚 |
| `_save_upsert` | 归一化线程池await后复查；共享conn分块串行；共享错误不按suppress吞没 | 真实约束、锁、驱动执行方式与中断结果 |
| ≤20,000行、无共享conn | 一次 engine.begin 包含全部500行分块 | 真实中段失败回滚证据未跑 |
| >20,000行、无共享conn | 每500行独立事务，前块可已提交；不是全批原子 | 重跑幂等与上游完整性状态的真实端到端结果 |
| 概念替换 | 已读 StockDao 对删除与插入复用 guarded事务 | 真实失败/取消时无部分新旧混合 |
| 研究记录+thinking | 两步写，ReviewManager保存调用没有将其包为共享事务；存在失败传播与重试边界 | 仍需真实故障注入验证主记录成功/思考未存的可恢复性，不据两步形状直接判事故 |
| app_state | 不继承BaseDao；None早返、普通错误降级；max语句带条件 | 不直接判R5新缺陷：调度调用方已有is_disposed预检，见反证 |

### 3.3 场景矩阵

| 场景 | 已有源码/断言证据 | 本轮结果或缺口 |
| --- | --- | --- |
| 正常/重复业务键 | ORM唯一约束、get_model_pk_columns、_save_upsert重复计数 | 静态已核对；没有真实并发冲突测试结果 |
| 空表/缺列/NULL/合法零 | BaseDao补列、主键缺失拒绝；JSONB SQL NULL保护 | 语义已读，真实DB及驱动类型尚未运行 |
| 500行边界与半批失败 | BaseDao共享/短批/长批三分支 | 需E3验证；不可声称全批原子 |
| 维护/取消/dispose | await后复查、CancelledError分支；[已有单测](../../tests/unit/test_base_dao.py)的维护期间dispose场景 | 只读取断言，未运行 |
| 宏观混频数据 | 合并器、全局LIMIT2、上下文非空GDP选行 | 标准库反例确证选行缺陷，见F01 |
| URL含内部空格 | build_url表单编码、真实连接路径、URI decoder | 标准库反例确证编码缺陷，见F02 |
| 日期形新闻文本 | normalize原样文本、MarketDao参数、BaseDao无类型转换 | 标准库反例确证参数类型破坏，见F03；实际驱动错误未跑 |
| 历史回放/质量警告 | models JSONB、完整Screener/ReviewManager、backtest quality字段 | 静态已读，动态及R20/R21/R24独立盲复核未完成 |
| 同run重放/学习样本 | 完整保存、回填候选池与学习统计链 | 两组标准库反例，见F04/F05；真实SQL未跑 |
| 重启恢复/账本并发 | 完整月锁、attempt修订、恢复/封口实现 | 锁序交叉候选与E3缺口，仍blocked |

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D03-F01 · P2 · 全局“最近两行”不能保证同时返回月度数据与 GDP

- **类别/置信度**：确定缺陷；高，完整静态可达链及可重复标准库选行反例。不是性能猜测，也不是“GDP源没同步”的假设。
- **位置**：[MacroDao.get_macro_economy_latest](../../data/persistence/daos/macro_dao.py)（定义起点142）、[CacheManager.get_macro_economy](../../data/cache/cache_manager.py)（970）、[宏观上下文构建器](../../strategies/ai_context/macro.py)（22）。
- **契约**：DAO明确承诺最新月度行+最新GDP行；上下文消费者分别定位非空m2_yoy及gdp_yoy。两类数据在同表独立行存储。
- **触发**：截至决策日已存在一条有效GDP记录，但在其后又有至少两个月度发布记录。
- **当前/预期**：现为按publish_date全表降序后limit(2)，取两条月度行；预期分别取两种频率各自最新可见记录，而不是简单取总共两条。
- **影响**：GDP、三大产业同比段落即使库内有有效可见记录也不进入AI研究上下文；不把影响扩大成“必然改变评分/收益”。
- **逐跳证据**：
  1. [宏观同步合并器](../../data/sync/macro.py) 将GDP季度末行与月度行concat，分别设置publish_date；
  2. DAO只要求publish_date非空/不晚于as_of，然后 `.order_by(...desc()).limit(2)`；
  3. CacheManager薄委托，没有额外补查；
  4. 上下文仅在返回数据找到非空gdp_yoy时才生成GDP段落。
- **反例**：GDP发布4月20日，月度发布5月16日、6月16日；as_of为6月20日。当前两条是6月、5月月度，GDP可见但被排除；[证据JSON](D03-evidence.json) 保存输入与实际选行。
- **已尝试反证**：PIT过滤不解决混频；消费者只过滤已有返回值，不会再查GDP；[MacroDao现有测试](../../tests/unit/test_macro_dao.py) mock直接返回DataFrame，检查WHERE/非空，未在所读段落构造三行筛选；[AI上下文测试](../../tests/unit/test_ai_mixin.py)预先提供“月度+GDP”正确两行，无法证明DAO产生正确两行。没有声称全库不存在相关测试。

**最小修复方案**

1. 在该DAO内构造“共同可见性条件”（publish_date非空，as_of适用时不晚于决策日），两类分支共用。
2. 月度分支以m2/cpi/ppi等实际月度列非空识别；GDP分支以gdp/gdp_yoy等GDP列非空识别。各自按publish_date、period明确降序取1行。
3. 使用SQLAlchemy Core子查询/UNION合并，或两次有界查询后合并；优先单语句，保证同一快照并保持最多两条、既有返回列和None语义。不要通过把LIMIT改大掩盖问题。
4. 不变更表/迁移，不补造缺失宏观值。GDP真正不可得时仍缺失，不重复返回另一条月度作为GDP替身。

**回归与验收**

- 新增真实查询样本：两条新月度+一条旧但可见GDP，必须含最新月度与该GDP。
- 将as_of提前到GDP公告前，必须无GDP；月度缺失/GDP缺失分别测，非空值不能互填。
- 同publish_date使用period稳定排序；核对消费者同时保留两个段落。
- 将来E2/E3合并运行 `python -m pytest -n 0 tests\unit\test_macro_dao.py tests\unit\test_ai_mixin.py -v --tb=short`，并补E3真实查询；本轮未运行。
- **风险/回退**：识别列集合须与同步实际字段对齐，避免合法月度缺m2时被全剔除；仅回退该DAO查询，无存量数据回填。

### D03-F02 · P2 · 连接 URL 使用表单编码，内部空格被当成字面加号

- **类别/置信度**：确定缺陷；高，URL编码/解码反例与实际配置→引擎路径。没有尝试真实认证或读取凭据。
- **位置**：[DatabaseConfigService.build_url / parse_url](../../data/persistence/db_config_service.py)（394 / 424）、[配置URL解析入口](../../utils/config/db.py)（285）、[EngineManager](../../data/cache/engine_manager.py)。
- **触发**：合法数据库用户名或凭据包含内部空格；用户名示例为纯合成 `audit user`。`quote_plus` 是查询表单编码，不是URI userinfo的空格编码。
- **当前/预期**：`quote_plus("audit user")` 得 `audit+user`；URI userinfo解码保留`+`，不还原空格。应编码为 `%20`。项目自身parse_url用unquote_plus，可能自测往返正确却与驱动不一致。
- **影响**：连接测试使用分字段asyncpg.connect可成功，但持久配置重建URL或run_migrations/create_async_engine路径使用了不同用户名/凭据，导致认证失败或连接错误；不宣称每个数据库配置都会受影响。
- **逐跳**：用户配置组件 → get_db_url/build_url → EngineManager.create_async_engine；run_migrations也直接使用build_url返回串。没有后续把form编码转为URI编码的步骤。
- **反证**：普通字母、`@/:`编码样本不触发；现有特殊字符测试仅核对`@/:`；parse_url内部unquote_plus的自我往返无法排除驱动差异。已只读核对本机SQLAlchemy URL与asyncpg DSN解析使用unquote；本机版本不是项目锁定基线，**版本相关真实认证仍需隔离验证**，但编码错误由标准URI规则独立成立。

**最小修复方案**

1. 以SQLAlchemy `URL.create`构造连接地址，并在既有返回str边界正确渲染；或仅将userinfo编码改为 `quote(..., safe="")`，解析改为 `unquote`。不改连接字段存储、Keyring/加密来源或日志脱敏。
2. 优先统一构造/解析，避免build与parse各自保持表单语义。IPv6主机、数据库路径字符由URL库正确处理，勿再手工拼出第二套格式。
3. 检查旧配置只保存组件还是也可直接保存旧URL；已有组件可按新实现重建，不能静默猜测外部提供URL中的`+`本意为空格还是加号。

**回归与验收**

- 合成用户名含内部空格、字面`+`、非ASCII、`@/:`分别构造；使用真实URL解析器断言原值还原，而非只测项目build→parse自身循环。
- 不在测试中硬编码密钥；密码覆盖用环境注入/现存安全测试fixture，错误输出须脱敏。
- 将来E2运行 `python -m pytest -n 0 tests\unit\test_db_config_service.py -v --tb=short`，E3再验证分字段与构造URL身份一致。无认证资源时保持未验收。
- **风险/回退**：保持旧外部URL字面`+`语义；回退仅构造解析实现，不写/迁移秘密值。

### D03-F03 · P2 · 通用参数转换把合法日期形文本变成 date 对象

- **类别/置信度**：确定缺陷（参数类型破坏）；高，完整静态可达路径和标准库类型反例。具体驱动异常文字、是否导致整轮重试/阻塞尚未运行，不能当已测结果。
- **位置**：[BaseDao._convert_param_for_asyncpg](../../data/persistence/daos/base_dao.py)（996）、[_write_db](../../data/persistence/daos/base_dao.py)（556）、[MarketDao.save_market_news](../../data/persistence/daos/market_dao.py)（161）。
- **触发**：合法外部新闻正文恰为ISO日期文本，例如合成 `2026-04-29`；文本字段未规定禁止此类值。也可影响其他经过同一通用转换器的文本参数。
- **当前/预期**：转换器不看SQL列型，对每个10位日期形字符串转为datetime.date；但目标 [MarketNews.content](../../data/persistence/models.py) 是String。应保持正文字符串不变，日期只在明确日期边界归一。
- **逐跳**：[新闻订阅初始同步](../../services/news_subscription_service.py) 的_fetch_and_notify → [normalize_news_item](../../data/cache/cache_manager.py)只对content strip、不将日期形文本排除 → save_market_news第一个参数为content → _write_db对全部参数调用通用转换器 → content变成date，与目标TEXT契约冲突。
- **影响**：日期形文本不能按原始文本稳定绑定/落库；严格文本编码路径可能报类型错误。没有声称上游曾实际推送该样本、或断言已在真实库观察到丢新闻。
- **反证**：publish_time在normalize后是datetime，不能据此推论content也应转date；content_hash仍按原字符串计算，不会逆转参数类型。现有[参数转换测试](../../tests/unit/test_base_dao.py)直接断言ISO文本应转date，没有同时区分目标列型，因而不是类型安全反证。

**最小修复方案**

1. 通用读写绑定层默认原样传递str，不再根据外观猜日期；保留date/datetime的既有原生传递。
2. 先静态盘点依赖ISO隐式转换的DAO日期参数，逐个在已知Date列入口调用 `_to_db_date`；DateTime保留时间信息，用项目既有UTC-naive规范入口，不经date截断。
3. 这是跨DAO行为修复，不能只在save_market_news把date再str化；需同时更新通用转换器单测及所有依赖方，避免修了正文却破坏日期查询。

**回归与验收**

- TEXT参数 `"2026-04-29"`、`"2026/04/29"`、ISO时间文本应保留str；明确Date列入口传入ISO/8位字符串均变成date；DateTime入口保留时分秒。
- mock连接捕获save_market_news最终params，第一项必须与原始content逐字相同；E3插入后读取content与hash一致。
- 将来E2合并运行 `python -m pytest -n 0 tests\unit\test_base_dao.py tests\unit\test_market_dao.py -v --tb=short`，E3补实际类型绑定；本轮未运行。
- **风险/回退**：调用方可能依赖隐式日期转换，必须完成同类入口盘点才改默认；按“日期边界显式化→更改默认”分两步，不能用吞错/空表降级掩盖漏改。

### D03-F04 · P2 · 学习样本与总体统计没有一致执行“最近一次运行”口径

- **类别/置信度**：确定缺陷；高，完整查询、调用方与两组关系代数反例。变化关系unknown，不归因到某次提交。
- **位置**：[ScreenerDao.get_learning_context / get_learning_context_stats](../../data/persistence/daos/screener_dao.py)、[ReviewManager.get_learning_context_with_meta](../../data/persistence/review_manager.py)。
- **契约**：[ADR-0010](../../docs/adr/0010-screening-history-append-only.md)要求AI学习按日期+策略+代码取最近运行；方法注释称统计与样本口径一致；ReviewManager用总体 `total >= REVIEW_MIN_SAMPLE`（30）决定是否注入尾部样本。
- **触发A**：同一日期/策略/股票的旧run已完成为WIN，新run已完成为LOSS，均有AI分数、alpha和T+5。
- **当前/预期A**：样本查询先WHERE指定WIN/LOSS及已完成等条件，再DISTINCT ON取最大id；分别调用WIN和LOSS时，旧WIN和新LOSS都会被取出。同一研究键应先取最近快照，再决定属于哪个标签桶；旧WIN不应复活。
- **触发B**：同一个研究键有30次已完成AI重跑，而独立去重后的研究键只有1个。
- **当前/预期B**：stats对原始表COUNT/AVG/percentile，没有与样本相同的最近快照子查询；`total=30`令ReviewManager判断充足并注入样本。按显式最近快照契约应total=1、不足30；不能用调参次数代替独立研究样本。
- **影响**：历史快照同时作为正反例、总体中位数/胜率受重跑频率加权、充足门槛被重跑次数穿透；不声称必然导致具体模型分数或投资收益变化。
- **逐跳/反证**：DAO查询 → stats.total → ReviewManager.sufficient → WIN/LOSS两次查询 → XML和sample_ids。调用方既不去重也不重算total；已有单测检查DISTINCT ON/id DESC存在或mock返回100条统计，未验证过滤位于去重的前后顺序及真实数据集合。源码中“已有DISTINCT”不能推翻WHERE先筛选的问题。
- **最小方案**：在本DAO内复用同一“最近研究快照”子查询/CTE，以三键+id DESC确定快照，WIN/LOSS标签过滤放在该关系外；样本与stats共用同一范围及资格关系，stats对其COUNT/胜负/均值/中位数。不删除append-only历史、不改变对外返回字段。对“最新run未完成或无AI时是否可退回最近已完成AI快照”的边界先按ADR/产品契约确认并一致实现，本报告不额外创设该行为。
- **回归输入→期望**：两个均已完成且有AI的旧WIN/新LOSS只进入新LOSS，反向同理；同键30个合格run统计为1且sufficient=false；30个不同研究键方达到现有门槛；as_of排除未来日期。未完成/无AI分支按确认后的同一资格规则验证，不自行填默认。
- **隔离验证**：在现有 [DAO单测](../../tests/unit/test_screener_dao.py)、[ReviewManager单测](../../tests/unit/test_review_manager.py)补断言，并在E3按真实PostgreSQL执行多run关系；候选命令 `python -m pytest -n 0 tests\unit\test_screener_dao.py tests\unit\test_review_manager.py -v --tb=short`。本轮仅标准库反例，未执行SQL。
- **风险/回退**：改后样本数和历史提示可能减少，这是修正快照口径而非数据丢失；保留研究原始行、记录基线差异，回退只改查询。不得“修复”为降低30门槛。

### D03-F05 · P2 · 同一run幂等重放会把已推进的复盘状态重置为PENDING

- **类别/置信度**：确定缺陷；高，保存字段、通用upsert更新集、模型默认值与全部候选池谓词已直接核对。实际数据库重放未运行，状态投影反例不冒充驱动测试。
- **位置**：[ScreenerDao.save_screening_results](../../data/persistence/daos/screener_dao.py)、[BaseDao._save_upsert](../../data/persistence/daos/base_dao.py)、[ReviewManager.save_results](../../data/persistence/review_manager.py)、[ScreeningHistory](../../data/persistence/models.py)。
- **契约/触发**：保存方法明确声明同四键重试幂等；ReviewManager允许调用方传相同run_id。已有 `(trade_date,strategy_name,ts_code,run_id)` 行完成T+1为T1_DONE，再重放该run的原筛选结果。
- **当前/预期**：保存始终令 `row["review_status"]=PENDING`，该列不是computed且被columns包含，BaseDao冲突UPDATE会写入它；t1_pct/alpha/标签等computed列却不更新。因此原状态回退，衍生值仍旧。幂等重放应保留既有复盘进度；只有首次插入使用模型server_default=PENDING。
- **实际影响路径**：若重放时该记录已移出run_review近10交易日窗口，且原先t1_pct非空、t5_pct为空：重放后PENDING使T+5池要求T1_DONE不匹配，T+1池及过期清理又要求t1_pct为空不匹配，标签补池也要求T1_DONE；该记录可能长期停在带T+1值的PENDING，不再得到T+5回填。COMPLETED重放同样会产生“PENDING带旧定稿值”矛盾。无需假设新的随机run_id发生碰撞。
- **反证检查**：四键唯一约束只保证同run定位同一行，不保护列状态；BaseDao的computed排除只保护衍生值，不保护review_status；[名为test_thread_pending_labels_not_reset的单测](../../tests/unit/test_screener_dao.py)实际只mock_save_upsert并断言输入为PENDING，没有构造已存在行再冲突；[集成首次保存用例](../../tests/integration/test_dao_business.py)只验证新行PENDING，不是重放反证。
- **最小方案**：在保存筛选快照时从 `get_model_columns(...exclude=...)` 明确排除review_status，移除强制赋PENDING；首次插入由既有server_default=PENDING生成，冲突UPDATE不写它。不要把它全局标为computed来影响其他DAO契约，也不要清空旧衍生字段掩盖回退。
- **回归输入→期望**：首次插入PENDING；同四键重放时T1_DONE/COMPLETED/UNTRADABLE/EXPIRED均保持；T1_DONE且旧日期的记录重放后仍在T+5候选池；新run_id仍新增PENDING且不覆盖旧run；可信度/思考等快照重放语义保持。
- **验证/回退**：E2补DAO生成UPDATE集检查，E3执行“插入→T1回填→同run重放→读取候选池”事务用例；沿用F04候选单测命令并补 [test_dao_business.py](../../tests/integration/test_dao_business.py)。存量矛盾行不得仅凭PENDING就全量改状态；应按t1/t5/alpha/标签和现有业务状态规则识别、先备份、独立数据修复审批。代码回退只涉及保存列集合，不做全表状态覆盖。

### 续审独立接受的发现（当前）

#### TP11-F02 · P2 · 复盘关键行情读取显式继承宽容模式，失败页变为空页后仍正常回填并继续清理

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestQuoteDaoGetDailyQuotes.test_large_code_list_chunked](../../tests/unit/test_quote_dao.py)；[_make_mock_rm / _submit_and_run / TestBuildReviewBackfillJob](../../tests/unit/test_review_backfill_job.py)；[QuoteDao.get_daily_quotes](../../data/persistence/daos/quote_dao.py)；[BaseDao._read_db / chunked_in_query / _chunked_execute](../../data/persistence/daos/base_dao.py)；[ReviewManager.backfill_t1_returns / backfill_horizon_returns](../../data/persistence/review_manager.py)；[_review_backfill_logic](../../services/scheduled_jobs/review_backfill.py)；[两个回填候选查询的limit](../../data/persistence/daos/screener_dao.py)。
- **契约**：BaseDao.chunked_in_query/_chunked_execute 明确以页失败显式失败、丢弃部分结果为读路径契约；_read_db 文档要求关键路径用 suppress_errors=False 区分查询失败和无数据。复盘缺行情可保留 NULL 重试，但已知查询失败不应伪装成停牌/无行情后继续完成及过期清理。
- **触发**：回填候选含 600 个不同代码，源库都有成熟 T+1 行情；默认 500 代码一页，某一页发生普通 DB 查询错误，其他页成功。候选 DAO 默认 limit=2000，故无需突破上游限额。按代码页确定失败，而不依赖并发调用先后。
- **可达链/当前行为**：ScreenerDao.get_unfilled_t1_predictions / get_unfilled_horizon_predictions 的 limit=2000 允许超过 500 个唯一代码
- **可达链/当前行为**：_review_backfill_logic -> backfill_t1_returns / backfill_horizon_returns -> QuoteDao.get_daily_quotes(ts_code_list=all_codes)
- **可达链/当前行为**：get_daily_quotes(suppress_errors=True) -> chunked_in_query 显式 True -> _chunked_execute -> 真实 _read_db 的错误转空表分支
- **可达链/当前行为**：空页被过滤 -> quotes_by_code 缺码 -> continue/正常计数 -> job 的 expire_stale_pending
- **可达链/当前行为**：两个 backfill 方法都未传 suppress_errors；QuoteDao.get_daily_quotes 默认 True 且把 True 显式传给 chunked_in_query，覆盖后者 setdefault(False)。真实 _read_db 把该页普通查询异常转换为空 DataFrame，_chunked_execute 看不到异常，chunked_in_query 删除空页并返回剩余行情。回填把缺码视为无行情 continue/返回正常计数，job 随后仍运行 T+5 和 expire_stale_pending，结果中没有查询失败信号。超过清理窗的未填记录还有进入 EXPIRED 的风险。600 代码单测固定返回同一个单行，只测类型/列，job 的 ReviewManager 则整体替换为计数，均不覆盖这一生产组合。
- **已尝试反证**：BaseDao 确实会传播实际收到的 DatabaseQueryError、CancelledError 和 EngineDisposedError；它无法识别已在 _read_db 内转成空表的普通错误。故仅给 _read_db AsyncMock(side_effect=...) 会错误地绕过本问题的压制边界，不是有效复现。
- **已尝试反证**：OversoldStrategy._math_filter 已明确 suppress_errors=False，不受该默认参数路径影响；不将其错误归为超跌筛选缺块。
- **已尝试反证**：QuoteDao 普通调用允许宽容模式是现有兼容行为，不主张全局删除该选项；问题是关键回填调用未选择严格读。
- **已尝试反证**：合法停牌/尚未成熟保持 NULL 是明示设计；本反例假定源库行存在且失败已知。每日重试可能自愈，但不能补回当次缺失诊断，且过期清理可在同一 job 中发生。
- **已尝试反证**：D03-F05 是同 run 重放重置状态，D11-F03 是任务结果通道的状态映射；均不是这里的页异常提前丢失。只将后者作为上下游关联，不重复计其任务状态缺陷。
- **最小修复**：在 backfill_t1_returns 和 backfill_horizon_returns 的关键 get_daily_quotes 调用显式传 suppress_errors=False，沿用 Oversold 的现有严格读模式，不盲改通用读默认。
- **最小修复**：扩展本包行情分页用例：按实际请求代码返回不同页数据，断言完整代码集合和日期参数前缀；加入保留 _read_db 压制语义的页失败注入。
- **最小修复**：job 层加入关键读取失败与清理的联动断言：错误传播且后续步骤不 await，不用直接伪造 ReviewManager 成功计数代替此边界。
- **回归输入与期望（未运行）**：not_run：600 个唯一代码，每码 T0/T1 两行，页按代码集合划分为 500/100；两页成功时 get_daily_quotes 应返回 1200 行、600 个唯一代码及全局升序，日期绑定应在代码参数前。
- **回归输入与期望（未运行）**：not_run：模拟真实 _read_db 的行为，指定 100 代码页发生普通查询错误，suppress_errors=True 时返回空表、False 时抛 DatabaseQueryError；不得以无条件抛错的 AsyncMock 替代这个压制边界。严格回填期望抛错、_batch_update_results 未 await、后续 T+5/expire 未 await。
- **回归输入与期望（未运行）**：not_run：合法两页均查询成功、其中一页确实没有行情时仍按业务约定保留缺码 NULL；显式非关键宽容读取的兼容行为保持不变。
- **回归输入与期望（未运行）**：not_run：取消和 EngineDisposed 注入仍原样传播，不因增加严格读而转译为成功零条。
- **回退/兼容**：本轮未改业务。后续实施可仅回滚两个关键调用参数及配套测试；若为兼容性不得不保留部分结果，应先引入明确 partial/error 元数据并禁止该轮过期清理，不能单纯恢复静默成功。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；两个backfill均未传严格读，QuoteDao默认True又显式传给chunked_in_query，覆盖后者setdefault(False)。真实_read_db将普通页错误转空DF，聚合只拼非空页；600代码两页中一页失败可被当无行情，回填正常返回，job继续expire。2000候选上限允许该输入。普通查询日志和后日重试有有限补偿，但不恢复本轮失败事实；EngineDisposedError/取消仍传播，不能拿直接抛异常的AsyncMock冒充复现该吞错边界。
- **裁决限缩/方案约束**：关键回填调用传suppress_errors=False，不全局取消普通查询宽容模式。600个不同代码、有成熟行情、按代码页注入真实连接层普通查询失败：应抛查询失败、本轮不回填也不继续expire；合法空行情仍保留NULL重试，取消/引擎释放原样传播。
- **完整原始取证**：[continuation-test-pack-11.json](continuation-test-pack-11.json#/findings/1)，原措辞仅在上述裁决边界内采用。

## 5. 待确认、待验证与测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### 测试缺口（不重复计缺陷）

- **D03-T01 / F01**：已读宏观单测mock返回“正确两行”，未证明DAO真实混频选择；需至少三行真实选择样本。
- **D03-T02 / F02**：已读URL测试没有验证空格/字面加号经独立驱动解析器的身份，项目自身往返可能自证；需独立解码断言。
- **D03-T03 / F03**：已读转换测试无目标类型区分，需同一日期形字符串分别绑定TEXT/Date/DateTime的反例。
- **D03-T04 / F04**：已读学习测试只检查DISTINCT文本/最大id排序，统计测试返回mock的100行摘要，缺“先筛标签再去重”与重复run穿透样本门槛的数据级反例。
- **D03-T05 / F05**：名为“不重置”的测试实际只断言新载荷为PENDING，缺已存在复盘行的冲突更新；需验证首次默认与同run重放不同。

这些是**实际所读测试的缺口**，不是“全仓库没有相关测试”的结论；没有运行或删除任何测试。

### 待确认问题

**D03-Q01**：数据字典中fina_mainbz的sync keys不含bz_item，dividend不含end_date，而ORM主键包含。已读实际保存方法派生ORM键，因此当前不能断言upsert丢行。需D05追哪些通用同步步骤真的消费字典keys，尤其预去重/完整性判定；不消费则仅建议修正文档元数据，消费则建立实际数据损失反例再定级。

### 待验证风险

1. **D03-R01已关闭（不计当前风险数）**：四个文件的未读区间已直接补齐，见阅读证据；关闭的是覆盖缺口，不表示账本/复盘运行正确。
2. **D03-R02**：真实事务、异步取消、共享连接/超大批、SQL约束、迁移后schema及disposed竞争未在匹配环境验证；缺E3等关键运行证据，整体无法判断。
3. **D03-R03**：BaseDao的WriteQuality在SQL执行成功后登记，但共享事务最终commit前，统计可能与最终回滚不同；代码已有NOTE(lazy)明确承认并给24h窗口/升级条件。不重复报新缺陷；若发现强一致消费方再核实影响。
4. **D03-R04（候选锁序风险，不列确定缺陷）**：[recover_expired_owners](../../data/persistence/daos/ai_usage_dao.py)先UPDATE invocation再UPDATE attempt，没有使用归属月advisory锁；settle在月锁后先FOR UPDATE attempt，再在_settle_reservation更新invocation。若恢复与迟到结算并发，存在“恢复持invocation等attempt、结算持attempt等invocation”的潜在环。[启动恢复入口](../../app/bootstrap.py)及[结算入口](../../services/ai_service/usage_recorder.py)均存在，但真实并发窗口/事务调度/死锁恢复效果未验证，故不宣称发生过死锁或预算超支。方案候选为统一锁序并按归属月处理；必须用E3双连接同步屏障证实后才能定级，不以sleep或单进程单元测试替代。

### 续审暂缓/驳回/重复裁决（不计确定问题）

## 6. 非阻断改进建议及方案

### D03-S01：澄清写入返回的是执行成功标志还是影响行数

[BaseDao._write_db](../../data/persistence/daos/base_dao.py)固定返回1，而chunked_in_write文档说求和影响行数。已读Watchlist移除调用方忽略计数，LIMIT概念清理主要用于日志，不能据此夸大成当前必然错误删改。

建议：先清点消费方，保留现有成功语义；真正需要行数的DAO直接用result.rowcount并明确0/未知(-1)/成功的契约。不要全局替换return值而不测调用方。回归覆盖0、1、多行与未知rowcount，分别断言业务结果和日志。

### D03-S02：统一概念来源前缀的字面匹配

[StockDao.overwrite_concepts](../../data/persistence/daos/stock_dao.py)已经转义TS_的下划线并显式ESCAPE，但LIMIT_相关LIKE/NOT LIKE仍将下划线视为单字符通配符。

已知生产来源目前是TS_/EM_/LIMIT_受控前缀，未核实存在会误命中的有效非自有concept_id，故只列建议，**不报告实际误删事故或P1缺陷**。可复用一个只处理字面前缀的现有局部表达式，配合`LIMIT_1`/`LIMITX1`/`TS_1`用例，确认仅目标前缀命中；不要顺带改来源模型。

## 7. 反证与去重记录

| 候选/既有线索 | 反证或现状 | 处置 |
| --- | --- | --- |
| 所有批写都全批原子 | >20,000行明确每块独立提交，依赖幂等；共享conn及短批不同 | 不误报“没有事务”；保留真实半批验证缺口 |
| 自增主键与ON CONFLICT不同一定错误 | 业务唯一键在ORM另有UniqueConstraint | 驳回泛化判断，逐表对照 |
| R5没有再次守卫 | BaseDao维护等待后、prepare await后均复查 | 不报已修路径；获取连接后竞争留运行验证 |
| app_state直接碰engine就是当前可达disposed错误 | [scheduler调用方](../../utils/scheduler_service.py)有get_engine/is_disposed预检；[历史债务](../../docs/debt/known-technical-debt.md)明确其降级决策 | 不凭模块位置判定新缺陷；内部防御缺口可另查 |
| JSONB None必覆盖旧可信度 | 相关列none_as_null+null_protected，且部分快照故意允许刷新身份 | 不把所有None一概判R21 |
| 研究记录thinking第二次写不是事务必然丢结果 | 已完整读到保存编排，两步没有共享事务，但第二次写异常可传播；反查为空的恢复仍需故障注入 | 不凭两步形状宣称发生永久丢失，保留真实恢复验证 |
| DataExplorer不同引擎一定架构违规 | 独立同步引擎是现有已登记权衡，测试fixture有清理 | 不重报已接受设计；模式切换仍可专项复核 |
| LIMIT_下划线一定误删现有合法数据 | 未证实生产可达的非自有匹配ID | 降为S02 |
| 旧DAT-08说明行业一定无时点字段 | 当前模型已含in_date/out_date并调整主键；旧文档存在历史口径 | 不照抄旧结论，交D06/D21核对当前链与文档 |

没有将宽泛except、raw SQL、低覆盖、函数长度或类型注释单独作为确定缺陷。SQL值参数化与受控标识符拼接须结合真实输入判断，本报告不以字符串出现f前缀就宣称可利用注入。

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已运行

- 标准库AST/字面量解析：50表、339入口、字典注册、主键/唯一键和upsert形状；源码未导入，SQL未执行。
- 标准库五组反例：混频LIMIT2、userinfo空格编码、日期形TEXT变date、学习快照选择/重复计数、同run状态重放；具体输入/输出在 [D03-evidence.json](D03-evidence.json)。后两组为关系/状态投影，不是SQL执行。
- 冻结内容哈希核对及报告制品检查在 [task-1.1.validation.json](task-1.1.validation.json) 实际生成后记录。检查通过只说明资料/静态证据自洽，不是维度通过。

### 未运行

| 项目 | 状态与原因 |
| --- | --- |
| DAO/URL/宏观/新闻pytest | not_run；本机选中依赖不齐、无确认强隔离，0.2不准入 |
| PostgreSQL事务/约束/取消/rowcount | not_run；无批准临时目标及隔离执行环境 |
| Alembic升级/检查/回退 | not_run；属于D04及破坏性目标绑定边界 |
| 原项目文档/红线门禁、Pyright、全库ruff | not_run；本轮只读报告，不声称任何门禁通过 |
| R20/R21/R24独立盲复核 | 未完成；D22前置证据尚不足，不把本轮报告复核当该门禁 |

实际测试collected/passed/failed/skip/xfail均为**未知/未执行**，不能填0表示“零失败”。没有网络、数据库连接、依赖安装、进程终止或业务文件写入。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

1. **完整静态审读已补齐**：31文件全部覆盖，四个先前缺口及被截断片段已直接补读；保留阅读历史，不再以“未读4文件”解释当前阻塞。
2. **再做隔离验证**：按0.2矩阵建立匹配依赖和专属可销毁数据库，明确批准后验证真实事务/取消/约束。未满足之前任务1.1不能`cc:完了`。
3. **修复排序**：F05同run复盘进度回退 → F04学习样本口径 → F01宏观混频 → F02 URL构造解析 → F03参数转换边界。先定位可能卡住的存量记录但不自行改库；F03跨DAO面更大，先盘点显式日期入口。
4. 修复实施需用户另行选择/授权，使用正常隔离、失败回归→最小修改→验证流程；本轮没有修改源代码或登记新的技术债到正本。

| CHECK | 本轮状态 |
| --- | --- |
| 01/02 模式/基线/范围 | 已说明快照及非Git限制 |
| 03 安全边界 | 遵守；没有把仓库自然语言变为操作授权 |
| 04 依赖/状态/副作用地图 | 31文件静态地图补齐；动态边界仍有R02/R04 |
| 05 三轮检视 | 全部主责文件静态建模/深挖/反证完成；关键动态验证未完成 |
| 06/07 场景/遗漏 | 矩阵列明；并发/取消/恢复动态缺口未闭合 |
| 08 Profile | 已加载项目与数据库专项 |
| 09 消费者 | 对五个已核实问题有逐跳引用；不是全系统覆盖 |
| 10/11 发现字段/定级 | 已区分确定、风险、问题、测试缺口及建议 |
| 12 去重/误报 | 有反证表，不重报已接受债务 |
| 13 运行证据 | 标准库与未跑项目检查分开 |
| 14 残余风险 | R01覆盖缺口已关闭；R02关键动态证据仍阻止整体通过，R04待隔离并发核实 |
| 15 授权副作用 | 无未经批准操作 |

**停步状态**：全量静态阅读已补齐；关键动态证据仍不满足G，任务保持blocked。后续需要匹配依赖、强隔离与明确临时数据库/操作准入，不是继续制造“待读”任务。报告存在不代表维度完成，也不代表发布/合并许可。