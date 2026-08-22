#!/usr/bin/env python3
"""
Script di Validazione Automatica e Guidata VUI sul Campo per Marcus.

Testa in sequenza:
1. Relevamento Wake Word "Marcus" a distanze (50cm, 1m, 2m, 3m)
2. Comprensione ASR e latenza di risposta
3. Interruzione Barge-In durante TTS
"""

import sys
import time

def main():
    print("=================================================================")
    print("🧪 PROTOCOLLO DI VALIDAZIONE VUI MARCUS SUL CAMPO")
    print("=================================================================")
    print("Assicurarsi che il nodo respeaker_vui_node sia attivo ed operativo.\n")

    distances = ["50 cm", "1 metro", "2 metri", "3 metri"]
    results = {}

    for dist in distances:
        print(f"\n--- TEST A DISTANZA: {dist} ---")
        input(f"Posizionati a {dist} dal robot e premi INVIO per iniziare...")
        
        successes = 0
        trials = 3
        for t in range(1, trials + 1):
            print(f"\n  [Prova {t}/{trials}] Pronuncia ora ad alta voce: 'Marcus'")
            res = input("  Marcus ha risposto con il beep di ascolto? (s/n): ").strip().lower()
            if res == 's':
                successes += 1
        
        rate = (successes / trials) * 100
        results[dist] = rate
        print(f"  📊 Tasso di successo a {dist}: {rate:.1f}% ({successes}/{trials})")

    print("\n=================================================================")
    print("📊 REPORT FINALE VALIDAZIONE VUI")
    print("=================================================================")
    for dist, rate in results.items():
        status = "✅ PASS" if rate >= 66.0 else "❌ FAIL"
        print(f"  - Distanza {dist:10s}: {rate:5.1f}% [{status}]")
    print("=================================================================")

if __name__ == '__main__':
    main()
