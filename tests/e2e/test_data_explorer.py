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

    # 点击查询按钮
    query_tooltip = I18n.get("common_query")
    await e2e_page.click_button(query_tooltip, timeout_ms=8000)

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

    # 在 SQL 编辑器中输入查询
    sql_label = I18n.get("data_sql_label")
    await e2e_page.fill_textbox(
        sql_label, "SELECT name, ts_code FROM stock_basic WHERE symbol = '000001'", timeout_ms=8000
    )

    # 点击执行按钮
    execute_text = I18n.get("data_sql_execute")
    await e2e_page.click_button(execute_text, timeout_ms=8000)

    # 验证结果中出现平安银行
    await e2e_page.expect_text("平安银行", timeout_ms=15000)
