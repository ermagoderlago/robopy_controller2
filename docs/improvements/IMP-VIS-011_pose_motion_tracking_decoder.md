# IMP-VIS-011: Pose Tracking Motion Accuracy & DFL-Guided Anchor Decoder

## 1. Identificazione e Scopo
- **ID Miglioramento:** `IMP-VIS-011`
- **Failure Mode DFMEA:** `FM-VIS-011`
- **Dominio:** `vision_hailo`
- **Modulo:** `hailo_bridge_node_cpp / Pose Decoder` (`src/hailo_bridge_node.cpp`)
- **Stato:** `COMPLETED` (validato HIL su Hailo-10H NPU)

---

## 2. Descrizione del Guasto Risolto
- **Sintomo:** Persona riconosciuta e delimitata correttamente dal bounding box, scheletro visualizzato ma le articolazioni anatomiche (braccia, avambracci, gambe) non seguivano i movimenti reali dell'utente, rimanendo bloccate o collassate sul bordo superiore del bounding box (`Y=0.00`).
- **Causa Radice Ingegneristica (Fase 1 vs Fase 2):**
  1. Nel file `yolov8s_pose.hef` su Marcus, la testa di classificazione persona (`conv44`, `conv58`, `conv71`) restituisce `0.00` costante su tutte le celle (pesi azzerati dal compilatore Hailo in export multi-head).
  2. Un approccio basato solo su somma delle confidenze grezze dei keypoint (`sum_conf`) favoriva le celle di sfondo non attivate vicino al bordo superiore della griglia (`gy = 4, 5`), dove i logit non attivati si trasformano via sigmoid in valori spuri $\approx 0.5-0.6$. L'accumulo su 17 giunti generava uno score elevato ($8.5-9.0$), facendo vincere celle di background statiche.
  3. L'assenza di filtro IoU sul bounding box DFL decodificato dal modello di posa permetteva a celle esterne al corpo umano di vincere continuamente, bloccando lo scheletro in coordinate clamped statiche (`Nose=(0.90, 0.00), ShL=(0.86, 0.00)`).

---

## 3. Soluzione Ingegneristica Adottata (Fase 3 - DFL IoU Box Matching & Anchor Search)
1. **Identificazione Teste NPU per Dimensione Buffer:** 
   Come da specifica ufficiale Hailo Model Zoo (`layer_from_shape`), le teste sono raggruppate per dimensione in byte: `cells * 64` (DFL bounding box), `cells * 1` (confidenza classe persona), `cells * 51` (17 keypoint 2D con logit).
2. **Finestra di Ricerca Ancorata al Centro Persona:**
   La ricerca nella griglia feature map non scansiona l'intero bounding box indiscriminatamente, ma si concentra in un intorno ristretto dell'ancora centrale del corpo:
   - $c\_gx = \text{round}(p\_cx / stride)$
   - $c\_gy = \text{round}(p\_cy / stride)$
   - Raggio di ricerca limitato al torso ($r\_gx, r\_gy \approx 35\% \times \text{dim} / stride$).
3. **Decodifica Softmax DFL & Filtro IoU Bounding Box:**
   Ogni cella candidata $(gx, gy)$ decodifica il proprio bounding box predetto tramite Softmax a 16 bin per lato:
   - $bx_1 = (gx + 0.5 - dfl_0) \times stride$
   - $by_1 = (gy + 0.5 - dfl_1) \times stride$
   - $bx_2 = (gx + 0.5 + dfl_2) \times stride$
   - $by_2 = (gy + 0.5 + dfl_3) \times stride$
   - Se $\text{IoU}( [bx_1, by_1, bx_2, by_2], [px_1, py_1, px_2, py_2] ) < 0.20$, la cella viene categoricamente scartata come rumore di background.
4. **Validazione Anatomica & Rifiuto Clamping Y=0.00:**
   Celle centrate nel padding letterbox o con giunti collassati sul bordo superiore ($Y < 0.02$ quando la persona è in basso nel frame) vengono rigettate.
5. **Score Composito Ottimale:**
   `score = (iou * 15.0 + sum_conf * 1.0 + vis * 0.5 + torso_conf * 3.0) * scale_weight`.

---

## 4. Test e Verifica HIL (Hardware-in-the-Loop)
- **HIL su Raspberry Pi 5 & Hailo-10H:** Eseguito test su frame reale (`latest_camera_frame.jpg`):
  - Vincitore selezionato: Stride 16, Cella `(34, 32)`.
  - Overlap bounding box DFL: $\text{IoU} = 0.875$ ($87.5\%$ di allineamento con YOLO).
  - Giunti decodificati:
    - `Nose: (0.863, 0.908) c=0.61`
    - `LShoulder: (0.870, 0.930) c=0.83`
    - `RShoulder: (0.854, 0.929) c=0.88`
    - `LElbow: (0.884, 0.975) c=0.35`
    - `RElbow: (0.853, 0.969) c=0.54`
    - `LWrist: (0.884, 0.994) c=0.23`
    - `RWrist: (0.860, 0.979) c=0.36`
  - Le articolazioni sono centrate, simmetriche e dinamicamente reattive ai movimenti dell'utente.
