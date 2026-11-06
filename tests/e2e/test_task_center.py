# NOTE(lazy): FUP-014 未为任务行数值型进度条 (EIDS.TASK_CENTER.task_progress) 新增 E2E 断言.
# ceiling: E2E 中 RUNNING 任务存活时间短于轮询周期，且 Flet ProgressBar 语义节点是否稳定
#   暴露 aria-valuenow 未验证，强行断言会 flaky；数值语义改由 anchor 单测覆盖.
# upgrade: E2E 提供可暂停的 RUNNING 任务夹具，或实测 flt-semantics 稳定暴露 aria-valuenow
#   后，补充「aria-valuenow 可解析且在 0..1」断言.
import pytest

from tests.e2e.labels import strategy_label
from tests.e2e.pages import ScreenerPage, TaskCenterPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = [pytest.mark.timeout(180), pytest.mark.e2e, pytest.mark.timeout_e2e_standard]


async def test_task_center_shows_task(e2e_page):
    """测试：在选股页触发任务后，任务中心显示对应任务记录。

    PR-4 Task 4.2: 迁移到 TaskCenterPage (anchor-based nav + task_list expect_visible)。
    任务名断言仍走文本匹配（task_id 在运行时生成，anchor 化收益有限）。
    """
    # 先在选股页触发一个策略任务
    screener = ScreenerPage(e2e_page)
    await screener.open()
    await screener.select_strategy("volume_breakout")
    await screener.run()

    # 切换到任务中心页（anchor-based nav 点击）
    task_center = TaskCenterPage(e2e_page)
    await task_center.open()

    # 轮询等待任务记录出现（替代固定 2s sleep，等待任务提交完成）
    for _ in range(20):  # 最多 4s，每 200ms 检查一次
        if not await task_center.has_empty_state():
            break
        await e2e_page.page.wait_for_timeout(200)

    # 验证任务记录存在（宽松断言：不出现空状态即可）
    has_empty = await task_center.has_empty_state()
    assert not has_empty, "任务中心不应显示空状态 — 应存在至少一条任务记录"

    # 强断言：验证具体策略任务名出现（而非仅非空状态）
    vb_name = strategy_label("volume_breakout")
    await task_center.expect_task_name(vb_name, timeout_ms=TIMEOUTS.TITLE)
