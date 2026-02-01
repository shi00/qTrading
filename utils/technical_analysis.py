import pandas as pd
import numpy as np

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
        if df is None or df.empty or 'adj_factor' not in df.columns:
            return df
            
        try:
            # Check if factors are valid
            latest_factor = df['adj_factor'].iloc[-1]
            if pd.isna(latest_factor) or latest_factor == 0:
                return df
                
            # If all factors are 1.0 or same, no need to adjust
            if (df['adj_factor'] == latest_factor).all():
                return df
                
            df_adj = df.copy()
            ratio = df_adj['adj_factor'] / latest_factor
            
            df_adj['close'] = df_adj['close'] * ratio
            df_adj['high'] = df_adj['high'] * ratio
            df_adj['low'] = df_adj['low'] * ratio
            df_adj['open'] = df_adj['open'] * ratio
            
            return df_adj
        except Exception as e:
            # Fallback to raw if calculation fails
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
        exp1 = df_calc['close'].ewm(span=fast, adjust=False).mean()
        exp2 = df_calc['close'].ewm(span=slow, adjust=False).mean()
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
            
        low_list = df_calc['low'].rolling(window=n, min_periods=n).min()
        low_list.fillna(value=df_calc['low'].expanding().min(), inplace=True)
        high_list = df_calc['high'].rolling(window=n, min_periods=n).max()
        high_list.fillna(value=df_calc['high'].expanding().max(), inplace=True)
        
        rsv = (df_calc['close'] - low_list) / (high_list - low_list) * 100
        
        k = rsv.ewm(com=m1-1, adjust=False).mean()
        d = k.ewm(com=m2-1, adjust=False).mean()
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
            
        ma5 = df_calc['close'].rolling(window=5).mean().iloc[-1]
        ma20 = df_calc['close'].rolling(window=20).mean().iloc[-1]
        
        if ma5 > ma20:
            return "UP"
        else:
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
        delta = df_calc['close'].diff()
        
        # Get gains (up) and losses (down)
        # up = delta.where(delta > 0, 0) # This keeps index
        # down = -delta.where(delta < 0, 0)
        
        # Standard RSI Logic (Wilder's Smoothing is ideal, but SMA is common substitution)
        # Let's use SMA for simplicity and speed as per plan, 
        # or ewm (Exponential) which approximates Wilder's if alpha=1/period.
        # Wilder's uses alpha = 1/n. Pandas ewm span=n uses alpha=2/(n+1), com=n-1 uses alpha=1/n.
        # So ewm(com=period-1, adjust=False) is Wilder's Smoothed.
        
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
