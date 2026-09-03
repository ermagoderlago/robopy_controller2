#!/usr/bin/env python3
"""
Audio Buffer Manager
====================
Isolated, high-throughput PCM audio buffering & echo cancellation gateway.
Handles 16kHz mono int16 audio streams with oldest-drop queue policies,
software Acoustic Echo Suppression (AES), and real-time barge-in gating.

Author: Marcus AI Engineering Team
Version: 01.00.00
"""

import time
import struct
import math
import threading
from collections import deque
from typing import Optional, Callable, Dict, Any


class AudioBufferManager:
    """
    Manages raw 16kHz mono PCM audio chunks between microphone, speaker,
    and Gemini Live API WebSocket, mitigating GIL latency and audio feedback.
    """

    SAMPLE_RATE = 16000
    BYTES_PER_SAMPLE = 2  # 16-bit int16
    DEFAULT_CHUNK_SIZE = 960  # 3 frames of 320 samples (60ms)

    def __init__(
        self,
        max_mic_chunks: int = 50,
        max_speaker_chunks: int = 100,
        barge_in_energy_thresh: float = 0.18,
        echo_suppression_gain: float = 0.05
    ):
        self._max_mic_chunks = max_mic_chunks
        self._max_speaker_chunks = max_speaker_chunks
        self.barge_in_energy_thresh = barge_in_energy_thresh
        self.echo_suppression_gain = echo_suppression_gain

        # Thread-safe deques for non-blocking pop/push
        self._mic_deque = deque(maxlen=self._max_mic_chunks)
        self._speaker_deque = deque(maxlen=self._max_speaker_chunks)
        self._mic_lock = threading.Lock()
        self._speaker_lock = threading.Lock()

        # State flags
        self._is_speaker_playing = False
        self._speaker_start_time = 0.0
        self._barge_in_streak = 0
        self._barge_in_callback: Optional[Callable[[], None]] = None

        # Metrics
        self._mic_chunks_received = 0
        self._mic_chunks_dropped = 0
        self._speaker_chunks_received = 0
        self._speaker_chunks_played = 0

    def set_barge_in_callback(self, callback: Callable[[], None]):
        """Registers callback invoked when user speaks over active speaker output."""
        self._barge_in_callback = callback

    def set_speaker_playing(self, playing: bool):
        """Notifies the buffer manager whether TTS or Live API audio is playing on the physical DAC."""
        with self._speaker_lock:
            self._is_speaker_playing = playing
            if playing:
                self._speaker_start_time = time.time()
            else:
                self._barge_in_streak = 0

    def is_speaker_playing(self) -> bool:
        with self._speaker_lock:
            return self._is_speaker_playing

    @staticmethod
    def calculate_rms(raw_bytes: bytes) -> float:
        """Calculates normalized RMS energy for int16 PCM audio chunk [0.0, 1.0]."""
        if not raw_bytes or len(raw_bytes) < 2:
            return 0.0
        count = len(raw_bytes) // 2
        try:
            samples = struct.unpack(f"<{count}h", raw_bytes[:count * 2])
            sum_squares = sum(s * s for s in samples)
            mean_square = sum_squares / count
            rms = math.sqrt(mean_square)
            return min(1.0, rms / 32768.0)
        except Exception:
            return 0.0

    def push_mic_chunk(self, raw_bytes: bytes) -> bool:
        """
        Pushes raw microphone audio chunk into the input queue.
        Applies software echo suppression if speaker is playing.
        Returns True if chunk was queued, False if dropped due to echo cancellation.
        """
        if not raw_bytes:
            return False

        self._mic_chunks_received += 1
        rms = self.calculate_rms(raw_bytes)

        with self._speaker_lock:
            speaker_active = self._is_speaker_playing

        if speaker_active:
            # Check for user barge-in (speech energy significantly above echo)
            if rms >= self.barge_in_energy_thresh:
                self._barge_in_streak += 1
                if self._barge_in_streak >= 2:
                    # User is actively speaking over the speaker -> Trigger Barge-In
                    self.clear_speaker_buffer()
                    self._is_speaker_playing = False
                    self._barge_in_streak = 0
                    if self._barge_in_callback:
                        try:
                            self._barge_in_callback()
                        except Exception:
                            pass
            else:
                self._barge_in_streak = 0
                # Suppress acoustic feedback by dropping chunk during playback
                self._mic_chunks_dropped += 1
                return False

        with self._mic_lock:
            self._mic_deque.append(raw_bytes)
        return True

    def pop_mic_chunk(self) -> Optional[bytes]:
        """Pops the oldest mic chunk from the FIFO queue."""
        with self._mic_lock:
            if self._mic_deque:
                return self._mic_deque.popleft()
        return None

    def push_speaker_chunk(self, raw_bytes: bytes):
        """Pushes synthesized audio chunk into speaker output FIFO queue."""
        if not raw_bytes:
            return
        self._speaker_chunks_received += 1
        with self._speaker_lock:
            self._speaker_deque.append(raw_bytes)

    def pop_speaker_chunk(self) -> Optional[bytes]:
        """Pops the oldest speaker chunk from the FIFO queue for physical audio playback."""
        with self._speaker_lock:
            if self._speaker_deque:
                self._speaker_chunks_played += 1
                return self._speaker_deque.popleft()
        return None

    def clear_speaker_buffer(self):
        """Flushes all queued speaker chunks immediately (e.g. on interruption / barge-in)."""
        with self._speaker_lock:
            self._speaker_deque.clear()
            self._is_speaker_playing = False

    def clear_mic_buffer(self):
        """Flushes all queued microphone chunks."""
        with self._mic_lock:
            self._mic_deque.clear()

    def get_metrics(self) -> Dict[str, Any]:
        """Returns buffer health and throughput statistics."""
        with self._mic_lock:
            mic_queued = len(self._mic_deque)
        with self._speaker_lock:
            spk_queued = len(self._speaker_deque)
            spk_playing = self._is_speaker_playing

        return {
            "mic_queued": mic_queued,
            "mic_received": self._mic_chunks_received,
            "mic_dropped": self._mic_chunks_dropped,
            "speaker_queued": spk_queued,
            "speaker_received": self._speaker_chunks_received,
            "speaker_played": self._speaker_chunks_played,
            "speaker_active": spk_playing
        }
