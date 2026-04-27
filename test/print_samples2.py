import wave
import struct

def check(wav_file):
    f = wave.open(wav_file, 'rb')
    n = f.getnframes()
    data = f.readframes(n)
    samples = struct.unpack('<' + str(n) + 'h', data)
    
    unique_samples = set(samples)
    print(f"[{wav_file}] Valori unici: {len(unique_samples)}")
    print(f"[{wav_file}] Max: {max(samples)}, Min: {min(samples)}")
    
    for i, s in enumerate(samples):
        if s != -1 and s != 0:
            print(f"[{wav_file}] Primo campione non banale all'indice {i}: {samples[i:i+20]}")
            break
            
check('/tmp/respeaker_CH1.wav')
check('/tmp/respeaker_CH2.wav')
