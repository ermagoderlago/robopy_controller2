# robopy_controller/robot_ai/skills/builtin/email_skill.py
"""
Robot AI Skills - Email Skill v2.0
===================================
Assistente email professionale per Marcus.
Legge, classifica, riassume e risponde alle email tramite IMAP/SMTP.
Polling automatico ogni 10 min con quiet hours, classificazione intelligente,
salvataggio RAG per email importanti, notifiche proattive.

Pattern: AsyncGenerator (come SearchSkill) — feedback vocale progressivo.
Dipendenze esterne: aioimaplib, aiosmtplib
"""

import asyncio
import email as email_lib
import hashlib
import json
import logging
import os
import re
import time
import datetime
from email.header import decode_header as _rfc2047_decode
from email.message import EmailMessage
from typing import Any, AsyncGenerator, Dict, List, Optional

import aioimaplib
import aiosmtplib

from ..base_skill import BaseSkill, Capability, SkillErrorCode, SkillMetadata, SkillResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Costanti a livello di modulo
# ---------------------------------------------------------------------------

_FALLBACK_RESPONSE: Dict[str, Any] = {
    "summary":     "Non ho potuto analizzare le email in questo momento.",
    "reply_draft": None,
    "ha_actions":  [],
    "priority":    "normal",
}

# Pattern per email interessanti (pacchi, corrieri, ordini)
_INTERESTING_PATTERNS = [
    re.compile(r'\b(amazon|aliexpress|ali\s*express|ebay|temu|wish)\b', re.I),
    re.compile(r'\b(tracking|spedizione|corriere|pacco|consegna|shipment|delivery|shipped)\b', re.I),
    re.compile(r'\b(BRT|Bartolini|DHL|UPS|FedEx|GLS|SDA|Poste\s*Italiane|TNT|Nexive)\b', re.I),
    re.compile(r'\b(ordine|order|conferma.*ordine|order.*confirm|fattura|invoice)\b', re.I),
]

# Quiet hours defaults
_QUIET_START = 23  # 23:00
_QUIET_END = 7     # 07:00
_POLL_INTERVAL_MIN = 10

_LLM_PROMPT_TEMPLATE = """
Sei l'assistente AI del robot Marcus, il fedele assistente di Luca Suffia.
Analizza le seguenti {n} email con attenzione professionale.
Rispondi ESCLUSIVAMENTE con un oggetto JSON valido.
Non aggiungere testo, markdown o backtick prima o dopo il JSON.

Intent dell'utente: {intent}
{reply_instruction}

Mittenti prioritari (VIP — segnala sempre): {vip_senders}

Email (in ordine cronologico inverso):
{emails_json}

Schema JSON richiesto (rispetta esattamente questa struttura):
{{
  "summary":     "<stringa, max 350 char, in italiano. Se intent=deepdive leggi per esteso. Altrimenti riassumi brevemente citando mittenti e oggetti>",
  "reply_draft": "<bozza risposta in prima persona come robot Marcus, professionale e cortese> | null",
  "ha_actions":  [{{"type": "reminder|store_memory|speak", "payload": {{"detail": "<str>"}}}}],
  "priority":    "urgent|normal|low",
  "tracking":    "<numero tracking se presente> | null",
  "classifications": [{{"from": "<mittente>", "class": "urgent|important|interesting|normal|spam"}}]
}}

Vincoli:
- "summary" se intent=deepdive, spiega il contenuto testuale della mail con dettagli. Altrimenti inizia con "Hai N email:" e cita i mittenti.
- "ha_actions" usa type=store_memory se l'email contiene date, scadenze, numeri tracking, consegne o dettagli importanti da ricordare.
- "priority" è "urgent" solo per emergenze o scadenze entro 24h.
- "reply_draft" è null se intent != "reply".
- "tracking" estrai numeri di tracking/spedizione se presenti nel corpo email.
- "classifications" classifica OGNI email: email da VIP sono "important", pacchi/ordini sono "interesting", newsletter/promo sono "spam".
""".strip()

# ---------------------------------------------------------------------------
# Pattern regex — definiti a livello di modulo per riuso in match() e
# _detect_intent()
# ---------------------------------------------------------------------------

_EXCLUSIONS = [
    re.compile(r'\b(hai\s+)(mandato|inviato|scritto)\b.{0,20}\b(mail|email)\b', re.I),
    re.compile(r'\bmanda\s+(un\s+)?(messaggio\s+)?(vocale|whatsapp|telegram|sms)\b', re.I),
    re.compile(r'\b(whatsapp|telegram|sms|messaggio\s+vocale)\b', re.I),
]

_INTENT_PATTERNS = [
    ("reply",   0.95, re.compile(r'\b(rispondi|manda|invia|scrivi)\b.{0,40}\b(email|mail|risposta)\b', re.I)),
    ("urgent",  0.90, re.compile(r'\b(urgent[ei]?|important[ei]?|priorit[àa])\b.{0,30}\b(email|mail)\b', re.I)),
    ("urgent",  0.90, re.compile(r'\b(email|mail)\b.{0,30}\b(urgent[ei]?|important[ei]?|priorit[àa])\b', re.I)),
    ("deepdive",0.90, re.compile(r'\b(approfondisci|dettagli|leggimi|tutta)\b.{0,25}\b(email|mail|quella)\b', re.I)),
    ("read",    0.85, re.compile(r'\b(leggi|controlla|apri|guarda|dammi)\b.{0,25}\b(email|mail|posta|messaggi)\b', re.I)),
    ("new",     0.85, re.compile(r'\b(ho|ci sono|nuov[ie]|arrivat[ei]|ricevut[eo])\b.{0,25}\b(mail|email|messaggi)\b', re.I)),
    ("summary", 0.80, re.compile(r'\b(riassumi|riepiloga|di\s+cosa\s+parlano|cosa\s+dicono)\b.{0,35}\b(email|mail)\b', re.I)),
]


class EmailSkill(BaseSkill):
    """
    Skill per leggere, riassumere e rispondere alle email.

    Usa il pattern AsyncGenerator per fornire feedback vocale progressivo
    durante operazioni potenzialmente lunghe (IMAP connect → fetch → LLM → SMTP).
    """

    def __init__(self, llm_service, config: Dict[str, Any] = None, memory_manager=None):
        super().__init__()
        self.llm_service = llm_service
        self.config      = config or {}
        self.memory_manager = memory_manager

        # Stato interno
        self._is_running:         bool                   = False
        self._current_task:       Optional[asyncio.Task]  = None
        self._credentials_warned: bool                   = False
        self._last_execution:     float                  = 0.0

        # Credenziali — SOLO da variabili d'ambiente, mai da config
        self.email_addr   = os.getenv("EMAIL_ADDRESS")
        self.email_pass   = os.getenv("EMAIL_PASSWORD")
        self.imap_server  = os.getenv("IMAP_SERVER",  self.config.get("imap_server",  "imap.gmail.com"))
        self.smtp_server  = os.getenv("SMTP_SERVER",  self.config.get("smtp_server",  "smtp.gmail.com"))
        self.imap_port    = int(os.getenv("IMAP_PORT", str(self.config.get("imap_port", 993))))
        self.smtp_port    = int(os.getenv("SMTP_PORT", str(self.config.get("smtp_port", 587))))
        # True → STARTTLS (porta 587)  |  False → SSL diretto (porta 465)
        self.smtp_starttls = os.getenv("SMTP_TLS", "true").lower() == "true"

        # Timeout in secondi — tutti configurabili da config
        self.t_connect    = self.config.get("timeout_connect", 10)
        self.t_fetch      = self.config.get("timeout_fetch",   15)
        self.t_send       = self.config.get("timeout_send",    20)
        self.t_llm        = self.config.get("llm_timeout",     20)
        self.max_emails   = self.config.get("max_emails",      30)  # v2.0: era 8
        self.min_interval = self.config.get("min_interval_s",   5)  # v2.0: ridotto, rate limit leggero
        
        self._recent_emails: List[Dict] = []

        # v2.0: Polling automatico
        self._poll_interval = self.config.get("poll_interval_min", _POLL_INTERVAL_MIN) * 60
        self._quiet_start   = self.config.get("quiet_start", _QUIET_START)
        self._quiet_end     = self.config.get("quiet_end", _QUIET_END)
        self._poll_task: Optional[asyncio.Task] = None

        # v2.0: Notifiche proattive
        self._notification_buffer: List[Dict] = []

        # v2.0: Deduplicazione email
        self._known_email_ids: set = set()

        # v2.0: Mittenti VIP (importanti)
        self._vip_senders: List[str] = self.config.get("vip_senders", [
            "luisella.bonfanti@gmail.com",
        ])

    # -----------------------------------------------------------------------
    # LLM Tool Schema
    # -----------------------------------------------------------------------
    def get_parameters_schema(self) -> Dict[str, Any]:
        """Ritorna lo schema JSON per il function calling di Gemini."""
        return {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["read", "reply", "urgent", "summary", "new", "deepdive"],
                    "description": (
                        "L'intento dell'operazione email. "
                        "'read': leggi ultime email, 'reply': rispondi all'ultima, "
                        "'urgent': cerca email urgenti, 'summary': riassumi inbox, "
                        "'new': controlla se ci sono nuovi messaggi, "
                        "'deepdive': approfondisci una mail specifica tra quelle lette."
                    )
                },
                "text": {
                    "type": "string",
                    "description": "Il testo completo del comando dell'utente."
                },
                "account": {
                    "type": "string",
                    "description": "Nome dell'account da controllare (default: 'default')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Numero massimo di email da leggere (default: 5)."
                },
                "date_filter": {
                    "type": "string",
                    "enum": ["today", "yesterday", "week", "all"],
                    "description": "Filtro temporale per la ricerca."
                },
                "email_id": {
                    "type": "string",
                    "description": "Nome del mittente o parola chiave se l'intent è deepdive."
                },
                "reply_to": {
                    "type": "string",
                    "description": "Nome o indirizzo email del mittente a cui rispondere (es: 'Luisella', 'mario@example.com'). Usato con intent=reply."
                }
            },
            "required": ["intent", "text"]
        }

    # -----------------------------------------------------------------------
    # Metadata
    # -----------------------------------------------------------------------
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="check_emails",
            description="Assistente email professionale: legge, classifica, riassume e risponde alle email tramite IMAP/SMTP. Polling automatico con notifiche.",
            version="2.0.0",
            keywords=[
                "email", "mail", "posta", "messaggi", "inbox", "casella",
                "leggi", "controlla", "rispondi", "urgent", "importante",
                "check_emails", "pacco", "tracking", "spedizione",
            ],
            priority=7,
            requires_internet=True,
            capabilities=[Capability.WEB_SEARCH],
        )

    # -----------------------------------------------------------------------
    # Match
    # -----------------------------------------------------------------------
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        """Calcola lo score di match per comandi email in italiano."""

        # Step 1 — Credenziali mancanti (blocco immediato)
        if not self.email_addr or not self.email_pass:
            if not self._credentials_warned:
                logger.warning(
                    "EmailSkill disabilitata: EMAIL_ADDRESS o EMAIL_PASSWORD "
                    "non impostate come variabili d'ambiente."
                )
                self._credentials_warned = True
            return 0.0

        # Step 2 — Rate limiting
        elapsed = time.time() - self._last_execution
        if self._last_execution > 0 and elapsed < self.min_interval:
            logger.debug(
                f"Rate limit: {elapsed:.0f}s dall'ultimo fetch "
                f"(min {self.min_interval}s)"
            )
            return 0.3   # Non 0: l'utente potrebbe insistere legittimamente

        # Step 3 — Pattern di esclusione
        if any(p.search(text) for p in _EXCLUSIONS):
            return 0.0

        # Step 4 — Pattern di intent (prendi il massimo score trovato)
        score = max(
            (s for _, s, p in _INTENT_PATTERNS if p.search(text)),
            default=0.0
        )
        return score

    # -----------------------------------------------------------------------
    # Intent detection (privato, riusa _INTENT_PATTERNS)
    # -----------------------------------------------------------------------
    def _detect_intent(self, text: str) -> str:
        """Determina l'intent dall'input testuale."""
        best_intent = "read"
        best_score  = 0.0

        for intent, score, pattern in _INTENT_PATTERNS:
            if pattern.search(text) and score > best_score:
                best_intent = intent
                best_score  = score

        # "new" intent → trattato come "read"
        if best_intent == "new":
            best_intent = "read"

        return best_intent

    # -----------------------------------------------------------------------
    # Email parsing (stdlib only)
    # -----------------------------------------------------------------------
    def _parse_message(self, raw_bytes: bytes) -> Dict[str, str]:
        """Parsa un messaggio email raw in un dizionario leggibile."""
        msg = email_lib.message_from_bytes(raw_bytes)

        # Subject: può essere multi-chunk RFC 2047 (es: =?utf-8?b?...?=)
        subject = ""
        for chunk, enc in _rfc2047_decode(msg.get("Subject", "") or ""):
            if isinstance(chunk, bytes):
                subject += chunk.decode(enc or "utf-8", errors="replace")
            else:
                subject += chunk

        sender = msg.get("From", "")
        date   = msg.get("Date", "")
        body   = self._extract_body(msg)

        return {
            "from":         sender,
            "subject":      subject.strip(),
            "date":         date,
            "body_snippet": body[:500],
        }

    def _extract_body(self, msg) -> str:
        """Estrae il corpo testuale: text/plain > text/html > stringa vuota."""
        body = ""

        if msg.is_multipart():
            # Prima passata: cerca text/plain non-attachment
            for part in msg.walk():
                ct   = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if ct == "text/plain" and "attachment" not in disp:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
            # Fallback: text/html
            if not body:
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            raw_html = payload.decode(charset, errors="replace")
                            body = re.sub(r"<[^>]+>", " ", raw_html)
                            break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    body = re.sub(r"<[^>]+>", " ", body)

        # Normalizza whitespace
        return re.sub(r"\s+", " ", body).strip()

    # -----------------------------------------------------------------------
    # IMAP fetch (cancellabile)
    # -----------------------------------------------------------------------
    async def _fetch_emails_task(
        self,
        intent: str,
        date_filter: str = "all",
        sender_filter: str = "",
        subject_filter: str = "",
    ) -> List[Dict]:
        """
        Connessione IMAP, fetch dei messaggi, parsing.
        Supporta filtro mittente/oggetto lato server via IMAP SEARCH.
        Cancellabile tramite asyncio.Task.cancel().
        """
        emails: List[Dict] = []

        imap = aioimaplib.IMAP4_SSL(host=self.imap_server, port=self.imap_port)
        try:
            # Handshake iniziale
            await asyncio.wait_for(
                imap.wait_hello_from_server(), timeout=self.t_connect
            )

            # Login
            login_resp = await asyncio.wait_for(
                imap.login(self.email_addr, self.email_pass),
                timeout=self.t_connect
            )
            if login_resp[0].upper() != "OK":
                raise PermissionError(f"Login IMAP rifiutato: {login_resp}")

            await asyncio.wait_for(
                imap.select("INBOX"), timeout=self.t_connect
            )

            # --- Costruzione SEARCH command ---
            # Priorità: filtri specifici > date_filter > UNSEEN
            if sender_filter:
                # IMAP SEARCH FROM è case-insensitive e cerca in tutta la stringa From:
                base_cmd = f'FROM "{sender_filter}"'
                if date_filter and date_filter != "all":
                    d_map = {
                        "today": datetime.date.today(),
                        "yesterday": datetime.date.today() - datetime.timedelta(days=1),
                        "week": datetime.date.today() - datetime.timedelta(days=7),
                    }
                    d = d_map.get(date_filter, datetime.date.today()).strftime("%d-%b-%Y")
                    search_cmd = f'SINCE {d} {base_cmd}'
                else:
                    search_cmd = base_cmd
            elif subject_filter:
                base_cmd = f'SUBJECT "{subject_filter}"'
                search_cmd = base_cmd
            elif date_filter == "today":
                d = datetime.date.today().strftime("%d-%b-%Y")
                search_cmd = f"SINCE {d}"
            elif date_filter == "yesterday":
                d = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%d-%b-%Y")
                search_cmd = f"SINCE {d}"
            elif date_filter == "week":
                d = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%d-%b-%Y")
                search_cmd = f"SINCE {d}"
            elif intent == "reply":
                search_cmd = "ALL"
            else:
                search_cmd = "UNSEEN"

            logger.info(f"IMAP SEARCH: '{search_cmd}'")

            _, search_data = await asyncio.wait_for(
                imap.search(search_cmd), timeout=self.t_fetch
            )

            # Fallback: se UNSEEN vuoto, prendi le ultime ALL
            raw_ids_str = " ".join(b.decode() for b in search_data if b)
            if not raw_ids_str.strip() and search_cmd == "UNSEEN":
                logger.info("EmailSkill: UNSEEN vuoto, fallback su ALL")
                _, search_data = await asyncio.wait_for(
                    imap.search("ALL"), timeout=self.t_fetch
                )

            # Fallback aggiuntivo per filtri specifici: se FROM non trova nulla, cerca in ALL
            if not raw_ids_str.strip() and sender_filter:
                logger.info(f"EmailSkill: nessun risultato FROM '{sender_filter}', cerco in subject")
                _, search_data = await asyncio.wait_for(
                    imap.search(f'SUBJECT "{sender_filter}"'), timeout=self.t_fetch
                )

            raw_ids_str = " ".join(b.decode() for b in search_data if b)
            all_ids = [i for i in raw_ids_str.split() if i.isdigit()]
            msg_ids = all_ids[-self.max_emails:]  # ultimi N (più recenti)

            logger.info(
                f"IMAP: trovati {len(all_ids)} ID, processo gli ultimi {len(msg_ids)}: {msg_ids}"
            )

            for msg_id in msg_ids:
                if not self._is_running:
                    logger.info("EmailSkill: fetch interrotto (cancellazione)")
                    break

                _, msg_data = await asyncio.wait_for(
                    imap.fetch(msg_id, "(RFC822)"),
                    timeout=self.t_fetch
                )
                # msg_data struttura tipica: [b'1 (RFC822 {size})', <raw_bytes>, b')']
                raw_bytes = None
                for item in msg_data:
                    if not isinstance(item, (bytes, bytearray)):
                        continue
                    item_bytes = bytes(item) if isinstance(item, bytearray) else item
                    # Decodifica snippet per controllo header IMAP (più permissivo)
                    snippet_upper = item_bytes[:100].decode(errors='ignore').upper()
                    # Scartiamo l'header del protocollo (es: "* 1 FETCH (RFC822 {1234})") e il footer ")"
                    if ("FETCH" in snippet_upper and "RFC822" in snippet_upper) or snippet_upper.strip() == ")":
                        continue
                    # Il primo chunk che non è protocollo è il vero contenuto RFC822
                    raw_bytes = item_bytes
                    break

                if raw_bytes:
                    try:
                        parsed = self._parse_message(raw_bytes)
                        emails.append(parsed)
                        logger.info(f"📧 Parsed email {msg_id}: Da={parsed['from']} Oggetto={parsed['subject']}")
                    except Exception as e:
                        logger.warning(f"Errore parsing msg_id={msg_id}: {e}")

        finally:
            # Logout best-effort — non propagare eccezioni dal logout
            try:
                await asyncio.wait_for(imap.logout(), timeout=3.0)
            except Exception:
                pass

        return emails

    # -----------------------------------------------------------------------
    # SMTP send (non propaga eccezioni)
    # -----------------------------------------------------------------------
    async def _send_reply_task(self, original: Dict, reply_text: str) -> bool:
        """
        Invia una risposta email via SMTP.
        Ritorna True se inviato, False con log warning se fallisce.
        NON propaga eccezioni — il caller continua anche se l'invio fallisce.
        """
        try:
            msg = EmailMessage()
            msg["From"]    = self.email_addr
            msg["To"]      = original.get("from", "")
            msg["Subject"] = f"Re: {original.get('subject', '')}"
            msg["In-Reply-To"] = original.get("message_id", "")
            msg.set_content(reply_text)

            if self.smtp_starttls:
                # Porta 587 — connessione plain + STARTTLS upgrade
                smtp = aiosmtplib.SMTP(
                    hostname=self.smtp_server,
                    port=self.smtp_port,
                    # NON passare use_tls=True qui: incompatibile con STARTTLS
                )
                await asyncio.wait_for(smtp.connect(), timeout=self.t_connect)
                await asyncio.wait_for(smtp.starttls(), timeout=self.t_connect)
            else:
                # Porta 465 — SSL diretto dal primo byte
                smtp = aiosmtplib.SMTP(
                    hostname=self.smtp_server,
                    port=self.smtp_port,
                    use_tls=True,
                )
                await asyncio.wait_for(smtp.connect(), timeout=self.t_connect)

            await asyncio.wait_for(
                smtp.login(self.email_addr, self.email_pass),
                timeout=self.t_connect
            )
            await asyncio.wait_for(
                smtp.send_message(msg),
                timeout=self.t_send
            )
            await smtp.quit()

            logger.info(f"Risposta inviata a: {original.get('from')!r}")
            return True

        except Exception as e:
            logger.warning(
                f"SMTP fallito ({type(e).__name__}: {e}) — "
                "la risposta non è stata inviata, ma il riassunto è disponibile"
            )
            return False

    # -----------------------------------------------------------------------
    # Execute — AsyncGenerator
    # -----------------------------------------------------------------------
    async def execute(
        self,
        text:    str,
        context: Dict[str, Any] = None
    ) -> AsyncGenerator[SkillResult, None]:
        """
        AsyncGenerator: ogni yield aggiorna lo stato vocale del robot.
        """
        self._is_running   = True
        self._current_task = None
        t_start = time.time()

        try:
            # ── 1. Credenziali ──────────────────────────────────────────
            if not self.email_addr or not self.email_pass:
                yield SkillResult.failure_result(
                    "Credenziali email non configurate",
                    error_code=SkillErrorCode.PERMISSION_DENIED,
                    speak="Non ho le credenziali per accedere alla tua email."
                )
                return

            # ── 2. Intent e Filtri ───────────────────────────────────────────────
            context = context or {}
            intent = context.get("intent")
            if not intent or intent == "read":
                # Fallback su regex se LLM ha generato 'read' di default
                intent = self._detect_intent(text)
            
            date_filter = context.get("date_filter", "all")
            search_keyword = context.get("email_id", "")
            reply_to = context.get("reply_to", "") or search_keyword
            sender_filter = ""  # inizializzato qui, valorizzato nel blocco IMAP

            logger.info(f"EmailSkill: intent={intent!r} date_filter={date_filter!r} reply_to={reply_to!r} text={text!r}")

            # v2.0: Reply a mittente specifico — cerca nelle email recenti
            if intent == "reply" and reply_to and self._recent_emails:
                target = self._find_email_by_sender(reply_to)
                if target:
                    emails = [target]
                    logger.info(f"📧 Reply to specific sender: {target['from']}")
                else:
                    yield SkillResult(
                        success=True,
                        message=f"Non trovo email recenti da {reply_to}",
                        speak=f"Non ho trovato email recenti da {reply_to}. Provo a controllare la posta."
                    )
                    # Fallthrough: prova a fare un fetch fresco
                    reply_to = ""  # reset per non ricercare dopo fetch

            elif intent == "deepdive" and self._recent_emails:
                yield SkillResult(
                    success=True,
                    message="Recupero i dettagli dell'email dalla memoria...",
                    speak="Certo, vado ad approfondire."
                )
                
                # Cerca l'email che corrisponde meglio a search_keyword o text
                target_email = self._recent_emails[0] # Default ultima
                if search_keyword:
                    for em in self._recent_emails:
                        if search_keyword.lower() in em["from"].lower() or search_keyword.lower() in em["subject"].lower():
                            target_email = em
                            break
                emails = [target_email]
            else:
                # ── 3. Connessione IMAP ─────────────────────────────────────
                yield SkillResult(
                    success=True,
                    message="Connessione al server email in corso...",
                    speak="Un momento, mi connetto al server email."
                )

                # Estrai sender_filter da email_id o reply_to
                # Viene usato sia per IMAP SEARCH FROM che come post-filtro locale
                sender_filter = (reply_to or search_keyword or "").strip()

                self._current_task = asyncio.create_task(
                    self._fetch_emails_task(intent, date_filter, sender_filter=sender_filter)
                )

                try:
                    emails = await self._current_task
                    if emails:
                        self._recent_emails = emails # salva in memoria per futuri deepdive

                except asyncio.CancelledError:
                    yield SkillResult(
                        success=False,
                        message="Operazione annullata dall'utente",
                        speak="Ok, ho interrotto la lettura delle email."
                    )
                    return

                except PermissionError as e:
                    logger.error(f"Autenticazione IMAP fallita: {e}")
                    yield SkillResult.failure_result(
                        f"Accesso email negato: {e}",
                        error_code=SkillErrorCode.PERMISSION_DENIED,
                        speak="Non riesco ad accedere alla tua email. "
                              "Controlla le credenziali o abilita la password per app."
                    )
                    return

                except (aioimaplib.Abort, asyncio.TimeoutError, OSError) as e:
                    logger.error(f"Errore IMAP ({type(e).__name__}): {e}")
                    yield SkillResult.failure_result(
                        f"Server email non raggiungibile: {e}",
                        error_code=SkillErrorCode.EXTERNAL_SERVICE_ERROR,
                        speak="Non riesco a contattare il server email. "
                              "Controlla la connessione di rete."
                    )
                    return

            # ── 4. Nessuna email nuova ──────────────────────────────────
            # sender_filter è definito sopra (pre-inizializzato a "")
            if not emails:
                if sender_filter and self._recent_emails:
                    # IMAP SEARCH non ha trovato nulla: post-filtro sulla cache locale
                    local_matches = [
                        em for em in self._recent_emails
                        if sender_filter.lower() in em.get("from", "").lower()
                        or sender_filter.lower() in em.get("subject", "").lower()
                    ]
                    if local_matches:
                        logger.info(f"📧 Post-filtro cache: {len(local_matches)} email per '{sender_filter}'")
                        emails = local_matches
                    else:
                        yield SkillResult(
                            success=True,
                            message=f"Nessuna email trovata da '{sender_filter}'",
                            speak=f"Non ho trovato email recenti da {sender_filter}."
                        )
                        return
                elif sender_filter:
                    yield SkillResult(
                        success=True,
                        message=f"Nessuna email trovata da '{sender_filter}'",
                        speak=f"Non ho trovato email recenti da {sender_filter}."
                    )
                    return
                else:
                    yield SkillResult(
                        success=True,
                        message="Nessun messaggio non letto",
                        speak="Non hai nuove email da leggere."
                    )
                    return

            if not emails:  # secondo check dopo post-filtro
                return


            n = len(emails)

            # ── 5. Analisi LLM ──────────────────────────────────────────
            yield SkillResult(
                success=True,
                message=f"Trovate {n} email, analisi in corso...",
                speak=f"Trovate {n} email. Le sto analizzando con l'AI."
            )

            # Costruzione prompt
            reply_instruction = (
                "L'utente vuole rispondere all'email più recente. "
                "Scrivi una risposta professionale e concisa in italiano."
                if intent == "reply" else ""
            )
            emails_json = json.dumps(
                emails, ensure_ascii=False, separators=(",", ":")
            )
            prompt = _LLM_PROMPT_TEMPLATE.format(
                n=n,
                intent=intent,
                reply_instruction=reply_instruction,
                emails_json=emails_json,
                vip_senders=", ".join(self._vip_senders),
            )

            # Chiamata LLM
            llm_data = dict(_FALLBACK_RESPONSE)
            try:
                response = await asyncio.wait_for(
                    self.llm_service.generate(prompt, max_tokens=1024),
                    timeout=self.t_llm,
                )
                if (response is None or not response.text
                        or not response.text.strip()):
                    logger.warning(
                        "LLM: risposta vuota o None — uso fallback"
                    )
                else:
                    llm_data = json.loads(response.text.strip())
                    logger.debug(
                        f"LLM: tokens={response.tokens_used}, "
                        f"latency={response.latency_ms:.0f}ms"
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    f"LLM: timeout dopo {self.t_llm}s — uso fallback"
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    f"LLM: JSON malformato ({e}) — "
                    f"risposta: {response.text[:300]!r}"
                )
            except Exception as e:
                logger.warning(
                    f"LLM: errore inatteso ({type(e).__name__}: {e})"
                )

            # ── 6. Invio risposta (solo se intent="reply") ──────────────
            reply_sent = False
            if intent == "reply" and llm_data.get("reply_draft"):
                yield SkillResult(
                    success=True,
                    message="Invio la risposta email...",
                    speak="Invio la risposta."
                )
                reply_sent = await self._send_reply_task(
                    emails[0], llm_data["reply_draft"]
                )
                if not reply_sent:
                    yield SkillResult(
                        success=True,    # Non blocca il flusso
                        message="Invio risposta fallito, ma il riassunto "
                                "è pronto",
                        speak="Non ho potuto inviare la risposta, "
                              "ma ecco il riassunto delle email."
                    )

            # ── 7. Risultato finale ─────────────────────────────────────
            latency_s = round(time.time() - t_start, 2)
            self._last_execution = time.time()

            summary  = llm_data.get("summary",  _FALLBACK_RESPONSE["summary"])
            priority = llm_data.get("priority",  "normal")
            actions  = llm_data.get("ha_actions", [])
            if not isinstance(actions, list):
                actions = []

            logger.info(
                f"EmailSkill completata: emails={n}, "
                f"priority={priority!r}, reply_sent={reply_sent}, "
                f"latency={latency_s}s"
            )

            yield SkillResult.success_result(
                message=summary,
                speak=summary[:120],    # max 120 char per sintesi vocale
                data={
                    "emails_count": n,
                    "priority":     priority,
                    "summary":      summary,
                    "reply_sent":   reply_sent,
                    "intent":       intent,
                    "latency_s":    latency_s,
                },
                actions=actions,
            )

        finally:
            # Garantisce sempre pulizia dello stato
            self._is_running   = False
            self._current_task = None

    # -----------------------------------------------------------------------
    # Cancel
    # -----------------------------------------------------------------------
    def cancel(self) -> None:
        """Annulla l'operazione in corso. Sicuro da chiamare da qualsiasi
        thread."""
        self._is_running = False
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            logger.info("EmailSkill: task IMAP annullato")

    # -----------------------------------------------------------------------
    # v2.0: Background Polling
    # -----------------------------------------------------------------------
    def start_background_tasks(self):
        """Avvia il polling automatico delle email (chiamato dall'orchestratore)."""
        if not self.email_addr or not self.email_pass:
            logger.warning("EmailSkill: polling non avviato (credenziali mancanti)")
            return
        self._poll_task = asyncio.create_task(self._email_poll_loop())
        logger.info(
            f"📧 Email polling v2.0 avviato (ogni {self._poll_interval // 60} min, "
            f"quiet {self._quiet_start}:00-{self._quiet_end}:00)"
        )

    async def _email_poll_loop(self):
        """Loop background: controlla email ogni N minuti, salta le quiet hours."""
        await asyncio.sleep(60)  # Attendi 1 min dopo boot per stabilizzazione
        while True:
            try:
                hour = datetime.datetime.now().hour
                if self._quiet_start <= hour or hour < self._quiet_end:
                    logger.debug(f"📧 Quiet hours ({hour}:00), polling sospeso")
                    await asyncio.sleep(300)  # Ricontrolla tra 5 min
                    continue
                
                await self._auto_check_emails()
            except asyncio.CancelledError:
                logger.info("📧 Polling loop annullato")
                break
            except Exception as e:
                logger.error(f"📧 Email poll error: {e}")
            
            await asyncio.sleep(self._poll_interval)

    async def _auto_check_emails(self):
        """Polling automatico: fetch, classifica, salva in RAG, bufferizza notifiche."""
        try:
            emails = await self._fetch_emails_task("read", "all")
            if not emails:
                logger.debug("📧 Polling: nessuna email trovata")
                return
            
            new_emails = self._filter_new_emails(emails)
            if not new_emails:
                logger.debug("📧 Polling: nessuna email nuova")
                return
            
            for em in new_emails:
                classification = self._classify_email(em)
                em["_classification"] = classification
                
                # Salva in RAG se importante/interessante
                if classification in ("urgent", "important", "interesting"):
                    await self._store_email_in_rag(em, classification)
                
                # Bufferizza notifica (escludi spam)
                if classification != "spam":
                    self._notification_buffer.append({
                        "from": em.get("from", "sconosciuto"),
                        "subject": em.get("subject", "(senza oggetto)"),
                        "classification": classification,
                        "timestamp": time.time(),
                    })
            
            self._recent_emails = emails  # Aggiorna cache completa
            logger.info(
                f"📧 Polling: {len(new_emails)} nuove email processate "
                f"({len(self._notification_buffer)} notifiche in buffer)"
            )
        except Exception as e:
            logger.error(f"📧 Auto-check failed: {e}")

    # -----------------------------------------------------------------------
    # v2.0: Email Classification
    # -----------------------------------------------------------------------
    def _classify_email(self, email_data: Dict) -> str:
        """Classifica email: urgent|important|interesting|normal|spam."""
        sender = email_data.get("from", "").lower()
        subject = email_data.get("subject", "")
        body = email_data.get("body_snippet", "")
        full_text = f"{subject} {body}"
        
        # VIP sender → important
        for vip in self._vip_senders:
            if vip.lower() in sender:
                return "important"
        
        # Pattern urgenza nel contenuto
        if re.search(r'\b(urgente|scadenza|deadline|immediato|critico|URGENTE)\b', full_text, re.I):
            return "urgent"
        
        # Pattern interessanti (pacchi, corrieri, ordini)
        for pattern in _INTERESTING_PATTERNS:
            if pattern.search(full_text):
                return "interesting"
        
        # Newsletter/marketing → spam
        if re.search(r'\b(unsubscribe|disiscriviti|newsletter|promotional|noreply|no-reply)\b', full_text, re.I):
            return "spam"
        
        return "normal"

    # -----------------------------------------------------------------------
    # v2.0: RAG Storage
    # -----------------------------------------------------------------------
    async def _store_email_in_rag(self, email_data: Dict, classification: str):
        """Salva email nel RAG per memoria a lungo termine."""
        if not self.memory_manager:
            return
        content = (
            f"Email [{classification.upper()}] ricevuta da {email_data.get('from', '?')}, "
            f"Oggetto: {email_data.get('subject', '?')}, "
            f"Data: {email_data.get('date', '?')}, "
            f"Contenuto: {email_data.get('body_snippet', '')[:400]}"
        )
        try:
            await self.memory_manager.store_background(
                f"email_{classification}",
                content,
                "email"
            )
            logger.info(f"📧 RAG: salvata email {classification} da {email_data.get('from', '?')}")
        except Exception as e:
            logger.warning(f"📧 RAG save failed: {e}")

    # -----------------------------------------------------------------------
    # v2.0: Notification Buffer (proattivo)
    # -----------------------------------------------------------------------
    def get_pending_notifications(self) -> List[Dict]:
        """Ritorna le notifiche email pendenti senza consumarle."""
        return list(self._notification_buffer)

    def consume_notifications(self) -> str:
        """Consuma e formatta le notifiche pendenti come testo per il prompt."""
        if not self._notification_buffer:
            return ""
        
        urgent = [n for n in self._notification_buffer if n["classification"] == "urgent"]
        important = [n for n in self._notification_buffer if n["classification"] == "important"]
        interesting = [n for n in self._notification_buffer if n["classification"] == "interesting"]
        normal = [n for n in self._notification_buffer if n["classification"] == "normal"]
        
        lines = []
        if urgent:
            lines.append(f"⚠️ {len(urgent)} email URGENTI:")
            for n in urgent:
                lines.append(f"  - Da {n['from']}: {n['subject']}")
        if important:
            lines.append(f"📌 {len(important)} email importanti:")
            for n in important:
                lines.append(f"  - Da {n['from']}: {n['subject']}")
        if interesting:
            lines.append(f"📦 {len(interesting)} aggiornamenti pacchi/ordini:")
            for n in interesting:
                lines.append(f"  - Da {n['from']}: {n['subject']}")
        if normal:
            lines.append(f"📧 {len(normal)} altre email")
        
        self._notification_buffer.clear()
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # v2.0: Sender Search
    # -----------------------------------------------------------------------
    def _find_email_by_sender(self, query: str) -> Optional[Dict]:
        """Cerca un'email nelle recenti per nome/indirizzo mittente."""
        query_lower = query.lower().strip()
        for em in self._recent_emails:
            sender = em.get("from", "").lower()
            if query_lower in sender:
                return em
        # Fallback: cerca anche nel subject
        for em in self._recent_emails:
            subject = em.get("subject", "").lower()
            if query_lower in subject:
                return em
        return None

    # -----------------------------------------------------------------------
    # v2.0: Deduplication
    # -----------------------------------------------------------------------
    def _filter_new_emails(self, emails: List[Dict]) -> List[Dict]:
        """Filtra solo email non ancora processate (dedup per hash)."""
        new = []
        for em in emails:
            key = f"{em.get('from', '')}_{em.get('subject', '')}_{em.get('date', '')}"
            email_hash = hashlib.md5(key.encode()).hexdigest()
            if email_hash not in self._known_email_ids:
                self._known_email_ids.add(email_hash)
                new.append(em)
        # Evita crescita infinita del set (max 500 hash)
        if len(self._known_email_ids) > 500:
            # Tieni solo gli ultimi 300
            self._known_email_ids = set(list(self._known_email_ids)[-300:])
        return new


# =============================================================================
# TEST — python -m pytest email_skill.py -v
# Richiede: pip install pytest pytest-asyncio
# =============================================================================
#
# import os, json, pytest
# from unittest.mock import AsyncMock, MagicMock, patch
#
#
# @pytest.fixture(autouse=True)
# def set_env(monkeypatch):
#     monkeypatch.setenv("EMAIL_ADDRESS", "test@gmail.com")
#     monkeypatch.setenv("EMAIL_PASSWORD", "fake_app_password")
#
#
# @pytest.fixture
# def skill():
#     mock_llm = AsyncMock()
#     mock_llm.generate.return_value = MagicMock(
#         text=json.dumps({
#             "summary": "Hai 2 email: una da Mario, una da Luca.",
#             "reply_draft": None,
#             "ha_actions": [],
#             "priority": "normal",
#         }),
#         tokens_used=150,
#         latency_ms=800.0,
#     )
#     return EmailSkill(llm_service=mock_llm, config={"min_interval_s": 0})
#
#
# # ── Test match() ─────────────────────────────────────────────────────────────
#
# def test_match_lettura(skill):
#     assert skill.match("leggi le mie email") >= 0.8
#
# def test_match_risposta(skill):
#     assert skill.match("rispondi all'ultima email dicendo che accetto") >= 0.9
#
# def test_match_esclusione_stato_robot(skill):
#     assert skill.match("hai mandato una mail?") == 0.0
#
# def test_match_esclusione_whatsapp(skill):
#     assert skill.match("manda un messaggio whatsapp a Mario") == 0.0
#
# def test_match_no_credentials():
#     s = EmailSkill(llm_service=AsyncMock(), config={})
#     # Rimuovi le env per questo test
#     os.environ.pop("EMAIL_ADDRESS", None)
#     os.environ.pop("EMAIL_PASSWORD", None)
#     s2 = EmailSkill(llm_service=AsyncMock(), config={})
#     assert s2.match("leggi le email") == 0.0
#
#
# # ── Test execute() ───────────────────────────────────────────────────────────
#
# @pytest.mark.asyncio
# async def test_execute_no_credentials():
#     """Senza credenziali, il primo yield è un fallimento."""
#     os.environ.pop("EMAIL_ADDRESS", None)
#     os.environ.pop("EMAIL_PASSWORD", None)
#     s = EmailSkill(llm_service=AsyncMock(), config={})
#     results = [r async for r in s.execute("leggi le email")]
#     assert len(results) >= 1
#     assert not results[-1].success
#     assert results[-1].error_code == SkillErrorCode.PERMISSION_DENIED
#
#
# @pytest.mark.asyncio
# async def test_execute_imap_timeout(skill, monkeypatch):
#     """Timeout IMAP → EXTERNAL_SERVICE_ERROR."""
#     async def failing_task(intent):
#         raise asyncio.TimeoutError()
#     monkeypatch.setattr(skill, "_fetch_emails_task", failing_task)
#     results = [r async for r in skill.execute("leggi le email")]
#     assert not results[-1].success
#     assert results[-1].error_code == SkillErrorCode.EXTERNAL_SERVICE_ERROR
#
#
# @pytest.mark.asyncio
# async def test_execute_no_emails(skill, monkeypatch):
#     """Nessuna email → yield success con messaggio specifico."""
#     async def empty_task(intent):
#         return []
#     monkeypatch.setattr(skill, "_fetch_emails_task", empty_task)
#     results = [r async for r in skill.execute("leggi le email")]
#     assert results[-1].success
#     assert "nessun" in results[-1].message.lower()
#
#
# @pytest.mark.asyncio
# async def test_execute_full_flow(skill, monkeypatch):
#     """Flusso completo: 2 email, LLM risponde, nessun invio."""
#     fake_emails = [
#         {"from": "mario@example.com", "subject": "Riunione",
#          "date": "Mon, 1 Jan 2025", "body_snippet": "Ci vediamo alle 15?"},
#         {"from": "luca@example.com",  "subject": "Preventivo",
#          "date": "Mon, 1 Jan 2025", "body_snippet": "Ti mando il preventivo."},
#     ]
#     async def mock_task(intent):
#         return fake_emails
#     monkeypatch.setattr(skill, "_fetch_emails_task", mock_task)
#
#     results = [r async for r in skill.execute("riassumi le ultime email")]
#     final = results[-1]
#     assert final.success
#     assert final.data["emails_count"] == 2
#     assert final.data["priority"] == "normal"
#     assert not final.data["reply_sent"]
#     assert isinstance(final.actions, list)
