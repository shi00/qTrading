# 单例模式实现模板

> 来源：从 CONTRIBUTING.md 迁移

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

### 单例注册清单

> R15 人工评审对照基准。新增/移除单例时同步更新本清单。

**注册单例（`@register_singleton`，12 个）**：

| 类名 | 模块路径 | 职责 |
|------|---------|------|
| `CacheManager` | `data/cache/cache_manager.py` | DAO 实例与引擎生命周期 facade |
| `ThreadPoolManager` | `utils/thread_pool.py` | IO/CPU 线程池调度 |
| `TaskManager` | `services/task_manager.py` | 后台任务编排 |
| `AIService` | `services/ai_service.py` | LLM 调用统一入口 |
| `SchedulerService` | `utils/scheduler_service.py` | 定时调度 |
| `DataProcessor` | `data/data_processor.py` | 数据质量扫描与处理 |
| `MarketDataService` | `data/domain_services/market_data_service.py` | 行情数据聚合 |
| `NewsSubscriptionService` | `services/news_subscription_service.py` | 新闻订阅生命周期 |
| `TushareClient` | `data/external/tushare_client.py` | Tushare API 客户端 |
| `AkshareConceptClient` | `data/external/akshare_concept_client.py` | Akshare 概念板块客户端 |
| `LocalModelManager` | `services/local_model_manager.py` | 本地模型生命周期 |
| `StrategyManager` | `strategies/all_strategies.py` | 策略注册表 |

**非注册单例（无 `@register_singleton`，但事实单例）**：

| 类名 | 模块路径 | 不纳入注册的原因 |
|------|---------|-----------------|
| `ConfigHandler` | `utils/config_handler.py` | 全静态方法（classmethod + 类级 cache），无实例概念，无需 `__new__` 单例化 |
| `ProxyManager` | `utils/proxy_manager.py` | 类级状态（`_no_proxy_domains` / `_initialized`），无实例概念，自定义 `_reset_singleton` 配合测试隔离 |

**非单例服务（每次按需实例化）**：

| 类名 | 模块路径 | 不纳入单例的原因 |
|------|---------|-----------------|
| `BacktestService` | `services/backtest_service.py` | 按需实例化，依赖注入 `CacheManager` + `engine_factory` + `strategy_lookup`，无全局共享状态 |

