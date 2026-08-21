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

        # --- Stato Operativo e Timer Persistenza ---
        self.current_state_str = "BATTERIA OK"
        self.is_charging = False
        self.docking_start_time: Optional[float] = None
        self.shutdown_start_time: Optional[float] = None
        self.docking_triggered = False
        self.shutdown_triggered = False
        self.poweroff_executed = False

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
            self._insert_raw_sample(float(msg.voltage))

    def _raw_float_cb(self, msg: Float32):
        """Callback per messaggi raw in formato std_msgs/Float32."""
        if not math.isnan(msg.data) and msg.data > 0.5:
            self._insert_raw_sample(float(msg.data))

    def _insert_raw_sample(self, v_val: float):
        """Normalizza la tensione (V) ed inserisce nel buffer circolare FIFO."""
        # Se espressa in formato 3S Waveshare (es. 36300 per 12.60V @ 100% full)
        scale_factor = getattr(self, 'esp32_adc_scale_factor', 2880.95)
        if v_val > 15000.0:
            voltage = v_val / scale_factor
        elif v_val > 1000.0:
            voltage = v_val / 1000.0
        elif v_val > 20.0:
            voltage = v_val / (scale_factor / 1000.0)
        elif v_val > 100.0:
            voltage = v_val / 100.0
        else:
            voltage = v_val

        with self.lock:
            self.latest_raw_voltage = voltage
            self.voltage_buffer.append(voltage)
            self.filtered_voltage = sum(self.voltage_buffer) / len(self.voltage_buffer)

    def _calculate_soc(self, v_filt: float) -> float:
        """
        Calcola lo State of Charge (SoC) normalizzato tra 0.0 (0%) e 1.0 (100%).
        Mappatura sulla curva 9.00V (0%) - 12.60V (100%).
        """
        if v_filt >= self.v_full:
            return 1.0
        elif v_filt <= self.v_shutdown:
            return 0.0
        else:
            return (v_filt - self.v_shutdown) / (self.v_full - self.v_shutdown)

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
            self.is_charging = True
            power_supply_status = BatteryState.POWER_SUPPLY_STATUS_CHARGING
            soc_ratio = -1.0  # Valore convenzionale per stato di carica
            foxglove_pct = 100.0  # Visualizzazione gauge piena con etichetta di carica
            self.current_state_str = "IN CARICA (12.8V)"
            speed_limit_val = 100.0

            # Inibizione totale di allarmi e trigger
            self.docking_start_time = None
            self.shutdown_start_time = None
            self.docking_triggered = False
            self.shutdown_triggered = False

        else:
            # --- FUNZIONAMENTO A BATTERIA (V < 12.65V) ---
            self.is_charging = False
            power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
            soc_ratio = self._calculate_soc(v_filt)
            foxglove_pct = round(soc_ratio * 100.0, 1)

            if v_filt > self.v_eco:
                # --- STATO 2: BATTERIA OK (> 10.20V / > 20% SoC) ---
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
        bat_msg.percentage = float(soc_ratio)
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
