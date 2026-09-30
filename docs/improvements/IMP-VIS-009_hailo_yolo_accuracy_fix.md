# Progetto di Miglioramento: Risoluzione Falsi Riconoscimenti YOLOv8 su Hailo-10H (IMP-VIS-009)

## 1. Identificazione e Classificazione
- **ID Progetto:** `IMP-VIS-009`
- **Failure Mode Correlato (DFMEA):** `FM-VIS-009`
- **Ambito Tecnologico:** Computer Vision, OAK-D Lite, Hailo-10H NPU, Mappatura Semantica Nav2
- **Data Apertura:** 2026-09-30
- **Stato:** `IN_PROGRESS`
- **Severità Iniziale (DFMEA):** Severity: 7, Occurrence: 8, Detection: 3 $\implies$ **RPN: 168**

---

## 2. Descrizione del Guasto e Impatto Sistemico
Il modulo di visione neurale su Hailo-10H (`hailo_bridge_node_cpp`) rileva correttamente gli oggetti e ne pubblica la semantica su `/hailo/semantic_objects`. Tuttavia, scambia frequentemente arredi orizzontali ingombranti (in particolare divani, sedie lunghe, letti) per la classe `persona` (`person`).
Questo errore sistematico provoca:
1. Iniezione di ostacoli dinamici non deperibili nella costmap locale/globale di Nav2 con ingombro non compatibile con l'arredo reale.
2. Attivazione errata del modulo di prossemica `engagement_monitor` che tenta di ingaggiare l'arredo con interazioni vocali VUI.
3. Rallentamenti o manovre di recovery ingiustificate durante la navigazione autonoma in ambienti domestici.

---

## 3. Analisi delle Cause Radice (5-Whys)
1. **Perché rileva persona invece di divano?**
   Perché i logit di classificazione di YOLOv8 favoriscono la classe `persona` (prior statistico dominante) rispetto alla classe `divano` (`couch`).
2. **Perché i logit sono confusi e rumorosi?**
   Perché il modello HEF è stato quantizzato INT8 con `hailo optimize --use-random-calib-set` (calibrazione sintetica su rumore casuale uniforme anziché immagini reali) a livello di ottimizzazione 0.
3. **Perché l'immagine in ingresso favorisce l'ambiguità?**
   Perché l'immagine passata alla NPU è monocromatica (grayscale estrapolata da `monoLeft` e convertita in `bgr8`), privando i layer convoluzionali di qualsiasi informazione cromatica (pelle, vestiti vs stoffa/pelle del divano).
4. **Perché la geometria dell'oggetto risulta compatibile con una persona?**
   Perché il ridimensionamento a 640x640 viene fatto con `cv::resize` diretto senza letterbox, stirando verticalmente il fotogramma 640x400 del 160% e trasformando le proporzioni orizzontali del divano in proporzioni verticali compatibili con una sagoma umana.
5. **Perché questi rilevamenti spuri non vengono scartati?**
   Perché la soglia di confidenza a runtime era stata impostata a 0.35 invece del valore nominale di `SPEC-03` ($\ge 0.55$).

---

## 4. Azioni Correttive & Piano d'Implementazione

### 4.1 Correzione Preprocessing C++ (Letterbox 1:1)
Sostituire in `src/hailo_bridge_node.cpp` lo stretch anamorfico con letterboxing standard a fattore di scala isotropo e padding simmetrico con valore pixel 114. Ricalcolare le coordinate inverse dei bounding box per preservare l'allineamento geometrico con la fotocamera.

### 4.2 Innalzamento Soglia di Confidenza (Allineamento SPEC-03)
Riportare `conf_thresh` al valore nominale di specifica (`0.55f`). Inserire inoltre un filtro di aspect ratio per la classe `persona` ($W/H \le 1.8$).

### 4.3 Ripristino Stream RGB Nativo (OAK-D Lite)
Configurare in `src/fast_flow_vo_node.cpp` l'acquisizione nativa dalla `ColorCamera` dell'OAK-D Lite a 640x400 BGR, mantenendo contemporaneamente lo stereo depth per RTAB-Map su bus USB 3.0.

### 4.4 Ricompilazione HEF con Calibrazione Reale COCO (WSL 2)
Generare un dataset di calibrazione di 256 campioni reali COCO e ricompilare `marcus_unified.hef` includendo le direttive di normalizzazione `.alls`.

---

## 5. RPN Target e Criteri di Accettazione

- **Punteggio Residuo Target (DFMEA):** Severity: 7, Occurrence: 2, Detection: 2 $\implies$ **RPN Target: 28** (-83% di rischio).
- **Criteri di Accettazione:**
  - Nessun rilevamento spurio di tipo `persona` in presenza di divani e tavoli in ambienti di test controllati.
  - Zero incremento del carico computazionale su CPU Core 0-1.
  - Latenza di inferenza NPU invariata ($<25\text{ ms}$).
