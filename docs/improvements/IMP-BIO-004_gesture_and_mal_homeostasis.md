# Progetto di Miglioramento: IMP-BIO-004 (Visual Safety Gestures & Homeostatic MAL Regulation)

- **Failure Mode Correlato:** `FM-BIO-004`
- **Sottosistema:** Cognitive/Vision
- **Classificazione:** PERFORMANCE
- **Stato:** COMPLETED

## Descrizione del Problema
Rischio di falsi allarmi gestuali (stop d'emergenza o barge-in involontario) su movimenti naturali dell'utente e rischio di proattività asfissiante/incoerente generata da variazioni visive temporanee o durante momenti in cui l'utente è concentrato al lavoro.

## Soluzione Architetturale
1. Triplo Gate Gestuale: (A) sguardo frontale verso Marcus; (B) polso fermo nello spazio; (C) segmentazione depth $64 \times 64$ della mano con analisi convessità palmare debounce 3 frame (300ms) per `OPEN_PALM_STOP` e silhouette pugno verticale per `THUMBS_UP_CONFIRM`.
2. Indice di Sorpresa Spaziale $S(\mathcal{O}_i)$ in C++ con distanza di Mahalanobis su ancore consolidate `rtabmap.db` ed esclusione classi dinamiche.
3. Regolatore Omeostatico MAL con vettore pulsioni (Curiosità, Socialità, Autonomia) e inibizione automatica da Teoria della Mente quando l'utente è in `FOCUSED_WORK_DND`.
