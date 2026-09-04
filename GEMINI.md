# Marcus AI Workspace Rules (Antigravity & Gemini)

Questo file definisce le regole di sistema vincolanti per l'agente **Antigravity** e per qualsiasi modello Gemini in esecuzione su questo workspace o direttamente a bordo di Marcus (Raspberry Pi 5).

---

## ⚡ Regole Cardinali & Inviolabili

1. **Lettura Obbligatoria del Core:** Prima di iniziare qualsiasi task, leggi `marcus_core_rules.md` nella radice del workspace per conoscere i limiti fisici assoluti (RAM 4GB, `colcon build -j1`, divieto STVL 3D, audio 16kHz->48kHz, pinning CPU Core 2-3).
2. **OBBLIGO DI LETTURA DELLA SCHEDA TECNICA PRIMA DI SCRIVERE CODICE:**
   > [!CAUTION]
   > È severamente vietato invocare `write_to_file`, `replace_file_content` o modificare file senza aver prima aperto ed esaminato con `view_file` la relativa Scheda Tecnica (`/docs/specs/SPEC-XX.md`).

---

## 🧭 Mappa Rapida: Quale Scheda Leggere?

Consulta la tabella di instradamento in [`docs/specs/SPECS_ROUTING.yaml`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPECS_ROUTING.yaml) o l'indice [`docs/specs/INDEX_SCHEDE_TECNICHE.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/INDEX_SCHEDE_TECNICHE.md):

* **Motori, Driver ESP32, PID, Cinetica, Teleop:** Leggi [`docs/specs/SPEC-01_actuation_and_motion.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-01_actuation_and_motion.md)
* **Navigazione Nav2, SLAM RTAB-Map, VIO C++, Costmap 2.5D, NOMAD, Scale:** Leggi [`docs/specs/SPEC-02_navigation_and_slam.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-02_navigation_and_slam.md)
* **Visione, OAK-D Lite, NPU Hailo-10H, YOLO, SuperPoint, ArcFace:** Leggi [`docs/specs/SPEC-03_vision_and_hailo_npu.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-03_vision_and_hailo_npu.md)
* **Audio VUI, ReSpeaker, DSP, KWS "Marcus", Gemini Live WebSocket, Barge-in:** Leggi [`docs/specs/SPEC-04_audio_vui_pipeline.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-04_audio_vui_pipeline.md)
* **Cervello TRINITY (RAG, CAG, MAG), SQLite WAL, Amigdala, Skill Registry:** Leggi [`docs/specs/SPEC-05_cognitive_brain_trinity.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-05_cognitive_brain_trinity.md)
* **Batteria LiPo 3S, BMS, Anti-Sag, Shutdown 9.0V, Docking 9.9V, Termica:** Leggi [`docs/specs/SPEC-06_power_bms_thermal_safety.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-06_power_bms_thermal_safety.md)
* **Host Pi 5, Build sequenziale `-j1`, Watchdog, Lifecycle, Zero BOM UTF-8:** Leggi [`docs/specs/SPEC-07_system_os_build_lifecycle.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-07_system_os_build_lifecycle.md)
* **Governance Agente, Git Branch, Pre-Commit CI, Rollback Policy:** Leggi [`docs/specs/SPEC-00_antigravity_governance.md`](file:///c:/Users/lsuffia/OneDrive%20-%20BRUGOLA%20OEB%20INDUSTRIALE%20SPA/Documents/robopy/antigravity/docs/specs/SPEC-00_antigravity_governance.md)

---

## 🛡️ Verifica Cognitiva Obbligatoria (`[COGNITIVE_CHECK]`)

Prima di qualsiasi output contenente codice modificato, l'agente DEVE includere:
```markdown
[COGNITIVE_CHECK]
- Scheda Tecnica letta con view_file: /docs/specs/SPEC-XX.md
- Vincoli di Zona Rossa verificati: [conferma rispetto vincoli fisici e hardware]
- Classificazione modifica: [Zona Verde (autonoma) / Zona Gialla (approvata)]
- Documentazione consultata e aggiornata: [file lezioni, ECO, DFMEA]
```
