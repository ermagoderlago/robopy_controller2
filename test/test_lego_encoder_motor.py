#!/usr/bin/env python3
"""
Test LEGO Encoder Motor via BuildHAT
======================================
Tests SPIKE-compatible clone encoder motors on ports C and D.
Patches buildhat to recognize type ID 17 (clone SPIKE motors).

Usage:
    source ~/ros2_venv/bin/activate
    python test/test_lego_encoder_motor.py
"""

import sys
import time
import signal

def timeout_handler(signum, frame):
    raise TimeoutError("Operazione in timeout")

try:
    from buildhat import Motor
    from buildhat.devices import Device
except ImportError:
    print("❌ buildhat non trovato. Installa con: pip install buildhat")
    sys.exit(1)


def patch_clone_motors():
    """Add clone SPIKE motor type IDs to buildhat's device list."""
    # Type ID 17 = clone SPIKE motor (non-original LEGO)
    clone_ids = {
        17: ("Motor", "Clone SPIKE Motor (typeID 17)"),
    }
    for tid, info in clone_ids.items():
        if tid not in Device._device_names:
            Device._device_names[tid] = info
            print(f"  ✅ Patch: aggiunto typeID {tid} come {info[1]}")
        else:
            print(f"  ℹ️  typeID {tid} già registrato come {Device._device_names[tid][1]}")


def scan_and_wait(timeout_sec=20):
    """Scan ports C/D and wait for device detection."""
    print("=" * 50)
    print("🔍 Scansione porte (attendo rilevamento...)")
    print("   Tieni i cavi ben premuti nelle porte!")
    print("=" * 50)

    Device._setup()
    time.sleep(1)

    from buildhat import Hat
    hat = Hat()
    print(f"  ⚡ Tensione ingresso: {hat.get_vin():.2f}V")
    hat._close()

    found = {}
    for attempt in range(timeout_sec * 2):  # Check every 0.5s
        for port_idx in [2, 3]:  # C=2, D=3
            port_letter = chr(ord('A') + port_idx)
            if port_letter in found:
                continue
            conn = Device._instance.connections[port_idx]
            tid = conn.typeid
            if tid != -1 and conn.connected:
                found[port_letter] = tid
                print(f"  [{attempt*0.5:.1f}s] Porta {port_letter}: typeID={tid} ← RILEVATO!")
        if len(found) == 2:
            break
        time.sleep(0.5)

    # Also report A and B
    for port_idx in [0, 1]:
        port_letter = chr(ord('A') + port_idx)
        conn = Device._instance.connections[port_idx]
        print(f"  Porta {port_letter}: typeID={conn.typeid} ({Device.name_for_id(conn.typeid)})")

    Device._instance.shutdown()
    # Reset Device singleton so Motor can reinitialize
    Device._instance = None
    Device._used = {0: False, 1: False, 2: False, 3: False}

    print()
    return found


def test_motor_on_port(port: str):
    """Test an encoder motor on the specified port."""
    print(f"=" * 50)
    print(f"🎮 Test Motore Encoder - Porta {port}")
    print(f"=" * 50)

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(15)

    try:
        motor = Motor(port)
        signal.alarm(0)
        print(f"  ✅ Motore inizializzato su porta {port}")
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⏰ Timeout inizializzazione su porta {port}")
        return False
    except Exception as e:
        signal.alarm(0)
        print(f"  ❌ Errore: {e}")
        return False

    # 1. Read initial position
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(5)
    try:
        pos = motor.get_position()
        signal.alarm(0)
        print(f"  📐 Posizione iniziale: {pos}°")
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⏰ Timeout lettura posizione")
    except Exception as e:
        signal.alarm(0)
        print(f"  ⚠️  Errore lettura posizione: {e}")

    # 2. Read speed
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(5)
    try:
        speed = motor.get_speed()
        signal.alarm(0)
        print(f"  🔄 Velocità attuale: {speed}")
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⏰ Timeout lettura velocità")
    except Exception as e:
        signal.alarm(0)
        print(f"  ⚠️  Errore lettura velocità: {e}")

    # 3. Rotation +90°
    print(f"\n  ▶️  Test: rotazione +90° (velocità 30%)...")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)
    try:
        motor.run_for_degrees(90, speed=30)
        time.sleep(1.5)
        pos_after = motor.get_position()
        signal.alarm(0)
        print(f"  ✅ Posizione dopo +90°: {pos_after}°")
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⏰ Timeout rotazione")
    except Exception as e:
        signal.alarm(0)
        print(f"  ❌ Errore: {e}")

    # 4. Rotation -90°
    print(f"  ◀️  Test: rotazione -90° (velocità 30%)...")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)
    try:
        motor.run_for_degrees(-90, speed=30)
        time.sleep(1.5)
        pos_after = motor.get_position()
        signal.alarm(0)
        print(f"  ✅ Posizione dopo -90°: {pos_after}°")
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⏰ Timeout rotazione")
    except Exception as e:
        signal.alarm(0)
        print(f"  ❌ Errore: {e}")

    # 5. Run at speed for 2 seconds
    print(f"  🏃 Test: velocità costante (30%) per 2 secondi...")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(10)
    try:
        motor.start(speed=30)
        for i in range(4):
            time.sleep(0.5)
            try:
                pos = motor.get_position()
                print(f"    t={0.5*(i+1):.1f}s → pos={pos}°")
            except Exception:
                print(f"    t={0.5*(i+1):.1f}s → lettura fallita")
        motor.stop()
        signal.alarm(0)
        print(f"  ✅ Motore fermato")
    except TimeoutError:
        signal.alarm(0)
        print(f"  ⏰ Timeout test velocità")
    except Exception as e:
        signal.alarm(0)
        print(f"  ❌ Errore: {e}")

    print(f"\n  🎉 Test porta {port} completato!")
    print()
    return True


def main():
    print("\n" + "🤖 " * 15)
    print("  TEST MOTORI ENCODER SPIKE (CLONE) - BuildHAT")
    print("🤖 " * 15 + "\n")

    # Step 0: Patch clone motor type IDs
    print("=" * 50)
    print("🔧 Patch per motori clone SPIKE")
    print("=" * 50)
    patch_clone_motors()
    print()

    # Step 1: Scan and wait for detection
    found = scan_and_wait(timeout_sec=20)

    if not found:
        print("⚠️  Nessun motore rilevato in 20 secondi.")
        print("   Suggerimenti:")
        print("   1. Tieni il cavo premuto nella porta durante lo scan")
        print("   2. Se il connettore ha gioco, prova a piegarlo leggermente")
        print("   3. I cloni possono avere contatti deboli — spingi forte")
        return

    # Step 2: Test found ports
    for port, tid in found.items():
        print(f"\n{'─' * 50}")
        print(f"  Test porta {port} (typeID={tid})")
        print(f"{'─' * 50}\n")
        try:
            test_motor_on_port(port)
        except Exception as e:
            print(f"  ❌ Errore generale: {e}")

    print("\n✅ Test completato.\n")


if __name__ == "__main__":
    main()
