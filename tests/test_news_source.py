import akshare as ak
import pandas as pd
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_news():
    try:
        print("Fetching Cailianshe Telegraph...")
        df = ak.stock_telegraph_cls()
        if df is None or df.empty:
            print("No data returned.")
            return

        print(f"Returned {len(df)} records.")
        print("Columns:", df.columns.tolist())
        
        # Display first 3 rows
        print("\n--- Sample Data ---")
        for i, row in df.head(3).iterrows():
            print(f"Row {i}:")
            for col in df.columns:
                val = str(row[col])
                if len(val) > 100: val = val[:100] + "..."
                print(f"  {col}: {val}")
            print("-" * 30)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_news()
