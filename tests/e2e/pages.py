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
from collections.abc import Mapping, Sequence
from io import BytesIO

import numpy as np
from PIL import Image
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

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


# ============================================================================
# 像素级渲染断言（拦截「语义树完整但未绘制」类缺陷）
#
# AI 大脑空白 BUG 教训：TabBarView 布局 NaN 沿父链传染时，CanvasKit 的
# <flt-semantics> 语义 DOM 与 paint 管线解耦——语义树仍完整、anchor 可见性
# 断言全绿，但画布一笔未画。本组断言用 Playwright 截图（合成器输出，WebGL
# 内容亦可捕获，规避 canvas getImageData 的 preserveDrawingBuffer 限制）做
# 像素统计，直接验证 paint 管线输出。
# ============================================================================


def _pixel_diff_ratio(image_bytes: bytes, *, sample_inset: int = 4, diff_threshold: int = 24) -> float:
    """统计截图中与背景采样色差异超阈值的像素占比（纯像素计算，可单测）。

    背景色取区域四角内缩 ``sample_inset`` 像素处 4 个采样点的中位数：区域
    空白时四角与全域同色（占比≈0），正常绘制时文字/控件使占比显著非零。

    Args:
        image_bytes: PNG 截图字节流。
        sample_inset: 背景采样点相对区域四角的内缩像素数。
        diff_threshold: 判定「非背景」的 RGB 通道最大差异阈值。
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    h, w = arr.shape[:2]
    corners = np.stack(
        [
            arr[sample_inset, sample_inset],
            arr[sample_inset, w - sample_inset - 1],
            arr[h - sample_inset - 1, sample_inset],
            arr[h - sample_inset - 1, w - sample_inset - 1],
        ]
    )
    bg = np.median(corners, axis=0)
    diff = np.abs(arr - bg).max(axis=-1)
    return float((diff > diff_threshold).mean())


async def assert_boxes_region_renders(
    page: Page,
    boxes: Sequence[Mapping[str, float]],
    *,
    min_ratio: float = 0.02,
    settle_ms: int = 400,
    message: str = "region",
) -> None:
    """断言锚点联合包围盒区域真实绘制：非背景像素占比 ≥ ``min_ratio``。

    Args:
        page: Playwright Page（FletPage.page 的原生 page）。
        boxes: 锚点 bounding box 列表（``_identifier_box`` 返回格式）。
        min_ratio: 非背景像素占比下限。
        settle_ms: 截图前等待渲染稳定的毫秒数。
        message: 失败信息前缀（定位是哪个页面区域）。

    Raises:
        AssertionError: 区域过小无法统计，或非背景像素占比低于阈值（语义树
            可能完整但 paint 管线未渲染）。
    """
    if not boxes:
        raise AssertionError(f"{message}: 未提供任何锚点 box")
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    vp = page.viewport_size or {"width": 1280, "height": 720}
    left = max(x0 - 8.0, 0.0)
    top = max(y0 - 8.0, 0.0)
    clip = {
        "x": left,
        "y": top,
        "width": min(x1 + 8.0, vp["width"]) - left,
        "height": min(y1 + 8.0, vp["height"]) - top,
    }
    if clip["width"] < 16 or clip["height"] < 16:
        raise AssertionError(f"{message}: 断言区域过小 {clip}，无法做像素统计")
    await page.wait_for_timeout(settle_ms)
    shot = await page.screenshot(clip=clip)
    ratio = _pixel_diff_ratio(shot)
    if ratio < min_ratio:
        raise AssertionError(
            f"{message}: 区域疑似未绘制（非背景像素占比 {ratio:.4f} < {min_ratio}）。"
            "语义树可能完整但 paint 管线未渲染（布局 NaN / 绘制崩溃），"
            "参见 docs/flet/v1-api-constraints.md「ft.TabBarView 禁止置于 "
            "scrollable 容器」约束条目。"
        )


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

    async def click_run_backtest(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击「去回测」跳转按钮（F07 full-mount：anchor 精确点击 + 重试兜底）。

        按钮文本与左导航 nav_backtest label 同名，click_button 文本定位存在
        歧义/吞点风险（full-mount 首跑实测误点后 _handle_backtest_jump 静默
        return），须走 RUN_BACKTEST_BUTTON anchor。整体重试（N=3）对齐
        DataPage.select_table 模式，抗 headless CanvasKit 吞点——以「按钮消失」
        为跳转确认（跳转后选股页 offstage，语义节点被排除，见 anchor.py P0-2
        矩阵；跳转未发生时按钮仍可见，重试点击）。
        """
        last_exc: Exception | None = None
        for idx in range(TIMEOUTS.RETRY_ATTEMPTS):
            try:
                await self.ap.click(EIDS.SCREENER.RUN_BACKTEST_BUTTON, timeout_ms=timeout_ms)
                # 跳转确认：按钮语义节点消失（选股页 offstage 语义排除）
                await self.ap.expect_hidden(EIDS.SCREENER.RUN_BACKTEST_BUTTON, timeout_ms=2000)
                return
            except Exception as exc:  # noqa: BLE001  # 记录末次异常，耗尽后抛原始异常
                last_exc = exc
                if idx < TIMEOUTS.RETRY_ATTEMPTS - 1:  # 仅尝试间休眠，末次失败不再等待直接抛原始异常
                    await asyncio.sleep(TIMEOUTS.RETRY_INTERVAL_MS / 1000)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("click_run_backtest: exhausted retries without recorded failure")  # 逻辑上不可达

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

    async def expand_advanced_settings(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """展开参数区「高级设置」折叠入口（MAJOR-07 最小视口 DoD）。

        先滚入视口再点：最小视口下参数侧栏可能把该入口推到可视区下沿，
        Playwright 对视口外坐标静默丢弃（对齐 AnchorPage.select_option 的既有处理）。
        """
        await self.ap.scroll_into_view(EIDS.SCREENER.ADVANCED_SETTINGS, timeout_ms=timeout_ms)
        await self.ap.click(EIDS.SCREENER.ADVANCED_SETTINGS, timeout_ms=timeout_ms)

    async def expect_row_in_viewport(self, ts_code: str, timeout_ms: int = TIMEOUTS.SCREEN_RESULT) -> None:
        """断言指定结果行完整落入视口（MAJOR-07：小窗口下结果表首行不被裁切）。"""
        await self.ap.expect_in_viewport(EIDS.SCREENER.result_row(ts_code), timeout_ms=timeout_ms)

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


class NewsRiskPage:
    """新闻风险解读页对象（Phase E3）：详情对话框内「证据 → 生成 → 解读」流。

    封装：运行放量突破策略打开详情 → 等待证据模式（生成按钮出现）→ 生成风险解读 →
    断言各 anchor（RISK_LEVEL / SUMMARY / EVENT_CARD / COVERAGE / EVENT_EMPTY）。
    仅封装 news-risk anchor 化交互；文本级断言仍透传 ``page.expect_text``。
    """

    def __init__(self, page: FletPage):
        self.page = page
        self.screener = ScreenerPage(page)
        self.ap = AnchorPage(page.page, page)

    async def open_detail(self, ts_code: str = "000001.SZ") -> None:
        """运行放量突破策略并打开平安银行详情对话框（种子数据 1 行命中）。"""
        await self.screener.open()
        await self.screener.select_strategy("volume_breakout")
        await self.screener.run()
        await self.screener.expect_result("平安银行")
        await self.screener.open_detail_dialog(ts_code)

    async def wait_evidence_ready(self, timeout_ms: int = TIMEOUTS.SCREEN_RESULT) -> None:
        """等待证据模式就绪（生成按钮出现，即 PHASE_EVIDENCE_READY）。"""
        await self.ap.expect_visible(EIDS.NEWS_RISK.GENERATE_BUTTON, timeout_ms=timeout_ms)

    async def generate(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击「生成风险解读」按钮（retry 抗吞）。

        E3 修复：detail dialog 内 evidence 列表会把生成按钮推到视口外（E2E 实证
        generate 按钮 y~1768 远超视口 900，playwright mouse.click 点击视口外坐标
        Flutter 收不到）。故先 ``scroll_into_view`` 把按钮滚入视口再 ``click``。

        _confirm 用短轮询等待 GENERATE_BUTTON 消失（触发后 phase→ANALYZING 按钮
        被替换）。对齐 ScreenerPage.run：区分"点击成功但异步渲染延迟（数帧内消失
        → True）"与"点击被吞（按钮持续可见 → 轮询超时 → False → 重试）"。首次生成
        缓存未命中（XP5 场景刻意前置）走 DELAY_MS=1500 的生成中阶段，2s 窗口内
        按钮保持隐藏，确认信号稳定。
        """

        async def _interact() -> None:
            await self.ap.scroll_into_view(EIDS.NEWS_RISK.GENERATE_BUTTON, timeout_ms=timeout_ms)
            await self.ap.click(EIDS.NEWS_RISK.GENERATE_BUTTON, timeout_ms=timeout_ms)

        async def _confirm() -> bool:
            try:
                await self.ap.expect_hidden(EIDS.NEWS_RISK.GENERATE_BUTTON, timeout_ms=2000)
                return True
            except PlaywrightTimeoutError:
                return False

        await retry_until_triggered(_interact, _confirm)

    async def wait_analyzing(self, timeout_ms: int = TIMEOUTS.FAST) -> None:
        """等待进入分析中阶段（ProgressRing + 文案）。"""
        await self.ap.expect_visible(EIDS.NEWS_RISK.ANALYZING, timeout_ms=timeout_ms)

    async def wait_ready(self, timeout_ms: int = TIMEOUTS.SCREEN_RESULT) -> None:
        """等待 ready 阶段完成（RISK_LEVEL anchor 出现）。"""
        await self.ap.expect_visible(EIDS.NEWS_RISK.RISK_LEVEL, timeout_ms=timeout_ms)

    async def assert_risk_level_visible(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        await self.ap.expect_visible(EIDS.NEWS_RISK.RISK_LEVEL, timeout_ms=timeout_ms)

    async def assert_risk_level_hidden(self, timeout_ms: int = TIMEOUTS.FAST) -> None:
        """断言风险等级不在 DOM（无 AI/生成前的证据模式不应渲染）。"""
        await self.ap.expect_hidden(EIDS.NEWS_RISK.RISK_LEVEL, timeout_ms=timeout_ms)

    async def assert_event_card_visible(self, idx: int = 0, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        await self.ap.expect_visible(EIDS.NEWS_RISK.event_card(idx), timeout_ms=timeout_ms)

    async def assert_summary_visible(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        # Flet 1.0.x out-of-sync 会部分丢弃嵌套的 Semantics 节点：E3 实证页面里
        # risk_level/coverage/event_card 锚点都渲染成功，唯独 SUMMARY 的 Semantics
        # 锚点缺失，而「风险摘要」裸标题 Text 稳定渲染。故改等标题文本（expect_text
        # 已具备 textContent + aria-label 双轨匹配），抗 out-of-sync。
        await self.page.expect_text(I18n.get("news_insight_summary"), timeout_ms=timeout_ms)

    async def assert_coverage_visible(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        await self.ap.expect_visible(EIDS.NEWS_RISK.COVERAGE, timeout_ms=timeout_ms)

    async def assert_event_empty_visible(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        await self.ap.expect_visible(EIDS.NEWS_RISK.EVENT_EMPTY, timeout_ms=timeout_ms)

    async def close_detail(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        await self.screener.close_detail_dialog(timeout_ms=timeout_ms)


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

        MAJOR-03: 数据管理 tab 新增「危险操作」卡片后 Tushare 连接卡被下移, 验证按钮
        bbox 中心落到视口下方 (E2E 实证 y≈950 > 视口高 900)。Playwright
        ``mouse.click`` 对**视口外坐标**无效——点击被静默丢弃 (不抛异常, 但 Flutter
        收不到 tap), 3 次重试 confirm 全 False → ``retry_until_triggered`` 误报
        "interaction not triggered"。故每次重试先 ``scroll_into_view`` 把按钮滚入
        视口再点（对齐 ``NewsRiskPage.generate`` 的同类修复）。

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
            # 每次重试先滚入视口：内容流把按钮推到视口外时，bbox 中心坐标在视口
            # 之外，mouse.click 点击落空（Flutter 收不到）——见方法 docstring MAJOR-03 段。
            await self.ap.scroll_into_view(EIDS.TUSHARE.VERIFY_BUTTON, timeout_ms=timeout_ms)
            await self.ap.click(EIDS.TUSHARE.VERIFY_BUTTON, timeout_ms=timeout_ms)

        async def _confirm() -> bool:
            # 短轮询等待「验证中」warning 文本（评审 F1 教训：把渲染延迟与真吞区分开）。
            # VM.verify_token 进入 is_verifying 即输出 tushare_verifying_in_progress，
            # CanvasKit 下该文本经 Flet 重渲染需数帧；单次 has_text 会把渲染延迟误判为
            # "未触发"而重复点击。此处轮询 2s：命中即确认点击已触发，不重复点击。
            # 同时检测验证结果错误文本（网络失败路径，覆盖 verify_token 已快速失败场景）。
            # 轮询窗口按 E2E_TIMEOUT_MULTIPLIER 缩放（对齐 ScreenerPage.run 的
            # expect_hidden 内部 _tm 缩放；此前裸 2.0s 在 CI 慢速 VM 下窗口过短导致
            # 全部 3 次 confirm 超时 → retry_until_triggered 误报未触发）。
            deadline = time.monotonic() + (2.0 * self.page._timeout_multiplier)  # noqa: SLF001  # 与 conftest slow marker 一致的 multiplier 访问
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


class ModelManagementPage:
    """设置「AI 大脑」内「模型管理」三页签 Page Object（S5 §11.1 / §11.2）。

    封装：进入设置 AI tab → 切「模型连接 / 场景配置 / 使用记录」三页签（锚定
    ``EIDS.MODEL.tab(role)``）→ 各页关键交互（连接表单 / 场景编辑器 / 使用记录）。
    向导模型阶段复用同一套共用组件与 anchor，故本 Page Object 亦覆盖向导内嵌项。

    设计原则（方案 §7.2 封装边界）：Page Object 持有 EIDS 引用，test_*.py 不直接
    import EIDS；anchor 化控件走 AnchorPage，非 anchor 化文案透传 FletPage。
    """

    #: 三页签 role（与 model_management_panel 页签顺序对齐）。
    TAB_CONNECTION = "connection"
    TAB_ROUTES = "routes"
    TAB_USAGE = "usage"

    def __init__(self, page: FletPage):
        self.page = page
        self.ap = AnchorPage(page.page, page)
        self.settings = SettingsPage(page)

    async def open(self, timeout_ms: int = TIMEOUTS.NAV) -> None:
        """进入设置页 AI tab（承载模型管理三页签）。"""
        await self.settings.open(timeout_ms=timeout_ms)
        await self.settings.click_tab("ai", timeout_ms=timeout_ms)

    # --- 三页签 ---
    async def select_tab(self, role: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """切换模型管理页签（``role`` ∈ connection/routes/usage）。"""
        await self.ap.click(EIDS.MODEL.tab(role), timeout_ms=timeout_ms)

    async def expect_tab_visible(self, role: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言模型管理页签可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.MODEL.tab(role), timeout_ms=timeout_ms)

    # --- 各页结构断言（供用例做 anchor 化结构校验，不直接暴露 EIDS 给 test_*.py） ---
    async def expect_connection_view(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言「模型连接」页关键交互可见（搜索框 + 添加连接）。"""
        await self.ap.expect_visible(EIDS.MODEL.SEARCH_INPUT, timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.ADD_CONNECTION, timeout_ms=timeout_ms)

    async def expect_connection_editor(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言连接编辑器（三步创建）已展开（名称 / 适配器字段 + 步骤导航）。"""
        await self.ap.expect_visible(EIDS.MODEL.NAME_INPUT, timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.ADAPTER_DROPDOWN, timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.EDITOR_NEXT, timeout_ms=timeout_ms)

    async def expect_routes_view(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言「场景配置」页关键交互可见（三场景导航 + 保存当前场景）。"""
        await self.ap.expect_visible(EIDS.MODEL.scenario_tab("news_annotation"), timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.ROUTES_SAVE_CURRENT, timeout_ms=timeout_ms)

    async def expect_picker_open(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言候选选择器已打开（搜索框 + 关闭按钮可见）。"""
        await self.ap.expect_visible(EIDS.MODEL.PICKER_SEARCH, timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.PICKER_CLOSE, timeout_ms=timeout_ms)

    async def expect_usage_view(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言「使用记录」页关键交互可见（来源过滤 + 导出 + 进入月汇总）。"""
        await self.ap.expect_visible(EIDS.MODEL.USE_ORIGIN_FILTER, timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.USE_EXPORT, timeout_ms=timeout_ms)
        await self.ap.expect_visible(EIDS.MODEL.USE_ENTER_MONTHLY, timeout_ms=timeout_ms)

    async def expect_renders_pixels(
        self,
        role: str,
        *,
        min_ratio: float = 0.02,
        settle_ms: int = 400,
        timeout_ms: int = TIMEOUTS.INTERACTION,
    ) -> None:
        """像素级断言：三页签栏 + ``role`` 子页首个交互锚点联合区域非空白。

        AI 大脑空白 BUG 回归守护：布局 NaN 传染时语义树完整（anchor 可见性
        断言全绿），本断言对锚点联合包围盒截图统计非背景像素占比，直接验证
        paint 管线输出（详见 ``assert_boxes_region_renders``）。
        """
        anchors: list[Eid] = [
            EIDS.MODEL.tab("connection"),
            EIDS.MODEL.tab("routes"),
            EIDS.MODEL.tab("usage"),
        ]
        if role == self.TAB_CONNECTION:
            anchors.append(EIDS.MODEL.SEARCH_INPUT)
        elif role == self.TAB_ROUTES:
            anchors.append(EIDS.MODEL.scenario_tab("news_annotation"))
        elif role == self.TAB_USAGE:
            anchors.append(EIDS.MODEL.USE_ORIGIN_FILTER)
        else:
            raise ValueError(f"unknown model tab role: {role}")
        boxes = [await self.ap._identifier_box(eid, timeout_ms) for eid in anchors]  # noqa: SLF001  # 包内复用私有 box 解析
        await assert_boxes_region_renders(
            self.page.page,
            boxes,
            min_ratio=min_ratio,
            settle_ms=settle_ms,
            message=f"AI 大脑「模型管理」{role} 页签区域",
        )

    # --- 「模型连接」页 ---
    async def search_connections(self, query: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """在搜索框输入连接名 / 模型名（通过 anchor）。"""
        await self.ap.fill(EIDS.MODEL.SEARCH_INPUT, query, timeout_ms=timeout_ms)

    async def select_status_filter(self, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """选择连接状态过滤项（全部 / 已启用 / 已停用，本地化文案）。"""
        await self.ap.select_option(EIDS.MODEL.STATUS_FILTER, option_text, timeout_ms=timeout_ms)

    async def click_add_connection(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击「添加连接」进入三步创建（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.ADD_CONNECTION, timeout_ms=timeout_ms)

    async def fill_connection_name(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填入连接名称（通过 anchor）。"""
        await self.ap.fill(EIDS.MODEL.NAME_INPUT, value, timeout_ms=timeout_ms)

    async def select_adapter(self, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """选择适配器 / 协议（通过 anchor）。"""
        await self.ap.select_option(EIDS.MODEL.ADAPTER_DROPDOWN, option_text, timeout_ms=timeout_ms)

    async def fill_model(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填入模型名（通过 anchor）。"""
        await self.ap.fill(EIDS.MODEL.MODEL_INPUT, value, timeout_ms=timeout_ms)

    async def fill_credential(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填入凭据（通过 anchor，不回显）。"""
        await self.ap.fill(EIDS.MODEL.CREDENTIAL_INPUT, value, timeout_ms=timeout_ms)

    async def fill_api_base(self, value: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """填入 API 地址（通过 anchor）。"""
        await self.ap.fill(EIDS.MODEL.API_BASE_INPUT, value, timeout_ms=timeout_ms)

    async def click_editor_prev(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """编辑器回退一步（通过 anchor）。

        编辑器字段组较高（连接信息含 provider/model/地址/凭据），步骤导航按钮可能
        被推到视口下沿；Playwright ``mouse.click`` 对视口外坐标静默丢弃，故先
        ``scroll_into_view`` 再点（对齐 ``SettingsPage.click_tushare_verify`` 既有处理）。
        """
        await self.ap.scroll_into_view(EIDS.MODEL.EDITOR_PREV, timeout_ms=timeout_ms)
        await self.ap.click(EIDS.MODEL.EDITOR_PREV, timeout_ms=timeout_ms)

    async def click_editor_next(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """编辑器前进一步（通过 anchor，先滚入视口再点，见 ``click_editor_prev``）。"""
        await self.ap.scroll_into_view(EIDS.MODEL.EDITOR_NEXT, timeout_ms=timeout_ms)
        await self.ap.click(EIDS.MODEL.EDITOR_NEXT, timeout_ms=timeout_ms)

    async def click_editor_test(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击编辑器「测试连接」（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.EDITOR_TEST, timeout_ms=timeout_ms)

    async def click_editor_save(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击编辑器「保存」（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.EDITOR_SAVE, timeout_ms=timeout_ms)

    async def click_editor_resolve(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击「重新读取」恢复提交结果未知（commit_unknown）状态（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.EDITOR_RESOLVE, timeout_ms=timeout_ms)

    # --- 「场景配置」页 ---
    async def select_scenario(self, scenario_id: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """切换场景导航（``scenario_id`` ∈ news_annotation/stock_analysis/news_risk）。"""
        await self.ap.click(EIDS.MODEL.scenario_tab(scenario_id), timeout_ms=timeout_ms)

    async def expect_scenario_visible(self, scenario_id: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言场景导航按钮可见（通过 anchor）。"""
        await self.ap.expect_visible(EIDS.MODEL.scenario_tab(scenario_id), timeout_ms=timeout_ms)

    async def click_add_candidate(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """打开候选选择器（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.ADD_CANDIDATE, timeout_ms=timeout_ms)

    async def search_picker(self, query: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """在候选选择器搜索框输入（通过 anchor）。"""
        await self.ap.fill(EIDS.MODEL.PICKER_SEARCH, query, timeout_ms=timeout_ms)

    async def close_picker(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """关闭候选选择器（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.PICKER_CLOSE, timeout_ms=timeout_ms)

    async def click_undo(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """撤销最后一次移动 / 移除（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.UNDO, timeout_ms=timeout_ms)

    async def toggle_advanced(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """展开 / 收起高级参数区（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.ADVANCED_TOGGLE, timeout_ms=timeout_ms)

    async def click_save_current(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """保存当前场景（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.ROUTES_SAVE_CURRENT, timeout_ms=timeout_ms)

    async def move_up(self, connection_id: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """候选行上移（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.move_up(connection_id), timeout_ms=timeout_ms)

    async def move_down(self, connection_id: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """候选行下移（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.move_down(connection_id), timeout_ms=timeout_ms)

    async def remove_candidate(self, connection_id: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """移除候选引用（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.remove(connection_id), timeout_ms=timeout_ms)

    # --- 「使用记录」页 ---
    async def select_origin_filter(self, option_text: str, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """选择来源过滤项（全部 / 业务 / 测试，本地化文案）。"""
        await self.ap.select_option(EIDS.MODEL.USE_ORIGIN_FILTER, option_text, timeout_ms=timeout_ms)

    async def click_enter_monthly(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """进入月汇总视图（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.USE_ENTER_MONTHLY, timeout_ms=timeout_ms)

    async def click_export(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """导出脱敏 CSV（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.USE_EXPORT, timeout_ms=timeout_ms)

    async def click_prev_page(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """使用记录上一页（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.USE_PREV_PAGE, timeout_ms=timeout_ms)

    async def click_next_page(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """使用记录下一页（通过 anchor）。"""
        await self.ap.click(EIDS.MODEL.USE_NEXT_PAGE, timeout_ms=timeout_ms)


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

    async def expect_model_substep(self, index: int, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """断言模型阶段第 ``index`` 子步骤指示器可见（LABEL anchor，存在性 / 高亮探测）。

        S5 §11.6：原「云端 AI」+「本地模型」两步合并为单步 ``model_scenario``，
        内部三子步骤（①连接信息 ②统计方式 ③场景与检查）。
        """
        await self.ap.expect_visible(EIDS.MODEL.substep(index), timeout_ms=timeout_ms)

    async def click_save_and_continue(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """点击模型阶段末子步骤「保存并继续」（通过 anchor，原子提交本阶段全部差异）。

        S5 §11.6：末子步骤(3) 复用同一 nav 按钮但换用 ``MODEL.SAVE_AND_CONTINUE``
        anchor；前两子步骤仍为 ``WIZARD.NEXT_BUTTON``（见 ``click_next``）。
        """
        await self.ap.click(EIDS.MODEL.SAVE_AND_CONTINUE, timeout_ms=timeout_ms)

    async def accept_risk_disclaimer(self, timeout_ms: int = TIMEOUTS.INTERACTION) -> None:
        """确保欢迎步投资风险提示确认已勾选（UX-09 / MAJOR-01），使「下一步」按钮可用。

        欢迎步未勾选时 NEXT 按钮 disabled；从欢迎步前进前须先调用本方法。

        幂等语义（**非 toggle**）：RISK_ACK 是受控 ``ft.Checkbox``，勾选态来自
        ``OnboardingState.risk_acknowledged``（由 ``ConfigHandler`` 持久化读取）。
        跨用例/跨会话该值可能已为 True，欢迎步渲染即已勾选——若无条件 click 会将其
        toggle 回 False → NEXT 再次 disabled → 后续点击空操作。故本方法**先读勾选态**，
        仅当未勾选时才点击，并在点击后确认状态已置位（抗 CanvasKit 高负载吞点击）。
        """
        eid = EIDS.WIZARD.RISK_ACK
        await self.ap.scroll_into_view(eid, timeout_ms=timeout_ms)
        if await self.ap.is_checked(eid, timeout_ms=timeout_ms):
            return
        await retry_until_triggered(
            lambda: self.ap.click(eid, timeout_ms=timeout_ms),
            lambda: self.ap.is_checked(eid, timeout_ms=timeout_ms),
            attempts=TIMEOUTS.RETRY_ATTEMPTS,
            interval_ms=TIMEOUTS.RETRY_INTERVAL_MS,
        )
        await self.page.page.wait_for_timeout(300)


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
