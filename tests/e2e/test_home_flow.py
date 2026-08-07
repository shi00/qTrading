import pytest

from tests.e2e.pages import HomePage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = [pytest.mark.timeout(180), pytest.mark.e2e, pytest.mark.timeout_e2e_standard]


async def test_home_view_loads(e2e_page):
    """测试：市场页（HomeView）基础加载 — 验证核心区块的静态标签可见。

    断言聚焦于 __init__ 阶段同步渲染的静态标签，不依赖异步行情/新闻数据加载，
    避免 E2E 离线环境下因外部 API abort 导致的 flaky。

    PR-4 Task 4.2: KPI 卡片断言迁移到 HomePage (anchor-based expect_visible)，
    非 anchor 化控件（页面标题、热门概念标题、新闻区标题）仍走文本断言。
    """
    home = HomePage(e2e_page)

    # 验证页面标题（非 anchor 化，走文本断言）
    await home.expect_title(timeout_ms=TIMEOUTS.TITLE)

    # 验证市场仪表盘的 4 个 KPI 卡片（anchor-based expect_visible）
    await home.expect_kpi_sh(timeout_ms=TIMEOUTS.TITLE)
    await home.expect_kpi_sz(timeout_ms=TIMEOUTS.TITLE)
    await home.expect_kpi_cyb(timeout_ms=TIMEOUTS.TITLE)
    await home.expect_kpi_northbound(timeout_ms=TIMEOUTS.TITLE)

    # 验证热门概念区标题（非 anchor 化，走文本断言）
    await home.expect_hot_concepts_title(timeout_ms=TIMEOUTS.TITLE)

    # 验证新闻区标题（非 anchor 化，走文本断言）
    await home.expect_live_news_title(timeout_ms=TIMEOUTS.TITLE)
