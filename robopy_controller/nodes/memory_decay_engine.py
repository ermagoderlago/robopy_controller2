#!/usr/bin/env python3
"""
Memory Decay Engine — Buffer Ambientale & Algoritmo di Oblio
===========================================================
Mantiene in memoria il contesto delle conversazioni e degli avvenimenti
rilevati durante l'ascolto continuo passivo di Marcus.

Funzionalità:
- Buffer di memoria ambientale a finestra temporale.
- Immunità temporale: gli ultimi 3 minuti (180s) di conversazione sono sempre preservati intact.
- Algoritmo di Oblio (Memory Decay): per gli eventi più vecchi di 3 minuti, calcola il Salience Score.
  Elimina frasi trascurabili, rumori o interlocuzioni vuote.
- Generazione del contesto ereditato per l'inizializzazione di Gemini Live API.
"""

import time
from typing import List, Dict, Any, Optional

IMMUNITY_DURATION_SEC = 180.0   # 3 minuti di immunità dal decay
MAX_MEMORY_AGE_SEC    = 1800.0  # 30 minuti massimo di permanenza buffer
MIN_SALIENCE_SCORE    = 0.4     # Soglia minima di rilevanza per conservare memorie oltre i 3 minuti

class MemoryUtterance:
    def __init__(self, speaker: str, text: str, timestamp: Optional[float] = None):
        self.speaker = speaker
        self.text = text.strip()
        self.timestamp = timestamp or time.time()
        self.salience_score = self._calculate_salience()

    def _calculate_salience(self) -> float:
        """Calcola l'indice di rilevanza della frase."""
        if not self.text:
            return 0.0
        
        words = self.text.split()
        length = len(words)
        
        # Parole monosillabiche o brevissime
        if length <= 2:
            return 0.2
        
        score = 0.5
        # Presenza di entità / nomi propri / domande
        if any(char in self.text for char in ['?', '!', ':']):
            score += 0.2
        if any(word.istitle() for word in words):
            score += 0.2
        if length >= 6:
            score += 0.1
            
        return min(1.0, score)


class MemoryDecayEngine:
    def __init__(self, immunity_sec: float = IMMUNITY_DURATION_SEC, max_age_sec: float = MAX_MEMORY_AGE_SEC):
        self.immunity_sec = immunity_sec
        self.max_age_sec = max_age_sec
        self.buffer: List[MemoryUtterance] = []

    def add_utterance(self, speaker: str, text: str) -> None:
        """Aggiunge una nuova trascrizione dal parlante identificato."""
        if not text or not text.strip():
            return
        utt = MemoryUtterance(speaker=speaker, text=text)
        self.buffer.append(utt)
        self.apply_decay()

    def apply_decay(self) -> None:
        """
        Esegue l'algoritmo di oblio:
        1. Rimuove memorie più vecchie di MAX_MEMORY_AGE_SEC.
        2. Per memorie tra immunity_sec e MAX_MEMORY_AGE_SEC, elimina quelle con salience < MIN_SALIENCE_SCORE.
        3. Memorie entro immunity_sec (ultimi 3 min) rimangono intatte.
        """
        now = time.time()
        filtered: List[MemoryUtterance] = []

        for utt in self.buffer:
            age = now - utt.timestamp
            if age > self.max_age_sec:
                continue  # Oblio per superamento tempo massimo (30 min)
            
            if age <= self.immunity_sec:
                filtered.append(utt)  # Immunità 3 minuti
            else:
                # Oltre i 3 minuti: passa solo se ha rilevanza sufficiente
                if utt.salience_score >= MIN_SALIENCE_SCORE:
                    filtered.append(utt)

        self.buffer = filtered

    def get_inherited_context(self, max_utterances: int = 4) -> str:
        """
        Genera la stringa di contesto ereditato da fornire a Gemini Live.
        Limita a max_utterances frasi più salienti (max ~250 token) per non saturare la quota API.
        """
        self.apply_decay()
        if not self.buffer:
            return ""

        # Prendi le ultime frasi salienti (fino a max_utterances)
        recent_salient = sorted(self.buffer, key=lambda x: (x.timestamp, x.salience_score), reverse=True)[:max_utterances]
        recent_salient.reverse() # Riordina in ordine cronologico

        lines = ["--- CONTESTO AMBIENTALE EREDITATO (ASCOLTO PASSIVO RECENTE) ---"]
        for utt in recent_salient:
            rel_time = int(time.time() - utt.timestamp)
            lines.append(f"[{rel_time}s fa] {utt.speaker}: {utt.text}")
        lines.append("-------------------------------------------------------------")
        return "\n".join(lines)


    def clear(self) -> None:
        """Resetta l'intero buffer di memoria."""
        self.buffer.clear()
