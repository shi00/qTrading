import asyncio
import json
import logging
import os
import sys
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

I18n.set_locale("zh")


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

    async def intercept_canvaskit(route):
        url = route.request.url
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


@pytest.fixture(scope="session")
def flet_app(tmp_path_factory):
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
