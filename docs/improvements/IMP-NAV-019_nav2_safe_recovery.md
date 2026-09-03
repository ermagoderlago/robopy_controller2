# 🛠️ Progetto di Miglioramento IMP-NAV-019
> **Target Failure Mode:** `FM-NAV-019` (Collisioni da Spurious Costmap Clearing durante i Recovery Behaviors Nav2)  
> **Priorità RPN Iniziale:** 512 -> **RPN Residuo:** 32 | **Stato:** COMPLETED | **Dominio:** Navigazione & Nav2 Control

---

## 1. Analisi del Problema & Cause Radice

### Problema
Quando il robot entra in stallo in passaggi stretti (tra sedie, tavoli o porte anguste), la sequenza di recupero standard di Nav2 eseguiva la cancellazione completa della costmap locale (`ClearEntireCostmap`) seguita da una rotazione sul posto a 90° (`Spin`). Avendo la telecamera frontale un FOV orizzontale limitato a $72.9^\circ$, gli ostacoli fisici posti lateralmente e dietro al robot venivano cancellati dalla memoria locale e il robot, ruotando alla cieca, collassava contro i mobili circostanti.

### Soluzione Implementata (Persistence Policy & Safe Disengagement Sequence)
1. **Costmap Layer Combination Policy:** Impostato `combination_method: 1` (Maximum) in `nav2_params_jazzy.yaml` e `nav2_params.yaml` per preservare il costo massimo degli ostacoli noti ed evitare la sovrascrittura impropria da raggio visivo vuoto.
2. **Rimozione del Blind Spin:** In `nav2_survival_bt.xml`, eliminato completamente il nodo `<Spin spin_dist="1.57"/>` dalla sequenza di recovery.
3. **Sequenza di Disimpegno Dolce:** Sostituito con un micro-arretramento controllato (`backup_dist="0.08"`, `backup_speed="0.08"`), seguito da una pausa di ricaricamento sensoriale da fermo e ripianificazione diretta del percorso senza rotazioni cieche fuori dal campo visivo.

---

## 2. File Modificati
- `robopy_controller/config/nav2_survival_bt.xml`
- `robopy_controller/config/nav2_params_jazzy.yaml`
- `robopy_controller/config/nav2_params.yaml`
- `test/unit/test_dfmea_nav_mitigations.py`
