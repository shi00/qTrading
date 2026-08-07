"""AnchorPage: 基于 EIDS + AnchorKind 的精确 Flet 控件定位。

CanvasKit 双轨语义映射（PoC 实证, reviews/poc/EVIDENCE.md）：
  INTERACTIVE/INPUT → [aria-label=EID] + 内层 [flt-tappable] / input
  LABEL/COMPLEX     → textContent 匹配

click 一律用 `page.mouse.click(bbox_center)`，因为 CanvasKit 不响应合成 DOM 事件。
"""

import time
from typing import Any

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import AnchorKind, Eid


class AnchorPage:
    """基于 EIDS 的精确 anchor 操作，与 FletPage 组合使用。

    分工：
      AnchorPage: click/fill/select_option/hover/expect_visible/expect_hidden/count (anchor-based)
      FletPage:   open() / expect_text / has_text (页面初始化 + 文本断言)
      page.mouse / page.keyboard / expect_download 通过 AnchorPage.page 直接暴露
    """

    def __init__(
        self,
        page: Page,
        fp: FletPage,
        timeout_multiplier: float | None = None,
    ):
        self.page = page
        self._fp = fp
        # None 时复用 FletPage 的 multiplier (CI slow marker 已由 conftest 设置)
        self._tm_mult = (
            timeout_multiplier if timeout_multiplier is not None else fp._timeout_multiplier  # noqa: SLF001  # 访问 FletPage 私有属性以对齐 timeout
        )

    def _tm(self, ms: int) -> int:
        return int(ms * self._tm_mult)

    # ----------------------------------------------------------------
    # 定位分派: 按 AnchorKind 走两种 locator 路径
    # ----------------------------------------------------------------

    def _locator_by_aria(self, eid_str: str) -> Locator:
        """INTERACTIVE/INPUT: 命中 aria-label 节点。

        Flet 0.86.3 CanvasKit 引擎将 ft.Semantics(label=EID) 与内层 Button 自带 label
        合并为 "<显示名>\\nEID"（见 e2e-artifacts/*-semantics.json 快照，与
        flet_page.py:440 已有的合并注释一致）；INPUT 类 ft.TextField 无自身 semantic
        label，保留纯 EID 形态。两种形态均以 EID **结尾**，用 CSS 后缀匹配 ``$=``
        精确命中。

        为什么不用 ``*=`` 子串匹配：EID 命名空间存在前缀重叠（如
        ``e2e.settings.tab.data`` 是 ``e2e.settings.tab.database`` 的前缀），
        ``*=`` 会同时匹配两者导致 strict mode violation（PR-478 CI 实证）。
        ``$=`` 要求 aria-label 以 EID 结尾，前缀重叠不再误命中。

        无后缀重叠由 ui/testing/e2e_ids.py 的 EID 命名规范 +
        tests/unit/ui/test_anchor.py 的 no-prefix 断言保证（附录 A 命名规范
        禁止 EID 之间互为后缀）。
        """
        return self.page.locator(f'flt-semantics[aria-label$="{eid_str}"]')

    async def _locate_by_text(
        self, eid_str: str, exact: bool, role_filter: str | None = None
    ) -> dict[str, float] | None:
        """LABEL/COMPLEX: textContent 匹配 (JS 侧, 因 Playwright :text-is 对非 role 节点不精确).

        返回节点 bounding rect dict {x,y,w,h} 或 None.
        exact=True:  textContent.trim() === eid_str       (LABEL)
        exact=False: textContent.trim().startsWith(eid_str + [. | \\n])
                     (COMPLEX：Dropdown 用 "."，GestureDetector 合并节点用 "\\n"）

        前缀匹配用**分隔符边界**规避嵌套冲突：避免
        `e2e.screener.run_button` 误命中 `e2e.screener.run_button_v2`。
        分隔符支持 `.`（EID 命名空间层级）与 `\\n`（GD 合并节点 textContent 里
        EID 与显示文本之间的换行分隔，PoC A7 实证）。
        """
        return await self.page.evaluate(
            r"""(args) => {
                const {label, exact, roleFilter} = args;
                const q = roleFilter
                    ? 'flt-semantics[role="' + roleFilter + '"]'
                    : 'flt-semantics';
                const el = Array.from(document.querySelectorAll(q))
                    .find(e => {
                        const t = (e.textContent || '').trim();
                        if (exact) return t === label || t.startsWith(label + '\n');
                        // 前缀匹配: t === label, 或 t 以 label + "." / "\n" 开头
                        // ("." = EID 命名空间层级; "\n" = GD 合并节点 EID 与显示文本分隔)
                        return t === label
                            || t.startsWith(label + '.')
                            || t.startsWith(label + '\n');
                    });
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""",
            {"label": eid_str, "exact": exact, "roleFilter": role_filter},
        )

    async def _wait_for_text_anchor(
        self,
        eid_str: str,
        exact: bool,
        role_filter: str | None,
        timeout_ms: int,
    ) -> dict[str, float]:
        """LABEL/COMPLEX 等待: 轮询 textContent 匹配直到 deadline.

        用 time.monotonic() 计算 deadline, 避免 elapsed 累加误差（_locate_by_text
        的 page.evaluate 耗时未计入 elapsed 会导致 CI slow 环境漏检）.
        """
        deadline = time.monotonic() + self._tm(timeout_ms) / 1000
        step_s = 0.2
        while time.monotonic() < deadline:
            r = await self._locate_by_text(eid_str, exact=exact, role_filter=role_filter)
            if r and r["w"] > 0 and r["h"] > 0:
                return r
            await self.page.wait_for_timeout(int(step_s * 1000))
        raise RuntimeError(
            f"AnchorPage: textContent anchor {eid_str!r} (exact={exact}, "
            f"role_filter={role_filter}) not found in {self._tm(timeout_ms)}ms"
        )

    async def _locate_inner_tappable_bbox(self, eid_str: str, timeout_ms: int) -> dict[str, Any]:
        """INTERACTIVE: 定位 [aria-label] 节点（含后代 [flt-tappable] 或自身可点击）并返回 bbox.

        优先查找后代 `flt-tappable`（Button 等标准交互控件生成的语义节点）。
        若后代不存在，回退到外层 `aria-label` 节点本身（`button=True` +
        GestureDetector.on_tap 在 Semantics 节点自身生成可点击语义）。
        """
        outer = self._locator_by_aria(eid_str)
        await outer.wait_for(state="attached", timeout=self._tm(timeout_ms))
        inner = outer.first.locator("flt-semantics[flt-tappable]").first
        try:
            await inner.wait_for(state="visible", timeout=self._tm(timeout_ms))
            box = await inner.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                return dict(box)
        except Exception:
            pass  # 后代 flt-tappable 不存在，回退到外层节点
        # 回退：button=True 场景下，Semantics 节点自身即为可点击节点
        box = await outer.first.bounding_box()
        if not box or box["width"] == 0 or box["height"] == 0:
            raise RuntimeError(
                f"AnchorPage: no valid clickable node for [aria-label={eid_str!r}]. "
                f"bbox={box}. Check Semantics(container=True, button=True) wraps an interactive control."
            )
        return dict(box)

    async def _locate_inner_input_bbox(self, eid_str: str, timeout_ms: int) -> dict[str, Any]:
        """INPUT: 定位 [aria-label] 后代 input/textarea 并返回 bbox."""
        outer = self._locator_by_aria(eid_str)
        await outer.wait_for(state="attached", timeout=self._tm(timeout_ms))
        inner = outer.first.locator("input, textarea").first
        await inner.wait_for(state="visible", timeout=self._tm(timeout_ms))
        box = await inner.bounding_box()
        if not box:
            raise RuntimeError(f"AnchorPage: no bbox for input under [aria-label={eid_str!r}]")
        return dict(box)

    @staticmethod
    def _normalize_box(box: dict[str, Any]) -> dict[str, float]:
        """_locate_by_text 返回 {x,y,w,h}，统一切换为 {x,y,width,height}."""
        return {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["w"]),
            "height": float(box["h"]),
        }

    # ----------------------------------------------------------------
    # 核心操作: click / fill / select_option / hover
    # ----------------------------------------------------------------

    async def click(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """按 AnchorKind 分派 click 目标 bbox, 一律走真实鼠标事件."""
        eid_str, kind = eid
        if kind == AnchorKind.INTERACTIVE:
            try:
                box = await self._locate_inner_tappable_bbox(eid_str, timeout_ms)
            except PlaywrightTimeoutError as exc:
                # 分类误标兜底：检查是否实际是 COMPLEX（GD 合并节点 + label 落 textContent）
                # 命中则抛出 actionable 报错，而非模糊 16s timeout（PoC A7 实证）
                probe = await self._locate_by_text(eid_str, exact=False, role_filter="button")
                if probe:
                    raise RuntimeError(
                        f"AnchorPage: EID {eid_str!r} declared INTERACTIVE but DOM shows "
                        f"COMPLEX pattern (label in textContent, role=button, merged node). "
                        f"Fix: change AnchorKind to COMPLEX in ui/testing/e2e_ids.py. "
                        f"Root cause: outer control is GestureDetector/Container(on_click), "
                        f"not ft.Button. See PoC A7 (reviews/poc/EVIDENCE.md)."
                    ) from exc
                raise
        elif kind == AnchorKind.COMPLEX:
            # COMPLEX 走 textContent 匹配, 需显式等待 (与 INTERACTIVE/INPUT 的 wait_for 对齐)
            r = await self._wait_for_text_anchor(eid_str, exact=False, role_filter="button", timeout_ms=timeout_ms)
            box = self._normalize_box(r)
        elif kind == AnchorKind.INPUT:
            box = await self._locate_inner_input_bbox(eid_str, timeout_ms)
        else:  # LABEL
            raise RuntimeError(f"AnchorPage.click: LABEL kind ({eid_str!r}) is display-only, not clickable")
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await self.page.mouse.click(cx, cy)

    async def hover(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, kind = eid
        if kind == AnchorKind.INTERACTIVE:
            box = await self._locate_inner_tappable_bbox(eid_str, timeout_ms)
        elif kind == AnchorKind.INPUT:
            box = await self._locate_inner_input_bbox(eid_str, timeout_ms)
        elif kind == AnchorKind.COMPLEX:
            r = await self._wait_for_text_anchor(eid_str, exact=False, role_filter="button", timeout_ms=timeout_ms)
            box = self._normalize_box(r)
        else:  # LABEL
            r = await self._wait_for_text_anchor(eid_str, exact=True, role_filter=None, timeout_ms=timeout_ms)
            box = self._normalize_box(r)
        await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    async def click_label(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 LABEL kind anchor 的位置（依赖事件冒泡到可点击父容器）。

        适用场景：LABEL anchor 包裹的控件本身不可点击，但位于可点击父容器内
        （如 ``NavigationRailDestination`` 的 label）。``click`` 方法拒绝 LABEL kind
        （语义上 LABEL 是 display-only），本方法显式声明"点击 LABEL 位置"的意图，
        通过真实鼠标事件触发父容器的 hit-testing。

        与 ``click`` 的区别：``click`` 按 AnchorKind 分派到 INTERACTIVE/COMPLEX/INPUT
        的可点击节点；``click_label`` 直接点击 LABEL textContent 的 bbox 中心，
        依赖事件冒泡。
        """
        eid_str, kind = eid
        if kind != AnchorKind.LABEL:
            raise RuntimeError(
                f"AnchorPage.click_label: only supports LABEL, got {kind} for {eid_str!r}. "
                f"Use click() for INTERACTIVE/COMPLEX/INPUT."
            )
        r = await self._wait_for_text_anchor(eid_str, exact=True, role_filter=None, timeout_ms=timeout_ms)
        box = self._normalize_box(r)
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await self.page.mouse.click(cx, cy)

    async def fill(self, eid: Eid, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, kind = eid
        if kind != AnchorKind.INPUT:
            raise RuntimeError(f"AnchorPage.fill: only supports INPUT, got {kind} for {eid_str!r}")
        box = await self._locate_inner_input_bbox(eid_str, timeout_ms)
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await self.page.mouse.click(cx, cy)
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.type(value, delay=30)

    async def select_option(
        self,
        dropdown_eid: Eid,
        option_text: str,
        timeout_ms: int = TIMEOUTS.INTERACTION,
    ) -> None:
        """打开 anchor 化的 Dropdown 并选中 option_text.

        Dropdown 是 COMPLEX kind, 顶层节点即可点击展开.
        option 面板节点由 Flet 动态生成, 无 anchor 覆盖, 仍用文本匹配.
        剩余风险: 同视图两个 Dropdown 出现同名选项 - EIDS 命名规范强制唯一.

        PR-478 修复 (4 个复合缺陷, 见 reviews/问题定位.md):
        - C1: ``self.click(...)`` 后主动 poll ``aria-expanded="true"``, 未展开
              立即抛错 ``dropdown did not expand``, 不再静默等待选项 20s.
        - C2: 选项点击用 ``page.mouse.click(cx, cy)`` 坐标点击, 与
              ``AnchorPage.click`` 一致, 绕开 CanvasKit ``flt-semantics`` 上
              不稳定的 actionability 检查.
        - C3: 选项定位用精确文本匹配 (等值 / 空格 / ``(`` / ``\\n`` 分隔) 取代
              ``filter(has_text=...)`` 子串匹配, 避免短文本 (如"代码") 误命中.
        - C4: 展开前若残留 ``aria-expanded="true"`` 按 ``Escape`` 先收合, 避免
              第二次 click 被 Material 3 DropdownMenu 解读为"关闭"而非"打开".
        - 原有"收合校验"逻辑保留 (``selection did not settle``), 向后兼容.
        """
        eid_str, kind = dropdown_eid
        # 0. 若 Dropdown 按钮当前已选中该选项且菜单未展开，则直接返回无需重复点击
        current_dd_text = await self.page.evaluate(
            """(eid) => {
                const el = Array.from(document.querySelectorAll('flt-semantics[role="button"]'))
                    .find(e => (e.textContent || '').trim().startsWith(eid));
                return el ? (el.textContent || '') : '';
            }""",
            eid_str,
        )
        if (
            option_text in current_dd_text
            and not await self.page.locator('flt-semantics[role="button"][aria-expanded="true"]').count()
        ):
            return

        async def _find_option_element() -> Any:
            option_handle = await self.page.evaluate_handle(
                r"""(args) => {
                    const {text, dropdownEid} = args;
                    const selectors = [
                        'flt-semantics[role="option"]',
                        'flt-semantics[role="menuitem"]',
                        'flt-semantics[role="button"]:not([aria-expanded])',
                        'flt-semantics:not([aria-expanded])'
                    ];
                    const rawEls = Array.from(document.querySelectorAll(selectors.join(',')));
                    const els = rawEls.filter(e => {
                        const r = e.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) return false;
                        const t = (e.textContent || '').trim();
                        // 仅过滤当前 dropdown 的触发器
                        // if (t.includes('e2e.')) return false;  // 移除过于宽泛的过滤
                        if (dropdownEid && t.startsWith(dropdownEid + '\n')) return false;
                        return true;
                    });

                    const normText = text.trim();

                    // 0. eid.option 格式精确匹配 (如 "e2e.data.dropdown.table.daily_quotes\n日线行情")
                    if (dropdownEid) {
                        const fullEid = dropdownEid + '.' + normText;
                        let found = els.find(e => {
                            const t = (e.textContent || '').trim();
                            return t.startsWith(fullEid + '\n') || t === fullEid;
                        });
                        if (found) return found;
                    }

                    // 1. 精确匹配
                    let found = els.find(e => (e.textContent || '').trim() === normText);
                    if (found) return found;

                    // 2. 前缀/别名匹配 (e.g. "stock_basic (股票列表)" 或 "stock_basic (Stock Basic)")
                    found = els.find(e => {
                        const t = (e.textContent || '').trim();
                        return t.startsWith(normText + ' ')
                            || t.startsWith(normText + '(')
                            || t.startsWith(normText + '\n');
                    });
                    if (found) return found;

                    // 3. 括号/包含匹配
                    found = els.find(e => {
                        const t = (e.textContent || '').trim();
                        return t.includes('(' + normText + ')') || t.includes(normText);
                    });
                    return found || null;
                }""",
                {"text": option_text, "dropdownEid": eid_str},
            )
            return option_handle.as_element()

        # 1. 优先探测选项是否已经在 DOM 中呈展开态
        option_element = await _find_option_element()
        if not option_element:
            # 展开菜单：获取 Dropdown bbox
            r = await self._wait_for_text_anchor(eid_str, exact=False, role_filter="button", timeout_ms=timeout_ms)
            box = self._normalize_box(r)

            # 策略 A: 点击右侧下拉箭角 (width - 15px), 直接触发表单展开
            arrow_x = box["x"] + max(box["width"] - 15.0, box["width"] / 2)
            arrow_y = box["y"] + box["height"] / 2
            await self.page.mouse.click(arrow_x, arrow_y)

            # 短轮询等待选项出现（最多等待 2s）
            quick_deadline = time.monotonic() + min(self._tm(timeout_ms) / 1000, 2.0)
            while time.monotonic() < quick_deadline:
                option_element = await _find_option_element()
                if option_element:
                    break
                await self.page.wait_for_timeout(100)

            # 策略 B: 若未出现，点击中心并按 ArrowDown 强制唤起下拉
            if not option_element:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                await self.page.mouse.click(cx, cy)
                await self.page.keyboard.press("ArrowDown")

                expand_deadline = time.monotonic() + self._tm(timeout_ms) / 1000
                while time.monotonic() < expand_deadline:
                    option_element = await _find_option_element()
                    if option_element:
                        break
                    await self.page.wait_for_timeout(100)

        if not option_element:
            raise RuntimeError(
                f"AnchorPage.select_option: option not found in dropdown menu: "
                f"dropdown={eid_str!r}, option={option_text!r}"
            )

        # 2. 用 force=True 强力点击选项，在 ElementHandle 被 DOM 卸载时重新获取
        for click_attempt in range(3):
            try:
                await option_element.click(force=True)
                break
            except Exception as click_err:
                if click_attempt < 2 and "not attached" in str(click_err).lower():
                    await self.page.wait_for_timeout(150)
                    fresh_elem = await _find_option_element()
                    if fresh_elem:
                        option_element = fresh_elem
                        continue
                raise

        # 3. 等待菜单收合与 Flet 事件循环处理
        settle_deadline = time.monotonic() + 1.0
        while time.monotonic() < settle_deadline:
            try:
                if not await option_element.is_visible():
                    break
            except Exception:
                break
            await self.page.wait_for_timeout(100)

    # ----------------------------------------------------------------
    # 断言与探测: expect_visible/expect_hidden/count
    # ----------------------------------------------------------------

    async def expect_visible(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, kind = eid
        if kind in (AnchorKind.INTERACTIVE, AnchorKind.INPUT):
            await self._locator_by_aria(eid_str).first.wait_for(state="visible", timeout=self._tm(timeout_ms))
        else:
            # LABEL/COMPLEX: 轮询 textContent 匹配, 无 Playwright locator 可 wait
            role_filter = "button" if kind == AnchorKind.COMPLEX else None
            await self._wait_for_text_anchor(
                eid_str,
                exact=(kind == AnchorKind.LABEL),
                role_filter=role_filter,
                timeout_ms=timeout_ms,
            )

    async def expect_hidden(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, kind = eid
        if kind in (AnchorKind.INTERACTIVE, AnchorKind.INPUT):
            await self._locator_by_aria(eid_str).first.wait_for(state="hidden", timeout=self._tm(timeout_ms))
        else:
            deadline = time.monotonic() + self._tm(timeout_ms) / 1000
            step_s = 0.2
            role_filter = "button" if kind == AnchorKind.COMPLEX else None
            while time.monotonic() < deadline:
                r = await self._locate_by_text(eid_str, exact=(kind == AnchorKind.LABEL), role_filter=role_filter)
                if not r:
                    return
                await self.page.wait_for_timeout(int(step_s * 1000))
            raise RuntimeError(f"AnchorPage.expect_hidden: {eid_str!r} still present in {self._tm(timeout_ms)}ms")

    async def count(self, eid: Eid) -> int:
        eid_str, kind = eid
        if kind in (AnchorKind.INTERACTIVE, AnchorKind.INPUT):
            return await self._locator_by_aria(eid_str).count()
        role_filter = "button" if kind == AnchorKind.COMPLEX else None
        return int(
            await self.page.evaluate(
                r"""(args) => {
                    const q = args.roleFilter
                        ? 'flt-semantics[role="' + args.roleFilter + '"]'
                        : 'flt-semantics';
                    return Array.from(document.querySelectorAll(q))
                        .filter(e => {
                            const t = (e.textContent || '').trim();
                            if (args.exact) return t === args.label || t.startsWith(args.label + '\n');
                            // 前缀匹配同 _locate_by_text: "." 或 "\n" 分隔
                            return t === args.label
                                || t.startsWith(args.label + '.')
                                || t.startsWith(args.label + '\n');
                        }).length;
                }""",
                {
                    "label": eid_str,
                    "exact": kind == AnchorKind.LABEL,
                    "roleFilter": role_filter,
                },
            )
        )
