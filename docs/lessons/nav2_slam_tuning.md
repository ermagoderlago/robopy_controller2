# Lezioni Apprese - Navigazione & SLAM Tuning

Questo documento raccoglie le lezioni apprese e le configurazioni relative a RTAB-Map, Nav2 e la gestione dei sensori geometrici di Marcus.

---

## 🗺️ RTAB-Map e Costmap Nav2

### Divieto di STVL e Proiezione 2.5D
* **Regola:** Non implementare la mappatura volumetrica continua 3D (STVL) su Raspberry Pi 5. La CPU ed il bus di memoria non sono in grado di sostenerne il calcolo.
* **Soluzione:** Proiettare gli ostacoli 3D estratti dalla visione artificiale in ostacoli costmap 2D localizzati (2.5D). Il nodo `semantic_costmap_injector.py` converte i bounding box tridimensionali in coordinate 2D e li inietta nel costmap Nav2 con un decadimento temporale associato.

### Allineamento dei Frame ID e `/scan`
* **Errore:** RTAB-Map fallisce l'aggiornamento con il messaggio `Could not convert laser scan msg! Aborting rtabmap update...`.
* **Causa:** Il frame associato ai messaggi del topic `/scan` generati da `depthimage_to_laserscan` non corrisponde all'albero statico delle trasformazioni geometriche.
* **Risoluzione:** Allineare i parametri di `depthimage_to_laserscan` impostando l'argomento `-p output_frame:=camera_link` per agganciarlo alla static TF `base_link ➔ camera_link`.

---

## 📐 Trasformazioni Geometriche (Static TFs)

### Albero delle Trasformazioni Standard ROS
* La gerarchia dei frame per Marcus deve rispettare rigorosamente gli standard ROS (REP-103 e REP-105):
  ```
  odom ➔ base_link ➔ camera_link ➔ camera_optical_frame
                  ➔ imu_link
  ```
* **camera_link:** Convenzione robotica (X=avanti, Y=sinistra, Z=alto).
* **camera_optical_frame:** Convenzione computer vision (X=destra, Y=basso, Z=avanti). Utilizzato come frame di riferimento per i dati dell'immagine e della nuvola di punti della camera.

---

## 🤖 Nav2 Behavior Trees e Parametri

### Percorsi dei File XML per i Behavior Tree (BT)
* **Problema:** Il nodo `bt_navigator` di Nav2 fallisce l'attivazione a causa di percorsi XML errati o non trovati nei parametri YAML.
* **Soluzione:** Nelle configurazioni `nav2_params_jazzy.yaml` e `nav2_params.yaml`, i parametri `default_nav_to_pose_bt_xml` e `default_bt_xml_filename` devono puntare esattamente al percorso del workspace installato:
  `/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_survival_bt.xml`
