#!/usr/bin/env python3
"""
Test Unitario per VoicePrintManager e MemoryDecayEngine
======================================================
Verifica il corretto funzionamento dell'identificazione vocale,
dell'immunità dei 3 minuti e dell'algoritmo di oblio.
"""

import time
import numpy as np
from robopy_controller.nodes.voiceprint_manager import VoicePrintManager
from robopy_controller.nodes.memory_decay_engine import MemoryDecayEngine

def test_voiceprint_manager():
    print("--- Testing VoicePrintManager ---")
    vpm = VoicePrintManager(storage_file="./test_voice_prints.json", similarity_threshold=0.72)
    
    # Generazione di due vettori di embedding fittizi per Luca e Marco
    emb_luca = np.array([1.0, 0.0, 0.0, 0.5], dtype=np.float32)
    emb_marco = np.array([0.0, 1.0, 0.5, 0.0], dtype=np.float32)
    
    # Enrollment Luca
    success = vpm.enroll_speaker("Luca", [emb_luca])
    assert success, "Enrollment Luca fallito"
    
    # Enrollment Marco
    success = vpm.enroll_speaker("Marco", [emb_marco])
    assert success, "Enrollment Marco fallito"
    
    # Identificazione campione Luca
    speaker, score = vpm.identify_speaker(emb_luca)
    print(f"Identificato: {speaker} (score={score:.4f})")
    assert speaker == "Luca", f"Atteso Luca, ottenuto {speaker}"
    assert score >= 0.72, f"Score sotto soglia: {score}"
    
    # Identificazione campione sconosciuto
    emb_unknown = np.array([0.1, 0.1, 0.1, 0.1], dtype=np.float32)
    speaker, score = vpm.identify_speaker(emb_unknown)
    print(f"Campione rumoroso/sconosciuto: {speaker} (score={score:.4f})")
    assert speaker == "Sconosciuto", f"Atteso Sconosciuto, ottenuto {speaker}"
    
    print("[SUCCESS] VoicePrintManager test superato!")

def test_memory_decay_engine():
    print("--- Testing MemoryDecayEngine ---")
    mde = MemoryDecayEngine(immunity_sec=180.0, max_age_sec=1800.0)
    
    # Aggiungi frasi recenti (entro 3 min -> immuni)
    mde.add_utterance("Luca", "Ciao Marcus, puoi verificare la temperatura nella stanza?")
    mde.add_utterance("Marco", "Sì, fa un po' caldo oggi.")
    
    context = mde.get_inherited_context()
    print("Contesto Ereditato Recente:\n", context)
    assert "Luca: Ciao Marcus" in context, "Frase recente mancante"
    assert "Marco: Sì, fa un po' caldo oggi." in context, "Frase recente mancante"
    
    # Aggiungi frase vecchia irrilevante con timestamp simulato (4 minuti fa)
    mde.add_utterance("Marco", "uhm")
    mde.buffer[-1].timestamp = time.time() - 250.0  # 250s fa (>180s immunity)
    mde.buffer[-1].salience_score = 0.1 # Rilevanza bassa
    
    # Aggiungi frase vecchia saliente (5 minuti fa)
    mde.add_utterance("Luca", "Ricordati che la riunione è fissata per le ore 15:00!")
    mde.buffer[-1].timestamp = time.time() - 300.0  # 300s fa (>180s immunity)
    mde.buffer[-1].salience_score = 0.8 # Rilevanza alta
    
    mde.apply_decay()
    context_after = mde.get_inherited_context()
    print("Contesto Ereditato dopo Decay:\n", context_after)
    
    # L'interlocuzione vuota 'uhm' di 4 min fa deve essere stata eliminata dall'oblio
    assert "Marco: uhm" not in context_after, "L'algoritmo di oblio non ha cancellato la frase futile"
    # La frase importante con orario di 5 min fa deve essere preservata
    assert "riunione è fissata per le ore 15:00" in context_after, "La frase saliente è stata cancellata eroneamente"
    
    print("[SUCCESS] MemoryDecayEngine test superato!")

if __name__ == '__main__':
    test_voiceprint_manager()
    test_memory_decay_engine()
