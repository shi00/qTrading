"""E2E: embedded 模式 Onboarding 全流程测试 (P3-18, P1-8).

验证 embedded 模式下 Onboarding 数据库步骤的零配置体验:
1. 启动 app (embedded 模式 + fake_sidecar)
2. Onboarding database step 显示 "本地数据库已自动准备" 只读状态
3. 不显示 host/port/user/password/database 必填项
4. 点击 "验证并继续" 完成 database step
5. 进入后续步骤 (Token/AI 等)
6. 导航回退正常 (database step → 欢迎页)
7. EmbeddedStatusCard 完整状态显示 (status_message + info_message)

关键约束:
- 禁 xFail (DoD 11 + user_profile 强制约束)
- Windows skipif 合规 (skipif ≠ xFail，CanvasKit 渲染问题)
- i18n key 来自 EmbeddedStatusCardViewModel: embedded_pg_ready / embedded_pg_no_config_needed
- Windows CanvasKit 修复受阻: 保留 skipif, 记录为 P1-14 决策 (见 reviews/pg_plan.md §22)
"""

import sys

import pytest

from tests.e2e.timeouts import TIMEOUTS
from ui.i18n import I18n

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows Flet/Playwright CanvasKit textbox 渲染问题 (P3-WinE2E-Skip)",
)
async def test_embedded_onboarding_zero_config_first_launch(embedded_wizard_page) -> None:
    """E2E: embedded 模式首次启动 Onboarding 全流程 (P3-18, DoD 11, 禁 xFail)。

    验证：
    1. 启动 app (embedded 模式 + fake_sidecar)
    2. Onboarding database step 显示 "本地数据库已自动准备" 只读状态
    3. 不显示 host/port/user/password/database 必填项
    4. 点击 "验证并继续" 完成 database step
    5. 进入后续步骤 (Token/AI 等)
    """
    # 1. 验证欢迎页
    welcome_guide = I18n.get("wizard_welcome_guide")
    await embedded_wizard_page.expect_text(welcome_guide)

    # 2. 点击 "开始使用" 进入 database step
    btn_start = I18n.get("wizard_btn_start")
    await embedded_wizard_page.click_button(btn_start)

    # 3. 验证 embedded 模式只读状态 (EmbeddedStatusCard 显示的 i18n key)
    embedded_ready_text = I18n.get("embedded_pg_ready")
    await embedded_wizard_page.expect_text(embedded_ready_text)

    # 4. 验证不显示 external 模式的表单输入框
    #    (用外部模式独有按钮 "测试连接" 作为判断依据，
    #     避免 "主机" 被 embedded_pg_no_config_needed 误匹配)
    db_test_conn_btn = I18n.get("db_test_connection")
    assert not await embedded_wizard_page.has_text(db_test_conn_btn)

    # 5. 点击 "验证并继续" 完成 database step
    btn_verify = I18n.get("wizard_btn_verify_next")
    await embedded_wizard_page.click_button(btn_verify)

    # 6. 验证进入 Token step
    token_title = I18n.get("wizard_step1_title")
    await embedded_wizard_page.expect_text(token_title, timeout_ms=TIMEOUTS.WIZARD_TOKEN)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows Flet/Playwright CanvasKit textbox 渲染问题 (P3-WinE2E-Skip)",
)
async def test_embedded_wizard_forward_then_back(embedded_wizard_page) -> None:
    """E2E: embedded 模式导航回退 (欢迎→database step→返回欢迎) (P1-8).

    验证 EmbeddedStatusCard 替代 ExternalPgForm 后, 导航回退逻辑不受影响:
    1. 欢迎页 → 点击 "开始使用" → database step (EmbeddedStatusCard 显示)
    2. 点击 "上一步" → 返回欢迎页
    """
    # 1. 验证欢迎页
    welcome_guide = I18n.get("wizard_welcome_guide")
    await embedded_wizard_page.expect_text(welcome_guide)

    # 2. 点击 "开始使用" 进入 database step
    btn_start = I18n.get("wizard_btn_start")
    await embedded_wizard_page.click_button(btn_start)

    # 3. 验证 embedded 模式只读状态 (确认进入 database step)
    embedded_ready_text = I18n.get("embedded_pg_ready")
    await embedded_wizard_page.expect_text(embedded_ready_text)

    # 4. 点击 "上一步" 返回欢迎页
    btn_prev = I18n.get("wizard_btn_prev")
    await embedded_wizard_page.click_button(btn_prev)

    # 5. 验证返回欢迎页
    await embedded_wizard_page.expect_text(welcome_guide)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows Flet/Playwright CanvasKit textbox 渲染问题 (P3-WinE2E-Skip)",
)
async def test_embedded_db_info_message_displayed(embedded_wizard_page) -> None:
    """E2E: embedded 模式 EmbeddedStatusCard 显示完整状态 (status + info) (P1-8).

    验证 EmbeddedStatusCardViewModel 初始化的 status_message 和 info_message
    都在 UI 中正确渲染:
    1. status_message: "本地数据库已自动准备" (embedded_pg_ready)
    2. info_message: "无需配置主机/端口/密码" (embedded_pg_no_config_needed)
    """
    # 1. 点击 "开始使用" 进入 database step
    btn_start = I18n.get("wizard_btn_start")
    await embedded_wizard_page.click_button(btn_start)

    # 2. 验证 status_message 显示 (embedded_pg_ready)
    embedded_ready_text = I18n.get("embedded_pg_ready")
    await embedded_wizard_page.expect_text(embedded_ready_text)

    # 3. 验证 info_message 显示 (embedded_pg_no_config_needed)
    embedded_no_config_text = I18n.get("embedded_pg_no_config_needed")
    await embedded_wizard_page.expect_text(embedded_no_config_text)
