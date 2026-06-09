#!/usr/bin/env python3
"""
Test Hailo-10H NPU Driver and Inference
=======================================
Direct hardware diagnostics script to verify PCIe connection,
HailoRT Python API availability, HEF model loading, and context creation.

Use this immediately after mounting the physical AI HAT+.

Version: 01.00.00
"""

import os
import sys
import time

try:
    import hailo_platform
    from hailo_platform import HEF, VDevice, ConfigureParams, FormatType
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False


def main():
    print("======================================================")
    print(" 🧠 HAILO-10H NPU HARDWARE DIAGNOSTICS")
    print("======================================================")

    # 1. Check Python API
    if not HAILO_AVAILABLE:
        print("❌ ERRORE: Libreria 'hailo_platform' non trovata nel PYTHONPATH.")
        print("   Assicurati di aver attivato l'ambiente di sistema corretto")
        print("   o installato gli user space packages di HailoRT.")
        sys.exit(1)
    
    print(f"✅ Libreria 'hailo_platform' rilevata (Versione SDK: {hailo_platform.__version__})")

    # 2. Check PCIe Device Connection
    print("\n🔍 Rilevamento hardware PCIe in corso...")
    try:
        devices = VDevice.detect()
        if not devices:
            print("❌ ERRORE: Nessun dispositivo NPU Hailo rilevato sul bus PCIe.")
            print("   Verifiche suggerite:")
            print("   - Esegui 'lspci' per verificare se la scheda è accoppiata al bus.")
            print("   - Controlla i log di sistema: 'dmesg | grep -i hailo'.")
            print("   - Assicurati che 'dtparam=pciex1' sia abilitato in config.txt.")
            sys.exit(1)
        
        print(f"✅ Dispositivi Hailo PCIe rilevati: {len(devices)} dispositivo/i")
        for idx, dev in enumerate(devices):
            print(f"   [{idx}] ID Dispositivo: {dev}")
    except Exception as e:
        print(f"❌ ERRORE durante il rilevamento dei dispositivi: {e}")
        sys.exit(1)

    # 3. Create VDevice Context
    print("\n⚡ Inizializzazione contesto virtuale VDevice...")
    try:
        with VDevice() as target_device:
            print("✅ Contesto VDevice creato con successo.")
            
            # 4. Optional HEF load check if path provided
            if len(sys.argv) > 1:
                hef_path = sys.argv[1]
                print(f"\n📂 Caricamento del modello HEF: {hef_path} ...")
                if not os.path.exists(hef_path):
                    print(f"❌ ERRORE: File HEF non trovato a '{hef_path}'")
                    sys.exit(1)
                
                start_time = time.time()
                hef = HEF(hef_path)
                configure_params = target_device.create_configure_params(hef)
                network_group = target_device.configure(hef, configure_params)[0]
                
                print(f"✅ Modello caricato e configurato in {time.time() - start_time:.3f} secondi.")
                print(f"   Gruppo di rete configurato: {network_group.name}")
                
                # Print input/output streams details
                print("\n   [Streams di Input]:")
                for stream_info in hef.get_input_stream_infos():
                    print(f"     - {stream_info.name}: Shape {stream_info.shape}, Formato {stream_info.format.type}")
                    
                print("   [Streams di Output]:")
                for stream_info in hef.get_output_stream_infos():
                    print(f"     - {stream_info.name}: Shape {stream_info.shape}, Formato {stream_info.format.type}")
            else:
                print("\n💡 Suggerimento: Passa il percorso di un file .hef come argomento")
                print("    per testare il caricamento del modello sull'NPU.")
                print("    Esempio: python3 test_npu_inference.py /percorso/modello.hef")
                
    except Exception as e:
        print(f"❌ ERRORE durante l'inizializzazione o l'utilizzo del dispositivo: {e}")
        sys.exit(1)

    print("\n======================================================")
    print(" 🎉 DIAGNOSTICA COMPLETATA CON SUCCESSO!")
    print("======================================================")


if __name__ == '__main__':
    main()
