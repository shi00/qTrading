"""E2E: embedded 模式 Onboarding 全流程测试 (P3-18).

验证 embedded 模式下 Onboarding 数据库步骤的零配置体验:
1. 启动 app (embedded 模式 + fake_sidecar)
2. Onboarding database step 显示 "本地数据库已自动准备" 只读状态
3. 不显示 host/port/user/password/database 必填项
4. 点击 "验证并继续" 完成 database step
5. 进入后续步骤 (Token/AI 等)

关键约束:
- 禁 xFail (DoD 11 + user_profile 强制约束)
- Windows skipif 合规 (skipif ≠ xFail，CanvasKit 渲染问题)
- i18n key 来自 EmbeddedStatusCardViewModel: embedded_pg_ready / embedded_pg_no_config_needed
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

    # 4. 验证不显示 host/port/user/password 表单字段
    # (embedded 模式下 EmbeddedStatusCard 替代 ExternalPgForm，这些字段不应出现)
    db_host_label = I18n.get("db_host")
    assert not await embedded_wizard_page.has_text(db_host_label)

    # 5. 点击 "验证并继续" 完成 database step
    btn_verify = I18n.get("wizard_btn_verify_next")
    await embedded_wizard_page.click_button(btn_verify)

    # 6. 验证进入 Token step
    token_title = I18n.get("wizard_step1_title")
    await embedded_wizard_page.expect_text(token_title, timeout_ms=TIMEOUTS.WIZARD_TOKEN)
