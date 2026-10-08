# IMP-VIS-004: Stabilizzazione Alimentazione Drone-Grade e Connessione USB 3.0 SuperSpeed Reale OAK-D Lite

## 1. Failure Mode Reference
- **ID:** `FM-VIS-004`
- **Subsystem:** Vision / Hardware Power (`OAK-D Lite / Bus USB 3.0`)
- **Failure:** Fallback degradato a USB 2.0 High-Speed (480 Mbps) con saturazione della banda passante e crash loop `Couldn't read data from stream: 'rect' (X_LINK_ERROR)`.
- **Cause:** Ripple e caduta di tensione (voltage sag) sul rail 5V USB del Raspberry Pi 5 durante i picchi transitori di assorbimento ($di/dt$) di Hailo-10H NPU, SSD NVMe e motori, con conseguente fallimento del link training SuperSpeed del transceiver USB 3.0 dell'OAK-D Lite.
- **Initial RPN:** 384 (Severity: 8, Occurrence: 8, Detection: 6)

---

## 2. Solution Architecture & Hardware Mitigation
1. **Modulo di Alimentazione Step-Down / BEC di Derivazione Droni:**
   - Installato modulo step-down switching sincrono ad altissima efficienza e reiezione di ripple (<20mV), derivato da componentistica heavy-duty per droni e FPV (capacità 5V/6V @ 5A-10A continui, condensatori a bassissima ESR).
   - Tensione erogata stabilizzata permanentemente a **5.25V**, compensando le cadute resistive dei cablaggi anche durante picchi di carico massimo simultaneo (NPU Hailo a 40 TOPS + CPU Cortex-A76 + motori + lidar).

2. **Negoziazione e Mantenimento USB 3.0 SuperSpeed Reale (5 Gbps):**
   - Con l'alimentazione stabile, il transceiver Cypress USB 3.0 dell'OAK-D Lite aggancia e mantiene stabilmente la velocità **SuperSpeed (5000M)**, confermata da dmesg del kernel Linux:
     `New USB device found, idVendor=03e7, idProduct=2485, bcdDevice= 0.02, SuperSpeed USB device`.
   - **Throughput Disponibile:** ~400-450 MB/s reali (oltre 10 volte superiore al limite pratico di ~35 MB/s di USB 2.0).

3. **Decoupling Neurale (Fase F1):**
   - Tutte le reti neurali (YOLOv8, SuperPoint, Pose Tracking) sono state rimosse dalla VPU MyriadX dell'OAK-D Lite e migrate sull'NPU Hailo-10H (`hailo_bridge_node_cpp`).
   - Il bus USB 3.0 trasporta ora unicamente:
     * Stereo Depth 16UC1 (400p @ 20-30 fps): ~15-25 MB/s
     * RGB preview (320x320/400p): ~10-15 MB/s
     * IMU packet stream (50-200 Hz): <0.1 MB/s
   - **Banda totale utilizzata:** ~30-40 MB/s su 450 MB/s disponibili (**Headroom di banda > 88%**).
   - Azzeramento totale dei timeout XLink e scomparsa permanente di `X_LINK_ERROR`.

---

## 3. Residual Risk & RPN Scoring
- **Severity:** 8 (Resta immutata: il crash della camera primaria comporta cecità stereo per Nav2/SLAM).
- **Occurrence:** 1 (Ridotta da 8 a 1: con rail 5.25V stabilizzato dal BEC drone-grade e buffer di banda >88%, il link training SuperSpeed è stabile).
- **Detection:** 2 (Migliorata da 6 a 2: dmesg segnala istantaneamente la velocità 5000M all'avvio e la diagnostica rileva qualsiasi frame drop).
- **Residual RPN:** $8 \times 1 \times 2 = 16$ (Ridotto da 384 a 16, Rischio LOW - Mitigazione CHIUSA).
