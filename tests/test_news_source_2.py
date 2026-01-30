import akshare as ak
import pandas as pd
import logging

def test_news_alternatives():
    print("Testing alternatives...")
    
    # Method 1: Global News (Sina)
    try:
        print("\n1. stock_info_global_sina")
        df = ak.stock_info_global_sina()
        print(df.head(2) if df is not None else "None")
    except Exception as e:
        print(f"Failed: {e}")

    # Method 2: Cailianshe (try checking dir(ak) if possible, but here just robust check)
    # The error said 'stock_telegraph_cls' missing.
    # Maybe 'stock_news_cls'?
    
    # Let's inspect akshare structure roughly if possible or just try known ones.
    # Common ones: stock_news_em (EastMoney), stock_info_global_cls
    
    try:
        print("\n2. stock_news_em (Broad market)")
        # Empty symbol might give broad news or fail
        df = ak.stock_news_em(symbol="300059") 
        print(df.head(2) if df is not None else "None")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    test_news_alternatives()
