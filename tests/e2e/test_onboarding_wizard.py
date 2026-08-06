import logging
import os
import re
import sys

import pytest

from ui.i18n import I18n
from tests.e2e.pages import WizardPage
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
    await wizard_page.expect_text(welcome_guide, timeout_ms=TIMEOUTS.PAGE_OPEN)

    db_title = I18n.get("wizard_overview_db_title")
    assert await wizard_page.has_text(db_title)


@pytest.mark.mutates_config
async def test_wizard_language_switch(wizard_page):
    """测试：语言切换（纯 UI，无后端依赖）。

    PR-3 注记：wizard 中的语言 dropdown 未 anchor 化（onboarding_wizard.py 仅
    anchor 化了 PREV/SKIP/NEXT 按钮），保留 FletPage.select_dropdown 文本定位。
    """
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
        lang_label_en = I18n.get("settings_language", locale="en_US")
        try:
            await wizard_page.select_dropdown(lang_label_en, lang_zh, timeout_ms=TIMEOUTS.INTERACTION)
            # 轮询等待中文欢迎词重新出现，确认 locale 已还原
            for _ in range(25):
                if await wizard_page.has_text(welcome_guide_zh):
                    break
                await wizard_page.page.wait_for_timeout(200)
        except Exception as e:  # noqa: BLE001
            logger.warning("[onboarding_wizard] restore language to zh failed: %s", e, exc_info=True)
            I18n.set_locale("zh_CN")


async def test_wizard_forward_then_back(wizard_page):
    """测试：欢迎→数据库→返回欢迎。

    PR-3: prev 按钮迁移到 WizardPage anchor 操作（btn_start 未 anchor 化，保留 click_button）。
    """
    wp = WizardPage(wizard_page)
    btn_start = I18n.get("wizard_btn_start")
    await wizard_page.click_button(btn_start)

    db_title = I18n.get("wizard_db_title")
    await wizard_page.expect_text(db_title)

    # PR-3: prev 按钮已 anchor 化，用 WizardPage.click_prev 定位
    await wp.click_prev()

    welcome_guide = I18n.get("wizard_welcome_guide")
    await wizard_page.expect_text(welcome_guide)


# Tech debt: P3-WinE2E-Skip — Windows Flet/Playwright CanvasKit textbox 渲染 + 向导状态隔离问题。
# 详见 docs/debt/known-technical-debt.md P3-WinE2E-Skip。
# embedded 模式跳过：wizard database 步骤在 embedded 模式下渲染 EmbeddedStatusCard
# （只读状态卡片，无 host/port/user/password/database 表单字段），无法填写外部 DB 配置。
# embedded 模式下 database 步骤验证为 always-true（_validate_database_embedded），
# "验证失败"场景不存在；外部 DB 配置验证失败流程由集成测试覆盖（test_onboarding_wizard_integration.py）。
@pytest.mark.skipif(
    sys.platform == "win32" or os.environ.get("QTRADING_DATABASE_MODE", "embedded").lower() == "embedded",
    reason=(
        "Windows Flet/Playwright CanvasKit textbox 渲染 + 向导状态隔离问题 (P3-WinE2E-Skip); "
        "embedded 模式下 wizard database 步骤渲染 EmbeddedStatusCard（无外部 DB 表单），"
        "外部 DB 配置验证失败场景不存在（embedded 验证为 always-true）"
    ),
)
async def test_wizard_db_validation_failure(wizard_page):
    """测试：数据库校验失败时停留在当前步骤。

    PR-3 注记：btn_start / btn_verify_next / db_host 等 TextField 未 anchor 化
    （PR-3 范围仅 anchor 化 next/prev/skip/token），保留 FletPage.click_button / fill_textbox。
    """
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


# Tech debt: P3-WinE2E-Skip — Windows CI 环境 CanvasKit 中文字体网络加载失败致 textbox a11y 节点未渲染。
# 复验证据：CI run 30145028141 no-sidecar matrix FAILED，fill_textbox 在 wait_for(state='attached') 阶段超时。
# 根因分析：docs/debt/win-e2e-skip-revalidation/2026-07-25-wizard-db-validation-failure-analysis.md
# 决策记录：docs/debt/win-e2e-skip-revalidation/2026-07-25-decisions.md
# 替代覆盖：tests/integration/test_onboarding_wizard_integration.py
# 详见 docs/debt/known-technical-debt.md P3-WinE2E-Skip。
# embedded 模式跳过：wizard database 步骤在 embedded 模式下渲染 EmbeddedStatusCard
# （只读状态卡片，无 host/port/user/password/database 表单字段），无法填写外部 DB 配置。
# embedded 模式下 database 步骤验证为 always-true（_validate_database_embedded），
# 外部 DB 配置验证流程由集成测试覆盖（test_onboarding_wizard_integration.py）。
@pytest.mark.skipif(
    sys.platform == "win32" or os.environ.get("QTRADING_DATABASE_MODE", "embedded").lower() == "embedded",
    reason=(
        "Windows CI 环境 CanvasKit 中文字体（NotoSansSC）从 fonts.gstatic.com 网络加载失败"
        "（net::ERR_FAILED），textbox a11y 节点未渲染到 DOM，fill_textbox 在"
        " wait_for(state='attached') 阶段超时 (P3-WinE2E-Skip); "
        "embedded 模式下 wizard database 步骤渲染 EmbeddedStatusCard（无外部 DB 表单），"
        "外部 DB 配置验证流程由集成测试覆盖"
    ),
)
async def test_wizard_db_validation_success(wizard_page):
    """测试：数据库校验成功后前进到 Token 步骤（A 类门禁，用 CI 测试库）。

    PR-3 注记：btn_start / btn_verify_next / db_host 等 TextField 未 anchor 化
    （PR-3 范围仅 anchor 化 next/prev/skip/token），保留 FletPage.click_button / fill_textbox。
    """
    from tests.conftest import _get_test_db_url

    db_url = os.environ.get(
        "E2E_DATABASE_URL",
        _get_test_db_url(),
    )
    db = _parse_db_url(db_url)

    db_title = I18n.get("wizard_db_title")
    # [PITFALL FIX] 前一个用例 test_wizard_db_validation_failure 可能将向导留在 DB 步骤，
    # 导致"开始使用"按钮不存在。先检查是否已在 DB 步骤，若在则跳过导航点击。
    try:
        await wizard_page.expect_text(db_title, timeout_ms=2000)
    except Exception:  # noqa: BLE001
        btn_start = I18n.get("wizard_btn_start")
        await wizard_page.click_button(btn_start)
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
