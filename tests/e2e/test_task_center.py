import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_task_center_shows_task(e2e_page):
    """测试：在选股页触发任务后，任务中心显示对应任务记录。"""
    # 先在选股页触发一个策略任务
    screener_label = I18n.get("nav_screener")
    await e2e_page.click_text(screener_label, timeout_ms=15000)

    select_label = I18n.get("select_strategy")
    await e2e_page.select_dropdown(select_label, "volume_breakout", timeout_ms=10000)

    run_text = I18n.get("run_screening")
    await e2e_page.click_button(run_text, timeout_ms=10000)

    # 短暂等待任务提交
    await e2e_page.page.wait_for_timeout(2000)

    # 切换到任务中心页
    tasks_label = I18n.get("nav_tasks")
    await e2e_page.click_text(tasks_label, timeout_ms=15000)

    # 验证任务记录存在（宽松断言：不出现空状态即可）
    empty_title = I18n.get("task_empty_title")
    has_empty = await e2e_page.has_text(empty_title)
    assert not has_empty, "任务中心不应显示空状态 — 应存在至少一条任务记录"
