import asyncio
import json
import logging
import os
import random
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


@pytest.fixture(scope="session")
async def e2e_playwright():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        yield p


@pytest.fixture(scope="session")
async def e2e_browser(e2e_playwright):
    browser = await e2e_playwright.chromium.launch(channel=BROWSER_CHANNEL, headless=True)
    yield browser
    await browser.close()


@pytest.fixture(scope="session")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsProactorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def pytest_collection_modifyitems(items):
    for item in items:
        if not any(marker.name in ("unit", "integration", "e2e") for marker in item.iter_markers()):
            item.add_marker(pytest.mark.e2e)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


def _spawn(tmp_path_factory, config: dict, env_overrides: dict):
    cfg_dir = tmp_path_factory.mktemp("e2e_cfg")
    cfg_file = cfg_dir / "user_settings.json"
    cfg_file.write_text(json.dumps(config), encoding="utf-8")
    proc, url = start_flet_app(cfg_file, env_overrides)
    return proc, url


def _terminate(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()


class AppServer:
    def __init__(self, proc, url: str):
        self.proc = proc
        self.url = url

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
    page.on("console", lambda msg: logger.debug("[BROWSER CONSOLE] %s: %s", msg.type, msg.text))
    page.on("pageerror", lambda err: logger.debug("[BROWSER ERROR] %s", err))
    fp = FletPage(page, timeout_multiplier=TIMEOUT_MULTIPLIER)

    # CRITICAL WORKAROUND for E2E Flakiness:
    # Flet's web app downloads canvaskit.js and canvaskit.wasm from unpkg.com on startup.
    # In CI and sometimes local environments, unpkg.com can be extremely slow or timeout,
    # causing the entire Playwright test to fail with a TimeoutError waiting for the page to load.
    # To fix this, we intercept network requests and serve the canvaskit files directly from
    # the local 'mock_assets' folder. We also abort font requests to speed up test execution.
    # [PITFALL FIX] 拦截并缓存 CanvasKit WASM 文件加载
    # 坑点：Flet (Flutter Web) 启动时会动态下载 canvaskit.js 和 canvaskit.wasm。
    # Playwright E2E 测试如果在 CI 环境或者无头模式下，由于网络波动，加载这两个文件极慢。
    # 更糟糕的是，如果加载超时，页面渲染会直接卡死在白屏，导致所有元素（如标题、按钮）等待超时 (TimeoutError)。
    # 解决方案：我们在 e2e 启动时拦截对 canvaskit 文件的请求，并使用本地预下载的版本进行响应。
    # 这将原本需要数十秒的网络请求压缩至几毫秒，从而稳定保障 Flet UI 的秒级加载。
    async def intercept_canvaskit(route, request):
        url = request.url
        if "fonts.googleapis.com" in url:
            await route.abort()
            return
        if "fonts.gstatic.com" in url:
            await route.abort()
            return
        if "canvaskit" in url:
            filename = url.split("/")[-1]
            from pathlib import Path

            local_path = Path(__file__).resolve().parent / "mock_assets" / "canvaskit" / filename
            if local_path.exists():
                content_type = "application/wasm" if filename.endswith(".wasm") else "application/javascript"
                await route.fulfill(status=200, content_type=content_type, body=local_path.read_bytes())
                return
        await route.continue_()

    await page.route("**/*", intercept_canvaskit)

    try:
        await fp.open(app.url)
    except Exception:
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
            await page.wait_for_timeout(2000)
            error_text = I18n.get("error_db_init_failed")
            if await fp.has_text(error_text):
                log_contents = ""
                try:
                    log_path = Path("logs/e2e-flet-app.log")
                    if log_path.exists():
                        log_contents = log_path.read_text(encoding="utf-8")
                except Exception:
                    pass
                raise RuntimeError(f"Flet app shows DB initialization error UI.\nApp Log:\n{log_contents}")
        except RuntimeError:
            await context.close()
            raise
        except Exception:  # noqa: BLE001
            pass  # Don't fail if the check itself fails

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
            today_str = date.today().isoformat()

            # stock_basic
            await conn.execute(
                """
                INSERT INTO stock_basic (ts_code, symbol, name, area, industry, market, list_date, list_status)
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
                cal_rows.append((d.isoformat(), "SSE", is_open, pretrade.isoformat()))
                d += timedelta(days=1)
            await conn.executemany(
                "INSERT INTO trade_cal (cal_date, exchange, is_open, pretrade_date) VALUES ($1::date, $2, $3, $4::date)",
                cal_rows,
            )

            # daily_quotes — 平安银行 + 贵州茅台 × 60 个交易日
            rng = random.Random(42)
            quote_rows = []
            indicator_rows = []
            for i, td in enumerate(trade_dates):
                # 平安银行：pct_chg 3.0~6.0, turnover_rate 3.5~6.0（通过 VolumeBreakoutStrategy 过滤）
                pa_close = 12.0 + i * 0.05
                pa_pct_chg = round(rng.uniform(3.0, 6.0), 4)
                pa_pre_close = round(pa_close / (1 + pa_pct_chg / 100), 4)
                pa_change = round(pa_close - pa_pre_close, 4)
                pa_vol = rng.randint(500000, 1000000)
                pa_amount = round(pa_close * pa_vol / 100, 4)
                quote_rows.append(
                    (
                        "000001.SZ",
                        td.isoformat(),
                        round(pa_pre_close - 0.1, 4),
                        round(pa_close + 0.1, 4),
                        round(pa_pre_close - 0.2, 4),
                        pa_close,
                        pa_pre_close,
                        pa_change,
                        pa_pct_chg,
                        pa_vol,
                        pa_amount,
                    )
                )
                # 贵州茅台：pct_chg 0.2~1.5（不通过过滤）
                mt_close = 1800.0 + i * 0.3
                mt_pct_chg = round(rng.uniform(0.2, 1.5), 4)
                mt_pre_close = round(mt_close / (1 + mt_pct_chg / 100), 4)
                mt_change = round(mt_close - mt_pre_close, 4)
                mt_vol = rng.randint(20000, 40000)
                mt_amount = round(mt_close * mt_vol / 100, 4)
                quote_rows.append(
                    (
                        "600519.SH",
                        td.isoformat(),
                        round(mt_pre_close - 2, 4),
                        round(mt_close + 2, 4),
                        round(mt_pre_close - 5, 4),
                        mt_close,
                        mt_pre_close,
                        mt_change,
                        mt_pct_chg,
                        mt_vol,
                        mt_amount,
                    )
                )

                # daily_indicators
                pa_turnover = round(rng.uniform(3.5, 6.0), 4)
                indicator_rows.append(
                    (
                        "000001.SZ",
                        td.isoformat(),
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
                        td.isoformat(),
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
                """
                INSERT INTO daily_quotes
                    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                VALUES ($1, $2::date, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                quote_rows,
            )
            await conn.executemany(
                """
                INSERT INTO daily_indicators
                    (ts_code, trade_date, pe_ttm, pb, ps_ttm, total_mv, circ_mv, turnover_rate, turnover_rate_f, volume_ratio)
                VALUES ($1, $2::date, $3, $4, $5, $6, $7, $8, $9, $10)
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
                        td.isoformat(),
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
                """
                INSERT INTO index_daily
                    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                VALUES ($1, $2::date, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                index_rows,
            )

            # sync_status — 质量门控评级依据
            sync_rows = [
                ("daily_quotes", today_str, today_str, 120, "success", "success", 0),
                ("daily_indicators", today_str, today_str, 120, "success", "success", 0),
                ("financial_reports", today_str, today_str, 0, "success", "empty", 0),
                ("stock_basic", today_str, today_str, 2, "success", "success", 0),
                ("trade_cal", today_str, today_str, len(cal_rows), "success", "success", 0),
                ("index_daily", today_str, today_str, 60, "success", "success", 0),
            ]
            await conn.executemany(
                """
                INSERT INTO sync_status
                    (table_name, last_sync_date, last_data_date, record_count, status, last_result_status, error_count)
                VALUES ($1, $2::date, $3::date, $4, $5, $6, $7)
                """,
                sync_rows,
            )

            # suspend_d — 空表（无停牌记录，确保 is_tradable=TRUE）
            # financial_reports — 空表（volume_breakout 不依赖此数据）

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
    proc, url = _spawn(
        tmp_path_factory,
        config={
            "onboarding_complete": True,
            "locale": "zh",
            # CRITICAL WORKAROUND:
            # We pass db_host="" here to prevent Pydantic from filling in the default value "127.0.0.1".
            # If db_host is populated, ConfigHandler.get_db_url() assumes the user has configured
            # a database and ignores the DATABASE_URL environment variable, attempting instead
            # to rebuild the URL from db_host/db_user/astock and the password from keyring/env.
            # Since CI_PG_PASSWORD doesn't automatically map to DB_PASSWORD in Python, the password
            # evaluates to empty, and ConfigHandler produces an invalid connection string,
            # leading to a mysterious 'password authentication failed' error.
            # Passing db_host="" forces ConfigHandler to fallback to the explicitly provided
            # DATABASE_URL from the E2E environment overrides.
            "db_host": "",
        },
        env_overrides={
            "TS_TOKEN": "e2e-dummy-token",
            "AI_API_KEY": "e2e-dummy-key",
            "DATABASE_URL": TEST_DATABASE_URL,
        },
    )
    app = AppServer(proc, url)
    yield app
    _terminate(proc)


@pytest.fixture(scope="session")
def wizard_app(tmp_path_factory):
    proc, url = _spawn(
        tmp_path_factory,
        config={"locale": "zh"},
        env_overrides={"DATABASE_URL": TEST_DATABASE_URL},
    )
    app = AppServer(proc, url)
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
