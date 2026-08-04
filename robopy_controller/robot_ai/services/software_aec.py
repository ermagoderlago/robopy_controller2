#!/usr/bin/env python3
"""
Software AEC (Adaptive Echo Cancellation) tramite algoritmo NLMS (Normalized Least Mean Squares).
Modulo per soppressione eco software durante la riproduzione full-duplex.
"""

import numpy as np

class SoftwareAEC:
    """
    Cancellatore di eco acustico software (NLMS).
    Filtra la componente del segnale di altoparlante dal canale microfonico.
    """
    def __init__(self, filter_len: int = 1024, mu: float = 0.02, eps: float = 1e-6):
        self.filter_len = filter_len
        self.mu = mu
        self.eps = eps
        self.w = np.zeros(filter_len, dtype=np.float32)
        self.ref_buffer = np.zeros(filter_len, dtype=np.float32)

    def reset(self):
        self.w.fill(0.0)
        self.ref_buffer.fill(0.0)

    def process_chunk(self, mic_chunk: np.ndarray, ref_chunk: np.ndarray) -> np.ndarray:
        """
        mic_chunk: float32 array (campioni microfono)
        ref_chunk: float32 array (campioni altoparlante)
        restituisce: float32 array con eco cancellata
        """
        n = len(mic_chunk)
        clean = np.zeros(n, dtype=np.float32)

        for i in range(n):
            # Fai scorrere il riferimento
            self.ref_buffer[1:] = self.ref_buffer[:-1]
            self.ref_buffer[0] = ref_chunk[i] if i < len(ref_chunk) else 0.0

            # Stima eco
            echo_est = np.dot(self.w, self.ref_buffer)

            # Sottrai eco
            e = mic_chunk[i] - echo_est
            clean[i] = e

            # Aggiornamento NLMS dei pesi
            norm = np.dot(self.ref_buffer, self.ref_buffer) + self.eps
            self.w += (self.mu / norm) * e * self.ref_buffer

        return clean
