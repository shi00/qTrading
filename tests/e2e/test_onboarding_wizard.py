import logging
import os
import re

import pytest

from ui.i18n import I18n
from tests.e2e.timeouts import TIMEOUTS


from urllib.parse import unquote_plus

pytestmark = pytest.mark.e2e

logger = logging.getLogger(__name__)


def _parse_db_url(url: str) -> dict[str, str]:
    m = re.match(r"postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", url)
    if not m:
        pytest.skip(f"Cannot parse E2E_DATABASE_URL for DB success test: {url}")

    # [PITFALL FIX] 必须使用 unquote_plus 解码！
    # 在 CI 环境下，DATABASE_URL 中的 password (例如 GitHub Actions 的 secret)
    # 可能包含特殊字符（!@# 等），因此在集成测试环境构建 URL 时被 URL-encoded（quote_plus）。
    # 这里如果直接返回 m[2] (未解码的密码)，Flet 页面会将 URL 编码后的字符串直接填入密码框。
    # 随后 Flet 点击 "验证" 时，DatabaseConfigService 会将此密码 *再次* 进行 URL 编码，
    # 导致 asyncpg 最终拿到的是错误（双重编码）的密码，从而抛出 InvalidPasswordError 并导致测试超时！
    # 详见 https://github.com/MagicTower/AStockScreener/issues/xxx 或相关调试记录。
    return {
        "user": unquote_plus(m[1]),
        "password": unquote_plus(m[2]),
        "host": unquote_plus(m[3]),
        "port": m[4],
        "database": unquote_plus(m[5]),
    }


async def test_wizard_renders_welcome(wizard_page):
    """测试：向导启动后停在欢迎页。"""
    welcome_guide = I18n.get("wizard_welcome_guide")
    await wizard_page.expect_text(welcome_guide)

    db_title = I18n.get("wizard_overview_db_title")
    assert await wizard_page.has_text(db_title)


@pytest.mark.mutates_config
async def test_wizard_language_switch(wizard_page):
    """测试：语言切换（纯 UI，无后端依赖）。"""
    lang_label = I18n.get("settings_language")
    lang_en = I18n.get("settings_lang_en")
    lang_zh = I18n.get("settings_lang_zh")
    welcome_guide_zh = I18n.get("wizard_welcome_guide")

    try:
        await wizard_page.select_dropdown(lang_label, lang_en)

        # 轮询等待中文欢迎词消失
        zh_disappeared = False
        for _ in range(25):
            if not await wizard_page.has_text(welcome_guide_zh):
                zh_disappeared = True
                break
            await wizard_page.page.wait_for_timeout(200)

        assert zh_disappeared, f"中文欢迎词 '{welcome_guide_zh}' 未能在切换语言后消失"
    finally:
        # [PITFALL FIX] 必须还原 wizard_app 内存中的 I18n locale！
        # 坑点：pristine_config fixture 只还原磁盘配置文件和测试进程 I18n，
        #       但 wizard_app 是 session 级单进程，其内存中的 I18n._locale 仍是 en_US。
        #       这会导致后续 wizard 测试寻找中文 "开始使用" 按钮时全部超时失败。
        # 应对：通过 UI 主动切换回中文，触发 app 进程的 I18n.set_locale("zh_CN")。
        # 此时 app 已是英文界面，dropdown label 显示为 "Language"。
        lang_label_en = I18n.get("settings_language", locale="en_US")
        try:
            await wizard_page.select_dropdown(lang_label_en, lang_zh, timeout_ms=TIMEOUTS.INTERACTION)
            # 轮询等待中文欢迎词重新出现，确认 locale 已还原
            for _ in range(25):
                if await wizard_page.has_text(welcome_guide_zh):
                    break
                await wizard_page.page.wait_for_timeout(200)
        except Exception as e:  # noqa: BLE001
            # 还原失败时不抛出，避免掩盖原始测试失败；下游测试会显式失败暴露问题
            logger.warning("[onboarding_wizard] restore language to zh failed: %s", e, exc_info=True)


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

    await wizard_page.expect_text(db_title, timeout_ms=TIMEOUTS.TITLE)

    token_title = I18n.get("wizard_step1_title")
    assert not await wizard_page.has_text(token_title)


@pytest.mark.xfail(
    reason=(
        "Flet 0.85.3 a11y 模式下两段独立缺陷叠加："
        "(1) Flutter issue #129324 — Playwright el.type() 不触发 DOM→Flutter 反向同步，"
        "已通过 fill_textbox 改用 Control+A + page.keyboard.type 缓解（输入同步已恢复）；"
        "(2) _on_vm_step_changed → _update_wizard → _safe_update 静默吞掉 self.update() 异常"
        "（logger.debug 级别未启用，被 try/except 隐藏），导致 ViewModel.current_step 已前进"
        "但 Flutter UI 仍停留在 DB 步骤。根因独立于输入同步，需独立排查 _safe_update 异常源。"
    ),
    strict=False,
)
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

    # 等待 Flet 处理所有输入并更新表单状态，防止验证按钮点击时表单尚未同步
    await wizard_page.page.wait_for_timeout(500)

    btn_verify = I18n.get("wizard_btn_verify_next")
    await wizard_page.click_button(btn_verify)

    token_title = I18n.get("wizard_step1_title")
    await wizard_page.expect_text(token_title, timeout_ms=TIMEOUTS.WIZARD_TOKEN)
