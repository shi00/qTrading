"""utils/qfq.py 的 qfq_ratio_series 属性测试（纯 pytest，不依赖 hypothesis）。

覆盖 qfq.py:25-57 的所有分支：
- 37-38: None/empty → None
- 44-45: filled 全 null → None
- 41: ffill/bfill 填充
- 50-51: latest=0/null → None
- 54-55: 全相同 → None
- 57: 返回 filled/latest
"""

import pandas as pd
import pytest

from utils.qfq import qfq_ratio_series

pytestmark = pytest.mark.unit


def test_qfq_ratio_none_returns_none():
    """None 输入返回 None（qfq.py:37）。"""
    assert qfq_ratio_series(None) is None


def test_qfq_ratio_empty_returns_none():
    """空 Series 输入返回 None（qfq.py:38）。"""
    assert qfq_ratio_series(pd.Series([], dtype=float)) is None


def test_qfq_ratio_all_nan_returns_none():
    """filled 全 null 时返回 None（qfq.py:44-45）。

    覆盖 qfq.py:44 的 `filled.isna().all()` 分支。
    hypothesis 不生成 NaN，需手动构造。
    """
    series = pd.Series([float("nan"), float("nan"), float("nan")])
    assert qfq_ratio_series(series) is None


def test_qfq_ratio_partial_nan_filled_and_returned():
    """部分 NaN 被 ffill/bfill 填充后正常返回比率（qfq.py:41,57）。

    覆盖 qfq.py:41 的 `filled = series.ffill().bfill()` 路径。
    """
    series = pd.Series([1.0, float("nan"), 2.0])
    result = qfq_ratio_series(series)
    assert result is not None
    assert result.iloc[-1] == pytest.approx(1.0)  # 2.0 / 2.0 = 1.0
    assert result.iloc[0] == pytest.approx(0.5)  # 1.0 / 2.0 = 0.5


def test_qfq_ratio_latest_zero_returns_none():
    """latest 为 0 时返回 None（qfq.py:50-51）。

    覆盖 qfq.py:50 的 `if latest_factor == 0 or pd.isna(latest_factor):` 分支。
    """
    series = pd.Series([1.0, 0.0, 0.0])
    assert qfq_ratio_series(series) is None


def test_qfq_ratio_all_identical_returns_none():
    """全相同值时返回 None（qfq.py:54-55）。

    覆盖 qfq.py:54 的 `if filled.nunique() == 1:` 分支。
    """
    series = pd.Series([5.0, 5.0, 5.0])
    assert qfq_ratio_series(series) is None


def test_qfq_ratio_last_is_one_when_returned():
    """返回的比率序列最后一个元素应为 1.0（qfq.py:57）。

    覆盖 qfq.py:57 的 `return filled / latest_factor` 路径。
    latest_factor = filled.iloc[-1]，所以 filled.iloc[-1] / latest_factor = 1.0。
    """
    series = pd.Series([1.0, 2.0, 4.0])
    result = qfq_ratio_series(series)
    assert result is not None
    assert result.iloc[-1] == pytest.approx(1.0)


def test_qfq_ratio_consistency():
    """比率一致性：result[i] = series[i] / series[-1]（qfq.py:57）。

    覆盖 qfq.py:57 的 `return filled / latest_factor` 路径，
    验证返回值的数学正确性。
    """
    series = pd.Series([10.0, 20.0, 40.0, 80.0])
    result = qfq_ratio_series(series)
    assert result is not None
    latest = series.iloc[-1]
    for i, val in enumerate(series):
        assert result.iloc[i] == pytest.approx(val / latest)


def test_qfq_ratio_negative_values():
    """负值序列也能正确计算比率（qfq.py:57）。

    覆盖 qfq.py:57 对负值的处理。
    """
    series = pd.Series([-10.0, -20.0, -40.0])
    result = qfq_ratio_series(series)
    assert result is not None
    assert result.iloc[-1] == pytest.approx(1.0)
    assert result.iloc[0] == pytest.approx(0.25)  # -10 / -40 = 0.25


def test_qfq_ratio_single_element():
    """单元素序列：nunique()==1 应返回 None（qfq.py:54-55）。"""
    series = pd.Series([5.0])
    assert qfq_ratio_series(series) is None
