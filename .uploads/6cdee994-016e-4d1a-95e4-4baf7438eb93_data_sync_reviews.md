# D05 数据源、同步、水位与完整性检视

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责32文件：32完整语义阅读、0完成有限来源核对、0待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共8项；其中本次新增接受3项缺陷、0项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**任务状态 blocked，不能宣称 D05 全量检视完成。** 32 个主责文件共 14,961 行，完整审读 20 文件/8,747 行，部分审读 3 文件，未语义审读 9 文件。实际已读区间合计 10,557 行，尚余 4,404 行。搜索、哈希和元数据统计均未充当全文阅读。

| 类别 | 数量 | ID |
| --- | --- | --- |
| defect | 5 | D05-F01～F05 |
| scenario_gap | 0 | — |
| test_gap | 2 | D05-T01、T02 |
| question | 1 | D05-Q01 |
| risk | 2 | D05-R01、R02 |
| out_of_scope | 0 | — |
| suggestion | 0 | — |

最高严重度 P1：F01、F02；其余确定缺陷为 P2。确定缺陷依据完整静态可达链，不是项目运行复现；变化关系均为 unknown（无 Git 历史）。按 review-policy，两个 P1 构成发布阻断建议，`gate_verdict=fail`；此外，**全量覆盖结论仍无法判断**，G 未满足、独立复核 pending。没有批准修复、发布或任何动态命令。

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D05-evidence.json](D05-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

### 2.1 边界和依据

- 当前是无版本控制兜底的下载快照。仅新增本报告和 `D05-evidence.json`；不改源代码、测试、Plans、scope、索引或其他维度产物。
- 冻结依据：`scope.csv` 中 `primary_dimension=D05` 的确切 32 项；`snapshot.json.inventory_sha256=be4d0bbd515585b3cdb45f9a452e34b05230aa5adc8c2de60ee163bff312ac98`。32 项内容 SHA-256 均与 scope 冻结值一致。
- 契约：根 Plans 的 1.3/D05/G、[共同 brief](all-run-brief.md)、[0.2 准入矩阵](validation-matrix.md)、`docs\patterns\data-sync.md` 的 C5/C15/C16/C18、`docs\reviews\ai-review.md`、project / messaging-data-pipeline / third-party-rpa / library-sdk-plugin profiles、review-policy。
- 已先查技术债及例外。外部行级校验、通用跨调用熔断、旧财报不可恢复版本等不重新计缺陷。D03-F01～F05 与本报告五项不同根因，未重复计数。
- 不安装、不联网、不导入/执行项目、不运行测试/数据库/迁移、不读取秘密值或真实用户配置。配置代码可读，不代表实际配置已核验。

### 2.2 全部主责文件的真实阅读账

下表 SHA 为 SHA-256 前 12 位便于人工核对；**完整 64 位哈希、区间、余段和关键符号见配套 JSON 的全部 32 个 `file_reads`**。区间均为闭区间。pending 的符号未核实，故不从 AST/目录猜填。

| 文件（均相对仓库） | 总行 | 状态/实读区间 | SHA 前缀 | 关键符号 |
| --- | --- | --- | --- | --- |
| `data\constants.py` | 403 | read 1–403 | 16121a805318 | FINANCIAL_BATCH_TABLES、WATERMARK_KEY_PREFIXES、indices_to_sync |
| `data\data_processor.py` | 1390 | read 1–1390 | 4f8e185f757a | initialize_system、run_daily_update、run_concept_sync、sync_comprehensive_fundamentals |
| `data\external\__init__.py` | 27 | read 1–27 | 17ba6da8fe5b | **getattr** |
| `data\external\akshare_concept_client.py` | 198 | read 1–198 | 959ddc5a2e12 | get_concept_list、get_concept_constituents |
| `data\external\akshare_rate_limiter.py` | 41 | read 1–41 | 68e3ea57dde5 | get_akshare_rate_limiter、reset_akshare_rate_limiter |
| `data\external\news_fetcher.py` | 1077 | pending 无实读 | 6a1c6de074bf | 未核实 |
| `data\external\news_sources\__init__.py` | 6 | read 1–6 | 30862da9d2aa | 包说明，无执行定义 |
| `data\external\news_sources\circuit_breaker.py` | 134 | pending | 7382f7d7d4ec | 未核实 |
| `data\external\news_sources\cleaning.py` | 103 | pending | c893a6e646d2 | 未核实 |
| `data\external\news_sources\cninfo.py` | 285 | pending | 03c267dcfe00 | 未核实 |
| `data\external\news_sources\corpus.py` | 203 | pending | a11c8a555599 | 未核实 |
| `data\external\news_sources\dedup.py` | 128 | pending | d5fdc93b6b21 | 未核实 |
| `data\external\news_sources\registry.py` | 187 | pending | a2e00f0a2579 | 未核实 |
| `data\external\news_sources\sina_7x24.py` | 309 | pending | 2e641af4b386 | 未核实 |
| `data\external\news_sources\sina_stock.py` | 205 | pending | d586e23c625b | 未核实 |
| `data\external\tushare\__init__.py` | 16 | read 1–16 | 5e97220699ac | 三个子服务导出 |
| `data\external\tushare\api_wrapper.py` | 386 | read 1–386 | 6597373b631a | _handle_api_call、_handle_api_call_paginated |
| `data\external\tushare\capability_probe.py` | 725 | reading 1–243 | 8c3ba796457e | is_api_available、mark_api_unavailable、get_effective_synced_tables |
| `data\external\tushare\rate_limiter.py` | 118 | read 1–118 | 25844f1e6d71 | _build_rate_limiters、reload_rate_limiters |
| `data\external\tushare_client.py` | 1681 | reading 1–145；780–1440；1581–1681 | cc96eb4ec6a3 | get_daily_quotes、get_disclosure_date、get_stk_holdernumber、get_pledge_stat |
| `data\mixins\__init__.py` | 1 | read 1 | b7098937408a | 包说明，无执行定义 |
| `data\mixins\calendar_mixin.py` | 37 | read 1–37 | f4f23bb88b4a | CalendarMixin.ensure_trade_cal |
| `data\mixins\health_mixin.py` | 1177 | reading 1–400；918–1177 | f4ad5a11e628 | _compute_tier、_assign_basic_tier、run_quality_scan |
| `data\sync\base.py` | 367 | read 1–367 | 7e7a8de56b56 | SyncResult.is_complete/merge、ISyncStrategy |
| `data\sync\concept_sync.py` | 436 | read 1–436 | 40aa1cbf6b00 | AKShareConceptSyncStrategy、ConceptSyncResult |
| `data\sync\errors.py` | 2 | read 1–2 | 906db1218fed | InitSyncError |
| `data\sync\financial.py` | 1393 | read 1–1393 | 929b0f95e307 | _run_full_sync、_run_incremental_sync、_sync_corporate_actions_by_date |
| `data\sync\historical.py` | 1614 | read 1–1614 | 9667e9d51630 | _run_historical_sync、sync_daily_market_snapshot、_completed_dates |
| `data\sync\holder.py` | 1051 | read 1–1051 | 7184868657d0 | _run_impl、_sync_top10_holders、_sync_pledge_stat |
| `data\sync\macro.py` | 735 | read 1–735 | 1f94246b66eb | _sync_macro_monthly、_sync_shibor_daily、_sync_index_weights |
| `data\sync\name_change.py` | 137 | read 1–137 | be77e10ef875 | NameChangeSyncStrategy._run_impl |
| `data\sync\sw_industry.py` | 389 | read 1–389 | 34236f18eaba | _sync_classify、_sync_members、_save_members_checkpoint |

精确续读：capability `244–725`；client `146–779、1441–1580`；health `401–917`；九个 pending 文件均从第 1 行开始。没有 excluded、read_failed 或 provenance_checked。

跨维度只读取证局限在所需函数/断言：SyncDao、FinancialDao、MarketDao、QuoteDao、CacheManager.financial_transaction、app_state_service、async_utils、TaskManager 取消入口、Scheduler 概念消费入口及五个单测文件的已列区间；不代表这些支持文件已全读。

## 3. 模型与场景

### 3.1 入口到恢复的实际链

| 链 | 取数/转换 | 保存与结果 | 日期/恢复 |
| --- | --- | --- | --- |
| 行情 | initialize_system / run_daily_update → HistoricalSyncStrategy；daily+adj_factor、basic、事件表、逐指数 fan-out | save_if_ok → 对应 DAO；逐表 sync_status、关键/可选失败表、rows_written | DENSE 日期交集再查质量分；稀疏 attempted 水位只是遥测；合法空按 (表,日) 单独登记；失败日批次重试 |
| 财务全量 | initialize_system → sync_comprehensive_fundamentals → _run_impl → _run_full_sync；L/D 股票池、四份 core + mainbz/audit | core 按公告版本合并；financial_transaction 内 reports 与 step4 marker 同连接；aux best-effort 在事务外 | step4 marker + 最小报告期数续传；全量失败计数只活在当前轮，F01 |
| 财务增量 | disclosure_date(actual_date) → (stock,period) → 四 core 同上 | 非空保存返回正数才解决；core 失败保持 pending，合法空另分支 | disclosure checkpoint 为连续解决日、set_app_state_max；pending 阻止跨洞；**企业行动在该检查点之后执行且无独立恢复目标**，F02 |
| 企业行动 | FINANCIAL_BATCH_TABLES 四项按 ann_date | forecast/express/dividend/repurchase 各 DAO | 当前仅复用本轮财务 dates；普通失败没有结构化结果或 pending，F02 |
| 股东/质押 | 最近两个报告期 holdernumber 分页；top10 逐股；pledge_stat 最近四周；share_float/pledge_detail/holdertrade 窗口 | 已读分页客户端打 truncated；holder 保存部分但返回 -1；top10 缓存股票键/5000 行 checkpoint | 多数重跑依赖数据存在或固定窗口；errors 是中止计数但被误当完整性计数，F05 |
| 宏观 | 月度 M2/CPI/PPI、GDP；Shibor+LPR；MAJOR_INDICES 权重 | MacroDao / MarketDao upsert；估算 publish_date | 月度期数、Shibor 最新日期；**指数权重共用所有指数 MAX 日期**，F04 |
| 基础维度 | stock_basic；SW 分类→L3 成员；namechange 不加日期过滤 | DAO upsert；SW 去重键源码为 ts_code/l3_code/in_date | SW 保存 checkpoint；初始化显式跑 SW/name，权限错误进入 errors；不把旧债中的“全局快照”直接当当前模型事实 |
| 概念 | run_concept_sync → 新建 AKShare strategy → 列表/每板块成分 → stocks 权威映射 | 完全解析板块删除重建，部分解析只 upsert；LIMIT_ 已停写、清理旧污染 | facade 按来源 success 聚合，Scheduler 据 is_complete 推进日键；软失败 success 的契约冲突 Q01 |
| 新闻/离线语料 | canonical 描述了三源解析、去重、熔断、CorpusStore | **实现尚未语义审读** | 不用文档宣称分页、去重、时间转换或幂等正确，属于剩余阻塞 |

### 3.2 场景核对

| 场景 | 已读实现/断言与结论 | 运行证据 |
| --- | --- | --- |
| 合法空、无权限、真实失败 | save_if_ok 区分 EMPTY / SKIPPED_PERMISSION / FETCH_FAILED / SAVE_FAILED；F03 是指数子 fan-out 的漏检 | not_run |
| 中途分页失败 / max_pages | api_wrapper 返回 truncated；holdernumber、pledge_stat 检查标志、不推进相应成功水位 | not_run |
| 正常无数据财报 | 增量合法空可解决目标；全量空股票不写 step4 marker，避免永久跳过未来报告 | not_run |
| 财报 core 部分失败 | core_failed 阻止残缺主表落库；增量 pending 有实读断言 | not_run |
| 全量失败再正常重跑 | 跨两轮/模式未闭合，F01 | 静态链及 L01 独立日期算例 |
| 企业行动一天失败 | 不进入 gather 异常结果，且披露检查点已越过，F02 | 静态链及 L02 |
| 乱序水位 | attempted 与 disclosure 用 set_app_state_max，SQL 条件只允许字典序增大；YYYYMMDD 下顺序等价 | 真实并发事务 not_run |
| 完整性自我参照 | QuoteDao 分母来自 stock_basic 存活区间/交易日历，不是已抓 quotes；quotes 未达标有 veto。stock_basic 部分缺失仍可能污染分母，不能用 saved=fetched 证明源完整 | 库级分母/缺口反例 not_run |
| 部分指数成功、其他失败 | 行情指数 F03、权重按全表 MAX 续传 F04，不能以其余指数成功替代目标集合完成 | 静态链及 L03/L04 |
| 股东单个数据族失败 | errors 小于 5 被留在 success，F05 | 静态链及 L05 |
| 重跑幂等 / 半批事务 | 主表保存调用 upsert；全量财务 report+marker 同事务；aux 在事务外为明确设计，不误报原子性缺陷 | 真库约束/回滚 not_run |
| 取消 / 超时 / 恢复 | asyncio 取消经 wrapper/strategy 自然传播；TaskManager 同时置 event 和 task.cancel；仅置 event 的 fan-out 等待及底层线程排空尚缺证据，R01 | not_run |
| 同报告期多公告版本 | _dedup_financial_df 保留 ann_date 维度；不重复报告旧版本覆盖债；不同 core 的公告日期是否对齐待验证 R02 | not_run |

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

五项均为 defect；高置信度依据是逐跳静态可达链，**没有运行项目复现**。历史引入关系 unknown；未查到适用的已批准例外。方案只供将来单独授权实施。

### D05-F01 · P1 · 财务全量部分失败后，普通重跑切到增量，失败股票历史不再自动补齐

- **位置/根因**：`financial.py::FinancialSyncStrategy._run_impl/_run_full_sync/_run_incremental_sync/_status_data_date`。分支只看 financial_reports 是否有 last_sync_date，不看此前全量未解决目标。全量只保存 failed_stock_count 汇总，未保存失败股票重试集合或“全量未完成”状态。
- **逐跳证据**：DataProcessor.initialize_system → sync_comprehensive_fundamentals(force=False) → 首轮全量；某股 core_failed 导致不保存、不写 step4 marker；全量尾部仍 update_sync_status(financial_reports,today,...,partial)。SyncDao.update_sync_status 无条件 INSERT last_data_date，冲突时也总更新 last_sync_date。下轮 _run_impl 因已有 last_sync_date 选择增量；无 disclosure checkpoint 时，近期 legacy_date 取次日起窗。同日重试窗口为空，即使隔日重试也只查询新披露，不按缺失 step4 marker 回补旧报告。
- **触发/实际→预期**：2024-10-21 首次全量 A 成功、B 网络失败；记录 partial 且日期为当日。同日不带 force 再跑，应继续 B 的历史窗口，实际无 disclosure 日期可处理，能返回 success；缺 B 的旧历史继续存在。初始化第二轮可不再把财务记为 degraded。
- **影响**：重要基本面历史缺失被普通“重试”掩盖；直到用户显式强制全量或 B 再有相关披露才可能补入，后者也不保证补齐历史报告期。
- **反证**：reports 与完成 marker 同事务确实保护失败股票不被标完成，但只有全量路径读取 marker。`get_incomplete_financial_stocks` 同样只在全量使用；增量 pending 只承接披露目标，不承接全量失败股票。强制重跑能恢复，不等于普通重试已闭环。
- **最小符号级方案**：为全量轮次保留独立“未完成”状态/失败股票目标（可复用 AppState，无需改变财报主键）。开始全量前登记未完成；_run_full_sync 失败或中断保留该状态，成功才清理。_run_impl 在选择增量前先恢复该轮，复用现有 step4 marker，**不等同 force=True 清空成功 marker**。不要简单对所有 partial 都转全量：增量披露 partial 也使用同状态，会形成另一种跳过 pending 的循环。
- **兼容/存量**：不改 L/D 股票池、合法空股票暂不标完成、ann_date 版本键、aux best-effort 或正常增量路径。旧库已丢失败集合时只能依据未完成 marker/报告期覆盖生成候选并受控补抓，不能从一个 last_data_date 推断所有股票完成。不降低财报版本保留。
- **回归输入→期望**：两股首轮 B 失败→同日正常再跑 B 被抓、A 不重复重抓；再失败仍 partial；取消后同会话恢复；成功后第三轮才走增量；原有增量 pending 不被此状态分支吞掉。
- **验证准入/回退**：E2 在 `tests\unit\test_financial_sync.py` 加跨轮有状态 fake 测试；E3 验证真实 reports/marker 事务。均 not_run。代码回退须保留新恢复状态，不能删 pending；数据补抓独立批准，禁止重置全部已成功 marker。

### D05-F02 · P1 · 企业行动普通失败在内部被吞掉，披露检查点却使失败日期退出后续同步窗口

- **位置/根因**：`financial.py::_run_incremental_sync/_sync_corporate_actions_by_date` 及内部 `sync_one_date_table`。普通外部/保存异常被分类记录后隐式返回 None；外层只对 gather 中的 Exception 标 partial，因而看不到这些失败。恢复窗口又只来自财务披露日期。
- **逐跳证据**：增量日期核心目标成功或合法空 → set_app_state_max(disclosure_checkpoint,day) → 企业行动阶段调用四表；某表普通网络失败落内部 except，未更新失败状态、未返回错误、未写 pending → gather 收到 None → 本轮可保持 success。下轮 checkpoint+1，甚至 days_to_process 为空就提前 return，不会再调用企业行动补旧日期。
- **触发/实际→预期**：2024-10-21 disclosure 合法空、forecast 请求失败，其他三表成功。实际披露 checkpoint 可达 20241021、没有 forecast 重试目标且整体成功；预期保留 forecast(20241021) 未解决目标并明确 partial，下一次即使披露无新日期仍重试。
- **影响**：预告、快报、分红、回购重要事件可在普通增量重跑中持续缺日，不是单纯少一条日志。独立复核撤回“force也因旧完成标记提前返回”：force先调用clear_step4_sync_status删除旧标记，能够重新进入全量路径；昂贵的人工恢复不代表普通增量自动恢复正确。
- **反证**：TokenBucket 的单次重试耗尽后仍可进入该分支；增量 day_has_error/pending 只覆盖 core disclosure targets。外层 Exception 检查只能看到被重新抛出的异常，无法发现已转换为 None 的普通失败。合法空与权限拒绝本来有独立分支，不应把它们全改成失败。
- **最小符号级方案**：sync_one_date_table 返回带 table/date/status/saved 的明确结果；汇总真实失败到 SyncResult，并写相应 failed/save_failed 状态。为企业行动保留独立 (date,table) 未解决集合/完成游标，在 _run_incremental_sync 的“无日期返回”之前恢复；不要把“披露已完成”游标改写成“所有数据都完成”。失败/取消未处理目标保留，成功或契约认可的合法空才解除。
- **兼容/存量**：保留四表独立失败隔离、合法空、skipped_permission 及能力刷新语义，不无限重试明确无权限接口；保留原 upsert 键和成功表数据。已越过的旧失败日期不在新 pending 内，需另批有限窗口补抓；不能仅部署代码就声称旧数据已恢复。
- **回归输入→期望**：核心空+forecast 抛普通 RuntimeError/网络错误→partial+待补，次轮无新披露仍调用 forecast；保存失败同样待补；其余表仍成功；合法空无错误、权限仍区别于 EMPTY；企业行动中途取消后剩余目标可恢复。
- **验证准入/回退**：E2 扩展 `test_financial_sync.py`（已有 error-path 用例多为仅 await）；E3 I05 补提交/恢复证据，全部 not_run。恢复状态不可在回退时清空；不删除成功表重抓整年。

### D05-F03 · P2 · 行情指数 fan-out 把请求异常过滤成合法空，写入错误空日豁免

- **位置/根因**：`historical.py::sync_daily_market_snapshot.fetch_indices`。gather 保留普通异常对象，但只检查权限异常，然后过滤非空 DataFrame；剩下的普通异常没有回检。
- **逐跳证据**：get_index_daily 抛普通异常 → gather_return_exceptions_propagating_cancel 返回异常列表 → valid 为空 → 返回 ("index",空DataFrame,None) → save_if_ok=EMPTY → safe_update_status success/0 → mark_empty_days(index_daily,day)。QuoteDao.get_bulk_sync_quality_scores 对 count=0 且登记合法空的非 DENSE 表直接 exempt；历史 resume 用该分数决定跳过。
- **触发/实际→预期**：七个指数均超时/RuntimeError，quotes/basic 正常。实际 index_daily 记录合法空且不进 failed_optional_tables；预期 FETCH_FAILED、不得登记合法空。部分指数成功、其余失败也被拼接成功载荷掩盖。
- **影响**：临时外部故障被持久化为“已核实无数据”，后续质量评分/补抓可能绕过真实指数缺口。不是要求可选指数失败阻断行情关键表。
- **反证**：外层 results_list 的 BaseException 检查无效，因为内层已把异常列表转换成成功 tuple；统一 gather 只保证 CancelledError 被重抛，不自动传播普通错误。已有 test_fetch_indices_exception 只断言方法返回 True，未验证状态/空日登记。
- **最小符号级方案**：fetch_indices 在过滤 DataFrame 前遍历异常：EngineDisposedError/系统级/非 Exception 的 BaseException 按现有传播边界抛出；普通失败返回明确 fetch_failed（最小安全选择是暂不保存该次不完整指数集合），不要变成 EMPTY。只有全部请求成功且返回可信空载荷时才走合法空。保留现有权限跳过和关键行情错误隔离。
- **兼容/存量**：不修改指数目标集合、基准配置、DAO 主键或日更 is_complete 的可选表定义。旧 index_daily 空日标记无法仅凭标记区分真实空/旧故障；受控重抓候选缺口后再校正标记，不能全表删除。
- **回归输入→期望**：全普通失败→fetch_failed、failed_optional_tables 含 index_daily、mark_empty_days 未接收该表；一成六败不能 HAS_DATA 完整成功；全成功合法空仍可 EMPTY；权限错误仍 skipped_permission；CancelledError/EngineDisposedError 传播。
- **验证准入/回退**：E2 `tests\unit\test_historical_sync.py tests\unit\test_async_utils.py`；E3 `test_historical_sync_integrity.py` 验证空日登记到质量评分，not_run。回退代码不回退已补齐指数数据；避免恢复错误空日标记。

### D05-F04 · P2 · 指数权重以全表 MAX 日期续传，其他指数成功后会永久越过失败指数的历史窗口

- **位置/根因**：`macro.py::_sync_index_weights`；跨层支持 `MarketDao.get_latest_index_weight_date` 使用不带 index_code 的 `SELECT MAX(trade_date)`。恢复位置的粒度比目标集合粗。
- **逐跳证据**：MAJOR_INDICES 某指数失败→本轮 result 标 partial，但其他指数正常保存；尾部仍 update_sync_status(index_weight,today,iw_saved)。下轮从实际全表 MAX 判断“30 天内已同步”直接 return；超过 30 天后所有指数统一从 global_max+1 起取，失败指数此前的窗口永远不再请求。
- **触发/实际→预期**：空库首轮 A 失败、B 数据到 2024-06-14；7 月 16 日重跑统一 start=20240615，A 首轮应有的历史区间丢失。预期 A 保留初始起点，B 按自身完成日期/月度节流。
- **影响**：指数成分历史不全且普通重跑无法补旧缺口；本轮 partial 的可见性并没有修复恢复正确性。
- **反证**：最新日期读取来自实际表，不受 sync_status 的 partial 水位保护；仅改尾部 status 不足以修复。月度频率是明确设计，本项不反对月度刷新，也不臆断真实接口每月数据量/限额。
- **最小符号级方案**：MarketDao 提供按 index_code 的最新日期批量映射，_sync_index_weights 为每个目标独立计算初始窗口/下一窗口及月度跳过；保存失败指数的未解决窗口，使重跑不受成功指数节流。尾部总体状态反映 partial，不把全表 MAX 称为全部目标完成。
- **兼容/存量**：保留 MAJOR_INDICES、月度节流和原 upsert；无须改权重主键。无记录/明显落后指数可用既有初始化 lookback 补抓；已有区间内部缺口需单独核对，per-index MAX 也不能证明内部无洞。
- **回归输入→期望**：A 失败/B 成功后立即重跑仍重试 A；B 不重复初始历史；31 天后 A 起点不越过失败窗口；全部成功仍每月刷新；权限拒绝/合法空依契约分开。
- **验证准入/回退**：E2 `tests\unit\test_macro_sync.py` 增加跨两轮持久状态测试；E3 I05 验真实保存/按指数日期查询，not_run。回退保留补入历史数据及失败目标元数据，不缩短重试窗口。

### D05-F05 · P2 · 股东同步把“未达到中止阈值”当成“整体成功”

- **位置/根因**：`holder.py::HolderSyncStrategy._run_impl`。多个 `_sync_*` 返回 -1 时只 errors+=1；只有 errors>=5 才置 partial/写汇总 errors。1～4 个表/报告期失败时仍是默认 success。
- **逐跳证据**：run → 例如 _sync_stk_holdernumber 普通失败或 truncated 返回 -1 → errors=1；其他数据族成功；尾部 errors<5 且未取消→不改状态、不追加 errors → 返回 success。DataProcessor.initialize_system 消费此结果时也不会看到 failed；top10 单股失败的特殊 partial 分支不能保护其他数据族。
- **触发/实际→预期**：第一个报告期 holdernumber 失败，第二期及 top10、pledge 等成功。实际 result 可以 success 且 errors=[]；预期至少 partial，带失败表/报告期，仍继续其他表。
- **影响**：未完成股东数据被任务结果和初始化流程误报成功，真实分页截断亦可能仅停水位而丢失总体失败信号。
- **反证**：_MAX_ERRORS=5 作为“中止阈值”合理，不是“容许四次失败也算完整”的契约；top10 已有部分失败测试说明结果应反映部分缺口。已有 max_errors 测试只覆盖达到阈值，少量错误用例仅 assert result is not None。
- **最小符号级方案**：将中止判据与最终结果判据分离：任一 -1 登记 table/period 失败，最终 errors>0 且无更严重终态则 partial；errors>=5 仍保留原提前中止行为。不要覆盖已设 failed/cancelled；保留明确权限状态，不能把权限拒绝当成功合法空。`DataProcessor.initialize_system`目前只检查holder_res.failed，忽略partial/errors；若验收包含“不假报初始化完整”，必须同步将该消费分支加入degraded_steps，而非只改生产者。
- **兼容/存量**：不改保存流程、partial 分页已保存数据、top10 checkpoint/缓存股票逻辑或 API 配额。无 schema 迁移；只需在已有窗口内重跑受影响数据，不能凭新结果补写旧水位。
- **回归输入→期望**：分别 1、2、4 次 -1→partial+失败目标；5 次仍中止；零失败 success；top10 自带 partial 不回退 success；取消优先；truncated 返回 -1 也改变整体状态。再从初始化消费者输入holder partial/errors，断言最终降级提示保留缺口，而不是完整成功。
- **验证准入/回退**：E2 `tests\unit\test_holder_sync.py` 增加结果断言，not_run。只读结果契约变严格，需同步检查任务展示；若回退不得把旧缺口解释为完整成功。

### 续审独立接受的发现（当前）

#### NI-F01 · P2 · 缺失或非法发布时间按每轮当前时间重建身份，同一快讯反复入库、排队和告警

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[_fetch_sina_7x24_fallback / NewsFetcher.get_latest_global_news](../../data/external/news_fetcher.py)；[NewsSubscriptionService._fetch_and_notify / get_hash](../../services/news_subscription_service.py)；[MarketDao.save_market_news](../../data/persistence/daos/market_dao.py)；[MarketNews.**table_args**](../../data/persistence/models.py)；[parse_feed_item](../../data/external/news_sources/sina_7x24.py)；[normalize_news_item](../../data/cache/cache_manager.py)。
- **契约**：新闻轮询按已见身份去重，持久化以(content_hash,publish_time)幂等；采集失败/重复轮询不应把同一源记录变成新事件。data-sync canonical中源ID是新浪7x24去重键，解析失败的时间原本为None。
- **触发**：源站连续两轮返回同一ID及相同非空正文，但create_time无法解析；或CLS同一正文缺失/非法ctime。两轮get_now落在不同秒，第二轮仍由源窗口返回该条。
- **可达链/当前行为**：sina_7x24.py::parse_feed_item输出稳定source_id和publish_time=None。
- **可达链/当前行为**：news_fetcher.py::_fetch_sina_7x24_fallback输出time=get_now且不透传source_id；CLS适配具有同类缺省分支。
- **可达链/当前行为**：news_subscription_service.py::_fetch_and_notify以time+归一正文生成hash、按时间水位放行，落库成功后才标已见。
- **可达链/当前行为**：market_dao.py::save_market_news与models.py::MarketNews仅以内容hash和发布时间冲突；变化的时间绕过两层现有去重。
- **可达链/当前行为**：在线适配丢弃7x24已有source_id，并把坏/缺时间替换成各轮当前时间。服务get_hash包含time，新的时间也大于旧水位，因此_seen_hashes未命中并再次保存、排队；DAO唯一键的publish_time已不同，内容相同也不会冲突。普通轮询还会再次发送新消息告警。
- **已尝试反证**：只在缺失/非法时间场景成立，正常源时间稳定时已有时间水位、LRU和DB唯一键有效。
- **已尝试反证**：tests\unit\test_news_fetcher.py已读单轮用例明确允许回退当前时间；这能证明兼容意图，但其断言未覆盖跨轮幂等，不能推翻可达消费者后果。
- **已尝试反证**：cache_manager.py::normalize_news_item正确执行CST转UTC，不会将两个不同秒重新合并；本项不是重复报八小时时区问题。
- **已尝试反证**：仅在成功持久化后标记已见、整批完成后推进水位的现有保护正确，但身份每轮改变时不能保护。
- **已尝试反证**：原D05五项为财务/指数/股东恢复根因，与本项不同；是否并入冻结审计仍受本轮哈希未核阻塞。
- **最小修复**：在现有publish_time非空持久化契约下，最小安全改动是隔离/跳过缺失或坏时间条目并显式报告时间不可用，而不是把None直接写入非空列或伪造成当前事件。
- **最小修复**：若产品要求保留这些条目，另行明确稳定source/source_id的身份及observed_at与publish_time质量表示，再接入轮询去重和持久化；不能只改get_hash忽略时间，否则会误合并不同日期的真实重复公告。
- **最小修复**：回查现有同正文不同观察秒记录前须单独授权；不能仅按正文hash删历史，因为合法重复发布也可能同文。
- **回归输入与期望（未运行）**：未执行。未来在tests\unit\test_news_fetcher.py及tests\unit\test_news_subscription_service.py增加跨两轮fake源、fake时钟、fake DAO/queue断言；两轮同ID坏时间不能产生第二个事件身份、第二次通知或第二次分类入队。
- **回归输入与期望（未运行）**：对照：正常不同日期/不同源ID的同文记录仍按批准契约保留；正常时间源重复轮询仍幂等；取消仍直接传播。
- **回归输入与期望（未运行）**：若选择跳过策略，断言源/时间不可信告警可见且DB调用为零，不将排除条目计为有效覆盖。
- **回归输入与期望（未运行）**：真实DB唯一约束及存量纠偏未验证，不将静态链称项目复现。
- **回退/兼容**：本轮不改实现。将来回退保留原始源ID及时间可信度信息；不得清空已见集合、回退水位或批量删除新闻来掩盖重复。
- **独立裁决**：[continuation-review-core.json](continuation-review-core.json)；sina.parse_feed_item保留稳定source_id且坏发布时间为None；在线fallback丢弃source_id并每轮把None换get_now，CLS坏ctime亦如此。订阅get_hash含time，时间水位放行后来观察秒，DAO唯一键是(content_hash,publish_time)，因此同ID/正文跨两轮可再次首写、排队，普通轮询再告警。主动反证：normalize_news_item明确设计时间缺失回退当前，这解释局部兼容意图，不提供跨轮幂等；正常源时间稳定、成功保存后标seen及水位单调均有效，但不能抵消身份变化。不是时区减8小时问题，也不据静态路径断言线上已有重复数据。
- **裁决限缩/方案约束**：当前非空publish_time契约下优先隔离/跳过坏时间并显式报告；如必须保留，先定义source_id/observed_at及时间质量契约再接入幂等，不将None直接写非空列。
- **裁决限缩/方案约束**：不要只按正文去重或批删同文历史，会误合并真实重发。未来同ID坏时间跨两轮不得第二次通知/分类/新事件；真实不同日期同文对照仍保留。
- **完整原始取证**：[continuation-news-ingestion.json](continuation-news-ingestion.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### NI-F02 · P3 · 新浪回退新闻在普通增量轮询被重标CLS，来源修正依赖非保证成功的分析回写

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[_fetch_sina_7x24_fallback](../../data/external/news_fetcher.py)；[NewsSubscriptionService._fetch_and_notify / _processing_loop / _safe_queue_put](../../services/news_subscription_service.py)；[CacheManager.normalize_news_item](../../data/cache/cache_manager.py)；[MarketDao.save_market_news](../../data/persistence/daos/market_dao.py)。
- **契约**：data-sync canonical及回退函数契约要求新浪回退项带source=sina_7x24，落库不误标CLS。normalize_news_item尊重item内source。
- **触发**：服务已过初始同步，CLS失败/空或开路后收到有正常时间的新浪7x24新条目；分析消费尚未完成，或队列溢出丢弃该条。
- **可达链/当前行为**：news_fetcher.py::_fetch_sina_7x24_fallback提供正确source。
- **可达链/当前行为**：news_subscription_service.py::_fetch_and_notify普通增量的normalize_news_item输入硬编码CLS；落库后才调用_safe_queue_put。
- **可达链/当前行为**：cache_manager.py::normalize_news_item只透传所给source，无原始源识别能力；market_dao.py首写及冲突更新均采用该值。
- **可达链/当前行为**：_processing_loop从原item恢复source是有效但有条件补偿；_safe_queue_put满队列丢弃路径允许已首写新闻不再进入该补偿。
- **可达链/当前行为**：增量分支丢弃item原source，重新构造source=CLS保存。初始分支保留原source，普通增量不保留。processing_loop成功处理原item时确实会回写正确source，但其完成不是初次保存的前置条件；_safe_queue_put明确允许队列满时丢弃旧项，故部分误标可能不获修正。
- **已尝试反证**：初始同步分支直接normalize完整item，来源正确，不能泛化为所有写入错误。
- **已尝试反证**：完整读过processing_loop有关回写；成功分析能够覆盖错误source，已据此限制影响与严重度。
- **已尝试反证**：DB没有来源推断触发器证据；当前源码三个upsert分支均将excluded.source写回，不会独立识别新浪。
- **已尝试反证**：队列溢出丢弃本身是显式有界取舍，不在此重新报队列策略缺陷；只用于证明补偿不是保证。
- **最小修复**：普通增量首写从原item复制source，只有缺失时才用CLS默认；可保留显式清洗后的content/publish_time及空tags，避免引入其他字段行为变化。
- **最小修复**：同步检查初始、普通、分析回写三条路径的相同source传递，不把修复扩大成更换队列框架。
- **最小修复**：已误标数据缺少可靠原源证据时不得批量从CLS改新浪；仅可据已有受控源记录另批纠正。
- **回归输入与期望（未运行）**：未执行。未来在tests\unit\test_news_subscription_service.py输入source=sina_7x24的第二轮消息，断言首次save_market_news载荷已经正确，即使假queue不消费。
- **回归输入与期望（未运行）**：对照CLS缺省source仍为CLS、初始与分析回写不退化；队列溢出情况下首写来源不变。
- **回归输入与期望（未运行）**：只用fake DAO/queue，不联网、不启动真实消费者或数据库。
- **回退/兼容**：本轮无代码修改。将来仅撤来源透传分支；不得回滚已正确的来源数据或通过关闭回退源回避问题。
- **独立裁决**：[continuation-review-core.json](continuation-review-core.json)；fallback返回source=sina_7x24，初始同步直接normalize原item正确；普通增量却重建source='CLS'再保存。normalize只透传所给source，DAO冲突更新也照写。处理循环使用原item成功时能修正，但首写先于入队，_safe_queue_put允许满队列丢旧项，因此修正非必达。主动反证：不能说所有新浪新闻永久丢来源，正常处理会恢复，影响限定首写误标及未补偿项，所以P3恰当。队列有界丢弃本身是现行取舍，不另报缺陷。
- **裁决限缩/方案约束**：普通增量首写保留原item.source，仅缺失时默认CLS；保留当前正文清洗、时间和tags语义，不靠后续AI回写修正。
- **裁决限缩/方案约束**：未来分别覆盖初始/增量、消费成功/队列溢出，首次保存即应为sina_7x24；正常CLS仍为CLS。
- **完整原始取证**：[continuation-news-ingestion.json](continuation-news-ingestion.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### NI-F03 · P2 · 换Token后旧普通请求仍写入新Token的API负缓存，已有全局熔断保护未覆盖能力缓存

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TushareClient._set_token_core / set_token_async / _handle_api_call](../../data/external/tushare_client.py)；[TushareApiWrapper._handle_api_call](../../data/external/tushare/api_wrapper.py)；[CapabilityProbeService.mark_api_unavailable / is_api_available / get_effective_synced_tables](../../data/external/tushare/capability_probe.py)；[TestTokenInvalidRaceCondition.test_stale_coroutine_does_not_override_token_invalid_after_set_token](../../tests/unit/test_tushare_client_token_race.py)；[换token及probe实际调用段](../../ui/viewmodels/tushare_config_panel_view_model.py)；[P3-REV06-Retry-CircuitBreaker](../../docs/debt/known-technical-debt.md)。
- **契约**：能力缓存描述当前token；换token清空cache及runtime_marks，旧请求结果不得污染新token。probe_api_capabilities已对旧token结果实现归属检查，普通调用仅对_token_invalid写入做了同类检查。
- **触发**：普通API调用在旧token下进入executor并await；用户通过真实UI命令await set_token_async换为新token并清缓存；旧bound func随后返回权限/旧token认证错误。令旧返回晚于新token的probe完成，或选不在probe候选池的API。
- **可达链/当前行为**：ui\viewmodels\tushare_config_panel_view_model.py调用set_token_async，然后可probe；它没有等待所有旧普通请求结束。
- **可达链/当前行为**：tushare_client.py::_set_token_core清两个字典并替换pro；api_wrapper.py已经把旧func提交线程池，因此await期间可交错换token。
- **可达链/当前行为**：api_wrapper.py权限分支先调用mark_api_unavailable(runtime=True)，只有后续_token_invalid写入在loop-local lock内比较entry_token。
- **可达链/当前行为**：capability_probe.py::mark_api_unavailable写同一组合根字典；is_api_available在一小时内返回False，api_wrapper入口据此抛缓存权限错误；get_effective_synced_tables据False排除相应表。
- **可达链/当前行为**：现有token-race完整用例实际构造同类交错，但只断言token及is_token_invalid，没有断言is_api_available或runtime_marks。此为源码证据，不是已运行失败。
- **可达链/当前行为**：api_wrapper权限分支先无条件mark_api_unavailable(api_name,runtime=True)，把False与时间戳写入当前共享缓存，随后才检查entry_token以阻止旧协程设置新token的_token_invalid。全局熔断可正确保持False，但API能力已被污染；下一次新token普通调用入口先读False而fast-fail，get_effective_synced_tables也裁去相关表。
- **已尝试反证**：_token_invalid的entry_token检查有效，未将本项错误描述为旧协程再次熔断新token；污染对象是API缓存。
- **已尝试反证**：probe自身有token快照检查及取消回滚，故不把probe结果丢弃失效算作本根因；普通API不走该保护。
- **已尝试反证**：UI换token后主动probe是补偿但非无条件：旧响应可以更晚返回，且probe候选池不含全部API。
- **已尝试反证**：runtime=True有TTL、不落持久化且get_capability_cache过滤临时False；这些保护不能阻止is_api_available及同步表过滤在当前会话读取False。
- **已尝试反证**：仅在旧请求仍飞行且返回权限类错误时成立；正常换token后无旧返回的场景已有清缓存保护。成功路径mark_api_available也未检查归属，建议同一修复覆盖，但未另计一个缺陷。
- **最小修复**：将普通请求cache写入与token归属校验作为同一状态更新边界；旧generation结果不调用mark_api_available/mark_api_unavailable，也不为新token调度该结果的持久化。
- **最小修复**：优先用请求所属token/pro或单调generation快照，在持相应共享状态锁时校验并更新，兼容__init__同步换token路径；不要仅把一个无锁if放到await之前。
- **最小修复**：保持TTL/权威负缓存区别、原错误传播、全局token熔断、双桶与probe取消行为；不建议全局禁用负缓存。
- **最小修复**：修复代码不会自动清除当前会话已污染条目；如需清理，只对当前token重新判定，不能根据旧错误把新token标无权限。
- **回归输入与期望（未运行）**：未执行。扩展tests\unit\test_tushare_client_token_race.py已有完整交错用例：旧token请求阻塞→set_token_async→新token可用标记/完成probe→释放旧权限错误；断言is_token_invalid=False且is_api_available不被改为False、runtime_marks无旧请求新增项。
- **回归输入与期望（未运行）**：随后用新token再次调用相同API，断言实际新func被调用而不是缓存fast-fail；对TABLE_TO_API_MAP内API断言相应表仍在effective列表。
- **回归输入与期望（未运行）**：增加旧成功响应晚于新token权威False的对照，不能把新False覆盖True；增加同token真实权限失败仍保留runtime TTL的正向用例。
- **回归输入与期望（未运行）**：全部使用受控fake executor/同步点，不发Tushare请求；本轮没有导入测试或启动线程。
- **回退/兼容**：本轮无实现变更。未来回退保留原有全局token熔断及TTL；不得通过恢复旧token、持久化临时False或清空全部质量信息掩盖竞态。
- **独立裁决**：[continuation-review-core.json](continuation-review-core.json)；普通_handle_api_call已在executor等待旧bound func，UI可await set_token_async替换pro并清共享cache。旧权限响应回来先无条件mark_api_unavailable(runtime=True)，只有后面的_token_invalid写入检查entry_token。CapabilityProbeService.__getattr__读同一client字典，不是旧缓存副本；下一调用先读False而fast-fail，effective表过滤也读取它。主动反证：probe有token快照检查，新token全局熔断不会被旧错误覆盖，runtime负缓存1小时且不持久化；但旧响应可晚于新probe，现会话能力仍污染。不是已登记跨调用熔断缺失债。
- **裁决限缩/方案约束**：成功和权限失败的能力写入都绑定请求所属token/pro或单调generation；归属核验与共享cache更新构成同一原子状态边界，兼容同步初始化换token。
- **裁决限缩/方案约束**：旧请求仍向自身调用者返回原结果，不给新token发布旧能力结论；保持TTL、权威False、熔断、限流和取消政策。
- **裁决限缩/方案约束**：未来旧请求阻塞→换token→新probe完成→旧权限失败，断言新能力和effective表未被污染；旧成功也不得覆盖新权威False。
- **完整原始取证**：[continuation-news-ingestion.json](continuation-news-ingestion.json#/findings/2)，原措辞仅在上述裁决边界内采用。

## 5. 待确认待验证及测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D05-Q01 · question · 概念软失败 success 与“所需来源全部成功”消费契约冲突

`AKShareConceptSyncStrategy.sync_one_board` 对空成分直接返回，对未解析代码只警告/跳过；只有异常重试耗尽才登记 failed_boards。因此某板块全部未解析或所有板块空响应，source 仍可 success，facade.is_complete 为真，Scheduler._run_concept_sync 推进当日键。旧映射保护本身是正确的，不能以此宣称本日已刷新。

**反证很重要**：`test_concept_sync.py` 的 `test_partially_resolved_board_not_marked_for_replacement`、`test_empty_constituents_board_skipped_no_record`、`test_all_codes_unresolved_board_skipped_no_record` 明确断言 SUCCESS。这是现存可执行预期，而非单纯遗漏。故本项列**契约待确认，不计新缺陷**。

需要产品/维护者明确 success 指“已安全保留旧数据”还是“本日映射刷新完成”。若后者：保留 upsert-only/禁止删除的安全动作，同时记录 incomplete_boards、置 partial；外层来源聚合与 Scheduler 不推进当日键，后续重试。若前者：消费水位/界面须明确“已尝试且沿用旧映射”，不能继续叫完整刷新。验证：一个正常板块+一个未知码/空板块→旧数据保留、完成状态与批准契约一致；不得简单修改测试期待以制造通过。

### D05-R01 · risk · 仅置取消事件的长 fan-out 响应上界未证明

`_run_concept_source` 构造的新策略不在 DataProcessor.strategies 注册表中；board gather 内没有持续读取 context.cancel_event，外部取数/限速 await 期间可能直到整组完成才轮询。financial/holder/SW 的“每 2 秒”循环检查也不构成单次 await 的两秒上界。

**保护/降级**：TaskManager._cancel_task_impl 同时 event.set 和 asyncio_task.cancel，正常任务取消有硬取消通道；`gather_return_exceptions_propagating_cancel`、各 except 不会把实际 CancelledError 转为 success。不能把“可能 event-only 迟缓”误报为用户取消一定失效或 R2 吞没。E2 需分别实测仅置 event、父 task.cancel、单子任务取消和底层 IO 线程排空；无外网，不用真实长任务。缺 caller 使用 event-only 的决定证据，置信度 medium。

### D05-R02 · risk · 四个财务源公告版本不齐时合并结果的业务完整性尚未闭环

`_dedup_financial_df` 保留 (end_date,ann_date)，四源均有 ann_date 时按该键 outer merge。若某源有较晚修订、另几源只保留早版本，会形成“最新公告行只含部分领域”的结果；缺 ann_date 时又退回 end_date 合并，可能扩大组合。当前未核实真实上游样本/消费者按领域取版的完整链，**不认定确定缺陷**。需要离线已脱敏响应样本、同报告期多版本真库查询及 D06/D08 的 PIT 消费核对。不得直接回退按 end_date 去重；这会重新丢公告版本并违反已实现的 DATA-05。

### D05-T01 · test_gap · 已读测试以单轮返回值/仅 await 为主，不能证明失败后的第二轮恢复

- financial 2370–2450 验证 partial/status 写入，却不再次 run；默认 make_ctx 只给一份 sync_status 返回值，不能模拟真实落库后的分支切换。
- financial 820–915 的 corporate 普通异常用例多数只有 await；未断言 result partial、失败状态或次轮 pending。
- historical 935–980 的指数异常只断言返回 True，未查空日豁免/failed_optional_tables。
- macro 1285–1340 验证本轮 partial，不检查下一轮 global MAX 已推进后的窗口。
- holder 1040–1085 对少量错误仅断言非 None。

这些是**实际读到的断言局限**，不是声称已全文搜索全部测试后证明“仓库绝无覆盖”。建议沿原测试文件增加共享 fake 持久状态的跨轮测试，不新增无关测试框架。

### D05-T02 · test_gap · 真实事务/乱序与取消恢复证据缺失

现有 wrapper、DAO 和同连接调用可静态确认，但 E3 的回滚、并发单调写、主键幂等、部分批写、旧空日纠正均 not_run；动态依赖/隔离未满足 0.2。`set_app_state_max` 的单调性不等于“最大值前无未解决洞”，F01/F02/F04 正是不能用数字单调代替覆盖闭合的例子。D15/D22 应承接实际断言复核和跨链独立复核。

### 续审暂缓/驳回/重复裁决（不计确定问题）

## 6. 改进建议及方案

没有另设无证据的重构/性能建议；五项最小方案均随根因给出，不建议重写同步框架。

推荐实施顺序（本轮不实施）：先 F01/F02 的恢复状态闭合，再 F03 修正错误合法空生产者，接着 F04 的 per-index 窗口和 F05 的聚合状态。Q01 先决策再改；R02 先取得同报告期多版本样本，不能为了省事丢弃 ann_date。代码修复与存量补抓分开授权：新代码不能追溯找回已经丢失的重试集合，补抓须有限窗口、保留成功数据、重跑两次证明幂等。

## 7. 反证与去重记录

| 候选/历史条目 | 处理 |
| --- | --- |
| P2-REV06-External-SchemaValidation | 仍是独立既有债；缺列 warning/None 补列不当作本轮新发现。F01/F02 是合法请求失败的恢复路径，不依赖脏 schema |
| P3-REV06-Retry-CircuitBreaker | api_wrapper 已有限次重试、双桶降速、jitter、成功恢复；不重复建议“增加重试/限流”。跨调用连续失败熔断仍按旧债跟进 |
| P3-DAT07-Restatement | `_dedup_financial_df` 已保留公告日；迁移前不可恢复历史不重新报。R02 仅保留为新组合场景待验证，不指责旧存量 |
| P3-DAT08-SnapshotLookahead | 债文称快照，而当前 SW 同步明确使用 in_date 历史键；未追完模型/查询前不直接复述为当前缺陷，交 D06 对照 |
| P3-DAT08-IndustryPollution | sync_stock_basic 已保留 Tushare 原行业，不建议额外污染回刷；沿用既有全量自愈权衡 |
| aux 不与主财报同事务 | 源码明确 best-effort + upsert；主 reports/step4 marker 同 guarded transaction。未误报 aux 非原子性本身 |
| 稀疏水位 max 跳日期 | attempted 只用于遥测，空日豁免已改 (表,日) 精确登记；不是“高水位前全豁免”。F03 是空日生产者错误，未重复报告已修旧问题 |
| 自我参照 quotes 评分 | QuoteDao 分母不是本批 fetched/saved，且有 quotes_veto；不重复报告“同步缩水仍必然高分”。stock_basic 内容完整性仍不能由写入新鲜度证明 |
| 分页截断被直接当成功 | holdernumber/pledge_stat 已看 truncated 返回 -1，不报未处理分页；F05 只报外层漏聚合 -1 |
| 所有普通异常都传播 | gather wrapper 仅传播 CancelledError；F03 已核实普通异常需消费者处理，不能用函数名假定框架会保护 |
| 概念空/未知码状态 | 旧测试明确 SUCCESS，因此降级 Q01，不把已测试权衡直接包装为 defect |
| EX-0017/EX-0018 | 应用服务轮询/回测持久化的 R5 例外，不覆盖本轮 data/sync 候选；EX-0021 仅解释 scheduler lazy import，并未批准同步假成功 |
| D03-F01～F05 | 分别为宏观读取窗口、URL编码、日期参数转换、学习统计口径、复盘状态重放；与本轮写入恢复/聚合不同根因 |

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已运行（仅静态取证，不是项目测试）

1. PowerShell/自写标准库脚本筛选 scope 的 D05，计算行数与 SHA-256：32/32 内容哈希与冻结一致，退出码 0。首轮和收尾均核对；无业务导入、无持久化源文件。
2. 依据实际工具阅读范围做区间计数：20 read、3 reading、9 pending；10,557/14,961 行，余 4,404。不存在 AST 扫描冒充全读。
3. 独立标准库状态/日期算例 L01～L05：下一日起窗=2024-10-22；已推进披露日不在新窗口；过滤异常只剩 0 个载荷；全局指数续传起点=2024-06-15；1<5 不触发中止。三个纯算术断言通过、退出码 0。**这只是报告推演的算术校对，不是被测函数复现或业务断言通过**。
4. JSON/覆盖/哈希/报告章节的机械自校验结果记录在配套 evidence 的 `artifact_validation`。仅自校验，不冒充独立 review。

### 未运行

| 项目 | 状态/原因 | 将来准入及通过条件 |
| --- | --- | --- |
| 目标 Python 单测 | not_run；本机依赖不匹配、无确认强隔离 | E2；先相同目标 collect-only，记录实际节点/数量，再跑；新增失败反例在修复前失败、修复后通过 |
| PostgreSQL 同步/事务集成 | not_run；无专属目标、无数据库访问批准 | E3；I05 目标存在但尚未逐文件完成执行安全审查，须排除真实 API，独占可销毁数据库及授权后执行 |
| Ruff/Pyright/pre-commit | not_run；非源码修改；pre-commit 含自动修复/依赖副作用，且本机版本不匹配 | 本轮不得用其成功输出代替检视；未来实现按 0.2 再准入 |
| 网络 API 契约/配额/真实返回量 | not_run；明确禁止联网/真实 token | 离线已脱敏契约样本优先；任何外发另批 |
| R20/R21/R24 独立会话复核 | pending；本任务禁止嵌套代理 | 由 Lead/D22 独立新上下文复核，不以本报告自评替代 |

未来 E2 候选命令（**全部 not_run，不是执行批准**；文件确实存在，禁止臆造 pytest 节点）：

```
python -m pytest -n 0 -v --tb=short tests\unit\test_financial_sync.py tests\unit\test_historical_sync.py tests\unit\test_holder_sync.py tests\unit\test_macro_sync.py tests\unit\test_concept_sync.py tests\unit\test_async_utils.py
```

I05 已存在路径：`tests\integration\test_sync_integrity.py`、`test_historical_sync_integrity.py`、`test_data_source_sync_progress.py`；新增补抓/回滚场景由具 E3 条件的后续任务审核后执行。实际 collected/passed/skipped/xfail 全部 unknown，运行用例数 **0**。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

1. **先完成余 4,404 行**，尤其 capability 持久化/探测、client token 生命周期、health 主评估、全部新闻源/语料内核。当前不能声称“每一条采集链均完成映射”。
2. Lead 对五项静态链进行独立复核；Q01 单独处理契约冲突；D06 复核分母可信度/版本/PIT，D11/D12 复核取消边界，D15/D22 接运行与跨链证据。
3. 完成 E2/E3 准入后执行有状态跨轮回归、重复/乱序/部分批/取消恢复及真实事务检查；未满足环境条件不得在共享本机试跑或安装补齐。
4. F01/F02 在发布前处理；修复与有限窗口存量补抓须另获授权。本轮不修改 Plans/scope；仅交付可复核证据，不声称问题已修复或 G 已通过。

| CHECK | 本轮状态 |
| --- | --- |
| 01/02 | 对象、快照、非目标与 32 项已明确；Git 差异/提交历史 unavailable |
| 03 | 遵循只读与不可信输入边界，无秘密实读 |
| 04/05 | 对已读同步核心完成覆盖建模、深挖、反证；未读链明确未完成 |
| 06/07 | 成功/空/权限/失败/乱序/重跑/取消场景均登记；动态验证未完成 |
| 08 | 已读项目、数据管道、第三方、SDK profiles |
| 09/10 | 确定发现逐跳追调用者/DAO/消费者，有反证及精确方案 |
| 11/12 | 七类分别计数；旧债/例外/契约冲突未混入新缺陷 |
| 13/14 | not_run、unknown、未读区间与 G blocked 明确 |
| 15 | 无联网/安装/业务执行/数据库访问/修复/提交/嵌套代理；只写两个产物 |

R5 自查：本轮无新增 DAO/维护代码；已读 reports/marker 经 guarded transaction，指数内层普通异常漏检见 F03，不能扩大声称所有 R5 路径通过。R17：无新增表/列。R21：失败伪装空/成功的静态证据见 F02/F03/F05；自动红线脚本 not_run，独立复核 pending。R18：无 Git 下载快照已声明，实际文件改动仅本报告与配套 JSON。