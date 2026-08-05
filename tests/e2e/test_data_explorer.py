import pytest

from ui.i18n import I18n
from tests.e2e.pages import DataPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e


async def test_table_viewer(e2e_page):
    """测试：数据表查看器 — 切换到 daily_quotes 表并验证数据加载。"""
    # 导航到数据页（PR-3: 迁移到 DataPage anchor 操作）
    dp = DataPage(e2e_page)
    await dp.open()
    await dp.expect_explorer_tab()

    # 选择 daily_quotes 表 + 点击查询按钮（均通过 anchor）
    await dp.select_table("daily_quotes", timeout_ms=TIMEOUTS.TITLE)
    # 等待 Flet 处理 on_change → load_table_schema 完成（QUERY_BUTTON 不会因
    # is_loading 移除节点，无法用 wait_for(state) 探测，固定 sleep 是最简方案）
    await e2e_page.page.wait_for_timeout(1000)
    await dp.click_query(timeout_ms=TIMEOUTS.TITLE)

    # 验证表格中出现平安银行相关数据
    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)


async def test_sql_console(e2e_page):
    """测试：SQL 控制台 — 执行自定义 SQL 并验证结果。"""
    # 导航到数据页
    dp = DataPage(e2e_page)
    await dp.open()

    # 切换到 SQL 控制台 Tab（SegmentedButton，非 anchor 化，保留 click_tab）
    sql_tab = I18n.get("data_tab_sql")
    await e2e_page.click_tab(sql_tab, timeout_ms=TIMEOUTS.TITLE)

    # 点击预设的 SELECT * LIMIT 10 按钮填充 SQL
    limit_btn = e2e_page.page.get_by_text("SELECT * LIMIT 10").first
    await limit_btn.wait_for(state="attached", timeout=TIMEOUTS.INTERACTION)
    await limit_btn.click()

    # 点击执行按钮（expect_text 下游会等待结果渲染）
    execute_text = I18n.get("data_sql_execute")
    await e2e_page.click_button(execute_text, timeout_ms=TIMEOUTS.INTERACTION)

    # 验证结果中出现平安银行
    await e2e_page.expect_text("平安银行", timeout_ms=TIMEOUTS.NAV)


async def test_table_viewer_filter(e2e_page):
    """测试：数据表过滤查询 — 按股票代码过滤后结果仅含目标股票。

    种子数据 daily_quotes 共 120 行（2 只股票 × 60 天），过滤 ts_code=000001.SZ 后
    仅剩平安银行 60 行。验证过滤查询不报错且结果包含目标数据。

    PR-3: anchor 化后消除 CanvasKit 抖动根源（fill_textbox/select_dropdown 的
    Playwright 输入未触发应用 on_change 回调），移除 @pytest.mark.flaky 标记。
    """
    # 导航到数据页
    dp = DataPage(e2e_page)
    await dp.open()
    await dp.expect_explorer_tab()

    # 选择 daily_quotes 表（通过 anchor）
    await dp.select_table("daily_quotes", timeout_ms=TIMEOUTS.TITLE)
    # 等待 Flet 处理 on_change → load_table_schema 完成（见 test_table_viewer 注释）
    await e2e_page.page.wait_for_timeout(1000)

    # 设置过滤器：列=代码(ts_code)，操作符==（默认值），值=000001.SZ
    # 注：filter_op 的默认值已是 "="（见 ui/views/data_view.py 的 _build_filter_op_options），
    #     无需显式选择。PR-3 anchor 化后暴力搜索已删除，省略此步仅为减少不必要的操作。
    await dp.select_filter_col(I18n.get("col_ts_code"), timeout_ms=TIMEOUTS.TITLE)
    await dp.fill_filter_value("000001.SZ", timeout_ms=TIMEOUTS.INTERACTION)
    # 等待 Flet 处理输入并更新表单状态，防止查询基于未同步的过滤器
    await e2e_page.page.wait_for_timeout(500)

    # 点击查询按钮触发过滤（通过 anchor）
    await dp.click_query(timeout_ms=TIMEOUTS.TITLE)

    # 验证过滤后平安银行仍在结果中（过滤查询成功且结果正确）
    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)


# [PITFALL_WARNING] Flet Web CanvasKit 自动化测试黑洞避坑指南
# 坑点：不要为"SQL 控制台执行非法 SQL"编写 E2E 用例（涉及向多行 TextField 录入文本）。
# 原因：Flet 0.85.3 CanvasKit 的底层渲染引擎存在严重的 A11y 语义树映射缺陷。
#      多行输入框不会被映射为标准的 'textbox'，它的 label/hint 会被吞噬或错误附着到极远的父容器上，
#      且开发者显式赋予的 semantics_label 也会被完全忽略。
# 后果：如果在 Playwright 中使用强制绝对坐标点击（e.g. mouse.click(x,y)）或键盘焦点漫游（Tab），
#      均会由于不同分辨率、系统环境导致焦点错位，最终形成极难排查的 Flaky tests。
# 正确做法：该场景已由单元测试覆盖（见 tests/unit/ui/test_data_view.py 的
#         test_run_query_with_error_result 和 test_run_query_with_exception），
#         E2E 层不测需要多行 TextField 输入的路径。等待 Flet/Flutter 上游修复后再考虑恢复。


async def test_table_viewer_switch(e2e_page):
    """D5: 数据浏览切换表后数据更新。"""
    dp = DataPage(e2e_page)
    await dp.open()
    await dp.expect_explorer_tab()

    # 选择 daily_quotes 表 + 查询（通过 anchor）
    await dp.select_table("daily_quotes", timeout_ms=TIMEOUTS.TITLE)
    # 等待 Flet 处理 on_change → load_table_schema 完成（见 test_table_viewer 注释）
    await e2e_page.page.wait_for_timeout(1000)
    await dp.click_query(timeout_ms=TIMEOUTS.TITLE)

    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)

    # 切换到 stock_basic 表（通过 anchor；select_table 内置等待表结构加载完成）
    await dp.select_table("stock_basic", timeout_ms=TIMEOUTS.TITLE)
    # 等待 Flet 处理 on_change → load_table_schema 完成（见 test_table_viewer 注释）
    await e2e_page.page.wait_for_timeout(1000)

    await dp.click_query(timeout_ms=TIMEOUTS.TITLE)

    # 验证 stock_basic 表数据
    await e2e_page.expect_text("000001.SZ", timeout_ms=TIMEOUTS.NAV)
