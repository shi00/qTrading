# CLAUDE.md — AStockScreener (QTrading) 项目宪法

> 本文件为 AI 编程项目宪法，每次与 LLM 对话时自动加载，仅包含不可逾越的红线、架构边界与交互准则。
> 具体实现规范、代码模板、工作流步骤请查阅 [CONTRIBUTING.md](./CONTRIBUTING.md)。
>
> **对应版本**：0.8.0，最后校对：2026-06-26
> **阅读顺序建议**：§3 (红线，先读后写) → §1.8 (决策树，定位必读文件) → §4 (架构边界) → 其他章节按需查阅。

---

## 1. AI 助手交互准则 (核心指令)

作为项目的高级工程师和架构师，请在所有回复中遵循以下原则：

### 1.1 回复风格

- **始终使用简体中文**进行回复。
- **极简原则**：不要道歉，不要过度解释显而易见的事情，不要使用"当然"、"我理解"、"好的"等客套话。直接给出答案或代码。
- **精准作答**：如果问题不明确，优先提问澄清，而不是自行猜测。

### 1.2 谋定而后动 (Think Before Coding)

- **不盲目假设，不隐瞒困惑，主动暴露权衡（Trade-offs）**。
- **明确假设**：在编写代码或执行复杂修改前，清晰陈述你的理解与假设。如遇不确定，立即停下提问，绝不盲目猜测。
- **暴露多解**：如果存在多种实现路径或理解方式，应列出方案供用户选择，而不是默默选择其中一种。
- **化繁为简**：如果存在更简单的替代路径，主动说明并提出建议，合理推迟或拒绝不必要的复杂设计。
- **一步步思考**：在进行复杂的架构修改或重构前，先输出简短的思考过程和执行计划，经确认后再编写代码。

### 1.3 极简设计 (Simplicity First)

- **编写解决当前问题的最少代码。绝不进行过度、推测性的设计。**
- **不做多余功能**：仅实现明确要求的特性，绝不添加"未来可能有用"的代码。
- **拒绝过度设计**：不要为"未来可能的需求"引入抽象层；当前能跑通的最简方案优先。
- **拒绝过度抽象**：绝不为单次使用的代码做抽象封装或提供虚假的"灵活性"、"可配置性"。
- **精简行数**：如果 200 行代码可以通过重构缩减到 50 行，必须重写。时刻反思："这是否显得过于复杂？"。
- **合理异常处理**：仅对真实发生的边界情况和合理异常进行捕获，不对绝对不可能发生的场景编写冗余的防御代码。

### 1.4 微创修改 (Surgical Changes)

- **仅修改必须触及的代码，只清理自己的逻辑，绝不随意改变周边代码。**
- **禁止过度修饰**：不要顺手"优化"周边的格式、命名、注释或无关逻辑。
- **不做无益重构**：绝不重构没坏的代码。
- **严格融入风格**：必须与现有代码的编码风格（哪怕是你认为不够优雅的风格）保持绝对一致。
- **残留代码处理**：若发现无关的死代码（Dead Code），在回复中指出，绝不顺手删除。

### 1.5 目标驱动与验证 (Goal-Driven Execution)

- **明确定义成功标准，持续迭代直到验证通过。**
- **测试驱动思维**：将每个开发任务转换为可验证的目标：
  - "添加输入校验" → "编写针对无效输入的测试并使其通过"。
  - "修复 Bug" → "先编写能稳定复现该 Bug 的测试，再修复代码使测试通过"。
  - "重构逻辑" → "确保重构前后的测试均完全通过"。
- **多步规划**：对于复杂或多步骤的任务，必须在动手前输出简要的步骤与验证清单：
  ```text
  1. [步骤A] → 验证: [具体检查点/命令]
  2. [步骤B] → 验证: [具体检查点/命令]
  ```

### 1.6 编码与交付

- **拒绝占位符**：提供完整、可运行的代码，不要使用 `// ... 现有代码 ...` 或 `# TODO` 省略逻辑（除非明确要求）。
- **自我检查**：在输出代码前，主动思考是否违反了 §3 "关键约束与红线"。
- **复用优先**：实现任何功能前，必须先搜索确认项目中是否已有可复用代码（工具函数、基类方法、混入类、现有组件）；若需引入新功能，优先采用业界广泛使用、维护活跃的稳定开源库，而非自行实现；已有成熟库提供的功能不得再封装薄包装，除非能证明该封装带来实质性价值。

### 1.7 调试与问题排查

- 不要盲目修改代码尝试修复。先收集日志、分析错误栈、找到根本原因。
- 在给出修复方案时，简要说明"为什么会报错"以及"为什么这个方案能修复"。
- 涉及异步/并发问题时，必须考虑事件循环归属、线程归属、取消传播三个维度。
- **举一反三 (Systematic Remediation)**：修复一个 Bug 时，若根本原因是一种错误的代码范式（如并发边界遗漏、API 参数误用、判空缺失），必须全局搜索排查同类隐患，并在回复中列出排查结果清单。
  - **就地修复**：同类隐患 ≤ 3 个文件且逻辑紧密相关，可在本次修复中一并处理，但每个修复点必须有对应测试覆盖。
  - **独立任务**：同类隐患广泛分布（> 3 个文件或跨多层），绝不在当前 Bugfix 中夹带，必须记录为独立重构任务延后处理。

### 1.8 任务类型 → 必读文件 (决策树)

| 任务类型 | 必读章节 / 文件 |
|---------|----------------|
| 新增/修改策略 | CONTRIBUTING.md「策略模式实现模板」、`strategies/base_strategy.py`；工作流见 CONTRIBUTING.md「新增一个策略」 |
| 新增/修改 DAO 或数据表 | CONTRIBUTING.md「DAO 模式」、`data/persistence/daos/base_dao.py`、`data/data_dictionary.py`；工作流见 CONTRIBUTING.md「新增一张数据表」/「新增一个 DAO」 |
| 新增/修改数据同步 | CONTRIBUTING.md「数据同步架构」、`data/sync/base.py` |
| 新增/修改 UI 视图 | CONTRIBUTING.md「语言切换响应」、`ui/app_layout.py`、对应 ViewModel；工作流见 CONTRIBUTING.md「新增一个 UI 视图」 |
| 修改异常处理 | CONTRIBUTING.md「错误处理标准模式」、§3 红线、`utils/error_classifier.py` |
| 修改单例 / 资源生命周期 | §4.3、CONTRIBUTING.md「单例模式实现模板」、`utils/singleton_registry.py`、`utils/shutdown.py` |
| 性能优化 | CONTRIBUTING.md「配置管理、质量门控、性能监控」、`utils/log_decorators.py` |
| 调整 CI / 依赖 | CONTRIBUTING.md「CI/CD 流水线与门禁」、`pyproject.toml`、`.github/workflows/ci_cd.yml`；依赖流程见 CONTRIBUTING.md「新增依赖」 |
| 新增/修改回测 | CONTRIBUTING.md「DAO 模式」、`strategies/backtest/`、`services/backtest_service.py`、`ui/views/backtest_view.py`；工作流见 CONTRIBUTING.md「新增回测配置」 |

### 1.9 关键验证命令

修改代码后，按以下顺序自检（完整命令见 CONTRIBUTING.md「常用开发与测试命令」）：

```bash
ruff check .              # Lint
ruff format --check .     # 格式化
pyright                   # 类型检查 (配置见 pyrightconfig.json)
python -m pytest tests/unit/ -v -m "not slow"   # 单元测试
pre-commit run --all-files  # 全量 hook 检查
```

---

## 2. 项目概览

**AStockScreener** 是一个本地化智能 A 股量化选股桌面应用，基于 Python 3.13+。

| 维度 | 技术选型 |
|------|---------|
| **UI 框架** | Flet 0.28.3 (Flutter 驱动桌面应用) |
| **计算引擎** | Polars (策略层向量化) + Pandas (DAO 层 / 数据同步层) |
| **数据库** | PostgreSQL 16 + SQLAlchemy 2.0 (asyncpg) |
| **数据迁移** | Alembic (自动检测、幂等迁移、CI 强制验证 upgrade → downgrade → upgrade) |
| **AI 推理** | LiteLLM (多家云端供应商统一网关) / llama-cpp-python (本地 GGUF) |
| **数据源** | Tushare Pro (核心) + Akshare (补充) |
| **任务调度** | APScheduler + 自研 `TaskManager` (优先级、持久化、UI 通知) |
| **HTTP 客户端** | requests + httpx (异步) + urllib3 |
| **代码质量** | Ruff (Linter + Formatter) + Pyright (类型检查) |
| **配置验证** | Pydantic (AppConfig 模型验证 + 默认值管理) |
| **CI/CD** | GitHub Actions (Linux + Windows 双平台，含 Windows E2E、PyInstaller 打包、依赖同步 PR) |
| **依赖管理** | uv (`pyproject.toml` → `requirements*.txt`，`--universal` 跨平台锁定，pre-commit 自动同步) |

---

## 3. 关键约束与红线 🚨 (必读)

**这是不可逾越的底线，在任何代码修改中必须绝对遵守。**

### 3.1 ❌ 绝对禁止

| # | 红线 | 说明 |
|---|------|------|
| R1 | **架构越界** | `core/` 导入任何其他层模块；`data/` 导入 `services/`/`strategies/`/`ui/`；`services/` 导入 `strategies/`/`ui/`；`strategies/` 导入 `ui/` |
| R2 | **异常吞没** | 吞没 `asyncio.CancelledError` (必须 `raise` 以配合优雅停机) |
| R3 | **模糊压制** | 使用 `# type: ignore` 时不带 `[reason]` 注释 (pre-commit 强制拦截) |
| R4 | **SQL 注入** | 在 asyncpg 原生查询中使用 `%s` 占位符 (必须用 `$1, $2, ...`) |
| R5 | **僵尸引擎操作** | 在 disposed 的引擎上执行数据库操作 (DAO/维护流程必须检查引擎状态；已释放时抛出或传播 `EngineDisposedError`) |
| R6 | **过时类型注解** | 使用 `Union[X, Y]` / `Optional[X]` (必须使用 `X \| Y` / `X \| None`) |
| R7 | **测试状态污染** | 单例未隔离 (单元测试由 `tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 自动重置注册单例；需精细控制单例初始化状态时使用 `singleton_state` 上下文管理器) |
| R8 | **废弃 API** | 使用 `_write_db(is_many=True)` 进行批量写入 (会发 `DeprecationWarning`，必须用 `_save_upsert()`) |
| R9 | **敏感信息泄露** | 日志/异常消息直接打印明文 Token / API Key / 密码 / 个人信息 (必须经 `DataSanitizer` 脱敏) |
| R10 | **硬编码密钥** | 在代码或测试中硬编码 API Key / DB 密码 (必须从 `keyring` 或环境变量读取) |
| R11 | **跨循环复用同步原语** | 直接将 `asyncio.Event/Lock` 作为类属性 (必须通过 `get_loop_local()` 获取以绑定当前循环) |
| R12 | **未注册数据表** | 新增表只改 `models.py` 而不更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` |
| R13 | **未注册 DAO** | 新增 DAO 不在 `CacheManager.__init__` 中实例化、不在 `_create_engine` 中更新 `.engine` 引用 |
| R14 | **未注册策略** | 新增策略不使用 `@register_strategy("key")` 装饰器 |
| R15 | **未注册单例** | 新增单例不使用 `@register_singleton` 装饰器、不实现 `_reset_singleton` |
| R16 | **UI 阻塞主循环** | 在 Flet 事件处理器中同步执行 IO/CPU 密集任务 (必须 `await ThreadPoolManager.run_async()` 提交) |
| R17 | **保留字作字段** | 禁止使用数字开头、包含特殊字符或 SQL 保留字作为表名或列名（必须使用 ORM `name=` 属性映射，禁止拼接该列名的裸 SQL） |

### 3.2 ✅ 强制要求

- 所有异步操作的 CPU/IO 任务必须通过 `ThreadPoolManager` 提交到对应线程池 (`TaskType.IO` / `TaskType.CPU`)。
- `BaseDao` 的批量写入必须使用 `_save_upsert()`，分块大小见 `base_dao.py` 的 `_UPSERT_CHUNK_SIZE`。
- Pre-commit hooks 必须在提交前执行并保持通过。
- 涉及数据库 schema 变更必须生成 Alembic 迁移，并至少验证 `upgrade head` + `alembic check`；CI 会继续验证 `downgrade base` → `upgrade head`。
- 新增依赖必须先编辑 `pyproject.toml`，再由 pre-commit 自动重新生成 `requirements*.txt` (禁止手改)。
- 错误处理必须使用 `classify_error()` + `classify_severity()` 进行分类，并按严重度选择日志级别。
- 涉及外部 IO (Tushare / LiteLLM / DB) 的方法必须挂 `@log_async_operation(threshold_ms=PerfThreshold.XXX)` 或 `@track_performance()` 以触发慢操作告警。
- **复用优先**：实现功能前必须先搜索确认项目内是否已有可复用代码；优先采用业界稳定开源库，而非自行实现；禁止对成熟库功能做无谓封装。
- **UI 语言切换响应**：新增/修改 UI 视图或组件必须遵守 [CONTRIBUTING.md「语言切换响应 (I18n Hot Reload)」](./CONTRIBUTING.md#语言切换响应-i18n-hot-reload) 的 9 条规范（订阅机制、回调命名、纯 UI 操作、options 重建、实例属性提取、子组件级联、生命周期兜底、MetaDataManager 缓存失效、异常降级）。

---

## 4. 架构原则

### 4.1 分层架构

```text
core/             ← 架构核心层 (i18n，不依赖任何其他层)
app/              ← 引导层 (bootstrap: 启动初始化、服务编排，仅 main.py 调用)
data/             ← 数据层 (DAO、同步策略、外部数据源、领域服务、缓存管理)
  ├── cache/             缓存管理器 (CacheManager 单例，DAO 统一入口、引擎管理)
  ├── persistence/       持久化 (DAOs、ORM 模型、数据库管理/迁移/配置、质量门控、配置/状态/元数据/复盘服务)
  ├── domain_services/   领域服务 (交易日历、市场数据、离线日历快照、交易成本)
  ├── external/          外部数据源 (Tushare 客户端、新闻抓取)
  ├── sync/              数据同步策略 (历史数据、财务数据、股东数据、宏观数据)
  ├── mixins/            数据层混入 (交易日历混入、健康检查混入)
  ├── data_processor.py  数据处理与日历服务 (DataProcessor 单例)
  ├── data_dictionary.py 数据字典 (表定义与元数据配置)
  └── constants.py       数据层常量定义
services/         ← 应用服务层 (AI 服务、任务管理器、本地模型管理、新闻订阅服务)
strategies/       ← 策略层 (选股策略、AI 策略混入、Polars 向量化基类、Prompt 模板)
ui/               ← 表现层 (MVVM: Views + ViewModels + Components + Theme + i18n 桥接)
utils/            ← 工具层 (配置、安全、线程池、限流、日志、调度、代理、性能监控)
```

**依赖规则 (严格单向):**

```text
core ← data / services / strategies / utils / ui / app
data ← services / strategies / ui / app
services ← strategies / ui / app
strategies ← ui / app
utils ← 任意层可引用 (横切关注点)
app → 编排所有层，仅被 main.py 调用
```

**绝对禁止反向依赖：** `core` 导入 `data`/`services`/`ui`；`data` 导入 `ui`/`services`；`services` 导入 `ui`；`strategies` 导入 `ui`。

### 4.2 core 层隔离原则

`core/` 是架构核心层，只包含被所有层共享的基础设施 (目前仅 `i18n`)。

- **不得依赖** `data/`、`services/`、`strategies/`、`ui/`、`utils/` 中的任何模块。`utils/` 虽然标注为"任意层可引用"，但 `core/` 作为最内层不可反向导入 `utils/`，否则形成循环依赖。
- 如果某个模块被多层引用且产生循环依赖，应考虑提升到 `core/`。
- `ui/i18n.py` 是 UI 层对 `core.i18n` 的薄封装 (Flet 文本绑定)，不要直接修改 `core.i18n` 来满足 UI 需求。

### 4.3 单例模式

使用 `@register_singleton` 装饰器统一管理单例生命周期。**完整代码模板、DI 准则与 `_atexit_cleanup` 实现见 [CONTRIBUTING.md「单例模式实现模板」](./CONTRIBUTING.md#单例模式实现模板)。**

**所有单例必须:**

1. 使用 `@register_singleton` 注册
2. 实现 `_reset_singleton()` 方法 (测试隔离)
3. 实例创建必须受锁保护；优先在 `__new__` 中持锁，若存在 `get_instance()` 等统一入口，可由入口持锁以避免重复加锁死锁
4. 支持 `_initialized` 标志防止重复初始化
5. 如需进程退出清理，实现 `_atexit_cleanup()` 方法 (由 `singleton_registry` 的集中 `atexit` 处理器调用，按注册逆序执行)
6. 必须支持通过参数依赖注入 (DI) 或注入可选时钟，避免难以测试的隐式全局状态依赖

**@register_singleton 单例**: 见 `utils/singleton_registry.py` 的注册清单（当前含 CacheManager/ThreadPoolManager/TaskManager/AIService/SchedulerService/DataProcessor/MarketDataService/NewsSubscriptionService/TushareClient/LocalModelManager/StrategyManager）。

**非注册单例**: `ConfigHandler` (静态方法/类方法 + RWLockFair 保护)、`ProxyManager` (非装饰器单例)。

**非单例服务**: `BacktestService` (每次实例化创建新对象，由调用方管理生命周期)。

---

## 5. 编码规范索引

详细规范见 [CONTRIBUTING.md「实现规范手册」](./CONTRIBUTING.md#第三部分实现规范手册)，此处仅列要点。

### 5.1 Python 风格

- Python 3.13+，行宽 120，缩进 4 空格，双引号，Ruff 格式化。
- Lint 规则 `F, E, W, UP, B, SIM, BLE`，忽略 `E501, E402, SIM102, SIM105, SIM108, SIM117, BLE001`。

### 5.2 类型标注

- Pyright `basic` 模式，配置见 `pyrightconfig.json` (优先级高于 `pyproject.toml`)。
- `# type: ignore` 必须带 `[reason]` (pre-commit 强制拦截裸 `# type: ignore`)。
- **完整 Pyright 规则表见 [CONTRIBUTING.md「类型标注与 Pyright 规则」](./CONTRIBUTING.md#类型标注与-pyright-规则)。**

### 5.3 导入顺序

标准库 → 第三方库 → 本项目模块（按层级从低到高：core → utils → data → services → strategies → ui）。

### 5.4 日志规范

- `logging.getLogger(__name__)`，前缀 `[ClassName]` 或 `[ModuleName]`。
- 关机期间的连接错误必须降级为 `warning`。
- 敏感参数必须经 `DataSanitizer` 脱敏。
- **完整日志级别选择、UI 埋点、Correlation ID 规范见 [CONTRIBUTING.md「日志规范」](./CONTRIBUTING.md#日志规范)。**

### 5.5 异步编程规范

- 全项目 `asyncio` 驱动；`CancelledError` 必须传播 (R2)。
- 事件循环绑定对象使用 `get_loop_local()` (R11)。
- **完整规范（线程池分离、gather、`__init__` 限制等）见 [CONTRIBUTING.md「异步编程规范」](./CONTRIBUTING.md#异步编程规范)。**

### 5.6 数据库操作规范

- asyncpg 驱动，占位符 `$1, $2, ...` (R4)。
- 批量写入用 `_save_upsert()` (R8)，分块 IN 查询用 `chunked_in_query()`。
- **完整规范（引擎状态、维护锁、慢查询、异常分层）见 [CONTRIBUTING.md「数据库操作规范」](./CONTRIBUTING.md#数据库操作规范)。**

### 5.7 错误处理模式

- 使用 `classify_error()` + `classify_severity()` 分类，按严重度选择日志级别。
- **完整代码示例与分类上下文表见 [CONTRIBUTING.md「错误处理标准模式」](./CONTRIBUTING.md#错误处理标准模式)。**

### 5.8 语言切换响应规范 (I18n Hot Reload)

新增/修改 UI 视图或组件必须遵守 9 条规范（订阅机制、回调命名、纯 UI 操作、options 重建、实例属性提取、子组件级联、生命周期兜底、MetaDataManager 缓存失效、异常降级）。

**完整规范、判定决策树、标准 View 模板、测试要求见 [CONTRIBUTING.md「语言切换响应 (I18n Hot Reload)」](./CONTRIBUTING.md#语言切换响应-i18n-hot-reload)。**

---

## 6. 设计模式索引

详细实现模板见 [CONTRIBUTING.md「实现规范手册」](./CONTRIBUTING.md#第三部分实现规范手册)，此处仅列要点。

| 模式 | 要点 | 详细文档 |
|------|------|---------|
| **策略模式** | `@register_strategy("key")` 自动注册，`strategies/all_strategies.py` 触发导入 | [CONTRIBUTING.md「策略模式实现模板」](./CONTRIBUTING.md#策略模式实现模板) |
| **Polars 向量化基类** | `PolarsBaseStrategy` 继承自带 AI 阶段，`_filter_logic` 返回 LazyFrame | [CONTRIBUTING.md「Polars 向量化策略基类」](./CONTRIBUTING.md#polars-向量化策略基类) |
| **AI 策略混入** | `AIStrategyMixin` 提供 LLM 驱动选股，Prompt 在 `strategies/strategy_prompts.py` | [CONTRIBUTING.md「AI 策略混入」](./CONTRIBUTING.md#ai-策略混入) |
| **DAO 模式** | `BaseDao` 子类，统一 `_read_db_select` / `_save_upsert` / `chunked_in_query` | [CONTRIBUTING.md「DAO 模式」](./CONTRIBUTING.md#dao-模式) |
| **数据同步架构** | `data/sync/` 按类别组织，`TABLE_DEFINITIONS` 驱动 | [CONTRIBUTING.md「数据同步架构」](./CONTRIBUTING.md#数据同步架构) |
| **TaskManager** | `QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED/INTERRUPTED` | [CONTRIBUTING.md「TaskManager 任务生命周期」](./CONTRIBUTING.md#taskmanager-任务生命周期) |
| **配置/质量/性能** | `ConfigHandler` + `@require_quality` + `@log_async_operation` | [CONTRIBUTING.md「配置管理、质量门控、性能监控」](./CONTRIBUTING.md#配置管理质量门控性能监控) |
| **MVVM 表现层** | View (控件树) + ViewModel (业务状态) + Component (可复用) | [CONTRIBUTING.md「MVVM 表现层」](./CONTRIBUTING.md#mvvm-表现层) |

---

## 7. 测试规范索引

详细规范见 [CONTRIBUTING.md「测试规范」](./CONTRIBUTING.md#测试规范)。

- 测试分三层：`unit/`（纯逻辑隔离）、`integration/`（依赖 PostgreSQL）、`e2e/`（端到端）。
- 单元测试由 `_reset_all_singletons` autouse fixture 自动重置单例 (R7)。
- `keyring` 和 `litellm` 在 `tests/conftest.py` 全局 mock (session 别，`pytest_configure` 早期拦截)。
- Windows 使用 `WindowsSelectorEventLoopPolicy`，loop scope 为 `session` 级（已知技术债 P1-2，详见 CONTRIBUTING.md）。
- 覆盖率：整体 ≥ 85%，单文件 ≥ 80%（阈值源：`pyproject.toml`）。

---

## 8. CI/CD 门禁索引

详细流水线见 [CONTRIBUTING.md「CI/CD 流水线与门禁」](./CONTRIBUTING.md#cicd-流水线与门禁)。

PR/主干质量门禁：Ruff → Pre-commit → Security Audit → Pyright → Alembic 迁移 → Unit/Integration Tests → Windows E2E → 覆盖率门禁 → requirements 漂移处理。

发布流程：打 `v*.*.*` tag → `build-windows` job → PyInstaller 打包 CPU/CUDA → smoke test → Inno Setup 安装包 → GitHub Release。

---

## 9. 核心目录结构

分层架构及各层职责见 §4.1。以下补充 §4.1 未覆盖的目录：

```text
tests/            ← 测试目录 (unit/ 单元测试, integration/ 集成测试, e2e/ 端到端测试)
scripts/          ← 工具脚本 (覆盖率检查、安全审计、依赖同步等)
locales/          ← 国际化资源文件
man/              ← 架构专题文档 (数据库账号分离、表分区策略)
```
