#!/usr/bin/env python3
"""
Nodo VUI (Voice User Interface) per ReSpeaker Lite via USB Audio nativo.
=======================================================================
Versione 5.1 — Raw int16 pipeline (Stable & Clean)

Changelog v5.1:
  [#1] Pulizia codice morto, rimosse inizializzazioni doppie e metodi orfani.
  [#2] Sincronizzazione versione log di avvio.

Changelog v5.0:
  [#1] Rimosso intero stack DSP float32 (SciPy Butterworth, sosfilt, scalatura):
       Porcupine e WebRTC VAD ricevono il segnale int16 grezzo dal mic.
  [#2] CPU usage su Pi5: da ~40% a ~2-3%.
  [#3] Rimosse tutte le stampe di debug nel hot-path del callback.

Changelog v3.0:
  [#1] VAD gate WebRTC con ring buffer pre-roll 500 ms
  [#2] CHUNK_SIZE=960 (3 frame VAD esatti da 320 campioni)
  [#3] Zero allocazioni heap nel callback audio (buffer pre-allocati in __init__)
  [#4] Gestione TTS con reset completo dello stato VAD

Changelog v2.5 (legacy):
  [#1] threading.Event per _tts_active e _listening: atomici, zero Lock nel callback
  [#2] Coda audio output maxsize 512
  [#3] Contatore errori consecutivi nel callback: log CRITICAL oltre soglia
"""

import os
import pickle
import sys
sys.modules['pickle5'] = pickle
import threading
import queue
import time
import audioop

# Nuovi import per DSP
import numpy as np
import webrtcvad
try:
    from scipy.signal import butter, sosfilt, sosfilt_zi
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# [v6.4] Audio Recovery - Force Sync for Gain 30x
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import Bool, String, Float32
from robopy_controller.msg import AudioData            # classe AudioData usata per _pub_speech
import pyaudio
import pvporcupine

try:
    from dotenv import load_dotenv
    # Fix: Usa il path assoluto per caricare .env, poiché in ROS2 la CWD può variare.
    load_dotenv("/mnt/ssd/robopy_controller_host/.env", override=True)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Costanti globali (fuori dalla classe)
# ---------------------------------------------------------------------------
SAMPLE_RATE        = 16000
CHUNK_SIZE         = 960    # frames_per_buffer in pyaudio.open() — cambiarli insieme
FRAME_SIZE         = 320    # campioni per frame VAD (20 ms @ 16 kHz)

# Ring buffer: abbastanza grande da coprire pause brevi tra frasi (2.5 sec)
PRE_ROLL_FRAMES    = 25                       # 25 × 20 ms = 500 ms di pre-roll
MAX_RING_FRAMES    = PRE_ROLL_FRAMES + 100    # 125 × 20 ms = 2.5 sec totali

# Soglie VAD standard (possono essere modificate da parametri)
MIN_SPEECH_FRAMES  = 4     # frame VAD=True consecutivi per aprire il gate (v17.0: 80ms reattività far-field)
MAX_SILENCE_FRAMES = 25    # frame VAD=False consecutivi per chiudere il gate (v5.8: 500ms)
MAX_RESIDUAL       = FRAME_SIZE   # dimensione buffer residuo (= FRAME_SIZE)

# Pre-roll: bytearray pre-allocato (int16 = 2 byte/campione)
PRE_ROLL_BYTES     = PRE_ROLL_FRAMES * FRAME_SIZE * 2

# Numero di errori consecutivi nel callback prima di loggare CRITICAL
_CALLBACK_ERROR_THRESHOLD = 20

# Soglia dimensione chunk (bytes) per distinguere Native Audio da TTS classico.
# Aumentato a 65536 per allineamento a nuovi chunk da 46KB di Gemini Live (v13.6).
_NATIVE_AUDIO_CHUNK_THRESHOLD = 65536

# Sample rate del Native Audio di Gemini Live API (24kHz mono PCM int16)
_NATIVE_AUDIO_RATE = 24000
# Sample rate del TTS classico e del microfono
_STD_AUDIO_RATE    = 16000


def _load_keys():
    """Carica le chiavi API da setup_keys.sh centrale."""
    setup_keys_path = '/mnt/ssd/robopy_controller_host/setup_keys.sh'
    keys_to_load = [
        'GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'PICOVOICE_API_KEY',
        'GOOGLE_APPLICATION_CREDENTIALS', 'HA_TOKEN'
    ]
    if os.path.exists(setup_keys_path):
        try:
            with open(setup_keys_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    for key_name in keys_to_load:
                        clean_line = line.replace('export ', '').strip()
                        if clean_line.startswith(f'{key_name}='):
                            val = clean_line.split('=', 1)[1].strip().strip('"').strip("'")
                            if '#' in val: val = val[:val.index('#')].strip()
                            os.environ[key_name] = val
        except Exception:
            pass


class ReSpeakerVUINode(Node):

    def __init__(self):
        super().__init__('respeaker_vui_node')
        self.get_logger().info("--- ReSpeaker VUI Node v5.1 (Optimized Native Pipeline) ---")
        _load_keys()

        # ------------------------------------------------------------------ #
        # Parametri ROS 2
        # ------------------------------------------------------------------ #
        self.declare_parameter('stt_gain',              30.0)  # [v5.5] 30x hardware gain per ReSpeaker Lite
        self.declare_parameter('noise_gate_threshold',  1500.0)
        self.declare_parameter('wakeword_sensitivity',  0.92)  # [v4.0] default alzato
        self.declare_parameter('playback_prebuffer',    2)
        self.declare_parameter('listen_timeout_sec',    180.0)
        self.declare_parameter('max_silence_frames',    35)     # [v6.0] 35 * 20ms = 700ms di pausa naturale
        self.declare_parameter('access_key',            '')
        self.declare_parameter('device_name',            'ReSpeaker')
        self.declare_parameter('sample_rate',            16000)
        self.declare_parameter('enable_vad_gate',        True)   # [v3.0] parametro debug
        self.declare_parameter('enable_barge_in',        True)   # [v5.6] barge-in AEC-aware
        self.declare_parameter('barge_in_min_tts_ms',    1500.0) # [v5.6] ms di grace period prima che il barge-in si attivi
        self.declare_parameter('barge_in_min_frames',    15)     # [v10.4] ~300ms di voce sostenuta per trigger barge-in (mitiga eco)
        self.declare_parameter('diag_mode',              False)  # [v10.1] Diagnostica estesa VUI
        self.declare_parameter('enable_adaptive_threshold', True) # [v12.0] Auto-calibration threshold
        self.declare_parameter('enable_adaptive_silence',   True) # [v12.0] Adaptive speech duration
        self.declare_parameter('playback_volume',           0.10) # [v13.6] Volume di riproduzione base
        self.declare_parameter('enable_auto_volume',        True) # [v13.6] Regolazione automatica volume su rumore

        self._cfg_enable_vad_gate   = self.get_parameter('enable_vad_gate').get_parameter_value().bool_value
        self._cfg_enable_barge_in   = self.get_parameter('enable_barge_in').get_parameter_value().bool_value
        self._barge_in_min_tts_s    = self.get_parameter('barge_in_min_tts_ms').get_parameter_value().double_value / 1000.0
        self._barge_in_min_frames   = self.get_parameter('barge_in_min_frames').get_parameter_value().integer_value
        self._cfg_diag_mode         = self.get_parameter('diag_mode').get_parameter_value().bool_value
        self._cfg_max_silence       = self.get_parameter('max_silence_frames').get_parameter_value().integer_value
        self.enable_adaptive_threshold = self.get_parameter('enable_adaptive_threshold').get_parameter_value().bool_value
        self.enable_adaptive_silence   = self.get_parameter('enable_adaptive_silence').get_parameter_value().bool_value
        self.playback_volume           = self.get_parameter('playback_volume').get_parameter_value().double_value
        self.enable_auto_volume        = self.get_parameter('enable_auto_volume').get_parameter_value().bool_value

        # Inizializzazione stabile EMA rumore ambientale (evita hasattr in hot-path)
        # [v12.1 FIX] Valore iniziale realistico per hardware Pi5 + ReSpeaker Lite.
        # Con EMA=50 → boosted=1500 → threshold=1200 → troppo bassa per il rumore ventola (~870 RMS raw).
        # Con EMA=800 → boosted=24000 → clampato a 6000 → sicuro fin dal primo frame.
        # L'EMA convergerà al valore reale in ~5-10s di silenzio (alpha=0.05, ~20 chunks).
        self._ambient_noise_ema = 300.0
        self._ambient_noise_chunks = 0
        self._is_playing_out = False  # [v13.7] Impedisce all'eco del proprio altoparlante di sballare l'EMA

        access_key               = self.get_parameter('access_key').get_parameter_value().string_value
        if not access_key:
            access_key = os.environ.get('PICOVOICE_API_KEY', '')
            if access_key:
                self.get_logger().info("Picovoice API Key caricata dalle variabili d'ambiente.")

        self.device_name_target  = self.get_parameter('device_name').get_parameter_value().string_value
        self.sample_rate         = self.get_parameter('sample_rate').get_parameter_value().integer_value
        self.stt_gain            = self.get_parameter('stt_gain').get_parameter_value().double_value
        self.noise_gate_threshold = self.get_parameter('noise_gate_threshold').get_parameter_value().double_value
        sensitivity              = self.get_parameter('wakeword_sensitivity').get_parameter_value().double_value
        self._listen_timeout_sec = self.get_parameter('listen_timeout_sec').get_parameter_value().double_value
        self._prebuffer_size     = self.get_parameter('playback_prebuffer').get_parameter_value().integer_value

        if not access_key:
            self.get_logger().error("PICOVOICE_API_KEY non trovata! Inseriscila nel launch file o in setup_keys.sh")
            self.porcupine = None
        else:
            self.get_logger().info("Inizializzazione Porcupine...")
            try:
                from ament_index_python.packages import get_package_share_directory
                pkg_share    = get_package_share_directory('robopy_controller')
                keyword_path = os.path.join(pkg_share, 'config', 'wake_word', 'marcus.ppn')
                model_path   = os.path.join(pkg_share, 'config', 'wake_word', 'porcupine_params_it.pv')

                if os.path.exists(keyword_path) and os.path.exists(model_path):
                    self.get_logger().info(f"Access Key (prefix): {access_key[:4]}***")
                    self.get_logger().info(f"Wake word: {keyword_path} (exists=True)")
                    self.get_logger().info(f"Model path: {model_path} (exists=True)")
                    
                    self.porcupine = pvporcupine.create(
                        access_key=access_key,
                        keyword_paths=[keyword_path],
                        model_path=model_path,
                        sensitivities=[sensitivity]
                    )
                    self.get_logger().info(f"Porcupine Init SUCCESS (Custom Marcus) | sensitivity={sensitivity}")
                else:
                    self.porcupine = pvporcupine.create(
                        access_key=access_key,
                        keywords=['porcupine'],
                        sensitivities=[sensitivity]
                    )
                    self.get_logger().warning("Keyword 'marcus.ppn' non trovata, uso 'porcupine'.")
            except Exception as e:
                self.get_logger().error(f"Errore Porcupine: {e}")
                self.porcupine = None

        # Porcupine frame_length (normalmente 512).
        # CHUNK_SIZE=960 è compatibile con Porcupine.
        if self.porcupine:
            self.frame_length = self.porcupine.frame_length
        else:
            self.frame_length = 512

        # ------------------------------------------------------------------ #
        # Buffer di lavoro NumPy (solo interi nativi, zero float)
        # ------------------------------------------------------------------ #
        self._int16_vad_buf = np.empty(CHUNK_SIZE, dtype=np.int16)
        self._silence_buf   = np.zeros(FRAME_SIZE, dtype=np.int16)

        # [DSP-HOT] Residuo inter-callback (attivo sempre)
        self._vad_residual_buf = np.empty(MAX_RESIDUAL, dtype=np.int16)
        self._vad_residual_raw_buf = np.empty(MAX_RESIDUAL, dtype=np.int16)
        self._vad_residual_len = 0
        self._assembly_buf     = np.empty(FRAME_SIZE, dtype=np.int16)  # pre-allocato, zero alloc nel callback
        self._assembly_raw_buf     = np.empty(FRAME_SIZE, dtype=np.int16)  # pre-allocato per VAD raw

        # [DSP-HOT] Ring buffer pre-roll (2.5 sec — copre pause tra frasi)
        self._speech_ring    = np.zeros((MAX_RING_FRAMES, FRAME_SIZE), dtype=np.int16)
        self._ring_write_idx = 0

        # [DSP-HOT] Pre-roll bytearray pre-allocato (evita allocazioni frequenti)
        self._preroll_ba = bytearray(PRE_ROLL_BYTES)

        # [v3.1] Residuo Porcupine (evita perdita campioni wake-word)
        self._porcupine_residual_buf = np.empty(self.frame_length, dtype=np.int16)
        self._porcupine_residual_len = 0
        self._porcupine_assembly_buf = np.empty(self.frame_length, dtype=np.int16)

        # [v3.0] Stato VAD
        self._vad                 = webrtcvad.Vad(3)
        self._speech_frame_count  = 0
        self._silence_frame_count = 0
        self._is_speech_active    = False

        # Contatori diagnostica (inizializzati qui, non con hasattr nel hot-path)
        self._rms_chunk_count = 0
        self._stt_chunk_count = 0
        self._audio_chunk_count = 0
        self._out_hw_rate = self.sample_rate
        self._last_ai_speaking_time = 0.0

        # [v3.0] Stato TTS — scritto e letto SOLO nel callback audio (no race condition)
        self._tts_active          = False
        self._tts_start_time      = 0.0
        # [v5.6] Stato barge-in (reset ad ogni fine TTS)
        self._barge_in_triggered  = False
        self._barge_in_frame_count = 0
        # [v5.7] Debug VAD
        self._voice_frame_count    = 0
        self._is_tts_speaking      = False # [v10.1]
        self._is_music_playing     = False # [v11.0]

        # ------------------------------------------------------------------ #
        # Peak Limiter / AGC software real-time parameters [v16.0]
        # ------------------------------------------------------------------ #
        self._limiter_gain = 1.0  # Moltiplicatore di guadagno dinamico corrente
        # Rilascio: in 800ms-1000ms risale linearmente a 1.0. 
        # Calcoliamo il delta per frame/chunk. Ogni chunk = 960 campioni a 16kHz = 60ms.
        # Per risalire da 0.0 a 1.0 in 900ms, servono 15 chunk.
        # Quindi il guadagno aumenta di 1.0 / 15 = ~0.0667 per chunk.
        self._limiter_release_rate = 0.0667  # incremento lineare per chunk (60ms)

        # ------------------------------------------------------------------ #
        # Filtro Passa-Alto @ 140 Hz (HPF) per eliminare il ronzio ventola Pi 5 [v17.0]
        # ------------------------------------------------------------------ #
        if HAS_SCIPY:
            self._hpf_sos = butter(2, 140.0, btype='highpass', fs=SAMPLE_RATE, output='sos')
            self._hpf_zi_l = sosfilt_zi(self._hpf_sos) * 0.0
            self._hpf_zi_r = sosfilt_zi(self._hpf_sos) * 0.0
        else:
            self._hpf_sos = None
            self._hpf_prev_x_l = 0.0
            self._hpf_prev_y_l = 0.0
            self._hpf_prev_x_r = 0.0
            self._hpf_prev_y_r = 0.0


        # ------------------------------------------------------------------ #
        # [v2.5 #1] Stato VUI con threading.Event
        # ------------------------------------------------------------------ #
        # self.stt_gain gia' impostata sopra
        self._prebuffer_size     = self.get_parameter('playback_prebuffer').get_parameter_value().integer_value
        self._last_playback_time = 0.0

        self._ev_listening = threading.Event()
        self._ev_tts       = threading.Event()
        self._shutdown     = False
        self._listen_timer = None

        # [v2.5 #3] Contatore errori consecutivi nel callback
        self._consecutive_errors = 0

        # Thread-safety output stream
        self._out_lock = threading.Lock()

        # Coda audio output
        self._audio_out_queue = queue.Queue(maxsize=512)
        self._playback_thread = threading.Thread(
            target=self._playback_worker, daemon=True, name='vui_playback')
        self._playback_thread.start()

        # Coda audio input thread-safe (Ring Buffer) per la callback microfonica
        self._audio_in_queue = queue.Queue(maxsize=100)
        self._worker_thread = threading.Thread(
            target=self._audio_processing_worker, daemon=True, name='vui_audio_worker')


        # ------------------------------------------------------------------ #
        # Publisher / Subscriber
        # ------------------------------------------------------------------ #
        qos_profile_audio = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        self.ww_pub        = self.create_publisher(String,    '/wake_word',              10)
        self.mic_mute_pub  = self.create_publisher(Bool,      '/ai/input/mic_mute',      10)
        self.audio_pub     = self.create_publisher(AudioData, '/ai/input/audio_chunk',  qos_profile_audio)
        self.led_pub       = self.create_publisher(String,    '/respeaker/led_command',  10)
        self.barge_in_pub  = self.create_publisher(Bool,      '/ai/barge_in',            10)  # [v5.6]
        self.ambient_noise_pub = self.create_publisher(Float32, '/ai/ambient_noise',       10)  # [v11.0] Auto-Volume

        # [v3.0] _pub_speech alias per il VAD gate (usa lo stesso topic audio_pub)
        self._pub_speech  = self.audio_pub

        self._current_mood = 'IDLE'
        self.create_subscription(Bool,      '/ai/tts/speaking',         self._tts_speaking_cb,  10)
        self.create_subscription(AudioData, '/respeaker/speaker_audio', self._speaker_audio_cb, 10)
        self.create_subscription(Bool,      '/ai/input/mic_mute',       self._mic_mute_cb,      10)
        self.create_subscription(Bool,      '/ai/music_playing',        self._music_playing_cb, 10)
        self.create_subscription(String,    '/ai/conversation/mood',    self._mood_cb,          10)
        self.create_subscription(Bool,      '/ai/conversation/interrupt', self._interrupt_cb,     10)

        # ------------------------------------------------------------------ #
        # PyAudio — usa CHUNK_SIZE come frames_per_buffer
        # ------------------------------------------------------------------ #
        self.pa = pyaudio.PyAudio()
        input_device_index, output_device_index = self._find_audio_devices()

        if input_device_index is None:
            self.get_logger().warning(
                f"Device '{self.device_name_target}' non trovato, uso default.")

        # [v3.8] Rileva il sample rate nativo HW del device di output
        # Il DAC del ReSpeaker Lite lavora a 48kHz. PyAudio accetta 16kHz
        # ma il chip lo ignora: serve resampling esplicito.
        self._out_hw_rate = self.sample_rate  # fallback
        if output_device_index is not None:
            try:
                dev_info = self.pa.get_device_info_by_index(output_device_index)
                self._out_hw_rate = int(dev_info.get('defaultSampleRate', self.sample_rate))
                self.get_logger().info(
                    f"🔊 Output device HW native rate: {self._out_hw_rate} Hz")
            except Exception:
                pass

        try:
            self.in_stream = self.pa.open(
                rate=SAMPLE_RATE,
                channels=2,
                format=pyaudio.paInt16,
                input=True,
                input_device_index=input_device_index,
                frames_per_buffer=CHUNK_SIZE,          # [v3.0] CHUNK_SIZE=960
                stream_callback=self._audio_input_callback,
                start=False
            )
            self.get_logger().info(f"🎤 Input stream (mic) aperto! (Idx={input_device_index})")
        except Exception as e:
            self.get_logger().error(f"Errore apertura input stream: {e}. STT non funzionerà.")
            self.in_stream = None

        try:
            self.out_stream = self.pa.open(
                rate=self._out_hw_rate,
                channels=2,
                format=pyaudio.paInt16,
                output=True,
                output_device_index=output_device_index
            )
            self.get_logger().info(
                f"✅ Output stream aperto a {self._out_hw_rate} Hz Stereo "
                f"(HW native: {self._out_hw_rate} Hz)")
        except Exception as e:
            self.get_logger().error(f"Errore fatale apertura output stream: {e}")
            self.out_stream = None

        if self.in_stream:
            self.in_stream.start_stream()
        self._worker_thread.start()
        self.set_led('IDLE')

        # [v6.0] Registra callback per parametri dinamici (hot-swap)
        self.add_on_set_parameters_callback(self._parameter_callback)

        # Stato playback
        self._playback_ratecv_state = None
        self._last_audio_source     = None

        self.get_logger().info(
            f"VUI Node pronto | CHUNK_SIZE={CHUNK_SIZE} | FRAME_SIZE={FRAME_SIZE} | "
            f"PRE_ROLL_FRAMES={PRE_ROLL_FRAMES} | sensitivity={sensitivity} | "
            f"stt_gain={self.stt_gain}"
        )



    def _parameter_callback(self, params):
        from rcl_interfaces.msg import SetParametersResult
        for p in params:
            if p.name == 'max_silence_frames':
                self._cfg_max_silence = p.value
                self.get_logger().info(f"Parametro max_silence_frames aggiornato a: {p.value}")
            elif p.name == 'listen_timeout_sec':
                self._listen_timeout_sec = p.value
                self.get_logger().info(f"Parametro listen_timeout_sec aggiornato a: {p.value}")
            elif p.name == 'stt_gain':
                self.stt_gain = p.value
                self.get_logger().info(f"Parametro stt_gain aggiornato a: {p.value}")
            elif p.name == 'noise_gate_threshold':
                self.noise_gate_threshold = p.value
                self.get_logger().info(f"Parametro noise_gate_threshold aggiornato a: {p.value}")
            elif p.name == 'enable_adaptive_threshold':
                self.enable_adaptive_threshold = p.value
                self.get_logger().info(f"Parametro enable_adaptive_threshold aggiornato a: {p.value}")
            elif p.name == 'enable_adaptive_silence':
                self.enable_adaptive_silence = p.value
                self.get_logger().info(f"Parametro enable_adaptive_silence aggiornato a: {p.value}")
            elif p.name == 'playback_volume':
                self.playback_volume = p.value
                self.get_logger().info(f"Parametro playback_volume aggiornato a: {p.value:.2f}")
            elif p.name == 'enable_auto_volume':
                self.enable_auto_volume = p.value
                self.get_logger().info(f"Parametro enable_auto_volume aggiornato a: {p.value}")
            elif p.name == 'diag_mode':
                self._cfg_diag_mode = p.value
                self.get_logger().info(f"Parametro diag_mode aggiornato a: {p.value}")
        return SetParametersResult(successful=True)

    # ------------------------------------------------------------------ #
    # Worker thread riproduzione
    # ------------------------------------------------------------------ #
    def _playback_worker(self):
        self.get_logger().info("Avvio playback_worker con Jitter Buffer Adattivo")
        _chunks_played = 0

        while not self._shutdown:
            try:
                if self._audio_out_queue.empty():
                    self._is_playing_out = False  # Coda vuota: altoparlante inattivo
                    time.sleep(0.005)  # idle: 5ms ok, non spreca CPU
                    continue

                qsize = self._audio_out_queue.qsize()

                try:
                    first_chunk = self._audio_out_queue.queue[0]
                    is_live = len(first_chunk) < _NATIVE_AUDIO_CHUNK_THRESHOLD
                except (IndexError, AttributeError):
                    is_live = True

                required_chunks = 2 if is_live else self._prebuffer_size
                
                # [v4.0] Ottimizzazione dinamica per chunk grandi (TTS)
                # Se il primo chunk è gigante (>12KB, circa 400ms), partiamo subito
                # per evitare i "scatti" dovuti all'attesa del secondo secondo di audio.
                if len(first_chunk) > 12288:
                    required_chunks = 1

                if not self._is_playing_out and qsize < required_chunks:
                    time.sleep(0.005)  # Attesa caricamento buffer
                    continue

                # [DEBUG] Log inizio drain (solo all'inizio di ogni burst)
                if _chunks_played == 0 or _chunks_played % 50 == 0:
                    self.get_logger().info(
                        f"🔈 [PLAYBACK] drain start: q={qsize}, "
                        f"required={required_chunks}, live={is_live}")

                # [FIX] Rimosso il break — svuota la coda in una raffica continua
                # così lo stream PyAudio non va mai in underrun
                while not self._audio_out_queue.empty() and not self._shutdown:
                    try:
                        self._is_playing_out = True  # Altoparlante attivo e in riproduzione
                        pcm_bytes = self._audio_out_queue.get_nowait()
                        if self.out_stream is not None:
                            with self._out_lock:
                                self.out_stream.write(pcm_bytes)
                                _chunks_played += 1
                    except queue.Empty:
                        break

            except Exception as e:
                self.get_logger().error(f"Errore playback worker: {e}")
                time.sleep(0.1)

    def _play_audio(self, pcm_bytes: bytes):
        self._last_playback_time = time.time()
        try:
            self._audio_out_queue.put_nowait(pcm_bytes)
        except queue.Full:
            self.get_logger().warning("Coda audio piena, frame saltato.")

    def _generate_beep(self, freq: float = 1000.0, duration: float = 0.3) -> bytes:
        """Genera un beep sinusoidale stereo con fade-out al rate hardware."""
        rate = self._out_hw_rate if self._out_hw_rate is not None else SAMPLE_RATE
        t    = np.linspace(0, duration, int(rate * duration), False)
        fade = np.linspace(1.0, 0.0, len(t))
        # Volume udibile ma non distorto (~20% picco)
        mono = (np.sin(2 * np.pi * freq * t) * fade * 6500).astype(np.int16)
        
        # Stereo interleaved
        stereo = np.empty(len(t) * 2, dtype=np.int16)
        stereo[0::2] = mono
        stereo[1::2] = mono
        return stereo.tobytes()

    # ------------------------------------------------------------------ #
    # LED
    # ------------------------------------------------------------------ #
    def set_led(self, effect: str):
        msg = String()
        msg.data = f"LED_EFFECT:{effect}\n"
        self.led_pub.publish(msg)

    # ------------------------------------------------------------------ #
    # Subscriber callbacks
    # ------------------------------------------------------------------ #
    def _tts_speaking_cb(self, msg: Bool):
        self._is_tts_speaking = msg.data
        if msg.data:
            self._ev_tts.set()
            # [v14.1] LED SUCCESS (verde fisso) quando Marcus sta parlando
            self.set_led('SUCCESS')
        else:
            self._ev_tts.clear()
            if not self._ev_listening.is_set():
                # Torna allo stato dell'umore corrente quando finisce di parlare
                self.set_led(self._current_mood)

    def _mood_cb(self, msg: String):
        self._current_mood = msg.data
        self.get_logger().info(f"🎭 [VUI] Nuovo stato emotivo ricevuto: {self._current_mood}")
        # Se non stiamo parlando (TTS) e non stiamo ascoltando attivamente, aggiorna subito il LED
        if not self._is_tts_speaking and not self._ev_listening.is_set():
            self.set_led(self._current_mood)

    def _music_playing_cb(self, msg: Bool):
        """[v11.0] Tracks if Spotify is playing to inhibit VAD"""
        self._is_music_playing = msg.data
        if msg.data:
            self.get_logger().info("🎵 [MUSIC] Spotify in riproduzione: VAD inibito (Porcupine attivo).")
        else:
            self.get_logger().info("🎵 [MUSIC] Spotify in pausa: VAD riattivato.")

    def _mic_mute_cb(self, msg: Bool):
        if msg.data:
            self._stop_listen_timer()
            self._ev_listening.clear()
            self.set_led(self._current_mood)
            # Svuota la coda audio per interrompere immediatamente il parlato
            while not self._audio_out_queue.empty():
                try:
                    self._audio_out_queue.get_nowait()
                except queue.Empty:
                    break
            # Genera ed esegue un beep di disattivazione a due toni calanti
            try:
                beep_bytes = self._generate_beep(freq=600.0, duration=0.15) + self._generate_beep(freq=400.0, duration=0.15)
                self._audio_out_queue.put_nowait(beep_bytes)
            except Exception:
                pass
        else:
            self._ev_listening.set()
            self.set_led('LISTENING')
            self._start_listen_timer()  # [FIX] Start auto-timeout when unmuted

    def _interrupt_cb(self, msg: Bool):
        if msg.data:
            self.get_logger().info("🤫 [VUI] Ricevuto comando di INTERRUPT/SILENCE! Svuoto la coda audio...")
            # Svuota la coda audio per interrompere immediatamente il parlato
            drained = 0
            while not self._audio_out_queue.empty():
                try:
                    self._audio_out_queue.get_nowait()
                    drained += 1
                except queue.Empty:
                    break
            self.get_logger().info(f"🔇 [VUI] Svuotati {drained} chunk dalla coda di riproduzione.")

    def _speaker_audio_cb(self, msg: AudioData):
        if self._shutdown or not msg.data:
            return
        try:
            raw_bytes = bytes(msg.data)

            # [DEBUG] Log primo chunk e ogni 20 successivi
            self._audio_chunk_count += 1
            is_first = (self._audio_chunk_count == 1)

            is_live = len(raw_bytes) < _NATIVE_AUDIO_CHUNK_THRESHOLD
            current_source = "live" if is_live else "tts"

            if self._last_audio_source != current_source:
                self._playback_ratecv_state = None
                self._last_audio_source = current_source
                self.get_logger().info(f"🔊 [SPEAKER] Cambio sorgente: {current_source}")

            # Auto-regolazione dinamica del volume in funzione del rumore ambientale
            if self.enable_auto_volume:
                # Regola dinamicamente il volume basandosi sulla baseline di 0.10 (silenzio, EMA <= 100)
                # fino a un massimo di 0.50 (rumoroso, EMA >= 800)
                raw_noise = self._ambient_noise_ema
                if raw_noise <= 100.0:
                    self.playback_volume = 0.10
                elif raw_noise >= 800.0:
                    self.playback_volume = 0.50
                else:
                    self.playback_volume = 0.10 + (raw_noise - 100.0) * (0.40 / 700.0)

            input_rate = _NATIVE_AUDIO_RATE if is_live else _STD_AUDIO_RATE

            # Converti sempre il sample rate se il rate d'ingresso differisce da quello hardware
            if self.out_stream and input_rate != self._out_hw_rate:
                audio_converted_bytes, self._playback_ratecv_state = audioop.ratecv(
                    raw_bytes, 2, 1, input_rate, self._out_hw_rate, self._playback_ratecv_state
                )
                audio_np = np.frombuffer(audio_converted_bytes, dtype=np.int16)
            else:
                audio_np = np.frombuffer(raw_bytes, dtype=np.int16)

            # Applica guadagno/riduzione volume e previene overflow/clipping
            scaled_audio = audio_np.astype(np.float32) * self.playback_volume
            audio_np = np.clip(scaled_audio, -32768, 32767).astype(np.int16)
            
            # Duplica mono in stereo interleaved
            final_audio = np.repeat(audio_np, 2).tobytes()
            audio_ref = audio_np

            rms = np.sqrt(np.mean(audio_ref.astype(np.float32) ** 2))
            intensity = int(min(255, (rms / 2000.0) * 255.0))

            if is_first or self._audio_chunk_count % 20 == 0:
                qsize = self._audio_out_queue.qsize()
                self.get_logger().info(
                    f"🔊 [SPEAKER] chunk #{self._audio_chunk_count}: "
                    f"{len(raw_bytes)}B -> {len(final_audio)}B "
                    f"(src={current_source}, HW={self._out_hw_rate}Hz, "
                    f"rms={rms:.0f}, q={qsize})")

            if intensity > 10:
                self.set_led(f"SPEAKING:{intensity}")

            self._play_audio(final_audio)
        except Exception as e:
            self.get_logger().error(f"Errore speaker_audio_cb: {e}", exc_info=True)

    # ------------------------------------------------------------------ #
    # Timeout listener
    # ------------------------------------------------------------------ #
    def _start_listen_timer(self):
        self._stop_listen_timer()
        self._listen_timer = self.create_timer(
            self._listen_timeout_sec, self._on_listen_timeout)

    def _stop_listen_timer(self):
        if self._listen_timer is not None:
            self._listen_timer.cancel()
            self._listen_timer = None

    def _on_listen_timeout(self):
        self.get_logger().warning(
            f"Timeout ascolto ({self._listen_timeout_sec}s): reset IDLE automatico.")
        self._stop_listen_timer()
        self._ev_listening.clear()
        self.set_led(self._current_mood)
        mute_msg = Bool()
        mute_msg.data = True
        self.mic_mute_pub.publish(mute_msg)

    # ------------------------------------------------------------------ #
    # [v3.0] PUBBLICAZIONE ROS 2 — metodi VAD gate
    # ------------------------------------------------------------------ #
    def _publish_preroll(self) -> None:
        """
        Pubblica i PRE_ROLL_FRAMES frame come un singolo messaggio ROS 2.
        Usa memoryview su _preroll_ba per evitare copie intermedie.
        """
        mv = memoryview(self._preroll_ba)
        for i in range(PRE_ROLL_FRAMES):
            idx   = (self._ring_write_idx - PRE_ROLL_FRAMES + i) % MAX_RING_FRAMES
            start = i * FRAME_SIZE * 2
            end   = start + FRAME_SIZE * 2
            mv[start:end] = self._speech_ring[idx].tobytes()
        msg      = AudioData()
        msg.data = bytes(self._preroll_ba)   # copia necessaria per ROS 2
        self._pub_speech.publish(msg)

    def _publish_audio_frame(self, frame_int16: np.ndarray) -> None:
        msg      = AudioData()
        msg.data = frame_int16.tobytes()     # [DSP-HOT] tobytes() su view 320 campioni
        self._pub_speech.publish(msg)

    def _publish_end_of_speech(self) -> None:
        msg      = AudioData()
        msg.data = b''                       # frame vuoto = segnale end-of-speech per il LLM
        self._pub_speech.publish(msg)
        self.get_logger().info("[VAD] <<< VOICE END: Segnale End-of-Speech inviato a Gemini.")
        # [v14.1] LED THINKING (blu flicker) = stiamo aspettando la risposta di Gemini
        self.set_led('THINKING')
        # [v6.1] Reset del timer di ascolto: i 3 minuti ripartono dall'ultima parola pronunciata
        self._start_listen_timer()

    # ------------------------------------------------------------------ #
    # [v3.0] _process_vad_frame — eseguito nel hot-path del callback
    # ------------------------------------------------------------------ #
    def _process_vad_frame(self, frame_boosted: np.ndarray, frame_raw: np.ndarray) -> None:
        """
        Processa un singolo frame VAD da 320 campioni.
        Aggiorna ring buffer SEMPRE. Gestisce speech gate.
        Zero allocazioni heap.
        """
        # [DSP-HOT] aggiorna ring buffer SEMPRE con il segnale BOOSTED da caricare su Gemini
        np.copyto(self._speech_ring[self._ring_write_idx], frame_boosted)
        self._ring_write_idx = (self._ring_write_idx + 1) % MAX_RING_FRAMES

        # [DSP-HOT] Calcolo RMS per noise gate pre-VAD (su segnale BOOSTED)
        rms = np.sqrt(np.mean(frame_boosted.astype(np.float32)**2))
        
        # Soglia dinamica: aumentiamo leggermente durante il TTS per ignorare eco residua
        current_threshold = self.noise_gate_threshold
        if self._is_tts_speaking:
            current_threshold *= 1.2
            
        if rms < current_threshold:
            is_voice = False
        elif self._is_music_playing:
            # [v11.0] VAD Inibito se suona musica (per evitare eco), ci affidiamo solo alla Wake Word
            is_voice = False
        else:
            # [DSP-HOT] tobytes() su segnale RAW (non boosted) per evitare falsi positivi nel VAD
            try:
                is_voice = self._vad.is_speech(frame_raw.tobytes(), SAMPLE_RATE)
            except ValueError as e:
                self.get_logger().error(f'[VAD] frame malformato (bug): {e}')
                is_voice = False
            except Exception as e:
                self.get_logger().warn(f'[VAD] errore generico: {e}')
                is_voice = False

        if is_voice:
            self._speech_frame_count  += 1
            self._silence_frame_count  = 0

            if (self._speech_frame_count >= MIN_SPEECH_FRAMES
                    and not self._is_speech_active):
                self._is_speech_active = True
                self._voice_frame_count = 0
                self.get_logger().info("[VAD] >>> VOICE START (Gated for TTS)")
                # [v10.2] Se l'AI parla, non inviamo ancora il pre-roll al cloud per evitare eco-interruzione.
                # Il pre-roll verrà inviato se scatta il Barge-in locale o alla fine del TTS.
                if not self._is_tts_speaking:
                    self._publish_preroll()   

            if self._is_speech_active:
                self._voice_frame_count += 1
                if self._voice_frame_count % 50 == 0:
                    self.get_logger().info(f"🎤 [VAD] ...registrazione in corso ({self._voice_frame_count} frames)...")
                
                # [v10.2] Soppressione upload verso Gemini durante il TTS per evitare auto-interruzione
                if not self._is_tts_speaking or self._barge_in_triggered:
                    self._publish_audio_frame(frame_boosted)
        else:
            self._speech_frame_count = 0
            if self._is_speech_active:
                self._silence_frame_count += 1
                if self._silence_frame_count >= self._cfg_max_silence:
                    self._is_speech_active    = False
                    self._silence_frame_count = 0
                    self._voice_frame_count   = 0
                    self._publish_end_of_speech()

    # ------------------------------------------------------------------ #
    # [v3.1] Helper per rilevazione Wake Word
    # ------------------------------------------------------------------ #
    def _on_wakeword_detected(self):
        """Gestisce le azioni da compiere quando viene rilevata la wake word."""
        if self._ev_listening.is_set():
            return # Già in ascolto, evita beep multipli se la parola è ripetuta

        self.get_logger().info("WAKE WORD 'MARCUS' RILEVATA!")
        self._ev_listening.set()
        self.set_led('LISTENING')

        # Svuota la coda audio per interrompere eventuali messaggi in corso
        while not self._audio_out_queue.empty():
            try:
                self._audio_out_queue.get_nowait()
            except queue.Empty:
                break

        self._play_audio(self._generate_beep())
        self._start_listen_timer()

        # Comunica il cambio di stato ai nodi AI
        unmute_msg = Bool()
        unmute_msg.data = False
        self.mic_mute_pub.publish(unmute_msg)

        ww_msg = String()
        ww_msg.data = "detected"
        self.ww_pub.publish(ww_msg)

    # ------------------------------------------------------------------ #
    # Callback audio di input — eseguita dal thread interno PyAudio
    # Si limita ad accodare i byte raw nella coda thread-safe
    # ------------------------------------------------------------------ #
    def _audio_input_callback(self, in_data, frame_count, time_info, status):
        if not self._shutdown and in_data:
            try:
                self._audio_in_queue.put_nowait(in_data)
            except queue.Full:
                pass
        return (None, pyaudio.paContinue)

    # ------------------------------------------------------------------ #
    # Worker thread di processamento audio in background
    # Esegue tutta la logica pesante di VAD e Porcupine
    # ------------------------------------------------------------------ #
    def _audio_processing_worker(self):
        self.get_logger().info("Worker thread di processamento audio VUI avviato.")
        while not self._shutdown:
            try:
                in_data = self._audio_in_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self.porcupine is None:
                continue

            try:
                # ---------------------------------------------------------- #
                # Mix L+R → mono int16 con Filtro Passa-Alto (HPF @ 140 Hz)
                # ---------------------------------------------------------- #
                audio_stereo = np.frombuffer(in_data, dtype=np.int16)
                stt_gain_to_use = self.stt_gain
                
                l_ch = audio_stereo[::2].astype(np.float32)
                r_ch = audio_stereo[1::2].astype(np.float32)
                n    = min(len(l_ch), len(r_ch), CHUNK_SIZE)
                
                # 1. Filtro Passa-Alto @ 140 Hz (HPF) per eliminare ronzio ventola Pi 5
                if HAS_SCIPY and self._hpf_sos is not None:
                    hp_l, self._hpf_zi_l = sosfilt(self._hpf_sos, l_ch[:n], zi=self._hpf_zi_l)
                    hp_r, self._hpf_zi_r = sosfilt(self._hpf_sos, r_ch[:n], zi=self._hpf_zi_r)
                else:
                    # RC High-Pass Filter fallback (fc ≈ 140Hz @ 16kHz)
                    alpha_hpf = 0.9478
                    hp_l = np.zeros(n, dtype=np.float32)
                    hp_r = np.zeros(n, dtype=np.float32)
                    yl, xl = self._hpf_prev_y_l, self._hpf_prev_x_l
                    yr, xr = self._hpf_prev_y_r, self._hpf_prev_x_r
                    for i in range(n):
                        yl = alpha_hpf * (yl + l_ch[i] - xl)
                        xl = l_ch[i]
                        hp_l[i] = yl
                        yr = alpha_hpf * (yr + r_ch[i] - xr)
                        xr = r_ch[i]
                        hp_r[i] = yr
                    self._hpf_prev_y_l, self._hpf_prev_x_l = yl, xl
                    self._hpf_prev_y_r, self._hpf_prev_x_r = yr, xr

                # 2. Selezione dinamica del canale con maggior energia vocale pulita
                rms_l_hp = np.sqrt(np.mean(hp_l ** 2))
                rms_r_hp = np.sqrt(np.mean(hp_r ** 2))

                if rms_r_hp > rms_l_hp * 1.15:
                    selected_hp = hp_r
                    rms_hp_current = rms_r_hp
                else:
                    selected_hp = hp_l
                    rms_hp_current = rms_l_hp
                
                # Calcolo continuo EMA rumore ambientale (aggiornato sul segnale HPF quando l'AI non parla e VAD è inattivo)
                if not self._is_tts_speaking and not self._is_music_playing and not getattr(self, '_is_playing_out', False) and not self._is_speech_active:
                    if rms_hp_current < self._ambient_noise_ema * 1.5:
                        alpha_ema = 0.05
                    else:
                        alpha_ema = 0.001
                    self._ambient_noise_ema = (alpha_ema * float(rms_hp_current)) + ((1.0 - alpha_ema) * self._ambient_noise_ema)
                    # Baseline HPF ambient tra 30.0 e 400.0 RMS
                    self._ambient_noise_ema = float(np.clip(self._ambient_noise_ema, 30.0, 400.0))
                    
                    self._ambient_noise_chunks += 1
                    if self._ambient_noise_chunks >= 16:  # Ogni ~1 sec (@16000Hz / 960)
                        self._ambient_noise_chunks = 0
                        msg = Float32()
                        msg.data = self._ambient_noise_ema
                        self.ambient_noise_pub.publish(msg)

                # A. Auto-regolazione soglia noise gate (se abilitata) calibrata su HPF per far-field
                if self.enable_adaptive_threshold:
                    boosted_ambient = self._ambient_noise_ema * stt_gain_to_use
                    # Soglia dinamica per far-field: clamped tra [800.0, 4500.0] (intercetta voce a 2-3m, RMS ~1500-3000)
                    self.noise_gate_threshold = float(np.clip(boosted_ambient * 1.25 + 300.0, 800.0, 4500.0))

                # B. Taratura adattiva del timeout silenzio (se abilitato)
                if self.enable_adaptive_silence:
                    raw_ambient = self._ambient_noise_ema
                    if raw_ambient < 100.0:
                        self._cfg_max_silence = 40  # ~800ms (silenzio perfetto)
                    elif raw_ambient < 200.0:
                        self._cfg_max_silence = 28  # ~560ms
                    elif raw_ambient < 350.0:
                        self._cfg_max_silence = 35  # ~700ms
                    else:
                        self._cfg_max_silence = 45  # ~900ms

                # Diagnostica Volume MIC (RMS ogni ~1s @ 960 chunks)
                self._rms_chunk_count += 1
                rms_l = rms_l_hp
                rms_r = rms_r_hp
                rms_boosted = 0.0
                
                # ---------------------------------------------------------- #
                # Controllo Parlato AI (TTS o Gemini Live playback)
                # ---------------------------------------------------------- #
                ai_speaking_now = self._ev_tts.is_set() or getattr(self, '_is_playing_out', False)
                ai_speaking_was = self._tts_active
                tts_now = ai_speaking_now

                # Transizione False→True: registra timestamp di inizio parlato AI
                if ai_speaking_now and not ai_speaking_was:
                    self._tts_start_time      = time.monotonic()
                    self._barge_in_triggered  = False
                    self._barge_in_frame_count = 0

                self._tts_active = ai_speaking_now
                self._is_tts_speaking = ai_speaking_now

                # Salva l'ultimo istante in cui l'AI ha parlato
                if ai_speaking_now:
                    self._last_ai_speaking_time = time.monotonic()

                # Calcola il periodo di cooldown (600 ms) per dissipare l'eco residua hardware del microfono
                ai_cooldown_active = False
                if self._last_ai_speaking_time > 0.0:
                    if time.monotonic() - self._last_ai_speaking_time < 0.6:
                        ai_cooldown_active = True

                # Transizione True→False o durante cooldown: AI ha terminato di parlare — reset VAD
                if (ai_speaking_was and not ai_speaking_now) or ai_cooldown_active:
                    self._speech_frame_count  = 0
                    self._silence_frame_count = 0
                    self._is_speech_active    = False
                    self._vad_residual_len    = 0
                    self._ring_write_idx      = 0
                    self._barge_in_triggered  = False
                    self._barge_in_frame_count = 0

                # Dynamic Gain Control: riduciamo il guadagno durante il TTS per evitare eco
                stt_gain_to_use = self.stt_gain
                if self._is_tts_speaking:
                    # Se l'AI parla, abbassiamo il boost per proteggere il VAD dall'eco
                    # ma lo manteniamo a un livello moderato per consentire il barge-in e la wake word
                    stt_gain_to_use = max(2.5, self.stt_gain * 0.15)
                elif ai_cooldown_active:
                    # Durante il cooldown di 600ms, azzeriamo il guadagno software per assorbire l'eco finale del buffer
                    stt_gain_to_use = 0.0

                if self._cfg_diag_mode and self._rms_chunk_count % 16 == 0:
                    rms_boosted = rms_l * stt_gain_to_use
                    self.get_logger().info(
                        f"🎤 [MIC] Volume HPF: L_RMS={rms_l:.1f} | R_RMS={rms_r:.1f} | BOOSTED={rms_boosted:.1f} | "
                        f"Ambient_EMA={self._ambient_noise_ema:.1f} | Gate={self.noise_gate_threshold:.1f} | "
                        f"MaxSilence={self._cfg_max_silence} frames (Gain: {stt_gain_to_use}x)")
                
                # ---------------------------------------------------------- #
                # Applica stt_gain_to_use al segnale d'ingresso filtrato per VAD e Porcupine
                # con Peak Limiter / AGC software [v17.0]
                # ---------------------------------------------------------- #
                # 1. Applica il guadagno software iniziale sul segnale HPF selezionato
                boosted_float = selected_hp[:n] * stt_gain_to_use

                # 2. Peak Limiter / AGC Software in tempo reale sui chunk PCM
                # Soglia (Threshold): 26000. Se il picco supera 26000, attiva attenuazione istantanea (Attack = 0ms)
                # in modo che i campioni non saturino oltre 30000.
                peak_val = np.max(np.abs(boosted_float))
                
                if peak_val > 26000.0:
                    # Tempo di attacco istantaneo (0ms): Calcola il moltiplicatore necessario sul chunk corrente
                    target_gain = 30000.0 / peak_val
                    # Applica immediatamente il fattore di compressione limitatore se è più forte del guadagno corrente
                    if target_gain < self._limiter_gain:
                        self._limiter_gain = target_gain
                else:
                    # Tempo di rilascio lineare (Release Time ~900ms): risale verso 1.0
                    if self._limiter_gain < 1.0:
                        self._limiter_gain = min(1.0, self._limiter_gain + self._limiter_release_rate)

                # 3. Applica il limiter_gain calcolato dinamicamente a tutti i campioni del chunk
                if self._limiter_gain < 1.0:
                    boosted_float *= self._limiter_gain

                # 4. Cast sicuro a int16 con clipping a 16-bit
                np.copyto(self._int16_vad_buf[:n], np.clip(boosted_float, -32768, 32767).astype(np.int16))


                # ---------------------------------------------------------- #
                # Porcupine — wake word detection su segnale int16 grezzo
                # ---------------------------------------------------------- #
                is_listening = self._ev_listening.is_set()

                if not is_listening:
                    porc_idx = 0

                    # A. Gestione residuo dal callback precedente
                    if self._porcupine_residual_len > 0:
                        needed = self.frame_length - self._porcupine_residual_len
                        if needed <= CHUNK_SIZE:
                            np.copyto(self._porcupine_assembly_buf[:self._porcupine_residual_len],
                                      self._porcupine_residual_buf[:self._porcupine_residual_len])
                            np.copyto(self._porcupine_assembly_buf[self._porcupine_residual_len:],
                                      self._int16_vad_buf[:needed])

                            pcm = self._porcupine_assembly_buf.tolist()
                            result = self.porcupine.process(pcm)
                            if result >= 0:
                                self.get_logger().info("!!! PORCUPINE MATCH DETECTED (Pre-roll loop) !!!")
                                self._on_wakeword_detected()

                            porc_idx = needed
                            self._porcupine_residual_len = 0

                    # B. Processa i blocchi interi nel chunk corrente
                    while porc_idx + self.frame_length <= CHUNK_SIZE:
                        porcupine_frame = self._int16_vad_buf[porc_idx : porc_idx + self.frame_length]

                        pcm = porcupine_frame.tolist()
                        result = self.porcupine.process(pcm)
                        if result >= 0:
                            self.get_logger().info("!!! PORCUPINE MATCH DETECTED (Main loop) !!!")
                            self._on_wakeword_detected()
                        porc_idx += self.frame_length

                    # C. Conserva il residuo finale
                    rest = CHUNK_SIZE - porc_idx
                    if rest > 0:
                        np.copyto(self._porcupine_residual_buf[:rest], self._int16_vad_buf[porc_idx:])
                        self._porcupine_residual_len = rest

                    # Aggiorna is_listening se è stata rilevata la wake word nel loop
                    is_listening = self._ev_listening.is_set()

                # ---------------------------------------------------------- #
                # Barge-In Detection: voce sostenuta durante TTS
                # ---------------------------------------------------------- #
                if (tts_now
                        and self._cfg_enable_barge_in
                        and not self._barge_in_triggered
                        and (time.monotonic() - self._tts_start_time) > self._barge_in_min_tts_s):

                    if self._is_speech_active:
                        self._barge_in_frame_count += 1
                    else:
                        self._barge_in_frame_count = 0

                    if self._is_speech_active and self._barge_in_frame_count % 2 == 0:
                         self.get_logger().info(
                              f"🕵️ [DEBUG-AEC] TTS Echo Alert | Frame: {self._barge_in_frame_count} | "
                              f"Raw RMS: {rms_l:.1f} | Boosted: {rms_boosted:.1f}"
                         )

                    if self._barge_in_frame_count >= self._barge_in_min_frames:
                        self._barge_in_triggered = True
                        self.get_logger().warning(
                            f"🎤 [BARGE-IN] Interruzione rilevata dopo {self._barge_in_frame_count} frames di voce sostenuta. Interrompo Marcus..."
                        )
                        drained = 0
                        while not self._audio_out_queue.empty():
                            try:
                                self._audio_out_queue.get_nowait()
                                drained += 1
                            except queue.Empty:
                                break
                        self.get_logger().info(f"🔇 [BARGE-IN] Svuotati {drained} chunk dalla coda")

                        bi_msg = Bool()
                        bi_msg.data = True
                        self.barge_in_pub.publish(bi_msg)

                        self._ev_listening.set()
                        self._start_listen_timer()
                        is_listening = True

                # ---------------------------------------------------------- #
                # VAD gate — solo se in ascolto
                # ---------------------------------------------------------- #
                if is_listening:
                    if not self._cfg_enable_vad_gate:
                        self._publish_audio_frame(self._int16_vad_buf[:FRAME_SIZE])
                        self._consecutive_errors = 0
                        continue

                    start_idx = 0

                    if self._vad_residual_len > 0:
                        needed = FRAME_SIZE - self._vad_residual_len
                        np.copyto(self._assembly_buf[:self._vad_residual_len],
                                  self._vad_residual_buf[:self._vad_residual_len])
                        np.copyto(self._assembly_buf[self._vad_residual_len:],
                                  self._int16_vad_buf[:needed])
                                  
                        selected_hp_int16 = np.clip(selected_hp[:needed], -32768, 32767).astype(np.int16)
                        np.copyto(self._assembly_raw_buf[:self._vad_residual_len],
                                  self._vad_residual_raw_buf[:self._vad_residual_len])
                        np.copyto(self._assembly_raw_buf[self._vad_residual_len:],
                                  selected_hp_int16)
                                  
                        self._process_vad_frame(self._assembly_buf, self._assembly_raw_buf)
                        start_idx = needed
                        self._vad_residual_len = 0

                    i = start_idx
                    selected_hp_all_int16 = np.clip(selected_hp, -32768, 32767).astype(np.int16)
                    while i + FRAME_SIZE <= CHUNK_SIZE:
                        self._process_vad_frame(
                            self._int16_vad_buf[i:i + FRAME_SIZE],
                            selected_hp_all_int16[i:i + FRAME_SIZE]
                        )
                        i += FRAME_SIZE

                    residuo = CHUNK_SIZE - i
                    if residuo > 0:
                        np.copyto(self._vad_residual_buf[:residuo],
                                  self._int16_vad_buf[i:])
                        np.copyto(self._vad_residual_raw_buf[:residuo],
                                  selected_hp_all_int16[i:])
                        self._vad_residual_len = residuo

                self._consecutive_errors = 0

            except Exception as e:
                self._consecutive_errors += 1
                if self._consecutive_errors == 1:
                    self.get_logger().warning(f"Errore worker audio: {e}")
                elif self._consecutive_errors % 100 == 0:
                    self.get_logger().error(f"Errore worker audio #{self._consecutive_errors}: {e}")


    # ------------------------------------------------------------------ #
    # Device discovery
    # ------------------------------------------------------------------ #
    def _find_audio_devices(self):
        in_idx = out_idx = None
        info       = self.pa.get_host_api_info_by_index(0)
        numdevices = info.get('deviceCount')

        self.get_logger().info("Dispositivi audio disponibili:")
        for i in range(numdevices):
            dev   = self.pa.get_device_info_by_host_api_device_index(0, i)
            name  = dev.get('name')
            n_in  = dev.get('maxInputChannels')
            n_out = dev.get('maxOutputChannels')
            self.get_logger().info(f"  [{i}] {name} (in:{n_in}, out:{n_out})")

            # Primo tentativo: cerca il dispositivo target (es. ReSpeaker)
            if self.device_name_target.lower() in name.lower():
                if n_in > 0 and in_idx is None:
                    in_idx = i
                if n_out > 0 and out_idx is None:
                    out_idx = i

        # Secondo tentativo fallback: se non trovati, cerca PipeWire/PulseAudio/Default per multiplexing condiviso
        if in_idx is None or out_idx is None:
            for i in range(numdevices):
                dev   = self.pa.get_device_info_by_host_api_device_index(0, i)
                name  = dev.get('name')
                n_in  = dev.get('maxInputChannels')
                n_out = dev.get('maxOutputChannels')
                if "pulse" in name.lower() or "default" in name.lower() or "pipewire" in name.lower():
                    if n_in > 0 and in_idx is None:
                        in_idx = i
                    if n_out > 0 and out_idx is None:
                        out_idx = i

        return in_idx, out_idx

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #
    def destroy_node(self):
        self._shutdown = True
        try:
            self.get_logger().info("Spegnimento VUI Node...")
        except Exception:
            pass # Il contesto potrebbe essere già invalidato
        self._stop_listen_timer()
        if hasattr(self, '_playback_thread'):
            self._playback_thread.join(timeout=2.0)
        if hasattr(self, '_worker_thread'):
            self._worker_thread.join(timeout=2.0)
            
        if hasattr(self, 'in_stream') and self.in_stream:
            self.in_stream.stop_stream()
            self.in_stream.close()
        if hasattr(self, 'out_stream') and self.out_stream:
            self.out_stream.stop_stream()
            self.out_stream.close()
            
        if hasattr(self, 'porcupine') and self.porcupine:
            self.porcupine.delete()
            
        if hasattr(self, 'pa'):
            self.pa.terminate()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    try:
        node = ReSpeakerVUINode()
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
            pass
        finally:
            node.destroy_node()
    except Exception as e:
        # Fallback if logger is unavailable
        print(f"Errore fatale nel nodo VUI: {e}", file=sys.stderr)
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
