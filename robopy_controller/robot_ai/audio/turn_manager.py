import time
import os
import numpy as np
import logging
try:
    import onnxruntime as ort
    HAS_ONNX = False  # [FIX] Forced disabled due to ARM64 ONNXRuntime LSTM bugs causing prob=0.0
except ImportError:
    HAS_ONNX = False

try:
    import webrtcvad
    HAS_WEBRTC = True
except ImportError:
    HAS_WEBRTC = False

class TurnManager:
    """
    Manages Voice Activity Detection (VAD) and conversational turns.
    Uses Silero VAD (ONNX) if available, otherwise falls back to WebRTC VAD.
    """
    def __init__(self, sample_rate=16000, pre_roll_ms=500, max_silence_ms=700, logger=None):
        self.sample_rate = sample_rate
        self.max_silence_ms = max_silence_ms
        self.logger = logger or logging.getLogger("TurnManager")
        
        self.use_silero = False
        self.ort_session = None
        
        if HAS_ONNX:
            model_path = os.path.join(os.path.dirname(__file__), 'silero_vad.onnx')
            if os.path.exists(model_path):
                try:
                    # Load Silero VAD ONNX
                    opts = ort.SessionOptions()
                    opts.inter_op_num_threads = 1
                    opts.intra_op_num_threads = 1
                    
                    self.ort_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'], sess_options=opts)
                    self.use_silero = True
                    self.reset_states()
                    self.logger.info("✅ Silero VAD ONNX caricato con successo.")
                except Exception as e:
                    self.logger.warning(f"Impossibile caricare Silero VAD ONNX: {e}")
        
        self.vad = None
        if not self.use_silero and HAS_WEBRTC:
            self.vad = webrtcvad.Vad(3)  # 3 = aggressive noise/TV rejection for home robot
            self.logger.info("⚠️ Active WebRTC VAD (Mode 3 - Aggressive Noise/TV Rejection).")
            
        # State
        self.is_speech_active = False
        
        # Hysteresis counters (in ms, approx based on chunk sizes)
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        
        # Thresholds
        self.start_prob = 0.6
        self.end_prob = 0.35
        self.start_hysteresis_ms = 120.0
        
        # Buffer
        pre_roll_samples = int((pre_roll_ms / 1000.0) * sample_rate)
        self.ring_buffer = np.zeros(pre_roll_samples, dtype=np.int16)
        self.ring_ptr = 0

    def reset_states(self):
        """Resets the ONNX model RNN states (Silero VAD v4 requires a single 'state' tensor)."""
        self._state = np.zeros((2, 1, 128)).astype(np.float32)

    def reset_state(self):
        """Resets the VAD state machine, e.g., on wake word detection."""
        self.is_speech_active = False
        self.speech_ms = 0.0
        self.silence_ms = 0.0
        if self.use_silero:
            self.reset_states()

    def process_chunk(self, chunk_int16: np.ndarray) -> bool:
        """
        Processes an audio chunk and updates VAD state.
        Returns True if speech is detected (active turn).
        """
        n = len(chunk_int16)
        chunk_ms = (n / self.sample_rate) * 1000.0
        
        # Append to ring buffer
        cap = len(self.ring_buffer)
        if n >= cap:
            self.ring_buffer[:] = chunk_int16[-cap:]
            self.ring_ptr = 0
        else:
            space_at_end = cap - self.ring_ptr
            if n <= space_at_end:
                self.ring_buffer[self.ring_ptr:self.ring_ptr+n] = chunk_int16
                self.ring_ptr = (self.ring_ptr + n) % cap
            else:
                self.ring_buffer[self.ring_ptr:cap] = chunk_int16[:space_at_end]
                self.ring_buffer[0:n-space_at_end] = chunk_int16[space_at_end:]
                self.ring_ptr = n - space_at_end

        # Calculate RMS for pre-gate filtering
        rms = np.sqrt(np.mean(np.square(chunk_int16.astype(np.float32))) + 1e-6)

        is_voice_frame = False
        prob = 0.0
        
        if self.use_silero:
            # Silero ONNX specifically requires exactly 512 samples per call at 16kHz
            if not hasattr(self, '_silero_buffer'):
                self._silero_buffer = np.array([], dtype=np.int16)
                
            self._silero_buffer = np.concatenate((self._silero_buffer, chunk_int16))
            
            # Process as many 512 chunks as we have
            while len(self._silero_buffer) >= 512:
                sub_chunk = self._silero_buffer[:512]
                self._silero_buffer = self._silero_buffer[512:]
                
                sub_rms = np.sqrt(np.mean(np.square(sub_chunk.astype(np.float32))) + 1e-6)
                if sub_rms < 25.0:
                    prob = 0.0
                else:
                    audio_float32 = sub_chunk.astype(np.float32) / 32768.0
                    ort_inputs = {
                        'input': np.expand_dims(audio_float32, 0),
                        'sr': np.array(self.sample_rate, dtype=np.int64),
                        'state': self._state
                    }
                    out, self._state = self.ort_session.run(None, ort_inputs)
                    prob = out[0][0]
                
                # Update hysteresis for this 512 chunk (32ms)
                chunk_ms_512 = 32.0
                if self.is_speech_active:
                    is_voice_sub = prob > self.end_prob
                else:
                    is_voice_sub = prob > self.start_prob
                    
                if is_voice_sub:
                    self.speech_ms += chunk_ms_512
                    self.silence_ms = 0.0
                    if not self.is_speech_active and self.speech_ms >= self.start_hysteresis_ms:
                        self.is_speech_active = True
                else:
                    self.silence_ms += chunk_ms_512
                    self.speech_ms = 0.0
                    if self.is_speech_active and self.silence_ms >= self.max_silence_ms:
                        self.is_speech_active = False

            return self.is_speech_active
                
        elif self.vad is not None:
            # RMS Pre-Gate: se il livello è tipico del silenzio/ventola/TV lontana (< 110.0), scarta a priori
            if rms < 110.0:
                is_voice_frame = False
            else:
                # WebRTC chunking (3 frame da 20ms = 320 campioni)
                voice_parts = 0
                total_parts = 0
                for i in range(0, len(chunk_int16), 320):
                    sub = chunk_int16[i:i+320]
                    if len(sub) == 320:
                        total_parts += 1
                        try:
                            if self.vad.is_speech(sub.tobytes(), self.sample_rate):
                                voice_parts += 1
                        except:
                            pass
                if total_parts > 0:
                    is_voice_frame = (voice_parts / total_parts) >= 0.6
        else:
            is_voice_frame = rms > 300.0

        # Hysteresis Logic for non-silero
        if is_voice_frame:
            self.speech_ms += chunk_ms
            self.silence_ms = 0.0
            if not self.is_speech_active and self.speech_ms >= self.start_hysteresis_ms:
                self.is_speech_active = True
        else:
            self.silence_ms += chunk_ms
            self.speech_ms = 0.0
            if self.is_speech_active and self.silence_ms >= self.max_silence_ms:
                self.is_speech_active = False
                
        return self.is_speech_active

    def get_preroll(self) -> np.ndarray:
        """Returns the pre-roll audio correctly ordered."""
        return np.concatenate((self.ring_buffer[self.ring_ptr:], self.ring_buffer[:self.ring_ptr]))
