# Progetto di Miglioramento IMP-COG-002: MARCUS Acronym Identity & Active RAG Retrieval

## 1. Descrizione del Malfunzionamento (Failure Mode)
* **Failure ID:** FM-COG-002 / FM-LLM-006
* **Dominio:** Cognitive AI / Cloud, Memory & Orchestration
* **Sintomo:** Interrogato sull'origine o il significato del suo nome, Marcus allucinava o non ricordava la definizione *"MARCUS — Modular Autonomous Robotic Control Unit System"*, anche dopo che l'utente gliela aveva spiegata in precedenza.
* **Causa Radice:**
  1. Assenza della definizione estesa dell'acronimo nel system prompt predefinito di `llm_service.py`.
  2. Mancata esecuzione della query semantica RAG prima di comporre il prompt in `ConversationManager._process_locked`, per cui i fatti memorizzati su ChromaDB in background non venivano mai richiamati o iniettati nel contesto del modello.

## 2. Azioni Correttive e Mitigazione
1. **Dichiarazione dell'Identità Residente:** Registrazione vincolante dell'acronimo nel system prompt in `llm_service.py`:
   `MARCUS — Modular Autonomous Robotic Control Unit System`.
2. **Ricerca Semantica RAG Attiva:** Invocazione asincrona di `memory_store.search(clean_text, top_k=3)` in `conversation.py` per arricchire il prompt con la sezione `[MEMORIE EPISODICHE E FATTI APPRESI (RAG)]`.
3. **Protezione dei Fatti Appresi (`LEARNED_FACT`):** Classificazione automatica delle definizioni fornite dall'utente come `LEARNED_FACT` con `importance=1.0` e `amygdala_protected="true"`, prevenendo la potatura sinaptica notturna.

## 3. Verifica e Collaudo
* **Unit Test Automated:** `test/unit/test_rag_acronym_memory.py` (Passed 3/3 tests).
* **ECO Riferimento:** `docs/ecos/orchestration_rag_ecos.md#ECO-2026-07-30-002`
* **Lezione Riferimento:** `docs/lessons/orchestration_and_rag.md#identita-dellacronimo-e-ricerca-semantica-rag-attiva`
