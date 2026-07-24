"""E2E: 真实 sidecar + 真实 PG 的 embedded 模式 Onboarding 全流程测试。

覆盖 spec §3.6：真实 sidecar 完整应用启动 E2E 验证。

与 ``test_onboarding_embedded.py`` 的区别：
- 后者用 ``fake_sidecar``（Python 脚本模拟 sidecar 协议）
- 本测试用真实 Rust sidecar binary + 真实 PG 17，验证完整应用启动链路

验证内容：
1. 真实 sidecar 启动 → app 内部 ``prepare_database_runtime()`` 协调
2. Onboarding database step 显示 "本地数据库已自动准备" 只读状态
3. 点击 "验证并继续" 完成 database step
4. 进入后续步骤（Token/AI 等）
5. 导航回退正常

依赖：
- ``embedded_real_wizard_page`` fixture（真实 sidecar + 真实 PG 启动的 wizard app）

标记：
- ``pytest.mark.e2e``
- ``pytest.mark.embedded_real``

Windows skipif：
- 对齐 ``test_onboarding_embedded.py`` 的 CanvasKit 渲染问题 skipif
- 用户决策：Windows E2E 保留 skipif（推荐）
"""

import sys

import pytest

from tests.e2e.helpers.app_launcher import PROJECT_ROOT
from tests.e2e.timeouts import TIMEOUTS
from ui.i18n import I18n

pytestmark = [pytest.mark.e2e, pytest.mark.embedded_real]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows Flet/Playwright CanvasKit textbox 渲染问题 (P3-WinE2E-Skip)",
)
async def test_embedded_real_onboarding_zero_config_first_launch(embedded_real_wizard_page) -> None:
    """E2E: 真实 sidecar embedded 模式首次启动 Onboarding 全流程。

    验证：
    1. 启动 app (embedded 模式 + 真实 sidecar)
    2. Onboarding database step 显示 "本地数据库已自动准备" 只读状态
    3. 不显示 host/port/user/password/database 必填项
    4. 点击 "验证并继续" 完成 database step
    5. 进入后续步骤 (Token/AI 等)
    """
    # 1. 验证欢迎页
    welcome_guide = I18n.get("wizard_welcome_guide")
    await embedded_real_wizard_page.expect_text(welcome_guide)

    # 2. 点击 "开始使用" 进入 database step
    btn_start = I18n.get("wizard_btn_start")
    await embedded_real_wizard_page.click_button(btn_start)

    # 3. 验证 embedded 模式只读状态 (EmbeddedStatusCard 显示的 i18n key)
    embedded_ready_text = I18n.get("embedded_pg_ready")
    await embedded_real_wizard_page.expect_text(embedded_ready_text)

    # 4. 验证不显示 external 模式的表单输入框
    #    (用外部模式独有按钮 "测试连接" 作为判断依据，
    #     避免 "主机" 被 embedded_pg_no_config_needed 误匹配)
    db_test_conn_btn = I18n.get("db_test_connection")
    assert not await embedded_real_wizard_page.has_text(db_test_conn_btn)

    # 5. 点击 "验证并继续" 完成 database step
    btn_verify = I18n.get("wizard_btn_verify_next")
    await embedded_real_wizard_page.click_button(btn_verify)

    # 6. 验证进入 Token step
    token_title = I18n.get("wizard_step1_title")
    await embedded_real_wizard_page.expect_text(token_title, timeout_ms=TIMEOUTS.WIZARD_TOKEN)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows Flet/Playwright CanvasKit textbox 渲染问题 (P3-WinE2E-Skip)",
)
async def test_embedded_real_wizard_forward_then_back(embedded_real_wizard_page) -> None:
    """E2E: 真实 sidecar embedded 模式导航回退 (欢迎→database step→返回欢迎)。

    验证真实 sidecar 启动后，导航回退逻辑不受影响。
    """
    # 1. 验证欢迎页
    welcome_guide = I18n.get("wizard_welcome_guide")
    await embedded_real_wizard_page.expect_text(welcome_guide)

    # 2. 点击 "开始使用" 进入 database step
    btn_start = I18n.get("wizard_btn_start")
    await embedded_real_wizard_page.click_button(btn_start)

    # 3. 验证 embedded 模式只读状态 (确认进入 database step)
    embedded_ready_text = I18n.get("embedded_pg_ready")
    await embedded_real_wizard_page.expect_text(embedded_ready_text)

    # 4. 点击 "上一步" 返回欢迎页
    btn_prev = I18n.get("wizard_btn_prev")
    await embedded_real_wizard_page.click_button(btn_prev)

    # 5. 验证返回欢迎页
    await embedded_real_wizard_page.expect_text(welcome_guide)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows Flet/Playwright CanvasKit textbox 渲染问题 (P3-WinE2E-Skip)",
)
async def test_embedded_real_db_info_message_displayed(embedded_real_wizard_page) -> None:
    """E2E: 真实 sidecar embedded 模式 EmbeddedStatusCard 显示完整状态 (status + info)。

    验证 EmbeddedStatusCardViewModel 初始化的 status_message 和 info_message
    都在 UI 中正确渲染。
    """
    # 1. 点击 "开始使用" 进入 database step
    btn_start = I18n.get("wizard_btn_start")
    await embedded_real_wizard_page.click_button(btn_start)

    # 2. 验证 status_message 显示 (embedded_pg_ready)
    embedded_ready_text = I18n.get("embedded_pg_ready")
    await embedded_real_wizard_page.expect_text(embedded_ready_text)

    # 3. 验证 info_message 显示 (embedded_pg_no_config_needed)
    embedded_no_config_text = I18n.get("embedded_pg_no_config_needed")
    await embedded_real_wizard_page.expect_text(embedded_no_config_text)


async def test_real_embedded_app_db_queryable(embedded_real_wizard_page) -> None:
    """E2E: 真实 sidecar 启动后，应用日志间接验证 DB 已就绪 + 表已迁移 (spec §3.6 用例 2)。

    验证：
    1. 应用日志含 ``[Bootstrap] embedded postgres ready on 127.0.0.1:<port>``
       — 证明真实 sidecar 启动 + PG 就绪
    2. 应用日志含 ``[TaskManager] init_db``
       — 证明 CacheManager.init_db 已执行（含 alembic 迁移，表已创建）

    决策：测试进程无法跨进程获取 app 子进程的 sidecar URL（密码在子进程内存的
    password_file 中，不输出到日志），改为验证日志含 ready + 迁移消息，间接验证
    DB 可查询性（实际 SQL 查询由集成测试 test_embedded_pg_bootstrap.py 覆盖）。

    注意：使用 ``embedded_real_wizard_page`` 而非 ``embedded_real_wizard_app``
    是因为 Flet Web 模式下 ``main(page)`` 仅在第一个 page 连接时才执行，
    若不连接浏览器则 sidecar 不会启动、日志为空。
    """
    welcome_guide = I18n.get("wizard_welcome_guide")
    await embedded_real_wizard_page.expect_text(welcome_guide)

    log_path = PROJECT_ROOT / "logs" / "e2e-flet-app.log"
    content = log_path.read_text(encoding="utf-8", errors="replace")
    assert "[Bootstrap] embedded postgres ready on 127.0.0.1:" in content, (
        "应用日志未含 embedded postgres ready 消息，sidecar 可能未启动成功"
    )
    assert "[TaskManager] init_db" in content, "应用日志未含 init_db 消息，迁移可能未执行"
