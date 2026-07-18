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


async def test_detail_dialog_open_close(e2e_page):
    """测试：点击行打开详情对话框，验证关键字段渲染，点击关闭按钮关闭对话框。

    验证项：
    1. 点击表格行 → 对话框打开
    2. 验证 ts_code/name/close/PE/PB 等字段标签渲染（StockDetailDialog 渲染约定）
    3. 点击"关闭"按钮 → 对话框隐藏
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


@pytest.mark.xfail(
    reason="Flet Web FilePicker.save_file 使用浏览器 File System Access API "
    "(window.showSaveFilePicker) 打开原生保存对话框，不触发 Playwright download 事件。"
    "源码层面需改用 anchor.download 或 blob URL 下载方式才能被 Playwright 捕获，"
    "或测试侧需 mock FilePicker 验证调用参数。当前两种方案均未实现，测试超时 → xfail。",
    strict=False,
)
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


@pytest.mark.xfail(
    reason="UI 仅支持 CSV 导出（screener_view._on_export_click: allowed_extensions=['csv']），"
    "Excel 导出未实现。源码需新增 Excel 导出支持后此测试才能通过。",
    strict=False,
)
async def test_export_screener_results_excel(e2e_page):
    """测试：点击导出按钮触发 Excel 文件下载。

    期望行为（DoD）：导出按钮支持 Excel 格式，文件名以 ``.xlsx`` 结尾。
    当前状态：UI 仅支持 CSV 导出（``screener_view._on_export_click`` 中
    ``allowed_extensions=["csv"]``、``default_filename=f"screener_results_{timestamp}.csv"``），
    无 Excel 导出选项。测试期望 ``.xlsx`` 扩展名，实际下载为 ``.csv``，断言失败 → xfail。
    """
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()
    await screener.expect_result("平安银行")

    # 用 expect_download 捕获下载事件（与 CSV 测试同按钮，UI 无独立 Excel 导出按钮）
    async with e2e_page.page.expect_download(timeout=TIMEOUTS.SCREEN_RESULT) as download_info:
        await screener.click_export(timeout_ms=TIMEOUTS.INTERACTION)

    download = await download_info.value
    filename = download.suggested_filename

    # 期望 Excel 文件（.xlsx 结尾）；实际为 .csv，断言失败 → xfail
    assert filename.endswith(".xlsx"), f"文件名扩展名不符（期望 .xlsx）: {filename}"


@pytest.mark.xfail(
    reason="对话框为 modal=True（stock_detail_dialog.py:AlertDialog(modal=True)），"
    "modal 对话框不会因点击外部而关闭。源码需改为 modal=False 或添加 on_dismiss 处理后此测试才能通过。",
    strict=False,
)
async def test_detail_dialog_outside_click_close(e2e_page):
    """测试：点击对话框外部 → 验证对话框关闭。

    期望行为（DoD）：点击对话框外部区域关闭对话框。
    当前状态：``stock_detail_dialog.py`` 中 ``ft.AlertDialog(modal=True)``，
    modal 对话框的 barrier 不响应点击关闭，点击外部后对话框保持打开。
    ``wait_for(state="hidden")`` 超时 → xfail。
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

    # 期望对话框关闭：等待 PE 标签消失；modal=True 时不会关闭，超时 → xfail
    # timeout 用 TIMEOUTS.INTERACTION（8s）避免 xfail 用例长时间挂起占用 worker
    pe_locator = e2e_page.page.get_by_text(pe_label, exact=False)
    await pe_locator.wait_for(state="hidden", timeout=TIMEOUTS.INTERACTION)
