"""
E2E Tests for Onboarding Wizard

Prerequisites:
    pip install playwright pytest-playwright && playwright install

Run:
    1. Start the application server first:
       python main.py
    2. Run E2E tests:
       pytest tests/e2e/test_onboarding_wizard_e2e.py -m e2e --headed

Note: All E2E tests run automatically when Playwright is installed and
    the app server is reachable. They skip gracefully otherwise.
    For full wizard flow tests that require a test database, set
    E2E_TEST_DB=1 environment variable.
"""

import os
from typing import TYPE_CHECKING

import pytest


def enable_flet_accessibility(page):
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    enable_btn = page.locator('button[aria-label="Enable accessibility"], [aria-label="Enable accessibility"]')
    if enable_btn.count() > 0:
        enable_btn.first.evaluate("el => el.click()")
        page.wait_for_timeout(1000)


if TYPE_CHECKING:
    pass


def _is_server_reachable(url="http://localhost:8550", timeout=2):
    try:
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except (OSError, urllib.error.URLError):
        return False


def _playwright_available():
    try:
        import importlib.util

        return importlib.util.find_spec("playwright") is not None
    except ImportError:
        return False


def _has_test_db_env():
    return os.environ.get("E2E_TEST_DB", "").lower() in ("1", "true", "yes")


skip_if_no_playwright = pytest.mark.skipif(
    not _playwright_available(),
    reason="playwright not installed",
)

skip_if_no_server = pytest.mark.skipif(
    not _is_server_reachable(),
    reason="application server not reachable on localhost:8550",
)

skip_if_no_test_db = pytest.mark.skipif(
    not _has_test_db_env(),
    reason="E2E_TEST_DB=1 not set (requires test database for full wizard flow)",
)


@pytest.fixture(scope="module")
def browser_context():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-untyped]

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()
        playwright.stop()
    except ImportError:
        pytest.skip("playwright not installed, skipping E2E tests")


@pytest.fixture
def page(browser_context):
    page = browser_context.new_page()
    yield page
    page.close()


class TestOnboardingWizardSmoke:
    """Smoke tests: run automatically when Playwright + server available"""

    @skip_if_no_playwright
    @skip_if_no_server
    def test_app_loads_and_shows_welcome(self, page):
        page.goto("http://localhost:8550", timeout=10000)
        enable_flet_accessibility(page)
        welcome_visible = page.locator("text=欢迎使用").or_(page.locator("text=Welcome"))
        welcome_visible.wait_for(timeout=10000)
        assert welcome_visible.is_visible()

    @skip_if_no_playwright
    @skip_if_no_server
    def test_wizard_first_step_renders(self, page):
        page.goto("http://localhost:8550", timeout=10000)
        enable_flet_accessibility(page)
        start_button = page.locator("button:has-text('开始使用')").or_(page.locator("button:has-text('Get Started')"))
        start_button.wait_for(timeout=10000)
        assert start_button.is_visible()
        start_button.click(force=True)
        db_config = page.locator("text=数据库配置").or_(page.locator("text=Database Configuration"))
        db_config.wait_for(timeout=5000)
        assert db_config.is_visible()


class TestOnboardingWizardE2E:
    """Full E2E tests: require Playwright + server + test database (E2E_TEST_DB=1)"""

    @skip_if_no_playwright
    @skip_if_no_server
    @skip_if_no_test_db
    def test_wizard_navigation_flow(self, page):
        page.goto("http://localhost:8550")
        enable_flet_accessibility(page)

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.locator("text=开始使用").click(force=True)
        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.fill("input[aria-label*='主机']", "localhost")
        page.fill("input[aria-label*='端口']", "5432")
        page.fill("input[aria-label*='用户']", "postgres")
        page.fill("input[aria-label*='密码']", "password")
        page.fill("input[aria-label*='数据库']", "testdb")

        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=Token", timeout=5000)

        page.locator("text=上一步").click(force=True)

        page.wait_for_selector("text=数据库配置", timeout=5000)

        host_value = page.input_value("input[aria-label*='主机']")
        assert host_value == "localhost"

    @skip_if_no_playwright
    @skip_if_no_server
    @skip_if_no_test_db
    def test_required_step_validation(self, page):
        page.goto("http://localhost:8550")
        enable_flet_accessibility(page)

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.locator("text=开始使用").click(force=True)

        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=请填写", timeout=3000)

    @skip_if_no_playwright
    @skip_if_no_server
    @skip_if_no_test_db
    def test_skip_optional_step(self, page):
        page.goto("http://localhost:8550")
        enable_flet_accessibility(page)

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.locator("text=开始使用").click(force=True)
        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.fill("input[aria-label*='主机']", "localhost")
        page.fill("input[aria-label*='端口']", "5432")
        page.fill("input[aria-label*='用户']", "postgres")
        page.fill("input[aria-label*='密码']", "password")
        page.fill("input[aria-label*='数据库']", "testdb")
        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=Token", timeout=5000)
        page.fill("input[aria-label*='Token']", "test_token_12345")
        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=云端 AI", timeout=5000)
        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=本地模型", timeout=5000)

        page.locator("text=跳过").click(force=True)

        page.wait_for_selector("text=数据同步", timeout=5000)

    @skip_if_no_playwright
    @skip_if_no_server
    @skip_if_no_test_db
    def test_complete_wizard_flow(self, page):
        page.goto("http://localhost:8550")
        enable_flet_accessibility(page)

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.locator("text=开始使用").click(force=True)

        page.wait_for_selector("text=数据库配置", timeout=5000)
        page.fill("input[aria-label*='主机']", "localhost")
        page.fill("input[aria-label*='端口']", "5432")
        page.fill("input[aria-label*='用户']", "postgres")
        page.fill("input[aria-label*='密码']", "password")
        page.fill("input[aria-label*='数据库']", "testdb")
        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=Token", timeout=5000)
        page.fill("input[aria-label*='Token']", "test_token_12345")
        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=云端 AI", timeout=5000)
        page.locator("text=验证并继续").click(force=True)

        page.wait_for_selector("text=本地模型", timeout=5000)
        page.locator("text=跳过").click(force=True)

        page.wait_for_selector("text=数据同步", timeout=5000)
        page.locator("text=下一步").click(force=True)

        page.wait_for_selector("text=定时任务", timeout=5000)
        page.locator("text=下一步").click(force=True)

        page.wait_for_selector("text=配置完成", timeout=5000)
        page.locator("text=开始使用").click(force=True)

        page.wait_for_url("**/home**", timeout=5000)


class TestOnboardingWizardE2EShortcuts:
    """E2E tests for wizard shortcuts and edge cases"""

    @skip_if_no_playwright
    @skip_if_no_server
    @skip_if_no_test_db
    def test_back_navigation_preserves_input(self, page):
        page.goto("http://localhost:8550")
        enable_flet_accessibility(page)

        page.wait_for_selector("text=欢迎使用", timeout=10000)
        page.locator("text=开始使用").click(force=True)

        page.wait_for_selector("text=数据库配置", timeout=5000)
        page.fill("input[aria-label*='主机']", "myhost")
        page.fill("input[aria-label*='端口']", "9999")

        page.locator("text=上一步").click(force=True)

        page.wait_for_selector("text=欢迎使用", timeout=5000)
        page.locator("text=开始使用").click(force=True)

        page.wait_for_selector("text=数据库配置", timeout=5000)

        host_value = page.input_value("input[aria-label*='主机']")
        port_value = page.input_value("input[aria-label*='端口']")

        assert host_value == "myhost"
        assert port_value == "9999"

    @skip_if_no_playwright
    @skip_if_no_server
    @skip_if_no_test_db
    def test_language_switch_preserves_state(self, page):
        page.goto("http://localhost:8550")
        enable_flet_accessibility(page)

        page.wait_for_selector("text=欢迎使用", timeout=10000)
        page.locator("text=开始使用").click(force=True)

        page.wait_for_selector("text=数据库配置", timeout=5000)
        page.fill("input[aria-label*='主机']", "testhost")

        page.locator("[data-testid='language-switch']").click(force=True)
        page.locator("text=English").click(force=True)

        page.wait_for_selector("text=Database Configuration", timeout=5000)

        host_value = page.input_value("input[aria-label*='Host']")
        assert host_value == "testhost"
