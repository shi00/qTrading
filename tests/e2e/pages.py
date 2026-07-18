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

    async def click_column_header(self, col_label: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击表格列头触发排序（列头文本格式如 ``pct_chg (涨跌幅)``）。"""
        await self.page.click_text(col_label, timeout_ms=timeout_ms)

    async def click_row_by_text(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击表格中包含指定文本的行（用于触发行 on_click 打开详情对话框）。

        策略（按可靠性顺序）：
        1. ``page.evaluate`` 直接调用 ``el.click()`` 在最内层 ``flt-tappable`` 元素上 —
           直接触发 Flet semantics 的 tap 事件，绕过 Playwright 的 actionability 检查。
           选最内层（最小面积）的 ``flt-tappable`` 以避免命中父容器（如表格卡片）。
        2. ``page.mouse.click()`` 在文本 bounding box 中心 — 通过 Flutter hit-testing
           分发到行 Container.on_click（canvas 层面点击）。
        3. ``flt-tappable`` 祖先元素 click — Playwright 标准点击。
        4. 文本元素 force click — 兜底。
        """
        scaled = self.page._tm(timeout_ms)
        page = self.page.page
        text_loc = page.get_by_text(text, exact=False).first
        # visible（非 attached）确保 CanvasKit 已渲染文本，bounding_box 返回有效坐标
        try:
            await text_loc.wait_for(state="visible", timeout=scaled)
        except Exception:  # noqa: BLE001
            await text_loc.wait_for(state="attached", timeout=scaled)

        # 策略 1: page.evaluate 直接 el.click() 最内层 flt-tappable 元素
        try:
            clicked = await page.evaluate(
                """(searchText) => {
                    const elements = Array.from(document.querySelectorAll('[flt-tappable]'));
                    const matching = elements.filter(el => el.textContent.includes(searchText));
                    if (matching.length === 0) return false;
                    // 选最小面积的（最内层 = 行 Container，避免命中表格卡片等父容器）
                    matching.sort((a, b) => {
                        const aBox = a.getBoundingClientRect();
                        const bBox = b.getBoundingClientRect();
                        return (aBox.width * aBox.height) - (bBox.width * bBox.height);
                    });
                    matching[0].click();
                    return true;
                }""",
                text,
            )
            if clicked:
                return
        except Exception:  # noqa: BLE001
            pass

        # 策略 2: mouse.click 在文本中心（通过 Flutter hit-testing 触发行 on_click）
        try:
            box = await text_loc.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                await page.mouse.click(cx, cy)
                return
        except Exception:  # noqa: BLE001
            pass

        # 策略 3: 找文本的 flt-tappable 祖先（行 Container）并点击
        try:
            # locator().filter(has=...) 匹配包含指定 locator 的元素；取 first 避免匹配多个
            tappable_ancestor = page.locator("[flt-tappable]").filter(has=text_loc).first
            if await tappable_ancestor.count() > 0:
                await tappable_ancestor.click(force=True, timeout=self.page._tm(3000))
                return
        except Exception:  # noqa: BLE001
            pass

        # 策略 4: 兜底直接 force click 文本
        await text_loc.click(force=True, timeout=self.page._tm(3000))
