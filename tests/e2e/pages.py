"""E2E Page Object 层。

封装跨测试重复的导航与选股交互序列，消除测试代码重复。
内部使用 FletPage helper 与 labels.py 提供的本地化文案。

PR-2: ScreenerPage 交互方法迁移到 AnchorPage（anchor-based 精确定位），
消除 CanvasKit 抖动下的文本/按钮误匹配。
PR-4 Task 4.3: click_row/open_detail_dialog 迁移到 anchor（result_row(ts_code)），
消除 6 层降级 + 3 次重试，anchor 定位稳定无需重试。

PR-3: 新增 SettingsPage / DataPage / BacktestPage / WizardPage，封装各视图
anchor 化控件。test_*.py 不直接 import EIDS（封装边界，方案 §7.2）。
非 anchor 化控件（label 文本、无 anchor 的 TextField/Button）仍透传 FletPage
方法（click_text / click_button / fill_textbox / expect_text / has_text）。
"""

import asyncio
import logging
import time

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.i18n import I18n
from tests.e2e.helpers.anchor_page import AnchorPage, retry_until_triggered
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
        """点击执行选股按钮，带"确认触发"重试兜底。

        PR-4 Task 4.1: 用 retry_until_triggered 包裹点击，抗 headless CanvasKit
        渲染吞点击（CI 高负载下偶发物理鼠标点击落在中间帧被吞）。点击后确认
        RUN_BUTTON 消失作为触发指标：state.loading=True 时按钮被替换为 STOP，
        anchor 消失即代表 loading 已开始。

        评审 F1: _confirm 改为短轮询等待 RUN_BUTTON 消失（expect_hidden 2s），
        区分两种场景——点击成功但异步 loading 渲染延迟（按钮数帧内消失 → 返回
        True，不重复点击）与点击被吞（RUN_BUTTON 持续可见 → 轮询至超时返回
        False → 触发重试）。避免旧 count 即时判定把渲染延迟误判为"未触发"而
        重复点击导致 30s+ 超时误报。
        """

        async def _interact() -> None:
            await self.ap.click(EIDS.SCREENER.RUN_BUTTON, timeout_ms=timeout_ms)

        async def _confirm() -> bool:
            try:
                await self.ap.expect_hidden(EIDS.SCREENER.RUN_BUTTON, timeout_ms=2000)
                return True
            except PlaywrightTimeoutError:
                return False

        await retry_until_triggered(_interact, _confirm)

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

    async def click_row(self, ts_code: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击表格行触发 on_click 打开详情对话框（通过 ts_code anchor）。

        PR-4 Task 4.3: anchor-based 精确定位，替代 6 层降级文本匹配。
        行 anchor 由 ``screener_view.row_anchor=lambda row: EIDS.SCREENER.result_row(
        row["ts_code"])`` 注入，AnchorKind=COMPLEX（GestureDetector 合并节点）。
        """
        await self.ap.click(EIDS.SCREENER.result_row(ts_code), timeout_ms=timeout_ms)

    async def open_detail_dialog(
        self,
        ts_code: str,
        max_attempts: int = 3,
        timeout_ms: int = TIMEOUTS.INTERACTION,
    ) -> None:
        """点击行打开详情对话框，带重试验证关闭按钮 anchor 渲染。

        PR-4 Task 4.3: anchor 化后用 ``click_row`` + ``expect_visible(EIDS.DETAIL_DIALOG.CLOSE_BUTTON)``。
        保留重试机制以抵抗 CI 环境下 CanvasKit hit-testing 的偶发抖动。
        """
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self.click_row(ts_code, timeout_ms=timeout_ms)
                await self.ap.expect_visible(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, timeout_ms=TIMEOUTS.FAST)
                if attempt > 1:
                    logger.info("open_detail_dialog: succeeded on attempt %d for ts_code '%s'", attempt, ts_code)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.debug(
                    "open_detail_dialog: attempt %d/%d failed for ts_code '%s': %s",
                    attempt,
                    max_attempts,
                    ts_code,
                    exc,
                )
        raise AssertionError(
            f"open_detail_dialog: dialog did not open after {max_attempts} attempts for ts_code '{ts_code}'. "
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

    async def click_tushare_verify(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击 Tushare 面板"验证 Token"按钮，带"确认触发"重试兜底。

        CanvasKit 下 click_button fallback 定位偶发不触发 Flutter 回调 → 改用
        AnchorPage INTERACTIVE 路径的 bbox 中心鼠标点击（PR669 E2E 修复）。

        PR681: 单次 AnchorPage click 在 CI 慢速 headless CanvasKit 下仍偶发被吞
        （verify_token 未启动 → 无错误文本 → 60s 断言失败）。对齐 ``ScreenerPage.run``
        的 retry_until_triggered 抗吞模式。

        confirm 信号: ``is_verifying=True`` 时再点击同步渲染 warning 文本
        ("tushare_verifying_in_progress"，无外部 IO)，或任一验证结果错误文本出现。
        两者均无需等待外部 Tushare IO 完成即可确认点击已真正触发 verify_token。
        （disabled 态在 CanvasKit 语义 DOM 无 aria-disabled/aria-hidden 可检测，
        反幻觉实证已排除，故不以 disabled 作 confirm 信号。）
        """
        verifying_text = I18n.get("tushare_verifying_in_progress")
        # 与 test_tushare_token_validate_and_save 的 possible_errors 同源
        error_keys = (
            "wizard_err_token_invalid",
            "wizard_err_token_network",
            "wizard_err_token_timeout",
            "wizard_err_token_server",
            "wizard_err_token_unknown",
        )

        async def _interact() -> None:
            await self.ap.click(EIDS.TUSHARE.VERIFY_BUTTON, timeout_ms=timeout_ms)

        async def _confirm() -> bool:
            # 短轮询等待「验证中」warning 文本（评审 F1 教训：把渲染延迟与真吞区分开）。
            # VM.verify_token 进入 is_verifying 即输出 tushare_verifying_in_progress，
            # CanvasKit 下该文本经 Flet 重渲染需数帧；单次 has_text 会把渲染延迟误判为
            # "未触发"而重复点击。此处轮询 2s：命中即确认点击已触发，不重复点击。
            # 同时检测验证结果错误文本（网络失败路径，覆盖 verify_token 已快速失败场景）。
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if await self.page.has_text(verifying_text):
                    return True
                for k in error_keys:
                    if await self.page.has_text(I18n.get(k)):
                        return True
                # self.page 是 FletPage（非 Playwright Page），wait_for_timeout 需
                # 走底层 Playwright page（.page），否则 AttributeError（PR684 CI 实证）。
                await self.page.page.wait_for_timeout(100)
            return False

        await retry_until_triggered(_interact, _confirm)


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

        PR-4 Task 4.2: 整体重试（N=3）抗 CI 高负载下 headless CanvasKit 渲染
        抖动导致的下拉展开/选项渲染抖动（option not found）或 TABLE_READY 未按时
        出现. 重试粒度 = 整个 select_table（含 select_option + TABLE_READY 等待），
        每次失败休眠 RETRY_INTERVAL_MS，RETRY_ATTEMPTS 次耗尽抛最后一次的原始异常.
        """
        last_exc: Exception | None = None
        for idx in range(TIMEOUTS.RETRY_ATTEMPTS):
            try:
                await self.ap.select_option(EIDS.DATA.TABLE_DROPDOWN, table_name, timeout_ms=timeout_ms)
                try:
                    await self.ap.expect_hidden(EIDS.DATA.TABLE_READY, timeout_ms=1000)
                except Exception:
                    pass
                await self.ap.expect_visible(EIDS.DATA.TABLE_READY, timeout_ms=timeout_ms)
                return
            except Exception as exc:  # noqa: BLE001  # 记录末次异常，耗尽后抛原始异常
                last_exc = exc
                if idx < TIMEOUTS.RETRY_ATTEMPTS - 1:  # 仅尝试间休眠，末次失败不再等待直接抛原始异常
                    await asyncio.sleep(TIMEOUTS.RETRY_INTERVAL_MS / 1000)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("select_table: exhausted retries without recorded failure")  # 逻辑上不可达

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
