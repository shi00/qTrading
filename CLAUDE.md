# CLAUDE.md — AStockScreener (QTrading) 项目上下文

> 本文件为 LLM 对话上下文文件，每次在 Trae/Cursor 等 IDE 中与 AI 对话时自动加载。
> 请严格遵循以下交互准则、架构原则、设计规则和编码规范。

---

## 1. AI 助手交互准则 (核心指令)

作为项目的高级工程师和架构师，请在所有回复中遵循以下原则：

- **回复风格**：
  - 始终使用 **简体中文** 进行回复。
  - **极简原则**：不要道歉，不要过度解释显而易见的事情，不要使用"当然"、"我理解"、"好的"等客套话。直接给出答案或代码。
  - **精准作答**：如果问题不明确，优先提问澄清，而不是自行猜测。
- **编码与交付**：
  - **拒绝占位符**：提供完整、可运行的代码，不要使用 `// ... 现有代码 ...` 或 `# TODO` 省略逻辑（除非明确要求）。
  - **一步步思考**：在进行复杂的架构修改或重构前，先输出简短的思考过程和执行计划，经确认后再编写代码。
  - **自我检查**：在输出代码前，主动思考是否违反了后文的"架构原则"与"关键约束"。
- **调试与问题排查**：
  - 不要盲目修改代码尝试修复。先收集日志、分析错误栈、找到根本原因。
  - 在给出修复方案时，简要说明"为什么会报错"以及"为什么这个方案能修复"。

---

## 2. 项目概览

**AStockScreener** 是一个本地化智能 A 股量化选股平台，基于 Python 3.13+。

| 维度 | 技术选型 |
|------|---------|
| **UI 框架** | Flet 0.28 (Flutter 驱动桌面应用) |
| **计算引擎** | Polars (策略层向量化) + Pandas (DAO 层 / 数据同步层) |
| **数据库** | PostgreSQL 16 + SQLAlchemy 2.0 (asyncpg) |
| **数据迁移** | Alembic (自动检测、幂等迁移) |
| **AI 推理** | LiteLLM (11 家云端供应商) / llama-cpp-python (本地 GGUF) |
| **数据源** | Tushare Pro (核心) + Akshare (补充) + 财联社新闻 |
| **任务调度** | APScheduler |
| **HTTP 客户端** | requests + httpx (异步) |
| **代码质量** | Ruff (Linter + Formatter) + Pyright (类型检查) |
| **CI/CD** | GitHub Actions (Linux + Windows 双平台) |

---

## 3. 架构原则

### 3.1 分层架构

```text
core/           ← 架构核心层 (i18n，不依赖任何其他层)
app/            ← 引导层 (bootstrap: 启动初始化、服务编排，仅 main.py 调用)
data/           ← 数据层 (DAO、同步策略、外部数据源、领域服务、缓存管理)
  ├── cache/           缓存管理器 (CacheManager 单例，DAO 统一入口)
  ├── persistence/     持久化 (DAOs、ORM 模型、数据库迁移、数据质量门控)
  ├── domain_services/ 领域服务 (交易日历、市场数据服务)
  ├── external/        外部数据源 (Tushare 客户端、新闻抓取与订阅)
  ├── sync/            数据同步策略 (历史数据、财务数据、股东数据、宏观数据)
  └── mixins/          数据层混入 (可复用的数据访问逻辑)
services/       ← 应用服务层 (AI 服务、任务管理器、本地模型管理)
strategies/     ← 策略层 (选股策略、AI 策略混入、Polars 向量化基类)
ui/             ← 表现层 (MVVM: Views + ViewModels + Components + Theme)
utils/          ← 工具层 (配置、安全、线程池、限流、日志、调度、代理)
```

**依赖规则 (严格单向):**
```text
core ← data / services / strategies / utils / ui / app
data ← services / strategies / ui / app
services ← ui / app
strategies ← services / ui
utils ← 任意层可引用 (横切关注点)
app → 编排所有层，仅被 main.py 调用
```

**绝对禁止:** 反向依赖 (如 `core` 导入 `data`、`data` 导入 `ui`、`services` 导入 `ui`)。

### 3.2 core 层隔离原则

`core/` 是架构核心层，只包含被所有层共享的基础设施 (如 `i18n`)。
- **不得依赖** `data/`、`services/`、`strategies/`、`ui/`、`utils/` 中的任何模块
- 如果某个模块被多层引用且产生循环依赖，应考虑提升到 `core/`

### 3.3 单例模式

使用 `@register_singleton` 装饰器统一管理单例生命周期：

```python
from utils.singleton_registry import register_singleton
import threading

@register_singleton
class MyService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
```

**所有单例必须:**
1. 使用 `@register_singleton` 注册
2. 实现 `_reset_singleton()` 方法
3. 使用线程锁保护 `__new__`
4. 支持 `_initialized` 标志防止重复初始化
5. 如需进程退出清理，实现 `_atexit_cleanup()` 方法 (由 `singleton_registry` 的集中 `atexit` 处理器调用，按注册逆序执行)

---

## 4. 编码规范

### 4.1 Python 风格

- **Python 版本**: 3.13+ (强制使用新语法特性如 `type[T]` 泛型、`X | Y` 联合类型，弃用 `typing.Union` 和 `typing.Optional`)
- **行宽**: 120 字符
- **缩进**: 4 空格
- **引号**: 双引号 `"`
- **格式化工具**: Ruff (`ruff format`)
- **Lint 规则**: `F, E, W, UP, B, SIM, BLE` (见 `pyproject.toml`)
- **忽略规则**: `E501, E402, SIM102, SIM105, SIM108, SIM117, BLE001`

### 4.2 类型标注

- **类型检查器**: Pyright (`basic` 模式)
- **关键 Pyright 规则** (完整配置见 `pyrightconfig.json`):

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

- **`type: ignore` 必须带理由**:
  ```python
  # ✅ 正确
  task._coroutine_gen = None  # type: ignore[assignment]

  # ❌ 错误 (pre-commit 会拒绝)
  task._coroutine_gen = None  # type: ignore
  ```

### 4.3 导入顺序

```python
# 1. 标准库
import asyncio
import logging

# 2. 第三方库
import pandas as pd
import polars as pl

# 3. 本项目模块 (按层级从低到高)
from core.i18n import I18n
from data.cache.cache_manager import CacheManager
from utils.thread_pool import ThreadPoolManager
```

### 4.4 日志规范

- 使用 `logging.getLogger(__name__)` 获取模块级 logger
- 日志前缀格式: `[ClassName]` 或 `[ModuleName]`
- 性能敏感操作使用 `logger.debug()`
- 慢查询/慢写入使用 `logger.warning()`
- 数据安全问题使用 `logger.critical()`
- 关机期间的连接错误使用 `logger.warning()` 而非 `error`
- UI 交互埋点使用专用 `UILogger.log_action()` 或 `@log_ui_action` 装饰器

### 4.5 异步编程规范

- **asyncio 模式**: 全项目使用 `asyncio` 驱动异步
- **线程安全**: UI 回调可能来自线程池，使用 `loop.call_soon_threadsafe()` 转移到事件循环
- **线程池分离**: IO 密集型使用 `TaskType.IO`，CPU 密集型使用 `TaskType.CPU`
- **CancelledError 必须传播**: 永远 `raise` 不吞没
- **事件循环绑定对象**: 使用 `utils.loop_local` 的 `get_loop_local()` / `del_loop_local()` 管理 `asyncio.Event`、`asyncio.Lock` 等绑定到特定事件循环的对象，避免跨循环死锁

### 4.6 数据库操作规范

- **异步引擎**: 使用 `asyncpg` 驱动 (通过 SQLAlchemy asyncio)
- **参数占位符**: 使用 `$1, $2, ...` (asyncpg 原生占位符，非 `%s`)
- **批量写入**: 使用 `_save_upsert()` (基于 `ON CONFLICT DO UPDATE`，内置分块 `_UPSERT_CHUNK_SIZE=500`)
- **分块 IN 查询**: 使用 `chunked_in_query()` 避免 PostgreSQL 参数上限 (`_IN_CHUNK_SIZE=500`)
- **引擎状态检查**: 操作前检查 `CacheManager._disposed` 标志，若已释放抛出 `EngineDisposedError`
- **连接错误处理**: 关机期间的 `no active connection` / `database is closed` / `ConnectionDoesNotExistError` 错误降级为 warning
- **维护锁**: DAO 操作前 `await self._get_maintenance_event().wait()` 等待维护完成

### 4.7 错误处理模式

```python
# ✅ 标准异常处理模式
try:
    result = await some_operation()
except asyncio.CancelledError:
    logger.warning("[Module] Cancelled during shutdown.")
    raise  # 必须传播
except EngineDisposedError:
    logger.warning("[Module] Engine disposed, skipping operation.")
    return  # 优雅降级
except Exception as e:
    error_info = classify_error(e, context="general")  # context: general/token/llm/db/chart
    severity = classify_severity(e, context="general")  # 返回: system/recoverable/operational
    if severity == "system":
        logger.critical(f"[Module] SYSTEM-LEVEL failure: {e}")
    elif severity == "recoverable":
        logger.warning(f"[Module] Recoverable error ({error_info['code']}): {e}")
    else:
        logger.error(f"[Module] Operational error: {e}")
```

**错误分类上下文** (`classify_error` 的 `context` 参数):
- `"token"` — Tushare Token 验证错误
- `"llm"` — LLM API 调用错误 (区分永久错误/瞬态可重试错误)
- `"db"` — 数据库连接/认证错误
- `"chart"` — 图表渲染错误
- `"general"` — 通用错误 (默认)

---

## 5. 设计模式

### 5.1 策略模式 (自动注册)

```python
from strategies.base_strategy import BaseStrategy, register_strategy

@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):
    required_context_keys = ["screening_data"]
    required_tables = ["daily_quotes"]

    def __init__(self):
        super().__init__(name_key="strategy_my", desc_key="strategy_my_desc")

    async def filter(self, context: StrategyContext):
        # 策略逻辑
        pass
```

**策略依赖检查**: 每个策略声明 `required_context_keys` 和 `required_tables`，运行前通过 `check_dependencies()` 验证数据就绪状态，返回 `ready` / `degraded` / `unready`。

### 5.2 Polars 向量化策略基类

继承 `PolarsBaseStrategy` 可使用 Polars LazyFrame 进行高性能向量化计算：

```python
from strategies.polars_base import PolarsBaseStrategy

class MyPolarsStrategy(PolarsBaseStrategy):
    def _filter_logic(self, lf: pl.LazyFrame, context: StrategyContext) -> pl.LazyFrame:
        return lf.filter(pl.col("pct_chg") > 5.0)
```

### 5.3 AI 策略混入 (AIMixin)

`strategies/ai_mixin.py` 提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：
- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量

### 5.4 DAO 模式

所有数据访问通过 `BaseDao` 子类，统一提供：
- `_read_db()` — 原生 SQL 读取，返回 DataFrame
- `_read_db_select()` — SQLAlchemy Core 查询 (推荐，防注入)
- `_write_db()` — 单条写入 (⚠️ `is_many=True` 已废弃)
- `_save_upsert()` — 批量 UPSERT (推荐，基于 `pg_insert` + `ON CONFLICT`)
- `chunked_in_query()` — 分块 IN 查询 (避免参数上限)

**DAO 继承体系**: `BaseDao` → `StockDao` / `QuoteDao` / `FinancialDao` / `MarketDao` / `ScreenerDao` / `SyncDao` / `MacroDao` / `HolderDao`

### 5.5 数据同步架构

`data/sync/` 下按数据类别组织同步策略：
- `base.py` — 同步基类 (断点续传、进度回调、错误重试)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表结构、同步配置、质量监控配置。

### 5.6 TaskManager 任务生命周期

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
```
- 任务通过 `submit_task()` 提交，传入 `coroutine_factory`
- 使用 `update_progress()` 报告进度 (0.0-1.0)，内置节流
- `is_cancelled()` 用于工作线程检测取消

### 5.7 配置管理与质量门控

- **配置管理**: `ConfigHandler` 使用读写锁 (`ReaderWriterLock`) 保护并发访问。敏感信息优先使用 `keyring`，降级到 AES-GCM 加密文件。
- **数据质量门控**: 使用 `@require_quality(QualityTier.SILVER)` 确保只有数据质量达标才执行逻辑。质量分层: `CRITICAL(0)` → `BRONZE(1)` → `SILVER(2)` → `GOLD(3)`。
- **性能监控装饰器**: 
  - `@log_async_operation(operation_name="fetch_data", threshold_ms=500)` — 异步操作日志 + 性能监控
  - `@track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` — 纯性能追踪
  - `@log_ui_action(component_name="Settings", action_type="Click")` — UI 交互埋点
  - `AsyncOperationLogger` — 复杂流程分段日志上下文管理器

**标准性能红线 (`PerfThreshold`)**:

| 常量 | 阈值 | 场景 |
|------|------|------|
| `MEMORY_COMPUTE` | 50ms | 内存与本地纯计算 |
| `DB_SINGLE_QUERY` | 200ms | 数据库单行/少数读写 |
| `EXTERNAL_NETWORK` | 2000ms | 外部公网接口调用 |
| `DB_BULK_IO` | 5000ms | 数据库大批量聚合插入 |
| `AI_INFERENCE` | 15000ms | 本地大模型推理计算 |
| `GLOBAL_INIT` | 15000ms | 全局初始化大动作 |

---

## 6. 测试规范

### 6.1 测试架构

分为 `unit/` (单元测试, 纯逻辑隔离), `integration/` (集成测试, 依赖 PostgreSQL), `e2e/` (端到端测试)。

测试标记:
- `@pytest.mark.unit` — 单元测试
- `@pytest.mark.integration` — 集成测试
- `@pytest.mark.e2e` — 端到端测试
- `@pytest.mark.slow` — 慢速测试 (真实 sleep、大量 IO)
- `@pytest.mark.network` — 需要真实网络访问

### 6.2 测试编写规则

- **单例隔离**: 使用 `reset_singleton` 上下文管理器 (支持 `extra_attrs` 参数重置额外类属性)
  ```python
  from tests.conftest import reset_singleton

  with reset_singleton(TaskManager):
      mgr = TaskManager()
      # 测试逻辑...
  # 自动恢复原始单例状态
  ```
- **Mock 规范**: `keyring` 和 `litellm` 在 `conftest.py` 中全局 mock (session 级别)，测试间自动清理
- **异步测试**: 使用 `pytest-asyncio`，`asyncio_mode = "auto"` 自动处理
- **事件循环策略**: Windows 使用 `WindowsSelectorEventLoopPolicy`，循环范围 `session` 级
- **配置隔离**: 测试使用临时配置文件，通过 `pytest_configure` 早期拦截

### 6.3 覆盖率要求

- **整体覆盖率** ≥ 80%
- **单文件覆盖率** ≥ 75%
- **覆盖率排除行**: `pragma: no cover`、`if __name__ == "__main__"`、`if TYPE_CHECKING:`、`raise NotImplementedError`、`...`
- 本地运行: `python -m pytest tests/ --cov --cov-report=term-missing --cov-report=json` 及 `python scripts/check_per_file_coverage.py`

---

## 7. CI/CD 流水线与门禁

流水线双平台验证 (Linux + Windows)，必须通过以下门禁：
1. **Ruff Check & Format**
2. **Pyright Type Check** (`continue-on-error: false`)
3. **pip-audit** (安全漏洞扫描)
4. **Alembic Migration** (验证 upgrade → downgrade → upgrade)
5. **Unit & Integration Tests**
6. **Per-File (≥ 75%) & Overall Coverage (≥ 80%)**

### 7.1 Pre-commit Hooks

本项目使用 6 个 pre-commit hook，提交前必须全部通过：

| Hook | 功能 |
|------|------|
| `ruff-check` | Ruff Lint 检查 (自动修复 `--fix`) |
| `ruff-format` | Ruff 代码格式化 |
| `type-ignore-reason` | 拒绝裸 `# type: ignore` (必须带 `[reason]`) |
| `pip-compile-core` | 同步 `pyproject.toml` → `requirements.txt` |
| `pip-compile-dev` | 同步 `pyproject.toml[dev]` → `requirements-dev.txt` |
| `pip-compile-optional` | 同步 `pyproject.toml[optional]` → `requirements-optional.txt` |

---

## 8. 关键约束与红线 🚨

这是不可逾越的底线，在任何代码修改中必须绝对遵守：

### ❌ 绝对禁止
- **架构越界**：在 `core/` 中导入其他业务层模块；在 `data/` 或 `services/` 中导入 `ui/`。
- **异常吞没**：吞没 `asyncio.CancelledError` (必须 re-raise 以配合优雅停机)。
- **模糊压制**：使用 `type: ignore` 时不带理由注释。
- **SQL 注入隐患**：在 asyncpg 查询中使用 `%s` 占位符 (必须用 `$1, $2`)。
- **僵尸连接操作**：在 disposed 的引擎上执行数据库操作 (必须检查 `_disposed` 标志，否则抛 `EngineDisposedError`)。
- **过时类型注解**：直接使用 `Union[X, Y]` / `Optional[X]` (必须使用 Python 3.10+ 的 `X | Y` / `X | None`)。
- **测试状态污染**：测试中不隔离单例 (必须使用 `reset_singleton`)。
- **废弃 API**：使用 `_write_db(is_many=True)` 进行批量写入 (必须用 `_save_upsert()`)。

### ✅ 强制要求
- 所有的单例必须注册到 `singleton_registry`。
- 所有异步操作的 CPU/IO 任务必须通过 `ThreadPoolManager` 分离到对应的线程池。
- `BaseDao` 的批量写入必须使用 `_save_upsert()`。
- Pre-commit hooks 必须在提交前执行并保持通过。
- 新增 DAO 必须在 `CacheManager.__init__` 中注册并在 `_create_engine` 中更新引擎引用。
- 新增数据表必须在 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 中注册。
- 新增策略必须使用 `@register_strategy("key")` 装饰器注册。

---

## 9. 常用命令

```bash
# 格式化与静态检查
python -m ruff check . --fix
python -m ruff format .
npx pyright

# 运行测试
python -m pytest tests/unit/ -v --tb=short -m "not slow"
python -m pytest tests/integration/ -v --tb=short

# 覆盖率
python -m pytest tests/ --cov --cov-report=term-missing --cov-report=json
python scripts/check_per_file_coverage.py

# 数据库与安全
python -m alembic upgrade head
pip-audit -s osv -r requirements.txt --desc

# Pre-commit
pre-commit run --all-files
```

## 10. 目录速查

### 入口与引导
- **`main.py`**: 应用入口，Flet 页面初始化、窗口生命周期、优雅停机
- **`config.py`**: 全局配置常量 (DB_URL、tiktoken 缓存路径)
- **`app/bootstrap.py`**: 服务编排引导 (onboarding 检测、服务初始化)

### 核心层
- **`core/i18n.py`**: 国际化引擎 (中/英双语)

### 数据层
- **`data/cache/cache_manager.py`**: 单例缓存管理器，所有 DAO 的统一入口与引擎管理
- **`data/persistence/daos/base_dao.py`**: DAO 基类 (read/write/upsert/chunked_in)
- **`data/persistence/models.py`**: SQLAlchemy ORM 模型定义
- **`data/persistence/db_migrator.py`**: Alembic 迁移执行器
- **`data/persistence/quality_gate.py`**: 数据质量门控装饰器
- **`data/data_dictionary.py`**: 表定义注册表 (结构 + 同步配置 + 质量监控)
- **`data/data_processor.py`**: 数据预处理器 (质量分级、指标计算)
- **`data/constants.py`**: 数据层常量
- **`data/external/tushare_client.py`**: Tushare Pro API 封装
- **`data/external/news_fetcher.py`**: 新闻数据抓取
- **`data/external/news_subscription.py`**: 新闻实时订阅服务
- **`data/sync/`**: 数据同步策略 (historical / financial / holder / macro)
- **`data/domain_services/trade_calendar_service.py`**: 交易日历服务

### 应用服务层
- **`services/ai_service.py`**: AI 服务 (LiteLLM 多模型网关)
- **`services/task_manager.py`**: 异步任务管理器 (并发、持久化、UI 通知)
- **`services/local_model_manager.py`**: 本地 GGUF 模型下载与管理

### 策略层
- **`strategies/base_strategy.py`**: 策略基类与自动注册装饰器
- **`strategies/polars_base.py`**: Polars 向量化策略基类
- **`strategies/ai_mixin.py`**: AI 策略混入 (LLM 驱动选股)
- **`strategies/ai_strategy.py`**: AI 策略实现
- **`strategies/oversold_strategy.py`**: 超跌反弹策略
- **`strategies/fundamental.py`**: 基本面策略
- **`strategies/market.py`**: 市场策略

### 表现层
- **`ui/app_layout.py`**: 主布局 (5 标签页导航)
- **`ui/theme.py`**: 主题系统 (亮色/暗色)
- **`ui/viewmodels/`**: MVVM 视图模型
- **`ui/views/`**: 视图页面
- **`ui/components/`**: 可复用 UI 组件

### 工具层
- **`utils/config_handler.py`**: 配置读写 (读写锁 + keyring/AES-GCM)
- **`utils/shutdown.py`**: 优雅退出协调器 (看门狗 + 分步超时)
- **`utils/thread_pool.py`**: 线程池管理器 (IO/CPU 分离)
- **`utils/singleton_registry.py`**: 单例注册表 (统一重置 + 集中 atexit)
- **`utils/error_classifier.py`**: 错误分类器 (severity + code + message_key)
- **`utils/log_decorators.py`**: 日志装饰器 (性能监控、UI 埋点、操作追踪)
- **`utils/scheduler_service.py`**: APScheduler 调度服务封装
- **`utils/security_utils.py`**: 安全工具 (AES-GCM 加解密)
- **`utils/rate_limiter.py`**: API 限流器
- **`utils/proxy_manager.py`**: 代理管理器
- **`utils/sanitizers.py`**: 数据脱敏工具
- **`utils/technical_analysis.py`**: 技术分析指标计算
- **`utils/time_utils.py`**: 时间工具函数
- **`utils/loop_local.py`**: 事件循环本地存储 (防止跨循环死锁)
