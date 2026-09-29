"""AnchorPage: 基于 EIDS identifier 属性的精确 Flet 控件定位。

单一识别路径：精确选择器
``flt-semantics[flt-semantics-identifier="<EID>"]``，无 kind 分派、无启发式、无回退
（PoC EVIDENCE.md P0-2 矩阵：identifier 节点存在性与 AnchorKind 无关）。identifier 由
``anchored()``（E2E 模式）注入 ``ft.Semantics(identifier=EID)``，CanvasKit 落为 DOM 属性
``flt-semantics-identifier``（P0-1 实证）。

``anchored()`` 同时保留 ``label=EID``（无障碍 label 通道），供
``tests/e2e/test_screener_anchor_smoke.py`` 的 ``container=True`` 独立性守护断言使用，
但已不再是定位通道。

AnchorKind 仅承载 kind 特定行为（见 ui/testing/e2e_ids.py）：``INPUT`` 需下潜后代
``input``/``textarea``；``LABEL`` 为 display-only（click/scroll 拒绝）；
``INTERACTIVE``/``COMPLEX`` 在 identifier 路径下定位行为一致。

click 一律用 `page.mouse.click(bbox_center)`，因为 CanvasKit 不响应合成 DOM 事件。
"""

import asyncio
import time
from typing import Any
from collections.abc import Awaitable, Callable, Mapping

from playwright.async_api import Locator, Page

from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import AnchorKind, Eid


def _as_box(box: Mapping[str, Any]) -> dict[str, float]:
    """把 Playwright bounding_box 归一为 ``{x, y, width, height}`` float dict。

    显式构造（而非 ``dict(box)``）以保持 ``dict[str, float]`` 返回类型，
    避免 pyright 对 ``bounding_box()`` 值类型推断为 ``object`` 的报错。
    """
    return {
        "x": float(box["x"]),
        "y": float(box["y"]),
        "width": float(box["width"]),
        "height": float(box["height"]),
    }


async def retry_until_triggered(
    interact: Callable[[], Awaitable[None]],
    confirm: Callable[[], Awaitable[bool]],
    attempts: int = TIMEOUTS.RETRY_ATTEMPTS,
    interval_ms: int = TIMEOUTS.RETRY_INTERVAL_MS,
) -> None:
    """步骤级"确认触发 + N 次重试"兜底，抗 headless CanvasKit 渲染吞点击。

    CI 高负载 → headless Chromium 无 GPU + CanvasKit 软件渲染 → 帧率不稳 →
    偶发物理鼠标点击落在渲染中间帧被吞（交互未触发）。本函数每次交互后调用
    ``confirm`` 判断是否真正触发，未触发则休眠 ``interval_ms`` 后重试。

    纯 asyncio 逻辑，不依赖 Playwright/Page，可独立单测（无需真实浏览器）。
    """
    last_exc: Exception | None = None
    for idx in range(attempts):
        try:
            await interact()
            if await confirm():
                return
        except Exception as exc:  # noqa: BLE001  # 记录末次异常，尝试耗尽后统一抛 RuntimeError
            last_exc = exc
        if idx < attempts - 1:
            await asyncio.sleep(interval_ms / 1000)
    if last_exc is not None:
        raise RuntimeError(
            f"retry_until_triggered: interaction not confirmed after {attempts} attempts. Last error: {last_exc!r}"
        ) from last_exc
    raise RuntimeError(f"retry_until_triggered: interaction not triggered after {attempts} attempts")


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
    # identifier 精确路径: 单一选择器, 无 kind 分派 / 无启发式
    # ----------------------------------------------------------------

    def _locator_by_identifier(self, eid_str: str) -> Locator:
        """精确选择器: 命中带 `flt-semantics-identifier` 属性的语义节点。

        EID 命名空间仅含 ASCII 字母数字下划线 + `.`，不含引号/反斜杠，故可直接内插
        到属性选择器中（无需转义）。P0-2 矩阵实证该节点存在性与 AnchorKind 无关，
        因此四类 kind 共用同一选择器——启发式匹配（后缀/前缀/role 过滤）与
        strict mode violation 风险不复存在。
        """
        return self.page.locator(f'flt-semantics[flt-semantics-identifier="{eid_str}"]')

    async def _identifier_node_box(self, eid_str: str, timeout_ms: int) -> dict[str, float]:
        """identifier 节点自身 bbox。

        P0-2 矩阵实证: Button 系列 / Text / Dropdown 顶层 / Dropdown 选项面板 /
        ListView 行 / Dialog 内节点的 identifier 节点 bbox 与真实可点击节点一致
        （Text 类节点自身即交互面, `pointer-events: auto`）。
        """
        locator = self._locator_by_identifier(eid_str)
        await locator.first.wait_for(state="attached", timeout=self._tm(timeout_ms))
        box = await locator.first.bounding_box()
        if not box or box["width"] == 0 or box["height"] == 0:
            raise RuntimeError(
                f"AnchorPage: identifier node {eid_str!r} has no valid bbox (box={box}). "
                f"Check that anchored() wrapped a rendered, non-offstage control."
            )
        return _as_box(box)

    async def _identifier_input_box(self, eid_str: str, timeout_ms: int) -> dict[str, float]:
        """INPUT: identifier 节点后代 `input` / `textarea` 的 bbox。

        P0-2 矩阵行 7-8 实证: TextField 的 identifier 节点 bbox（200×48）与真实
        `input` bbox（208×54）**不一致**（边框/内边距差异），故取真实输入面。
        """
        outer = self._locator_by_identifier(eid_str)
        await outer.first.wait_for(state="attached", timeout=self._tm(timeout_ms))
        inner = outer.first.locator("input, textarea").first
        await inner.wait_for(state="visible", timeout=self._tm(timeout_ms))
        box = await inner.bounding_box()
        if not box:
            raise RuntimeError(f"AnchorPage: no bbox for input under identifier node {eid_str!r}")
        return _as_box(box)

    async def _identifier_box(self, eid: Eid, timeout_ms: int) -> dict[str, float]:
        """identifier 路径的交互 bbox 解析: INPUT 取后代 input, 其余取节点自身。"""
        eid_str, kind = eid
        if kind == AnchorKind.INPUT:
            return await self._identifier_input_box(eid_str, timeout_ms)
        return await self._identifier_node_box(eid_str, timeout_ms)

    async def _read_expanded_by_identifier(self, eid_str: str) -> str | None:
        """identifier 路径读取下拉展开态 (`aria-expanded`)。

        P0-2 矩阵行 12 实证: Dropdown 顶层节点带 `role=button` + `aria-expanded`。
        """
        return await self.page.evaluate(
            r"""(args) => {
                const el = document.querySelector(
                    'flt-semantics[flt-semantics-identifier="' + args.label + '"]');
                return el ? el.getAttribute('aria-expanded') : null;
            }""",
            {"label": eid_str},
        )

    async def scroll_into_view(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """把 anchor 所在 identifier 节点滚动到视口中心，供 click 前调用。

        E3 实证：dialog/长表单内目标控件可能被内容流推到视口外，Playwright
        `mouse.click(bbox_center)` 对视口外坐标无效（点击落空，Flutter 收不到）。
        滚动使控件进入视口后再点。用 JS `scrollIntoView({block,inline:'center'})`
        而非 Playwright `scroll_into_view_if_needed`：CanvasKit 语义节点经测试仅响应
        原生 scrollIntoView。identifier 是唯一定位通道，无 kind 分派。

        LABEL 为 display-only，无可滚动目标，显式拒绝。滚动画后短暂等待 Flutter
        滚动动画稳定。
        """
        eid_str, kind = eid
        if kind == AnchorKind.LABEL:
            raise RuntimeError(
                f"AnchorPage.scroll_into_view: LABEL kind ({eid_str!r}) is display-only, not scrollable target"
            )
        await self.page.evaluate(
            r"""(args) => {
                const el = document.querySelector(
                    'flt-semantics[flt-semantics-identifier="' + args.label + '"]');
                if (el) el.scrollIntoView({block: 'center', inline: 'center'});
            }""",
            {"label": eid_str},
        )
        await self.page.wait_for_timeout(300)

    # ----------------------------------------------------------------
    # 核心操作: click / fill / select_option / hover
    # ----------------------------------------------------------------

    async def click(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """解析 identifier 目标 bbox, 一律走真实鼠标事件.

        kind 分派仅剩 LABEL 拒绝（display-only）；INTERACTIVE/COMPLEX/INPUT 共用
        identifier 选择器，bbox 按 kind 语义解析（INPUT 下潜后代 input，见
        `_identifier_box`）。
        """
        eid_str, kind = eid
        if kind == AnchorKind.LABEL:
            raise RuntimeError(f"AnchorPage.click: LABEL kind ({eid_str!r}) is display-only, not clickable")
        box = await self._identifier_box(eid, timeout_ms)
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await self.page.mouse.click(cx, cy)

    async def hover(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        box = await self._identifier_box(eid, timeout_ms)
        await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    async def click_label(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 LABEL kind anchor 的位置（依赖事件冒泡到可点击父容器）。

        适用场景：LABEL anchor 包裹的控件本身不可点击，但位于可点击父容器内
        （如 ``NavigationRailDestination`` 的 label）。``click`` 方法拒绝 LABEL kind
        （语义上 LABEL 是 display-only），本方法显式声明"点击 LABEL 位置"的意图，
        通过真实鼠标事件触发父容器的 hit-testing。

        与 ``click`` 的区别：``click`` 拒绝 LABEL；``click_label`` 直接点击 LABEL
        identifier 节点的 bbox 中心，依赖事件冒泡。
        """
        eid_str, kind = eid
        if kind != AnchorKind.LABEL:
            raise RuntimeError(
                f"AnchorPage.click_label: only supports LABEL, got {kind} for {eid_str!r}. "
                f"Use click() for INTERACTIVE/COMPLEX/INPUT."
            )
        box = await self._identifier_box(eid, timeout_ms)
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await self.page.mouse.click(cx, cy)

    async def fill(self, eid: Eid, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, kind = eid
        if kind != AnchorKind.INPUT:
            raise RuntimeError(f"AnchorPage.fill: only supports INPUT, got {kind} for {eid_str!r}")
        box = await self._identifier_input_box(eid_str, timeout_ms)
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await self.page.mouse.click(cx, cy)
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.type(value, delay=30)

    async def _find_option_element(self, option_text: str, eid_str: str) -> Any:
        """定位下拉选项。

        Flet 0.86.5 CanvasKit 下拉选项**无** option/menuitem 角色，渲染为
        ``role="button"`` 节点（text 形如 ``"key (别名)"``），且由 Flet 动态生成、
        无 anchor 覆盖（无 identifier），故仍需文本匹配。候选集按优先级
        搜索：菜单角色 → 按钮(下拉选项) → 宽泛兜底；匹配优先级 精确 > 前缀别名 >
        ``(别名)`` 括号模式 > 裸子串。避免裸子串 ``"代码"`` 误命中页面导航/列头
        等无关文本（PR 585 E2E 失败根因，见 reviews/plans/2026-08-25-...）。
        """
        option_handle = await self.page.evaluate_handle(
            r"""(args) => {
                const {text, dropdownEid} = args;
                const normText = text.trim();
                const isVisible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                const notSelf = (e) => { const t=(e.textContent||'').trim(); return !(dropdownEid && (t.startsWith(dropdownEid+'\n')||t.startsWith(dropdownEid+'.'))); };
                const matchPrio = (t) => {
                    if (t === normText) return 1;
                    if (t.startsWith(normText+' ')||t.startsWith(normText+'(')||t.startsWith(normText+'\n')) return 2;
                    if (t.includes('('+normText+')')) return 3;
                    if (t.includes(normText)) return 4;
                    return 0;
                };
                // 候选组按优先级：菜单角色 → 按钮(下拉选项) → 宽泛兜底
                const groups = [
                    'flt-semantics[role="option"], flt-semantics[role="menuitem"]',
                    'flt-semantics[role="button"]:not([aria-expanded])',
                    'flt-semantics:not([aria-expanded])',
                ];
                for (const sel of groups) {
                    const els = Array.from(document.querySelectorAll(sel)).filter(isVisible).filter(notSelf);
                    for (let prio = 1; prio <= 4; prio++) {
                        const hit = els.find(e => matchPrio((e.textContent||'').trim()) === prio);
                        if (hit) return hit;
                    }
                }
                return null;
            }""",
            {"text": option_text, "dropdownEid": eid_str},
        )
        return option_handle.as_element()

    async def select_option(
        self,
        dropdown_eid: Eid,
        option_text: str,
        timeout_ms: int = TIMEOUTS.INTERACTION,
    ) -> None:
        """打开 anchor 化的 Dropdown 并选中 option_text.

        Dropdown 是 COMPLEX kind, 顶层 identifier 节点即可点击展开.
        option 面板节点由 Flet 动态生成, 无 anchor/identifier 覆盖, 仍用文本匹配.
        剩余风险: 同视图两个 Dropdown 出现同名选项 - EIDS 命名规范强制唯一.

        PR-478 修复 (4 个复合缺陷, 见 reviews/问题定位.md):
        - C1: 主动 poll ``aria-expanded="true"``, 未展开立即抛错
              ``dropdown did not expand``, 不再静默等待选项 20s.
        - C2: 选项点击用 ``page.mouse.click(cx, cy)`` 坐标点击, 与
              ``AnchorPage.click`` 一致, 绕开 CanvasKit ``flt-semantics`` 上
              不稳定的 actionability 检查.
        - C3: 选项定位用精确文本匹配 (等值 / 空格 / ``(`` / ``\\n`` 分隔) 取代
              ``filter(has_text=...)`` 子串匹配, 避免短文本 (如"代码") 误命中.
        - C4: 展开前若残留 ``aria-expanded="true"`` 按 ``Escape`` 先收合, 避免
              第二次 click 被 Material 3 DropdownMenu 解读为"关闭"而非"打开".
        - C5 (PR 585 E2E 修复): 预探测选项**仅当菜单当前确认展开**时执行.
              菜单关闭时选项节点不存在, 全页面文本匹配会误命中页面其他同文本元素
              (导航/结果表表头「代码」), 导致菜单从未打开、点击落空. 故关闭态一律
              先展开再搜索. 选项搜索用**候选组优先级** (菜单角色 → role=button
              下拉选项 → 宽泛兜底) + **匹配优先级** (精确 > 前缀别名 > "(别名)"
              括号模式 > 裸子串), 避免裸子串误命中页面无关文本.
        - C6 (视口外点击静默丢弃): 展开前先 ``scroll_into_view`` 把 Dropdown 滚入
              视口再取 bbox 点击. MAJOR-08 将日志级别等技术参数收敛进默认折叠的
              「高级（开发者）」分组后, 展开分组会把 Dropdown 推到视口下方; Playwright
              ``mouse.click`` 对视口外坐标静默丢弃 (不抛异常、Flutter 收不到 tap),
              下拉永不展开 → 选项节点不存在 → "option not found".
        - 收合确认 (坑点 6 步骤级重试): 空等 2s + ``retry_until_triggered`` 确认
              菜单收合, 选择未落地即抛明确错误, 不静默返回.
        """
        eid_str, kind = dropdown_eid
        if kind != AnchorKind.COMPLEX:
            raise RuntimeError(f"AnchorPage.select_option: only supports COMPLEX, got {kind} for {eid_str!r}")

        # 0. C4: 若下拉残留 expanded 状态（上次选择未完全收合），先按 Escape 收合
        expanded_check = await self._read_expanded_by_identifier(eid_str)
        if expanded_check == "true":
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(300)

        # 1. 门控预探测：仅当菜单当前确认展开 (aria-expanded=true) 才在 DOM 中查找选项。
        #    菜单关闭时选项节点不存在（Flet 动态生成，见 canvaskit-rendering-e2e-guide 坑点 3），
        #    此时全页面文本匹配会误命中页面其他同文本元素（如结果表表头「代码」），
        #    导致菜单从未打开、点击落空（PR 585 E2E 失败根因）。故关闭态一律走展开流程。
        menu_expanded = await self._read_expanded_by_identifier(eid_str)
        # Any: 后续闭包 (_interact/_menu_closed) 经 nonlocal 捕获，此处显式声明避免
        # pyright 在捕获点对 `else None` 分支报 Optional 成员访问 (reportOptionalMemberAccess)
        option_element: Any = await self._find_option_element(option_text, eid_str) if menu_expanded == "true" else None
        if not option_element:
            # 展开前先把 Dropdown 滚入视口，再取 bbox。
            # MAJOR-08 将日志级别等技术参数收敛进默认折叠的「高级（开发者）」分组后，
            # 展开分组会把 Dropdown 推到视口下方（CI run 36535917168 实证 identifier
            # 节点 bbox y=991 > 视口高 900）。Playwright ``mouse.click`` 对视口外坐标
            # **静默丢弃**（不抛异常、Flutter 收不到 tap）→ 下拉永不展开 → 选项节点
            # 不存在 → "option not found"。故点击前必须 scroll_into_view 并重新取 bbox
            # （同 ``SettingsPage.click_tushare_verify`` 的既有处理）。
            await self.scroll_into_view(dropdown_eid, timeout_ms)
            box = await self._identifier_node_box(eid_str, timeout_ms)

            # 策略 A: 点击右侧下拉箭角 (width - 15px), 直接触发表单展开
            arrow_x = box["x"] + max(box["width"] - 15.0, box["width"] / 2)
            arrow_y = box["y"] + box["height"] / 2
            await self.page.mouse.click(arrow_x, arrow_y)

            # 短轮询等待选项出现（最多等待 2s）
            quick_deadline = time.monotonic() + min(self._tm(timeout_ms) / 1000, 2.0)
            while time.monotonic() < quick_deadline:
                option_element = await self._find_option_element(option_text, eid_str)
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
                    option_element = await self._find_option_element(option_text, eid_str)
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
                    fresh_elem = await self._find_option_element(option_text, eid_str)
                    if fresh_elem:
                        option_element = fresh_elem
                        continue
                raise

        # 3. 确认菜单收合（选择落地）。CanvasKit 高负载下选项点击偶发被吞：
        #    click 返回成功但 on_select 未触发、菜单保持展开，静默返回会导致下游
        #    snackbar/状态断言全部超时。先空等 2s 正常收合；未收合则重定位选项
        #    重试点击（interact+confirm 模式，见 canvaskit-rendering-e2e-guide 坑点 6
        #    步骤级重试），耗尽仍不收合即抛明确错误，不静默吞。
        async def _interact() -> None:
            nonlocal option_element
            fresh_elem = await self._find_option_element(option_text, eid_str)
            if fresh_elem:
                option_element = fresh_elem
            await option_element.click(force=True)

        async def _menu_closed() -> bool:
            try:
                return not await option_element.is_visible()
            except Exception:
                return True

        settle_deadline = time.monotonic() + 2.0
        while time.monotonic() < settle_deadline:
            if await _menu_closed():
                return
            await self.page.wait_for_timeout(100)
        await retry_until_triggered(_interact, _menu_closed, attempts=3, interval_ms=400)

    # ----------------------------------------------------------------
    # 断言与探测: expect_visible/expect_hidden/count
    # ----------------------------------------------------------------

    async def expect_visible(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, _ = eid
        # 四类 kind 共用精确选择器, 无 kind 分派 (P0-2 矩阵)
        await self._locator_by_identifier(eid_str).first.wait_for(state="visible", timeout=self._tm(timeout_ms))

    async def expect_hidden(self, eid: Eid, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        eid_str, _ = eid
        await self._locator_by_identifier(eid_str).first.wait_for(state="hidden", timeout=self._tm(timeout_ms))

    async def count(self, eid: Eid) -> int:
        # 单一选择器计数, 无启发式的 strict mode violation 风险
        return await self._locator_by_identifier(eid[0]).count()
