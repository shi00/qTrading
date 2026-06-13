import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page
from ui.viewmodels.onboarding_view_model import STEP_CONFIGS


class TestStepConfig:
    def test_step_configs_count(self):
        assert len(STEP_CONFIGS) == 8

    def test_welcome_step_no_prev(self):
        assert STEP_CONFIGS[0].show_prev is False

    def test_welcome_step_has_next(self):
        assert STEP_CONFIGS[0].show_next is True

    def test_welcome_step_not_required(self):
        assert STEP_CONFIGS[0].required is False

    def test_database_step_required(self):
        assert STEP_CONFIGS[1].required is True

    def test_database_step_validates_before_next(self):
        assert STEP_CONFIGS[1].validate_before_next is True

    def test_token_step_required(self):
        assert STEP_CONFIGS[2].required is True

    def test_cloud_ai_step_required(self):
        assert STEP_CONFIGS[3].required is True

    def test_local_model_step_not_required(self):
        assert STEP_CONFIGS[4].required is False

    def test_local_model_step_has_skip(self):
        assert STEP_CONFIGS[4].show_skip is True

    def test_complete_step_id(self):
        assert STEP_CONFIGS[7].id == "complete"


class TestOnboardingWizard:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = MagicMock()
        self.patches = [
            patch("ui.views.onboarding_wizard.I18n", self.mock_i18n),
            patch("ui.views.onboarding_wizard.AppColors", self.mock_ac),
            patch("ui.views.onboarding_wizard.AppStyles", self.mock_as),
            patch("ui.views.onboarding_wizard.ConfigHandler", self.mock_ch),
            patch("ui.viewmodels.onboarding_view_model.DataProcessor"),
            patch("ui.views.onboarding_wizard.DatabaseConfigPanel", MagicMock()),
            patch("ui.views.onboarding_wizard.TushareConfigPanel", MagicMock()),
            patch("ui.views.onboarding_wizard.LLMConfigPanel", MagicMock()),
            patch("ui.views.onboarding_wizard.LocalModelConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_wizard(self, mock_page, on_complete=None):
        from ui.views.onboarding_wizard import OnboardingWizard

        return OnboardingWizard(mock_page, on_complete=on_complete)

    def test_initial_step_is_zero(self, mock_page):
        wizard = self._make_wizard(mock_page)
        assert wizard.vm.current_step == 0

    @pytest.mark.asyncio
    async def test_next_step_advances(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._update_wizard = MagicMock()
        await wizard._next_step()
        assert wizard.vm.current_step == 1

    @pytest.mark.asyncio
    async def test_next_step_blocks_on_validation_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 1
        wizard.vm.validate_and_persist_current_step = AsyncMock(return_value=False)
        await wizard._next_step()
        assert wizard.vm.current_step == 1

    @pytest.mark.asyncio
    async def test_prev_step_does_not_go_below_zero(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 0
        await wizard._prev_step()
        assert wizard.vm.current_step == 0

    def test_update_wizard_updates_step_container(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 1
        wizard._update_wizard()
        assert wizard.step_container.content == wizard.steps_content[1]

    def test_update_wizard_shows_indicators_for_config_steps(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 3
        wizard._update_wizard()
        assert wizard.step_indicators.visible is True

    def test_update_wizard_hides_indicators_for_welcome(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 0
        wizard._update_wizard()
        assert wizard.step_indicators.visible is False

    def test_update_wizard_hides_indicators_for_complete(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 7
        wizard._update_wizard()
        assert wizard.step_indicators.visible is False

    def test_update_wizard_shows_header_for_welcome(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 0
        wizard._update_wizard()
        assert wizard.header_container.visible is True

    def test_update_wizard_shows_header_for_complete(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 7
        wizard._update_wizard()
        assert wizard.header_container.visible is True

    def test_update_wizard_hides_header_for_config_steps(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 3
        wizard._update_wizard()
        assert wizard.header_container.visible is False

    def test_on_mount_subscribes_i18n(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard._on_mount()
        self.mock_i18n.subscribe.assert_called_once()
        assert wizard._locale_subscription_id == "sub_id"

    def test_on_unmount_unsubscribes_i18n(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard._locale_subscription_id = "sub_id"
        wizard._on_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")
        assert wizard._locale_subscription_id is None

    def test_on_locale_change_updates_header_title(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_title = wizard.header_title
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"en_{key}" if key == "wizard_welcome_title" else key
        wizard._on_locale_change("en_US")
        assert original_title.value == "en_wizard_welcome_title"
        assert wizard.header_title is original_title

    def test_on_locale_change_updates_header_desc(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_desc = wizard.header_desc
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: (
            f"en_{key}" if key == "wizard_welcome_desc_with_time" else key
        )
        wizard._on_locale_change("en_US")
        assert original_desc.value == "en_wizard_welcome_desc_with_time"
        assert wizard.header_desc is original_desc

    def test_on_locale_change_updates_gradient_guide_text(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_text = wizard.gradient_guide_text
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"en_{key}" if key == "wizard_welcome_guide" else key
        wizard._on_locale_change("en_US")
        assert original_text.value == "en_wizard_welcome_guide"
        assert wizard.gradient_guide_text is original_text

    def test_header_title_is_in_ui_tree(self, mock_page):
        wizard = self._make_wizard(mock_page)
        header_column = wizard.header_container
        assert wizard.header_title in header_column.controls
        assert wizard.header_desc in header_column.controls

    def test_on_language_change_wizard_preserves_header_reference(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_header_container = wizard.header_container
        original_header_title = wizard.header_title
        original_header_desc = wizard.header_desc
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        wizard._on_language_change_wizard(MagicMock())
        assert wizard.header_container is original_header_container
        assert wizard.header_title is original_header_title
        assert wizard.header_desc is original_header_desc

    def test_on_language_change_wizard_updates_header_title_directly(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_title = wizard.header_title
        original_desc = wizard.header_desc
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"en_{key}" if "welcome" in key else key
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        wizard._on_language_change_wizard(MagicMock())
        assert original_title.value == "en_wizard_welcome_title"
        assert original_desc.value == "en_wizard_welcome_desc_with_time"
        assert wizard.header_title is original_title
        assert wizard.header_desc is original_desc
        self.mock_ch.set_locale.assert_called_with("en_US")

    def test_on_panel_loading_change_shows_overlay_when_loading(self, mock_page):
        """面板加载中 → 遮罩显示"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._on_panel_loading_change(True)
        assert wizard.loading_overlay.visible is True

    def test_on_panel_loading_change_hides_overlay_when_not_loading_and_not_validating(self, mock_page):
        """面板加载完成 + VM 校验完成 → 遮罩隐藏"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.validation_in_progress = False
        wizard._on_panel_loading_change(False)
        assert wizard.loading_overlay.visible is False

    def test_on_panel_loading_change_keeps_overlay_when_validating(self, mock_page):
        """面板加载完成但 VM 校验中 → 遮罩保持显示"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._show_loading_overlay(True)
        wizard.vm.validation_in_progress = True
        wizard._on_panel_loading_change(False)
        assert wizard.loading_overlay.visible is True

    @pytest.mark.asyncio
    async def test_cleanup_vm_cancels_sync_and_disposes(self, mock_page):
        """卸载时取消进行中的同步并清理 VM"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.sync_in_progress = True
        wizard.vm.cancel_sync = AsyncMock()
        wizard.vm.dispose = MagicMock()
        await wizard._cleanup_vm()
        wizard.vm.cancel_sync.assert_awaited_once()
        wizard.vm.dispose.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_vm_disposes_without_sync(self, mock_page):
        """卸载时无进行中同步，直接清理 VM"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.sync_in_progress = False
        wizard.vm.cancel_sync = AsyncMock()
        wizard.vm.dispose = MagicMock()
        await wizard._cleanup_vm()
        wizard.vm.cancel_sync.assert_not_awaited()
        wizard.vm.dispose.assert_called_once()

    def test_language_change_rebinds_panel_callbacks(self, mock_page):
        """语言切换后 VM 回调指向新面板"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        wizard._on_language_change_wizard(MagicMock())
        # 验证回调已更新为新面板的方法
        assert wizard.vm.fn_validate_database is not None
        assert wizard.vm.fn_validate_database is wizard.database_panel.save_config
        assert wizard.vm.fn_validate_token is wizard.tushare_panel.verify_token

    def test_on_vm_step_changed_triggers_update_wizard(self, mock_page):
        """VM 步骤变更回调触发 View 更新"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 3
        wizard._update_wizard = MagicMock()
        wizard._on_vm_step_changed()
        wizard._update_wizard.assert_called_once()
