import array
import wave
import argparse

def main():
    infile = '/tmp/respeaker_test_raw32.wav'
    print(f"Lettura del file grezzo {infile}...")
    
    try:
        with open(infile, 'rb') as f:
            f.read(44) # salta header originario
            raw_bytes = f.read()
    except FileNotFoundError:
        print(f"Errore: File {infile} non trovato.")
        return
        
    # Leggiamo i dati come int32_t (4 byte per campione I2S)
    int32_array = array.array('i', raw_bytes)
    
    if len(int32_array) == 0:
        print("Il file audio è vuoto.")
        return
        
    # I2S manda: CH1_int32, CH2_int32, CH1_int32, CH2_int32...
    ch1_raw = int32_array[0::2]
    ch2_raw = int32_array[1::2]
    
    # L'audio a 16 bit reale è nei 16 bit più alti (MSW) ignorando l'LSW (che è rumore)
    ch1_16 = array.array('h', [x >> 16 for x in ch1_raw])
    ch2_16 = array.array('h', [x >> 16 for x in ch2_raw])

    # Salviamo i due canali in WAV separati per capire dove sta la voce
    for idx, (ch_name, ch_data) in enumerate([("CH1", ch1_16), ("CH2", ch2_16)]):
        peak = max(abs(s) for s in ch_data) if len(ch_data) else 0
        mean = sum(ch_data)/len(ch_data) if len(ch_data) else 0
        var = sum((s-mean)**2 for s in ch_data)/len(ch_data)
        std = var**0.5
        
        print(f"[{ch_name}] Peak: {peak}, DC: {mean:+.1f}, Vol (StdDev): {std:.1f}")
        
        outfile = f"/tmp/respeaker_{ch_name}.wav"
        with wave.open(outfile, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(ch_data.tobytes())
            
    print("\n✅ Fatto. Puoi ascoltare i due canali separati dal Raspberry Pi con:")
    print("aplay /tmp/respeaker_CH1.wav")
    print("aplay /tmp/respeaker_CH2.wav")

if __name__ == '__main__':
    main()
