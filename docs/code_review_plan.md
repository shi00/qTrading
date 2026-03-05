# AStockScreener 代码检视执行计划（细化版）

基于 [code_review_guidelines.md](code_review_guidelines.md) 的 43 条规则。这标志着全面审计后的最细粒度、彻底无死角的行动指南。

---

## 波次一：策略引擎层 — ⚡ 最高优先级

---

### 1.1 [quality_gate.py](../data/quality_gate.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 门控逻辑严谨性 | R1 | 人工阅读 `@require_quality` 的内部检查逻辑 | 能正确识别并拦截失效数据源 |
| 异常吞咽 | R41 | `grep -C 2 "except" quality_gate.py` | 失败必须打印包含模块名的 ERROR 日志并阻断流程 |

---

### 1.2 [polars_base.py](../strategies/polars_base.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| `filter()` 上是否有 `@require_quality` | R1 | `grep -n "require_quality" polars_base.py` | 必须在 `filter` 方法上存在装饰器 |
| `filter()` 中异常捕获是否上报 | R41 | 人工阅读 `except` 块 | `except` 中不得出现空 `pass`，至少 `logger.error` |
| 异常兜底与状态上报 | R35 | 人工审查 | 返回空表时，系统兜底逻辑能够让调用方明确感知失败原因 |

---

### 1.3 [fundamental.py](../strategies/fundamental.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 每个 Strategy 子类的 `_filter_logic` 是否有 `@require_quality` | R1 | `grep -n "require_quality" fundamental.py` | 如果装饰器**仅**在基类 `filter()` 上，子类可豁免；否则必须显式标注 |
| `context.get(...)` 获取的每一个键是否在 ViewModel 中有对应装载 | R2 | 列出所有 `context.get('xxx')` 调用，逐一在 `screener_view_model.py` 的 `run_strategy()` 中搜索该键 | 每个 context 键都必须有对应的数据源 |
| 涉及财报字段的 SQL 是否按 `ann_date` 过滤 | R4 | `grep -n "ann_date\|end_date" fundamental.py` | 如有日期过滤，必须使用 `ann_date` 而非 `end_date` |
| `.filter()` 中 Null 值的处理 | R6 | 找到所有 `pl.col('xxx') < N` 比较，检查前后是否有 `.drop_nulls()` 或显式 Null 逻辑 | 每个数值比较前必须明确 Null 的去留策略 |
| 参数化完整性一致性 | R38 | 人工比对 `get_parameters()` 的 `default` 与 `.get(key, default)` | 二者严格相等 |
| 裸数字常量拒绝 | R38 | `grep -n "[0-9]" fundamental.py` 逐行审查 | 所有阈值要么来自 `context.get("params")` 要么声明为顶部常量 |

---

### 1.4 [market.py](../strategies/market.py) 与 [oversold_strategy.py](../strategies/oversold_strategy.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| context 依赖键的上游装载 | R2 | 同 1.3 | 同上 |
| 技术形态策略是否使用复权价格列 | R7 | 搜索引用的列名 `close`, `open`, `high`, `low` | 必须使用复权列（如 `adj_close`）或确认数据源已复权 |
| Null 处理与参数化完整性 | R6, R38 | 同 1.3 | 同上 |

---

### 1.5 [ai_strategy.py](../strategies/ai_strategy.py) + [ai_mixin.py](../strategies/ai_mixin.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| Prompt 安全交叉提示 | R27 | 提示执行者：审查此处 AI 引擎调用时，务必同步跳转检查 `strategy_prompts.py` (见 4.1) | 做到上下文坚壁防注入 |
| 是否有 `@require_quality` | R1 | grep | 存在 |
| AI 送评前是否有数量上限控制 | R25 | `grep -n "head\|max_candidates\|limit" ai_mixin.py` | 存在明确的 `head(N)` 或 `max_candidates` 截断 |
| JSON 解析是否有 `try/except` | R26 | `grep -n "json.loads" ai_mixin.py ai_strategy.py` 并查上下文 | 被 `try/except JSONDecodeError` 包裹 |
| 解析失败的降级策略 | R26 | 人工审查 except 块 | 失败时赋默认低分（如 `score=0`），而非 raise |

---

### 1.6 [all_strategies.py](../strategies/all_strategies.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 所有策略文件是否都被 import | R3 | 交叉比对 `strategies/` 的 .py 物理文件与模块引用 | 必须引入 |

---

### 1.7 [screener_view_model.py](../ui/viewmodels/screener_view_model.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| `run_strategy()` 中 context 装载了哪些键 | R2 | `grep -n "context\[" screener_view_model.py` | 与策略的 `context.get()` 完美匹配 |
| 是否有业务计算逻辑混入 | R40 | 人工审查函数体积，核实事件处理流 | ViewModel 仅充当指挥官，严禁 DataFrame 的计算 |
| 并发写装载锁 | R37 | 审查异步装载 `context` 是否安全挂载 | 并行事件处理需要 `asyncio.Lock()` |

---

### 1.8 [screener_dao.py](../data/daos/screener_dao.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 复合索引与查询墙 | R14 | 此文件含最重型 SQL，必须强对齐 `data/schema.sql` 验证 `WHERE`/`JOIN` | 高频键必须命中 SQL 索引树 |
| SQL JOIN 笛卡尔爆破防御 | R5 | `grep -n "JOIN" screener_dao.py`，逐个检查 ON 子句 | 每个 JOIN 必须包裹 `ts_code` + `trade_date` |
| 财报日期使用 | R4 | `grep -n "ann_date\|end_date"` | 使用 `ann_date` |
| 字段是否在数据字典中注册 | R12 | 人工比对 SELECT 与 data_dictionary.py | 无黑户字段 |

---

## 波次二：存储 + 系统韧性层 — 🔴 高优先级

---

### 2.1 [database_manager.py](../data/database_manager.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 单例守卫 | R28 | `grep -n "_initialized" database_manager.py` | 确保在长生命周期中只初始化一个连接池 |
| 写库资源挂起与并发安全 | R37 | 检查内部对 `_engine` 引用的封装是否有锁保护 | 读写操作分离或安全排队 |
| DB 引擎优雅关闭 | R29 | 检索 `engine.dispose()` | 在 `close` 生命周期能安全切断 IO |

---

### 2.2 全部 DAOs 模块（逐文件）

涵盖: `base_dao.py` 及所有具体业务表的 DAO

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 落盘 Upsert 原则 | R8 | `grep -n "INSERT\|_save_upsert\|DELETE" <file>` | 禁止 `DELETE` 紧连 `INSERT`，全量 `_save_upsert` |
| Schema 同步 | R11 | 取出建表列名与 `cache_manager.py` 比对 | 变动字段全匹配 |
| 索引与查询对齐 | R14 | 提取文件内的所有 `WHERE` 和 `JOIN ON` 子句，人工去 `data/schema.sql` 找对应 `CREATE INDEX` | 高频查询键必须在 SQL 表的索引树上 |
| 并发下的锁事务 | R9 | 检查写查操作是否有 `engine.begin` 或对应锁处理 | DB 锁死避免 |

---

### 2.3 [cache_manager.py](../data/cache_manager.py) 与 [data_dictionary.py](../data/data_dictionary.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 数据库物理重建安全 | R10 | 阅读 `clear_all_cache` 中 `_maintenance_event.clear()` 的应用 | 删表与并行读写安全隔离 |
| UI 字段挂靠 | R12 | 抽取 `data_dictionary.py` 所有键比对 | 无落单列 |

---

### 2.4 [data_processor.py](../data/data_processor.py) + 单例组件抽查

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 单例生命周期防重入 | R28 | 抽查核心单例文件 (`cache_manager`, `data_processor`, 等) | 存在 `__init__` 防重入守卫 |
| 后台任务的收拢释放 | R29 | 检查这些单例的 `close()` \| `stop()` | 完美回收不留僵尸线 |

---

### 2.5 [main.py](../main.py) 与 [config_handler.py](../utils/config_handler.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 全局钩子与兜底 | R29, R35 | 阅读启动与退出上下文 | 所有单例关闭；有未捕获异常 `sys.excepthook` 兜垫 |
| 原子持久化 | R30 | `grep -rn "open.*settings\|json.dump" utils/` | 任何用户设置变动只走 `_save_json_atomically()` |
| 配置断层检测 | R31 | 工具与肉眼核对 `DEFAULT_CONFIG` 与 UI 页读写 | 新增配置有默认值兜底，无 KeyError |
| 凭证泄露防御 | R33 | `grep -n "token\|api_key" main.py` | 代码库与输出绝无明文 |

---

### 2.6 [tushare_client.py](../data/tushare_client.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 节流与重试断点自愈 | R32 | 查阅网络层 | 重试配置接通；按日期分切避免全死 |
| Token 防日志脱库 | R33 | 查阅 `logger.xxx` 请求体 | 脱敏 |
| 统一时间刻度 | R34 | 时区关键词检索 | 锁定 `Asia/Shanghai` |

---

## 波次三：Flet 前端层 — 🟡 中优先级

---

### 3.1 [home_view_model.py](../ui/viewmodels/home_view_model.py)

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 神级方法肥胖症 | R39 | 阅读视图模型的长篇大论方法 | 梳理拆分 |
| 业务逻辑的纯净度 | R40 | 审查界面控制事件 | 严禁在 Model 中写 SQL 或合并 DF |
| 并发任务状态管理安全 | R37 | 对首页进度条共享变量的更新是否有异步挂起保护 | 无错乱 |

---

### 3.2 逐视图常规检查 (其余 6 大视图及组件)

涵盖: `screener_view.py`, `settings_view.py`, `home_view.py`, `data_view.py`, `task_center_view.py`, `onboarding_wizard.py` 以及所有的 `components/*.py`

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| PubSub 配对泄漏 | R15 | `grep -c "subscribe" ; grep -c "unsubscribe"` | 严格 1:1，在 unmount 释放 |
| 红屏断言防御 | R16 | 审查涉及 `await` 之后的 `update()` | 强行挂载 `if self.page:` 防御盾 |
| 路由栈膨胀 | R17 | 审查导航切换方法 | 平级切换 `views.clear()` |
| 越权初始化 | R18 | 查看 `__init__` 函数 | 不调用依赖 DOM 生命周期的函数 |
| 阻塞主心跳 | R19 | 查验非异步挂载的计算 | 耗时通过 `run_task()` |
| 频繁刷帧双发 | R20 | (针对 `virtual_table` 或长列表) | 只执行最后一帧总更新 |
| DOM结构爆炸 | R21 | (针对大量条目前端) | 利用 Virtual 列化保护长列表 |
| 万国文混编 | R22 | `grep -n "ft.Text(\"[^\x00-\x7F]"` | 全部过 `I18n.get()` 提取 |

---

### 3.3 [i18n.py](../ui/i18n.py) 与设置面板

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 字典双生残疾 | R23 | 脚本对比差集 | 空 |
| 字典内容冗余 | R24 | `len(set(zh.values())) == len(zh.values())` | 值长度与语义项一致，无重复文本 |

---

## 波次四：全局坏味道与纪律 与 AI核心网段 — 🟢 常规优先级

---

### 4.1 AI服务层与本地引擎

涵盖：`services/ai_service.py`, `services/local_model_manager.py`, `strategies/strategy_prompts.py`

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| AI 控制阀值上限 | R25 | `grep -n "Semaphore\|max_workers\|head("` | 存在清晰的提额阻断与并发限流 |
| JSON幻觉容错降级 | R26 | `grep -n "json.loads"` 查上下文 | 面向 AI 的解析一定被 `try/except` 包裹且有默认返回 |
| Prompt 防注入 | R27 | 检查外部变量拼接入 prompt 时是否有 XML/定界符包裹 | 用户/新闻输入被牢牢限制在 `<text>` 中 |

---

### 4.2 全量自动化命令行查杀 (全局面审)

此项全自动执行，快速扫刷：

| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 异常生吞 | R41 | `grep -rn "except" --include="*.py" . \| grep -B0 -A1 "pass$"` | 不得出现空处理 |
| 时区乱序 | R34 | `grep -rn "datetime.now()\|utcnow" --include="*.py" .` | 除非带 tz，全部替换为上海时区 |
| 冗余代码死区 (DRY) | R42 | `pylint --disable=all --enable=duplicate-code --min-similarity-lines=10 strategies/ data/daos/ utils/` | AST 高度去重 |
| 僵尸进口墓地 | R43 | 在顶级目录调用 `vulture . --min-confidence 80` | 清理孤魂函数与 imports |
| 日志级别分布合规 | R36 | `grep -rn "logger\.warning\|logger\.info\|logger\.error" --include="*.py" . \| wc -l` (结合人工) | 没有在大循环里疯狂刷屏 info 的病变 |

---

## 波次五：核心系统外围的精细补充 — ⚪ 兜底精研覆盖

针对剩余 30 个基建边缘组件文件的特性审查：

### 5.1 数据同步与网络采集层 (`sync_strategies/*.py`, `news_fetcher.py`, `news_subscription.py`)
| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 断点续传与防断网 | R32 | 各类的拉取逻辑是否有 `retry` 策略，或者依靠 `Task` 层重试 | 具有基本面断链抵抗 |
| 单位与量纲洗防 | R13 | 在将宽表存入库中的前置清洗里 | 处理并注释万元到亿元等量换 |
| 新闻流网络防封禁 | R32 | `news_fetcher/subscription.py` 中查重试和限流 | 防止刷爆第三方接口 |
| 网络异常单例兜底 | R28, R41 | 查阅生命周期与 `except` 捕获 | 底层断网只报 error 不死线程 |

### 5.2 并发与工具体系 (`utils/*.py` 重点)
| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| `thread_pool.py` 异常隔离 | R35/R37 | 工作线程抛出异常时如何收集并反馈给调度器？ | 不能黑洞吞噬并陷入永久等待死锁 |
| `rate_limiter.py` 联动 | R32 | 必须引用并消费 `ConfigHandler` 里的延迟阀值设置 | 保证动态速率反馈 |
| `technical_analysis.py` 公式 | R7/R6 | 处理序列是否假定了传入数据不能带有 null 且是复权后的 | 具备先决防区 |

### 5.3 偏门 UI 残边检查
| 检查项 | 规则 | 验证方法 | 通过标准 |
|--------|------|---------|---------|
| 弹窗销毁流 | R15/R21 | 检查 `ai_settings_dialog.py`，`toast_manager.py` 生命周期 | 用毕销毁对象而非仅仅不可见隐藏 |

---

## 检视输出模板

每个文件检视完成后，输出以下格式：

```markdown
### [文件名] 检视结果

| 规则 | 结果 | 发现 | 严重级别 |
|------|------|------|---------|
| R1   | ✅ 通过 | — | — |
| R2   | ❌ 不通过 | context.get('block_trade') 在 ViewModel 中无装载 | P0 |
| R38  | ⚠️ 建议 | L45 的 `> 50` 建议参数化 | P2 |
```

严重级别定义：
- **P0**：必须立即修复，会导致数据错误、安全裸奔或系统物理崩溃
- **P1**：应在本迭代修复，会导致前端红屏死亡或体验恶化
- **P2**：建议修复，属于架构坏味道、规范偏移与冗余代码改进
