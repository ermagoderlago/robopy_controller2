# IMP-NAV-041: Risoluzione Deadlock Nav2 Standby/Motion-Gate, Heading Tracking Rotazioni Pure e Filtraggio Piano Terra 2.5D

## 1. Dati del Failure Mode
- **ID Failure Mode:** `FM-NAV-041`
- **Descrizione:** Nav2 pianifica con successo percorsi o missioni di esplorazione frontiere, ma il robot rimane fisicamente immobile; mancata generazione comandi `/cmd_vel` per tolleranza di heading zero durante rotazioni sul posto (`RotationShimController`), stallo Catch-22 tra standby manager e motion gate, e abort pianificazione per falsi positivi costmap negativa su pavimenti piani.
- **Severity iniziale:** 8 | **Occurrence iniziale:** 6 | **Detection iniziale:** 6 | **RPN iniziale:** 288
- **Severity residua (target):** 8 | **Occurrence residua:** 2 | **Detection residua:** 1 | **RPN residuo:** 16
- **Status:** COMPLETED

---

## 2. Analisi Root Cause (5 Whys)
1. *Perché Marcus non si muoveva pur avendo Nav2 pianificato una missione?*
   Perché il Behavior Tree di navigazione e i controller Nav2 rimanevano in attesa di sblocco motion gate, mentre il supervisore di standby e il controller server erano bloccati in un deadlock d'interblocco logico.
2. *Perché si verificava il deadlock d'interblocco?*
   - Catch-22: `SensorStandbyManager` attendeva che `/cmd_vel` fosse attivo per svegliare i sensori e rilasciare `/robot/motion_gate=True`, ma Nav2 attendeva che i sensori fossero stabili e il motion gate aperto prima di calcolare e inviare comandi su `/cmd_vel`.
   - `RotationShimController` in `nav2_params_jazzy.yaml` imponeva una rotazione sul posto ad allineamento angolare stringente, ma il `ResourceGovernor` e il multiplexer consideravano la velocità lineare nulla ($v=0$) come stato di immobilità pura, inibendo il tracking di heading ed azzerando l'orientamento.
3. *Perché MPPI o Controller Server scartavano le traiettorie o andavano in timeout?*
   - Desincronizzazione del watchdog del controller (`controller_patience` vs orologio `use_sim_time` / wall-clock).
   - In presenza di telecamera OAK-D Lite, `semantic_costmap_injector.py` considerava qualsiasi piccolo gradino planare o discontinuità di profondità vicina a terra come ostacolo negativo irreversibile (`negative_obstacle`), bloccando la costmap locale e globale all'origine stessa del robot.
4. *Quali componenti software erano coinvolte?*
   - `robopy_controller/nodes/sensor_standby_manager.py` (Deadlock gate & standby)
   - `robopy_controller/nodes/resource_governor_node.py` e `services/resource_governor.py` (In-place spin heading tracking)
   - `robopy_controller/nodes/semantic_costmap_injector.py` (Soglia altimetrica differenziale Z con discriminazione del piano di calpestio `z < -0.10m`)
   - `robopy_controller/config/nav2_params_jazzy.yaml` (Watchdog, toleranze RotationShim e MPPI)
   - `launch/custom_nav2_launch.py` e `restart_hailo.sh` (Clock e lifecycle synchronization)
5. *Come risolvere strutturalmente senza compromettere la sicurezza?*
   - Handshake bidirezionale non-bloccante: lo stato di pianificazione attiva Nav2 (goal status `ACCEPTED`/`EXECUTING`) attiva immediatamente il pre-wake dei sensori e apre il `motion_gate` prima che il controller server inizi a campionare.
   - Supporto esplicito delle rotazioni pure: consentire al `ResourceGovernor` di classificare il moto rotazionale puro ($|\omega| > 0.05\text{ rad/s}$) come stato cinematico attivo `IN_TRANSIT_ROTATING`.
   - Filtraggio piano terra per ostacoli negativi: registrare un dislivello negativo solo se la quota altimetrica nel frame mappa scende effettivamente al di sotto del piano stradale/pavimento ($z_{map} < -0.10\text{ m}$), prevenendo falsi ostacoli su piastrelle, tappeti o riflessioni superficiali.

---

## 3. Piano di Azione e Implementazione
1. **Risoluzione Catch-22 Motion Gate (`sensor_standby_manager.py`):**
   - Intercettare l'intenzione di moto attiva (`nav_intent == True` o goal attivo) per eseguire il wake preventivo istantaneo senza attendere `/cmd_vel > 0`.
2. **Abilitazione Heading Tracking Rotazioni Pure (`resource_governor.py`, `resource_governor_node.py`):**
   - Includere la soglia di velocità angolare ($|\omega| \ge 0.05\text{ rad/s}$) nella valutazione del moto effettivo e nel tracking di sicurezza.
3. **Filtro Differenziale Ground Plane (`semantic_costmap_injector.py`):**
   - Vincolo congiunto: $\Delta z > 0.15\text{ m}$ E quota assoluta $z_{map} < -0.10\text{ m}$ rispetto al piano terra, discriminando nettamente i veri vuoti (scale, gradini a scendere) dai falsi allarmi del sensore ottico.
4. **Configurazione Nav2 Ottimizzata (`nav2_params_jazzy.yaml`):**
   - Rallentamento controllato in rotazione sul posto, allineamento tolleranze angolari `RotationShimController` ($0.15\text{ rad}$) e sincronizzazione temporale.

---

## 4. Criteri di Validazione e Test
- **Simulazione Gazebo & Closed-Loop Suite:**
  - Esecuzione `run_sim_tests.bat` (25/25 test superati con successo in 1.26s).
  - Test specifico `test/unit/test_negative_obstacle_injector.py` (2/2 test superati, zero falsi positivi su pavimenti continui).
  - Simulazione missione autonoma in ambiente Smart Home 3D con Nav2 (`robot_simulation/scripts/run_closed_loop_mission_sim.py`).
- **Verifica su Robot Reale (Marcus Pi 5):**
  - Sincronizzazione ed hot-swap completati con successo su `robopy@marcus`.
  - Verifica live stato batteria (12.68 V, 100%), CPU thermal (60.6°C), memoria RAM (2.58 GB, sotto la soglia di rischio).
  - Invocazione servizio `/frontier_exploration/evaluate` -> esito `success=True`.
  - Avvio e arresto test `/frontier_exploration/start` e `/frontier_exploration/stop` -> esito `success=True`.
  - Action Server `/navigate_to_pose` verificato con successo (`SERVER_CONNECTED_OK`).
