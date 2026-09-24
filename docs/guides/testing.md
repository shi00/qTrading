# 测试规范

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.1 R7（测试状态污染红线）与 §1.5（目标驱动与验证）；实现细则以本节为准。

### 测试架构

分为 `unit/` (单元测试, 纯逻辑隔离), `integration/` (集成测试, 依赖 PostgreSQL), `e2e/` (端到端测试)。Flutter Web CanvasKit 渲染行为与 E2E 避坑指南详见 [`docs/flet/canvaskit-rendering-e2e-guide.md`](../flet/canvaskit-rendering-e2e-guide.md)。

测试 marker 清单见 [`pyproject.toml`](../../pyproject.toml) 的 `[tool.pytest.ini_options].markers`（含 `unit` / `integration` / `e2e` / `slow` / `network` / `database` / `migration` / `ai` / `no_auto_mock` / `mutates_config` / `no_db` 等）。本文档不手工维护子集，以 `pyproject.toml` 为单一事实源。

### 测试资产地图

> 仓库测试资产共 **495 个 `.py`**（`tests/` 递归计数，不含 `__init__.py` 外的 fixture/工具）。本节是测试资产的可发现索引：先查"去哪个目录、按什么关键词、用什么 fixture"，再着手写，避免重复造轮子（§3.2 复用优先）。entry-level 运行命令见「marker → 运行命令」表；E2E / embedded 完整前置见「集成与 E2E 环境」小节。

#### 测试目录 → 覆盖对象 → 如何定位

| 测试目录（glob） | 覆盖对象 | 如何定位（关键词 / glob） | 新增用例入该目录的条件 |
|---|---|---|---|
| `tests/unit/test_*.py`（顶层，约 210 个文件） | 各层纯逻辑单测 + **治理/文档门禁**（见下） | `tests/unit/test_<主题>.py`；按类名如 `test_no_cancelled_error_swallow.py`（R2）/ `test_no_class_attr_asyncio_primitives.py`（R11）/ `test_architecture_boundaries.py`（R1）/ `test_redline_checks.py` / `test_data_dictionary_alignment.py`（R12）/ `test_cache_manager_dao_registry.py`（R13）/ `test_r8_enforcement.py`（R8）/ `test_i18n_*.py` | 不依赖真实 DB / 外部服务，用 mock 隔离即归此域；门禁 / 脚本 / 工具类变更的配套测试必放此 |
| `tests/unit/strategies/` | 策略类（`strategies/`） | `strategies/`、`backtest/` 等子目录 | 单测断言 `required_quality_tier` + `_filter_logic`（Polars 夹具） |
| `tests/unit/data/` + `persistence/` + `daos/` + `data/domain_services/` + `data/persistence/daos/` | `data/` 层：DAO / 数据清洗 / 域服务 | `tests/unit/data/**`、`tests/unit/daos/**`、`tests/unit/persistence/**` | 用 mock engine / mock TushareClient 隔离真实 IO（sync/DAO 顶层单测如 `test_holder_sync.py` 亦在此域） |
| `tests/unit/services/` + `ai_service/` | `services/` 层：应用服务 / AI 服务 / 编排 | `tests/unit/services/**`、`services/ai_service/` | 服务单测；涉及 is_refresh 生命周期 / 轮询编排 |
| `tests/unit/ui/`（含 `viewmodels/` / `views/` / `components/`） | `ui/` 层：ViewModel / 视图 / 组件 | `tests/unit/ui/**` | ViewModel 状态快照 + subscribe 断言；依赖 `tests/unit/ui/conftest.py` 的 page stub |
| `tests/unit/utils/`、`tests/unit/scripts/` | `utils/` 横切 + `scripts/` 工程工具 | 按被测模块名 `tests/unit/utils/*` | utils / 工程脚本逻辑 |
| `tests/builders/` | 测试数据构建器（`stock_data.py`） | `tests/builders/*.py` | 跨文件共享的最小真实数据集构造（stock_basic / trade_cal / daily_quotes） |
| `tests/integration/`（约 70 个文件） | DAO / 服务 / 模型 / schema（真实 `test_astock` PostgreSQL） | `tests/integration/*.py`；`fixtures/mvd_data.py`；`tests/integration/_db_config.py` | 需真实 DB / schema 的行为验证（标记 `integration`） |
| `tests/e2e/`（+`fixtures/fake_sidecar.py`） | Flet UI 端到端 / embedded 启动 / onboarding 流程 | `tests/e2e/*.py` | 端到端真实运行（标记 `e2e`），依赖 E2E 环境（见下） |

**治理 / 文档门禁测试**（`tests/unit/` 顶层，与施工门禁强相关）：
`test_docs_consistency.py`（文档一致性）· `test_docs_template_symbols.py`（文档代码块符号可落地性）· `test_redline_checks.py`（红线检查）· `test_architecture_boundaries.py`（R1 分层）· `test_import_linter_config.py`（import-linter 契约）· `test_no_cancelled_error_swallow.py`（R2）· `test_no_class_attr_asyncio_primitives.py`（R11）· `test_check_diff_coverage.py` · `test_check_per_file_coverage.py` · `test_check_staged_weak_assertions.py` · `test_scan_weak_assertions.py` · `test_run_pyright_changed.py`。新增/修改门禁或工程脚本规范时，配套测试一律放入 `tests/unit/` 顶层。

#### conftest 层级与 autouse fixture

| 文件 | 作用域 | 关键 fixture / hook |
|---|---|---|
| `tests/conftest.py` | session | `pytest_asyncio_loop_factories()`（Windows SelectorEventLoop）、`singleton_state` 上下文管理器、`mock_external_services` autouse（mock `NewsFetcher`/`ReviewManager`）、`isolate_config_file`、keyring/litellm 全局 mock、`pytest_configure` 早期拦截 |
| `tests/unit/conftest.py` | unit | **`_reset_all_singletons` autouse**（R7 单例隔离）、`_isolate_egress_audit_disk` 等 8 个 autouse 状态重置 fixture |
| `tests/unit/ui/conftest.py` | unit-ui | page stub（`Control.update()` 已挂载兼容，按 monkeypatch 隔离） |
| `tests/integration/conftest.py` | integration | `_v1_page_compat`、`_isolate_tushare_token_file`、`function_engine` 等；session 级 DB 环境搭建 |
| `tests/e2e/conftest.py` | e2e | session 级真 sidecar 启动 fixture（`real_sidecar_binary_e2e` / `flet_app` / `embedded_wizard_app` 等） |

#### marker → 运行命令

> marker 清单单一事实源为 `pyproject.toml` `[tool.pytest.ini_options].markers`（`unit` / `integration` / `e2e` / `slow` / `network` / `database` / `migration` / `ai` / `no_auto_mock` / `mutates_config` / `no_db` / `embedded_real` / `meta` / `xdist_group` / `timeout_e2e_*`）。以下为常用运行入口。

| 关注域 | 运行命令 |
|---|---|
| 单元测试 | `python -m pytest tests/unit/ -n auto -v --tb=short` |
| 集成测试 | `python -m pytest tests/integration/ -n auto -v --tb=short`（需 `test_astock` 库，见 CONTRIBUTING.md「数据库设置」） |
| E2E 本地跑 | 根目录 `python run_e2e_local.py`（绕过 PowerShell ExecutionPolicy，自动设 sidecar/artifact/env 变量） |
| embedded 真实 sidecar 集成/E2E | 见 [how-to.md「10. 运行 embedded 模式真实 sidecar 测试」](../guides/how-to.md#10-运行-embedded-模式真实-sidecar-测试)（前提、命令、skip 行为/CI 均在该节，不复制） |

#### 测试质量治理工具

| 工具 | 用途 | 入口 |
|---|---|---|
| flaky 处置 | 重复跑 pytest 对比多轮结果，定位 flaky nodeid | `python scripts/detect_flaky.py --path tests/unit/ --runs 10 --parallel 4 --reruns 2`；如需仅复跑上次失败用例追加 `--lf` |
| 弱断言治理 | 扫描弱断言（裸布尔 / pass / Mock 弱断言等），baseline 增量阻断 | CI：`python scripts/scan_weak_assertions.py --base tests/weak_assertion_baseline.json`（baseline 条目数只降不升，`--update-baseline` 开发态覆盖）；pre-commit：`scripts/check_staged_weak_assertions.py`（仅阻断新增）；行内白名单 `# noqa: weak-assertion <reason>` |

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
> - **整体覆盖率**：具体数值见 `pyproject.toml` 中的 `fail_under`（目前为 ≥ 85%）。**CI 不强制整体 85%**——`ci_cd.yml` 的 pytest 未传 `--cov-fail-under`，`fail_under=85` 仅在本地命令生效；CI 强制的是下方单文件 ≥80% 与 diff coverage ≥80%
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

---

## 完成判定（canonical 入口）

- 新测试已按对应「测试编写模板」落地（DAO / ViewModel / 异步取消 / 策略），未发明未对齐的非标准写法
- 覆盖率达标（单文件 ≥ 80% 与 diff coverage ≥80% 为 CI 强制；整体 ≥ 85% 为本地目标；分层阈值为 advisory）
- E2E 用例按要求串行/网络隔离（Windows skipif 等既有约定）依从

_最小验证命令：_ `ruff` + `pyright` + 对应 `tests/unit/`/`tests/integration/`；
        E2E → `python -m pytest tests/e2e/`；文档改动 → `python scripts/check_docs_consistency.py`。
