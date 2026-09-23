"""qfq 双实现（qfq_ratio_series vs qfq_ratio_expr）交叉等价性固化测试。

背景（reviews/开源组件使用检视报告.md §4.2/§5.2 D4）：utils/qfq.py 的 qfq
双实现 API 不对称——Polars 版 ``qfq_ratio_expr`` 支持 ``ref: Literal["last", "first"]``
（回测 Point-in-Time 基准），而 pandas 版 ``qfq_ratio_series`` 此前无 ``ref``
参数、只能是 "last"。若 pandas 路径代码需要 PIT 语义，会静默拿到 "last"——
即回测前视偏差（lookahead bias），且无任何报错。

D4 修复（本测试配套验证的目标契约）：
- ``qfq_ratio_series`` 补齐 ``ref: Literal["last","first"] = "last"``，向后兼容
  （默认 "last" 与原实现逐元素等价），"first" 与 ``qfq_ratio_expr(ref="first")`` 等价。
- 两实现交叉等价性零覆盖 → 本测试组 A 排除数值分叉、组 B 显式固化已知返回契约分叉。

等价域结论（组 A，allclose(rtol=1e-9, atol=0) 内等价）：
- 随机因子 / 含 null 首值 / 多 ts_code 组，在 ``ref`` 取 "last" / "first"
  两种基准下，pandas 侧逐组 ``qfq_ratio_series(s, ref)`` 与 Polars 侧
  ``df.select(qfq_ratio_expr(ref=ref))`` 逐元素等价。

返回契约分叉（组 B 显式固化，非等价断言；数值等价但返回形态不同）：
- B1 ``base == 0``（latest=0 或 first=0）：pandas 返 ``None``（调用方判据
  "无需调整"） vs Polars 内联全 ``1.0``。
- B2 filled 全 null：pandas 返 ``None`` vs Polars 内联全 ``1.0``。
- B3 全部因子相同：pandas 优化短路返 ``None`` vs Polars 返全 ``1.0``。

收敛指引：双实现 API 已对齐（语义一致），返回契约（None vs 1.0 内联）为
pandas "无调整哨兵" 与 Polars "恒等比率" 的表达差异，消费方各自已能处理，
本测试固化现状而非消除；如需统一可收敛 pandas 到 ``all`` Series(1.0) 或
Polars 到 null，届时同步更新组 B 断言。
"""

import numpy as np
import pandas as pd
import polars as pl
import pytest

from utils.qfq import qfq_ratio_expr, qfq_ratio_series

pytestmark = pytest.mark.unit

RTOL = 1e-9
ATOL = 0.0


# ---------------------------------------------------------------------------
# 帮助函数（组 A 等价域：pandas 逐组跑 vs Polars 全表 expr）
# ---------------------------------------------------------------------------


def _pandas_per_group(ts_code: list[str], factors: list[float], ref: str) -> pd.Series:
    """逐组调 qfq_ratio_series；返回 None 的组按全 NaN 标记（组 A 不应触发）。"""
    df = pd.DataFrame({"ts_code": ts_code, "adj_factor": factors})
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for _code, sub in df.groupby("ts_code", sort=False):
        res = qfq_ratio_series(sub["adj_factor"], ref=ref)
        if res is not None:
            assert len(res) == len(sub)
            out.loc[sub.index] = res.to_numpy()
    return out


def _assert_equivalent(ts_code: list[str], factors: list[float], ref: str) -> None:
    """组 A：排除已知分叉（base==0/全 null/全相同）的前提下逐元素 allclose。"""
    pl_df = pl.DataFrame({"ts_code": ts_code, "adj_factor": factors})
    pl_ratio = pl_df.with_columns(qfq_ratio_expr("adj_factor", "ts_code", ref=ref))["qfq_ratio"].to_pandas()

    pd_ratio = _pandas_per_group(ts_code, factors, ref)

    assert np.allclose(pd_ratio.to_numpy(dtype=float), pl_ratio.to_numpy(dtype=float), rtol=RTOL, atol=ATOL)


# ---------------------------------------------------------------------------
# 组 A：数值等价（等价域）—— ref="last" / "first" 双基准
# ---------------------------------------------------------------------------


class TestQfqEquivalenceDomain:
    @pytest.mark.parametrize("ref", ["last", "first"])
    def test_random_factors_single_group(self, ref: str):
        ts_code = ["000001.SZ"] * 6
        factors = [2.0, 1.5, 1.8, 1.2, 1.0, 1.3]
        _assert_equivalent(ts_code, factors, ref)

    @pytest.mark.parametrize("ref", ["last", "first"])
    def test_leading_null_single_group(self, ref: str):
        # 首值为 None，需 bfill；base 在 "first" 下为首个非 null 填充值
        ts_code = ["000001.SZ"] * 5
        factors = [None, 2.0, 1.0, 1.5, 1.2]
        _assert_equivalent(ts_code, factors, ref)

    @pytest.mark.parametrize("ref", ["last", "first"])
    def test_multi_group(self, ref: str):
        # 多组 ts_code，Polars 按 over(ts_code) 逐组基准；pandas 逐组独立
        ts_code = ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ", "000003.SZ", "000003.SZ"]
        factors = [2.0, 3.0, 1.0, 1.5, 2.5, 1.1]
        _assert_equivalent(ts_code, factors, ref)

    @pytest.mark.parametrize("ref", ["last", "first"])
    def test_random_values_with_internal_null(self, ref: str):
        # 单组多行（组内相邻随机因子必不全相等，规避组 B B3 全相同短路）；
        # 组内插入 null 验证 ffill/bfill 填充后两侧对齐。
        rng = np.random.default_rng(42)
        factors = list(np.round(rng.uniform(0.5, 4.0, 12), 6))
        factors[3] = None
        factors[8] = None
        ts_code = ["000001.SZ"] * 12
        _assert_equivalent(ts_code, factors, ref)


# ---------------------------------------------------------------------------
# 组 B：返回契约分叉显式固化（数值等价、返回形态不同；非等价断言）
# ---------------------------------------------------------------------------


class TestQfqContractDivergenceSolidified:
    def test_b1_zero_latest_base(self):
        """latest=0：pandas 返 None vs Polars 内联全 1.0。"""
        series = pd.Series([2.0, 0.0])
        assert qfq_ratio_series(series) is None
        assert qfq_ratio_series(series, ref="last") is None

        pl_df = pl.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "adj_factor": [2.0, 0.0]})
        ratio = pl_df.with_columns(qfq_ratio_expr("adj_factor", "ts_code", ref="last"))["qfq_ratio"]
        assert ratio.to_list() == pytest.approx([1.0, 1.0])

    def test_b1_prime_zero_first_base(self):
        """ref="first" 且首值为 0：pandas 返 None vs Polars 内联全 1.0。"""
        series = pd.Series([0.0, 2.0])
        assert qfq_ratio_series(series, ref="first") is None

        pl_df = pl.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "adj_factor": [0.0, 2.0]})
        ratio = pl_df.with_columns(qfq_ratio_expr("adj_factor", "ts_code", ref="first"))["qfq_ratio"]
        assert ratio.to_list() == pytest.approx([1.0, 1.0])

    def test_b2_all_null(self):
        """filled 全 null：pandas 返 None vs Polars 内联全 1.0。"""
        series = pd.Series([None, None])
        assert qfq_ratio_series(series) is None
        assert qfq_ratio_series(series, ref="first") is None

        pl_df = pl.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "adj_factor": [None, None]})
        ratio = pl_df.with_columns(qfq_ratio_expr("adj_factor", "ts_code", ref="last"))["qfq_ratio"]
        assert ratio.to_list() == pytest.approx([1.0, 1.0])

    @pytest.mark.parametrize("ref", ["last", "first"])
    def test_b3_all_identical(self, ref: str):
        """全部因子相同（优化短路）：pandas 返 None vs Polars 返全 1.0。"""
        series = pd.Series([5.0, 5.0, 5.0])
        assert qfq_ratio_series(series, ref=ref) is None

        pl_df = pl.DataFrame({"ts_code": ["000001.SZ"] * 3, "adj_factor": [5.0, 5.0, 5.0]})
        ratio = pl_df.with_columns(qfq_ratio_expr("adj_factor", "ts_code", ref=ref))["qfq_ratio"]
        assert ratio.to_list() == pytest.approx([1.0, 1.0, 1.0])
