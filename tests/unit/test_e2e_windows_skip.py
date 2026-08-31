"""Unit tests for tests.e2e._windows_skip helper (Windows E2E skipif).

Tests the --run-windows-skip CLI option behavior:
- Default (no flag): skipif markers preserved
- With --run-windows-skip: skipif markers removed from items

This enables the windows-skip-revalidation CI job to temporarily un-skip
the 8 Windows E2E test cases for Flet 0.86.2 revalidation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.e2e._windows_skip import add_windows_skip_option, strip_windows_skipif

pytestmark = pytest.mark.unit


class _MockMarker:
    """Minimal mock of pytest Mark for skipif/slow/e2e markers."""

    def __init__(self, name: str) -> None:
        self.name = name


class _MockItem:
    """Minimal mock of pytest.Item with mutable own_markers list."""

    def __init__(self, marker_names: list[str]) -> None:
        self.own_markers: list[_MockMarker] = [_MockMarker(name) for name in marker_names]


def _mock_config(run_windows_skip: bool) -> MagicMock:
    """Mock pytest.Config with --run-windows-skip option returning given value."""
    config = MagicMock()
    config.getoption.return_value = run_windows_skip
    return config


class TestStripWindowsSkipif:
    """Tests for strip_windows_skipif() function."""

    def test_no_op_when_flag_not_set(self) -> None:
        """Without --run-windows-skip, skipif markers must be preserved."""
        config = _mock_config(run_windows_skip=False)
        items = [_MockItem(["skipif"]), _MockItem([])]

        result = strip_windows_skipif(config, items)

        assert result == 0
        assert len(items[0].own_markers) == 1
        assert items[0].own_markers[0].name == "skipif"

    def test_removes_skipif_when_flag_set(self) -> None:
        """With --run-windows-skip, skipif markers must be removed."""
        config = _mock_config(run_windows_skip=True)
        items = [_MockItem(["skipif"]), _MockItem([])]

        result = strip_windows_skipif(config, items)

        assert result == 1
        assert len(items[0].own_markers) == 0
        assert len(items[1].own_markers) == 0

    def test_only_removes_skipif_not_other_markers(self) -> None:
        """With --run-windows-skip, only skipif markers removed; slow/e2e preserved."""
        config = _mock_config(run_windows_skip=True)
        items = [_MockItem(["skipif", "slow", "e2e"])]

        result = strip_windows_skipif(config, items)

        assert result == 1
        assert len(items[0].own_markers) == 2
        assert {m.name for m in items[0].own_markers} == {"slow", "e2e"}

    def test_no_skipif_items_unchanged_when_flag_set(self) -> None:
        """With --run-windows-skip, items without skipif are not counted as un-skipped."""
        config = _mock_config(run_windows_skip=True)
        items = [_MockItem(["slow"]), _MockItem([])]

        result = strip_windows_skipif(config, items)

        assert result == 0
        assert len(items[0].own_markers) == 1

    def test_removes_multiple_skipif_markers_on_same_item(self) -> None:
        """With --run-windows-skip, all skipif markers on same item are removed (counted once)."""
        config = _mock_config(run_windows_skip=True)
        # 同一 item 上 2 个 skipif markers（模拟 win32 + py_version 双条件）
        item = _MockItem(["skipif", "skipif", "e2e"])

        result = strip_windows_skipif(config, [item])

        # 返回值按 item 计数（非按 marker 计数）：1 个 item 被 un-skip
        assert result == 1
        # 所有 skipif markers 被移除，仅保留 e2e
        assert len(item.own_markers) == 1
        assert item.own_markers[0].name == "e2e"

    def test_simulate_8_win_e2e_skip_cases(self) -> None:
        """Simulate the 8 Windows E2E skipif use cases: all have skipif, all un-skipped."""
        config = _mock_config(run_windows_skip=True)
        # 8 items each with skipif (mirrors the 8 Windows E2E skipif use cases)
        items = [_MockItem(["skipif", "e2e"]) for _ in range(8)]

        result = strip_windows_skipif(config, items)

        assert result == 8
        for item in items:
            assert len(item.own_markers) == 1
            assert item.own_markers[0].name == "e2e"


class TestAddWindowsSkipOption:
    """Tests for add_windows_skip_option() function."""

    def test_registers_cli_option(self) -> None:
        """--run-windows-skip must be registered as a store_true option with default False."""
        parser = MagicMock()

        add_windows_skip_option(parser)

        # call_args_list 同时验证调用次数（==1）与参数内容（强断言，避免弱 mock 断言）
        calls = parser.addoption.call_args_list
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == "--run-windows-skip"
        assert kwargs["action"] == "store_true"
        assert kwargs["default"] is False
