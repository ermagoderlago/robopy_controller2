import asyncio
import time
import sys
import os

# Aggiungi il path per importare i moduli robopy
sys.path.append('/mnt/ssd/robopy_controller_host')

from robot_ai.rag.llama_index_store import LlamaIndexMemoryStore
from robot_ai.core.config_manager import ConfigManager

async def test_l1():
    print("Inizializzazione test L1 Cache...")
    config = ConfigManager()
    
    # Inizializziamo lo store direttamente
    # Passiamo None come embedding_service per triggerare il fallback sync se necessario nel test
    store = LlamaIndexMemoryStore(config)
    
    # 1. Inserimento 5 messaggi univoci
    print("Step 1: Inserimento 5 messaggi univoci...")
    for i in range(5):
        txt = f"Messaggio unico per L1 {i} @ {time.time()}"
        print(f"  - Aggiunta: {txt}")
        await store.add(txt, {"memory_type": "conversation"})
    
    # 2. Chiama get_recent() e misura latenza
    # Deve rispondere dalla L1 (RAM)
    print("\nStep 2: Chiamata get_recent() (RAM Speed Test)...")
    start = time.perf_counter()
    recent = await store.get_recent(limit=5)
    end = time.perf_counter()
    
    latency_ms = (end - start) * 1000
    print(f"\nRISULTATO: Latenza get_recent(): {latency_ms:.4f} ms")
    print("Elementi recuperati (dovrebbero essere gli ultimi 5 in ordine inverso):")
    for r in recent:
        print(f" - {r.content}")

if __name__ == '__main__':
    asyncio.run(test_l1())
