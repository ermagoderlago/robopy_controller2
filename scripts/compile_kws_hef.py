#!/usr/bin/env python3
"""
Script di Compilazione HEF per Keyword Spotting su Hailo-10H NPU.
Prende in ingresso un modello ONNX (Conv1D / KWS) e genera il file HEF quantizzato int8.
"""

import sys
import os

def compile_onnx_to_hef(onnx_path, output_hef_path):
    print(f"🔧 [HailoCompiler] Avvio compilazione ONNX ➔ HEF per NPU Hailo-10H...")
    print(f"  Input ONNX:  {onnx_path}")
    print(f"  Output HEF:  {output_hef_path}")
    
    try:
        from hailo_sdk_client import ClientRunner
        
        runner = ClientRunner(hw_arch="hailo10h")
        hn, npz = runner.translate_onnx_model(onnx_path, model_name="marcus_kws")
        
        # Quantizzazione int8
        runner.optimize_full_precision()
        
        # Compilazione target HEF
        hef_data = runner.compile()
        
        with open(output_hef_path, "wb") as f:
            f.write(hef_data)
            
        print(f"✅ [HailoCompiler] Compilazione completata con successo: {output_hef_path}")
    except ImportError:
        print("⚠️ [HailoCompiler] Hailo Dataflow Compiler (hailo_sdk_client) non presente in questo ambiente Host Windows.")
        print("💡 Per la compilazione, eseguire questo script all'interno della shell Hailo Software Suite su Linux/RPi5.")
    except Exception as e:
        print(f"❌ [HailoCompiler] Errore durante la compilazione HEF: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        compile_onnx_to_hef(sys.argv[1], sys.argv[2])
    else:
        print("Uso: python compile_kws_hef.py <input.onnx> <output.hef>")
