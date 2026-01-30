import akshare as ak
import pandas as pd
import logging

def verify_feeds():
    print("=== Verifying News Feeds ===")
    
    # 1. Sina 7x24 Global Financial News
    # This is often the best source for a "ticker"
    try:
        print("\n[Test 1] ak.stock_info_global_sina()")
        df = ak.stock_info_global_sina()
        if df is not None and not df.empty:
            print(f"✅ Success! Got {len(df)} items.")
            print("Columns:", df.columns.tolist())
            print(df.head(1).to_string())
        else:
            print("❌ Returned empty.")
    except Exception as e:
        print(f"❌ Error: {e}")

    # 2. EastMoney Broad News
    try:
        print("\n[Test 2] ak.stock_news_em(symbol='300059') (Specific Stock)")
        df = ak.stock_news_em(symbol='300059')
        if df is not None and not df.empty:
            print(f"✅ Success! Got {len(df)} items.")
        else:
            print("❌ Returned empty.")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    verify_feeds()
