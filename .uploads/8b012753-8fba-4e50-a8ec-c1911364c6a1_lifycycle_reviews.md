# D02 启动、配置及资源生命周期

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责21文件：20完整语义阅读、0完成有限来源核对、1待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共5项；其中本次新增接受0项缺陷、0项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**发现确定问题，任务保持 `blocked`；不能把本次静态交付视为 G 完成。**

- 主责共 **21 文件**：20 个允许读取的文件、**7,258 行全部分段直接读到 EOF**；20 个内容哈希均匹配 scope 冻结值，标准库 AST 解析成功。部分读取 0、允许读取但未读 0。
- `.env.example` **没有打开、没有内容哈希、没有计算行数**；只核对路径、大小、mtime，记为 `status=pending / read_mode=metadata_only`。因此仍有 1 个受保护 pending，绝不宣称 21 文件全部内容审读。
- 独立复核后保留五项确定缺陷，F05因生产Web支持契约不足降为风险；最高 P1。按 review-policy，F01 对发布形成 **`gate_verdict=fail`**；同时，G 的完整覆盖/动态证据仍不足，`task_status=blocked`。这不是所有维度的全局门禁结论。
- 下述全部项目检查、真实磁盘故障注入、数据库、Flet、多会话和子进程验证均 **`not_run`**。执行过的仅为自己编写的标准库 hash/AST/纯数据反例及报告一致性核对，不是项目测试。

| 类别 | 数量 | 标识 |
| --- | --- | --- |
| 确定缺陷 defect | 5 | D02-F01–F04、F06 |
| 场景遗漏 scenario_gap | 0 | 不与缺陷重复登记 |
| 测试缺口 test_gap | 5 | D02-T01–T05；限定已读取断言，不声称全库无测试 |
| 待确认问题 question | 1 | D02-Q01 |
| 待验证风险 risk | 4 | D02-R01–R03、原D02-F05 |
| 范围外风险 out_of_scope | 0 | 未代替其他主责维度审查 |
| 非阻断建议 suggestion | 0 | 具体修复在对应缺陷下，不另凑建议 |

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D02-evidence.json](D02-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

### 2.1 主责逐文件覆盖

完整哈希、实际读段、真实符号及补充交叉读取见 [D02-evidence.json](D02-evidence.json)。哈希/AST不是语义阅读替代品。

| 文件（仓库相对路径） | 实际读取行段 | 状态及重点 |
| --- | --- | --- |
| `.env.example` | 无 | metadata_only / pending，3,338 字节；不读取内容 |
| `app\application.py` | 1–634 | read；ApplicationSession、run、重试等待、事件接线 |
| `app\bootstrap.py` | 1–830 | read；服务/调度组装、部分失败、PG准备、模式与门控 |
| `app\error_logging.py` | 1–41 | read；分类、严重度、脱敏 |
| `app\startup_controller.py` | 1–299 | read；启动/失败/重试/重配置/升级状态 |
| `app\window_lifecycle.py` | 1–316 | read；关闭、断连、升级退出 |
| `config.py` | 1–84 | read；资源根/用户根、环境配置结构、导入副作用 |
| `main.py` | 1–108 | read；main、自检及Flet入口；未执行 |
| `utils\app_env.py` | 1–43 | read；打包态E2E禁用 |
| `utils\config\__init__.py` | 1–15 | read；域拆分边界 |
| `utils\config\app_prefs.py` | 1–239 | read；偏好/确认标记/返回语义 |
| `utils\config\db.py` | 1–451 | read；模式优先级、URL覆盖、凭证保存、连接池 |
| `utils\config\llm.py` | 1–503 | read；LLM/本地配置、提示词、预设 |
| `utils\config\secrets.py` | 1–560 | read；凭证来源、降级、旧密钥删除前置条件 |
| `utils\config\storage.py` | 1–851 | read；文件锁、RWLock、缓存、原子提交、CAS/回执 |
| `utils\config\sync.py` | 1–128 | read；同步参数边界 |
| `utils\config_handler.py` | 1–634 | read；共享类状态、ContextVar、facade |
| `utils\config_models.py` | 1–725 | read；配置schema、缺失/安全态/迁移态 |
| `utils\constants.py` | 1–17 | read；积分档位事实源 |
| `utils\shutdown.py` | 1–639 | read；步骤、预算、取消、成功标记与资源释放 |
| `utils\singleton_registry.py` | 1–141 | read；注册、reset、逆序atexit与成功短路 |

工具截断位置另行补读：`application.py` 630–634、`storage.py` 470–494、`config_models.py` 698–725；未把截断输出算作覆盖。配置代码可读不等于配置值可读；没有导入 `config.py`，因此没有执行其中 `load_dotenv()`。

### 2.2 依据与边界

已读共同 brief、Plans 任务2.2/D02卡/G、检视核心协议、项目/库插件/消息管道/CLI/前端 Profile、review-policy、[单例生命周期](../../docs/architecture/singleton-lifecycle.md)、[配置约束](../../docs/patterns/config-quality-perf.md)、技术债相关段及例外注册表。

本轮只写本报告和 evidence JSON，不更新共享 scope、Plans、索引；不运行应用/测试，不安装、不联网、不创建数据库、不读取真实用户配置/Keyring/`.env*`。补充读取其他维度的源代码与测试仅用于候选反证，不认领其全文件覆盖。

## 3. 模型与场景

### 3.1 启动、配置及所有权模型

`main → application.run → ensure_defaults / 模式解析 → prepare_database_runtime → ApplicationSession → CacheManager + Coordinator → StartupController → initialize_services`。

- 正常初始化：异步引擎/schema → 调度 Recorder/Compatibility/Executor → TaskManager恢复 → Scheduler/News/Market → 能力预热/校验 → auto-probe → READY。
- 状态归属：StartupController的 `_services_initialized`、Coordinator与Toast为会话级；CacheManager、TaskManager、轮询器、模型管理器、线程池为进程级单例；`_scheduling_services` 和 pending列表是模块全局。**会话级状态不自动隔离进程级资源。**
- 配置：普通保存按「文件锁→RWLock」重读磁盘、顶层merge、Pydantic、`tmp + flush + fsync + os.replace`；MM域另经revision CAS，安全/迁移子域由专用参数保护，同一次替换写回执。替换后异常读回裁决 committed/commit_unknown，不盲目重试。
- 新鲜读取的MM配置校验失败抛 `ConfigFreshReadError`，不伪造空授权；普通 `load_config` 的降级缓存并不具备“磁盘确无凭证”的证明力（F01）。本维度没有金融分数/置信度计算；没有用合法0值填未知的通过结论。

| 资源 | 创建/持有 | 观察到的释放路径 | 边界 |
| --- | --- | --- | --- |
| 异步engine/DAO | CacheManager→EngineManager→DaoRegistry | Step3 经DataProcessor.close→cache.close；异常会话另直接close | DAO引用在成功dispose后统一清空；无DataProcessor时见R03 |
| 同步engine | DataExplorerQueryClient类级共享 | Step7 close_all | 与异步engine独立，不把两个dispose混算 |
| 任务/自动probe | TaskManager与controller返回task | Step0取消并有限等待；probe状态变化时登记 | 晚退出任务/线程不因cancel自动消失，见R01 |
| 新闻/市场/定时器 | 进程单例 | Step1 stop_async/stop；部分启动失败逆序补偿 | 会话断连误触发进程释放，F05 |
| 模型worker | LocalModelManager / NewsSentimentScorer | Step5/5b，经to_thread强制卸载 | 两者启动中guard不同，R01 |
| 用量Recorder | bootstrap调度组合根 | Step5b之后、Step6之前的动态回调 | 数据库已在Step3关闭；失败重试还可能绑定旧代，F02/F04 |
| 线程池/维护命令 | ThreadPoolManager / MaintenanceService | Step6 request_cancel + shutdown(wait=False) | 不宣称运行中同步任务已经退出 |
| PG sidecar | prepare_database_runtime→EmbeddedPostgresService | Step8 stop_sync，经to_thread/30s内层等待 | 内层故障被吞成成功，F03 |
| 单例atexit | register_singleton注册 | 逆注册顺序 `_atexit_cleanup` | graceful标记为True后整体跳过，F03会影响兜底 |
| 订阅/UI监听 | Market/News与ViewModel生命周期共同持有 | 已读Market remove_listener、News stop_async；视图卸载由其他维度主责 | 未读取所有VM订阅/卸载路径，不声称全部已释放 |

registry导入期要求生产单例存在 `_reset_singleton`，重复注册幂等；reset还复位graceful/atexit标记。ConfigHandler是静态类状态，不因没有 `@register_singleton` 就报漏注册。21个生产单例清单来自canonical；**未完整逐一审读21个单例的reset实现**。补读发现CacheManager/DataProcessor的reset仅清引用，是否由fixture先行释放需结合D12/D19核查，不能用registry的“存在性”检查证明资源已释放。

### 3.2 状态与故障矩阵

本表所有“运行证据”均为 `not_run`。

| 场景 / Given→When | 实现状态与结果 | 静态测试证据/缺口 |
| --- | --- | --- |
| 首次且参数缺失→start | LOADING→NEED_ONBOARDING；云key不是硬门槛 | check_onboarding_needed已读；bootstrap测试含该分支 |
| 完整配置→初始化成功 | LOADING→READY，保存onboarding标记失败不应阻止当前使用 | controller捕获异常；未把READY等同所有持久化成功 |
| PG准备失败→Retry/Exit | 错误页→有界退避→重试；等待finally唤醒线程；取消传播 | `_wait_for_*`已读；真实UI/线程数量未测 |
| schema需升级→Upgrade | NEED_UPGRADE→UPGRADE_IN_PROGRESS→SUCCESS/FAILED | controller升级链；驱动/迁移D04，不运行 |
| TaskManager恢复失败→Retry | INIT_FAILED→LOADING；再次组装会替换Recorder，回调仍旧代 | F04；串行多会话测试并非失败重试测试 |
| 部分轮询启动失败/取消 | `_stop_started_services`逆序补偿，外层重新抛原始错误 | 补偿shield保留；内部再取消时的运行证据缺失R02 |
| INIT_FAILED→Reconfigure | LOADING→close旧engine→复位标记→NEED_ONBOARDING | 双击守卫存在；布尔False/close异常未完整处理，Q01/R03 |
| 两会话并存→B连接/断开 | B重新TaskManager恢复；任一非E2E断连全局清理 | F05；仅独立controller单测不能证明共享资源隔离 |
| 正常关闭含未落账用量 | 先Step3 dispose，后动态Recorder flush | F02；Step2只flush TaskManager |
| PG慢停/停止异常→关闭 | Step8捕获后正常返回；graceful可能被误置True | F03；现有单步测试正固化“不抛”而未核对全链成功 |
| 重复关闭 | 同一Coordinator shield同一task，完成后返回记忆结果 | 幂等guard存在；不等于不同Coordinator之间互斥 |
| 外层取消/总超时 | 主路径保留CancelledError；其余清理继续后重抛 | 总超时不是线程硬终止；R02需计时验证 |

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D02-F01 · P1 · 配置降级默认被误作“无密文”证明，启动可删除仍需使用的旧密钥

- **类别/置信度/历史**：defect；high（完整入口→破坏点→后果链）；changeRelation=unknown，无Git不能断言引入时间。
- **位置/契约**：`application._run_session`；`storage.load_config`（449–494）、`secrets.purge_legacy_key_if_safe`（498–530）；补读 `SecurityManager.purge_legacy_key_files`。该函数自身及canonical都要求“确认无仍需解密字段”后才删除。
- **触发输入**：已有AES密文与当前用户数据目录内legacy主/备密钥；包含密文的原配置JSON发生可手工修复的语法损坏，冷缓存加载后回默认；不存在其他可恢复密钥副本。合成复现显式选 `QTRADING_DATABASE_MODE=external`，避免默认embedded启动失败使流程提前停在会话之前；不需要真实DB、Token或Keyring值，purge在判断是否进入向导之前执行。
- **逐跳/当前行为**：`run.ensure_defaults`识别JSON损坏而保留原件→`load_config`读取异常后缓存默认值→默认值的密文字段全空→external准备直接返回→`_run_session`仍无条件调用purge→purge把“默认空字段”当已迁移→SecurityManager删除主key、备key、marker并清 `_key`。即使以后修好JSON语法，保留的密文也不能再解密。另有ValidationError/安全域损坏回默认的同类入口，但不把所有缺默认键的配置都断言为同一触发状态。
- **预期/影响**：无法证明配置实际内容时保持密钥；当前可把暂时的配置格式/读取故障升级为凭证不可恢复丢失。不是宣称所有安装都触发：新安装无legacy文件会短路。
- **反证**：确认ensure_defaults确实保留坏文件，但purge不查看这个结果；keyring/env优先只能保护已经迁出的凭证；SecurityManager明确委托调用方做残留判断，不再二次检查；若存在旧安装副本可人工恢复，降低实际损失，不消除无副本路径。已读purge测试mock正常dict，未模拟“磁盘有密文、加载回默认”。
- **最小修复**：给purge使用不降级的新鲜磁盘完整性读取（沿用storage既有锁序；不能只把默认缓存深拷贝）；缺失/不可读/校验失败/迁移来源不确定一律不删。在同一锁保护内判定实际顶层及provider密文并清理，同时把仍会使用legacy key的写者纳入同一key使用→持久化临界区；做不到则禁用启动期自动删除，不能把单次锁内检查当作并发删除证明。保留无文件无操作，不新增自动凭据轮换。
- **回归输入→期望**：① 原件含合成AES占位字段、破坏JSON闭合符、external冷启动→key/备份/marker删除函数零次，修复JSON后仍可使用原key；② 读取拒绝→同样零次；③ 合法且无密文、确认无legacy写者→允许一次清理；④ 另一进程正在以旧key加密/提交→清理拒绝或完整串行，不形成“新密文已提交、key被删”。
- **验证/回退**：E2后扩展现有 `tests\unit\utils\test_config_handler.py::TestPurgeLegacyKeyIfSafe` 与配置读取用例，本轮not_run。实施前在隔离合成目录验证，不能测试真实密钥。回退只撤销自动清理调用；已误删密钥须靠独立备份/重新录入，不能从密文“修复”，禁止自动重建密钥覆盖现场。

### D02-F02 · P2 · Recorder最终落账排在异步engine释放之后，关机补账必然失去可用DAO

- **类别/置信度/历史**：defect；high，完整静态可达链；unknown。
- **位置/契约**：`_CLEANUP_STEPS`、`ShutdownCoordinator._run_cleanup_steps`、`_step3_close_processor`、`bootstrap._drain_pending_scheduling_coordinators`；用量Recorder的最终flush契约要求DAO仍可用。
- **触发**：DataProcessor已初始化，统一调度Recorder存在 `_pending_settle` 或有month元数据的已观察未提交用量；先前短暂DB失败已恢复，正常退出本应可补交。
- **逐跳**：Step0取消任务→Step1停轮询→Step2仅TaskManager.flush_persistence→Step3 `DataProcessor.close→CacheManager.close→EngineManager.dispose`，DAO.engine置None→Step5b之后才调用Recorder.aclose→`_flush_before_close→_settle_record→_ai_usage_dao_provider→ai_usage_dao.settle_attempt_usage→BaseDao._check_engine`拒绝未初始化engine→Recorder返回persisted=False、告警后清空内存事实。
- **影响/预期**：本可关机补记的真实用量/费用修订丢失，保留预留可能只靠租约降级，并非所有历史费用都错误。应在任务/模型用量观察收敛后、dispose数据库前做最终账本flush。日志warning不是补账成功。
- **反证**：Step2没有Recorder flush；DAO守卫确实阻止僵尸访问，但不会自动恢复原engine；正常早已落库的attempt不受此路径影响；Recorder的告警使它不是静默伪造“已结算”，但不修复顺序错误。既有步骤顺序测试只验证TaskManager flush在Step3之前。
- **最小修复**：将“停止/收敛worker”与“dispose异步engine”拆开编排，保持单例不在关闭时新建；在任务与worker终止、Recorder最终flush之后再由CacheManager直接释放engine。不能只把Recorder随意提前到仍可能出现late usage之前。同步engine及PG仍最后关闭。
- **回归输入→期望**：pending结算一条+已观察未终态一条，DAO写入先失败一次再恢复→关闭时两条在dispose前落库、revision幂等不双计；记录事件顺序为生产停止→最终落账→dispose→PGstop；真实账本不可用时不得声称已落账。
- **验证/回退**：E2扩展 `tests\unit\test_shutdown.py` 并结合Recorder既有测试，E3经另批合成账本再测；not_run。只调整资源依赖顺序，不改账本schema/计价。若回退旧顺序须保留明确未落账警告并阻止以此宣告完整优雅停机，不能重放未知提交造成双计。

### D02-F03 · P2 · PG关键停止步骤把内部超时/异常转换为ok，错误标记优雅停机

- **类别/置信度/历史**：defect；high；unknown。
- **位置/契约**：`ShutdownCoordinator._step8_stop_embedded_postgres`（598–639）→`_run_async_step`→`_execute_cleanup`→`mark_graceful_shutdown_completed`；canonical明确关键失败/超时不得标记成功。
- **触发/链路**：sidecar `stop_sync`超过内层30s（配置允许默认60s），或抛RuntimeError/OSError→Step8捕获只告警→正常返回→外层35s步骤包装记 `ok=True,timed_out=False`→全关键结果为ok时 `cleanup_success=True` 且graceful标记True→窗口路径撤销watchdog，registry退出时跳过兜底。
- **影响**：UI/调用方收到“优雅完成”，实际上线程/PG仍可能退出中或停止失败；异常退出兜底还被关闭。不宣称必然形成孤儿PG，父进程/sidecar实际退出仍需E5/E6验证。
- **反证**：30s内层比35s外层短，故不是外层wait_for必定补救；CancelledError不被捕获，该分支传播是正确的；现有Step8测试只断言异常/超时不抛，未断言最终cleanup_success和graceful。
- **最小修复**：内层超时与异常脱敏记录后重新抛出（或显式返回可失败StepResult并由包装消费），让关键失败沿已有聚合路径传播；仍继续其余清理，保持无sidecar时no-op和R2传播。不要在事件循环上join未退出线程。
- **回归输入→期望**：stop_sync受控超时/抛异常，其余步骤成功→Step8.ok=False，超时标记真实，do_cleanup=False，graceful=False；正常停止→True；取消→CancelledError上抛。
- **验证/回退**：E2运行/扩展 `tests\unit\test_shutdown_step8.py tests\unit\test_shutdown.py tests\unit\test_window_lifecycle.py`；not_run。修复不改变PG停止协议/数据目录；回退前保留“不完整关闭”语义，不可恢复为假成功。

### D02-F04 · P2 · 启动失败后Retry重建Recorder，却没有把新代注册到当前清理器

- **类别/置信度/历史**：defect；high，顺序反例及完整静态链；unknown。与已注释F40的“多会话深度交错”不是同一触发场景。
- **位置**：`application._run_session`注册一次→`bootstrap.initialize_services`在TaskManager.init_db前assemble→`_drain_pending_scheduling_coordinators`一次性消费pending→`StartupController.retry`。
- **触发/当前**：第一次DB/schema成功，组装Recorder A并给coordinator绑A；随后TaskManager.init_db失败返回INIT_FAILED。用户Retry时 `_services_initialized=False`，再次assemble产生B并替换全局provider；pending已空，不补挂B。当前业务用B，但退出只关闭A。单会话、串行重试即可触发，无需罕见并发。
- **预期/影响**：每个活跃调度代均有明确所有者及关闭路径；当前B的内存补账/窗口缓冲无人最终flush，A虽幂等关闭也不能代替B。F02修好先后顺序后，本问题仍会存在。
- **反证**：controller仅成功后置初始化flag，因此失败确实允许重组；失败返回值被controller处理，ApplicationSession正常退出不会rollback；现有F40测试验证串行新会话重新register以及pending清空，未验证同一会话组装后失败再Retry；不是把已知F40内存债再计一次。
- **最小修复**：同一启动会话持有调度组合结果，失败重试选择复用尚有效代，或显式关闭旧代并原子重绑cleanup到新代；将coordinator/组合结果关联作为显式参数，不用“下一次任意assemble”消费全局pending。保持适配器协议验证和未组装返回None。若更换旧代，必须先处理其未落账事实。
- **回归输入→期望**：注入TaskManager首次init失败、第二次成功→记录当前Recorder身份及cleanup绑定，B关闭一次且A若被替换也被有序收口；重复Retry不累积回调/孤儿；并列测试“组装前失败”与“组装后失败”。
- **验证/回退**：E2扩展 `tests\unit\test_bootstrap.py tests\unit\test_startup_controller.py tests\unit\test_application_run.py`；not_run。无需配置迁移；回退保持旧代可用而不是先销毁再换失败，不把新代缓冲丢弃。

### D02-F05 · 独立复核降为条件风险，见第5章

保留原ID以便追溯，不再计入确定缺陷。原“canonical明确支持生产Web多会话”论据撤回。

### D02-F06 · P2 · 嵌套配置的读改写在文件锁外，两个进程保存不同预设仍会互相覆盖

- **类别/置信度/历史**：defect；high（完整UI→配置链+纯数据确定反例）；unknown。
- **位置**：`StrategyMetaMixin.save_preset/delete_preset→ConfigHandler→llm.save_strategy_preset`（429–443）/`delete_strategy_preset`（446–461）→`storage.load_config/save_config`。
- **触发/当前**：A/B进程共享配置文件。A缓存某策略预设 `{old}`；B成功新增B；A随后从旧缓存复制整份嵌套dict并新增A；save_config虽持锁重读最新磁盘，但顶层 `current_config.update({strategy_presets_key: {old,A}})`整体替换为旧嵌套值，B静默消失。删除路径同样可复活他进程已删除的条目。两次save都可返回True。
- **预期/影响**：对不同预设名的编辑不丢失已提交用户方案。原子replace只保证文件不半截，不保证锁外逻辑更新正确；MM域的CAS并不覆盖策略预设键。
- **反证**：deepcopy解决缓存引用污染，不解决新鲜度；跨进程锁确实有效，但保护范围不包括前面的读取/编辑；现有MM测试“普通log_level保存保留另进程MM提交”是不同顶层键，并不能推翻同嵌套键覆盖。现有预设测试mock load/save，覆盖单快照追加和失败不污染，没有这两个独立快照的断言。
- **最小修复**：对预设“新增/修改一个名字/删除一个名字”提供锁内磁盘read-modify-write操作，沿用文件锁→RWLock及schema/原子写；不要传锁外整份预设dict。重名保留现行覆盖语义；不同名合并；删除不存在按现行False。可用revision冲突提示替代，但不为此强迫全部偏好进入MM CAS。
- **回归输入→期望**：两个独立进程先都缓存old，B新增B后A新增A→磁盘含old/A/B；B删old、A另增A→old不复活；故障注入写失败→磁盘及本进程缓存均保持已提交值；同名编辑结果符合明确最后写者语义。
- **验证/回退**：E2扩展 `tests\unit\utils\test_config_handler.py`、`tests\unit\test_model_management_config.py` 的隔离双进程模式；本轮只运行纯dict反例，不运行项目测试/子进程。无schema迁移；回退前保留合成配置备份，按冲突提示处理而非恢复整份旧快照覆盖新值。

### 续审独立接受的发现（当前）

本维度没有新增接受项；不以问题配额制造缺陷。

## 5. 待确认待验证及测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

- **D02-F05 / risk / 潜在P2**：非E2E双Page条件下，`handle_disconnect→do_cleanup`确实释放进程共享池，第二会话`init_db`也会更新活任务历史。但[Flet约束](../../docs/flet/v1-api-constraints.md)明确项目仅桌面端、Web专属不适用；main默认入口和bootstrap未来多session注释不能证明当前受支持生产形态。先裁定支持范围，不能强推多会话架构改造。若支持双Page，再用E2/E5证明A断连不影响B、第二会话不做全表崩溃恢复，并按进程runtime/会话资源划分所有权；若仅单Page，核入口约束。独立复核依据见[review-claims-A.json](review-claims-A.json)，动态均not_run。

| ID / 类别 | 已有证据与缺口 | 精确取得方式 / 承接 |
| --- | --- | --- |
| D02-T01 test_gap | purge测试只mock正常dict；未验证“原件含密文但JSON损坏使load降级默认”的external冷启动组合 | E2对F01四输入断言零删除/允许删除，D02修复任务 |
| D02-T02 test_gap | shutdown顺序测试只比较TaskManager flush与Step3；Step8测试只验证日志/不抛 | 增加真实Coordinator聚合+Recorder+engine替身的顺序和成功标记断言，覆盖F02/F03 |
| D02-T03 test_gap | F40现有串行会话/交错测试，不等于同会话组装后失败再Retry | F04两次initialize及绑定身份/关闭计数，D02 |
| D02-T04 test_gap | 已读application测试Coordinator/CacheManager均为替身；controller实例隔离不证明底层共享安全 | 共享同一池/DB/任务对象的双Page测试，E5生产模式双会话，D02/D17 |
| D02-T05 test_gap | 预设单快照mock测试与MM不同顶层键并发测试，未覆盖同嵌套键旧缓存 | F06独立进程带barrier的读改写输入→磁盘断言，D02/D19 |
| D02-Q01 question | reconfigure仅捕获异常；真实set_onboarding_complete可返回False，未检查；成功启动同样不记录False，但继续READY是已声明设计 | 确认失败持久化的UI契约；若reconfigure必须中止，检查 `saved is True`，保留成功启动非阻断语义。已读reconfigure测试只注入OSError，不能证明False已处理 |
| D02-R01 risk | Step5 LocalModel guard只有ready/path，Step5b另含worker_proc；初次spawn到ready前ready=False/path空。取消等待不等于worker线程停止 | D10/D11沿所有模型调用者确认TaskManager/独立测试任务所有权；E2阻塞spawn/ready并在Step0取消，确认Step5不会漏卸载。尚未证明所有调用入口的补偿，故不升级确定孤儿缺陷 |
| D02-R02 risk | `_run_cleanup_steps`取消后继续各步骤再重抛；wait_for等待其取消处理，不能把timeout_s当严格墙钟上限；to_thread无法强杀 | E2可控事件注入首步总超时+后续慢步骤，量测watchdog/强退/取消顺序；独立核查bootstrap补偿期间二次取消。无真实计时，不报告性能SLA违约 |
| D02-R03 risk | Step3仅DataProcessor实例存在时关engine；早期错误/升级页可能仅有CacheManager；close异常也可能在reconfigure的try外使状态停LOADING | D12/D17复核首次错误页实际组件是否创建processor；E2注入“CacheManager有engine、processor不存在”及dispose异常，断言最终engine可达释放、UI能恢复；不假设所有失败页均如此 |

### 续审暂缓/驳回/重复裁决（不计确定问题）

## 6. 改进建议及方案

不增加无依据的重构、日志或性能建议。第4章已给每项确定问题的具体目标、最小步骤、保留行为、输入→期望、验证准入及回退；均为**待另行授权实施**，不是已执行修复。

推荐实施顺序：F01先阻断破坏性自动删除；F02/F03修复“最后落账→dispose→可信停止结果”；F04修复重试代的所有权；F05先裁定生产Web单/多会话契约再实现；F06修复预设锁内更新。F02和F04分别是顺序、绑定身份两个根因，修一项不能关闭另一项。

## 7. 反证与去重记录

| 候选/线索 | 裁定与已尝试反证 |
| --- | --- |
| URL内部空格认证失败 | **引用 [D03-F02](D03-database.md)**；D02 get_db_url→build_url同根因，不再计数，不提出第二套修复 |
| ConfigHandler缺singleton装饰器 | 驳回：canonical明确静态类状态不纳注册；registry生产类导入时校验reset存在 |
| shutdown/data及config/db跨层导入 | EX-0001、EX-0008–EX-0015、EX-0020已登记；不把这些lazy-import重报架构缺陷，契约闭合归D01 |
| 新闻轮询吞EngineDisposedError | EX-0017允许应用服务优雅停止；不是本报告F02的“最终落账之前dispose”豁免 |
| 同步函数名出现在bootstrap | 不据名字报告阻塞；`P3-App-SyncFunctions-In-Async-Bootstrap`登记短同步设计，纯路径/内存检查无新性能缺陷。keyring/10s文件锁实际延迟仍未知，不伪造耗时 |
| 普通保存总会覆盖另一进程MM提交 | 驳回泛化：文件锁内重读且只写log_level等独立顶层键会保留MM；F06限定同嵌套dict锁外读改写 |
| MM草稿可修改授权/迁移状态 | 已读CAS剥离并回填disk security/migration，schema保留active→sealed条件；未找到本范围内有效调用链绕过，普通save_config带MM不属约定调用方式，不报无调用者漏洞 |
| 异常后一律等于未提交 | 驳回：CAS有replaced阶段与operation_id读回，不能把commit_unknown当失败重试；真实故障窗口仍not_run |
| 所有服务启动失败都无补偿 | 驳回泛化：启动轮询部分失败有逆序停止和shield；F04限定组装后失败返回再重试的Recorder换代 |
| F40深度交错已知NOTE(lazy) | 保留为已知限制，不另计该窗口；F04新增的是同一会话串行Retry，可达且不受其“罕见并发”解释保护 |
| 关闭取消被全部吞没 | 驳回泛化：controller/主shutdown/Step8 CancelledError传播，registered任务主动取消与外层取消不能混同；补偿/墙钟预算列R02，不出安全通过结论 |
| 线程池热重载会取消旧队列 | canonical要求旧池cancel_futures=False；本报告没有把shutdown(wait=False,cancel_futures=True)误作reload行为 |
| CacheManager重建engine仍持旧DAO引用 | 成功close→engine=None→DaoRegistry.sync_engines清空；init_db重建再同步。没有把旧引用作为新增确定问题，真实并发窗口归D12 |

技术债另核对 `P3-ExcUtils-NoteLazy-Classify`、`P3-CON04-Subprocess-Cancel`、`P3-UX06-Startup-SLA-E2E-Gap`：不将已允许基础设施兜底、不可取消restore、未实测SLA重新记为确定缺陷。资源仍需实际收敛证据，债务条目不等于运行验证。

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已执行（均无仓库导入）

1. PowerShell按scope筛选D02；20个允许文件SHA-256与冻结值逐一相等；`.env.example`只 `Get-Item`，大小3,338及mtime已写证据。
2. 自写标准库 `csv/pathlib/hashlib/ast` 对20文件解析，退出0：`primary_files=21, readable_files=20, readable_lines=7258, hashes_match=true, ast_parse=pass`。AST定位报告所用真实函数；没有 `eval/exec` 或导入项目。
3. 纯数据反例L1：A旧缓存加A覆盖磁盘已有B，输出 `keys=old,A`；L2：pending消费后再次组装，输出 `active=recorder-B, callback=recorder-A`。只是所述顺序逻辑的证明，不是运行产品。
4. 两份落盘产物的标准库一致性核对：JSON可解析、范围/行段/哈希/符号真实、六缺陷及七分类统计与报告一致、九章存在、受保护项无hash/读段；实际输出见JSON的 `artifact_validation`，不是项目门禁。

### 未执行

| 项目 | 状态 | 原因/未来验收 |
| --- | --- | --- |
| U02及所有pytest（包括仅collect） | not_run | 用户明确禁止，且准入矩阵E2未建立、依赖不对齐 |
| Ruff/Pyright/pre-commit/文档检查器 | not_run | 不执行仓库入口；pre-commit有自动改写/安装副作用；本轮没有源代码修改 |
| 配置CAS多进程故障、真实磁盘原子提交/权限 | not_run | 需E2合成路径与完整隔离，不能接触用户配置/密钥 |
| 真实DAO/PG停止/迁移 | not_run | 无E3/E6目标及批准，不创建DB |
| Flet双会话、关闭中断、线程/worker残留 | not_run | 无E5环境，不启动应用/浏览器/模型/sidecar |
| 全新上下文独立复核 | pending | 按约定不嵌套代理，由Lead组织D22/独立审读 |

将来E2最小现有命令（**未执行**；新增回归需先失败再修复）：

```
python -m pytest -n 0 tests\unit\test_bootstrap.py tests\unit\test_startup_controller.py tests\unit\test_application_run.py tests\unit\test_window_lifecycle.py tests\unit\test_shutdown.py tests\unit\test_shutdown_step8.py tests\unit\test_model_management_config.py tests\unit\utils\test_config_handler.py -v --tb=short
```

这些既有文件路径已核对；没有声称原测试已覆盖新增组合场景。用例数/skip/xfail/退出码为null，不用0伪装测试已运行。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

- G不足：受保护metadata_only项仍pending；关键故障/取消/多会话/账本持久化证据均not_run；独立复核pending。交付报告不改变Plans任务状态，Lead不得直接标完成。
- 第4章确定问题依完整静态路径成立，不依赖未运行测试才能“猜出”根因；P1发布阻断与动态证据不足同时披露。
- 需D10/D11/D12/D17补足模型任务/订阅/engine及多会话所有权，D19提供经批准隔离验证，D22独立复核结论；没有主读这些维度或重复其文件覆盖。
- CHECK-01/03–12/14/15：在上述声明范围完成；CHECK-02无Git历史/diff，改以冻结hash，历史归因unknown；CHECK-13准确记录not_run但运行证据不足。独立复核与验证不以本报告自评替代。
- 实际新增仅：`reviews\20261101-full-audit\D02-lifecycle.md`、`reviews\20261101-full-audit\D02-evidence.json`。未修改源代码、测试、配置、共享scope/Plans/索引；不提交。