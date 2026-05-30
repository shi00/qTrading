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

TEST_DATABASE_URL = os.environ.get(
    "E2E_DATABASE_URL",
    _get_test_db_url(),
)
BROWSER_CHANNEL = os.environ.get("E2E_BROWSER_CHANNEL", "msedge")
if not BROWSER_CHANNEL:
    BROWSER_CHANNEL = None

ARTIFACT_DIR = Path(os.environ.get("E2E_ARTIFACT_DIR", "e2e-artifacts"))

# 强制将测试运行进程的 I18n 语言设为中文，以匹配 E2E 测试环境的语言
from core.i18n import I18n

I18n.set_locale("zh")


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


async def _make_page(url: str, request) -> FletPage:
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    browser = await p.chromium.launch(channel=BROWSER_CHANNEL, headless=True)
    context = await browser.new_context(viewport={"width": 1400, "height": 900})
    await context.tracing.start(screenshots=True, snapshots=True)
    page = await context.new_page()
    page.on("console", lambda msg: logger.debug("[BROWSER CONSOLE] %s: %s", msg.type, msg.text))
    page.on("pageerror", lambda err: logger.debug("[BROWSER ERROR] %s", err))
    fp = FletPage(page)
    await fp.open(url)
    fp.bind_context((p, browser, context, page, request))
    return fp


async def _teardown_page(fp: FletPage) -> None:
    pw_context = fp.get_context()
    if not pw_context:
        return
    p, browser, context, page, request = pw_context
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
        await browser.close()
        await p.stop()


@pytest.fixture(scope="session")
def flet_app(tmp_path_factory):
    proc, url = _spawn(
        tmp_path_factory,
        config={
            "onboarding_complete": True,
            "db_url": TEST_DATABASE_URL,
            "locale": "zh",
        },
        env_overrides={
            "TS_TOKEN": "e2e-dummy-token",
            "AI_API_KEY": "e2e-dummy-key",
        },
    )
    yield url
    _terminate(proc)


@pytest.fixture(scope="session")
def wizard_app(tmp_path_factory):
    proc, url = _spawn(
        tmp_path_factory,
        config={"locale": "zh"},
        env_overrides={"DATABASE_URL": TEST_DATABASE_URL},
    )
    yield url
    _terminate(proc)


@pytest.fixture
async def e2e_page(flet_app, request):
    fp = await _make_page(flet_app, request)
    yield fp
    await _teardown_page(fp)


@pytest.fixture
async def wizard_page(wizard_app, request):
    fp = await _make_page(wizard_app, request)
    yield fp
    await _teardown_page(fp)
