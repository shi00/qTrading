import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_table_viewer(e2e_page):
    """测试：数据表查看器 — 切换到 daily_quotes 表并验证数据加载。"""
    # 导航到数据页
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=15000)

    # 确认在数据浏览 Tab
    explorer_tab = I18n.get("data_tab_explorer")
    await e2e_page.expect_text(explorer_tab)

    # 选择 daily_quotes 表
    table_label = I18n.get("data_select_table")
    await e2e_page.select_dropdown(table_label, "daily_quotes", timeout_ms=10000)
    await e2e_page.page.wait_for_timeout(2000)

    # 点击查询按钮
    query_btn = e2e_page.page.locator('[aria-label="查询"], [aria-label*="查询"]').first
    await query_btn.wait_for(state="attached", timeout=8000)
    await query_btn.click(timeout=10000, force=True)

    # 验证表格中出现平安银行相关数据
    await e2e_page.expect_text("000001.SZ", timeout_ms=15000)


async def test_sql_console(e2e_page):
    """测试：SQL 控制台 — 执行自定义 SQL 并验证结果。"""
    # 导航到数据页
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=15000)

    # 切换到 SQL 控制台 Tab
    sql_tab = I18n.get("data_tab_sql")
    await e2e_page.click_tab(sql_tab, timeout_ms=10000)

    # 点击预设的 SELECT * LIMIT 10 按钮填充 SQL
    limit_btn = e2e_page.page.get_by_text("SELECT * LIMIT 10").first
    await limit_btn.wait_for(state="attached", timeout=8000)
    await limit_btn.click()

    # 稍微等待 Flet 状态更新
    await e2e_page.page.wait_for_timeout(500)

    # 点击执行按钮
    execute_text = I18n.get("data_sql_execute")
    await e2e_page.click_button(execute_text, timeout_ms=8000)

    # 验证结果中出现平安银行
    await e2e_page.expect_text("平安银行", timeout_ms=15000)


async def test_sql_console_error(e2e_page):
    """E3: SQL 控制台执行非法 SQL 时显示错误提示。"""
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=15000)

    sql_tab = I18n.get("data_tab_sql")
    await e2e_page.click_tab(sql_tab, timeout_ms=10000)

    # 输入非法 SQL
    sql_label = I18n.get("data_sql_label")
    await e2e_page.fill_textbox(sql_label, "SELECTT * FROM nonexistent", timeout_ms=8000)

    execute_text = I18n.get("data_sql_execute")
    await e2e_page.click_button(execute_text, timeout_ms=8000)

    # 验证错误提示（取 i18n 前缀，UI 显示已 format 的完整错误信息）
    error_prefix = I18n.get("data_sql_error").split(":")[0]
    await e2e_page.expect_text(error_prefix, timeout_ms=10000)


async def test_table_viewer_switch(e2e_page):
    """D5: 数据浏览切换表后数据更新。"""
    data_label = I18n.get("nav_data")
    await e2e_page.click_text(data_label, timeout_ms=15000)

    explorer_tab = I18n.get("data_tab_explorer")
    await e2e_page.expect_text(explorer_tab)

    # 选择 daily_quotes 表
    table_label = I18n.get("data_select_table")
    await e2e_page.select_dropdown(table_label, "daily_quotes", timeout_ms=10000)
    await e2e_page.page.wait_for_timeout(2000)

    query_btn = e2e_page.page.locator('[aria-label="查询"], [aria-label*="查询"]').first
    await query_btn.wait_for(state="attached", timeout=8000)
    await query_btn.click(timeout=10000, force=True)

    await e2e_page.expect_text("000001.SZ", timeout_ms=15000)

    # 切换到 stock_basic 表
    await e2e_page.select_dropdown(table_label, "stock_basic", timeout_ms=10000)
    await e2e_page.page.wait_for_timeout(2000)

    await query_btn.wait_for(state="attached", timeout=8000)
    await query_btn.click(timeout=10000, force=True)

    # 验证 stock_basic 表数据
    await e2e_page.expect_text("000001.SZ", timeout_ms=15000)
