# 数据同步架构

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（data 分层）、§3.1 R2（取消传播红线）；实现架构见本节。

`data/sync/` 下按数据类别组织同步策略：

- `base.py` — 同步基础定义 (`SyncContext` 依赖注入容器、`SyncResult` 结果数据类、`ISyncStrategy` 策略接口，含取消支持)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步
- `concept_sync.py` — 概念板块映射同步（AKShare 东财概念板块 / Tushare 涨跌停）
- `sw_industry.py` — 申万行业分类同步（全局快照，月度更新）
- `name_change.py` — 证券更名历史同步
- `errors.py` — 同步异常定义 (`InitSyncError`)

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表级同步配置与质量监控配置；表级元数据（alias/quality_config/sync_config）仍登记于此，列级结构（列集合、i18n 标签）自 OSS-01 起从 ORM（`models.py`）派生，不再双写。

## Tushare Syncer 设计模式

`data/sync/` 下经 Tushare 拉取的 syncer 通过 `TushareClient` 单例（见 [singleton-lifecycle.md](../architecture/singleton-lifecycle.md#单例模式实现模板)）拉取数据，统一遵循以下设计模式：

### 数据流向

```
Tushare API  →  TushareClient（限流 + 重试 + token 熔断）
            →  ISyncStrategy.sync()（断点续传 + 分块）
            →  BaseDao._save_upsert()（批量 upsert）
            →  quality_gate（数据质量评分）
```

### 限流与重试（C5）

- `TushareClient` 内置 `TokenBucket` 限流器，按积分档位设每分钟请求上限（req/min），限流数值真相源见 `data/external/tushare_client.py` 的 `_POINT_TIER_PRESETS`；档位枚举真相源为 `utils/constants.py` 的 `TUSHARE_POINT_TIERS`（`data/constants.py` 中的同名常量仅为兼容再导出，见 [how-to.md 5.1 Tushare 集成工作流](../guides/how-to.md#51-tushare-集成工作流简述)）。
- 网络错误与限流错误自动重试（指数退避 + jitter），重试上限由 `TushareClient` 配置控制；超阈值后通过 `classify_error()` 分类并触发慢操作告警。
- 外部 IO 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` 触发性能监控。

### 质量门控（C15）

- **`data/sync` 层豁免说明**：`data/sync/` 作为数据同步入口，职责是拉取外部数据并落库（写入 `quality_gate` 评分所需的源数据），自身不直接消费业务数据决策；下游策略层（`strategies/`）强制 `@require_quality` 门控，故 `data/sync` 层不声明 `required_quality_tier`、不挂 `@require_quality` 装饰器。质量评分由 `QuoteDAO.get_sync_quality_score()` 在 syncer 落库后异步评估，syncer 仅负责把数据写入 `*_quality_score` 表供后续策略消费。
- 同步完成后由 `QuoteDAO.get_sync_quality_score()` 评估单日数据同步质量分数（基于相对基准法），低于阈值时标记该日为不完整，下次同步会自动补齐。
- 跨源一致性校验（Tier 3 Gold）由 `data/persistence/data_quality.py` 与 `quality_gate.py` 负责，详情见 [config-quality-perf.md](./config-quality-perf.md)。

### 错误处理（C16）

- 所有 syncer 的 `except` 块必须遵循 CLAUDE.md §3.1 R2：`except asyncio.CancelledError: raise`，禁止吞没取消异常。
- `TushareAPIPermissionError` 由 syncer 捕获并跳过对应 API，不阻塞其他 API 同步；capability 缓存经 `TushareClient.mark_api_unavailable()`（委托 `CapabilityProbeService.mark_api_unavailable`）更新，UI capability 指示器据此反映。
- token 认证失败触发全局熔断：`_token_invalid` 标志置 True 后所有 API 调用 fast-fail，避免无效 token 下每个 API 独立重试刷屏。`set_token()` 重置标志恢复。
  - 该熔断标志经 `tushare_client.py` 的 `_get_token_invalid_lock()`（loop-local `asyncio.Lock`）串行化读写。
- 外部 IO 异常必须经 `classify_error(e, context="general")` 分类后按严重度选择日志级别；敏感数据（token/密码）必须经 `DataSanitizer` 脱敏。

### 取消传播（C18）

- `SyncContext.cancel_event` 作为依赖注入容器传递到 syncer，syncer 在分块循环中检查 `cancel_event.is_set()` 主动退出。

  > **NOTE（R11 防类推）**：任务**取消**信号在 `SyncContext` 内以 `cancel_event` 注入，属 `SyncContext` 的 per-run 实例属性、非类属性；而 [task-manager.md](./task-manager.md) 的 `TaskManager` 任务取消用的是 **`threading.Event`**（见 `services/task_manager.py::_cancel_event`，事件驱动跨线程），非 `asyncio.Event`。两处同为"取消协程"，但产物类型不同。切勿因跨文档类推，把 `asyncio.Event`/`threading.Event` 直接写成 `@register_singleton` 类属性或模块级属性（触发 R11「跨循环复用同步原语」，须经 `get_loop_local()` 获取绑定当前循环）。
- syncer 主动退出时必须 `raise asyncio.CancelledError`（或让其向上传播），由 `TaskManager` 统一处理任务状态转换。
- `ThreadPoolManager.run_async()` 包装的同步阻塞段也需响应取消（通过 `cancel_event` 协作式取消，非强制 kill）。

### 数据库连接生命周期（review03-C13 契约）

**三类生命周期状态与恢复入口**（严格区分引擎身份、查询恢复与停止语义，遵守 R5 僵尸引擎红线）：

1. **首次 schema 初始化与引擎按需创建（`CacheManager.init_db()`）**：
   - `CacheManager.__init__` 在已配置连接串时创建 `AsyncEngine` 并经 `DaoRegistry.sync_engines()` 同步至全部 DAO；若启动时未配置连接串（`self.engine is None`），则在向导完成后首次调用 `CacheManager.init_db()` 时按需调用 `_create_engine()` 创建引擎并执行 `DatabaseMigrator.init_db()`。
   - **幂等短路与 `force` 的真实边界**：当 `self._schema_initialized` 为 `True`、`not force` 且 `self.engine is not None` 时，`init_db()` 直接返回；即便传入 `force=True`，也仅在 `self.engine is None` 时才调用 `_create_engine()` 创建新引擎——若 `self.engine` 非空，`init_db(force=True)` **不会重建引擎或连接池**，仅在既有引擎上重跑 `DatabaseMigrator.init_db()`。
2. **显式释放后的引擎重建（`await CacheManager.close()` → `await CacheManager.init_db()`）**：
   - `CacheManager.close()` 将当前引擎标记为已释放（`self._disposed = True` 且 `engine_provider.mark_disposed(True)` 将该引擎对象永久记入 `_disposed_engines` 集合）、调用 `EngineManager.dispose()` 关闭连接池、将 `self.engine` 与全部 DAO 的 `engine` 引用置为 `None`，并重置 `self._schema_initialized = False`。
   - **R5 僵尸引擎不变量**：引擎释放不可逆，`BaseDao._check_engine()` 在每次操作及维护等待唤醒后检查引擎状态——当 DAO 的 `engine` 已被清空为 `None` 时抛出 `RuntimeError`（提示未初始化）；当外部残留引用仍指向已进入 `_disposed_engines` 的旧引擎对象时抛出 `EngineDisposedError`。
   - 若需在释放后重建引擎（如重新配置数据库连接 `StartupController.reconfigure()` 或彻底重建连接池），必须先完成 `await cache_manager.close()` 清空引擎引用，再调用 `await cache_manager.init_db()` 创建**全新** `AsyncEngine` 实例并同步至全部 DAO；单独调用 `init_db()` 无法替换非空引擎。
3. **数据库服务暂不可达 / 重启时的连接池恢复与重试语义**：
   - **引擎身份保持不变**：PostgreSQL 进程重启或短暂不可达期间，`CacheManager.engine` 未执行 `close()`，引擎引用非空且未标记 disposed，此时 DAO 操作因连接被拒/超时抛出连接异常或 `DatabaseQueryError`（若正处于飞行中的事务或查询连接被服务端中途断开且匹配 `classify_error(e, "db")["code"] == "interrupted"`，则会被转译为 `EngineDisposedError`，但引擎对象本身并未进入 `_disposed_engines`）。
   - **单连接失效 vs 服务不可达**：连接池配置的 `pool_pre_ping=True` 能在数据库服务已恢复可连后，于下次借出连接（checkout）时自动检测并丢弃服务端重启前遗留的失效连接、建立新物理连接；但在数据库进程不可达期间，`pool_pre_ping` 无法自愈，操作将持续抛出连接异常。
   - **不实现后台自动重连循环**：不在数据层内置后台重连轮询，以避免重连风暴并防止掩盖真实故障（PG 崩溃属异常事件，应向用户暴露明确错误并引导诊断）。待外部或内置 PG 服务进程恢复后，常规查询可经既有引擎的 `pool_pre_ping=True` 在重跑任务时恢复连接；若需彻底重置连接池或切换连接配置，则须显式执行 `await cache_manager.close()` 后再调用 `await cache_manager.init_db()`。

- **失败表现与分类**：已释放引擎引用上的操作抛 `EngineDisposedError`（R5 守卫），`engine is None` 时抛 `RuntimeError`；数据库进程不可达或连接中断期间，DAO 执行路径（`_guarded_begin` / `_write_db` / `_save_upsert` / `_read_db` / `_read_db_select`）将 `classify_error(e, "db")["code"] == "interrupted"` 的中途断连异常转译为 `EngineDisposedError`，其余连接被拒/超时异常按 `suppress_errors` 契约抛出原异常/`DatabaseQueryError` 或记录日志返回兜底值，统一经 `classify_error` + `classify_severity`（`log_classified`）按严重度分级记录，禁止静默吞没。
- **UI 提示映射**：连接失败/`EngineDisposedError` 应映射为可操作用户提示（如"数据库连接已断开，点击重新连接"），而非通用错误——该映射属错误反馈路径（报告 05 范围）。

> **操作指引**：新增/修改数据同步源的完整步骤见 [docs/guides/how-to.md](../guides/how-to.md)「5. 新增一个外部数据源」与「5.1 Tushare 集成工作流（简述）」；新增同步表前须更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS`，并遵循本节 CLAUDE.md §3.1 R2（取消传播）与 §3.2（质量门控）约束。

## 同步排障（质量分 / 数据不完整 / 续传中断）

> 本表只列**同步专属**现象，避免与通用排查正本双写：开发期典型问题的「现象 → 原因 → 排查点」见 [CONTRIBUTING.md 排查典型问题](../../CONTRIBUTING.md#排查典型问题)；运行期操作步骤（日志位置 / 诊断包 / 性能劣化三步）见 [how-to.md 运行期排障](../guides/how-to.md#12-运行期排障)。

| 现象 | 可能原因 | 排查点 |
|------|---------|--------|
| 某日质量分低于阈值 | 源端当日数据缺失，或相对基准法判定数据不完整 | 用 `QuoteDAO.get_sync_quality_score()` 复核该日评分；`*_quality_score` 表标记为不完整的日期会在下次同步自动补齐（见「质量门控（C15）」） |
| 同步后某表仍缺数据 / 数据不完整 | 表未在 `TABLE_DEFINITIONS` 注册，或分块写入未覆盖全量 | 核对 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册项；确认 syncer 分块循环与 `_save_upsert()` 的覆盖范围 |
| 续传 / 同步中途中断且不再恢复 | `cancel_event` 被置位未清理，或取消异常被吞没 | 确认 syncer 分块循环检查 `cancel_event.is_set()`，主动退出必须 `raise asyncio.CancelledError`（R2，见「取消传播（C18）」） |
| 续传/同步中 DAO 操作抛 `EngineDisposedError`、`RuntimeError`（Engine not initialized）或连接异常 | ① 抛 `EngineDisposedError` 或 `RuntimeError`：引擎已被 `CacheManager.close()` 释放（如停机/重配期间，R5 守卫生效或 `engine` 已置 `None`），或飞行中连接被中途断开（`interrupted`）；② 抛连接异常/`DatabaseQueryError`：PG 服务进程暂不可达，连接池 `pool_pre_ping` 在服务恢复前无法自愈 | ① 若引擎已 `close()` 释放（`self.engine is None`），须待停机/重配流程结束后调用 `await CacheManager.init_db()` 创建新引擎（禁止在已释放旧引擎上绕过 R5 继续操作）；② 若属 PG 进程崩溃/重启，先确认 PG 服务恢复，重跑同步即可经 `pool_pre_ping=True` 恢复连接，或显式执行 `await CacheManager.close()` → `await CacheManager.init_db()` 彻底重建引擎（见「数据库连接生命周期（review03-C13 契约）」） |

---

## 新闻源采集（离线采集器内核）

新闻源采集器内核落 `data/external/news_sources/`（每源一个模块；`__init__` 不聚合导出，避免多源任务在同一文件冲突）。采集规范：每源限速 ≥ 2~3 秒 / 请求、常规 `User-Agent`、按源去重键、单源连续失败 N 次熔断告警（参照 `data/external/news_fetcher.py` 已有的 Sina / CLS 熔断计数实现）。

与 `data/external/news_fetcher.py` 的边界：离线采集器复用独立解析内核，**不 import** `news_fetcher`（避免循环导入）；在线集成已由 N3-4 完成：个股新闻层（`_fetch_stock_news_core` Layer 2）用新浪个股（同步 `httpx.Client` 直连，IO 线程池内核执行、不套 async 装饰器）替换 `ak.stock_news_em`，`source_label` 新增 `sina_stock`（content 为空串——列表页无正文）；`get_latest_global_news` 为 CLS 主源 + 新浪 7x24 回退（async-native，CLS 空数据 / 失败 / 熔断时启用，回退源独立熔断计数，返回项 `source="sina_7x24"`）。

### 新浪 7x24（`data/external/news_sources/sina_7x24.py`）

`GET https://zhibo.sina.com.cn/api/zhibo/feed`（`zhibo_id=152`，JSON，无签名 / 无 Cookie）。解析 `result.data.feed.list[]`：`id`（去重键）/ `rich_text`（正文）/ `create_time`（`YYYY-MM-DD HH:MM:SS` 北京时间）/ `tag[{id,name}]` / `docurl`；`ext` 为 **JSON 字符串**，含 `stocks[]`。

**A 股 `ts_code` 映射规则（2026-10-08 实测确认）**：`ext.stocks[]` 每项 `{market, symbol, key}`；**A 股 = `market == "cn"`**，`symbol` 形如 `sh688333` / `sz002544` / `bj920670`（交易所小写前缀 + 6 位数字），映射为 `NNNNNN.SH` / `.SZ` / `.BJ`；其余 market（`us` / `hk` / `fund` / `foreign` / `commodity` / `global` / `worldIndex` / `sb` / `uk` / `CFF`，大小写不统一）不映射。

> **陷阱**：`cn` 市场**混入指数 / 板块**——`sh000xxx`（上证指数系列）/ `sz399xxx`（深证指数系列）/ `bj899xxx`（北证 50）/ `si*`·`sih*`（新浪板块码）。这些可映射为合法指数 `ts_code`（指数与个股号段不冲突，如 `000001.SH` 为指数、`000001.SZ` 为平安银行），但语义上非个股，须经 `is_index_code()` 区分后由调用方决定是否保留。时间口径同 `news_fetcher._parse_news_time`：`create_time` 为北京时间文本，按 CST 归属后转 **UTC tz-naive** 存库。

### 新浪个股新闻（`data/external/news_sources/sina_stock.py`）

`GET https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{sh600519}.phtml`（HTML，**GB18030 编码**，每页 40 条；分页参数 `Page`，如 `?Page=2`，实测 200 / 40 条）。

列表行在 `<div class="datelist">` 内，形如 `2026-10-08&nbsp;18:03&nbsp;&nbsp;<a target='_blank' href='URL'>标题</a>`；新闻行 `href` 用**单引号**、页面其余导航链接用双引号，故规格正则（仅匹配单引号 `href='...'`）天然只命中新闻行。去重键为 **URL**（规格 §3.3），页内去重由 `parse_news_list` 负责、跨页由调用方按 `source_id` 汇总。

**时间口径**：行内时间为 `YYYY-MM-DD HH:MM`（**无秒**，北京时间），须先补 `:00` 至 19 位再解析（`_parse_news_time` 只接受 19~23 位，16 位会返回 `None` 导致时间丢失），按 CST 归属后转 **UTC tz-naive** 存库。关联标的由请求方已知，`parse_news_list(symbol=...)` 复用 `sina_7x24.map_symbol_to_ts_code` 得 `ts_code`。

### 巨潮资讯公告（`data/external/news_sources/cninfo.py`）

`POST https://www.cninfo.com.cn/new/hisAnnouncement/query`（表单 `column` / `tabName=fulltext` / `seDate=YYYY-MM-DD~YYYY-MM-DD` / `pageNum` / `pageSize`；JSON）。实测 2026-10-09：单日深市 `totalRecordNum=743`、单页 30 条；板块列用 `column`（深市 `szse` / 沪市 `sse` / 北交所 `bj`）。

解析顶层 `announcements[]`：`announcementId`（去重键，规格 §3.3）/ `secCode`（6 位）/ `secName` / `announcementTitle`（可能含 `<em>` 高亮）/ `announcementTime`（毫秒时间戳）/ `adjunctUrl`（相对 PDF 路径，加 `https://static.cninfo.com.cn/` 前缀后实测 200 `application/pdf`）/ `pageColumn`（板块列）。分页信息取 `totalRecordNum` / `hasMore` / `totalpages`。

**时间口径**：`announcementTime` 为**毫秒时间戳（UTC 绝对时刻）**，先构造 tz-aware(UTC) 再经 `to_utc_for_db` 转 **UTC tz-naive** 入库；**不得按北京时间二次换算**（否则偏移 8 小时）。

**标题清洗**（`clean_title`）：去 HTML 标签 / 实体（含 `<em>`）→ 去公司全称前缀（仅「…公司」后紧跟「关于」时剥离）→ 去「关于……的公告」外壳，保留事件主体；前缀剥离的保守边界见内核 `NOTE(lazy)`。

**标的关联**：`secCode` + `pageColumn` 前缀（实测 `SZCY` 深创业板 / `SZZB` 深主板 / `SHKCB` 沪科创 / `BJS` 北交所）映射 `ts_code`（`.SZ` / `.SH` / `.BJ`）；`secCode` 非 6 位或前缀未知时 `ts_code` 为 `None`。

### 同花顺 7x24（补测结论：不纳入）

2026-10-08 补测：`GET https://news.10jqka.com.cn/tapp/news/push/stock/`（`page` / `pagesize`）返回 200 JSON，`data.list[]` 含 `id` / `title` / `digest` / `url` / `ctime`（unix 秒字符串）/ `stock[{name, stockCode, stockMarket}]`；其余接口（`tapp/news/push/alllist/`、`tapp/news/roll/`、`tapp/news/real/`）实测返回 404。

**结论：不纳入主源。** 理由：① 与新浪 7x24 定位重叠；② `stock[].stockMarket` 为**无文档数值编码**（实测 33=深市 / 17=沪市 / 151=北交所 / 177=港股 / 185·186·169=美股 / 20·36=基金 等），须自维护映射表且随上游变动失效率高；③ 增加第 4 个源的熔断 / 限速 / 解析维护面，收益不足。

### 语料库编排（N1-4，`scripts/collect_corpus.py`）

按源插件化聚合三源（新浪 7x24 / 新浪个股 / 巨潮）→ 清洗去重 → 落 **SQLite 语料库**（`data/corpus/news_corpus.db`，离线、与应用 PostgreSQL 解耦），产出训练语料（规格 §3.3 / §4.1）。内核分三块，均落 `data/external/news_sources/`：

- **`registry.py`**：`SourcePlugin` 抽象（`build_queries` / `fetch` / 归一化 schema）、`CollectQuery` / `CollectParams`、`all_plugins()` / `get_plugin(name)`；内置每源限速 `DEFAULT_RATE_LIMIT_SECONDS = 2.5s`（规格 §3.3 ≥ 2~3s）。
- **`circuit_breaker.py`**：通用单源熔断（`threshold` 连续失败 N 次开路 / `cooldown_seconds` 冷却后 half-open 探活），沿用 `data/external/news_fetcher.py` 的 Sina / CLS 熔断模式。
- **`cleaning.py`**：与源无关的通用清洗（去 HTML/实体/模板语、快讯截断 `FLASH_MAX_CHARS=120`、例行条目过滤）；巨潮标题的公司前缀剥离已在解析内核 `cninfo.clean_title` 完成，此处不重复。
- **`dedup.py`**：字符 3-gram SimHash + 分段分桶索引（`NearDuplicateIndex`）做近重复去重（规格 §4.2.3 同一事件多源转载）；对内容级改写不敏感的敏感度上限见 `NOTE(lazy)`。
- **`corpus.py`**：`CorpusStore`（标准库 `sqlite3`）——表 `corpus_documents` 主键 `(source, source_id)`，`INSERT OR IGNORE` 幂等写库；`publish_time` 缺失存 `NULL`（R21）；SimHash 无符号 64 位经 two's complement 无损往返（SQLite INTEGER 有符号 64 位）；`iter_documents()` 提供只读全量遍历（供离线标注 / 抽样 / 训练切分消费）。

`collect_corpus.py` 为 CLI 编排层，仅依赖 `data/external/news_sources/`；跨请求单独维护指纹索引，做去重并统计清洗/丢弃计数。

### 离线标注消费（N2-1，`scripts/laya_annotator.py`）

`CorpusStore.iter_documents()` 之上承载本地 **laya-multilingual 零样本三分类标注**（choice 原语：利好 / 中性 / 利空）与**人工抽检 Gate**（一致率 ≥85% 才允许批量标注，不达标该路线置 `blocked`）。模型输出视为不可信输入，非法 label / 非有限 / 越界置信度一律丢弃计数，不做猜测性修复（R21）；置信度取 `answer_confidence`（= `max(p)`）而非归一化熵。`laya` / `torch` 不进运行时依赖与 CI，推理全在本地（数据不出本机）。

---

## 完成判定（canonical 入口）

- 同步任务遵循 Tushare Syncer 设计模式，数据流向符合本文件「数据流向」
- 限流与重试（C5）、质量门控（C15）、错误处理（C16）、取消传播（C18）均已落实
- 数据库连接走统一生命周期契约（review03-C13），未重复开关连接

_最小验证命令：_ 改动 `data/` → ruff + pyright + `tests/unit/` 对应用例；涉 DB 查询再运行 `tests/integration/`（需 DB）。
        文档改动 → `python scripts/check_docs_consistency.py`。
