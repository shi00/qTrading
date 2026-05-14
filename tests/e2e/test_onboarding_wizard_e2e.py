"""
E2E Tests for Onboarding Wizard

Prerequisites:
    pip install playwright pytest-playwright && playwright install

Run:
    1. Start the application server first:
       python -m core.main
    2. Run E2E tests (remove skip marker or use --override-ini):
       pytest tests/e2e/test_onboarding_wizard_e2e.py -m e2e --headed
       pytest tests/e2e/ -m e2e --override-ini="markers=e2e" -k "not skip"

Note: These tests are skipped by default because they require:
    - A running application server on localhost:8550
    - Playwright browser binaries installed
    - A test database configured
"""

import pytest


@pytest.fixture(scope="module")
def browser_context():
    """Create browser context for E2E tests"""
    try:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()
        playwright.stop()
    except ImportError:
        pytest.skip("playwright not installed, skipping E2E tests")


@pytest.fixture
def page(browser_context):
    """Create a new page for each test"""
    page = browser_context.new_page()
    yield page
    page.close()


class TestOnboardingWizardE2E:
    """E2E tests for complete wizard flow"""

    @pytest.mark.skip(reason="Requires running application server")
    def test_wizard_navigation_flow(self, page):
        """Test complete wizard navigation flow"""
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

    @pytest.mark.skip(reason="Requires running application server")
    def test_required_step_validation(self, page):
        """Test required step validation prevents empty input"""
        page.goto("http://localhost:8550")

        page.wait_for_selector("text=欢迎使用", timeout=10000)

        page.click("button:has-text('开始使用')")

        page.wait_for_selector("text=数据库配置", timeout=5000)

        page.click("button:has-text('验证并继续')")

        page.wait_for_selector("text=请填写", timeout=3000)

    @pytest.mark.skip(reason="Requires running application server")
    def test_skip_optional_step(self, page):
        """Test skipping optional local model step"""
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

    @pytest.mark.skip(reason="Requires running application server")
    def test_complete_wizard_flow(self, page):
        """Test completing the entire wizard"""
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

    @pytest.mark.skip(reason="Requires running application server")
    def test_back_navigation_preserves_input(self, page):
        """Test that going back preserves user input"""
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

    @pytest.mark.skip(reason="Requires running application server")
    def test_language_switch_preserves_state(self, page):
        """Test that language switching preserves form state"""
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
