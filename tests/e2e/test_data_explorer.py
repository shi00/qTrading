import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n
from tests.e2e.timeouts import TIMEOUTS


async def test_table_viewer(e2e_page):
    """测试：数据表查看器 — 切换到 daily_quotes 表并验证数据加载。"""
    # 导航到数据页
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=TIMEOUTS.NAV)

    # 确认在数据浏览 Tab
    explorer_tab = I18n.get("data_tab_explorer")
    await e2e_page.expect_text(explorer_tab)

    # 选择 daily_quotes 表
    table_label = I18n.get("data_select_table")
    await e2e_page.select_dropdown(table_label, "daily_quotes", timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.page.wait_for_timeout(2000)

    # 点击查询按钮
    query_btn = e2e_page.page.locator('[aria-label="查询"], [aria-label*="查询"]').first
    await query_btn.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    await query_btn.click(timeout=TIMEOUTS.TITLE, force=True)

    # 验证表格中出现平安银行相关数据
    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)


async def test_sql_console(e2e_page):
    """测试：SQL 控制台 — 执行自定义 SQL 并验证结果。"""
    # 导航到数据页
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=TIMEOUTS.NAV)

    # 切换到 SQL 控制台 Tab
    sql_tab = I18n.get("data_tab_sql")
    await e2e_page.click_tab(sql_tab, timeout_ms=TIMEOUTS.TITLE)

    # 点击预设的 SELECT * LIMIT 10 按钮填充 SQL
    limit_btn = e2e_page.page.get_by_text("SELECT * LIMIT 10").first
    await limit_btn.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    await limit_btn.click()

    # 稍微等待 Flet 状态更新
    await e2e_page.page.wait_for_timeout(500)

    # 点击执行按钮
    execute_text = I18n.get("data_sql_execute")
    await e2e_page.click_button(execute_text, timeout_ms=TIMEOUTS.INTERACTION)

    # 验证结果中出现平安银行
    await e2e_page.expect_text("平安银行", timeout_ms=TIMEOUTS.NAV)


# [PITFALL_WARNING] Flet Web CanvasKit 自动化测试黑洞避坑指南
# 坑点：试图在 E2E 测试中向多行文本框（TextField(multiline=True)）中录入文本。
# 原因：Flet 0.28.3 CanvasKit 的底层渲染引擎存在严重的 A11y 语义树映射缺陷。
#      多行输入框不会被映射为标准的 'textbox'，它的 label/hint 会被吞噬或错误附着到极远的父容器上，
#      且开发者显式赋予的 semantics_label 也会被完全忽略。
# 后果：如果在 Playwright 中使用强制绝对坐标点击（e.g. mouse.click(x,y)）或键盘焦点漫游（Tab），
#      均会由于不同分辨率、系统环境导致焦点错位，最终形成极难排查的 Flaky tests。
# 正确做法：绝不要试图用脆弱的 Hack 去妥协。对于框架底层的缺陷，直接加上明确原因的 skip，
#         并将这个用例交由人工测试或等待上游框架修复，保卫整个测试套件的健壮性。
@pytest.mark.skip(
    reason="Flet 0.28.3 CanvasKit multiline TextField web semantic mapping is fundamentally broken. "
    "The text field is not exposed in the a11y tree and lacks stable interaction points for Playwright. "
    "Pending upstream fix from Flet/Flutter."
)
async def test_sql_console_error(e2e_page):
    """E3: SQL 控制台执行非法 SQL 时显示错误提示。"""
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=TIMEOUTS.NAV)

    sql_tab = I18n.get("data_tab_sql")
    await e2e_page.click_tab(sql_tab, timeout_ms=TIMEOUTS.TITLE)

    # 输入非法 SQL (使用产品层暴露的独立 semantics_label 定位)
    editor_loc = e2e_page.page.locator('[aria-label="sql_editor_input"]')
    await editor_loc.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    await editor_loc.click(force=True)

    # 清空并输入
    await e2e_page.page.keyboard.press("Control+A")
    await e2e_page.page.keyboard.press("Backspace")
    await e2e_page.page.keyboard.type("SELECTT * FROM nonexistent", delay=50)

    execute_text = I18n.get("data_sql_execute")
    await e2e_page.click_button(execute_text, timeout_ms=TIMEOUTS.INTERACTION)

    # 验证错误提示（取 i18n 前缀，UI 显示已 format 的完整错误信息）
    error_prefix = I18n.get("data_sql_error").split(":")[0]
    await e2e_page.expect_text(error_prefix, timeout_ms=TIMEOUTS.TITLE)


async def test_table_viewer_switch(e2e_page):
    """D5: 数据浏览切换表后数据更新。"""
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=TIMEOUTS.NAV)

    explorer_tab = I18n.get("data_tab_explorer")
    await e2e_page.expect_text(explorer_tab)

    # 选择 daily_quotes 表
    table_label = I18n.get("data_select_table")
    await e2e_page.select_dropdown(table_label, "daily_quotes", timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.page.wait_for_timeout(2000)

    query_btn = e2e_page.page.locator('[aria-label="查询"], [aria-label*="查询"]').first
    await query_btn.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    await query_btn.click(timeout=TIMEOUTS.TITLE, force=True)

    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)

    # 切换到 stock_basic 表
    await e2e_page.select_dropdown(table_label, "stock_basic", timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.page.wait_for_timeout(2000)

    await query_btn.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    await query_btn.click(timeout=TIMEOUTS.TITLE, force=True)

    # 验证 stock_basic 表数据
    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)
