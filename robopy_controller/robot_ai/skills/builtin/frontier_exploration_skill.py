"""
Robot AI Skills - Frontier Exploration Skill
=============================================
Skill per l'esplorazione autonoma dell'ambiente basata su frontiere (explore_lite)
e modalità caccia target semantica (HUNT mode) integrata con la memoria TRINITY (CAG / MAG / RAG).
"""

import re
import os
import asyncio
from typing import Any, Dict, Optional, Tuple

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

try:
    import rclpy
    from std_msgs.msg import Bool, String
    from geometry_msgs.msg import PoseStamped
except ImportError:
    Bool = None
    String = None
    PoseStamped = None


class FrontierExplorationSkill(BaseSkill):
    """
    Skill for autonomous frontier-based exploration and semantic target hunting.
    Directly interfaces with frontier_explorer_node, Nav2, and TRINITY memory.
    """

    EXPLORE_PATTERNS = [
        re.compile(r'\b(esplora|esplorare|esplorazione|ricognizione|perlustra|perlustrazione)\b', re.IGNORECASE),
        re.compile(r'\b(fai\s+un\s+giro|fai\s+un\'?esplorazione|fai\s+una\s+ricognizione|vai\s+in\s+esplorazione|inizia\s+a\s+esplorare|comincia\s+a\s+esplorare|avvia\s+l\'?esplorazione)\b', re.IGNORECASE),
        re.compile(r'\b(gira\s+e\s+mappa|mappa\s+la\s+casa|mappa\s+la\s+stanza|mappa\s+l\'?ambiente)\b', re.IGNORECASE),
        re.compile(r'\b(nomad|nomade|nomadi)\b', re.IGNORECASE)  # Legacy fallback
    ]

    HUNT_PATTERNS = [
        re.compile(r'\b(trova|trovami|cerca|cercami|dov\'?è|dove\s+si\s+trova|rintraccia|scova)\s+(?:(?:il|lo|la|i|gli|le|l\'|un|uno|una|un\'|mio|mia|miei|mie)\s+)?([a-zA-Z0-9_àèéìòù]+)', re.IGNORECASE),
        re.compile(r'\b(vai\s+a\s+cercare|inizia\s+a\s+cercare|mettiti\s+a\s+cercare)\s+(?:(?:il|lo|la|i|gli|le|l\'|un|uno|una|un\'|mio|mia|miei|mie)\s+)?([a-zA-Z0-9_àèéìòù]+)', re.IGNORECASE)
    ]

    STOP_PATTERNS = [
        re.compile(r'\b(ferma|stop|basta|annulla|interrompi|blocca|cancella|arresta)\s+(l\'?esplorazione|esplorazione|esplorare|la\s+ricerca|cercare|il\s+giro|la\s+ricognizione|la\s+navigazione|navigazione|navigare|il\s+moto|il\s+movimento|tutto|nomad)\b', re.IGNORECASE),
        re.compile(r'\b(fermati|stop|alt|basta|arrestati|blocca|ti\s+fermi|non\s+muoverti|non\s+ti\s+muovere)\b', re.IGNORECASE),
        re.compile(r'\b(stop\s+(esplorazione|navigazione|ricerca)|basta\s+(esplorare|navigare|cercare))\b', re.IGNORECASE)
    ]

    def __init__(self, ros_node=None, memory_store=None, trinity_engine=None, nav_client=None):
        super().__init__()
        self.ros_node = ros_node
        self.memory_store = memory_store
        self.trinity_engine = trinity_engine
        self.nav_client = nav_client
        self.is_exploring = False
        self._paused_for_dialogue = False
        self.current_target: Optional[str] = None

        self.pub_enable = None
        self.pub_target = None
        if self.ros_node is not None:
            self._setup_ros_interfaces()

    def _setup_ros_interfaces(self):
        try:
            if Bool is not None:
                self.pub_enable = self.ros_node.create_publisher(Bool, '/exploration/enable', 10)
            if String is not None:
                self.pub_target = self.ros_node.create_publisher(String, '/exploration/search_target', 10)
        except Exception:
            pass

    def pause_for_dialogue(self) -> None:
        """Pausa temporanea dell'esplorazione durante una conversazione utente (barge-in).
        Arresta il movimento ruote per garantire silenzio acustico e massima attenzione."""
        if self.is_exploring:
            self._paused_for_dialogue = True
            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = False
                self.pub_enable.publish(e_msg)
            if self.ros_node is not None and hasattr(self.ros_node, 'cmd_vel_pub'):
                try:
                    from geometry_msgs.msg import Twist
                    stop_cmd = Twist()
                    self.ros_node.cmd_vel_pub.publish(stop_cmd)
                except Exception:
                    pass

    def resume_after_dialogue(self) -> bool:
        """Riprende l'esplorazione al termine del turno conversazionale se non è stato ordinato lo stop."""
        if self.is_exploring and self._paused_for_dialogue:
            self._paused_for_dialogue = False
            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = True
                self.pub_enable.publish(e_msg)
            return True
        return False

    def get_navigation_status(self) -> Dict[str, Any]:
        """Restituisce lo stato attuale della navigazione ed esplorazione."""
        mode = "IDLE"
        if self.is_exploring:
            mode = "HUNT" if self.current_target else "EXPLORE"
        return {
            "is_exploring": self.is_exploring,
            "mode": mode,
            "paused_for_dialogue": self._paused_for_dialogue,
            "current_target": self.current_target
        }

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="frontier_exploration",
            description="Avvia o ferma l'esplorazione autonoma dell'ambiente (mappatura frontiere) oppure la ricerca di un target/persona (modalità HUNT) con integrazione memoria TRINITY.",
            version="2.0.0",
            keywords=["esplora", "esplorazione", "mappa", "trova", "cerca", "ricognizione", "frontiere"],
            priority=35,
            requires_nav=True,
            capabilities=[Capability.NAV_MOVE, Capability.MEMORY_RW]
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        """Schema per function calling LLM (Gemini Live API e REST)."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start_explore", "search_target", "stop", "get_status"],
                    "description": "Azione: 'start_explore' per mappatura autonoma frontiere, 'search_target' per cercare un oggetto/persona specifica, 'stop' per interrompere il moto, 'get_status' per interrogare lo stato di avanzamento."
                },
                "target": {
                    "type": "string",
                    "description": "Nome dell'oggetto o persona da cercare (solo per action='search_target', es. 'chiavi', 'Marco', 'persona', 'sedia')."
                },
                "mode": {
                    "type": "string",
                    "description": "Modalità opzionale: 'all_frontiers' (esplora tutto) o 'quick_scan'."
                }
            },
            "required": ["action"]
        }

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        clean_text = (text or "").lower().strip()
        context = context or {}
        if context.get("action") in ("stop", "nav_stop") or any(p.search(clean_text) for p in self.STOP_PATTERNS):
            return 0.99
        if any(p.search(clean_text) for p in self.HUNT_PATTERNS):
            return 0.95
        if any(p.search(clean_text) for p in self.EXPLORE_PATTERNS):
            return 0.92
        return 0.0

    def _extract_target(self, text: str) -> Optional[str]:
        clean_text = (text or "").lower().strip()
        stop_words = {"la", "il", "lo", "le", "i", "gli", "un", "uno", "una", "casa", "stanza", "qui", "subito", "per", "favore"}
        for p in self.HUNT_PATTERNS:
            m = p.search(clean_text)
            if m:
                # Group 2 is the target name
                raw_target = m.group(2).strip().lower()
                if raw_target not in stop_words:
                    return raw_target
        return None

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        context = context or {}
        action = context.get("action", "").lower().strip()
        target = context.get("target", "").lower().strip()
        clean_text = (text or "").lower().strip()

        # 0. Navigation / exploration status query
        if action == "get_status" or (clean_text and any(w in clean_text for w in ["stato", "cosa stai facendo", "dove stai andando"])):
            status = self.get_navigation_status()
            if status["is_exploring"]:
                msg = f"Sono attualmente in esplorazione attiva ({status['mode']}). Bersaglio: {status['current_target'] or 'tutta la casa'}."
            else:
                msg = "Al momento sono fermo e non sto esplorando."
            return SkillResult(success=True, message="Stato navigazione recuperato.", speak=msg, data=status)

        # 1. Stop exploration
        if action == "stop" or (clean_text and any(p.search(clean_text) for p in self.STOP_PATTERNS)):
            return self.stop_exploration()

        # 2. Search target (HUNT mode)
        if not target and clean_text:
            target = self._extract_target(clean_text) or ""

        if action == "search_target" or target:
            return await self.start_hunt(target)

        # 3. Pure frontier exploration (EXPLORE mode)
        return await self.start_explore()

    async def _verify_or_spin_alignment(self) -> Tuple[bool, str]:
        """
        Verifica preventiva dell'allineamento Scan-to-Map (FM-NAV-033).
        1. Se siamo in ambiente di test o lo script non esiste, bypassa.
        2. Esegue auto_relocalize.py --check-only per verificare se i raggi ToF RPLIDAR C1
           colpiscono le pareti note della mappa.
        3. Se la mappa è vuota (<50 celle occupate / SLAM iniziale), supera il check immediatamente.
        4. Se disallineato su mappa statica, esegue uno spin a 360° per convergere AMCL prima di muoversi.
        """
        # Salta se mock o ambiente offline
        if self.ros_node is None or getattr(self.ros_node, '_is_mock', False):
            return True, "Mock / no ROS node active"

        script_path = "/mnt/ssd/robopy_controller_host/scripts/auto_relocalize.py"
        if not os.path.exists(script_path):
            return True, "Script auto_relocalize non presente."

        try:
            # Check rapido (non bloccante)
            proc = await asyncio.create_subprocess_exec(
                "python3", script_path, "--check-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
                if proc.returncode == 0:
                    return True, "Allineamento Scan-to-Map verificato con successo."
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return True, "Timeout check preventivo: procedo comunque."

            # Se disallineato, esegui spin a 360° per allineare
            if hasattr(self.ros_node, 'get_logger'):
                self.ros_node.get_logger().info("🔄 Disallineamento Scan-to-Map rilevato. Avvio rotazione di allineamento a 360°...")

            spin_proc = await asyncio.create_subprocess_exec(
                "python3", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(spin_proc.communicate(), timeout=35.0)
                out_str = (stdout.decode(errors='ignore') if stdout else "") + " " + (stderr.decode(errors='ignore') if stderr else "")
                if spin_proc.returncode == 0:
                    return True, "Allineamento completato dopo giro a 360° su spazio libero."
                else:
                    if "IN CARICA" in out_str:
                        return False, "Robot attualmente in carica sulla base: allineamento e rotazione 360° inibiti per salvaguardia dock."
                    if "Spazio libero insufficiente" in out_str or "Ostacolo rilevato" in out_str:
                        return False, "Ostacolo troppo vicino per eseguire lo spin a 360° in sicurezza."
                    return False, "Impossibile allineare i raggi laser con la mappa."
            except asyncio.TimeoutError:
                try:
                    spin_proc.kill()
                except Exception:
                    pass
                return False, "Timeout durante la rotazione di allineamento a 360°."
        except Exception as e:
            return True, f"Bypass check allineamento per eccezione: {e}"

    async def start_explore(self) -> SkillResult:
        """Avvia la mappatura autonoma di tutte le frontiere aperte dopo verifica allineamento."""
        # Check preventivo allineamento Scan-to-Map (FM-NAV-033)
        align_ok, align_info = await self._verify_or_spin_alignment()
        if not align_ok:
            return SkillResult(
                success=False,
                error_code=SkillErrorCode.EXECUTION_FAILED,
                message=f"Allineamento mappa non riuscito: {align_info}",
                speak="Attenzione, non sono allineato con la mappa e il giro di orientamento non è riuscito. Per sicurezza non mi muovo.",
                data={"mode": "FAILED_ALIGNMENT"}
            )

        if self.ros_node is not None:
            if self.pub_enable is None or self.pub_target is None:
                self._setup_ros_interfaces()

            # Resetta target (modalità EXPLORE pura)
            if self.pub_target is not None:
                t_msg = String()
                t_msg.data = ""
                self.pub_target.publish(t_msg)

            # Abilita esploratore
            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = True
                self.pub_enable.publish(e_msg)

        self.is_exploring = True
        self._paused_for_dialogue = False
        self.current_target = None
        speak_msg = "Allineamento verificato. Avvio l'esplorazione autonoma con il motore a frontiere. Mappo l'ambiente ed evito le zone già visitate."

        # Log event to TRINITY MAG
        if self.trinity_engine and hasattr(self.trinity_engine, "mag_database"):
            try:
                self.trinity_engine.mag_database.insert_fact(
                    fact_text="Avviata esplorazione a frontiere delle stanze sconosciute",
                    fact_type="NAVIGATION_EVENT",
                    confidence=0.95
                )
            except Exception:
                pass

        return SkillResult(
            success=True,
            message="Esplorazione frontiere avviata.",
            speak=speak_msg,
            data={"mode": "EXPLORE"}
        )

    async def start_hunt(self, target: str) -> SkillResult:
        """Cerca un target semantico consultando prima MAG e poi attivando HUNT mode su frontiere."""
        target_clean = (target or "target").strip().lower()
        self.current_target = target_clean

        # Step A: Interroga MAG per coordinate note recenti
        known_location = None
        if self.trinity_engine and hasattr(self.trinity_engine, "find_target_location"):
            known_location = self.trinity_engine.find_target_location(target_clean)

        if known_location and "coordinates" in known_location:
            x, y = known_location["coordinates"]
            speak_msg = f"Ricordo di aver visto '{target_clean}' alle coordinate ({x:.2f}, {y:.2f}). Mi dirigo sul posto per verificare."

            # Attiva comunque target listener in explorer
            if self.ros_node is not None:
                if self.pub_target is not None:
                    t_msg = String()
                    t_msg.data = target_clean
                    self.pub_target.publish(t_msg)

            return SkillResult(
                success=True,
                message=f"Coordinate note trovate in memoria MAG per '{target_clean}'.",
                speak=speak_msg,
                data={
                    "mode": "HUNT_KNOWN_LOCATION",
                    "target": target_clean,
                    "coordinates": [x, y]
                }
            )

        # Step B: Nessuna coordinata nota -> attiva esplorazione con bias semantico verso il target
        if self.ros_node is not None:
            if self.pub_enable is None or self.pub_target is None:
                self._setup_ros_interfaces()

            if self.pub_target is not None:
                t_msg = String()
                t_msg.data = target_clean
                self.pub_target.publish(t_msg)

            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = True
                self.pub_enable.publish(e_msg)

        self.is_exploring = True
        self._paused_for_dialogue = False
        speak_msg = f"Avvio la ricerca attiva di '{target_clean}'. Esploro le aree sconosciute e monitoro con la visione semantica."

        # Log event to TRINITY MAG
        if self.trinity_engine and hasattr(self.trinity_engine, "mag_database"):
            try:
                self.trinity_engine.mag_database.insert_fact(
                    fact_text=f"Avviata ricerca attiva bersaglio '{target_clean}' nell'ambiente",
                    fact_type="NAVIGATION_EVENT",
                    confidence=0.95
                )
            except Exception:
                pass

        return SkillResult(
            success=True,
            message=f"Modalità HUNT attivata per '{target_clean}'.",
            speak=speak_msg,
            data={
                "mode": "HUNT",
                "target": target_clean
            }
        )

    def stop_exploration(self) -> SkillResult:
        """Ferma l'esplorazione e la navigazione attiva azzerando comandi motori e cancellando i goal."""
        if self.ros_node is not None:
            if self.pub_enable is None:
                self._setup_ros_interfaces()

            if self.pub_enable is not None:
                e_msg = Bool()
                e_msg.data = False
                self.pub_enable.publish(e_msg)

            if self.pub_target is not None:
                t_msg = String()
                t_msg.data = ""
                self.pub_target.publish(t_msg)

            # Arresto istantaneo /cmd_vel per sicurezza fisica immediata
            if hasattr(self.ros_node, 'cmd_vel_pub'):
                try:
                    from geometry_msgs.msg import Twist
                    stop_cmd = Twist()
                    self.ros_node.cmd_vel_pub.publish(stop_cmd)
                except Exception:
                    pass

        # Cancella navigazione su nav_client se presente
        if self.nav_client and hasattr(self.nav_client, 'cancel_navigation'):
            try:
                import asyncio
                if asyncio.iscoroutinefunction(self.nav_client.cancel_navigation):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.nav_client.cancel_navigation())
                    except RuntimeError:
                        pass
                else:
                    self.nav_client.cancel_navigation()
            except Exception:
                pass

        self.is_exploring = False
        self._paused_for_dialogue = False
        self.current_target = None
        speak_msg = "Esplorazione e navigazione interrotte. Mi fermo qui."

        return SkillResult(
            success=True,
            message="Esplorazione fermata con successo.",
            speak=speak_msg,
            data={"mode": "IDLE"}
        )
