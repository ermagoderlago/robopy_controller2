#!/usr/bin/env python3
"""
battery_manager_node.py - Marcus AI Battery Management System (BMS) Node
========================================================================
Gestore avanzato dello stato di carica (SoC), rilevamento alimentazione da rete / cuccia (12.80V),
filtraggio rumore e voltage sag (Moving Average N=20 a 5Hz), timer di persistenza (3s) e
pubblicazione telemetria per Foxglove Studio, Nav2 ed arresto controllato di sistema (OS Graceful Shutdown).

Architettura Hardware Power Path OR-ing (Diodi Ideali):
- Funzionamento da rete / in carica (V >= 12.70V): Bus a 12.80V fisso da alimentatore 24V step-down.
  La chimica della batteria è isolata; SoC convenzionale e inibizione trigger docking/shutdown.
- Funzionamento a batteria (V < 12.65V): Pacco Li-ion 3S (Range 9.0V - 12.6V, Nominale 11.1V).
  - 100% Carica Piena: 12.60V (4.20V/cella)
  - Nominale: 11.10V (3.70V/cella)
  - Soglia ECO / Riduzione Dinamica (<= 20%): 10.20V (3.40V/cella) -> Limitatore velocità/accel al 50%
  - Soglia Rientro Base / Cuccia (<= 12%): 9.90V (3.30V/cella) -> Trigger docking persistente (>3s)
  - Soglia Critica / Shutdown OS (<= 0%): 9.00V (3.00V/cella) -> Stop motori immediato e poweroff OS (>3s)
"""

import os
import sys
import time
import math
import subprocess
import threading
from collections import deque
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import BatteryState
from std_msgs.msg import Float32, String, Bool
from geometry_msgs.msg import Twist

# Supporto opzionale per Nav2 SpeedLimit
try:
    from nav2_msgs.msg import SpeedLimit
    HAS_NAV2_MSGS = True
except ImportError:
    HAS_NAV2_MSGS = False


class BatteryManagerNode(Node):
    def __init__(self):
        super().__init__('battery_manager_node')

        # --- Dichiarazione Parametri ---
        self.declare_parameter('raw_voltage_topic', '/battery/raw')
        self.declare_parameter('battery_state_topic', '/battery_state')
        self.declare_parameter('foxglove_pct_topic', '/foxglove/battery_pct')
        self.declare_parameter('foxglove_status_topic', '/foxglove/power_status')
        self.declare_parameter('docking_trigger_topic', '/robot/docking/trigger')
        self.declare_parameter('undock_trigger_topic', '/robot/docking/undock_trigger')
        self.declare_parameter('shutdown_topic', '/robot/system/shutdown')
        self.declare_parameter('speed_limit_topic', '/speed_limit')
        self.declare_parameter('legacy_voltage_topic', '/motor/battery_voltage')

        self.declare_parameter('charging_threshold_voltage', 12.70)
        self.declare_parameter('charging_bus_voltage', 12.80)
        self.declare_parameter('full_voltage', 12.60)
        self.declare_parameter('nominal_voltage', 11.10)
        self.declare_parameter('eco_voltage', 10.20)
        self.declare_parameter('docking_voltage', 9.90)
        self.declare_parameter('shutdown_voltage', 9.00)

        # Parametri Batteria Panasonic NCR18650B (3S2P: 6 celle, 6800 mAh, 11.1V nominale)
        self.declare_parameter('total_capacity_ah', 6.80)            # 2x 3400 mAh = 6.8 Ah (75.5 Wh)
        self.declare_parameter('battery_chemistry', 'NCR18650B_3S2P')
        self.declare_parameter('internal_resistance_ohm', 0.085)     # ~85 mOhm pack 3S2P comprensivo di BMS, connettori e cavi
        self.declare_parameter('base_quiescent_current_a', 1.20)     # ~1.20A @ 12V assorbiti da Step-Down 2 (Pi5, Hailo, LiDAR, OAK-D)
        self.declare_parameter('use_ocv_table', True)                # Abilita Lookup Table OCV specifica per Panasonic NCR18650B

        # Parametri Caricatore CC-CV e Scucciamento Automatico
        self.declare_parameter('charger_current_a', 1.50)           # 1.50A CC-CV step-down charger
        self.declare_parameter('full_charge_soc_thresh', 0.98)       # 98% -> considerato carico completo
        self.declare_parameter('post_dock_verify_sec', 5.0)          # Durata finestra verifica post-undock (secondi)
        self.declare_parameter('post_dock_min_voltage', 12.45)       # Tensione minima OCV per carica verificata (~95% SoC)

        self.declare_parameter('filter_window_size', 20)
        self.declare_parameter('sample_rate_hz', 5.0)
        self.declare_parameter('persistence_sec', 3.0)
        self.declare_parameter('speed_limit_eco_pct', 50.0)
        self.declare_parameter('auto_poweroff', False)
        self.declare_parameter('esp32_adc_scale_factor', 2880.95)

        # --- Lettura Parametri ---
        self.raw_voltage_topic = self.get_parameter('raw_voltage_topic').value
        self.battery_state_topic = self.get_parameter('battery_state_topic').value
        self.foxglove_pct_topic = self.get_parameter('foxglove_pct_topic').value
        self.foxglove_status_topic = self.get_parameter('foxglove_status_topic').value
        self.docking_trigger_topic = self.get_parameter('docking_trigger_topic').value
        self.undock_trigger_topic = self.get_parameter('undock_trigger_topic').value
        self.shutdown_topic = self.get_parameter('shutdown_topic').value
        self.speed_limit_topic = self.get_parameter('speed_limit_topic').value
        self.legacy_voltage_topic = self.get_parameter('legacy_voltage_topic').value

        self.v_charging_thresh = float(self.get_parameter('charging_threshold_voltage').value)
        self.v_charging_bus = float(self.get_parameter('charging_bus_voltage').value)
        self.v_full = float(self.get_parameter('full_voltage').value)
        self.v_nominal = float(self.get_parameter('nominal_voltage').value)
        self.v_eco = float(self.get_parameter('eco_voltage').value)
        self.v_docking = float(self.get_parameter('docking_voltage').value)
        self.v_shutdown = float(self.get_parameter('shutdown_voltage').value)

        self.total_capacity_ah = float(self.get_parameter('total_capacity_ah').value)
        self.battery_chemistry = str(self.get_parameter('battery_chemistry').value)
        self.internal_resistance_ohm = float(self.get_parameter('internal_resistance_ohm').value)
        self.base_quiescent_current_a = float(self.get_parameter('base_quiescent_current_a').value)
        val_ocv = self.get_parameter('use_ocv_table').value
        self.use_ocv_table = bool(val_ocv) if val_ocv is not None else True

        val_charger = self.get_parameter('charger_current_a').value
        self.charger_current_a = float(val_charger) if (val_charger is not None and float(val_charger) > 0.0) else 1.50
        val_thresh = self.get_parameter('full_charge_soc_thresh').value
        self.full_charge_soc_thresh = float(val_thresh) if (val_thresh is not None and float(val_thresh) > 0.5) else 0.98
        val_verify = self.get_parameter('post_dock_verify_sec').value
        self.post_dock_verify_sec = float(val_verify) if (val_verify is not None and float(val_verify) > 0.0) else 5.0
        val_min_v = self.get_parameter('post_dock_min_voltage').value
        self.post_dock_min_voltage = float(val_min_v) if (val_min_v is not None and float(val_min_v) > 10.0) else 12.45


        # Tabella OCV calibrata da datasheet ufficiale Panasonic NCR18650B per pacco 3S (12.60V max, 9.00V min)
        # Coppie: (Tensione_V, SoC_ratio) ordinate in modo decrescente
        self.ncr18650b_ocv_table = [
            (12.60, 1.00),  # 4.20V/cella - 100% Carica completa
            (12.45, 0.95),  # 4.15V/cella - 95%
            (12.24, 0.90),  # 4.08V/cella - 90%
            (11.97, 0.80),  # 3.99V/cella - 80%
            (11.70, 0.70),  # 3.90V/cella - 70%
            (11.46, 0.60),  # 3.82V/cella - 60%
            (11.25, 0.50),  # 3.75V/cella - 50%
            (11.04, 0.40),  # 3.68V/cella - 40% (Altezza tipica del plateau Li-ion)
            (10.86, 0.30),  # 3.62V/cella - 30%
            (10.65, 0.20),  # 3.55V/cella - 20%
            (10.35, 0.15),  # 3.45V/cella - 15% (Prossimità soglia ECO 10.20V)
            (10.05, 0.10),  # 3.35V/cella - 10% (Prossimità soglia Docking 9.90V)
            (9.60,  0.05),  # 3.20V/cella - 5%  (Ginocchio profondo di scarica)
            (9.00,  0.00),  # 3.00V/cella - 0%  (Soglia critica spegnimento OS)
        ]

        self.filter_window_size = int(self.get_parameter('filter_window_size').value)
        self.sample_rate_hz = float(self.get_parameter('sample_rate_hz').value)
        self.persistence_sec = float(self.get_parameter('persistence_sec').value)
        self.speed_limit_eco_pct = float(self.get_parameter('speed_limit_eco_pct').value)
        self.auto_poweroff = bool(self.get_parameter('auto_poweroff').value)
        self.esp32_adc_scale_factor = float(self.get_parameter('esp32_adc_scale_factor').value)

        # --- Strutture Dati & Filtro Anti-Sag ---
        self.lock = threading.Lock()
        self.voltage_buffer = deque(maxlen=self.filter_window_size)
        self.latest_raw_voltage = self.v_nominal
        self.filtered_voltage = self.v_nominal
        self.latest_current = 0.0


        # --- Stato Operativo e Timer Persistenza ---
        self.current_state_str = "BATTERIA OK"
        self.is_charging = False
        self.docking_start_time: Optional[float] = None
        self.shutdown_start_time: Optional[float] = None
        self.docking_triggered = False
        self.shutdown_triggered = False
        self.poweroff_executed = False

        # --- Modello Ricarica Stimata CC-CV e Scucciamento ---
        self.charge_start_time: Optional[float] = None
        self.last_charge_tick: Optional[float] = None
        self.estimated_charging_soc: float = 1.0
        self.last_discharging_soc: float = 0.50
        self.undock_triggered: bool = False

        # --- Verifica Post-Docking (appena staccato dalla carica) ---
        self.post_dock_verify_start: Optional[float] = None
        self.post_dock_verification_success: Optional[bool] = None

        # --- Profili QoS ---
        qos_reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        qos_sensor = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # --- Publishers ---
        self.pub_battery_state = self.create_publisher(BatteryState, self.battery_state_topic, qos_reliable)
        self.pub_foxglove_pct = self.create_publisher(Float32, self.foxglove_pct_topic, qos_reliable)
        self.pub_foxglove_status = self.create_publisher(String, self.foxglove_status_topic, qos_reliable)
        self.pub_docking_trigger = self.create_publisher(Bool, self.docking_trigger_topic, qos_reliable)
        self.pub_undock_trigger = self.create_publisher(Bool, self.undock_trigger_topic, qos_reliable)
        self.pub_system_shutdown = self.create_publisher(String, self.shutdown_topic, qos_reliable)
        self.pub_legacy_voltage = self.create_publisher(Float32, self.legacy_voltage_topic, qos_reliable)
        self.pub_emergency_stop = self.create_publisher(Twist, '/cmd_vel_mux/input/safety_override', qos_reliable)


        if HAS_NAV2_MSGS:
            self.pub_speed_limit = self.create_publisher(SpeedLimit, self.speed_limit_topic, qos_reliable)
        else:
            self.pub_speed_limit = self.create_publisher(Float32, self.speed_limit_topic, qos_reliable)

        # --- Subscribers ---
        self.create_subscription(BatteryState, self.raw_voltage_topic, self._raw_battery_state_cb, qos_sensor)
        self.create_subscription(Float32, '/battery/raw_voltage', self._raw_float_cb, qos_sensor)
        self.create_subscription(BatteryState, '/battery_state_raw', self._raw_battery_state_cb, qos_sensor)

        # --- Timer Ciclo di Controllo & Pubblicazione ---
        timer_period = 1.0 / max(self.sample_rate_hz, 1.0)
        self.timer = self.create_timer(timer_period, self._control_and_publish_loop)

        self.get_logger().info(
            f"⚡ [BatteryManager] Inizializzato con successo. "
            f"ChargingThresh={self.v_charging_thresh}V, Eco={self.v_eco}V, "
            f"Docking={self.v_docking}V, Shutdown={self.v_shutdown}V, "
            f"WindowSize={self.filter_window_size}, Persistence={self.persistence_sec}s"
        )

    def _raw_battery_state_cb(self, msg: BatteryState):
        """Callback per messaggi raw in formato sensor_msgs/BatteryState."""
        if not math.isnan(msg.voltage) and msg.voltage > 0.5:
            curr = float(msg.current) if not math.isnan(msg.current) else None
            self._insert_raw_sample(float(msg.voltage), curr)

    def _raw_float_cb(self, msg: Float32):
        """Callback per messaggi raw in formato std_msgs/Float32."""
        if not math.isnan(msg.data) and msg.data > 0.5:
            self._insert_raw_sample(float(msg.data))

    def _insert_raw_sample(self, v_val: float, current_val: Optional[float] = None):
        """Normalizza la tensione (V) ed inserisce nel buffer circolare FIFO."""
        # Se espressa in millivolti da INA219 (es. 11100 per 11.10V)
        # Fallback legacy per 36300 divider ESP32
        scale_factor = getattr(self, 'esp32_adc_scale_factor', 2880.95)
        if v_val > 30000.0:
            voltage = v_val / scale_factor
        elif v_val > 500.0:
            voltage = v_val / 1000.0
        elif v_val > 50.0:
            voltage = v_val / 100.0
        else:
            voltage = v_val

        with self.lock:
            self.latest_raw_voltage = voltage
            self.voltage_buffer.append(voltage)
            self.filtered_voltage = sum(self.voltage_buffer) / len(self.voltage_buffer)
            if current_val is not None and not math.isnan(current_val):
                self.latest_current = current_val

    def _interpolate_ocv(self, v_ocv: float) -> float:
        """Interpolazione lineare a tratti sulla tabella OCV Panasonic NCR18650B (3S)."""
        table = getattr(self, 'ncr18650b_ocv_table', [])
        if not table:
            return (v_ocv - self.v_shutdown) / max(0.01, self.v_full - self.v_shutdown)
        if v_ocv >= table[0][0]:
            return 1.0
        if v_ocv <= table[-1][0]:
            return 0.0
        for i in range(len(table) - 1):
            v_high, soc_high = table[i]
            v_low, soc_low = table[i + 1]
            if v_low <= v_ocv <= v_high:
                frac = (v_ocv - v_low) / max(1e-4, v_high - v_low)
                return max(0.0, min(1.0, soc_low + frac * (soc_high - soc_low)))
        return 0.0

    def _calculate_soc(self, v_filt: float, current_a: float = 0.0) -> float:
        """
        Calcola lo State of Charge (SoC) normalizzato tra 0.0 (0%) e 1.0 (100%).
        Compensa la caduta ohmica (IR sag) e interpola sulla curva OCV reale Panasonic NCR18650B.
        """
        r_int = getattr(self, 'internal_resistance_ohm', 0.085)
        v_ocv = v_filt + (max(0.0, current_a) * r_int)

        if getattr(self, 'use_ocv_table', True):
            return self._interpolate_ocv(v_ocv)
        else:
            if v_ocv >= self.v_full:
                return 1.0
            elif v_ocv <= self.v_shutdown:
                return 0.0
            else:
                return (v_ocv - self.v_shutdown) / (self.v_full - self.v_shutdown)

    def _control_and_publish_loop(self):
        """Ciclo deterministico a frequenza fissa (5Hz) per gestione stato e pubblicazione."""
        now = time.monotonic()

        with self.lock:
            v_filt = self.filtered_voltage

        # =========================================================================
        # 1. VALUTAZIONE STATO OPERATIVO E TRANSIZIONI CON PERSISTENZA
        # =========================================================================
        power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        soc_ratio = 1.0
        foxglove_pct = 100.0
        speed_limit_val = 100.0
        dock_trigger_val = False

        if v_filt >= self.v_charging_thresh:
            # --- STATO 1: IN CARICA / DA RETE (V >= 12.70V) ---
            # Il bus misura 12.80V fissi. La chimica è isolata dal diodo ideale 2.
            if not self.is_charging:
                self.is_charging = True
                self.charge_start_time = now
                self.last_charge_tick = now
                start_soc = getattr(self, 'last_discharging_soc', 0.10)
                self.estimated_charging_soc = max(0.05, min(0.99, start_soc))
                self.undock_triggered = False
                self.post_dock_verify_start = None
                self.get_logger().info(
                    f"🔌 [BatteryManager] Connesso alla ricarica (12.8V). "
                    f"SoC di partenza={self.estimated_charging_soc*100:.1f}%, Caricatore CC-CV={self.charger_current_a}A"
                )

            dt_charge = (now - self.last_charge_tick) if self.last_charge_tick is not None else 0.2
            dt_charge = max(0.01, min(2.0, dt_charge))
            self.last_charge_tick = now

            # Modello CC-CV:
            # In fase CC (SoC < 80%): corrente costante charger_current_a
            # In fase CV (SoC >= 80%): la corrente decresce asintoticamente
            if self.estimated_charging_soc < 0.80:
                eff_curr = self.charger_current_a
            else:
                headroom = max(0.0, (1.0 - self.estimated_charging_soc) / 0.20)
                eff_curr = max(0.12, self.charger_current_a * (headroom ** 0.65))

            delta_ah = eff_curr * (dt_charge / 3600.0)
            self.estimated_charging_soc = min(1.0, self.estimated_charging_soc + (delta_ah / max(0.1, self.total_capacity_ah)))
            soc_ratio = self.estimated_charging_soc
            foxglove_pct = round(soc_ratio * 100.0, 1)

            rem_ah = max(0.0, (1.0 - self.estimated_charging_soc) * self.total_capacity_ah)
            rem_min = (rem_ah / max(0.15, eff_curr)) * 60.0

            if self.estimated_charging_soc >= self.full_charge_soc_thresh:
                # --- CARICA COMPLETA ---
                power_supply_status = BatteryState.POWER_SUPPLY_STATUS_FULL
                self.current_state_str = "CARICA COMPLETA (100%)"
                if not self.undock_triggered:
                    self.undock_triggered = True
                    self.get_logger().info(
                        "🏠 [BatteryManager] Batteria carica al 100%! Invio trigger di SCUCCIAMENTO / UNDOCK."
                    )
            else:
                # --- IN FASE DI CARICA ---
                power_supply_status = BatteryState.POWER_SUPPLY_STATUS_CHARGING
                self.current_state_str = f"IN CARICA ({foxglove_pct:.0f}%) - Mancano ~{rem_min:.0f}m"
                self.undock_triggered = False

            speed_limit_val = 100.0
            # Inibizione totale di allarmi e trigger di docking in ingresso
            self.docking_start_time = None
            self.shutdown_start_time = None
            self.docking_triggered = False
            self.shutdown_triggered = False

        else:
            # --- FUNZIONAMENTO A BATTERIA (V < 12.65V) ---
            if self.is_charging:
                self.is_charging = False
                self.charge_start_time = None
                self.last_charge_tick = None
                # Avvia la finestra di verifica post-docking
                self.post_dock_verify_start = now
                self.post_dock_verification_success = None
                self.get_logger().info(
                    "🚪 [BatteryManager] Robot disconnesso dalla base/alimentatore. "
                    f"Avvio verifica reale tensione LiPo post-dock ({self.post_dock_verify_sec}s)..."
                )

            # Stima della corrente totale: motori (da INA219) + carico base Step-Down 2 (Pi5, Hailo, LiDAR, OAK-D)
            motor_curr = self.latest_current if hasattr(self, 'latest_current') and not math.isnan(self.latest_current) else 0.0
            est_total_current = max(0.0, motor_curr) + getattr(self, 'base_quiescent_current_a', 1.20)
            soc_ratio = self._calculate_soc(v_filt, est_total_current)
            self.last_discharging_soc = soc_ratio
            foxglove_pct = round(soc_ratio * 100.0, 1)

            # Gestione della verifica post-dock
            is_post_dock_verifying = False
            if self.post_dock_verify_start is not None:
                elapsed_verify = now - self.post_dock_verify_start
                if elapsed_verify < self.post_dock_verify_sec:
                    is_post_dock_verifying = True
                    self.current_state_str = f"VERIFICA CARICA ({v_filt:.2f}V)"
                else:
                    self.post_dock_verify_start = None
                    if v_filt >= self.post_dock_min_voltage:
                        self.post_dock_verification_success = True
                        self.get_logger().info(
                            f"✅ [BatteryManager] Verifica post-dock SUPERATA: Tensione reale={v_filt:.2f}V "
                            f"(>= {self.post_dock_min_voltage}V). Batteria pienamente carica!"
                        )
                    else:
                        self.post_dock_verification_success = False
                        self.get_logger().warn(
                            f"⚠️ [BatteryManager] Verifica post-dock: Tensione reale={v_filt:.2f}V "
                            f"(< {self.post_dock_min_voltage}V). Carica incompleta o interrotta precocemente."
                        )

            if is_post_dock_verifying:
                # Durante la finestra di verifica post-dock (5 secondi):
                # Inibisce allarmi di scarica, il robot è appena uscito dalla cuccia
                speed_limit_val = 100.0
                self.docking_start_time = None
                self.shutdown_start_time = None
                self.docking_triggered = False
                self.shutdown_triggered = False

            elif v_filt > self.v_eco:
                # --- STATO 2: BATTERIA OK (> 10.20V / > 20% SoC) ---
                if getattr(self, 'post_dock_verification_success', None) is True:
                    self.current_state_str = "CARICA VERIFICATA OK (100%)"
                else:
                    self.current_state_str = "BATTERIA OK"
                speed_limit_val = 100.0
                self.docking_start_time = None
                self.shutdown_start_time = None
                self.docking_triggered = False
                self.shutdown_triggered = False

            elif self.v_docking < v_filt <= self.v_eco:
                # --- STATO 3: ECO MODE (9.90V < V <= 10.20V / 12% - 20% SoC) ---
                self.current_state_str = "ECO MODE (<20%)"
                speed_limit_val = self.speed_limit_eco_pct
                self.docking_start_time = None
                self.shutdown_start_time = None
                self.docking_triggered = False
                self.shutdown_triggered = False


            elif self.v_shutdown < v_filt <= self.v_docking:
                # --- STATO 4: SOGLIA RIENTRO IN BASE / CUCCIA (9.00V < V <= 9.90V / <= 12% SoC) ---
                speed_limit_val = self.speed_limit_eco_pct
                self.shutdown_start_time = None

                if self.docking_start_time is None:
                    self.docking_start_time = now

                dur = now - self.docking_start_time
                if dur >= self.persistence_sec:
                    self.docking_triggered = True
                    dock_trigger_val = True
                    self.current_state_str = "ALLARME RIENTRO"
                    self.get_logger().warn(
                        f"🪫 [BatteryManager] Soglia Rientro in Base confermata ({v_filt:.2f}V per {dur:.1f}s)! Trigger docking attivo.",
                        throttle_duration_sec=2.0
                    )
                else:
                    self.current_state_str = "ECO MODE (<20%)"

            else:
                # --- STATO 5: SOGLIA CRITICA / SHUTDOWN OS (V <= 9.00V / <= 0% SoC) ---
                speed_limit_val = self.speed_limit_eco_pct
                if self.docking_start_time is not None and (now - self.docking_start_time >= self.persistence_sec):
                    dock_trigger_val = True

                if self.shutdown_start_time is None:
                    self.shutdown_start_time = now

                dur = now - self.shutdown_start_time
                if dur >= self.persistence_sec:
                    self.shutdown_triggered = True
                    self.current_state_str = "CRITICO SHUTDOWN"
                    self.get_logger().error(
                        f"🚨 [BatteryManager] SOTTOTENSIONE CRITICA ({v_filt:.2f}V per {dur:.1f}s)! Arresto motori e spegnimento OS imminente.",
                        throttle_duration_sec=1.0
                    )
                    self._trigger_emergency_shutdown(v_filt)
                else:
                    self.current_state_str = "ALLARME RIENTRO"

        # =========================================================================
        # 2. PUBBLICAZIONE TOPIC ROS 2
        # =========================================================================
        stamp = self.get_clock().now().to_msg()

        # Topic 1: /battery_state (sensor_msgs/BatteryState)
        bat_msg = BatteryState()
        bat_msg.header.stamp = stamp
        bat_msg.header.frame_id = 'base_link'
        bat_msg.voltage = float(v_filt)
        if hasattr(self, 'latest_current') and not math.isnan(self.latest_current):
            bat_msg.current = float(self.latest_current)
        bat_msg.percentage = float(soc_ratio)
        cap_ah = getattr(self, 'total_capacity_ah', 6.80)
        bat_msg.capacity = float(cap_ah)
        bat_msg.design_capacity = float(cap_ah)
        bat_msg.charge = float(soc_ratio * cap_ah)
        bat_msg.present = True
        bat_msg.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
        bat_msg.power_supply_status = power_supply_status

        self.pub_battery_state.publish(bat_msg)

        # Topic 2: /foxglove/battery_pct (std_msgs/Float32)
        pct_msg = Float32()
        pct_msg.data = float(foxglove_pct)
        self.pub_foxglove_pct.publish(pct_msg)

        # Topic 3: /foxglove/power_status (std_msgs/String)
        status_msg = String()
        status_msg.data = self.current_state_str
        self.pub_foxglove_status.publish(status_msg)

        # Topic 4: /robot/docking/trigger (std_msgs/Bool)
        dock_msg = Bool()
        dock_msg.data = bool(dock_trigger_val)
        self.pub_docking_trigger.publish(dock_msg)

        # Topic 4b: /robot/docking/undock_trigger (std_msgs/Bool)
        undock_msg = Bool()
        undock_msg.data = bool(self.undock_triggered)
        self.pub_undock_trigger.publish(undock_msg)


        # Topic 5: /motor/battery_voltage (std_msgs/Float32) per compatibilità supervisor
        leg_msg = Float32()
        leg_msg.data = float(v_filt)
        self.pub_legacy_voltage.publish(leg_msg)

        # Topic 6: /speed_limit (nav2_msgs/SpeedLimit o std_msgs/Float32)
        if HAS_NAV2_MSGS:
            sl_msg = SpeedLimit()
            sl_msg.header.stamp = stamp
            sl_msg.header.frame_id = 'base_link'
            sl_msg.percentage = True
            sl_msg.speed_limit = float(speed_limit_val)
            self.pub_speed_limit.publish(sl_msg)
        else:
            sl_fallback = Float32()
            sl_fallback.data = float(speed_limit_val)
            self.pub_speed_limit.publish(sl_fallback)

    def _trigger_emergency_shutdown(self, v_filt: float):
        """Esegue la sequenza di emergenza: arresto motori e spegnimento controllato OS."""
        # 1. Pubblica messaggio di allarme su /robot/system/shutdown
        shutdown_msg = String()
        shutdown_msg.data = f"CRITICAL_SHUTDOWN: Battery voltage {v_filt:.2f}V <= {self.v_shutdown}V for > {self.persistence_sec}s"
        self.pub_system_shutdown.publish(shutdown_msg)

        # 2. Invia Twist 0.0 immediato a priorità 0 su safety override
        stop_cmd = Twist()
        self.pub_emergency_stop.publish(stop_cmd)

        # 3. Spegnimento OS Graceful Shutdown se abilitato
        if self.auto_poweroff and not self.poweroff_executed:
            self.poweroff_executed = True
            self.get_logger().error("🛑 [BatteryManager] Esecuzione OS Graceful Shutdown (sudo poweroff)...")
            threading.Thread(target=self._execute_poweroff_worker, daemon=True).start()

    def _execute_poweroff_worker(self):
        """Thread asincrono per lanciare il poweroff del sistema operativo senza bloccare ROS 2."""
        try:
            # Sync dei filesystem per proteggere NVMe / SD
            os.system("sync")
            time.sleep(0.5)
            subprocess.run(["sudo", "systemctl", "poweroff", "-i"], check=False)
        except Exception as e:
            self.get_logger().error(f"Errore durante l'esecuzione di poweroff: {e}")


def main(args=None):
    rclpy.init(args=args)
    node = BatteryManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
