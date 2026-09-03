# 🛠️ Progetto di Miglioramento IMP-NAV-016
> **Target Failure Mode:** `FM-NAV-016` (Falso Loop Closure e Distorsione Mappa SLAM da Aliasing Percettivo)  
> **Priorità RPN Iniziale:** 378 -> **RPN Residuo:** 36 | **Stato:** COMPLETED | **Dominio:** Navigazione & SLAM (RTAB-Map)

---

## 1. Analisi del Problema & Cause Radice

### Problema
In ambienti interni simmetrici (corridoi rettilinei con porte e pareti identiche, stanze con mobilio uniforme), l'algoritmo DBoW3 di RTAB-Map può generare similarità visiva fittizia con luoghi visitati in precedenza. Con mappe di profondità a risoluzione ridotta, la verifica geometrica RANSAC non possiede sufficiente granularità per rigettare il falso loop closure, provocando rotazioni o traslazioni spurie di $\pm 180^\circ$ nell'albero delle trasformazioni `map -> odom`.

### Soluzione Implementata (Blindatura Parametri & ROI Floor Masking)
1. **Soglia di Similarità Visiva Rafforzata:** Innalzamento di `Rtabmap/LoopThr` da 0.15 a **0.20** per richiedere una coerenza semantica e visiva significativamente maggiore prima di proporre una chiusura dell'anello.
2. **Tolleranza Riproiezione PnP Rigida:** `Vis/PnPReprojError` impostato a **2.5** pixel e `Vis/MinInliers` fissato a **15** inlier 3D stabili.
3. **ROI Floor Exclusion Masking:** Aggiunto `Kp/RoiRatios: "0.0 0.0 0.10 0.0"` per tagliare il 10% inferiore dell'immagine, escludendo riflessi del pavimento, fughe di piastrelle e texture calpestabili che causano falsi accoppiamenti geometrici.
4. **Verifica Topologica del Grafo:** Confermato `RGBD/LoopClosureRejectionWithGraph: "true"` per rigettare trasformazioni incoerenti con il grafo di adiacenza odometrico.

---

## 2. File Modificati
- `robopy_controller/config/rtabmap.yaml`
- `test/unit/test_dfmea_nav_mitigations.py`
