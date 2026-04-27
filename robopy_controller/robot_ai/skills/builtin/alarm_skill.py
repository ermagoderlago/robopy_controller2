"""
Robot AI Skills - Alarm Skill
==============================
Skill per la gestione di sveglie e promemoria programmati.

Permette di creare, modificare, eliminare e gestire sveglie con:
- Nomi personalizzati per ogni sveglia
- Ricorrenza: giornaliera, feriale, fine settimana, settimanale, mensile, annuale, una tantum
- Messaggio personalizzato con supporto a data/meteo
- Attesa risposta dall'utente (con retry a 1 minuto)
- Salvataggio su memoria RAG

# SKILL: AlarmSkill
# VERSION: 1.0.0
# CAPABILITIES: memory.rw, web.search
# TOPICS_SUB: []
# TOPICS_PUB: [/ai/conversation/response]
"""

import asyncio
import json
import logging
import re
import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

logger = logging.getLogger("robot_ai.skills.alarm_skill")

# ---------------------------------------------------------------------------
# Percorso persistenza sveglie (sul Pi)
# ---------------------------------------------------------------------------
ALARMS_FILE = "/mnt/ssd/robopy_controller_host/robopy_controller/state/alarms.json"

# Giorni della settimana in italiano
WEEKDAY_NAMES_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
WEEKDAY_NAMES_SHORT = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]

# Tipi di ricorrenza supportati
RECURRENCE_TYPES = {
    "giornaliera": "daily",
    "ogni giorno": "daily",
    "feriale": "weekdays",
    "feriali": "weekdays",
    "lavorativo": "weekdays",
    "lavorativi": "weekdays",
    "fine settimana": "weekend",
    "weekend": "weekend",
    "settimanale": "weekly",
    "mensile": "monthly",
    "annuale": "yearly",
    "una volta": "once",
    "una tantum": "once",
}


class AlarmSkill(BaseSkill):
    """
    Skill per la gestione delle sveglie di Marcus.

    Comandi supportati:
    - "Crea sveglia [nome] alle [ora]"
    - "Modifica sveglia [nome]"
    - "Elimina sveglia [nome]"
    - "Elenca le sveglie"
    - "Quali sveglie ho?"
    - "Ha suonato la sveglia [nome]?"
    """

    def __init__(self, memory_manager=None, llm_service=None):
        super().__init__()
        self._memory_manager = memory_manager
        self._llm_service = llm_service
        self._alarms: Dict[str, Dict] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._load_alarms()
        # Avvio scheduler in background al primo execute
        self._scheduler_started = False

    # ------------------------------------------------------------------
    # BaseSkill API
    # ------------------------------------------------------------------

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="alarm",
            description=(
                "Skill per gestire le sveglie (creare, eliminare, modificare, elencare). "
                "ISTRUZIONE PER L'LLM: Se l'utente chiede di creare o modificare una sveglia e non specifica TUTTI i dettagli necessari "
                "(es. orario esatto, tipo di ricorrenza come giornaliera/settimanale/una tantum), "
                "NON chiamare subito il tool con valori casuali. Invece, fai domande chiarificatrici all'utente per capire: "
                "1) A che ora deve suonare? 2) Quante volte o in quali giorni deve ripetersi? "
                "Solo quando hai tutti i dettagli, invoca questo tool con l'azione 'create' o 'modify'."
            ),
            version="1.0.1",
            keywords=[
                "sveglia", "allarme", "promemoria", "ricordami", "alle ore",
                "ogni mattina", "ogni giorno", "feriale", "crea sveglia",
                "elimina sveglia", "modifica sveglia", "elenca sveglie",
                "quante sveglie", "quali sveglie",
            ],
            priority=9,
            capabilities=[Capability.MEMORY_RW],
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        t = text.lower()
        # Alta priorità per comandi espliciti
        if any(k in t for k in ["crea sveglia", "nuova sveglia", "imposta sveglia", "aggiungi sveglia"]):
            return 0.95
        if any(k in t for k in ["elimina sveglia", "cancella sveglia", "rimuovi sveglia"]):
            return 0.95
        if any(k in t for k in ["modifica sveglia", "cambia sveglia", "aggiorna sveglia"]):
            return 0.95
        if any(k in t for k in ["elenca sveglie", "quali sveglie", "quante sveglie", "lista sveglie", "mostra sveglie"]):
            return 0.95
        if re.search(r"ha suonato.*sveglia|sveglia.*suonata|risposta.*sveglia", t):
            return 0.90
        # Presenza generica di "sveglia" + ora o giorno
        if "sveglia" in t and re.search(r"\d{1,2}[:\s]\d{2}|\balle\b|\borgni\b|\bogni\b", t):
            return 0.80
        if "sveglia" in t:
            return 0.50
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        # Avvia lo scheduler la prima volta
        if not self._scheduler_started:
            self._scheduler_started = True
            asyncio.create_task(self._alarm_scheduler_loop())

        context = context or {}
        
        # Se Gemini ha fornito i parametri strutturati tramite tool call:
        action = context.get("action")
        
        if action:
            if action == "create":
                return await self._cmd_create_from_params(context)
            elif action == "delete":
                return await self._cmd_delete_from_params(context)
            elif action == "modify":
                return await self._cmd_modify_from_params(context)
            elif action == "list":
                return await self._cmd_list()
            elif action == "history":
                return await self._cmd_history_from_params(context)

        # -- Fallback al text matching se non c'è action strutturata --
        t = text.lower()

        # -- CREA --
        if any(k in t for k in ["crea", "nuova", "imposta", "aggiungi"]) and "sveglia" in t:
            return await self._cmd_create(text)

        # -- ELIMINA --
        if any(k in t for k in ["elimina", "cancella", "rimuovi"]) and "sveglia" in t:
            return await self._cmd_delete(text)

        # -- MODIFICA --
        if any(k in t for k in ["modifica", "cambia", "aggiorna"]) and "sveglia" in t:
            return await self._cmd_modify(text)

        # -- ELENCA --
        if any(k in t for k in ["elenca", "quali", "quante", "lista", "mostra", "dimmi"]) and "sveglie" in t:
            return await self._cmd_list()

        # -- STORICO RISPOSTA --
        if re.search(r"ha suonato|risposta|ricorda|cosa ha detto", t) and "sveglia" in t:
            return await self._cmd_history(text)

        return SkillResult.failure_result(
            "Comando sveglia non riconosciuto o parametri mancanti.",
            SkillErrorCode.INVALID_PARAMETERS,
            speak="Non ho capito bene come impostare la sveglia. Puoi dirmi l'orario e se deve ripetersi?"
        )

    # ------------------------------------------------------------------
    # Comandi
    # ------------------------------------------------------------------

    async def _cmd_create_from_params(self, params: Dict[str, Any]) -> SkillResult:
        name = params.get("name") or self._auto_name()
        if name in self._alarms:
            return SkillResult.failure_result(
                f"Sveglia '{name}' già esistente.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Esiste già una sveglia chiamata {name}. Usa l'azione 'modify' per cambiarla."
            )

        alarm_time = params.get("time")
        if not alarm_time:
            return SkillResult.failure_result(
                "Orario mancante.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak="A che ora devo impostare la sveglia?"
            )

        recurrence = params.get("recurrence", "once")
        # Se settimanale, assumiamo oggi come weekday di default se non meglio specificato (Gemini dovrà gestire questo se può, altrimenti default)
        weekday = datetime.datetime.now().weekday() if recurrence == "weekly" else None
        
        message = params.get("message") or f"Sveglia {name}! È ora."
        with_weather = params.get("with_weather", False)

        alarm = {
            "name": name,
            "time": alarm_time,           # "HH:MM"
            "recurrence": recurrence,     # "daily","weekdays","weekend","weekly","monthly","yearly","once"
            "weekday": weekday,           # 0-6 (lun-dom) solo per "weekly"
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "message": message,
            "with_weather": with_weather,
            "enabled": True,
            "history": [],
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._alarms[name] = alarm
        self._save_alarms()

        desc = self._describe_alarm(alarm)
        return SkillResult.success_result(
            message=f"Sveglia '{name}' creata con successo.",
            data={"alarm": alarm},
            speak=f"Fatto. Sveglia {name} creata. {desc}"
        )

    async def _cmd_delete_from_params(self, params: Dict[str, Any]) -> SkillResult:
        name = params.get("name")
        if not name or name not in self._alarms:
            names = ", ".join(self._alarms.keys()) if self._alarms else "nessuna"
            return SkillResult.failure_result(
                f"Sveglia '{name}' non trovata.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Non ho trovato la sveglia {name or 'specificata'}. Le sveglie attive sono: {names}."
            )
        del self._alarms[name]
        self._save_alarms()
        return SkillResult.success_result(
            message=f"Sveglia '{name}' eliminata.",
            speak=f"Sveglia {name} eliminata."
        )

    async def _cmd_modify_from_params(self, params: Dict[str, Any]) -> SkillResult:
        name = params.get("name")
        if not name or name not in self._alarms:
            return SkillResult.failure_result(
                f"Sveglia '{name}' non trovata.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Non ho trovato la sveglia {name or 'specificata'}."
            )

        alarm = self._alarms[name]
        if "time" in params and params["time"]:
            alarm["time"] = params["time"]
        if "recurrence" in params and params["recurrence"]:
            alarm["recurrence"] = params["recurrence"]
        if "message" in params and params["message"]:
            alarm["message"] = params["message"]
        if "with_weather" in params:
            alarm["with_weather"] = params["with_weather"]

        self._save_alarms()
        desc = self._describe_alarm(alarm)
        return SkillResult.success_result(
            message=f"Sveglia '{name}' modificata.",
            speak=f"Sveglia {name} aggiornata. {desc}"
        )

    async def _cmd_history_from_params(self, params: Dict[str, Any]) -> SkillResult:
        name = params.get("name")
        alarm = self._alarms.get(name) if name else None
        if not alarm:
            # Cerca l'ultima sveglia con storia
            for a in reversed(list(self._alarms.values())):
                if a.get("history"):
                    alarm = a
                    break
        if not alarm or not alarm.get("history"):
            return SkillResult.success_result(
                message="Nessuno storico disponibile.",
                speak="Non ho registrazioni per questa sveglia."
            )
        last = alarm["history"][-1]
        fired_at = last.get("fired_at", "orario sconosciuto")
        response = last.get("user_response", "nessuna risposta")
        speak = (
            f"La sveglia {alarm['name']} è suonata il {fired_at}. "
            f"Risposta dell'utente: {response}."
        )
        return SkillResult.success_result(message=speak, speak=speak)

    # ------------------------------------------------------------------
    # Vecchi helper di Fallback (basati su text matching)
    # ------------------------------------------------------------------

    async def _cmd_create(self, text: str) -> SkillResult:
        name = self._extract_alarm_name(text) or self._auto_name()
        if name in self._alarms:
            return SkillResult.failure_result(
                f"Sveglia '{name}' già esistente.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Esiste già una sveglia chiamata {name}. Usa 'modifica sveglia {name}' per cambiarla."
            )

        alarm_time = self._extract_time(text)
        if not alarm_time:
            return SkillResult.failure_result(
                "Ora non trovata nel testo.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak="Non ho capito l'orario. A che ora devo impostare la sveglia?"
            )

        recurrence, weekday = self._extract_recurrence(text)
        message = self._extract_message(text) or f"Sveglia {name}! È ora di alzarsi."
        with_weather = any(k in text.lower() for k in ["meteo", "tempo", "previsioni", "clima"])

        alarm = {
            "name": name,
            "time": alarm_time,           # "HH:MM"
            "recurrence": recurrence,     # "daily","weekdays","weekend","weekly","monthly","yearly","once"
            "weekday": weekday,           # 0-6 (lun-dom) solo per "weekly"
            "date": self._extract_date(text),  # "YYYY-MM-DD" per once/monthly/yearly
            "message": message,
            "with_weather": with_weather,
            "enabled": True,
            "history": [],
            "created_at": datetime.datetime.now().isoformat(),
        }
        self._alarms[name] = alarm
        self._save_alarms()

        desc = self._describe_alarm(alarm)
        return SkillResult.success_result(
            message=f"Sveglia '{name}' creata.",
            data={"alarm": alarm},
            speak=f"Sveglia {name} creata. {desc}"
        )

    async def _cmd_delete(self, text: str) -> SkillResult:
        name = self._extract_alarm_name(text)
        if not name or name not in self._alarms:
            names = ", ".join(self._alarms.keys()) if self._alarms else "nessuna"
            return SkillResult.failure_result(
                f"Sveglia '{name}' non trovata.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Non ho trovato la sveglia {name or 'specificata'}. Le sveglie attive sono: {names}."
            )
        del self._alarms[name]
        self._save_alarms()
        return SkillResult.success_result(
            message=f"Sveglia '{name}' eliminata.",
            speak=f"Sveglia {name} eliminata."
        )

    async def _cmd_modify(self, text: str) -> SkillResult:
        name = self._extract_alarm_name(text)
        if not name or name not in self._alarms:
            return SkillResult.failure_result(
                f"Sveglia '{name}' non trovata.",
                SkillErrorCode.INVALID_PARAMETERS,
                speak=f"Non ho trovato la sveglia {name or 'specificata'}."
            )

        alarm = self._alarms[name]
        new_time = self._extract_time(text)
        if new_time:
            alarm["time"] = new_time
        new_rec, new_wd = self._extract_recurrence(text)
        if new_rec != "once":
            alarm["recurrence"] = new_rec
            if new_wd is not None:
                alarm["weekday"] = new_wd
        new_msg = self._extract_message(text)
        if new_msg:
            alarm["message"] = new_msg

        self._save_alarms()
        desc = self._describe_alarm(alarm)
        return SkillResult.success_result(
            message=f"Sveglia '{name}' modificata.",
            speak=f"Sveglia {name} aggiornata. {desc}"
        )

    async def _cmd_list(self) -> SkillResult:
        if not self._alarms:
            return SkillResult.success_result(
                message="Nessuna sveglia attiva.",
                speak="Non hai sveglie impostate."
            )
        lines = []
        for alarm in self._alarms.values():
            status = "attiva" if alarm["enabled"] else "disattivata"
            lines.append(f"{alarm['name']} alle {alarm['time']}, {self._describe_recurrence(alarm)}, {status}")
        speak = f"Hai {len(self._alarms)} sveglie: " + ". ".join(lines) + "."
        return SkillResult.success_result(
            message=speak,
            data={"alarms": list(self._alarms.values())},
            speak=speak
        )

    async def _cmd_history(self, text: str) -> SkillResult:
        name = self._extract_alarm_name(text)
        alarm = self._alarms.get(name) if name else None
        if not alarm:
            # Cerca l'ultima sveglia con storia
            for a in reversed(list(self._alarms.values())):
                if a.get("history"):
                    alarm = a
                    break
        if not alarm or not alarm.get("history"):
            return SkillResult.success_result(
                message="Nessuno storico disponibile.",
                speak="Non ho registrazioni per questa sveglia."
            )
        last = alarm["history"][-1]
        fired_at = last.get("fired_at", "orario sconosciuto")
        response = last.get("user_response", "nessuna risposta")
        speak = (
            f"La sveglia {alarm['name']} è suonata il {fired_at}. "
            f"Risposta dell'utente: {response}."
        )
        return SkillResult.success_result(message=speak, speak=speak)

    # ------------------------------------------------------------------
    # Scheduler loop — gira in background finché il nodo è vivo
    # ------------------------------------------------------------------

    async def _alarm_scheduler_loop(self):
        logger.info("[ALARM] Scheduler sveglie avviato.")
        while True:
            try:
                now = datetime.datetime.now()
                for name, alarm in list(self._alarms.items()):
                    if not alarm.get("enabled"):
                        continue
                    if self._should_fire(alarm, now):
                        await self._fire_alarm(alarm, now)
            except Exception as e:
                logger.error(f"[ALARM] Errore scheduler: {e}", exc_info=True)
            # Controlla ogni 30 secondi
            await asyncio.sleep(30)

    def _should_fire(self, alarm: Dict, now: datetime.datetime) -> bool:
        """True se la sveglia deve suonare nel minuto corrente."""
        h, m = map(int, alarm["time"].split(":"))
        if now.hour != h or now.minute != m:
            return False

        # Evita doppio scatto: controlla storico
        if alarm.get("history"):
            last = alarm["history"][-1]
            last_dt = datetime.datetime.fromisoformat(last.get("fired_at", "2000-01-01"))
            if (now - last_dt).total_seconds() < 60:
                return False

        rec = alarm["recurrence"]
        wd = now.weekday()  # 0=lun, 6=dom

        if rec == "daily":
            return True
        if rec == "weekdays":
            return wd < 5
        if rec == "weekend":
            return wd >= 5
        if rec == "weekly":
            return wd == alarm.get("weekday", 0)
        if rec == "once":
            d = alarm.get("date")
            if d:
                return now.strftime("%Y-%m-%d") == d
            return True
        if rec == "monthly":
            d = alarm.get("date", "")
            if d and len(d) >= 5:
                return now.strftime("%m-%d") == d[5:]
            return False
        if rec == "yearly":
            d = alarm.get("date", "")
            if d:
                return now.strftime("%m-%d") == d[5:]
            return False
        return False

    async def _fire_alarm(self, alarm: Dict, now: datetime.datetime):
        """Suona la sveglia, attende risposta, salva in memoria."""
        logger.info(f"[ALARM] Sveglia '{alarm['name']}' in scatto.")
        message = self._build_fire_message(alarm, now)

        # Prima notifica
        if self._llm_service:
            try:
                await self._llm_service.tts_speak(message)
            except Exception:
                pass

        # Attendi risposta per 60 secondi
        user_response = await self._wait_for_response(timeout=60)

        # Se nessuna risposta, riprova una volta dopo 60 secondi
        if not user_response:
            logger.info(f"[ALARM] Nessuna risposta. Ri-trasmetto tra 60s.")
            await asyncio.sleep(60)
            if self._llm_service:
                try:
                    await self._llm_service.tts_speak(message)
                except Exception:
                    pass
            user_response = await self._wait_for_response(timeout=60) or "nessuna risposta"

        # Log in memoria
        entry = {
            "fired_at": now.isoformat(),
            "message": message,
            "user_response": user_response,
        }
        alarm.setdefault("history", []).append(entry)

        # Se "once" → disattiva dopo lo scatto
        if alarm["recurrence"] == "once":
            alarm["enabled"] = False

        self._save_alarms()

        # Salva in RAG
        if self._memory_manager:
            try:
                mem_text = (
                    f"Sveglia '{alarm['name']}' suonata il {now.strftime('%d/%m/%Y alle %H:%M')}. "
                    f"Messaggio: {message}. Risposta utente: {user_response}."
                )
                await self._memory_manager.store_background(
                    f"sveglia {alarm['name']}", mem_text, "alarm"
                )
            except Exception as e:
                logger.warning(f"[ALARM] Impossibile salvare in RAG: {e}")

    def _build_fire_message(self, alarm: Dict, now: datetime.datetime) -> str:
        """Costruisce il messaggio parlato della sveglia."""
        day_name = WEEKDAY_NAMES_IT[now.weekday()]
        date_str = now.strftime(f"{day_name} %d %B %Y")
        time_str = now.strftime("%H e %M")
        base = f"Sono le {time_str} di {date_str}. {alarm['message']}"
        return base

    async def _wait_for_response(self, timeout: int = 60) -> Optional[str]:
        """
        Attende una risposta vocale per 'timeout' secondi.
        In questa implementazione restituisce None (il loop di conversazione
        del VUI gestirà l'input; una vera integrazione richiederebbe un
        future/event condiviso con l'orchestratore).
        """
        await asyncio.sleep(timeout)
        return None

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _extract_alarm_name(self, text: str) -> Optional[str]:
        """Estrae il nome della sveglia dal testo."""
        # Cerca nomi tra virgolette o apostrofi
        m = re.search(r'["\']([^"\']+)["\']', text)
        if m:
            return m.group(1).strip()
        # Cerca dopo "sveglia <Nome>" con maiuscola
        m = re.search(r'sveglia\s+([A-Z][a-zA-Zàèéìòù]+(?:\s+[A-Z][a-zA-Zàèéìòù]+)?)', text)
        if m:
            return m.group(1).strip()
        # Cerca dopo keyword + "sveglia"
        m = re.search(
            r'(?:crea|nuova|imposta|elimina|cancella|modifica|cambia|aggiorna)\s+sveglia\s+([a-zA-Zàèéìòù]+)',
            text, re.IGNORECASE
        )
        if m:
            return m.group(1).strip().capitalize()
        # Cerca nomi comuni
        for name in self._alarms:
            if name.lower() in text.lower():
                return name
        return None

    def _auto_name(self) -> str:
        """Genera un nome automatico se non specificato."""
        i = 1
        while f"Sveglia{i}" in self._alarms:
            i += 1
        return f"Sveglia{i}"

    def _extract_time(self, text: str) -> Optional[str]:
        """Estrae HH:MM dal testo."""
        # Formato HH:MM o H:MM
        m = re.search(r'\b(\d{1,2})[:\s](\d{2})\b', text)
        if m:
            h, mn = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mn <= 59:
                return f"{h:02d}:{mn:02d}"
        # "alle sette" ecc. - numeri testuali
        word_hours = {
            "mezzanotte": 0, "una": 1, "due": 2, "tre": 3, "quattro": 4,
            "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9,
            "dieci": 10, "undici": 11, "mezzogiorno": 12,
            "tredici": 13, "quattordici": 14, "quindici": 15,
            "sedici": 16, "diciassette": 17, "diciotto": 18,
            "diciannove": 19, "venti": 20, "ventuno": 21,
            "ventidue": 22, "ventitre": 23,
        }
        tl = text.lower()
        for word, hour in word_hours.items():
            if f"alle {word}" in tl or f"all'{word}" in tl:
                return f"{hour:02d}:00"
        return None

    def _extract_recurrence(self, text: str) -> Tuple[str, Optional[int]]:
        """Estrae tipo di ricorrenza e giorno (se settimanale)."""
        tl = text.lower()
        for key, rec_type in RECURRENCE_TYPES.items():
            if key in tl:
                # Cerca giorno specifico per "weekly"
                wd = None
                if rec_type == "weekly":
                    for i, day in enumerate(WEEKDAY_NAMES_IT):
                        if day in tl:
                            wd = i
                            break
                    for i, day in enumerate(WEEKDAY_NAMES_SHORT):
                        if day in tl:
                            wd = i
                            break
                return rec_type, wd

        # Cerca nomi di giorni senza "settimanale"
        for i, day in enumerate(WEEKDAY_NAMES_IT):
            if f"ogni {day}" in tl or f"il {day}" in tl:
                return "weekly", i

        return "once", None

    def _extract_date(self, text: str) -> Optional[str]:
        """Estrae una data in formato YYYY-MM-DD dal testo."""
        # Formato GG/MM/AAAA o GG-MM-AAAA
        m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', text)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            try:
                return datetime.date(y, mo, d).isoformat()
            except ValueError:
                pass
        return None

    def _extract_message(self, text: str) -> Optional[str]:
        """Estrae un messaggio personalizzato indicato dopo 'messaggio:' o 'dici:'."""
        m = re.search(r'(?:messaggio|dici|pronuncia)[:\s]+["\']?(.+?)["\']?$', text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    def _describe_alarm(self, alarm: Dict) -> str:
        rec = self._describe_recurrence(alarm)
        weather = " Con meteo." if alarm.get("with_weather") else ""
        return f"Suonerà alle {alarm['time']}, {rec}.{weather}"

    def _describe_recurrence(self, alarm: Dict) -> str:
        rec = alarm["recurrence"]
        if rec == "daily":
            return "ogni giorno"
        if rec == "weekdays":
            return "solo nei giorni feriali"
        if rec == "weekend":
            return "nel weekend"
        if rec == "weekly":
            wd = alarm.get("weekday")
            day = WEEKDAY_NAMES_IT[wd] if wd is not None else "?"
            return f"ogni {day}"
        if rec == "monthly":
            d = alarm.get("date", "?")
            return f"il {d[8:10]} di ogni mese" if len(d) >= 10 else "mensile"
        if rec == "yearly":
            d = alarm.get("date", "?")
            return f"il {d[8:10]}/{d[5:7]} di ogni anno" if len(d) >= 10 else "annuale"
        if rec == "once":
            d = alarm.get("date")
            return f"il {d}" if d else "una volta sola"
        return rec

    # ------------------------------------------------------------------
    # Persistenza
    # ------------------------------------------------------------------

    def _load_alarms(self):
        try:
            import os
            if os.path.exists(ALARMS_FILE):
                with open(ALARMS_FILE, "r", encoding="utf-8") as f:
                    self._alarms = json.load(f)
                logger.info(f"[ALARM] Caricate {len(self._alarms)} sveglie da {ALARMS_FILE}")
        except Exception as e:
            logger.warning(f"[ALARM] Impossibile caricare sveglie: {e}")
            self._alarms = {}

    def _save_alarms(self):
        try:
            import os
            os.makedirs(os.path.dirname(ALARMS_FILE), exist_ok=True)
            with open(ALARMS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._alarms, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[ALARM] Impossibile salvare sveglie: {e}")

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "delete", "modify", "list", "history"],
                    "description": "Azione da eseguire. IMPORTANTE: Prima di usare 'create' o 'modify', devi essere certo di sapere l'orario e la ricorrenza."
                },
                "name": {
                    "type": "string",
                    "description": "Nome della sveglia (es. 'Mattina', 'Lavoro'). Obbligatorio per delete/modify."
                },
                "time": {
                    "type": "string",
                    "description": "Orario della sveglia in formato HH:MM (es. '07:30'). Obbligatorio per create."
                },
                "recurrence": {
                    "type": "string",
                    "enum": ["daily", "weekdays", "weekend", "weekly", "monthly", "yearly", "once"],
                    "description": "Tipo di ricorrenza. Chiedi all'utente se non lo specifica."
                },
                "message": {
                    "type": "string",
                    "description": "Messaggio personalizzato da pronunciare allo scatto."
                },
                "with_weather": {
                    "type": "boolean",
                    "description": "Se True, include il meteo nel messaggio."
                },
            },
            "required": ["action"],
        }
