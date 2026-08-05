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
            """(args) => {
                const {label, exact, roleFilter} = args;
                const q = roleFilter
                    ? 'flt-semantics[role="' + roleFilter + '"]'
                    : 'flt-semantics';
                const el = Array.from(document.querySelectorAll(q))
                    .find(e => {
                        const t = (e.textContent || '').trim();
                        if (exact) return t === label;
                        // 前缀匹配: t === label, 或 t 以 label + "." / "\\n" 开头
                        // ("." = EID 命名空间层级; "\\n" = GD 合并节点 EID 与显示文本分隔)
                        return t === label
                            || t.startsWith(label + '.')
                            || t.startsWith(label + '\\n');
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
        """
        eid_str, kind = dropdown_eid
        if kind != AnchorKind.COMPLEX:
            raise RuntimeError(f"AnchorPage.select_option: only supports COMPLEX, got {kind} for {eid_str!r}")
        await self.click(dropdown_eid, timeout_ms=timeout_ms)
        option_loc = (
            self.page.locator('flt-semantics[role="button"]:not([aria-expanded])').filter(has_text=option_text).first
        )
        await option_loc.wait_for(state="visible", timeout=self._tm(5000))
        box = await option_loc.bounding_box()
        if not box:
            raise RuntimeError(f"AnchorPage.select_option: no bbox for option '{option_text}'")
        await self.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

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
                """(args) => {
                    const q = args.roleFilter
                        ? 'flt-semantics[role="' + args.roleFilter + '"]'
                        : 'flt-semantics';
                    return Array.from(document.querySelectorAll(q))
                        .filter(e => {
                            const t = (e.textContent || '').trim();
                            if (args.exact) return t === args.label;
                            // 前缀匹配同 _locate_by_text: "." 或 "\\n" 分隔
                            return t === args.label
                                || t.startsWith(args.label + '.')
                                || t.startsWith(args.label + '\\n');
                        }).length;
                }""",
                {
                    "label": eid_str,
                    "exact": kind == AnchorKind.LABEL,
                    "roleFilter": role_filter,
                },
            )
        )
