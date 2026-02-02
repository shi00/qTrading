import sys
import os

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.config_handler import ConfigHandler
import tushare as ts

token = ConfigHandler.get_token()
ts.set_token(token)
pro = ts.pro_api()

trade_date = '20260202'  # Today (Sunday)

print('=== 上证指数 (000001.SH) ===')
df1 = pro.index_daily(ts_code='000001.SH', trade_date=trade_date)
print(f'Rows: {len(df1) if df1 is not None else 0}')
print(df1 if df1 is not None and not df1.empty else 'Empty DataFrame')

print()
print('=== 深证成指 (399001.SZ) ===')
df2 = pro.index_daily(ts_code='399001.SZ', trade_date=trade_date)
print(f'Rows: {len(df2) if df2 is not None else 0}')
print(df2 if df2 is not None and not df2.empty else 'Empty DataFrame')

print()
print('=== 创业板指 (399006.SZ) ===')
df3 = pro.index_daily(ts_code='399006.SZ', trade_date=trade_date)
print(f'Rows: {len(df3) if df3 is not None else 0}')
print(df3 if df3 is not None and not df3.empty else 'Empty DataFrame')

print()
print('=== 北向资金 ===')
df4 = pro.moneyflow_hsgt(trade_date=trade_date)
print(f'Rows: {len(df4) if df4 is not None else 0}')
print(df4 if df4 is not None and not df4.empty else 'Empty DataFrame')
