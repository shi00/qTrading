# 测试规范

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.1 R7（测试状态污染红线）与 §1.5（目标驱动与验证）；实现细则以本节为准。

### 测试架构

分为 `unit/` (单元测试, 纯逻辑隔离), `integration/` (集成测试, 依赖 PostgreSQL), `e2e/` (端到端测试)。Flutter Web CanvasKit 渲染行为与 E2E 避坑指南详见 [`docs/flet/canvaskit-rendering-e2e-guide.md`](../flet/canvaskit-rendering-e2e-guide.md)。

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
- **DB 隔离**: 集成测试连接 `test_astock` 数据库 (CI 通过 service container 启动 PostgreSQL 16，模拟最低兼容外置环境；生产内置为 PostgreSQL 16.14.0)，通过 `TEST_DB_*` 环境变量配置。

### 覆盖率要求

> [!NOTE]
> 覆盖率阈值的单一事实源位于 `pyproject.toml`。
> - **整体覆盖率**：具体数值见 `pyproject.toml` 中的 `fail_under`（目前为 ≥ 85%）
> - **单文件覆盖率**：具体数值见 `pyproject.toml` 中的 `per_file_minimum`（目前为 ≥ 80%，由 `scripts/check_per_file_coverage.py` 强制检查）
> - **分层单文件覆盖率**：`services/`、`strategies/`、`data/` 单文件阈值 ≥ 90%，`ui/` ≥ 85%（`pyproject.toml` `per_file_minimum_by_path`，最长前缀匹配，未匹配目录仍按默认 ≥ 80%）；当前 `enforce_layered=false` 为 advisory 报告模式（`scripts/check_per_file_coverage.py --report` 输出，CI「Report Layered Coverage」step），不阻断；补齐分层覆盖率后翻转强制
> - **覆盖率源**：`core`, `app`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）
> - **覆盖率排除行**：`pragma: no cover`、`if __name__ == "__main__"`、`if TYPE_CHECKING:`、`raise NotImplementedError`、`...`
> - **覆盖率 omit 文件**：`pyproject.toml` `[tool.coverage.run].omit` 含 `main.py`（标注 `NOTE(lazy)`；升级触发条件：重构 `main.py` 拆出可测的 bootstrap 模块后移除 omit）

### 测试编写模板

> R19（未配套测试的业务逻辑变更）由 CI 强制，AI 必须交付符合项目约定的测试。下列模板为可直接复制的最小可运行样例（真实测试惯例摘录，`how-to.md` 各流程末尾的"编写单测"指向此处对应模板锚点）。

#### 模板 1：DAO 单测（mock engine 隔离 DB）

```python
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.holder_dao import HolderDao

pytestmark = pytest.mark.unit


def _make_dao():
    dao = HolderDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=None)
    dao._write_db = AsyncMock(return_value=0)
    return dao


@pytest.mark.asyncio
async def test_save_valid():
    dao = _make_dao()
    df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240630"], "holder_num": [100]})
    assert await dao.save_holder_number(df) == 5
    dao._write_db.assert_called_once()
```

要点：用 `MagicMock(spec=AsyncEngine)` 构造 DAO 隔离真实 DB；对 `_save_upsert` / `_read_db` / `_write_db` 打 `AsyncMock` 桩；busy 写入路径等若涉事务须覆盖 `_guarded_begin`（可 `@asynccontextmanager` mock）。对应 [how-to.md 第 2 条流程第 5 步](../../docs/guides/how-to.md#2-新增一个-dao)。

#### 模板 2：ViewModel 单测（state 快照 + subscribe 通知）

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from ui.viewmodels.system_viewmodel import SystemViewModel

pytestmark = pytest.mark.unit


def _subscribe(vm):
    snapshots = []
    vm.subscribe(lambda s: snapshots.append(s))
    return snapshots


@pytest.mark.asyncio
async def test_command_updates_state(monkeypatch):
    # 覆盖 VM 依赖（ConfigHandler / TushareClient / ThreadPoolManager），
    # 使 ThreadPoolManager.run_async 直接调用同步函数（避免线程池依赖）
    mock_client = MagicMock()
    mock_client.probe_api_capabilities = AsyncMock(return_value={"daily": True})

    async def _mock_run_async(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tp = MagicMock()
    mock_tp.return_value.run_async = _mock_run_async

    monkeypatch.setattr("data.external.tushare_client.TushareClient", lambda: mock_client)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tp)

    vm = SystemViewModel()
    snapshots = _subscribe(vm)
    await vm.on_tier_changed("points_5000")

    assert vm.state.probe_result is not None
    assert any(s.probe_result is not None for s in snapshots)  # subscribe 通知收到
```

要点：`vm.subscribe(cb)` 收集 state 快照；`await vm.<command>(...)` 触发；断言 `vm.state` 不可变快照 + 快照列表（验证订阅通知，对应 mvvm.md VM 契约）。VM 构造依赖外部 service 时用 `monkeypatch.setattr` 覆盖依赖（必要时 `MagicMock(spec=...)` 强制接口契约）。对应 [how-to.md 第 4 条流程第 5 步](../../docs/guides/how-to.md#4-新增一个-ui-视图)。

#### 模板 3：异步取消单测（R2 取消传播）

```python
import asyncio
import pytest
from utils.async_utils import gather_return_exceptions_propagating_cancel

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_cancelled_error_propagated():
    async def ok():
        return "ok"

    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await gather_return_exceptions_propagating_cancel(ok(), cancel())
```

要点：R2（异常吞没）唯一可自动验证的表达方式即 `pytest.raises(asyncio.CancelledError)`。凡涉业务并发的循环/分组收集逻辑，须断言取消不被吞没。

#### 模板 4：策略单测（Polars 夹具 + 依赖声明）

```python
import pandas as pd
import polars as pl
import pytest

from data.persistence.quality_gate import QualityTier
from strategies.market import VolumeBreakoutStrategy

pytestmark = pytest.mark.unit


def test_required_quality_tier():
    assert VolumeBreakoutStrategy.required_quality_tier == QualityTier.SILVER


def test_market_trend_filter_selects_in_range():
    strategy = VolumeBreakoutStrategy()
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "pct_chg": [3.0, 8.0, 5.0],
            "turnover_rate": [5.0, 5.0, 5.0],
            "total_mv": [100.0, 200.0, 300.0],
        }
    )
    lf = pl.from_pandas(df).lazy()
    result = strategy._filter_logic(lf, {"params": {}}).collect()
    assert set(result["ts_code"].to_list()) == {"000001.SZ", "000003.SZ"}
```

要点：先断言 `required_quality_tier` 类属性（质量门控依赖声明，对应 §3.2 强制要求）；用 `pd.DataFrame` → `pl.from_pandas().lazy()` 构造向量化输入，断言 `_filter_logic` 结果。对应 [how-to.md 第 3 条流程第 7 步](../../docs/guides/how-to.md#3-新增一个策略)。
