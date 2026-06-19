"""E2E Page Object 层。

封装跨测试重复的导航与选股交互序列，消除测试代码重复。
内部使用 FletPage helper 与 labels.py 提供的本地化文案。
"""

from core.i18n import I18n
from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.labels import strategy_label
from tests.e2e.timeouts import TIMEOUTS


class App:
    """顶层导航 Page Object，封装侧边栏导航点击。"""

    def __init__(self, page: FletPage):
        self.page = page

    async def goto(self, nav_key: str, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到指定页面（通过 nav i18n key，如 "nav_screener"）。"""
        label = I18n.get(nav_key)
        await self.page.click_text(label, timeout_ms=timeout_ms)


class ScreenerPage:
    """选股页 Page Object，封装选策略→执行→等结果序列。"""

    def __init__(self, page: FletPage):
        self.page = page
        self.app = App(page)

    async def open(self) -> None:
        """导航到选股页。"""
        await self.app.goto("nav_screener")

    async def select_strategy(self, strategy_key: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """选择指定策略（通过策略 key，内部解析为本地化显示名）。"""
        select_label = I18n.get("select_strategy")
        name = strategy_label(strategy_key)
        await self.page.select_dropdown(select_label, name, timeout_ms=timeout_ms)

    async def run(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """点击执行选股按钮。"""
        run_text = I18n.get("run_screening")
        await self.page.click_button(run_text, timeout_ms=timeout_ms)

    async def expect_result(self, text: str, timeout_ms: int = TIMEOUTS.SCREEN_RESULT) -> None:
        """等待选股结果文本出现。"""
        await self.page.expect_text(text, timeout_ms=timeout_ms)

    async def expect_text(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """等待选股页任意文本出现。"""
        await self.page.expect_text(text, timeout_ms=timeout_ms)
