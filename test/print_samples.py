import wave
import struct

def check(wav_file):
    f = wave.open(wav_file, 'rb')
    n = f.getnframes()
    data = f.readframes(n)
    samples = struct.unpack('<' + str(n) + 'h', data)
    print(f"[{wav_file}] Primi 20 campioni: {samples[:20]}")
    
check('/tmp/respeaker_CH1.wav')
check('/tmp/respeaker_CH2.wav')
