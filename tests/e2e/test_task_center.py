import pytest

from ui.i18n import I18n
from tests.e2e.labels import strategy_label
from tests.e2e.pages import App, ScreenerPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e


async def test_task_center_shows_task(e2e_page):
    """测试：在选股页触发任务后，任务中心显示对应任务记录。"""
    # 先在选股页触发一个策略任务
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()

    # 切换到任务中心页
    app = App(e2e_page)
    await app.goto("nav_tasks")

    # 轮询等待任务记录出现（替代固定 2s sleep，等待任务提交完成）
    empty_title = I18n.get("task_empty_title")
    for _ in range(20):  # 最多 4s，每 200ms 检查一次
        if not await e2e_page.has_text(empty_title):
            break
        await e2e_page.page.wait_for_timeout(200)

    # 验证任务记录存在（宽松断言：不出现空状态即可）
    has_empty = await e2e_page.has_text(empty_title)
    assert not has_empty, "任务中心不应显示空状态 — 应存在至少一条任务记录"

    # 强断言：验证具体策略任务名出现（而非仅非空状态）
    vb_name = strategy_label("volume_breakout")
    await e2e_page.expect_text(vb_name, timeout_ms=TIMEOUTS.TITLE)
