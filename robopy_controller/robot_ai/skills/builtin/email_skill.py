# robopy_controller/robot_ai/skills/builtin/email_skill.py
"""
Robot AI Skills - Email Skill
==============================
Legge, riassume e risponde alle email tramite IMAP/SMTP con analisi LLM.

Pattern: AsyncGenerator (come SearchSkill) — feedback vocale progressivo.
Dipendenze esterne: aioimaplib, aiosmtplib
"""

import asyncio
import email as email_lib
import json
import logging
import os
import re
import time
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

_LLM_PROMPT_TEMPLATE = """
Sei l'assistente AI del robot Marcus. Analizza le seguenti {n} email.
Rispondi ESCLUSIVAMENTE con un oggetto JSON valido.
Non aggiungere testo, markdown o backtick prima o dopo il JSON.

Intent dell'utente: {intent}
{reply_instruction}

Email (in ordine cronologico inverso):
{emails_json}

Schema JSON richiesto (rispetta esattamente questa struttura):
{{
  "summary":     "<stringa, max 150 char, comprensibile se letta ad alta voce, in italiano>",
  "reply_draft": "<bozza risposta in prima persona come robot Marcus> | null",
  "ha_actions":  [{{"type": "reminder|light_on|light_off|speak", "payload": {{"detail": "<str>"}}}}],
  "priority":    "urgent|normal|low"
}}

Vincoli:
- "summary" inizia sempre con "Hai N email:" e cita i mittenti principali
- "ha_actions" è sempre una lista JSON, mai null (usa [] se nessuna azione)
- "priority" è "urgent" solo per emergenze o scadenze entro 24h
- "reply_draft" è null se intent != "reply"
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

    def __init__(self, llm_service, config: Dict[str, Any] = None):
        super().__init__()
        self.llm_service = llm_service
        self.config      = config or {}

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
        self.max_emails   = self.config.get("max_emails",       8)
        self.min_interval = self.config.get("min_interval_s",  30)

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
                    "enum": ["read", "reply", "urgent", "summary", "new"],
                    "description": (
                        "L'intento dell'operazione email. "
                        "'read': leggi ultime email, 'reply': rispondi all'ultima, "
                        "'urgent': cerca email urgenti, 'summary': riassumi inbox, "
                        "'new': controlla se ci sono nuovi messaggi."
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
            description="Legge, riassume e risponde alle email tramite IMAP/SMTP",
            version="1.1.0",
            keywords=[
                "email", "mail", "posta", "messaggi", "inbox", "casella",
                "leggi", "controlla", "rispondi", "urgent", "importante", "check_emails"
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
    async def _fetch_emails_task(self, intent: str) -> List[Dict]:
        """
        Connessione IMAP, fetch dei messaggi non letti, parsing.
        Cancellabile tramite asyncio.Task.cancel().
        """
        emails: List[Dict] = []

        imap = aioimaplib.IMAP4_SSL(host=self.imap_server, port=self.imap_port)
        try:
            # Handshake iniziale
            await asyncio.wait_for(
                imap.wait_hello_from_server(), timeout=self.t_connect
            )

            # Login — fallimento qui = credenziali errate (non errore di rete)
            login_resp = await asyncio.wait_for(
                imap.login(self.email_addr, self.email_pass),
                timeout=self.t_connect
            )
            # aioimaplib ritorna (status, [data]): status è 'OK' o 'NO'/'BAD'
            if login_resp[0].upper() != "OK":
                raise PermissionError(f"Login IMAP rifiutato: {login_resp}")

            await asyncio.wait_for(
                imap.select("INBOX"), timeout=self.t_connect
            )

            # Fetch solo l'ultimo (intent reply) o tutti gli UNSEEN
            if intent == "reply":
                # Cerca l'ultimo messaggio in assoluto
                _, search_data = await asyncio.wait_for(
                    imap.search("ALL"), timeout=self.t_fetch
                )
            else:
                _, search_data = await asyncio.wait_for(
                    imap.search("UNSEEN"), timeout=self.t_fetch
                )
                # Fallback: se non ci sono nuove mail, proviamo a prendere le ultime ALL
                # per dare comunque una risposta all'utente (tranne per intent urgent che deve essere specifico)
                raw_ids_str = " ".join(b.decode() for b in search_data if b)
                if not raw_ids_str.strip() and intent in ["read", "summary"]:
                    logger.info("EmailSkill: UNSEEN vuoto, fallback su ALL per intent 'read/summary'")
                    _, search_data = await asyncio.wait_for(
                        imap.search("ALL"), timeout=self.t_fetch
                    )

            # Uniamo tutti i chunk della risposta SEARCH e filtriamo solo gli ID numerici
            raw_ids_str = " ".join(b.decode() for b in search_data if b)
            all_ids     = [i for i in raw_ids_str.split() if i.isdigit()]
            msg_ids     = all_ids[-self.max_emails:]       # ultimi N (più recenti)

            logger.info(
                f"IMAP: trovati {len(all_ids)} ID validi, "
                f"processo gli ultimi {len(msg_ids)}: {msg_ids}"
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
                    if not isinstance(item, bytes):
                        continue
                    # Decodifica snippet per controllo header IMAP (più permissivo)
                    snippet_upper = item[:100].decode(errors='ignore').upper()
                    # Scartiamo l'header del protocollo (es: "* 1 FETCH (RFC822 {1234})") e il footer ")"
                    if ("FETCH" in snippet_upper and "RFC822" in snippet_upper) or snippet_upper.strip() == ")":
                        continue
                    # Il primo chunk che non è protocollo è il vero contenuto RFC822
                    raw_bytes = item
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

            # ── 2. Intent ───────────────────────────────────────────────
            intent = self._detect_intent(text)
            logger.info(f"EmailSkill: intent={intent!r} text={text!r}")

            # ── 3. Connessione IMAP ─────────────────────────────────────
            yield SkillResult(
                success=True,
                message="Connessione al server email in corso...",
                speak="Un momento, mi connetto al server email."
            )

            self._current_task = asyncio.create_task(
                self._fetch_emails_task(intent)
            )

            try:
                emails = await self._current_task

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
            if not emails:
                yield SkillResult(
                    success=True,
                    message="Nessun messaggio non letto",
                    speak="Non hai nuove email da leggere."
                )
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
