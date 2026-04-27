#!/usr/bin/env python3
"""Script di diagnostica rapida per il ReSpeaker Lite."""
import sys, os, time

print("=== ReSpeaker Probe ===")
print(f"Python: {sys.version}")

# 1. Soppressione ALSA
try:
    from ctypes import cdll, CFUNCTYPE, c_char_p, c_int
    h = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)(lambda *a: None)
    cdll.LoadLibrary('libasound.so.2').snd_lib_error_set_handler(h)
    print("[1] ALSA suppression OK")
except Exception as e:
    print(f"[1] ALSA suppression: {e}")

# 2. Redirez stderr (per nascondere messaggi Jack)
devnull = open(os.devnull, 'w')
old_fd = os.dup(2)
os.dup2(devnull.fileno(), 2)
try:
    import pyaudio
    os.dup2(old_fd, 2)
    devnull.close()
    print("[2] PyAudio import OK")
except Exception as e:
    os.dup2(old_fd, 2)
    devnull.close()
    print(f"[2] PyAudio import FAIL: {e}")
    sys.exit(1)

# 3. Elenca dispositivi
try:
    pa = pyaudio.PyAudio()
    print(f"\n[3] Capture devices trovati:")
    found_idx = None
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d['maxInputChannels'] > 0:
            marker = " <-- ReSpeaker" if "ReSpeaker" in d['name'] else ""
            print(f"    [{i}] {d['name']}  ch={int(d['maxInputChannels'])}  sr={int(d['defaultSampleRate'])}{marker}")
            if "ReSpeaker" in d['name'] and found_idx is None:
                found_idx = i
    pa.terminate()
    print(f"\n    Indice scelto: {found_idx}")
except Exception as e:
    print(f"[3] PyAudio list FAIL: {e}")
    sys.exit(1)

# 4. Apertura stream
print("\n[4] Test apertura stream 16kHz stereo...")
q_ok = False
try:
    import queue, numpy as np
    q = queue.Queue()
    def cb(in_data, frame_count, time_info, status):
        q.put(np.frombuffer(in_data, dtype=np.int16).copy())
        return (None, pyaudio.paContinue)

    devnull2 = open(os.devnull, 'w')
    old_fd2 = os.dup(2)
    os.dup2(devnull2.fileno(), 2)
    pa = pyaudio.PyAudio()
    stream = pa.open(rate=16000, channels=2, format=pyaudio.paInt16,
                     input=True, input_device_index=found_idx,
                     frames_per_buffer=512, stream_callback=cb)
    os.dup2(old_fd2, 2)
    devnull2.close()

    print("    Stream aperto — campionando 2 secondi...")
    time.sleep(2)
    frames = 0
    rms_vals = []
    while not q.empty():
        raw = q.get()
        left = raw[::2].astype(np.float32)
        rms_vals.append(float(np.sqrt(np.mean(left**2))))
        frames += 1

    stream.stop_stream()
    stream.close()
    pa.terminate()

    if rms_vals:
        avg_rms = sum(rms_vals) / len(rms_vals)
        peak    = max(rms_vals)
        print(f"    Frame ricevuti : {frames}")
        print(f"    RMS medio      : {avg_rms:.1f}")
        print(f"    RMS picco      : {peak:.1f}")
        if avg_rms > 10:
            print("    [OK] Segnale audio presente!")
        else:
            print("    [WARN] Segnale quasi zero — guadagno necessario")
        q_ok = True
    else:
        print("    [FAIL] Nessun frame ricevuto!")

except Exception as e:
    print(f"    [FAIL] {e}")

# 5. Test seriale
print("\n[5] Test seriale /dev/ttyACM0...")
try:
    import serial
    ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1, write_timeout=0.5,
                        rtscts=False, dsrdtr=False)
    ser.dtr = True; ser.rts = False
    time.sleep(0.1)
    ser.reset_input_buffer()
    ser.write(b'PING\n'); ser.flush()
    resp = ser.readline().decode('utf-8', errors='replace').strip()
    print(f"    PING -> '{resp}'")
    ser.write(b'LED_EFFECT:THINKING\n'); ser.flush()
    resp2 = ser.readline().decode('utf-8', errors='replace').strip()
    print(f"    LED  -> '{resp2}'")
    ser.close()
except serial.SerialTimeoutException:
    print("    TIMEOUT: ESP32 buffer pieno (firmware potrebbe essere bloccato sull'I2C)")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n=== Fine Probe ===")
print(f"Audio OK: {q_ok}")
