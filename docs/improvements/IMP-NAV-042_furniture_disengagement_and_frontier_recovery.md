# IMP-NAV-042: Disimpegno Fisico da Gambe Tavoli e Prevenzione Cascading Blacklist Esplorazione Frontiere

## 1. Descrizione del Problema e Analisi Tecnica
Durante le sessioni di esplorazione autonoma e mappatura SLAM di Marcus in ambienti domestici arredati, il robot si è arrestato frontalmente contro la gamba di un tavolo.
L'analisi telemetrica dei log di runtime ha evidenziato tre difetti critici concomitanti:
1. **Micro-Backup Insufficiente in `nav2_survival_bt.xml`**:
   La sequenza `SurvivalRecoverySequence` conteneva un'azione `<BackUp backup_dist="0.08" backup_speed="0.08" />`. Un arretramento di soli 8 cm non consentiva al footprint differenziale di Marcus (lunghezza 36 cm, raggio 18 cm) di uscire dal buffer di inflazione e dal raggio di sicurezza dell'ostacolo.
2. **Assenza di Svolta di Disimpegno nel BT**:
   Dopo l'arretramento lineare, il Behavior Tree ripianificava immediatamente senza ruotare la prua del robot. La prua rimaneva puntata esattamente contro la gamba del tavolo, causando l'aborto immediato del piano da parte del controller MPPI (`status: 6` / `STATUS_ABORTED`).
3. **Cascading Blacklist in `FrontierExplorerNode`**:
   Quando Nav2 abortiva l'azione a causa della prossimità all'ostacolo, il nodo frontiere considerava la coordinata del target non raggiungibile e la inseriva permanentemente nella blacklist (`self.engine.blacklist_frontier(goal_xy)`). Trovandosi fisicamente bloccato nello stesso punto, ogni nuovo goal verso qualsiasi stanza veniva abortito in sequenza rapida (10 goal in 8 secondi), esaurendo tutte le frontiere e forzando l'uscita prematura con `ALL_FRONTIERS_BELOW_THRESHOLD (0.38m < 0.40m)`.

## 2. Modifiche Implementate

### A. Potenziamento Recovery Behavior Tree (`robopy_controller/config/nav2_survival_bt.xml`)
- Incrementato il backup di disimpegno da `0.08m` a `0.22m` (`backup_speed: 0.10 m/s`).
- Inserita una rotazione angolare controllata di disimpegno:
  `<Spin spin_dist="0.785" name="DisengageSpin" error_code_id="{spin_error_code}"/>` ($\approx 45^\circ$).
- Questa rotazione sposta l'orientamento della prua e dei sensori frontali lontano dall'ostacolo senza violare il divieto di Zona Rossa di spin ciechi a $90^\circ/180^\circ$ (SPEC-02).

### B. Calibrazione Costmap ed Evitamento Tavoli (`robopy_controller/config/nav2_params_jazzy.yaml`)
- Aumentato `repulsion_weight: 3.5` (da 2.5) in `ObstaclesCritic` di MPPI per mantenere traiettorie a maggior distanza di sicurezza da strutture sottili come gambe di tavoli e sedie.
- Calibrato `cost_scaling_factor: 2.8` (da 3.2) in `local_costmap` e `global_costmap` per generare un gradiente di costo più progressivo che scoraggia passaggi a meno di 25-30 cm dagli ostacoli.

### C. Anti-Cascading Blacklist & Stuck Recovery (`robopy_controller/nodes/frontier_explorer_node.py`)
- Corretto l'uso del parametro istanza `self.min_frontier_size` in `evaluate_frontiers()` (precedentemente vincolato alla costante di classe `0.40m`).
- Ridotto il valore di default di `min_frontier_size` da `0.40m` a `0.25m` per permettere l'esplorazione attorno all'arredamento domestico.
- Introdotto contatore di aborti consecutivi `self.consecutive_aborts`: se $\ge 2$ tentativi falliscono in successione, il nodo rileva che il robot è vincolato localmente, esegue autonomamente la manovra `_execute_disengage_recovery()` (arretramento 22 cm e pivot 45°) e resetta la blacklist transitoria.
- Aggiunto fallback prima di dichiarare `COMPLETED`: se le frontiere sono azzerate ma vi erano elementi in blacklist, la blacklist viene resettata per un secondo passaggio prima di arrestare i motori.

## 3. Risultati & Validazione Sperimentale
- **Disimpegno Fisico su Marcus Pi 5**: Marcus è stato disimpegnato con successo dalla gamba del tavolo con spostamento netto registrato di $+27.2\text{ cm}$ e rotazione di $41.4^\circ$, liberando completamente lo spazio frontale.
- **Sincronizzazione Host**: Tutti i file di configurazione e i nodi aggiornati sono stati sincronizzati e installati in `/mnt/ssd/robopy_controller_host/`.
- **DFMEA**: RPN ridotto da 336 a 16 (`FM-NAV-042`).
