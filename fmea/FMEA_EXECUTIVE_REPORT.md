# 📊 Report Esecutivo DFMEA - Marcus AI Robot Platform
**Data Generazione:** 2026-07-30 23:39:54  
**Metodologia:** AIAG-VDA FMEA Standard con Regola Override Severità ($S \ge 9 \implies$ REVISION_MANDATORY)

---

## 📈 Sintesi Statistica Rischio

| Metrica | Valore | Note / Impatto |
| :--- | :---: | :--- |
| **Totale Modalità di Guasto (FM)** | **48** | Copertura integrata dei sottosistemi Marcus |
| **🟢 Risk Level LOW** | **27** | $RPN_{res} \le 50$ (Sotto controllo) |
| **🟡 Risk Level MEDIUM** | **7** | $51 \le RPN_{res} \le 199$ (Monitoraggio attivo) |
| **🟠 Risk Level HIGH** | **2** | $200 \le RPN_{res} \le 349$ (Mitigazione obbligatoria) |
| **🔴 Risk Level CRITICAL** | **0** | $RPN_{res} \ge 350$ (Blocco rilasci) |
| **🚨 REVISION_MANDATORY** | **12** | **Override Severità ($S \ge 9$)** - Massima Priorità Ingegneristica |

### Ripartizione per Sottosistema:
- **System/DDS:** 3 failure modes
- **Nav2:** 8 failure modes
- **VUI Audio:** 10 failure modes
- **Vision:** 2 failure modes
- **Hardware/Power:** 8 failure modes
- **ESP32:** 1 failure modes
- **AI/LangGraph:** 5 failure modes
- **System/Compute:** 1 failure modes
- **Vision/Hardware:** 1 failure modes
- **Nav2/Vision:** 1 failure modes
- **AI/Cognitive:** 6 failure modes
- **Simulation/Testing:** 2 failure modes

---

## 🚨 Modalità di Guasto ad Alta Priorità (REVISION_MANDATORY / HIGH / CRITICAL)

| ID FM | Sottosistema | Componente | Modo di Guasto | S_init ➔ S_res | RPN_init ➔ RPN_res | Livello Rischio | Stato Mitigazione | ECO Ref |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **FM-LLM-004** | AI/LangGraph | `dynamic_skill_creator` | Esecuzione di codice auto-generato (Skill) pericoloso, instabile o fuori controllo (AI Code Hazard) | 9 ➔ 9 | 324 ➔ **324** | `REVISION_MANDATORY` | `OPEN` | N/A |
| **FM-SYS-004** | Hardware/Power | `battery_management / OS` | Corruzione del filesystem (SSD/SD) per spegnimento brutale dovuto a 'Battery Cliff' (crollo improvviso di tensione) | 9 ➔ 9 | 315 ➔ **315** | `REVISION_MANDATORY` | `OPEN` | N/A |
| **FM-ACT-006** | Hardware/Power | `waveshare_motor_driver / IMU` | Il robot viene sollevato da terra (in volo) mentre è in movimento, le ruote continuano a girare a vuoto alla massima velocità | 8 ➔ 8 | 280 ➔ **280** | `HIGH` | `OPEN` | N/A |
| **FM-VUI-006** | VUI Audio | `respeaker_vui_node` | Distorsione audio meccanica dell'altoparlante (Clipping fisico e saturazione) | 8 ➔ 8 | 240 ➔ **240** | `HIGH` | `OPEN` | N/A |
| **FM-NAV-009** | Nav2/Vision | `semantic_costmap_injector / oak_d_lite` | Caduta dalle scale o da dislivelli (Negative Obstacle Fall) | 10 ➔ 10 | 560 ➔ **210** | `REVISION_MANDATORY` | `IN_PROGRESS` | [`docs/ecos/nav2_slam_ecos.md#ECO-2026-07-30-004`](docs/ecos/nav2_slam_ecos.md#ECO-2026-07-30-004) |
| **FM-ACT-007** | Hardware/Power | `chassis / IMU` | Ribaltamento fisico del robot (Tipped Over / Rollover) | 9 ➔ 9 | 180 ➔ **180** | `REVISION_MANDATORY` | `OPEN` | N/A |
| **FM-ACT-001** | Hardware/Power | `waveshare_motor_driver` | Immobilità del robot con ronzio prolungato dei motori in condizioni di stiction o batteria scarica | 9 ➔ 9 | 360 ➔ **36** | `REVISION_MANDATORY` | `CLOSED` | [`docs/ecos/actuation_ecos.md#ECO-2026-07-22-010`](docs/ecos/actuation_ecos.md#ECO-2026-07-22-010) |
| **FM-NAV-006** | Nav2 | `waveshare_motor_driver / oak_superpoint_odometry` | Slittamento ruote (Wheel Slip) su piastrelle/tappeti con conseguente deriva odometrica accumulata e disallineamento della posa globale | 9 ➔ 9 | 315 ➔ **36** | `REVISION_MANDATORY` | `OPEN` | [`docs/ecos/actuation_ecos.md`](docs/ecos/actuation_ecos.md) |
| **FM-ACT-005** | Hardware/Power | `waveshare_motor_driver` | Reset improvviso della scheda ESP32 (Brownout Microcontrollore) per picco di assorbimento in accelerazione | 9 ➔ 9 | 162 ➔ **18** | `REVISION_MANDATORY` | `OPEN` | [`docs/ecos/actuation_ecos.md`](docs/ecos/actuation_ecos.md) |
| **FM-SYS-001** | System/DDS | `build_system` | Out-Of-Memory (OOM) Kill indotto dal compilatore C++ clang++ durante la build | 9 ➔ 9 | 162 ➔ **9** | `REVISION_MANDATORY` | `CLOSED` | [`docs/lessons/dev_and_deployment.md#compilazione`](docs/lessons/dev_and_deployment.md#compilazione) |
| **FM-NAV-001** | Nav2 | `nav2_costmap_2d` | Saturazione CPU indotta da STVL 3D (Spatiotemporal Voxel Layer) con freeze del sistema | 9 ➔ 9 | 432 ➔ **9** | `REVISION_MANDATORY` | `CLOSED` | [`docs/ecos/nav2_slam_ecos.md#ECO-2026-06-10-001`](docs/ecos/nav2_slam_ecos.md#ECO-2026-06-10-001) |
| **FM-ACT-003** | Hardware/Power | `waveshare_motor_driver` | Guasto hardware sul canale encoder sinistro (odl) con lettura bloccata a pochi tick | 9 ➔ 9 | 54 ➔ **9** | `REVISION_MANDATORY` | `IN_PROGRESS` | [`docs/ecos/actuation_ecos.md#ECO-2026-07-17-005`](docs/ecos/actuation_ecos.md#ECO-2026-07-17-005) |
| **FM-SYS-003** | Hardware/Power | `usb_bus_power` | Caduta di tensione (Brownout) sul bus USB del Pi 5 all'accensione della telecamera OAK-D | 9 ➔ 9 | 243 ➔ **9** | `REVISION_MANDATORY` | `CLOSED` | [`docs/ecos/nav2_slam_ecos.md#ECO-2026-07-29-brownout`](docs/ecos/nav2_slam_ecos.md#ECO-2026-07-29-brownout) |
| **FM-VUI-004** | VUI Audio | `wake_word_node / respeaker_vui_node` | Blocco dell'avvio o eccezione di licenza per Picovoice Porcupine EOL / passaggio a piano a pagamento | 9 ➔ 9 | 162 ➔ **9** | `REVISION_MANDATORY` | `IN_PROGRESS` | [`docs/ecos/audio_vui_ecos.md#ECO-2026-07-30-003`](docs/ecos/audio_vui_ecos.md#ECO-2026-07-30-003) |

---

## 📋 Registro Completo Failure Modes (Ordinato per RPN Residuo Decrescente)

| ID FM | Sottosistema | Componente | Modo di Guasto | S_res | O_res | D_res | RPN Residuo | Livello Rischio | Stato | Lesson Ref |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **FM-LLM-004** | AI/LangGraph | `dynamic_skill_creator` | Esecuzione di codice auto-generato (Skill) pericoloso, instabile o fuori controllo (AI Code Hazard) | 9 | 6 | 6 | **324** | `REVISION_MANDATORY` | `OPEN` | [`docs/lessons/llm_live_api.md`](docs/lessons/llm_live_api.md) |
| **FM-SYS-004** | Hardware/Power | `battery_management / OS` | Corruzione del filesystem (SSD/SD) per spegnimento brutale dovuto a 'Battery Cliff' (crollo improvviso di tensione) | 9 | 5 | 7 | **315** | `REVISION_MANDATORY` | `OPEN` | [`docs/lessons/dev_and_deployment.md`](docs/lessons/dev_and_deployment.md) |
| **FM-ACT-006** | Hardware/Power | `waveshare_motor_driver / IMU` | Il robot viene sollevato da terra (in volo) mentre è in movimento, le ruote continuano a girare a vuoto alla massima velocità | 8 | 7 | 5 | **280** | `HIGH` | `OPEN` | [`docs/lessons/actuation_motor_driver.md`](docs/lessons/actuation_motor_driver.md) |
| **FM-VUI-006** | VUI Audio | `respeaker_vui_node` | Distorsione audio meccanica dell'altoparlante (Clipping fisico e saturazione) | 8 | 6 | 5 | **240** | `HIGH` | `OPEN` | [`docs/lessons/telemetry_and_autotuning.md`](docs/lessons/telemetry_and_autotuning.md) |
| **FM-NAV-009** | Nav2/Vision | `semantic_costmap_injector / oak_d_lite` | Caduta dalle scale o da dislivelli (Negative Obstacle Fall) | 10 | 7 | 3 | **210** | `REVISION_MANDATORY` | `IN_PROGRESS` | [`docs/lessons/nav2_slam_tuning.md`](docs/lessons/nav2_slam_tuning.md) |
| **FM-NAV-007** | Nav2 | `rtabmap` | Perdita del Tracking SLAM per mancanza di Inliers in ambienti non strutturati (es. corridoi bianchi) | 7 | 7 | 4 | **196** | `MEDIUM` | `OPEN` | [`docs/lessons/telemetry_and_autotuning.md`](docs/lessons/telemetry_and_autotuning.md) |
| **FM-SYS-005** | System/Compute | `pi5_thermal_manager` | Perdita scadenze real-time (Deadline Miss) e movimento a scatti causati da Thermal Throttling della CPU | 6 | 8 | 4 | **192** | `MEDIUM` | `OPEN` | [`docs/lessons/telemetry_and_autotuning.md`](docs/lessons/telemetry_and_autotuning.md) |
| **FM-SYS-006** | System/DDS | `fastdds_middleware` | Caduta dell'albero TF e latenza critica dei topic ROS 2 causata da Multicast Discovery Storm | 8 | 6 | 4 | **192** | `MEDIUM` | `OPEN` | [`docs/lessons/dev_and_deployment.md`](docs/lessons/dev_and_deployment.md) |
| **FM-ACT-007** | Hardware/Power | `chassis / IMU` | Ribaltamento fisico del robot (Tipped Over / Rollover) | 9 | 5 | 4 | **180** | `REVISION_MANDATORY` | `OPEN` | [`docs/lessons/actuation_motor_driver.md`](docs/lessons/actuation_motor_driver.md) |
| **FM-VUI-005** | VUI Audio | `respeaker_vui_node` | Trascrizione ASR incomprensibile o allucinata ('Voce Distorta / Sorgente Lontana') | 6 | 8 | 3 | **144** | `MEDIUM` | `OPEN` | [`docs/lessons/telemetry_and_autotuning.md`](docs/lessons/telemetry_and_autotuning.md) |
| **FM-VIS-003** | Vision/Hardware | `oak_d_mount / tf_broadcaster` | Falso rilevamento ostacoli (Muri Inesistenti) o mancata rilevazione pavimento causati da Drift Meccanico (Sag) della telecamera | 8 | 6 | 2 | **96** | `MEDIUM` | `COMPLETED` | [`docs/lessons/vision_hailo_npu.md`](docs/lessons/vision_hailo_npu.md) |
| **FM-SIM-002** | Simulation/Testing | `synthetic_scenario_generator` | Divergenza tra Simulazione e Realtà ('Sim-to-Real Cognitive Gap') | 7 | 3 | 3 | **63** | `MEDIUM` | `OPEN` | [`docs/lessons/orchestration_and_rag.md`](docs/lessons/orchestration_and_rag.md) |
| **FM-COG-003** | AI/Cognitive | `chroma_synaptic_manager / RAG` | Inquinamento Vettoriale di ChromaDB da Errate Interpretazioni di RPE (Sarcasmo / Falsi Positivi) | 6 | 3 | 3 | **54** | `MEDIUM` | `OPEN` | [`docs/lessons/orchestration_and_rag.md`](docs/lessons/orchestration_and_rag.md) |
| **FM-COG-002** | AI/Cognitive | `PredictiveRouterNode / Amigdala Digitale` | Inibizione Sinaptica Eccessiva e Paralisi Decisionale ('Anedonia / Helplessness Learned') | 8 | 2 | 3 | **48** | `LOW` | `OPEN` | [`docs/lessons/orchestration_and_rag.md`](docs/lessons/orchestration_and_rag.md) |
| **FM-COG-004** | AI/Cognitive | `rag_context_builder / llm_reasoner` | Saturazione del Contesto e Allucinazione per Conflitto Vettoriale in Contesti Complessi | 8 | 2 | 3 | **48** | `LOW` | `OPEN` | [`docs/lessons/orchestration_and_rag.md`](docs/lessons/orchestration_and_rag.md) |
| **FM-COG-001** | AI/Cognitive | `CriticEvaluatorNode / PredictiveRouterNode` | Saturazione Dopaminergica ed Entrapment Comportamentale (Ciclo Positivo Continuo / Dipendenza) | 7 | 2 | 3 | **42** | `LOW` | `OPEN` | [`docs/lessons/orchestration_and_rag.md`](docs/lessons/orchestration_and_rag.md) |
| **FM-ACT-001** | Hardware/Power | `waveshare_motor_driver` | Immobilità del robot con ronzio prolungato dei motori in condizioni di stiction o batteria scarica | 9 | 2 | 2 | **36** | `REVISION_MANDATORY` | `CLOSED` | [`docs/lessons/actuation_motor_driver.md#stiction-kick`](docs/lessons/actuation_motor_driver.md#stiction-kick) |
| **FM-NAV-006** | Nav2 | `waveshare_motor_driver / oak_superpoint_odometry` | Slittamento ruote (Wheel Slip) su piastrelle/tappeti con conseguente deriva odometrica accumulata e disallineamento della posa globale | 9 | 2 | 2 | **36** | `REVISION_MANDATORY` | `OPEN` | [`docs/lessons/actuation_motor_driver.md`](docs/lessons/actuation_motor_driver.md) |
| **FM-NAV-002** | Nav2 | `oak_superpoint_odometry_node` | Stallo del tracciamento VO con blocco permanente della posa a 0.000m | 8 | 2 | 2 | **32** | `LOW` | `MITIGATED` | [`docs/lessons/nav2_slam_tuning.md#visual-odometry`](docs/lessons/nav2_slam_tuning.md#visual-odometry) |
| **FM-NAV-005** | Nav2 | `semantic_costmap_injector / rtabmap` | Corruzione permanente della mappa RTAB-Map ed inserimento di ostacoli fantasma o mancato rilevamento di ostacoli trasparenti (vetri/specchi) e sotto/sopra la Pitch della telecamera | 8 | 2 | 2 | **32** | `LOW` | `IN_PROGRESS` | [`docs/lessons/nav2_slam_tuning.md#25d-costmap`](docs/lessons/nav2_slam_tuning.md#25d-costmap) |
| **FM-LLM-005** | AI/LangGraph | `cloud_llm_gateway / hailo_qwen_fallback` | Mancata risposta vocale dell'AI al parlato dell'utente per errore API Cloud (Quota 429, Auth 403, Billing/Abbonamento o Outage Google) | 8 | 2 | 2 | **32** | `LOW` | `OPEN` | [`docs/lessons/llm_live_api.md`](docs/lessons/llm_live_api.md) |
| **FM-SIM-001** | Simulation/Testing | `shadow_memory_store / sandbox_evaluator` | Inquinamento della Memoria di Produzione da Dati Sintetici (Shadow Memory Leakage) | 8 | 2 | 2 | **32** | `LOW` | `OPEN` | [`docs/lessons/orchestration_and_rag.md`](docs/lessons/orchestration_and_rag.md) |
| **FM-NAV-008** | Nav2 | `local_planner_mppi` | Urti sistematici o micro-oscillazioni della traiettoria per incompatibilità parametrica di lungo termine | 5 | 3 | 2 | **30** | `LOW` | `COMPLETED` | [`docs/lessons/telemetry_and_autotuning.md`](docs/lessons/telemetry_and_autotuning.md) |
| **FM-LLM-003** | AI/LangGraph | `live_connection_manager` | Stallo conversazionale e congelamento dello stato VUI (robot bloccato in 'THINKING' senza risposta) | 7 | 2 | 2 | **28** | `LOW` | `OPEN` | [`docs/lessons/llm_live_api.md`](docs/lessons/llm_live_api.md) |
| **FM-VUI-005** | VUI Audio | `hailo_voiceprint_node / hailo_bridge_node` | Latenza elevata ed accumulo buffer audio (audio drift) per overhead computazionale dell'NPU durante l'inferenza di speaker ID | 7 | 2 | 2 | **28** | `LOW` | `IN_PROGRESS` | [`docs/lessons/audio_vui_pipeline.md#hailo-voiceprint`](docs/lessons/audio_vui_pipeline.md#hailo-voiceprint) |
| **FM-VUI-003** | VUI Audio | `respeaker_vui_node` | Falsi rilevamenti di presenza vocale (VAD) ed invio continuo di rumore di fondo a Gemini Live | 6 | 2 | 2 | **24** | `LOW` | `CLOSED` | [`docs/lessons/audio_vui_pipeline.md#hpf-filter`](docs/lessons/audio_vui_pipeline.md#hpf-filter) |
| **FM-VUI-004** | VUI Audio | `respeaker_vui_node` | Acoustic Echo Leakage ed auto-interruzione continua della sintesi vocale del robot | 6 | 2 | 2 | **24** | `LOW` | `OPEN` | [`docs/lessons/audio_vui_pipeline.md#peak-limiter`](docs/lessons/audio_vui_pipeline.md#peak-limiter) |
| **FM-VUI-006** | VUI Audio | `voiceprint_manager` | Errata attribuzione del parlato (False Speaker Match) tra persone con timbro simile o in presenza di rumore | 6 | 2 | 2 | **24** | `LOW` | `IN_PROGRESS` | [`docs/lessons/audio_vui_pipeline.md#voiceprint-enrollment`](docs/lessons/audio_vui_pipeline.md#voiceprint-enrollment) |
| **FM-VUI-007** | AI/Cognitive | `memory_decay_engine` | Oblio aggressivo di informazioni importanti pronunciate poco prima di invocare 'Marcus' o accumulo indefinito di conversazioni futili | 6 | 2 | 2 | **24** | `LOW` | `IN_PROGRESS` | [`docs/lessons/orchestration_and_rag.md#algoritmo-oblio-memoria`](docs/lessons/orchestration_and_rag.md#algoritmo-oblio-memoria) |
| **FM-VUI-002** | VUI Audio | `respeaker_vui_node` | Distorsione acustica da clipping digitale per saturazione dell'ampiezza dei campioni PCM 16-bit | 5 | 2 | 2 | **20** | `LOW` | `CLOSED` | [`docs/lessons/audio_vui_pipeline.md#peak-limiter`](docs/lessons/audio_vui_pipeline.md#peak-limiter) |
| **FM-ACT-005** | Hardware/Power | `waveshare_motor_driver` | Reset improvviso della scheda ESP32 (Brownout Microcontrollore) per picco di assorbimento in accelerazione | 9 | 1 | 2 | **18** | `REVISION_MANDATORY` | `OPEN` | [`docs/lessons/actuation_motor_driver.md`](docs/lessons/actuation_motor_driver.md) |
| **FM-VIS-001** | Vision | `oak_superpoint_odometry_node` | Crash per lettura Heap Out-Of-Bounds durante il parsing dei tensor di output dell'NPU Hailo | 8 | 1 | 2 | **16** | `LOW` | `CLOSED` | [`docs/lessons/vision_hailo_npu.md#memory-safety`](docs/lessons/vision_hailo_npu.md#memory-safety) |
| **FM-VIS-002** | Vision | `hailo_bridge_node` | Race condition e crash dell'infezione NPU con errore HAILO_INVALID_OPERATION | 8 | 1 | 2 | **16** | `LOW` | `CLOSED` | [`docs/lessons/vision_hailo_npu.md#npu-concurrency`](docs/lessons/vision_hailo_npu.md#npu-concurrency) |
| **FM-VUI-001** | VUI Audio | `respeaker_vui_node` | Microfono completamente silenzioso (RMS ~40) indotto dal routing errato su PipeWire | 7 | 1 | 2 | **14** | `LOW` | `CLOSED` | [`docs/lessons/audio_vui_pipeline.md#hardware-capture`](docs/lessons/audio_vui_pipeline.md#hardware-capture) |
| **FM-LLM-002** | AI/LangGraph | `llm_live_api` | Risposte multiple e sovrapposte ('doppia voce') ad una singola frase dell'utente | 5 | 1 | 2 | **10** | `LOW` | `CLOSED` | [`docs/lessons/llm_live_api.md`](docs/lessons/llm_live_api.md) |
| **FM-SYS-001** | System/DDS | `build_system` | Out-Of-Memory (OOM) Kill indotto dal compilatore C++ clang++ durante la build | 9 | 1 | 1 | **9** | `REVISION_MANDATORY` | `CLOSED` | [`marcus_core_rules.md#1-memoria-ram-e-limite-host`](marcus_core_rules.md#1-memoria-ram-e-limite-host) |
| **FM-NAV-001** | Nav2 | `nav2_costmap_2d` | Saturazione CPU indotta da STVL 3D (Spatiotemporal Voxel Layer) con freeze del sistema | 9 | 1 | 1 | **9** | `REVISION_MANDATORY` | `CLOSED` | [`docs/lessons/nav2_slam_tuning.md#25d-costmap`](docs/lessons/nav2_slam_tuning.md#25d-costmap) |
| **FM-ACT-003** | Hardware/Power | `waveshare_motor_driver` | Guasto hardware sul canale encoder sinistro (odl) con lettura bloccata a pochi tick | 9 | 1 | 1 | **9** | `REVISION_MANDATORY` | `IN_PROGRESS` | [`docs/lessons/actuation_motor_driver.md#encoder-diagnostics`](docs/lessons/actuation_motor_driver.md#encoder-diagnostics) |
| **FM-SYS-003** | Hardware/Power | `usb_bus_power` | Caduta di tensione (Brownout) sul bus USB del Pi 5 all'accensione della telecamera OAK-D | 9 | 1 | 1 | **9** | `REVISION_MANDATORY` | `CLOSED` | [`marcus_core_rules.md`](marcus_core_rules.md) |
| **FM-VUI-004** | VUI Audio | `wake_word_node / respeaker_vui_node` | Blocco dell'avvio o eccezione di licenza per Picovoice Porcupine EOL / passaggio a piano a pagamento | 9 | 1 | 1 | **9** | `REVISION_MANDATORY` | `IN_PROGRESS` | [`docs/lessons/audio_vui_pipeline.md#picovoice-free-tier-eol`](docs/lessons/audio_vui_pipeline.md#picovoice-free-tier-eol) |
| **FM-NAV-003** | Nav2 | `lifecycle_manager_navigation` | Crash all'avvio dello stack Nav2 per mancata corrispondenza nei nomi dei nodi controllati | 8 | 1 | 1 | **8** | `LOW` | `CLOSED` | [`docs/lessons/nav2_slam_tuning.md#lifecycle`](docs/lessons/nav2_slam_tuning.md#lifecycle) |
| **FM-ACT-002** | ESP32 | `waveshare_motor_driver` | Blocco del parser seriale ESP32 e scarto sistematico dei pacchetti di comando di velocità | 8 | 1 | 1 | **8** | `LOW` | `CLOSED` | [`docs/lessons/actuation_motor_driver.md#esp32-protocol`](docs/lessons/actuation_motor_driver.md#esp32-protocol) |
| **FM-ACT-004** | Hardware/Power | `waveshare_motor_driver` | Cancellazione delle velocità differenziali per inversione speculare dei tick dell'encoder destro | 8 | 1 | 1 | **8** | `LOW` | `CLOSED` | [`docs/lessons/actuation_motor_driver.md#kinematics`](docs/lessons/actuation_motor_driver.md#kinematics) |
| **FM-NAV-004** | Nav2 | `robot_localization_ekf` | Blocco pubblicazione TF odom->base_link da parte del nodo robot_localization | 8 | 1 | 1 | **8** | `LOW` | `CLOSED` | [`docs/lessons/nav2_slam_tuning.md`](docs/lessons/nav2_slam_tuning.md) |
| **FM-VUI-008** | VUI Audio | `respeaker_vui_node / live_connection_manager` | Conflitto o stallo dell'I/O audio con mancato invio dello stream PCM a Gemini Live o perdita dei primi 2 secondi di audio | 8 | 1 | 1 | **8** | `LOW` | `IN_PROGRESS` | [`docs/lessons/audio_vui_pipeline.md#hailo-gemini-handoff`](docs/lessons/audio_vui_pipeline.md#hailo-gemini-handoff) |
| **FM-COG-002** | AI/Cognitive | `conversation_manager / llm_service` | Perdita dell'acronimo di identità e mancata ricerca RAG in conversazione | 7 | 1 | 1 | **7** | `LOW` | `CLOSED` | [`docs/lessons/orchestration_and_rag.md#identita-dellacronimo-e-ricerca-semantica-rag-attiva`](docs/lessons/orchestration_and_rag.md#identita-dellacronimo-e-ricerca-semantica-rag-attiva) |
| **FM-SYS-002** | System/DDS | `system_scripts` | Errore di esecuzione script: OSError [Errno 8] Exec format error | 6 | 1 | 1 | **6** | `LOW` | `CLOSED` | [`marcus_core_rules.md#1-memoria-ram-e-limite-host`](marcus_core_rules.md#1-memoria-ram-e-limite-host) |
| **FM-LLM-001** | AI/LangGraph | `respeaker_vui_node` | Effetto 'Darth Vader' / 'Chipmunk' (audio accelerato o gravemente alterato) in riproduzione | 4 | 1 | 1 | **4** | `LOW` | `CLOSED` | [`marcus_core_rules.md#3-pipeline-vui-e-audio-pcm-streaming`](marcus_core_rules.md#3-pipeline-vui-e-audio-pcm-streaming) |

---

## 🔒 Protocollo Anti-Regressione & Regola Operativa per lo Sviluppatore AI

Per ogni futura modifica al codice di Marcus, l'agente di sviluppo DEVI attenersi al seguente ciclo ad anello chiuso:
1. **Consultazione DFMEA:** Prima di modificare un nodo ROS 2 o uno script, ispezionare `fmea/dfmea.yaml` per individuare i guasti correlati.
2. **Aggiornamento o Creazione Entry:** Se la modifica introduce un nuovo potenziale guasto o ne mitiga uno esistente, aggiornare il punteggio `residual_scoring` ed aggiungere un elemento in `history`.
3. **Esecuzione Ricalcolo:** Eseguire `python fmea/calculate_and_report_fmea.py` per sincronizzare RPN e report.
4. **Verifica Override:** Assicurarsi che nessuna voce con $S \ge 9$ rimanga con `mitigation_status: OPEN` senza una misura di contenimento architetturale testata e registrata nel corrispondente file ECO under `docs/ecos/`.
