#!/usr/bin/env python3
import sqlite3
import sys
import os

db_path = sys.argv[1] if len(sys.argv) > 1 else '/mnt/ssd/maps/piano_terra.db'
print(f"Checking {db_path}...")

try:
    con = sqlite3.connect(db_path)
    res = con.execute("PRAGMA integrity_check;").fetchall()
    print("Integrity check result:")
    for r in res[:20]:
        print(" ", r[0])
    if len(res) > 20:
        print(f" ... and {len(res)-20} more errors.")
except Exception as e:
    print(f"Error during integrity check: {e}")
