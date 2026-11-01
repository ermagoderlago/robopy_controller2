# 🧪 Master Test & Re-verification Matrix — Marcus AI Platform

Questo documento costituisce il **Protocollo Master di Verifica e Validazione** per l'agente AI **Antigravity** e per il team di sviluppo.  
Definisce l'inventario completo dei test unitari, test di integrazione, benchmark avversariali e collaudi fisici live su hardware da eseguire obbligatoriamente a fronte di modifiche al codice sorgente, categorizzati per livello di impatto e sottosistema.

---

## 🚦 Livelli di Modifica del Codice & Criteri di Esecuzione

| Livello di Modifica | Definizione | Ambito Tipico | Suite Obbligatoria da Eseguire |
| :---: | :--- | :--- | :--- |
| **Livello 1**<br/>*Patch Minore* | Bugfix puntuale interno, ritocco prompt, aggiornamento costanti | Un singolo file `.py` senza alterazione di interfacce ROS o contratti | Test unitari specifici del modulo target (`pytest tests/test_<modulo>.py`) |
| **Livello 2**<br/>*Rimodulazione Sottosistema* | Modifica di un nodo ROS 2, della pipeline audio VUI, del client NPU o del database MAG | Più moduli di uno stesso sottosistema (`SPEC-01`..`SPEC-07`) | Suite completa del sottosistema + Test avversariali correlati |
| **Livello 3**<br/>*Modifica Profonda / Architetturale* | Refactoring del Router conversazionale, aggiornamento dipendenze, ricompilazione workspace, migrazione cloud/local | Impatto trasversale su orchestratore, sensori o build system | **Matrice Completa di Non-Regressione (All-Pass Gate)** |

---

## 🗺️ Inventario dei Test per Sottosistema

### 1. 🎙️ Voice User Interface (VUI) & Audio Pipeline (`SPEC-04`)
*Ambito:* Acquisizione audio 16kHz PCM, KWS "Marcus", resampling DAC 48kHz, barge-in limiter, voiceprint.

| File di Test | Tipologia | Obiettivo & Asserzioni | Mitigazione DFMEA |
| :--- | :---: | :--- | :--- |
| [`tests/test_programmatic_wakeword.py`](file:///d:/LUCA/robopy/tests/test_programmatic_wakeword.py) | Unit/Sim | Validazione innesco KWS ("Marcus") tramite audio sintetico iniettato | `FM-VUI-004` |
| [`tests/test_audio_buffer_manager.py`](file:///d:/LUCA/robopy/tests/test_audio_buffer_manager.py) | Unit | Pre-roll buffer circolare (500ms), lock rientranti e thread-safety ALSA | `FM-VUI-002`, `FM-VUI-003` |
| [`tests/test_vui_audio_resampling.py`](file:///d:/LUCA/robopy/tests/test_vui_audio_resampling.py) | Unit | Correttezza matematica resampling `audioop.ratecv` (16kHz ➔ 48kHz nativo DAC) | `FM-VUI-001` |
| [`tests/test_voiceprint_manager.py`](file:///d:/LUCA/robopy/tests/test_voiceprint_manager.py) | Unit | Estrazione embedding vocale ECAPA-TDNN e matching biometrico cosenico | `FM-VUI-007` |
| [`tests/test_extended_session_gating.py`](file:///d:/LUCA/robopy/tests/test_extended_session_gating.py) | Unit | Finestra conversazionale estesa (180s) e directed follow-up gating | `FM-VUI-021`, `FM-VUI-035b` |
| [`scripts/test_aec_hardware.py`](file:///d:/LUCA/robopy/scripts/test_aec_hardware.py) | Hardware Live | Verifica cancellazione acustica d'eco (AEC) su ReSpeaker Lite USB | `FM-VUI-002` |

---

### 2. 🧠 Hailo-10H NPU, Modello Locale Qwen2 & Metaprompt Fusion (`SPEC-03` & `SPEC-05`)
*Ambito:* Inferenza locale su NPU Hailo-10H, ChatML parsing, isolamento token, prevenzione history bleed e loop tautologici.

| File di Test | Tipologia | Obiettivo & Asserzioni | Mitigazione DFMEA |
| :--- | :---: | :--- | :--- |
| [`tests/test_hailo_client.py`](file:///d:/LUCA/robopy/tests/test_hailo_client.py) | Unit/Mock | Client asincrono Hailo, timeout hardware (10s), endpoint `/api/chat`, circuit breaker | `FM-LLM-005` |
| [`tests/test_metaprompt_fusion.py`](file:///d:/LUCA/robopy/tests/test_metaprompt_fusion.py) | Unit | Rispetto tetto token SLM (< 1200 token), ChatML tags e sezioni TRINITY | `FM-LLM-001` |
| [`tests/test_hailo_qwen_adversarial_iter2.py`](file:///d:/LUCA/robopy/tests/test_hailo_qwen_adversarial_iter2.py) | Adversarial | Isolamento storico `[RICHIESTA ATTUALE]`, zero loop apologetici 'automa' | `FM-LLM-014` |
| [`tests/test_persona_hybrid_templates.py`](file:///d:/LUCA/robopy/tests/test_persona_hybrid_templates.py) | Unit | Estrazione dual-mode testo/azione, zero leak di sintassi JSON nel TTS | `FM-LLM-003`, `FM-LLM-010` |
| [`tests/test_conversation_hybrid_failover.py`](file:///d:/LUCA/robopy/tests/test_conversation_hybrid_failover.py) | Integrazione | Failover istantaneo Gemini Cloud ➔ Qwen NPU locale su quota esaurita (429) | `FM-LLM-005`, `FM-NAV-034` |
| [`tests/test_benchmark_hailo_40_prompts.py`](file:///d:/LUCA/robopy/tests/test_benchmark_hailo_40_prompts.py) | Suite Pytest | Verifica della matrice 40 prompt (16 utente, 16 percezione/telemetria, 4 memoria, 4 safety) | `FM-LLM-013`, `FM-LLM-014` |
| [`scripts/benchmark_hailo_40_prompts.py`](file:///d:/LUCA/robopy/scripts/benchmark_hailo_40_prompts.py) | Live End-to-End | Benchmark automatizzato su Marcus: TTFT, latenza, asserzioni semantiche, odometria | `FM-LLM-013`, `FM-LLM-014` |
| [`tests/test_remote_chat.py`](file:///d:/LUCA/robopy/tests/test_remote_chat.py) | DDS Live | Invio query singola su `/robopy/conversation_rx` con ricezione da `/ai/conversation/response` | `FM-VUI-039` |

---

### 3. 🏛️ Cervello TRINITY, Memoria Autobiografica MAG, RAG & Sandbox (`SPEC-05`)
*Ambito:* Database SQLite WAL, Zettelkasten, RAG ChromaDB nativo, CAG telemetria, Amigdala e Kinematic Sandbox.

| File di Test | Tipologia | Obiettivo & Asserzioni | Mitigazione DFMEA |
| :--- | :---: | :--- | :--- |
| [`tests/test_trinity_engine.py`](file:///d:/LUCA/robopy/tests/test_trinity_engine.py) | Unit | Aggregazione contestuale RAG/CAG/MAG, prompt augmentation < 800ms | `FM-TRI-001`, `FM-COG-002` |
| [`tests/test_mag_database.py`](file:///d:/LUCA/robopy/tests/test_mag_database.py) | Unit | Persistenza SQLite WAL, sincronizzazione `NORMAL`, indici FTS5 e robustezza crash | `FM-TRI-001` |
| [`tests/test_milestone3_memory_analytics.py`](file:///d:/LUCA/robopy/tests/test_milestone3_memory_analytics.py) | Unit/FastPath | Fast-path temporale MAG (<10ms) per query retrospettive ("cosa abbiamo discusso oggi") | `FM-LLM-013` |
| [`tests/test_amygdala.py`](file:///d:/LUCA/robopy/tests/test_amygdala.py) | Unit | Riflesso di allarme, calcolo RMS/ZCR, inibizione dopaminergica e override di emergenza | `FM-COG-001` |
| [`tests/test_cag_collector.py`](file:///d:/LUCA/robopy/tests/test_cag_collector.py) | Unit | Polling telemetria hardware CAG (batteria, CPU temp, RAM, odometria) cache 5s | `FM-TRI-001` |
| [`tests/test_skill_executor.py`](file:///d:/LUCA/robopy/tests/test_skill_executor.py) | Sandbox | Kinematic Sandbox pre-flight: blocco comandi oltre limiti fisici ($v > 0.25$ m/s, $d > 2.0$ m) | `FM-LLM-004` |
| [`tests/test_adversarial_cognitive_grounding.py`](file:///d:/LUCA/robopy/tests/test_adversarial_cognitive_grounding.py) | Adversarial | Rifiuto allucinazioni cognitive e verifica ancoraggio ai fatti Zettelkasten | `FM-LLM-002` |

---

### 4. 🧭 Navigazione Nav2, SLAM RTAB-Map & Costmap 2.5D (`SPEC-02`)
*Ambito:* Divieto assoluto STVL 3D, proiezione geometrica 2.5D, AMCL 2D, prevenzione scale, allocazione SSD.

| File di Test | Tipologia | Obiettivo & Asserzioni | Mitigazione DFMEA |
| :--- | :---: | :--- | :--- |
| [`tests/test_navigation_client.py`](file:///d:/LUCA/robopy/tests/test_navigation_client.py) | Unit | Client action Nav2 `NavigateToPose`, cancellazione goal e monitoraggio stato | `FM-NAV-030` |
| [`tests/test_frontier_exploration_trinity.py`](file:///d:/LUCA/robopy/tests/test_frontier_exploration_trinity.py) | Integrazione | Esplorazione frontiere coordinata con memoria semantica e costmap 2.5D | `FM-NAV-015` |
| [`tests/test_adversarial_m2_lidar_kidnapped.py`](file:///d:/LUCA/robopy/tests/test_adversarial_m2_lidar_kidnapped.py) | Adversarial | Resilienza a perdita di localizzazione (kidnapped robot) e ostacoli improvvisi | `FM-NAV-016` |
| [`scripts/test_nav2_motion_verification.py`](file:///d:/LUCA/robopy/scripts/test_nav2_motion_verification.py) | Live Nav2 | Verifica comandi cinematica Nav2 senza carico eccessivo CPU (no STVL) | `FM-NAV-001` |

---

### 5. ⚙️ Attuazione, Motori ESP32, Cinematica & BMS (`SPEC-01` & `SPEC-06`)
*Ambito:* Controllo motori DC Waveshare, encoder 1440 CPR, watchdog seriale, monitoraggio LiPo 3S2P.

| File di Test | Tipologia | Obiettivo & Asserzioni | Mitigazione DFMEA |
| :--- | :---: | :--- | :--- |
| [`scripts/test_forward_backward_30cm.py`](file:///d:/LUCA/robopy/scripts/test_forward_backward_30cm.py) | Hardware Live | Spostamento calibrato 30 cm avanti e indietro con riscontro tick encoder | `FM-MOT-001`, `FM-MOT-004` |
| [`scripts/test_spin_360.py`](file:///d:/LUCA/robopy/scripts/test_spin_360.py) | Hardware Live | Rotazione completa 360° con verifica simmetria e dead-reckoning IMU/odometria | `FM-MOT-002` |
| [`scripts/test_battery_topics.py`](file:///d:/LUCA/robopy/scripts/test_battery_topics.py) | Hardware Live | Monitoraggio tensione LiPo, anti-sag filter e soglie critiche (9.0V shutdown, 9.9V dock) | `FM-SYS-003`, `FM-SYS-004` |

---

### 6. ☁️ Gemini Live Cloud Streaming (Riattivazione Quote / Crediti Futuri)
*Ambito:* Connessione WebSocket bidirezionale Gemini Live API, streaming audio 16kHz mono, gestione reconnection.

| File di Test | Tipologia | Obiettivo & Asserzioni | Mitigazione DFMEA |
| :--- | :---: | :--- | :--- |
| [`tests/test_gemini_lifecycle.py`](file:///d:/LUCA/robopy/tests/test_gemini_lifecycle.py) | Unit/Mock | Ciclo di vita sessione Gemini Live, handling handshake e token renewal | `FM-VUI-036` |
| [`scripts/test_live_conversation.py`](file:///d:/LUCA/robopy/scripts/test_live_conversation.py) | Live Cloud | Sessione vocale bidirezionale live completa con microfono ReSpeaker e Gemini Cloud | `FM-VUI-021` |

---

## 🛠️ Procedura Operativa di Esecuzione su Marcus (Raspberry Pi 5)

Per eseguire le suite di test sul robot, connettersi via SSH impostando rigorosamente l'ambiente ROS 2 / CycloneDDS:

```bash
# 1. Impostazione ambiente ROS 2 CycloneDDS (Domain 42)
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/tmp/cyclonedds_robopy.xml
source /home/robopy/ros2_jazzy/install/setup.bash
source /mnt/ssd/robopy_controller_host/install/setup.bash

# 2. Esecuzione Suite Unitaria e Adversarial (Fast, < 30s)
cd /mnt/ssd/robopy_controller_host
python3 -m pytest tests/test_hailo_client.py tests/test_metaprompt_fusion.py tests/test_hailo_qwen_adversarial_iter2.py -v

# 3. Esecuzione Benchmark Completo 40-Prompt (NPU Hailo-10H Live)
python3 scripts/benchmark_hailo_40_prompts.py --mode=ros2 --timeout=15.0

# 4. Collaudo Istantaneo Singola Query DDS Live
python3 tests/test_remote_chat.py "qual è la capitale della francia?"
```

---

## 🔒 Regola di Chiusura DFMEA & Governance
Nessuna modifica che tocchi un Failure Mode di livello `HIGH`, `CRITICAL` o `REVISION_MANDATORY` ($S \ge 9$) può essere chiusa in `fmea/dfmea.yaml` senza che il test di verifica corrispondente (riportato in questa matrice) sia stato eseguito con esito **100% PASS** sull'hardware fisico di Marcus.
