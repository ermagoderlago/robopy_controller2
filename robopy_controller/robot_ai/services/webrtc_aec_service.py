"""
WebRTCAECService - Advanced Acoustic Echo Cancellation per Marcus VUI.

Utilizza la libreria C webrtc-audio-processing per sottrare l'audio altoparlante (far-end)
dal segnale microfonico (near-end) lavorando su sotto-chunk trasversali da 10ms (160 campioni @ 16kHz).
"""

import os
import sys
import numpy as np
import threading
import queue

try:
    from webrtc_audio_processing import AudioProcessingModule as APM
    HAS_WEBRTC = True
except ImportError:
    HAS_WEBRTC = False


class WebRTCAECService:
    """
    Gestore di Cancellazione dell'Eco Acustico (AEC) basato su WebRTC APM.
    Processa audio in sotto-frame rigidi da 10ms (160 campioni a 16kHz).
    """

    def __init__(self, sample_rate=16000, delay_ms=40):
        self.sample_rate = sample_rate
        self.delay_ms = delay_ms  # Ritardo hardware misurato tra DAC speaker e ADC microfono
        self.frame_size = 160     # Exactly 10ms @ 16kHz
        
        self.far_end_queue = queue.Queue(maxsize=200)
        self.has_webrtc = HAS_WEBRTC
        self._lock = threading.Lock()

        if self.has_webrtc:
            try:
                self.apm = APM(aec=True, agc=False, ns=True)
                self.apm.set_sample_rate(self.sample_rate)
                print("✅ [WebRTCAEC] Modulo WebRTC Audio Processing (10ms framing) inizializzato con successo!")
            except Exception as e:
                print(f"⚠️ [WebRTCAEC] Impossibile inizializzare WebRTC APM: {e}")
                self.has_webrtc = False
        else:
            print("⚠️ [WebRTCAEC] Libreria 'webrtc-audio-processing' non installata. Modalità fallback pass-through attiva.")

    def push_far_end_pcm(self, pcm_bytes: bytes):
        """
        Riceve i pacchetti audio riprodotti dal TTS (far-end) e li inserisce nella coda circolare.
        """
        if not self.has_webrtc or not pcm_bytes:
            return
            
        try:
            # Scompone il pacchetto far-end in sotto-frame da 160 campioni (10ms)
            int16_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
            for i in range(0, len(int16_arr), self.frame_size):
                sub_frame = int16_arr[i:i + self.frame_size]
                if len(sub_frame) == self.frame_size:
                    self.far_end_queue.put_nowait(sub_frame.tobytes())
        except queue.Full:
            pass

    def process_near_end_pcm(self, near_pcm_bytes: bytes) -> bytes:
        """
        Elabora il chunk microfonico (near-end) spacchettandolo in frame da 10ms (160 campioni),
        applica la cancellazione dell'eco e restituisce i byte puliti.
        """
        if not self.has_webrtc or not near_pcm_bytes:
            return near_pcm_bytes

        try:
            near_arr = np.frombuffer(near_pcm_bytes, dtype=np.int16)
            clean_out = []

            for i in range(0, len(near_arr), self.frame_size):
                sub_near = near_arr[i:i + self.frame_size]
                if len(sub_near) < self.frame_size:
                    clean_out.append(sub_near.tobytes())
                    continue

                # Preleva il sotto-frame far-end corrispondente
                try:
                    sub_far_bytes = self.far_end_queue.get_nowait()
                except queue.Empty:
                    sub_far_bytes = b"\x00\x00" * self.frame_size

                # Esegue AEC WebRTC sul sotto-frame 10ms
                try:
                    clean_sub_bytes = self.apm.process(sub_near.tobytes(), sub_far_bytes)
                    clean_out.append(clean_sub_bytes)
                except Exception:
                    clean_out.append(sub_near.tobytes())

            return b"".join(clean_out)
        except Exception:
            return near_pcm_bytes
