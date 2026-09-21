"""OSS-05：技术指标 pandas/Polars 双实现等价性固化测试。

背景（reviews/开源组件使用检视报告.md §6）：RSI/MACD/KDJ 各有两份实现——
pandas 侧 ``get_rsi``/``get_macd``/``get_kdj``（末值快速路径）与 Polars 侧
``get_rsi_expr``/``get_macd_expr``/``get_kdj_expr``（表达式工厂）。本测试将
两侧的等价域与分叉面固化为显式断言，防止未声明的口径漂移。

核心结论（诊断探针实证，全部在 allclose(rtol=1e-9, atol=1e-12) 内等价）：
- MACD dif/dea 全场景等价（含短序列 n=8，实测 max|Δ|≤3e-13 量级）；
- KDJ k/d/j 在 random/up/down/short 场景等价；
- RSI 预热期后可比区 [period:] 等价（limit_head 一字板段两侧均以 50 相遇）。

4 处边界分叉（组 B 固化，R21 存量缺陷现状固化、非目标契约）：
- B1 RSI 预热期：pd ``fillna(50)`` 全序列（含 ewm 种子污染值）vs
  pl ``min_samples=period`` 前 period 根 null（真实「未知」，不伪装）。
- B2 MACD hist 倍率：pd ``hist=dif-dea`` vs pl ``macd=(dif-dea)×2``。
  收敛方向：pl ×2 为国内行情软件通行口径（正本），收敛时 pd 侧改 ×2
  并同步更新本断言。
- B3 KDJ 全横盘：pd rsv=0/0=NaN → k/d/j 全 NaN vs pl ``fill_nan(50)`` → 全 50。
- B4 KDJ 连续一字板（恒定价格段，整窗同价触发 rsv=0/0）：pd 侧 pandas ewm
  「头部 NaN 输入 → NaN 输出；中尾部 NaN 输入 → 延续值」vs pl 侧「填 50 递推」。
  B4a 头部注入经 com=2 EWM 按 (2/3)^i 衰减，末值残余分叉≈8e-10（仅固化
  衰减收敛行为）；峰值在 k[9]。B4b 尾部单点注入不衰减，末值真分叉。

消费面事实（收敛指引）：
- ``get_macd``/``get_kdj`` 唯一产品调用方 strategies/ai_mixin.py:1535-1536
  （60 根历史窗口）；KDJ flat 时 NaN 以 "k: nan" 注入 AI prompt（ai_mixin.py:1541）。
- ``get_rsi`` 零产品调用方；``get_kdj_expr`` 零生产消费方（仅测试引用）。
- 收敛（统一到 Polars 正本）时须同步更新：B1/B2/B3/B4 全部断言与组 C 对照。

衍生任务登记（本测试范围外，独立处理）：
1. qfq 双实现（qfq_ratio_series vs qfq_ratio_expr）交叉等价性零覆盖，需独立测试。
2. KDJ flat NaN 经 ai_mixin 注入 AI prompt "k: nan" 的 prompt 质量缺陷。
3. get_rsi 零产品调用方，评估删除。
4. KDJ 连续一字板分叉收敛（pd NaN-跳过 vs pl 50-填充）。

适用域说明：
- 无 NaN 输入、无 adj_factor（QFQ 路径不触发，pd 侧 ``_get_qfq_df`` 原样返回）。
- pandas 侧为算法重建序列：``get_*`` 不暴露序列仅返回末值，序列级断言只能
  重建镜像；重建 helper 逐行镜像 get_* 内部算法，get_* 算法变更时须同步更新
  （此为固化契约的一部分）。
- 组 A 末值采用三源互证（接口末值 vs 重建末值 vs expr 末值，allclose），
  绝对值不 pin（跨平台浮点稳健）；仅分叉契约处（B4/C1'）pin 绝对值
  （pytest.approx rel=1e-9）。
- B4a 分叉区超 rtol 点数为种子/平台依赖值（实测 k=50/51、d/j=51/51），
  断言用下限阈值（≥49/51），非稳定契约。
"""

import numpy as np
import pandas as pd
import polars as pl
import pytest

from utils.technical_analysis import TechnicalAnalysis

pytestmark = pytest.mark.unit

RTOL = 1e-9
ATOL = 1e-12

RSI_PERIOD = 6
MACD_FAST, MACD_SLOW, MACD_SIGN = 12, 26, 9
KDJ_N, KDJ_M1, KDJ_M2 = 9, 3, 3


# ---------------------------------------------------------------------------
# 场景生成（每场景独立固定种子，与测试执行顺序无关）
# ---------------------------------------------------------------------------


def _ohl(close, rng):
    high = close * (1 + np.abs(rng.normal(0, 0.005, len(close))))
    low = close * (1 - np.abs(rng.normal(0, 0.005, len(close))))
    return pd.DataFrame({"close": close, "high": high, "low": low})


def _make_random(n, seed):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    return _ohl(close, rng)


def _make_up(n, seed):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(np.abs(rng.normal(0.01, 0.005, n))))
    return _ohl(close, rng)


def _make_down(n, seed):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(-np.cumsum(np.abs(rng.normal(0.01, 0.005, n))))
    return _ohl(close, rng)


def _make_flat(n):
    close = np.full(n, 100.0)
    return pd.DataFrame({"close": close, "high": close.copy(), "low": close.copy()})


def _make_limit_head(n=60, n_limit=9, seed=51):
    """前 n_limit 根恒定价格（连续一字板），其余随机游走。

    「一字板」= 恒定价格段（high=low=close=const）：整窗同价才触发 rsv=0/0；
    递变价格（真实连续涨停逐日抬价）不会触发，不构成本场景。
    """
    rng = np.random.default_rng(seed)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n - n_limit)))
    close = np.concatenate([np.full(n_limit, 100.0), walk])
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    high[:n_limit] = 100.0
    low[:n_limit] = 100.0
    return pd.DataFrame({"close": close, "high": high, "low": low})


def _make_limit_tail(n=60, n_limit=9, seed=52):
    """后 n_limit 根恒定价格（连续一字板，涨停价=末根游走价×1.1），其余随机游走。"""
    rng = np.random.default_rng(seed)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n - n_limit)))
    p1 = walk[-1] * 1.1
    close = np.concatenate([walk, np.full(n_limit, p1)])
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    high[n - n_limit :] = p1
    low[n - n_limit :] = p1
    return pd.DataFrame({"close": close, "high": high, "low": low})


# ---------------------------------------------------------------------------
# pandas 算法重建（逐行镜像 get_* 内部算法；get_* 算法变更时须同步更新）
# ---------------------------------------------------------------------------


def _rsi_pd_series(close, period):
    delta = close.diff()
    up, down = TechnicalAnalysis._split_delta(delta)
    ma_up = up.ewm(com=period - 1, adjust=False).mean()
    ma_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = np.where(ma_down == 0, np.where(ma_up == 0, np.nan, np.inf), ma_up / ma_down)
    rsi = 100 - (100 / (1 + rs))
    return pd.Series(rsi, index=close.index).fillna(50)


def _macd_pd_series(close, fast, slow, sign):
    exp1 = close.ewm(span=fast, adjust=False).mean()
    exp2 = close.ewm(span=slow, adjust=False).mean()
    dif = exp1 - exp2
    dea = dif.ewm(span=sign, adjust=False).mean()
    return {"dif": dif, "dea": dea, "hist": dif - dea}  # pandas 版无 ×2


def _kdj_pd_series(df, n, m1, m2):
    low_list = df["low"].rolling(window=n, min_periods=n).min()
    low_list = low_list.fillna(value=df["low"].expanding().min())
    high_list = df["high"].rolling(window=n, min_periods=n).max()
    high_list = high_list.fillna(value=df["high"].expanding().max())
    rsv = (df["close"] - low_list) / (high_list - low_list) * 100
    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    return {"k": k, "d": d, "j": 3 * k - 2 * d, "rsv": rsv}


# ---------------------------------------------------------------------------
# Polars expr 执行
# ---------------------------------------------------------------------------


def _pl_rsi(df, period=RSI_PERIOD):
    return (
        pl.from_pandas(df[["close"]].reset_index(drop=True))
        .with_columns(TechnicalAnalysis.get_rsi_expr("close", period=period, alias="rsi"))
        .select("rsi")
        .to_series()
        .to_pandas()
    )


def _pl_macd(df, fast=MACD_FAST, slow=MACD_SLOW, sign=MACD_SIGN):
    return (
        pl.from_pandas(df[["close"]].reset_index(drop=True))
        .with_columns(TechnicalAnalysis.get_macd_expr("close", fast=fast, slow=slow, sign=sign))
        .unnest("macd_struct")
        .to_pandas()
    )


def _pl_kdj(df, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2):
    return (
        pl.from_pandas(df[["high", "low", "close"]].reset_index(drop=True))
        .with_columns(TechnicalAnalysis.get_kdj_expr("high", "low", "close", n=n, m1=m1, m2=m2))
        .unnest("kdj_struct")
        .to_pandas()
    )


def _rtol_exceed_count(pd_series, pl_series):
    """序列相对差超 RTOL 的点数（与诊断探针同口径）。"""
    a = pd_series.to_numpy(dtype=float)
    b = pl_series.to_numpy(dtype=float)
    return int((np.abs(a - b) > RTOL * np.maximum(np.abs(a), 1e-12)).sum())


# ---------------------------------------------------------------------------
# 组 A：数值等价（适用域：无 NaN 输入、无 adj_factor/QFQ 路径）
# ---------------------------------------------------------------------------


class TestSeriesEquivalence:
    @pytest.mark.parametrize(
        ("make", "seed"),
        [(_make_random, 42), (_make_up, 43), (_make_down, 44)],
        ids=["random", "up", "down"],
    )
    def test_trend_scenarios_rsi_macd_kdj_equivalent(self, make, seed):
        df = make(200, seed)

        pd_rsi, plr = _rsi_pd_series(df["close"], RSI_PERIOD), _pl_rsi(df)
        assert np.allclose(pd_rsi.iloc[RSI_PERIOD:], plr.iloc[RSI_PERIOD:], rtol=RTOL, atol=ATOL)

        m_pd, mpl = _macd_pd_series(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGN), _pl_macd(df)
        assert np.allclose(m_pd["dif"], mpl["dif"], rtol=RTOL, atol=ATOL)
        assert np.allclose(m_pd["dea"], mpl["dea"], rtol=RTOL, atol=ATOL)

        k_pd, kpl = _kdj_pd_series(df, KDJ_N, KDJ_M1, KDJ_M2), _pl_kdj(df)
        for col in ("k", "d", "j"):
            assert np.allclose(k_pd[col], kpl[col], rtol=RTOL, atol=ATOL)

    def test_flat_scenario_rsi_macd_equivalent(self):
        """flat：RSI 两侧恒 50（pd 全序列 / pl 预热期后）；MACD 两侧恒 0。

        KDJ 在 flat 下无等价域（pd 全 NaN vs pl 全 50），由组 B B3 固化。
        """
        df = _make_flat(200)

        pd_rsi, plr = _rsi_pd_series(df["close"], RSI_PERIOD), _pl_rsi(df)
        assert bool((pd_rsi == 50).all())
        assert plr.iloc[:RSI_PERIOD].isna().all()
        assert bool((plr.iloc[RSI_PERIOD:] == 50).all())

        m_pd, mpl = _macd_pd_series(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGN), _pl_macd(df)
        assert bool((m_pd["dif"].abs() < 1e-12).all())
        assert bool((mpl["macd"].abs() < 1e-12).all())

    @pytest.mark.parametrize(
        "df_maker",
        [lambda: _make_limit_head(60, 9, 51), lambda: _make_limit_tail(60, 9, 52)],
        ids=["head", "tail"],
    )
    def test_limit_scenarios_rsi_macd_equivalent(self, df_maker):
        """连续一字板场景：RSI 仅用 close（一字板段两侧均以 50 相遇）、MACD 不受
        high/low 影响，均保持等价；KDJ 分叉由组 B B4 固化。"""
        df = df_maker()

        pd_rsi, plr = _rsi_pd_series(df["close"], RSI_PERIOD), _pl_rsi(df)
        assert np.allclose(pd_rsi.iloc[RSI_PERIOD:], plr.iloc[RSI_PERIOD:], rtol=RTOL, atol=ATOL)

        m_pd, mpl = _macd_pd_series(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGN), _pl_macd(df)
        assert np.allclose(m_pd["dif"], mpl["dif"], rtol=RTOL, atol=ATOL)
        assert np.allclose(m_pd["dea"], mpl["dea"], rtol=RTOL, atol=ATOL)

    def test_short_scenario_series_equivalent(self):
        """n=8 短序列：RSI 可比区仅 2 点（pl 前 period 根 null 剔除后）；
        MACD n=8 < slow+2 无满窗语义但两侧 EWM 递推等价；KDJ n=8 < 9 时
        pd 走 expanding 回填、pl min_samples=1 窗口同为 [0..i]，等价。"""
        df = _make_random(8, 45)

        pd_rsi, plr = _rsi_pd_series(df["close"], RSI_PERIOD), _pl_rsi(df)
        assert len(pd_rsi.iloc[RSI_PERIOD:]) == 2
        assert np.allclose(pd_rsi.iloc[RSI_PERIOD:], plr.iloc[RSI_PERIOD:], rtol=RTOL, atol=ATOL)

        m_pd, mpl = _macd_pd_series(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGN), _pl_macd(df)
        assert np.allclose(m_pd["dif"], mpl["dif"], rtol=RTOL, atol=ATOL)
        assert np.allclose(m_pd["dea"], mpl["dea"], rtol=RTOL, atol=ATOL)

        k_pd, kpl = _kdj_pd_series(df, KDJ_N, KDJ_M1, KDJ_M2), _pl_kdj(df)
        for col in ("k", "d", "j"):
            assert np.allclose(k_pd[col], kpl[col], rtol=RTOL, atol=ATOL)

    def test_random_interface_last_values_match_expr(self):
        """接口末值三源互证：get_* 末值 == 重建末值 == expr 末值（allclose，
        绝对值不 pin；MACD hist 末值与 pl macd 末值按 B2 倍率折算后互证）。"""
        df = _make_random(200, 42)

        pd_rsi, plr = _rsi_pd_series(df["close"], RSI_PERIOD), _pl_rsi(df)
        assert TechnicalAnalysis.get_rsi(df, period=RSI_PERIOD) == pytest.approx(plr.iloc[-1], rel=RTOL)
        assert TechnicalAnalysis.get_rsi(df, period=RSI_PERIOD) == pytest.approx(pd_rsi.iloc[-1], rel=RTOL)

        m_pd, mpl = _macd_pd_series(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGN), _pl_macd(df)
        _, macd_last, hist_last = TechnicalAnalysis.get_macd(df)
        assert macd_last == pytest.approx(mpl["dif"].iloc[-1], rel=RTOL)
        assert macd_last == pytest.approx(m_pd["dif"].iloc[-1], rel=RTOL)
        assert 2 * hist_last == pytest.approx(mpl["macd"].iloc[-1], rel=RTOL)

        k_pd, kpl = _kdj_pd_series(df, KDJ_N, KDJ_M1, KDJ_M2), _pl_kdj(df)
        _, curr_k, curr_d, curr_j = TechnicalAnalysis.get_kdj(df)
        for got, pd_s, pl_s in (
            (curr_k, k_pd["k"], kpl["k"]),
            (curr_d, k_pd["d"], kpl["d"]),
            (curr_j, k_pd["j"], kpl["j"]),
        ):
            assert got == pytest.approx(pd_s.iloc[-1], rel=RTOL)
            assert got == pytest.approx(pl_s.iloc[-1], rel=RTOL)


# ---------------------------------------------------------------------------
# 组 B：边界语义固化（R21 存量缺陷现状固化、非目标契约；断言两侧各自语义）
# ---------------------------------------------------------------------------


class TestBoundaryDivergenceSolidified:
    def test_b1_rsi_warmup_fillna50_vs_null(self):
        """B1 RSI 预热期：pd fillna(50) 全序列（含 ewm 种子污染值）vs pl 前
        period 根 null（真实「未知」）。收敛时（统一 pl 正本）须同步更新。"""
        df = _make_random(200, 42)
        pd_rsi, plr = _rsi_pd_series(df["close"], RSI_PERIOD), _pl_rsi(df)
        assert plr.iloc[:RSI_PERIOD].isna().all()
        assert not pd_rsi.iloc[:RSI_PERIOD].isna().any()
        assert pd_rsi.iloc[0] == 50.0

    def test_b2_macd_hist_factor_two(self):
        """B2 MACD hist 倍率：pd hist=dif-dea vs pl macd=(dif-dea)×2（恒 2 倍、
        符号一致）。收敛方向：pl ×2 为国内行情软件通行口径（正本），收敛时
        pd 侧改 ×2 并同步更新本断言。flat 场景 hist 恒 0（0×2=0 平凡）不纳入。"""
        scenarios = [
            ("random", _make_random(200, 42)),
            ("up", _make_up(200, 43)),
            ("down", _make_down(200, 44)),
            ("limit_head", _make_limit_head(60, 9, 51)),
            ("limit_tail", _make_limit_tail(60, 9, 52)),
        ]
        for name, df in scenarios:
            m_pd = _macd_pd_series(df["close"], MACD_FAST, MACD_SLOW, MACD_SIGN)
            mpl = _pl_macd(df)
            assert np.allclose(2 * m_pd["hist"], mpl["macd"], rtol=RTOL, atol=ATOL), name

    def test_b3_kdj_flat_all_nan_vs_all_fifty(self):
        """B3 KDJ 全横盘：pd rsv=0/0=NaN → k/d/j 全 NaN（flat 时 NaN 经
        ai_mixin.py:1541 以 "k: nan" 注入 AI prompt，见衍生任务 2）vs
        pl fill_nan(50) → k=d=j=50。收敛时须同步更新。"""
        df = _make_flat(200)
        k_pd, kpl = _kdj_pd_series(df, KDJ_N, KDJ_M1, KDJ_M2), _pl_kdj(df)
        for col in ("k", "d", "j"):
            assert k_pd[col].isna().all(), col
            assert bool((kpl[col] == 50).all()), col

    def test_b4a_kdj_head_limit_boards(self):
        """B4a 头部 9 根连续一字板（n=60）：pd 头部 NaN 输入 → k/d[:9] 全 NaN
        （递推从 k[9]=rsv[9] 重新 seed）vs pl 填 50 递推（k/d[:9] 全 50）。
        分叉峰值在 k[9]；此后按 (2/3)^i 衰减，末值残余分叉≈8e-10（仅固化
        衰减收敛行为，非等价断言）。点数为种子/平台依赖值，断言用下限阈值。"""
        df = _make_limit_head(60, 9, 51)
        k_pd, kpl = _kdj_pd_series(df, KDJ_N, KDJ_M1, KDJ_M2), _pl_kdj(df)

        # 结构：两侧头部各自语义
        assert k_pd["k"].iloc[:9].isna().all()
        assert k_pd["d"].iloc[:9].isna().all()
        assert bool((kpl["k"].iloc[:9] == 50).all())
        assert bool((kpl["d"].iloc[:9] == 50).all())

        # 峰值 k[9]：pd=rsv[9]（重新 seed），pl=2/3×50+1/3×rsv[9]（携带 50 历史）
        assert k_pd["k"].iloc[9] == pytest.approx(19.815235159250406, rel=RTOL)
        assert kpl["k"].iloc[9] == pytest.approx(39.938411719750135, rel=RTOL)
        rel_peak = abs(k_pd["k"].iloc[9] - kpl["k"].iloc[9]) / max(abs(k_pd["k"].iloc[9]), 1e-12)
        assert rel_peak > 0.5  # 实测 101.55%

        # 分叉区 [9:]：超 rtol 点数下限（实测 k=50/51、d=51/51、j=51/51）
        for col in ("k", "d", "j"):
            assert _rtol_exceed_count(k_pd[col].iloc[9:], kpl[col].iloc[9:]) >= 49, col

        # 末值：衰减后残余分叉≈8e-10，两侧各自 pin（仅固化衰减收敛行为）
        assert k_pd["k"].iloc[-1] == pytest.approx(39.51971447153434, rel=RTOL)
        assert kpl["k"].iloc[-1] == pytest.approx(39.51971450309408, rel=RTOL)

    def test_b4b_kdj_tail_limit_boards(self):
        """B4b 尾部 9 根连续一字板（n=60）：仅 rsv[59]=0/0=NaN（窗口 51-59 整段
        同价）。pandas ewm 中尾部 NaN 输入输出延续值（k[59] 非 NaN）——这正是
        末值数值分叉的前提；pl 侧 rsv[59] 填 50 递推。单点注入不衰减，
        末值真分叉。d 由 k 二阶 ewm 传导，分叉收窄（实测 5.59%），仅两侧 pin。"""
        df = _make_limit_tail(60, 9, 52)
        k_pd, kpl = _kdj_pd_series(df, KDJ_N, KDJ_M1, KDJ_M2), _pl_kdj(df)

        # 前提：整窗同价触发 0/0；pandas ewm 中尾 NaN 延续（非 NaN 输出）
        assert np.isnan(k_pd["rsv"].iloc[59])
        assert not np.isnan(k_pd["k"].iloc[59])

        # 末值两侧各自 pin + 相对差下限（k 实测 16.60%、j 实测 37.95%）
        pd_k, pl_k = float(k_pd["k"].iloc[59]), float(kpl["k"].iloc[59])
        pd_d, pl_d = float(k_pd["d"].iloc[59]), float(kpl["d"].iloc[59])
        pd_j, pl_j = float(k_pd["j"].iloc[59]), float(kpl["j"].iloc[59])
        assert pd_k == pytest.approx(99.60144246218978, rel=RTOL)
        assert pl_k == pytest.approx(83.06762830812652, rel=RTOL)
        assert pd_d == pytest.approx(98.5684645973902, rel=RTOL)
        assert pl_d == pytest.approx(93.05719321270243, rel=RTOL)
        assert pd_j == pytest.approx(101.66739819178895, rel=RTOL)
        assert pl_j == pytest.approx(63.08849849897467, rel=RTOL)
        assert abs(pd_k - pl_k) / max(abs(pd_k), 1e-12) > 0.10
        assert abs(pd_j - pl_j) / max(abs(pd_j), 1e-12) > 0.30

        # 接口一致性：get_kdj 末值与 pd 重建末值同源（互证镜像未漂移）
        _, curr_k, curr_d, curr_j = TechnicalAnalysis.get_kdj(df)
        assert curr_k == pytest.approx(pd_k, rel=RTOL)
        assert curr_d == pytest.approx(pd_d, rel=RTOL)
        assert curr_j == pytest.approx(pd_j, rel=RTOL)


# ---------------------------------------------------------------------------
# 组 C：跨实现分叉面（pd 接口哨兵 vs pl expr 仍产出；纯 pd 哨兵行为已由
# test_technical_analysis.py 覆盖，此处只固化两侧对照）
# ---------------------------------------------------------------------------


class TestCrossImplSentinelFace:
    def test_c1_rsi_n_equals_period_sentinel_vs_all_null(self):
        """C1 n=6(=period)：pd 接口 len<period+1 → 哨兵 50.0 vs pl expr
        min_samples=period（仅 5 个非 null delta）→ 全 null。"""
        df = _make_random(6, 46)
        assert TechnicalAnalysis.get_rsi(df, period=RSI_PERIOD) == 50.0
        assert _pl_rsi(df).isna().all()

    def test_c1_prime_rsi_short_boundary_real_value(self):
        """C1' n=8 边界：len=8 > period+1=7 → get_rsi 返回真实值（非哨兵）。
        注：哨兵 50.0 与真实 50.0 不可区分是 R21 已知问题（缺失值伪装），
        本例 pin 的是固定种子下的确定性真实值。"""
        df = _make_random(8, 45)
        assert TechnicalAnalysis.get_rsi(df, period=RSI_PERIOD) == pytest.approx(57.531187188849444, rel=RTOL)

    def test_c2_macd_short_sentinel_vs_expr_output(self):
        """C2 n=8：pd 接口 len<slow+2=28 → ("UNKNOWN", 0, 0) vs pl expr
        min_samples=0 → 全数值序列。"""
        df = _make_random(8, 45)
        assert TechnicalAnalysis.get_macd(df) == ("UNKNOWN", 0, 0)
        mpl = _pl_macd(df)
        assert len(mpl) == 8
        assert bool(mpl["macd"].notna().all())

    def test_c3_kdj_short_sentinel_vs_expr_output(self):
        """C3 n=8：pd 接口 len<9 → ("UNKNOWN", 0, 0, 0) vs pl expr
        rolling min_samples=1 → 全数值序列。"""
        df = _make_random(8, 45)
        assert TechnicalAnalysis.get_kdj(df) == ("UNKNOWN", 0, 0, 0)
        kpl = _pl_kdj(df)
        assert len(kpl) == 8
        assert bool(kpl["k"].notna().all())
