"""Unit tests for AppStyles.calc_dropdown_width."""

import flet as ft
import pytest

from ui.theme import AppStyles

pytestmark = pytest.mark.unit


def test_calc_dropdown_width_defaults() -> None:
    """Test calc_dropdown_width with None/empty inputs returns min_width default (200)."""
    width = AppStyles.calc_dropdown_width(options=None, label=None)
    assert width == AppStyles.CONTROL_WIDTH_MD  # 200.0


def test_calc_dropdown_width_short_text() -> None:
    """Test short options bounded by min_width."""
    opts = [ft.dropdown.Option("1", "A"), ft.dropdown.Option("2", "B")]
    width = AppStyles.calc_dropdown_width(options=opts, label="Short")
    assert width == AppStyles.CONTROL_WIDTH_MD  # 200.0


def test_calc_dropdown_width_long_points_tier_text() -> None:
    """Test Points Tier options like '2000 pts (200/min)' expand beyond 200px."""
    opts = [
        ft.dropdown.Option("120", "120 pts (50/min)"),
        ft.dropdown.Option("2000", "2000 pts (200/min)"),
        ft.dropdown.Option("15000", "15000 pts (500/min)"),
    ]
    label = "Points Tier"
    width = AppStyles.calc_dropdown_width(options=opts, label=label)
    assert width > 240.0
    assert width <= AppStyles.CONTROL_WIDTH_LG  # 400.0


def test_calc_dropdown_width_cjk_text() -> None:
    """Test CJK fullwidth characters visual length calculation."""
    opts = [
        ft.dropdown.Option("2000", "2000 分 (200/min)"),
        ft.dropdown.Option("15000", "15000 分 (500/min)"),
    ]
    label = "积分档位"
    width = AppStyles.calc_dropdown_width(options=opts, label=label)
    assert width > 230.0


def test_calc_dropdown_width_raw_string_options() -> None:
    """Test options passed as string sequence instead of Option instances."""
    opts = ["Option 1 (Short)", "Option 2 (Very long text description for testing)"]
    width = AppStyles.calc_dropdown_width(options=opts, label="Select Option")
    assert width > 300.0


def test_calc_dropdown_width_clamped_at_max_width() -> None:
    """Test extremely long text is clamped at max_width."""
    opts = ["A" * 100]
    width = AppStyles.calc_dropdown_width(options=opts, max_width=350.0)
    assert width == 350.0


def test_calc_dropdown_width_custom_bounds() -> None:
    """Test custom min_width, max_width, and padding."""
    opts = [ft.dropdown.Option("1", "Short")]
    width = AppStyles.calc_dropdown_width(options=opts, min_width=150.0, max_width=300.0)
    assert width == 150.0
