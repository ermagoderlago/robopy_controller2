import os
import sys
import wave
import json
import argparse
import numpy as np

# Aggiungiamo robopy_controller al path per poter importare dsp e turn manager
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from robopy_controller.robot_ai.audio.dsp_pipeline import DSPPipeline
from robopy_controller.robot_ai.audio.turn_manager import TurnManager

def replay_wav(wav_path, sample_rate=16000):
    if not os.path.exists(wav_path):
        print(f"File non trovato: {wav_path}")
        return None
        
    wf = wave.open(wav_path, 'rb')
    if wf.getframerate() != sample_rate:
        print(f"Warning: sample rate {wf.getframerate()} != {sample_rate}")
        
    n_frames = wf.getnframes()
    audio_bytes = wf.readframes(n_frames)
    wf.close()
    
    # Assumiamo 16-bit mono per la simulazione (il VUI estrae L_CH prima di passare al DSP)
    # Se stereo, estraiamo L_CH
    audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
    if wf.getnchannels() == 2:
        audio_int16 = audio_int16[::2]
        
    return audio_int16

def process_offline(audio_int16, sample_rate=16000):
    """
    Simula l'arrivo dei chunk da 60ms a 16kHz
    Restituisce gli eventi VAD e l'audio che sarebbe stato inviato a Gemini.
    """
    dsp = DSPPipeline(sample_rate=sample_rate, max_gain=4.0)
    # Max silence = 45 frames (900ms)
    turn_mgr = TurnManager(sample_rate=sample_rate, pre_roll_ms=500, max_silence_ms=900)
    
    chunk_size = 960 # 60ms @ 16kHz
    
    events = []
    output_audio = []
    was_speech = False
    
    for i in range(0, len(audio_int16), chunk_size):
        chunk = audio_int16[i:i+chunk_size]
        if len(chunk) < chunk_size:
            break
            
        time_sec = i / sample_rate
        
        # 1. DSP
        processed = dsp.process(chunk)
        
        # 2. VAD
        is_speech = turn_mgr.process_chunk(processed)
        
        if is_speech and not was_speech:
            events.append({"event": "VOICE_START", "time": time_sec})
            # Invia preroll
            output_audio.append(turn_mgr.get_preroll())
            output_audio.append(processed)
            
        elif not is_speech and was_speech:
            events.append({"event": "VOICE_END", "time": time_sec})
            
        elif is_speech:
            output_audio.append(processed)
            
        was_speech = is_speech
        
    if output_audio:
        final_audio = np.concatenate(output_audio)
    else:
        final_audio = np.array([], dtype=np.int16)
        
    return events, final_audio

def main():
    parser = argparse.ArgumentParser(description="Replay offline per audio calibration")
    parser.add_argument("--session-dir", type=str, required=True, help="Path alla directory della sessione (es. /mnt/ssd/audio_lab/sessions/2026...)")
    parser.add_argument("--out-dir", type=str, default="tmp/replay_out", help="Directory dove salvare gli output processati")
    args = parser.parse_args()
    
    if not os.path.exists(args.session_dir):
        print(f"Session dir {args.session_dir} non trovata.")
        return
        
    os.makedirs(args.out_dir, exist_ok=True)
    
    meta_path = os.path.join(args.session_dir, "metadata.json")
    if not os.path.exists(meta_path):
        print(f"Nessun metadata.json trovato in {args.session_dir}")
        return
        
    with open(meta_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
        
    results = []
    
    for item in metadata:
        wav_file = item["file"]
        wav_path = os.path.join(args.session_dir, wav_file)
        
        print(f"\nProcessing {wav_file}...")
        audio = replay_wav(wav_path)
        if audio is None:
            continue
            
        events, processed_audio = process_offline(audio)
        
        print(f"  Eventi: {events}")
        print(f"  Frames estratti: {len(processed_audio)}")
        
        # Salva l'audio processato (quello che Gemini riceverebbe)
        out_wav = os.path.join(args.out_dir, f"proc_{wav_file}")
        with wave.open(out_wav, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(processed_audio.tobytes())
            
        # Determiniamo se è andato a buon fine (almeno un VOICE_START)
        success = any(e['event'] == 'VOICE_START' for e in events)
        
        item_res = {
            "id": item["id"],
            "file": wav_file,
            "events": events,
            "success": success
        }
        results.append(item_res)
        
    res_path = os.path.join(args.out_dir, "replay_results.json")
    with open(res_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
        
    print(f"\n✅ Replay completato. Risultati in {res_path}")

if __name__ == "__main__":
    main()
