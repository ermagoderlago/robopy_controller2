# Engineering Change Orders - Navigazione & SLAM

Questo documento raccoglie la cronologia delle modifiche ingegneristiche (ECO) apportate al modulo di navigazione, SLAM e allineamento geometrico di Marcus.

---

## 📈 ECO-2026-06-13-001: ROS 2 Jazzy Compilation Stability, Depth Image Compatibility, and NAV2 Launch Fixes
* **Stato:** ✅ **Completato, Sincronizzato e Compilato sul Robot**
* **Descrizione:** Risoluzione delle problematiche di stabilità della compilazione e integrazione hardware/software della telecamera OAK-D Lite con NAV2 sul Raspberry Pi 5.
* **Modifiche apportate:**
  * Modificata la codifica del frame di profondità da `"mono16"` a `"16UC1"` in `oak_superpoint_odometry_node.cpp` per garantire la compatibilità con il nodo `depthimage_to_laserscan`.
  * Aggiornati i parametri `default_nav_to_pose_bt_xml` e `default_bt_xml_filename` in `nav2_params_jazzy.yaml` e `nav2_params.yaml` per puntare al percorso corretto installato `/mnt/ssd/robopy_controller_host/install/robopy_controller/share/robopy_controller/config/nav2_survival_bt.xml`.

---

## 📈 ECO-2026-06-14-001: USB Power Stabilization and Depth-to-LaserScan TF Fixes
* **Stato:** ✅ **Completato, Sincronizzato e Attivo sul Robot**
* **Descrizione:** Diagnosi e risoluzione dei blocchi di tensione indotti sul bus USB del Pi 5 all'accensione della camera (SSD andava in sola lettura per brownout) tramite l'introduzione di un USB Hub alimentato esternamente. Risoluzione degli errori di conversione laser scan di RTAB-Map SLAM tramite l'allineamento dei parametri di `depthimage_to_laserscan`.
* **Modifiche apportate:**
  * Aggiunto il parametro `-p output_frame:=camera_link` all'invocazione di `depthimage_to_laserscan_node` nel launch script `restart_hailo.sh` per allineare il frame_id del topic `/scan` alle static TFs caricate in memoria.
  * Spostate camera OAK-D Lite ed SSD su un hub USB alimentato esternamente (tensione stabile, `vcgencmd get_throttled` fisso a `0x0`). RTAB-Map ora si sincronizza correttamente a 1.0Hz aggiornando le mappe.
