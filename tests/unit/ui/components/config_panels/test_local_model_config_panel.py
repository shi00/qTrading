"""LocalModelConfigPanel._on_locale_change 单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel

pytestmark = pytest.mark.unit


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_config_handler():
    with patch("ui.components.config_panels.local_model_config_panel.ConfigHandler") as m:
        m.get_local_ai_config.return_value = {
            "local_model_path": "",
            "n_threads": 4,
            "n_gpu_layers": -1,
            "n_batch": 512,
            "n_ctx": 4096,
            "flash_attn": True,
        }
        m.get_local_ai_timeout.return_value = 300
        yield m


@pytest.fixture
def mock_i18n():
    with patch("ui.components.config_panels.local_model_config_panel.I18n") as m:
        m.get.side_effect = lambda key, default="", **kw: default or key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_app_colors():
    with patch("ui.components.config_panels.local_model_config_panel.AppColors") as m:
        m.SUCCESS = "#4caf50"
        m.WARNING = "#ff9800"
        m.ERROR = "#f44336"
        m.PRIMARY = "#1976d2"
        m.TEXT_SECONDARY = "#999"
        yield m


@pytest.fixture
def mock_app_styles():
    with patch("ui.components.config_panels.local_model_config_panel.AppStyles") as m:
        m.primary_button.return_value = MagicMock(spec=ft.ButtonStyle)
        m.secondary_button.return_value = MagicMock(spec=ft.ButtonStyle)
        yield m


@pytest.fixture
def mock_section_header():
    with patch("ui.components.config_panels.local_model_config_panel.SectionHeader") as m:
        # 使用 plain MagicMock 以支持 update_locale() 自定义方法
        m.return_value = MagicMock()
        yield m


@pytest.fixture
def mock_page():
    page = MagicMock(spec=ft.Page)
    page.overlay = []
    page.update = MagicMock(spec=[])
    return page


def _make_panel(
    mock_config_handler,
    mock_i18n,
    mock_app_colors,
    mock_app_styles,
    mock_section_header,
    mock_page,
    **kwargs,
):
    """创建 LocalModelConfigPanel 实例并绑定 mock page"""
    kwargs.setdefault("on_verify_model", AsyncMock(return_value=True))
    panel = LocalModelConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


# ════════════════════════════════════════════════════════════════════════════
# TestLocalModelConfigPanelLocaleChange
# ════════════════════════════════════════════════════════════════════════════


class TestLocalModelConfigPanelLocaleChange:
    """测试 _on_locale_change 方法（直接更新控件 i18n 文本，不重建控件）"""

    def test_on_locale_change_does_not_call_build_ui(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_locale_change 不应调用 _build_ui（避免触发 ConfigHandler.get_local_ai_config 等 IO）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        initial_cfg_count = mock_config_handler.get_local_ai_config.call_count
        initial_timeout_count = mock_config_handler.get_local_ai_timeout.call_count
        with (
            patch.object(panel, "_safe_update"),
            patch.object(panel, "_build_ui") as mock_build_ui,
        ):
            panel._on_locale_change()
        mock_build_ui.assert_not_called()
        # 不应再调用 ConfigHandler（即不触发 IO）
        assert mock_config_handler.get_local_ai_config.call_count == initial_cfg_count
        assert mock_config_handler.get_local_ai_timeout.call_count == initial_timeout_count

    def test_on_locale_change_updates_text_labels(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 7 个控件（model_path_input、btn_select_file、timeout_input、gpu_auto_switch、batch_input、ctx_input、flash_attn_switch）的 label/text 被更新"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel.model_path_input.label == "settings_local_model_path"
        assert panel.btn_select_file.content == "settings_btn_select_file"
        assert panel.timeout_input.label == "settings_local_ai_timeout"
        assert panel.gpu_auto_switch.label == "settings_local_gpu_auto"
        assert panel.batch_input.label == "settings_local_batch"
        assert panel.ctx_input.label == "settings_local_ctx"
        assert panel.flash_attn_switch.label == "settings_local_flash_attn"

    def test_on_locale_change_updates_buttons(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 verify_button、save_button 的 text 被更新"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel.verify_button.content == "wizard_btn_verify_model"
        assert panel.save_button.content == "settings_save_config"

    def test_on_locale_change_preserves_input_values(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证控件当前 value 不变（关键：不重建控件）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # 设置自定义 value
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "120"
        panel.batch_input.value = "1024"
        panel.ctx_input.value = "8192"
        panel.gpu_auto_switch.value = False
        panel.flash_attn_switch.value = False
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        # value 不应变
        assert panel.model_path_input.value == "/models/test.gguf"
        assert panel.timeout_input.value == "120"
        assert panel.batch_input.value == "1024"
        assert panel.ctx_input.value == "8192"
        assert panel.gpu_auto_switch.value is False
        assert panel.flash_attn_switch.value is False

    def test_on_locale_change_updates_header_text(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 _header_text.update_locale() 被调用"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        panel._header_text.update_locale.assert_called_once()

    def test_on_locale_change_updates_desc_and_advanced(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 _desc_text.value、_advanced_title.value、_advanced_subtitle.value 被更新"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel._desc_text.value == "settings_local_ai_desc"
        assert panel._advanced_title.value == "ai_advanced_settings"
        assert panel._advanced_subtitle.value == "settings_hint_restart"

    def test_on_locale_change_calls_safe_update(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证最后调用 _safe_update"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel, "_safe_update") as mock_safe_update:
            panel._on_locale_change()
        mock_safe_update.assert_called_once()

    def test_on_locale_change_preserves_file_picker_instance(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """验证 file_picker 实例不变（关键：不重建控件）"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        original_file_picker = panel.file_picker
        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()
        assert panel.file_picker is original_file_picker


# ════════════════════════════════════════════════════════════════════════════
# TestLocalModelConfigPanelCoverage
# ════════════════════════════════════════════════════════════════════════════


class TestLocalModelConfigPanelCoverage:
    """补全 LocalModelConfigPanel 行/分支覆盖至 ≥80%。

    覆盖目标：_on_input_change Slider tooltip、_on_select_file_click 边界、
    _on_verify_click/_on_save_click 调度、_do_save_click_async 异常、
    async_verify_model 状态分支、save_config 类型转换异常、
    _set_loading_state 内部禁用、did_mount page=None、_on_locale_change 鲁棒性。
    """

    # ── _on_input_change: Slider tooltip 更新（L310-317）──────────────────────

    def test_on_input_change_slider_integer_tooltip(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """Slider 整数值：tooltip 设为 str(int(val))"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        slider = ft.Slider(min=1, max=16, value=4)
        slider.update = MagicMock()
        e = MagicMock()
        e.control = slider

        panel._on_input_change(e)

        assert slider.tooltip == "4"
        slider.update.assert_called_once()

    def test_on_input_change_slider_float_tooltip(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """Slider 浮点值：tooltip 设为 str(round(val, 2))"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        slider = ft.Slider(min=0, max=100, value=4.5)
        slider.update = MagicMock()
        e = MagicMock()
        e.control = slider

        panel._on_input_change(e)

        assert slider.tooltip == "4.5"
        slider.update.assert_called_once()

    def test_on_input_change_slider_none_value_no_crash(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """Slider value=None：跳过 tooltip 更新，不抛异常"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        slider = ft.Slider(min=1, max=16, value=4)
        slider.value = None
        slider.update = MagicMock()
        e = MagicMock()
        e.control = slider

        panel._on_input_change(e)  # 不应抛异常

        slider.update.assert_not_called()

    def test_on_input_change_slider_attribute_error_safe(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """Slider update() 抛 AttributeError 时被 except 捕获，不传播"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        slider = ft.Slider(min=1, max=16, value=4)
        slider.update = MagicMock(side_effect=AttributeError("update failed"))
        e = MagicMock()
        e.control = slider

        panel._on_input_change(e)  # 不应抛异常

        # tooltip 已在 update 之前被设置
        assert slider.tooltip == "4"

    # ── _on_select_file_click: 边界（L346, L351->exit, L354->exit）─────────────

    @pytest.mark.asyncio
    async def test_on_select_file_click_no_page_returns(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """page=None 时早返回，不调用 file_picker"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None
        with patch.object(panel.file_picker, "pick_files", AsyncMock()) as mock_pick:
            await panel._on_select_file_click(None)

        mock_pick.assert_not_called()
        assert panel.model_path_input.value == ""

    @pytest.mark.asyncio
    async def test_on_select_file_click_empty_result_no_update(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """result=None 或 files 为空时不更新 model_path_input"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.model_path_input.value = "/original/path.gguf"

        # result=None
        with patch.object(panel.file_picker, "pick_files", AsyncMock(return_value=None)):
            await panel._on_select_file_click(None)
        assert panel.model_path_input.value == "/original/path.gguf"

        # result.files 为空列表
        empty_result = MagicMock()
        empty_result.files = []
        with patch.object(panel.file_picker, "pick_files", AsyncMock(return_value=empty_result)):
            await panel._on_select_file_click(None)
        assert panel.model_path_input.value == "/original/path.gguf"

    # ── _on_verify_click: 调度与重入保护（L358-363）─────────────────────────────

    def test_on_verify_click_already_verifying_shows_warning(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_is_verifying=True 时显示警告并返回，不调度 run_task"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._is_verifying = True
        with (
            patch.object(panel, "_show_warning") as mock_warn,
            patch.object(panel.page, "run_task") as mock_run,
        ):
            panel._on_verify_click(MagicMock())

        mock_warn.assert_called_once()
        mock_run.assert_not_called()

    def test_on_verify_click_dispatches_run_task(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_is_verifying=False 且 page 存在时调度 _async_verify_and_notify"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with (
            patch.object(panel, "_show_warning") as mock_warn,
            patch.object(panel.page, "run_task") as mock_run,
        ):
            panel._on_verify_click(MagicMock())

        mock_warn.assert_not_called()
        mock_run.assert_called_once_with(panel._async_verify_and_notify)

    # ── _on_save_click + _do_save_click_async 异常（L373-374, L383-385）─────────

    def test_on_save_click_dispatches_run_task(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_save_click 调度 _do_save_click_async"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        with patch.object(panel.page, "run_task") as mock_run:
            panel._on_save_click(MagicMock())

        mock_run.assert_called_once_with(panel._do_save_click_async)

    @pytest.mark.asyncio
    async def test_do_save_click_async_exception_shows_error(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """run_async 抛异常时显示错误并记录日志"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(side_effect=RuntimeError("tpm io error"))
        with (
            patch(
                "ui.components.config_panels.local_model_config_panel.ThreadPoolManager",
                return_value=mock_tpm,
            ),
            patch.object(panel, "_show_error") as mock_err,
        ):
            await panel._do_save_click_async()

        mock_err.assert_called_once()

    # ── async_verify_model: 状态分支（L421-422, L439-440）──────────────────────

    @pytest.mark.asyncio
    async def test_async_verify_model_already_verifying_returns_false(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_is_verifying=True 时通过校验后返回 False，不重复验证"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel._is_verifying = True
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"

        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()

        assert result is False
        # _is_verifying 未被重置（没进入 try/finally）
        assert panel._is_verifying is True

    @pytest.mark.asyncio
    async def test_async_verify_model_callback_returns_false_shows_error(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """on_verify_model 返回 False 时显示加载失败错误"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.on_verify_model = AsyncMock(return_value=False)
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"

        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.sleep"),
            patch.object(panel, "_safe_update"),
            patch.object(panel, "_show_error") as mock_err,
        ):
            result = await panel.async_verify_model()

        assert result is False
        mock_err.assert_called_once()

    # ── save_config: 类型转换异常（L486-488）────────────────────────────────────

    def test_save_config_invalid_batch_returns_false(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """batch_input 非数字时 int() 抛 ValueError，被捕获返回 False"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"
        panel.batch_input.value = "not_a_number"

        result = panel.save_config()

        assert result is False
        mock_config_handler.save_local_ai_config.assert_not_called()

    def test_save_config_invalid_ctx_returns_false(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """ctx_input 非数字时 int() 抛 ValueError，被捕获返回 False"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"
        panel.ctx_input.value = "invalid_ctx"

        result = panel.save_config()

        assert result is False
        mock_config_handler.save_local_ai_config.assert_not_called()

    # ── _set_loading_state: 内部 loading 禁用（L456->463）──────────────────────

    def test_set_loading_state_internal_disabled_skips_ui(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_show_internal_loading=False 时跳过内部控件更新，仅调用 on_loading_change"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
            show_internal_loading=False,
        )
        on_loading = MagicMock()
        panel.on_loading_change = on_loading

        initial_progress_visible = panel.progress_indicator.visible
        initial_verify_disabled = panel.verify_button.disabled

        panel._set_loading_state(True)

        # 内部控件状态未变
        assert panel.progress_indicator.visible == initial_progress_visible
        assert panel.verify_button.disabled == initial_verify_disabled
        # 但回调被调用
        on_loading.assert_called_once_with(True)

    # ── did_mount: page=None（L562->566）──────────────────────────────────────

    def test_did_mount_no_page_skips_services_append(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """page=None 时跳过 services.append，仍订阅 i18n"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        panel.page = None

        panel.did_mount()

        mock_i18n.subscribe.assert_called_once()

    # ── _on_locale_change: 鲁棒性（L586-613 分支, L614-615 异常）───────────────

    def test_on_locale_change_missing_attrs_no_crash(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """部分控件属性缺失时（hasattr=False 分支），不抛异常"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        # 删除部分控件属性，触发 hasattr=False 分支
        del panel.model_path_input
        del panel._advanced_title
        del panel._header_text

        with patch.object(panel, "_safe_update"):
            panel._on_locale_change()  # 不应抛异常

    def test_on_locale_change_exception_caught(
        self,
        mock_config_handler,
        mock_i18n,
        mock_app_colors,
        mock_app_styles,
        mock_section_header,
        mock_page,
    ):
        """_on_locale_change 内部抛异常时被 except 捕获，记录 warning"""
        panel = _make_panel(
            mock_config_handler,
            mock_i18n,
            mock_app_colors,
            mock_app_styles,
            mock_section_header,
            mock_page,
        )
        mock_i18n.get.side_effect = RuntimeError("i18n error")

        panel._on_locale_change()  # 不应抛异常
