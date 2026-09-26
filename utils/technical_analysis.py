import numpy as np
import pandas as pd

from core.i18n import I18n
from utils.qfq import qfq_ratio_series


class TechnicalAnalysis:
    """
    Calculates technical indicators for AI Feature Engineering.
    """

    @staticmethod
    def _get_qfq_df(df):
        """
        Calculate Forward Adjusted Prices (QFQ) if adj_factor is present.
        Adjusts open, high, low, close.
        Target: Normalize to the LATEST available date in the dataframe.
        """
        if df is None or df.empty or "adj_factor" not in df.columns:
            return df

        try:
            if "trade_date" in df.columns:
                df_adj = df.sort_values("trade_date").copy()
            else:
                df_adj = df.copy()

            ratio = qfq_ratio_series(df_adj["adj_factor"])
            if ratio is None:
                return df

            df_adj["close"] = df_adj["close"] * ratio
            df_adj["high"] = df_adj["high"] * ratio
            df_adj["low"] = df_adj["low"] * ratio
            df_adj["open"] = df_adj["open"] * ratio
            # Keep volume on the same basis as price after QFQ.
            safe_ratio = ratio.where(ratio > 0, np.nan)
            for vol_col in ("vol", "volume"):
                if vol_col in df_adj.columns:
                    df_adj[vol_col] = (df_adj[vol_col] / safe_ratio).where(safe_ratio.notna(), df_adj[vol_col])

            return df_adj
        except (ValueError, TypeError, KeyError):
            return df

    @staticmethod
    def get_macd(df, fast=12, slow=26, sign=9):
        """Calculate MACD, Signal, Hist (using QFQ).

        OSS-05 收敛：唯一正本为 Polars get_macd_expr。收敛前本方法为独立 Pandas
        实现，柱状图口径为 ``hist = dif - dea``（未 ×2），与策略层
        ``macd = (dif - dea) × 2`` 差 2 倍（D3）。现委托 Polars 正本计算并
        返回末值，第三位返回值变为 ×2 的 macd 柱（B2 收敛方向，国内行情软件
        通行口径）。
        """
        if df is None or len(df) < slow + 2:
            return "UNKNOWN", 0, 0

        # Use Adjusted Prices
        df_calc = TechnicalAnalysis._get_qfq_df(df)

        import polars as pl

        result = (
            pl.from_pandas(df_calc[["close"]])
            .lazy()
            .select(TechnicalAnalysis.get_macd_expr(fast=fast, slow=slow, sign=sign))
            .unnest("macd_struct")
            .collect()
        )
        curr_macd = result["macd"][-1]
        curr_dif = result["dif"][-1]
        # 预热期 null 是真实「未知」（R21），显式返回 None 而非填充业务合法值
        if curr_macd is None or curr_dif is None:
            return "UNKNOWN", None, None
        prev_macd = result["macd"][-2]

        status = "NEUTRAL"
        if prev_macd is not None and prev_macd < 0 and curr_macd > 0:
            status = "GOLDEN_CROSS"
        elif prev_macd is not None and prev_macd > 0 and curr_macd < 0:
            status = "DEATH_CROSS"
        elif curr_macd > 0:
            status = "BULLISH"
        else:
            status = "BEARISH"

        return status, float(curr_dif), float(curr_macd)

    @staticmethod
    def get_kdj(df, n=9, m1=3, m2=3):
        """Calculate KDJ (using QFQ)。

        OSS-05 收敛：唯一正本为 Polars get_kdj_expr。收敛前本方法为独立 Pandas
        实现，一字板/全横盘（hhv==llv → rsv=0/0）时 k/d/j 为 NaN，经 ai_mixin
        以 "k: nan" 注入 AI prompt（D1），且 NaN 比较全为 False 导致静默判为
        NEUTRAL。现委托 Polars 正本计算后取末值，边界语义（整窗同价取中性 50、
        预热期前 n−1 根为 null）统一由 get_kdj_expr 承载。

        本方法只取末值，故其 None 分支的真实可达路径是**末窗含缺失价**（末 n 窗内
        high/low 的非空计数 < n → RSV 为 null → 末值为真实未知），而非「预热期」。
        ``len(df) < n`` 时返回 ``("UNKNOWN", 0, 0, 0)`` 为既有哨兵语义，本次不改。
        """
        if df is None or len(df) < n:
            return "UNKNOWN", 0, 0, 0

        # Use Adjusted Prices
        df_calc = TechnicalAnalysis._get_qfq_df(df)

        import polars as pl

        result = (
            pl.from_pandas(df_calc[["high", "low", "close"]])
            .lazy()
            .select(TechnicalAnalysis.get_kdj_expr(n=n, m1=m1, m2=m2))
            .unnest("kdj_struct")
            .collect()
        )
        curr_k, curr_d, curr_j = (result[c][-1] for c in ("k", "d", "j"))
        # R21：末窗含缺失价 → 末值为真实「未知」，显式返回 None，不填充数值
        if curr_k is None or curr_d is None or curr_j is None:
            return "UNKNOWN", None, None, None

        status = "NEUTRAL"
        if curr_k > 80:
            status = "OVERBOUGHT"
        elif curr_k < 20:
            status = "OVERSOLD"

        return status, float(curr_k), float(curr_d), float(curr_j)

    @staticmethod
    def calculate_rsi_pandas(close: pd.Series, period: int = 14) -> pd.Series:
        """
        使用 Polars 计算 RSI 序列并转回 Pandas（SC-05 合一：唯一正本为 Polars get_rsi_expr）。

        此方法返回完整的 RSI 序列，用于后续分析：
        - 连续超卖天数
        - 恐慌速跌偏离度
        - 超卖钝化检测

        SC-05: 合一前本方法为独立 Pandas 实现（min_periods=period），与 Polars 版
        （min_samples=0）口径不一致导致 EWM 种子污染与两套边界处理漂移。
        现改为调用 get_rsi_expr 计算后转回，预热期语义（前 period 根为 NaN/null）与
        边界语义（无涨有跌→0、无跌有涨→100、无涨无跌→NaN 缺失）由 Polars 唯一实现承载；
        R21：无涨无跌（全平盘）时 RSI 业务上无定义，以缺失而非中性 50 表示。
        （D7：独立 pandas 末值实现 get_rsi 已删除，本方法是 pandas 侧 RSI 的唯一入口。）

        Args:
            close: 收盘价序列（需按时间升序排列）
            period: RSI 周期（默认 14）

        Returns:
            RSI 序列（0-100），数据不足时返回空 Series
        """
        if close is None or len(close) < period + 1:
            return pd.Series(dtype=float)

        import polars as pl

        df = pd.DataFrame({"close": close})
        rsi = (
            pl.from_pandas(df)
            .lazy()
            .with_columns(TechnicalAnalysis.get_rsi_expr("close", period=period, alias="rsi"))
            .select("rsi")
            .collect()
            .to_series()
            .to_pandas()
        )
        # SC-05: Polars to_pandas 产生 RangeIndex，重赋原 close 的 index 保持调用方语义
        rsi.index = close.index
        return rsi

    @staticmethod
    def analyze_rsi_oversold_features(close: pd.Series, period: int = 14) -> dict:
        """
        分析 RSI 超卖特征，用于判断"黄金坑" vs "价值陷阱"。

        返回三个关键特征：
        1. consecutive_oversold_days: 连续超卖天数（衡量跌势持续性）
        2. days_since_healthy: 距上次多头状态的间隔天数（恐慌速跌 vs 阴跌耗损）
        3. stagnation_detected: 是否检测到超卖钝化（价格新低但 RSI 未新低）

        Args:
            close: 收盘价序列（需按时间升序排列）
            period: RSI 周期

        Returns:
            dict 包含特征值和描述文本
        """
        lookback_days = max(60, period + 20)
        if close is not None and len(close) > lookback_days:
            close = close.tail(lookback_days)

        rsi = TechnicalAnalysis.calculate_rsi_pandas(close, period)

        if rsi.empty or close is None or len(close) < max(20, period + 6):
            return {
                "consecutive_oversold_days": 0,
                "days_since_healthy": None,
                "stagnation_detected": False,
                "feature_text": "RSI 状态: 最近历史数据不足，暂不解读超卖形态",
            }

        current_rsi = rsi.iloc[-1]

        consecutive_days = 0
        oversold_mask = rsi < 30
        if oversold_mask.iloc[-1]:
            for i in range(len(oversold_mask) - 1, -1, -1):
                if oversold_mask.iloc[i]:
                    consecutive_days += 1
                else:
                    break

        healthy_mask = rsi > 50
        days_since_healthy = None
        if healthy_mask.any():
            last_healthy_idx = healthy_mask[healthy_mask].index[-1]
            days_since_healthy = len(rsi) - rsi.index.get_loc(last_healthy_idx) - 1  # type: ignore[union-attr]

        stagnation_detected = False
        recent_window = min(len(rsi), 20)
        if recent_window >= 20:
            recent_close = close.tail(recent_window)
            recent_rsi = rsi.tail(recent_window)

            is_price_new_low = recent_close.iloc[-1] <= recent_close.iloc[:-1].min()

            rsi_min = recent_rsi.min()
            if rsi_min > 0:
                rsi_deviation_pct = (current_rsi - rsi_min) / rsi_min * 100
                stagnation_detected = is_price_new_low and (rsi_deviation_pct > 5)

        healthy_text = f"近{len(rsi)}日内未回到多头状态(>50)"
        panic_str = ""
        if days_since_healthy is not None:
            panic_str = I18n.get("ai_panic_drop") if days_since_healthy <= 8 else I18n.get("ai_slow_decay")
            healthy_text = f"距上次多头状态(>50)已历经 {days_since_healthy} 天 {panic_str}"

        stagnation_str = I18n.get("ai_rsi_oversold_stagnation") if stagnation_detected else ""

        feature_text = f"近{len(rsi)}日观察: 已连续 {consecutive_days} 天处于超卖(<30)；{healthy_text}"
        if stagnation_str:
            feature_text += f" {stagnation_str}"

        return {
            "consecutive_oversold_days": consecutive_days,
            "days_since_healthy": days_since_healthy,
            "stagnation_detected": stagnation_detected,
            "feature_text": feature_text,
            "current_rsi": round(current_rsi, 2),
        }

    # ==========================
    # Polars Expression Factories
    # ==========================
    @staticmethod
    def get_rsi_expr(col_name="close", period=6, alias="rsi"):
        """
        Returns a Polars Expression for RSI calculation.
        Use with .over('ts_code') for grouped calculation.

        SC-05: min_samples=period 与 Pandas 版 calculate_rsi_pandas 的 min_periods=period
        对齐，消除 EWM 种子污染（新上市/次新股前 period 根不产出值）。
        本表达式只在「业务上确实无法计算 RSI」时产出 null（预热期数据不足、
        全平盘导致 0/0），是真实的「未知」，不 fill_null / fill_nan 伪装（R21）。
        下游须显式处理缺失——见 strategies/oversold_strategy.py 的
        ``.is_not_null()`` + 阈值过滤：无法计算 RSI 的标的既不算超跌也不算
        中性，按缺失显式排除，而非隐式依赖 null 比较被丢弃。
        """
        import polars as pl

        # standard RSI with ewm (matches Pandas ewm(com=period-1))
        # Polars: ewm_mean(com=...)

        delta = pl.col(col_name).diff()
        up = delta.clip(lower_bound=0)
        down = delta.clip(upper_bound=0).abs()

        roll_up = up.ewm_mean(com=period - 1, adjust=False, min_samples=period)
        roll_down = down.ewm_mean(com=period - 1, adjust=False, min_samples=period)

        rs = roll_up / roll_down
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # 分母为 0 的两种情形：
        # - roll_up > 0 且 roll_down == 0（只涨不跌）→ rs = inf，100/(1+inf) = 0 → RSI = 100，正确；
        # - roll_up == roll_down == 0（全平盘，无涨无跌）→ rs = 0/0 = NaN，RSI 业务上无定义。
        # R21: 无定义归一为 null（与预热期同为真实「未知」），不填 50 等业务上合法的中性值
        # （填 50 会让该标的落在「不超跌」的安全区而被静默排除，语义上是用合法值伪装缺失）。
        # 下游须显式处理缺失（见 get_rsi_expr docstring），不得再填充中性值。

        return rsi.fill_nan(None).alias(alias)

    @staticmethod
    def get_macd_expr(col_name="close", fast=12, slow=26, sign=9):
        """Returns a Polars Expression (struct) for MACD.

        预热期 null 是真实的「未知」，不 fill_null 伪装（R21，与 get_rsi_expr 一致）——
        下游 ``.filter(pl.col("macd") > 0)`` 对 null 返回 null 自然丢弃该行。
        macd 柱取 (dif-dea)×2，为国内行情软件通行口径（OSS-05 B2 正本）。
        min_samples 取窗口长度，消除 EWM 种子污染（新上市/次新股前 slow 根不产出
        值），与 get_rsi_expr 的 min_samples=period 对齐。
        """
        import polars as pl

        # EMA
        ema_fast = pl.col(col_name).ewm_mean(span=fast, adjust=False, min_samples=slow)
        ema_slow = pl.col(col_name).ewm_mean(span=slow, adjust=False, min_samples=slow)
        dif = ema_fast - ema_slow
        dea = dif.ewm_mean(span=sign, adjust=False, min_samples=sign)
        macd = (dif - dea) * 2  # Typical MACD histogram

        return pl.struct(
            [dif.alias("dif"), dea.alias("dea"), macd.alias("macd")],
        ).alias("macd_struct")

    @staticmethod
    def get_kdj_expr(high="high", low="low", close="close", n=9, m1=3, m2=3):
        """KDJ 表达式工厂（唯一正本）。

        预热期语义：``min_samples=n`` 使前 n−1 根因不足整窗而 RSV 为 null；null 在
        ``ewm_mean`` 中逐位置传播，故 k/d/j 前 n−1 根同步为 null（真实「未知」，
        不填充业务合法值，R21）。

        边界语义：整窗同价（一字板/全横盘）时 ``hhv==llv`` → RSV 为 0/0，该窗 RSV
        在业务上无定义，取中性 50；``fill_nan`` 只作用于 NaN，不触碰预热期 null。

        与行情软件的差异：行情软件以 K/D 初值 50 递推，本实现以「首根有效 RSV」
        播种，差异按 ``(2/3)^t`` 衰减（m1=3），预热期后收敛。
        """
        import polars as pl

        # RSV
        llv = pl.col(low).rolling_min(window_size=n, min_samples=n)
        hhv = pl.col(high).rolling_max(window_size=n, min_samples=n)

        rsv = (pl.col(close) - llv) / (hhv - llv) * 100
        # fill_nan：整窗同价（hhv==llv → 0/0）时该窗 RSV 业务上无定义，取中性 50
        # ——这是逐窗定义值，与「递归序列的种子初值」是两件事。不 fill_null：
        # 预热期（前 n−1 根不足整窗）为真实未知，伪装成 50 属 R21 违规。
        # 口径不一致（既有设计，保留不改）：RSI 对同类 0/0 取 null（真实未知），
        # KDJ 取中性 50；详见 docs/debt/known-technical-debt.md 的 P3-TA-OSS05-DualImpl。
        rsv = rsv.fill_nan(50)

        # K, D, J via EWM
        # Pandas KDJ uses .ewm(com=m1-1). Polars same.
        # Note: KDJ is recursive. K = 2/3*PreK + 1/3*RSV.
        # This is exactly EWM with alpha=1/3 => com=2.
        # m1=3 => com=2.

        k = rsv.ewm_mean(com=m1 - 1, adjust=False, min_samples=0)
        d = k.ewm_mean(com=m2 - 1, adjust=False, min_samples=0)
        j = 3 * k - 2 * d

        return pl.struct([k.alias("k"), d.alias("d"), j.alias("j")]).alias("kdj_struct")
