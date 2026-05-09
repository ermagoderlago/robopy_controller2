"""
Robot AI Services - TTS Service
================================
Text-to-Speech service using Google Cloud TTS or local fallback.
"""

import asyncio
import os
import tempfile
import threading
from typing import Any, Dict, Optional, Union
import base64

try:
    from google.cloud import texttospeech
    HAS_GOOGLE_TTS = True
except ImportError:
    HAS_GOOGLE_TTS = False

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

try:
    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

from ..core.config_manager import ConfigManager
from ..core.exceptions import TTSError
from ..core.event_bus import EventBus, EventType
from ..core.circuit_breaker import CircuitBreakerRegistry
from ..utils.logging_utils import get_logger


class TTSService:
    """
    Text-to-Speech service.
    
    Features:
    - Google Cloud TTS execution
    - Pygame audio playback
    - Caching of synthesized audio
    - Fallback mechanisms
    
    Usage:
        tts = TTSService()
        await tts.speak("Ciao, come stai?")
    """
    
    def __init__(self, config_manager: ConfigManager = None):
        self.logger = get_logger("tts_service")
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        self.event_bus = EventBus()
        
        # Audio playback init
        if HAS_PYGAME:
            try:
                pygame.mixer.init()
            except Exception as e:
                self.logger.error(f"Failed to init pygame mixer: {e}")
        
        # PyAudio output for raw streams
        self._pyaudio_interface = None
        self._output_stream = None
        self._respeaker_device_index = None
        if HAS_PYAUDIO:
            try:
                self._pyaudio_interface = pyaudio.PyAudio()
                self._respeaker_device_index = self._find_respeaker_device_index()
                if self._respeaker_device_index is not None:
                    self.logger.info(
                        f"🔊 ReSpeaker speaker trovato (PyAudio device #{self._respeaker_device_index}). "
                        "Uscita audio instradata sul ReSpeaker."
                    )
                self._output_stream = self._pyaudio_interface.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    output=True,
                    output_device_index=self._respeaker_device_index  # None = default
                )
            except Exception as e:
                self.logger.error(f"Failed to init pyaudio output: {e}")

        # Pygame: re-init su device ALSA ReSpeaker se disponibile
        if HAS_PYGAME:
            try:
                respeaker_alsa = self._find_respeaker_alsa_card()
                if respeaker_alsa is not None:
                    os.environ['SDL_AUDIODRIVER'] = 'alsa'
                    os.environ['AUDIODEV'] = respeaker_alsa
                    self.logger.info(f"🔊 Pygame SDL audio → ReSpeaker ALSA ({respeaker_alsa})")
                pygame.mixer.init()
            except Exception as e:
                self.logger.error(f"Failed to init pygame mixer: {e}")

        
        # Google TTS Client
        self._client = None
        self._setup_google_client()
        
        # State
        self._is_speaking = False
        self._lock = asyncio.Lock()
        
        # Cache
        self._cache_dir = os.path.join(tempfile.gettempdir(), "robot_tts_cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        
        # Circuit breaker
        self._breaker = CircuitBreakerRegistry().get_or_create(
            "tts",
            failure_threshold=5,
            recovery_timeout=60
        )
        
        self.logger.info("TTS Service initialized")
    
    def _find_respeaker_device_index(self) -> Optional[int]:
        """
        Scansiona i dispositivi PyAudio e restituisce l'indice del primo
        che corrisponde al ReSpeaker / Seeed / ESP32 USB Audio.
        Restituisce None se non trovato (fallback: default del sistema).
        """
        if not self._pyaudio_interface:
            return None
        keywords = ['respeaker', 'seeed', 'esp32', 'esp32s3', 'xiao', 'uac']
        try:
            for i in range(self._pyaudio_interface.get_device_count()):
                info = self._pyaudio_interface.get_device_info_by_index(i)
                if info.get('maxOutputChannels', 0) > 0:
                    name = info.get('name', '').lower()
                    if any(k in name for k in keywords):
                        return i
        except Exception:
            pass
        return None

    def _find_respeaker_alsa_card(self) -> Optional[str]:
        """
        Cerca la scheda ALSA del ReSpeaker leggendo /proc/asound/cards.
        Restituisce una stringa tipo 'hw:2,0' o None.
        """
        import re
        try:
            with open('/proc/asound/cards', 'r') as f:
                content = f.read()
            keywords = ['respeaker', 'seeed', 'esp32', 'xiao', 'uac']
            for line in content.splitlines():
                if any(k in line.lower() for k in keywords):
                    match = re.match(r'\s*(\d+)', line)
                    if match:
                        return f"hw:{match.group(1)},0"
        except Exception:
            pass
        return None

    def _setup_google_client(self):
        """Setup Google Cloud TTS client."""
        if not HAS_GOOGLE_TTS:
            self.logger.warning("google-cloud-texttospeech not installed")
            return
            
        # Check for credentials
        # Ideally, GOOGLE_APPLICATION_CREDENTIALS should be set
        # If not, we might check for API key (though Google Cloud usually prefers service account json)
        # For this implementation, we assume environment is set up or we can construct client specific ways
        try:
            self._client = texttospeech.TextToSpeechClient()
        except Exception as e:
            self.logger.warning(f"Failed to create Google TTS client: {e}")
    
    async def speak(self, text: str, language: str = None, priority: bool = False) -> bool:
        """
        Synthesize and speak text.
        
        Args:
            text: Text to speak
            language: Language code (default: from config)
            priority: If true, interrupt current speech
            
        Returns:
            True if successful
        """
        if not text:
            return False
            
        async with self._lock:
            # Stop current if priority
            if self._is_speaking and priority:
                self.stop()
            
            # If still speaking and not priority, wait or skip?
            # Simple queueing behavior could be implemented here
            while self._is_speaking:
                await asyncio.sleep(0.1)
            
            self._is_speaking = True
            self.event_bus.publish(EventType.TTS_STARTED, {"text": text})
            
            try:
                # 1. Check cache or Synthesize
                audio_file = await self._breaker.call_async(
                    self._synthesize, text, language
                )
                
                # 2. Play
                await self._play_audio(audio_file)
                
                self.event_bus.publish(EventType.TTS_COMPLETED, {"text": text})
                return True
                
            except Exception as e:
                self.logger.error(f"TTS failed: {e}")
                self.event_bus.publish(EventType.TTS_FAILED, {"error": str(e)})
                return False
                
            finally:
                self._is_speaking = False
    
    async def _synthesize(self, text: str, language: str = None) -> str:
        """Synthesize text to audio file."""
        import hashlib
        
        lang = language or self.ai_config.tts.language
        text_hash = hashlib.md5(f"{text}_{lang}".encode()).hexdigest()
        filename = os.path.join(self._cache_dir, f"{text_hash}.mp3")
        
        # Return cached if exists
        if os.path.exists(filename):
            return filename
        
        if self._client:
            # Perform Google Cloud TTS request
            try:
                synthesis_input = texttospeech.SynthesisInput(text=text)
                
                voice = texttospeech.VoiceSelectionParams(
                    language_code=lang,
                    name=self.ai_config.tts.voice
                )
                
                audio_config = texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.MP3,
                    speaking_rate=self.ai_config.tts.speaking_rate,
                    pitch=self.ai_config.tts.pitch
                )
            
                response = await asyncio.to_thread(
                    self._client.synthesize_speech,
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config
                )
                
                with open(filename, "wb") as out:
                    out.write(response.audio_content)
                    
                return filename
                
            except Exception as e:
                self.logger.error(f"Google TTS synthesis error: {e}")
                raise TTSError(f"Synthesis failed: {e}")
        else:
            # Console fallback
            self.logger.info(f"[CONSOLE TTS] {text}")
            return "CONSOLE_TTS"
    
    async def _play_audio(self, filename: str) -> None:
        """Play audio file."""
        if filename == "CONSOLE_TTS":
            return

        if not HAS_PYGAME:
            self.logger.warning("Cannot play audio: pygame not installed")
            return
        
        try:
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            
            # Wait for completion
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
                
        except Exception as e:
            self.logger.error(f"Audio playback error: {e}")
            raise TTSError(f"Playback failed: {e}")

    def play_raw_pcm(self, data: bytes) -> None:
        """
        Play raw PCM chunks (16kHz, mono, s16le).
        Used for Gemini Live ultra-low latency audio.
        """
        if self._output_stream:
            try:
                self._output_stream.write(data)
            except Exception as e:
                self.logger.error(f"Failed to play raw PCM chunk: {e}")
        else:
            self.logger.debug("Raw PCM ignored: no audio output stream available")

    
    def stop(self) -> None:
        """Stop current playback."""
        if HAS_PYGAME and pygame.mixer.get_init():
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self._is_speaking = False
