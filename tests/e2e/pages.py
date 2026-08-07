"""E2E Page Object 层。

封装跨测试重复的导航与选股交互序列，消除测试代码重复。
内部使用 FletPage helper 与 labels.py 提供的本地化文案。

PR-2: ScreenerPage 交互方法迁移到 AnchorPage（anchor-based 精确定位），
消除 CanvasKit 抖动下的文本/按钮误匹配。click_row_by_text/open_detail_dialog
暂保留文本定位（PR-4 收尾删除，需 row_text→ts_code 映射才能迁移到 anchor）。

PR-3: 新增 SettingsPage / DataPage / BacktestPage / WizardPage，封装各视图
anchor 化控件。test_*.py 不直接 import EIDS（封装边界，方案 §7.2）。
非 anchor 化控件（label 文本、无 anchor 的 TextField/Button）仍透传 FletPage
方法（click_text / click_button / fill_textbox / expect_text / has_text）。
"""

import logging

from core.i18n import I18n
from tests.e2e.helpers.anchor_page import AnchorPage
from tests.e2e.helpers.flet_page import FletPage
from tests.e2e.labels import strategy_label
from tests.e2e.timeouts import TIMEOUTS
from ui.testing.e2e_ids import EIDS, Eid

logger = logging.getLogger(__name__)

# PR-4 Task 4.2: nav i18n key → EID 映射 (与 app_layout._NAV_EIDS 对齐)
_NAV_EIDS: dict[str, Eid] = {
    "nav_market": EIDS.NAV.MARKET,
    "nav_screener": EIDS.NAV.SCREENER,
    "nav_backtest": EIDS.NAV.BACKTEST,
    "nav_data": EIDS.NAV.DATA,
    "nav_tasks": EIDS.NAV.TASKS,
    "nav_settings": EIDS.NAV.SETTINGS,
    "nav_watchlist": EIDS.NAV.WATCHLIST,
}


class App:
    """顶层导航 Page Object，封装侧边栏导航点击。

    PR-4 Task 4.2: 导航点击迁移到 AnchorPage.click_label (anchor-based 精确定位)，
    消除 CanvasKit 抖动下的文本误匹配。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.ap = AnchorPage(page.page, page)

    async def goto(self, nav_key: str, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到指定页面（通过 nav i18n key，如 "nav_screener"）。

        PR-4 Task 4.2: 使用 anchor-based click_label 替代 click_text。
        nav label 是 LABEL kind，click_label 点击 label 位置依赖事件冒泡到
        NavigationRailDestination 父容器。
        """
        await self.ap.click_label(_NAV_EIDS[nav_key], timeout_ms=timeout_ms)


class ScreenerPage:
    """选股页 Page Object，封装选策略→执行→等结果序列。

    PR-2: 交互方法迁移到 AnchorPage（anchor-based 精确定位）。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.app = App(page)
        self.ap = AnchorPage(page.page, page)

    async def open(self) -> None:
        """导航到选股页。"""
        await self.app.goto("nav_screener")

    async def select_strategy(self, strategy_key: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """选择指定策略（通过策略 key，内部解析为本地化显示名）。"""
        name = strategy_label(strategy_key)
        await self.ap.select_option(EIDS.SCREENER.STRATEGY_DROPDOWN, name, timeout_ms=timeout_ms)

    async def run(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """点击执行选股按钮。"""
        await self.ap.click(EIDS.SCREENER.RUN_BUTTON, timeout_ms=timeout_ms)

    async def expect_result(self, text: str, timeout_ms: int = TIMEOUTS.SCREEN_RESULT) -> None:
        """等待选股结果文本出现。"""
        await self.page.expect_text(text, timeout_ms=timeout_ms)

    async def expect_text(self, text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """等待选股页任意文本出现。"""
        await self.page.expect_text(text, timeout_ms=timeout_ms)

    async def click_column_header(self, col_id: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击表格列头触发排序（通过列 id，如 ``pct_chg``）。

        PR-2: 迁移到 AnchorPage（col_id → EIDS.SCREENER.column_header(col_id)）。
        列头文本仍可见（GestureDetector 不合并子树），文本验证不受影响。
        """
        await self.ap.click(EIDS.SCREENER.column_header(col_id), timeout_ms=timeout_ms)

    async def click_export(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 CSV 导出按钮。"""
        await self.ap.click(EIDS.SCREENER.EXPORT_CSV_BUTTON, timeout_ms=timeout_ms)

    async def click_export_excel(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 Excel 导出按钮。"""
        await self.ap.click(EIDS.SCREENER.EXPORT_EXCEL_BUTTON, timeout_ms=timeout_ms)

    async def close_detail_dialog(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击详情对话框关闭按钮（PR-2: 迁移到 AnchorPage）。"""
        await self.ap.click(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, timeout_ms=timeout_ms)

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

        验证标志：关闭按钮 anchor（``EIDS.DETAIL_DIALOG.CLOSE_BUTTON``）
        在对话框打开时立即渲染（不像 K 线图异步加载），是稳定的渲染完成标志。
        PR-2: 验证迁移到 anchor（替代文本匹配），与 close_detail_dialog 定位策略一致。
        """
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self.click_row_by_text(row_text, timeout_ms=TIMEOUTS.INTERACTION)
                await self.ap.expect_visible(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, timeout_ms=TIMEOUTS.FAST)
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


# ============================================================================
# PR-3: SettingsPage / DataPage / BacktestPage / WizardPage
#
# 设计原则（方案 §7.2 封装边界）：
# - Page Object 持有 EIDS 引用，test_*.py 不直接 import EIDS
# - anchor 化控件走 AnchorPage（精确定位）；非 anchor 化控件透传 FletPage 方法
# - 不重写已稳定的 FletPage 文本/按钮定位逻辑，最小化改造范围
# ============================================================================


class SettingsPage:
    """设置页 Page Object，封装 tab 切换 + 三个 Dropdown 的 anchor 操作。

    非 anchor 化控件（如 System tab 内的高级模式开关、LLM API Key TextField）
    仍透传 ``self.page`` 的 FletPage 方法（click_text / fill_textbox / click_button）。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.app = App(page)
        self.ap = AnchorPage(page.page, page)

    async def open(self, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到设置页（通过侧边栏 nav_settings）。"""
        await self.app.goto("nav_settings", timeout_ms=timeout_ms)

    async def expect_title(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """等待设置页标题可见。"""
        await self.page.expect_text(I18n.get("settings_title"), timeout_ms=timeout_ms)

    async def click_tab(self, tab_role: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击指定 tab（通过 anchor，role 如 ``data`` / ``database`` / ``ai`` / ``tasks`` / ``notify`` / ``system``）。

        从 i18n key ``settings_tab_<role>`` 派生，与 ``settings_view._TAB_CONFIG`` 对齐。
        """
        await self.ap.click(EIDS.SETTINGS.tab(tab_role), timeout_ms=timeout_ms)
        # 等待 tab 文本可见（与原 _navigate_to_settings_tab 行为一致，验证 tab 切换成功）
        await self.page.expect_text(I18n.get(f"settings_tab_{tab_role}"), timeout_ms=TIMEOUTS.FAST)

    async def select_language(self, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """选择语言下拉项（通过 anchor）。"""
        await self.ap.select_option(EIDS.SETTINGS.LANGUAGE_DROPDOWN, option_text, timeout_ms=timeout_ms)

    async def select_theme(self, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """选择主题下拉项（通过 anchor）。"""
        await self.ap.select_option(EIDS.SETTINGS.THEME_DROPDOWN, option_text, timeout_ms=timeout_ms)

    async def select_log_level(self, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """选择日志级别下拉项（通过 anchor）。"""
        await self.ap.select_option(EIDS.SETTINGS.LOG_LEVEL_DROPDOWN, option_text, timeout_ms=timeout_ms)


class DataPage:
    """数据浏览器页 Page Object，封装 3 个 Dropdown + 过滤值 TextField + 查询按钮的 anchor 操作。"""

    def __init__(self, page: FletPage):
        self.page = page
        self.app = App(page)
        self.ap = AnchorPage(page.page, page)

    async def open(self, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到数据页（通过侧边栏 nav_data）。"""
        await self.app.goto("nav_data", timeout_ms=timeout_ms)

    async def expect_explorer_tab(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """等待数据浏览 Tab 标签可见（确认页面加载）。"""
        await self.page.expect_text(I18n.get("data_tab_explorer"), timeout_ms=timeout_ms)

    async def select_table(self, table_name: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """选择数据表（通过 anchor），并等待新表 schema 加载完成。

        PR-478 修复: 替代固定 ``wait_for_timeout(1000)`` sleep. 通过等待
        ``TABLE_READY`` EID 先消失（reset_table_state 清空 table_columns）
        再出现（load_table_schema 重新填充 table_columns）来确认切表完成.
        只等待可见会误命中上一张表遗留的 ready 状态，必须先消失再出现.
        """
        await self.ap.select_option(EIDS.DATA.TABLE_DROPDOWN, table_name, timeout_ms=timeout_ms)
        try:
            await self.ap.expect_hidden(EIDS.DATA.TABLE_READY, timeout_ms=1000)
        except Exception:
            pass
        await self.ap.expect_visible(EIDS.DATA.TABLE_READY, timeout_ms=timeout_ms)

    async def select_filter_col(self, col_label: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """选择过滤列（通过 anchor，``col_label`` 为本地化列名如 ``代码``）。"""
        await self.ap.select_option(EIDS.DATA.FILTER_COL_DROPDOWN, col_label, timeout_ms=timeout_ms)

    async def select_filter_op(self, op_label: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """选择过滤操作符（通过 anchor，``op_label`` 为本地化操作符如 ``=``）。"""
        await self.ap.select_option(EIDS.DATA.FILTER_OP_DROPDOWN, op_label, timeout_ms=timeout_ms)

    async def fill_filter_value(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填充过滤值（通过 anchor）。"""
        await self.ap.fill(EIDS.DATA.FILTER_VALUE_INPUT, value, timeout_ms=timeout_ms)

    async def click_query(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击查询按钮（通过 anchor）。"""
        await self.ap.click(EIDS.DATA.QUERY_BUTTON, timeout_ms=timeout_ms)


class BacktestPage:
    """回测页 Page Object，封装策略 Dropdown + 运行/取消按钮 + 初始资金 TextField 的 anchor 操作。"""

    def __init__(self, page: FletPage):
        self.page = page
        self.app = App(page)
        self.ap = AnchorPage(page.page, page)

    async def open(self, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到回测页（通过侧边栏 nav_backtest）。"""
        await self.app.goto("nav_backtest", timeout_ms=timeout_ms)

    async def expect_title(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """等待回测页标题可见。"""
        await self.page.expect_text(I18n.get("backtest_view_title"), timeout_ms=timeout_ms)

    async def select_strategy(self, strategy_key: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """选择策略（通过 anchor，``strategy_key`` 如 ``volume_breakout``，内部解析为本地化名）。"""
        name = strategy_label(strategy_key)
        await self.ap.select_option(EIDS.BACKTEST.STRATEGY_DROPDOWN, name, timeout_ms=timeout_ms)

    async def fill_initial_capital(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填充初始资金（通过 anchor）。"""
        await self.ap.fill(EIDS.BACKTEST.INITIAL_CAPITAL_INPUT, value, timeout_ms=timeout_ms)

    async def click_run(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击开始回测按钮（通过 anchor）。"""
        await self.ap.click(EIDS.BACKTEST.RUN_BUTTON, timeout_ms=timeout_ms)

    async def click_cancel(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击取消按钮（通过 anchor）。"""
        await self.ap.click(EIDS.BACKTEST.CANCEL_BUTTON, timeout_ms=timeout_ms)


class WizardPage:
    """向导页 Page Object，封装 next/prev/skip 按钮 + token TextField 的 anchor 操作。

    非 anchor 化控件（如 db_host/db_port/db_user/db_password/db_name TextField、
    wizard_btn_start / wizard_btn_verify_next Button）仍透传 FletPage 方法
    （fill_textbox / click_button）。这些控件未 anchor 化是 PR-3 范围决策
    （wizard_page fixture 独立于 e2e_page，db 验证用例在 Windows/embedded 模式
    下被 skipif 跳过，anchor 化收益有限）。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.ap = AnchorPage(page.page, page)

    async def click_next(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 next 按钮（通过 anchor）。"""
        await self.ap.click(EIDS.WIZARD.NEXT_BUTTON, timeout_ms=timeout_ms)

    async def click_prev(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 prev 按钮（通过 anchor）。"""
        await self.ap.click(EIDS.WIZARD.PREV_BUTTON, timeout_ms=timeout_ms)

    async def click_skip(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 skip 按钮（通过 anchor）。"""
        await self.ap.click(EIDS.WIZARD.SKIP_BUTTON, timeout_ms=timeout_ms)

    async def fill_token(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填充 token 输入框（通过 anchor）。"""
        await self.ap.fill(EIDS.WIZARD.TOKEN_INPUT, value, timeout_ms=timeout_ms)


# ============================================================================
# PR-4 Task 4.2: NavPage / HomePage / TaskCenterPage
#
# 设计原则（方案 §7.2 封装边界）：
# - Page Object 持有 EIDS 引用，test_*.py 不直接 import EIDS
# - nav 导航走 click_label (LABEL kind anchor)
# - KPI 卡片 / 任务行走 expect_visible (LABEL kind anchor, 纯展示断言)
# - 非 anchor 化控件（页面标题、新闻区标题等）仍透传 FletPage.expect_text
# ============================================================================


class NavPage:
    """导航栏 Page Object，封装 nav label anchor 的点击与可见性断言。

    PR-4 Task 4.2: nav label 是 LABEL kind anchor，click_label 点击 label 位置
    依赖事件冒泡到 NavigationRailDestination 父容器。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.ap = AnchorPage(page.page, page)

    async def goto(self, nav_key: str, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到指定页面（通过 nav i18n key，如 "nav_screener"）。"""
        await self.ap.click_label(_NAV_EIDS[nav_key], timeout_ms=timeout_ms)

    async def expect_visible(self, nav_key: str, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """断言指定 nav label 可见（通过 anchor）。"""
        await self.ap.expect_visible(_NAV_EIDS[nav_key], timeout_ms=timeout_ms)


class HomePage:
    """首页 Page Object，封装 KPI 卡片 anchor 断言。

    PR-4 Task 4.2: KPI 卡片标题是 LABEL kind anchor (e2e.home.kpi.*)，
    走 expect_visible (textContent 精确匹配)。非 anchor 化控件（页面标题、
    热门概念标题、新闻区标题）仍透传 FletPage.expect_text。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.ap = AnchorPage(page.page, page)

    async def expect_title(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """等待首页标题可见。"""
        await self.page.expect_text(I18n.get("home_title"), timeout_ms=timeout_ms)

    async def expect_kpi_sh(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """断言上证指数 KPI 卡片可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.HOME.KPI_SH, timeout_ms=timeout_ms)

    async def expect_kpi_sz(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """断言深证成指 KPI 卡片可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.HOME.KPI_SZ, timeout_ms=timeout_ms)

    async def expect_kpi_cyb(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """断言创业板指 KPI 卡片可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.HOME.KPI_CYB, timeout_ms=timeout_ms)

    async def expect_kpi_northbound(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """断言北向资金 KPI 卡片可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.HOME.KPI_NORTHBOUND, timeout_ms=timeout_ms)

    async def expect_hot_concepts_title(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """等待热门概念区标题可见（非 anchor 化，走文本断言）。"""
        await self.page.expect_text(I18n.get("home_hot_concepts"), timeout_ms=timeout_ms)

    async def expect_live_news_title(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """等待新闻区标题可见（非 anchor 化，走文本断言）。"""
        await self.page.expect_text(I18n.get("home_live_news"), timeout_ms=timeout_ms)


class TaskCenterPage:
    """任务中心 Page Object，封装任务列表区域 anchor 断言。

    PR-4 Task 4.2: 任务列表区域是 LABEL kind anchor (e2e.task_center.task_list)，
    走 expect_visible。任务行卡片是动态 LABEL kind anchor (task_row(task_id))，
    但 task_id 在运行时生成，E2E 测试通常通过任务名文本断言而非 task_id anchor。
    非 anchor 化控件（页面标题、空状态文本）仍透传 FletPage.expect_text/has_text。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.app = App(page)
        self.ap = AnchorPage(page.page, page)

    async def open(self, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """导航到任务中心页（通过侧边栏 nav_tasks）。"""
        await self.app.goto("nav_tasks", timeout_ms=timeout_ms)

    async def expect_title(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """等待任务中心标题可见。"""
        await self.page.expect_text(I18n.get("nav_tasks"), timeout_ms=timeout_ms)

    async def expect_task_list(self, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """断言任务列表区域可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.TASK_CENTER.TASK_LIST, timeout_ms=timeout_ms)

    async def has_empty_state(self) -> bool:
        """检查是否显示空状态（非 anchor 化，走文本匹配）。"""
        return await self.page.has_text(I18n.get("task_empty_title"))

    async def expect_task_name(self, name: str, timeout_ms: int = TIMEOUTS.TITLE) -> None:
        """等待指定任务名出现（非 anchor 化，走文本断言）。"""
        await self.page.expect_text(name, timeout_ms=timeout_ms)
