import pytest

from ui.i18n import I18n
from tests.e2e.labels import strategy_desc_label
from tests.e2e.pages import ScreenerPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e


async def test_screener_page_loads(e2e_page):
    """测试：选股页能正常加载。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()

    await screener.expect_text(I18n.get("screener_title"), timeout_ms=TIMEOUTS.INTERACTION)
    await screener.expect_text(I18n.get("select_strategy"), timeout_ms=TIMEOUTS.INTERACTION)


async def test_run_screener_strategy(e2e_page):
    """测试：执行放量突破策略，验证平安银行出现在选股结果中。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()

    # 等待策略执行完成（轮询"平安银行"文本出现，超时 30s）
    await screener.expect_result("平安银行")


async def test_screener_no_results(e2e_page):
    """E1: 选股策略返回空结果时显示无结果提示。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()

    # 选择超跌反弹策略 — 种子数据不含 RSI 超卖条件，策略应返回空结果
    await screener.select_strategy("oversold")
    await screener.run()

    # 验证空结果状态提示
    await screener.expect_result(I18n.get("screener_no_results"))


async def test_screener_strategy_switch(e2e_page):
    """D1: 切换策略后描述文本更新。"""
    screener = ScreenerPage(e2e_page)
    await screener.open()

    # ScreenerPage.select_strategy 内部通过 strategy_label() 将 key 解析为本地化显示名，
    # 避免向 select_dropdown 传递 raw_key 导致匹配失败
    await screener.select_strategy("volume_breakout")

    # 验证放量突破策略描述出现
    vb_desc = strategy_desc_label("volume_breakout")
    await screener.expect_text(vb_desc, timeout_ms=TIMEOUTS.FAST)

    # 切换到超跌反弹策略
    await screener.select_strategy("oversold")

    # 验证超跌反弹策略描述出现（动态描述，需 format 参数）
    os_desc = I18n.get("strategy_oversold_dynamic_desc").format(period=14, threshold=30)
    await screener.expect_text(os_desc, timeout_ms=TIMEOUTS.FAST)


async def test_screener_pagination_info(e2e_page):
    """测试：选股结果分页信息文本 — 运行策略后验证页码信息可见。

    种子数据仅平安银行满足放量突破阈值，结果 1 行，页码为"第 1 页 / 共 1 页"。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 验证页码信息文本（结果加载完成后渲染）
    page_info = I18n.get("screener_page_info").format(current=1, total=1)
    await screener.expect_text(page_info, timeout_ms=TIMEOUTS.INTERACTION)


async def test_screener_sort_by_column(e2e_page):
    """测试：点击列头后表格按对应列升序/降序排序。

    点击 ``pct_chg`` 列头后，列头出现 ↑（升序）→ 再次点击出现 ↓（降序）。
    通过列头箭头标记验证排序状态切换（virtual_table.PaginatedTable 渲染约定）。
    种子数据仅 1 行结果，无法验证排序前后行序变化，但可验证排序触发。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 列头文本格式: "pct_chg (涨跌幅)"（MetaDataManager.get_column_alias 渲染约定）
    col_label = f"pct_chg ({I18n.get('col_pct_chg')})"

    # 第一次点击 → 升序 (↑)，virtual_table.next_sort_state 新列默认升序
    await screener.click_column_header(col_label, timeout_ms=TIMEOUTS.INTERACTION)
    await screener.expect_text(f"{col_label} ↑", timeout_ms=TIMEOUTS.FAST)

    # 第二次点击 → 降序 (↓)，next_sort_state 翻转方向
    await screener.click_column_header(col_label, timeout_ms=TIMEOUTS.INTERACTION)
    await screener.expect_text(f"{col_label} ↓", timeout_ms=TIMEOUTS.FAST)


@pytest.mark.flaky(reruns=2, reruns_delay=1)
async def test_detail_dialog_open_close(e2e_page):
    """测试：点击行打开详情对话框，验证关键字段渲染，点击关闭按钮关闭对话框。

    验证项：
    1. 点击表格行 → 对话框打开
    2. 验证 ts_code/name/close/PE/PB 等字段标签渲染（StockDetailDialog 渲染约定）
    3. 点击"关闭"按钮 → 对话框隐藏

    flaky 注记：xdist 并行运行时偶发 worker 崩溃（隔离运行稳定 PASS），
    用 pytest-rerunfailures 自动重跑 2 次（间隔 1s）以吸收基础设施抖动。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 点击行（通过行内唯一文本"平安银行"定位）
    await screener.click_row_by_text("平安银行", timeout_ms=TIMEOUTS.INTERACTION)

    # 验证对话框打开：等待详情字段标签出现
    # stock_detail_dialog.py: _build_content 渲染 detail_pe/detail_pb/detail_price 等标签
    pe_label = I18n.get("detail_pe")  # "PE(TTM)"
    pb_label = I18n.get("detail_pb")  # "PB"
    price_label = I18n.get("detail_price")  # "现价"
    valuation_section = I18n.get("detail_sec_valuation")  # "估值指标"

    await screener.expect_text(valuation_section, timeout_ms=TIMEOUTS.INTERACTION)
    await screener.expect_text(pe_label, timeout_ms=TIMEOUTS.FAST)
    await screener.expect_text(pb_label, timeout_ms=TIMEOUTS.FAST)
    await screener.expect_text(price_label, timeout_ms=TIMEOUTS.FAST)

    # 点击关闭按钮（stock_detail_dialog.py: actions=[TextButton(I18n.get("common_close"))]）
    close_text = I18n.get("common_close")  # "关闭"
    await screener.page.click_button(close_text, timeout_ms=TIMEOUTS.INTERACTION)

    # 验证对话框关闭：等待详情字段标签消失（DOM 移除或隐藏）
    # 用 Playwright locator.wait_for(state="hidden") 替代固定 sleep
    # timeout 单位为毫秒，TIMEOUTS.INTERACTION=8000ms（8s）足够等待对话框关闭动画
    pe_locator = e2e_page.page.get_by_text(pe_label, exact=False)
    await pe_locator.wait_for(state="hidden", timeout=TIMEOUTS.INTERACTION)


async def test_export_screener_results_csv(e2e_page):
    """测试：点击导出按钮触发 CSV 文件下载。

    用 Playwright ``expect_download`` 捕获下载事件，断言：
    1. 文件名包含 ``screener_results_`` 前缀（screener_view._on_export_click 约定）
    2. 文件名以 ``.csv`` 结尾（allowed_extensions=["csv"]）
    3. 文件名包含日期戳（``%Y%m%d_%H%M%S`` 格式）
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 用 expect_download 捕获下载事件（不依赖真实 sleep）
    # timeout 单位为毫秒，SCREEN_RESULT=30000ms（30s）足够等待导出按钮 → 下载触发
    async with e2e_page.page.expect_download(timeout=TIMEOUTS.SCREEN_RESULT) as download_info:
        await screener.click_export(timeout_ms=TIMEOUTS.INTERACTION)

    download = await download_info.value
    filename = download.suggested_filename

    # 断言文件名约定（screener_view.py: default_filename = f"screener_results_{timestamp}.csv"）
    assert filename.startswith("screener_results_"), f"文件名前缀不符: {filename}"
    assert filename.endswith(".csv"), f"文件名扩展名不符: {filename}"
    # 日期戳格式: YYYYMMDD_HHMMSS（8位日期 + 下划线 + 6位时间）
    date_stamp = filename.removeprefix("screener_results_").removesuffix(".csv")
    assert len(date_stamp) == 15 and date_stamp[8] == "_", f"日期戳格式不符: {filename}"
    assert date_stamp.replace("_", "").isdigit(), f"日期戳非数字: {filename}"


async def test_export_screener_results_excel(e2e_page):
    """测试：点击 Excel 导出按钮触发 Excel 文件下载。

    期望行为（DoD）：导出按钮支持 Excel 格式，文件名以 ``.xlsx`` 结尾。
    实现：screener_view._on_export_excel_click + VM 导出 + openpyxl，UI 新增独立 Excel 导出按钮。
    Web 模式下 _do_export 通过 src_bytes 传给 FilePicker.save_file，Flet 用 Blob + <a download>
    触发浏览器下载，Playwright 可捕获 download 事件。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 用 expect_download 捕获下载事件（Excel 导出按钮触发）
    async with e2e_page.page.expect_download(timeout=TIMEOUTS.SCREEN_RESULT) as download_info:
        await screener.click_export_excel(timeout_ms=TIMEOUTS.INTERACTION)

    download = await download_info.value
    filename = download.suggested_filename

    # 期望 Excel 文件（.xlsx 结尾）
    assert filename.endswith(".xlsx"), f"文件名扩展名不符（期望 .xlsx）: {filename}"


async def test_detail_dialog_outside_click_close(e2e_page):
    """测试：点击对话框外部 → 验证对话框关闭。

    期望行为（DoD）：点击对话框外部区域关闭对话框。
    当前状态：Task 5.1 已修复 ``stock_detail_dialog.py`` 中 ``ft.AlertDialog(modal=False)``
    + ``on_dismiss=_close`` 回调（commit 91f9006c），点击对话框外部应触发 on_dismiss 关闭对话框。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 点击行打开对话框
    await screener.click_row_by_text("平安银行", timeout_ms=TIMEOUTS.INTERACTION)

    # 验证对话框打开：等待 PE 标签出现
    pe_label = I18n.get("detail_pe")
    await screener.expect_text(pe_label, timeout_ms=TIMEOUTS.INTERACTION)

    # 点击对话框外部（页面左上角，远离对话框中心）
    await e2e_page.page.mouse.click(10, 10)

    # 期望对话框关闭：等待 PE 标签消失（modal=False + on_dismiss 应触发关闭）
    # timeout 用 TIMEOUTS.INTERACTION（8s）足够等待对话框关闭动画
    pe_locator = e2e_page.page.get_by_text(pe_label, exact=False)
    await pe_locator.wait_for(state="hidden", timeout=TIMEOUTS.INTERACTION)


async def test_screener_pct_chg_positive_prefix(e2e_page):
    """P3-15: 涨跌前置 +/- 符号 (色盲友好) — 平安银行 pct_chg > 0 应显示 "+" 前缀。

    种子数据中平安银行 pct_chg 显式高于 VolumeBreakoutStrategy 阈值 (正数)，
    virtual_table P3-15 对正数 trend 列前置 "+" (U+002D 普通连字符用于负数)。
    验证表格中存在 "+数字" 格式文本，不依赖颜色区分涨跌。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # pct_chg 列为 _TREND_COLS 成员, 正值前置 "+" (virtual_table.py L189-192)
    # 种子数据 round(rng.uniform(*_PA_PCT_CHG_RANGE), 4) → 4 位小数正数
    # 用正则定位 "+数字.数字" 格式文本 (CanvasKit 语义节点)
    plus_prefix_loc = e2e_page.page.locator("text=/\\+\\d+\\.\\d+/")
    await plus_prefix_loc.first.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    assert await plus_prefix_loc.count() > 0, "P3-15: 正值 pct_chg 未显示 '+' 前缀 (色盲友好)"
