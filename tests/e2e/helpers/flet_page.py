import logging
from typing import Any

from playwright.async_api import Page, Playwright, Browser, BrowserContext

from tests.e2e.timeouts import TIMEOUTS

logger = logging.getLogger(__name__)


class FletPage:
    def __init__(self, page: Page, timeout_multiplier: float = 1.0):
        self.page = page
        self._pw_context: tuple[Playwright, Browser, BrowserContext, Page, Any] | None = None
        self._timeout_multiplier = timeout_multiplier

    def _tm(self, base_ms: int) -> int:
        return int(base_ms * self._timeout_multiplier)

    def bind_context(self, pw_tuple: tuple[Playwright, Browser, BrowserContext, Page, Any]) -> None:
        self._pw_context = pw_tuple

    def get_context(self) -> tuple[Playwright, Browser, BrowserContext, Page, Any] | None:
        return self._pw_context

    async def open(self, url: str, timeout_ms: int = TIMEOUTS.PAGE_OPEN) -> None:
        scaled = self._tm(timeout_ms)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=scaled)
        await self.page.wait_for_selector("flutter-view, flt-glass-pane, flt-semantics-placeholder", timeout=scaled)
        ph = self.page.locator("flt-semantics-placeholder")
        if await ph.count() > 0:
            await ph.first.dispatch_event("click")
            await self.page.wait_for_selector("flt-semantics", timeout=scaled)
        await self.page.wait_for_timeout(int(3000 * self._timeout_multiplier))
        for _ in range(10):
            count = await self.page.locator("flt-semantics, [role]").count()
            if count >= 5:
                break
            await self.page.wait_for_timeout(1000)

    async def _click_with_fallback(self, name: str, role: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        scaled = self._tm(timeout_ms)
        btn = self.page.get_by_role(role, name=name)
        by_label = self.page.locator(
            f'[aria-label="{name}"], [aria-label*="{name}"]:not([role="tabpanel"]):not([role="group"]):not([role="region"])'
        )
        text_loc = self.page.get_by_text(name, exact=False)

        # Combine all candidates using .or_() so that we wait for whichever appears first!
        loc_combined = btn.or_(by_label).or_(text_loc).first
        try:
            await loc_combined.wait_for(state="attached", timeout=scaled)
        except Exception as e:
            logger.debug("Combined click target not found for '%s' (role: %s): %s", name, role, e)

        if await btn.count() > 0:
            try:
                await btn.first.click(timeout=self._tm(3000))
                return
            except Exception:
                pass

        if await by_label.count() > 0:
            try:
                await by_label.first.click(timeout=self._tm(3000))
                return
            except Exception:
                pass

        if await text_loc.count() > 0:
            target = text_loc.first
            try:
                box = await target.bounding_box()
                if box:
                    await self.page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    return
            except Exception:
                pass
            try:
                await target.click(force=True, timeout=self._tm(3000))
                return
            except Exception:
                pass

        # If everything fails, try clicking the combined locator to trigger standard playwright error
        await loc_combined.click(timeout=self._tm(3000))

    async def click_button(self, name: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        await self._click_with_fallback(name, "button", timeout_ms)

    async def click_tab(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 Tab 按钮（Flet 0.28.3 ElevatedButton(icon+text) 兼容）。"""
        await self._click_with_fallback(text, "button", timeout_ms)

    async def click_text(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        scaled = self._tm(timeout_ms)
        loc_text = self.page.get_by_text(text, exact=False)
        loc_aria = self.page.locator(
            f'[aria-label*="{text}"]:not([role="tabpanel"]):not([role="group"]):not([role="region"])'
        )
        loc_combined = loc_text.or_(loc_aria).first
        await loc_combined.wait_for(state="attached", timeout=scaled)
        await loc_combined.click(timeout=scaled, force=True)

    async def fill_textbox(self, label: str, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        scaled = self._tm(timeout_ms)
        loc1 = self.page.get_by_role("textbox", name=label)
        loc2 = self.page.locator(
            f'input[aria-label*="{label}" i], textarea[aria-label*="{label}" i], [role="textbox"][aria-label*="{label}" i]'
        )
        loc_combined = loc1.or_(loc2).first

        try:
            try:
                await loc_combined.wait_for(state="visible", timeout=scaled)
                el = loc_combined
            except Exception:
                # Fallback: check if there's exactly 1 textbox on the page
                try:
                    loc3 = self.page.get_by_role("textbox")
                    await loc3.first.wait_for(state="visible", timeout=self._tm(2000))
                    if await loc3.count() == 1:
                        el = loc3.first
                    else:
                        el = loc1.first
                except Exception:
                    el = loc1.first

            await el.click(timeout=scaled)
            try:
                await el.fill(value, timeout=scaled)
            except Exception:
                await el.clear()
                await el.type(value, delay=30)
        except Exception:
            try:
                # [PITFALL_WARNING] Flet TextField 元素定位黑洞
                # 坑点：在 Flet Web 中，get_by_role("textbox", name="xxx") 几乎永远找不到输入框。
                # 原因：CanvasKit 渲染模式下，DOM 节点并不是标准的 input，而是一个扁平的 <flt-semantics>。
                #      Flet 经常把 TextField 的 label 文本“吸附”到极远的父级容器的 aria-label 上，
                #      或者完全吞噬角色属性。这导致精确匹配文本框根本不可能。
                # 应对：我们只能采用极其宽泛的模糊匹配（见上方的 [aria-label*="..."]）。
                #      如果连模糊匹配都失败了，这里提供了一个“最后的倔强” fallback：
                #      尝试直接去点击那个 label 的纯文本节点，然后用键盘模拟全选删除和输入。
                # 针对 Flet multiline=True 的黑盒行为：它把 label 文本合并到了父容器（如 tabpanel）的 aria-label 中
                logger.warning(
                    f"fill_textbox standard method failed for label '{label}', trying fallback (aria-label click + keyboard)"
                )

                # 寻找 aria-label 包含目标文本的节点
                label_loc = self.page.locator(f'[aria-label*="{label}"]').first
                await label_loc.wait_for(state="attached", timeout=3000)

                # Flet 的这种父节点通常有 pointer-events: none，但 force=True 会计算中心坐标并触发真正的鼠标点击
                # 由于这是整个区域的父节点，点击中心极大概率落在 expand=True 的多行文本框内
                await label_loc.click(force=True)

                # 清除原有内容并输入
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")
                await self.page.keyboard.type(value, delay=50)
                return
            except Exception as fallback_exc:
                logger.error(f"fill_textbox fallback failed for label '{label}': {fallback_exc}")
                raise

    async def select_dropdown(
        self, current_or_label: str, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION
    ) -> None:
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
        elif "volume_breakout" in opt_lower:
            opt_match_key = "放量突破"
        elif "oversold" in opt_lower:
            opt_match_key = "超跌反弹"

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
                        await loc.click(timeout=self._tm(3000), force=True)
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
                        await target.click(timeout=self._tm(3000), force=True)
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

        wait_cycles = max(1, (self._tm(timeout_ms) // 2) // 200)
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

    async def expect_text(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        scaled = self._tm(timeout_ms)
        loc_text = self.page.get_by_text(text, exact=False)
        loc_aria = self.page.locator(
            f'[aria-label*="{text}"]:not([role="tabpanel"]):not([role="group"]):not([role="region"])'
        )
        loc_combined = loc_text.or_(loc_aria).first
        try:
            await loc_combined.wait_for(state="attached", timeout=scaled)
            return
        except Exception as e:
            logger.debug("Combined locator wait_for failed for text '%s': %s", text, e)

        # Fallback: poll input field values
        import time

        start_time = time.time()
        while (time.time() - start_time) * 1000 < scaled:
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
            await self.page.wait_for_timeout(200)

        if logger.isEnabledFor(logging.DEBUG):
            self._dump_dom_debug(text)

        # [PITFALL_WARNING] expect_text 报错 "Timeout 1ms exceeded" 的幻觉
        # 坑点：当这个函数因为超时找不到元素而报错时，Playwright 抛出的异常会显示 "Timeout 1ms exceeded"。
        # 原因：由于 Flet 的渲染特殊性，本函数内部实现了一个总时长为 timeout_ms 的轮询等待循环（见上方）。
        #      如果轮询结束仍然没找到文本，为了向外抛出带有完整堆栈信息的 Playwright TimeoutError，
        #      这里故意执行了一个 timeout=1ms 的 wait_for 触发报错。
        # 正确做法：看到 "Timeout 1ms exceeded" 报错时，不要以为是 timeout_ms 参数没传进去，
        #         这实际上意味着它已经实打实地等待了你传入的足额时间（比如 5000ms 或 15000ms）后依然失败。
        await loc_combined.wait_for(state="attached", timeout=1)

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
