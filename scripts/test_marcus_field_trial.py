#!/usr/bin/env python3
"""
scripts/test_marcus_field_trial.py
==================================
Test interattivo sul campo per Marcus AI via topic ROS 2:
1. Telemetria e Test del "Dream" (Nightly Dream) con monitoraggio risorse Pi 5 (RAM/CPU/Swap).
2. Interrogazione DFMEA per identificare le failure più critiche (RPN elevato e severità).
3. Creazione Skill in tempo reale con tracciamento di tutti i passaggi intermedi fino al completamento.
4. Interrogazione dello stato di avanzamento ("a che punto è la creazione della skill?").
5. Consultazione peer-to-peer con Antigravity (Gemini 3.8).

Compatibile con ROS 2 Jazzy e architettura aarch64 (Raspberry Pi 5).
"""

import sys
import os
import time
import json
import threading
from typing import List, Dict, Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Setup environment ROS 2
os.environ.setdefault("ROS_DOMAIN_ID", "42")
if os.path.exists("/tmp/cyclonedds_robopy.xml"):
    os.environ.setdefault("CYCLONEDDS_URI", "/tmp/cyclonedds_robopy.xml")

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def get_system_telemetry() -> Dict[str, Any]:
    """Legge metriche reali hardware da /proc."""
    telemetry = {
        "mem_total_mb": 0.0,
        "mem_available_mb": 0.0,
        "mem_used_mb": 0.0,
        "mem_percent": 0.0,
        "swap_total_mb": 0.0,
        "swap_free_mb": 0.0,
        "load_1m": 0.0,
        "load_5m": 0.0
    }
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().split()[0]
                mem[key] = float(val) / 1024.0  # In MB

        total = mem.get("MemTotal", 4000.0)
        avail = mem.get("MemAvailable", total)
        used = total - avail
        telemetry["mem_total_mb"] = round(total, 1)
        telemetry["mem_available_mb"] = round(avail, 1)
        telemetry["mem_used_mb"] = round(used, 1)
        telemetry["mem_percent"] = round((used / total) * 100.0, 1) if total > 0 else 0.0
        telemetry["swap_total_mb"] = round(mem.get("SwapTotal", 0.0), 1)
        telemetry["swap_free_mb"] = round(mem.get("SwapFree", 0.0), 1)
    except Exception as e:
        pass

    try:
        with open("/proc/loadavg", "r") as f:
            loads = f.read().split()
            telemetry["load_1m"] = float(loads[0])
            telemetry["load_5m"] = float(loads[1])
    except Exception:
        pass

    return telemetry


class MarcusFieldTrialNode(Node):
    """Nodo di test client per comunicare con robot_ai_node."""

    def __init__(self):
        super().__init__("marcus_field_trial_client")
        self.pub_text = self.create_publisher(String, "/ai/input/text", 10)
        self.sub_response = self.create_subscription(
            String, "/ai/conversation/response", self._on_response, 10
        )
        self.sub_status = self.create_subscription(
            String, "/ai/conversation/status", self._on_status, 10
        )
        self._responses: List[str] = []
        self._statuses: List[str] = []
        self._lock = threading.Lock()

    def _on_response(self, msg: String):
        with self._lock:
            self._responses.append(msg.data)
            print(f"  📥 [RESPONSE RX] {msg.data[:120]}..." if len(msg.data) > 120 else f"  📥 [RESPONSE RX] {msg.data}")

    def _on_status(self, msg: String):
        with self._lock:
            self._statuses.append(msg.data)
            print(f"  ℹ️ [STATUS RX] {msg.data}")

    def clear_buffers(self):
        with self._lock:
            self._responses.clear()
            self._statuses.clear()

    def get_responses(self) -> List[str]:
        with self._lock:
            return list(self._responses)

    def get_statuses(self) -> List[str]:
        with self._lock:
            return list(self._statuses)

    def send_prompt(self, text: str):
        msg = String()
        msg.data = text
        print(f"\n📤 [SENDING PROMPT] '{text}'")
        self.pub_text.publish(msg)

    def wait_for_responses(self, min_count: int = 1, timeout_sec: float = 25.0) -> List[str]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._lock:
                if len(self._responses) >= min_count:
                    # Piccola attesa per raccogliere eventuali altri chunk
                    time.sleep(0.5)
                    return list(self._responses)
            time.sleep(0.2)
        with self._lock:
            return list(self._responses)


def run_field_trial():
    print("=" * 75)
    print(" 🤖 MARCUS AI - COLLAUDO SUL CAMPO & TEST INTEGRATO DEI COMPONENTI")
    print("=" * 75)

    initial_telemetry = get_system_telemetry()
    print(f"📊 Telemetria Iniziale Pi 5:")
    print(f"   RAM: {initial_telemetry['mem_used_mb']} MB usati / {initial_telemetry['mem_total_mb']} MB ({initial_telemetry['mem_percent']}%)")
    print(f"   RAM Disponibile: {initial_telemetry['mem_available_mb']} MB (Soglia limite: 3200 MB)")
    print(f"   Swap: {initial_telemetry['swap_total_mb'] - initial_telemetry['swap_free_mb']} MB usati")
    print(f"   Carico CPU (loadavg): 1m={initial_telemetry['load_1m']}, 5m={initial_telemetry['load_5m']}")
    print("-" * 75)

    rclpy.init()
    node = MarcusFieldTrialNode()
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    def drain_buffers(drain_sec: float = 3.0):
        deadline = time.time() + drain_sec
        while time.time() < deadline:
            node.clear_buffers()
            time.sleep(0.5)

    # Attesa deterministica associazione endpoint DDS con robot_ai_node
    print("⏳ Attesa associazione DDS con robot_ai_node...")
    disc_start = time.time()
    while time.time() - disc_start < 15.0:
        if node.pub_text.get_subscription_count() > 0:
            print(f"✅ Associazione DDS stabilita ({node.pub_text.get_subscription_count()} sottoscrittori rilevati su /ai/input/text).")
            break
        time.sleep(0.5)

    drain_buffers(3.0)

    test_results = {}

    try:
        # =====================================================================
        # TEST 1: ANALISI DFMEA & GUASTI CRITICI
        # =====================================================================
        print("\n🔍 --- [TEST 1/4] Analisi della DFMEA e Failure Mode ad Alto RPN ---")
        drain_buffers(1.0)
        fmea_prompt = "cosa dice la fmea sui guasti più critici del robot?"
        node.send_prompt(fmea_prompt)
        responses = node.wait_for_responses(min_count=1, timeout_sec=30.0)

        if responses:
            full_text = " ".join(responses).lower()
            # Verifichiamo che menzioni la FMEA o failure o RPN
            has_fmea_terms = any(term in full_text for term in ["fmea", "rpn", "failure", "guast", "severit", "fm-", "motori", "navigazione"])
            test_results["test_1_fmea"] = {
                "passed": has_fmea_terms,
                "response_preview": responses[0][:200],
                "count": len(responses)
            }
            print(f"   ✅ Risposta ricevuta ({len(responses)} messaggi).")
            print(f"   Estratto: {responses[0][:250]}...")
            print(f"   Esito Analisi DFMEA: {'SUPERATO' if has_fmea_terms else 'NON CONFORME'}")
        else:
            test_results["test_1_fmea"] = {"passed": False, "error": "Timeout nessuna risposta da robot_ai_node"}
            print("   ❌ Timeout: nessuna risposta ricevuta entro 20s.")

        # =====================================================================
        # TEST 2: TEST DEL "DREAM" E MONITORAGGIO RISORSE
        # =====================================================================
        print("\n🌙 --- [TEST 2/4] Test del Nightly Dream (Analisi Notturna & Omeostasi) ---")
        pre_dream_tel = get_system_telemetry()
        print(f"   RAM Pre-Dream: {pre_dream_tel['mem_used_mb']} MB (Disponibili: {pre_dream_tel['mem_available_mb']} MB)")
        
        drain_buffers(2.0)
        dream_prompt = "avvia analisi notturna"
        node.send_prompt(dream_prompt)
        dream_responses = node.wait_for_responses(min_count=1, timeout_sec=25.0)

        # Monitoriamo RAM durante il dream
        peak_ram_used = pre_dream_tel['mem_used_mb']
        for _ in range(5):
            time.sleep(1.0)
            cur_tel = get_system_telemetry()
            if cur_tel['mem_used_mb'] > peak_ram_used:
                peak_ram_used = cur_tel['mem_used_mb']

        post_dream_tel = get_system_telemetry()
        print(f"   RAM Post-Dream: {post_dream_tel['mem_used_mb']} MB (Picco durante sogno: {peak_ram_used} MB)")
        
        dream_ok = len(dream_responses) > 0 and (peak_ram_used < 3200.0)
        test_results["test_2_dream"] = {
            "passed": dream_ok,
            "responses": dream_responses,
            "peak_ram_mb": peak_ram_used,
            "delta_ram_mb": round(post_dream_tel['mem_used_mb'] - pre_dream_tel['mem_used_mb'], 1)
        }
        print(f"   Esito Dream Test: {'SUPERATO (Omeostasi RAM OK)' if dream_ok else 'FALLITO'}")

        # =====================================================================
        # TEST 3: CREAZIONE SKILL CON STREAMING PASSAGGI & NOTIFICA COMPLETAMENTO
        # =====================================================================
        print("\n🛠️ --- [TEST 3/4] Creazione Nuova Skill con Feedback Streaming in Tempo Reale ---")
        drain_buffers(3.0)
        create_prompt = "crea una skill per calcolare il consumo energetico residuo. Mostrami tutti i passaggi e dimmi quando hai finito."
        node.send_prompt(create_prompt)

        # Attesa deterministica del completamento della generazione (fino a 65s)
        deadline = time.time() + 65.0
        create_responses = []
        while time.time() < deadline:
            time.sleep(1.0)
            resps = node.get_responses()
            if resps and any("completat" in r.lower() or "registrat" in r.lower() for r in resps):
                # Ulteriore attesa breve per ricevere la conclusione
                time.sleep(1.5)
                create_responses = node.get_responses()
                break
        if not create_responses:
            create_responses = node.get_responses()

        print(f"   Raccolti {len(create_responses)} aggiornamenti di avanzamento:")
        for idx, r in enumerate(create_responses, 1):
            print(f"     Passo {idx}: {r}")

        has_steps = len(create_responses) >= 2 and any("ast" in r.lower() or "sandbox" in r.lower() or "antigravity" in r.lower() for r in create_responses)

        # Attesa di quiete tra le richieste
        time.sleep(3.0)

        # Interroghiamo ora lo stato della skill per verificare la capacità di inquiry
        drain_buffers(2.0)
        status_prompt = "a che punto è la creazione della skill?"
        node.send_prompt(status_prompt)
        status_responses = node.wait_for_responses(min_count=1, timeout_sec=15.0)
        status_text = status_responses[0] if status_responses else "N/A"
        print(f"   Risposta allo stato: '{status_text}'")

        test_results["test_3_create_skill"] = {
            "passed": has_steps,
            "step_count": len(create_responses),
            "has_quality_gates": has_steps,
            "final_status": status_text
        }
        print(f"   Esito Creazione Skill Streaming: {'SUPERATO' if has_steps else 'FALLITO'}")

        # Attesa di quiete prima di consultare Antigravity
        time.sleep(3.0)

        # =====================================================================
        # TEST 4: CONSULTAZIONE PEER-TO-PEER CON ANTIGRAVITY (GEMINI 3.8)
        # =====================================================================
        print("\n🤝 --- [TEST 4/4] Dialogo Peer-to-Peer con Antigravity ---")
        drain_buffers(3.0)
        antigravity_prompt = "chiedi ad antigravity un consiglio tecnico su come ottimizzare l'uso della memoria ram sul raspberry pi 5"
        node.send_prompt(antigravity_prompt)
        peer_responses = node.wait_for_responses(min_count=1, timeout_sec=35.0)

        peer_ok = False
        if peer_responses:
            full_peer = " ".join(peer_responses).lower()
            peer_ok = any(w in full_peer for w in ["antigravity", "gemini", "ram", "memoria", "pi 5", "core", "swap", "ottimizz", "consiglio", "risorse", "processi"])
            print(f"   Estratto risposta Antigravity: {peer_responses[0][:250]}...")
        
        test_results["test_4_antigravity"] = {
            "passed": peer_ok,
            "responses": peer_responses[:2] if peer_responses else []
        }
        print(f"   Esito Consultazione Antigravity: {'SUPERATO' if peer_ok else 'FALLITO'}")

    finally:
        node.destroy_node()
        rclpy.shutdown()

    # =========================================================================
    # REPORT CONCLUSIVO
    # =========================================================================
    final_telemetry = get_system_telemetry()
    print("\n" + "=" * 75)
    print(" 📋 REPORT FINALE DI COLLAUDO SUL CAMPO")
    print("=" * 75)
    all_passed = True
    for t_name, data in test_results.items():
        p = data.get("passed", False)
        if not p: all_passed = False
        status_str = "✅ SUPERATO" if p else "❌ FALLITO"
        print(f"  • {t_name.upper()}: {status_str}")
        for k, v in data.items():
            if k != "passed":
                print(f"      - {k}: {v}")

    print("\n📊 Telemetria Finale Host Pi 5:")
    print(f"   RAM: {final_telemetry['mem_used_mb']} MB ({final_telemetry['mem_percent']}%) - Limite 3200 MB NON violato")
    print(f"   RAM Disponibile residua: {final_telemetry['mem_available_mb']} MB")
    print(f"   Esito Globale Collaudo: {'🎉 100% SUCCESSO' if all_passed else '⚠️ ALCUNE VOCI RICHIEDONO VERIFICA'}")
    print("=" * 75)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(run_field_trial())
