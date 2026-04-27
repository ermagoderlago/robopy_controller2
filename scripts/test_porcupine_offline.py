#!/usr/bin/env python3
"""
[v4.3] Diagnosi Finale â€” Caccia al Riconoscimento.
Prova la versione 16kHz Stereo RAW per capire quale canale usare.

Prerequisito: python3 record_marcus.py (salva /tmp/test_marcus_stereo.wav)
"""

import sys
import os
import wave
import audioop
import numpy as np

# --- Configurazione ---
SETUP_KEYS_PATH  = '/mnt/ssd/robopy_controller_host/setup_keys.sh'
KEYWORD_PATH     = '/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/wake_word/marcus.ppn'
MODEL_PATH       = '/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/wake_word/porcupine_params_it.pv'
WAV_FILE         = '/tmp/test_marcus_stereo.wav'
CHUNK_SIZE       = 960


def load_api_key():
    key = os.environ.get('PICOVOICE_API_KEY', '')
    if key: return key
    if os.path.exists(SETUP_KEYS_PATH):
        with open(SETUP_KEYS_PATH, 'r') as f:
            for line in f:
                line = line.strip().replace('export ', '')
                if line.startswith('PICOVOICE_API_KEY='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    return ''


def prepare_audio_variants(wav_path):
    with wave.open(wav_path, 'rb') as wf:
        sr         = wf.getframerate()
        n_channels = wf.getnchannels()
        sw         = wf.getsampwidth()
        raw        = wf.readframes(wf.getnframes())

    print(f"ðŸ“‚ WAV: {n_channels}ch, {sr}Hz, {sw*8}bit")
    
    from scipy.signal import butter, sosfilt, sosfilt_zi
    sos = butter(4, [200, 4000], btype='bandpass', fs=16000, output='sos')
    zi = sosfilt_zi(sos).astype(np.float32)

    audio_all = np.frombuffer(raw, dtype=np.int16)
    variants = {}
    
    if n_channels == 2:
        left = audio_all[::2]
        variants['LEFT']  = left
        variants['RIGHT'] = audio_all[1::2]
        variants['MIX']   = ((audio_all[::2].astype(np.int32) + audio_all[1::2].astype(np.int32)) // 2).astype(np.int16)
        
        # [TEST FILTRO]: Emula fedelmente lo step del VUI Node (senza l'attenuazione /3.0 per ora)
        float_buf, _ = sosfilt(sos, left.astype(np.float32), zi=zi)
        variants['LEFT_FILTERED'] = np.clip(float_buf, -32767, 32767).astype(np.int16)
        
        # [TEST ATTENUAZIONE]: Emula l'attenuazione /3.0 introdotta di recente nel nodo
        variants['LEFT_ATTENUATED'] = (left.astype(np.float32) / 3.0).astype(np.int16)
        
    else:
        variants['MONO']  = audio_all
        
    return variants


def test_variant(porcupine_module, access_key, audio_np, keyword, model, sensitivity=0.95):
    try:
        kwargs = {'access_key': access_key, 'sensitivities': [sensitivity]}
        if keyword: 
            kwargs['keyword_paths'] = [keyword]
            if model: kwargs['model_path'] = model
        else:
            kwargs['keywords'] = ['porcupine']
            
        porcupine = porcupine_module.create(**kwargs)
    except Exception as e:
        return -1, str(e)

    frame_len  = porcupine.frame_length
    detections = 0
    porc_idx   = 0
    total      = len(audio_np)
    pos = 0
    while pos + frame_len <= len(audio_np):
        f = audio_np[pos : pos + frame_len]
        if porcupine.process(f.tolist()) >= 0:
            detections += 1
        pos += frame_len
    
    porcupine.delete()
    return detections, ""


def main():
    print("â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—")
    print("â•‘    Laboratorio Diagnosi Finale â€” Marcus Wake Word       â•‘")
    print("â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n")

    key = load_api_key()
    if not key: print("âŒ API Key non trovata"); return

    if not os.path.exists(WAV_FILE):
        print(f"âŒ File {WAV_FILE} non trovato. Registra prima con record_marcus.py")
        return

    import pvporcupine
    print(f"âœ… Versione Porcupine: {getattr(pvporcupine, 'VERSION', '4.x')}")
    
    variants = prepare_audio_variants(WAV_FILE)

    print(f"\nðŸ”¬{'='*55}")
    print(f"{'VARIANTE':<10} | {'WORD':<12} | {'MODEL':<5} | {'RES'}")
    print(f"{'-'*10}+{'-'*14}+{'-'*7}+{'-'*6}")

    # Test Matrix
    for name, audio in variants.items():
        # 1. Test 'porcupine' (built-in)
        cnt, _ = test_variant(pvporcupine, key, audio, None, None)
        print(f"{name:<10} | porcupine    | ENG   | {cnt if cnt >= 0 else 'ERR'}")
        
        # 2. Test 'Marcus' (ITA)
        cnt, err = test_variant(pvporcupine, key, audio, KEYWORD_PATH, MODEL_PATH)
        print(f"{name:<10} | marcus       | ITA   | {cnt if cnt >= 0 else 'ERR: ' + err[:40]}")

    print(f"{'='*57}")
    print("\nðŸ’¡ INTERPRETAZIONE:")
    print(" - Se 'porcupine' funziona e 'marcus' no: Il file .ppn Ã¨ vecchio/incompatibile.")
    print(" - Se nessuno dei due funziona: L'audio Ã¨ vuoto, distorto o sul canale sbagliato.")


if __name__ == '__main__':
    main()
