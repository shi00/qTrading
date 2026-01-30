import akshare as ak
import pandas as pd
import datetime

def test_cctv():
    print("Testing CCTV News (Authoritative Policy Source)...")
    try:
        # CCTV news usually takes a date. Default is latest.
        # Format: YYYYMMDD
        today = datetime.datetime.now().strftime("%Y%m%d")
        print(f"Fetching for {today}...")
        
        df = ak.news_cctv(date=today)
        
        if df is None or df.empty:
            # Try yesterday if today is empty (e.g. early morning)
            yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y%m%d")
            print(f"Today empty, trying {yesterday}...")
            df = ak.news_cctv(date=yesterday)

        if df is not None and not df.empty:
            print(f"✅ CCTV Success! Got {len(df)} items.")
            print("Columns:", df.columns.tolist())
            print(df.head(2).to_string())
        else:
            print("❌ CCTV Returned empty.")

    except Exception as e:
        print(f"❌ CCTV Error: {e}")

def test_cls():
    print("\nTesting Cailianshe (CLS) - Attempt 2...")
    try:
        # Some versions use stock_info_global_cls
        # Let's try likely names if stock_telegraph_cls failed
        if hasattr(ak, 'stock_info_global_cls'):
            print("Found stock_info_global_cls! Testing...")
            df = ak.stock_info_global_cls()
            print(f"✅ CLS Success! Got {len(df)} items.")
        else:
            print("❌ stock_info_global_cls not found.")
            
    except Exception as e:
        print(f"❌ CLS Error: {e}")

if __name__ == "__main__":
    test_cctv()
    test_cls()
