# ECO: Attention Supervisor (Context Switching)

## Data
2026-08-06

## Motivo della Modifica (FMEA / Problema)
La CPU del Raspberry Pi 5 risultava saturata (100%) a causa dell'esecuzione parallela di SLAM (RTAB-Map), VAD continuo, inferenza TTS, e Navigazione Nav2. Inoltre, i rumori dei motori in movimento causavano falsi positivi nel microfono.

## Modifica Architetturale Implementata
È stato introdotto il nodo `attention_supervisor_node.py` in `robopy_controller/nodes/`.
Questo nodo agisce come un demone di "attenzione selettiva":
- Iscrizione a `/cmd_vel` per leggere l'intento di movimento.
- **Se in movimento (cmd_vel > 0):** Mette in pausa RTAB-Map tramite servizio `/rtabmap/pause` e spegne il microfono pubblicando `True` su `/ai/input/mic_mute`.
- **Se fermo (timeout 1.5s):** Riattiva RTAB-Map tramite `/rtabmap/resume` e riapre il microfono pubblicando `False`.

## Vincoli Rispettati
- **Memoria / CPU:** Risolve attivamente la saturazione CPU evitando elaborazioni concorrenti inutili (SLAM in movimento o VAD in movimento).
- **Core Pinning:** Questo è un nodo leggerissimo Python, gira sui core standard e cede i core 2-3 ai carichi pesanti.

## Impatto su Altri Nodi
- `setup.py` è stato aggiornato per esporre il nodo.
- Richiede la presenza di `rtabmap` per evitare warning di servizio non disponibile (gestito internamente con timeout non bloccante).
- Si interfaccia perfettamente con il `respeaker_vui_node` (che già supporta `/ai/input/mic_mute`).
