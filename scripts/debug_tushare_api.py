
import tushare as ts
import pandas as pd
import os
import sys

# Add project root to path to import config
sys.path.append(os.getcwd())

try:
    from utils.config_handler import ConfigHandler
    token = ConfigHandler.get_token()
    print(f"Token loaded: {token[:6]}******")
except Exception as e:
    print(f"Failed to load token: {e}")
    sys.exit(1)

# NOTE: Brotli monkey patch removed — urllib3 >= 2.6.2 handles Brotli natively.

pro = ts.pro_api(token)

def test_api(api_name, **kwargs):
    print(f"\nTesting {api_name} with params: {kwargs}")
    try:
        if api_name == 'daily_basic': # daily_indicators
            df = pro.daily_basic(**kwargs)
        elif api_name == 'moneyflow':
            df = pro.moneyflow(**kwargs)
        elif api_name == 'adj_factor':
            df = pro.adj_factor(**kwargs)
        else:
            print(f"Unknown API: {api_name}")
            return
            
        if df is None:
            print("Response is None")
        elif df.empty:
            print("Response is Empty DataFrame")
        else:
            print(f"Response Shape: {df.shape}")
            print(f"Columns: {df.columns.tolist()}")
            print("Sample Data:")
            print(df.head(2))
            
            # Check unique codes
            if 'ts_code' in df.columns:
                unique_codes = df['ts_code'].nunique()
                print(f"Unique ts_codes: {unique_codes}")
                
    except Exception as e:
        print(f"API Call Failed: {e}")

if __name__ == "__main__":
    import datetime
    
    # Get trade calendar to find latest open day
    try:
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=20)).strftime('%Y%m%d')
        
        df_cal = pro.trade_cal(start_date=start_date, end_date=end_date, is_open='1')
        if not df_cal.empty:
            trade_date = df_cal['cal_date'].values[-1] # verified open day
            print(f"Latest trade date found: {trade_date}")
            
            # 1. Daily Basic (Indicators)
            test_api('daily_basic', trade_date=trade_date)
            
            # 2. Moneyflow
            test_api('moneyflow', trade_date=trade_date)
            
            # 3. Adj Factor
            test_api('adj_factor', trade_date=trade_date)
            
        else:
            print("Could not find recent trade date.")
            
    except Exception as e:
        print(f"Error getting calendar: {e}")
    
    print("\n--- End of Debug ---")
