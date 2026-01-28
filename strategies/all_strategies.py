from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    def __init__(self, name, description):
        self.name = name
        self.description = description

    @abstractmethod
    def filter(self, context):
        """
        Execute strategy logic.
        :param context: Dict containing 'screening_data' DataFrame with merged daily+financial data
        :return: Filtered DataFrame
        """
        pass

class ValueStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("价值投资", "标准：市盈率 5-20倍 | 市净率 0.5-3倍 | 股息率 > 2% (寻找低估值蓝筹)")

    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Drop rows with NaN in critical columns
        required_cols = ['pe_ttm', 'pb', 'dv_ttm']
        df = df.dropna(subset=[c for c in required_cols if c in df.columns])
        if df.empty:
            return pd.DataFrame()
        
        # Simple screening: PE 5-20, PB 0.5-3, Div Yield > 2%
        mask = (
            (df['pe_ttm'] > 5) & (df['pe_ttm'] < 20) &
            (df['pb'] > 0.5) & (df['pb'] < 3) &
            (df['dv_ttm'] > 2)
        )
        result = df[mask].copy()
        return result.sort_values('dv_ttm', ascending=False)

# --- 2. Growth Strategy ---
class GrowthStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("高成长策略", "标准：营收增长 > 20% | 净利增长 > 25% | ROE > 15%")

    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Use or_yoy (revenue YoY) and netprofit_yoy from financial_reports
        # Filter: revenue growth > 20%, profit growth > 25%, ROE > 15%
        mask = (
            (df['or_yoy'] > 20) &
            (df['netprofit_yoy'] > 25) &
            (df['roe'] > 15)
        )
        result = df[mask].copy()
        return result.sort_values('roe', ascending=False)

# --- 3. Dividend Strategy ---
class DividendStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("高股息策略", "标准：股息率(TTM) > 4% (防御性现金牛资产)")
        
    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # High dividend yield > 4%
        mask = (df['dv_ttm'] > 4)
        result = df[mask].copy()
        return result.sort_values('dv_ttm', ascending=False)

# --- 4. Technical Breakout ---
class TechnicalBreakoutStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("技术突破", "标准：当日涨幅 2-7% | 换手率 3-15% (放量活跃)")

    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Simplified: Today's gain 2-7%, high turnover
        # Full implementation would need historical data for MA comparison
        mask = (
            (df['pct_chg'] > 2) & 
            (df['pct_chg'] < 7) &
            (df['turnover_rate'] > 3) &
            (df['turnover_rate'] < 15)
        )
        result = df[mask].copy()
        return result.sort_values('pct_chg', ascending=False)

# --- 5. Northbound Capital ---
class NorthboundStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("北向资金", "标准：北向资金持股比例 > 5% (外资重仓股)")

    def filter(self, context):
        # This requires northbound holding data from cache
        df = context.get('northbound_data')
        if df is None or df.empty:
            # Fallback to screening_data with note
            return pd.DataFrame()
        
        # Filter high holding ratio > 5% and ensure A-Share code
        mask = (df['ratio'] > 5) & (df['ts_code'].astype(str).str.endswith(('.SH', '.SZ')))
        return df[mask].sort_values('ratio', ascending=False)

# --- 6. Oversold Rebound ---
class OversoldStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("超跌反弹", "标准：当日跌幅 > 3% | 市盈率 < 30 (短期错杀)")

    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Today's decline > 3% but PE still reasonable
        # Real implementation needs 20-day cumulative drop
        mask = (
            (df['pct_chg'] < -3) &
            (df['pe_ttm'] > 0) & (df['pe_ttm'] < 30)
        )
        result = df[mask].copy()
        return result.sort_values('pct_chg', ascending=True)

# --- 7. Institutional Buying (LHB) ---
class InstitutionalStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("龙虎榜机构", "标准：龙虎榜机构净买入 > 3000万 (主力资金抢筹)")

    def filter(self, context):
        # Requires top_list data
        lhb = context.get('top_list')
        if lhb is None or lhb.empty:
            return pd.DataFrame()
        
        # Filter for rows where data exists
        if 'net_amount' not in lhb.columns:
            return pd.DataFrame()
            
        # Logic:
        # 1. Net buying > 3000万 (30 million)
        # 2. Buying amount / Turnover > 10% (Indicates high institutional participation)
        
        # Keep non-NaN
        df = lhb.dropna(subset=['net_amount'])
        
        # 3000万 = 30000000 (unit in table might be 10000? Tushare top_list amount is in 10000 RMB usually, need to verify. 
        # Tushare doc: net_amount is "净成交额", unit "万元" usually? Let's assume 万.
        # Actually Tushare top_list amount is usually in "万元". So 3000万 = 3000.
        
        mask = (df['net_amount'] > 3000) 
        result = df[mask].copy()
        return result.sort_values('net_amount', ascending=False)

# --- 8. Shareholder Concentration ---
class ChipConcentrationStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("筹码集中 (暂不可用)", "股东户数大幅减少")

    def filter(self, context):
        # Placeholder: This requires quarterly shareholder data which is not in daily sync
        # Returning empty for now
        return pd.DataFrame()

# --- 9. Block Trade ---
class BlockTradeStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("大宗交易", "标准：大宗成交额 > 1000万 (关注主力吸筹)")

    def filter(self, context):
        block = context.get('block_trade')
        if block is None or block.empty:
            return pd.DataFrame()
            
        # Tushare block_trade: price, vol(万股), amount(万元), buyer, seller
        
        # Logic:
        # 1. Premium or near-price trade (price >= yesterday close... but we only have deal price)
        # 2. Large amount > 1000万 (1000)
        
        if 'amount' not in block.columns:
            return pd.DataFrame()
            
        df = block.dropna(subset=['amount'])
        mask = (df['amount'] > 1000)
        
        result = df[mask].copy()
        
        # Group by stock to sum up amounts if multiple deals
        if not result.empty:
            result = result.groupby('ts_code').agg({
                'amount': 'sum',
                'vol': 'sum',
                'price': 'mean' # Weighted avg ideal but this is simple
            }).reset_index()
            
        return result.sort_values('amount', ascending=False)

# --- 10. High Quality Cashflow ---
class CashFlowStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("现金流优质", "标准：资产负债率 < 50% | ROE > 10% (稳健经营)")
    
    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Low debt, high ROE
        mask = (
            (df['debt_to_assets'] < 50) &
            (df['roe'] > 10)
        )
        result = df[mask].copy()
        return result.sort_values('roe', ascending=False)

# --- 11. Low PE Large Cap ---
class LargePEStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("大盘低估", "标准：总市值 > 500亿 | 市盈率 < 15倍 (核心资产)")
    
    def filter(self, context):
        df = context.get('screening_data')
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Market cap > 500亿, PE < 15
        mask = (
            (df['total_mv'] > 5000000) &  # 万元 -> 500亿
            (df['pe_ttm'] > 0) & (df['pe_ttm'] < 15)
        )
        result = df[mask].copy()
        return result.sort_values('total_mv', ascending=False)


class StrategyManager:
    def __init__(self):
        self.strategies = {
            "value": ValueStrategy(),
            "growth": GrowthStrategy(),
            "dividend": DividendStrategy(),
            "tech_breakout": TechnicalBreakoutStrategy(),
            "northbound": NorthboundStrategy(),
            "oversold": OversoldStrategy(),
            "institutional": InstitutionalStrategy(),
            "chips": ChipConcentrationStrategy(),
            "block_trade": BlockTradeStrategy(),
            "cashflow": CashFlowStrategy(),
            "large_pe": LargePEStrategy(),
        }

    def get_strategy(self, key):
        return self.strategies.get(key)
    
    def get_all_names(self):
        return {k: v.name for k, v in self.strategies.items()}
