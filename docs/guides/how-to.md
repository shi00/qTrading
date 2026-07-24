# 标准开发工作流 (How-To)

> 来源：从 CONTRIBUTING.md 迁移

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

1. 先确认 `ui.hooks.use_viewmodel` 是否已满足当前 ViewModel 消费需求；若未满足，先实现/扩展该 hook（见 [MVVM 表现层](../patterns/mvvm.md#mvvm-表现层)）。
2. 在 `ui/viewmodels/` 下创建对应 ViewModel：暴露不可变 state snapshot、commands、`subscribe(callback) -> unsub`，禁止 import Flet、禁止持有 Flet 控件、禁止调 `page.update()`/`control.update()`。
3. 在 `ui/views/` 下创建 `@ft.component` 声明式 View：只读取 state 渲染控件树，只在事件中调用 commands；禁止 `did_mount`/`will_unmount`/`self.update()`/`UserControl`/`PageRefMixin`（见 [V1 声明式 UI 开发规范](../flet/v1-api-constraints.md#v1-声明式-ui-开发规范)）。
4. i18n 文案由 VM 输出 key + params，View 按当前 locale 渲染；locale 变化作为 View 层声明式状态源触发重渲染（见 [V1 声明式 UI 开发规范](../flet/v1-api-constraints.md#v1-声明式-ui-开发规范) 中的 i18n 状态驱动规则）。
5. 响应式布局优先使用声明式 state / props / `ResponsiveRow`，禁止新增 `handle_resize` 鸭子分发式命令式代码。
6. 若需注册新标签页，再修改 `ui/app_layout.py`。
7. UI 事件中的同步 IO/CPU 密集任务必须通过 `ThreadPoolManager.run_async()` 或 `TaskManager.submit_task()` 提交，避免阻塞 Flet 主循环（对应 CLAUDE.md §3.1 R16）。
8. UI 事件回调使用 `@log_ui_action` 装饰器埋点。
9. 按 [变更类型 → 最小验证子集](../../CONTRIBUTING.md#变更类型--最小验证子集) 运行 UI 相关验证。

### 5. 新增一个外部数据源

1. 在 `data/external/` 下创建客户端模块，封装第三方 SDK 或 HTTP API。
2. 使用 `utils/rate_limiter.py` 提供的限流器避免触发对方风控。
3. 网络错误必须用 `classify_error(e, context="general")` 分类，自动处理重试。
4. 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)`。
5. 若需走代理，使用 `utils/proxy_manager.py`。

### 5.1 Tushare 集成工作流（简述）

新增/修改 Tushare API 接入时遵循以下工作流：

1. **客户端封装**：在 `data/external/tushare_client.py` 的 `TushareProApi` Protocol 中声明 API callable，并在对应 wrapper 方法中调用 `self._handle_api_call(...)` 统一限流/重试/熔断。
2. **积分档位映射**：若新 API 有积分要求，在 `data/constants.py` 的 `TUSHARE_POINT_TIERS` 与 `TushareClient._TIER_API_COVERAGE` 中追加（保持单一真相源）。
3. **同步策略**：在 `data/sync/` 下对应 syncer 文件中实现 `ISyncStrategy.sync()`，通过 `SyncContext` 注入 `cancel_event`，分块调用 `TushareClient` wrapper。
4. **表注册**：在 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 中注册新表（表名、同步配置、质量监控配置）。
5. **DAO 实现**：在 `data/persistence/daos/` 下创建对应 DAO，继承 `BaseDao`，使用 `_save_upsert()` 批量写入。
6. **质量门控**：syncer 写入前挂 `@require_quality(QualityTier.X)`，同步后由 `QuoteDAO.get_sync_quality_score()` 评估质量分数。
7. **取消传播**：syncer 分块循环中检查 `cancel_event.is_set()`，主动退出时 `raise asyncio.CancelledError`（R2 红线）。
8. **错误处理**：`except asyncio.CancelledError: raise`；`TushareAPIPermissionError` 捕获后跳过对应 API；其他异常经 `classify_error()` 分类。
9. **测试**：在 `tests/unit/test_historical_sync.py` / `test_financial_sync.py` 等对应测试文件中补充用例，使用 mock TushareClient 隔离外部 API。

详细设计模式（限流/质量门控/错误处理/取消传播）见 [data-sync.md](../patterns/data-sync.md#tushare-syncer-设计模式)。

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

1. 使用 `@register_singleton` 装饰器注册类（代码模板见[单例模式实现模板](../architecture/singleton-lifecycle.md#单例模式实现模板)）。
2. 实现 `_reset_singleton()` 类方法 (测试隔离必须)。
3. 实例创建必须受 `threading.Lock` 保护 (优先在 `__new__` 中持锁)。
4. 支持 `_initialized` 标志防止重复初始化。
5. 如需进程退出清理，实现 `_atexit_cleanup()` 类方法。
6. 在 [CLAUDE.md §4.3](../../CLAUDE.md#43-单例模式) 的单例列表中补充新单例名称。
7. 在 `tests/unit/` 下编写单测；常规隔离由 `_reset_all_singletons` autouse fixture 自动处理，需精细控制单例初始化状态时使用 `singleton_state` 上下文管理器。

### 9. 内置 PostgreSQL 离线维护

> 适用场景：应用无法启动（数据目录损坏 / 启动失败 / 需要离线备份恢复）时，使用 sidecar CLI 进行离线维护。

#### 9.1 前置条件

- 应用必须完全退出（sidecar 会获取 PGDATA 锁，运行中无法维护）
- sidecar binary 位于 `sidecars/qtrading-pg-sidecar[.exe]`
- 数据目录默认在 `<app data>/postgres/17/data`（可通过 `AppConfig.embedded_pg_data_root` 自定义）

#### 9.2 诊断（doctor）

```bash
sidecars/qtrading-pg-sidecar doctor --data-dir <数据目录>
```

输出 JSON（schema `qtrading.embedded_postgres.doctor.v1`），含 `initialized` / `pg_version` / `critical_files_missing` / `postgres_alive` / `state_file` / `issues` 等字段。

exit code 含义：
- `0` 成功（PG 运行中）
- `20` PG 未运行（容忍，doctor 是只读诊断）
- `40` 数据目录损坏
- `50` 锁冲突（应用未完全退出）

#### 9.3 备份（dump）

```bash
sidecars/qtrading-pg-sidecar dump --data-dir <数据目录> --output <备份文件路径>
```

输出 PostgreSQL custom format 备份文件，可用 `pg_restore` 工具恢复。

#### 9.4 恢复（restore）

```bash
sidecars/qtrading-pg-sidecar restore --data-dir <数据目录> --input <备份文件路径> [--target-data-dir <新数据目录>]
```

**重要**：sidecar 采用原子切换策略 — 恢复到新目录而非覆盖原目录，避免恢复中途失败导致数据丢失。恢复成功后需手动切换数据目录指向新目录。

#### 9.5 维护实例（maintenance-shell）

```bash
sidecars/qtrading-pg-sidecar maintenance-shell --data-dir <数据目录>
```

启动临时维护实例（不竞争主实例锁），输出含 `psql_path` + `connection_string_redacted`（密码已脱敏）的 JSON，用户可用 psql 直接连接进行高级维护。

#### 9.6 错误分类

| exit code | 错误类型 | 用户提示 |
|-----------|---------|---------|
| 10 | sidecar_arg_error | 维护命令参数错误 |
| 11 | initdb_failed | 数据库初始化失败 |
| 12 | pg_start_failed | 数据库启动失败 |
| 15 | disk_full | 磁盘空间不足，请清理后重试 |
| 20 | pg_not_running | 幂等，doctor 容忍 |
| 40 | pgdata_corrupt | 数据目录损坏，请使用恢复向导 |
| 50 | lock_conflict | 请先关闭 qTrading 再执行维护操作 |

#### 9.7 Python 服务封装

工程实现见 `services/embedded_pg_maintenance_service.py`（`EmbeddedPgMaintenanceService` 单例），4 个命令（`doctor` / `dump` / `restore` / `maintenance_shell`）通过 `ThreadPoolManager.run_async(TaskType.IO)` 提交同步 `subprocess.run` 避免阻塞事件循环（R16）。设置页「数据库」标签底部的「离线维护工具」说明区块指向本章节。

### 10. 运行 embedded 模式真实 sidecar 测试

> 适用场景：验证内置 PostgreSQL（子进程启动）真实场景的端到端覆盖，包含真实 Rust sidecar binary + 真实 PostgreSQL 17。

#### 10.1 测试范围

| 测试文件 | 层级 | 覆盖内容 |
|---------|------|---------|
| `tests/integration/test_embedded_postgres_real_sidecar.py` | 集成 | 真实 sidecar 启动协议（ready JSON、password_file、sha256 校验、stop 释放进程、日志收集） |
| `tests/integration/test_embedded_pg_migration_regression.py` | 集成 | embedded PG 上 Alembic 完整迁移回归（upgrade head / downgrade base / upgrade head / check） |
| `tests/integration/test_embedded_pg_dao_rw.py` | 集成 | CacheManager + DAO 读写（StockDao / QuoteDao 批量 upsert、事务回滚） |
| `tests/integration/test_embedded_pg_bootstrap.py` | 集成 | `prepare_database_runtime()` embedded 路径启动协调 + 完整 bootstrap 流程 |
| `tests/e2e/test_onboarding_embedded_real.py` | E2E | 真实 sidecar 完整应用启动 + Onboarding UI 流程（Linux only，Windows skipif） |

所有测试标记 `@pytest.mark.embedded_real`，使用 `real_embedded_pg` session-scoped fixture 共享 sidecar 实例（避免每个测试重复 initdb）。

#### 10.2 本地运行前提

sidecar binary 三种来源（按 `tests/_sidecar_binary.py::find_sidecar_binary()` 定位顺序）：

1. **环境变量**（推荐）：`SIDECAR_BINARY_PATH` 指向 binary 绝对路径
2. **开发模式默认路径**：`sidecars/qtrading-pg-sidecar[.exe]`（cwd-relative）
3. **cargo build 产物**：`sidecars/qtrading-pg-sidecar/target/release/qtrading-pg-sidecar[.exe]`

**方式 A：从 GitHub Release 下载**（推荐，无需 Rust 工具链）

```bash
# 1. 查找最新 sidecar-v* release
gh release list --repo <your-repo> --limit 100 --exclude-drafts --exclude-pre-releases | grep "sidecar-v"

# 2. 下载对应平台 binary + sha256sums（以 Linux x86_64 为例）
gh release download <tag> --repo <your-repo> \
  --pattern qtrading-pg-sidecar-linux-x86_64 \
  --pattern sha256sums-x86_64-unknown-linux-gnu.txt \
  --dir sidecars/qtrading-pg-sidecar/target/release/

# 3. rename 为期望的 binary 名
mv sidecars/qtrading-pg-sidecar/target/release/qtrading-pg-sidecar-linux-x86_64 \
   sidecars/qtrading-pg-sidecar/target/release/qtrading-pg-sidecar

# 4. 设置环境变量（SHA256 从 sha256sums 文件提取）
export SIDECAR_BINARY_PATH=$(pwd)/sidecars/qtrading-pg-sidecar/target/release/qtrading-pg-sidecar
export SIDECAR_SHA256=$(awk '{print $1}' sidecars/qtrading-pg-sidecar/target/release/sha256sums-x86_64-unknown-linux-gnu.txt)
```

**方式 B：cargo build**（需要 Rust 工具链）

```bash
cd sidecars/qtrading-pg-sidecar
cargo build --release
# binary 位于 sidecars/qtrading-pg-sidecar/target/release/qtrading-pg-sidecar[.exe]
```

#### 10.3 运行命令

```bash
# 集成测试（串行 -n 1 避免 sidecar 实例并发竞争）
python -m pytest tests/integration/test_embedded_postgres_real_sidecar.py \
                 tests/integration/test_embedded_pg_migration_regression.py \
                 tests/integration/test_embedded_pg_dao_rw.py \
                 tests/integration/test_embedded_pg_bootstrap.py \
                 -v --tb=short -n 1

# E2E 测试（Linux only，Windows skipif）
python -m pytest tests/e2e/test_onboarding_embedded_real.py -v --tb=short
```

#### 10.4 skip 行为

sidecar binary 缺失时（三种来源均未找到），`real_sidecar_binary` fixture 触发 `pytest.skip("real sidecar binary not found")`，所有 `embedded_real` 测试自动 skip（不 fail）。这确保本地开发不强制依赖 sidecar binary。

#### 10.5 CI 集成

CI 通过 `.github/workflows/ci_cd.yml` 的 `embedded-tests` job 自动运行（Linux + Windows matrix）：
- 从最新 `sidecar-v*` stable release 下载 binary + sha256sums
- SHA256 供应链完整性校验
- 设置 `SIDECAR_BINARY_PATH` + `SIDECAR_SHA256` + `QTRADING_DATABASE_MODE=embedded` 环境变量
- 串行运行集成测试 + Linux E2E 测试
- 作为 main 分支必需检查阻塞 merge（需手动配置 branch protection）
