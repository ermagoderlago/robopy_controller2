#!/usr/bin/env python3
"""
test_respeaker.py — Marcus Calibration Tool v3.0
=================================================
Testa il microfono ReSpeaker Lite, visualizza il VU meter
colorato in tempo reale e salva l'audio in /tmp/tuning_marcus.wav.

Il controllo LED/DSP via seriale è un bonus opzionale: se l'ESP32
non risponde, lo script funziona comunque per l'audio.

Uso:
    python3 test_respeaker.py [--port /dev/ttyACM0] [--gain 30]
"""

import sys, os, time, threading, argparse, queue, wave, struct

# ── Soppressione errori ALSA/Jack su stderr ─────────────────────────────────
import ctypes
try:
    _handler = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
    ctypes.cdll.LoadLibrary('libasound.so.2') \
               .snd_lib_error_set_handler(_handler(lambda *a: None))
except Exception:
    pass
# Redireziona stderr di libjack verso /dev/null prima del import pyaudio
import subprocess
_devnull = open(os.devnull, 'w')
_old_stderr_fd = os.dup(2)
os.dup2(_devnull.fileno(), 2)
import pyaudio
os.dup2(_old_stderr_fd, 2)   # ripristina stderr
_devnull.close()
# ────────────────────────────────────────────────────────────────────────────

import numpy as np
import pvporcupine

# Colori terminale ANSI
G  = '\033[92m'   # verde
Y  = '\033[93m'   # giallo
R  = '\033[91m'   # rosso
C  = '\033[96m'   # ciano
B  = '\033[1m'    # bold
DIM= '\033[2m'    # dim
RS = '\033[0m'    # reset

# ── Configurazione Audio ─────────────────────────────────────────────────────
SAMPLE_RATE = 16000
CHANNELS    = 2
CHUNK       = 512          # = Porcupine frame length
RECORD_PATH = '/tmp/tuning_marcus.wav'
ALSA_CARD   = 'hw:0,0'    # ReSpeaker Lite

# ── Soglie VU meter ──────────────────────────────────────────────────────────
RMS_LOW     = 100          # sotto: silenzio (grigio)
RMS_OK_LO   = 800          # zona verde (segnale buono)
RMS_OK_HI   = 18000        # limite verde
RMS_WARN    = 25000        # giallo → forte
RMS_CLIP    = 30000        # rosso  → clipping


def _load_keys(path='/mnt/ssd/robopy_controller_host/setup_keys.sh'):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip().replace('export ', '')
            if '=' in line:
                k, v = line.split('=', 1)
                if k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")


def _init_porcupine():
    _load_keys()
    ppn = '/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/wake_word/marcus.ppn'
    pv  = '/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/wake_word/porcupine_params_it.pv'
    key = os.environ.get('PICOVOICE_API_KEY', '')
    try:
        if os.path.exists(ppn):
            p = pvporcupine.create(access_key=key, keyword_paths=[ppn],
                                   model_path=pv, sensitivities=[0.95])
        else:
            p = pvporcupine.create(access_key=key, keywords=['porcupine'])
        print(f'{G}Porcupine OK{RS}')
        return p
    except Exception as e:
        print(f'{Y}Porcupine: {e}{RS}')
        return None


class SerialProxy:
    """Gestisce la seriale in modo silenzioso e non-bloccante."""
    def __init__(self, port, baud=115200):
        self.port   = port
        self.baud   = baud
        self.online = False
        self._ser   = None
        self._lock  = threading.Lock()
        self._try_connect()

    def _try_connect(self):
        try:
            import serial
            s = serial.Serial(self.port, self.baud,
                              timeout=0.1, write_timeout=0.2,
                              rtscts=False, dsrdtr=False)
            # flush anything stuck in the ESP32 buffer
            for _ in range(5):
                s.write(b'\n')
                s.flush()
                time.sleep(0.05)
            s.reset_input_buffer()
            self._ser   = s
            self.online = True
        except Exception:
            self.online = False

    def send(self, cmd):
        """Invia il comando; non lancia mai eccezioni."""
        if not self.online or self._ser is None:
            return False
        with self._lock:
            try:
                self._ser.write(f'{cmd}\n'.encode())
                self._ser.flush()
                return True
            except Exception:
                self.online = False
                return False

    def sync_dsp(self, aec, agc, ns):
        self.send(f'DSP_AEC:{1 if aec else 0}')
        self.send(f'DSP_AGC:{1 if agc else 0}')
        self.send(f'DSP_NS:{1 if ns else 0}')

    def close(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass


class MarcusCalibrator:
    def __init__(self, port, gain, baud=115200):
        self.gain    = float(gain)
        self.dsp_aec = True
        self.dsp_agc = True
        self.dsp_ns  = True
        self.running = True
        self.match_count = 0
        self.last_status = ''

        self._q   = queue.Queue(maxsize=50)
        self._ser = SerialProxy(port, baud)
        self._porc = _init_porcupine()

        status_ser   = f'{G}ONLINE{RS}'  if self._ser.online  else f'{Y}OFFLINE{RS}'
        status_porc  = f'{G}ONLINE{RS}'  if self._porc        else f'{Y}NO KEY{RS}'
        print(f'Serial:    {status_ser}   ({port})')
        print(f'Porcupine: {status_porc}')
        print(f'Recording: {G}{RECORD_PATH}{RS}')

        if self._ser.online:
            self._ser.sync_dsp(self.dsp_aec, self.dsp_agc, self.dsp_ns)

        # Wave writer
        self._wf = wave.open(RECORD_PATH, 'wb')
        self._wf.setnchannels(1)
        self._wf.setsampwidth(2)
        self._wf.setframerate(SAMPLE_RATE)

    # ── Audio callback (eseguito da PyAudio in un thread separato) ───────────
    def _audio_cb(self, in_data, frame_count, time_info, status):
        raw = np.frombuffer(in_data, dtype=np.int16)
        # Isoliamo il canale Left (indici pari)
        left = raw[::2].astype(np.float32)
        boosted = np.clip(left * self.gain, -32768, 32767).astype(np.int16)
        self._wf.writeframes(boosted.tobytes())
        try:
            self._q.put_nowait(boosted)
        except queue.Full:
            pass
        return (None, pyaudio.paContinue)

    # ── VU meter helper ───────────────────────────────────────────────────────
    def _vu(self, rms, width=40):
        n = int(min(width, rms / (RMS_CLIP / width)))
        if rms < RMS_LOW:
            color, label = DIM, 'SILENZIO'
        elif rms < RMS_OK_LO:
            color, label = DIM+G, 'BASSO   '
        elif rms < RMS_OK_HI:
            color, label = G,     'OK      '
        elif rms < RMS_WARN:
            color, label = Y,     'FORTE   '
        else:
            color, label = R,     'CLIPPING'
        bar = color + '█' * n + RS + '░' * (width - n)
        return bar, label

    # ── Loop principale ───────────────────────────────────────────────────────
    def run(self):
        pa = pyaudio.PyAudio()

        # Trova il dispositivo ReSpeaker
        dev_idx = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if 'ReSpeaker' in info['name'] or info['name'].startswith('hw:0'):
                dev_idx = i
                break

        try:
            stream = pa.open(
                rate=SAMPLE_RATE, channels=CHANNELS,
                format=pyaudio.paInt16, input=True,
                input_device_index=dev_idx,
                frames_per_buffer=CHUNK,
                stream_callback=self._audio_cb
            )
            print(f'{G}Audio stream OK{RS}')
        except Exception as e:
            print(f'{R}Audio Error: {e}{RS}')
            print(f'{Y}Verifica che ROS sia fermato (tmux kill-session -t marcus){RS}')
            pa.terminate()
            return

        self._print_header()
        stream.start_stream()

        try:
            while self.running:
                try:
                    frame = self._q.get(timeout=0.15)
                except queue.Empty:
                    continue

                # Rilevamento Marcus
                if self._porc:
                    try:
                        if self._porc.process(frame.tolist()) >= 0:
                            self.match_count += 1
                            self.last_status = f'{G}{B}>>> MARCUS! <<<{RS}'
                            self._ser.send('LED_EFFECT:LISTENING')
                    except Exception:
                        pass

                # Calcola RMS
                rms  = float(np.sqrt(np.mean(frame.astype(np.float64)**2)))
                bar, label = self._vu(rms)
                ser_status = f'{G}●{RS}' if self._ser.online else f'{Y}○{RS}'
                aec_s = f'AEC:{"▲" if self.dsp_aec else "▼"}'
                agc_s = f'AGC:{"▲" if self.dsp_agc else "▼"}'
                ns_s  = f'NS:{"▲" if self.dsp_ns  else "▼"}'

                line = (f'\r{C}RMS:{rms:5.0f}{RS} [{bar}] {label}  '
                        f'{B}Gain:{self.gain:.0f}x{RS}  '
                        f'{aec_s} {agc_s} {ns_s}  '
                        f'SER:{ser_status}  '
                        f'Hits:{self.match_count}  '
                        f'{self.last_status}')
                sys.stdout.write(line + '    ')
                sys.stdout.flush()
                if self.last_status:
                    time.sleep(0.5)
                    self.last_status = ''

        except KeyboardInterrupt:
            pass
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
            self._wf.close()
            if self._porc:
                self._porc.delete()
            self._ser.close()
            print(f'\n\n{B}Fine Calibrazione{RS}')
            print(f'  Hits Marcus : {self.match_count}')
            print(f'  Audio file  : {G}{RECORD_PATH}{RS}')
            print(f'  Riascolta   : aplay {RECORD_PATH}')

    def _print_header(self):
        print(f'\n{B}{"═"*70}{RS}')
        print(f'{B}  Marcus Calibration Tool v3.0{RS}')
        print(f'{"═"*70}')
        print(f'  {G}+{RS}/{G}={RS} Gain +1   {R}-{RS} Gain -1')
        print(f'  a  Toggle AEC      g  Toggle AGC      n  Toggle NS')
        print(f'  l  LED THINKING    k  LED IDLE         q  QUIT')
        print(f'{"─"*70}')
        print(f'  Zone VU: {DIM}▒ SILENZIO{RS}  {G}▓ OK{RS}  {Y}▓ FORTE{RS}  {R}▓ CLIPPING{RS}')
        print(f'{"═"*70}\n')

    # ── Input keyboard (thread separato) ─────────────────────────────────────
    def input_loop(self):
        import termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while self.running:
                ch = sys.stdin.read(1)
                if ch == 'q':
                    self.running = False
                elif ch in ('+', '='):
                    self.gain += 1.0
                elif ch == '-':
                    self.gain = max(1.0, self.gain - 1.0)
                elif ch == 'a':
                    self.dsp_aec = not self.dsp_aec
                    self._ser.sync_dsp(self.dsp_aec, self.dsp_agc, self.dsp_ns)
                elif ch == 'g':
                    self.dsp_agc = not self.dsp_agc
                    self._ser.sync_dsp(self.dsp_aec, self.dsp_agc, self.dsp_ns)
                elif ch == 'n':
                    self.dsp_ns = not self.dsp_ns
                    self._ser.sync_dsp(self.dsp_aec, self.dsp_agc, self.dsp_ns)
                elif ch == 'l':
                    self._ser.send('LED_EFFECT:THINKING')
                elif ch == 'k':
                    self._ser.send('LED_EFFECT:IDLE')
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='/dev/ttyACM0')
    ap.add_argument('--gain', type=float, default=30.0)
    args = ap.parse_args()

    cal = MarcusCalibrator(args.port, args.gain)
    th = threading.Thread(target=cal.input_loop, daemon=True)
    th.start()
    cal.run()


if __name__ == '__main__':
    main()
