import logging
from typing import Any

from playwright.async_api import Page, Playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)


class FletPage:
    def __init__(self, page: Page):
        self.page = page
        self._pw_context: tuple[Playwright, Browser, BrowserContext, Page, Any] | None = None

    def bind_context(self, pw_tuple: tuple[Playwright, Browser, BrowserContext, Page, Any]) -> None:
        self._pw_context = pw_tuple

    def get_context(self) -> tuple[Playwright, Browser, BrowserContext, Page, Any] | None:
        return self._pw_context

    async def open(self, url: str, timeout_ms: int = 45000) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.page.wait_for_selector("flutter-view, flt-glass-pane, flt-semantics-placeholder", timeout=timeout_ms)
        ph = self.page.locator("flt-semantics-placeholder")
        if await ph.count() > 0:
            await ph.first.dispatch_event("click")
            await self.page.wait_for_selector("flt-semantics", timeout=timeout_ms)
        await self.page.wait_for_timeout(3000)
        for _ in range(10):
            count = await self.page.locator("flt-semantics, [role]").count()
            if count >= 5:
                break
            await self.page.wait_for_timeout(1000)

    async def _click_with_fallback(self, name: str, role: str, timeout_ms: int = 8000) -> None:
        """使用多策略回退点击机制，兼容 Flet 0.28.3 因带 icon 拆分语义节点的问题。"""
        btn = self.page.get_by_role(role, name=name)
        if await btn.count() > 0:
            try:
                await btn.first.click(timeout=3000)
                return
            except Exception:
                pass

        by_label = self.page.locator(f'flt-semantics[aria-label="{name}"]')
        if await by_label.count() > 0:
            try:
                await by_label.first.click(timeout=3000)
                return
            except Exception:
                pass

        text_loc = self.page.get_by_text(name, exact=True).first
        await text_loc.wait_for(state="attached", timeout=timeout_ms)
        box = await text_loc.bounding_box()
        if box:
            await self.page.mouse.click(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
            return

        await text_loc.click(force=True, timeout=timeout_ms)

    async def click_button(self, name: str, timeout_ms: int = 8000) -> None:
        await self._click_with_fallback(name, "button", timeout_ms)

    async def click_tab(self, text: str, timeout_ms: int = 8000) -> None:
        """点击 Tab 按钮（Flet 0.28.3 ElevatedButton(icon+text) 兼容）。"""
        await self._click_with_fallback(text, "button", timeout_ms)

    async def click_text(self, text: str, timeout_ms: int = 8000) -> None:
        loc = self.page.get_by_text(text, exact=False).first
        await loc.wait_for(state="attached", timeout=timeout_ms)
        await loc.click(timeout=timeout_ms)

    async def fill_textbox(self, label: str, value: str, timeout_ms: int = 8000) -> None:
        el = self.page.get_by_role("textbox", name=label).first
        await el.wait_for(state="visible", timeout=timeout_ms)
        await el.click(timeout=timeout_ms)
        try:
            await el.fill(value, timeout=timeout_ms)
        except Exception:
            await el.clear()
            await el.type(value, delay=30)

    async def select_dropdown(self, current_or_label: str, option_text: str, timeout_ms: int = 8000) -> None:
        norm_label = current_or_label.lower()
        match_keys = [current_or_label, norm_label]
        if "语言" in norm_label or "language" in norm_label:
            match_keys.extend(["language", "语言", "locale", "简体中文", "english", "chinese", "简体中文 / english"])
        elif "主题" in norm_label or "theme" in norm_label:
            match_keys.extend(["theme", "主题", "浅色", "深色", "light", "dark", "浅色 / 深色", "light / dark"])

        match_keys = list(set(match_keys))

        opt_match_key = option_text
        opt_lower = option_text.lower()
        if "深色" in opt_lower or "dark" in opt_lower:
            opt_match_key = "dark"
        elif "浅色" in opt_lower or "light" in opt_lower:
            opt_match_key = "light"
        elif "简体中文" in opt_lower or "chinese" in opt_lower or "zh" in opt_lower:
            opt_match_key = "简体中文"
        elif "english" in opt_lower or "en" in opt_lower:
            opt_match_key = "english"

        def get_option_locators():
            return [
                self.page.locator(f'[role="option"][aria-label*="{option_text}" i]').first,
                self.page.locator(f'[role="option"][aria-label*="{opt_match_key}" i]').first,
                self.page.locator('[role="option"]').filter(has_text=option_text).first,
                self.page.locator('[role="option"]').filter(has_text=opt_match_key).first,
                self.page.locator('[role="button"]').filter(has_text=option_text).first,
                self.page.locator('[role="button"]').filter(has_text=opt_match_key).first,
                self.page.locator('[role="menuitem"]').filter(has_text=option_text).first,
                self.page.locator('[role="menuitem"]').filter(has_text=opt_match_key).first,
                self.page.locator(f'[aria-label="{option_text}"]').first,
                self.page.locator(f'[aria-label="{opt_match_key}"]').first,
                self.page.locator(f'[role="button"][aria-label*="{option_text}" i]').first,
                self.page.locator(f'[role="button"][aria-label*="{opt_match_key}" i]').first,
                self.page.locator(f'[role="menuitem"][aria-label*="{option_text}" i]').first,
                self.page.locator(f'[role="menuitem"][aria-label*="{opt_match_key}" i]').first,
            ]

        async def check_option_visible() -> bool:
            for loc in get_option_locators():
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        return True
                except Exception:
                    pass
            return False

        async def click_option() -> bool:
            for idx, loc in enumerate(get_option_locators()):
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        if logger.isEnabledFor(logging.DEBUG):
                            desc = await loc.evaluate("""e => ({
                                tag: e.tagName,
                                role: e.getAttribute('role'),
                                aria: e.getAttribute('aria-label') || '',
                                text: e.textContent || '',
                                rect: e.getBoundingClientRect().toJSON()
                            })""")
                            logger.debug(
                                "尝试点击选项候选[%d]: tag=%s, role=%s, aria='%s', text='%s', rect=%s",
                                idx,
                                desc["tag"],
                                desc["role"],
                                desc["aria"],
                                desc["text"],
                                desc["rect"],
                            )
                        await loc.click(timeout=3000, force=True)
                        logger.debug("选项候选[%d]点击成功", idx)
                        return True
                except Exception as ex:
                    logger.debug("选项候选[%d]点击抛出异常: %s", idx, ex)
            return False

        initial_visible = await check_option_visible()
        logger.debug("select_dropdown: initial_visible=%s", initial_visible)

        if not initial_visible:
            trigger_targets = []
            for key in match_keys:
                trigger_targets.append(self.page.locator(f'input[aria-label*="{key}" i]').first)
                trigger_targets.append(self.page.locator(f'[aria-label*="{key}" i]').first)
            trigger_targets.append(self.page.get_by_text(current_or_label, exact=False).first)

            triggered = False
            for idx, target in enumerate(trigger_targets):
                try:
                    if await target.count() > 0:
                        if logger.isEnabledFor(logging.DEBUG):
                            desc = await target.evaluate("""e => ({
                                tag: e.tagName,
                                role: e.getAttribute('role'),
                                aria: e.getAttribute('aria-label') || '',
                                text: e.textContent || '',
                                rect: e.getBoundingClientRect().toJSON()
                            })""")
                            logger.debug(
                                "尝试点击触发器候选[%d]: tag=%s, role=%s, aria='%s', text='%s', rect=%s",
                                idx,
                                desc["tag"],
                                desc["role"],
                                desc["aria"],
                                desc["text"],
                                desc["rect"],
                            )
                        await target.click(timeout=3000, force=True)
                        triggered = True
                        logger.debug("触发器候选[%d]点击成功", idx)
                        break
                except Exception as ex:
                    logger.debug("触发器候选[%d]点击失败: %s", idx, ex)
                    continue

            if triggered:
                for _ in range(15):
                    await self.page.wait_for_timeout(300)
                    if await check_option_visible():
                        break

        wait_cycles = max(1, (timeout_ms // 2) // 200)
        option_ready = False
        for _ in range(wait_cycles):
            if await check_option_visible():
                option_ready = True
                break
            await self.page.wait_for_timeout(200)

        if not option_ready:
            if logger.isEnabledFor(logging.DEBUG):
                self._dump_semantics_debug()
            raise RuntimeError(f"Timeout waiting for option '{option_text}' (key: '{opt_match_key}') to appear")

        await self.page.wait_for_timeout(350)
        clicked = await click_option()
        if not clicked:
            raise RuntimeError(f"Failed to click option '{option_text}' (key: '{opt_match_key}')")

    async def expect_text(self, text: str, timeout_ms: int = 8000) -> None:
        """期望页面上存在指定文本。

        Flet 0.28.3 在列表/容器中可能将多个子控件的文本合并到父 group 节点的 aria-label 中。
        本方法采用"文本节点匹配"与"aria-label 模糊匹配"双重策略。
        """
        loc = self.page.get_by_text(text, exact=False).first
        try:
            await loc.wait_for(state="attached", timeout=2000)
            return
        except Exception:
            pass

        loc_aria = self.page.locator(f'[aria-label*="{text}"]').first
        try:
            await loc_aria.wait_for(state="attached", timeout=2000)
            return
        except Exception:
            pass

        try:
            inputs_match = self.page.locator("input")
            count = await inputs_match.count()
            for i in range(count):
                el = inputs_match.nth(i)
                val = await el.evaluate("e => e.value")
                if text.lower() in val.lower():
                    return
        except Exception:
            pass

        if logger.isEnabledFor(logging.DEBUG):
            self._dump_dom_debug(text)

        loc_final = self.page.locator(f'[aria-label*="{text}"]').first
        await loc_final.wait_for(state="attached", timeout=timeout_ms)

    async def has_text(self, text: str) -> bool:
        """检查页面上是否存在指定文本。"""
        if await self.page.get_by_text(text, exact=False).count() > 0:
            return True
        return await self.page.locator(f'[aria-label*="{text}"]').count() > 0

    async def dump_semantics(self) -> list[dict]:
        return await self.page.eval_on_selector_all(
            "flt-semantics, [role]",
            "els => els.map(e => ({role:e.getAttribute('role'), aria:e.getAttribute('aria-label'), text:(e.textContent||'').trim().slice(0,40)}))",
        )

    def _dump_semantics_debug(self) -> None:
        """在 DEBUG 级别输出当前语义树快照（仅 logger.isEnabledFor(DEBUG) 时调用）。"""
        import asyncio

        async def _dump():
            try:
                nodes = await self.page.eval_on_selector_all(
                    "flt-semantics, [role]",
                    """els => els.map(e => ({
                        tag: e.tagName,
                        role: e.getAttribute('role'),
                        aria: e.getAttribute('aria-label') || '',
                        id: e.id,
                        text: e.textContent || ''
                    }))""",
                )
                logger.debug("=== E2E DEBUG: CURRENT SEMANTICS NODES ===")
                for i, n in enumerate(nodes):
                    if n["role"] or n["aria"] or n["text"].strip():
                        logger.debug(
                            "[%d] tag=%s role=%s aria='%s' id=%s text='%s'",
                            i,
                            n["tag"],
                            n["role"],
                            n["aria"],
                            n["id"],
                            n["text"].strip()[:50],
                        )
                logger.debug("==========================================")
            except Exception as ex:
                logger.debug("Failed to dump debug semantics: %s", ex)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_dump())
        except RuntimeError:
            pass

    def _dump_dom_debug(self, text: str) -> None:
        """在 DEBUG 级别输出 DOM 状态（仅 logger.isEnabledFor(DEBUG) 时调用）。"""
        import asyncio

        async def _dump():
            try:
                inputs = await self.page.eval_on_selector_all(
                    "input",
                    """els => els.map(e => ({
                        tag: e.tagName,
                        value: e.value,
                        aria: e.getAttribute('aria-label') || '',
                        id: e.id,
                    }))""",
                )
                logger.debug("=== E2E DEBUG: ALL INPUT ELEMENTS ===")
                for idx, ip in enumerate(inputs):
                    logger.debug(
                        "[%d] tag=%s value='%s' aria='%s' id=%s", idx, ip["tag"], ip["value"], ip["aria"], ip["id"]
                    )

                nodes = await self.page.eval_on_selector_all(
                    "flt-semantics, [role]",
                    """els => els.map(e => ({
                        tag: e.tagName,
                        role: e.getAttribute('role'),
                        aria: e.getAttribute('aria-label') || '',
                        id: e.id,
                        text: e.textContent || ''
                    }))""",
                )
                logger.debug("=== E2E DEBUG: CURRENT SEMANTICS NODES (expect_text '%s') ===", text)
                for i, n in enumerate(nodes):
                    if n["role"] or n["aria"] or n["text"].strip():
                        logger.debug(
                            "[%d] tag=%s role=%s aria='%s' id=%s text='%s'",
                            i,
                            n["tag"],
                            n["role"],
                            n["aria"],
                            n["id"],
                            n["text"].strip()[:60],
                        )
                logger.debug("==========================================")
            except Exception as ex:
                logger.debug("Failed to dump debug DOM in expect_text: %s", ex)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_dump())
        except RuntimeError:
            pass
