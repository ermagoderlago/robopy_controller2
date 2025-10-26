#!/usr/bin/env python3
# IMU_node.py
# Nodo ROS2 per ICM-20948 via SPI + AK09916 (magnetometro) tramite I2C-master interno.
# Calibrazione: COMMENTATA per ora (puoi riabilitarla decommentando le chiamate).

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu, MagneticField
import spidev
import time
import math
import sys

# --- costanti e conversioni ---
DEG2RAD = math.pi / 180.0
G_TO_MS2 = 9.80665
MAG_UT_PER_LSB = 0.15  # µT/LSB (AK09916 typical)
MAG_TO_T = 1e-6

# Register / bank helpers
REG_BANK_SEL = 0x7F

# Bank indexes (user bank value written directly)
BANK_0 = 0x00
BANK_1 = 0x10
BANK_2 = 0x20
BANK_3 = 0x30

# Common registers (bank 0)
WHO_AM_I = 0x00
PWR_MGMT_1 = 0x06
USER_CTRL = 0x03
PWR_MGMT_2 = 0x07
ACCEL_XOUT_H = 0x2D
GYRO_XOUT_H = 0x33
EXT_SENS_BASE_1 = 0x3B  # possibile base (alcune board usano 0x49) -> test fallback

# Bank 2 registers (examples)
ACCEL_CONFIG = 0x14
GYRO_CONFIG_1 = 0x01

# Bank 3: I2C master / SLV registers
I2C_MST_CTRL = 0x01  # bank3
I2C_MST_DELAY_CTRL = 0x02  # bank3
I2C_SLV0_ADDR = 0x03  # bank3
I2C_SLV0_REG = 0x04   # bank3
I2C_SLV0_CTRL = 0x05  # bank3

# SLV4 regs (bank3)
I2C_SLV4_ADDR = 0x31
I2C_SLV4_REG = 0x32
I2C_SLV4_DO = 0x33
I2C_SLV4_CTRL = 0x34

# AK09916 magnetometer
AK_ADDR = 0x0C
AK_WHO = 0x01
AK_ST1 = 0x10
AK_HXL = 0x11
AK_CNTL2 = 0x31

# ------------------------------------------------------------
# Driver per ICM-20948 (semplificato ma robusto per debug)
# ------------------------------------------------------------
class ICM20948:
    def __init__(self, spi_bus=0, spi_dev=0, spi_speed=1000000, verbose=True):
        self.verbose = verbose
        try:
            self.spi = spidev.SpiDev()
            self.spi.open(spi_bus, spi_dev)
            # usa la mode/speed che ha funzionato nei tuoi test (se diverso, cambialo)
            self.spi.mode = 0b00
            self.spi.max_speed_hz = spi_speed
        except Exception as e:
            raise RuntimeError(f"Impossibile aprire SPI: {e}")

        # offset/bias (calibrazione disabilitata per ora)
        self.accel_offset = [0.0, 0.0, 0.0]  # in g
        self.gyro_offset = [0.0, 0.0, 0.0]   # in deg/s

        self.mag_addr = AK_ADDR

        # inizializza dispositivo e magnetometro
        self._init_icm()
        # configura il magnetometro in modo robusto
        self._robust_mag_setup()

        # se vuoi attivare la calibrazione automatica commenta le righe seguenti
        # self.quick_calibrate()

    # --- low level SPI ---
    def _xfer(self, bytes_out):
        return self.spi.xfer2(bytes_out)

    def _write_reg(self, reg, val):
        # scrivi: bit7 = 0
        self._xfer([reg & 0x7F, val])
        # breve delay per sicurezza
        time.sleep(0.001)

    def _read_reg(self, reg, length=1):
        # leggi: invia reg|0x80 poi dummy bytes
        resp = self._xfer([reg | 0x80] + [0x00] * length)
        return resp[1:]  # esclude il byte echo

    def _set_bank(self, bank):
        # bank è 0x00 / 0x10 / 0x20 / 0x30
        self._write_reg(REG_BANK_SEL, bank)
        time.sleep(0.001)

    # --- init imu ---
    def _init_icm(self):
        # check WHO_AM_I
        self._set_bank(BANK_0)
        who = self._read_reg(WHO_AM_I, 1)[0]
        if self.verbose:
            print(f"[ICM] WHO_AM_I = 0x{who:02X}")
        if who != 0xEA:
            raise RuntimeError(f"ICM-20948 non trovato! WHO_AM_I=0x{who:02X}")

        # soft reset
        self._write_reg(PWR_MGMT_1, 0x80)
        time.sleep(0.1)
        # wake up, auto clock
        self._write_reg(PWR_MGMT_1, 0x01)
        time.sleep(0.02)

        # disable I2C passthrough and keep SPI-only control (USER_CTRL clearing managed later)
        # config sensori (bank 2)
        self._set_bank(BANK_2)
        # ±2g accel, ±250dps gyro (valori 0x00)
        self._write_reg(ACCEL_CONFIG, 0x00)
        self._write_reg(GYRO_CONFIG_1, 0x00)
        time.sleep(0.01)
        self._set_bank(BANK_0)
        time.sleep(0.01)

    # --- robust mag setup (SLV4 write CNTL2, SLV0 read ST1+HXL..HZH) ---
    def _robust_mag_setup(self):
        try:
            # 1) abilita I2C master (USER_CTRL bit I2C_MST_EN = 0x20)
            self._set_bank(BANK_0)
            try:
                cur = self._read_reg(USER_CTRL, 1)[0]
            except Exception:
                cur = 0x00
            self._write_reg(USER_CTRL, cur | 0x20)
            time.sleep(0.01)

            # 2) configura I2C_MST in bank3
            self._set_bank(BANK_3)
            # I2C_MST_CTRL = 0x07 (clock ~345kHz)
            self._write_reg(I2C_MST_CTRL, 0x07)
            # I2C_MST_DELAY_CTRL: enable shadow + enable SLV0 delay (bit7 | bit0)
            self._write_reg(I2C_MST_DELAY_CTRL, 0x81)
            time.sleep(0.01)

            # 3) SLV4: scrivi AK_CNTL2 = 0x08 (continuous mode) via SLV4 write
            # SLV4_ADDR (write) = AK_ADDR & 0x7F
            self._write_reg(I2C_SLV4_ADDR, self.mag_addr & 0x7F)
            self._write_reg(I2C_SLV4_REG, AK_CNTL2)
            self._write_reg(I2C_SLV4_DO, 0x08)   # continuous mode
            # start SLV4 transaction
            self._write_reg(I2C_SLV4_CTRL, 0x80)
            time.sleep(0.02)

            # 4) SLV0: configura lettura ST1(0x10) + HXL..HZH + ST2 (8 byte)
            self._write_reg(I2C_SLV0_ADDR, 0x80 | self.mag_addr)  # read
            self._write_reg(I2C_SLV0_REG, AK_ST1)
            self._write_reg(I2C_SLV0_CTRL, 0x80 | 0x08)  # enable + len=8
            time.sleep(0.02)

            # 5) torna bank0 e lascia che l'ICM copi dati in EXT_SENS_DATA
            self._set_bank(BANK_0)
            time.sleep(0.02)

            if self.verbose:
                print("[ICM] Magnetometro configurato (SLV4 write + SLV0 read).")
        except Exception as e:
            print("[WARN] _robust_mag_setup failed:", e)
            self._set_bank(BANK_0)

    # --- letture raw ---
    def read_acceleration_raw(self):
        try:
            self._set_bank(BANK_0)
            data = self._read_reg(ACCEL_XOUT_H, 6)
            x = int.from_bytes(bytes(data[0:2]), 'big', signed=True)
            y = int.from_bytes(bytes(data[2:4]), 'big', signed=True)
            z = int.from_bytes(bytes(data[4:6]), 'big', signed=True)
            return x, y, z
        except Exception:
            return 0, 0, 0

    def read_gyroscope_raw(self):
        try:
            self._set_bank(BANK_0)
            data = self._read_reg(GYRO_XOUT_H, 6)
            x = int.from_bytes(bytes(data[0:2]), 'big', signed=True)
            y = int.from_bytes(bytes(data[2:4]), 'big', signed=True)
            z = int.from_bytes(bytes(data[4:6]), 'big', signed=True)
            return x, y, z
        except Exception:
            return 0, 0, 0

    def read_acceleration(self):
        x, y, z = self.read_acceleration_raw()
        scale = 16384.0  # ±2g
        return (x/scale - self.accel_offset[0],
                y/scale - self.accel_offset[1],
                z/scale - self.accel_offset[2])

    def read_gyroscope(self):
        x, y, z = self.read_gyroscope_raw()
        scale = 131.0  # ±250 dps
        return (x/scale - self.gyro_offset[0],
                y/scale - self.gyro_offset[1],
                z/scale - self.gyro_offset[2])

    # --- lettura magnetometro con fallback di base ---
    def read_magnetometer(self):
        # proviamo due possibili base per EXT_SENS_DATA (dipende dal breakout): 0x3B e 0x49
        for ext_base in (0x3B, 0x49):
            try:
                self._set_bank(BANK_0)
                data = self._read_reg(ext_base, 8)
                if len(data) < 8:
                    continue
                st1 = data[0]
                if not (st1 & 0x01):
                    # DRDY non settato -> dati non pronti
                    continue
                # AK09916: HXL,HXH,YXL,YXH,ZXL,ZXH (little-endian pairs)
                hx = int.from_bytes(bytes([data[1], data[2]]), 'little', signed=True)
                hy = int.from_bytes(bytes([data[3], data[4]]), 'little', signed=True)
                hz = int.from_bytes(bytes([data[5], data[6]]), 'little', signed=True)
                st2 = data[7]
                if (st2 & 0x08):
                    # overflow
                    continue
                # converti in Tesla
                mx = hx * MAG_UT_PER_LSB * MAG_TO_T
                my = hy * MAG_UT_PER_LSB * MAG_TO_T
                mz = hz * MAG_UT_PER_LSB * MAG_TO_T
                return mx, my, mz
            except Exception:
                continue
        return None

    # --- quick calibrate (COMMENTATA: abilitala solo quando vuoi) ---
    def quick_calibrate(self, samples=100):
        # Questa routine è utile ma è commentata nell'uso standard
        gx = gy = gz = 0.0
        for _ in range(samples):
            x, y, z = self.read_gyroscope_raw()
            gx += x/131.0; gy += y/131.0; gz += z/131.0
            time.sleep(0.005)
        self.gyro_offset = [gx/samples, gy/samples, gz/samples]

        ax = ay = az = 0.0
        for _ in range(samples):
            x, y, z = self.read_acceleration_raw()
            ax += x/16384.0; ay += y/16384.0; az += z/16384.0
            time.sleep(0.005)
        avg = [ax/samples, ay/samples, az/samples]
        # rimuovo solo il bias, non la gravità in modo aggressivo (se vuoi vedi commenti)
        self.accel_offset = [avg[0], avg[1], avg[2] - 1.0]

# ------------------------------------------------------------
# Nodo ROS2
# ------------------------------------------------------------
class IMUPublisher(Node):
    def __init__(self):
        super().__init__('imu_publisher')
        self.pub_imu = self.create_publisher(Imu, 'imu/data_raw', 10)
        self.pub_mag = self.create_publisher(MagneticField, 'imu/mag', 10)

        # Inizializzazione ICM (se fallisce, vogliamo vedere l'eccezione)
        try:
            self.imu = ICM20948(spi_bus=0, spi_dev=0, spi_speed=1000000, verbose=True)
        except Exception as e:
            self.get_logger().error(f"Impossibile inizializzare ICM: {e}")
            raise

        # Se vuoi disabilitare la quick_calibrate, lascia tutto com'è (è commentata nel driver)
        # Se vuoi eseguire calibrazione rapida automatica decommenta la riga:
        # self.imu.quick_calibrate()

        # Timer di publish (IMU 50Hz). Magnetometro verrà letto ogni MAG_POLL_INTERVAL
        self.publish_hz = 50.0
        self.mag_poll_interval = 0.1  # secondi (100 ms)
        self._last_mag_time = 0.0

        self.timer = self.create_timer(1.0 / self.publish_hz, self._on_timer)
        self._counter = 0
        self.get_logger().info("Nodo IMU avviato (IMU + MAG). Calibrazione disabilitata per ora.")

    def _on_timer(self):
        self._counter += 1
        # Leggi IMU
        ax, ay, az = self.imu.read_acceleration()
        gx, gy, gz = self.imu.read_gyroscope()

        # Pubblica IMU
        imu_msg = Imu()
        imu_msg.header.stamp = self.get_clock().now().to_msg()
        imu_msg.header.frame_id = 'imu_link'
        imu_msg.linear_acceleration.x = ax * G_TO_MS2
        imu_msg.linear_acceleration.y = ay * G_TO_MS2
        imu_msg.linear_acceleration.z = az * G_TO_MS2
        imu_msg.angular_velocity.x = gx * DEG2RAD
        imu_msg.angular_velocity.y = gy * DEG2RAD
        imu_msg.angular_velocity.z = gz * DEG2RAD
        imu_msg.orientation_covariance[0] = -1.0
        imu_msg.linear_acceleration_covariance = [0.04,0,0, 0,0.04,0, 0,0,0.04]
        imu_msg.angular_velocity_covariance = [0.02,0,0, 0,0.02,0, 0,0,0.02]
        self.pub_imu.publish(imu_msg)

        # Leggi magnetometro con rate limit
        now = time.time()
        if now - self._last_mag_time >= self.mag_poll_interval:
            mag = self.imu.read_magnetometer()
            self._last_mag_time = now
            if mag is not None:
                mx, my, mz = mag
                mag_msg = MagneticField()
                mag_msg.header = imu_msg.header
                mag_msg.magnetic_field.x = mx
                mag_msg.magnetic_field.y = my
                mag_msg.magnetic_field.z = mz
                self.pub_mag.publish(mag_msg)
                mag_str = f"{mx:.3e}, {my:.3e}, {mz:.3e} T"
            else:
                mag_str = "no mag"
        else:
            mag_str = "(skip)"

        # Log informativo (ogni ciclo)
        self.get_logger().info(
            f"ACC: [{ax:6.3f}, {ay:6.3f}, {az:6.3f}] g | "
            f"GYR: [{gx:7.2f}, {gy:7.2f}, {gz:7.2f}] °/s | "
            f"MAG: {mag_str}"
        )

def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = IMUPublisher()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Errore nodale: {e}", file=sys.stderr)
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
