import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import asyncpg
import flet as ft
import pytest
import pytest_asyncio
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from data.cache.cache_manager import CacheManager
from data.persistence.db_url_override import override_db_url
from data.persistence.models import (
    BlockTrade,
    DailyIndicators,
    DailyQuotes,
    Dividend,
    FinaAudit,
    FinaMainbz,
    FinancialReports,
    MacroEconomy,
    MarketNews,
    MoneyflowDaily,
    NorthboundHolding,
    PledgeStat,
    ShiborDaily,
    StockBasic,
    StkHoldernumber,
    Top10Holders,
    TopList,
    TradeCal,
)
from tests.integration.fixtures.mvd_data import (
    MVD_BLOCK_TRADE,
    MVD_DAILY_INDICATORS,
    MVD_DAILY_QUOTES,
    MVD_DIVIDEND,
    MVD_FINA_AUDIT,
    MVD_FINA_MAINBZ,
    MVD_FINANCIAL_REPORTS,
    MVD_MACRO_ECONOMY,
    MVD_MARKET_NEWS,
    MVD_MONEYFLOW_DAILY,
    MVD_NORTHBOUND_HOLDING,
    MVD_PLEDGE_STAT,
    MVD_SHIBOR_DAILY,
    MVD_STK_HOLDERNUMBER,
    MVD_STOCK_BASIC,
    MVD_TOP10_HOLDERS,
    MVD_TOP_LIST,
    MVD_TRADE_CAL,
)

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _v1_page_compat(monkeypatch):
    """Per-test V1 page 兼容桩（替代旧 mock_flet 全局桩，方案 §3.3.1）。

    V1 中 ``ft.Control.page`` 改为只读 property（通过 ``parent`` 链查找），
    ``Control.update()`` 要求控件已挂载。本 fixture 用 monkeypatch 作用域隔离地
    恢复 V0 兼容行为：page 可读写、未挂载 ``update()`` 静默返回。

    集成测试（如 test_config_panels.py）用 ``panel.page = mock_page`` 注入 page。
    与 tests/unit/ui/conftest.py 的同名 fixture 行为一致，避免重复维护两套桩。
    """
    # Any: fget 在 V1 只读 property 类型存根中推断为 None，运行时必为可调用对象
    original_page_get: Any = ft.Control.page.fget
    original_update: Any = ft.Control.update

    @property
    def page(self) -> ft.Page | None:
        mock_page = self.__dict__.get("_mock_page")
        if mock_page is not None:
            return mock_page
        try:
            return original_page_get(self)
        except RuntimeError:
            return None

    @page.setter
    def page(self, value: ft.Page | None) -> None:
        self.__dict__["_mock_page"] = value

    def update(self) -> None:
        if self.__dict__.get("_mock_page") is None:
            try:
                original_page_get(self)
            except RuntimeError:
                return
        original_update(self)

    monkeypatch.setattr(ft.Control, "page", page)
    monkeypatch.setattr(ft.Control, "update", update)


TEST_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
TEST_DB_USER = os.environ.get("TEST_DB_USER", "postgres")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD") or os.environ.get("CI_PG_PASSWORD")
if not TEST_DB_PASSWORD:
    _run_id = os.environ.get("GITHUB_RUN_ID", "")
    if _run_id:
        TEST_DB_PASSWORD = hashlib.sha256(f"astock_ci_{_run_id}".encode()).hexdigest()[:24]
    else:
        import getpass

        try:
            _local_user = getpass.getuser()
        except (OSError, KeyError):
            _local_user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
        TEST_DB_PASSWORD = hashlib.sha256(f"astock_local_{_local_user}".encode()).hexdigest()[:24]
    import warnings

    warnings.warn(
        "Using derived test DB password. Set TEST_DB_PASSWORD or CI_PG_PASSWORD env var for production CI.",
        UserWarning,
        stacklevel=2,
    )

_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", f"test_astock_{_xdist_worker}" if _xdist_worker else "test_astock")
if _xdist_worker and _xdist_worker not in TEST_DB_NAME:
    TEST_DB_NAME = f"{TEST_DB_NAME}_{_xdist_worker}"
if not TEST_DB_NAME.startswith("test_"):
    raise ValueError(f"TEST_DB_NAME must start with 'test_' for safety, got: {TEST_DB_NAME!r}")
if not TEST_DB_NAME.replace("_", "").isalnum():
    raise ValueError("TEST_DB_NAME must contain only letters, digits, and underscores")
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
if TEST_DB_HOST not in _ALLOWED_HOSTS:
    raise ValueError(f"TEST_DB_HOST must be one of {_ALLOWED_HOSTS} for safety, got: {TEST_DB_HOST!r}")

# URL-encode password to handle special characters like '@', ':', '/' etc.
_ENCODED_PASSWORD = quote_plus(TEST_DB_PASSWORD) if TEST_DB_PASSWORD else ""
TEST_DB_URL = f"postgresql+asyncpg://{TEST_DB_USER}:{_ENCODED_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"


def pytest_collection_modifyitems(items):
    for item in items:
        if not any(marker.name in ("unit", "integration", "e2e") for marker in item.iter_markers()):
            item.add_marker(pytest.mark.integration)


_test_engine: AsyncEngine | None = None
_test_db_initialized = False


async def _ensure_test_db():
    global _test_db_initialized
    if _test_db_initialized:
        return

    conn = await asyncpg.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        user=TEST_DB_USER,
        password=TEST_DB_PASSWORD,
        database="postgres",
        timeout=5.0,
    )
    try:
        db_name_sql = TEST_DB_NAME.replace('"', '""')
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name_sql}"')
        _test_db_initialized = True
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_engine():
    global _test_engine

    if _test_engine is None:
        await _ensure_test_db()

        # 强制会话时区为 UTC，与生产环境前提对齐（stock_dao.py 注释：PG 时区必须为 UTC）。
        # 否则 SQL `now()` 返回本地时区时间，与 to_utc_for_db() 写入的 UTC tz-naive 比较时偏移，
        # 导致 cooldown / next_retry_at 语义错误（本地 Windows PG 默认 Asia/Shanghai 会失败）。
        from tests._helpers import create_test_engine

        _test_engine = create_test_engine(TEST_DB_URL, echo=False)

        from data.persistence.db_migrator import DatabaseMigrator

        with override_db_url(TEST_DB_URL):
            await DatabaseMigrator.init_db(_test_engine, auto_migrate=True)

    yield _test_engine

    try:
        if _test_engine is not None:
            await _test_engine.dispose()
    finally:
        _test_engine = None

    if _xdist_worker:
        try:
            conn = await asyncpg.connect(
                host=TEST_DB_HOST,
                port=TEST_DB_PORT,
                user=TEST_DB_USER,
                password=TEST_DB_PASSWORD,
                database="postgres",
                timeout=5.0,
            )
            try:
                db_name_sql = TEST_DB_NAME.replace('"', '""')
                await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
            finally:
                await conn.close()
        except (OSError, asyncpg.PostgresError):
            pass


async def _cleanup_mvd_data(test_engine: AsyncEngine):
    """清理 mvd_data 注入的所有数据，保证 setup 幂等且 teardown 完整。

    被 mvd_data fixture 在 setup（插入前）和 teardown（测试后）两次调用：
    - setup 前清理：防止上一个测试残留数据导致 UniqueViolationError（根因修复）
    - teardown 后清理：防止本测试数据影响下一个测试
    """
    async with test_engine.begin() as conn:
        # L4（无 ts_code，全表清理；MVD 仅注入少量数据，安全）
        await conn.execute(delete(MarketNews))
        await conn.execute(delete(ShiborDaily))
        await conn.execute(delete(MacroEconomy))
        # L3
        await conn.execute(delete(TopList).where(TopList.ts_code == "000001.SZ"))
        await conn.execute(delete(BlockTrade).where(BlockTrade.ts_code == "000001.SZ"))
        await conn.execute(delete(StkHoldernumber).where(StkHoldernumber.ts_code == "000001.SZ"))
        await conn.execute(delete(Top10Holders).where(Top10Holders.ts_code == "000001.SZ"))
        await conn.execute(delete(PledgeStat).where(PledgeStat.ts_code == "000001.SZ"))
        # L2
        await conn.execute(delete(Dividend).where(Dividend.ts_code == "000001.SZ"))
        await conn.execute(delete(FinaMainbz).where(FinaMainbz.ts_code == "000001.SZ"))
        await conn.execute(delete(FinaAudit).where(FinaAudit.ts_code == "000001.SZ"))
        await conn.execute(delete(FinancialReports).where(FinancialReports.ts_code == "000001.SZ"))
        # L1
        await conn.execute(delete(NorthboundHolding).where(NorthboundHolding.ts_code == "000001.SZ"))
        await conn.execute(delete(MoneyflowDaily).where(MoneyflowDaily.ts_code == "000001.SZ"))
        await conn.execute(delete(DailyIndicators).where(DailyIndicators.ts_code.in_(["000001.SZ", "600000.SH"])))
        await conn.execute(delete(DailyQuotes).where(DailyQuotes.ts_code.in_(["000001.SZ", "600000.SH"])))
        # L0
        await conn.execute(delete(TradeCal).where(TradeCal.exchange == "SSE"))
        # StockBasic 全表清理（非仅 MVD 股票）：TestDatabaseBase.asyncTearDown 不清理数据，
        # 其末尾测试残留的非 MVD 股票会导致 prompt_validator 的 check_multi_period_data /
        # check_field_exists 随机抽样到无财务数据的股票，injector 返回 False。
        await conn.execute(delete(StockBasic))


@dataclass
class FletTestPage:
    """``flet_test_page`` fixture 返回值：含 page + 集成测试辅助方法（方案 §3.3.3 + Phase 3.0.1 扩展）。

    Phase 3.0.1 扩展：支持声明式组件 ``use_state``/``use_viewmodel`` 真订阅测试。
    - ``wait_for_render``: 基于控件数量轮询（向后兼容）
    - ``wait_for_condition``: 通用条件轮询（支持内容断言，替代仅数量检查）
    - ``find_control``: 深度优先查找满足谓词的控件（用于断言 state 变更后的内容）

    典型用法（state 变更后断言内容）::

        vm.set_current_step(2)  # 触发 state 变更 → Renderer reconcile
        ftp.wait_for_condition(
            lambda: ftp.find_control(lambda c: isinstance(c, ft.Text) and c.value == "Step 2") is not None
        )
    """

    page: ft.Page

    def wait_for_render(self, timeout: float = 2.0, expected_controls: int | None = None) -> None:
        """轮询 ``page.controls`` 长度变化，超时抛 ``TimeoutError``（方案 §3.3.3 M3）。

        Args:
            timeout: 超时秒数，默认 2.0。
            expected_controls: 期望的控件数量；None 表示当前数量 + 1。
        """
        deadline = time.monotonic() + timeout
        initial = len(self.page.controls)
        target = expected_controls if expected_controls is not None else initial + 1
        while time.monotonic() < deadline:
            if len(self.page.controls) >= target:
                return
            time.sleep(0.05)
        raise TimeoutError(f"wait_for_render 超时: 期望 {target} 个控件，实际 {len(self.page.controls)}")

    def wait_for_condition(
        self,
        predicate: Callable[[], bool],
        timeout: float = 2.0,
        interval: float = 0.05,
    ) -> None:
        """通用条件轮询：``predicate`` 返回 True 时返回，超时抛 ``TimeoutError``（Phase 3.0.1）。

        用于声明式组件 ``use_state``/``use_viewmodel`` 触发重渲染后的内容断言。
        ``wait_for_render`` 仅感知控件数量变化，无法感知 state 变更后的内容更新；
        本方法配合 ``find_control`` 可断言"控件内容已反映新 state"。

        Args:
            predicate: 返回 bool 的可调用对象；True 表示条件满足。
            timeout: 超时秒数，默认 2.0。
            interval: 轮询间隔秒数，默认 0.05。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                # predicate 内部访问控件属性可能因渲染未完成抛异常，继续轮询
                pass
            time.sleep(interval)
        raise TimeoutError(f"wait_for_condition 超时: predicate 未在 {timeout}s 内返回 True")

    def find_control(self, predicate: Callable[[ft.BaseControl], bool]) -> ft.BaseControl | None:
        """深度优先查找满足 ``predicate`` 的控件（Phase 3.0.1）。

        用于断言 state 变更后的内容（如 ``lambda c: isinstance(c, ft.Text) and c.value == "new"``）。
        遍历 ``page.controls`` 及其子控件（``content``/``controls`` 属性）。

        Args:
            predicate: 接受 ``ft.BaseControl`` 返回 bool 的可调用对象。

        Returns:
            第一个满足谓词的控件；未找到返回 None。
        """
        return _find_control_recursive(self.page.controls, predicate)


def _find_control_recursive(
    controls: Sequence[ft.BaseControl],
    predicate: Callable[[ft.BaseControl], bool],
) -> ft.BaseControl | None:
    """``find_control`` 的递归实现（模块级，避免 dataclass 方法递归开销）。"""
    for control in controls:
        try:
            if predicate(control):
                return control
        except Exception:
            pass
        # 深度优先：先 content（单控件），再 controls（控件列表）
        content = getattr(control, "content", None)
        if isinstance(content, ft.BaseControl):
            found = _find_control_recursive([content], predicate)
            if found is not None:
                return found
        children = getattr(control, "controls", None)
        if isinstance(children, list):
            found = _find_control_recursive(children, predicate)
            if found is not None:
                return found
    return None


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def flet_test_page():
    """启动完整 Flet app 返回 ``FletTestPage``（page + ``wait_for_render``，方案 §3.3.3）。

    通过 ``ft.run_async`` + ``AppView.FLET_APP_HIDDEN`` 启动隐藏 Flet app，
    在 ``main`` 回调中捕获 page。session 作用域避免每次测试重新启动 app。

    首次运行需下载 Flet bundle（one-time），预热后启动较快。
    spike 验证：``ft.run_async`` + ``FLET_APP_HIDDEN`` 可在 60s 内捕获 page
    （含首次 bundle 下载），``page.add`` 后 ``page.controls`` 立即更新。

    Windows 限制：``ft.run_async`` 的 socket server 不兼容
    ``WindowsSelectorEventLoop``（抛 ``NotImplementedError``），而 pytest-asyncio
    在 Windows 强制 selector policy。因此 Windows 本地无法运行依赖此 fixture
    的测试（probe 测试已加 ``skipif(win32)``）。本地 Windows 验证请用独立 spike：
    ``python -m tests.integration._spike_flet_run_async``。

    Headless Linux 限制：``ft.run_async`` 内部 ``is_linux_server()`` 检测
    ``DISPLAY`` 环境变量——CI ubuntu-latest headless 下返回 True，强制
    ``view=AppView.WEB_BROWSER``（flet app.py L188-190），启动 web server
    等待浏览器连接，无浏览器则 main 回调永不触发，fixture 挂起 120s 超时。
    因此依赖此 fixture 的测试在 CI headless Linux 下也需 skip（probe 测试
    已加 ``skipif(_IS_HEADLESS_LINUX)``）。本地 Linux 需有 X server 或用
    ``xvfb-run``。技术债：CI 完整验证需装 ``xvfb`` + ``flet_desktop``。
    """
    captured: list[ft.Page] = []
    ready = asyncio.Event()

    async def app_main(page: ft.Page) -> None:
        captured.append(page)
        ready.set()

    task = asyncio.create_task(ft.run_async(app_main, view=ft.AppView.FLET_APP_HIDDEN, port=0))
    try:
        await asyncio.wait_for(ready.wait(), timeout=120.0)
        yield FletTestPage(page=captured[0])
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # task 被主动取消是预期行为（fixture teardown），非业务异常吞没
            pass


@pytest_asyncio.fixture
async def mvd_data(test_engine):
    """
    Function 级 MVD：包含 L0 到 L4 的全量最小可行数据集。
    每个测试独立插入并清理，保证绝对的读写隔离安全，
    完美避开与 make_clean_db_fixture 的跨测试冲突。

    同时负责将 CacheManager 单例的引擎指向 TEST_DB_URL，
    使 prompt_validator / Level 3 测试中的 CacheManager() 能读到 MVD 数据。

    幂等保证：setup 插入前先调用 _cleanup_mvd_data 清理可能残留的同主键数据，
    避免 xdist 下上一个测试（如 test_quote_dao 的 clean_db 无 teardown）残留导致
    UniqueViolationError。try/finally 包裹确保 setup 失败时 CacheManager 单例仍被清理。
    """
    from contextlib import ExitStack

    with ExitStack() as url_stack:
        url_stack.enter_context(override_db_url(TEST_DB_URL))

        # --- Setup: 重置 CacheManager 单例并重新创建 ---
        CacheManager._reset_singleton()
        cache = CacheManager()
        try:
            # init_db 幂等：db_schema_ready autouse fixture 已建表，此处仅设置 _schema_initialized
            await cache.init_db(auto_migrate=True)

            # --- Setup: 幂等清理（防止上一个测试残留数据导致主键冲突）---
            await _cleanup_mvd_data(test_engine)

            # --- Setup: 插入 MVD 数据并 commit ---
            async with test_engine.begin() as conn:
                # L0 基础层
                await conn.execute(insert(StockBasic), MVD_STOCK_BASIC)
                await conn.execute(insert(TradeCal), MVD_TRADE_CAL)
                # L1 行情层
                await conn.execute(insert(DailyQuotes), MVD_DAILY_QUOTES)
                await conn.execute(insert(DailyIndicators), MVD_DAILY_INDICATORS)
                await conn.execute(insert(MoneyflowDaily).values(MVD_MONEYFLOW_DAILY))
                await conn.execute(insert(NorthboundHolding).values(MVD_NORTHBOUND_HOLDING))
                # L2 财务层
                await conn.execute(insert(FinancialReports), MVD_FINANCIAL_REPORTS)
                await conn.execute(insert(FinaAudit).values(MVD_FINA_AUDIT))
                await conn.execute(insert(FinaMainbz).values(MVD_FINA_MAINBZ))
                await conn.execute(insert(Dividend).values(MVD_DIVIDEND))
                # L3 辅助层
                await conn.execute(insert(PledgeStat).values(MVD_PLEDGE_STAT))
                await conn.execute(insert(Top10Holders), MVD_TOP10_HOLDERS)
                await conn.execute(insert(StkHoldernumber), MVD_STK_HOLDERNUMBER)
                await conn.execute(insert(BlockTrade).values(MVD_BLOCK_TRADE))
                await conn.execute(insert(TopList).values(MVD_TOP_LIST))
                # L4 宏观层
                await conn.execute(insert(MacroEconomy).values(MVD_MACRO_ECONOMY))
                await conn.execute(insert(ShiborDaily).values(MVD_SHIBOR_DAILY))
                await conn.execute(insert(MarketNews).values(MVD_MARKET_NEWS))

            yield
        finally:
            # --- Teardown: 显式定向删除数据并 commit ---
            try:
                await _cleanup_mvd_data(test_engine)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[mvd_data] teardown data deletion failed: %s", e)
            # --- Teardown: 关闭 CacheManager 引擎并重置单例 ---
            try:
                await cache.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[mvd_data] CacheManager close failed during teardown: %s", e)
            CacheManager._reset_singleton()


@pytest_asyncio.fixture
async def prompt_data_set(mvd_data):
    """
    Prompt 校验器专用 fixture：依赖 mvd_data 确保所有声明表均有数据。
    这是一个纯转发 fixture，本身不做额外操作，仅用于语义化依赖声明。
    """
    yield


@pytest.fixture
def test_db_url_override():
    """统一 override_db_url fixture（P2-4）。

    用法：在测试类或测试函数中声明该 fixture，即可在整个测试作用域内
    覆盖 config.DB_URL / DATABASE_URL / ConfigHandler.get_db_url，
    避免手动保存/恢复全局状态。

    禁止在测试中直接写 `config.DB_URL = ...`，除非测试目标就是验证配置模块本身。
    """
    with override_db_url(TEST_DB_URL):
        yield TEST_DB_URL


@pytest.fixture(autouse=True)
def _reset_thread_pool():
    from utils.thread_pool import ThreadPoolManager

    ThreadPoolManager._reset_singleton()
    yield
    ThreadPoolManager._reset_singleton()


# 使用隔离 DB 的 fixture 列表（与 db_schema_ready 配合跳过 test_engine 依赖）
_ISOLATED_DB_FIXTURES = frozenset(
    {
        "migrated_db_engine",
        "partial_db_engine",
        "empty_status_db_engine",
        "fresh_db_engine",
        "head_db_engine",
        "concurrent_db_engine",
        "corrupted_db_engine",
        "metadata_db_engine",
        "alembic_db_engine",
        "consistency_engine",
        "db_via_init_db",
        "db_via_alembic",
    }
)


@pytest.fixture(autouse=True)
def _test_engine_dep(request):
    """同步解析 test_engine，避免在 async fixture 内调用 getfixturevalue 触发
    ``Runner.run() cannot be called from a running event loop``。

    pytest-asyncio 对 async fixture 的首次 setup 需调用 ``runner.run()``，
    若 ``getfixturevalue`` 在已运行的事件循环（即另一个 async fixture setup）中
    调用，则抛 RuntimeError。将解析移至 sync fixture 可在无事件循环时完成 setup。

    no_db / isolated fixture 场景返回 None，不触发 test_engine 创建。
    """
    if request.node.get_closest_marker("no_db"):
        return None
    if _ISOLATED_DB_FIXTURES & set(request.fixturenames):
        return None
    return request.getfixturevalue("test_engine")


@pytest_asyncio.fixture(autouse=True)
async def db_schema_ready(request, _test_engine_dep):
    """Ensure database schema is ready before each test.

    Skip for tests that use isolated database fixtures (e.g., migrated_db_engine,
    metadata_db_engine, etc.) to avoid conflicting with their own database setup.

    Skip for tests marked ``no_db`` (e.g., flet_test_page probe) — 这类测试
    不需要 DB，不应触发 ``test_engine`` 创建。

    test_engine 通过同步 fixture ``_test_engine_dep`` 解析，避免在 async 上下文
    中调用 ``getfixturevalue`` 触发 ``Runner.run()`` 嵌套事件循环错误。
    """
    if _test_engine_dep is not None:
        from data.persistence.db_migrator import DatabaseMigrator

        with override_db_url(TEST_DB_URL):
            await DatabaseMigrator.init_db(_test_engine_dep, auto_migrate=True)

    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def cleanup_singletons_session():
    """Ensure all registered singletons with async close() are cleaned up at session teardown.

    This avoids synchronous connection pool disposal warnings (MissingGreenlet) at exit.
    """
    yield
    import inspect
    import logging
    from utils.singleton_registry import _registry, reset_all_singletons

    logger = logging.getLogger(__name__)

    # Clean up singletons in reverse order of registration
    for cls in reversed(list(_registry)):
        if hasattr(cls, "_instance"):
            inst = cls._instance
            if inst is not None:
                if hasattr(inst, "close") and inspect.iscoroutinefunction(inst.close):
                    try:
                        await inst.close()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(
                            "Failed to async close singleton %s during session teardown: %s", cls.__name__, e
                        )
    reset_all_singletons()
