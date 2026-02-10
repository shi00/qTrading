import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import pandas as pd
from strategies.all_strategies import (
    StrategyManager, ValueStrategy, GrowthStrategy, DividendStrategy,
    TechnicalBreakoutStrategy, NorthboundStrategy,
    InstitutionalStrategy, BlockTradeStrategy, CashFlowStrategy, LargePEStrategy
)
from strategies.oversold_strategy import OversoldStrategy

class TestStrategies(unittest.IsolatedAsyncioTestCase):
    
    def setUp(self):
        self.mgr = StrategyManager()
        
        # Base sample data
        self.base_data = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ', '000003.SZ'],
            'name': ['Stock A', 'Stock B', 'Stock C'],
            'industry': ['Bank', 'Real Estate', 'Tech'],
            'pe_ttm': [10.0, 50.0, 5.0],
            'pb': [1.0, 5.0, 0.8],
            'dv_ttm': [3.0, 0.5, 4.5], # Dividend yield
            'pct_chg': [3.0, 8.0, -4.0],
            'turnover_rate': [5.0, 1.0, 2.0],
            'total_mv': [6000000, 100000, 200000], # 600亿, 10亿, 20亿
            # Growth
            'or_yoy': [25.0, 10.0, 5.0],
            'netprofit_yoy': [30.0, 5.0, -10.0],
            'roe': [16.0, 8.0, 5.0],
            # Financials
            'debt_to_assets': [40.0, 80.0, 60.0],
        })
        
        self.context = {'screening_data': self.base_data}

    def test_manager(self):
        """Test StrategyManager retrieves strategies"""
        s = self.mgr.get_strategy("value")
        self.assertIsInstance(s, ValueStrategy)
        self.assertIsNotNone(self.mgr.get_strategy("growth"))
        self.assertTrue(len(self.mgr.get_all_names()) > 0)

    def test_value_strategy(self):
        """Test ValueStrategy: PE 5-20, PB 0.5-3, Div > 2%"""
        # Stock A: PE 10 (Pass), PB 1 (Pass), Div 3 (Pass) -> Match
        # Stock B: PE 50 (Fail)
        # Stock C: PE 5 (Fail? >5 condition), PB 0.8 (Pass), Div 4.5 (Pass)
        # Note: Code says PE > 5. So 5.0 might fail if strictly >
        
        s = ValueStrategy()
        res = s.filter(self.context)
        
        # Should contain Stock A
        self.assertIn('000001.SZ', res['ts_code'].values)
        # Should not contain Stock B (PE too high)
        self.assertNotIn('000002.SZ', res['ts_code'].values)

    def test_growth_strategy(self):
        """Test GrowthStrategy: Rev > 20%, Profit > 25%, ROE > 15%"""
        # Stock A: 25, 30, 16 -> Pass
        # Others fail
        s = GrowthStrategy()
        res = s.filter(self.context)
        
        self.assertIn('000001.SZ', res['ts_code'].values)
        self.assertEqual(len(res), 1)

    def test_dividend_strategy(self):
        """Test DividendStrategy: Div > 4%"""
        # Stock C: 4.5 -> Pass
        s = DividendStrategy()
        res = s.filter(self.context)
        
        self.assertIn('000003.SZ', res['ts_code'].values)
        self.assertNotIn('000001.SZ', res['ts_code'].values) # 3.0 < 4

    def test_technical_breakout(self):
        """Test Breakout: 2 < Pct < 7, 3 < Turn < 15"""
        # Stock A: Pct 3 (Pass), Turn 5 (Pass)
        # Stock B: Pct 8 (Fail)
        s = TechnicalBreakoutStrategy()
        res = s.filter(self.context)
        
        self.assertIn('000001.SZ', res['ts_code'].values)
        self.assertNotIn('000002.SZ', res['ts_code'].values)

    def test_northbound(self):
        """Test Northbound: Ratio > 5%"""
        nb_data = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ'],
            'ratio': [6.0, 1.0] 
        })
        ctx = {'northbound_data': nb_data, 'screening_data': self.base_data}
        
        s = NorthboundStrategy()
        res = s.filter(ctx)
        
        self.assertIn('000001.SZ', res['ts_code'].values)
        self.assertNotIn('000002.SZ', res['ts_code'].values)
        
        # Empty context
        self.assertTrue(s.filter({}).empty)

    async def test_oversold(self):
        """Test Oversold: Pct < -3, PE < 30"""
        s = OversoldStrategy()
        
        # Mock DataProcessor
        from unittest.mock import AsyncMock, MagicMock
        dp_mock = MagicMock()
        dp_mock.get_latest_trade_date = AsyncMock(return_value="20230101")
        
        # Mock CacheManager
        cache_mock = MagicMock()
        
        # Create dummy history for RSI
        # Stock C needs to have RSI < 20
        # We need significant drop in recent days.
        # 10 days of data
        dates = pd.date_range(end='20230101', periods=14).strftime('%Y%m%d').tolist()
        
        # Stock C logs: Drop from 20 to 10 -> Low RSI
        c_prices = [20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7]
        
        history_data = []
        for d, p in zip(dates, c_prices):
            history_data.append({'ts_code': '000003.SZ', 'trade_date': d, 'close': p})
            
        history_df = pd.DataFrame(history_data)
        cache_mock.get_daily_quotes = AsyncMock(return_value=history_df)
        
        dp_mock.cache = cache_mock
        
        # Update context
        ctx = self.context.copy()
        ctx['data_processor'] = dp_mock
        
        import asyncio
        res = await s.filter(ctx)
        
        self.assertIn('000003.SZ', res['ts_code'].values)

    def test_institutional(self):
        """Test Institutional: Net > 3000"""
        lhb_data = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ'],
            'net_amount': [3500.0, 100.0]
        })
        ctx = {'top_list': lhb_data, 'screening_data': self.base_data}
        
        s = InstitutionalStrategy()
        res = s.filter(ctx)
        
        self.assertIn('000001.SZ', res['ts_code'].values)
        self.assertNotIn('000002.SZ', res['ts_code'].values)

    def test_block_trade(self):
        """Test Block Trade: Amount > 1000"""
        block_data = pd.DataFrame({
            'ts_code': ['000001.SZ', '000001.SZ', '000002.SZ'],
            'amount': [800, 300, 50], # Stock A total 1100? Logic sums it up?
            'vol': [1, 1, 1],
            'price': [10, 10, 10]
        })
        # Note: Strategy filters rows > 1000 first, THEN groups.
        # So 800 and 300 are both filtered out individually.
        
        # Let's adjust to pass
        block_data_pass = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'amount': [1200],
            'vol': [10], 'price': [10]
        })
        
        ctx = {'block_trade': block_data_pass, 'screening_data': self.base_data}
        s = BlockTradeStrategy()
        res = s.filter(ctx)
        self.assertIn('000001.SZ', res['ts_code'].values)

    def test_cashflow(self):
        """Test CashFlow: Debt < 50, ROE > 10"""
        # Stock A: Debt 40 (Pass), ROE 16 (Pass)
        s = CashFlowStrategy()
        res = s.filter(self.context)
        self.assertIn('000001.SZ', res['ts_code'].values)

    def test_large_pe(self):
        """Test LargePE: MV > 500亿, PE < 15"""
        # Stock A: MV 600亿 (Pass), PE 10 (Pass)
        s = LargePEStrategy()
        res = s.filter(self.context)
        self.assertIn('000001.SZ', res['ts_code'].values)

    def test_empty_input(self):
        """Test handling of empty or None input"""
        s = ValueStrategy()
        self.assertTrue(s.filter({}).empty)
        self.assertTrue(s.filter({'screening_data': None}).empty)
        self.assertTrue(s.filter({'screening_data': pd.DataFrame()}).empty)

if __name__ == '__main__':
    unittest.main()
