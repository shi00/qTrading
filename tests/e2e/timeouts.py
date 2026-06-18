"""E2E 测试超时常量集中配置。

提供基础超时值（未缩放），由 FletPage._tm() 按全局倍率缩放。
用法：
    from tests.e2e.timeouts import TIMEOUTS
    await e2e_page.click_text("选股", timeout_ms=TIMEOUTS.NAV)

注：E2E_TIMEOUT_MULTIPLIER 环境变量由 conftest.py 读取并传入 FletPage，
此处不重复缩放，避免双重倍率。
"""


class TIMEOUTS:
    # 页面打开（最慢，含 CanvasKit 加载）
    PAGE_OPEN = 45000
    # 导航点击（侧边栏切换）
    NAV = 15000
    # 标题/文本出现（页面渲染完成）
    TITLE = 10000
    # 通用交互默认（按钮、下拉、输入框）
    INTERACTION = 8000
    # 快速断言（tab 切换、snackbar）
    FAST = 5000
    # 选股结果（策略执行 + 渲染）
    SCREEN_RESULT = 30000
    # 回测首卡（策略执行 + NAV 计算）
    BACKTEST = 60000
    # 引导向导 token 校验
    WIZARD_TOKEN = 20000
