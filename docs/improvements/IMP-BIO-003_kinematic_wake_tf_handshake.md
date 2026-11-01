# Progetto di Miglioramento: IMP-BIO-003 (Kinematic Wake TF Handshake & Standby Keepalive)

- **Failure Mode Correlato:** `FM-BIO-003`
- **Sottosistema:** Navigation/Localization
- **Classificazione:** SAFETY
- **Stato:** COMPLETED

## Descrizione del Problema
In ROS 2 Jazzy, `amcl` pubblica `map -> odom` solo alla ricezione di `/scan`. Quando il motore del LiDAR viene arrestato per risparmio energetico e preservazione meccanica, `/scan` si azzera e la trasformata scade dopo 5 secondi, causando `TF Extrapolation Error` su Nav2. Inoltre, a ruote bloccate AMCL non aggiorna la posa alla riaccensione dei sensori se $\Delta d < 5\text{ cm}$.

## Soluzione Architetturale
1. `StandbyTFKeepalive`: durante lo standby del LiDAR, `SensorStandbyManager` ripubblica a $1.0\text{ Hz}$ la trasformata latched `map -> odom` garantendo continuità perfetta dell'albero TF.
2. Step 0 del risveglio: arresto immediato del timer keepalive per evitare conflitti dual-publisher.
3. Step 1-2: avvio `/start_motor` e validazione di 3 scansioni complete a 360°.
4. Step 3: invocazione forzata del servizio `/request_nomotion_update` su `amcl` e pulizia costmap prima di verificare la covarianza $P_{xx} + P_{yy} < 0.08\text{ m}^2$.
5. Step 4: apertura `/robot/motion_gate = True` solo a posa e sensori validati.
