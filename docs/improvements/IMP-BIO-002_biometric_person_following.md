# Progetto di Miglioramento: IMP-BIO-002 (Biometric Person Following & Torso Torsion Kinematics)

- **Failure Mode Correlato:** `FM-BIO-002`
- **Sottosistema:** Navigation/HRI
- **Classificazione:** SAFETY
- **Stato:** COMPLETED

## Descrizione del Problema
L'inseguimento persone classico rischia collisioni quando l'utente si volta di scatto o si ferma in una porta, oppure rischia il target hijacking quando un familiare attraversa la traiettoria tra Marcus e l'utente seguito.

## Soluzione Architetturale
1. Target Locking biometrico continuo con veto esplicito su identità ArcFace differenti da `target_identity`.
2. Tracking cinematico 3D con filtro di Kalman su `odom` e istogramma colore torso quando l'utente è di spalle.
3. Anticipazione cinematica della torsione spalle ($\Delta Z_{\text{shoulders}}$): taglio predittivo di `/speed_limit` a $10\text{ Hz}$ al $45\%$ non appena $|\dot{\psi}_{\text{torso}}| \ge 0.45\text{ rad/s}$.
4. Invio goal Nav2 a $\le 1.0\text{ Hz}$ con deadband spaziale $0.40\text{ m}$ e recovery su `last_known_breadcrumb` in caso di occlusione dietro angolo.
