# 测试目录镜像迁移计划

> 关联文档：`docs/tests/unit_test.md` A 节（测试架构与策略层面检视）
> 关联规范：`CLAUDE.md` §7（测试规范）、§1.4（微创修改）
> 创建日期：2026-06-19
> 试点任务：Task 23 (UNIT-A4)

---

## 1. 背景与动机

`docs/tests/unit_test.md` 的 A 节指出：`tests/unit/` 根目录平铺了 126 个测试文件，未镜像源码目录结构。这导致：

1. **定位成本高**：查找某个源码模块的测试需要在根目录按文件名前缀扫描，无法通过目录路径直接定位。
2. **分组语义缺失**：无法通过 `pytest tests/unit/<module>/` 单独运行某一层的测试。
3. **与现有镜像目录不一致**：项目已有 `tests/unit/ui/`、`tests/unit/data/`、`tests/unit/strategies/`、`tests/unit/services/` 四个镜像子目录，但大量本应归入这些子目录（或新增子目录）的测试仍散落在根目录。

本计划通过**有界试点**验证目录镜像的可行性与收益，再分批推进剩余文件的迁移。

---

## 2. 目录镜像原则

**核心原则**：测试目录结构镜像源码目录结构。

```text
源码路径                    → 测试路径
utils/config_handler.py     → tests/unit/utils/test_config_handler.py
data/persistence/daos/      → tests/unit/data/persistence/daos/test_*.py
services/ai_service.py      → tests/unit/services/test_ai_service.py
```

**规则**：

1. **一对一镜像**：`<source>/<module>.py` 的测试位于 `tests/unit/<source>/test_<module>.py`。
2. **保留 git 历史**：迁移必须使用 `git mv`，确保 blame / log 可追溯。
3. **不改变测试内容**：迁移仅移动文件，不修改测试逻辑、不调整导入（项目 `pythonpath = ["."]` 保证绝对导入在任意深度可用）。
4. **子目录 `__init__.py`**：新增子目录须创建空 `__init__.py`（与 `tests/unit/ui/__init__.py` 保持一致）。
5. **conftest 作用域**：`tests/unit/conftest.py` 的 autouse fixture（`_reset_all_singletons`）对子目录自动生效，无需复制。
6. **跨模块/元测试例外**：测试基础设施本身（如 `test_pollution_detection.py`、`test_conftest_safety.py`）不映射单一源码模块，保留在根目录或归入 `tests/unit/infra/`。

---

## 3. 试点迁移（Task 23，已完成）

本试点迁移 5 个 `utils/` 层测试到 `tests/unit/utils/`，验证流程与收益。

### 3.1 迁移清单

| 原路径 | 新路径 | 源码映射 |
|--------|--------|----------|
| `tests/unit/test_config_handler.py` | `tests/unit/utils/test_config_handler.py` | `utils/config_handler.py` |
| `tests/unit/test_thread_pool.py` | `tests/unit/utils/test_thread_pool.py` | `utils/thread_pool.py` |
| `tests/unit/test_singleton_registry.py` | `tests/unit/utils/test_singleton_registry.py` | `utils/singleton_registry.py` |
| `tests/unit/test_security_utils.py` | `tests/unit/utils/test_security_utils.py` | `utils/security_utils.py` |
| `tests/unit/test_sanitizers.py` | `tests/unit/utils/test_sanitizers.py` | `utils/sanitizers.py` |

新增文件：`tests/unit/utils/__init__.py`（空文件，与 `tests/unit/ui/__init__.py` 一致）。

### 3.2 验证结果

- **收集数**：迁移前 316，迁移后 316（一致）。
- **通过数**：314 passed, 2 skipped（2 个 skip 为 PyInstaller spec 测试的既有行为，与迁移无关）。
- **Ruff**：`ruff check tests/unit/utils/` 全部通过。
- **Git 识别**：5 个文件均被 `git status` 识别为 `renamed`，历史保留。

### 3.3 试点结论

- 绝对导入在子目录下正常工作，无需调整测试内容。
- `conftest.py` 的 autouse fixture 自动继承，无需复制。
- `git mv` + `__init__.py` 模式可复用于后续批量迁移。

---

## 4. 剩余文件迁移计划

剩余约 119 个测试文件平铺在 `tests/unit/` 根目录，按源码模块分组、按优先级分批迁移。

### 4.1 优先级定义

- **P0（高）**：源码模块文件数多、测试文件集中，迁移后定位收益最大。
- **P1（中）**：源码模块文件数中等，迁移收益明显。
- **P2（低）**：跨模块/元测试，不映射单一源码，需单独处理。

### 4.2 按模块分组的迁移计划

#### P0 — `utils/` 层（剩余 ~19 个文件）

源码 `utils/` 共 23 个模块，试点已迁移 5 个，剩余测试文件：

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_async_utils.py` | `tests/unit/utils/` | `utils/async_utils.py` |
| `test_correlation.py` | `tests/unit/utils/` | `utils/correlation.py` |
| `test_diagnostics.py` | `tests/unit/utils/` | `utils/diagnostics.py` |
| `test_error_classifier.py` | `tests/unit/utils/` | `utils/error_classifier.py` |
| `test_exception_hooks.py` | `tests/unit/utils/` | `utils/exception_hooks.py` |
| `test_llm_providers.py` | `tests/unit/utils/` | `utils/llm_providers.py` |
| `test_llm_config.py` | `tests/unit/utils/` | `utils/llm_providers.py` / `config.py` |
| `test_log_decorators_extended.py` | `tests/unit/utils/` | `utils/log_decorators.py` |
| `test_logger_extended.py` | `tests/unit/utils/` | `utils/logger.py` |
| `test_loop_local.py` | `tests/unit/utils/` | `utils/loop_local.py` |
| `test_prompt_guard.py` | `tests/unit/utils/` | `utils/prompt_guard.py` |
| `test_proxy_manager.py` | `tests/unit/utils/` | `utils/proxy_manager.py` |
| `test_qfq.py` | `tests/unit/utils/` | `utils/qfq.py` |
| `test_qfq_property.py` | `tests/unit/utils/` | `utils/qfq.py` |
| `test_scheduler_service.py` | `tests/unit/utils/` | `utils/scheduler_service.py` |
| `test_service_rate_limiter.py` | `tests/unit/utils/` | `utils/rate_limiter.py` |
| `test_shutdown.py` | `tests/unit/utils/` | `utils/shutdown.py` |
| `test_technical_analysis.py` | `tests/unit/utils/` | `utils/technical_analysis.py` |
| `test_time_utils.py` | `tests/unit/utils/` | `utils/time_utils.py` |
| `test_utils_config.py` | `tests/unit/utils/` | `utils/config_handler.py` |
| `test_utils_config_lock.py` | `tests/unit/utils/` | `utils/config_handler.py` |
| `test_config_handler_keyring_fallback.py` | `tests/unit/utils/` | `utils/config_handler.py` |
| `test_config_models.py` | `tests/unit/utils/` | `utils/config_models.py` |
| `test_singleton_atexit_cleanup.py` | `tests/unit/utils/` | `utils/singleton_registry.py` |
| `test_singletons_isolation.py` | `tests/unit/utils/` | `utils/singleton_registry.py` |

#### P0 — `data/persistence/daos/` 层（~9 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_base_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/base_dao.py` |
| `test_financial_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/financial_dao.py` |
| `test_holder_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/holder_dao.py` |
| `test_macro_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/macro_dao.py` |
| `test_market_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/market_dao.py` |
| `test_quote_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/quote_dao.py` |
| `test_screener_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/screener_dao.py` |
| `test_stock_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/stock_dao.py` |
| `test_sync_dao.py` | `tests/unit/data/persistence/daos/` | `data/persistence/daos/sync_dao.py` |

#### P0 — `data/sync/` 层（~6 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_sync_base.py` | `tests/unit/data/sync/` | `data/sync/base.py` |
| `test_financial_sync.py` | `tests/unit/data/sync/` | `data/sync/financial.py` |
| `test_historical_sync.py` | `tests/unit/data/sync/` | `data/sync/historical.py` |
| `test_holder_sync.py` | `tests/unit/data/sync/` | `data/sync/holder.py` |
| `test_macro_sync.py` | `tests/unit/data/sync/` | `data/sync/macro.py` |
| `test_sync_type_consistency.py` | `tests/unit/data/sync/` | `data/sync/` |

#### P1 — `data/persistence/` 层（~12 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_app_state_service.py` | `tests/unit/data/persistence/` | `data/persistence/app_state_service.py` |
| `test_data_quality.py` | `tests/unit/data/persistence/` | `data/persistence/data_quality.py` |
| `test_database_manager.py` | `tests/unit/data/persistence/` | `data/persistence/database_manager.py` |
| `test_db_config_service.py` | `tests/unit/data/persistence/` | `data/persistence/db_config_service.py` |
| `test_db_migrator.py` | `tests/unit/data/persistence/` | `data/persistence/db_migrator.py` |
| `test_db_url_override.py` | `tests/unit/data/persistence/` | `data/persistence/db_url_override.py` |
| `test_metadata_manager.py` | `tests/unit/data/persistence/` | `data/persistence/metadata_manager.py` |
| `test_model_indexes.py` | `tests/unit/data/persistence/` | `data/persistence/models.py` |
| `test_field_mapping.py` | `tests/unit/data/persistence/` | `data/persistence/models.py` |
| `test_quality_gate.py` | `tests/unit/data/persistence/` | `data/persistence/quality_gate.py` |
| `test_review_manager.py` | `tests/unit/data/persistence/` | `data/persistence/review_manager.py` |
| `test_persistence_init.py` | `tests/unit/data/persistence/` | `data/persistence/__init__.py` |

#### P1 — `data/external/` 层（~5 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_news_fetcher.py` | `tests/unit/data/external/` | `data/external/news_fetcher.py` |
| `test_tushare_client.py` | `tests/unit/data/external/` | `data/external/tushare_client.py` |
| `test_tushare_capability.py` | `tests/unit/data/external/` | `data/external/tushare_client.py` |
| `test_tushare_fixes.py` | `tests/unit/data/external/` | `data/external/tushare_client.py` |
| `test_tushare_api_fields.py` | `tests/unit/data/external/` | `data/external/tushare_client.py` |

#### P1 — `data/domain_services/` 与 `data/mixins/` 层（~6 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_market_data_service.py` | `tests/unit/data/domain_services/` | `data/domain_services/market_data_service.py` |
| `test_offline_calendar.py` | `tests/unit/data/domain_services/` | `data/domain_services/offline_calendar.py` |
| `test_trade_calendar_service.py` | `tests/unit/data/domain_services/` | `data/domain_services/trade_calendar_service.py` |
| `test_calendar_mixin.py` | `tests/unit/data/mixins/` | `data/mixins/calendar_mixin.py` |
| `test_health_mixin.py` | `tests/unit/data/mixins/` | `data/mixins/health_mixin.py` |
| `test_data_dictionary.py` / `test_data_dictionary_alignment.py` | `tests/unit/data/` | `data/data_dictionary.py` |

#### P1 — `data/` 根与 `data/cache/` 层（~4 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_constants.py` | `tests/unit/data/` | `data/constants.py` |
| `test_data_processor.py` | `tests/unit/data/` | `data/data_processor.py` |
| `test_cache_manager.py` | `tests/unit/data/cache/` | `data/cache/cache_manager.py` |
| `test_cache_manager_lifecycle.py` | `tests/unit/data/cache/` | `data/cache/cache_manager.py` |

#### P1 — `services/` 层（~9 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_ai_service.py` | `tests/unit/services/` | `services/ai_service.py` |
| `test_ai_service_failover.py` | `tests/unit/services/` | `services/ai_service.py` |
| `test_ai_service_prompt_dump_retention.py` | `tests/unit/services/` | `services/ai_service.py` |
| `test_local_model_manager.py` | `tests/unit/services/` | `services/local_model_manager.py` |
| `test_news_subscription.py` | `tests/unit/services/` | `services/news_subscription_service.py` |
| `test_news_subscription_i18n_tags.py` | `tests/unit/services/` | `services/news_subscription_service.py` |
| `test_news_subscription_lru.py` | `tests/unit/services/` | `services/news_subscription_service.py` |
| `test_news_subscription_viewmodel.py` | `tests/unit/services/` | `services/news_subscription_service.py` |
| `test_task_manager.py` | `tests/unit/services/` | `services/task_manager.py` |

#### P1 — `strategies/` 层（~15 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_ai_mixin.py` | `tests/unit/strategies/` | `strategies/ai_mixin.py` |
| `test_ai_strategy.py` | `tests/unit/strategies/` | `strategies/ai_strategy.py` |
| `test_oversold_strategy.py` | `tests/unit/strategies/` | `strategies/oversold_strategy.py` |
| `test_oversold_prompt_alignment.py` | `tests/unit/strategies/` | `strategies/oversold_strategy.py` |
| `test_strategy_oversold_context.py` | `tests/unit/strategies/` | `strategies/oversold_strategy.py` |
| `test_strategy_base.py` | `tests/unit/strategies/` | `strategies/base_strategy.py` |
| `test_strategy_fundamental.py` | `tests/unit/strategies/` | `strategies/fundamental.py` |
| `test_strategy_market.py` | `tests/unit/strategies/` | `strategies/market.py` |
| `test_strategy_cap_quality_distribution.py` | `tests/unit/strategies/` | `strategies/` |
| `test_polars_filter_async_offload.py` | `tests/unit/strategies/` | `strategies/polars_base.py` |
| `test_prompt_injection_separation.py` | `tests/unit/strategies/` | `strategies/ai_mixin.py` |
| `test_prompt_validator.py` | `tests/unit/strategies/` | `strategies/prompt_validator.py` |
| `test_lookahead_bias.py` | `tests/unit/strategies/` | `strategies/` |
| `test_lookahead_guard.py` | `tests/unit/strategies/` | `strategies/` |
| `test_tier_consistency.py` | `tests/unit/strategies/` | `strategies/` |

#### P2 — `core/`、`app/`、`config.py` 层（~5 个文件）

| 测试文件 | 目标路径 | 源码映射 |
|---------|---------|---------|
| `test_i18n.py` | `tests/unit/core/` | `core/i18n.py` |
| `test_bootstrap.py` | `tests/unit/app/` | `app/bootstrap.py` |
| `test_config.py` | `tests/unit/`（根，对应根级 `config.py`） | `config.py` |
| `test_config_ai_concurrency.py` | `tests/unit/`（根） | `config.py` |
| `test_i18n_keys_completeness.py` | `tests/unit/core/` | `core/i18n.py` |

> 注：`config.py` 位于项目根目录，其测试可保留在 `tests/unit/` 根目录，或归入 `tests/unit/config/`（需新增 `__init__.py`）。

#### P2 — UI 相关散落文件（~6 个文件）

这些文件本应归入 `tests/unit/ui/`，但当前在根目录：

| 测试文件 | 目标路径 |
|---------|---------|
| `test_ui_view_cleanup.py` | `tests/unit/ui/` |
| `test_ui_deep_link.py` | `tests/unit/ui/` |
| `test_ui_home_vm.py` | `tests/unit/ui/` |
| `test_screener_view_model.py` | `tests/unit/ui/` |
| `test_ai_history_text.py` | `tests/unit/ui/` |
| `test_chart_utils.py` | `tests/unit/ui/`（或 `tests/unit/utils/`，视源码归属） |

#### P2 — 跨模块/元测试（~16 个文件，保留根目录或归入 `tests/unit/infra/`）

这些测试不映射单一源码模块，测试的是测试基础设施、架构边界、文档一致性等横切关注点：

| 测试文件 | 建议归属 |
|---------|---------|
| `test_pollution_detection.py` | `tests/unit/infra/` 或保留根目录 |
| `test_conftest_safety.py` | `tests/unit/infra/` |
| `test_infra_loop_isolation.py` | `tests/unit/infra/` |
| `test_infra_init_order.py` | `tests/unit/infra/` |
| `test_infra_history_config.py` | `tests/unit/infra/` |
| `test_infra_queue_threadpool_ratelimit.py` | `tests/unit/infra/` |
| `test_concurrency_audit.py` | `tests/unit/infra/` |
| `test_architecture_boundaries.py` | `tests/unit/infra/` |
| `test_boundary_conditions.py` | `tests/unit/infra/` |
| `test_lazy_imports.py` | `tests/unit/infra/` |
| `test_onboarding_api_contracts.py` | `tests/unit/infra/` |
| `test_verify_versions.py` | `tests/unit/infra/` |
| `test_docs_consistency.py` | `tests/unit/infra/` |
| `test_inspect_compat.py` | `tests/unit/infra/` |
| `test_keyring_mock_contract.py` | `tests/unit/infra/` |
| `test_strategy_manager.py` | `tests/unit/strategies/` 或 `tests/unit/services/`（视源码归属） |

---

## 5. 迁移注意事项

### 5.1 迁移前检查

1. **导入扫描**：确认目标测试文件使用绝对导入（项目惯例），无相对导入需调整。`pyproject.toml` 的 `pythonpath = ["."]` 保证任意目录深度的绝对导入可用。
2. **跨文件引用**：用 Grep 搜索 `from tests.unit.test_xxx` 或 `tests.unit.test_xxx`，确认无其他文件引用待迁移测试。
3. **conftest 作用域**：`tests/unit/conftest.py` 的 autouse fixture 对所有子目录生效，无需复制；但若子目录有特殊 fixture 需求（如 `tests/unit/ui/conftest.py`），需单独评估。
4. **收集数基线**：迁移前运行 `python -m pytest <原文件> --collect-only -q` 记录收集数，迁移后对比。

### 5.2 迁移操作

1. **使用 `git mv`**：保留 git 历史，`git status` 应识别为 `renamed`。
2. **新增 `__init__.py`**：每个新子目录创建空 `__init__.py`（与现有 `tests/unit/ui/__init__.py`、`tests/unit/data/` 等一致）。
3. **不修改测试内容**：迁移仅移动文件，不改变测试逻辑、导入、断言。

### 5.3 迁移后验证

1. **收集数一致**：`python -m pytest <新目录> --collect-only -q` 与迁移前一致。
2. **通过数一致**：`python -m pytest <新目录> -v` 全部通过（skip 数也应一致）。
3. **Ruff 通过**：`ruff check <新目录>` 无错误。
4. **Git 识别**：`git status` 显示 `renamed`，非 `deleted + new file`。

### 5.4 风险与回滚

- **风险低**：迁移不改变测试内容，绝对导入在 `pythonpath = ["."]"` 下不受目录深度影响。
- **回滚**：`git mv <新路径> <原路径>` 即可还原，无副作用。

---

## 6. 推进节奏建议

1. **每批一个模块层**：如 `utils/` 剩余文件作为一批，`data/persistence/daos/` 作为一批，便于回归验证。
2. **每批提交一次**：保持 commit 粒度可控，便于 review 与回滚。
3. **优先 P0**：`utils/`、`data/persistence/daos/`、`data/sync/` 收益最大，建议先行。
4. **元测试最后处理**：P2 跨模块测试需单独决策归属（根目录 vs `tests/unit/infra/`），不与模块迁移混在一起。
