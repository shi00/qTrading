"""Tests for scripts/check_per_file_coverage.py 分层覆盖率门禁 (D37).

验证:
- load_config: 读取 pyproject.toml [tool.custom_coverage]，返回默认阈值/分层列表/enforce 标志
- match_threshold: 最长前缀匹配，路径归一化，未匹配回退默认阈值
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_per_file_coverage import (  # noqa: E402 - sys.path 注入后导入
    load_config,
    match_threshold,
)


# ============================================================================
# load_config: 读取 pyproject.toml 配置
# ============================================================================


class TestLoadConfig:
    """load_config: 读取 [tool.custom_coverage] 配置."""

    def test_returns_default_threshold(self):
        default, _, _ = load_config()
        assert default == 80

    def test_returns_layered_thresholds_sorted_by_length_desc(self):
        _, layered, _ = load_config()
        # 四个分层路径
        prefixes = [p for p, _ in layered]
        assert set(prefixes) == {"services/", "strategies/", "data/", "ui/"}
        # 按长度降序：strategies/ (11) > services/ (9) > ui/ (3) == data/ (5)
        # strategies/ 最长，应排首位
        assert prefixes[0] == "strategies/"

    def test_returns_enforce_layered_flag(self):
        _, _, enforce = load_config()
        # 当前 NOTE(lazy) advisory 模式：enforce_layered = false
        assert enforce is False

    def test_layered_thresholds_values(self):
        _, layered, _ = load_config()
        thresholds = dict(layered)
        assert thresholds["services/"] == 90
        assert thresholds["strategies/"] == 90
        assert thresholds["data/"] == 90
        assert thresholds["ui/"] == 85


# ============================================================================
# match_threshold: 最长前缀匹配
# ============================================================================


class TestMatchThreshold:
    """match_threshold: 按目录分层匹配阈值."""

    LAYERED = [
        ("strategies/", 90),
        ("services/", 90),
        ("data/", 90),
        ("ui/", 85),
    ]

    def test_services_matches_90(self):
        threshold, prefix = match_threshold("services/embedded_pg_maintenance_service.py", 80, self.LAYERED)
        assert threshold == 90
        assert prefix == "services/"

    def test_strategies_matches_90(self):
        threshold, prefix = match_threshold("strategies/base_strategy.py", 80, self.LAYERED)
        assert threshold == 90
        assert prefix == "strategies/"

    def test_ui_matches_85(self):
        threshold, prefix = match_threshold("ui/viewmodels/backup_restore_view_model.py", 80, self.LAYERED)
        assert threshold == 85
        assert prefix == "ui/"

    def test_data_matches_90(self):
        threshold, prefix = match_threshold("data/persistence/daos/base_dao.py", 80, self.LAYERED)
        assert threshold == 90
        assert prefix == "data/"

    def test_app_falls_back_to_default(self):
        threshold, prefix = match_threshold("app/bootstrap.py", 80, self.LAYERED)
        assert threshold == 80
        assert prefix is None

    def test_utils_falls_back_to_default(self):
        threshold, prefix = match_threshold("utils/singleton_registry.py", 80, self.LAYERED)
        assert threshold == 80
        assert prefix is None

    def test_core_falls_back_to_default(self):
        threshold, prefix = match_threshold("core/i18n.py", 80, self.LAYERED)
        assert threshold == 80
        assert prefix is None

    def test_longest_prefix_wins(self):
        """嵌套目录最长前缀优先：data/sync/ 应匹配 data/ 而非更短的前缀."""
        layered = [
            ("data/", 90),
            ("data/sync/", 95),  # 更长的前缀
        ]
        # 按长度降序排序后 data/sync/ 优先
        layered_sorted = sorted(layered, key=lambda kv: len(kv[0]), reverse=True)
        threshold, prefix = match_threshold("data/sync/base.py", 80, layered_sorted)
        assert threshold == 95
        assert prefix == "data/sync/"

    def test_backslash_path_normalized(self):
        """Windows 反斜杠路径归一化为 / 后匹配."""
        threshold, prefix = match_threshold("services\\embedded_pg_maintenance_service.py", 80, self.LAYERED)
        assert threshold == 90
        assert prefix == "services/"

    def test_leading_dot_slash_stripped(self):
        threshold, prefix = match_threshold("./services/foo.py", 80, self.LAYERED)
        assert threshold == 90
        assert prefix == "services/"

    def test_no_partial_match_at_filename(self):
        """services_foo.py 不应匹配 services/ 前缀（需以 / 分隔）."""
        threshold, prefix = match_threshold("services_helper.py", 80, self.LAYERED)
        assert threshold == 80
        assert prefix is None

    def test_empty_layered_falls_back_to_default(self):
        threshold, prefix = match_threshold("services/foo.py", 80, [])
        assert threshold == 80
        assert prefix is None
