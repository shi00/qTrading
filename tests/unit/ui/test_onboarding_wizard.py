import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page
from ui.views.onboarding_wizard import STEP_CONFIGS


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
        self.patches = [
            patch("ui.views.onboarding_wizard.I18n", self.mock_i18n),
            patch("ui.views.onboarding_wizard.AppColors", self.mock_ac),
            patch("ui.views.onboarding_wizard.AppStyles", self.mock_as),
            patch("ui.views.onboarding_wizard.ConfigHandler"),
            patch("ui.views.onboarding_wizard.DataProcessor"),
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
        assert wizard.current_step == 0

    def test_initial_step_validated_empty(self, mock_page):
        wizard = self._make_wizard(mock_page)
        assert wizard.step_validated == {}

    def test_on_input_change_resets_validation(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.step_validated["database"] = True
        wizard._on_input_change("database")
        assert wizard.step_validated["database"] is False

    @pytest.mark.asyncio
    async def test_next_step_advances(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._update_wizard = MagicMock()
        await wizard._next_step()
        assert wizard.current_step == 1

    @pytest.mark.asyncio
    async def test_next_step_validates_required(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 1
        wizard._validate_and_persist_current_step = MagicMock(return_value=_async_true())
        wizard._update_wizard = MagicMock()
        await wizard._next_step()
        wizard._validate_and_persist_current_step.assert_called_once()

    @pytest.mark.asyncio
    async def test_next_step_blocks_on_validation_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 1
        wizard._validate_and_persist_current_step = MagicMock(return_value=_async_false())
        await wizard._next_step()
        assert wizard.current_step == 1

    @pytest.mark.asyncio
    async def test_next_step_on_complete_calls_callback(self, mock_page):
        callback = MagicMock(return_value=_async_none())
        wizard = self._make_wizard(mock_page, on_complete=callback)
        set_page(wizard, mock_page)
        wizard.current_step = 7
        await wizard._next_step()
        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_prev_step_goes_back(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 3
        wizard._update_wizard = MagicMock()
        await wizard._prev_step()
        assert wizard.current_step == 2

    @pytest.mark.asyncio
    async def test_prev_step_does_not_go_below_zero(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 0
        await wizard._prev_step()
        assert wizard.current_step == 0

    @pytest.mark.asyncio
    async def test_prev_step_resets_validation(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 1
        wizard.step_validated["database"] = True
        wizard._update_wizard = MagicMock()
        await wizard._prev_step()
        assert wizard.step_validated["database"] is False

    @pytest.mark.asyncio
    async def test_skip_step_advances(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 4
        wizard._update_wizard = MagicMock()
        await wizard._skip_step()
        assert wizard.current_step == 5

    @pytest.mark.asyncio
    async def test_skip_step_does_not_exceed_max(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 7
        await wizard._skip_step()
        assert wizard.current_step == 7

    def test_update_wizard_updates_step_container(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 1
        wizard._update_wizard()
        assert wizard.step_container.content == wizard.steps_content[1]

    def test_update_wizard_shows_indicators_for_config_steps(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 3
        wizard._update_wizard()
        assert wizard.step_indicators.visible is True

    def test_update_wizard_hides_indicators_for_welcome(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 0
        wizard._update_wizard()
        assert wizard.step_indicators.visible is False

    def test_update_wizard_hides_indicators_for_complete(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 7
        wizard._update_wizard()
        assert wizard.step_indicators.visible is False

    def test_update_wizard_shows_header_for_welcome(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 0
        wizard._update_wizard()
        assert wizard.header_container.visible is True

    def test_update_wizard_shows_header_for_complete(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 7
        wizard._update_wizard()
        assert wizard.header_container.visible is True

    def test_update_wizard_hides_header_for_config_steps(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 3
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

    def test_show_loading_overlay(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._show_loading_overlay(True)
        assert wizard.loading_overlay.visible is True
        assert wizard._validation_in_progress is True

    def test_hide_loading_overlay(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._show_loading_overlay(False)
        assert wizard.loading_overlay.visible is False
        assert wizard._validation_in_progress is False

    @pytest.mark.asyncio
    async def test_validate_and_persist_skips_if_already_validated(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.current_step = 1
        wizard.step_validated["database"] = True
        result = await wizard._validate_and_persist_current_step()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_and_persist_returns_true_for_no_validator(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.current_step = 0
        result = await wizard._validate_and_persist_current_step()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_and_save_schedule_valid_time(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.schedule_enabled.value = True
        wizard.schedule_time.value = "16:30"
        result = await wizard._validate_and_save_schedule()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_and_save_schedule_invalid_time_defaults(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.schedule_enabled.value = True
        wizard.schedule_time.value = "25:99"
        result = await wizard._validate_and_save_schedule()
        assert result is True
        assert wizard.schedule_time.value == "16:30"

    @pytest.mark.asyncio
    async def test_validate_and_save_schedule_empty_time_defaults(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.schedule_enabled.value = True
        wizard.schedule_time.value = ""
        result = await wizard._validate_and_save_schedule()
        assert result is True
        assert wizard.schedule_time.value == "16:30"

    @pytest.mark.asyncio
    async def test_validate_and_save_local_model_empty_path(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.local_model_panel.model_path_input = MagicMock()
        wizard.local_model_panel.model_path_input.value.strip.return_value = ""
        result = await wizard._validate_and_save_local_model()
        assert result is True

    def test_data_processor_lazy_init(self, mock_page):
        wizard = self._make_wizard(mock_page)
        assert wizard._data_processor is None
        dp = wizard.data_processor
        assert dp is not None
        assert wizard._data_processor is not None

    @pytest.mark.asyncio
    async def test_validate_and_persist_database_step(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 1
        wizard.database_panel.test_connection = AsyncMock(return_value=True)
        wizard.database_panel.get_config = MagicMock(
            return_value={
                "host": "localhost",
                "port": "5432",
                "user": "test",
                "password": "test",
                "database": "testdb",
            }
        )
        with patch("data.persistence.db_config_service.DatabaseConfigService") as mock_dbcs:
            mock_dbcs.ensure_tables_exist = AsyncMock(return_value=(True, "OK"))
            result = await wizard._validate_and_persist_current_step()
        assert result is True
        assert wizard.step_validated["database"] is True

    @pytest.mark.asyncio
    async def test_validate_and_persist_database_step_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 1
        wizard.database_panel.test_connection = AsyncMock(return_value=False)
        result = await wizard._validate_and_persist_current_step()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_and_persist_token_step(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.current_step = 2
        wizard.tushare_panel.verify_token = AsyncMock(return_value=True)
        result = await wizard._validate_and_persist_current_step()
        assert result is True
        assert wizard.step_validated["token"] is True

    @pytest.mark.asyncio
    async def test_validate_and_persist_cloud_ai_step(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.current_step = 3
        wizard.llm_config_panel.async_verify_connection = AsyncMock(return_value=True)
        result = await wizard._validate_and_persist_current_step()
        assert result is True
        assert wizard.step_validated["cloud_ai"] is True

    @pytest.mark.asyncio
    async def test_validate_and_save_schedule_disabled(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.schedule_enabled.value = False
        wizard.schedule_time.value = "16:30"
        result = await wizard._validate_and_save_schedule()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_and_save_local_model_valid_path(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.local_model_panel.model_path_input = MagicMock()
        wizard.local_model_panel.model_path_input.value.strip.return_value = "/path/to/model.gguf"
        wizard.local_model_panel.async_verify_model = AsyncMock(return_value=True)
        result = await wizard._validate_and_save_local_model()
        assert result is True
        wizard.local_model_panel.save_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_save_local_model_verify_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.local_model_panel.model_path_input = MagicMock()
        wizard.local_model_panel.model_path_input.value.strip.return_value = "/path/to/model.gguf"
        wizard.local_model_panel.async_verify_model = AsyncMock(return_value=False)
        result = await wizard._validate_and_save_local_model()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_and_save_cloud_ai_valid(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.llm_config_panel.async_verify_connection = AsyncMock(return_value=True)
        result = await wizard._validate_and_save_cloud_ai()
        assert result is True
        wizard.llm_config_panel.save_current_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_save_cloud_ai_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.llm_config_panel.async_verify_connection = AsyncMock(return_value=False)
        result = await wizard._validate_and_save_cloud_ai()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_and_save_token_valid(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.tushare_panel.verify_token = AsyncMock(return_value=True)
        result = await wizard._validate_and_save_token()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_and_save_token_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.tushare_panel.verify_token = AsyncMock(return_value=False)
        result = await wizard._validate_and_save_token()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_and_save_database_success(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.database_panel.test_connection = AsyncMock(return_value=True)
        wizard.database_panel.get_config = MagicMock(
            return_value={
                "host": "localhost",
                "port": "5432",
                "user": "test",
                "password": "test",
                "database": "testdb",
            }
        )
        with patch("data.persistence.db_config_service.DatabaseConfigService") as mock_dbcs:
            mock_dbcs.ensure_tables_exist = AsyncMock(return_value=(True, "OK"))
            result = await wizard._validate_and_save_database()
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_and_save_database_connection_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.database_panel.test_connection = AsyncMock(return_value=False)
        result = await wizard._validate_and_save_database()
        assert result is False

    @pytest.mark.asyncio
    async def test_validate_and_save_database_tables_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.database_panel.test_connection = AsyncMock(return_value=True)
        wizard.database_panel.get_config = MagicMock(
            return_value={
                "host": "localhost",
                "port": "5432",
                "user": "test",
                "password": "test",
                "database": "testdb",
            }
        )
        wizard.database_panel.status_text = MagicMock()
        wizard.database_panel._safe_update = MagicMock()
        with patch("data.persistence.db_config_service.DatabaseConfigService") as mock_dbcs:
            mock_dbcs.ensure_tables_exist = AsyncMock(return_value=(False, "Connection refused"))
            result = await wizard._validate_and_save_database()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_sync_quick_success(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 5
        mock_dp = MagicMock()
        mock_dp.initialize_system = AsyncMock(return_value=True)
        wizard._data_processor = mock_dp
        with patch("ui.views.onboarding_wizard.asyncio.sleep", new_callable=AsyncMock):
            await wizard._start_sync(quick=True)
        mock_dp.initialize_system.assert_called_once()
        assert wizard.sync_in_progress is False
        assert mock_dp.initialize_system.call_args[1]["quick"] is True

    @pytest.mark.asyncio
    async def test_start_sync_full_success(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 5
        mock_dp = MagicMock()
        mock_dp.initialize_system = AsyncMock(return_value=True)
        wizard._data_processor = mock_dp
        with patch("ui.views.onboarding_wizard.asyncio.sleep", new_callable=AsyncMock):
            await wizard._start_sync(quick=False)
        mock_dp.initialize_system.assert_called_once()
        assert mock_dp.initialize_system.call_args[1]["quick"] is False

    @pytest.mark.asyncio
    async def test_start_sync_cancelled(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 5
        mock_dp = MagicMock()
        mock_dp.initialize_system = AsyncMock(return_value=False)
        wizard._data_processor = mock_dp
        await wizard._start_sync(quick=True)
        assert wizard.sync_in_progress is False
        assert wizard.btn_quick_sync.disabled is False
        assert wizard.btn_full_sync.disabled is False
        assert wizard.btn_sync_later.disabled is False

    @pytest.mark.asyncio
    async def test_start_sync_exception(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 5
        mock_dp = MagicMock()
        mock_dp.initialize_system = AsyncMock(side_effect=Exception("sync error"))
        wizard._data_processor = mock_dp
        with (
            patch("utils.error_classifier.classify_error", return_value={"type": "general"}),
            patch("utils.error_classifier.get_error_message", return_value="Error occurred"),
        ):
            await wizard._start_sync(quick=True)
        assert wizard.sync_in_progress is False
        assert wizard.btn_quick_sync.disabled is False
        assert wizard.btn_full_sync.disabled is False
        assert wizard.btn_sync_later.disabled is False
        assert wizard.btn_cancel_sync.visible is False

    @pytest.mark.asyncio
    async def test_start_sync_progress_callback(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 5
        mock_dp = MagicMock()
        mock_dp.initialize_system = AsyncMock(return_value=True)
        wizard._data_processor = mock_dp
        with patch("ui.views.onboarding_wizard.asyncio.sleep", new_callable=AsyncMock):
            await wizard._start_sync(quick=True)
        call_kwargs = mock_dp.initialize_system.call_args[1]
        assert "progress_callback" in call_kwargs
        cb = call_kwargs["progress_callback"]
        cb(75, 100, "Three quarters")
        assert wizard.sync_progress.value == 0.75
        assert wizard.sync_status.value == "Three quarters"

    @pytest.mark.asyncio
    async def test_cancel_sync_with_processor(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        mock_dp = MagicMock()
        mock_dp.stop = AsyncMock()
        wizard._data_processor = mock_dp
        wizard.sync_in_progress = True
        await wizard._cancel_sync()
        mock_dp.stop.assert_called_once()
        assert wizard.sync_in_progress is False
        assert wizard.btn_quick_sync.disabled is False
        assert wizard.btn_full_sync.disabled is False
        assert wizard.btn_sync_later.disabled is False
        assert wizard.btn_cancel_sync.visible is False

    @pytest.mark.asyncio
    async def test_cancel_sync_without_processor(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._data_processor = None
        wizard.sync_in_progress = True
        await wizard._cancel_sync()
        assert wizard.sync_in_progress is False

    @pytest.mark.asyncio
    async def test_cancel_sync_exception(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        mock_dp = MagicMock()
        mock_dp.stop = AsyncMock(side_effect=Exception("cancel error"))
        wizard._data_processor = mock_dp
        wizard.sync_in_progress = True
        await wizard._cancel_sync()
        assert wizard.sync_in_progress is False

    @pytest.mark.asyncio
    async def test_validate_and_persist_local_model_step(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard.current_step = 4
        wizard.local_model_panel.model_path_input = MagicMock()
        wizard.local_model_panel.model_path_input.value.strip.return_value = ""
        result = await wizard._validate_and_persist_current_step()
        assert result is True
        assert wizard.step_validated["local_model"] is True

    @pytest.mark.asyncio
    async def test_validate_and_persist_local_model_step_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 4
        wizard.local_model_panel.model_path_input = MagicMock()
        wizard.local_model_panel.model_path_input.value.strip.return_value = "/path/to/model.gguf"
        wizard.local_model_panel.async_verify_model = AsyncMock(return_value=False)
        result = await wizard._validate_and_persist_current_step()
        assert result is False
        assert "local_model" not in wizard.step_validated

    @pytest.mark.asyncio
    async def test_validate_and_persist_schedule_step(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.current_step = 6
        wizard.schedule_enabled.value = True
        wizard.schedule_time.value = "16:30"
        result = await wizard._validate_and_persist_current_step()
        assert result is True
        assert wizard.step_validated["schedule"] is True

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


def _async_true():
    import asyncio

    fut = asyncio.Future()
    fut.set_result(True)
    return fut


def _async_false():
    import asyncio

    fut = asyncio.Future()
    fut.set_result(False)
    return fut


def _async_none():
    import asyncio

    fut = asyncio.Future()
    fut.set_result(None)
    return fut
