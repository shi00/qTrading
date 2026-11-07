"""Tests for scripts/check_per_file_coverage.py 分层覆盖率门禁 (D37).

验证:
- load_config: 读取 pyproject.toml [tool.custom_coverage]，返回默认阈值/分层列表/enforce 标志
- match_threshold: 最长前缀匹配，路径归一化，未匹配回退默认阈值
- main: coverage.json 输入校验（fail-closed）与 0/1/2 退出码、全零语句 SKIP
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_per_file_coverage  # noqa: E402 - sys.path 注入后导入
from check_per_file_coverage import (  # noqa: E402 - sys.path 注入后导入
    CoverageDataError,
    _report_layered,
    _validate_file_entry,
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


class TestReportLayered:
    """review07-G14: _report_layered 分层达标率统计（不阻断，输出 stderr）。"""

    def test_groups_and_counts(self, capsys):
        files = {
            "services/foo.py": {"summary": {"percent_covered": 92.0, "num_statements": 100, "missing_lines": 8}},
            "services/bar.py": {"summary": {"percent_covered": 85.0, "num_statements": 100, "missing_lines": 15}},
            "strategies/baz.py": {"summary": {"percent_covered": 95.0, "num_statements": 50, "missing_lines": 3}},
            "core/corge.py": {"summary": {"percent_covered": 70.0, "num_statements": 20, "missing_lines": 6}},
        }
        layered = [("services/", 90), ("strategies/", 90)]
        _report_layered(files, 80, layered)
        err = capsys.readouterr().err
        assert "::warning::[分层覆盖率]" in err
        assert "services/    threshold=90%" in err
        assert "1/2 达标" in err  # services 92/85 → 1 达标 1 未达标
        assert "strategies/  threshold=90%" in err
        assert "core/corge.py" in err  # (default) 组未达标样例
        assert "(default)" in err

    def test_all_pass_no_below_samples(self, capsys):
        files = {
            "services/foo.py": {"summary": {"percent_covered": 95.0, "num_statements": 100, "missing_lines": 5}},
            "ui/bar.py": {"summary": {"percent_covered": 88.0, "num_statements": 40, "missing_lines": 4}},
        }
        layered = [("services/", 90), ("ui/", 85)]
        _report_layered(files, 80, layered)
        err = capsys.readouterr().err
        assert "2/2 达标" not in err  # 不同组各自统计，无此总体行
        assert "services/    threshold=90%  1/1 达标" in err
        assert "ui/          threshold=85%  1/1 达标" in err
        assert "未达标" not in err


# ============================================================================
# main: coverage.json 输入校验（fail-closed）与退出码
# ============================================================================


def _entry(pct: float, stmts: int = 100, missing: int | None = None) -> dict:
    """构造合法的单文件 coverage 条目."""
    return {
        "summary": {
            "percent_covered": pct,
            "num_statements": stmts,
            "missing_lines": stmts - int(stmts * pct / 100) if missing is None else missing,
        }
    }


def _run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: object = None,
    raw: str | None = None,
    report: bool = False,
    layered: list[tuple[str, int]] | None = None,
    enforce: bool = False,
) -> int:
    """在临时目录写入 coverage.json、替换 ROOT/配置后调用 main()."""
    coverage_file = tmp_path / "coverage.json"
    coverage_file.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(check_per_file_coverage, "ROOT", tmp_path)
    monkeypatch.setattr(check_per_file_coverage, "load_config", lambda: (80, layered or [], enforce))
    monkeypatch.setattr(sys, "argv", ["check_per_file_coverage.py"] + (["--report"] if report else []))
    return check_per_file_coverage.main()


class TestMainInputValidation:
    """缺 files / 空 files / 损坏 JSON / 条目结构非法必须失败退出，不得判 PASS."""

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"files": {}},
            {"meta": {"version": "1"}},
            {"files": []},
        ],
    )
    def test_missing_or_empty_files_returns_2(self, tmp_path, monkeypatch, capsys, payload):
        assert _run_main(tmp_path, monkeypatch, payload=payload) == 2
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert "PASS" not in out

    def test_non_dict_toplevel_returns_2(self, tmp_path, monkeypatch, capsys):
        assert _run_main(tmp_path, monkeypatch, payload=[1, 2, 3]) == 2
        assert "ERROR" in capsys.readouterr().out

    def test_broken_json_returns_2(self, tmp_path, monkeypatch, capsys):
        assert _run_main(tmp_path, monkeypatch, raw="{not json") == 2
        assert "ERROR" in capsys.readouterr().out

    def test_unreadable_file_returns_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(check_per_file_coverage, "ROOT", tmp_path)
        monkeypatch.setattr(check_per_file_coverage, "load_config", lambda: (80, [], False))
        monkeypatch.setattr(sys, "argv", ["check_per_file_coverage.py"])
        assert check_per_file_coverage.main() == 2
        assert "ERROR" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "entry",
        [
            "not-a-dict",
            {"summary": "not-a-dict"},
            {"summary": {"num_statements": -1, "missing_lines": 0, "percent_covered": 90.0}},
            {"summary": {"num_statements": True, "missing_lines": 0, "percent_covered": 90.0}},
            {"summary": {"num_statements": 10.0, "missing_lines": 0, "percent_covered": 90.0}},
            {"summary": {"num_statements": 10, "missing_lines": 11, "percent_covered": 90.0}},
            {"summary": {"num_statements": 10, "missing_lines": -1, "percent_covered": 90.0}},
            {"summary": {"num_statements": 10, "missing_lines": True, "percent_covered": 90.0}},
            {"summary": {"num_statements": 10, "missing_lines": 0, "percent_covered": "90"}},
            {"summary": {"num_statements": 10, "missing_lines": 0, "percent_covered": True}},
            {"summary": {"num_statements": 10, "missing_lines": 0, "percent_covered": 101}},
            {"summary": {"num_statements": 10, "missing_lines": 0, "percent_covered": float("nan")}},
        ],
    )
    def test_invalid_file_entry_returns_2(self, tmp_path, monkeypatch, capsys, entry):
        assert _run_main(tmp_path, monkeypatch, payload={"files": {"app/foo.py": entry}}) == 2
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert "PASS" not in out

    def test_non_str_files_key_rejected(self):
        with pytest.raises(CoverageDataError) as exc_info:
            _validate_file_entry(1, {"summary": {"num_statements": 10, "missing_lines": 0, "percent_covered": 90.0}})
        assert "files 键必须为字符串" in str(exc_info.value)


class TestMainThresholds:
    """默认阈值 80%：低于 1、达到 0；全零语句 SKIP 且不宣称达标."""

    def test_below_default_returns_1(self, tmp_path, monkeypatch, capsys):
        payload = {"files": {"app/foo.py": _entry(79.9, stmts=1000, missing=201)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_at_default_returns_0(self, tmp_path, monkeypatch, capsys):
        payload = {"files": {"app/foo.py": _entry(80.0)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload) == 0
        assert "PASS" in capsys.readouterr().out

    def test_all_zero_statements_skips_without_pass_claim(self, tmp_path, monkeypatch, capsys):
        payload = {"files": {"app/empty.py": _entry(0, stmts=0, missing=0)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload) == 0
        out = capsys.readouterr().out
        assert "SKIP" in out
        assert "无可执行语句" in out
        assert "PASS" not in out
        assert "达标" not in out

    def test_all_zero_statements_report_mode_skips(self, tmp_path, monkeypatch, capsys):
        payload = {"files": {"app/empty.py": _entry(0, stmts=0, missing=0)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload, report=True) == 0
        captured = capsys.readouterr()
        assert "SKIP" in captured.out
        assert "::warning::[分层覆盖率]" not in captured.err


class TestMainReportMode:
    """--report 保留不阻断语义：合法输入 0，非法输入仍 2."""

    def test_report_valid_returns_0(self, tmp_path, monkeypatch, capsys):
        payload = {"files": {"services/foo.py": _entry(92.0), "app/bar.py": _entry(70.0)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload, report=True, layered=[("services/", 90)]) == 0
        assert "::warning::[分层覆盖率]" in capsys.readouterr().err

    def test_report_invalid_returns_2(self, tmp_path, monkeypatch, capsys):
        assert _run_main(tmp_path, monkeypatch, payload={"files": {}}, report=True) == 2
        assert "ERROR" in capsys.readouterr().out


class TestMainLayered:
    """分层阈值回归：最长前缀生效，enforce=false 仅 advisory."""

    def test_longest_prefix_uses_tighter_threshold(self, tmp_path, monkeypatch, capsys):
        layered = [("data/sync/", 95), ("data/", 90)]
        payload = {"files": {"data/sync/x.py": _entry(92.0)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload, layered=layered, enforce=True) == 1
        assert "[ENFORCE]" in capsys.readouterr().out

    def test_advisory_mode_does_not_block(self, tmp_path, monkeypatch, capsys):
        layered = [("data/sync/", 95), ("data/", 90)]
        payload = {"files": {"data/sync/x.py": _entry(92.0)}}
        assert _run_main(tmp_path, monkeypatch, payload=payload, layered=layered, enforce=False) == 0
        out = capsys.readouterr().out
        assert "[ADVISORY]" in out
        assert "PASS" in out
