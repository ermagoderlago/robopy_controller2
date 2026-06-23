# Lezioni Apprese - Visione & Calcolo NPU (Hailo)

Questo documento descrive le lezioni apprese su OAK-D Lite, l'acceleratore NPU Hailo-10H, la fusione semantica 3D e i relativi driver C++ e Python di Marcus.

---

## 🔌 Hardware e Connettività USB (X_LINK_ERROR)

### Saturazione del Bus USB 2.0
* **Problema:** La camera OAK-D Lite si disconnette bruscamente dopo 60-90 secondi di streaming, entrando in crash loop con l'errore:
  `Couldn't read data from stream: 'rect' (X_LINK_ERROR)`
* **Causa:** Il kernel rileva la camera su bus USB "High-Speed" (USB 2.0 a 480 Mbps). Il flusso RGB compresso + profondità raw + features SuperPoint satura completamente il bus, causando la perdita di pacchetti di controllo. DepthAI interpreta il ping mancato come disconnessione hardware.
* **Risoluzione:**
  1. Collegare la fotocamera esclusivamente a una **porta USB 3.0 (blu)** del Raspberry Pi 5 o dell'Hub USB alimentato.
  2. Verificare che il cavo utilizzato supporti la larghezza di banda USB 3.0 (i cavi standard per sola ricarica degradano la connessione a USB 2.0).
  3. Controllare tramite `dmesg` che venga stampato `SuperSpeed USB device`.

### Caduta di Tensione e Reset dell'SSD
* **Regola Permanente:** All'avvio simultaneo di fotocamera, NPU ed array microfonico, il picco di assorbimento manda in sottotensione le porte USB del Pi 5, provocando il reset dell'SSD host (`reset SuperSpeed USB device`) e bloccando il filesystem in sola lettura. Utilizzare sempre un **Hub USB alimentato esternamente** per camera ed SSD.

---

## 🧠 Compilazione Modelli NPU (Hailo HEF)

### Limitazioni Hardware Hailo-10H e API InferModel
* **API Legacy:** Le API legacy `VStream` e il comando `ConfigureParams.create_from_hef` non sono supportati su Hailo-10H e sollevano l'errore `HAILO_NOT_IMPLEMENTED`.
* **Risoluzione:** Riscrivere i nodi per utilizzare le moderne API `InferModel` tramite `VDevice.create_infer_model(hef_path)`.
* **Crash di Fallback Silenzioso:** Assicurarsi che nel modulo di importazione non ci siano classi obsolete o mancanti (es. `InferVStream` rimosso nelle nuove versioni SDK) che sollevino `ImportError` silenziati nei blocchi `try...except`, inducendo il nodo a ricadere nella simulazione software (`sim_mode:=True`).

### Unificazione dei Contesti (Joined HEF)
* **Contesto:** Eseguire reti multiple su Hailo-10H richiede un file HEF unificato per evitare contese hardware e tempi di caricamento alternati sul bus PCIe.
* **Join dei Modelli:** Si esegue unendo i modelli nativi in formato `.har` tramite `hailo join`, e successivamente compilando l'intero pacchetto tramite `hailo optimize` e `hailo compiler`.
* **Suddivisione Host-NPU (Esempio NetVLAD):** Il compilatore Hailo fallisce la traduzione dell'intero modello NetVLAD a causa dei nodi di pooling (reshape 4D➔3D e normalizzazioni). La soluzione ottimale è compilare su NPU il solo backbone di estrazione (MobileNetV2 + reducer 1x1) e calcolare il pooling NetVLAD sulla CPU host tramite NumPy/C++ (<1ms).

---

## 📐 C++ Semantic Mapper e Depth image Encoding

### Incompatibilità Encoding Profondità
* **Problema:** Il nodo standard `depthimage_to_laserscan` (che genera `/scan` per Nav2) rifiuta lo stream di profondità C++ della fotocamera.
* **Causa:** Il driver C++ pubblicava la profondità come `"mono16"`, mentre il nodo richiede specificamente `"16UC1"` o `"32FC1"`.
* **Risoluzione:** Modificare il driver C++ per forzare l'encoding `"16UC1"`.

### C++ Core Pinning e Ottimizzazione
* **Core Pinning:** `hailo_bridge_node` e `marcus_semantic_mapper_cpp` devono essere vincolati ai Core CPU 2 e 3 del Raspberry Pi 5 per isolare il calcolo intensivo dai thread di I/O.
* **Eigen vs PCL:** Per ottimizzare i tempi di calcolo, la back-projection geometrica C++ del mapper deve utilizzare matrici Eigen pure ed intrinseci camera pre-allocati, evitando l'overhead e le runtime allocations della libreria PCL.
