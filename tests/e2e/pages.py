"""E2E Page Object 层。

封装跨测试重复的导航与选股交互序列，消除测试代码重复。
内部使用 FletPage helper 与 labels.py 提供的本地化文案。
"""

import logging

from core.i18n import I18n
from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.labels import strategy_label
from tests.e2e.timeouts import TIMEOUTS

logger = logging.getLogger(__name__)


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

    async def click_export(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 CSV 导出按钮。"""
        await self.page.click_button(I18n.get("screener_export"), timeout_ms=timeout_ms)

    async def click_export_excel(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 Excel 导出按钮。"""
        await self.page.click_button(I18n.get("data_export_excel"), timeout_ms=timeout_ms)

    async def click_row_by_text(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击表格中包含指定文本的行（用于触发行 on_click 打开详情对话框）。

        策略（按可靠性顺序）：
        1. ``flt-semantics[flt-tappable]`` bounding_box 中心 → ``page.mouse.click`` —
           **首选**: 真实鼠标事件通过 Flutter hit-testing 触发 GestureDetector.on_tap.
           CanvasKit 不响应合成 DOM el.click() (Playwright click(force=True)),
           只响应真实鼠标事件. GestureDetector(on_tap) 生成 flt-tappable 语义属性.
        2. ``flt-semantics[role="button"]`` bounding_box 中心 → ``page.mouse.click`` —
           降级: 旧版 Container(ink=True, on_click) 生成 role=button 但不生成 flt-tappable.
        3. 文本节点 bounding_box 中心 → ``page.mouse.click`` — 纯文本 canvas 坐标点击.
        4. ``flt-semantics[flt-tappable]`` force click — 合成 DOM 事件降级.
        5. ``flt-semantics[role="button"]`` force click — 合成 DOM 事件降级.
        6. 文本节点 force click — 最终兜底.
        """
        scaled = self.page._tm(timeout_ms)
        page = self.page.page
        text_loc = page.get_by_text(text, exact=False).first
        # visible（非 attached）确保 CanvasKit 已渲染文本，bounding_box 返回有效坐标
        try:
            await text_loc.wait_for(state="visible", timeout=scaled)
            logger.debug("click_row_by_text: text_loc visible for '%s'", text)
        except Exception as exc1:  # noqa: BLE001
            logger.debug("click_row_by_text: text_loc visible failed for '%s', trying attached: %s", text, exc1)
            try:
                await text_loc.wait_for(state="attached", timeout=scaled)
            except Exception as exc2:  # noqa: BLE001
                logger.warning("click_row_by_text: text_loc not even attached for '%s': %s", text, exc2, exc_info=True)
                raise  # 连文本都找不到，直接抛，不静默

        # 策略 1: flt-semantics[flt-tappable] 包含文本 → bounding_box 中心 → page.mouse.click
        # 首选: 真实鼠标事件触发 Flutter hit-testing → GestureDetector.on_tap 回调.
        # CanvasKit 不响应合成 DOM el.click() (force=True), 只响应真实鼠标事件.
        try:
            tappable = page.locator("flt-semantics[flt-tappable]").filter(has_text=text).first
            if await tappable.count() > 0:
                box = await tappable.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    await page.mouse.click(cx, cy)
                    await page.wait_for_timeout(500)
                    logger.debug(
                        "click_row_by_text: strategy 1 SUCCESS (mouse.click on flt-tappable bb=(%s,%s)) for '%s'",
                        cx,
                        cy,
                        text,
                    )
                    return
                logger.debug("click_row_by_text: strategy 1 bounding_box invalid for '%s': %s", text, box)
            else:
                logger.debug("click_row_by_text: strategy 1 no flt-tappable for '%s'", text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("click_row_by_text: strategy 1 failed for '%s': %s", text, exc)

        # 策略 2: flt-semantics[role="button"] 包含文本 → bounding_box 中心 → page.mouse.click
        # 降级: 旧版 Container(ink=True, on_click) 生成 role=button 但不生成 flt-tappable.
        try:
            row_btn = page.locator('flt-semantics[role="button"]').filter(has_text=text).first
            if await row_btn.count() > 0:
                box = await row_btn.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    await page.mouse.click(cx, cy)
                    await page.wait_for_timeout(500)
                    logger.debug(
                        "click_row_by_text: strategy 2 SUCCESS (mouse.click on role=button bb=(%s,%s)) for '%s'",
                        cx,
                        cy,
                        text,
                    )
                    return
                logger.debug("click_row_by_text: strategy 2 bounding_box invalid for '%s': %s", text, box)
        except Exception as exc:  # noqa: BLE001
            logger.debug("click_row_by_text: strategy 2 failed for '%s': %s", text, exc)

        # 策略 3: 文本 bounding_box 中心 → page.mouse.click
        try:
            box = await text_loc.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                await page.mouse.click(cx, cy)
                await page.wait_for_timeout(500)
                logger.debug(
                    "click_row_by_text: strategy 3 SUCCESS (mouse.click on text bb=(%s,%s)) for '%s'",
                    cx,
                    cy,
                    text,
                )
                return
            logger.debug("click_row_by_text: strategy 3 text bounding_box invalid for '%s': %s", text, box)
        except Exception as exc:  # noqa: BLE001
            logger.debug("click_row_by_text: strategy 3 failed for '%s': %s", text, exc)

        # 策略 4: flt-semantics[flt-tappable] 包含文本 → Playwright click(force=True)
        # 降级: 合成 DOM 事件 (对部分 Flet 控件有效, 但 CanvasKit 行点击不可靠)
        try:
            tappable_fc = page.locator("flt-semantics[flt-tappable]").filter(has_text=text).first
            if await tappable_fc.count() > 0:
                await tappable_fc.click(force=True, timeout=self.page._tm(3000))
                logger.debug("click_row_by_text: strategy 4 SUCCESS (flt-tappable force click) for '%s'", text)
                return
        except Exception as exc:  # noqa: BLE001
            logger.debug("click_row_by_text: strategy 4 failed for '%s': %s", text, exc)

        # 策略 5: flt-semantics[role="button"] 包含文本 → Playwright click(force=True)
        try:
            row_btn_fc = page.locator('flt-semantics[role="button"]').filter(has_text=text).first
            if await row_btn_fc.count() > 0:
                await row_btn_fc.click(force=True, timeout=self.page._tm(3000))
                logger.debug("click_row_by_text: strategy 5 SUCCESS (role=button force click) for '%s'", text)
                return
        except Exception as exc:  # noqa: BLE001
            logger.debug("click_row_by_text: strategy 5 failed for '%s': %s", text, exc)

        # 策略 6: 兜底直接 force click 文本
        try:
            await text_loc.click(force=True, timeout=self.page._tm(3000))
            logger.debug("click_row_by_text: strategy 6 SUCCESS (text force click) for '%s'", text)
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("click_row_by_text: strategy 6 failed for '%s': %s", text, exc)

        # 全部失败：抛出明确异常（不再静默），附带 DOM 调试信息
        logger.error("click_row_by_text: ALL strategies failed for '%s'. Dumping semantics...", text)
        self.page._dump_dom_debug(f"click_row_all_failed_{text}")
        self.page._dump_semantics_debug()
        raise RuntimeError(
            f"click_row_by_text: all 6 strategies failed for text='{text}'. "
            "Most likely the row Container.on_click callback is not wired, "
            "or the role=button semantics node is not generated by ink=True, "
            "or the row is rendered outside the visible viewport with zero bounding_box. "
            "Check DEBUG logs above for per-strategy failure details."
        )

    async def open_detail_dialog(self, row_text: str, max_attempts: int = 3) -> None:
        """点击行打开详情对话框，带重试验证对话框已渲染。

        根因（Phase 9.2）：``click_row_by_text`` 在 CI CanvasKit 抖动下，
        mouse.click 执行了但 Flutter hit-testing 偶发未触发 ``on_tap``，
        导致对话框没打开。此方法封装"点击 + 验证 + 重试"逻辑，
        确保对话框真正打开后才返回。

        验证标志：关闭按钮（``role="button", name=I18n.get("common_close")``）
        在对话框打开时立即渲染（不像 K 线图异步加载），是稳定的渲染完成标志。
        """
        close_text = I18n.get("common_close")
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self.click_row_by_text(row_text, timeout_ms=TIMEOUTS.INTERACTION)
                close_btn = self.page.page.get_by_role("button", name=close_text)
                await close_btn.wait_for(state="attached", timeout=self.page._tm(TIMEOUTS.FAST))
                if attempt > 1:
                    logger.info("open_detail_dialog: succeeded on attempt %d for '%s'", attempt, row_text)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.debug(
                    "open_detail_dialog: attempt %d/%d failed for '%s': %s",
                    attempt,
                    max_attempts,
                    row_text,
                    exc,
                )
        raise AssertionError(
            f"open_detail_dialog: dialog did not open after {max_attempts} attempts for '{row_text}'. "
            f"Last error: {last_exc}"
        ) from last_exc
