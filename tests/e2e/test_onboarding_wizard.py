import os
import re

import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


def _parse_db_url(url: str) -> dict[str, str]:
    m = re.match(r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", url)
    if not m:
        pytest.skip(f"Cannot parse E2E_DATABASE_URL for DB success test: {url}")
    return {"user": m[1], "password": m[2], "host": m[3], "port": m[4], "database": m[5]}


async def test_wizard_renders_welcome(wizard_page):
    """测试：向导启动后停在欢迎页。"""
    welcome_guide = I18n.get("wizard_welcome_guide")
    await wizard_page.expect_text(welcome_guide)

    db_title = I18n.get("wizard_overview_db_title")
    assert await wizard_page.has_text(db_title)


async def test_wizard_language_switch(wizard_page):
    """测试：语言切换（纯 UI，无后端依赖）。"""
    lang_label = I18n.get("settings_language")
    lang_en = I18n.get("settings_lang_en")
    await wizard_page.select_dropdown(lang_label, lang_en)

    welcome_guide_zh = I18n.get("wizard_welcome_guide")
    # 轮询等待中文欢迎词消失
    zh_disappeared = False
    for _ in range(25):
        if not await wizard_page.has_text(welcome_guide_zh):
            zh_disappeared = True
            break
        await wizard_page.page.wait_for_timeout(200)

    assert zh_disappeared, f"中文欢迎词 '{welcome_guide_zh}' 未能在切换语言后消失"

    # 恢复中文，避免污染后续依赖中文 locale 的测试
    # （wizard_app 是 session 级别 fixture，语言切换会持久化到 config）
    lang_zh = I18n.get("settings_lang_zh")
    await wizard_page.select_dropdown("language", lang_zh)


async def test_wizard_forward_then_back(wizard_page):
    """测试：欢迎→数据库→返回欢迎。"""
    btn_start = I18n.get("wizard_btn_start")
    await wizard_page.click_button(btn_start)

    db_title = I18n.get("wizard_db_title")
    await wizard_page.expect_text(db_title)

    btn_prev = I18n.get("wizard_btn_prev")
    await wizard_page.click_button(btn_prev)

    welcome_guide = I18n.get("wizard_welcome_guide")
    await wizard_page.expect_text(welcome_guide)


async def test_wizard_db_validation_failure(wizard_page):
    """测试：数据库校验失败时停留在当前步骤。"""
    btn_start = I18n.get("wizard_btn_start")
    await wizard_page.click_button(btn_start)

    db_title = I18n.get("wizard_db_title")
    await wizard_page.expect_text(db_title)

    db_host_label = I18n.get("db_host")
    await wizard_page.fill_textbox(db_host_label, "10.255.255.1")

    btn_verify = I18n.get("wizard_btn_verify_next")
    await wizard_page.click_button(btn_verify)

    await wizard_page.expect_text(db_title, timeout_ms=10000)

    token_title = I18n.get("wizard_step1_title")
    assert not await wizard_page.has_text(token_title)


async def test_wizard_db_validation_success(wizard_page):
    """测试：数据库校验成功后前进到 Token 步骤（A 类门禁，用 CI 测试库）。"""
    from tests.conftest import _get_test_db_url

    db_url = os.environ.get(
        "E2E_DATABASE_URL",
        _get_test_db_url(),
    )
    db = _parse_db_url(db_url)

    btn_start = I18n.get("wizard_btn_start")
    await wizard_page.click_button(btn_start)

    db_title = I18n.get("wizard_db_title")
    await wizard_page.expect_text(db_title)

    await wizard_page.fill_textbox(I18n.get("db_host"), db["host"])
    await wizard_page.fill_textbox(I18n.get("db_port"), db["port"])
    await wizard_page.fill_textbox(I18n.get("db_user"), db["user"])
    await wizard_page.fill_textbox(I18n.get("db_password"), db["password"])
    await wizard_page.fill_textbox(I18n.get("db_name"), db["database"])

    btn_verify = I18n.get("wizard_btn_verify_next")
    await wizard_page.click_button(btn_verify)

    token_title = I18n.get("wizard_step1_title")
    await wizard_page.expect_text(token_title, timeout_ms=15000)


@pytest.mark.network
@pytest.mark.slow
async def test_wizard_full_happy_path(wizard_page):
    """需 E2E_REAL_TS_TOKEN / E2E_REAL_AI_KEY 等真实凭证，走到"配置完成"。
    实现细节在 M3+ 落地；此处仅占位说明范围。"""
    pytest.skip("需要真实 Tushare/LLM 凭证，仅本地/夜间运行")
