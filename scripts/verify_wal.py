import asyncio
import os
import sqlite3
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WAL_Check")

DB_PATH = r"d:\workspace\Quantitative Trading\astock_screener\astock.db"

def check_wal():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        return

    print(f"Checking DB: {DB_PATH}")
    wal_path = DB_PATH + "-wal"
    if os.path.exists(wal_path):
        size_mb = os.path.getsize(wal_path) / (1024 * 1024)
        print(f"WAL File Size: {size_mb:.2f} MB")
    else:
        print("WAL File not found (Clean?)")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check Journal Mode
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        print(f"Journal Mode: {mode}")

        # Check WAL Checkpoint Status
        # returns (busy, log, checkpointed)
        # busy: 1 if a checkpoint operation failed to complete
        # log: number of frames in the WAL file
        # checkpointed: number of frames that have been checkpointed
        cursor.execute("PRAGMA wal_checkpoint(PASSIVE)")
        res_passive = cursor.fetchone()
        print(f"Passive Checkpoint Result: {res_passive}")
        
        # If huge difference between log and checkpointed, it's growing
        if res_passive[1] > res_passive[2] + 1000:
             print("WARNING: WAL is growing significantly larger than checkpointed data.")

        conn.close()
    except Exception as e:
        print(f"Error accessing DB: {e}")

if __name__ == "__main__":
    check_wal()
