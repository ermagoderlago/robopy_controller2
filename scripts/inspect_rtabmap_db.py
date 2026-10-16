#!/usr/bin/env python3
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/ssd/rtabmap.db"
print(f"=== Inspecting {db_path} ===")

con = sqlite3.connect(db_path)
cur = con.cursor()

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Tables: {tables}")

if "Node" in tables:
    count = cur.execute("SELECT count(*) FROM Node").fetchone()[0]
    print(f"Node count: {count}")

if "Link" in tables:
    # RTAB-Map Link types: 0=Neighbor, 1=Global loop closure, 2=Local space/proximity, 3=Local time
    links = cur.execute("SELECT type, count(*) FROM Link GROUP BY type").fetchall()
    print("Links by type:")
    type_names = {0: "Neighbor", 1: "Global Loop", 2: "Proximity (Local Space)", 3: "Local Time", 4: "User", 5: "Virtual"}
    for t, c in links:
        print(f"  Type {t} ({type_names.get(t, 'Unknown')}): {c}")

if "Data" in tables:
    data_cols = [d[0] for d in cur.execute("SELECT * FROM Data LIMIT 1").description]
    print(f"Data columns: {data_cols}")
    row = cur.execute("SELECT id, length(image), length(depth), length(scan), length(obstacle_cells) FROM Data LIMIT 5").fetchall()
    print(f"Data sample (id, img_len, depth_len, scan_len, obs_len): {row}")

con.close()
