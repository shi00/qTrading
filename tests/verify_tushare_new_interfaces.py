
import asyncio
import pandas as pd
from data.tushare_client import TushareClient
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
import datetime

# Setup
setup_logging()
client = TushareClient()

async def verify_interfaces():
    print("="*50)
    print("Verifying New Tushare Interfaces (Step 4)")
    print("="*50)
    
    # Target Stock: Find a stock that likely has all data.
    # Ping An Bank (000001.SZ) or Kweichow Moutai (600519.SH) are good candidates.
    ts_code = "600519.SH" 
    print(f"Target Stock: {ts_code}")
    
    end_date = datetime.datetime.now().strftime('%Y%m%d')
    start_date = (datetime.datetime.now() - datetime.timedelta(days=365*2)).strftime('%Y%m%d')
    
    test_cases = [
        ("Audit Opinion", client.get_fina_audit, {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}),
        ("Main Business", client.get_fina_mainbz, {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}),
        ("Forecast", client.get_forecast, {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}),
        ("Pledge Stat", client.get_pledge_stat, {"ts_code": ts_code, "end_date": end_date}),
        ("Repurchase", client.get_repurchase, {"ts_code": ts_code, "start_date": start_date}),
        ("Dividend", client.get_dividend, {"ts_code": ts_code, "start_date": start_date}),
        ("Limit List (Daily)", client.get_limit_list, {"trade_date": "20240201"}), # Pick a valid trading day
        ("Suspend List (Daily)", client.get_suspend_d, {"trade_date": "20240201"}),
        ("Margin Detail (Daily)", client.get_margin_detail, {"trade_date": "20240201"}),
        ("Index Daily", client.get_index_daily, {"ts_code": "000001.SH", "start_date": "20240101", "end_date": "20240201"})
    ]
    
    for name, func, kwargs in test_cases:
        print(f"\n[Test] Checking {name}...")
        try:
            # Tushare client is sync, but we might wrap it if needed. 
            # The client methods are synchronous requests.
            df = func(**kwargs)
            
            if df is not None and not df.empty:
                print(f"PASS: Got {len(df)} records.")
                print(f"   Columns: {df.columns.tolist()[:5]}...")
                print(f"   Sample: {df.iloc[0].to_dict()}")
            else:
                print(f"WARN: Returned OK but empty (might be no data for this period/stock).")
                
        except Exception as e:
            print(f"FAIL: {e}")

if __name__ == "__main__":
    asyncio.run(verify_interfaces())
