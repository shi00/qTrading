"""PRF-07：写库转换热路径（``_normalize_records_frame``）性能定基 + NULL 归一化输出等价性门禁。

对应报告 06「PRF-07」第 2 条：对 ``_normalize_records_frame`` 定基单次耗时，同时补一个
输出等价性测试锁定 NULL 归一化语义（守护 PRF-03 的向量化收益不被回退）。

集成策略（与 xdist 并存）：
- 基准用标准库 ``time.perf_counter`` 自行计时（D2.2 起不再依赖 pytest-benchmark：pytest 9.2
  移除了 ``Node._nodeid``，而 pytest-benchmark 5.3.0 仍引用该私有属性，两者不兼容）。
- 本项目 pytest addopts 强制 ``-n auto --dist=loadgroup``，并行下计时噪声大，故本文件在
  xdist 下自动退至 2 万行小帧（见 ``_bench_df``），避免常规全量跑平白多出数十秒的
  20 万行转换；此模式只跑通、耗时不具可比性。
- 要获得可比对的单次耗时（用于 PerfThreshold 校准与趋势观察），定向不带 xdist 运行：
  ``python -m pytest tests/unit/test_base_dao_perf.py -n0 -s``。
"""

from __future__ import annotations

import logging
import math
import os
import time

import numpy as np
import pandas as pd
import pytest

from data.persistence.daos.base_dao import _normalize_records_frame

pytestmark = pytest.mark.unit

logger = logging.getLogger(__name__)

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


def _bench_df(n: int | None = None) -> pd.DataFrame:
    """写库基准帧（默认 20 万行 × 6 列，仿真真实批量载荷，含 2% 数值 NaN）。

    xdist 全量跑时自动退到 2 万行小帧：并行下计时噪声大且定基不具可比性，用大帧会平白
    拖慢每次常规 CI 全量跑；仅 -n0 定向基准才使用基准尺寸。
    """
    if n is None:
        n = 20_000 if os.environ.get("PYTEST_XDIST_WORKER") else 200_000
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


def test_normalize_records_frame_null_normalization_equivalence() -> None:
    """NULL 归一输出等价性：数值/对象列 NaN 全部归一为 None，date 列产 date 对象。"""
    df = _sample_df()
    records, coerce_stats = _normalize_records_frame(df, list(_DATE_COLS), list(_DATETIME_COLS))

    _assert_no_nan_residual(records)
    assert len(records) == len(df)
    # 数值列与对象列的 NaN 必须变为 None（而非保留浮点 NaN）
    assert records[0]["price"] == 10.5
    assert records[1]["price"] is None
    assert records[0]["code"] == "000001"
    assert records[1]["code"] is None
    assert records[1]["amount"] == 500.0
    # date 列归一为 datetime.date 对象（DB Date 列专用），int/str 列原样保留
    assert str(records[0]["dt"]).startswith("2024-01-01")
    assert records[0]["qty"] == 100
    # 样本日期列均合法，不产生任何 coerce 告警（若出现说明 date cols 分类回归）
    assert coerce_stats == {}


def test_normalize_records_frame_write_path_bench() -> None:
    """写库转换基准：PRF-07 定基（20 万行 × 6 列），只记录耗时、不设硬阈值。

    本测试是"定基存续化"而非硬门禁——超时阈值由 PRF-07-5 的 PerfThreshold 校准承担，
    常规 xdist 全量跑下自动退至 2 万行小帧（见 ``_bench_df``），仅定向 ``-n0 -s`` 运行
    才用基准尺寸并输出可比对的单次耗时。
    """
    df = _bench_df()
    date_cols = list(_DATE_COLS)
    datetime_cols = list(_DATETIME_COLS)

    _normalize_records_frame(df, date_cols, datetime_cols)  # 预热一次，确保计时落在稳定段

    rounds = 3
    start = time.perf_counter()
    records: list[dict] = []
    for _ in range(rounds):
        records, _ = _normalize_records_frame(df, date_cols, datetime_cols)
    per_call_ms = (time.perf_counter() - start) / rounds * 1000

    assert len(records) == len(df)  # 计时轮次确实完成了转换，而非空转
    assert per_call_ms > 0  # 计时有效（perf_counter 单调）
    logger.info("PRF-07 写库转换基准：%.1f ms/次（%d 行 × %d 轮）", per_call_ms, len(df), rounds)
