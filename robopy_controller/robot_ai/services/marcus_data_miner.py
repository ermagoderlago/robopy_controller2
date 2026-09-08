#!/usr/bin/env python3
"""
Marcus Data Miner - Autonomous Telemetry Collector & Bottleneck Analyzer
=======================================================================
Modulo per la raccolta passiva di telemetria operativa, diagnostica e prestazioni
per alimentare il ciclo di auto-miglioramento autonomo (Project Autopoiesis).

Vincoli e Governance:
1. SPEC-07 & FM-SYS-004: Scrittura su SSD NVMe in formato SQLite WAL compresso.
2. SPEC-07 & FM-SYS-008: Zero overhead in RAM; buffer circolare limitato (max 120 campioni)
   e batch flush a intervalli di 10 minuti (60 campioni).
3. IDLE GATING MANDATORIO:
   "A navigazione spenta e robot fermo, nessuna raccolta dati."
   Il campionamento a 0.1 Hz si attiva SOLO durante la navigazione Nav2 o con robot in movimento.
4. SPEC-05: Nessuna dipendenza esterna da Home Assistant per notifiche e analisi.
"""

import os
import sys
import time
import math
import json
import sqlite3
import logging
from collections import deque
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("robot_ai.marcus_data_miner")

# Rilevamento facoltativo ROS 2 per funzionamento ibrido (Node o Service Standalone)
_ROS2_AVAILABLE = False
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from nav_msgs.msg import Path as NavPath, Odometry
    from geometry_msgs.msg import Twist
    from std_msgs.msg import String
    from action_msgs.msg import GoalStatusArray
    from sensor_msgs.msg import Imu
    _ROS2_AVAILABLE = True
except ImportError:
    pass


def resolve_default_db_path() -> Path:
    """Risolve il percorso ottimale per il database di telemetria su SSD."""
    ssd_path = Path("/mnt/ssd/robopy_controller_host/data/telemetry_history.db")
    if ssd_path.parent.exists():
        return ssd_path
    
    # Fallback su directory utente locale o repo
    workspace_data = Path(__file__).resolve().parents[3] / "data"
    if workspace_data.exists():
        return workspace_data / "telemetry_history.db"
    
    home_dir = Path.home() / ".marcus" / "data"
    home_dir.mkdir(parents=True, exist_ok=True)
    return home_dir / "telemetry_history.db"


class MarcusDataMiner:
    """
    Motore di estrazione telemetrica con persistenza transazionale su SQLite.
    Fornisce sia le funzionalità interne di monitoraggio sia le API di interrogazione
    per il Nightly Dream Service e il Curiosity Evolution Engine.
    """

    def __init__(self, db_path: Optional[Path] = None, buffer_limit: int = 120):
        self.db_path = Path(db_path) if db_path else resolve_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.buffer_limit = buffer_limit
        self.sample_buffer = deque(maxlen=self.buffer_limit)
        
        # Stato operativo (Idle Gating)
        self.is_navigating = False
        self.last_motion_time = 0.0
        self.last_flush_time = time.time()
        self.latest_linear_vel = 0.0
        self.latest_angular_vel = 0.0
        self.latest_cte = 0.0
        self.latest_jitter = 0.0
        self.latest_ram_mb = 0.0
        self.latest_cpu_pct = 0.0
        self.stop_and_go_count = 0
        self.recovery_event_count = 0
        self.watchdog_warning_count = 0
        self.llm_latency_ms = 0.0
        self.was_moving = False
        # Stato operativo IMU e Collisioni
        self.latest_max_linear_accel = 0.0
        self.collision_event_count = 0
        self.collision_threshold_g = 1.5  # Soglia in G (circa 14.7 m/s^2) oltre cui si considera urto
        self.last_accel_x = 0.0
        self.last_accel_y = 0.0

        # Buffer di jitter angolare (ultimi 20 campioni)
        self.recent_angular_velocities = deque(maxlen=20)
        self.latest_path_poses = None

        self._init_sqlite_schema()

    def _init_sqlite_schema(self):
        """Inizializza le tabelle SQLite in modalità WAL (SPEC-05 / SPEC-07)."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            
            # Tabella campioni puntuali
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    session_date TEXT NOT NULL,
                    robot_state TEXT NOT NULL,
                    linear_vel REAL,
                    angular_vel REAL,
                    cross_track_error REAL,
                    angular_jitter REAL,
                    max_linear_accel REAL,
                    collision_count INTEGER,
                    ram_used_mb REAL,
                    cpu_percent REAL,
                    watchdog_ok INTEGER,
                    recovery_count INTEGER,
                    llm_latency_ms REAL,
                    event_tag TEXT,
                    extra_json TEXT
                );
            """)
            
            # Upgrade per retrocompatibilità (aggiunta colonne se il DB esiste già)
            try:
                cursor.execute("ALTER TABLE telemetry_samples ADD COLUMN max_linear_accel REAL DEFAULT 0.0;")
                cursor.execute("ALTER TABLE telemetry_samples ADD COLUMN collision_count INTEGER DEFAULT 0;")
            except sqlite3.OperationalError:
                pass # Le colonne esistono già

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_date ON telemetry_samples(session_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry_samples(timestamp);")

            # Tabella aggregata giornaliera per l'Orchestratore (Nightly Dream)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_aggregates (
                    date TEXT PRIMARY KEY,
                    total_samples INTEGER,
                    active_nav_minutes REAL,
                    total_moving_minutes REAL,
                    avg_cross_track_error REAL,
                    max_cross_track_error REAL,
                    avg_angular_jitter REAL,
                    max_angular_jitter REAL,
                    max_linear_accel REAL,
                    total_collision_events INTEGER,
                    avg_ram_mb REAL,
                    max_ram_mb REAL,
                    recovery_events_count INTEGER,
                    watchdog_warnings_count INTEGER,
                    top_anomaly_tag TEXT,
                    updated_at REAL
                );
            """)
            
            try:
                cursor.execute("ALTER TABLE daily_aggregates ADD COLUMN max_linear_accel REAL DEFAULT 0.0;")
                cursor.execute("ALTER TABLE daily_aggregates ADD COLUMN total_collision_events INTEGER DEFAULT 0;")
            except sqlite3.OperationalError:
                pass

            conn.commit()
            conn.close()
            logger.info(f"Database telemetria inizializzato in {self.db_path} (WAL mode).")
        except Exception as e:
            logger.error(f"Errore inizializzazione database SQLite: {e}")

    # -----------------------------------------------------------------------
    # Logica di Idle Gating (Vincolo Utente: a robot fermo, nessuna raccolta)
    # -----------------------------------------------------------------------
    def update_motion_state(self, linear_v: float, angular_v: float):
        """Aggiorna le velocità istantanee e rileva moto attivo."""
        self.latest_linear_vel = linear_v
        self.latest_angular_vel = angular_v
        now = time.time()
        self.recent_angular_velocities.append(angular_v)

        # Consideriamo in moto se velocità lineare > 0.01 m/s o angolare > 0.02 rad/s
        is_moving_now = (abs(linear_v) > 0.01 or abs(angular_v) > 0.02)
        if is_moving_now:
            self.last_motion_time = now

        # Calcolo stop-and-go
        if self.was_moving and abs(linear_v) < 0.005:
            self.stop_and_go_count += 1
            self.was_moving = False
        elif abs(linear_v) > 0.03:
            self.was_moving = True

    def set_navigation_state(self, is_navigating: bool):
        """Imposta se Nav2 ha una missione attiva."""
        self.is_navigating = is_navigating

    def update_imu_state(self, accel_x: float, accel_y: float, accel_z: float):
        """
        Registra l'accelerazione lineare per rilevare impatti/urti.
        Calcola la magnitudine sul piano XY (o XYZ, ma solitamente l'urto è su XY per robot a terra).
        """
        # Calcoliamo la magnitudine 2D dell'accelerazione (assumendo Z è gravità o meno rilevante per l'urto frontale)
        # Convertiamo l'accelerazione in G (assumendo che i dati siano in m/s^2)
        accel_mag_g = math.hypot(accel_x, accel_y) / 9.81
        
        # Aggiorniamo il massimo rilevato in questo tick
        if accel_mag_g > self.latest_max_linear_accel:
            self.latest_max_linear_accel = accel_mag_g

        # Rilevamento impatto: se supera la soglia (e.g. 1.5G sul piano)
        if accel_mag_g > self.collision_threshold_g:
            self.collision_event_count += 1
            logger.warning(f"[DataMiner] Rilevato potenziale urto! Accelerazione: {accel_mag_g:.2f} G")

    def is_idle(self) -> bool:
        """
        Determina se il robot è in stato IDLE:
        - Navigazione Nav2 inattiva
        - Nessun movimento negli ultimi 5.0 secondi
        """
        if self.is_navigating:
            return False
        now = time.time()
        if (now - self.last_motion_time) < 5.0:
            return False
        return True

    # -----------------------------------------------------------------------
    # Calcolo Metriche Real-Time
    # -----------------------------------------------------------------------
    def calculate_angular_jitter(self) -> float:
        """Calcola la deviazione standard della velocità angolare recente."""
        if len(self.recent_angular_velocities) < 4:
            return 0.0
        vals = list(self.recent_angular_velocities)
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        return float(math.sqrt(var))

    def update_cross_track_error(self, robot_x: float, robot_y: float, path_poses: Optional[List[Any]] = None):
        """Calcola la distanza perpendicolare (CTE) dal segmento del percorso globale."""
        if path_poses:
            self.latest_path_poses = path_poses

        if not self.latest_path_poses or len(self.latest_path_poses) < 2:
            self.latest_cte = 0.0
            return

        min_dist = float("inf")
        try:
            for i in range(len(self.latest_path_poses) - 1):
                p1 = self.latest_path_poses[i]
                p2 = self.latest_path_poses[i + 1]
                # Gestione pose ROS 2 o tuple (x, y)
                x1 = p1.pose.position.x if hasattr(p1, "pose") else p1[0]
                y1 = p1.pose.position.y if hasattr(p1, "pose") else p1[1]
                x2 = p2.pose.position.x if hasattr(p2, "pose") else p2[0]
                y2 = p2.pose.position.y if hasattr(p2, "pose") else p2[1]

                dx = x2 - x1
                dy = y2 - y1
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-6:
                    dist = math.hypot(robot_x - x1, robot_y - y1)
                else:
                    t = max(0.0, min(1.0, ((robot_x - x1) * dx + (robot_y - y1) * dy) / seg_len_sq))
                    proj_x = x1 + t * dx
                    proj_y = y1 + t * dy
                    dist = math.hypot(robot_x - proj_x, robot_y - proj_y)

                if dist < min_dist:
                    min_dist = dist
            self.latest_cte = float(min_dist) if min_dist != float("inf") else 0.0
        except Exception:
            self.latest_cte = 0.0

    def _read_system_resources(self) -> Tuple[float, float]:
        """Legge memoria RAM usata (MB) e stima CPU per verificare i limiti di SPEC-07."""
        ram_mb = 0.0
        cpu_pct = 0.0
        try:
            # Metodo linux standard veloce senza overhead psutil
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            meminfo[parts[0].strip()] = parts[1].strip()
                    total_kb = int(meminfo.get("MemTotal", "0 kB").split()[0])
                    avail_kb = int(meminfo.get("MemAvailable", "0 kB").split()[0])
                    ram_mb = round((total_kb - avail_kb) / 1024.0, 1)
        except Exception:
            pass
        return ram_mb, cpu_pct

    # -----------------------------------------------------------------------
    # Ciclo di Campionamento a 0.1 Hz (Ogni 10 secondi)
    # -----------------------------------------------------------------------
    def sample_tick(self, event_tag: str = "NORMAL", extra: Optional[Dict[str, Any]] = None) -> bool:
        """
        Esegue un tick di campionamento.
        Ritorna True se il dato è stato registrato, False se scartato dall'Idle Gating.
        """
        # REGOLA VINCOLANTE: se a navigazione spenta e robot fermo, NESSUNA RACCOLTA DATI
        if self.is_idle():
            logger.debug("[DataMiner] Idle gating attivo: navigazione spenta e robot fermo. Campionamento saltato.")
            return False

        now = time.time()
        session_date = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        state_str = "NAVIGATING" if self.is_navigating else "MANUAL_MOVING"
        
        self.latest_jitter = self.calculate_angular_jitter()
        ram_mb, cpu_pct = self._read_system_resources()
        if ram_mb > 0:
            self.latest_ram_mb = ram_mb

        # Tag anomalie automatici
        detected_tag = event_tag
        if self.latest_cte > 0.20:
            detected_tag = "HIGH_CTE"
        elif self.latest_jitter > 0.35:
            detected_tag = "HIGH_JITTER"
        elif self.collision_event_count > 0:
            detected_tag = "COLLISION_EVENT"
        elif self.recovery_event_count > 0:
            detected_tag = "RECOVERY_EVENT"

        sample = (
            now,
            session_date,
            state_str,
            round(self.latest_linear_vel, 4),
            round(self.latest_angular_vel, 4),
            round(self.latest_cte, 4),
            round(self.latest_jitter, 4),
            round(self.latest_max_linear_accel, 4),
            self.collision_event_count,
            round(self.latest_ram_mb, 1),
            round(self.latest_cpu_pct, 1),
            1 if self.watchdog_warning_count == 0 else 0,
            self.recovery_event_count,
            round(self.llm_latency_ms, 1),
            detected_tag,
            json.dumps(extra or {})
        )

        self.sample_buffer.append(sample)

        # Reset accumulatori parziali per il prossimo tick
        self.latest_max_linear_accel = 0.0
        self.collision_event_count = 0

        # Se il buffer raggiunge 60 campioni (10 min di moto continuo) o sono passati 600s, effettua il flush
        if len(self.sample_buffer) >= 60 or (now - self.last_flush_time) >= 600.0:
            self.flush_to_sqlite()

        return True

    # -----------------------------------------------------------------------
    # Flush Transazionale Batch su SQLite
    # -----------------------------------------------------------------------
    def flush_to_sqlite(self) -> int:
        """Svuota il buffer in-memory e persiste i campioni in un'unica transazione SQLite."""
        if not self.sample_buffer:
            return 0

        samples_to_write = list(self.sample_buffer)
        count = len(samples_to_write)

        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            
            cursor.executemany("""
                INSERT INTO telemetry_samples (
                    timestamp, session_date, robot_state, linear_vel, angular_vel,
                    cross_track_error, angular_jitter, max_linear_accel, collision_count,
                    ram_used_mb, cpu_percent, watchdog_ok, recovery_count, 
                    llm_latency_ms, event_tag, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, samples_to_write)
            
            conn.commit()
            conn.close()

            # Rimozione dei campioni scritti
            for _ in range(count):
                if self.sample_buffer:
                    self.sample_buffer.popleft()

            self.last_flush_time = time.time()
            self._update_daily_aggregate()
            logger.info(f"[DataMiner] Flush completato con successo: {count} campioni scritti su SSD.")
            return count
        except Exception as e:
            logger.error(f"[DataMiner] Errore durante il flush transazionale: {e}")
            return 0

    def _update_daily_aggregate(self):
        """Ricalcola e aggiorna il riepilogo giornaliero per l'Orchestratore."""
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    COUNT(*),
                    AVG(cross_track_error),
                    MAX(cross_track_error),
                    AVG(angular_jitter),
                    MAX(angular_jitter),
                    MAX(max_linear_accel),
                    SUM(collision_count),
                    AVG(ram_used_mb),
                    MAX(ram_used_mb),
                    MAX(recovery_count)
                FROM telemetry_samples
                WHERE session_date = ?;
            """, (today,))
            row = cursor.fetchone()
            
            if row and row[0] > 0:
                count, avg_cte, max_cte, avg_jit, max_jit, max_accel, sum_col, avg_ram, max_ram, max_rec = row
                active_minutes = round((count * 10.0) / 60.0, 1)

                # Identifica il top anomaly tag
                cursor.execute("""
                    SELECT event_tag, COUNT(*) as c
                    FROM telemetry_samples
                    WHERE session_date = ? AND event_tag != 'NORMAL'
                    GROUP BY event_tag ORDER BY c DESC LIMIT 1;
                """, (today,))
                tag_row = cursor.fetchone()
                top_tag = tag_row[0] if tag_row else "NORMAL"

                cursor.execute("""
                    INSERT INTO daily_aggregates (
                        date, total_samples, active_nav_minutes, total_moving_minutes,
                        avg_cross_track_error, max_cross_track_error,
                        avg_angular_jitter, max_angular_jitter,
                        max_linear_accel, total_collision_events,
                        avg_ram_mb, max_ram_mb, recovery_events_count,
                        watchdog_warnings_count, top_anomaly_tag, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        total_samples = excluded.total_samples,
                        active_nav_minutes = excluded.active_nav_minutes,
                        total_moving_minutes = excluded.total_moving_minutes,
                        avg_cross_track_error = excluded.avg_cross_track_error,
                        max_cross_track_error = excluded.max_cross_track_error,
                        avg_angular_jitter = excluded.avg_angular_jitter,
                        max_angular_jitter = excluded.max_angular_jitter,
                        max_linear_accel = excluded.max_linear_accel,
                        total_collision_events = excluded.total_collision_events,
                        avg_ram_mb = excluded.avg_ram_mb,
                        max_ram_mb = excluded.max_ram_mb,
                        recovery_events_count = excluded.recovery_events_count,
                        top_anomaly_tag = excluded.top_anomaly_tag,
                        updated_at = excluded.updated_at;
                """, (
                    today, count, active_minutes, active_minutes,
                    round(avg_cte or 0.0, 4), round(max_cte or 0.0, 4),
                    round(avg_jit or 0.0, 4), round(max_jit or 0.0, 4),
                    round(max_accel or 0.0, 4), sum_col or 0,
                    round(avg_ram or 0.0, 1), round(max_ram or 0.0, 1),
                    max_rec or 0, self.watchdog_warning_count,
                    top_tag, time.time()
                ))
                conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[DataMiner] Errore aggiornamento aggregati giornalieri: {e}")

    # -----------------------------------------------------------------------
    # API per Nightly Dream Service & Curiosity Evolution Engine
    # -----------------------------------------------------------------------
    def get_daily_summary(self, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Restituisce le statistiche aggregate di una specifica giornata (default: oggi)."""
        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM daily_aggregates WHERE date = ?;", (target_date,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return dict(row)
        except Exception as e:
            logger.error(f"Errore lettura summary per data {target_date}: {e}")
        return {
            "date": target_date,
            "total_samples": 0,
            "avg_cross_track_error": 0.0,
            "avg_angular_jitter": 0.0,
            "top_anomaly_tag": "NO_DATA"
        }

    def get_unmitigated_bottlenecks(self, days: int = 3) -> List[Dict[str, Any]]:
        """
        Individua pattern critici degli ultimi N giorni per orientare la scelta
        autonoma del 'Tema del Giorno' da parte dell'Orchestratore (Gemini 3.1 Pro).
        """
        bottlenecks = []
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM daily_aggregates 
                ORDER BY date DESC LIMIT ?;
            """, (days,))
            rows = cursor.fetchall()
            conn.close()

            for r in rows:
                date = r["date"]
                if (r["total_collision_events"] or 0) > 0:
                    bottlenecks.append({
                        "subsystem": "Sicurezza/Cinematica",
                        "severity_score": 10,
                        "description": f"Rilevati {r['total_collision_events']} urti (picchi acc > 1.5G) in data {date}.",
                        "recommended_focus": "Analisi logiche ostacoli e tuning soglia accelerometro"
                    })
                if (r["max_cross_track_error"] or 0) > 0.15:
                    bottlenecks.append({
                        "subsystem": "Nav2",
                        "severity_score": 8,
                        "description": f"Errore traiettoria (CTE) elevato ({r['max_cross_track_error']:.3f}m) in data {date}.",
                        "recommended_focus": "Tuning parametri MPPI (costo deviazione e lookahead)"
                    })
                if (r["max_angular_jitter"] or 0) > 0.30:
                    bottlenecks.append({
                        "subsystem": "Attuazione",
                        "severity_score": 7,
                        "description": f"Jitter angolare significativo ({r['max_angular_jitter']:.3f} rad/s) in data {date}.",
                        "recommended_focus": "Ottimizzazione slew rate e soglia attrito statico in waveshare_motor_driver.py"
                    })
                if (r["recovery_events_count"] or 0) > 0:
                    bottlenecks.append({
                        "subsystem": "Nav2",
                        "severity_score": 9,
                        "description": f"Registrati {r['recovery_events_count']} comportamenti di recovery Nav2 in data {date}.",
                        "recommended_focus": "Revisione Survival BT e tolleranze di clearing costmap"
                    })
                if (r["max_ram_mb"] or 0) > 3100.0:
                    bottlenecks.append({
                        "subsystem": "System/DDS",
                        "severity_score": 8,
                        "description": f"Picco di RAM critico ({r['max_ram_mb']:.1f} MB) vicino alla soglia 3200MB di SPEC-07.",
                        "recommended_focus": "Audit memory leak e potatura cache vettoriali ChromaDB/RAG"
                    })
        except Exception as e:
            logger.error(f"Errore analisi colli di bottiglia: {e}")
        return bottlenecks


# ---------------------------------------------------------------------------
# Nodo ROS 2 MarcusDataMinerNode (Standalone o Integrato)
# ---------------------------------------------------------------------------
if _ROS2_AVAILABLE:
    class MarcusDataMinerNode(Node):
        """Nodo ROS 2 per la telemetria passiva con idle gating."""

        def __init__(self):
            super().__init__('marcus_data_miner_node')
            self.declare_parameter('db_path', str(resolve_default_db_path()))
            self.declare_parameter('sample_interval_sec', 10.0)  # 0.1 Hz

            db_str = self.get_parameter('db_path').get_parameter_value().string_value
            self.miner = MarcusDataMiner(db_path=Path(db_str))

            # QoS per sensori e telemetria
            sensor_qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                history=HistoryPolicy.KEEP_LAST,
                depth=5
            )

            # Sottoscrizioni
            self.create_subscription(Twist, '/cmd_vel', self._cmd_vel_cb, 10)
            self.create_subscription(Odometry, '/odometry/filtered', self._odom_cb, sensor_qos)
            self.create_subscription(NavPath, '/plan', self._path_cb, 10)
            self.create_subscription(GoalStatusArray, '/navigate_to_pose/_action/status', self._nav_status_cb, 10)
            self.create_subscription(String, '/diagnostics_warnings', self._diag_cb, 10)
            self.create_subscription(Imu, '/imu/data', self._imu_cb, sensor_qos)

            # Timer di campionamento (0.1 Hz)
            interval = self.get_parameter('sample_interval_sec').get_parameter_value().double_value
            self.create_timer(interval, self._timer_cb)

            self.get_logger().info(f"MarcusDataMinerNode avviato. Database: {db_str} | Rate: {1.0/interval:.2f}Hz (Idle Gated)")

        def _cmd_vel_cb(self, msg: Twist):
            self.miner.update_motion_state(msg.linear.x, msg.angular.z)

        def _odom_cb(self, msg: Odometry):
            self.miner.update_motion_state(msg.twist.twist.linear.x, msg.twist.twist.angular.z)
            self.miner.update_cross_track_error(msg.pose.pose.position.x, msg.pose.pose.position.y)

        def _path_cb(self, msg: NavPath):
            if msg.poses:
                self.miner.latest_path_poses = msg.poses

        def _nav_status_cb(self, msg: GoalStatusArray):
            # Status 1: ACCEPTED, 2: EXECUTING
            is_active = any(s.status in [1, 2] for s in msg.status_list)
            self.miner.set_navigation_state(is_active)

        def _diag_cb(self, msg: String):
            if "watchdog" in msg.data.lower():
                self.miner.watchdog_warning_count += 1
            if "recovery" in msg.data.lower():
                self.miner.recovery_event_count += 1

        def _imu_cb(self, msg: Imu):
            self.miner.update_imu_state(
                msg.linear_acceleration.x,
                msg.linear_acceleration.y,
                msg.linear_acceleration.z
            )

        def _timer_cb(self):
            # Tick con idle gating integrato
            recorded = self.miner.sample_tick()
            if recorded:
                self.get_logger().debug(f"[DataMiner] Campione registrato. Buffer: {len(self.miner.sample_buffer)}")


def main(args=None):
    if not _ROS2_AVAILABLE:
        print("ROS 2 (rclpy) non disponibile nell'ambiente corrente. Esecuzione del test standalone...")
        miner = MarcusDataMiner()
        print(f"Test completato. Summary odierno: {miner.get_daily_summary()}")
        return

    rclpy.init(args=args)
    node = MarcusDataMinerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.miner.flush_to_sqlite()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
