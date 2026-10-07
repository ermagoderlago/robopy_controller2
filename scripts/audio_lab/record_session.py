import os
import time
import json
import wave
import argparse
from datetime import datetime

# Optional PyAudio for capturing
try:
    import pyaudio
except ImportError:
    pyaudio = None

def get_corpus_files(corpus_dir):
    files = {}
    if os.path.exists(corpus_dir):
        for f in os.listdir(corpus_dir):
            if f.endswith('.txt'):
                files[f] = []
                with open(os.path.join(corpus_dir, f), 'r', encoding='utf-8') as file:
                    for line in file:
                        line = line.strip()
                        if line:
                            files[f].append(line)
    return files

def record_audio(output_path, duration_sec, sample_rate=16000, channels=2):
    if not pyaudio:
        print("PyAudio non disponibile, impossibile registrare.")
        return
        
    p = pyaudio.PyAudio()
    # Trova il ReSpeaker (o usa default)
    device_index = None
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if "ReSpeaker" in info.get("name", ""):
            device_index = i
            break
            
    print(f"🎤 Registrazione in corso ({duration_sec}s)...")
    stream = p.open(format=pyaudio.paInt16,
                    channels=channels,
                    rate=sample_rate,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=1024)
                    
    frames = []
    for _ in range(0, int(sample_rate / 1024 * duration_sec)):
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)
        
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
        wf.setframerate(sample_rate)
        wf.writeframes(b''.join(frames))
        
    print(f"✅ Salvato: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Registrazione Audio Lab per Marcus")
    parser.add_argument("--matrix", type=str, default="core", help="Matrice da eseguire (core o full)")
    parser.add_argument("--distance", type=str, required=True, help="Distanza (es. 2.0m)")
    parser.add_argument("--angle", type=str, required=True, help="Angolo (es. 0_deg)")
    parser.add_argument("--env", type=str, required=True, help="Condizione (es. silent)")
    args = parser.parse_args()
    
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = f"/mnt/ssd/audio_lab/sessions/{session_id}"
    os.makedirs(out_dir, exist_ok=True)
    
    # Let's fallback to local dir if /mnt/ssd isn't accessible
    if not os.path.exists("/mnt/ssd/audio_lab"):
        out_dir = f"tmp/audio_lab/sessions/{session_id}"
        os.makedirs(out_dir, exist_ok=True)
        
    corpus_dir = "docs/audio_calibration/corpus"
    corpus = get_corpus_files(corpus_dir)
    
    if not corpus:
        print(f"Nessun file corpus trovato in {corpus_dir}")
        return
        
    print(f"\n🚀 Inizio Sessione di Calibrazione: {session_id}")
    print(f"Distanza: {args.distance} | Angolo: {args.angle} | Env: {args.env}")
    print(f"Salvataggio in: {out_dir}\n")
    
    metadata = []
    
    for filename, lines in corpus.items():
        # Se matrix core, saltiamo alcuni file
        if args.matrix == "core" and filename not in ["01_wake.txt", "02_comandi_brevi.txt", "03_domande_medie.txt", "04_frasi_lunghe.txt"]:
            continue
            
        print(f"\n--- Preparazione per {filename} ---")
        input("Premi INVIO per iniziare le frasi di questo file...")
        
        for idx, line in enumerate(lines):
            # Parse ID and text
            parts = line.split(":", 1)
            if len(parts) == 2:
                utt_id, text = parts[0].strip(), parts[1].strip()
            else:
                utt_id, text = f"UNK-{idx}", line
                
            print(f"\n[LEGGI AD ALTA VOCE]: {text}")
            
            # Stima durata
            duration = max(3, int(len(text) / 10) + 1)
            
            # Wait for user
            input("Premi INVIO e inizia a parlare...")
            
            wav_filename = f"{utt_id}_{args.distance}_{args.angle}_{args.env}.wav"
            wav_path = os.path.join(out_dir, wav_filename)
            
            record_audio(wav_path, duration)
            
            metadata.append({
                "id": utt_id,
                "text": text,
                "distance": args.distance,
                "angle": args.angle,
                "env": args.env,
                "file": wav_filename
            })
            
    # Salva metadati
    meta_path = os.path.join(out_dir, "metadata.json")
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)
        
    print(f"\n🎉 Sessione completata! Dati e metadati salvati in {out_dir}")

if __name__ == "__main__":
    main()
