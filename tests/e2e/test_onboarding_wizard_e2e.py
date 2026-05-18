"""
E2E Tests for Onboarding Wizard

Prerequisites:
    pip install playwright pytest-playwright && playwright install

Run:
    1. Start the application server first:
       python main.py
    2. Run E2E tests:
       pytest tests/e2e/test_onboarding_wizard_e2e.py -m e2e --headed

Note: Smoke tests (TestOnboardingWizardSmoke) run automatically when
    Playwright is installed and the app server is reachable. They skip
    gracefully otherwise. Full E2E tests (TestOnboardingWizardE2E,
    TestOnboardingWizardE2EShortcuts) still require explicit server setup.
"""

import pytest


def _is_server_reachable(url="http://localhost:8550", timeout_ms=2000):
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="commit")
            return True
        except Exception:
            return False
        finally:
            page.close()
            browser.close()
            pw.stop()
    except Exception:
        return False


def _playwright_available():
    try:
        import importlib.util

        return importlib.util.find_spec("playwright") is not None
    except ImportError:
        return False


skip_if_no_playwright = pytest.mark.skipif(
    not _playwright_available(),
    reason="playwright not installed",
)

skip_if_no_server = pytest.mark.skipif(
    not _is_server_reachable(),
    reason="application server not reachable on localhost:8550",
)


@pytest.fixture(scope="module")
def browser_context():
    try:
        from playwright.sync_api import sync_playwright

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
        welcome_visible = page.locator("text=欢迎使用").or_(page.locator("text=Welcome"))
        welcome_visible.wait_for(timeout=10000)
        assert welcome_visible.is_visible()

    @skip_if_no_playwright
    @skip_if_no_server
    def test_wizard_first_step_renders(self, page):
        page.goto("http://localhost:8550", timeout=10000)
        start_button = page.locator("button:has-text('开始使用')").or_(page.locator("button:has-text('Get Started')"))
        start_button.wait_for(timeout=10000)
        assert start_button.is_visible()
        start_button.click()
        db_config = page.locator("text=数据库配置").or_(page.locator("text=Database Configuration"))
        db_config.wait_for(timeout=5000)
        assert db_config.is_visible()


class TestOnboardingWizardE2E:
    """Full E2E tests: require explicit server setup"""

    @pytest.mark.skip(reason="Requires running application server with test DB")
    def test_wizard_navigation_flow(self, page):
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.click("button:has-text('开始使用')")
        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.fill("input[placeholder*='主机']", "localhost")
        page.fill("input[placeholder*='端口']", "5432")
        page.fill("input[placeholder*='用户']", "postgres")
        page.fill("input[placeholder*='密码']", "password")
        page.fill("input[placeholder*='数据库']", "testdb")

        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=Token", timeout=5000)

        page.click("button:has-text('上一步')")

        page.wait_for_selector("text=数据库配置", timeout=5000)

        host_value = page.input_value("input[placeholder*='主机']")
        assert host_value == "localhost"

    @pytest.mark.skip(reason="Requires running application server with test DB")
    def test_required_step_validation(self, page):
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.click("button:has-text('开始使用')")

        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=请填写", timeout=3000)

    @pytest.mark.skip(reason="Requires running application server with test DB")
    def test_skip_optional_step(self, page):
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.click("button:has-text('开始使用')")
        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.fill("input[placeholder*='主机']", "localhost")
        page.fill("input[placeholder*='端口']", "5432")
        page.fill("input[placeholder*='用户']", "postgres")
        page.fill("input[placeholder*='密码']", "password")
        page.fill("input[placeholder*='数据库']", "testdb")
        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=Token", timeout=5000)
        page.fill("input[placeholder*='Token']", "test_token_12345")
        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=云端 AI", timeout=5000)
        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=本地模型", timeout=5000)

        page.click("button:has-text('跳过')")

        page.wait_for_selector("text=数据同步", timeout=5000)

    @pytest.mark.skip(reason="Requires running application server with test DB")
    def test_complete_wizard_flow(self, page):
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.click("button:has-text('开始使用')")

        page.wait_for_selector("text=数据库配置", timeout=5000)
        page.fill("input[placeholder*='主机']", "localhost")
        page.fill("input[placeholder*='端口']", "5432")
        page.fill("input[placeholder*='用户']", "postgres")
        page.fill("input[placeholder*='密码']", "password")
        page.fill("input[placeholder*='数据库']", "testdb")
        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=Token", timeout=5000)
        page.fill("input[placeholder*='Token']", "test_token_12345")
        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=云端 AI", timeout=5000)
        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=本地模型", timeout=5000)
        page.click("button:has-text('跳过')")

        page.wait_for_selector("text=数据同步", timeout=5000)
        page.click("button:has-text('下一步')")

        page.wait_for_selector("text=定时任务", timeout=5000)
        page.click("button:has-text('下一步')")

        page.wait_for_selector("text=配置完成", timeout=5000)
        page.click("button:has-text('开始使用')")

        page.wait_for_url("**/home**", timeout=5000)


class TestOnboardingWizardE2EShortcuts:
    """E2E tests for wizard shortcuts and edge cases"""

    @pytest.mark.skip(reason="Requires running application server with test DB")
    def test_back_navigation_preserves_input(self, page):
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)
        page.click("button:has-text('开始使用')")

        page.wait_for_selector("text=数据库配置", timeout=5000)
        page.fill("input[placeholder*='主机']", "myhost")
        page.fill("input[placeholder*='端口']", "9999")

        page.click("button:has-text('上一步')")

        page.wait_for_selector("text=欢迎使用", timeout=5000)
        page.click("button:has-text('开始使用')")

        page.wait_for_selector("text=数据库配置", timeout=5000)

        host_value = page.input_value("input[placeholder*='主机']")
        port_value = page.input_value("input[placeholder*='端口']")

        assert host_value == "myhost"
        assert port_value == "9999"

    @pytest.mark.skip(reason="Requires running application server with test DB")
    def test_language_switch_preserves_state(self, page):
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)
        page.click("button:has-text('开始使用')")

        page.wait_for_selector("text=数据库配置", timeout=5000)
        page.fill("input[placeholder*='主机']", "testhost")

        page.click("[data-testid='language-switch']")
        page.click("text=English")

        page.wait_for_selector("text=Database Configuration", timeout=5000)

        host_value = page.input_value("input[placeholder*='Host']")
        assert host_value == "testhost"
