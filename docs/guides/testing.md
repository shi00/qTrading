# 测试规范

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.1 R7（测试状态污染红线）与 §1.5（目标驱动与验证）；实现细则以本节为准。

### 测试架构

分为 `unit/` (单元测试, 纯逻辑隔离), `integration/` (集成测试, 依赖 PostgreSQL), `e2e/` (端到端测试)。

测试 marker 清单见 [`pyproject.toml`](../../pyproject.toml) 的 `[tool.pytest.ini_options].markers`（含 `unit` / `integration` / `e2e` / `slow` / `network` / `database` / `migration` / `ai` / `no_auto_mock` / `mutates_config` / `no_db` 等）。本文档不手工维护子集，以 `pyproject.toml` 为单一事实源。

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
- **事件循环 scope**（事实源 [`pyproject.toml`](../../pyproject.toml) `[tool.pytest.ini_options]`）：
  - **unit test**：`asyncio_default_test_loop_scope = "function"`（每个测试独立循环，隔离单例/loop-local 状态，避免测试间污染）
  - **integration / e2e**：在 `tests/integration/conftest.py` 等处通过 `@pytest_asyncio.fixture(scope="session", loop_scope="session")` 显式 override，复用 session 级事件循环以降低启动开销
  - Windows 事件循环通过 `tests/conftest.py::pytest_asyncio_loop_factories()` hook 返回 `asyncio.SelectorEventLoop`，与原 `WindowsSelectorEventLoopPolicy` 等价（hook 替换了 pytest-asyncio 已废弃的 `event_loop_policy` fixture）
- **配置隔离**: 测试使用临时配置文件 (`tempfile.mkdtemp`)，通过 `pytest_configure` 在 import 之前重写 `utils.config_handler.CONFIG_FILE`。
- **DB 隔离**: 集成测试连接 `test_astock` 数据库 (CI 通过 service container 启动 PostgreSQL 16，模拟最低兼容外置环境；生产内置为 PostgreSQL 17.2.0)，通过 `TEST_DB_*` 环境变量配置。

### 覆盖率要求

> [!NOTE]
> 覆盖率阈值的单一事实源位于 `pyproject.toml`。
> - **整体覆盖率**：具体数值见 `pyproject.toml` 中的 `fail_under`（目前为 ≥ 85%）
> - **单文件覆盖率**：具体数值见 `pyproject.toml` 中的 `per_file_minimum`（目前为 ≥ 80%，由 `scripts/check_per_file_coverage.py` 强制检查）
> - **覆盖率源**：`core`, `app`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）
> - **覆盖率排除行**：`pragma: no cover`、`if __name__ == "__main__"`、`if TYPE_CHECKING:`、`raise NotImplementedError`、`...`
> - **覆盖率 omit 文件**：`pyproject.toml` `[tool.coverage.run].omit` 含 `main.py`（标注 `NOTE(lazy)`；升级触发条件：重构 `main.py` 拆出可测的 bootstrap 模块后移除 omit）
