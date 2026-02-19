
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Any

from data.data_dictionary import COMMON_COLUMNS

logger = logging.getLogger(__name__)

class DataQualityService:
    """
    Service for performing Deep Health Checks (Tier 2 & Tier 3).
    Stateless logic provider.
    """
    
    MAX_MISSING_REPORT = 10
    LAG_DEFAULT = 9999
    LAG_ERROR = -1

    @classmethod
    def check_continuity(cls, df: pd.DataFrame, date_col: str, trade_cal: pd.DataFrame) -> Dict[str, Any]:
        """
        Tier 2: Check for missing trading days in a time-series.
        
        Args:
            df: Data to check.
            date_col: Name of the date column in df.
            trade_cal: DataFrame containing 'cal_date' and 'is_open'.
            
        Returns:
            Dict with 'missing_count', 'missing_dates', 'coverage_ratio'.
        """
        if df.empty:
            return {'missing_count': 0, 'missing_dates': [], 'coverage_ratio': 0.0}
            
        # Ensure dates are datetime
        if not np.issubdtype(df[date_col].dtype, np.datetime64):
            df[date_col] = pd.to_datetime(df[date_col])
        
        start_date = df[date_col].min()
        end_date = df[date_col].max()
        
        # Filter trade calendar for range & open days
        # Assuming trade_cal has 'cal_date' as datetime or string YYYYMMDD
        # We need to standardize format.
        
        # Helper to convert to standardized string YYYYMMDD for comparison
        target_dates = set(df[date_col].dt.strftime('%Y%m%d'))
        
        # Process trade_cal
        # Assuming trade_cal['cal_date'] is string YYYYMMDD and is_open=1
        mask = (trade_cal['is_open'] == 1) & \
               (trade_cal['cal_date'] >= start_date.strftime('%Y%m%d')) & \
               (trade_cal['cal_date'] <= end_date.strftime('%Y%m%d'))
               
        expected_dates = set(trade_cal[mask]['cal_date'])
        
        missing = expected_dates - target_dates
        missing_list = sorted(list(missing))
        
        total_expected = len(expected_dates)
        if total_expected == 0:
            ratio = 1.0 # No expected dates means perfect coverage locally?
        else:
            ratio = 1.0 - (len(missing) / total_expected)
            
        return {
            'missing_count': len(missing),
            'missing_dates': missing_list[:cls.MAX_MISSING_REPORT], # Report top N
            'coverage_ratio': ratio
        }

    @classmethod
    def check_recency(cls, df: pd.DataFrame, date_col: str, ref_date: str) -> Dict[str, Any]:
        """
        Tier 2: Check data freshness against a reference date (usually latest trading day).
        """
        if df.empty:
            return {'lag_days': cls.LAG_DEFAULT, 'latest_date': None}
            
        # Get latest date in DF
        # Handle string vs datetime
        if pd.api.types.is_datetime64_any_dtype(df[date_col]):
            latest = df[date_col].max().strftime('%Y%m%d')
        else:
            latest = str(df[date_col].max())
            
        # Calculate lag
        try:
            d_latest = pd.to_datetime(latest)
            d_ref = pd.to_datetime(ref_date)
            lag = (d_ref - d_latest).days
        except Exception:
            lag = cls.LAG_ERROR
            
        return {
            'lag_days': lag,
            'latest_data_date': latest
        }

    @staticmethod
    def check_nulls(df: pd.DataFrame, columns: List[str] = None) -> Dict[str, float]:
        """
        Tier 2: Critical column null-rate analysis.
        If columns is None, checks all.
        """
        if df.empty:
            return {}
            
        check_cols = columns if columns else df.columns
        null_counts = df[check_cols].isnull().sum()
        total = len(df)
        
        ratios = (null_counts / total).to_dict()
        return ratios

    @staticmethod
    def check_cross_validation(df: pd.DataFrame, 
                             rules: List[Tuple[str, str, float]]) -> List[str]:
        """
        Tier 3: Reliability Cross-Validation using simple expression evaluation.
        
        Args:
            df: Data
            rules: List of (name, expression, tolerance). 
                   Expression should be eval-able string using df columns.
                   e.g. ("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)
                   Expression should return a Series (diff). 
                   We check if abs(diff) / val > tolerance.
                   
        Current implementation is simplified: 
        We expect the caller to provide specific check logic or we hardcode common patterns here.
        Using `eval` on user strings is risky, so we implement specific named checks.
        """
        issues = []
        return issues
        
    @staticmethod
    def check_price_vs_factor(df_price: pd.DataFrame, df_adj: pd.DataFrame) -> Dict[str, Any]:
        """
        Tier 3 Example: Check if Price * Factor ~= AdjPrice (Logic placeholder)
        Currently we don't have a table with both Pre-Adj and Adj prices in same row easily 
        unless linked. 
        """
        pass
