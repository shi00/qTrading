import pytest

from ui.i18n import I18n
from tests.e2e.timeouts import TIMEOUTS

pytestmark = pytest.mark.e2e


async def test_home_view_loads(e2e_page):
    """测试：市场页（HomeView）基础加载 — 验证核心区块的静态标签可见。

    断言聚焦于 __init__ 阶段同步渲染的静态标签，不依赖异步行情/新闻数据加载，
    避免 E2E 离线环境下因外部 API abort 导致的 flaky。
    """
    # 应用启动默认停在市场页，无需导航点击
    # 验证页面标题
    await e2e_page.expect_text(I18n.get("home_title"), timeout_ms=TIMEOUTS.TITLE)

    # 验证市场仪表盘的指数/资金标签
    await e2e_page.expect_text(I18n.get("home_index_sh"), timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.expect_text(I18n.get("home_index_sz"), timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.expect_text(I18n.get("home_index_cyb"), timeout_ms=TIMEOUTS.TITLE)
    await e2e_page.expect_text(I18n.get("home_northbound"), timeout_ms=TIMEOUTS.TITLE)

    # 验证热门概念区标题
    await e2e_page.expect_text(I18n.get("home_hot_concepts"), timeout_ms=TIMEOUTS.TITLE)

    # 验证新闻区标题
    await e2e_page.expect_text(I18n.get("home_live_news"), timeout_ms=TIMEOUTS.TITLE)
