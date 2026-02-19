
import asyncio
import os
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Setup paths to import config
sys.path.append(os.getcwd())
try:
    import config
    DB_PATH = config.DB_PATH
except ImportError:
    # Fallback if config not found
    DB_PATH = "astock.db"

async def main():
    # Use local variable, initialized from global or detected
    target_db_path = DB_PATH
    
    print(f"Target DB: {target_db_path}")
    if not os.path.exists(target_db_path):
        # Try default locations if not found
        if os.path.exists("astock.db"):
            target_db_path = "astock.db"
        elif os.path.exists("stock_data.db"):
            target_db_path = "stock_data.db"
        else:
            print(f"DB not found at {target_db_path}")
            return

    engine = create_async_engine(f"sqlite+aiosqlite:///{target_db_path}")
    
    async with engine.connect() as conn:
        for table_name in ["stock_basic", "moneyflow_daily", "daily_indicators"]:
            print(f"\nChecking table: {table_name}")
            
            # 1. Total Count
            try:
                r = await conn.execute(text(f"SELECT count(*) FROM {table_name}"))
                count = r.scalar()
                print(f"Total Rows: {count}")
            except Exception as e:
                print(f"Table {table_name} not found or error: {e}")
                continue

            if count == 0:
                print("Table is empty.")
                continue

            # 2. Distinct TS Codes
            r = await conn.execute(text(f"SELECT count(DISTINCT ts_code) FROM {table_name}"))
            distinct_codes = r.scalar()
            print(f"Distinct TS Codes: {distinct_codes}")

            # 3. Latest Date (Skip for stock_basic if no trade_date)
            if table_name != "stock_basic":
                r = await conn.execute(text(f"SELECT MAX(trade_date) FROM {table_name}"))
                max_date = r.scalar()
                print(f"Latest Date: {max_date}")

            # 4. Sample Rows
            limit_rows = 3
            ts_col = "ts_code"
            order_col = "trade_date" if table_name != "stock_basic" else "ts_code"
            
            r = await conn.execute(text(f"SELECT * FROM {table_name} ORDER BY {order_col} DESC LIMIT {limit_rows}"))
            rows = r.fetchall()
            print(f"Sample Data (First {limit_rows}):")
            for row in rows:
                print(row)

    await engine.dispose()

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
