from data.tushare_client import TushareClient
import logging
import datetime
import pandas as pd
from utils.config_handler import ConfigHandler

# Setup simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TuShareCheck")

def check_data():
    client = TushareClient()
    
    # We need a recent trading day. Let's find one.
    # Today is 2026-01-31 (Saturday). Last reliable trading day likely 2026-01-30 (Friday) or 2026-01-29.
    # Let's try 2026-01-23 (Friday) to be safe from holiday delays, or just 2026-01-30.
    trade_date = "20260123" 
    
    print(f"=== Checking TuShare Data for {trade_date} ===")
    
    # 1. Northbound (HK -> Connect)
    try:
        df_hk = client.get_hk_hold(trade_date=trade_date)
        if df_hk is not None and not df_hk.empty:
            print(f"[OK] Northbound (hk_hold): Found {len(df_hk)} records.")
            print(df_hk.head(2))
        else:
            print("[FAIL] Northbound (hk_hold): No data or empty.")
    except Exception as e:
        print(f"[ERROR] Northbound: {e}")

    # 2. Block Trade
    try:
        df_block = client.get_block_trade(trade_date=trade_date)
        if df_block is not None and not df_block.empty:
            print(f"[OK] Block Trade: Found {len(df_block)} records.")
            print(df_block.head(2))
        else:
            print("[FAIL] Block Trade: No data or empty.")
    except Exception as e:
        print(f"[ERROR] Block Trade: {e}")

    # 3. Dragon Tiger (LHB) - Top List
    try:
        df_top = client.get_top_list(trade_date=trade_date)
        if df_top is not None and not df_top.empty:
            print(f"[OK] Dragon Tiger (top_list): Found {len(df_top)} records.")
            print(df_top.head(2))
        else:
            print("[FAIL] Dragon Tiger (top_list): No data or empty.")
    except Exception as e:
        print(f"[ERROR] Dragon Tiger: {e}")

    # 4. Institutional Detail
    try:
        df_inst = client.get_top_inst(trade_date=trade_date)
        if df_inst is not None and not df_inst.empty:
            print(f"[OK] Institutional (top_inst): Found {len(df_inst)} records.")
            print(df_inst.head(2))
        else:
            print("[FAIL] Institutional (top_inst): No data or empty.")
    except Exception as e:
        print(f"[ERROR] Institutional: {e}")

if __name__ == "__main__":
    if not ConfigHandler.get_token():
        print("Error: No Tushare token found in settings.")
    else:
        check_data()
