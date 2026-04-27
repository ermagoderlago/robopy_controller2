"""
Robot AI Services - ASR Service
================================
Automatic Speech Recognition service using Google Cloud Speech or local fallback.
"""

import asyncio
import threading
import queue
import time
from typing import Any, Callable, Dict, Optional, Union

try:
    from google.cloud import speech
    HAS_GOOGLE_ASR = True
except ImportError:
    HAS_GOOGLE_ASR = False

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

from ..core.config_manager import ConfigManager
from ..core.exceptions import ASRError
from ..core.event_bus import EventBus, EventType
from ..core.circuit_breaker import CircuitBreakerRegistry
from ..utils.logging_utils import get_logger


class ASRService:
    """
    Automatic Speech Recognition service.
    
    Features:
    - Google Cloud Speech-to-Text streaming
    - Wake word detection (simulated or via external library)
    - Microphone stream handling
    - Voice activity detection (basic)
    
    Usage:
        asr = ASRService()
        asr.start_listening()
        
        # Subscribe to events
        event_bus.subscribe(EventType.VOICE_COMMAND_RECOGNIZED, on_command)
    """
    
    def __init__(self, config_manager: ConfigManager = None):
        self.logger = get_logger("asr_service")
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        self.event_bus = EventBus()
        
        # Audio stream
        self._audio_interface = None
        self._audio_stream = None
        self._is_listening = False
        self._stop_event = threading.Event()
        self._audio_queue = queue.Queue()
        
        # Google ASR Client
        self._client = None
        if self.ai_config.asr.enabled:
            self._setup_google_client()
        else:
            self.logger.info("Legacy Google ASR disabled in config")
        
        # Circuit breaker
        self._breaker = CircuitBreakerRegistry().get_or_create(
            "asr",
            failure_threshold=5,
            recovery_timeout=60
        )
        
        self.logger.info("ASR Service initialized")
    
    def _setup_google_client(self):
        """Setup Google Cloud ASR client."""
        if not HAS_GOOGLE_ASR:
            self.logger.warning("google-cloud-speech not installed")
            return
            
        try:
            self._client = speech.SpeechClient()
        except Exception as e:
            self.logger.warning(f"Failed to create Google ASR client: {e}")
    
    def start_listening(self) -> bool:
        """Start listening for voice commands."""
        if self._is_listening:
            return True
            
        if not HAS_PYAUDIO:
            self.logger.error("pyaudio not installed")
            return False
        
        try:
            self._audio_interface = pyaudio.PyAudio()
            self._audio_stream = self._audio_interface.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024,
                stream_callback=self._audio_callback
            )
            
            self._is_listening = True
            self._stop_event.clear()
            
            # Start processing thread
            self._thread = threading.Thread(target=self._process_audio, daemon=True)
            self._thread.start()
            
            self.event_bus.publish(EventType.ASR_STARTED, {})
            self.logger.info("ASR listening started")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start ASR: {e}")
            return False
    
    def stop_listening(self) -> None:
        """Stop listening."""
        self._is_listening = False
        self._stop_event.set()
        
        if self._audio_stream:
            self._audio_stream.stop_stream()
            self._audio_stream.close()
            self._audio_stream = None
            
        if self._audio_interface:
            self._audio_interface.terminate()
            self._audio_interface = None
            
        self.event_bus.publish(EventType.ASR_STOPPED, {})
        self.logger.info("ASR listening stopped")
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback for pyaudio stream."""
        if self._is_listening:
            self._audio_queue.put(in_data)
            # Publish raw audio chunk for ultra-low latency Live API
            self.event_bus.publish("asr_audio_chunk", {"data": in_data})
        return (None, pyaudio.paContinue)
    
    def _process_audio(self):
        """Process audio stream (background thread)."""
        if self._client and self.ai_config.asr.enabled:
            self._process_google_stream()
        else:
            if not self.ai_config.asr.enabled:
                self.logger.info("Old ASR engine disabled, only publishing raw chunks for Live API")
            else:
                self.logger.warning("No ASR engine available, audio ignored")
            # Drain queue
            while not self._stop_event.is_set():
                try:
                    self._audio_queue.get(timeout=1.0)
                except queue.Empty:
                    pass
    
    def _generator(self):
        """Audio generator for Google API."""
        while not self._stop_event.is_set():
            try:
                # Get chunk from queue
                chunk = self._audio_queue.get(timeout=0.1)
                yield chunk
            except queue.Empty:
                continue
    
    def _process_google_stream(self):
        """Stream audio to Google Cloud Speech."""
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=self.ai_config.asr.language,
        )
        
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True  # Get partial results
        )
        
        while not self._stop_event.is_set():
            try:
                requests = (
                    speech.StreamingRecognizeRequest(audio_content=content)
                    for content in self._generator()
                )
                
                responses = self._client.streaming_recognize(streaming_config, requests)
                
                for response in responses:
                    if self._stop_event.is_set():
                        break
                        
                    if not response.results:
                        continue
                        
                    result = response.results[0]
                    if not result.alternatives:
                        continue
                        
                    transcript = result.alternatives[0].transcript.strip()
                    
                    if result.is_final:
                        self.logger.info(f"Recognized: {transcript}")
                        
                        # Check wake word if configured
                        # Note: Streaming wake word detection is better done locally (e.g. Porcupine)
                        # This checks if the full sentence starts with wake word
                        wake_word = self.ai_config.asr.wake_word
                        if wake_word and not transcript.lower().startswith(wake_word.lower()):
                            if not self._is_listening_continuously():
                                continue
                        
                        self.event_bus.publish(EventType.VOICE_COMMAND_RECOGNIZED, {
                            "text": transcript,
                            "confidence": result.alternatives[0].confidence
                        })
                    else:
                        # Interim result
                        pass
                        
            except Exception as e:
                self.logger.error(f"ASR stream error: {e}")
                time.sleep(1.0)  # Wait before retrying
    
    def _is_listening_continuously(self) -> bool:
        """Check if we should listen without wake word (e.g. during conversation)."""
        # TODO: Implement conversation mode state check
        return False
