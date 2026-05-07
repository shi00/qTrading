import numpy as np
import pandas as pd

from ui.i18n import I18n


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
            valid_factors = df_adj["adj_factor"].dropna()
            if valid_factors.empty:
                return df

            latest_factor = valid_factors.iloc[-1]
            if latest_factor == 0:
                return df

            df_adj["adj_factor"] = df_adj["adj_factor"].ffill().fillna(latest_factor)

            if (df_adj["adj_factor"] == latest_factor).all():
                return df

            ratio = df_adj["adj_factor"] / latest_factor

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
        except Exception:
            return df

    @staticmethod
    def get_macd(df, fast=12, slow=26, sign=9):
        """
        Calculate MACD, Signal, Hist (using QFQ).
        """
        if df is None or len(df) < slow + 2:
            return "UNKNOWN", 0, 0

        # Use Adjusted Prices
        df_calc = TechnicalAnalysis._get_qfq_df(df)

        # Standard MACD
        exp1 = df_calc["close"].ewm(span=fast, adjust=False).mean()
        exp2 = df_calc["close"].ewm(span=slow, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=sign, adjust=False).mean()
        hist = macd - signal

        # Latest values
        curr_hist = hist.iloc[-1]
        prev_hist = hist.iloc[-2]

        status = "NEUTRAL"
        if prev_hist < 0 and curr_hist > 0:
            status = "GOLDEN_CROSS"
        elif prev_hist > 0 and curr_hist < 0:
            status = "DEATH_CROSS"
        elif curr_hist > 0:
            status = "BULLISH"
        else:
            status = "BEARISH"

        return status, macd.iloc[-1], hist.iloc[-1]

    @staticmethod
    def get_kdj(df, n=9, m1=3, m2=3):
        """
        Calculate KDJ (using QFQ).
        """
        if df is None or len(df) < n:
            return "UNKNOWN", 0, 0, 0

        # Use Adjusted Prices
        df_calc = TechnicalAnalysis._get_qfq_df(df)

        low_list = df_calc["low"].rolling(window=n, min_periods=n).min()
        low_list = low_list.fillna(value=df_calc["low"].expanding().min())
        high_list = df_calc["high"].rolling(window=n, min_periods=n).max()
        high_list = high_list.fillna(value=df_calc["high"].expanding().max())

        rsv = (df_calc["close"] - low_list) / (high_list - low_list) * 100

        k = rsv.ewm(com=m1 - 1, adjust=False).mean()
        d = k.ewm(com=m2 - 1, adjust=False).mean()
        j = 3 * k - 2 * d

        curr_k = k.iloc[-1]
        curr_d = d.iloc[-1]
        curr_j = j.iloc[-1]

        status = "NEUTRAL"
        if curr_k > 80:
            status = "OVERBOUGHT"
        elif curr_k < 20:
            status = "OVERSOLD"

        return status, curr_k, curr_d, curr_j

    @staticmethod
    def analyze_trend(df):
        """
        Simple MA trend analysis (using QFQ).
        """
        if df is None or len(df) < 20:
            return "UNKNOWN"

        # Use Adjusted Prices
        df_calc = TechnicalAnalysis._get_qfq_df(df)

        ma5 = df_calc["close"].rolling(window=5).mean().iloc[-1]
        ma20 = df_calc["close"].rolling(window=20).mean().iloc[-1]

        if ma5 > ma20:
            return "UP"
        return "DOWN"

    @staticmethod
    def get_rsi(df, period=6):
        """
        Calculate RSI (using QFQ).
        :param df: DataFrame with 'close' (and 'adj_factor' for QFQ)
        :param period: RSI period (default 6)
        :return: Last RSI value (float) or 50 if insufficient data
        """
        if df is None or len(df) < period + 1:
            return 50.0

        # Use Adjusted Prices
        df_calc = TechnicalAnalysis._get_qfq_df(df)

        # Calculate price changes
        delta = df_calc["close"].diff()

        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)

        # Use Wilder's Smoothing (alpha = 1/period)
        ma_up = up.ewm(com=period - 1, adjust=False).mean()
        ma_down = down.ewm(com=period - 1, adjust=False).mean()

        # Avoid division by zero: when ma_down=0, RSI=100
        rs = np.where(ma_down == 0, np.inf, ma_up / ma_down)
        rsi = 100 - (100 / (1 + rs))

        # Handle nan (e.g. initial window)
        rsi = pd.Series(rsi).fillna(50)

        return float(rsi.iloc[-1])

    @staticmethod
    def calculate_rsi_pandas(close: pd.Series, period: int = 14) -> pd.Series:
        """
        使用 Pandas 计算 RSI 序列（EMA 平滑方式，与 Polars 版本一致）。

        与 get_rsi() 不同，此方法返回完整的 RSI 序列，用于后续分析：
        - 连续超卖天数
        - 恐慌速跌偏离度
        - 超卖钝化检测

        Args:
            close: 收盘价序列
            period: RSI 周期（默认 14）

        Returns:
            RSI 序列（0-100），数据不足时返回空 Series
        """
        if close is None or len(close) < period + 1:
            return pd.Series(dtype=float)

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(upper=0)

        avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rs = rs.replace([np.inf, -np.inf], np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.fillna(50)
        rsi = rsi.clip(lower=0, upper=100)

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
            days_since_healthy = len(rsi) - rsi.index.get_loc(last_healthy_idx) - 1  # type: ignore

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
        """
        import polars as pl

        # standard RSI with ewm (matches Pandas ewm(com=period-1))
        # Polars: ewm_mean(com=...)

        delta = pl.col(col_name).diff()
        up = delta.clip(lower_bound=0)
        down = delta.clip(upper_bound=0).abs()

        roll_up = up.ewm_mean(com=period - 1, adjust=False, min_samples=0)
        roll_down = down.ewm_mean(com=period - 1, adjust=False, min_samples=0)

        rs = roll_up / roll_down
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # Handle division by zero (inf) -> 100?
        # Polars handles inf arithmetic usually?
        # If roll_down is 0, rs is inf. 100/(1+inf) is 0. 100-0 = 100. Correct.
        # But if both are 0? Nan.

        return rsi.fill_nan(50.0).fill_null(50.0).alias(alias)

    @staticmethod
    def get_macd_expr(col_name="close", fast=12, slow=26, sign=9):
        import polars as pl

        # EMA
        ema_fast = pl.col(col_name).ewm_mean(span=fast, adjust=False, min_samples=0)
        ema_slow = pl.col(col_name).ewm_mean(span=slow, adjust=False, min_samples=0)
        dif = ema_fast - ema_slow
        dea = dif.ewm_mean(span=sign, adjust=False, min_samples=0)
        macd = (dif - dea) * 2  # Typical MACD histogram

        return pl.struct(
            [
                dif.fill_null(0.0).alias("dif"),
                dea.fill_null(0.0).alias("dea"),
                macd.fill_null(0.0).alias("macd"),
            ],
        ).alias("macd_struct")

    @staticmethod
    def get_kdj_expr(high="high", low="low", close="close", n=9, m1=3, m2=3):
        import polars as pl

        # RSV
        llv = pl.col(low).rolling_min(
            window_size=n,
            min_samples=1,
        )  # min_samples not fully supported in old polars?
        # rolling_min in Polars usually requires window_size.
        # Handle dynamic window? No, just standard rolling.
        hhv = pl.col(high).rolling_max(window_size=n, min_samples=1)

        rsv = (pl.col(close) - llv) / (hhv - llv) * 100
        # Check div by zero
        rsv = rsv.fill_nan(50).fill_null(50)

        # K, D, J via EWM
        # Pandas KDJ uses .ewm(com=m1-1). Polars same.
        # Note: KDJ is recursive. K = 2/3*PreK + 1/3*RSV.
        # This is exactly EWM with alpha=1/3 => com=2.
        # m1=3 => com=2.

        k = rsv.ewm_mean(com=m1 - 1, adjust=False, min_samples=0)
        d = k.ewm_mean(com=m2 - 1, adjust=False, min_samples=0)
        j = 3 * k - 2 * d

        return pl.struct([k.alias("k"), d.alias("d"), j.alias("j")]).alias("kdj_struct")
