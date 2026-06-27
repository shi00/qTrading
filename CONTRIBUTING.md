# Contributing to AStockScreener

感谢你考虑为 AStockScreener 做贡献！本文档分为三部分：人类贡献者指南、开发环境与命令参考、实现规范手册。

> **AI 编程助手注意**：[CLAUDE.md](./CLAUDE.md) 是项目宪法（红线、架构边界、交互准则），每次会话自动加载。本文件第三部分「实现规范手册」承接宪法中移出的代码模板与详细规范，需要时按需查阅。

## 目录

- [第一部分：人类贡献者指南](#第一部分人类贡献者指南)
  - [行为准则](#行为准则)
  - [如何贡献](#如何贡献)
  - [Pull Request 流程](#pull-request-流程)
  - [代码审查与合并](#代码审查与合并)
- [第二部分：开发环境与命令参考](#第二部分开发环境与命令参考)
  - [前置要求](#前置要求)
  - [安装步骤](#安装步骤)
  - [数据库设置](#数据库设置)
  - [常用开发与测试命令](#常用开发与测试命令)
  - [代码风格基础](#代码风格基础)
  - [提交信息规范](#提交信息规范)
- [第三部分：实现规范手册](#第三部分实现规范手册)
  - [单例模式实现模板](#单例模式实现模板)
  - [策略模式实现模板](#策略模式实现模板)
  - [Polars 向量化策略基类](#polars-向量化策略基类)
  - [AI 策略混入](#ai-策略混入)
  - [DAO 模式](#dao-模式)
  - [数据同步架构](#数据同步架构)
  - [TaskManager 任务生命周期](#taskmanager-任务生命周期)
  - [配置管理、质量门控、性能监控](#配置管理质量门控性能监控)
  - [MVVM 表现层](#mvvm-表现层)
  - [类型标注与 Pyright 规则](#类型标注与-pyright-规则)
  - [日志规范](#日志规范)
  - [异步编程规范](#异步编程规范)
  - [数据库操作规范](#数据库操作规范)
  - [错误处理标准模式](#错误处理标准模式)
  - [测试规范](#测试规范)
  - [CI/CD 流水线与门禁](#cicd-流水线与门禁)
  - [语言切换响应 (I18n Hot Reload)](#语言切换响应-i18n-hot-reload)
  - [标准开发工作流 (How-To)](#标准开发工作流-how-to)
  - [排查典型问题](#排查典型问题)
  - [已知架构技术债 (Known Technical Debt)](#已知架构技术债-known-technical-debt)

---

# 第一部分：人类贡献者指南

## 行为准则

本项目采用贡献者公约作为行为准则。参与此项目即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

如果你发现了 bug，请通过 [GitHub Issues](https://github.com/shi00/qTrading/issues) 提交。提交前请：

1. 搜索现有 issues，确认没有被报告过
2. 按以下清单提供信息：
   - 问题描述
   - 复现步骤
   - 期望行为
   - 实际行为
   - 环境信息（操作系统、Python 版本等）

### 提出新功能

欢迎提出新功能建议！请在 Issue 中详细描述：

- 功能描述
- 使用场景
- 可能的实现方案

### 代码复用与避免重复造轮子

在开始编写新代码前，请务必遵循**复用优先**原则，避免重复造轮子：
1. **优先复用工程已有代码**：开发新功能前，先全局搜索项目中是否已有类似的工具函数、基础类、UI 组件或业务逻辑。
2. **优先使用成熟开源库**：若需引入常见的基础功能，优先采用业界广泛使用、维护活跃的稳定开源库，而非自行实现。
3. **避免无谓的封装**：如果已有成熟库提供了所需功能，请直接使用，不要对其进行单薄的二次封装，除非这种封装能带来明显的业务价值（如统一鉴权、异常转换等）。

### 提交代码

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## Pull Request 流程

### 提交前自检

1. 确保所有测试通过（单元、集成、E2E 视变更范围）
2. 确保代码覆盖率达标（整体 ≥ 85%，单文件 ≥ 80%）
3. 运行 `pre-commit run --all-files`
4. 更新相关文档（README / CLAUDE.md / CONTRIBUTING.md / SECURITY.md 视变更范围）

### PR 描述模板

提交 PR 时，GitHub 会自动加载项目预设的模板内容。
为了避免文档与实际模板内容的漂移不一致，请直接查阅实际文件：[`.github/PULL_REQUEST_TEMPLATE.md`](./.github/PULL_REQUEST_TEMPLATE.md)。

> **注意**：提交 PR 时无需手动复制该模板，只需在 GitHub 自动生成的草稿基础上如实勾选与填写。请务必确认满足“提交前自检清单（强制全部核对）”项。

## 代码审查与合并

### 审查要求

- 所有 PR 需要至少一位 reviewer 批准
- 某些关键路径（如 `data/persistence/`、`strategies/`）需要 CODEOWNERS 批准
- 解决所有 review 意见后才能合并

### 合并策略

我们使用 **Merge Queue** 确保合并安全：

1. PR 获得批准后，点击 "Ready for review" → "Merge when ready"
2. 系统会自动将 PR 加入合并队列
3. 在队列中会与 main 最新代码组合后重新运行 CI
4. 通过后自动合并

---

# 第二部分：开发环境与命令参考

## 前置要求

- Python 3.13+
- PostgreSQL 16+
- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (依赖管理工具)

## 安装步骤

```bash
# 克隆仓库
git clone https://github.com/shi00/qTrading.git
cd qTrading

# 创建虚拟环境
uv venv
.venv\Scripts\activate  # Windows
# 或 source .venv/bin/activate  # Linux/macOS

# 安装依赖
uv pip install --system -r requirements.txt
uv pip install --system -r requirements-optional.txt
uv pip install --system -r requirements-dev.txt

# 安装 pre-commit hooks
pre-commit install

# 运行测试验证环境
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

> 项目使用 7 个 pre-commit hook (Ruff lint/format、裸 `type: ignore` 检测、requirements 同步、版本一致性校验)，详见 `.pre-commit-config.yaml` 或 [Pre-commit Hooks](#pre-commit-hooks)。

## 数据库设置

> [!NOTE]
> 项目的数据库命名约定如下：
> - **项目名**：`AStockScreener`
> - **本地生产/开发库**：`astock_screener`（使用 `createdb astock_screener` 创建，由 Alembic 迁移驱动）
> - **本地集成测试库**：`test_astock`（由测试配置自动加载与清空，详见 [测试规范](#测试规范)）

```bash
# 创建数据库
createdb astock_screener

# 运行迁移
python -m alembic upgrade head
```

## 常用开发与测试命令

```bash
# 格式化与静态检查
python -m ruff check . --fix
python -m ruff format .
python -m pyright

# 运行测试
python -m pytest tests/unit/ -v --tb=short -m "not slow"
python -m pytest tests/integration/ -n auto -v --tb=short
python -m pytest tests/e2e/ -v --tb=short

# 覆盖率
python -m pytest tests/ --cov --cov-report=term-missing --cov-report=json
python scripts/check_per_file_coverage.py

# 数据库与安全
python -m alembic upgrade head
python -m alembic check
python -m alembic downgrade base
python -m alembic upgrade head
python scripts/run_pip_audit.py --requirements requirements.txt requirements-optional.txt --allowlist .security/audit-allowlist.yml --sources pypi osv

# 依赖同步 (通常由 pre-commit 自动触发)
uv pip compile --universal --no-emit-index-url pyproject.toml -o requirements.txt
uv pip compile --universal --no-emit-index-url --extra dev pyproject.toml -o requirements-dev.txt
uv pip compile --universal --no-emit-index-url --extra optional pyproject.toml -o requirements-optional.txt

# Pre-commit
pre-commit run --all-files

# 启动应用
python main.py
```

## 代码风格基础

### Python 代码规范

- 行宽：120 字符
- 缩进：4 空格
- 引号：双引号
- 使用 Python 3.13+ 语法（`X | None` 而非 `Optional[X]`）

### 工具

我们使用以下工具确保代码质量：

- **Ruff**: Lint 和格式化（规则 `F, E, W, UP, B, SIM, BLE`，忽略 `E501, E402, SIM102, SIM105, SIM108, SIM117, BLE001`）
- **Pyright**: 静态类型检查（`basic` 模式，配置见 `pyrightconfig.json`）
- **pytest**: 测试框架

### 运行检查

```bash
# Lint 检查
ruff check .

# 格式化
ruff format .

# 类型检查
pyright

# 运行测试
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

### 类型注解

- 所有公共函数必须有类型注解
- 使用 `# type: ignore[错误码]  # 原因` 格式抑制类型错误
- 禁止裸 `# type: ignore`（pre-commit 会拦截，对应 [CLAUDE.md R3](./CLAUDE.md#31--绝对禁止)）

## 提交信息规范

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### 类型

| 类型 | 描述 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具相关 |
| `ci` | CI 配置相关 |

### 示例

```
feat(strategy): add MACD crossover strategy

- Add MACD calculation using Polars
- Implement signal generation logic
- Add unit tests for edge cases

Closes #123
```

---

# 第三部分：实现规范手册

> 本部分承接 [CLAUDE.md](./CLAUDE.md) 宪法中移出的代码模板、详细规范与工作流步骤，供开发人员与 AI 编码助手按需查阅。

## 单例模式实现模板

> 对应 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式)。

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

**设计准则：依赖注入优先**

新增单例须支持依赖注入/可注入时钟：构造函数应接收可选的 config/clock 注入参数，默认走生产实现（ConfigHandler/time.monotonic），测试可传 fake。这样无需替换 sys.modules 或全局 patch。

```python
def __init__(self, *, config=None, clock=None):
    self._config = config  # None → 走 ConfigHandler
    self._clock = clock or time.monotonic  # None → 走 time.monotonic
```

## 策略模式实现模板

> 对应 [CLAUDE.md §6.1](./CLAUDE.md#6-设计模式索引)。

```python
from strategies.base_strategy import BaseStrategy, register_strategy
from strategies.utils import StrategyContext

@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):
    required_context_keys: tuple[str, ...] = ("screening_data",)
    required_tables: tuple[str, ...] = ("daily_quotes",)
    required_history_days = 60

    def __init__(self):
        super().__init__(name_key="strategy_my", desc_key="strategy_my_desc")

    async def filter(self, context: StrategyContext):
        # 策略逻辑：返回过滤后的 DataFrame
        ...
```

- **策略入口**: `strategies/all_strategies.py` 通过导入触发 `@register_strategy`，由 `_STRATEGY_REGISTRY` 统一暴露。
- **策略 API**: 依赖声明 (`required_context_keys`/`required_tables`/`required_history_days`/`required_apis`)、动态参数 (`get_parameters()`)、动态描述 (`get_dynamic_description()`)、依赖检查 (`check_dependencies()`) — 详见 `strategies/base_strategy.py`。
- **新增策略流程**: 见 [标准开发工作流](#3-新增一个策略)。

## Polars 向量化策略基类

> 对应 [CLAUDE.md §6.2](./CLAUDE.md#6-设计模式索引)。

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

> 注：上述类属性模式适用于 `PolarsBaseStrategy` 子类。非 `PolarsBaseStrategy` 子类（如 `OversoldStrategy` 继承 `BaseStrategy` + `AIStrategyMixin`）可使用 `@require_quality` 装饰器。

## AI 策略混入

> 对应 [CLAUDE.md §6.3](./CLAUDE.md#6-设计模式索引)。

`strategies/ai_mixin.py` 的 `AIStrategyMixin` 类提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：

- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量、Prompt 安全防护 (`utils/prompt_guard.py`)
- Prompt 模板集中在 `strategies/strategy_prompts.py`，响应校验在 `strategies/prompt_validator.py`

## DAO 模式

> 对应 [CLAUDE.md §6.4](./CLAUDE.md#6-设计模式索引)。

所有数据访问通过 `BaseDao` 子类，统一提供：

- `_read_db()` — 原生 SQL 读取，返回 DataFrame
- `_read_db_select()` — SQLAlchemy Core 查询 (**推荐**，防注入)
- `_write_db()` — 单条写入 (⚠️ `is_many=True` 已废弃，使用会触发 `DeprecationWarning`)
- `_save_upsert()` — 批量 UPSERT (**推荐**，基于 `pg_insert` + `ON CONFLICT`)
- `chunked_in_query()` — 分块 IN 查询 (避免参数上限)

**DAO 继承体系**: `BaseDao` → 具体子类见 `data/persistence/daos/` 目录

## 数据同步架构

> 对应 [CLAUDE.md §6.5](./CLAUDE.md#6-设计模式索引)。

`data/sync/` 下按数据类别组织同步策略：

- `base.py` — 同步基础定义 (`SyncContext` 依赖注入容器、`SyncResult` 结果数据类、`ISyncStrategy` 策略接口，含取消支持)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表结构、同步配置、质量监控配置。

## TaskManager 任务生命周期

> 对应 [CLAUDE.md §6.6](./CLAUDE.md#6-设计模式索引)。

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
```

- 任务通过 `submit_task()` 提交，传入 `coroutine_factory` (无参可调用对象，返回 coroutine)
- 使用 `update_progress(progress)` 报告进度 (0.0-1.0)，内置节流避免 UI 风暴
- 工作协程内部使用 `is_cancelled()` 检测取消信号 (用户主动取消 / 应用退出)
- 任务持久化到本地，重启后 `RUNNING` 状态会被回填为 `INTERRUPTED`

## 配置管理、质量门控、性能监控

> 对应 [CLAUDE.md §6.7](./CLAUDE.md#6-设计模式索引)。

### 配置管理

`ConfigHandler` 使用读写锁 (`rwlock.RWLockFair`) 保护并发访问。敏感信息优先使用 `keyring`，降级到 AES-GCM 加密文件 (`utils/security_utils.py`)。

### 数据质量门控

使用 `@require_quality(QualityTier.SILVER)` 确保只有数据质量达标才执行逻辑。质量分层: `CRITICAL(0)` → `BRONZE(1)` → `SILVER(2)` → `GOLD(3)`。`STRICT_QUALITY_GATE` 环境变量控制严格模式（默认开启，设为 `false` 关闭）。

### 性能监控装饰器

`utils/log_decorators.py` 提供：

- `@log_async_operation(operation_name="fetch_data", threshold_ms=500)` — 异步操作日志 + 性能监控 + 自动脱敏
- `@track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` — 纯性能追踪 (轻量)
- `@log_ui_action(component_name="Settings", action_type="Click")` — UI 交互埋点
- `AsyncOperationLogger` — 复杂流程分段日志上下文管理器
- **取舍**: 同一函数只挂一个性能装饰器，优先选 `@log_async_operation` (功能更完整)。

**标准性能红线 (`PerfThreshold`)**: 具体数值见 `utils/log_decorators.py`，涵盖内存计算/DB单查询/外部网络/DB批量IO/AI推理/全局初始化六类场景。

## MVVM 表现层

> 对应 [CLAUDE.md §6.8](./CLAUDE.md#6-设计模式索引)。

- **View** (`ui/views/`): 仅负责构建 Flet 控件树和绑定事件，不持有业务状态。事件回调将 (用户意图, 参数) 转发给 ViewModel。
- **ViewModel** (`ui/viewmodels/`): 持有业务状态 (DataFrame、筛选结果、加载标记)，调用 services/strategies/data 层，通过回调通知 View 刷新。
- **Component** (`ui/components/`): 可复用控件 (图表、对话框、虚拟表格、Toast)，不耦合具体业务。
- **Theme** (`ui/theme.py`): 亮/暗主题切换，颜色/字体 token 集中管理。
- **i18n** (`ui/i18n.py`): 对 `core.i18n` 的 UI 层薄封装，提供 Flet 文本绑定。

## 类型标注与 Pyright 规则

> 对应 [CLAUDE.md §5.2](./CLAUDE.md#52-类型标注)。

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

## 日志规范

> 对应 [CLAUDE.md §5.4](./CLAUDE.md#54-日志规范)。

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

## 异步编程规范

> 对应 [CLAUDE.md §5.5](./CLAUDE.md#55-异步编程规范)。

- **asyncio 模式**: 全项目使用 `asyncio` 驱动异步。
- **线程安全**: UI 回调可能来自线程池，使用 `loop.call_soon_threadsafe()` 转移到事件循环。
- **线程池分离**: IO 密集型使用 `TaskType.IO`，CPU 密集型 (NumPy/Pandas 等 GIL 释放型) 使用 `TaskType.CPU`；纯 Python CPU 密集任务应使用 `ProcessPoolExecutor` (项目暂无)。
- **CancelledError 必须传播**: 永远 `raise` 不吞没，否则破坏优雅停机 (对应 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止))。
- **事件循环绑定对象**: 使用 `utils.loop_local` 的 `get_loop_local()` / `del_loop_local()` / `clear_all_loop_locals()` 管理 `asyncio.Event`、`asyncio.Lock` 等绑定到特定事件循环的对象，避免跨循环死锁 (对应 [CLAUDE.md R11](./CLAUDE.md#31--绝对禁止))。
- **`asyncio.gather`** 涉及失败可恢复场景使用 `return_exceptions=True`，并在调用方逐个分类异常。
- **不要在 `__init__`** 中调用 `asyncio.create_task()`，会绑定到错误的事件循环；改为提供 `async def initialize()` 方法。

## 数据库操作规范

> 对应 [CLAUDE.md §5.6](./CLAUDE.md#56-数据库操作规范)。

- **异步引擎**: 使用 `asyncpg` 驱动 (通过 SQLAlchemy asyncio)。
- **参数占位符**: 使用 `$1, $2, ...` (asyncpg 原生占位符，非 `%s`) (对应 [CLAUDE.md R4](./CLAUDE.md#31--绝对禁止))。
- **批量写入**: 使用 `_save_upsert()` (基于 `ON CONFLICT DO UPDATE`，内置分块，大小见 `base_dao.py`) (对应 [CLAUDE.md R8](./CLAUDE.md#31--绝对禁止))。
- **分块 IN 查询**: 使用 `chunked_in_query()` 避免 PostgreSQL 参数上限 (分块大小见 `base_dao.py`)。
- **引擎状态检查**: DAO 操作前必须确认引擎仍可用；关机/释放后继续访问时应抛出或传播 `EngineDisposedError`，调用方按关机降级处理。
- **维护锁**: DAO 操作前 `await self._get_maintenance_event().wait()` 等待维护完成 (基类已自动处理)。
- **慢查询阈值**: 见 `base_dao.py` 配置 (基类自动告警，无需手动埋点)。
- **DB 异常应在 DAO 层处理**: 业务层只接收 `EngineDisposedError` 和业务异常，不应直接捕获 `asyncpg.*Error`。

## 错误处理标准模式

> 对应 [CLAUDE.md §5.7](./CLAUDE.md#57-错误处理模式)。

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

## 测试规范

> 对应 [CLAUDE.md §7](./CLAUDE.md#7-测试规范索引)。

### 测试架构

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

### 测试编写规则

- **单例隔离**: 单元测试（`tests/unit/`）由 `tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 自动重置所有注册单例。集成测试和 e2e 测试不自动重置单例，需手动管理。需精细控制单例初始化状态时（如测试 `__init__` 重复初始化防护），可使用 `singleton_state` 上下文管理器：

  ```python
  from tests.conftest import singleton_state

  with singleton_state(TaskManager, extra_attrs=["_initialized"]):
      mgr = TaskManager()
      # 测试逻辑...
  # 自动恢复原始单例状态
  ```

- **外部服务 Mock**: 单元测试由 `mock_external_services` autouse fixture 自动 mock 外部网络调用 (`NewsFetcher`/`ReviewManager`)。测试自身模块需跳过 mock 时，在文件顶部声明 `pytestmark = pytest.mark.no_auto_mock`。

- **Mock 规范**: `keyring` 和 `litellm` 在 `tests/conftest.py` 中全局 mock (session 别，`pytest_configure` 早期拦截)，每个测试后清理状态。
- **异步测试**: 使用 `pytest-asyncio`，`asyncio_mode = "auto"` 自动处理 (`async def test_xxx()` 即可)。
- **事件循环策略**: Windows 使用 `WindowsSelectorEventLoopPolicy`，loop scope 为 `session` 级。*(注：这引入了测试态特有的已知泄漏问题，详见本手册底部的「已知架构技术债」)*
- **配置隔离**: 测试使用临时配置文件 (`tempfile.mkdtemp`)，通过 `pytest_configure` 在 import 之前重写 `utils.config_handler.CONFIG_FILE`。
- **DB 隔离**: 集成测试连接 `test_astock` 数据库 (CI 通过 service container 启动 PostgreSQL 16)，通过 `TEST_DB_*` 环境变量配置。

### 覆盖率要求

> [!NOTE]
> 覆盖率阈值的单一事实源位于 `pyproject.toml`。
> - **整体覆盖率**：具体数值见 `pyproject.toml` 中的 `fail_under`（目前为 ≥ 85%）
> - **单文件覆盖率**：具体数值见 `pyproject.toml` 中的 `per_file_minimum`（目前为 ≥ 80%，由 `scripts/check_per_file_coverage.py` 强制检查）
> - **覆盖率源**：`core`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）
> - **覆盖率排除行**：`pragma: no cover`、`if __name__ == "__main__"`、`if TYPE_CHECKING:`、`raise NotImplementedError`、`...`

## CI/CD 流水线与门禁

> 对应 [CLAUDE.md §8](./CLAUDE.md#8-cicd-门禁索引)。

GitHub Actions 双平台验证 (`.github/workflows/ci_cd.yml`)，PR/主干质量门禁包括：

1. **Fast Ruff Check & Format** (Python 3.13 + 3.14 experimental)
2. **Pre-commit Hooks** (Ruff、格式化、裸 `type: ignore`、requirements 同步)
3. **Security Audit** (`scripts/run_pip_audit.py`，扫描 `requirements.txt`、`requirements-optional.txt`、`requirements-dev.txt`，使用 `.security/audit-allowlist.yml`)
4. **Pyright Type Check** (版本见 `ci_cd.yml`，`continue-on-error: false`)
5. **Alembic Migration** (`upgrade head` → `alembic check` → `downgrade base` → `upgrade head`)
6. **Unit & Integration Tests** (Linux/Windows unit，Linux integration)
7. **Windows E2E Tests** (`tests/e2e/`，Chromium + PostgreSQL)
8. **Per-File (≥ 80%) & Overall Coverage (≥ 85%)**
9. **requirements*.txt 漂移处理** (`requirements-drift` job 检测到 main 分支漂移时，由 `update-requirements` job 创建同步 PR)

发布流程: 打 `v*.*.*` tag → 触发 `build-windows` job → PyInstaller 打包 CPU/CUDA 两个变体 → smoke test → Inno Setup 制作安装包 → GitHub Release 发布。

**其他 workflow**: CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、自动化 Release PR (`release-please.yml`)、依赖更新机器人 (`renovate.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)。

### Pre-commit Hooks

本项目使用 7 个 pre-commit hook (定义在 `.pre-commit-config.yaml`，含 Ruff lint/format、裸 `type: ignore` 检测、requirements 同步、版本一致性校验)，提交前必须全部通过。

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链

## 语言切换响应 (I18n Hot Reload)

> 对应 [CLAUDE.md §5.8](./CLAUDE.md#58-语言切换响应规范-i18n-hot-reload)。

程序运行后动态切换语言时，所有 UI 控件必须正确刷新。新增/修改 UI 视图或组件时，必须遵守以下 9 条规范。

### 判定决策树

按顺序自问，命中则需遵守对应子规范：

```
我的视图/组件展示 I18n.get() 文案吗？
├─ 是 → 规范 1（订阅机制）+ 规范 9（异常降级与判空）
├─ 有 ft.Dropdown 且 options 含 I18n.get() 文案吗？
│   └─ 是 → 规范 4（options 重建保留 value）
├─ 有内联 ft.Text/ft.IconButton 使用 I18n.get() 吗？
│   └─ 是 → 规范 5（实例属性提取）
├─ 含子 panel/子组件吗？
│   ├─ 是 → 规范 6（子组件级联与状态保留）
│   └─ 子组件是否满足「永久子组件模式豁免」三条件？
│       └─ 是 → 子组件可不自行订阅（规范 1 例外），父级联调用其 update_locale
├─ 是延迟挂载或缓存的视图吗？（_view_cache/tab_contents）
│   └─ 是 → 规范 7（生命周期兜底）
├─ 使用 MetaDataManager.get_table_alias/get_column_alias 吗？
│   └─ 是 → 规范 8（缓存失效）
└─ refresh_locale 需要重算数据吗？
    └─ 是 → 规范 3（纯 UI 操作，复用 VM 缓存）
```

### 9 条规范

1. **订阅机制**：展示 i18n 文案的 View/Component 必须在 `did_mount` 中调用 `I18n.subscribe(callback)` 并将返回的 `subscription_id` 保存为实例属性（如 `self._locale_subscription_id`），在 `will_unmount` 中调用 `I18n.unsubscribe(subscription_id)`。`subscribe` 默认 `sync_immediately=True`，订阅时立即触发一次回调，保证延迟挂载组件用当前 locale 渲染。

   **例外（永久子组件模式豁免）**：若子组件同时满足以下三个条件，可不自行订阅 locale 变更，由父视图级联调用其 `update_locale`/`refresh_locale` 完成刷新：
   - (1) 子组件在父 `__init__` 中创建，整个生命周期不被重建；
   - (2) 父 `refresh_locale`/`_on_locale_change` 显式级联调用子组件的 `update_locale`/`refresh_locale`；
   - (3) 子组件不会被独立挂载（如不在页面顶层独立使用）。

   **不适用本豁免的场景**：
   - 若父视图在 `refresh_locale` 中重建子组件（如 `OnboardingWizard`/`AIBrainTab` 的重建子 panel 模式），必须遵守规范 6（旧 panel `will_unmount` + 新 panel 订阅或父级联），不适用本豁免。
   - 本豁免仅针对规范 1（订阅机制）。子组件的 `update_locale`/`refresh_locale` 实现仍必须遵守规范 4（options 重建）、规范 5（实例属性提取）、规范 8（MetaDataManager 缓存失效）、规范 9（异常降级与判空）。
   - **不确定是否满足豁免条件时，默认订阅。**

   适用本豁免的组件清单：`NewsFeed`、`MarketDashboard`、`BacktestConfigPanel`、`BacktestResultPanel`、`SQLConsoleTab`、`TableViewerTab`、`HealthScoreCard`/`MetricTile`/`KeyMetricsGrid`/`CoverageDetailTable`（health_report_dialog 内子组件）、`DashboardCard`/`SettingRow`/`MetricCard`/`ActionChip`/`StatusBadge`/`SectionHeader`（settings_widgets 哑组件）。

2. **回调命名与签名**：回调方法统一命名为 `refresh_locale`（视图层）或 `_on_locale_change`（已在用的旧组件可保留），**方法签名无参数**（`def refresh_locale(self):` / `def _on_locale_change(self):`）。`I18n.subscribe` 是零参调用，带参签名中的 `new_locale` 参数永远是默认值（死参数）。

3. **纯 UI 操作**：`refresh_locale` 必须是纯 UI 操作，**禁止网络 IO / 数据库查询**；只能刷新控件字段值（`text`/`label`/`tooltip`/`hint_text`/`options` 等）。需要重算的数据（如日期格式化）应复用 ViewModel 缓存，不触发新请求。

4. **下拉框 options 同步**：所有 `ft.Dropdown` 的 `options` 中如果包含 `I18n.get()` 翻译文案，`refresh_locale` 必须重建 `options` 列表（**保留当前 `value`**），不能只刷新 `label`。枚举型 options（如主题、日志级别、积分档位）必须重建；语言选择器 options 也需重建以保持一致性。

   **⚠️ 必须强制刷新 value（Flet 已知坑）**：`Control._set_attr_internal` 对相等值短路（源码 `flet/core/control.py:189` 的 `orig_val[0] != value` 判断），不标记 dirty。因此以下两种"看似正确"的写法**均无效**，前端收不到 value 变更，闭合状态下选中项显示文本不会刷新：

   - `dropdown.value = dropdown.value`（自赋值）
   - `saved = dropdown.value; ...; dropdown.value = saved`（saved_value 模式）

   必须先置 `None` 再恢复，强制触发两次 dirty：

   ```python
   saved = dropdown.value
   dropdown.value = None  # 强制触发 dirty（Flet 对相等值短路，必须先置空）
   dropdown.options = [...]  # 重建 options（含新 locale 文案）
   dropdown.value = saved   # 再次触发 dirty，前端 rebuild 选中项文本
   ```

   原理：`value=None` 时原值非空，被改写为 `""` 并触发 dirty；`value=saved` 时原值为 `""`，再次触发 dirty。前端收到两次 value 变更事件，`DropdownButton` 必然 rebuild 并重新查找选中项显示文本。

   **边界情况**：当 `saved_value` 为 `None`（用户未选中）时，闭合态本就无文本需刷新，无需此修复，但套用本模式也无副作用；当 `saved_value` 为空字符串 `""` 时本方案失效（两次赋值均被短路），实际项目 option key 应非空，若出现需单独处理。

5. **实例属性提取**：内联在 `ft.Row`/`ft.Column` 中且 `tooltip`/`text` 来自 `I18n.get()` 的控件（如 `ft.IconButton`、`ft.Text`），必须提取为实例属性（`self.xxx_btn`/`self.xxx_text`），否则 `refresh_locale` 无法引用。`MetricCard`/`ActionChip`/`StatusBadge` 等复合组件通过 `set_label`/`set_text` 方法刷新。

6. **子组件级联与状态保留**：父视图的 `refresh_locale` 必须级联调用子组件的 `refresh_locale`（若存在）或 `_on_locale_change`。包含子 panel 的视图（如 `OnboardingWizard`、`AIBrainTab`、`BacktestView`）在重建子 panel 前必须先调用旧 panel 的 `will_unmount`，取消其 I18n 订阅避免泄漏；**重建后必须恢复旧 panel 的用户输入状态**（如表单值、选中项），避免用户输入丢失。

7. **生命周期兜底**：延迟挂载或缓存的视图（如 `AppLayout._view_cache`、`SettingsView.tab_contents`）可能在未挂载时错过 I18n 通知。切换到此类视图时（`_on_tab_click`/`_execute_tab_switch`）必须显式调用 `refresh_locale` 兜底，确保文案与当前 locale 一致。

8. **MetaDataManager 缓存失效**：涉及表/列别名（`get_table_alias`/`get_column_alias`）的视图（如 `DataExplorerView`、`ScreenerView`），`refresh_locale` 必须先调用 `MetaDataManager.invalidate_cache()` 让别名缓存失效，再重建表头/列定义。

9. **异常降级与判空**：`refresh_locale` 与子组件的 `_on_locale_change` 整个方法体必须用 `try/except` 包裹（捕获 `Exception`，`CancelledError` 按 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止) 传播），异常降级为 `logger.warning`，不得抛出。末尾必须用 `if self.page: self.update()` 判空更新，避免组件未挂载时抛异常。

### 反模式（禁止）

- ❌ 在 `refresh_locale` 中调用网络请求 / 数据库查询 / 重型 CPU 计算
- ❌ 只刷新 `Dropdown.label` 而不重建 `options`
- ❌ 重建 `options` 时不保留当前 `value`
- ❌ `dropdown.value = dropdown.value` 自赋值刷新（被 Flet `_set_attr_internal` 短路，无效）
- ❌ 仅 `value = saved_value` 不先置 `None`（同样被短路，闭合态选中项文本不刷新）
- ❌ 重建子 panel 前不调用旧 panel 的 `will_unmount`
- ❌ 重建子 panel 后不恢复用户输入状态（如表单值、选中项）
- ❌ 内联 `ft.Text(I18n.get(...))` 不提取为实例属性，导致无法刷新
- ❌ 视图未订阅 I18n，依赖父视图级联调用（除满足规范 1「永久子组件模式豁免」三条件的组件外，如 `SQLConsoleTab`/`TableViewerTab`/`NewsFeed`/`MarketDashboard`/`BacktestConfigPanel`/`BacktestResultPanel`）
- ❌ 延迟挂载视图切换时不调用 `refresh_locale` 兜底
- ❌ 涉及表/列别名的视图未调用 `MetaDataManager.invalidate_cache()` 就重建表头
- ❌ `refresh_locale` 未用 `try/except` 包裹或末尾未判空 `self.page`

### 标准 View 模板

包含订阅、Dropdown options 重建、try/except、判空：

```python
import logging

import flet as ft

from ui.i18n import I18n

logger = logging.getLogger(__name__)


class MyView(ft.Container):
    def __init__(self, page):
        super().__init__()
        self.app_page = page
        self.expand = True
        self._locale_subscription_id: object | None = None

        # 提取为实例属性（规范 5）
        self.title_text = ft.Text(I18n.get("my_view_title"))
        self.action_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=I18n.get("my_view_refresh"),
            on_click=self._on_action,
        )

        # Dropdown（规范 4：options 含 I18n 文案）
        self.size_dropdown = ft.Dropdown(
            label=I18n.get("my_view_page_size"),
            value="20",
            options=[
                ft.dropdown.Option("10", text=f"10 {I18n.get('unit_per_page')}"),
                ft.dropdown.Option("20", text=f"20 {I18n.get('unit_per_page')}"),
                ft.dropdown.Option("50", text=f"50 {I18n.get('unit_per_page')}"),
            ],
        )

        self.content = ft.Column([self.title_text, self.size_dropdown, self.action_btn])

        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    def _on_mount(self):
        # 规范 1：订阅并保存 subscription_id
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def _on_unmount(self):
        # 规范 1：取消订阅
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    async def _on_action(self, e: ft.ControlEvent):
        """UI 按钮点击事件处理示例"""
        # [防阻塞规范 (CLAUDE.md R16)] 
        # Flet 支持 async def 事件处理器。对于耗时的 IO/CPU 操作，必须提交到 ThreadPoolManager，避免阻塞 UI 渲染。
        from utils.thread_pool import ThreadPoolManager, TaskType
        
        self.action_btn.disabled = True
        self.update()
        
        try:
            # 耗时操作必须抛到外部线程池执行，例如：
            # result = await ThreadPoolManager.run_async(TaskType.IO, my_service.fetch_data)
            pass
        except Exception as err:
            logger.error("Action failed", exc_info=True)
            # 处理错误并展示 toast...
        finally:
            self.action_btn.disabled = False
            if self.page:  # [防崩溃规范] 异步结束更新UI前必须判空，防止组件在等待期间已被卸载
                self.update()

    def refresh_locale(self):
        # 规范 9：整个方法体 try/except 包裹
        try:
            # 规范 5：刷新实例属性
            self.title_text.value = I18n.get("my_view_title")
            self.action_btn.tooltip = I18n.get("my_view_refresh")

            self.size_dropdown.label = I18n.get("my_view_page_size")
            # 规范 4：重建 options 并强制刷新 value（Flet 对相等值短路，必须先置 None）
            # value 恢复放在 try 外，确保 options 重建失败也能恢复（§5.8 规范 9 异常降级）
            saved_value = self.size_dropdown.value
            self.size_dropdown.value = None  # 强制触发 dirty
            try:
                per_page = I18n.get("unit_per_page")
                self.size_dropdown.options = [
                    ft.dropdown.Option(k, text=f"{k} {per_page}")
                    for k in ("10", "20", "50")
                ]
            except Exception as rebuild_err:
                logger.debug(f"[MyView] size_dropdown options rebuild skipped: {rebuild_err}")
            self.size_dropdown.value = saved_value  # 无论 options 重建是否成功都恢复 value

            # 规范 6：级联子组件（若有）
            # child_refresh = getattr(self.child_panel, "refresh_locale", None)
            # if callable(child_refresh):
            #     child_refresh()

            # 规范 9：判空更新
            if self.page:
                self.update()
        except Exception as e:
            logger.warning(f"[MyView] refresh_locale failed: {e}")
```

### 重建子 panel 保留用户输入（规范 6 实战要点）

```python
def _rebuild_steps_after_locale_change(self):
    """语言切换后重建子面板，保留用户输入状态"""
    # 1. 取消旧面板的 I18n 订阅
    for panel_attr in ("database_panel", "tushare_panel"):
        old_panel = getattr(self, panel_attr, None)
        if old_panel and hasattr(old_panel, "will_unmount"):
            try:
                old_panel.will_unmount()
            except Exception as e:
                logger.debug("Panel cleanup failed: %s", e)

    # 2. 重建前提取旧面板的用户输入
    saved_enabled = getattr(self.schedule_enabled, "value", True) if hasattr(self, "schedule_enabled") else True
    saved_time = getattr(self.schedule_time, "value", None) if hasattr(self, "schedule_time") else None

    # 3. 创建新面板
    self._init_schedule_controls()

    # 4. 恢复用户输入
    if hasattr(self, "schedule_enabled"):
        self.schedule_enabled.value = saved_enabled
    if hasattr(self, "schedule_time") and saved_time is not None:
        self.schedule_time.value = saved_time
```

### 测试要求（语言切换场景必须在用例中覆盖）

- 订阅/取消订阅配对测试（`did_mount` 订阅 + `will_unmount` 取消）
- `refresh_locale` 异常降级测试（mock I18n.get 抛异常，断言不抛出 + `logger.warning` 被调用）
- Dropdown options 重建后 `value` 保留断言
- 重建子 panel 后用户输入状态保留断言
- 涉及 MetaDataManager 的视图必须断言 `invalidate_cache()` 被调用

## 标准开发工作流 (How-To)

### 1. 新增一张数据表

1. 在 `data/persistence/models.py` 中添加 SQLAlchemy ORM 模型 (继承 `Base`)。
2. 在 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 中注册：表名 → 同步配置、质量监控配置、依赖关系。
3. 运行 `python -m alembic revision --autogenerate -m "add xxx table"`，**人工检查** 生成的迁移文件。
4. 运行 `python -m alembic upgrade head` 验证。
5. 若需要 DAO 访问，参考[新增一个 DAO](#2-新增一个-dao)。

### 2. 新增一个 DAO

1. 在 `data/persistence/daos/` 下创建 `xxx_dao.py`，继承 `BaseDao`。
2. 实现读写方法，**只用** `_read_db_select` / `_save_upsert` / `chunked_in_query`，禁止裸 SQL 字符串拼接。
3. 在 `data/cache/cache_manager.py` 的 `CacheManager.__init__` 中实例化：`self.xxx_dao = XxxDao(self.engine)`。
4. 在 `CacheManager._create_engine` 中更新 `.engine` 引用：`self.xxx_dao.engine = self.engine`。
5. 在 `tests/unit/` 下编写对应单测，使用 mock engine 隔离 DB。

### 3. 新增一个策略

1. 在 `strategies/` 下创建 `xxx_strategy.py`。
2. 使用 `@register_strategy("key")` 装饰器注册；继承 `BaseStrategy` (普通) 或 `PolarsBaseStrategy` (向量化)。
3. 声明 `required_context_keys` / `required_tables` / `required_history_days`。
4. 若需访问 LLM，使用 `AIStrategyMixin` 混入；Prompt 添加到 `strategies/strategy_prompts.py`。继承 `PolarsBaseStrategy` 时已自带 AI 阶段（可通过 `enable_ai_analysis = False` 关闭）。
5. 在 `strategies/all_strategies.py` 的 `_import_all_strategies()` 中导入该模块以触发自动注册。
6. 在 `locales/` 添加 `strategy_xxx` / `strategy_xxx_desc` 等 i18n key。
7. 在 `tests/unit/` 下编写单测。

### 4. 新增一个 UI 视图

1. 在 `ui/views/` 下创建 `xxx_view.py`，View 只构建控件树。
2. 在 `ui/viewmodels/` 下创建对应 ViewModel，持有业务状态、调用 services/data 层。
3. 在 `ui/app_layout.py` 中注册新标签页 (如需)。
4. UI 事件回调使用 `@log_ui_action` 装饰器埋点。
5. 异步耗时操作必须通过 `ThreadPoolManager.run_async()` 或 `TaskManager.submit_task()` 提交。
6. 若视图展示 i18n 文案，必须遵守 [语言切换响应 (I18n Hot Reload)](#语言切换响应-i18n-hot-reload)。

### 5. 新增一个外部数据源

1. 在 `data/external/` 下创建客户端模块，封装第三方 SDK 或 HTTP API。
2. 使用 `utils/rate_limiter.py` 提供的限流器避免触发对方风控。
3. 网络错误必须用 `classify_error(e, context="general")` 分类，自动处理重试。
4. 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)`。
5. 若需走代理，使用 `utils/proxy_manager.py`。

### 6. 新增依赖

1. 编辑 `pyproject.toml`：
   - 运行时依赖加到 `[project] dependencies`
   - 开发依赖加到 `[project.optional-dependencies] dev`
   - 可选依赖加到 `[project.optional-dependencies] optional`
2. `git commit` 时 pre-commit 会自动运行 `uv pip compile --universal` 重新生成对应的 `requirements*.txt`。
3. 本地安装新依赖: `uv pip install --system -r requirements.txt -r requirements-dev.txt`；如需可选功能，再安装 `requirements-optional.txt`。

### 7. 新增回测配置

1. 在 `strategies/backtest/config.py` 中定义回测参数 (`BacktestConfig`)。
2. 在 `strategies/backtest/adapter.py` 中适配待回测的策略。
3. 通过 `services/backtest_service.py` 的 `run_backtest()` 启动。
4. 结果通过 `BacktestDAO` 持久化，由 `ui/views/backtest_view.py` 展示。

### 8. 新增一个单例

1. 使用 `@register_singleton` 装饰器注册类（代码模板见[单例模式实现模板](#单例模式实现模板)）。
2. 实现 `_reset_singleton()` 类方法 (测试隔离必须)。
3. 实例创建必须受 `threading.Lock` 保护 (优先在 `__new__` 中持锁)。
4. 支持 `_initialized` 标志防止重复初始化。
5. 如需进程退出清理，实现 `_atexit_cleanup()` 类方法。
6. 在 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式) 的单例列表中补充新单例名称。
7. 在 `tests/unit/` 下编写单测；常规隔离由 `_reset_all_singletons` autouse fixture 自动处理，需精细控制单例初始化状态时使用 `singleton_state` 上下文管理器。

## 排查典型问题

| 现象 | 可能原因 | 排查点 |
|------|---------|--------|
| 测试间状态污染 | 单例未注册到 `singleton_registry` | 检查 `@register_singleton`；需精细控制时用 `singleton_state` 包裹并检查 `extra_attrs` |
| `RuntimeError: no running event loop` | 跨循环使用同步原语 | 改用 `get_loop_local` (对应 [CLAUDE.md R11](./CLAUDE.md#31--绝对禁止)) |
| `EngineDisposedError` | 关机期间继续访问 DB | 在调用方捕获并降级，或检查 `_disposed` 早退 |
| 慢查询告警 | SQL 缺索引 / 数据量过大 / N+1 | 看 `[ClassName] Slow Read/Write` 日志，结合 `EXPLAIN` |
| Pyright 报错但运行时正常 | Optional 未判空 / 泛型推断失败 | 用 `assert x is not None` 收窄类型，或显式标注 |
| Ruff `UP*` 报错 | 使用了过时语法 | 跑 `ruff check . --fix` 自动升级 |
| Tushare 限流 | 短时调用过多 | 看 `utils/rate_limiter.py` 配置；考虑加缓存 |
| 优雅停机卡住/超时 | `CancelledError` 被吞没 | 搜索 `except asyncio.CancelledError` 后无 `raise`；参见 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止) |

## 已知架构技术债 (Known Technical Debt)

项目开发演进过程中产生了一些需要明确跟踪的技术债与设计限制，请在排查深层问题时参考：

| 级别 | 问题描述 | 产生背景与现状 | 期望的最终解法 |
|------|---------|---------------|--------------|
| **P1-2** | **Windows 测试事件循环泄露** | Windows 使用 `WindowsSelectorEventLoopPolicy` 时测试 loop scope 妥协为 `session` 级，导致 `asyncio.Event/Lock` 跨测试泄漏。当前依赖 `reset_loop_local_cache` fixture 维持隔离。 | 中期应将 Windows 测试作用域降级回 `function` 彻底修复，降级后删除该隔离 fixture (见探测用例 `test_infra_loop_isolation.py`)。 |

---

## 获取帮助

- **GitHub Issues**: 提问或报告问题
- **Email**: louis2sin@gmail.com

---

再次感谢你的贡献！
