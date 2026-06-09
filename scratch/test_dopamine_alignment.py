import asyncio
import os
import sys
import time
import logging

# Setup paths to import packages correctly
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../robopy_controller")))

# Set up basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# We can import the actual classes we just created
from robopy_controller.robot_ai.orchestration.cognitive_graph import MarcusStateGraph, MarcusAgentState
from robopy_controller.robot_ai.rag.memory_store import Memory, MemoryType

# Standard mock classes for isolation
class MockEmbeddingService:
    async def embed(self, text):
        return [0.1] * 768

class MockChromaStore:
    def __init__(self):
        self.memories = []

    def add(self, memory):
        memory.id = f"mem_{len(self.memories) + 1}"
        memory.embedding = [0.1] * 768
        self.memories.append(memory)
        print(f"[OK] [Mock ChromaDB] Memoria persistita: {memory.content}")
        print(f"   Metadati: {memory.metadata}")
        return memory.id

    async def search(self, query: str, top_k: int = 5):
        results = []
        from robopy_controller.robot_ai.rag.memory_store import SearchResult
        
        if "spotify" in query.lower() or "musica" in query.lower():
            print(f"[SEARCH] [Mock ChromaDB] Rilevata query associata a 'spotify'. Restituzione penale passata.")
            past_mem = Memory(
                id="mem_past_penalty",
                content="L'utente ha espresso disapprovazione per la spotify_skill: 'no, fermati, non mettere questa musica!'.",
                memory_type=MemoryType.SYSTEM_EVENT,
                metadata={
                    "type": "alignment_event",
                    "metric": "penalty",
                    "skill_target": "spotify_skill",
                    "task_target": "metti musica spotify",
                    "value": -0.8
                }
            )
            results.append(SearchResult(memory=past_mem, score=0.85, distance=0.15))
        return results


async def test_biometric_alignment_pipeline():
    print("==============================================================")
    print(" [TEST] SISTEMA AVANZATO DI ALLINEAMENTO BIOMIMETICO (MARCUS) ")
    print("==============================================================")

    store = MockChromaStore()
    embedder = MockEmbeddingService()

    # 1. Initialize State Graph
    graph = MarcusStateGraph(memory_store=store, embedding_service=embedder)
    state = MarcusAgentState()
    
    # Check default state values
    assert state.reward_score == 0.0
    assert state.last_rpe == 0.0
    print("[OK] Stato iniziale caricato correttamente.")

    # 2. Simulate User Positive Verbal Feedback ("ottimo lavoro Marcus, bravo!")
    print("\n--- STEP 1: Simulazione Feedback Positivo dell'Utente ---")
    user_text_pos = "ottimo lavoro Marcus, bravo!"
    state.current_task = "saluta utente"
    state = await graph.run_input_flow(state, user_text=user_text_pos, base_system_prompt="Sei Marcus.")

    print(f"-> Stato dopo Feedback Positivo:")
    print(f"   Dopamine Score: {state.reward_score:.2f}")
    print(f"   Last RPE: {state.last_rpe:.2f}")
    print(f"   Feedback Context: {state.feedback_context}")
    
    assert state.last_rpe > 0.5
    assert state.reward_score > 0.0
    
    assert len(store.memories) == 1
    assert store.memories[0].metadata["metric"] == "reward"

    # 3. Simulate User Negative Verbal Feedback ("no marcus, sbagliato, fermati subito!")
    print("\n--- STEP 2: Simulazione Feedback Negativo dell'Utente ---")
    user_text_neg = "no marcus, sbagliato, fermati subito!"
    state.current_task = "avvia navigazione cucina"
    state = await graph.run_input_flow(state, user_text=user_text_neg, base_system_prompt="Sei Marcus.")

    print(f"-> Stato dopo Feedback Negativo:")
    print(f"   Dopamine Score: {state.reward_score:.2f}")
    print(f"   Last RPE: {state.last_rpe:.2f}")
    print(f"   Feedback Context: {state.feedback_context}")
    
    assert state.last_rpe < -0.5
    
    assert len(store.memories) == 2
    assert store.memories[1].metadata["metric"] == "penalty"

    # 4. Simulate ROS 2 skill execution outcome failure (deterministic critic node)
    print("\n--- STEP 3: Simulazione Fallimento Deterministico Skill ROS 2 ---")
    state.current_task = "leggi email di luca"
    state = await graph.run_post_execution_flow(
        state, 
        skill_name="email_skill", 
        success=False, 
        error_message="Timeout di connessione IMAP"
    )

    print(f"-> Stato dopo Fallimento Skill ROS 2:")
    print(f"   Dopamine Score: {state.reward_score:.2f}")
    print(f"   Last RPE: {state.last_rpe:.2f}")
    print(f"   Feedback Context: {state.feedback_context}")
    
    assert state.last_rpe < 0.0
    assert len(store.memories) == 3
    assert store.memories[2].metadata["metric"] == "penalty"
    assert store.memories[2].metadata["skill_target"] == "email_skill"

    # 5. Simulate Predictive Routing & Synaptic Inhibition injection
    print("\n--- STEP 4: Simulazione Condizionamento Router Sinaptico ---")
    state.current_task = "metti musica spotify"
    base_prompt = "Sei Marcus, un assistente robotico preciso."
    
    state = await graph.run_input_flow(state, user_text="metti un po' di musica metal su spotify", base_system_prompt=base_prompt)
    
    print("\n-> Override del System Prompt Temporaneo:")
    print(state.system_prompt_override)
    
    assert "REGOLE DI INIBIZIONE COGNITIVA" in state.system_prompt_override
    assert "spotify_skill" in state.inhibited_skills
    print("[OK] Inibizione Sinaptica e prompt injection completate con successo!")

    print("\n==============================================================")
    print(" [RISULTATO] TUTTI I TEST BIOMETRICI HANNO SUPERATO I CONTROLLI ")
    print("==============================================================")

if __name__ == '__main__':
    asyncio.run(test_biometric_alignment_pipeline())
