import asyncio
import json
import logging
import os
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.e2e.helpers.app_launcher import start_flet_app
from tests.e2e.helpers.flet_page import FletPage

from tests.conftest import _get_test_db_url

logger = logging.getLogger(__name__)

TEST_DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get(
    "E2E_DATABASE_URL",
    _get_test_db_url(),
)
BROWSER_CHANNEL = os.environ.get("E2E_BROWSER_CHANNEL", "chromium")
if not BROWSER_CHANNEL:
    BROWSER_CHANNEL = None

ARTIFACT_DIR = Path(os.environ.get("E2E_ARTIFACT_DIR", "e2e-artifacts"))

TIMEOUT_MULTIPLIER = float(os.environ.get("E2E_TIMEOUT_MULTIPLIER", "1.0"))

from core.i18n import I18n

I18n.initialize("zh")

# A1: 种子数据阈值从被测策略参数派生，消除隐式契约
# 策略改阈值时种子自动跟随，契约由代码强制而非注释维持
from strategies.market import VolumeBreakoutStrategy

_vb_params = {p["name"]: p["default"] for p in VolumeBreakoutStrategy().get_parameters()}
# 必过样本（平安银行）：显式高于 VolumeBreakoutStrategy 阈值
_PA_PCT_CHG_RANGE = (_vb_params["pct_chg_min"] + 1.0, _vb_params["pct_chg_max"] - 1.0)
_PA_TURNOVER_RANGE = (
    _vb_params["turnover_min"] + 0.5,
    _vb_params["turnover_min"] + 3.0,
)
# 必不过样本（贵州茅台）：显式低于 pct_chg_min 阈值
_MT_PCT_CHG_RANGE = (
    max(0.0, _vb_params["pct_chg_min"] - 1.8),
    max(0.0, _vb_params["pct_chg_min"] - 0.5),
)

# A8: 裸 SQL 列清单常量化 — 与 ORM 模型对齐校验
# INSERT 仅写入业务列（省略 updated_at/created_at 等 server_default 列及可空列），
# 故常量是 ORM 列的子集；此处断言子集关系以捕获 schema 漂移/列名拼写错误。
from data.persistence.models import (
    DailyIndicators,
    DailyQuotes,
    IndexDaily,
    StockBasic,
    SyncStatus,
    TradeCal,
)

STOCK_BASIC_COLUMNS = (
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "market",
    "list_date",
    "list_status",
)
TRADE_CAL_COLUMNS = ("cal_date", "exchange", "is_open", "pretrade_date")
DAILY_QUOTES_COLUMNS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "adj_factor",
)
DAILY_INDICATORS_COLUMNS = (
    "ts_code",
    "trade_date",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "total_mv",
    "circ_mv",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
)
INDEX_DAILY_COLUMNS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
SYNC_STATUS_COLUMNS = (
    "table_name",
    "last_sync_date",
    "last_data_date",
    "record_count",
    "status",
    "last_result_status",
    "error_count",
)


def _assert_columns_subset(name: str, cols: tuple[str, ...], orm_cls: type) -> None:
    orm_cols = {c.name for c in orm_cls.__table__.columns}
    extra = set(cols) - orm_cols
    assert not extra, f"{name} 含 {orm_cls.__name__} ORM 不存在的列: {extra}"


_assert_columns_subset("STOCK_BASIC_COLUMNS", STOCK_BASIC_COLUMNS, StockBasic)
_assert_columns_subset("TRADE_CAL_COLUMNS", TRADE_CAL_COLUMNS, TradeCal)
_assert_columns_subset("DAILY_QUOTES_COLUMNS", DAILY_QUOTES_COLUMNS, DailyQuotes)
_assert_columns_subset("DAILY_INDICATORS_COLUMNS", DAILY_INDICATORS_COLUMNS, DailyIndicators)
_assert_columns_subset("INDEX_DAILY_COLUMNS", INDEX_DAILY_COLUMNS, IndexDaily)
_assert_columns_subset("SYNC_STATUS_COLUMNS", SYNC_STATUS_COLUMNS, SyncStatus)


@pytest.fixture(scope="session")
async def e2e_playwright():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        yield p


@pytest.fixture(scope="session")
async def e2e_browser(e2e_playwright):
    # 启动期断言 canvaskit.wasm 本地存在（route handler 离线化依赖此文件）
    wasm_path = Path(__file__).resolve().parent / "mock_assets" / "canvaskit" / "canvaskit.wasm"
    if not wasm_path.exists():
        raise RuntimeError(
            f"canvaskit.wasm not found at {wasm_path}. "
            "E2E 离线化依赖此文件，请从 https://unpkg.com/canvaskit-wasm@latest/bin/canvaskit.wasm "
            "下载并放置到 tests/e2e/mock_assets/canvaskit/ 目录"
        )
    browser = await e2e_playwright.chromium.launch(
        channel=BROWSER_CHANNEL, headless=os.environ.get("E2E_HEADED", "0") != "1"
    )
    yield browser
    await browser.close()


@pytest.fixture(scope="session")
def event_loop_policy():
    # 覆盖 tests/conftest.py 中的 event_loop_policy（使用 WindowsSelectorEventLoopPolicy）。
    # E2E 测试需要 WindowsProactorEventLoopPolicy：Flet 子进程启动 (subprocess.Popen) 与
    # Playwright 异步驱动在 Windows 上依赖 Proactor 事件循环，Selector 不支持子进程。
    if sys.platform == "win32":
        return asyncio.WindowsProactorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


def _spawn(tmp_path_factory, config: dict, env_overrides: dict) -> tuple:
    cfg_dir = tmp_path_factory.mktemp("e2e_cfg")
    cfg_file = cfg_dir / "user_settings.json"
    cfg_file.write_text(json.dumps(config), encoding="utf-8")
    proc, url = start_flet_app(cfg_file, env_overrides)
    return proc, url, cfg_file


def _terminate(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("[E2E] Flet app PID %s 未能在 kill 后回收", proc.pid)


class AppServer:
    def __init__(self, proc, url: str, config_file: Path | None = None):
        self.proc = proc
        self.url = url
        self.config_file = config_file

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def assert_alive(self) -> None:
        if not self.is_alive():
            retcode = self.proc.returncode
            raise RuntimeError(
                f"Flet app process (PID {self.proc.pid}) has exited with code {retcode}. "
                f"URL was {self.url}. Check logs/e2e-flet-app.log for details."
            )


async def _make_page(browser, app: AppServer, request, *, check_db_error: bool = False) -> FletPage:
    app.assert_alive()

    context = await browser.new_context(viewport={"width": 1400, "height": 900})
    await context.tracing.start(screenshots=True, snapshots=True)
    page = await context.new_page()
    page.on(
        "console",
        lambda msg: logger.debug("[BROWSER CONSOLE] %s: %s", msg.type, msg.text),
    )
    page.on("pageerror", lambda err: logger.debug("[BROWSER ERROR] %s", err))
    fp = FletPage(page, timeout_multiplier=TIMEOUT_MULTIPLIER)

    # CRITICAL WORKAROUND for E2E Flakiness:
    # Flet's web app downloads canvaskit.js and canvaskit.wasm from unpkg.com on startup.
    # In CI and sometimes local environments, unpkg.com can be extremely slow or timeout,
    # causing the entire Playwright test to fail with a TimeoutError waiting for the page to load.
    # To fix this, we intercept canvaskit requests and serve them from local mock_assets.
    # Other external requests (fonts, icons, etc.) are aborted to force offline mode.
    # [PITFALL FIX] 拦截外部资源请求，强制离线化 E2E 测试
    # 坑点：Flet (Flutter Web) 启动时会动态下载 canvaskit.js 和 canvaskit.wasm。
    # Playwright E2E 测试如果在 CI 环境或者无头模式下，由于网络波动，加载这两个文件极慢。
    # 更糟糕的是，如果加载超时，页面渲染会直接卡死在白屏，导致所有元素（如标题、按钮）等待超时 (TimeoutError)。
    # 解决方案：拦截外部资源请求，canvaskit 命中本地缓存则 fulfill，其余外部请求强制 abort（离线）。
    # 内部请求（Flet app 本身、data/blob URI）继续放行。
    async def intercept_external(route, request):
        url = request.url
        # 内部请求（Flet app 本身、data/blob URI）直接放行
        if url.startswith(("http://localhost", "http://127.0.0.1", "data:", "blob:")):
            await route.continue_()
            return
        # 外部资源：仅 canvaskit 命中本地缓存
        if "canvaskit" in url:
            filename = url.split("/")[-1]
            local_path = Path(__file__).resolve().parent / "mock_assets" / "canvaskit" / filename
            if local_path.exists():
                content_type = "application/wasm" if filename.endswith(".wasm") else "application/javascript"
                await route.fulfill(status=200, content_type=content_type, path=str(local_path))
                return
        # 未命中本地缓存的外部请求：强制离线
        await route.abort()

    await page.route("**/*", intercept_external)

    try:
        await fp.open(app.url)
    except Exception as exc:
        logger.warning("[E2E] fp.open(%s) failed: %s", app.url, exc)
        if not app.is_alive():
            logger.error(
                "[E2E] Flet app process died during page open. PID %d, exit code %s",
                app.proc.pid,
                app.proc.returncode,
            )
        await context.close()
        raise

    # Fail-fast: detect error UI (e.g. "数据库初始化失败") instead of
    # waiting for Playwright's 45s timeout on subsequent interactions.
    # Only enabled for flet_app (which calls initialize_services).
    if check_db_error:
        try:
            error_text = I18n.get("error_db_init_failed")
            # 轮询等待错误 UI 出现（fail-fast，替代固定 2s sleep）
            for _ in range(10):  # 最多 2s，每 200ms 检查一次
                if await fp.has_text(error_text):
                    # R9: 不内联日志原文（可能含 DB 连接串/密码），只引用已脱敏的日志工件路径
                    raise RuntimeError(
                        "Flet app shows DB initialization error UI. See sanitized log artifact: logs/e2e-flet-app.log"
                    )
                await page.wait_for_timeout(200)
        except RuntimeError:
            await context.close()
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("[E2E] DB error UI check failed (non-fatal): %s", exc)

    fp.bind_context((None, None, context, page, request))
    return fp


async def _teardown_page(fp: FletPage) -> None:
    pw_context = fp.get_context()
    if not pw_context:
        return
    _, _, context, page, request = pw_context
    failed = any(
        getattr(request.node, f"rep_{when}", None) and getattr(request.node, f"rep_{when}").failed
        for when in ("setup", "call")
    )
    try:
        if failed:
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            name = request.node.name
            await page.screenshot(path=str(ARTIFACT_DIR / f"{name}.png"))
            await context.tracing.stop(path=str(ARTIFACT_DIR / f"{name}-trace.zip"))
        else:
            await context.tracing.stop()
    finally:
        await context.close()


def _parse_asyncpg_dsn(sqlalchemy_url: str) -> str:
    """将 SQLAlchemy asyncpg DSN 转换为 asyncpg 原生 DSN。

    postgresql+asyncpg://user:pass@host:port/db → postgresql://user:pass@host:port/db
    """
    return sqlalchemy_url.replace("+asyncpg", "")


def _generate_trade_dates(n_days: int = 60) -> list[date]:
    """生成最近 n_days 个交易日（排除周末），从最新到最旧排列。"""
    today = date.today()
    trading: list[date] = []
    current = today
    while len(trading) < n_days:
        if current.weekday() < 5:  # 周一至周五
            trading.append(current)
        current -= timedelta(days=1)
    trading.reverse()
    return trading


async def _seed_e2e_data() -> None:
    """向测试数据库播种 E2E 所需的基准数据。"""
    import asyncpg
    from sqlalchemy.ext.asyncio import create_async_engine
    from data.persistence.db_migrator import DatabaseMigrator
    from data.persistence.db_url_override import override_db_url

    # Ensure tables are migrated before seeding
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    try:
        with override_db_url(TEST_DATABASE_URL):
            await DatabaseMigrator.init_db(engine, auto_migrate=True)
    except Exception as e:
        # R9: 脱敏后抛出，用 from None 显式抑制异常链，防原始异常（可能含 DB 连接串）泄漏进 junit XML
        logger.warning("[E2E] DB migration failed during seed: %s", type(e).__name__)
        from utils.sanitizers import DataSanitizer

        raise RuntimeError(f"E2E seed aborted: DB migration failed: {DataSanitizer.sanitize_error(e)}") from None
    finally:
        await engine.dispose()

    dsn = _parse_asyncpg_dsn(TEST_DATABASE_URL)
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # 清理
            await conn.execute(
                """
                TRUNCATE TABLE
                    daily_quotes,
                    daily_indicators,
                    financial_reports,
                    suspend_d,
                    index_daily,
                    sync_status,
                    stock_basic,
                    trade_cal
                CASCADE
                """
            )

            trade_dates = _generate_trade_dates(60)
            today = date.today()

            # stock_basic
            await conn.execute(
                f"""
                INSERT INTO stock_basic ({", ".join(STOCK_BASIC_COLUMNS)})
                VALUES
                    ($1, $2, $3, $4, $5, $6, $7, $8),
                    ($9, $10, $11, $12, $13, $14, $15, $16)
                """,
                "000001.SZ",
                "000001",
                "平安银行",
                "深圳",
                "银行",
                "主板",
                date(1991, 4, 3),
                "L",
                "600519.SH",
                "600519",
                "贵州茅台",
                "贵州",
                "白酒",
                "主板",
                date(2001, 8, 27),
                "L",
            )

            # trade_cal — 最近 90 天内所有日期，工作日 is_open=1
            cal_rows = []
            d = date.today() - timedelta(days=90)
            end = date.today()
            while d <= end:
                is_open = 1 if d.weekday() < 5 else 0
                pretrade = d - timedelta(days=3) if d.weekday() == 0 else d - timedelta(days=1)
                cal_rows.append((d, "SSE", is_open, pretrade))
                d += timedelta(days=1)
            await conn.executemany(
                f"INSERT INTO trade_cal ({', '.join(TRADE_CAL_COLUMNS)}) VALUES ($1, $2, $3, $4)",
                cal_rows,
            )

            # daily_quotes — 平安银行 + 贵州茅台 × 60 个交易日
            rng = random.Random(42)
            quote_rows = []
            indicator_rows = []
            for i, td in enumerate(trade_dates):
                # 平安银行：pct_chg/turnover 显式高于 VolumeBreakoutStrategy 阈值（通过过滤）
                pa_close = 12.0 + i * 0.05
                pa_pct_chg = round(rng.uniform(*_PA_PCT_CHG_RANGE), 4)
                pa_pre_close = round(pa_close / (1 + pa_pct_chg / 100), 4)
                pa_change = round(pa_close - pa_pre_close, 4)
                pa_vol = rng.randint(500000, 1000000)
                pa_amount = round(pa_close * pa_vol / 100, 4)
                quote_rows.append(
                    (
                        "000001.SZ",
                        td,
                        round(pa_pre_close - 0.1, 4),
                        round(pa_close + 0.1, 4),
                        round(pa_pre_close - 0.2, 4),
                        pa_close,
                        pa_pre_close,
                        pa_change,
                        pa_pct_chg,
                        pa_vol,
                        pa_amount,
                        1.0,
                    )
                )
                # 贵州茅台：pct_chg 显式低于 pct_chg_min 阈值（不通过过滤）
                mt_close = 1800.0 + i * 0.3
                mt_pct_chg = round(rng.uniform(*_MT_PCT_CHG_RANGE), 4)
                mt_pre_close = round(mt_close / (1 + mt_pct_chg / 100), 4)
                mt_change = round(mt_close - mt_pre_close, 4)
                mt_vol = rng.randint(20000, 40000)
                mt_amount = round(mt_close * mt_vol / 100, 4)
                quote_rows.append(
                    (
                        "600519.SH",
                        td,
                        round(mt_pre_close - 2, 4),
                        round(mt_close + 2, 4),
                        round(mt_pre_close - 5, 4),
                        mt_close,
                        mt_pre_close,
                        mt_change,
                        mt_pct_chg,
                        mt_vol,
                        mt_amount,
                        1.0,
                    )
                )

                # daily_indicators
                pa_turnover = round(rng.uniform(*_PA_TURNOVER_RANGE), 4)
                indicator_rows.append(
                    (
                        "000001.SZ",
                        td,
                        round(rng.uniform(5.0, 8.0), 4),
                        round(rng.uniform(0.5, 0.8), 4),
                        round(rng.uniform(1.5, 2.0), 4),
                        25000000 + i * 10000,
                        25000000 + i * 10000,
                        pa_turnover,
                        round(pa_turnover * 1.1, 4),
                        round(rng.uniform(0.8, 1.5), 4),
                    )
                )
                mt_turnover = round(rng.uniform(0.2, 0.8), 4)
                indicator_rows.append(
                    (
                        "600519.SH",
                        td,
                        round(rng.uniform(30.0, 40.0), 4),
                        round(rng.uniform(10.0, 14.0), 4),
                        round(rng.uniform(13.0, 17.0), 4),
                        220000000 + i * 50000,
                        220000000 + i * 50000,
                        mt_turnover,
                        round(mt_turnover * 1.1, 4),
                        round(rng.uniform(0.5, 1.0), 4),
                    )
                )

            await conn.executemany(
                f"""
                INSERT INTO daily_quotes
                    ({", ".join(DAILY_QUOTES_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                quote_rows,
            )
            await conn.executemany(
                f"""
                INSERT INTO daily_indicators
                    ({", ".join(DAILY_INDICATORS_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                indicator_rows,
            )

            # index_daily — 沪深300 × 60 个交易日
            index_rows = []
            idx_close = 3900.0
            for td in trade_dates:
                idx_pct = round(rng.uniform(-0.5, 0.5), 4)
                idx_pre_close = round(idx_close, 4)
                idx_close = round(idx_close * (1 + idx_pct / 100), 4)
                idx_change = round(idx_close - idx_pre_close, 4)
                index_rows.append(
                    (
                        "000300.SH",
                        td,
                        round(idx_close - 5, 4),
                        round(idx_close + 10, 4),
                        round(idx_close - 10, 4),
                        round(idx_close, 4),
                        idx_pre_close,
                        idx_change,
                        idx_pct,
                        rng.randint(50000000, 80000000),
                        round(rng.uniform(3000000, 5000000), 4),
                    )
                )
            await conn.executemany(
                f"""
                INSERT INTO index_daily
                    ({", ".join(INDEX_DAILY_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                index_rows,
            )

            # sync_status — 质量门控评级依据
            sync_rows = [
                ("daily_quotes", today, today, 120, "success", "success", 0),
                ("daily_indicators", today, today, 120, "success", "success", 0),
                ("financial_reports", today, today, 0, "success", "empty", 0),
                ("stock_basic", today, today, 2, "success", "success", 0),
                ("trade_cal", today, today, len(cal_rows), "success", "success", 0),
                ("index_daily", today, today, 60, "success", "success", 0),
            ]
            await conn.executemany(
                f"""
                INSERT INTO sync_status
                    ({", ".join(SYNC_STATUS_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                sync_rows,
            )

            # suspend_d — 空表（无停牌记录，确保 is_tradable=TRUE）
            # financial_reports — 空表（volume_breakout 不依赖此数据）

            # A8: 关键表计数断言 — 期望计数从播种数据派生，计数不符抛 RuntimeError
            # 表名为硬编码字面量（非用户输入），无 SQL 注入风险
            _expected_counts = {
                "stock_basic": 2,
                "daily_quotes": len(quote_rows),
                "daily_indicators": len(indicator_rows),
                "trade_cal": len(cal_rows),
                "index_daily": len(index_rows),
            }
            for _table, _expected in _expected_counts.items():
                _actual = await conn.fetchval(f"SELECT count(*) FROM {_table}")
                if _actual != _expected:
                    raise RuntimeError(f"E2E seed 计数不符: table={_table}, expected={_expected}, actual={_actual}")

        logger.info("[E2E Seeding] Database seeded successfully.")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def seed_e2e_data():
    """Session 级数据库播种：在所有 E2E 测试之前注入基准数据。"""
    await _seed_e2e_data()
    yield


@pytest.fixture(scope="session")
def flet_app(tmp_path_factory, seed_e2e_data):
    """
    [PITFALL_WARNING] 全局 Session 级 Flet App 与 状态污染 (Ripple Effect)

    坑点：整个 E2E 测试套件只会在最开始启动 *一次* Flet 应用进程（为了节省启动时间）。
    因此，所有的测试用例都在 **同一个正在运行的 App 实例** 上进行。

    影响：如果你在测试 A 中修改了全局状态（例如：切换了语言、主题、甚至缓存了某个页面的数据），
    这些状态修改会 **持久化并泄漏** 到测试 B、测试 C 中！

    典型案例：
    测试 A 切换语言到英文后失败退出，导致测试 B 寻找中文 "选股" 时全部抛出 Timeout 超时错误。

    应对策略：
    1. 【必须】所有修改了全局状态的测试用例，必须使用 `try...finally` 块确保将状态恢复到基准线！
    2. 对于语言切换等破坏性极强的状态，建议放在测试套件的末尾执行（通过文件命名或 pytest 排序）。
    3. 如果遇到莫名其妙的下游测试全部超时，请首先检查上一个执行的用例是否引发了状态污染。
    """
    proc, url, cfg_file = _spawn(
        tmp_path_factory,
        config={
            "onboarding_complete": True,
            "locale": "zh",
        },
        env_overrides={
            "TS_TOKEN": "e2e-dummy-token",
            "AI_API_KEY": "e2e-dummy-key",
            "DATABASE_URL": TEST_DATABASE_URL,
        },
    )
    app = AppServer(proc, url, cfg_file)
    yield app
    _terminate(proc)


@pytest.fixture(scope="session")
def wizard_app(tmp_path_factory):
    proc, url, cfg_file = _spawn(
        tmp_path_factory,
        config={"locale": "zh"},
        env_overrides={"DATABASE_URL": TEST_DATABASE_URL},
    )
    app = AppServer(proc, url, cfg_file)
    yield app
    _terminate(proc)


@pytest.fixture
async def e2e_page(e2e_browser, flet_app: AppServer, request):
    fp = await _make_page(e2e_browser, flet_app, request, check_db_error=True)
    yield fp
    await _teardown_page(fp)


@pytest.fixture
async def wizard_page(e2e_browser, wizard_app: AppServer, request):
    fp = await _make_page(e2e_browser, wizard_app, request)
    yield fp
    await _teardown_page(fp)


@pytest.fixture(autouse=True)
def pristine_config(request):
    """配置快照/还原：仅对标记 ``mutates_config`` 的用例激活。

    用例前快照当前配置文件 + 测试进程 I18n locale，用例后还原。
    覆盖主题/语言两个维度（配置文件整体快照，含 DB 等所有键）。

    注意：Flet app 是 session 级单进程，其内存中的 ConfigHandler 缓存无法从测试进程
    直接清空。本 fixture 还原磁盘配置文件，确保下一次 app 重启读到干净配置；
    同时还原测试进程的 I18n locale，避免后续用例的断言字符串语言错乱。
    """
    marker = request.node.get_closest_marker("mutates_config")
    if marker is None:
        yield
        return

    # 根据用例请求的 page fixture 推断对应的 app 配置文件
    config_file: Path | None = None
    if "e2e_page" in request.fixturenames:
        app: AppServer = request.getfixturevalue("flet_app")
        config_file = app.config_file
    elif "wizard_page" in request.fixturenames:
        app = request.getfixturevalue("wizard_app")
        config_file = app.config_file

    # 快照磁盘配置文件
    file_snapshot: str | None = None
    if config_file and config_file.exists():
        file_snapshot = config_file.read_text(encoding="utf-8")

    # 快照测试进程 I18n locale
    locale_snapshot = I18n.current_locale()

    yield

    # 还原磁盘配置文件
    if config_file and file_snapshot is not None:
        try:
            config_file.write_text(file_snapshot, encoding="utf-8")
        except OSError as e:
            logger.warning("[pristine_config] 还原配置文件失败 %s: %s", config_file, e)

    # 还原测试进程 I18n locale
    if I18n.current_locale() != locale_snapshot:
        try:
            I18n.set_locale(locale_snapshot)
        except Exception as e:  # noqa: BLE001
            logger.warning("[pristine_config] 还原 I18n locale 到 %s 失败: %s", locale_snapshot, e)
