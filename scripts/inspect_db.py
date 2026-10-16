#!/usr/bin/env python3
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else '/mnt/ssd/maps/pianoterra2_opt.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Tables in {db_path}:")
for t in tables:
    count = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"  - {t}: {count} rows")

# Check if there is Grid table or Map table
for t in tables:
    if 'grid' in t.lower() or 'map' in t.lower() or 'info' in t.lower():
        cols = [col[1] for col in c.execute(f"PRAGMA table_info({t})").fetchall()]
        print(f"Schema of {t}: {cols}")
