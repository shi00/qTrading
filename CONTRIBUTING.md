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
  - [响应式布局规范 (Responsive Layout)](#响应式布局规范-responsive-layout)
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
> - **覆盖率源**：`core`, `app`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）
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

   **⚠️ 必须使用 `refresh_dropdown_options()` 工具函数（Flet 已知坑）**：`Control._set_attr_internal` 对相等值短路（源码 `flet/core/control.py:189` 的 `orig_val[0] != value` 判断），不标记 dirty。在批量 `page.update()` 中，`value` 从 X→None→X 的最终值等于原值，前端只收到最终值，`DropdownButton` 不触发 rebuild，闭合态选中项显示文本不刷新。

   必须使用 `ui.i18n.refresh_dropdown_options(dropdown, new_options)` 工具函数，它通过分两步 `control.update()` 解决：

   ```python
   from ui.i18n import refresh_dropdown_options

   # 正确写法：使用工具函数
   refresh_dropdown_options(self.theme_dropdown, [
       ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),
       ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),
   ])
   ```

   原理：工具函数内部先提交 `value=None` + 新 `options`，通过 `control.update()` 立即发送到前端（清除选中项显示）；再提交 `value=saved`，再次 `control.update()` 使前端用新 options 的 text 更新显示。`control.update()` 未挂载时抛 `AssertionError`，被工具函数 catch 后属性仍标记 dirty，后续 `page.update()` 兜底。

   **边界情况**：当 `saved_value` 为 `None`（用户未选中）时，闭合态本就无文本需刷新，但套用本工具函数也无副作用；当 `saved_value` 为空字符串 `""` 时本方案失效（两次赋值均被短路），实际项目 option key 应非空，若出现需单独处理。

   **禁止的手动写法**（以下写法在批量 `page.update()` 中无效）：

   - `dropdown.value = dropdown.value`（自赋值）
   - `saved = dropdown.value; dropdown.value = None; ...; dropdown.value = saved`（saved_value 模式，不调用 `control.update()` 时无效）

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
- ❌ 手动 `value = None; options = [...]; value = saved` 不调用 `control.update()`（批量 `page.update()` 只发送最终值，闭合态选中项文本不刷新）
- ❌ 不使用 `refresh_dropdown_options()` 工具函数重建含 i18n 文案的 Dropdown options
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

from ui.i18n import I18n, refresh_dropdown_options

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
            # 规范 4：使用 refresh_dropdown_options 重建 options（分步 control.update() 强制刷新）
            try:
                per_page = I18n.get("unit_per_page")
                refresh_dropdown_options(
                    self.size_dropdown,
                    [ft.dropdown.Option(k, text=f"{k} {per_page}") for k in ("10", "20", "50")],
                )
            except Exception as rebuild_err:
                logger.debug(f"[MyView] size_dropdown options rebuild skipped: {rebuild_err}")

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

## 响应式布局规范 (Responsive Layout)

> 对应 [CLAUDE.md §5.9](./CLAUDE.md#59-响应式布局规范-responsive-layout)。

本规范确保应用在 960px (最小窗口) 到 4K (3840px) 的各种分辨率下均能提供良好体验。新增/修改 UI 视图或组件时必须遵守以下 7 条规范。

### 背景与约束

- 项目仅桌面端 (Flet 0.85.3)，`main.py` 设置 `page.window.min_width = 1280`、`min_height = 640`、默认 `width = 1280`。
- 内容区净宽 = 窗口宽度 − nav_rail (展开 180 / 折叠 80) − divider (1) − body padding (40)。
- `AppLayout` 已实现 `page.on_resize` 的 100ms 防抖分发：通过鸭子类型调用 `current_view.handle_resize(width, height)` (见 `ui/app_layout.py` 的 `_handle_resize`)。
- **Flet 0.85.3 API 关键约束**：
  - 正确的事件属性名为 `page.on_resize`（不带 d），**不是** `on_resized`。
  - `WindowResizeEvent` 携带实时 `width` / `height` 属性。
  - `page.width` / `page.window.width` 仅在页面连接时 (`fetch_page_details_async`) 更新一次，**resize 事件中不会刷新**，返回过时值或 0。因此 `handle_resize` 必须通过参数接收实时尺寸，**禁止**在 `handle_resize` 内读取 `self.page.width`。
- Web 模式 (`_is_web_mode()`) 下跳过窗口约束，但 resize 分发机制仍然生效。

### 断点分级表

基于 `handle_resize` 接收的 `width` 参数 (来自 `WindowResizeEvent.width`) 的 4 级断点，新增到 `AppStyles` (见 `ui/theme.py`)：

| 断点常量 | 阈值 (px) | 典型场景 | 内容区净宽 (nav 展开) |
|---------|-----------|---------|---------------------|
| `BREAKPOINT_COMPACT` | `< 1200` | 最小窗口 960，笔记本竖屏 | 739 ~ 979 |
| `BREAKPOINT_STANDARD` | `1200 ~ 1599` | 默认窗口 1280，1080p | 979 ~ 1379 |
| `BREAKPOINT_WIDE` | `1600 ~ 2399` | 2K 显示器 | 1379 ~ 2179 |
| `BREAKPOINT_ULTRA_WIDE` | `≥ 2400` | 4K / 带鱼屏 | ≥ 2179 |

> **注意**：Flet 内置 `ResponsiveRow` 断点 (xs<576, sm≥576, md≥768, lg≥992, xl≥1200) 在 `min_width=960` 约束下，xs/sm 基本触发不到，实际有效的是 md/lg/xl。本项目断点常量用于 `handle_resize()` 中的条件判断，与 `ResponsiveRow` 的 col 配置互补。
>
> **设计选择**：断点基于 `WindowResizeEvent.width`（窗口总宽度）而非内容区净宽，这是有意的设计。同一窗口宽度下 nav 折叠/展开会改变内容区净宽，但断点保持稳定，避免 nav 切换导致侧栏宽度跳变。nav 折叠带来的额外空间由 `expand=True` 的主内容区自然吸收。

### 10 条规范

#### 规范 1：断点分级 — 视图必须感知当前窗口尺寸

新增视图时，在 `handle_resize(width, height)` 中通过 `width` 参数 (来自 `WindowResizeEvent.width`) 读取实时窗口宽度，对照断点表调整布局。**禁止**读取 `self.page.width` (仅连接时更新，resize 时返回过时值)。

```python
# ui/theme.py — AppStyles 类中新增
BREAKPOINT_COMPACT = 1200      # < 此值视为紧凑模式
BREAKPOINT_STANDARD = 1600     # < 此值视为标准模式
BREAKPOINT_ULTRA_WIDE = 2400   # ≥ 此值视为超宽屏

@staticmethod
def get_breakpoint(page_width: int | None) -> str:
    """返回当前断点级别: 'compact' | 'standard' | 'wide' | 'ultra_wide'。"""
    if page_width is None or page_width < AppStyles.BREAKPOINT_COMPACT:
        return "compact"
    if page_width < AppStyles.BREAKPOINT_STANDARD:
        return "standard"
    if page_width < AppStyles.BREAKPOINT_ULTRA_WIDE:
        return "wide"
    return "ultra_wide"
```

#### 规范 2：侧栏动态宽度 — 禁止固定像素宽度

包含侧栏 (sidebar) 的视图 (如 `BacktestView`、`ScreenerView`) 必须根据断点动态计算侧栏宽度，禁止硬编码固定值。

| 断点 | 侧栏宽度 | 理由 |
|------|---------|------|
| compact (<1200) | 280px | 内容区仅 739px，侧栏 280px 后主区保留 449px |
| standard (1200~1599) | 340px | 内容区 ~1059px，主区保留 707px |
| wide/ultra_wide (≥1600) | 380px | 内容区充足，侧栏可放宽 |

```python
# ui/theme.py — AppStyles 类中新增
SIDEBAR_WIDTH_COMPACT = 280
SIDEBAR_WIDTH_STANDARD = 340
SIDEBAR_WIDTH_WIDE = 380

@staticmethod
def get_sidebar_width(page_width: int | None) -> int:
    """根据窗口宽度返回合适的侧栏宽度。"""
    breakpoint = AppStyles.get_breakpoint(page_width)
    if breakpoint == "compact":
        return AppStyles.SIDEBAR_WIDTH_COMPACT
    if breakpoint == "standard":
        return AppStyles.SIDEBAR_WIDTH_STANDARD
    return AppStyles.SIDEBAR_WIDTH_WIDE
```

#### 规范 3：handle_resize 实现 — 所有视图必须实现

`AppLayout` 已通过鸭子类型调用 `current_view.handle_resize()`。**所有视图必须实现此方法**，即使为空方法也必须存在并注释 `# No responsive adjustment needed`，以表明已评估响应式需求。这与 I18n 规范"所有展示文案的视图必须订阅"对称。

以下布局类型的视图必须在 `handle_resize` 中实现实际逻辑：

- 左右分栏 (如 config_panel + result_panel) → 动态调整侧栏宽度
- 侧栏显隐切换 (如 history_tree) → 刷新主内容区
- 表格视口刷新 (如虚拟表格) → 重新计算可见行数
- 图表/数据密集区域 → 根据高度调整最小高度或可见行数
- 任何依赖窗口尺寸的动态布局

**标准模板**：

```python
def handle_resize(self, width: float = 0, height: float = 0) -> None:
    """窗口尺寸变化时调整布局。由 AppLayout 防抖后调用 (约 100ms)。

    Args:
        width: 当前窗口宽度 (来自 WindowResizeEvent.width)，0 表示未知
            (如 nav 折叠触发的内部 resize，此时视图应保留当前布局)
        height: 当前窗口高度 (来自 WindowResizeEvent.height)，0 表示未知
    """
    if not width:
        # width=0 表示是 nav 折叠等内部触发，无新尺寸；保留当前布局即可
        return
    try:
        new_sidebar = AppStyles.get_sidebar_width(width)
        # 仅在宽度实际变化时更新，避免无意义 refresh
        sidebar_container = self._get_sidebar_container()  # 视图自行实现获取逻辑
        if sidebar_container.width != new_sidebar:
            sidebar_container.width = new_sidebar
            sidebar_container.update()  # 局部更新，不用 self.update()
    except Exception as e:
        logger.debug("[%s] handle_resize skipped: %s", self.__class__.__name__, e)
```

**性能约束**（必须遵守）：

- **禁止重建 content**：`handle_resize` 内禁止 `self.content = self._build_content()`，只能修改已有控件属性 (如 `.width`、`.visible`)。重建 content 会销毁所有子控件状态 (滚动位置、输入值、选中态)。
- **禁止 IO/CPU 密集操作**：`handle_resize` 在 resize 防抖后同步调用，阻塞 UI 线程。禁止查询数据库、加载文件、执行策略计算。
- **局部更新优先**：仅调用发生变化的子控件的 `.update()`，避免 `self.update()` 触发整树 diff。复杂视图 (如 BacktestView 含图表+表格) 尤其重要。
- **幂等性**：相同 `page.width` 多次调用必须产生相同结果，`handle_resize` 不得有副作用 (如计数器自增、状态修改)。

**已实现**：`ScreenerView.handle_resize` (刷新表格视口)。
**待补实现**：`BacktestView`、`DataExplorerView`、`HomeView` (空方法)、`TaskCenterView` (空方法)、`SettingsView` (空方法)。

#### 规范 4：控件宽度策略 — expand 优先，禁止裸硬编码

| 场景 | 允许 | 禁止 |
|------|------|------|
| 容器内自适应控件 | `expand=True` | `width=200` 等裸数字 |
| 侧栏内表单控件 | `AppStyles.COL_HALF` / `COL_FULL` 配合 `ResponsiveRow` | `width=AppStyles.CONTROL_WIDTH_MD` |
| Dialog / 弹窗内控件 | `AppStyles.CONTROL_WIDTH_LG` 等常量 | 裸数字 (但 Dialog 场景尺寸相对固定，可放宽) |
| 图标/进度条等装饰性元素 | 固定小尺寸 (如 `width=20`) | — |

**例外**：`AppStyles.CONTROL_WIDTH_*` 常量可用于**顶层全宽 Row** 中的独立控件——即该 Row 不嵌套在任何固定/动态宽度容器 (侧栏、卡片面板) 内，宽度由 body 内容区直接决定。例如 `BacktestView` 的 `strategy_dropdown` 位于左右分栏之上的全宽行，`width=CONTROL_WIDTH_LG` 合理。嵌套在侧栏内的 Row 不属于顶层全宽 Row。

#### 规范 5：ResponsiveRow 强制配置 col — 禁止无 col 的 ResponsiveRow

`ft.ResponsiveRow` 必须为每个子 `Column`/`Container` 指定 `col` 参数，使用 `AppStyles.COL_*` 常量。

```python
# ✅ 正确
ft.ResponsiveRow(
    [ft.Column([control], col=AppStyles.COL_HALF)],
)

# ❌ 错误 — 退化为纵向 Column，浪费横向空间
ft.ResponsiveRow([ft.Column([control])])
```

**侧栏 (固定/动态宽度容器) 内**：只能使用 `COL_HALF` 或 `COL_FULL`，禁止 `COL_THIRD`/`COL_QUARTER` (侧栏宽度不足以支撑 3+ 列)。

#### 规范 6：scroll 兜底 — 作为最后防线而非主要手段

- 工具栏等横向密集区域应在 `ft.Row` 上设置 `scroll=ft.ScrollMode.AUTO` 作为兜底。
- 但 **scroll 不得作为掩盖布局缺陷的手段**：若控件累计宽度经常超过容器宽度，应优先改用 `ResponsiveRow` 或 `wrap=True`。
- `scroll=ft.ScrollMode.AUTO` 的 `ft.Column` 必须设置 `padding=ft.padding.only(right=8)` 避免内容与滚动条重叠。

#### 规范 7：max_width 约束 — 防止超宽屏内容过度拉伸

在 `ui/app_layout.py` 的 `body` Container 上设置最大宽度，防止 4K 屏内容全宽铺开导致阅读困难。

**实现方式**：在 `AppLayout._handle_resize()` 中统一处理 (而非每个视图各自处理)。注意 Flet 的 `Container.alignment` 控制的是**内容在 Container 内的对齐**，不是 Container 自身在父容器中的对齐。因此 `body` 设置 `width` 后需要用外层 Row 的 `alignment` 来实现居中：

```python
# ui/app_layout.py — 常量
MAX_CONTENT_WIDTH = 2200  # 4K 屏内容区最大宽度

# ui/app_layout.py — _init_ui 中，main_layout Row 包裹 body
# main_layout = ft.Row([nav_rail, divider, body_wrapper], expand=True)
# body_wrapper = ft.Row([body], alignment=ft.MainAxisAlignment.CENTER)  # 居中容器

async def _handle_resize(self):
    # ... 现有防抖逻辑 ...
    if self.page:
        nav_width = 80 if self._nav_collapsed else 180
        available = self.page.width - nav_width - 1  # 减去 divider
        body_width = min(available, MAX_CONTENT_WIDTH)
        if self.body.width != body_width:
            self.body.width = body_width
            self.body.update()
    # ... 现有 handle_resize 分发 ...
```

> **关键**：`body` 的父容器 (main_layout Row) 需通过 `alignment=ft.MainAxisAlignment.CENTER` 实现居中。若 `body.width < available`，Row 会将 body 居中显示；若 `body.width == available`，Row 铺满。

#### 规范 8：触发时机完整性 — 任何改变内容区宽度的操作都必须分发 resize

`AppLayout._handle_resize()` 是 resize 事件的唯一分发入口。**任何改变内容区可用宽度的操作**，不仅是窗口拖拽，都必须触发 `schedule_resize()` 分发，否则视图会基于过时的 `page.width` 渲染。

当前状态：

| 操作 | 状态 | 说明 |
|------|------|------|
| 窗口拖拽 | ✅ 已修复 | `page.on_resize` → `schedule_resize(width, height)` → 100ms 防抖 → `handle_resize(width, height)` |
| **nav_rail 折叠/展开** (`_toggle_nav`) | ✅ 已修复 | `_toggle_nav` 末尾调用 `schedule_resize()` (复用缓存尺寸) |
| **tab 切换挂载新视图** | ✅ 已修复 | `_execute_tab_switch` 中 `refresh_locale` 后追加 `handle_resize` 兜底 |
| **i18n 语言切换** | ✅ 已修复 | `_on_locale_change` 末尾调用 `schedule_resize()` 重新验证布局 |
| 侧栏显隐切换 (如 `ScreenerView` history_tree) | 视图内部处理 | 视图内部处理可接受 |

**实现方式** (已完成)：

```python
# main.py — on_resize 回调，传递实时尺寸
async def _on_resize(e):
    # ...
    width = getattr(e, "width", 0) or 0
    height = getattr(e, "height", 0) or 0
    layout.schedule_resize(width, height)

page.on_resize = _on_resize  # 注意：是 on_resize (不带 d)

# ui/app_layout.py — _toggle_nav 末尾
def _toggle_nav(self, e):
    # ... 现有折叠/展开逻辑 ...
    self.schedule_resize()  # 复用缓存尺寸，不传新参数

# ui/app_layout.py — _execute_tab_switch 中，refresh_locale 之后
if hasattr(new_view, "handle_resize"):
    new_view.handle_resize(self._current_width, self._current_height)

# ui/app_layout.py — _on_locale_change 末尾
self.schedule_resize()  # 语言切换后重新验证布局
```

#### 规范 9：高度维度 — 对高度敏感的视图必须响应 `page.height`

规范 1-8 仅关注宽度，但 `min_height=640` 下多个视图存在高度维度问题：

- `BacktestView` 配置面板内容溢出 (scroll 兜底，但用户需大量滚动)
- `DataExplorerView` 表格每页行数固定，低高度下只能看到 3-4 行
- 图表区在低高度下被压缩到不可读

**要求**：对高度敏感的视图 (含图表、表格、长表单) 必须在 `handle_resize` 中同时处理 `height` 参数：

```python
def handle_resize(self, width: float = 0, height: float = 0) -> None:
    if not width:
        return
    try:
        # 宽度维度
        new_sidebar = AppStyles.get_sidebar_width(width)
        if self._sidebar_container.width != new_sidebar:
            self._sidebar_container.width = new_sidebar
            self._sidebar_container.update()
        # 高度维度 (对高度敏感的视图)
        if height and height < 720:  # 紧凑高度
            self._table_page_size = 10  # 减少可见行数
        elif height:
            self._table_page_size = 20
    except Exception as e:
        logger.debug("[%s] handle_resize skipped: %s", self.__class__.__name__, e)
```

**判定标准**：视图包含以下元素之一即为"高度敏感"：
- `ft.DataTable` / 虚拟表格 (行数受高度影响)
- 图表组件 (最小可读高度)
- 超过 5 个表单项的长表单 (低高度下需折叠或分页)

#### 规范 10：i18n 与响应式交互 — 语言切换后必须重新验证布局

语言切换后文案长度变化 (中文"调仓频率"4 字 vs 英文"Rebalance Frequency"18 字符)，可能导致之前不溢出的布局突然溢出。

**要求**：`refresh_locale()` 完成 content 重建后，若视图实现了 `handle_resize`，必须一并调用以重新验证布局。

**实现方式** (已完成)：在 `AppLayout` 的 i18n 订阅回调中，`refresh_locale` 之后追加 `schedule_resize`：

```python
# ui/app_layout.py — _on_locale_change 末尾
def _on_locale_change(self):
    # ... 现有 refresh_locale 逻辑 ...
    # 语言切换后文案长度变化可能导致布局溢出，触发 resize 重新验证布局
    self.schedule_resize()  # 复用缓存尺寸
```

> **注意**：`refresh_locale` 重建 content 后控件树变化，`handle_resize` 中对控件属性的引用必须基于新 content。若 `handle_resize` 通过索引访问控件 (如 `self.content.controls[4].controls[0]`)，需确保索引在重建后仍然有效。推荐在视图上保存控件引用 (如 `self._sidebar_container`) 而非依赖索引。

### 判定决策树

按顺序自问，命中则需遵守对应子规范：

```
我的视图/组件包含布局控件吗？
├─ 有左右分栏 (sidebar + main) 吗？
│   └─ 是 → 规范 2 (侧栏动态宽度) + 规范 3 (handle_resize)
├─ 有 ft.ResponsiveRow 吗？
│   └─ 是 → 规范 5 (强制配置 col)
├─ ResponsiveRow 在侧栏/固定宽度容器内吗？
│   └─ 是 → 只用 COL_HALF / COL_FULL (规范 5)
├─ 有硬编码 width=数字 的控件吗？
│   └─ 是 → 改为 expand=True 或 AppStyles 常量 (规范 4)
├─ 有 scroll=ft.ScrollMode.AUTO 的 Column 吗？
│   └─ 是 → 设置 padding=ft.padding.only(right=8) (规范 6)
├─ 有改变内容区宽度的操作 (nav 折叠、tab 切换) 吗？
│   └─ 是 → 规范 8 (触发时机完整性)
├─ 含表格/图表/长表单等高度敏感元素吗？
│   └─ 是 → 规范 9 (高度维度)
├─ 视图展示 i18n 文案吗？
│   └─ 是 → 规范 10 (i18n 与响应式交互)
└─ 视图在 960×640 最小窗口下验证过吗？
    └─ 是 → 确认无溢出、无截断、无重叠 (规范 1 断点验证)
```

### 标准 View 检查清单

新增/修改视图时，对照此清单逐项确认：

- [ ] 视图在 960×640 最小窗口下无横向溢出、无纵向截断 (内容区净宽 ~739px)
- [ ] 视图在 1280×800 默认窗口下布局合理 (内容区净宽 ~1059px)
- [ ] 视图在 1920×1080 宽屏下不出现内容过度拉伸
- [ ] 视图已实现 `handle_resize()` (含分栏布局的须有实际逻辑，纯纵向的须有空方法)
- [ ] `handle_resize` 遵守性能约束 (无重建 content、无 IO、局部更新、幂等)
- [ ] 侧栏宽度通过 `AppStyles.get_sidebar_width()` 动态计算，非硬编码
- [ ] 所有 `ResponsiveRow` 子元素已配置 `col` 参数 (使用 `AppStyles.COL_*` 常量)
- [ ] 侧栏内的 `ResponsiveRow` 只使用 `COL_HALF` 或 `COL_FULL`
- [ ] 无裸 `width=数字` 硬编码 (Dialog 场景除外)
- [ ] `scroll=ft.ScrollMode.AUTO` 的 Column 已设置右侧 padding 避让滚动条
- [ ] 控件在 `refresh_locale()` 重建 content 后，Container 层级样式 (bgcolor/border 等) 不丢失
- [ ] `handle_resize` 中控件引用基于实例属性 (如 `self._sidebar_container`)，不依赖 content 索引

### 现有视图合规状态 (截至 2026-06-29)

| 视图 | 合规状态 | 待修复项 |
|------|---------|---------|
| `HomeView` | ⚠️ 部分 | 未实现 `handle_resize` (需补空方法) |
| `ScreenerView` | ⚠️ 部分 | 参数面板 3×`width=200` 无 wrap；已实现 `handle_resize` |
| `BacktestView` | ❌ 不合规 | `expand=1/2` 比例分栏；Slider `width=200` 硬编码；未实现 `handle_resize` |
| `DataExplorerView` | ⚠️ 部分 | 工具栏累计 ~840px 硬编码 (有 scroll 兜底)；新闻 cell `width=400` 反模式；未实现 `handle_resize`；高度敏感未处理 |
| `TaskCenterView` | ⚠️ 部分 | 双重 padding (body 20px + view 20px)；未实现 `handle_resize` (需补空方法) |
| `SettingsView` | ⚠️ 部分 | 未实现 `handle_resize` (需补空方法) |
| `MarketDashboard` | ❌ 不合规 | `ResponsiveRow` 无 col 配置，4 张卡退化为纵向堆叠 |
| `OnboardingWizard` | ❌ 不合规 | `ResponsiveRow` 无 col 配置，6 张卡纵向堆叠 |
| `AppLayout` | ✅ 已修复 | `on_resize` 事件注册正确；`schedule_resize` 缓存并传递实时尺寸；`_toggle_nav` 触发 resize；tab 切换有 `handle_resize` 兜底；i18n 回调触发 resize；(max_width 约束待实现) |

### 测试要求

新增/修改视图的响应式布局时，必须编写以下测试：

1. **断点函数单元测试**：`AppStyles.get_breakpoint()` 和 `get_sidebar_width()` 的边界值覆盖 (959/960/1199/1200/1599/1600/2399/2400/None)。
2. **handle_resize 单元测试**：调用 `handle_resize(width, height)` 传入各断点值 (959/960/1199/1200/1599/1600/2399/2400)，断言侧栏 Container 的 `width` 属性变化正确；断言相同参数多次调用结果不变 (幂等性)；断言 `width=0` 时提前返回不修改布局。
3. **handle_resize 性能约束测试**：mock 后断言 `handle_resize` 内未调用 `self.content = ...` (重建 content)、未调用数据库/文件 IO 方法。
4. **handle_resize 异常降级测试**：mock 控件引用为 None 或抛异常，断言 `handle_resize` 不抛出 (降级为 `logger.debug`)。
5. **空方法验证**：纯纵向视图 (HomeView 等) 必须断言 `hasattr(view, "handle_resize")` 为 True，即使为空方法。
6. **960×640 最小窗口布局验证** (手工或 E2E)：确认无横向溢出、无控件截断、无滚动条与控件重叠。

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
7. 若视图含分栏布局或依赖窗口尺寸，必须实现 `handle_resize()` 并遵守 [响应式布局规范 (Responsive Layout)](#响应式布局规范-responsive-layout)。对照标准 View 检查清单逐项确认。

### 5. 新增一个外部数据源

1. 在 `data/external/` 下创建客户端模块，封装第三方 SDK 或 HTTP API。
2. 使用 `utils/rate_limiter.py` 提供的限流器避免触发对方风控。
3. 网络错误必须用 `classify_error(e, context="general")` 分类，自动处理重试。
4. 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)`。
5. 若需走代理，使用 `utils/proxy_manager.py`。

### 6. 新增与升级依赖

1. **编辑依赖配置**：
   - 编辑 `pyproject.toml`：
     - 运行时依赖加到 `[project] dependencies`
     - 开发依赖加到 `[project.optional-dependencies] dev`
     - 可选依赖加到 `[project.optional-dependencies] optional`
   - 若要升级已有依赖，可运行 `uv lock --upgrade` 更新锁文件。
2. **生成与编译 `requirements*.txt`**：
   - **自动化生成**：在 `git commit` 时，本地 pre-commit 钩子会自动运行 `uv pip compile` 重新编译所有的 `requirements*.txt`。
   - **手动即时生成（用于本地即时升级调试）**：若在 commit 前需要使升级或新依赖立即在本地生效，请手动编译：
     ```bash
     uv pip compile --universal --no-emit-index-url pyproject.toml -o requirements.txt
     uv pip compile --universal --no-emit-index-url --extra dev pyproject.toml -o requirements-dev.txt
     uv pip compile --universal --no-emit-index-url --extra optional pyproject.toml -o requirements-optional.txt
     ```
3. **本地安装新依赖**：运行以下命令将编译后的依赖同步到本地环境：
   ```bash
   uv pip install --system -r requirements.txt -r requirements-dev.txt
   # 如需可选功能：
   uv pip install --system -r requirements-optional.txt
   ```

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
