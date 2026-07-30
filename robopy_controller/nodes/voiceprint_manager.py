#!/usr/bin/env python3
"""
VoicePrint Manager — Gestione Impronte Vocali e Speaker Identification
========================================================================
Gestisce l'estrazione, il matching e l'enrollment degli Speaker Embeddings
delle persone che interagiscono con Marcus.

Funzionalità:
- Calcolo Cosine Similarity tra vettori di embedding vocali (dim 128 o 512).
- Gestione registro locale `user_voice_prints.json`.
- Procedura di Enrollment (registrazione multi-campione per calcolo centroide).
- Identificazione parlante con soglia di confidenza minima (default >= 0.72).
"""

import os
import json
import numpy as np
from typing import Dict, Optional, Tuple, List

VOICE_PRINTS_FILE = "/mnt/ssd/robopy_controller_host/user_voice_prints.json"

class VoicePrintManager:
    def __init__(self, storage_file: str = VOICE_PRINTS_FILE, similarity_threshold: float = 0.72):
        self.storage_file = storage_file
        self.threshold = similarity_threshold
        self.profiles: Dict[str, np.ndarray] = {}
        self.load_profiles()

    def load_profiles(self) -> None:
        """Carica le impronte vocali salvate dal file JSON locale."""
        self.profiles = {}
        if not os.path.exists(self.storage_file):
            return

        try:
            with open(self.storage_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for name, vec_list in data.items():
                    arr = np.array(vec_list, dtype=np.float32)
                    norm = np.linalg.norm(arr)
                    if norm > 1e-6:
                        arr /= norm
                    self.profiles[name.lower()] = arr
        except Exception as e:
            print(f"[VoicePrintManager] Errore durante il caricamento di {self.storage_file}: {e}")

    def save_profiles(self) -> bool:
        """Salva il registro aggiornato delle impronte vocali in formato JSON."""
        try:
            os.makedirs(os.path.dirname(self.storage_file), exist_ok=True)
            data = {}
            for name, arr in self.profiles.items():
                data[name] = arr.tolist()
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"[VoicePrintManager] Errore durante il salvataggio: {e}")
            return False

    def identify_speaker(self, embedding: np.ndarray) -> Tuple[str, float]:
        """
        Identifica la persona dal vettore di embedding vocale.
        Restituisce (nome_persona, score_similarita). Se sotto soglia, restituisce ('Sconosciuto', score).
        """
        if embedding is None or len(embedding) == 0 or len(self.profiles) == 0:
            return "Sconosciuto", 0.0

        norm = np.linalg.norm(embedding)
        if norm < 1e-6:
            return "Sconosciuto", 0.0
        emb_norm = embedding / norm

        best_name = "Sconosciuto"
        best_score = -1.0

        for name, profile_emb in self.profiles.items():
            score = float(np.dot(emb_norm, profile_emb))
            if score > best_score:
                best_score = score
                best_name = name

        if best_score >= self.threshold:
            return best_name.capitalize(), best_score
        else:
            return "Sconosciuto", max(0.0, best_score)

    def enroll_speaker(self, name: str, embeddings_list: List[np.ndarray]) -> bool:
        """
        Esegue l'enrollment di un nuovo utente calcolando il centroide dei vettori forniti.
        """
        if not name or not embeddings_list:
            return False

        valid_embs = []
        for emb in embeddings_list:
            norm = np.linalg.norm(emb)
            if norm > 1e-6:
                valid_embs.append(emb / norm)

        if not valid_embs:
            return False

        # Calcolo del centroide medio
        mean_emb = np.mean(valid_embs, axis=0)
        mean_norm = np.linalg.norm(mean_emb)
        if mean_norm > 1e-6:
            mean_emb /= mean_norm

        self.profiles[name.lower()] = mean_emb
        return self.save_profiles()
