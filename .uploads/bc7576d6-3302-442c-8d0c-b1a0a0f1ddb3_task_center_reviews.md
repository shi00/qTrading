# D11：任务中心、调度、重试与持久化

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责6文件：6完整语义阅读、0完成有限来源核对、0待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共7项；其中本次新增接受0项缺陷、0项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**静态审读发现问题；G 尚未满足，任务保持 blocked，gate_verdict=indeterminate。** 六个主责文件共 **3,103 行全部分段读到 EOF**，完整/部分/未读为 **6/0/0**；六个内容哈希均匹配冻结 scope。没有实施修复，没有运行项目、测试、数据库或第三方库代码，没有安装、联网或读取秘密。

| 类别 | 数量 | ID |
| --- | --- | --- |
| 确定缺陷 defect | 6 | D11-F01、F03–F07；5 项 P2、1 项 P3 |
| 场景遗漏 scenario_gap | 0 | 不与下列缺陷重复统计 |
| 测试缺口 test_gap | 4 | D11-T01–T04 |
| 待确认 question | 1 | D11-Q01 |
| 待验证风险 risk | 3 | D11-R01–R02、原F02 |
| 范围外 out_of_scope | 0 | 跨维度既有问题仅引用，不重新计数 |
| 非阻断建议 suggestion | 1 | D11-S01 |

最高确定严重度 P2。F02因独立复核未能访问同一依赖源码而暂缓，不计确定缺陷；F05/F06/F07另有独立数据状态反例，**不是项目失败测试**。无 Git 历史，变化关系统一为 unknown，不能归因到某次提交。没有发布、修复或合并批准。

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D11-evidence.json](D11-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

### 2.1 主责覆盖与版本边界

依据 `scope.csv` 的 `primary_dimension=D11`，不按目录猜测范围。冻结标识为 `snapshot.json.inventory_sha256=be4d0bbd515585b3cdb45f9a452e34b05230aa5adc8c2de60ee163bff312ac98`。以下是规范化阅读段；原始分段及补读段、完整 SHA-256、方法索引见 JSON。来源为本地 Python 源文件，不涉及生成资源来源核验。

| 文件 | 行数 | 完整阅读段 | 关键入口/边界 |
| --- | --- | --- | --- |
| `services\scheduled_jobs\__init__.py` | 11 | 1–11 | job 注入/分层说明 |
| `services\scheduled_jobs\nightly_prediction.py` | 229 | 1–229 | `_run_nightly_prediction`、`_prediction_logic`、`build_nightly_prediction_job` |
| `services\scheduled_jobs\review_backfill.py` | 103 | 1–103 | `_run_review_backfill`、`_review_backfill_logic`、`build_review_backfill_job` |
| `services\task_manager.py` | 1,440 | 1–1440 | `submit_task`、`_task_runner`、cancel/retry/clear、`init_db`、快照/墓碑/flush |
| `services\task_rebuild.py` | 61 | 1–61 | `INIT_HISTORICAL_SYNC_KEY`、`_rebuild_init_historical_sync` |
| `utils\scheduler_service.py` | 1,259 | 1–1259 | cron、watchdog、misfire、补偿、水位、状态快照 |

排除、失败均为 0。源码不改；当前是归档解压目录，无版本控制兜底，仅新增本报告和 `D11-evidence.json`。没有改 scope、Plans、索引或其他维度产物。

### 2.2 契约及关联阅读

- 已读本轮 `all-run-brief.md`、Plans 2.5/D11 卡及公共 G；[TaskManager 正本](....%5Cdocs%5Cpatterns%5Ctask-manager.md)、[应用服务正本](....%5Cdocs%5Cpatterns%5Capplication-service.md)。
- 已读审计核心协议、project/messaging-data-pipeline Profile、review-policy；结合已提供宪法 R2/R5/R7/R11/R18/R21/R22 判断，不创设新红线。
- 关联源码仅定向读调用入口/消费者：bootstrap 装配、初始化 VM、DataProcessor 日更及初始化、任务中心 VM/View、app_state、引擎 provider、线程池/清理 gather、交易日服务。它们不计入 D11 全量覆盖数，也不替代 D02/D03/D05/D13 主读。
- 时区结论额外静态读取本地 **APScheduler 3.11.3** 分发源码；版本与 `requirements.txt` 锁定一致。只用标准库元数据定位并读取两个方法，没有 import APScheduler。未联网核验发行物来源，不将本地包源码哈希当供应链认证。
- 已查技术债索引及 R1 例外：Scheduler 现有 lazy-import 经 EX-0004–0007、EX-0016、EX-0019、EX-0021 登记，不重新报架构越界；CON04 不重复；D03-F05 不重复。

## 3. 模型与场景

### 3.1 状态和数据边界

```
调用方 submit_task
  ├─ 无可用 loop / unique_key 已占用 ──> None（未接纳）
  └─ 同步占 key(task_id owner) ──> loop enqueue ──> QUEUED
      └─ [exclusive_group loop-local 锁] → [并发 semaphore] → RUNNING
          ├─ 正常返回（目前不判业务 outcome） ──> COMPLETED
          ├─ 普通异常 ──> FAILED
          └─ CancelledError / 主动取消 ──> CANCELLED
QUEUED / RUNNING ──异常进程退出、下次 init_db──> INTERRUPTED
FAILED / INTERRUPTED + factory ──retry_task──> 新 task_id、复用 unique_key
终态 ──clear──> 内存移除 + 墓碑 → 等在途 upsert → DELETE → 已确认墓碑
已确认墓碑 ──容量淘汰──> 无墓碑（迟到 runner 可再次 upsert：F06）
```

每次快照分配 `persist_seq`；DB 冲突更新用 `old.seq < new.seq` 拒绝乱序旧快照。**这只约束同一已存在行；DELETE 后的新 INSERT 不受旧序号保护。** 历史 retry 使用新 ID，不把旧历史行拿来继续写，因此“重启后全局 seq 从 0 开始”本身不是当前重试缺陷。

定时链：`bootstrap._register_scheduler_jobs → SchedulerService.start → cron/watchdog/load_db_state/misfire → submit_task → 业务 factory → 水位/结果 → TaskManager 快照 → TaskCenterViewModel.TaskRow → View 状态徽章`。APScheduler 的 `max_instances=1` 保护它所等待的提交包装协程，不等同于保护提交后独立运行的任务；真正的跨触发去重是 unique_key，同组串行另由组锁负责。

### 3.2 场景矩阵

下表“证据”均为源码或已读断言；运行证据均未取得。T01–T04 给出补证方式，不以测试名冒充测试结果。

| 场景 / Given–When | 当前实现 / Then | 证据及结论 |
| --- | --- | --- |
| 同 key 并发提交 | 在线程锁内占位，仅一方接纳 | `submit_task`；已有 owner 断言，T01 待动态 |
| A 取消、B 接同 key、A 迟到 finally | 仅 owner 匹配才释放，不抹 B | `_release_active_key`；非新缺陷 |
| 无 loop / 关闭窗口拒投 | 返回 None 并释放占位 | `submit_task` 捕获 RuntimeError；无创建协程泄漏 |
| 同组排队 | 先组锁后信号量；等待不占并发许可 | `_acquire_task_slot`；测试有组/跨组断言 |
| 配置热更时同组仍运行 | 推迟换 semaphore，不重置组锁 | `reload_config`；非新并发突破结论 |
| QUEUED 取消 / 等锁取消 | 状态 CANCELLED、cancel event/Task.cancel；runner 传播取消 | `_cancel_task_impl/_task_runner`；T01 |
| 正常返回但已被取消 | 不覆盖 CANCELLED 为 COMPLETED | runner T1 守卫；已读状态模拟断言 |
| 取消后用户逻辑抛普通异常 | 保留 CANCELLED，记录伴随错误 | runner T3 守卫；非“失败覆盖取消” |
| 概念结果 failed/partial；日更完整性 false/未知 | factory 正常返回，runner 标 COMPLETED | **F03**；状态和业务结果脱节 |
| 夜间合法空候选 | 不推进预测水位，返回空结果说明 | 明确设计，不单独认定失败 |
| 夜间预算/未定价拒绝且保存 0 行 | 不推进水位，但仍为 COMPLETED | F03 的同类边界，须与合法空区分 |
| catchup 某日异常/关键表失败 | 不越过失败日；允许连续成功前缀推进；返回 partial | 水位保护存在；任务绿态仍见 F03 |
| 高 seq 终态先到、低 seq RUNNING 后到 | ON CONFLICT WHERE 拒旧 | SQL 文本及既有测试断言；真实 PG 未验 T02 |
| clear 与正在写库的 upsert 交错 | 立墓碑、等在途结束再 DELETE | 已读删除屏障测试；常规路径非新缺陷 |
| DELETE 失败/引擎不可用 | pending IDs 保留，下一次 clear 重试 | 返回 False，但 UI 命令不等待 DB 确认；S01 |
| clear 后取消 runner 迟到，墓碑仍在 | 拒绝迟到快照 | `_begin_inflight_persist`，常规屏障有效 |
| clear 后 ≥1000 次后续清理，再迟到 finally | A 墓碑可淘汰，A 行重新插入 | **F06**；L01 |
| 一笔写 A 未完成，拒投 B | B 可能释放计数两次，把 A 也扣掉 | **F07**；L02，真实停机交错未验 |
| 重启加载 QUEUED/RUNNING | UPDATE 为 INTERRUPTED，再反序列化 | `init_db`；不做自动重放 |
| 历史 retry、已注册/未注册 factory | 从 history 查，注册工厂恢复；否则不显示 retry | `is_retryable/_rehydrate_retry_info` |
| 历史 retry 的组/可取消/参数/目标 | group 明确不持久化；cancellable 默认 False；kwargs 不含原配置快照或目标 | Q01/R02；不将已声明组边界计新缺陷 |
| 自动更新关闭，但存在遗漏日 | watchdog/load 仍提交行情补偿 | **F01** |
| 非东八区主机 | scheduler tz 为上海，四个显式 CronTrigger 却取主机 tz | **F02** |
| 概念/回填 misfire、日更已完成 | 只检查行情补偿，不补原 job | **F04** |
| loader 读取旧日更值后等待其他 key；日更成功 | loader 最后把旧值覆盖回内存 | **F05**；L03 |
| 非交易日/日历未知 | 日更及预测跳过；异常退离线三态，None 不放行 | 交易日正反分支已读；非 weekday 猜测 |
| 跨 loop / 多会话 / 关闭中提交 | 单例 `_loop` 可被后续 init_db 替换；停机无入口关闭标志 | D02-F05 共享所有权关联；R01，不另计同根因 |

### 3.3 取消异常与线程池边界

- 六文件所有 `except` 均已逐项核对，另用 AST 建索引：TaskManager 的 runner、历史解析、快照写、DELETE 的 `except asyncio.CancelledError` 均有 raise；Scheduler 的 watchdog、自举查询、逐日补偿同样 raise。
- nightly 的日历降级及 review_backfill 的 `except Exception` 不会捕获目标 Python 的 `asyncio.CancelledError`（BaseException）；后者普通异常记录后也 raise。task_rebuild 无吞取消 handler，进度拒绝显式抛 CancelledError。概念 `status=="cancelled"` 转取消，不推进水位。
- `cancel_all_running_async` 先取消/持久化，再有限等待 runner，join/persist 超时只告警；`gather_for_shutdown_cleanup(return_exceptions=True)` 接收已取消子任务是清理收集边界，不等于业务协程吞掉取消；外层取消仍可向上传播。没有把超时告警当作线程已退出证据。
- [ThreadPoolManager.run_async](....%5Cutils%5Cthread_pool.py) 用 `run_in_executor`，仅取消等待，不能杀正在运行的同步函数。Concept 传递 cancel_event；其他同步依赖是否合作退出需要逐依赖验证。**已有 P3-CON04-Subprocess-Cancel 承载该限制，本报告不新增同义缺陷，也不声称会强制中止 worker。**

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D11-F01 · P2 · 关闭自动更新后，启动和看门狗仍可提交自动行情补偿

- **类别/置信度/历史**：defect/high；完整入口到数据调用链；unknown，未见同根因既有登记。
- **位置/契约**：`SchedulerService._load_db_state/_watch_config_changes/_catch_up_missed_updates/_catchup_logic`；`_run_daily_update` 明确以 `ConfigHandler.is_auto_update_enabled()` 为全局使能，不能仅关闭 cron 分支却保留同类自动 IO。
- **触发**：`auto_update_enabled=False`，已存在较旧 `_last_update_date` 或可自举数据前沿，交易日历返回遗漏日，且无同组活动任务。
- **逐跳/当前**：非 E2E bootstrap 无条件 start Scheduler → 启动 loader 完成后调用 catchup；或第二轮及之后 watchdog 读到 enabled=False 仍走末尾 catchup → `_catch_up_missed_updates` 不读使能 → `submit_task(unique_key="daily_sync_catchup")` → `_catchup_logic` → `DataProcessor.sync_daily_market_snapshot`。后者无该使能守卫，仍会取数和写库。
- **预期/影响**：关闭自动更新应阻止新自动日更及其补偿；当前仍消耗行情数据源配额、写行情表。非“关闭时自动取消已运行任务”的扩大需求。
- **反证**：正常日更和夜间预测确有使能检查，但 catchup 不是经它们调用；watchdog 的 current_enabled 只参与“配置是否改变”，没有提前返回；组锁/去重只能串行/去重，不能授权自动更新；首轮 watchdog 早退不覆盖 loader 或后续周期。
- **最小修复**：在共享 catchup 入口按自动更新使能拒绝新提交；在等待组锁后开始补偿业务前再次判定，避免关闭前已排队的任务在关闭后发起新 IO。区分手动同步，不将其绑到自动开关；明确关闭对已经运行任务是完成当前步还是合作停止。
- **回归输入→期望**：分别经 loader、watchdog、misfire 进入，False+遗漏日均不 submit；True+遗漏日接纳；同组排队期间 True→False 后不得开始新的自动补偿 IO；手动日更不受影响。
- **验证/回退**：E2 在现有 `test_scheduler_service.py` 补入口级断言，不只测 `_run_daily_update.test_disabled`；与真实 TaskManager 联测。回退仅撤新守卫，不改水位、不删除任务；未运行，可能保留已排队任务的意图需产品明确。

### D11-F02 · risk · 四个 CronTrigger 未显式指定时区：依赖语义待独立核实

- **类别/置信度/历史**：risk/medium；项目侧调用已核，初检记录匹配版本依赖源码，但独立复核无法据模糊包路径访问同一源码；当前不作为确定缺陷；unknown。
- **位置/契约**：`SchedulerService.__init__/_schedule_jobs` 明确承诺不依赖主机时区；`utils.time_utils.get_now` 用 Asia/Shanghai。
- **触发**：主机本地时区不是 Asia/Shanghai，例如 UTC；四次 `CronTrigger(hour=..., minute=...)` 都不传 timezone/start/end。
- **逐跳**：`start → _schedule_jobs → CronTrigger.__init__` 在未指定时区时执行 `get_localzone()` → `scheduler.add_job → BaseScheduler._create_trigger` 对已是 BaseTrigger 的参数直接 return，不走 `trigger_args.setdefault("timezone", self.timezone)`。
- **条件影响**：若匹配依赖确认上述语义，16:30、17:00、18:00、20:30按主机墙钟调度而watchdog用上海日期，UTC主机可能发生8小时时刻偏移；不能在依赖证据复核闭合前将此条件推演作为当前生产确定行为。须取得可访问的完整方法与实际路径，再裁定。
- **反证**：AsyncIOScheduler 确实传入 timezone="Asia/Shanghai"，但仅字符串 trigger 创建路径会继承；现有测试检查 job ID、hour/minute，没有检查 trigger.timezone/真实 next fire。不能以 scheduler.timezone 已正确推翻。
- **最小修复**：四个 CronTrigger 显式传 `timezone=self.scheduler.timezone`，或统一通过 `"cron"`+hour/minute 让 scheduler 构造；保留现有合法时间解析和排序告警。
- **回归输入→期望**：隔离 E2 分别模拟 UTC、America/New_York、Asia/Shanghai 主机，在固定上海交易日比较四 job 的 trigger.timezone 与 next_fire_time；主机 DST 切换不改变上海触发时刻；重载配置后同样成立。
- **验证/回退**：现有 scheduler 测试文件新增真实 trigger 断言；本轮仅读依赖，不实例化库。回退只撤时区参数；修复部署跨越计划时刻时需防一次补偿与旧计划重复，沿用 unique_key，不清成功水位。

### D11-F03 · P2 · 业务失败、部分完成及未知结果被正常返回通道标为 COMPLETED

- **类别/置信度/历史**：defect/high；factory→runner→任务中心消费者完整；unknown。不同业务入口同根因合并一项。
- **位置/契约**：`_run_daily_update._daily_update_logic`、`_catchup_logic`、`_run_concept_sync._concept_sync_logic`、`nightly_prediction._prediction_logic` → `TaskManager._task_runner` → `TaskCenterViewModel._refresh_from_tasks` → `task_center_view._STATUS_*`。任务契约区分 COMPLETED/FAILED，不能把未完成/失败当成功。
- **触发**：①日更返回 `SyncResult(failed_critical_tables=["daily_quotes"])` 或 None/无 is_complete 对象；②概念返回 status="failed"/"partial"；③补偿有失败日期；④夜间预算/未定价阻断而保存 0 行。
- **当前/影响**：各 factory 都可能返回 Message/str，runner 只要非取消、无异常即设 COMPLETED、progress=1、记录 Completed 日志并持久化。概念文案可写“失败”，但徽章仍是 SUCCESS+CHECK；日更不完整还返回 `sched_daily_done`，关键失败原因没有进入任务中心 error。is_retryable 排除 COMPLETED，用户不能从该行重试。水位未推进保护存在，但不能修正任务中心成功表象。
- **反证**：已读概念“不得呈现为完成”的测试仅 mock submit 后手调 factory，断言 message key/水位，没有经过真实 runner/TaskRow；日更 None 测试反而只断言返回 Message；合法空候选是允许的完成结果，不应全部改成失败。TaskManager 对 CancelledError 的终态保护正确，与本项不是同因。
- **最小修复**：在编排边界将关键失败/未知结果转换为明确失败异常或受约束的任务 outcome，再由 runner 映射终态；先用现有 FAILED+具体 Message 保留诊断，避免为本修复无必要扩展 DB 枚举。部分结果可定义显式非成功 outcome，但不得靠 description 猜状态。保留成功、合法空、预算拒绝、partial、取消的不同语义，并让适用的 FAILED 可同会话重试。
- **回归输入→期望**：经真实 submit/runner 驱动 failed/partial/None/合法成功/合法空/预算拒绝六类；失败不能输出 Completed 日志/绿色徽章或进度伪完成；TaskRow.error/描述可诊断；不完整不推进水位；用户取消始终 CANCELLED；正常空候选不被误改失败。校验历史读取后仍保留 outcome。
- **验证/回退**：E2 联合 task_manager、scheduler、nightly、任务中心 VM/View 测试；E3 验证持久化。回退只撤 outcome 映射/异常转译，不全表改历史状态；既有 COMPLETED 但业务失败的存量记录不可仅凭文本批量修正。

### D11-F04 · P2 · 概念/回填 misfire 被转成行情补偿，原 job 并没有补跑

- **类别/置信度/历史**：defect/high；完整 dispatcher 路径；unknown。
- **位置/契约**：`SchedulerService._on_job_missed/_catch_up_missed_updates/_run_concept_sync/_run_review_backfill`；misfire 分支注释承诺回补业务 job 遗漏。
- **触发**：concept_sync_daily_refresh 或 review_backfill 超过宽限期；行情水位已到今天，无遗漏行情日。
- **逐跳/当前**：`EVENT_JOB_MISSED → _on_job_missed` 对四个业务 ID 一律 `create_task(_catch_up_missed_updates(include_today=True,bypass_backoff=True))`；后者 `end <= last_dt` 早退。没有调用原概念/回填入口。即使补了行情，它也只调单日市场快照，不执行概念/ReviewManager 回填链。预测另有 watchdog 后援，不能推及其他 job。
- **预期/影响**：按原 job 补偿，或诚实登记“未补跑/等待下次”而非把不同操作当补偿。当前概念/回填至少延迟到下一次正常 cron；若持续在该时刻繁忙可长期不运行。未声称单次 miss 永久丢掉全部复盘数据。
- **反证**：APScheduler max_instances/misfire_grace_time 不会在错过后自动执行另一个业务函数；`review_backfill` 自身数据库幂等不等于已被调度；已有 misfire 测试只断言创建的是行情 catchup 协程，恰未断言原 job 会运行。
- **最小修复**：按 job_id 派发对应补偿，日更用现有行情补偿，概念走其使能/日期/unique_key，回填走注册 callable；预测保留日更完整性前提及每日付费重试上限。若需先补行情，显式等待其成功再派原 job，不把 submit 成功当业务完成；不可直接越过 F01 开关。
- **回归输入→期望**：日更已是今天时分别注入四 job 的 miss；概念/回填至少一次进入各自提交链，重复 miss 只保留一个同 key 在途任务；禁用概念时不跑；预测日更不完整不跑。补行情失败不得宣称原 job 已补完。
- **验证/回退**：E2 扩展 `TestOnJobMissedCatchup`，用同步屏障观察真实 wrapper/TaskManager；回退 dispatcher 不删任务/水位；上线注意重复投递依赖各业务既有幂等，不能移除 unique_key。

### D11-F05 · P2 · 启动 loader 可用早先读到的旧值覆盖刚推进的内存水位

- **类别/置信度/历史**：defect/high；完整 await 交错及 L03；unknown。
- **位置/契约**：`SchedulerService._load_db_state` 对三 key 连续 await 后无条件赋值；`_mark_*_done_db` 承诺内存 max 单调；R22/任务卡水位要求。
- **触发/逐跳**：start 安排 loader 与 cron/watchdog → loader 首次 get_app_state 读到日更 D29，继续 await 预测/概念值 → 同 loop 日更/补偿成功 `_mark_daily_update_done_db(D30)`，内存及 DB 成为 D30 → loader 恢复执行 `self._last_update_date=db_daily`，回到 D29。没有锁或代际检查；三种水位均可能受同形影响。
- **预期/影响**：启动读取不能回退读取期间已确认的新成功。DB 内仍为 D30 并不阻止本进程用 D29 重复提交行情补偿；预测对 `_last_update_date==today` 的就绪检查也可能重新被关闭。不是声称 DB 的原子 upsert 失效。
- **反证**：标记方法 max、DB `set_app_state_max` 的保护均存在，但 loader 绕过内存 max；“启动 loader 只跑一次”不禁止首次任务和其他协程交错。已有乱序水位测试 mock `_persist_run_date_db` 或只并发两个 mark，没有 loader。
- **最小修复**：每 key 记录 load 开始时的成功更新代际；未发生本地成功更新时按“DB 是正本”正常覆盖启动缓存，发生更新则将 DB 值与本次成功值单调合并，或先完成加载再放行所有消费者。不能简单一律 max 启动配置缓存，否则错误/未来缓存将压过 DB 正本。
- **回归输入→期望**：用两个 Event 卡在 loader 已取 D29、未赋值处，再推进 D30，释放后内存/DB均不低于D30；分别覆盖三 key；另测纯启动缓存为 D31、DB为D29且期间无成功，必须仍遵守 DB 正本策略，不能“修好竞态却保留坏缓存”。
- **验证/回退**：E2 加 loader/mark 联测，E3 确认实际 SQL；本轮 L03 仅赋值模型。回退代际协调只改加载策略，保留 DB 单调写；并发加载和配置失败降级仍需 R01 补证。

### D11-F06 · P2 · 已确认墓碑按容量淘汰时未确认生产者退出，迟到 runner 可复活已删任务

- **类别/置信度/历史**：defect/high；cancel→clear→evict→finally→INSERT 完整；L01 验证成员关系；unknown。
- **位置/契约**：`_cancel_task_impl/_clear_finished_impl/_register_deletion_barrier/_mark_db_deleted/_task_runner/_persist_task/_begin_inflight_persist/_persist_snapshot`；TaskManager 屏障注释明确要求“删除后不复活”。
- **触发**：A 可取消且协程正在合法异步 finally 清理；用户 cancel 后 A 立即成为终态，clear 可以删除 A、建立墓碑并提交 DELETE；A 尚未离开 runner。随后至少 1000 个其他任务完成并被清理，使 `_MAX_DELETION_TOMBSTONES` 淘汰 A 的已确认墓碑。该同会话长尾是条件，不声称常态发生。
- **逐跳/当前**：cancel 不 join A → clear 从 `_tasks` 移除 A，但 `_background_tasks`/runner 局部仍持引用 → DELETE 只等待已经 `_begin_inflight_persist` 的写，不等待未来 finally → `_mark_db_deleted` 允许 A 墓碑成为可淘汰项 → 后续清理淘汰 A → A finally 无条件 `_persist_task(task)` 产生新快照 → 无墓碑放行 → INSERT 在已删行上成功，重启可重新加载 A。
- **预期/影响**：硬删除应在旧生产者及已排队写都不可能再写后才撤销屏障；当前清理后的历史可能复活。无需假设 worker 吞取消：finally 清理完成后仍可正确 re-raise CancelledError。
- **反证**：有墓碑期间和“已经进入 DB 的写”场景确有保护，原测试均验证了它们；容量上限显式存在，但没有 runner/quiescence 判据。`persist_seq` 的 WHERE 只用于冲突 UPDATE，删除后没有旧行可比较，无法防 INSERT；JSON L01不代替真实 SQL。
- **最小修复**：区分终态 UI 与执行生产者已退出；墓碑只有在 DELETE 已确认、runner 已结束、该 ID 已排队/在途写归零后才可淘汰。按 ID 跟踪排队写而非仅写库内计数；runner finally 对已撤销的任务不再生成持久快照。不要仅把容量从1000调大或以清理时 cancel 再发一次代替屏障。
- **回归输入→期望**：屏障卡住 A 的业务 finally，cancel→clear→确认 DELETE；再完成/清理1000个不同 ID，释放 A；DB及重载都无 A，排队旧快照同样不得复活；并测 DELETE失败、超过容量尚未确认项、在途写超时。使用 Event，不用随机 sleep 证明。
- **验证/回退**：E2 扩展 `TestTaskManagerDeletionBarrier`，E3跑真实 PG INSERT/DELETE交错；本轮无 DB。回退时优先临时保留仍有生产者的墓碑，不能通过无限保留所有历史永久消耗内存；不自动删除可能复活的存量行，避免误伤新任务。

### D11-F07 · P3 · 快照调度同步失败路径会递减 pending 两次

- **类别/置信度/历史**：defect/high；确定计数错误，L02；unknown。未把潜在停机丢账升级成已发生数据丢失。
- **位置/契约**：`_queue_persist_snapshot → _schedule_coro._handle_drop → flush_persistence`；计数必须等于尚未完成的被跟踪写，flush 才可确定等待结束。
- **触发/逐跳**：A 仍有一笔 pending；为 B `_queue_persist_snapshot` 加一 → 目标 loop 不可运行，或 `call_soon_threadsafe` 抛 RuntimeError → `_handle_drop` 调 on_drop=_decrement → `_schedule_coro` 返回 False → 调用者 `if not scheduled: _decrement()` 再扣一次。计数 1→2→1→0，A 尚未完成。
- **当前/预期/影响**：B 的 reservation 应只释放一次；现在无关 A 也被扣掉。`flush_persistence` 只看 pending，跨 loop/关闭竞争中可能过早认为排空；实际提前关闭 DB 的可达交错仍须 E2/E3，不据此宣称每次停机必丢任务。
- **反证**：clamp=max(0,...) 隐藏负数而不保留计数正确性；已有“无 loop 计数归零”和“launch 失败归零”测试都从0起，前者无法发现误扣其他写，后者 create_task 异步失败只走一次，与同步 False 路径不同。
- **最小修复**：明确 reservation 单一释放所有者，令 `_schedule_coro` 的成功/失败 drop 都至多调用 on_drop 一次，调用方不再对同一次拒投二次补偿；或每 reservation 使用幂等完成令牌。避免仅去掉 clamp。
- **回归输入→期望**：先让 A 写被 Event 阻塞，令 B 同步拒投；pending应为1且 flush不得返回，释放A后才为0；另测无loop、投递抛RuntimeError、launch抛RuntimeError、正常完成，每笔只扣一次。
- **验证/回退**：E2 现有两个错误路径测试类补“已有其他pending”的断言；L02只是整数模型。回退只影响计数补偿，不能把等待超时加大当修复；停机排空与异常写成功确认仍须分别表达。

### D11-F02当前裁决：依赖语义证据已闭合，恢复为P2确定缺陷

已直接读取与开发锁一致的APScheduler 3.11.3完整CronTrigger.__init__及BaseScheduler._create_trigger。前者缺timezone/start/end时用本地时区，后者原样返回已构造BaseTrigger；四个项目cron调用未传timezone，scheduler级上海时区不补救。详见[dependency-timezone-review.json](dependency-timezone-review.json)。这是对首轮暂缓项的恢复，不是新增第二个根因；当前仍未执行调度器或项目测试。

最小修复：四处显式传scheduler.timezone或交字符串cron构造；回归在UTC/纽约/上海主机比较真实next_fire_time，保留unique_key与水位、避免部署跨时刻重复补偿。首轮下方risk文字是当时证据不足的历史记录，不再作为当前分类。

### 续审独立接受的发现（当前）

本维度没有新增接受项；不以问题配额制造缺陷。

## 5. 待确认、待验证及测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

| ID / 类别 | 缺口、已得证据 | 取得方式 / 承接 |
| --- | --- | --- |
| D11-T01 / test_gap | 已读状态守卫测试模拟赋 CANCELLED；同组串行用 sleep；尚无本轮真实取消/完成/clear/重入交错运行证据 | E2 使用 Event/Barrier 控制真实 submit→runner；覆盖同键取消重投第三次拒绝、等组锁取消、多loop、关闭中提交。D11 后续验证 |
| D11-T02 / test_gap | seq 测试断言 SQL 含 WHERE，不执行 PG；墓碑测试为内存 fake DB且未跨容量；flush错误测试从0计数 | E2补 F06/F07；E3真实“高seq先到/低seq后到”、DELETE/迟到INSERT、回滚/引擎释放。D11联D03，不运行集成 conftest |
| D11-T03 / test_gap | rebuild 测试用 `SAMPLE_KWARGS={"quick":False,"days":250}`，替身接受 **kwargs；真实 `initialize_system(progress_callback=None, quick=False)` 不接受 days。生产提交目前 kwargs={}，不能据此报现有任务必失败 | E2用真实签名约束替身并从 serialize→init_db→retry→factory 全链验证；原值、非法JSON、合法非对象JSON、工厂未知/版本不兼容、设置变化。D11联D13 |
| D11-T04 / test_gap | scheduler测试已读只断言Cron字段/返回Message/水位；未验证时区、关闭开关的catchup、原job misfire、loader交错及最终TaskRow状态 | E2补 F01–F05 的真实runner/状态消费者；将四个业务job success/partial/failed/unknown/cancel逐项断言。D11联D13/D14 |
| D11-Q01 / question | 崩溃重试应沿用原参数/原配置，还是按当前配置续传？原VM提交没有kwargs；rebuild读取当前 init_history_years、当前日期、当前DataProcessor。group不持久化是正本已声明边界；cancellable也未存列，历史重试默认False | 产品/架构确认后固化重建契约及版本；若要求原样重建，保存必要无秘密参数与任务能力，不序列化闭包；不把合法当前配置重算擅自判错 |
| D11-R01 / risk / P2 | E2/E3尚无：真实取消传播、多loop、shutdown有限等待、快照失败/DB提交结果、app_state读取失败语义。`get_app_state`可能失败返回None而loader仍置loaded=True；配置缓存是last-writer-wins，不能当实时DB权威 | 匹配隔离依赖/专属DB并明确批准后故障注入；结合D02共享生命周期复核。未证明具体错账或重复费用，不新增确定缺陷 |
| D11-R02 / risk / P2 | 真实DB目标未知：没有读取URL/用户配置/凭据。TaskManager写时取当前CacheManager实例；DELETE取当前cm.engine；scheduler用当前engine provider；rebuild构造当前DataProcessor，无任务目标指纹 | E3使用非秘密合成目标A/B，观察目标指纹、engine generation、schema与任务记录归属；配置切换/恢复备份时明确拒绝或确认跨目标重放。不能声称已验证“连对真实库”或存在实际串库事故 |

这些是实际读取或执行证据不足，不是断言全仓库没有相关测试。Q01 不隐含批准新增 schema；如后续需要列变更，仍按迁移门禁单独授权。

### 续审暂缓/驳回/重复裁决（不计确定问题）

## 6. 改进建议及方案

**D11-S01 / suggestion / 非阻断：把“接纳、执行结果、持久确认”作为三个可观察维度。**

当前 submit 返回 task_id 仅表示已投递；APScheduler包装函数返回不代表TaskManager业务完成；`_persist_snapshot`可记录失败后返回、`flush_persistence`只表示tracked pending归零；clear命令不向VM返回持久删除的成功/待重试结果。不能把这些接口误解释成“DB已经保存/删除成功”。`get_jobs_status_snapshot`对未知字段保留None是正确的，不应为了面板好看改成0/正常。

建议在现有任务记录/事件中关联 scheduler job_id、最近任务ID及失败/持久待确认信号，供状态面板按实际任务结果更新；清理失败显示“本地已移除，持久清理待重试”。收益是可运维而不假成功；代价是订阅/状态契约及可能的持久模型变更。仅在需要跨重启审计/故障解释时采用，不引入第二个任务状态真相源。失败原因继续经DataSanitizer，保留None；新增确认能力回归断言“写失败不等于flush成功落库、取消不误记失败”。回退UI增强不丢原始任务记录，不把unknown降为success。

## 7. 反证与去重记录

| 候选或关联结论 | 本轮独立核实 / 反证 | 处置 |
| --- | --- | --- |
| 取消后Completed/Failed覆盖 | runner T1/T3 + CancelledError 分支守卫存在 | 驳回该候选；F03只针对业务正常返回 |
| 同键旧owner释放新任务key | 所有普通释放路径比较task_id owner；停机才无条件clear | 驳回普通运行路径候选；停机入口屏障待R01 |
| 同组任务挤占全部许可 | 组锁在信号量外；reload不换组锁 | 驳回 |
| DB乱序写必把终态改回RUNNING | seq WHERE守卫存在 | 驳回一般结论；T02补真实SQL证据 |
| 墓碑功能完全无效 | 未淘汰时、在途写/DELETE失败路径均有保护 | 驳回泛化；仅F06保留容量+未退出生产者反例 |
| 新登记墓碑在pending更新前可能被淘汰 | `_clear_finished_db` 会再次登记全部pending targets，写入准入后的在途也被等待 | 不另报“该顺序必然复活”；F06使用已确认墓碑后续淘汰，不依赖此候选 |
| 重启seq归零使retry不更新 | retry新建ID；历史不直接续写旧ID | 驳回单凭seq归零的结论 |
| 非法JSON默认参数必误重跑 | 非JSON文本关闭factory；合法非dict被降为{}是边界缺口，但生产serializer输出dict | T03，不把损坏DB/外部写入当已发生生产缺陷 |
| 崩溃重试丢exclusive_group | 正本明确仅进程内有效，模型无列 | 已知边界；Q01，不新计缺陷 |
| ThreadPool cancel不会停止同步函数 | 本轮直接读run_async及CON04索引 | 引用P3-CON04-Subprocess-Cancel；不重复 |
| 同run重放回退复盘状态 | 已读D03-F05；当前nightly生成新run_id，backfill按已有状态筛选 | 只引用[D03-F05](D03-database.md)，不新增D11发现 |
| 多会话重新init_db/单会话断开全局停机 | 当前bootstrap per-session guard、TaskManager.init_db全表残留恢复及单例_loop足以确认共享边界；未复述未读停机实现 | 引用D02-F05共享生命周期根因；不新增同义发现，动态R01 |
| 日更/补偿关键失败仍推进最终日 | catchup记录failed_dates与连续前缀；日更is_complete缺省False；日历未知跳过 | 驳回一般“直接推进”结论；F05单独保留loader回退 |
| nightly无候选不推进水位 | 代码明确保留重试，同时watchdog每天最多补触发一次 | 接受设计；不声称必须无限自动重试付费预测 |
| 日更乱序mark使DB水位回退 | 内存max + set_app_state_max条件原子upsert；配置缓存LWW是声明的启动缓存 | 驳回mark路径倒退；F05针对另一路loader |

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已执行：只读、标准库静态取证

| 检查 | 命令/方法 | 实际结果 / 限制 |
| --- | --- | --- |
| 范围/哈希 | PowerShell here-string→`python -`，`csv.DictReader`筛选D11，`pathlib.read_bytes`+`hashlib.sha256` | 6项均匹配scope；总3103行；退出0 |
| 语义阅读 | view明确行段，截断处补读；方法位置再由`ast.parse`索引 | 六文件全读，不以AST替代阅读；退出0 |
| handler索引 | 同上标准库AST遍历ExceptHandler | 主责显式CancelledError handler均有raise，结合完整异常体检查；退出0 |
| APScheduler依赖语义 | `importlib.metadata.distribution("APScheduler")`定位源码，读取/AST输出CronTrigger.__init__与BaseScheduler._create_trigger | 本地3.11.3与锁定一致；两份源码hash存JSON；无库导入/调用；退出0 |
| L01（F06） | 独立OrderedDict/set数据模型：A已确认删除、再清理1000个ID | A墓碑保留=False，迟到upsert准入=True；只是逻辑成员关系 |
| L02（F07） | 独立整数reservation模型 | A应pending=1，B同步drop两次释放后报告0；只是算术 |
| L03（F05） | 独立顺序赋值模型 | mark后20261030，loader恢复后20261029；只是事件序列 |
| 产物检查 | 仅对本轮两产物作JSON/字段/链接/计数/原文件hash校验 | 实际命令及结果存JSON `artifact_validation`，不是项目测试 |

Python环境沿用0.2矩阵的共享Windows/Python3.13环境；不把已存在的APScheduler匹配扩展为其余依赖匹配。静态反例是本轮手写数据模型，没有eval/exec项目或抽取的仓库代码。没有其他脚本文件/临时目录或缓存写入。

### 未执行

- **项目运行检查 0、项目测试 0、DB 0、网络 0**；用例数/skip/xfail均为not_run，而不是“0失败”。
- Ruff、format、pre-commit、pyright、项目检查脚本均未执行；既不是实施变更，也未完成执行安全准入。没有默认开启测试收集或conftest。
- U11 当前 unavailable；缺匹配依赖和确认的E2强隔离；持久化还缺E3、专属可销毁DB及明确批准。不能在本机为验证审计顺手启动服务或安装。
- 后续仅在满足准入后，候选已有文件命令：
`python -m pytest -n 0 tests\unit\test_task_manager.py tests\unit\test_scheduler_service.py tests\unit\test_task_rebuild.py tests\unit\test_nightly_prediction_job.py tests\unit\ui\test_task_center_view_model.py tests\unit\ui\test_task_center_view.py -v --tb=short`
文件存在已核，不把计划新增用例伪造为现存node。命令**本轮未运行**；真实PG交错用例尚须在经审核的E3测试方案中补齐，不能虚构集成节点。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

1. 七项静态缺陷可交付修复建议，不等于修复已实施；建议先处理F01/F02自动动作范围和时区，F03结果真实性，再处理F04/F05恢复控制及F06/F07持久屏障。
2. 取得E2/E3证据前，G不足且task2.5仍blocked。交由Lead独立复核发现、核对覆盖、统一索引；本担当不更新Plans/索引，不嵌套代理或提交。
3. R21相关结论/后续修复须独立会话复核；本轮未做，`review.status=pending`。D02/D03既有结论不能替代本维度验证；Q01和R02未闭合时不声称崩溃恢复“完全原样、同目标”。
4. 不能依据本报告对真实DB做存量状态修正、回滚水位或清理任务；那需要单独数据修复授权、备份和逐行可恢复判据。

| CHECK | 状态 |
| --- | --- |
| 01 模式/基线/非目标 | 已固定六文件快照及只读授权 |
| 02 全量范围 | 6/6完整；非Git，无diff归因；关联文件仅局部 |
| 03 规则/不可信输入 | 已核；未执行仓库嵌入命令 |
| 04 影响模型 | 状态图、持久及调度消费者已列 |
| 05 三轮 | 全读建模、候选深挖、反证去重完成；动态未做 |
| 06 场景 | 矩阵含取消、恢复、未知、并发、超时 |
| 07 遗漏 | T01–T04、Q01、R01–R02已显式登记 |
| 08 Profile | project、messaging-data-pipeline已读 |
| 09 消费者/schema | 定向读TaskHistory、TaskRow/View、app_state及装配，不越权读真实配置 |
| 10 发现完整 | 每项有入口、触发、影响、反证、最小修复、回归、回退 |
| 11 分类/历史 | 七分类分开；unknown历史，不归因某提交 |
| 12 去重 | CON04、D03-F05、D02-F05及已声明组边界不重复 |
| 13 验证真实性 | 静态取证与运行项目严格分开；项目0次 |
| 14 门禁 | indeterminate/blocked；E2/E3与独立复核待取得 |
| 15 授权 | 仅写D11两文件，无外部/业务副作用 |