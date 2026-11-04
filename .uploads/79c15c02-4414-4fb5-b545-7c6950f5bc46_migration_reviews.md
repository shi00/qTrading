# D04 · Alembic 迁移与存量兼容

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责51文件：51完整语义阅读、0完成有限来源核对、0待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共2项；其中本次新增接受0项缺陷、0项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**任务 1.2：静态审读已交付，整体仍为 `blocked`；门禁 `indeterminate`，不是迁移通过或发布批准。**

- 主责 **51/51 文件、5,272 行完整阅读**；部分读 0、未读 0、排除 0、读取失败 0。逐文件范围、真实哈希和关键符号见 [D04-evidence.json](D04-evidence.json)。主责文件均与冻结清单哈希一致。
- 44 个修订构成严格单链 **base → 0001 → … → 0044**；无缺父、重复修订、分支标签或额外依赖。此结论来自完整阅读后的常量解析，不是运行 Alembic 的结果。
- 确认两个 **P2 回退缺陷**：`D04-F01` 多版本财报无法回建旧主键；`D04-F02` 关注价格反算后可能无法窄化。两者不等于“升级必然失败”，也不等于已接受的数据清理本身有错。
- 动态项目检查 **0**、数据库连接 **0**、安装/联网/迁移 **0**。只执行自主编写的标准库静态解析、哈希和独立数学/键反例；未导入、`eval` 或 `exec` 仓库源码。
- 当前没有匹配且经确认的强隔离 PostgreSQL 环境及执行授权；真实升级、回退、再升级、锁/事务/取消/断电证据均 `not_run`。G 未满足。

| 分类 | 数量 | ID |
| --- | --- | --- |
| 确定缺陷 `defect` | 2 | D04-F01、D04-F02，均 P2/high |
| 场景遗漏 `scenario_gap` | 0 | — |
| 测试缺口 `test_gap` | 1 | D04-F06 |
| 待确认 `question` | 1 | D04-F05 |
| 待验证风险 `risk` | 2 | D04-F03、D04-F04 |
| 范围外风险 `out_of_scope` | 0 | — |
| 非阻断建议 `suggestion` | 1 | D04-F07 |

无 Git 历史，不能判定引入提交，所有新记录的变化关系为 `unknown`。既有孤儿修复债务保留原 ID，不另计成新的确定缺陷。

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D04-evidence.json](D04-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

**冻结依据**：`snapshot.json` 的 `inventory_sha256 = be4d0bbd515585b3cdb45f9a452e34b05230aa5adc8c2de60ee163bff312ac98`。按 `scope.csv.primary_dimension == D04` 确定全部主责，非目录估算。

主责组成：`alembic.ini`；`alembic\README`、`env.py`、`_shared_helpers.py`、`script.py.mako`；`alembic\versions\` 全部 44 个迁移；`data\persistence\db_migrator.py`；`scripts\migrate_strategy_name_to_i18n_key.py`。本维度没有二进制、受保护凭据或来源核对型文件。长文件分段读至 EOF，搜索和 AST 不计为语义阅读。

契约依据：

- [本轮共同契约](all-run-brief.md)、`Plans.md` 的任务 1.2、D04 卡及公共门槛 G。
- `CLAUDE.md` §3/§4；`docs\reviews\ai-review.md`、项目 Profile、数据库迁移 Profile、`review-policy.yaml`；`docs\patterns\dao-pattern.md`。
- `docs\debt\known-technical-debt.md` 的孤儿修复设计限制及财报历史重述权衡；`docs\governance\exceptions.yml` 的相关条目检索未见迁移器专用豁免。
- `validation-matrix.md` 的 E1/E3、I04、C-M1～C-M3。仅 E1 静态取证获准。

有界旁证：ORM 的相关类、DAO 的财报写入/upsert/关注起点、`CacheManager.init_db`、迁移集成/单测及 CI 的迁移步骤。实读范围单列于证据 `supporting_reads`；**不把这些旁证文件冒充完整覆盖，更不代替 D03/D15 主责审计**。ORM 全文的 AST 结构对照也不是全文语义阅读。

当前为下载快照，**无版本控制兜底**。仅新增本报告与 `D04-evidence.json`；不改源、测试、迁移、共享 scope、Plans、索引或其他维度产物。不修复、不安装、不联网、不启动项目/DB、不访问真实配置/用户数据。

## 3. 模型与场景

### 3.1 入口、目标绑定与事务边界

1. `CacheManager.init_db` → `DatabaseMigrator.init_db(engine, auto_migrate=...)` → `_get_current_revision`。
2. 无版本记录视为 bootstrap，直接 `_run_alembic_upgrade`，**即使 auto_migrate=False**；对应单测明确固定此行为，不能当作开关失效误报。未版本化但已有表不等于“物理空库”，需另验存量结构。
3. 已有版本先 `_heal_orphaned_revision`，再读取版本与 head；普通已知旧版本且禁自动迁移时抛 `DatabaseMigrationNeeded`。
4. `_run_alembic_upgrade` 从**传入 engine.url**构造 URL，放入 `Config.attributes["database_url"]`；`env.get_database_url` 优先消费 attributes，避免配置再次解析到其他数据库。`configure_logger=False` 避免覆盖应用日志；URL 使用 attributes 避免 `%` 插值。URL 对象以 `hide_password=False` 传给连接层而非打印。
5. 标准 `command.upgrade(cfg, "head")` 在线程池执行；`threading.Lock` 仅串行化**同一进程**的 Alembic 升级，不是跨进程数据库锁，也不覆盖前置孤儿修复。
6. `env.run_async_migrations` 新建 async engine/NullPool → `connection.run_sync(do_run_migrations)` → `context.begin_transaction()`。主链未见显式 autocommit/并发建索引；PostgreSQL 事务化 DDL 应与版本推进共同提交，但实际回滚、驱动行为和断电恢复未运行验证。
7. 取消显式重新抛出 `CancelledError`；这不证明线程中的 DDL 已停止。升级后非空版本不等于 head 会失败；**版本仍为 None 只 warning**，调用者随后可能标记 schema initialized，列为可选后置条件改善而非已证故障。

CLI 直接执行还会依次取 `sqlalchemy.url`、`ConfigHandler`、`config.DB_URL`、环境变量，不能用“设置了 DATABASE_URL”代替目标核验。应用的 engine URL 绑定不代表非 URL 的连接选项、search_path、角色、时区自动继承，相关外部配置仍未知。

### 3.2 逐修订前后条件与数据影响

下表的每行前置条件首先包含**已成功处于其直接父版本且结构未漂移**。全部行均已实际阅读 `upgrade` 与 `downgrade`；D=回退。版本链常量全部核对，不依赖文件名推断。

| revision ← parent | 额外前置/升级与后置 | 数据影响、D 与幂等边界 |
| --- | --- | --- |
| 0001 ← base | 建 35 张基线表、键、普通/部分/GIN 索引；表/索引存在性保护 | D 逆序删除基线表及研究记录；已有表仅按名称跳过，不校验既有列。重复表异常捕获不等于事务恢复证明 |
| 0002 ← 0001 | financial_reports 加 money_cap/accounts_receiv，nullable | 存量保持 NULL；列存在性保护；D 删列 |
| 0003 ← 0002 | macro 加 publish_date/updated_at；缺日期按下月16日回填、建日期索引 | 是保守估计日期而非真实公告时点；仅补 NULL；D 丢新列。SQL 依赖 PostgreSQL |
| 0004 ← 0003 | limit_list 按 `_NEW_COLUMNS` 加8列 | nullable、存在性保护；D 反序删列 |
| 0005 ← 0004 | 建 ai_concept_failures 与重试索引 | 已存在表整段返回，不补缺索引；D 丢队列；后续由0038下线该表 |
| 0006 ← 0005 | limit_list 的 limit → limit_type | 重命名保留值；检查源列；D 反向重命名 |
| 0007 ← 0006 | macro 加8个 GDP 列 | nullable；无手工重跑 guard；D 删列 |
| 0008 ← 0007 | 建旧 top_inst，PK(code,date) | D 删表；后续0017重建，不能将旧粒度当作当前 schema |
| 0009 ← 0008 | 建 stk_limit，PK(code,date) | D 删表；limit_type 为物理列名 |
| 0010 ← 0009 | 建旧 pledge_detail，PK(code,end_date) | D 删表；0017替换 |
| 0011 ← 0010 | 建旧 share_float，PK(code,float_date) | D 删表；0017扩粒度重建 |
| 0012 ← 0011 | 建 stk_holdertrade，四键含 holder_name/in_de | D 删表 |
| 0013 ← 0012 | 建 sw_industry_classify 与旧 member | D 删两表；旧 member 快照/旧字段被0020替换 |
| 0014 ← 0013 | shibor 加两 LPR 列，建 express 三键及 ann_date 索引 | D 删快报表/LPR列 |
| 0015 ← 0014 | shibor/app_state/行业分类保留字及数字列重命名；重建行业级别索引 | 值保留；D 对称重命名。名字变化与列内容变化不同 |
| 0016 ← 0015 | 建 watchlist，ts_code 唯一索引 | 已存在表直接返回；D 丢关注记录；0018转为唯一约束 |
| 0017 ← 0016 | 四张 DAT-09 明细表 drop+create，主键扩展到原因/席位/股东/金额维度 | **升级和D均丢这些表数据**；显式缓存重建方案，不是无损迁移；重新同步的覆盖范围未实测 |
| 0018 ← 0017 | thinking.history_id 升 bigint；索引清理/概念反查索引；watchlist唯一约束；stock_basic格式CHECK | 旧代码不合法值会使CHECK建立失败，不擅自删行；D 恢复索引并窄化 history_id，超 int32 边界仍需预检 |
| 0019 ← 0018 | screening_history 加 benchmark_code | 历史 NULL 表示未知；D 删列 |
| 0020 ← 0019 | sw_industry_member 重建为实际 l1/l2/l3/in/out_date，PK(code,l3,in_date) | 升级/D均丢成员表数据；D准确恢复0018之后的旧索引集 |
| 0021 ← 0020 | 建 stock_name_history，PK(code,start_date) | 无历史名称伪造回填；D 删表 |
| 0022 ← 0021 | 删除 ann_date=NULL 财报，ann_date非空、PK扩三键 | NULL脏行删除不可逆已明示；**多公告版本D未合并，见F01** |
| 0023 ← 0022 | task_history 加非空 persist_seq，server_default=0 | 旧行0序号；guard；D 丢顺序信息，非数据还原 |
| 0024 ← 0023 | 去重 screening 三键，旧 run/code UK → date/strategy/code UK | 丢旧研究行并经FK清 thinking；同时间保留最小id；NULL时间戳风险见F04。D重建旧UK |
| 0025 ← 0024 | task_history 加 unique_key/factory_key/retry_kwargs | 旧任务NULL表示不可重建；D丢重试信息 |
| 0026 ← 0025 | trade_cal 第60个交易日为界，旧PENDING/NULL且无t1置EXPIRED | 日历不足60行则cutoff NULL，不过期；D把所有EXPIRED改PENDING，不能恢复原NULL或区分来源 |
| 0027 ← 0026 | market_news 加7列及2索引 | 历史NULL；D丢新增新闻字段 |
| 0028 ← 0027 | 建 news_risk_brief，PK(code,input_hash) | D丢风险快照 |
| 0029 ← 0028 | backtest_results 加 quality/config JSONB | 历史NULL不是已获可信度证明；D丢元数据，消费者语义由D03/D06/D08续核 |
| 0030 ← 0029 | 超窗COMPLETED置EXPIRED；其余有alpha的COMPLETED清5列、转T1_DONE | 有意废弃错误旧口径；D no-op，不声称恢复旧值；日历不足时全部相关记录走重算 |
| 0031 ← 0030 | watchlist 加 added_trade_date/added_price N(12,6) | 历史NULL；D丢观察起点；价格语义后被0044修正 |
| 0032 ← 0031 | COMPLETED按窗处理；带旧值T1_DONE不论年龄清8列转PENDING | 研究上下文保留，旧收益重算；D no-op；超窗T1_DONE仍可能重入回填，属显式混基修正决策 |
| 0033 ← 0032 | screening UK扩 run_id，恢复append-only | 升级不删；D按前三键保留最大id并级联删thinking，是明示有损回退，不同于F01遗漏 |
| 0034 ← 0033 | screening 加 exec_warnings/filter_attribution JSONB | 历史NULL；D丢可信度元数据 |
| 0035 ← 0034 | 删除不属于已知来源前缀的概念行 | 下划线以ESCAPE字面匹配；D no-op；不能重建无前缀旧行 |
| 0036 ← 0035 | 建 sync_empty_days，PK(table,date) | 不把旧高水位推断成逐日事实；D删登记，可由后续真实同步重建 |
| 0037 ← 0036 | screening 加 conclusion_label | 历史/失败NULL；D丢定性结论 |
| 0038 ← 0037 | 清AI_LLM_/AI_DOUBAO_概念并删失败队列 | D只建空队列及索引，不恢复AI行；功能下线与迁移失误分开 |
| 0039 ← 0038 | market_news 加 Double score/conf 与 model列 | 无缺失值分数伪装回填；D丢打分 |
| 0040 ← 0039 | 建 invocation/attempt/monthly 三账本及索引 | 无FK为显式逻辑关联设计；D丢计量/预留账目，不是账本备份机制 |
| 0041 ← 0040 | 两入口加 metadata JSONB，none_as_null | 历史NULL；D丢执行元数据 |
| 0042 ← 0041 | brief 加 nullable generated_at，无default/回填 | 不伪造生成时刻；D删列 |
| 0043 ← 0042 | brief 加 execution_metadata JSONB，none_as_null | 历史NULL；D删列 |
| 0044 ← 0043 | added_price扩N(24,6)，缺可重算行情置NULL，其余改 close×adj | D反算close/adj后窄回N(12,6)；**越界见F02**；对有价但行情缺失行置NULL为显式语义保护 |

### 3.3 ORM 对齐与场景矩阵

完整阅读迁移后，自编 AST 结构投影重放建表/删表/增列/重命名/类型/nullability/PK 操作；结果为 **50表、643列**，表集合与 ORM 投影一致。初次投影报告 `limit_list`、`stk_limit` 各一对 `limit/limit_type` 差异；重读两个 ORM 类确认 `Column(..., name="limit_type")` 后全部解释，无未解释的该类差异。**不是 SQLAlchemy 编译/反射，不覆盖实际默认值、序列、约束名称、索引顺序/方法/操作符、数据库对象现状。**

关键演进对齐：财报三键对应 `FinancialReports`；thinking FK bigint对应 `ScreeningThinking`；申万时间三键对应 `SwIndustryMember`；screening四列UK对应 `ScreeningHistory.__table_args__`；watchlist唯一约束/乘积宽度对应 `Watchlist`。元数据新增均nullable，非空默认仅用于明确的计数/序号/账本数组，不将业务缺失分数补为0。

| 场景 | 实现/实读断言 | 当前证据 |
| --- | --- | --- |
| 空库/0001旧库升head | `init_db`；`TestIncrementalUpgrade` 查版本和money_cap/accounts_receiv | 静态可达；真实执行not_run |
| 引擎A、环境配置B | `_get_sync_database_url`→attributes→env优先级；`TestEngineBoundMigration`同时断言B未建表 | 源码支持目标绑定；A/B双库实测not_run |
| 禁自动迁移 | 单测分别固定fresh bootstrap/已知旧版本抛错 | not_run；孤儿路径另见F03 |
| populated回退/再升级 | F01/F02；0033有显式去重 | 反例已构造；真实迁移not_run |
| 合法旧库NULL时间戳 | 0001 nullable created_at；0024两条自连接 | 逻辑反例；生产存量未知，见F04 |
| 失败DDL/大表锁/超时 | env事务上下文；未设置显式迁移锁超时；0017/0020重建、0024自连接、0022重键 | 无测量，不给性能缺陷结论 |
| 同进程/跨进程并发 | 类锁；`TestConcurrentMigration`实际是同进程gather两个引擎 | 不能证明跨进程安全；not_run |
| 取消/中断/重试 | 显式raise取消；单测mock线程池取消；集成恢复测试人为建“版本在head但缺表” | 不是真实中途杀迁移/事务回滚证据；not_run |
| 孤儿/漂移/多头 | heal直接stamp；版本读取fetchone；链当前仅单头 | 合法head后改游标测试不能证明任意孤儿安全 |
| 独立策略名脚本 | 默认dry-run、参数化UPDATE、映射29项、未知名保留、连接finally关闭 | 映射单测不覆盖真实UK归一化冲突，见F05 |

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D04-F01 · P2 · 多版本财报使 0022 的回退无法重建旧主键

- **类别/置信度/历史关系**：`defect`，high（完整静态可达链＋键反例）；`unknown`。
- **位置**：`alembic\versions\0022_add_ann_date_to_financial_reports_pk.py:downgrade`；旁证 `FinancialDao.save_financial_reports`、`FinancialReports`、`BaseDao._save_upsert`。
- **契约**：0022 引入公告版本维度并承诺 D 还原旧PK；D04验收要求存量回退/再升级。文档只明确接受升级时删除NULL公告日行，**未说明合法多版本财报使回退不可执行**。
- **触发**：同一股票/报告期存在两个不同ann_date。输入如 `(999999.SZ, 2025-12-31, 2026-03-01)` 与 `(999999.SZ, 2025-12-31, 2026-04-01)`。
- **逐跳证据**：`save_financial_reports`从模型取三列PK → `_save_upsert`默认按完整PK冲突，不合并不同公告日 → head允许并保留两行 → 运维 `command.downgrade`越过0022 → `downgrade`仅 drop三键、ann_date置nullable → `create_primary_key(code,end_date)`在两行上冲突。标准库集合反例：三元组唯一=True、二元组唯一=False。
- **当前/预期与影响**：当前合法生产数据使回退失败，而非“有意保留某版而丢其他版”。应事先明确有损回退的决策/备份/确定性保留规则，或者明确该边界不可直接跨越并提供恢复路径。PostgreSQL事务可能回滚本次DDL，**不声称已经损坏或静默丢数据**。
- **反证**：当前三键PK不禁止旧二键重复；通用upsert也不合并它们。0033已有去重保护，但只处理screening表，不能保护财报。既有空库upgrade→downgrade→upgrade断言不含这组数据。历史重述“无法恢复旧值”的已接受权衡是另一问题，不豁免此回退失败。
- **最小方案**：不改当前源码或历史迁移。后续受控回退入口在越过0022前按旧二键预检；默认拒绝有冲突的裸回退并保留head。获明确有损回退授权后，先归档全部多版本行，再按 `ann_date DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST` 保留每组最新可见版本，复核二键唯一后调用原D。若产品要求直接支持裸Alembic回退，另行审批对历史 `downgrade` 的兼容补丁；不得改revision链或偷偷以新升级迁移预先删除财报版本。
- **回归输入→期望**：两公告版本、一版本、NULL公告日升级清理三组分别验证；默认预检不能删数据，授权有损路径留下指定最新行且归档齐全；回退到0021后再升head，恢复归档可重建两版本，PIT查询各时点取值不变。断言失败时版本/PK/行数仍与事务前一致。
- **验证/回退**：在既有 `tests\integration\test_data_db_migrator.py` 的回退测试组新增上述populated场景，并复用 ORM反射门禁；均not_run。实施失败保持/恢复head和完整备份，优先前向修复，不覆盖历史研究数据。保留哪一版是必须明确的有损兼容政策，不替用户默选。

### D04-F02 · P2 · 0044 反算后的合法商值可能超出旧价格列宽

- **类别/置信度/历史关系**：`defect`，high（模型→当前写入→D完整路径＋Decimal反例）；`unknown`。
- **位置**：`0044_watchlist_added_price_product_qfq.py:_REBASE_TO_QUOTIENT_SQL`、`downgrade`；旁证 `WatchlistDao._latest_quote/add_to_watchlist`、`DailyQuotes`、`Watchlist`。
- **契约**：0044明确实现对称反算与窄化，说明“反算值为价格量级，不会溢出”；实际允许的正因子没有该下界保证。
- **触发与逐跳证据**：`DailyQuotes.close=N(12,4), adj_factor=N(20,12)`允许 `close=10, adj=0.000001` → `_latest_quote`只要求正且有限，返回乘积 `0.000010` → 当前watchlist N(24,6)可正常固化该值 → D的谓词只要求行情存在、close非NULL、adj>0 → 反算 `10/0.000001=10,000,000` → 窄化 N(12,6)只有6位整数，最大 `999999.999999`，无法容纳。无需假设真实用户已出现该行情，也不要求调用外部API。
- **当前/预期与影响**：head下合法且应用接受的记录可使最近一次回退失败；不是当前乘积升级路径溢出。应在反算/窄化前识别不可表示值并进入明确的保留/置NULL/拒绝策略。不能用夹断到最大值或0伪装有效观察价。
- **反证**：正数检查不等于adj≥1；模型没有adj下界CHECK，DAO亦未设该下界。把N(24,6)扩宽只保护乘积，不能保证商的旧宽度。`_NULL_UNREBASABLE_SQL`不排除“正因子但商越界”的行；它不会消除此反例。不能凭供应商通常的复权因子分布排除所有本地允许输入。
- **最小方案**：保持head写入乘积语义/宽度不变。后续受控回退在绑定目标后，以同日行情计算并**先按6位小数舍入**商，预检 `abs(rounded_quotient) < 1000000`；还应避免先赋到N(24,6)时更大的商已溢出。默认有不可表示值则无写退出并保留head。若批准有损回退，归档原起点后仅将不可表示价置SQL NULL，保留added_trade_date，其余反算，再调用原D。直接历史D补丁须另批，不在本轮修改。
- **回归输入→期望**：`(10,0.000001)`、`(10,2)`、零/负/NULL因子、缺行情、舍入临界值分别验证；授权降级后不可表示价为NULL、可表示价为5，不能把整张watchlist清空；默认拒绝路径行值和0044版本保持不变；再升级后未知仍是NULL而非伪造值。
- **验证/回退**：给既有迁移集成测试增加0044专门populated回退场景，连同head schema反射运行；not_run。失败回滚事务，恢复原N(24,6)/乘积与备份；未经授权不批量清空观察价。

### 续审独立接受的发现（当前）

本维度没有新增接受项；不以问题配额制造缺陷。

## 5. 待确认待验证及测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D04-F03 · risk · 孤儿修复的实际 schema 前提没有被证明

原债务 **P3-M4-DbMigrator-OrphanHeal-SingleChain-Assumption** 仍存在，**不重复计新缺陷**。本次确认链确为单链，未触发既有“引入多头”升级条件。但“未知revision不在脚本集合中”本身不能证明它是head后代，线性也不能证明所有未来变更都是schema超集（0038实际删过表）。heal不检查schema，且在auto_migrate判定前写游标。

实读集成测试先正常升head，再把游标改成9999_orphaned；只证明**schema已完整**的特例，不覆盖实际旧版本脚本删除、外来revision或多游标。历史孤儿来源与本地用户库均未观察，不能断言已经造成缺表或版本伪装。建议另批隔离反例：旧schema+未知游标、head schema+未知游标、两版本行、auto_migrate=False，各自比较结构和副作用；再决定改为拒绝自动stamp、显式旧→新映射或schema指纹核对。由D04/D22承接，独立复核pending。

### D04-F04 · risk · 0024 对 NULL created_at 的历史去重不闭合

0001允许 `screening_history.created_at=NULL`；0024分别用 `<` 和 `=`比较时间戳。两个相同三键、不同run且created_at都NULL的合法旧schema行，经两条DELETE均残留，随后建三键UK失败；一条NULL＋一条非NULL也有同类问题。独立SQL三值逻辑模型得到 surviving_ids=[1,2]。

**不直接确认为缺陷**：正常 `save_screening_results` 排除created_at、DB默认now，尚无实读入口证明该NULL存量由产品正常路径写出，也未查看实际库。保持历史兼容风险。后续隔离造数覆盖NULL/相同时间/不同时间，明确NULL排序政策；若保证兼容所有旧schema合法行，可用单次 `row_number() over(partition by 三键 order by created_at DESC NULLS LAST, id DESC)` 确定性清理，必须先归档并确认thinking级联损失。当前不改迁移。

### D04-F05 · question · 策略名离线迁移支持哪些 schema/碰撞政策

独立脚本未检查数据库revision，逐别名UPDATE且无包住所有组的事务。自编常量反例证明：相同日期/股票、不同run的 `Value Investing` 与 `strategy_value_name`，归一化后在0024～0032的三键UK冲突；**head四键在不同run时不冲突**，所以不能把旧schema反例冒充当前必然错误。同run含两个别名还需实际生产约束/来源证据。

待明确支持最低revision、是否要求先升head、碰撞时保留两个研究运行还是合并。建议默认只读预检目标revision与归一化后冲突键，若政策未定则不写；确定政策后将写入包成可回滚事务，未知名仍原样保留。映射/DSN/脱敏单测不验证该数据库场景。当前不连接数据库或运行此脚本。

### D04-F06 · test_gap · 关键存量与真实故障证据仍未取得

这是验证证据缺口，不是凭空判定“项目没有测试”。已实读的空库/回退断言不包含F01/F02种子；同进程gather不能证明多进程；所谓中断恢复测试是手造“head但缺表”并重建schema，不是真正中途失败；取消单测mock线程池，不验证DDL线程退出。

需要：空库；0001/0016/0021/0023/0031/0043代表旧库携数据；F01/F02；NULL公告日、非法stock_basic代码CHECK、FK大整数边界；事务中失败/连接断开后重试；并发进程；取消时线程与连接生命周期；升级/回退/再升级的结构、版本、行数和备份校验。全部not_run，E3/批准未满足。D15可补充其他测试覆盖证据，不应由此报告推断全库无相关用例。

### 续审暂缓/驳回/重复裁决（不计确定问题）

## 6. 改进建议及方案

### D04-F07 · suggestion · 升级版本后置条件可以 fail-closed

`DatabaseMigrator._run_alembic_upgrade` 在new_rev=None时仅warning，`CacheManager.init_db`随后可置 `_schema_initialized=True`；单测 `test_upgrade_warns_on_none_revision`明确固定了该行为，当前未找到正常成功升级后游标缺失的可达故障，因此**不计缺陷**。

若维护者选择更强后置条件，可把“None/空版本”与“非head”统一为失败，保留原engine目标绑定、日志脱敏和取消传播；更新该单测，断言schema flag未置True、下次可重试。收益是故障下不假定初始化完成，代价是外部删除版本记录等异常可能从warning升级为启动失败。只改变异常分支，不自动重建/删库；回退是恢复原告警政策，不涉及用户数据变更。

## 7. 反证与去重记录

| 候选/既有结论 | 裁决与依据 |
| --- | --- |
| 任意历史迁移无手工guard就是当前幂等缺陷 | 驳回。Alembic版本推进与PG事务是主防线；0007/0008等直接重复调upgrade并非合法版本重跑。只有实际中断后部分提交证据才能升级此结论 |
| 0001吞DuplicateTableError即可安全继续事务 | 未采信此注释。事务失败恢复及asyncpg异常包装需真实验证；预检查正常命中时不会触发catch。无反例实测，不列确定缺陷 |
| 0002注释说“fresh从0001取新列”意味着当前空库漏列 | 驳回。实际0001不含两列，但完整链0002会添加；文字沿革不是当前schema缺失 |
| 0017/0020 drop+create、0030/0032/0035 no-op D一定是bug | 驳回“损失即缺陷”。源码明确是重建/废弃错误数据，D不承诺还原值；恢复效果和业务窗口仍需动态/需求复核，不能把注释当正式业务批准 |
| 0033 D去重丢旧run | 已明确的有损回退，按最大id保留并有FK级联；与F01未处理合法重复而失败不同 |
| 0022删除NULL公告日就是本次F01 | 去重。NULL清理已明示不可逆；F01只指合法多版本回退失败，不重复登记已接受财报历史重述权衡 |
| Python属性limit与迁移limit_type不一致 | 驳回。两个模型均实际用name=limit_type映射；初次AST投影的四个差异已逐一解释 |
| AUTO_MIGRATE=False下fresh升级 | 驳回。bootstrap行为被当前单测明确固定；普通旧库禁升级会抛异常。未知游标路径仅列F03，不混淆 |
| 环境配置不同导致应用必然迁错库 | 驳回。应用attributes优先于配置，且有A/B双库断言；直接CLI和非URL连接设置仍须核验 |
| 类锁/两个engine并发测试证明跨进程安全 | 驳回此证明。两个engine仍在同一Python进程共享锁 |
| 孤儿线性假设 | 原债务ID保留，F03只补实际证明边界，不重复新增defect |
| D03问题重复 | [D03报告](D03-database.md) 的F01～F05分别为宏观取样、URL编码、日期形文本、最新run口径及幂等重放复盘状态；本报告两个回退根因均不同 |

人工红线核对：R5 本轮未新增DAO/维护分支；实读写入旁证走 `_save_upsert/_write_db`，不改其disposed保护。R17 实际物理列映射已核对，0015前旧保留字是迁移历史，不作为当前命名缺陷。R21 两条方案不填合法分数/价格常量，0044不可算价保留NULL；可信度元数据消费者仍由D06/D08/D22独立复核，本维度不给R21全产品通过结论。R18 无Git归档，仅写两份审计产物并列明清单，未git init。

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已运行：仅自主标准库静态/独立逻辑检查

| 检查 | 方式、结果与限制 |
| --- | --- |
| 主责完整性 | csv/pathlib/hashlib：51文件、5272行，逐项hash与scope匹配；不是语义阅读替代品 |
| 常量链 | ast仅读Constant，断言revision等于0001～0044、parent等于None/0001～0043；退出0，无分支/额外依赖 |
| 受限schema投影 | 自编AST遍历，不导入SQLAlchemy：50表643列；四项名称差异由两个name=映射解释；不比较真实默认值/索引/约束/反射 |
| F01反例 | 独立三元组/二元组集合唯一性：True/False，未运行SQL |
| F02反例 | Decimal(10)×Decimal(0.000001)=0.000010；除法=1.0E+7，大于旧列999999.999999，未运行DDL |
| F04/F05反例 | 独立三值逻辑保留NULL时间戳两行；安全提取29个字面映射，旧三键碰撞=True、head不同run四键碰撞=False |

以上通过 `@'…'@ | python -` 内联自主脚本执行，退出码均0，无项目导入/连接/业务文件写入。证据中保存算法边界、输入与输出，未把标准库检查计作项目测试。没有pytest用例运行，case/skip/xfail均未收集，不能写成“0失败”。

### 未运行

| 真实入口/命令 | 状态与前提 |
| --- | --- |
| `python -m alembic upgrade head`、`python -m alembic check`、`python -m alembic downgrade base`、再upgrade head | 全部not_run。来源为CI实际步骤；必须先E3并批准可销毁目标/角色，且显式绑定attributes，不能仅覆盖环境变量 |
| `python -m pytest -n 0 tests\integration\test_data_db_migrator.py tests\integration\test_orm_migration_consistency.py tests\integration\test_alembic_alignment.py tests\integration\test_alembic_idempotent.py -v --tb=short` | not_run。上述路径与断言均已核对；fixtures会CREATE/DROP DB，需另批并满足依赖/隔离基线 |
| `python -m pytest -n 0 tests\unit\test_db_migrator.py tests\unit\test_migrate_strategy_name_to_i18n_key.py tests\unit\scripts\test_migrate_strategy_name_to_i18n_key.py -v --tb=short` | not_run。收集/插件也会运行仓库代码，不能在共享本机当纯静态检查 |
| 自编新增的populated/失败注入用例 | 仅列设计，未创建、未运行，不虚构pytest节点 |
| Ruff/格式/pre-commit/Pyright/文档检查器 | not_run；本轮不改源码/治理正本，且共同契约禁止运行项目工具 |

环境事实沿用0.2验证证据：共享Windows、无经确认强隔离服务、关键依赖版本未对齐。未再探测本机数据库/真实DSN，未发起网络/生产/秘密读取。最终仅用标准库核对两份产物JSON、计数、路径及主责源hash；审计产物落盘不等于迁移执行成功。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

1. **保留blocked/indeterminate**：主责语义全读已完成，但G中的关键真实迁移与存量验证没有证据，独立复核pending；不更新共享Plans/索引为完成。
2. 优先在另行批准的E3环境复现F01/F02并先决定有损回退政策；当前可作为“不得直接跨这两个边界无预检回退”的运维限制，不擅自运行清理SQL。
3. 再验证孤儿来源/schema指纹、旧库NULL时间戳与独立策略名脚本兼容窗口；正常升级能力与灾难恢复能力分别验收。
4. 真实DDL锁时长、全链事务边界、取消后工作线程、跨进程竞态、数据库时区/search_path及安装包资源路径仍未知；不以静态单链、单测名称或空库roundtrip替代。
5. D06/D08/D22继续复核有损迁移后结论可信度与再同步覆盖，D15可补充未由本维度主读的测试证据；不把D03五条问题重复计数。

CHECK-01～15：01范围/模式/非目标已明确；02以冻结清单替代不可用Git差异，不声称恢复历史；03规则与只读授权已遵守；04入口/写入/副作用已建模；05三轮静态覆盖/深挖/反证已做；06正常/异常/并发/取消/恢复均建模，动态未验；07遗漏逐项登记；08专项Profile已读；09有界消费者/模型/测试旁证已查；10两缺陷均有完整路径及反例；11类别/置信度/严重度分离；12误报/旧债去重已列；13运行与not_run准确分栏；14范围/残余/G缺口明示；15未越授权边界。**检查表不是G完成声明。**

逐文件新增清单：`reviews\20261101-full-audit\D04-migrations.md`、`reviews\20261101-full-audit\D04-evidence.json`。其余源文件未修改。