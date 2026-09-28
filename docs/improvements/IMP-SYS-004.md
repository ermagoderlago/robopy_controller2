# Improvement SYS-004: Batteria Live e Skill NOMAD
- Aggiunta intercettazione \atteria\ in \ui_dialogue_engine.py\ per lettura immediata senza dipendere da LLM, in modo che l'utente possa conoscere il voltaggio in tempo reale prima che il BMS lo spenga d'emergenza.
- Sistemata la skill omad_exploration_skill.py\ rendendo \ction\ (start/stop) mandatory per forzare l'esecuzione via Function Calling di Gemini Live, invece di frasi allucinate in testo libero.
