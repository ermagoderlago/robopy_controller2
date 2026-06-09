# File Index

## File principali

| Percorso | Importanza | Descrizione |
|---|---|---|
| `./CMakeLists.txt` | Alta | Configurazione build C++ per il pacchetto principale |
| `./package.xml` | Alta | Metadati e dipendenze del pacchetto ROS 2 |
| `./setup.py` | Alta | Entry points e installazione dei nodi Python |
| `./setup.cfg` | Alta | Configurazioni aggiuntive per Python e linting |
| `./.env` | Alta | Variabili d'ambiente, chiavi API e segreti di configurazione |
| `./00_START_HERE.txt` | Alta | Punto di ingresso e panoramica della documentazione |
| `./INDEX.md` | Alta | Hub di navigazione per la ristrutturazione dei frame TF |
| `./QUICK_START.md` | Alta | Guida rapida all'avvio e test del sistema |
| `./README_FRAMES.md` | Alta | Documentazione principale per il sistema di coordinate |
| `./TF_RESTRUCTURE_SUMMARY.md` | Alta | Dettagli tecnici sull'architettura dei frame e trasformazioni |
| `./src/fast_flow_vo_node.cpp` | Alta | Nodo C++ principale per l'odometria visuale ad alte prestazioni |
| `./robopy_controller/nodes/superpoint_node.py` | Alta | Nodo Python per l'estrazione feature basata su AI |
| `./marcus_robot/package.xml` | Alta | Definizione del pacchetto robot Marcus e dipendenze |
| `./weights/Marcus_architecture.md` | Alta | Architettura del sistema di intelligenza artificiale Marcus |
| `./.agent/workflows/build.md` | Alta | Istruzioni per la compilazione e linee guida per l'IA |
| `./Marcus plan 01 planning.md` | Media | Pianificazione strategica e obiettivi del sistema Marcus |
| `./Marcus plan 02 risks tests ci.md` | Media | Analisi dei rischi, piano di test e integrazione continua |
| `./Marcus plan 03 runbook.md` | Media | Guida operativa (Runbook) per l'esecuzione del robot |
| `./Marcus plan 04 roadmap 30 60 90.md` | Media | Roadmap di sviluppo a 30, 60 e 90 giorni |
| `./Marcus plan 05 sprint 0 code.md` | Media | Note tecniche e dettagli implementativi dello Sprint 0 |
| `./IMPLEMENTATION_REPORT.md` | Media | Report dettagliato sulle implementazioni e verifiche effettuate |
| `./LAUNCH_UPDATE_GUIDE.md` | Media | Istruzioni per l'aggiornamento dei file di lancio ROS 2 |
| `./weights/lesson_learned.md` | Media | Archivio strutturato delle lezioni apprese e bug risolti |
| `./robopy_controller/nodes/` | Media | Cartella contenente i nodi Python del controller |
| `./robopy_controller/nodes/hailo_bridge_node.py` | Media | Nodo ROS 2 per l'interfaccia con Hailo-10H NPU |
| `./robopy_controller/nodes/semantic_costmap_injector.py` | Media | Proiezione ostacoli 3D in ostacoli costmap 2D |
| `./robopy_controller/nodes/engagement_monitor.py` | Media | Monitoraggio engagement gaze e prossemica HRI |
| `./robopy_controller/nodes/cloud_watchdog_node.py` | Media | Watchdog di rete per stato online/offline Gemini |
| `./robopy_controller/nodes/speaker_id_node.py` | Media | Verifica biometrica vocale ECAPA-TDNN |
| `./msg/SemanticObject.msg` | Media | Definizione messaggio oggetto semantico rilevato |
| `./msg/SemanticObjectArray.msg` | Media | Array di oggetti semantici per Nav2 costmap |
| `./msg/EngagementStatus.msg` | Media | Messaggio di stato dell'engagement HRI |
| `./launch/` | Media | Cartella contenente i file di lancio del sistema |
| `./test/` | Bassa | Test unitari e di integrazione del pacchetto |
| `./tests/` | Bassa | File di test aggiuntivi e legacy |
| `./test_live_25.py` | Bassa | Script di test per la stabilità della Live API |
| `./test_robot_ai.py` | Bassa | Test delle funzionalità di intelligenza artificiale |
| `./debug_run.log` | Bassa | File di log generato durante le sessioni di debug |
| `./marcus_sync.tar.gz` | Bassa | Backup compresso del workspace |
| `./tmp/` | Bassa | File temporanei e di staging |
| `./.cache/` | Bassa | Cache locale di modelli e strumenti |
