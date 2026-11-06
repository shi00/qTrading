"""DS-06: data_source_tab 质量等级行 E2E 模式呈现测试。

覆盖 ``_build_health_summary_content`` 的三态：
- E2E 模式（is_e2e_mode()=True）→ 显示「质量门控已禁用（E2E 模式）」，warning 色；
- 非 E2E + tier 有值 → 展示质量等级（原语义）；
- 非 E2E + tier None → 「未检查」。

纯渲染函数（无 page 挂载），断言解耦 I18n 语言状态（期望值用同一 I18n.get 调用生成）。
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import flet as ft
import pytest

from ui.i18n import I18n
from ui.testing.anchor import _e2e_enabled
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors
from ui.viewmodels.data_source_view_model import DataSourceState, HealthResultRow
from ui.views.settings_tabs.data_source_tab import (
    _build_health_summary_content,
    _build_secondary_progress_bar,
)

pytestmark = pytest.mark.unit


def _collect_texts(control: ft.Control) -> list[tuple[str, str]]:
    """深度收集 (text, color) 对，供断言渲染结果。"""
    found: list[tuple[str, str]] = []

    def walk(c):
        if isinstance(c, ft.Text):
            found.append((c.value, c.color))
        for attr in ("controls", "content"):
            v = getattr(c, attr, None)
            if isinstance(v, list):
                for sub in v:
                    walk(sub)
            elif v is not None:
                walk(v)

    walk(control)
    return found


class TestHealthSummaryQualityRowE2E:
    def test_e2e_mode_shows_gate_disabled_banner(self):
        """E2E 模式：tier 有值也显示「质量门控已禁用」，而非静默展示等级。"""
        row = HealthResultRow(quality_tier=2)
        with patch("ui.views.settings_tabs.data_source_tab.is_e2e_mode", return_value=True):
            col = _build_health_summary_content(row)
        texts = _collect_texts(col)
        values = [t for t, _ in texts]
        assert I18n.get("ds_e2e_gate_disabled") in values
        # 降级必须可见：E2E 禁用提示用 warning 色
        e2e_pair = next(pair for pair in texts if pair[0] == I18n.get("ds_e2e_gate_disabled"))
        assert e2e_pair[1] == AppColors.WARNING

    def test_normal_mode_shows_tier(self):
        """非 E2E + tier 有值 → 展示质量等级（原语义不变）。"""
        row = HealthResultRow(quality_tier=2)
        with patch("ui.views.settings_tabs.data_source_tab.is_e2e_mode", return_value=False):
            col = _build_health_summary_content(row)
        values = [t for t, _ in _collect_texts(col)]
        expected = f"{I18n.get('ds_data_quality_tier')}: {I18n.get('quality_tier_2')}"
        assert expected in values

    def test_normal_mode_tier_none_shows_not_checked(self):
        """非 E2E + tier None → 「未检查」。"""
        row = HealthResultRow(quality_tier=None)
        with patch("ui.views.settings_tabs.data_source_tab.is_e2e_mode", return_value=False):
            col = _build_health_summary_content(row)
        values = [t for t, _ in _collect_texts(col)]
        expected = f"{I18n.get('ds_data_quality_tier')}: {I18n.get('ds_not_checked')}"
        assert expected in values


@pytest.fixture(autouse=True)
def _clear_anchor_cache():
    """每个测试前后清理 anchored() 的 E2E 模式缓存。"""
    _e2e_enabled.cache_clear()
    yield
    _e2e_enabled.cache_clear()


class TestDataSourceProgressAnchor:
    """FUP-014: 数据同步数值型进度条 anchor 契约。"""

    def _state(self, **kwargs) -> DataSourceState:
        defaults = dict(is_syncing=True, active_key="daily_sync", progress=0.42, progress_message=None)
        defaults.update(kwargs)
        return cast(DataSourceState, SimpleNamespace(**defaults))

    def test_daily_sync_numeric_bar_anchored(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        control = _build_secondary_progress_bar(self._state(), visible=True)

        assert isinstance(control, ft.Semantics)
        assert control.identifier == EIDS.SETTINGS.DATA_SOURCE_PROGRESS[0]
        assert isinstance(control.content, ft.ProgressBar)
        assert control.content.value == pytest.approx(0.42)

    def test_cache_clear_indeterminate_not_anchored(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        control = _build_secondary_progress_bar(self._state(active_key="cache_clear"), visible=True)

        assert isinstance(control, ft.ProgressBar)
        assert control.value is None

    def test_hidden_numeric_bar_not_anchored(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        control = _build_secondary_progress_bar(self._state(), visible=False)

        assert isinstance(control, ft.ProgressBar)
        assert control.visible is False
        assert control.value == pytest.approx(0.42)
