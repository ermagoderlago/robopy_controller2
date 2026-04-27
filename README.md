<div align="center">
  <h1>🤖 Marcus AI</h1>
  <p><b>Advanced Robotic Framework Powered by Multimodal AI</b></p>

  [![ROS2](https://img.shields.io/badge/ROS2-Jazzy-22314E?logo=ros)](https://docs.ros.org/en/jazzy/)
  [![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
  [![Gemini](https://img.shields.io/badge/Gemini-Live_API-4285F4)](https://ai.google.dev/)
  [![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
</div>

---

## 📖 Panoramica

**Marcus AI** è un framework robotico all'avanguardia basato su **ROS 2 Jazzy**, progettato per creare un assistente robotico conversazionale dotato di embodied AI e intelligenza artificiale multimodale.

L'obiettivo del progetto è fondere le capacità di percezione avanzate (Visione Spaziale e Analisi Audio Zero-Latency) con ragionamento cognitivo in tempo reale usando Google Gemini Live API.

## 🌟 Caratteristiche Principali

- 🧠 **Architettura Multimodale**: Capacità di processare streaming audio/video simultaneo e operare tramite modelli multimodali.
- 🗣️ **Conversazione Zero-Latency**: Riproduzione e interazione vocale a bassissima latenza tramite Gemini Live API e ASR locale integrato.
- 📚 **Memoria RAG Avanzata**: Integrazione profonda con **LlamaIndex** e **ChromaDB** per dotare il robot di persistenza contestuale e abilità di richiamo avanzate a lungo e breve termine.
- 🌙 **Nightly Dream Analysis**: Pipeline automatica che processa, consolida ed estrude insight filosofico-comportamentali dai log giornalieri durante la fase notturna off-peak.
- 🔌 **Sistema di Skill Registrate (Plugin System)**: Espandibilità immediata tramite workflow sicuri validati tramite AST per creare nuove capacità (es. navigazione o domotica Home Assistant).

## 🧰 Requisiti Hardware

Marcus è nativamente progettato e ottimizzato per funzionare in scenari edge-computing severamente vincolati.
* **Core Unit**: Raspberry Pi 5 (8GB RAM consigliata).
* **Vision & Depth Processing**: Telecamera Stereoscopica OAK-D Lite per riconoscimento facciale, localizzazione ed esplorazione autonoma VSLAM.
* **Audio Interfacing**: ReSpeaker Lite (con ESP32 firmware) per Voice Activity Detection (VAD) rapida e Wake-Word hardware-based.
* **Storage**: Unità SSD NVMe ad alte prestazioni.

## ⚙️ Requisiti Software

* **OS**: Ubuntu 24.04 (o compatibile per Raspberry Pi).
* **Middleware**: ROS 2 Jazzy Jalisco.
* **Generative AI**: Chiave API Google valida (Gemini).
* Le dipendenze di sistema e Python possono essere trovate all'interno di `requirements.txt` e nel setup del workspace.

## 🚀 Installazione e Setup

### 1. Clona la repository

```bash
git clone https://github.com/tuo-profilo/marcus-ai.git
cd marcus-ai
```

### 2. Configura le Dipendenze

La compilazione del pacchetto ROS2 richiede una toolchain ottimizzata per Raspberry Pi (es. Clang).
Installa le dipendenze Python:

```bash
pip install -r requirements.txt
```

### 3. Configura le Chiavi di Accesso
Invece di inserire i segreti nel codice, assicurati di esporli in ambiente sicuro. Crea un file sorgente o usa `.env` in directory (escluso da git):

```bash
export GEMINI_API_KEY="la-tua-api-key"
```

### 4. Build del Controller ROS2
Posizionati nella root del tuo workspace ROS2 ed esegui la build colcon:

```bash
colcon build --packages-select robopy_controller --symlink-install
```

### 5. Avvio del Sistema

Una volta compilato e aver fatto il "source" del workspace locale:

```bash
ros2 run robopy_controller robot_ai_node
```
Oppure tramite launcher se presente:
```bash
ros2 launch robopy_controller marcus.launch.py
```

## 🤝 Contribuire

Siamo aperti a contributi, in particolare su:
1. Incremento del dataset comportamentale e nuove logiche di *Nightly Dream*.
2. Riduzione della RAM footprints tramite tecniche di quantization.
3. Creazione di nuove Skills usando il template `SkillGeneratorPipeline`.

Fai riferimento ai file presenti in `.agent/workflows/` per automatizzare o comprendere i flussi complessi. Fai attenzione ai **Conventional Commits** per le tue PR!

## 📜 Licenza
Questo progetto è distribuito sotto la licenza MIT - vedi il file `LICENSE` per dettagli.
