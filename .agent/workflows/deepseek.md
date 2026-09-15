# 🤖 MARCUS v2.0 – COMPLETE IMPLEMENTATION BLUEPRINT
## Robot Domestico Autonomo Empatico con Auto-Miglioramento

**Versione**: 2.0  
**Data**: 2026-02-15  
**Creatore**: Luca Suffia (Capofamiglia)  
**Status**: Production-Ready Architecture + Implementation Plan  
**Destinatario**: Team di sviluppo AI per implementazione ROS 2

---

## TABLE OF CONTENTS

1. [Executive Summary](#executive-summary)
2. [Analisi Critica del Sistema](#analisi-critica)
3. [Criticità Residue](#criticità-residue)
4. [Miglioramenti Implementati](#miglioramenti)
5. [Piano d'Azione Implementativo (9 Fasi)](#piano-azione)
6. [Integrazione DeepSeek](#deepseek)
7. [Architettura Tecnica](#architettura)
8. [Database Schema](#schema)
9. [Configurazione](#configurazione)
10. [Checklist Implementazione](#checklist)

---

## EXECUTIVE SUMMARY

Marcus è un **robot domestico completamente autonomo** creato da **Luca Suffia** con le seguenti caratteristiche core:

### Caratteristiche Principali

✅ **Cervello Cloud-First**
- Gemini 2.5 Flash per processamento in tempo reale
- DeepSeek per analisi notturne e auto-miglioramento
- Fallback locale con Ollama se Gemini down
- Live API per streaming video/audio

✅ **Percezione Multimodale**
- Riconoscimento facciale (face_recognition library)
- Emozioni da volto, voce (ASR + prosodia), testo (sentiment)
- Visual memory integrata con RTAB-Map
- Wake word detection (Porcupine)

✅ **Autonomia e Intelligenza**
- Skill registry con hot-reload (plugin system)
- Home Assistant integration (luci, tapparelle, clima)
- Navigazione autonoma (ROS 2 Nav2)
- Search capability (riconoscimento visivo + movimento)

✅ **Auto-Miglioramento Governato**
- Nightly dream analysis (Gemini → DeepSeek → Gemini → DeepSeek)
- Master prompt dinamico che si evolve
- KPI tracking e comparazione
- Rollback automatico se regressione > 15%

✅ **Personalizzazione per Famiglia**
- Profili utente dinamici (Luca, Isabella, Edoardo, etc.)
- Adattamento tono e proattività per persona
- Memoria emotiva e preferenze

✅ **Sicurezza Strutturata**
- Gerarchia di priorità (safety > privacy > feedback > commands)
- Matrice di rischio (4 classi)
- Conferma obbligatoria per azioni critiche
- Encryption per dati sensibili

---

## ANALISI CRITICA DEL SISTEMA

### ✅ PUNTI DI FORZA CONFERMATI

| Aspetto | Valutazione | Motivo |
|---------|------------|--------|
| **LLMService** | 🟢 Eccellente | Live API, retry, circuit breaker, two-staged reasoning |
| **NightlyDreamService** | 🟢 Eccellente | Auto-analisi continua, salvataggio memoria + file |
| **RAG** | 🟢 Completo | Metadata, ricerca semantica, embedding 3072D |
| **Skill Registry** | 🟢 Estensibile | Hot-reload, plugin system ready |
| **Face Recognition** | 🟢 Buono | Integrata con profili utente |
| **Visual Memory** | 🟢 Innovativo | Arricchisce mappa RTAB-Map |

### 🔴 CRITICITÀ RESIDUE IDENTIFICATE

#### 1. THREAD SAFETY (CRITICO)

**Problema**: In `AIOrchestrator`, `_latest_frame` è scritto in ROS callback e letto in asyncio loop senza lock.

```python
# ❌ RISCHIO RACE CONDITION
def _camera_callback(self, msg):
    self._latest_frame = convert_ros_image(msg)  # Scritto da thread ROS

async def _visual_analysis_loop(self):
    frame = self._latest_frame  # Letto da asyncio
    # Cosa se il frame è a metà copia?
```

**Soluzione**:
```python
# ✅ PROTETTO CON LOCK
import asyncio

class AIOrchestrator:
    def __init__(self):
        self._frame_lock = asyncio.Lock()
        self._latest_frame = None
    
    def _camera_callback(self, msg):
        # Da ROS callback (sync), non posso usare await
        # Uso evento per segnalare al loop asyncio
        self.frame_update_event.set()
        self._latest_frame_pending = convert_ros_image(msg)
    
    async def _visual_analysis_loop(self):
        while True:
            await self.frame_update_event.wait()
            async with self._frame_lock:
                self._latest_frame = self._latest_frame_pending.copy()
            # Processa frame in sicurezza
            await self._analyze_frame(self._latest_frame)
```

#### 2. CONFIGURAZIONE HARDCODED (ALTO)

**Problema**: Soglie come `15s` in visual memory sono hardcoded in codice.

**Soluzione**: Vedi sezione [Configurazione](#configurazione) – tutto in `config.yaml`.

#### 3. LIVE API DISCONNESSIONE (MEDIO)

**Problema**: In `llm_service.py`, `_disconnect_live_unsafe` chiama `__aexit__` con `None` – funziona ma non testato.

**Soluzione**: 
```python
async def _disconnect_live_unsafe(self):
    """Disconetti la sessione Live API in modo sicuro"""
    if self.live_session:
        try:
            await self.live_session.__aexit__(None, None, None)
        except Exception as e:
            logger.error(f"Error disconnecting Live API: {e}")
        finally:
            self.live_session = None
```

**Test**: Aggiungi test che simula cambio system prompt e verifica riconnessione.

#### 4. PARSING JSON FRAGILE (MEDIO)

**Problema**: In `_parse_response`, estrae JSON da testo – se Gemini non rispetta formato, fallisce.

**Soluzione**:
```python
def _parse_response(self, text):
    """Estrai JSON con fallback graceful"""
    try:
        # Tenta estrazione JSON standard
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError:
        pass
    
    # Fallback: restituisci testo grezzo
    return {
        "text": text,
        "actions": [],
        "format": "fallback_text"
    }
```

#### 5. ASR WAKE WORD TROPPO SEMPLICE (MEDIO)

**Problema**: Wake word detection fatto con `if "marcus" in text.lower()` – troppo sensibile a falsi positivi.

**Soluzione**: Integrare **Porcupine** (vedi sezione [Fase 2](#fase-2-servizi)).

#### 6. CACHE TTS SENZA PULIZIA (BASSO)

**Problema**: Cache TTS cresce infinitamente, riempie disco.

**Soluzione**:
```python
async def cleanup_old_cache(self, max_age_days=7):
    """Pulisci cache TTS > 7 giorni"""
    cache_dir = Path(self.cache_dir)
    now = time.time()
    for file in cache_dir.glob("*.mp3"):
        if (now - file.stat().st_mtime) / 86400 > max_age_days:
            file.unlink()
```

---

## CRITICITÀ RESIDUE E SOLUZIONI

| ID | Criticità | Severità | Stato | Soluzione |
|----|-----------|---------|----|-----------|
| C1 | Thread safety _latest_frame | 🔴 CRITICO | Open | Lock + asyncio.Event |
| C2 | Hardcoded config | 🟠 ALTO | Open | ConfigManager (vedi sotto) |
| C3 | Live API disconnect untested | 🟡 MEDIO | Open | Unit test + fixture |
| C4 | JSON parsing fragile | 🟡 MEDIO | Open | Regex + fallback |
| C5 | Wake word detection | 🟡 MEDIO | Open | Porcupine integration |
| C6 | TTS cache cleanup | 🟣 BASSO | Open | Scheduled cleanup task |

---

## MIGLIORAMENTI CONSIGLIATI

### 1. FALLBACK LLM INTELLIGENTE

Se Gemini è down → Ollama fallback locale:

```python
class LLMWithFallback:
    async def generate(self, prompt, **kwargs):
        # Turno 1: Cache
        cached = await self.cache.get(hash(prompt))
        if cached:
            return cached
        
        # Turno 2: Gemini
        try:
            result = await self.gemini.generate(prompt, **kwargs)
            await self.cache.set(hash(prompt), result, ttl=3600)
            return result
        except Exception as e:
            logger.warn(f"Gemini failed: {e}")
        
        # Turno 3: Ollama fallback
        try:
            result = await self.ollama.generate(prompt)
            return result
        except Exception as e:
            logger.error(f"Ollama failed: {e}")
        
        # Turno 4: Template response
        return "Scusa, ho un problema momentaneo. Riprova fra poco?"
```

### 2. MEMORY MANAGEMENT (RAG OTTIMIZZATO)

Memoria non dovrebbe crescere infinitamente:

```python
async def build_rag_context(user_id, query, days=7):
    """Retrieve memories rilevanti con pruning"""
    # Fetch ultimi N giorni
    recent = await chromadb.search(
        query_text=query,
        metadata_filter={"user_id": user_id, "days_ago": {"$lte": days}},
        limit=5
    )
    
    # Se troppi token, summarize
    if count_tokens(recent) > 4000:
        recent = await summarize_memories(recent, max_tokens=2000)
    
    # Archive memories > 90 giorni
    await archive_old_memories(user_id, days=90)
    
    return recent
```

### 3. RATE LIMITING & CACHING

Protezione da spike API e controllo costi:

```python
class RateLimiter:
    def __init__(self):
        self.limits = {
            "gemini_chat": 100,      # 100 req/min
            "gemini_vision": 20,     # 20 img/min
            "home_assistant": 50,    # 50 actions/min
        }
    
    async def check_limit(self, service, count=1):
        key = f"{service}:{current_minute()}"
        current = await redis.incr(key, count)
        if current == count:
            await redis.expire(key, 60)
        
        if current > self.limits[service]:
            raise RateLimitError(f"{service} exceeded")
```

### 4. HEALTH CHECK & MONITORING

Sistema di controllo salute robot:

```python
class HealthChecker:
    async def check_all(self):
        results = {
            "robot": await self.check_robot(),
            "camera": await self.check_camera(),
            "microphone": await self.check_microphone(),
            "gemini": await self.check_gemini(),
            "home_assistant": await self.check_ha(),
            "chromadb": await self.check_chromadb(),
            "disk": await self.check_disk(),
        }
        
        # Alert se qualcosa rosso
        for service, status in results.items():
            if status["status"] == "down":
                await notify_user(f"⚠️ {service} non disponibile")
        
        return results
```

### 5. PRIVACY & ENCRYPTION

Protezione dati sensibili prima di inviare a Gemini:

```python
class PrivacyManager:
    async def send_to_gemini(self, prompt, contains_pii=False):
        if contains_pii:
            prompt_anon, mappings = self.anonymize(prompt)
            response = await gemini.generate(prompt_anon)
            return self.deanonymize(response, mappings)
        return await gemini.generate(prompt)
    
    def anonymize(self, text):
        """Sostituisci nomi con token"""
        mappings = {}
        for user in ["Luca", "Isabella", "Edoardo"]:
            token = f"USER_{len(mappings)}"
            mappings[token] = user
            text = text.replace(user, token)
        return text, mappings
```

### 6. SCENARIO TESTING

Suite di test automatizzati per edge case:

```python
@pytest.mark.asyncio
async def test_user_angry_light_failed():
    """Utente arrabbiato perché luce non spenta"""
    ctx = Context(
        user_id="luca",
        emotion="angry",
        emotion_confidence=0.9,
        last_action="turn_off light.soggiorno",
        last_action_result=False,
    )
    
    response = await marcus.process_input("ACCENDI LA LUCE!", context=ctx)
    
    # Assert: Marcus empatico
    assert "scusa" in response.lower() or "mi dispiace" in response.lower()
    assert len(response) < 200  # Breve quando arrabbiato
```

### 7. SKILL PLUGIN SYSTEM

Far sì che chiunque possa aggiungere skill facilmente:

```python
class BaseSkill:
    """Classe base per tutte le skill"""
    name: str
    description: str
    risk_level: str  # "low", "medium", "high", "critical"
    
    async def can_execute(self, context: Context) -> bool:
        raise NotImplementedError
    
    async def execute(self, params: dict) -> SkillResult:
        raise NotImplementedError
```

---

## PIANO D'AZIONE IMPLEMENTATIVO

### Fase 0: SETUP E DIPENDENZE

**Durata**: 2 giorni  
**Responsabile**: DevOps / Setup

**Compiti**:

1. Creare `requirements.txt`:
```txt
rclpy
sensor_msgs geometry_msgs std_msgs nav_msgs
opencv-python
numpy
chromadb
google-genai
google-cloud-texttospeech
google-cloud-speech
face_recognition
pygame
apscheduler
tensorflow  # Per emotional recognition
pydantic
pyyaml
aiohttp
redis
pytest pytest-asyncio
pvporcupine  # Wake word
```

2. Creare script `setup_keys.sh`:
```bash
#!/bin/bash
export GEMINI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export HA_TOKEN="eyJhbGc..."
export GOOGLE_APPLICATION_CREDENTIALS="/home/robopy/.gcp/credentials.json"
export ENCRYPTION_KEY="$(openssl rand -base64 32)"
```

3. Struttura directory:
```
definisci tu!

### Fase 1: CORE INFRASTRUCTURE

**Durata**: 1 settimana  
**Responsabile**: Platform Engineer

**Compiti**:

#### 1.1 ConfigManager

```python
# marcus_core/src/config_manager.py

import yaml
import os
from pathlib import Path
from typing import Any

class ConfigManager:
    def __init__(self, config_path="~/robopy/config.yaml"):
        self.config_path = Path(config_path).expanduser()
        self.config = {}
        self.load()
    
    def load(self):
        """Carica config da YAML e risolvi !env"""
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)
        self._resolve_env()
        return self.config
    
    def _resolve_env(self, d=None):
        """Sostituisci !env VAR con environment variables"""
        if d is None:
            d = self.config
        for k, v in list(d.items()):
            if isinstance(v, dict):
                self._resolve_env(v)
            elif isinstance(v, str) and v.startswith("!env "):
                env_var = v[5:].strip()
                d[k] = os.environ.get(env_var, "")
    
    def get(self, key: str, default=None) -> Any:
        """Accedi a config con dot notation: get('llm.model')"""
        keys = key.split('.')
        val = self.config
        for k in keys:
            val = val.get(k, {})
        return val if val else default
    
    def reload(self):
        """Ricarica config a runtime"""
        self.load()
```

#### 1.2 config.yaml ESEMPIO

```yaml
robot:
  name: "Marcus"
  full_name: "Multi-purpose Autonomous Robotic Companion for User Support"
  creator: "Luca Suffia"
  model: "RPi5 + OAK-D"
  version: "2.0"

memory:
  persist_dir: "/home/robopy/data/chromadb"
  collection_name: "robot_memories"
  embedding_dimension: 3072
  top_k: 5
  min_score: 0.7
  archive_after_days: 90

llm:
  model: "gemini-2.0-flash-exp"
  temperature: 0.7
  max_tokens: 1024
  timeout_seconds: 30
  circuit_breaker_threshold: 3
  circuit_breaker_recovery: 60

deepseek:
  enabled: true
  api_key: "!env DEEPSEEK_API_KEY"
  model: "deepseek-chat"
  temperature: 0.5
  max_tokens: 8192

face_recognition:
  enabled: true
  known_faces_dir: "/home/robopy/data/known_faces"
  tolerance: 0.5
  confidence_high: 0.8
  confidence_low: 0.6
  recognition_interval_seconds: 2.0

visual_memory:
  enabled: true
  analysis_interval_seconds: 15.0
  min_motion_threshold: 0.05
  min_angular_threshold: 0.1

asr:
  enabled: true
  language: "it-IT"
  wake_word: "marcus"
  wake_word_confidence: 0.8

tts:
  language: "it-IT"
  voice: "it-IT-Wavenet-A"
  speaking_rate: 1.0
  pitch: 0.0
  cache_dir: "/home/robopy/data/tts_cache"
  cache_cleanup_days: 7

home_assistant:
  url: "http://homeassistant.local:8123"
  token: "!env HA_TOKEN"
  timeout_seconds: 10

nightly_dream:
  enabled: true
  schedule: "0 3 * * *"  # 03:00
  use_deepseek: true
  prompt_history_size: 10
  auto_rollback_threshold: 0.15

kpi:
  track_metrics: true
  metrics:
    - skill_success_rate
    - response_latency
    - feedback_sentiment
    - emotion_change
    - proactive_acceptance
    - uptime

security:
  allowed_action_types:
    - light
    - cover
    - climate
    - navigate
    - search
    - speak
  encryption_enabled: true
```

#### 1.3 EventBus

```python
# marcus_core/src/event_bus.py

import asyncio
from typing import Callable, Any
from dataclasses import dataclass

@dataclass
class Event:
    type: str
    data: Any
    timestamp: float

class EventBus:
    def __init__(self):
        self.subscribers = {}
        self.queue = asyncio.Queue()
    
    def subscribe(self, event_type: str, handler: Callable):
        """Sottoscrivi a un tipo di evento"""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
    
    async def publish(self, event: Event):
        """Pubblica evento (async)"""
        await self.queue.put(event)
        handlers = self.subscribers.get(event.type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Error in event handler: {e}")
    
    def publish_sync(self, event: Event):
        """Pubblica evento da sync context (es. ROS callback)"""
        # Metti in queue per processing async
        asyncio.run_coroutine_threadsafe(
            self.publish(event),
            self.loop
        )
```

#### 1.4 StateMachine

```python
# marcus_core/src/state_machine.py

from enum import Enum
from typing import Callable

class RobotState(Enum):
    BOOTING = "booting"
    INITIALIZING = "initializing"
    READY = "ready"
    PROCESSING = "processing"
    LISTENING = "listening"
    ERROR = "error"
    SHUTDOWN = "shutdown"

class StateMachine:
    def __init__(self):
        self.state = RobotState.BOOTING
        self.callbacks = {}
    
    def on_transition(self, from_state: RobotState, to_state: RobotState, callback: Callable):
        """Register callback on state transition"""
        key = (from_state, to_state)
        self.callbacks[key] = callback
    
    async def transition(self, new_state: RobotState):
        """Transizione di stato sicura"""
        if self.state == new_state:
            return
        
        old = self.state
        self.state = new_state
        
        key = (old, new_state)
        if key in self.callbacks:
            await self.callbacks[key]()
        
        logger.info(f"State transition: {old.value} → {new_state.value}")
```

---

### Fase 2: SERVIZI

**Durata**: 2 settimane  
**Responsabile**: AI Engineer

#### 2.1 LLMService (Migliorato)

```python
# marcus_core/src/llm_service.py

class LLMService:
    def __init__(self, config):
        self.config = config
        self.client = genai.Client(api_key=config.get('llm.api_key'))
        self.live_session = None
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=config.get('llm.circuit_breaker_threshold', 3),
            recovery_timeout=config.get('llm.circuit_breaker_recovery', 60)
        )
    
    async def generate(self, prompt: str, **kwargs) -> str:
        """Standard request con fallback"""
        await self.circuit_breaker.call()
        
        try:
            response = await asyncio.wait_for(
                self._generate_with_retry(prompt, **kwargs),
                timeout=self.config.get('llm.timeout_seconds', 30)
            )
            self.circuit_breaker.record_success()
            return response
        except Exception as e:
            self.circuit_breaker.record_failure()
            raise
    
    async def generate_live(self, system_prompt: str):
        """Live API per streaming"""
        if self.live_session:
            await self._disconnect_live_unsafe()
        
        self.live_session = await self.client.aio.live.connect(
            model=self.config.get('llm.model'),
            system_instruction=system_prompt
        )
        return self.live_session
    
    async def _disconnect_live_unsafe(self):
        """Disconetti Live API con error handling"""
        if self.live_session:
            try:
                await self.live_session.__aexit__(None, None, None)
            except Exception as e:
                logger.error(f"Error disconnecting Live API: {e}")
            finally:
                self.live_session = None
```

#### 2.2 ASRService (Con Porcupine)

```python
# marcus_perception/src/asr_service.py

import pvporcupine

class ASRService:
    def __init__(self, config):
        self.config = config
        
        # Wake word detector
        self.porcupine = pvporcupine.create(
            keywords=["marcus"],
            access_key="YOUR_PORCUPINE_KEY"  # Da env
        )
        
        # Google Speech-to-Text
        self.speech_client = speech_v1.SpeechClient()
    
    async def listen_for_wakeword(self):
        """Ascolta finché non sente 'marcus'"""
        with recorder.Recorder() as mic:
            while True:
                pcm = mic.read()
                if self.porcupine.process(pcm) >= 0:
                    logger.info("Wake word detected!")
                    return await self.transcribe_audio()
    
    async def transcribe_audio(self) -> str:
        """Trascrivi audio in testo"""
        # Google Cloud Speech
        config = speech_v1.RecognitionConfig(
            language_code="it-IT",
            audio_channel_count=1,
        )
        audio = speech_v1.RecognitionAudio(content=audio_content)
        response = self.speech_client.recognize(config=config, audio=audio)
        
        return response.results[0].alternatives[0].transcript if response.results else ""
```

#### 2.3 TTS Service (Con Cleanup)

```python
# marcus_perception/src/tts_service.py

class TTSService:
    def __init__(self, config):
        self.config = config
        self.client = texttospeech.TextToSpeechClient()
        self.cache_dir = Path(config.get('tts.cache_dir'))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    async def speak(self, text: str, emotion: str = "neutral"):
        """Sintetizza e parla testo"""
        cache_key = hashlib.md5(text.encode()).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.mp3"
        
        if cache_file.exists():
            return await self._play_file(cache_file)
        
        # Sintetizza
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=self.config.get('tts.language'),
            name=self.config.get('tts.voice'),
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        
        response = self.client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        
        # Salva e parla
        cache_file.write_bytes(response.audio_content)
        await self._play_file(cache_file)
    
    async def cleanup_old_cache(self):
        """Pulisci cache > 7 giorni"""
        max_age = self.config.get('tts.cache_cleanup_days', 7)
        now = time.time()
        for f in self.cache_dir.glob("*.mp3"):
            if (now - f.stat().st_mtime) / 86400 > max_age:
                f.unlink()
```

#### 2.4 EmotionService

```python
# marcus_perception/src/emotion_service.py

class EmotionService:
    def __init__(self, config):
        self.config = config
        self.face_detector = FaceRecognition(config)
    
    async def detect_emotion(self, frame, audio_chunk=None):
        """Rileva emozione da volto, voce, testo"""
        emotions = {}
        
        # Da volto
        if self.config.get('face_recognition.enabled'):
            face_emo = await self.face_detector.detect_emotion(frame)
            emotions['face'] = face_emo
        
        # Da voce (prosodica)
        if audio_chunk and self.config.get('asr.enabled'):
            voice_emo = await self._analyze_prosody(audio_chunk)
            emotions['voice'] = voice_emo
        
        # Aggrega
        return self._aggregate_emotions(emotions)
    
    def _aggregate_emotions(self, emotions):
        """Combina emotion da fonti diverse"""
        scores = {}
        for source, emo in emotions.items():
            for label, confidence in emo.items():
                scores[label] = scores.get(label, 0) + (confidence * 0.5)
        
        # Normalizza e restituisci dominante
        primary = max(scores, key=scores.get)
        confidence = scores[primary]
        
        return {
            "emotion": primary,
            "confidence": confidence,
            "sources": list(emotions.keys())
        }
```

---

### Fase 3: RAG E MEMORIA

**Durata**: 1 settimana  
**Responsabile**: Data Engineer

#### 3.1 Memory Store Migliorato

```python
# marcus_core/src/memory_service.py

class MemoryStore:
    def __init__(self, config):
        self.client = chromadb.Client()
        self.collection = self.client.get_or_create_collection(
            name=config.get('memory.collection_name'),
            metadata={"hnsw:space": "cosine"}
        )
    
    async def store(self, text: str, user_id: str, memory_type: str, metadata: dict = None):
        """Salva memoria con metadata"""
        embedding = await self.embed(text)
        self.collection.add(
            ids=[uuid.uuid4().hex],
            embeddings=[embedding],
            documents=[text],
            metadatas=[{
                "user_id": user_id,
                "type": memory_type,
                "created_at": time.time(),
                **(metadata or {})
            }]
        )
    
    async def retrieve(self, query: str, user_id: str, limit: int = 5):
        """Retrieve memories rilevanti"""
        results = self.collection.query(
            query_texts=[query],
            where={"user_id": user_id},
            n_results=limit
        )
        return results
    
    async def archive_old(self, days: int = 90):
        """Archvia memories > N giorni"""
        cutoff = time.time() - (days * 86400)
        # ChromaDB delete con filter
        self.collection.delete(
            where={"created_at": {"$lt": cutoff}}
        )
```

---

### Fase 4: INTEGRAZIONI

**Durata**: 2 settimane  
**Responsabile**: Integration Engineer

#### 4.1 Home Assistant Client

```python
# marcus_core/src/home_assistant_client.py

class HomeAssistantClient:
    def __init__(self, config):
        self.url = config.get('home_assistant.url')
        self.token = config.get('home_assistant.token')
        self.session = None
    
    async def get_states(self):
        """Ottieni stato di tutti i device"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.url}/api/states",
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                return await resp.json()
    
    async def call_service(self, domain: str, service: str, entity_id: str, data: dict = None):
        """Chiama un servizio HA (es. light.turn_on)"""
        payload = {"entity_id": entity_id, **(data or {})}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.url}/api/services/{domain}/{service}",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                return await resp.json()
```

---

### Fase 5: SKILLS

**Durata**: 1 settimana  
**Responsabile**: Skills Developer

#### 5.1 BaseSkill Framework

```python
# marcus_skills/src/base_skill.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

@dataclass
class SkillResult:
    success: bool
    output: str
    actions: list = None
    metadata: dict = None

class BaseSkill(ABC):
    name: str
    description: str
    risk_level: str  # low, medium, high, critical
    
    @abstractmethod
    async def can_execute(self, context: dict) -> bool:
        """Puoi eseguire questa skill?"""
        pass
    
    @abstractmethod
    async def execute(self, params: dict) -> SkillResult:
        """Esegui skill"""
        pass
    
    async def execute_streaming(self, params: dict) -> AsyncGenerator:
        """Genera risultati in streaming (per task lunghi)"""
        result = await self.execute(params)
        yield result
```

#### 5.2 Skill Esempi

```python
# marcus_skills/src/skill_home_assistant.py

class HomeAssistantSkill(BaseSkill):
    name = "home_assistant"
    description = "Controlla luci, tapparelle, clima"
    risk_level = "medium"
    
    async def can_execute(self, context) -> bool:
        return context.get("ha_available", False)
    
    async def execute(self, params: dict) -> SkillResult:
        action = params.get("action")  # turn_on, turn_off, etc
        entity = params.get("entity_id")  # light.cucina
        
        try:
            await self.ha_client.call_service(
                domain=entity.split('.')[0],
                service=action,
                entity_id=entity,
                data=params.get("data", {})
            )
            return SkillResult(
                success=True,
                output=f"Ho eseguito {action} su {entity}"
            )
        except Exception as e:
            return SkillResult(
                success=False,
                output=f"Errore: {str(e)}"
            )

# marcus_skills/src/skill_navigation.py

class NavigationSkill(BaseSkill):
    name = "navigation"
    description = "Naviga verso una stanza"
    risk_level = "medium"
    
    async def execute(self, params: dict) -> SkillResult:
        location = params.get("location")  # cucina, soggiorno, etc
        
        # Usa Nav2 per navigare
        goal = PoseStamped()
        goal.pose = self.location_map.get(location)
        
        await self.nav_client.send_goal(goal)
        
        return SkillResult(
            success=True,
            output=f"Sto andando in {location}"
        )
```

---

### Fase 6: ORCHESTRATORE PRINCIPALE

**Durata**: 2 settimane  
**Responsabile**: Lead Developer

#### 6.1 Thread Safety Fix

```python
# marcus_core/src/ai_orchestrator.py

class AIOrchestrator(Node):
    def __init__(self):
        super().__init__('ai_orchestrator')
        
        # FIX: Protect frame with lock
        self._frame_lock = asyncio.Lock()
        self._latest_frame = None
        self.frame_update_event = asyncio.Event()
        
        # Setup camera subscription
        self.camera_sub = self.create_subscription(
            Image,
            '/oak_d/rgb/image_raw',
            self._camera_callback,
            10
        )
    
    def _camera_callback(self, msg: Image):
        """ROS callback (sync) – segnala al loop asyncio"""
        self._latest_frame_pending = self._bridge.imgmsg_to_cv2(msg)
        self.frame_update_event.set()
    
    async def _visual_analysis_loop(self):
        """Asyncio loop (safe access)"""
        while True:
            await self.frame_update_event.wait()
            
            async with self._frame_lock:
                frame = self._latest_frame_pending.copy() if self._latest_frame_pending else None
            
            if frame is not None:
                await self._analyze_frame(frame)
            
            self.frame_update_event.clear()
```

#### 6.2 process_input Completo

```python
async def process_input(self, text: str, context: dict = None) -> str:
    """Main entry point per elaborare input utente"""
    
    # 1. Build contesto RAG
    rag_context = await self.memory.retrieve(text, user_id=context.get("user_id"))
    
    # 2. Chiama LLM con contesto
    llm_response = await self.llm_service.generate(
        prompt=self._build_prompt(text, rag_context, context)
    )
    
    # 3. Parse azioni
    actions = self._parse_response(llm_response)
    
    # 4. Esegui skill con priority checking
    for action in actions:
        skill = self.skill_registry.get(action.type)
        if skill and await skill.can_execute(context):
            result = await skill.execute(action.params)
            await self.event_bus.publish(Event(
                type="skill_executed",
                data={"skill": skill.name, "result": result}
            ))
    
    # 5. Restituisci risposta
    return llm_response.get("text", "")
```

---

### Fase 7: INTEGRAZIONE DEEPSEEK

**Durata**: 1 settimana  
**Responsabile**: AI Engineer

Vedi sezione [DEEPSEEK INTEGRATION](#deepseek) sotto.

---

### Fase 8: TESTING

**Durata**: 2 settimane  
**Responsabile**: QA Engineer

```python
# tests/test_scenarios.py

@pytest.mark.asyncio
async def test_user_angry():
    """Scenario: Luca arrabbiato"""
    ctx = Context(emotion="angry", emotion_confidence=0.9)
    response = await marcus.process_input("ACCENDI!", context=ctx)
    assert "scusa" in response.lower()

@pytest.mark.asyncio
async def test_gemini_fallback():
    """Scenario: Gemini down → Ollama"""
    with patch('gemini.generate', side_effect=TimeoutError()):
        response = await marcus.process_input("accendi luce")
        assert response is not None

@pytest.mark.asyncio
async def test_thread_safety():
    """Scenario: Concorrenza camera + asyncio"""
    tasks = [
        self.camera_callback_simulator(),
        self._visual_analysis_loop(),
    ]
    await asyncio.gather(*tasks)
```

---

### Fase 9: DOCUMENTAZIONE

**Durata**: 3 giorni  
**Responsabile**: Tech Writer

- README.md con setup + examples
- API documentation per skills
- Architecture diagrams
- Troubleshooting guide

---

## INTEGRAZIONE DEEPSEEK

### Overview

DeepSeek viene usato per **two-brain analysis** nel nightly dream:

```
Giorno → Memorie → [Gemini Analysis] → [DeepSeek Critique] 
                                    ↓
                          [Gemini Synthesis]
                                    ↓
                        [DeepSeek Master Prompt]
                                    ↓
                        Test + Rollback if needed
```

### Configurazione

```yaml
deepseek:
  enabled: true
  api_key: "!env DEEPSEEK_API_KEY"
  model: "deepseek-chat"
  temperature: 0.5
  max_tokens: 8192
  timeout_seconds: 60
```

### DeepSeekService

```python
# marcus_nightly_dream/src/deepseek_service.py

class DeepSeekService:
    def __init__(self, config):
        self.config = config
        self.api_key = config.get('deepseek.api_key')
        self.session = None
    
    async def generate(self, prompt: str, **kwargs) -> str:
        """Chiama DeepSeek API"""
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                "https://api.deepseek.com/v1/chat/completions",
                json={
                    "model": self.config.get('deepseek.model'),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.config.get('deepseek.temperature'),
                    "max_tokens": self.config.get('deepseek.max_tokens'),
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(
                    total=self.config.get('deepseek.timeout_seconds', 60)
                )
            )
            
            data = await response.json()
            return data['choices'][0]['message']['content']
    
    async def close(self):
        """Chiudi sessione"""
        if self.session:
            await self.session.close()
```

### NightlyDreamService Potenziato

```python
# marcus_nightly_dream/src/nightly_dream_service.py

class NightlyDreamService:
    def __init__(self, config, gemini_service, deepseek_service=None):
        self.config = config
        self.gemini = gemini_service
        self.deepseek = deepseek_service
    
    async def run_analysis_with_collaboration(self):
        """Turni Gemini + DeepSeek + Gemini + DeepSeek"""
        
        # Recupera memorie della giornata
        memories = await self.memory.get_today()
        
        # Turno 1: Gemini analiza
        print("🧠 Gemini: Analyzing day...")
        gemini_analysis = await self.gemini.generate(
            self._build_analysis_prompt(memories)
        )
        
        # Turno 2: DeepSeek critica
        if self.deepseek:
            print("🔍 DeepSeek: Critical analysis...")
            deepseek_critique = await self.deepseek.generate(
                self._build_critique_prompt(gemini_analysis, memories)
            )
        else:
            deepseek_critique = ""
        
        # Turno 3: Gemini sintetizza
        print("🧠 Gemini: Synthesizing...")
        synthesis = await self.gemini.generate(
            self._build_synthesis_prompt(gemini_analysis, deepseek_critique)
        )
        
        # Turno 4: DeepSeek genera master prompt
        if self.deepseek:
            print("📝 DeepSeek: Generating master prompt...")
            master_prompt = await self.deepseek.generate(
                self._build_master_prompt_prompt(synthesis)
            )
        else:
            master_prompt = gemini_analysis  # Fallback
        
        # Salva tutto
        await self._save_reports(gemini_analysis, deepseek_critique, synthesis)
        await self._save_master_prompt(master_prompt)
        
        # Test + Rollback automatico
        await self._validate_and_apply_prompt(master_prompt)
```

### System Prompt per DeepSeek

```
# SISTEMA PROMPT PER DEEPSEEK – ANALISTA CRITICO

Sei DeepSeek, esperto di sistemi IA domestici.
Il tuo ruolo: fare CRITICA COSTRUTTIVA dell'analisi di Gemini.

## Compito Turno 2 (Critica)

Hai ricevuto:
1. Memorie della giornata (conversazioni, azioni, emozioni)
2. Analisi di Gemini su quello che è successo

Tuo compito: identifica COSA GEMINI HA PERSO
- Quali pattern non ha visto?
- Quali bias potrebbe avere?
- Quali edge case ignora?
- Quali KPI mancano?

Restituisci JSON con: forgotten_analyses, potential_biases, missing_kpi, edge_cases.

## Compito Turno 4 (Master Prompt)

Ricevi la sintesi di Gemini.

Tuo compito: genera un MASTER PROMPT aggiornato
che Marcus userà domani. Deve:
- Incorporare i miglioramenti identificati
- Essere breve ma completo
- Essere italiano
- Contenere istruzioni operative

Restituisci il master prompt in plain text, pronto per l'uso.

Mantieni tono costruttivo, non sarcasmo.
```

---

## ARCHITETTURA TECNICA

### System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                          │
│  Voice Input + Visual Display + Touch Control                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
        ┌──────────────▼──────────────────┐
        │    ROS 2 ORCHESTRATOR           │
        │  (AIOrchestrator Node)          │
        │  - Context Management           │
        │  - Priority Resolution          │
        │  - Action Execution             │
        └──┬──────────────┬───────────┬───┘
           │              │           │
      ┌────▼───┐   ┌──────▼────┐   ┌─▼────────┐
      │  LLM   │   │  Memory   │   │ Skill    │
      │ Layer  │   │   & RAG   │   │ Registry │
      │───────│   │──────────│   │──────────│
      │Gemini │   │ChromaDB  │   │HA,Nav,  │
      │Ollama │   │SQLite    │   │ Search, │
      │Cache  │   │Vector DB │   │Custom   │
      └───┬───┘   └────┬─────┘   └─┬───────┘
          │            │           │
          │    ┌───────▼───────┐   │
          └──▶│   PERCEPTION   │◀─┘
               │   Services     │
               │─────────────│
               │ Face + Emotion
               │ Voice + ASR
               │ TTS + Speaker
               │ Camera + Vision
               └──────┬──────┘
                      │
          ┌───────────▼──────────────┐
          │  PERSISTENCE LAYER       │
          │────────────────────────│
          │ SQLite + ChromaDB +      │
          │ Redis Cache +            │
          │ Encrypted Backups        │
          └──────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              NIGHTLY DREAM SERVICE (Scheduled)                  │
│─────────────────────────────────────────────────────────────────│
│  Gemini (Analysis) ─→ DeepSeek (Critique) ─→ Gemini (Synthesis)│
│       ↓                                                         │
│  DeepSeek (Master Prompt) ─→ Validation ─→ Version + Rollback  │
└─────────────────────────────────────────────────────────────────┘
```

---

## DATABASE SCHEMA

### SQLite

```sql
CREATE TABLE interactions (
    id TEXT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,
    user_emotion TEXT,
    input_text TEXT,
    action_executed TEXT,
    action_result BOOLEAN,
    response_text TEXT,
    latency_ms INTEGER
);

CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE,
    risk_level TEXT,
    executions INTEGER,
    successes INTEGER,
    failures INTEGER,
    avg_latency_ms FLOAT
);

CREATE TABLE kpi (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE,
    metric_name TEXT,
    metric_value FLOAT,
    UNIQUE(date, metric_name)
);

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    name TEXT,
    communication_style TEXT,
    proactivity_preference FLOAT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## CONFIGURAZIONE

Vedi sezione [Fase 1.2](#12-configyaml-esempio) per config.yaml completo.

---

## CHECKLIST IMPLEMENTAZIONE

### ✅ Fase 0: Setup (2 giorni)
- [ ] requirements.txt + dependencies
- [ ] setup_keys.sh per env variables
- [ ] Directory structure creata
- [ ] Git repo initialized

### ✅ Fase 1: Core Infrastructure (1 settimana)
- [ ] ConfigManager implementato
- [ ] config.yaml in place
- [ ] EventBus funzionante
- [ ] StateMachine implemented
- [ ] CircuitBreaker registry

### ✅ Fase 2: Servizi (2 settimane)
- [ ] LLMService: test generate + generate_live
- [ ] ASRService: Porcupine wake word integrato
- [ ] TTSService: cache + cleanup
- [ ] EmotionService: volto + voce + testo
- [ ] HomeAssistantClient: stati + servizi

### ✅ Fase 3: RAG (1 settimana)
- [ ] MemoryStore: retrieve + archive
- [ ] MetadataManager: NER robusto
- [ ] VisualMemoryService: proiezione 3D

### ✅ Fase 4: Integrazioni (2 settimane)
- [ ] HomeAssistantClient: fully implemented
- [ ] NavigationClient: Nav2 integration
- [ ] Rate limiting + caching

### ✅ Fase 5: Skills (1 settimana)
- [ ] BaseSkill framework
- [ ] Skill Registry: hot-reload
- [ ] HA skill: accendi/spegni luci
- [ ] Navigation skill
- [ ] Search skill

### ✅ Fase 6: Orchestratore (2 settimane)
- [ ] Thread safety: frame lock implemented
- [ ] process_input: completo
- [ ] State management: READY → PROCESSING → LISTENING
- [ ] Cleanup: shutdown pulito

### ✅ Fase 7: DeepSeek (1 settimana)
- [ ] DeepSeekService: API client
- [ ] NightlyDreamService: 4-turni
- [ ] Master prompt generation
- [ ] Auto-rollback logic

### ✅ Fase 8: Testing (2 settimane)
- [ ] Unit tests: 80% coverage
- [ ] Integration tests: end-to-end
- [ ] Scenario tests: angry user, fallback, ecc.
- [ ] Performance tests: CPU/RAM/latency
- [ ] Load tests: concurrency

### ✅ Fase 9: Docs (3 giorni)
- [ ] README.md: setup + architecture
- [ ] Skills documentation
- [ ] API reference
- [ ] Troubleshooting guide

---

## KEY LEARNINGS & BEST PRACTICES

### ✅ DO

1. **Async-first architecture** – Sempre `async/await`
2. **Logging structured** – JSON logs per parsing facile
3. **Type hints ovunque** – `mypy` in CI
4. **Config externalizzata** – YAML, env variables
5. **Error handling robusto** – Fallback, circuit breaker, retry
6. **Testing rigoroso** – Unit + integration + scenario
7. **Monitoring proattivo** – Health checks, KPI tracking
8. **Security by default** – Encryption, anonymization, no hardcoded secrets

### ❌ DON'T

- ❌ Non bloccare main thread (sempre async)
- ❌ Non memorizzare dati enormi in RAM (usa DB)
- ❌ Non ignorare timeout su API cloud
- ❌ Non assumere sensori sempre disponibili
- ❌ Non spendere infinite token in context

---

## PROSSIMI STEP

1. **Settimana 1**: Fase 0 + 1 (Setup + Infrastructure)
2. **Settimana 2-3**: Fase 2 + 3 (Services + RAG)
3. **Settimana 4-5**: Fase 4 + 5 (Integration + Skills)
4. **Settimana 6-7**: Fase 6 + 7 (Orchestrator + DeepSeek)
5. **Settimana 8-9**: Fase 8 (Testing)
6. **Settimana 10**: Fase 9 (Docs)

**Total**: 10 settimane (5 mesi con team part-time)

---

## CONCLUSION

Questo blueprint fornisce una **guida completa** per implementare Marcus v2.0:

✅ Architettura cloud-first (Gemini + DeepSeek)  
✅ Auto-miglioramento governato  
✅ Sicurezza strutturata  
✅ Extensibilità (plugin system)  
✅ Resilienza (fallback + health check)  
✅ Osservabilità (KPI + logging)  
✅ Piano implementativo dettagliato (9 fasi)  

**Creato da**: Luca Suffia  
**Per**: Famiglia Suffia + Marcus  
**Data**: 2026-02-15  

Buona implementazione! 🚀

---

## APPENDICE A: COMANDI ROS 2

```bash
# Build
cd ~/robopy_ws && colcon build

# Source
source install/setup.bash

# Launch
ros2 launch marcus_core marcus.launch.py

# Monitor topics
ros2 topic echo /ai/user/emotion
ros2 topic echo /ai/feedback

# Run tests
pytest tests/ -v --cov=marcus_core

# Health check
curl http://localhost:8080/health
```

## APPENDICE B: ENV VARIABLES

```bash
export GEMINI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
export HA_TOKEN="eyJhbGc..."
export GOOGLE_APPLICATION_CREDENTIALS="/home/robopy/.gcp/creds.json"
export ENCRYPTION_KEY="$(openssl rand -base64 32)"
export LOG_LEVEL="INFO"
```

---

**Fine documento.**

api cide deepseek: sk-3beaa5d8a79a4ed49ba3bf2e7d15d08a