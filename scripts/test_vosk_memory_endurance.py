#!/usr/bin/env python3
"""
Script di Test di Endurance per verificare l'assenza di Memory Leak (FM-VUI-014)
durante le ri-inizializzazioni trasparenti del riconoscitore Vosk (Sentinel Value Pattern).
"""

import sys
import os
import time
import gc
import psutil

# Aggiunge il path delle librerie del robot
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from robopy_controller.robot_ai.services.local_asr_vosk import VoskASRManager

def run_endurance_test(iterations=100):
    print(f"🚀 Avvio Test di Endurance Memory Leak per Vosk ASR ({iterations} cicli)...")
    process = psutil.Process(os.getpid())
    
    recognized_count = 0
    def on_text_cb(text, is_partial):
        nonlocal recognized_count
        recognized_count += 1

    vosk_mgr = VoskASRManager(on_text_cb=on_text_cb)
    
    if not vosk_mgr.is_active():
        print("⚠️ Vosk non è attivo o il modello non è stato trovato localmente. Test ignorato.")
        return

    gc.collect()
    initial_memory_mb = process.memory_info().rss / (1024 * 1024)
    print(f"📊 Memoria RAM Iniziale (Modello Caricato): {initial_memory_mb:.2f} MB")

    # Genera 100ms di audio fittizio (1600 campioni int16 a 16kHz)
    dummy_pcm = b"\x00\x00" * 1600

    start_time = time.time()
    for i in range(1, iterations + 1):
        # Simula invio audio
        vosk_mgr.process_audio(dummy_pcm)
        
        # Simula il flush a fine frase
        vosk_mgr.force_flush()
        
        # Piccola pausa per permettere al worker thread di consumare il sentinel
        time.sleep(0.01)
        
        if i % 10 == 0 or i == iterations:
            current_mem_mb = process.memory_info().rss / (1024 * 1024)
            delta_mem = current_mem_mb - initial_memory_mb
            print(f"  [Iterazione {i:4d}/{iterations}] RAM: {current_mem_mb:.2f} MB (Delta: {delta_mem:+.2f} MB)")

    elapsed = time.time() - start_time
    final_mem_mb = process.memory_info().rss / (1024 * 1024)
    total_delta = final_mem_mb - initial_memory_mb
    
    print("\n=================== RISULTATI ENDURANCE TEST ===================")
    print(f"⏱️  Tempo totale: {elapsed:.2f}s")
    print(f"📈  RAM Iniziale: {initial_memory_mb:.2f} MB")
    print(f"📈  RAM Finale:   {final_mem_mb:.2f} MB")
    print(f"📊  Delta Totale: {total_delta:+.2f} MB")
    
    vosk_mgr.stop()
    
    # Valuta plateau tra l'assestamento iniziale ed i cicli successivi
    if current_mem_mb - initial_memory_mb < 20.0:
        print("✅ SUCCESS: Curva di memoria stabilizzata a plateau! Nessun Memory Leak SWIG rilevato.")
    else:
        print("⚠️ WARNING: Rilevata crescita continua della memoria.")

if __name__ == "__main__":
    run_endurance_test(iterations=200)
