import sqlite3
import time
import os

# Use a separate test DB to avoid locks
TEST_DB = "benchmark_test.db"

def benchmark():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    print(f"Creating test DB: {TEST_DB}...")
    conn = sqlite3.connect(TEST_DB)
    # Mirroring the real app settings
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL") 
    
    cursor = conn.cursor()

    # Create table structure similar to daily_indicators with the same PK
    cursor.execute("""
    CREATE TABLE daily_indicators (
        ts_code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        pe REAL,
        pe_ttm REAL,
        pb REAL,
        ps REAL,
        ps_ttm REAL,
        dv_ratio REAL,
        dv_ttm REAL,
        total_mv REAL,
        circ_mv REAL,
        total_share REAL,
        float_share REAL,
        free_share REAL,
        turnover_rate REAL,
        turnover_rate_f REAL,
        volume_ratio REAL,
        PRIMARY KEY (ts_code, trade_date)
    )
    """)
    conn.commit()

    # Generate dummy data (50,000 rows to make it significant)
    BATCH_SIZE = 1000
    NUM_BATCHES = 10
    print(f"Generating data ({BATCH_SIZE * NUM_BATCHES} rows)...")
    
    rows = []
    for i in range(BATCH_SIZE * NUM_BATCHES):
        rows.append((
            f"600{i%500:03d}.SH", # 500 unique stocks
            f"202501{i%30:02d}",   # 30 unique dates
            10.5, 12.3, 1.5, 2.0, 2.1, 0.5, 0.6,
            100000.0, 50000.0, 1000.0, 500.0, 400.0,
            1.2, 1.5, 0.8
        ))
    
    cols = ['ts_code', 'trade_date', 'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 
            'dv_ratio', 'dv_ttm', 'total_mv', 'circ_mv', 'total_share', 'float_share', 'free_share', 
            'turnover_rate', 'turnover_rate_f', 'volume_ratio']
    placeholders = ",".join(["?"] * len(cols))

    # --- Test 1: INSERT OR REPLACE ---
    print("\n--- Test 1: INSERT OR REPLACE ---")
    start_time = time.perf_counter()
    
    sql_replace = f"INSERT OR REPLACE INTO daily_indicators ({','.join(cols)}) VALUES ({placeholders})"
    
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i+BATCH_SIZE]
        cursor.executemany(sql_replace, batch)
        conn.commit()
        
    duration = time.perf_counter() - start_time
    print(f"Total time: {duration:.4f}s")
    print(f"TPS: {len(rows)/duration:.0f} rows/s")

    # --- Test 2: UPSERT (ON CONFLICT DO UPDATE) ---
    print("\n--- Test 2: ON CONFLICT DO UPDATE (Upsert) ---")
    # Truncate doesn't exist in sqlite, delete all
    cursor.execute("DELETE FROM daily_indicators")
    conn.commit()

    start_time = time.perf_counter()
    
    update_clause = ",".join([f"{col}=excluded.{col}" for col in cols if col not in ['ts_code', 'trade_date']])
    sql_upsert = f"""
        INSERT INTO daily_indicators ({','.join(cols)}) 
        VALUES ({placeholders}) 
        ON CONFLICT(ts_code, trade_date) DO UPDATE SET {update_clause}
    """
    
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i+BATCH_SIZE]
        cursor.executemany(sql_upsert, batch)
        conn.commit()
    
    duration = time.perf_counter() - start_time
    print(f"Total time: {duration:.4f}s")
    print(f"TPS: {len(rows)/duration:.0f} rows/s")

    conn.close()
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

if __name__ == "__main__":
    benchmark()
