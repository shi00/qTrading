import asyncio
import aiosqlite
import os

DB_PATH = r"d:\workspace\Quantitative Trading\astock_screener\astock.db"

async def check_counts():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # List all tables first
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables_in_db = [row[0] for row in await cursor.fetchall()]
            print(f"Tables in DB: {tables_in_db}")

        tables_to_check = ["stock_basic", "daily_quotes", "daily_indicators", "financial_reports", "moneyflow_daily", "stk_holdernumber"]
        print(f"Checking database: {DB_PATH}")
        for table in tables_to_check:
            if table not in tables_in_db:
                print(f"{table}: Not found")
                continue
            try:
                async with db.execute(f"SELECT count(*) FROM {table}") as cursor:
                    row = await cursor.fetchone()
                    count = row[0]
                    print(f"{table}: {count}")
            except Exception as e:
                print(f"{table}: Error ({e})")

if __name__ == "__main__":
    asyncio.run(check_counts())
