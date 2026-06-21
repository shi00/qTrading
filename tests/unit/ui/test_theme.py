from unittest.mock import MagicMock, patch

import pytest

from ui.theme import AppColors, AppStyles, ThemeName, CUSTOM_COLOR_PRESETS

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _save_restore_appcolors():
    saved_listeners = AppColors._listeners[:]
    saved_attrs = {key: getattr(AppColors, key) for key in CUSTOM_COLOR_PRESETS[ThemeName.DARK]}
    saved_attrs["RISE"] = AppColors.RISE
    saved_attrs["FALL"] = AppColors.FALL
    saved_attrs["TABLE_GRID_V"] = AppColors.TABLE_GRID_V
    saved_attrs["TABLE_GRID_H"] = AppColors.TABLE_GRID_H
    saved_attrs["_CURRENT_THEME_NAME"] = AppColors._CURRENT_THEME_NAME
    saved_attrs["_CURRENT_THEME_MODE"] = AppColors._CURRENT_THEME_MODE
    yield
    AppColors._listeners = saved_listeners
    for key, value in saved_attrs.items():
        setattr(AppColors, key, value)


class TestAppColorsSubscribe:
    def test_adds_listener(self):
        listener = MagicMock()
        AppColors.subscribe(listener)
        assert listener in AppColors._listeners

    def test_prevents_duplicate(self):
        listener = MagicMock()
        AppColors.subscribe(listener)
        AppColors.subscribe(listener)
        assert AppColors._listeners.count(listener) == 1


class TestAppColorsUnsubscribe:
    def test_removes_listener(self):
        listener = MagicMock()
        AppColors.subscribe(listener)
        AppColors.unsubscribe(listener)
        assert listener not in AppColors._listeners

    def test_no_error_if_not_found(self):
        listener = MagicMock()
        AppColors.unsubscribe(listener)


class TestAppColorsLoadTheme:
    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_sets_theme_name(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors._CURRENT_THEME_NAME == ThemeName.LIGHT

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_sets_theme_mode(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors._CURRENT_THEME_MODE == "LIGHT"

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_applies_dark_preset_colors(self):
        AppColors.load_theme(ThemeName.DARK)
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["UP"] == AppColors.UP
        assert CUSTOM_COLOR_PRESETS[ThemeName.DARK]["DOWN"] == AppColors.DOWN

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_applies_light_preset_colors(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["UP"] == AppColors.UP
        assert CUSTOM_COLOR_PRESETS[ThemeName.LIGHT]["DOWN"] == AppColors.DOWN

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_rise_alias(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors.RISE == AppColors.UP

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_fall_alias(self):
        AppColors.load_theme(ThemeName.LIGHT)
        assert AppColors.FALL == AppColors.DOWN

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_table_grid_v_alias(self):
        AppColors.load_theme(ThemeName.NAVY)
        assert AppColors.TABLE_GRID_V == AppColors.TABLE_GRID

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_syncs_table_grid_h_alias(self):
        AppColors.load_theme(ThemeName.NAVY)
        assert AppColors.TABLE_GRID_H == AppColors.TABLE_GRID

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_notifies_listeners(self):
        listener = MagicMock()
        AppColors.subscribe(listener)
        AppColors.load_theme(ThemeName.LIGHT)
        listener.assert_called_once()

    @patch(
        "ui.theme.THEME_MODE_MAP",
        {
            ThemeName.DARK: "DARK",
            ThemeName.LIGHT: "LIGHT",
            ThemeName.NAVY: "DARK",
            ThemeName.DRACULA: "DARK",
        },
    )
    def test_listener_exception_does_not_stop_others(self):
        bad_listener = MagicMock(side_effect=RuntimeError("boom"))
        good_listener = MagicMock()
        AppColors.subscribe(bad_listener)
        AppColors.subscribe(good_listener)
        AppColors.load_theme(ThemeName.LIGHT)
        good_listener.assert_called_once()


class TestAppStylesCard:
    def test_default_has_border_no_shadow(self):
        style = AppStyles.card()
        assert "border" in style
        assert "shadow" not in style

    def test_with_border_false(self):
        style = AppStyles.card(with_border=False)
        assert "border" not in style

    def test_with_shadow_true(self):
        style = AppStyles.card(with_shadow=True)
        assert "shadow" in style

    def test_with_border_and_shadow(self):
        style = AppStyles.card(with_border=True, with_shadow=True)
        assert "border" in style
        assert "shadow" in style

    def test_always_has_bgcolor_and_padding(self):
        style = AppStyles.card(with_border=False, with_shadow=False)
        assert "bgcolor" in style
        assert "padding" in style


class TestAppStylesDataTableRow:
    def test_even_index_returns_odd_color(self):
        result = AppStyles.data_table_row(0)
        assert result == AppColors.TABLE_ROW_ODD

    def test_odd_index_returns_even_color(self):
        result = AppStyles.data_table_row(1)
        assert result == AppColors.TABLE_ROW_EVEN

    def test_hovered_returns_odd_color(self):
        result = AppStyles.data_table_row(1, is_hovered=True)
        assert result == AppColors.TABLE_ROW_ODD


class TestAppStylesPriceChangeColor:
    def test_positive_returns_up(self):
        result = AppStyles.price_change_color(1.5)
        assert result == AppColors.UP

    def test_negative_returns_down(self):
        result = AppStyles.price_change_color(-2.0)
        assert result == AppColors.DOWN

    def test_zero_returns_on_surface_variant(self):
        result = AppStyles.price_change_color(0.0)
        assert result is not None
