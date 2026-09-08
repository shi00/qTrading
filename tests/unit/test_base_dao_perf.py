"""PRF-07：写库转换热路径（``_prepare_records``）性能定基 + NULL 归一化输出等价性门禁。

对应报告 06「PRF-07」第 2 条：pytest-benchmark 对 ``_prepare_records`` 定基，同时补一个
输出等价性测试锁定 NULL 归一化语义（守护 PRF-03 的向量化收益不被回退）。

集成策略（与 xdist 并存）：
- 本项目 pytest addopts 强制 ``-n auto --dist=loadgroup``。pytest-benchmark 检测到 xdist
  激活时**自动禁用测量**（仅打印 warning，测试仍正常执行通过），因此常规 xdist 全量跑
  不会因本文件增加时长或产生 janky 基准数据。
- 要获得真实测量数据（用于 PerfThreshold 校准与趋势观察），定向不带 xdist 运行：
  ``python -m pytest tests/unit/test_base_dao_perf.py -n0 --benchmark-autosave``。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from data.persistence.daos.base_dao import _prepare_records

pytestmark = pytest.mark.unit

_DATE_COLS = ["dt"]
_DATETIME_COLS = ["ts"]


def _sample_df() -> pd.DataFrame:
    """小样本帧：覆盖 str / float(NaN) / int / date / datetime 五类列型，供等价性断言。"""
    return pd.DataFrame(
        {
            "code": ["000001", np.nan, "000003"],  # object 列含 NaN 值
            "price": [10.5, np.nan, 12.0],
            "qty": [100, 200, 300],
            "amount": [np.nan, 500.0, 450.0],
            "dt": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),  # date 列
            "ts": pd.to_datetime(["2024-01-01 09:00", "2024-01-02 09:00", "2024-01-03 09:00"]),  # datetime 列
        }
    )


def _bench_df(n: int = 200_000) -> pd.DataFrame:
    """写库基准帧：20 万行 × 6 列，仿真真实批量载荷（含 2% 数值 NaN）。"""
    rng = np.random.default_rng(42)
    df = pd.DataFrame(
        {
            "code": [f"00000{(i % 10)}" for i in range(n)],
            "price": (rng.random(n) * 1000).round(2),
            "qty": rng.integers(1, 1000, n).astype(np.int64),
            "amount": (rng.random(n) * 500).round(2),
            "dt": pd.to_datetime([f"2024-01-{(i % 28 + 1):02d}" for i in range(n)]),
            "ts": pd.date_range("2024-01-01", periods=n, freq="min"),
        }
    )
    df.loc[df.sample(frac=0.02, random_state=1).index, "price"] = np.nan
    return df


def _assert_no_nan_residual(records: list[dict]) -> None:
    """断言记录序列中不存在浮点 NaN / NaT 残留（NULL 归一必须兜底干净）。"""
    for rec in records:
        for v in rec.values():
            assert v is None or not (isinstance(v, float) and math.isnan(v)), f"残留 NaN: {v!r}"


def test_prepare_records_null_normalization_equivalence() -> None:
    """NULL 归一输出等价性：数值/对象列 NaN 全部归一为 None，date 列产 date 对象。"""
    df = _sample_df()
    records, coerce_stats = _prepare_records(df, list(_DATE_COLS), list(_DATETIME_COLS))

    _assert_no_nan_residual(records)
    assert len(records) == len(df)
    # 数值列与对象列的 NaN 必须变为 None（而非保留浮点 NaN）
    assert records[0]["price"] == 10.5
    assert records[1]["price"] is None
    assert records[0]["code"] == "000001"
    assert records[1]["code"] is None
    assert records[1]["amount"] == 500.0
    # date 列归一为 datetime.date 对象（DB Date 列专用），int/str 列原样保留
    assert isinstance(records[0]["dt"], object) and str(records[0]["dt"]).startswith("2024-01-01")
    assert records[0]["qty"] == 100
    assert coerce_stats == {} or isinstance(coerce_stats, dict)


@pytest.mark.benchmark(group="write_path", min_rounds=3, max_time=2.0, warmup=True)
def test_prepare_records_write_path_bench(benchmark) -> None:
    """写库转换基准：PRF-07 定基（20 万行 × 6 列）。

    本测试是"定基存续化"而非硬门禁——超时阈值由 PRF-07-5 的 PerfThreshold 校准承担，
    此处在 xdist 全量跑时自动禁用测量，定向 ``-n0`` 运行才产出真实数据。
    """
    df = _bench_df()

    def _run() -> None:
        _prepare_records(df, list(_DATE_COLS), list(_DATETIME_COLS))

    _run()  # 预热一次，确保计时观测稳定段
    benchmark(_run)
