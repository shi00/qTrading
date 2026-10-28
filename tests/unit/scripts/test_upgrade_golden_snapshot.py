"""金标准快照工具（scripts/upgrade_golden_snapshot.py）单测。

覆盖三项契约：
1. ``--check`` 在 fixture 未改动时返回 0 并打印 SAME；
2. expected 被篡改时返回 1，且逐项打印策略名 / 差异类型 / 首个差异样本；
3. 浮点容差边界（score 绝对误差 ≤1e-9、NAV 相对误差 ≤1e-10）与成员/名次变化必判 DIFF。

测试只操作 ``tmp_path`` 下的 fixture 副本，不触碰仓库内 ``tests/fixtures/upgrade_golden/``。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

import scripts.upgrade_golden_snapshot as golden

pytestmark = pytest.mark.meta


def _prepare_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """把 fixtures 复制到临时目录，并让脚本只读写该副本。"""
    source = golden.FIXTURE_DIR
    input_dir = tmp_path / "inputs"
    expected_dir = tmp_path / "expected"
    shutil.copytree(source / "inputs", input_dir)
    shutil.copytree(source / "expected", expected_dir)
    monkeypatch.setattr(golden, "INPUT_DIR", input_dir)
    monkeypatch.setattr(golden, "EXPECTED_DIR", expected_dir)
    return input_dir, expected_dir


def _picks(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=golden.PICK_COLUMNS)


def _nav(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=golden.NAV_COLUMNS)


def test_check_returns_zero_on_clean_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_snapshot(tmp_path, monkeypatch)

    assert golden.main(["--check"]) == 0

    assert "SAME" in capsys.readouterr().out


def test_check_returns_one_when_pick_value_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, expected_dir = _prepare_snapshot(tmp_path, monkeypatch)
    target = expected_dir / "picks_oversold_ai.csv"
    frame = pd.read_csv(target, dtype={"ts_code": str})
    frame.loc[0, "ai_score"] = float(frame.loc[0, "ai_score"]) + 0.5
    frame.to_csv(target, index=False, float_format="%.12f", lineterminator="\n")

    assert golden.main(["--check"]) == 1

    output = capsys.readouterr().out
    assert "DIFF" in output
    assert "picks_oversold_ai.csv" in output
    assert "ai_score" in output


def test_abs_close_boundary_for_score() -> None:
    assert golden._abs_close(0.0, golden.SCORE_ABS_TOL, golden.SCORE_ABS_TOL)
    assert not golden._abs_close(0.0, golden.SCORE_ABS_TOL * 1.1, golden.SCORE_ABS_TOL)


def test_rel_close_boundary_for_nav() -> None:
    assert golden._rel_close(0.0, golden.NAV_REL_TOL)
    assert not golden._rel_close(0.0, golden.NAV_REL_TOL * 2.0)


def test_pick_score_within_abs_tolerance_is_same() -> None:
    expected = _picks([{"rank": 1, "ts_code": "000001.SZ", "score": 10.0, "ai_score": 50.0}])
    actual = _picks([{"rank": 1, "ts_code": "000001.SZ", "score": 10.0 + 5e-10, "ai_score": 50.0}])

    assert golden._diff_picks("picks_same.csv", expected, actual) == []


def test_pick_score_beyond_abs_tolerance_is_diff() -> None:
    expected = _picks([{"rank": 1, "ts_code": "000001.SZ", "score": 10.0, "ai_score": 50.0}])
    actual = _picks([{"rank": 1, "ts_code": "000001.SZ", "score": 10.0 + 2e-9, "ai_score": 50.0}])

    diffs = golden._diff_picks("picks_beyond.csv", expected, actual)

    assert len(diffs) == 1
    assert "picks_beyond.csv" in diffs[0]
    assert "score" in diffs[0]


def test_pick_member_or_rank_change_is_diff() -> None:
    expected = _picks(
        [
            {"rank": 1, "ts_code": "000001.SZ", "score": 10.0, "ai_score": 50.0},
            {"rank": 2, "ts_code": "000002.SZ", "score": 9.0, "ai_score": 40.0},
        ]
    )
    member_changed = _picks(
        [
            {"rank": 1, "ts_code": "000002.SZ", "score": 10.0, "ai_score": 50.0},
            {"rank": 2, "ts_code": "000001.SZ", "score": 9.0, "ai_score": 40.0},
        ]
    )
    rank_changed = _picks(
        [
            {"rank": 2, "ts_code": "000001.SZ", "score": 10.0, "ai_score": 50.0},
            {"rank": 1, "ts_code": "000002.SZ", "score": 9.0, "ai_score": 40.0},
        ]
    )

    member_diffs = golden._diff_picks("picks_member.csv", expected, member_changed)
    rank_diffs = golden._diff_picks("picks_rank.csv", expected, rank_changed)

    assert member_diffs and "成员/名次变化" in member_diffs[0]
    assert rank_diffs and "成员/名次变化" in rank_diffs[0]


def test_nav_within_rel_tolerance_is_same() -> None:
    expected = _nav([{"trade_date": "2025-12-30", "nav": 1_000_000.0}])
    actual = _nav([{"trade_date": "2025-12-30", "nav": 1_000_000.0 + 5e-5}])

    assert golden._diff_nav("backtest_nav.csv", expected, actual) == []


def test_nav_beyond_rel_tolerance_is_diff() -> None:
    expected = _nav([{"trade_date": "2025-12-30", "nav": 1_000_000.0}])
    actual = _nav([{"trade_date": "2025-12-30", "nav": 1_000_000.0 + 2e-4}])

    diffs = golden._diff_nav("backtest_nav.csv", expected, actual)

    assert len(diffs) == 1
    assert "nav" in diffs[0]


def test_nav_trade_date_change_is_diff() -> None:
    expected = _nav([{"trade_date": "2025-12-30", "nav": 1_000_000.0}])
    actual = _nav([{"trade_date": "2025-12-29", "nav": 1_000_000.0}])

    diffs = golden._diff_nav("backtest_nav.csv", expected, actual)

    assert len(diffs) == 1
    assert "交易日变化" in diffs[0]
