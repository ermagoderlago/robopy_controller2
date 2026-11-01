#!/usr/bin/env python3
import sys
import time
import json
sys.path.insert(0, "/mnt/ssd/robopy_controller_host")

from robopy_controller.robot_ai.trinity.mag_temporal_parser import MAGTemporalParser

def test_mag():
    queries = [
        "ti ricordi di cosa abbiamo discussp oggi?",
        "cosa abbiamo discusso oggi?",
        "a che ora parliamo solitamente?",
        "hai visto qualcosa di strano oggi?"
    ]
    for q in queries:
        t0 = time.time()
        res = MAGTemporalParser.handle_temporal_or_special_query(q, db_path="/home/robopy/mag_trinity.db")
        dt = (time.time() - t0) * 1000
        print(f"\nQuery: '{q}' ({dt:.1f}ms)")
        print(f"Result: '{res}'")

if __name__ == "__main__":
    test_mag()
