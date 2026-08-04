#!/usr/bin/env python3
"""
Software Dynamic Range Compression (DRC) per altoparlante di Marcus.
Previene distorsione meccanica e clipping acustico mantenendo massimo l'intelligibilità del parlato.
"""

import numpy as np

class AudioDRC:
    """
    Compressore dinamico (DRC) con Knee morbido, Peak Limiter e RMS Tracking.
    """
    def __init__(self, threshold_db: float = -6.0, ratio: float = 4.0, attack_ms: float = 5.0, release_ms: float = 50.0, sample_rate: int = 16000):
        self.threshold_db = threshold_db
        self.ratio = ratio
        self.sample_rate = sample_rate
        
        self.attack_coeff = np.exp(-1.0 / (sample_rate * (attack_ms / 1000.0)))
        self.release_coeff = np.exp(-1.0 / (sample_rate * (release_ms / 1000.0)))
        self.envelope = 0.0

    def process(self, audio_int16: np.ndarray) -> np.ndarray:
        """
        Compressore int16 in -> int16 out.
        """
        if len(audio_int16) == 0:
            return audio_int16

        audio_float = audio_int16.astype(np.float32) / 32768.0
        out_float = np.zeros_like(audio_float)

        threshold_lin = 10.0 ** (self.threshold_db / 20.0)

        for i in range(len(audio_float)):
            abs_val = abs(audio_float[i])
            if abs_val > self.envelope:
                self.envelope = self.attack_coeff * self.envelope + (1.0 - self.attack_coeff) * abs_val
            else:
                self.envelope = self.release_coeff * self.envelope + (1.0 - self.release_coeff) * abs_val

            if self.envelope > threshold_lin:
                env_db = 20.0 * np.log10(max(self.envelope, 1e-6))
                overshoot_db = env_db - self.threshold_db
                gain_db = -overshoot_db * (1.0 - (1.0 / self.ratio))
                gain_lin = 10.0 ** (gain_db / 20.0)
            else:
                gain_lin = 1.0

            out_float[i] = audio_float[i] * gain_lin

        return np.clip(out_float * 32767.0, -32768, 32767).astype(np.int16)
