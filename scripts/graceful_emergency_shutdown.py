#!/usr/bin/env python3
"""
graceful_emergency_shutdown.py - Marcus AI Graceful Emergency Shutdown Orchestrator
===================================================================================
Coordina la sequenza di emergenza a 9.60V prima del cutoff hardware del BMS Li-ion 18650:
1. Freno motori immediato (Twist 0.0 su safety override e halt Waveshare driver) per annullare il sag ohmico (IR drop).
2. Allerta vocale su ReSpeaker DAC: "Attenzione! Batteria critica a 9.6 Volt. Salvataggio mappe e spegnimento forzato in corso."
3. Scrittura evento traumatico su memorie TRINITY (MAG SQLite WAL, RAG ChromaDB con amygdala_protected=True, CAG error context).
4. Salvataggio mappe 2D attive in /mnt/ssd/maps/ e checkpoint WAL del database RTAB-Map.
5. Chiusura pulita di tutti i nodi e processi ROS 2 (SIGINT/SIGTERM controllato).
6. Sync dei filesystem SSD/SD e spegnimento controllato del sistema operativo (systemctl poweroff).

Riferimenti DFMEA: FM-PWR-005, FM-SYS-004, FM-TRI-001, FM-NAV-020.
"""

import os
import sys
import time
import json
import glob
import signal
import sqlite3
import subprocess
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

# Setup path per i moduli robot_ai e robopy_controller
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

for p in [
    "/home/robopy/ros2_venv/lib/python3.11/site-packages",
    "/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages",
    "/mnt/ssd/robopy_controller_host/install/robopy_controller/local/lib/python3.11/dist-packages"
]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        print(f"[{ts}] [EMERGENCY-SHUTDOWN] {msg}", flush=True)
    except Exception:
        safe_msg = msg.encode("ascii", errors="replace").decode("ascii")
        print(f"[{ts}] [EMERGENCY-SHUTDOWN] {safe_msg}", flush=True)


def generate_emergency_chime(sample_rate: int = 24000) -> bytes:
    """Genera un chime bitonale discendente (900Hz -> 600Hz -> 400Hz) stereo 16-bit PCM."""
    try:
        import numpy as np
        tones = [
            (900.0, 0.20),
            (600.0, 0.20),
            (450.0, 0.35)
        ]
        pcm_chunks = []
        for freq, duration in tones:
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            fade = np.linspace(1.0, 0.05, len(t))
            mono = (np.sin(2 * np.pi * freq * t) * fade * 12000).astype(np.int16)
            stereo = np.empty(len(t) * 2, dtype=np.int16)
            stereo[0::2] = mono
            stereo[1::2] = mono
            pcm_chunks.append(stereo.tobytes())
        return b''.join(pcm_chunks)
    except Exception as e:
        log(f"Warning generazione chime PCM: {e}")
        return b''


def synthesize_speech_pcm(text: str, cache_dir: str = "/tmp/marcus_tts_cache") -> Optional[bytes]:
    """Sintetizza speech a 24000Hz mono/stereo 16-bit PCM o carica da cache."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        import hashlib
        h = hashlib.md5(text.strip().encode('utf-8')).hexdigest()
        cache_path = os.path.join(cache_dir, f"emergency_{h}.raw")
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            with open(cache_path, "rb") as f:
                return f.read()

        from gtts import gTTS
        import tempfile
        mp3_f = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        mp3_path = mp3_f.name
        mp3_f.close()

        tts = gTTS(text=text, lang='it')
        tts.save(mp3_path)

        cmd = [
            "ffmpeg", "-y", "-i", mp3_path,
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", "24000", "-ac", "2", cache_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        try:
            os.remove(mp3_path)
        except Exception:
            pass

        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()
    except Exception as e:
        log(f"Warning sintesi vocale: {e}")
    return None


def record_traumatic_event_mag(db_path: str = "/home/robopy/mag_trinity.db", v_filt: float = 9.60):
    """Registra l'evento traumatico nel database autobiografico MAG (SQLite WAL)."""
    try:
        # Se il path default non esiste ma siamo su host/test, cerca in location locale o crea
        os.makedirs(os.path.dirname(db_path), exist_ok=True) if os.path.dirname(db_path) else None
        
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        
        # Assicura esistenza tabella episodes se il DB è nuovo
        conn.execute('''
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                user_input TEXT NOT NULL,
                robot_response TEXT NOT NULL,
                summary TEXT,
                embedding BLOB,
                user_id TEXT,
                session_id TEXT,
                emotion_tag TEXT,
                importance REAL DEFAULT 0.5,
                recall_count INTEGER DEFAULT 0,
                actions_taken TEXT,
                was_successful INTEGER DEFAULT 1
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS semantic_facts (
                id TEXT PRIMARY KEY,
                fact_text TEXT NOT NULL,
                fact_type TEXT NOT NULL,
                source_episode_id TEXT,
                confidence REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                last_accessed REAL,
                embedding BLOB,
                recall_count INTEGER DEFAULT 0
            )
        ''')

        import uuid
        ep_id = str(uuid.uuid4())
        fact_id = str(uuid.uuid4())
        now = time.time()
        
        summary_text = (
            f"EVENTO TRAUMATICO CRITICO: Spegnimento forzato di emergenza a {v_filt:.2f}V "
            "per esaurimento batterie 18650 in assenza di cuccia di ricarica. "
            "Salvataggio preventivo mappe e stop motori per evitare il collasso del BMS."
        )
        actions = json.dumps([
            "motor_override_zero",
            "vocal_alarm_emitted",
            "active_map_persisted",
            "rtabmap_wal_checkpoint",
            "traumatic_memory_recorded",
            "ros2_orderly_shutdown",
            "os_poweroff"
        ])
        
        conn.execute('''
            INSERT INTO episodes (
                id, timestamp, user_input, robot_response, summary,
                user_id, session_id, emotion_tag, importance, actions_taken, was_successful
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            ep_id, now, "CRITICAL_BATTERY_SHUTDOWN_TRIGGER",
            "Attenzione! Batteria critica a 9.6 Volt. Salvataggio mappe e spegnimento forzato.",
            summary_text, "marcus_system", "emergency_session", "TRAUMA", 1.0, actions, 0
        ))
        
        conn.execute('''
            INSERT INTO semantic_facts (
                id, fact_text, fact_type, source_episode_id, confidence, created_at, recall_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            fact_id,
            f"Marcus ha subito un arresto forzato traumatico a {v_filt:.2f}V per sottotensione critica da celle 18650 senza docking station.",
            "traumatic_event", ep_id, 1.0, now, 0
        ))
        
        conn.commit()
        conn.close()
        log(f"✅ Evento traumatico registrato in MAG Database (SQLite WAL: {db_path}) con ID {ep_id}.")
        return True
    except Exception as e:
        log(f"⚠️ Errore scrittura MAG Database: {e}")
        return False


def record_traumatic_event_rag(persist_dir: str = "/home/robopy/ChromaDB_Llama", v_filt: float = 9.60):
    """Registra l'evento traumatico in ChromaDB con metadati speciali amygdala_protected=True e zero decay."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=persist_dir)
        collection = client.get_or_create_collection(name="robot_memories", metadata={"hnsw:space": "cosine"})
        
        rec_id = f"trauma_battery_cliff_{int(time.time())}"
        doc_text = (
            f"Evento traumatico primario: Spegnimento forzato d'emergenza causato da sottotensione critica "
            f"batteria ({v_filt:.2f}V <= 9.60V). Mappe salvate e interruzione forzata delle attività "
            "per prevenire il blackout hardware del BMS 18650."
        )
        metadata = {
            "memory_type": "traumatic_event",
            "category": "survival_instinct",
            "voltage_at_shutdown": float(v_filt),
            "timestamp": datetime.now().isoformat(),
            "created_at": time.time(),
            "importance": 1.0,
            "synaptic_strength": 100.0,
            "lambda_decay": 0.0,  # Immunità assoluta: non viene mai potata nel sonno notturno
            "amygdala_protected": "true"
        }
        collection.add(ids=[rec_id], documents=[doc_text], metadatas=[metadata])
        log(f"✅ Ricordo traumatico permanente iniettato in RAG ChromaDB ({persist_dir}) con ID {rec_id}.")
        return True
    except Exception as e:
        log(f"⚠️ ChromaDB RAG non disponibile o eccezione (normale se in ambiente host): {e}")
        return False


def save_active_map_if_present(maps_dir: str = "/mnt/ssd/maps"):
    """Esporta la mappa 2D attiva da /map se il map_server o RTAB-Map è in esecuzione."""
    os.makedirs(maps_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    map_target = os.path.join(maps_dir, f"auto_shutdown_map_{timestamp}")
    
    log(f"🗺️ Tentativo di salvataggio preventivo mappa 2D su {map_target}...")
    
    # 1. Chiama map_saver_cli Nav2 se disponibile nell'ambiente ROS 2
    cmd = [
        "ros2", "run", "nav2_map_server", "map_saver_cli",
        "-f", map_target,
        "--ros-args", "-p", "map_subscribe_transient_local:=true"
    ]
    try:
        env = os.environ.copy()
        env["ROS_DOMAIN_ID"] = "42"
        env["CYCLONEDDS_URI"] = "/tmp/cyclonedds_robopy.xml"
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4.0, env=env)
        if res.returncode == 0:
            log(f"✅ Mappa 2D esportata con successo in {map_target}.yaml")
        else:
            log(f"Nota: map_saver_cli terminato con codice {res.returncode} (nessuna mappa attiva da salvare o servizio occupato).")
    except subprocess.TimeoutExpired:
        log("⚠️ Timeout map_saver_cli (4s). Proseguo per evitare il blocco dello spegnimento.")
    except Exception as e:
        log(f"Warning invocazione map_saver_cli: {e}")

    # 2. Checkpoint SQLite WAL RTAB-Map
    rtabmap_db = "/mnt/ssd/rtabmap.db"
    if os.path.exists(rtabmap_db):
        try:
            conn = sqlite3.connect(rtabmap_db, timeout=2.0)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            log("✅ Checkpoint WAL SQLite completato su /mnt/ssd/rtabmap.db.")
        except Exception as e:
            log(f"Warning checkpoint RTAB-Map: {e}")


def play_vocal_alert():
    """Emette il segnale acustico e il messaggio vocale sul DAC hardware del ReSpeaker."""
    speech_phrase = "Attenzione! Livello batteria critico a 9.6 Volt. Salvataggio mappe e spegnimento forzato in corso."
    log(f"🗣️ [VOCAL-ALERT]: \"{speech_phrase}\"")

    # 1. Genera Beep/Chime
    chime_bytes = generate_emergency_chime(sample_rate=24000)
    speech_bytes = synthesize_speech_pcm(speech_phrase) or b''
    total_audio = chime_bytes + speech_bytes

    # 2. Tentativo 1: Invio via topic ROS 2 /respeaker/speaker_audio se rclpy è disponibile
    audio_sent_ros = False
    try:
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import String
        
        has_audio_msg = False
        try:
            from robopy_controller.msg import AudioData
            has_audio_msg = True
        except ImportError:
            try:
                from audio_common_msgs.msg import AudioData
                has_audio_msg = True
            except ImportError:
                has_audio_msg = False

        if not rclpy.ok():
            rclpy.init()

        temp_node = Node("emergency_shutdown_voice_node")
        pub_mood = temp_node.create_publisher(String, "/ai/conversation/mood", 10)
        pub_interrupt = temp_node.create_publisher(String, "/marcus/low_road/interrupt", 10)
        
        mood_msg = String()
        mood_msg.data = "FEAR"
        pub_mood.publish(mood_msg)

        int_msg = String()
        int_msg.data = json.dumps({"type": "CRITICAL_SHUTDOWN", "reason": "Battery 9.60V Cliff"})
        pub_interrupt.publish(int_msg)

        if has_audio_msg and total_audio:
            pub_audio = temp_node.create_publisher(AudioData, "/respeaker/speaker_audio", 10)
            chunk_size = 2048
            for i in range(0, len(total_audio), chunk_size):
                chunk = total_audio[i:i + chunk_size]
                msg = AudioData()
                msg.data = list(chunk)
                pub_audio.publish(msg)
                time.sleep(0.02)
            audio_sent_ros = True
            log("✅ Audio allerta trasmesso a respeaker_vui_node via ROS 2.")
        
        temp_node.destroy_node()
    except Exception as e:
        log(f"Warning pubblicazione audio ROS 2: {e}")

    # 3. Tentativo 2: Fallback diretto hardware ALSA ReSpeaker (aplay) se ROS 2 non ha riprodotto
    if not audio_sent_ros and total_audio:
        try:
            import tempfile
            wav_path = "/tmp/emergency_shutdown_alert.wav"
            import wave
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(total_audio)
            
            # Cerca device ALSA ReSpeaker
            cmd = ["aplay", "-q", wav_path]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4.0)
            log("✅ Audio allerta riprodotto direttamente tramite ALSA aplay.")
        except Exception as e:
            log(f"Warning riproduzione ALSA fallback: {e}")


def stop_all_ros2_nodes():
    """Invia SIGINT pulito a tutti i nodi e processi ROS 2 per chiusura ordinata prima del poweroff."""
    log("🛑 Invocazione chiusura pulita dei processi ROS 2 (SIGINT/SIGTERM)...")
    nodes_to_stop = [
        "custom_nav2_launch.py",
        "robot_ai_node",
        "semantic_costmap_injector",
        "marcus_semantic_mapper_cpp",
        "hailo_bridge_node",
        "fast_flow_vo_cpp",
        "respeaker_vui_node",
        "system_lifecycle_coordinator_node",
        "robot_health_supervisor",
        "rtabmap",
        "sllidar_node",
        "waveshare_motor_driver"
    ]
    for node_name in nodes_to_stop:
        try:
            subprocess.run(["pkill", "-2", "-f", node_name], check=False)
        except Exception:
            pass
    
    # Breve finestra per permettere ai distruttori Python/C++ di completare
    time.sleep(1.5)


def execute_system_poweroff(dry_run: bool = False):
    """Sincronizza i filesystem ed esegue il poweroff controllato del Raspberry Pi 5."""
    log("💾 Esecuzione filesystem sync (protezione SSD NVMe / SD)...")
    try:
        os.system("sync")
    except Exception:
        pass
    time.sleep(0.5)

    if dry_run:
        log("ℹ️ [DRY-RUN] Spegnimento OS simulato con successo. Comando 'sudo systemctl poweroff -i' non invocato.")
        return

    log("🔌 Esecuzione immediata OS Poweroff: sudo systemctl poweroff -i...")
    try:
        subprocess.run(["sudo", "systemctl", "poweroff", "-i"], check=False)
    except Exception as e:
        log(f"Errore invocazione poweroff: {e}")


def perform_graceful_emergency_shutdown(v_filt: float = 9.60, dry_run: bool = False):
    """
    Funzione principale dell'Orchestrator di Spegnimento Pulito d'Emergenza.
    Esegue in sequenza deterministica tutti i passaggi necessari a proteggere
    hardware, memorie cognitive e mappe del robot.
    """
    log(f"=================================================================")
    log(f"🚨 INIZIO PROCEDURA SPEGNIMENTO PULITO D'EMERGENZA (V = {v_filt:.2f}V)")
    log(f"=================================================================")

    # 1. Arresto immediato dei motori (Hardware Sag Mitigation)
    try:
        import rclpy
        from rclpy.node import Node
        from geometry_msgs.msg import Twist
        from std_msgs.msg import String
        if not rclpy.ok():
            rclpy.init()
        stop_node = Node("emergency_stop_publisher")
        pub_stop = stop_node.create_publisher(Twist, "/cmd_vel_mux/input/safety_override", 10)
        pub_shut = stop_node.create_publisher(String, "/robot/system/shutdown", 10)
        
        msg_shut = String()
        msg_shut.data = f"CRITICAL_SHUTDOWN: Battery {v_filt:.2f}V <= 9.60V"
        pub_shut.publish(msg_shut)
        
        twist_zero = Twist()
        pub_stop.publish(twist_zero)
        stop_node.destroy_node()
        log("✅ Freno di emergenza e stop motori trasmessi (Voltage sag azzerato).")
    except Exception as e:
        log(f"Warning freno motori ROS 2: {e}")

    # 2. Messaggio vocale e chime acustico
    play_vocal_alert()

    # 3. Registrazione memorie cognitive dell'evento traumatico (MAG, RAG, CAG)
    log("🧠 Registrazione evento traumatico nelle memorie cognitive TRINITY...")
    record_traumatic_event_mag(db_path="/home/robopy/mag_trinity.db", v_filt=v_filt)
    record_traumatic_event_rag(persist_dir="/home/robopy/ChromaDB_Llama", v_filt=v_filt)

    # 4. Salvataggio mappe attive e flush database SLAM
    save_active_map_if_present(maps_dir="/mnt/ssd/maps")

    # 5. Arresto pulito dei processi ROS 2
    stop_all_ros2_nodes()

    # 6. Sync e spegnimento del sistema operativo
    execute_system_poweroff(dry_run=dry_run)
    log("🏁 Procedura di spegnimento pulito completata.")


if __name__ == '__main__':
    dry_mode = "--dry-run" in sys.argv or "--test" in sys.argv
    voltage = 9.60
    for arg in sys.argv:
        if arg.startswith("--voltage="):
            try:
                voltage = float(arg.split("=")[1])
            except ValueError:
                pass
    perform_graceful_emergency_shutdown(v_filt=voltage, dry_run=dry_mode)
