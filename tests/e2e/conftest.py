# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import json
import logging
import os
import random
import subprocess
import sys
import typing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import unquote_plus, urlparse

import pytest
import pytest_asyncio

# Session 级 keyring mock — 隔离 E2E 测试对宿主机 keyring 的污染
# 必须在任何可能 import keyring 的项目模块之前生效（参考 tests/conftest.py 的 _MOCK_KEYRING）
# utils/config_handler.py 在模块顶层 import keyring，而本文件通过 strategies/data 间接导入它，
# 故必须在项目 import 之前完成 sys.modules["keyring"] 替换
_E2E_KEYRING_STORE: dict[str, str] = {}
_E2E_ORIGINAL_KEYRING = sys.modules.get("keyring")


def _create_e2e_mock_keyring() -> MagicMock:
    """创建内存 mock keyring，提供 get/set/delete_password 接口。"""

    def get_password(service_name: str, username: str) -> str | None:
        return _E2E_KEYRING_STORE.get(f"{service_name}:{username}")

    def set_password(service_name: str, username: str, password: str) -> None:
        _E2E_KEYRING_STORE[f"{service_name}:{username}"] = password

    def delete_password(service_name: str, username: str) -> None:
        _E2E_KEYRING_STORE.pop(f"{service_name}:{username}", None)

    mock_kr = MagicMock()
    mock_kr.get_password = get_password
    mock_kr.set_password = set_password
    mock_kr.delete_password = delete_password
    mock_kr.errors = MagicMock()
    mock_kr.errors.NoKeyringError = type("NoKeyringError", (Exception,), {})
    mock_kr.errors.PasswordDeleteError = type("PasswordDeleteError", (Exception,), {})
    return mock_kr


_E2E_MOCK_KEYRING = _create_e2e_mock_keyring()
sys.modules["keyring"] = _E2E_MOCK_KEYRING

from tests.e2e.helpers.app_launcher import start_flet_app
from tests.e2e.helpers.flet_page import FletPage

from tests.conftest import _get_test_db_url

logger = logging.getLogger(__name__)

TEST_DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get(
    "E2E_DATABASE_URL",
    _get_test_db_url(),
)


def _extract_db_password_from_url(url: str) -> str:
    """从 DATABASE_URL 中解析密码，用于 E2E 子进程环境变量注入。

    ConfigHandler.save_db_password 在 DB_PASSWORD 环境变量存在时会跳过 keyring 写入，
    避免子进程（独立 Python 进程，不受测试进程 mock 影响）污染宿主机 keyring。
    """
    parsed = urlparse(url)
    if parsed.password:
        return unquote_plus(parsed.password)
    return ""


_E2E_DB_PASSWORD = _extract_db_password_from_url(TEST_DATABASE_URL)
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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def e2e_playwright():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        yield p


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def e2e_browser(e2e_playwright):
    # 启动期断言 canvaskit.wasm 本地存在（route handler 离线化依赖此文件）
    mock_root = Path(__file__).resolve().parent / "mock_assets"
    wasm_path = mock_root / "canvaskit" / "canvaskit.wasm"
    if not wasm_path.exists():
        raise RuntimeError(
            f"canvaskit.wasm not found at {wasm_path}. "
            "E2E 离线化依赖此文件，请从 flet_web 内置 canvaskit 目录复制："
            "`cp <site-packages>/flet_web/web/canvaskit/canvaskit.wasm "
            "tests/e2e/mock_assets/canvaskit/`（版本必须与 pyproject.toml 锁定的 flet 版本一致）"
        )
    # 启动期断言字体文件本地存在（CJK 回退字体离线化依赖）
    fonts_dir = mock_root / "fonts"
    if not fonts_dir.exists() or not any(fonts_dir.glob("*.woff2")):
        raise RuntimeError(
            f"字体文件未找到于 {fonts_dir}. "
            "E2E 离线化依赖 Noto Sans SC / Roboto woff2 字体分片，"
            "请用 diagnose_font_urls.py 捕获实际请求 URL 并下载到 tests/e2e/mock_assets/fonts/ 目录"
        )
    browser = await e2e_playwright.chromium.launch(
        channel=BROWSER_CHANNEL, headless=os.environ.get("E2E_HEADED", "0") != "1"
    )
    yield browser
    await browser.close()


def pytest_asyncio_loop_factories() -> dict[str, typing.Callable[[], asyncio.AbstractEventLoop]]:
    """Pytest-asyncio 1.4.0 hook for E2E tests: use ProactorEventLoop on Windows.

    覆盖 tests/conftest.py 中的 pytest_asyncio_loop_factories hook（使用 SelectorEventLoop）。
    E2E 测试需要 ProactorEventLoop：Flet 子进程启动 (subprocess.Popen) 与
    Playwright 异步驱动在 Windows 上依赖 Proactor 事件循环，Selector 不支持子进程。
    """
    if sys.platform == "win32":
        return {"proactor": asyncio.ProactorEventLoop}
    return {"default": asyncio.SelectorEventLoop}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """E2E 测试强制 session loop scope，避免跨 loop 访问 session fixtures。

    根因：``asyncio_default_test_loop_scope=function``（为 unit/integration 设置）
    导致 E2E 测试在 function loop 上执行。但 ``e2e_playwright``/``e2e_browser``
    是 session-scope async fixtures，在 session loop 上创建 Playwright 资源。
    function loop 上的 ``e2e_page`` 访问 session-loop-bound 的 browser 对象时，
    因 session loop idle 而永久 hang。

    修复：强制 E2E 测试用 session loop，与 session-scope async fixtures 共享 loop。
    """
    asyncio_marker = pytest.mark.asyncio(loop_scope="session")
    e2e_root = (config.rootpath / "tests" / "e2e").resolve()
    for item in items:
        try:
            Path(str(item.fspath)).resolve().relative_to(e2e_root)
        except ValueError:
            continue
        # append=False 将 marker 插入到 own_markers 列表开头，
        # 确保 get_closest_marker("asyncio") 返回此 marker（带 loop_scope=session），
        # 而非 pytest-asyncio AUTO 模式添加的无 loop_scope marker。
        item.add_marker(asyncio_marker, append=False)


@pytest.fixture(autouse=True, scope="session")
def mock_keyring():
    """Session 级 keyring mock：隔离 E2E 测试对宿主机 keyring 的污染。

    模块加载时已替换 sys.modules["keyring"]，此处 reinforce 并在 session 结束时
    防御性清理 AStockScreener 服务下的凭证残留，避免 mock 失效场景下的污染扩散。
    """
    sys.modules["keyring"] = _E2E_MOCK_KEYRING
    yield
    # 防御性兜底：清理 AStockScreener 服务下的凭证残留
    # 直接操作 _E2E_KEYRING_STORE，避免 import keyring 间接引用可能操作到真实 keyring
    _service = "AStockScreener"
    for _username in ("ts_token", "db_password", "ai_api_key"):
        _E2E_KEYRING_STORE.pop(f"{_service}:{_username}", None)
    # 遍历清理 ai_api_key_* 形式的 provider 凭证（mock store 可枚举）
    for _key in list(_E2E_KEYRING_STORE):
        if _key.startswith(f"{_service}:ai_api_key_"):
            _E2E_KEYRING_STORE.pop(_key, None)
    # 恢复原始 keyring
    if _E2E_ORIGINAL_KEYRING is not None:
        sys.modules["keyring"] = _E2E_ORIGINAL_KEYRING
    else:
        sys.modules.pop("keyring", None)


def _spawn(tmp_path_factory, config: dict, env_overrides: dict) -> tuple:
    cfg_dir = tmp_path_factory.mktemp("e2e_cfg")
    cfg_file = cfg_dir / "user_settings.json"
    cfg_file.write_text(json.dumps(config), encoding="utf-8")
    # tushare SDK set_token() 写入 ~/tk.csv，受限环境（TRAE Sandbox）会拒绝。
    # 将 USERPROFILE 重定向到 session 临时目录，隔离 tushare 文件写入。
    e2e_home = str(tmp_path_factory.mktemp("e2e_home"))
    proc, url = start_flet_app(cfg_file, {"USERPROFILE": e2e_home, **env_overrides})
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
    # Flet's web app downloads canvaskit.js and canvaskit.wasm from
    # https://www.gstatic.com/flutter-canvaskit/<engineRevision>/ on startup.
    # In CI and sometimes local environments, gstatic.com can be extremely slow or timeout,
    # causing the entire Playwright test to fail with a TimeoutError waiting for the page to load.
    # To fix this, we intercept canvaskit requests and serve them from local mock_assets.
    # Other external requests (icons, rive, etc.) are aborted to force offline mode.
    # NOTE: canvaskit 版本由 Flutter engineRevision 决定（见 flutter_bootstrap.js 的 buildConfig）。
    # 升级 flet 时若 engineRevision 变化，必须同步更新 mock_assets/canvaskit/ 下的文件，
    # 可从 site-packages/flet_web/web/canvaskit/ 复制对应版本。
    # [PITFALL FIX] 拦截外部资源请求，强制离线化 E2E 测试
    # 坑点：Flet (Flutter Web) 启动时会动态下载 canvaskit.js 和 canvaskit.wasm。
    # Playwright E2E 测试如果在 CI 环境或者无头模式下，由于网络波动，加载这两个文件极慢。
    # 更糟糕的是，如果加载超时，页面渲染会直接卡死在白屏，导致所有元素（如标题、按钮）等待超时 (TimeoutError)。
    # 解决方案：拦截外部资源请求，canvaskit 命中本地缓存则 fulfill，其余外部请求强制 abort（离线）。
    # 内部请求（Flet app 本身、data/blob URI）继续放行。
    # [PITFALL FIX 2] CJK 回退字体（Noto Sans SC / Roboto）必须本地化，不可依赖 CDN：
    # Flutter Web CanvasKit 运行时按需从 fonts.gstatic.com 下载 CJK 回退字体分片 (woff2)。
    # 若字体请求被 abort 或网络超时，回退字体度量异常会使选股页结果表格布局高度塌陷
    # (表头语义节点仅 ~4px)，PaginatedTable 虚拟化窗口构建 0 行 → 行语义节点 (如 "平安银行")
    # 永久缺失，所有依赖行文本的断言 (expect_result/expect_text) 超时失败。
    # 解决方案：字体请求按 URL 末尾文件名匹配 mock_assets/fonts/ 本地缓存，命中 fulfill，
    # 未命中 abort（与 canvaskit 同策略）。字体分片随 Flet 版本或测试内容变化时需用
    # diagnose_font_urls.py 重新捕获并更新本地缓存。
    #
    # 维护验证（Flet 升级后跑一次即可，URL 没变就无需重下字体）：
    #   PowerShell:
    #     Select-String -Path "<site-packages>/flet_web/web/main.dart.js" `
    #       -Pattern "notosanssc/v\d+/" -AllMatches |
    #       ForEach-Object { $_.Matches } | Select-Object -ExpandProperty Value -Unique
    #   - 输出 notosanssc/v37/ → 本地缓存继续有效
    #   - 输出其他版本号 → URL 变了，重跑 diagnose_font_urls.py 捕获新 URL 并下载替换
    async def intercept_external(route, request):
        url = request.url
        # 内部请求（Flet app 本身、data/blob URI）直接放行
        if url.startswith(("http://localhost", "http://127.0.0.1", "data:", "blob:")):
            await route.continue_()
            return
        # 字体 CDN：命中本地缓存则 fulfill，未命中 abort
        if "fonts.gstatic.com" in url or "fonts.googleapis.com" in url:
            filename = url.split("/")[-1]
            local_path = Path(__file__).resolve().parent / "mock_assets" / "fonts" / filename
            if local_path.exists():
                await route.fulfill(status=200, content_type="font/woff2", path=str(local_path))
                return
            await route.abort()
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("[E2E] DB error UI check failed (non-fatal): %s", exc, exc_info=True)

    fp.bind_context((None, None, context, page, request))
    return fp


async def _teardown_page(fp: FletPage, request, *, failed: bool = False) -> None:
    """Function 级 teardown：失败时保存 trace + screenshot，关闭 context。"""
    pw_context = fp.get_context()
    if not pw_context:
        return
    _, _, context, page, _request = pw_context
    try:
        if failed:
            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            name = request.node.name
            await page.screenshot(path=str(ARTIFACT_DIR / f"{name}.png"))
            await context.tracing.stop(path=str(ARTIFACT_DIR / f"{name}-trace.zip"))
        else:
            await context.tracing.stop()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug("[e2e_teardown] tracing stop failed: %s", e)
    finally:
        await context.close()


async def _ensure_locale_zh(fp: FletPage) -> None:
    """语言状态污染安全网：确保 flet_app 内存中的 I18n locale 为 zh_CN。

    test_settings_language_switch 切换语言后，其 finally 块可能因 CanvasKit
    渲染延迟/snackbar 干扰而恢复失败，导致 flet_app 内存 locale 仍是 en_US。
    pristine_config fixture 只还原磁盘配置和测试进程 I18n，不还原 app 内存 locale。
    本函数作为最后防线，在 e2e_page teardown 时检查并恢复中文，避免污染后续测试。

    设计要点：
    - 快速检查 has_text(nav_settings_zh)：正常测试几乎无开销（中文存在则 early return）
    - 恢复失败只 warning 不抛出：避免掩盖原始测试失败
    - R2: asyncio.CancelledError 必须 raise
    """
    nav_settings_zh = I18n.get("nav_settings", locale="zh_CN")
    try:
        if await fp.has_text(nav_settings_zh):
            return
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.debug("[e2e_page] locale 安全网 has_text 检查失败: %s", e, exc_info=True)
        return

    logger.warning("[e2e_page] 检测到语言污染（找不到中文导航文本），尝试通过 UI 恢复 zh_CN")
    try:
        nav_settings_en = I18n.get("nav_settings", locale="en_US")
        await fp.click_text(nav_settings_en, timeout_ms=8000)
        tab_system_en = I18n.get("settings_tab_system", locale="en_US")
        await fp.click_text(tab_system_en, timeout_ms=8000)
        lang_label_en = I18n.get("settings_language", locale="en_US")
        lang_zh = I18n.get("settings_lang_zh")
        await fp.select_dropdown(lang_label_en, lang_zh, timeout_ms=10000)
        for _ in range(25):
            if await fp.has_text(nav_settings_zh):
                logger.info("[e2e_page] locale 安全网成功恢复 zh_CN")
                return
            await fp.page.wait_for_timeout(200)
        logger.warning("[e2e_page] locale 安全网未能确认中文恢复")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("[e2e_page] locale 安全网恢复失败: %s", e, exc_info=True)


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
    from tests._helpers import create_test_engine
    from data.persistence.db_migrator import DatabaseMigrator
    from data.persistence.db_url_override import override_db_url

    # Ensure tables are migrated before seeding
    engine = create_test_engine(TEST_DATABASE_URL, echo=False)
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
            # 关键：让 "today" 始终等于种子数据中的最新交易日，而非自然日。
            # 否则在周末/节假日跑 CI 时，sync_status.last_data_date 会是自然日（如周日），
            # 但 daily_quotes.MAX(trade_date) 是上一个交易日（如周五），导致
            # ScreenerDao._get_latest_closed_trade_date 与种子数据不一致，策略查不到任何行。
            today = trade_dates[-1]

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

            # A9: 业务不变量自检 — 用与 ScreenerDao 完全相同的查询逻辑，验证
            # volume_breakout 策略默认参数下能命中"平安银行"。这是防御性契约：
            # 一旦未来有人改动阈值/表结构/seed 逻辑导致策略查不到数据，
            # 在 seed 阶段就报错，而不是让 5 个 E2E 测试在 30s 超时后才失败。
            _latest_td = await conn.fetchval("SELECT MAX(trade_date) FROM daily_quotes")
            if _latest_td != today:
                raise RuntimeError(
                    f"E2E seed 日期不一致: daily_quotes.MAX(trade_date)={_latest_td} 应等于最新交易日 today={today}"
                )

            _hit_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM daily_quotes q
                JOIN daily_indicators i ON q.ts_code = i.ts_code AND q.trade_date = i.trade_date
                WHERE q.ts_code = '000001.SZ'
                  AND q.trade_date = $1
                  AND q.pct_chg BETWEEN $2 AND $3
                  AND i.turnover_rate > $4
                """,
                today,
                _vb_params["pct_chg_min"],
                _vb_params["pct_chg_max"],
                _vb_params["turnover_min"],
            )
            if _hit_count == 0:
                raise RuntimeError(
                    "E2E seed 业务不变量失败: 平安银行在最新交易日 "
                    f"{today} 未满足 volume_breakout 默认参数 "
                    f"(pct_chg ∈ [{_vb_params['pct_chg_min']}, {_vb_params['pct_chg_max']}], "
                    f"turnover_rate > {_vb_params['turnover_min']})"
                )

        logger.info("[E2E Seeding] Database seeded successfully.")
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _ensure_e2e_db() -> None:
    """确保 E2E worker 的 ``test_astock_<worker>`` DB 存在。

    等价 ``tests/integration/conftest.py`` 的 ``_ensure_test_db``，
    因 conftest 作用域规则不对 ``tests/e2e/`` 生效（integration 的
    ``_ensure_test_db`` + ``db_schema_ready`` autouse fixture 仅对
    ``tests/integration/`` 子目录生效），此处显式调用以补齐 E2E 路径。

    根因 1（主因）：E2E 强制用 ProactorEventLoop（``pytest_asyncio_loop_factories``
    hook，subprocess.Popen + Playwright 需要），但 asyncpg 的 socket I/O 与
    Proactor 的 IOCP 模型不兼容，抛 ``ConnectionDoesNotExistError``。

    根因 2（协同因）：``_seed_e2e_data`` 只调 ``DatabaseMigrator.init_db``（建 schema），
    不创建 DB 本身。worker 若只跑 E2E，``test_astock_<worker>`` DB 永远不会被创建。

    修复：在独立线程中用 ``SelectorEventLoop`` 跑 ``_ensure_test_db``，避开
    ProactorEventLoop + asyncpg 兼容性问题。``asyncio.to_thread`` 在当前
    ProactorEventLoop 中调度，函数体在 default executor 线程中执行；新线程
    显式创建 ``SelectorEventLoop``（不修改全局 event_loop_policy，避免污染主线程）。
    """
    from tests.integration.conftest import _ensure_test_db

    def _run_in_selector_loop() -> None:
        # NOTE(lazy): Uses asyncio.SelectorEventLoop()/asyncio.new_event_loop() to run _ensure_test_db in a dedicated selector loop on a worker thread (avoids ProactorEventLoop+asyncpg incompatibility on Windows). ceiling: asyncio.new_event_loop 在 Python 3.13 仍可用, 未来版本可能 deprecate (暂无明确版本). upgrade: 项目升级 Python 3.16 前改用 asyncio.Runner.
        loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        try:
            loop.run_until_complete(_ensure_test_db())
        finally:
            loop.close()

    await asyncio.to_thread(_run_in_selector_loop)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seed_e2e_data(_ensure_e2e_db: None):
    """Session 级数据库播种：在所有 E2E 测试之前注入基准数据。

    显式依赖 ``_ensure_e2e_db`` 保证 DB 已创建（避免隐式时序脆弱性）。

    与 ``_ensure_e2e_db`` 同样在独立线程中用 ``SelectorEventLoop`` 跑：
    ``_seed_e2e_data`` 内部调用 ``DatabaseMigrator.init_db``（SQLAlchemy async
    engine + asyncpg driver）和 ``asyncpg.connect``，均受根因 1（ProactorEventLoop
    + asyncpg 不兼容）影响，必须在 ``SelectorEventLoop`` 中执行。
    """
    logger.info("[E2E Seeding] start _seed_e2e_data in SelectorEventLoop thread")

    def _run_in_selector_loop() -> None:
        # NOTE(lazy): Uses asyncio.SelectorEventLoop()/asyncio.new_event_loop() to run _seed_e2e_data in a dedicated selector loop on a worker thread (avoids ProactorEventLoop+asyncpg incompatibility on Windows). ceiling: asyncio.new_event_loop 在 Python 3.13 仍可用, 未来版本可能 deprecate (暂无明确版本). upgrade: 项目升级 Python 3.16 前改用 asyncio.Runner.
        loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        try:
            loop.run_until_complete(_seed_e2e_data())
        finally:
            loop.close()

    await asyncio.to_thread(_run_in_selector_loop)
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
            "DB_PASSWORD": _E2E_DB_PASSWORD,
            # keyring 25.7.0 原生支持 PYTHON_KEYRING_BACKEND 指定后端。
            # null 后端：set/delete 为 no-op，get 返回 None。
            # 一劳永逸隔离子进程所有 keyring 操作，覆盖 save_provider_credential、
            # _migrate_custom_models_credentials 等无法用 AI_API_KEY 短路的 per-provider 路径。
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
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
        env_overrides={
            "TS_TOKEN": "e2e-dummy-token",
            "AI_API_KEY": "e2e-dummy-key",
            "DATABASE_URL": TEST_DATABASE_URL,
            "DB_PASSWORD": _E2E_DB_PASSWORD,
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    app = AppServer(proc, url, cfg_file)
    yield app
    _terminate(proc)


@pytest_asyncio.fixture(loop_scope="session")
async def e2e_page(e2e_browser, flet_app: AppServer, request):
    """Function 级 Page：每用例独立 BrowserContext + Page，无跨用例状态污染。

    性能优化：删除 theme_switch + 消除硬等待 + CI 分级 multiplier。
    CanvasKit 加载 (~8.5s) 每用例发生，但可靠性优先于速度。

    loop_scope=session：与 PR #179 强制测试用 session loop 对齐，避免 function-loop
    fixture 访问 session-loop-bound e2e_browser 时跨 loop hang。
    """
    fp = await _make_page(e2e_browser, flet_app, request, check_db_error=True)
    if request.node.get_closest_marker("slow"):
        fp._timeout_multiplier = max(TIMEOUT_MULTIPLIER, 2.5)  # noqa: SLF001
    yield fp
    # 语言安全网：mutates_config 用例可能污染 flet_app 内存 locale
    if request.node.get_closest_marker("mutates_config"):
        await _ensure_locale_zh(fp)
    failed = any(
        getattr(request.node, f"rep_{when}", None) and getattr(request.node, f"rep_{when}").failed
        for when in ("setup", "call")
    )
    await _teardown_page(fp, request, failed=failed)


@pytest_asyncio.fixture(loop_scope="session")
async def wizard_page(e2e_browser, wizard_app: AppServer, request):
    """Function 级 Page（向导测试）：每用例独立 context，无状态污染。"""
    fp = await _make_page(e2e_browser, wizard_app, request)
    yield fp
    failed = any(
        getattr(request.node, f"rep_{when}", None) and getattr(request.node, f"rep_{when}").failed
        for when in ("setup", "call")
    )
    await _teardown_page(fp, request, failed=failed)


@pytest.fixture(scope="session")
def embedded_wizard_app(tmp_path_factory, mock_keyring):
    """Embedded 模式 wizard app fixture (P3-18)。

    与 wizard_app 区别：注入 QTRADING_DATABASE_MODE=embedded + fake_sidecar 路径，
    使 app 内部 EmbeddedPostgresService 启动时 Popen fake_sidecar 而非真实 sidecar。
    """
    from tests.e2e.fixtures.fake_sidecar import create_fake_sidecar

    fake_sidecar_path = create_fake_sidecar(tmp_path_factory.mktemp("fake_sidecar"))
    proc, url, cfg_file = _spawn(
        tmp_path_factory,
        config={
            "locale": "zh",
            "embedded_pg_enabled": True,
            "embedded_pg_sidecar_path": str(fake_sidecar_path),
        },
        env_overrides={
            "TS_TOKEN": "e2e-dummy-token",
            "AI_API_KEY": "e2e-dummy-key",
            "QTRADING_DATABASE_MODE": "embedded",
            "PYTHONKEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    app = AppServer(proc, url, cfg_file)
    yield app
    _terminate(proc)


@pytest_asyncio.fixture(loop_scope="session")
async def embedded_wizard_page(e2e_browser, embedded_wizard_app: AppServer, request):
    """Function 级 Page（embedded 模式向导测试, P3-18）。"""
    fp = await _make_page(e2e_browser, embedded_wizard_app, request)
    yield fp
    failed = any(
        getattr(request.node, f"rep_{when}", None) and getattr(request.node, f"rep_{when}").failed
        for when in ("setup", "call")
    )
    await _teardown_page(fp, request, failed=failed)


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
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("[pristine_config] 还原 I18n locale 到 %s 失败: %s", locale_snapshot, e)
