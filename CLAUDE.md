# CLAUDE.md — AStockScreener (QTrading) 项目上下文

> 本文件为 LLM 对话上下文文件，每次在 Trae/Cursor 等 IDE 中与 AI 对话时自动加载。
> 请严格遵循以下交互准则、架构原则、设计规则和编码规范。
>
> **阅读顺序建议**：§1 (AI 助手交互准则) → §3 (红线，先读后写) → §9 (目录结构，定位代码) → 其他章节按需查阅。开发工作流请参阅 [CONTRIBUTING.md](./CONTRIBUTING.md)。

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
- **不做多余功能**：仅实现明确要求的特性，绝不添加“未来可能有用”的代码。
- **拒绝过度设计**：不要为"未来可能的需求"引入抽象层；当前能跑通的最简方案优先。
- **拒绝过度抽象**：绝不为单次使用的代码做抽象封装或提供虚假的“灵活性”、“可配置性”。
- **精简行数**：如果 200 行代码可以通过重构缩减到 50 行，必须重写。时刻反思：“这是否显得过于复杂？”。
- **合理异常处理**：仅对真实发生的边界情况和合理异常进行捕获，不对绝对不可能发生的场景编写冗余的防御代码。

### 1.4 微创修改 (Surgical Changes)

- **仅修改必须触及的代码，只清理自己的逻辑，绝不随意改变周边代码。**
- **禁止过度修饰**：不要顺手“优化”周边的格式、命名、注释或无关逻辑。
- **不做无益重构**：绝不重构没坏的代码。
- **严格融入风格**：必须与现有代码的编码风格（哪怕是你认为不够优雅的风格）保持绝对一致。
- **残留代码处理**：若发现无关的死代码（Dead Code），在回复中指出，绝不顺手删除。

### 1.5 目标驱动与验证 (Goal-Driven Execution)

- **明确定义成功标准，持续迭代直到验证通过。**
- **测试驱动思维**：将每个开发任务转换为可验证的目标：
  - “添加输入校验” → “编写针对无效输入的测试并使其通过”。
  - “修复 Bug” → “先编写能稳定复现该 Bug 的测试，再修复代码使测试通过”。
  - “重构逻辑” → “确保重构前后的测试均完全通过”。
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

### 1.8 任务类型 → 必读文件 (决策树)

| 任务类型 | 必读章节 / 文件 |
|---------|----------------|
| 新增/修改策略 | §6.1、§6.2、§6.3、`strategies/base_strategy.py`；工作流见 CONTRIBUTING.md「新增一个策略」 |
| 新增/修改 DAO 或数据表 | §6.4、`data/persistence/daos/base_dao.py`、`data/data_dictionary.py`；工作流见 CONTRIBUTING.md「新增一张数据表」/「新增一个 DAO」 |
| 新增/修改数据同步 | §6.5、`data/sync/base.py` |
| 新增/修改 UI 视图 | §6.8、`ui/app_layout.py`、对应 ViewModel；工作流见 CONTRIBUTING.md「新增一个 UI 视图」 |
| 修改异常处理 | §5.7、§3 红线、`utils/error_classifier.py` |
| 修改单例 / 资源生命周期 | §4.3、`utils/singleton_registry.py`、`utils/shutdown.py` |
| 性能优化 | §6.7 性能红线、`utils/log_decorators.py` |
| 调整 CI / 依赖 | §8、`pyproject.toml`、`.github/workflows/ci_cd.yml`；依赖流程见 CONTRIBUTING.md「新增依赖」 |
| 新增/修改回测 | §6.4、`strategies/backtest/`、`services/backtest_service.py`、`ui/views/backtest_view.py`；工作流见 CONTRIBUTING.md「新增回测配置」 |

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
| R7 | **测试状态污染** | 单例未隔离 (单元测试由 `tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 自动重置注册单例；需精细控制单例初始化状态时使用 `reset_singleton` 上下文管理器) |
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
- `BaseDao` 的批量写入必须使用 `_save_upsert()`，分块由基类自动处理 (`_UPSERT_CHUNK_SIZE=500`)。
- Pre-commit hooks 必须在提交前执行并保持通过。
- 涉及数据库 schema 变更必须生成 Alembic 迁移，并至少验证 `upgrade head` + `alembic check`；CI 会继续验证 `downgrade base` → `upgrade head`。
- 新增依赖必须先编辑 `pyproject.toml`，再由 pre-commit 自动重新生成 `requirements*.txt` (禁止手改)。
- 错误处理必须使用 `classify_error()` + `classify_severity()` 进行分类，并按严重度选择日志级别。
- 涉及外部 IO (Tushare / LiteLLM / DB) 的方法必须挂 `@log_async_operation(threshold_ms=PerfThreshold.XXX)` 或 `@track_performance()` 以触发慢操作告警。
- **复用优先**：实现功能前必须先搜索确认项目内是否已有可复用代码；优先采用业界稳定开源库，而非自行实现；禁止对成熟库功能做无谓封装。

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

使用 `@register_singleton` 装饰器统一管理单例生命周期：

```python
import threading
from utils.singleton_registry import register_singleton

@register_singleton
class MyService:
    _instance = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        # ... 初始化逻辑 ...
        self._initialized = True

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    @classmethod
    def _atexit_cleanup(cls):
        """Optional: invoked by singleton_registry's centralized atexit handler."""
        if cls._instance is not None:
            # 释放外部资源 (线程池、连接、文件句柄等)
            ...
```

**所有单例必须:**

1. 使用 `@register_singleton` 注册
2. 实现 `_reset_singleton()` 方法 (测试隔离)
3. 实例创建必须受锁保护；优先在 `__new__` 中持锁，若存在 `get_instance()` 等统一入口，可由入口持锁以避免重复加锁死锁
4. 支持 `_initialized` 标志防止重复初始化
5. 如需进程退出清理，实现 `_atexit_cleanup()` 方法 (由 `singleton_registry` 的集中 `atexit` 处理器调用，按注册逆序执行)

**@register_singleton 单例**: `CacheManager`、`ThreadPoolManager`、`TaskManager`、`AIService`、`SchedulerService`、`DataProcessor`、`MarketDataService`、`NewsSubscriptionService`、`TushareClient`、`LocalModelManager`、`StrategyManager`。

**非注册单例**: `ConfigHandler` (静态方法/类方法 + RWLockFair 保护)、`ProxyManager` (非装饰器单例)。

**非单例服务**: `BacktestService` (每次实例化创建新对象，由调用方管理生命周期)。

---

## 5. 编码规范

### 5.1 Python 风格

- **Python 版本**: 3.13+ (强制使用新语法：`type[T]` 泛型、`X | Y` 联合类型、`type Alias = ...`、PEP 695 泛型类等；禁用 `typing.Union` 和 `typing.Optional`)
- **行宽**: 120 字符
- **缩进**: 4 空格
- **引号**: 双引号 `"`
- **格式化工具**: Ruff (`ruff format`)
- **Lint 规则**: `F, E, W, UP, B, SIM, BLE` (见 `pyproject.toml [tool.ruff.lint]`)
- **忽略规则**: `E501, E402, SIM102, SIM105, SIM108, SIM117, BLE001`

### 5.2 类型标注

- **类型检查器**: Pyright (`basic` 模式，版本见 `.github/workflows/ci_cd.yml`；完整配置见 `pyrightconfig.json`，优先级高于 `pyproject.toml`)
- **关键 Pyright 规则**:

| 规则 | 级别 | 说明 |
|------|------|------|
| `reportCallIssue` | `error` | 函数调用类型不匹配必须修复 |
| `reportOptionalOperand` | `error` | Optional 值参与运算必须先判空 |
| `reportOptionalIterable` | `error` | Optional 值用于迭代必须先判空 |
| `reportGeneralTypeIssues` | `error` | 通用类型问题必须修复 |
| `reportMissingImports` | `error` | 缺失导入必须修复 |
| `reportOptionalMemberAccess` | `warning` | Optional 成员访问应判空 |
| `reportArgumentType` | `warning` | 参数类型不匹配应修复 |
| `reportAttributeAccessIssue` | `warning` | 属性访问问题应修复 |
| `reportOptionalSubscript` | `warning` | Optional 值下标访问应判空 |

- **`type: ignore` 必须带理由** (pre-commit 强制拦截裸 `# type: ignore`):

  ```python
  # ✅ 正确
  task._coroutine_gen = None  # type: ignore[assignment]

  # ❌ 错误 (pre-commit 会拒绝)
  task._coroutine_gen = None  # type: ignore
  ```

### 5.3 导入顺序

```python
# 1. 标准库
import asyncio
import logging

# 2. 第三方库
import pandas as pd
import polars as pl

# 3. 本项目模块 (按层级从低到高：core → utils → data → services → strategies → ui)
from core.i18n import I18n
from utils.thread_pool import TaskType, ThreadPoolManager
from data.cache.cache_manager import CacheManager
```

### 5.4 日志规范

- 使用 `logging.getLogger(__name__)` 获取模块级 logger。
- 日志前缀格式: `[ClassName]` 或 `[ModuleName]`，便于过滤。
- **日志级别选择**:
  - `DEBUG` — 性能采样、细粒度执行轨迹 (生产默认不输出)
  - `INFO` — 关键状态变迁 (服务启动、连接建立、任务完成)
  - `WARNING` — 慢查询、慢写入、降级路径、关机期间的连接错误、可恢复异常
  - `ERROR` — 操作失败但不影响进程
  - `CRITICAL` — 系统级失败 (`MemoryError`、磁盘满)、数据完整性问题
- **关机期间** 的连接错误 (`no active connection` / `database is closed` / `ConnectionDoesNotExistError`) 必须降级为 `warning`，避免污染日志。
- **UI 交互埋点** 使用专用 `UILogger.log_action()` 类方法或 `@log_ui_action` 装饰器，自动写入 `ui.action` logger 通道。
- **敏感参数** 必须经 `DataSanitizer.sanitize_args()` 或 `DataSanitizer.sanitize_error()` 脱敏后再记录。
- **Correlation ID** 涉及跨模块的请求链路追踪，使用 `utils/correlation.py` 提供的工具串联日志。

### 5.5 异步编程规范

- **asyncio 模式**: 全项目使用 `asyncio` 驱动异步。
- **线程安全**: UI 回调可能来自线程池，使用 `loop.call_soon_threadsafe()` 转移到事件循环。
- **线程池分离**: IO 密集型使用 `TaskType.IO`，CPU 密集型 (NumPy/Pandas 等 GIL 释放型) 使用 `TaskType.CPU`；纯 Python CPU 密集任务应使用 `ProcessPoolExecutor` (项目暂无)。
- **CancelledError 必须传播**: 永远 `raise` 不吞没，否则破坏优雅停机。
- **事件循环绑定对象**: 使用 `utils.loop_local` 的 `get_loop_local()` / `del_loop_local()` / `clear_all_loop_locals()` 管理 `asyncio.Event`、`asyncio.Lock` 等绑定到特定事件循环的对象，避免跨循环死锁。
- **`asyncio.gather`** 涉及失败可恢复场景使用 `return_exceptions=True`，并在调用方逐个分类异常。
- **不要在 `__init__`** 中调用 `asyncio.create_task()`，会绑定到错误的事件循环；改为提供 `async def initialize()` 方法。

### 5.6 数据库操作规范

- **异步引擎**: 使用 `asyncpg` 驱动 (通过 SQLAlchemy asyncio)。
- **参数占位符**: 使用 `$1, $2, ...` (asyncpg 原生占位符，非 `%s`)。
- **批量写入**: 使用 `_save_upsert()` (基于 `ON CONFLICT DO UPDATE`，内置分块 `_UPSERT_CHUNK_SIZE=500`)。
- **分块 IN 查询**: 使用 `chunked_in_query()` 避免 PostgreSQL 参数上限 (`_IN_CHUNK_SIZE=500`)。
- **引擎状态检查**: DAO 操作前必须确认引擎仍可用；关机/释放后继续访问时应抛出或传播 `EngineDisposedError`，调用方按关机降级处理。
- **维护锁**: DAO 操作前 `await self._get_maintenance_event().wait()` 等待维护完成 (基类已自动处理)。
- **慢查询阈值**: 读 500ms / 写 2000ms / UPSERT 2000ms (基类自动告警，无需手动埋点)。
- **DB 异常应在 DAO 层处理**: 业务层只接收 `EngineDisposedError` 和业务异常，不应直接捕获 `asyncpg.*Error`。

### 5.7 错误处理模式

```python
# ✅ 标准异常处理模式
try:
    result = await some_operation()
except asyncio.CancelledError:
    logger.warning("[Module] Cancelled during shutdown.")
    raise  # R2: 必须传播
except EngineDisposedError:
    logger.warning("[Module] Engine disposed, skipping operation.")
    return  # 优雅降级
except Exception as e:
    error_info = classify_error(e, context="general")     # 返回 dict: code / message_key [/ format_args / should_retry]
    severity = classify_severity(e, context="general")    # 返回: system / recoverable / operational
    if severity == "system":
        logger.critical(f"[Module] SYSTEM-LEVEL failure: {e}", exc_info=True)
        raise  # 系统级错误必须上抛
    elif severity == "recoverable":
        logger.warning(f"[Module] Recoverable error ({error_info['code']}): {e}")
    else:
        logger.error(f"[Module] Operational error: {e}", exc_info=True)
```

**错误分类上下文** (`classify_error` 的 `context` 参数):

- `"token"` — Tushare Token 验证错误
- `"llm"` — LLM API 调用错误 (区分永久错误 / 瞬态可重试错误，返回 `should_retry` 字段)
- `"db"` — 数据库连接 / 认证错误
- `"chart"` — 图表渲染错误
- `"general"` — 通用错误 (默认)

**UI 层错误展示** 使用 `get_error_message(error_info)` 把 `message_key` 翻译为本地化文案。

---

## 6. 设计模式

### 6.1 策略模式 (自动注册)

```python
from strategies.base_strategy import BaseStrategy, register_strategy
from strategies.utils import StrategyContext

@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):
    required_context_keys = ["screening_data"]
    required_tables = ["daily_quotes"]
    required_history_days = 60

    def __init__(self):
        super().__init__(name_key="strategy_my", desc_key="strategy_my_desc")

    async def filter(self, context: StrategyContext):
        # 策略逻辑：返回过滤后的 DataFrame
        ...
```

- **策略入口**: `strategies/all_strategies.py` 通过导入所有策略模块触发 `@register_strategy`，由 `_STRATEGY_REGISTRY` 字典统一暴露。
- **策略依赖检查**: 每个策略声明 `required_context_keys` 和 `required_tables`，运行前通过 `check_dependencies()` 验证数据就绪状态，返回 `ready` / `degraded` / `unready`。`CONTEXT_KEY_TABLE_MAP` (`BaseStrategy` 类属性) 定义了 context key 到表名的映射。
- **动态参数**: 重写 `get_parameters()` 暴露可调参数 (`slider` / `number` / `dropdown` 三种 UI 控件)。
- **动态描述**: 重写 `get_dynamic_description(current_params)` 让描述随参数变化。
- **依赖声明**: 声明 `required_context_keys` / `required_tables` / `required_history_days`。可选声明 `required_apis: list[str] = []`（所需外部 API 端点列表，用于依赖检查）。

### 6.2 Polars 向量化策略基类

继承 `PolarsBaseStrategy` 使用 Polars LazyFrame 进行高性能向量化计算。
`PolarsBaseStrategy` 同时继承了 `AIStrategyMixin`，Polars 过滤后自动进入 AI 分析阶段（可通过 `enable_ai_analysis = False` 关闭）：

```python
from strategies.polars_base import PolarsBaseStrategy
from data.persistence.quality_gate import QualityTier

class MyPolarsStrategy(PolarsBaseStrategy):
    # 注：如需覆盖默认质量等级，应在类属性中定义 required_quality_tier = QualityTier.GOLD，而非在方法上加装饰器。
    required_quality_tier = QualityTier.SILVER

    def _filter_logic(self, lf: pl.LazyFrame, context: StrategyContext) -> pl.LazyFrame:
        return lf.filter(pl.col("pct_chg") > 5.0)
```

> 注：上述类属性模式适用于 `PolarsBaseStrategy` 子类。非 `PolarsBaseStrategy` 子类（如 `OversoldStrategy` 直接继承 `BaseStrategy`）可使用 `@require_quality` 装饰器。

### 6.3 AI 策略混入 (AIStrategyMixin)

`strategies/ai_mixin.py` 的 `AIStrategyMixin` 类提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：

- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量、Prompt 安全防护 (`utils/prompt_guard.py`)
- Prompt 模板集中在 `strategies/strategy_prompts.py`，响应校验在 `strategies/prompt_validator.py`

### 6.4 DAO 模式

所有数据访问通过 `BaseDao` 子类，统一提供：

- `_read_db()` — 原生 SQL 读取，返回 DataFrame
- `_read_db_select()` — SQLAlchemy Core 查询 (**推荐**，防注入)
- `_write_db()` — 单条写入 (⚠️ `is_many=True` 已废弃，使用会触发 `DeprecationWarning`)
- `_save_upsert()` — 批量 UPSERT (**推荐**，基于 `pg_insert` + `ON CONFLICT`)
- `chunked_in_query()` — 分块 IN 查询 (避免参数上限)

**DAO 继承体系**: `BaseDao` → `StockDao` / `QuoteDao` / `FinancialDao` / `MarketDao` / `ScreenerDao` / `SyncDao` / `MacroDao` / `HolderDao` / `BacktestDAO`

### 6.5 数据同步架构

`data/sync/` 下按数据类别组织同步策略：

- `base.py` — 同步基础定义 (`SyncContext` 依赖注入容器、`SyncResult` 结果数据类、`ISyncStrategy` 策略接口，含取消支持)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表结构、同步配置、质量监控配置。

### 6.6 TaskManager 任务生命周期

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
```

- 任务通过 `submit_task()` 提交，传入 `coroutine_factory` (无参可调用对象，返回 coroutine)
- 使用 `update_progress(progress)` 报告进度 (0.0-1.0)，内置节流避免 UI 风暴
- 工作协程内部使用 `is_cancelled()` 检测取消信号 (用户主动取消 / 应用退出)
- 任务持久化到本地，重启后 `RUNNING` 状态会被回填为 `INTERRUPTED`

### 6.7 配置管理、质量门控、性能监控

- **配置管理**: `ConfigHandler` 使用读写锁 (`rwlock.RWLockFair`) 保护并发访问。敏感信息优先使用 `keyring`，降级到 AES-GCM 加密文件 (`utils/security_utils.py`)。
- **数据质量门控**: 使用 `@require_quality(QualityTier.SILVER)` 确保只有数据质量达标才执行逻辑。质量分层: `CRITICAL(0)` → `BRONZE(1)` → `SILVER(2)` → `GOLD(3)`。`STRICT_QUALITY_GATE` 环境变量控制严格模式（默认开启，设为 `false` 关闭）。
- **性能监控装饰器** (`utils/log_decorators.py`):
  - `@log_async_operation(operation_name="fetch_data", threshold_ms=500)` — 异步操作日志 + 性能监控 + 自动脱敏
  - `@track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` — 纯性能追踪 (轻量)
  - `@log_ui_action(component_name="Settings", action_type="Click")` — UI 交互埋点
  - `AsyncOperationLogger` — 复杂流程分段日志上下文管理器
  - **取舍**: 同一函数只挂一个性能装饰器，优先选 `@log_async_operation` (功能更完整)。

**标准性能红线 (`PerfThreshold`)**:

| 常量 | 阈值 | 场景 |
|------|------|------|
| `MEMORY_COMPUTE` | 50ms | 内存与本地纯计算 |
| `DB_SINGLE_QUERY` | 200ms | 数据库单行/少数读写 |
| `EXTERNAL_NETWORK` | 2000ms | 外部公网接口调用 (如 Tushare) |
| `DB_BULK_IO` | 5000ms | 数据库大批量聚合插入 |
| `AI_INFERENCE` | 15000ms | 本地大模型推理计算 |
| `GLOBAL_INIT` | 15000ms | 全局初始化大动作 |

### 6.8 MVVM 表现层

- **View** (`ui/views/`): 仅负责构建 Flet 控件树和绑定事件，不持有业务状态。事件回调将 (用户意图, 参数) 转发给 ViewModel。
- **ViewModel** (`ui/viewmodels/`): 持有业务状态 (DataFrame、筛选结果、加载标记)，调用 services/strategies/data 层，通过回调通知 View 刷新。
- **Component** (`ui/components/`): 可复用控件 (图表、对话框、虚拟表格、Toast)，不耦合具体业务。
- **Theme** (`ui/theme.py`): 亮/暗主题切换，颜色/字体 token 集中管理。
- **i18n** (`ui/i18n.py`): 对 `core.i18n` 的 UI 层薄封装，提供 Flet 文本绑定。

---

## 7. 测试规范

### 7.1 测试架构

分为 `unit/` (单元测试, 纯逻辑隔离), `integration/` (集成测试, 依赖 PostgreSQL), `e2e/` (端到端测试)。

测试标记 (定义在 `pyproject.toml [tool.pytest.ini_options]`):

- `@pytest.mark.unit` — 单元测试
- `@pytest.mark.integration` — 集成测试
- `@pytest.mark.database` — 需要数据库连接
- `@pytest.mark.ai` — 涉及 AI 服务或模型调用
- `@pytest.mark.e2e` — 端到端测试
- `@pytest.mark.slow` — 慢速测试 (真实 sleep、大量 IO)
- `@pytest.mark.network` — 需要真实网络访问
- `@pytest.mark.no_auto_mock` — 跳过 `mock_external_services` autouse fixture (用于测试外部服务自身)

### 7.2 测试编写规则

- **单例隔离**: 单元测试（`tests/unit/`）由 `tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 自动重置所有注册单例。集成测试和 e2e 测试不自动重置单例，需手动管理。需精细控制单例初始化状态时（如测试 `__init__` 重复初始化防护），可使用 `reset_singleton` 上下文管理器：

  ```python
  from tests.conftest import reset_singleton

  with reset_singleton(TaskManager, extra_attrs=["_initialized"]):
      mgr = TaskManager()
      # 测试逻辑...
  # 自动恢复原始单例状态
  ```

- **外部服务 Mock**: 单元测试由 `mock_external_services` autouse fixture 自动 mock 外部网络调用和配置延迟 (`NewsFetcher`/`ReviewManager`/`ConfigHandler.get_sync_request_delay`)。测试自身模块需跳过 mock 时，在文件顶部声明 `pytestmark = pytest.mark.no_auto_mock`。

- **Mock 规范**: `keyring` 和 `litellm` 在 `tests/conftest.py` 中全局 mock (session 别，`pytest_configure` 早期拦截)，每个测试后清理状态。
- **异步测试**: 使用 `pytest-asyncio`，`asyncio_mode = "auto"` 自动处理 (`async def test_xxx()` 即可)。
- **事件循环策略**: Windows 使用 `WindowsSelectorEventLoopPolicy`，loop scope 为 `session` 级。
- **配置隔离**: 测试使用临时配置文件 (`tempfile.mkdtemp`)，通过 `pytest_configure` 在 import 之前重写 `utils.config_handler.CONFIG_FILE`。
- **DB 隔离**: 集成测试连接 `test_astock` 数据库 (CI 通过 service container 启动 PostgreSQL 16)，通过 `TEST_DB_*` 环境变量配置。

### 7.3 覆盖率要求

> [!NOTE]
> 覆盖率阈值的单一事实源位于 `pyproject.toml`。
> - **整体覆盖率**：具体数值见 `pyproject.toml` 中的 `fail_under`（目前为 ≥ 85%）
> - **单文件覆盖率**：具体数值见 `pyproject.toml` 中的 `per_file_minimum`（目前为 ≥ 80%，由 `scripts/check_per_file_coverage.py` 强制检查）
> - **覆盖率源**：`core`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）
> - **覆盖率排除行**：`pragma: no cover`、`if __name__ == "__main__"`、`if TYPE_CHECKING:`、`raise NotImplementedError`、`...`

---

## 8. CI/CD 流水线与门禁

GitHub Actions 双平台验证 (`.github/workflows/ci_cd.yml`)，PR/主干质量门禁包括：

1. **Fast Ruff Check & Format** (Python 3.13 + 3.14 experimental)
2. **Pre-commit Hooks** (Ruff、格式化、裸 `type: ignore`、requirements 同步)
3. **Security Audit** (`scripts/run_pip_audit.py`，扫描 `requirements.txt`、`requirements-optional.txt`、`requirements-dev.txt`，使用 `.security/audit-allowlist.yml`)
4. **Pyright Type Check** (`pyright==1.1.410`，`continue-on-error: false`)
5. **Alembic Migration** (`upgrade head` → `alembic check` → `downgrade base` → `upgrade head`)
6. **Unit & Integration Tests** (Linux/Windows unit，Linux integration)
7. **Windows E2E Tests** (`tests/e2e/`，Chromium + PostgreSQL)
8. **Per-File (≥ 80%) & Overall Coverage (≥ 85%)**
9. **requirements*.txt 漂移处理** (`requirements-drift` job 检测到 main 分支漂移时，由 `update-requirements` job 创建同步 PR)

发布流程: 打 `v*.*.*` tag → 触发 `build-windows` job → PyInstaller 打包 CPU/CUDA 两个变体 → smoke test → Inno Setup 制作安装包 → GitHub Release 发布。

**其他 workflow**: CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、自动化 Release PR (`release-please.yml`)、依赖更新机器人 (`renovate.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)。

### 8.1 Pre-commit Hooks

本项目使用 7 个 pre-commit hook (定义在 `.pre-commit-config.yaml`)，提交前必须全部通过：

| Hook | 功能 |
|------|------|
| `ruff-check` | Ruff Lint 检查 (自动修复 `--fix`) |
| `ruff-format` | Ruff 代码格式化 |
| `type-ignore-reason` | 拒绝裸 `# type: ignore` (必须带 `[reason]`) |
| `pip-compile-core` | 同步 `pyproject.toml` → `requirements.txt` (使用 `uv pip compile --universal`) |
| `pip-compile-dev` | 同步 `pyproject.toml[dev]` → `requirements-dev.txt` (使用 `uv pip compile --universal`) |
| `pip-compile-optional` | 同步 `pyproject.toml[optional]` → `requirements-optional.txt` (使用 `uv pip compile --universal`) |
| `verify-versions` | 校验 pyproject.toml / installer.iss / .release-please-manifest.json 版本一致性 (自动 `--fix`) |

---

## 9. 核心目录结构

分层架构及各层职责见 §4.1。以下补充 §4.1 未覆盖的目录：

```text
tests/            ← 测试目录 (unit/ 单元测试, integration/ 集成测试, e2e/ 端到端测试)
scripts/          ← 工具脚本 (覆盖率检查、安全审计、依赖同步等)
locales/          ← 国际化资源文件
man/              ← 架构专题文档 (数据库账号分离、表分区策略)
```