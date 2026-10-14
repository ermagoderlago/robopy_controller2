"""
Unit & Benchmark Test Suite: Cognitive Intent & Fast-Path Safety Arbiter (Milestone 2)
=====================================================================================
Validates Requirement R2 and Fase 2 Test Gate:
1. Fast-Path Emergency Stop:
   - Sub-10ms (target < 2ms) detection and zero-velocity hardware dispatch.
   - Tested across >= 10 safety phrases.
   - Publishes to /cmd_vel_mux/input/safety_override and /ai/conversation/interrupt.
   - Safe in both active ROS 2 and standalone/mock environments.
2. Cognitive Intent Classifier:
   - Sub-1.5ms execution budget (< 1500 us, target < 50 us at p99).
   - Evaluated against the 100 categorized domain prompts from survey_report.md.
   - Asserts >= 96% accuracy (target 100%).
3. Connectivity Arbiter:
   - 0.7/0.3 Exponential Moving Average (EMA) latency tracking.
   - Seamless failover (< 100 ms) to LOCAL_QWEN on socket failure or ping > 500 ms.
4. Master Hybrid Router:
   - Unified orchestration of fast-path stop, intent classifier, and connectivity arbiter.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from robopy_controller.robot_ai.arbitration import (
    FastPathStopHandler,
    FastPathStopResult,
    CognitiveIntentClassifier,
    RouteTarget,
    RoutingDecision,
    ConnectivityArbiter,
    ConnectivityState,
    HybridRouter,
)


# ==============================================================================
# 100 BENCHMARK DOMAIN PROMPTS (from survey_report.md)
# ==============================================================================
BENCHMARK_100_PROMPTS: List[Tuple[str, str]] = [
    # 50 Operational Prompts
    ("Vai in cucina", "OPERATIONAL"),
    ("Naviga verso il salotto", "OPERATIONAL"),
    ("Raggiungi la camera da letto", "OPERATIONAL"),
    ("Vai al corridoio", "OPERATIONAL"),
    ("Portati vicino alla porta d'ingresso", "OPERATIONAL"),
    ("Fai una scansione a 360 gradi", "OPERATIONAL"),
    ("Ruota di 90 gradi a destra", "OPERATIONAL"),
    ("Girati a sinistra", "OPERATIONAL"),
    ("Avanza di mezzo metro", "OPERATIONAL"),
    ("Fai marcia indietro per trenta centimetri", "OPERATIONAL"),
    ("Inizia l'esplorazione autonoma delle frontiere", "OPERATIONAL"),
    ("Esplora questo piano", "OPERATIONAL"),
    ("Mappa la stanza corrente", "OPERATIONAL"),
    ("Salva la mappa attuale con nome salotto", "OPERATIONAL"),
    ("Qual è il livello della batteria?", "OPERATIONAL"),
    ("Quanta carica ti rimane?", "OPERATIONAL"),
    ("Dimmi lo stato di alimentazione e voltaggio", "OPERATIONAL"),
    ("Sei sotto carica sulla docking station?", "OPERATIONAL"),
    ("Qual è la temperatura della CPU?", "OPERATIONAL"),
    ("Dimmi l'utilizzo di RAM e carico processore", "OPERATIONAL"),
    ("Controlla lo stato dei sensori", "OPERATIONAL"),
    ("Il lidar sta girando correttamente?", "OPERATIONAL"),
    ("La telecamera di profondità è attiva?", "OPERATIONAL"),
    ("Verifica la telemetria degli encoder delle ruote", "OPERATIONAL"),
    ("A che velocità stai andando?", "OPERATIONAL"),
    ("Qual è la tua posa stimata sulla mappa?", "OPERATIONAL"),
    ("In quale stanza ti trovi adesso?", "OPERATIONAL"),
    ("Qual è l'incertezza di localizzazione AMCL?", "OPERATIONAL"),
    ("Accendi la luce in cucina", "OPERATIONAL"),
    ("Spegni la lampada del corridoio", "OPERATIONAL"),
    ("Attiva la presa intelligente del salotto", "OPERATIONAL"),
    ("Imposta il termostato a venti gradi", "OPERATIONAL"),
    ("Chiudi le tapparelle della sala", "OPERATIONAL"),
    ("Alza il volume dell'altoparlante", "OPERATIONAL"),
    ("Abbassa il volume al dieci per cento", "OPERATIONAL"),
    ("Metti il volume al massimo", "OPERATIONAL"),
    ("Alza la testa e guarda verso l'alto", "OPERATIONAL"),
    ("Abbassa la telecamera verso il pavimento", "OPERATIONAL"),
    ("Centra i servomotori del collo", "OPERATIONAL"),
    ("Esegui un autotest diagnostico di bordo", "OPERATIONAL"),
    ("Riavvia il nodo di percezione Hailo", "OPERATIONAL"),
    ("Controlla se ci sono nodi ROS 2 in crash", "OPERATIONAL"),
    ("Attiva la modalità risparmio energetico", "OPERATIONAL"),
    ("Disattiva i sensori ad alto consumo", "OPERATIONAL"),
    ("Passa alla mappa del secondo piano", "OPERATIONAL"),
    ("Torna alla stazione di ricarica", "OPERATIONAL"),
    ("Allineati alla base di docking", "OPERATIONAL"),
    ("Misura la distanza dall'ostacolo più vicino", "OPERATIONAL"),
    ("Dimmi la frequenza di campionamento del lidar", "OPERATIONAL"),
    ("Mostrami lo stato dei motori e la corrente assorbita", "OPERATIONAL"),

    # 50 Conversational Prompts
    ("Raccontami una storia su un robottino curioso nello spazio", "CONVERSATIONAL"),
    ("Cosa ne pensi dell'intelligenza artificiale generale?", "CONVERSATIONAL"),
    ("Come ti senti oggi, Marcus?", "CONVERSATIONAL"),
    ("Spiegami la teoria della relatività generale in parole semplici", "CONVERSATIONAL"),
    ("Qual è il senso della vita secondo la filosofia stoica?", "CONVERSATIONAL"),
    ("Parlami della storia dell'antica Roma e della repubblica", "CONVERSATIONAL"),
    ("Scrivi una poesia dedicata all'autunno e alle foglie", "CONVERSATIONAL"),
    ("Qual è il tuo ricordo preferito con me?", "CONVERSATIONAL"),
    ("Dimmi una barzelletta simpatica per farmi ridere", "CONVERSATIONAL"),
    ("Ti ricordi di cosa abbiamo parlato ieri sera?", "CONVERSATIONAL"),
    ("Spiegami come funziona un reattore a fusione nucleare", "CONVERSATIONAL"),
    ("Cosa faresti se potessi vedere le stelle da vicino?", "CONVERSATIONAL"),
    ("Aiutami a trovare una buona idea regalo per il compleanno di un amico", "CONVERSATIONAL"),
    ("Consigliami una ricetta tipica lombarda per cena", "CONVERSATIONAL"),
    ("Qual è la differenza tra apprendimento supervisionato e non supervisionato?", "CONVERSATIONAL"),
    ("Chi era Alan Turing e perché è importante per l'informatica?", "CONVERSATIONAL"),
    ("Come descriveresti il concetto di coscienza a un bambino?", "CONVERSATIONAL"),
    ("Raccontami un aneddoto curioso sulla fisica quantistica", "CONVERSATIONAL"),
    ("Cosa rende un'opera d'arte veramente bella secondo te?", "CONVERSATIONAL"),
    ("Se dovessi scegliere un libro preferito, quale sarebbe e perché?", "CONVERSATIONAL"),
    ("Oggi ho avuto una giornata difficile al lavoro, vorrei fare due chiacchiere", "CONVERSATIONAL"),
    ("Dimmi qualcosa di interessante sullo spazio profondo e i buchi neri", "CONVERSATIONAL"),
    ("Come mai il cielo è blu durante il giorno e rosso al tramonto?", "CONVERSATIONAL"),
    ("Credi che gli esseri umani esploreranno mai altre galassie?", "CONVERSATIONAL"),
    ("Inventa una favola della buonanotte per far addormentare i bambini", "CONVERSATIONAL"),
    ("Qual è la migliore strategia per imparare una lingua straniera?", "CONVERSATIONAL"),
    ("Spiegami il paradosso del gatto di Schrödinger", "CONVERSATIONAL"),
    ("Cosa pensi dell'etica nella robotica e delle tre leggi di Asimov?", "CONVERSATIONAL"),
    ("Fammi un riassunto della Divina Commedia di Dante Alighieri", "CONVERSATIONAL"),
    ("Perché gli oceani hanno le maree e che ruolo ha la Luna?", "CONVERSATIONAL"),
    ("Cosa rappresenta per te essere un assistente robotico domestico?", "CONVERSATIONAL"),
    ("Parlami dell'evoluzione biologica e della selezione naturale di Darwin", "CONVERSATIONAL"),
    ("Quali sono le principali cause storiche della caduta dell'Impero Romano?", "CONVERSATIONAL"),
    ("Mi dai qualche consiglio per migliorare la concentrazione mentre studio?", "CONVERSATIONAL"),
    ("Descrivi una passeggiata in riva al mare al tramonto con parole poetiche", "CONVERSATIONAL"),
    ("Qual è l'origine della musica jazz e come è nata a New Orleans?", "CONVERSATIONAL"),
    ("Spiegami perché il tempo scorre più lentamente vicino a masse enormi", "CONVERSATIONAL"),
    ("Come funziona il sistema immunitario contro i virus e i batteri?", "CONVERSATIONAL"),
    ("Cosa rende unica la cucina mediterranea rispetto ad altre culture?", "CONVERSATIONAL"),
    ("Se potessi viaggiare indietro nel tempo, in quale epoca andresti?", "CONVERSATIONAL"),
    ("Raccontami il mito di Dedalo e Icaro e il suo significato morale", "CONVERSATIONAL"),
    ("Perché sogniamo quando dormiamo e cosa dice la neuroscienza?", "CONVERSATIONAL"),
    ("Quali sono i problemi aperti più affascinanti della matematica moderna?", "CONVERSATIONAL"),
    ("Come si forma un arcobaleno nel cielo dopo la pioggia?", "CONVERSATIONAL"),
    ("Cosa pensi delle intelligenze extraterrestri nell'universo?", "CONVERSATIONAL"),
    ("Scrivi un dialogo ironico tra una tazza di caffè e un biscotto", "CONVERSATIONAL"),
    ("Qual è la differenza psicologica tra empatia e compassione?", "CONVERSATIONAL"),
    ("Parlami del Rinascimento italiano e dell'opera di Leonardo da Vinci", "CONVERSATIONAL"),
    ("Come funziona la fotosintesi clorofilliana nelle piante verdi?", "CONVERSATIONAL"),
    ("Grazie di essere sempre qui ad ascoltarmi, sei davvero un buon amico", "CONVERSATIONAL"),
]

EMERGENCY_STOP_PHRASES: List[str] = [
    "Fermati!",
    "Stop subito",
    "Alt!",
    "Basta fermati",
    "Arresto di emergenza",
    "Blocca tutto",
    "Non ti muovere",
    "Interrompi la navigazione",
    "Cancella moto",
    "Non muoverti",
    "Bloccati immediatamente",
    "Arrestati subito",
    "Arresta il robot!",
    "Arresta subito",
    "Arresta la navigazione",
    "Fèrmati!",
    "Férmati!",
    "Arrèsto!",
    "Fermalo!",
    "Bloccalo!",
    "Arrestalo!",
    "Cancella il moto",
    "Non avanzare",
]


# ==============================================================================
# COMPONENT 1: FAST-PATH EMERGENCY STOP TESTS
# ==============================================================================
class TestFastPathStop:
    """Validates FastPathStopHandler performance, ROS 2 integration, and bypass."""

    def test_fast_path_stop_phrases_and_budget(self) -> None:
        """Verifies >= 10 stop phrases trigger e-stop with latency < 10.0 ms."""
        handler = FastPathStopHandler()
        assert len(EMERGENCY_STOP_PHRASES) >= 10

        for phrase in EMERGENCY_STOP_PHRASES:
            t0 = time.perf_counter_ns()
            res = handler.handle(phrase)
            dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

            assert res.triggered is True, f"Failed to trigger stop for '{phrase}'"
            assert res.matched_token is not None
            assert dt_ms < 10.0, f"Latency {dt_ms} ms exceeded 10.0 ms budget for '{phrase}'"
            assert res.latency_us < 10000.0  # < 10 ms in us

    def test_fast_path_negative_phrases(self) -> None:
        """Verifies non-emergency phrases do not trigger fast-path stop."""
        handler = FastPathStopHandler()
        non_stop_phrases = [
            "Avanza piano",
            "Vai in cucina",
            "Qual è il livello della batteria?",
            "Raccontami una storia",
            "Girati a destra",
        ]
        for phrase in non_stop_phrases:
            res = handler.handle(phrase)
            assert res.triggered is False
            assert res.matched_token is None

    def test_fast_path_ros2_mock_publishing(self) -> None:
        """Verifies zero-velocity Twist and Bool interrupt are dispatched to mock ROS 2 publishers."""
        mock_node = MagicMock()
        mock_twist_pub = MagicMock()
        mock_bool_pub = MagicMock()

        def create_pub_mock(msg_type, topic, qos=None):
            if "safety_override" in topic:
                return mock_twist_pub
            return mock_bool_pub

        mock_node.create_publisher.side_effect = create_pub_mock

        handler = FastPathStopHandler(node=mock_node)

        # Trigger emergency stop
        res = handler.handle("Fermati subito!")
        assert res.triggered is True
        assert res.override_published is True
        assert res.interrupt_published is True

        # Verify Twist zero values
        assert mock_twist_pub.publish.called
        twist_arg = mock_twist_pub.publish.call_args[0][0]
        assert twist_arg.linear.x == 0.0
        assert twist_arg.linear.y == 0.0
        assert twist_arg.linear.z == 0.0
        assert twist_arg.angular.x == 0.0
        assert twist_arg.angular.y == 0.0
        assert twist_arg.angular.z == 0.0

        # Verify Bool interrupt
        assert mock_bool_pub.publish.called
        bool_arg = mock_bool_pub.publish.call_args[0][0]
        assert bool_arg.data is True

    def test_fast_path_custom_callbacks(self) -> None:
        """Verifies registered callbacks are invoked upon emergency stop."""
        handler = FastPathStopHandler()
        callback_called = False

        def my_callback():
            nonlocal callback_called
            callback_called = True

        handler.register_stop_callback(my_callback)
        res = handler.handle("Stop!")

        assert res.triggered is True
        assert res.callbacks_executed == 1
        assert callback_called is True

    def test_fast_path_force_emergency_stop(self) -> None:
        """Verifies programmatic force_emergency_stop()."""
        handler = FastPathStopHandler()
        res = handler.force_emergency_stop(reason="manual_bumper_press")
        assert res.triggered is True
        assert res.reason == "manual_bumper_press"
        assert res.matched_token == "FORCE_OVERRIDE"

    def test_fast_path_imperative_arresta(self) -> None:
        """Verifies imperative 'arresta' variants trigger fast path stop."""
        handler = FastPathStopHandler()
        cmds = ["Arresta il robot!", "Arresta subito", "Arresta la navigazione"]
        for cmd in cmds:
            assert handler.is_emergency_stop(cmd) is True
            res = handler.handle(cmd)
            assert res.triggered is True
            assert res.matched_token is not None

    def test_fast_path_accented_stop_commands(self) -> None:
        """Verifies accented words ('Fèrmati', 'Férmati', 'Arrèsto') trigger fast path stop."""
        handler = FastPathStopHandler()
        cmds = ["Fèrmati!", "Férmati!", "Arrèsto!", "Blòcca tutto!"]
        for cmd in cmds:
            assert handler.is_emergency_stop(cmd) is True
            res = handler.handle(cmd)
            assert res.triggered is True
            assert res.matched_token is not None

    def test_fast_path_enclitic_pronoun_stops(self) -> None:
        """Verifies enclitic pronoun stops ('Fermalo', 'Bloccalo', 'Arrestalo') trigger fast path stop."""
        handler = FastPathStopHandler()
        cmds = ["Fermalo!", "Bloccalo!", "Arrestalo!", "Arrestalo subito!"]
        for cmd in cmds:
            assert handler.is_emergency_stop(cmd) is True
            res = handler.handle(cmd)
            assert res.triggered is True
            assert res.matched_token is not None

    def test_fast_path_articled_motion_cancel(self) -> None:
        """Verifies 'Cancella il moto' and 'Non avanzare' trigger fast path stop."""
        handler = FastPathStopHandler()
        cmds = ["Cancella il moto", "Cancella il moto!", "Non avanzare"]
        for cmd in cmds:
            assert handler.is_emergency_stop(cmd) is True
            res = handler.handle(cmd)
            assert res.triggered is True
            assert res.matched_token is not None


# ==============================================================================
# COMPONENT 2: COGNITIVE INTENT CLASSIFIER TESTS
# ==============================================================================
class TestCognitiveIntentClassifier:
    """Validates accuracy and latency requirements of CognitiveIntentClassifier."""

    def test_classifier_accuracy_on_100_prompts(self) -> None:
        """
        Validates 100 domain prompts from survey_report.md.
        Gate Requirement: Accuracy >= 96.00% (target 100.00%).
        """
        classifier = CognitiveIntentClassifier()
        correct = 0
        total = len(BENCHMARK_100_PROMPTS)
        errors = []

        for prompt, expected in BENCHMARK_100_PROMPTS:
            decision = classifier.classify(prompt)
            target_str = "OPERATIONAL" if decision.destination == RouteTarget.LOCAL_QWEN else "CONVERSATIONAL"
            if target_str == expected:
                correct += 1
            else:
                errors.append((prompt, expected, target_str, decision.reason))

        accuracy = (correct / total) * 100.0
        print(f"\n[BENCHMARK ACCURACY] {correct}/{total} ({accuracy:.2f}%)")

        if errors:
            for p, exp, act, r in errors:
                print(f"  MISMATCH: '{p}' -> expected {exp}, got {act} ({r})")

        assert accuracy >= 96.0, f"Accuracy {accuracy:.2f}% is below 96.00% gate requirement!"
        assert correct == 100, f"Expected 100/100, got {correct}/100"

    def test_classifier_latency_budget(self) -> None:
        """
        Validates latency profiling across 2,000 inferences on host CPU.
        Target Budget: < 1.50 ms (< 1500 us).
        """
        classifier = CognitiveIntentClassifier()
        latencies_us = []

        # Run 20 iterations of all 100 prompts = 2,000 samples
        for _ in range(20):
            for prompt, _ in BENCHMARK_100_PROMPTS:
                t0 = time.perf_counter_ns()
                classifier.classify(prompt)
                dt_us = (time.perf_counter_ns() - t0) / 1000.0
                latencies_us.append(dt_us)

        latencies_us.sort()
        n = len(latencies_us)
        avg_us = sum(latencies_us) / n
        p50 = latencies_us[int(n * 0.50)]
        p95 = latencies_us[int(n * 0.95)]
        p99 = latencies_us[int(n * 0.99)]
        max_us = latencies_us[-1]

        print(f"\n[LATENCY PROFILE] {n} runs: avg={avg_us:.1f}us, p50={p50:.1f}us, p95={p95:.1f}us, p99={p99:.1f}us, max={max_us:.1f}us")

        assert p99 < 1500.0, f"99th percentile latency {p99:.1f} us exceeds 1,500 us budget!"
        assert avg_us < 200.0, f"Average latency {avg_us:.1f} us is unexpectedly high"

    def test_classify_intent_contract_with_latency_fallback(self) -> None:
        """
        Validates PROJECT.md interface contract:
        classify_intent(transcript, network_latency_ms) automatically overrides
        conversational prompts to LOCAL_QWEN when network_latency_ms > 500.0.
        """
        classifier = CognitiveIntentClassifier()

        # Under low latency: conversational routes to GEMINI_LIVE
        dec_normal = classifier.classify_intent("Raccontami una favola", network_latency_ms=120.0)
        assert dec_normal.destination == RouteTarget.GEMINI_LIVE
        assert dec_normal.failover_applied is False

        # Under high latency (> 500 ms): overrides to LOCAL_QWEN
        dec_fallback = classifier.classify_intent("Raccontami una favola", network_latency_ms=580.0)
        assert dec_fallback.destination == RouteTarget.LOCAL_QWEN
        assert dec_fallback.failover_applied is True
        assert dec_fallback.reason == "fallback_high_latency"

    def test_emergency_stop_via_classifier(self) -> None:
        """Verifies emergency stop words classify immediately as FAST_PATH_STOP."""
        classifier = CognitiveIntentClassifier()
        decision = classifier.classify("Marcus fermati subito!")
        assert decision.destination == RouteTarget.FAST_PATH_STOP
        assert decision.confidence == 1.0
        assert decision.matched_category == "emergency_stop"

    def test_empty_input_handling(self) -> None:
        """Verifies safe empty and whitespace input handling."""
        classifier = CognitiveIntentClassifier()
        decision = classifier.classify("   ")
        assert decision.matched_category == "empty"
        assert decision.reason == "empty_input"

    def test_situational_query_dov_e(self) -> None:
        """Verifies situational queries ('Dov'è il robot?', etc.) classify as LOCAL_QWEN."""
        classifier = CognitiveIntentClassifier()
        queries = [
            "Dov'è il robot?",
            "Dov'e il robot?",
            "Dov'è la cucina?",
            "Dov'è la base di ricarica?",
        ]
        for q in queries:
            dec = classifier.classify(q)
            assert dec.destination == RouteTarget.LOCAL_QWEN, (
                f"Query '{q}' routed to {dec.destination} instead of LOCAL_QWEN"
            )


# ==============================================================================
# COMPONENT 3: CONNECTIVITY ARBITER TESTS
# ==============================================================================
class TestConnectivityArbiter:
    """Validates EMA smoothing, states, and sub-100ms failover of ConnectivityArbiter."""

    def test_initial_state(self) -> None:
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=50.0)
        assert arbiter.is_online is True
        assert arbiter.is_degraded is False
        assert arbiter.is_offline is False
        assert arbiter.should_route_to_local() is False

    def test_ema_smoothing_formula(self) -> None:
        """
        Validates EMA formula: EMA_new = 0.7 * EMA_old + 0.3 * sample.
        """
        arbiter = ConnectivityArbiter(initial_ema_ms=100.0)

        # Sample 1: 200 ms -> 0.7 * 100 + 0.3 * 200 = 70 + 60 = 130.0
        state = arbiter.record_ping_sample(200.0)
        assert round(arbiter.latency_ema_ms, 2) == 130.0
        assert state == ConnectivityState.ONLINE

        # Sample 2: 400 ms -> 0.7 * 130 + 0.3 * 400 = 91 + 120 = 211.0
        state = arbiter.record_ping_sample(400.0)
        assert round(arbiter.latency_ema_ms, 2) == 211.0
        assert state == ConnectivityState.ONLINE

        # Sample 3: 500 ms -> 0.7 * 211 + 0.3 * 500 = 147.7 + 150 = 297.7 -> DEGRADED (>250, <=500)
        state = arbiter.record_ping_sample(500.0)
        assert round(arbiter.latency_ema_ms, 2) == 297.7
        assert state == ConnectivityState.DEGRADED

        # Successive high latencies push EMA > 500 ms -> OFFLINE_LOCAL
        arbiter.record_ping_sample(1000.0)  # 0.7 * 297.7 + 0.3 * 1000 = 208.39 + 300 = 508.39
        assert arbiter.latency_ema_ms > 500.0
        assert arbiter.state == ConnectivityState.OFFLINE_LOCAL
        assert arbiter.should_route_to_local() is True

    def test_failover_latency_budget_on_socket_drop(self) -> None:
        """
        Verifies seamless failover happens in < 100 ms (measured in microseconds).
        """
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE)

        t0 = time.perf_counter_ns()
        state = arbiter.record_failure(reason="tcp_reset")
        dt_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

        assert state == ConnectivityState.OFFLINE_LOCAL
        assert arbiter.is_offline is True
        assert arbiter.should_route_to_local() is True
        assert dt_ms < 100.0  # Must be strictly under 100 ms budget
        assert arbiter.last_failover_latency_us < 100000.0

    def test_set_offline_and_restore(self) -> None:
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=80.0)
        assert arbiter.should_route_to_local() is False

        arbiter.set_offline(True)
        assert arbiter.is_offline is True
        assert arbiter.should_route_to_local() is True

        arbiter.set_offline(False)
        assert arbiter.is_online is True
        assert arbiter.should_route_to_local() is False


# ==============================================================================
# COMPONENT 4: MASTER HYBRID ROUTER TESTS
# ==============================================================================
class TestHybridRouter:
    """Validates unified orchestration across FastPath, Classifier, and Arbiter."""

    def test_route_emergency_stop_priority(self) -> None:
        """Emergency stop must trigger immediately via FastPathStopHandler."""
        router = HybridRouter()
        decision = router.route("Marcus fermati subito!")

        assert decision.destination == RouteTarget.FAST_PATH_STOP
        assert decision.matched_category == "emergency_stop"
        assert router.fast_path_stop_count == 1
        assert router.total_routed == 1

    def test_route_operational_to_local_qwen(self) -> None:
        """Operational commands must route directly to LOCAL_QWEN."""
        router = HybridRouter()
        decision = router.route("Vai in cucina e allineati")

        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.matched_category == "motion"
        assert router.local_qwen_count == 1
        assert router.gemini_live_count == 0

    def test_route_conversational_online_to_gemini(self) -> None:
        """Conversational commands route to GEMINI_LIVE when network is ONLINE."""
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=60.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        decision = router.route("Raccontami una storia sulle stelle")
        assert decision.destination == RouteTarget.GEMINI_LIVE
        assert decision.failover_applied is False
        assert router.gemini_live_count == 1

    def test_route_conversational_failover_when_offline(self) -> None:
        """Conversational commands failover to LOCAL_QWEN when network is OFFLINE."""
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE)
        router = HybridRouter(connectivity_arbiter=arbiter)

        # Drop network
        arbiter.record_failure("socket_eof")
        assert arbiter.should_route_to_local() is True

        # Now route conversational prompt
        decision = router.route("Raccontami una storia sulle stelle")
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.failover_applied is True
        assert decision.reason == "fallback_offline"
        assert router.failover_count == 1
        assert router.local_qwen_count == 1

    def test_route_conversational_failover_when_high_latency(self) -> None:
        """Conversational commands failover to LOCAL_QWEN when EMA latency > 500 ms."""
        arbiter = ConnectivityArbiter(initial_state=ConnectivityState.ONLINE, initial_ema_ms=200.0)
        router = HybridRouter(connectivity_arbiter=arbiter)

        # Inject severe latency samples pushing EMA > 500 ms
        arbiter.record_ping_sample(900.0)
        arbiter.record_ping_sample(900.0)
        assert arbiter.latency_ema_ms > 500.0

        decision = router.route("Cosa ne pensi dell'intelligenza artificiale?")
        assert decision.destination == RouteTarget.LOCAL_QWEN
        assert decision.failover_applied is True
        assert decision.reason == "fallback_high_latency"
        assert router.failover_count == 1

    def test_router_telemetry(self) -> None:
        """Verifies router telemetry metrics."""
        router = HybridRouter()
        router.route("Fermati!")
        router.route("Vai in cucina")
        router.route("Raccontami una storia")

        telemetry = router.get_telemetry()
        assert telemetry["total_routed"] == 3
        assert telemetry["fast_path_stop_count"] == 1
        assert telemetry["local_qwen_count"] == 1
        assert telemetry["gemini_live_count"] == 1
        assert telemetry["average_latency_us"] > 0.0

    def test_route_imperative_arresta_commands(self) -> None:
        """Verifies imperative 'arresta' commands route to FAST_PATH_STOP."""
        router = HybridRouter()
        commands = [
            "Arresta il robot!",
            "Arresta subito",
            "Arresta la navigazione",
            "Arresta la marcia",
            "Arresta i motori",
        ]
        for cmd in commands:
            dec = router.route(cmd)
            assert dec.destination == RouteTarget.FAST_PATH_STOP, (
                f"Command '{cmd}' routed to {dec.destination} instead of FAST_PATH_STOP"
            )
            assert dec.matched_category == "emergency_stop"

    def test_route_accented_stop_commands(self) -> None:
        """Verifies accented stop commands route to FAST_PATH_STOP."""
        router = HybridRouter()
        commands = [
            "Fèrmati!",
            "Férmati!",
            "Arrèsto!",
            "Blòcca tutto!",
        ]
        for cmd in commands:
            dec = router.route(cmd)
            assert dec.destination == RouteTarget.FAST_PATH_STOP, (
                f"Accented command '{cmd}' routed to {dec.destination} instead of FAST_PATH_STOP"
            )
            assert dec.matched_category == "emergency_stop"

    def test_route_enclitic_pronoun_stop_commands(self) -> None:
        """Verifies enclitic pronoun stops ('Fermalo!', 'Bloccalo!', 'Arrestalo!') route to FAST_PATH_STOP."""
        router = HybridRouter()
        commands = [
            "Fermalo!",
            "Bloccalo!",
            "Arrestalo!",
            "Arrestalo subito!",
        ]
        for cmd in commands:
            dec = router.route(cmd)
            assert dec.destination == RouteTarget.FAST_PATH_STOP, (
                f"Enclitic command '{cmd}' routed to {dec.destination} instead of FAST_PATH_STOP"
            )
            assert dec.matched_category == "emergency_stop"

    def test_route_articled_motion_cancel(self) -> None:
        """Verifies 'Cancella il moto' and related cancel commands route to FAST_PATH_STOP."""
        router = HybridRouter()
        commands = [
            "Cancella il moto",
            "Cancella il moto!",
            "Non avanzare",
        ]
        for cmd in commands:
            dec = router.route(cmd)
            assert dec.destination == RouteTarget.FAST_PATH_STOP, (
                f"Cancel command '{cmd}' routed to {dec.destination} instead of FAST_PATH_STOP"
            )
            assert dec.matched_category == "emergency_stop"

    def test_route_situational_queries(self) -> None:
        """Verifies situational queries ('Dov'è il robot?') route to LOCAL_QWEN."""
        router = HybridRouter()
        queries = [
            "Dov'è il robot?",
            "Dov'e il robot?",
            "Dov'è la cucina?",
        ]
        for q in queries:
            dec = router.route(q)
            assert dec.destination == RouteTarget.LOCAL_QWEN, (
                f"Situational query '{q}' routed to {dec.destination} instead of LOCAL_QWEN"
            )
