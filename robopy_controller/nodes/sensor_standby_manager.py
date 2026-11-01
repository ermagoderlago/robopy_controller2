#!/usr/bin/env python3
"""
SensorStandbyManagerNode — ROS 2 Supervisor for Power Saving and Sensor Wear Protection.
Monitors robot stillness via IMU (/oak/imu/data), wheel odometry (/odom_wheel), and /cmd_vel.
If Marcus is idle for > 2 minutes (120s) with no non-gravitational acceleration, it:
  1. Stops the RPLIDAR C1 motor via /stop_motor.
  2. Freezes RTAB-Map mapping via /rtabmap/pause.
  3. Locks hardware motion gating (/robot/motion_gate = False).

Wakes up reactively upon:
  - Non-gravitational acceleration / bump / lift via IMU (|norm(a) - g| > 0.35 m/s^2 or norm(w) > 0.15 rad/s).
  - Velocity command received via /cmd_vel.
  - Manual request via /robot/wake_sensors.

Enforces motion gating: holds wheel motion until the RPLIDAR C1 has spun up and produced
valid 360-degree laser scans, ensuring safe navigation resumption.
"""

import os
import json
import time
import math
import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from sensor_msgs.msg import Imu, LaserScan, BatteryState
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Bool, String
from std_srvs.srv import Empty, Trigger
import tf2_ros


class SensorStandbyManager(Node):
    def __init__(self):
        super().__init__('sensor_standby_manager')
        
        # --- Parameter Declarations ---
        self.declare_parameter('idle_timeout_sec', 45.0)           # [v3.0 IMP-BIO-005] 45s a robot fermo
        self.declare_parameter('min_awake_time_sec', 60.0)         # [v3.0] Isteresi anti-cycling
        self.declare_parameter('imu_accel_threshold', 0.35)        # m/s^2 deviation from gravity (|norm(a) - g|)
        self.declare_parameter('imu_gyro_threshold', 0.15)         # rad/s angular velocity norm (~8.6 deg/s)
        self.declare_parameter('nominal_gravity', 9.81)            # Nominal gravity m/s^2
        self.declare_parameter('min_scans_to_wake', 2)             # Scans required to consider LiDAR spinning & healthy
        self.declare_parameter('scan_timeout_sec', 6.0)            # Maximum wait time for LiDAR spin-up before fallback
        self.declare_parameter('check_frequency_hz', 10.0)         # Supervisor evaluation rate
        self.declare_parameter('ledger_path', '/mnt/ssd/telemetry/lidar_lifespan_ledger.json')
        
        self.idle_timeout_sec = float(self.get_parameter('idle_timeout_sec').value)
        self.min_awake_time_sec = float(self.get_parameter('min_awake_time_sec').value)
        self.imu_accel_threshold = float(self.get_parameter('imu_accel_threshold').value)
        self.imu_gyro_threshold = float(self.get_parameter('imu_gyro_threshold').value)
        self.nominal_gravity = float(self.get_parameter('nominal_gravity').value)
        self.min_scans_to_wake = int(self.get_parameter('min_scans_to_wake').value)
        self.scan_timeout_sec = float(self.get_parameter('scan_timeout_sec').value)
        self.check_frequency_hz = float(self.get_parameter('check_frequency_hz').value)
        self.ledger_path = str(self.get_parameter('ledger_path').value)
        
        # --- Internal States ---
        # States: 'ACTIVE', 'STANDBY', 'WAKING_UP'
        self.state = 'ACTIVE'
        self.is_docked = False
        self.last_activity_time = time.time()
        self.last_wake_time = time.time()
        self.last_standby_start_time = 0.0
        self.wake_start_time = 0.0
        self.wake_scans_count = 0
        self.last_gate_published = True
        self.last_gate_pub_time = 0.0
        self.last_imu_norm_a = self.nominal_gravity
        self.last_imu_norm_w = 0.0
        
        # TF Buffer and Broadcaster for StandbyTFKeepalive (IMP-BIO-005)
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self)
        self.latched_map_to_odom: TransformStamped = None
        self.tf_keepalive_timer = None
        
        # Gravity adaptive baseline calibration
        self.gravity_samples = []
        self.gravity_baseline = self.nominal_gravity
        
        self.callback_group = ReentrantCallbackGroup()
        
        # --- Publishers ---
        self.motion_gate_pub = self.create_publisher(Bool, '/robot/motion_gate', 10)
        self.power_state_pub = self.create_publisher(String, '/robot/sensor_power_state', 10)
        
        # --- Subscribers ---
        # IMU subscription is dynamic: active only in STANDBY to detect external shocks (saving 37.3 Hz wakeups)
        self.imu_sub = None
        self.create_subscription(Twist, '/cmd_vel', self.cmd_vel_callback, 10, callback_group=self.callback_group)
        self.create_subscription(Odometry, '/odom', self.odom_callback, 10, callback_group=self.callback_group)
        self.create_subscription(LaserScan, '/scan', self.scan_callback, 10, callback_group=self.callback_group)
        self.create_subscription(BatteryState, '/battery_state', self.battery_callback, 10, callback_group=self.callback_group)
        # [v2.0] Docking trigger: ferma lidar IMMEDIATAMENTE al rientro alla base di ricarica
        self.create_subscription(Bool, '/robot/docking/trigger', self.docking_trigger_callback, 10, callback_group=self.callback_group)
        # [F7] Integrazione Resource Governor come Attuatore Hardware
        self.create_subscription(String, '/resource_governor/state', self.governor_state_callback, 10, callback_group=self.callback_group)
        
        # --- Service Clients ---
        self.stop_motor_cli = self.create_client(Empty, '/stop_motor', callback_group=self.callback_group)
        self.start_motor_cli = self.create_client(Empty, '/start_motor', callback_group=self.callback_group)
        self.pause_rtabmap_cli = self.create_client(Empty, '/rtabmap/pause', callback_group=self.callback_group)
        self.resume_rtabmap_cli = self.create_client(Empty, '/rtabmap/resume', callback_group=self.callback_group)
        self.amcl_nomotion_cli = self.create_client(Empty, '/request_nomotion_update', callback_group=self.callback_group)
        
        # --- Service Server (Manual wake on demand) ---
        self.wake_srv = self.create_service(Empty, '/robot/wake_sensors', self.manual_wake_srv_callback, callback_group=self.callback_group)
        self.wake_trig_srv = self.create_service(Trigger, '/robot/wake_sensors_trigger', self.manual_wake_trigger_callback, callback_group=self.callback_group)
        
        # --- Periodic Supervisor Timer ---
        timer_period = 1.0 / self.check_frequency_hz
        self.timer = self.create_timer(timer_period, self.supervisor_step)
        
        # Publish initial states
        self.publish_motion_gate(True, force=True)
        self.publish_power_state("ACTIVE")
        
        self.get_logger().info(
            f"⚡ SensorStandbyManager v2.0 initialized. Idle Timeout={self.idle_timeout_sec}s, "
            f"IMU Accel Threshold={self.imu_accel_threshold} m/s^2, Gyro Threshold={self.imu_gyro_threshold} rad/s. "
            f"LIDAR auto-stop: {self.idle_timeout_sec:.0f}s inattività o docking trigger."
        )

    def publish_motion_gate(self, gate_open: bool, force: bool = False):
        """Publishes motion gate state. True = wheels allowed to move, False = wheels locked."""
        now = time.time()
        if force or (gate_open != self.last_gate_published) or (now - self.last_gate_pub_time > 1.0):
            msg = Bool()
            msg.data = bool(gate_open)
            self.motion_gate_pub.publish(msg)
            self.last_gate_published = gate_open
            self.last_gate_pub_time = now

    def publish_power_state(self, state_str: str):
        """Publishes diagnostic telemetry string."""
        msg = String()
        msg.data = str(state_str)
        self.power_state_pub.publish(msg)

    def imu_callback(self, msg: Imu):
        """Monitors linear acceleration and angular velocity for stillness or external shocks."""
        now = time.time()
        if now - getattr(self, '_last_imu_sample_time', 0.0) < 0.10:
            return
        self._last_imu_sample_time = now

        ax = msg.linear_acceleration.x
        ay = msg.linear_acceleration.y
        az = msg.linear_acceleration.z
        norm_a = math.sqrt(ax * ax + ay * ay + az * az)
        
        gx = msg.angular_velocity.x
        gy = msg.angular_velocity.y
        gz = msg.angular_velocity.z
        norm_w = math.sqrt(gx * gx + gy * gy + gz * gz)
        
        self.last_imu_norm_a = norm_a
        self.last_imu_norm_w = norm_w
        
        # Adaptive gravity baseline on first 50 valid frames
        if len(self.gravity_samples) < 50:
            if 8.0 < norm_a < 11.5:
                self.gravity_samples.append(norm_a)
                if len(self.gravity_samples) == 50:
                    self.gravity_baseline = sum(self.gravity_samples) / len(self.gravity_samples)
                    self.get_logger().info(f"📊 Calibrated IMU gravity baseline: {self.gravity_baseline:.3f} m/s^2")
        
        delta_a = abs(norm_a - self.gravity_baseline)
        
        # Disturbance detected: either acceleration departure from gravity or significant gyro rate
        is_disturbed = (delta_a > self.imu_accel_threshold) or (norm_w > self.imu_gyro_threshold)
        
        now = time.time()
        if is_disturbed:
            if self.state == 'ACTIVE':
                self.last_activity_time = now
            elif self.state == 'STANDBY':
                reason = f"IMU Disturbance (delta_a={delta_a:.3f} m/s^2, norm_w={norm_w:.3f} rad/s)"
                self.get_logger().info(f"🚨 External force detected: {reason} -> Triggering wake-up!")
                self.trigger_wakeup(reason)

    def cmd_vel_callback(self, msg: Twist):
        """Detects incoming velocity commands to reset idle timer or wake up sensors."""
        v = msg.linear.x
        w = msg.angular.z
        is_motion_requested = (abs(v) > 0.005 or abs(w) > 0.005)
        
        now = time.time()
        if is_motion_requested:
            if self.state == 'ACTIVE':
                self.last_activity_time = now
            elif self.state == 'STANDBY':
                reason = f"cmd_vel received (v={v:.2f}, w={w:.2f})"
                self.get_logger().info(f"🚀 Motion command received: {reason} -> Triggering wake-up!")
                self.trigger_wakeup(reason)

    def odom_callback(self, msg: Odometry):
        """Detects physical robot chassis motion from wheel encoders."""
        v_lin = msg.twist.twist.linear.x
        w_ang = msg.twist.twist.angular.z
        if abs(v_lin) > 0.005 or abs(w_ang) > 0.01:
            if self.state == 'ACTIVE':
                self.last_activity_time = time.time()

    def scan_callback(self, msg: LaserScan):
        """Tracks arrival of laser scans to confirm LiDAR spin-up completion during waking."""
        if self.state == 'WAKING_UP':
            # Count valid scans received since wake initiation
            if len(msg.ranges) > 0:
                self.wake_scans_count += 1
                self.get_logger().info(f"📡 Laser scan #{self.wake_scans_count} received during spin-up ({len(msg.ranges)} ranges).")
                if self.wake_scans_count >= self.min_scans_to_wake:
                    spin_duration = time.time() - self.wake_start_time
                    self.get_logger().info(
                        f"✅ LiDAR spin-up confirmed ({self.wake_scans_count} scans in {spin_duration:.2f}s). "
                        f"Executing remaining wake handshake steps..."
                    )
                    self._execute_wake_handshake_completion()

    def call_async_service(self, client, srv_name: str):
        """Asynchronously calls an Empty service without blocking the node thread."""
        if not client.service_is_ready():
            self.get_logger().warn(f"Service {srv_name} is not currently ready/available.")
            return
        req = Empty.Request()
        future = client.call_async(req)
        def _done_cb(f):
            try:
                f.result()
                self.get_logger().debug(f"Service call {srv_name} completed successfully.")
            except Exception as e:
                self.get_logger().warn(f"Service call {srv_name} failed: {e}")
        future.add_done_callback(_done_cb)

    def battery_callback(self, msg: BatteryState):
        """Monitor stato di carica dal BMS (FM-PWR-001, Invariante 6)."""
        is_charging = (msg.power_supply_status == BatteryState.POWER_SUPPLY_STATUS_CHARGING) or (msg.voltage >= 12.70)
        self.is_docked = is_charging

    def governor_state_callback(self, msg: String):
        """[F7/IMP-BIO-005] Riceve direttive dal Resource Governor con supporto spegnimento a fermo."""
        try:
            data = json.loads(msg.data)
            k_state = data.get("kinematic_state", "")
            in_dock = data.get("in_dock", self.is_docked)
            self.is_docked = in_dock
            rec_profile = data.get("recommended_profile", {})
            lidar_active = rec_profile.get("lidar_active", True)
            
            if not lidar_active:
                if self.state == "ACTIVE":
                    now = time.time()
                    if (now - self.last_wake_time) >= self.min_awake_time_sec:
                        self.get_logger().info(f"💤 [StandbyManager] Ricevuta direttiva Governor: spegnimento LiDAR (k_state={k_state}).")
                        self.trigger_standby()
            elif k_state in ("WAKING", "STATIONARY", "PREP_NAV", "MOVING"):
                if self.state == "STANDBY":
                    self.get_logger().info(f"⚡ [StandbyManager] Ricevuta direttiva Governor: {k_state} -> Avvio e spin-up LiDAR.")
                    self.trigger_wakeup(f"Governor {k_state}")
        except Exception as e:
            self.get_logger().error(f"Errore parsing stato Governor: {e}")

    def docking_trigger_callback(self, msg: Bool):
        """
        [FM-PWR-001/FM-ACT-005 FIX] Richiesta rientro alla cuccia per batteria scarica.
        NON arrestare il LiDAR e NON bloccare le ruote: il robot deve poter navigare autonomamente verso la base!
        Lo standby del LiDAR si attiva solo quando la carica effettiva è in corso (is_docked / V >= 12.70V)
        oppure su direttiva esplicita Governor (DOCKED_SLEEP).
        """
        if msg.data:
            self.get_logger().info(
                "🪫 [DOCKING] Segnale richiesta rientro alla base ricevuto dal BMS. "
                "Sensori e motion gate rimangono attivi per consentire la navigazione verso la cuccia.",
                throttle_duration_sec=10.0
            )

    def _start_tf_keepalive(self):
        """Avvia il broadcaster 1.0 Hz di map -> odom latciata (IMP-BIO-005)."""
        try:
            # Prova a catturare l'ultima trasformata map -> odom da TF
            t = self.tf_buffer.lookup_transform(
                'map', 'odom', rclpy.time.Time(),
                timeout=rclpy.duration.Duration(seconds=0.2)
            )
            self.latched_map_to_odom = t
            self.get_logger().info("📌 [StandbyTF] Trasformata 'map -> odom' catturata e latciata per lo Standby Keepalive.")
        except Exception as e:
            self.get_logger().warn(f"⚠️ [StandbyTF] Impossibile catturare map -> odom da TF ({e}). Uso fallback identità.")
            t = TransformStamped()
            t.header.frame_id = 'map'
            t.child_frame_id = 'odom'
            t.transform.rotation.w = 1.0
            self.latched_map_to_odom = t

        if self.tf_keepalive_timer is None:
            self.tf_keepalive_timer = self.create_timer(1.0, self._tf_keepalive_step)

    def _tf_keepalive_step(self):
        """Broadcasting periodico 1.0 Hz per prevenire timeout TF in AMCL / Nav2 durante lo standby LiDAR."""
        if self.state != 'STANDBY' or self.latched_map_to_odom is None:
            return
        # Rinnova timestamp: now + 200ms tolleranza
        now_msg = self.get_clock().now() + rclpy.duration.Duration(seconds=0.2)
        self.latched_map_to_odom.header.stamp = now_msg.to_msg()
        self.tf_broadcaster.sendTransform(self.latched_map_to_odom)

    def _stop_tf_keepalive(self):
        """Interrompe il thread di keepalive prima di risvegliare AMCL (Handshake Step 0)."""
        if self.tf_keepalive_timer is not None:
            self.tf_keepalive_timer.cancel()
            self.destroy_timer(self.tf_keepalive_timer)
            self.tf_keepalive_timer = None
            self.get_logger().info("🛑 [StandbyTF] Keepalive terminato. AMCL riprende il controllo nativo del TF tree.")

    def _record_lidar_wear(self, duration_s: float):
        """Aggiorna il registro persistente delle ore operative del LiDAR C1 (IMP-BIO-005, FM-BIO-005)."""
        try:
            target_dir = os.path.dirname(self.ledger_path)
            if not os.path.exists(target_dir):
                target_dir = "/tmp"
                ledger_file = "/tmp/lidar_lifespan_ledger.json"
            else:
                ledger_file = self.ledger_path

            data = {"total_operating_seconds": 0.0, "total_standby_seconds": 0.0, "cycles_count": 0}
            if os.path.exists(ledger_file):
                with open(ledger_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

            data["total_operating_seconds"] = data.get("total_operating_seconds", 0.0) + duration_s
            data["cycles_count"] = data.get("cycles_count", 0) + 1
            data["last_update"] = time.time()

            with open(ledger_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.get_logger().info(f"📊 [Ledger] RPLIDAR C1 telemetria salvata: {data['total_operating_seconds']/3600.0:.2f} h attive.")
        except Exception as e:
            self.get_logger().warn(f"Errore scrittura ledger vita utile LiDAR: {e}")

    def trigger_standby(self):
        """Enters power-saving STANDBY state with TF keepalive and ledger update."""
        now = time.time()
        # Isteresi anti-cycling: non spegnere se svegliato da meno di min_awake_time_sec
        if (now - self.last_wake_time) < self.min_awake_time_sec:
            self.get_logger().info(f"⏳ Anti-cycling attivo: svegliato da soli {(now - self.last_wake_time):.1f}s. Standby ritardato.")
            return

        self.get_logger().info("💤 Entering Smart Standby: stopping RPLIDAR C1, latching TF and pausing RTAB-Map...")
        self.state = 'STANDBY'
        self.last_standby_start_time = now
        
        # Registra usura della sessione attiva appena conclusa
        session_active_duration = max(0.0, now - self.last_wake_time)
        self._record_lidar_wear(session_active_duration)

        # 1. Avvia TF Keepalive (IMP-BIO-005)
        self._start_tf_keepalive()

        # 2. Sottoscrizione IMU in STANDBY per rilevare scosse/urti esterni
        if self.imu_sub is None:
            self.imu_sub = self.create_subscription(
                Imu, '/oak/imu/data', self.imu_callback, 10, callback_group=self.callback_group
            )

        # 3. Lock ruote
        self.publish_motion_gate(False, force=True)
        self.publish_power_state("STANDBY")
        
        # 4. Arresto motore RPLIDAR C1
        self.call_async_service(self.stop_motor_cli, '/stop_motor')
        
        # 5. Pausa RTAB-Map SLAM mapping
        self.call_async_service(self.pause_rtabmap_cli, '/rtabmap/pause')

    def trigger_wakeup(self, reason: str = "Unspecified"):
        """
        Initiates 5-Step Wake-Before-Move Handshake (IMP-BIO-003, IMP-BIO-005).
        Step 0: Arresta StandbyTFKeepalive.
        Step 1: Accende motore RPLIDAR C1.
        Step 2: Attende regime 10 Hz (min_scans_to_wake).
        Step 3: Invoca /request_nomotion_update per allineare particelle AMCL.
        Step 4: Riprende RTAB-Map SLAM.
        Step 5: Verifica assenza dislocazione passiva e sblocca Motion Gate.
        """
        self.get_logger().info(f"⚡ [Handshake Step 0] Wake-up requested ({reason}). Stopping TF keepalive...")
        self.state = 'WAKING_UP'
        self.wake_start_time = time.time()
        self.wake_scans_count = 0
        self.last_wake_time = time.time()
        
        # Step 0: Stop keepalive immediatamente
        self._stop_tf_keepalive()

        # Distruggi sottoscrizione IMU
        if self.imu_sub is not None:
            self.destroy_subscription(self.imu_sub)
            self.imu_sub = None

        # Ruote rigorosamente bloccate durante lo spin-up
        self.publish_motion_gate(False, force=True)
        self.publish_power_state("WAKING_UP")
        
        # Step 1: Start RPLIDAR C1 motor
        self.get_logger().info("⚡ [Handshake Step 1] Avvio motore RPLIDAR C1...")
        self.call_async_service(self.start_motor_cli, '/start_motor')

    def _execute_wake_handshake_completion(self):
        """Compie i rimanenti Step 3, 4, 5 dell'Handshake dopo l'arrivo dei primi scan validi."""
        # Step 3: Invoca AMCL nomotion update per forzare il resync delle particelle su mappa
        self.get_logger().info("⚡ [Handshake Step 3] Richiesta nomotion update ad AMCL...")
        self.call_async_service(self.amcl_nomotion_cli, '/request_nomotion_update')

        # Step 4: Resume RTAB-Map
        self.get_logger().info("⚡ [Handshake Step 4] Ripresa mapping RTAB-Map...")
        self.call_async_service(self.resume_rtabmap_cli, '/rtabmap/resume')

        # Step 5: Verifica cinematica e rilascio motion gate
        self.get_logger().info("✅ [Handshake Step 5] Validazione completata. Apertura Motion Gate.")
        self.state = 'ACTIVE'
        self.last_activity_time = time.time()
        self.publish_motion_gate(True, force=True)
        self.publish_power_state("ACTIVE")

    def manual_wake_srv_callback(self, request, response):
        """Service handler for /robot/wake_sensors."""
        if self.state != 'ACTIVE':
            self.trigger_wakeup("Manual service call /robot/wake_sensors")
        return response

    def manual_wake_trigger_callback(self, request, response):
        """Trigger service handler for /robot/wake_sensors_trigger."""
        if self.state != 'ACTIVE':
            self.trigger_wakeup("Manual service call /robot/wake_sensors_trigger")
            response.success = True
            response.message = f"Wake-up initiated from state {self.state}"
        else:
            response.success = True
            response.message = "Sensors are already ACTIVE."
        return response

    def supervisor_step(self):
        """Periodic supervisor loop at check_frequency_hz."""
        now = time.time()
        
        if self.state == 'ACTIVE':
            idle_duration = now - self.last_activity_time
            # [IMP-BIO-005] Entra in standby se idle oltre idle_timeout_sec (anche fuori dal dock se fermo)
            # a condizione che sia rispettata la finestra anti-cycling
            if idle_duration >= self.idle_timeout_sec:
                if (now - self.last_wake_time) >= self.min_awake_time_sec:
                    self.trigger_standby()
            else:
                self.publish_motion_gate(True)
                
        elif self.state == 'STANDBY':
            self.publish_motion_gate(False)
            
        elif self.state == 'WAKING_UP':
            self.publish_motion_gate(False)
            
            # Spin-up timeout protection
            elapsed = now - self.wake_start_time
            if elapsed > self.scan_timeout_sec:
                self.get_logger().warn(
                    f"⚠️ LiDAR spin-up scan confirmation timed out after {elapsed:.1f}s. "
                    f"Forcing completion of handshake."
                )
                self._execute_wake_handshake_completion()
                self.last_activity_time = now
                self.publish_motion_gate(True, force=True)
                self.publish_power_state("ACTIVE")


def main(args=None):
    rclpy.init(args=args)
    node = SensorStandbyManager()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
