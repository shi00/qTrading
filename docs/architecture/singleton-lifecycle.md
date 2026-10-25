# 单例模式实现模板

> 来源：从 CONTRIBUTING.md 迁移
> canonical_for: 单例注册清单与生命周期（P3-02 链接语义正本）

> 对应 [CLAUDE.md §4.3](../../CLAUDE.md#43-单例模式)。

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
        # CON-01: double-checked locking。__new__ 持锁创建实例，但 __init__ 在锁外执行会
        # 导致并发首次访问时两线程都见 _initialized=False 而重复初始化（前一个初始化被覆盖泄漏）。
        # 初始化整体移入锁内并二次检查，保证恰好执行一次。
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            # ... 初始化逻辑 ...
            self._initialized = True

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production.

        生产类经装饰器强制实现本方法，已注册生产单例不会走 ``close()`` 兜底
        （``tests.`` 模块豁免类除外）；持有线程池/连接/文件句柄等外部资源时必须在此先行释放。
        """
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    @classmethod
    def _atexit_cleanup(cls):
        """Optional: 异常退出兜底；由 singleton_registry 的单一 atexit handler
        按注册逆序调用，优雅停机已完成时整体短路。"""
        if cls._instance is not None:
            # 释放外部资源 (线程池、连接、文件句柄等)
            ...
```

**设计准则：依赖注入优先**

新增单例须支持依赖注入/可注入时钟：在模板的 `__init__` 签名上扩展可选的 config/clock 注入参数，默认走生产实现（ConfigHandler/time.monotonic），测试可传 fake。这样无需替换 sys.modules 或全局 patch。

```python
def __init__(self, *, config=None, clock=None):
    self._config = config  # None → 走 ConfigHandler
    self._clock = clock or time.monotonic  # None → 走 time.monotonic
```

### 生命周期阶段与停机顺序

1. **注册**：模块导入时 `@register_singleton` 将类追加到 `utils/singleton_registry.py::_registry`（重复注册幂等）。类必须实现 `_reset_singleton`，否则装饰器在导入期抛 `TypeError`（`tests.` 模块豁免）。
2. **初始化**：首次实例化按上文模板的双检锁执行，`_initialized` 保证初始化逻辑只执行一次（`TushareClient` 例外：重复实例化且注入不同 token 时会重跑 token 切换）。
3. **优雅停机（主路径）**：`ShutdownCoordinator.do_cleanup()` 按 `utils/shutdown.py::_CLEANUP_STEPS` 的 Step 0 → Step 8（含 Step 5b）释放资源：取消在途任务 → 停后台服务 → 刷盘 → 关 `DataProcessor`（含异步引擎）→ 清 Toast → 卸载 AI 模型 / 情绪子进程 → 关线程池 → 关共享同步引擎 → 停内置 PostgreSQL。持有资源的单例须接入对应步骤（复用既有静态步骤，或经 `register_cleanup_callback()` 新增动态步骤——后者在 Step 5b 后、Step 6 前按注册顺序执行；`_CLEANUP_STEPS` 有索引断言，勿随意插行）；停机步骤只读既有实例（`Cls._instance`，或 `get_instance()` 这类不构造新实例的访问器），禁止调用会创建资源的工厂/构造器，避免关机时重建资源。
4. **成功标记**：全部关键步骤成功后才调用 `mark_graceful_shutdown_completed()`；失败、超时与外层取消路径均不标记。
5. **atexit 兜底**：仅在未完成优雅停机时执行（`_atexit_fired` 保证至多一次），由 registry 的单一 atexit handler 按注册顺序的**逆序**调用各单例的 `_atexit_cleanup()`（未实现则跳过）；单个清理异常只告警，不阻断其余单例。
6. **取消语义（R2）**：停机步骤与清理方法不得吞没 `asyncio.CancelledError`，必须向上抛出；registry 只捕获 `Exception`，`CancelledError` 属 `BaseException`，会自然穿透。

### 单例注册清单

> R15 人工评审对照基准。新增/移除单例时同步更新本清单。

**注册单例（`@register_singleton`，21 个）**：

| 类名 | 模块路径 | 职责 |
|------|---------|------|
| `CacheManager` | `data/cache/cache_manager.py` | DAO 实例与引擎生命周期 facade |
| `ThreadPoolManager` | `utils/thread_pool.py` | IO/CPU 线程池调度 |
| `TaskManager` | `services/task_manager.py` | 后台任务编排 |
| `AIService` | `services/ai_service/__init__.py` | LLM 调用统一入口 |
| `AIUsageTracker` | `services/ai_service/usage_tracker.py` | 立即 AI 货币成本按月（分）原子累加与跨月轮换持久化 |
| `SchedulerService` | `utils/scheduler_service.py` | 定时调度 |
| `DataProcessor` | `data/data_processor.py` | 数据质量扫描与处理 |
| `MarketDataService` | `data/domain_services/market_data_service.py` | 行情数据聚合 |
| `NewsSubscriptionService` | `services/news_subscription_service.py` | 新闻订阅生命周期 |
| `TushareClient` | `data/external/tushare_client.py` | Tushare API 客户端 |
| `AkshareConceptClient` | `data/external/akshare_concept_client.py` | Akshare 概念板块客户端 |
| `LocalModelManager` | `services/local_model_manager.py` | 本地模型生命周期 |
| `NewsSentimentScorer` | `services/news_sentiment_scorer.py` | 新闻情绪打分子进程生命周期（FinBERT GGUF + numpy 分类头，ADR-0012） |
| `NewsSentimentIndexService` | `services/news_sentiment_index_service.py` | 新闻情绪指数聚合（N5-2：个股/市场日度指数，无共享可变状态，仅展示 ADR-0008） |
| `StrategyManager` | `strategies/all_strategies.py` | 策略注册表 |
| `MetaDataManager` | `data/persistence/metadata_manager.py` | 表/列别名预加载与缓存管理 |
| `EmbeddedPostgresService` | `data/persistence/embedded_postgres/service.py` | 内置 PostgreSQL 生命周期管理 |
| `EmbeddedPgMaintenanceService` | `services/embedded_pg_maintenance_service.py` | 内置 PostgreSQL 离线维护（备份/恢复/诊断） |
| `AppColors` | `ui/theme.py` | 双层主题色管理器（类级状态单例：Layer 2 颜色 + Observable state，`_reset_singleton` 复位 DARK 默认，根治跨测试泄漏） |
| `WriteQuality` | `data/persistence/write_quality.py` | 表级写入质量门控（coerce 比例/行数跟踪），支撑 DAT-03 写入侧质量降级 |
| `EgressAudit` | `utils/egress_audit.py` | LLM 云端外发元数据集中审计（SEC-03；JSONL 唯一事实源 + 会话计数 + recent 缓冲 + 单例写锁） |

**非注册单例（无 `@register_singleton`，但事实单例）**：

| 类名 | 模块路径 | 不纳入注册的原因 |
|------|---------|-----------------|
| `ConfigHandler` | `utils/config_handler.py` | 全静态方法（classmethod + 类级 cache），无实例概念，无需 `__new__` 单例化 |
| `ProxyManager` | `utils/proxy_manager.py` | 类级状态（`_no_proxy_domains` / `_initialized`），无实例概念，自定义 `_reset_singleton` 配合测试隔离 |
| `DataExplorerQueryClient` | `data/persistence/data_explorer_query_client.py` | 共享引擎非单例（`__init__` 为空、实例不唯一），类级 `_shared_engine` 由 `close_all()` 释放；无 `__new__`/`_instance` 不满足 R15 触发条件，测试隔离由 `_reset_data_explorer_shared_engine` autouse fixture 覆盖 |
| `OfflineCalendar` | `data/domain_services/offline_calendar.py` | 类级 `_calendar` + `get_instance` 缓存 `pandas_market_calendars` 不可变 SSE 日历，无状态变更/并发写/外部资源，R7 无实际危害；缓存语义变为可变或引入外部资源时需重新评估 |

**非单例服务（每次按需实例化）**：

| 类名 | 模块路径 | 不纳入单例的原因 |
|------|---------|-----------------|
| `BacktestService` | `services/backtest_service.py` | 按需实例化，依赖注入 `CacheManager` + `engine_factory` + `strategy_lookup`，无全局共享状态 |
| `ModelConfigurationService` | `services/model_configuration_service.py` | 统一模型管理配置 / 凭据 / 授权应用服务（ADR-0014 / S1），按需 DI 实例化，无进程级职责；事实源为 `model_management` 配置域与 keyring，实例内仅持同 operation_id 凭据租约映射（MM-12），不注册单例 |

### TushareClient 特殊说明

`TushareClient` 是注册单例中直接持有第三方 SDK 客户端句柄（tushare `ts.pro_api` 返回对象）的实例，特殊点如下：

- **Token 注入**：构造函数 `__init__(token=None, *, config=None, clock=None)` 支持依赖注入，`token` 缺省时走 `ConfigHandler.get_token()`（生产）或注入的 `config`（测试）。
- **`pro` 字段类型注解简化**：声明为非 Optional 的 `TushareProApi` 以避免 40+ 处 `reportOptionalMemberAccess` warning（见 `tushare_client.py` `pro` 字段旁注释），运行时可能为 None（token 未设置时）由 `_get_pro` 经 `TushareConfigError` 抛出。
- **`_token_invalid` 全局熔断标志**：`_handle_api_call` 触发熔断置 `True`，由 `set_token_async()` 重置为 `False`（`set_token()` 只是调度同一协程的同步包装；`__init__` 重复注入不同 token 时直接写 `False`，该路径不与 `_handle_api_call` 并发）。写入路径经 `_get_token_invalid_lock()` 提供的 loop-local `asyncio.Lock` 串行化，读取为无锁 bool 快照（async 路径不能持 `threading.Lock`，R11 合规）。
- **Token 脱敏**：所有日志与异常消息中 token 必须经 `DataSanitizer` 脱敏（R9 红线），由 `scripts/check_redlines.py` 的 `check_R_tushare_token_log` 静态守护。

### 测试隔离

- **单元测试（`tests/unit/`）**：`tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 在每个用例前后调用 `reset_all_singletons()`（逐类执行 `_reset_singleton`，单类异常仅告警不阻断），并复位模块级 `_graceful_shutdown_completed`（`_atexit_fired` 由 `reset_all_singletons()` 一并复位），使每个用例从「未优雅停机」基线开始。
- **非注册单例**：`ProxyManager._reset_singleton()` 由同一 autouse fixture 显式调用；`DataExplorerQueryClient` 由 `_reset_data_explorer_shared_engine` autouse fixture 复位（见上表）。
- **集成 / e2e 测试**：不做逐用例自动重置（integration 仅由 `tests/integration/conftest.py` 的 `cleanup_singletons_session` 在会话 teardown 统一关闭并 `reset_all_singletons()`），需手动调用 `_reset_singleton`，或用 `tests/conftest.py::singleton_state` 保存/恢复 `_instance`（后者不触发 registry 重置与 `_atexit_cleanup`）。
- 机制正本：[docs/guides/testing.md](../guides/testing.md)「测试编写规则」。

---

## 完成判定（canonical 入口）

- 新增单例走「单例注册清单」登记，生命周期（初始化/重置/停机/atexit）符合本文件模板与「生命周期阶段与停机顺序」
- 已实现 `_reset_singleton`（持有线程池/连接/文件句柄等外部资源者，须在其中先行释放）；持有资源的单例已接入 ShutdownCoordinator 对应步骤或 `register_cleanup_callback()`
- 按需实例化、依赖注入、无全局共享状态者按非单例处理（对照判定标准，未误登单例）
- 测试隔离方式符合「测试隔离」小节（unit 自动重置；集成 / e2e 手动）
- 涉及 `ThreadPoolManager` 提交边界（CLAUDE.md §3.2）与 R15 注册已核对

_最小验证命令：_ 单例/资源生命周期改动 → ruff + pyright + 相关 `tests/unit/` 用例；
        文档改动 → `python scripts/check_docs_consistency.py`。
