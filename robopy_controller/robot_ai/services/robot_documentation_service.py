"""
Robot AI Services - Robot Documentation Service
================================================
Servizio unificato per consentire a Marcus e alla sua architettura cognitiva (TRINITY, VUI, Live API)
di accedere, ricercare e comprendere tutta la documentazione tecnica del robot:
1. DFMEA (fmea/dfmea.yaml) - Failure modes, RPN, mitigazioni e cause.
2. ECO (docs/ecos/*.md) - Engineering Change Orders (distinguendo quelli generati da Marcus da quelli umani).
3. Lessons Learned (docs/lessons/*.md) - Approfondimenti e lezioni architetturali per sottosistema.
4. Schede Tecniche (docs/specs/SPEC-XX.md) - Vincoli di Zona Rossa, Verde e Gialla.
5. Diario Evolutivo & Quote (docs/evolution/) - Step di auto-miglioramento e stato quota token.
6. File di Configurazione & Codice - Lettura protetta (con blocco assoluto di .env e secrets.yaml).
"""

import os
import re
import yaml
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("robot_ai.robot_documentation_service")

# File segreti o protetti da non leggere mai (SPEC-05 / SPEC-00 Zona Rossa)
FORBIDDEN_FILES = {".env", "secrets.yaml", "secrets.yml", "id_rsa", "id_ed25519"}


def _resolve_workspace_root() -> Path:
    """Risolve la radice del workspace sia su Raspberry Pi 5 che su ambiente di sviluppo."""
    fixed_host_root = Path("/mnt/ssd/robopy_controller_host")
    if (fixed_host_root / "marcus_core_rules.md").exists():
        return fixed_host_root

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "marcus_core_rules.md").exists():
            return parent

    return fixed_host_root


class RobotDocumentationService:
    """
    Gestore per l'accesso e la ricerca semantica e strutturata nella documentazione di Marcus.
    """

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root) if workspace_root else _resolve_workspace_root()
        self.docs_dir = self.workspace_root / "docs"
        self.specs_dir = self.docs_dir / "specs"
        self.lessons_dir = self.docs_dir / "lessons"
        self.ecos_dir = self.docs_dir / "ecos"
        self.evolution_dir = self.docs_dir / "evolution"
        self.fmea_file = self.workspace_root / "fmea" / "dfmea.yaml"

    # =========================================================================
    # 1. DFMEA (Failure Modes, Cause, Effetti, RPN)
    # =========================================================================

    def get_dfmea_entries(self) -> List[Dict[str, Any]]:
        """Carica l'elenco completo dei failure mode da fmea/dfmea.yaml."""
        if not self.fmea_file.exists():
            logger.warning(f"File DFMEA non trovato in: {self.fmea_file}")
            return []
        try:
            with open(self.fmea_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Errore lettura DFMEA: {e}")
            return []

    def search_dfmea(
        self,
        query: str = "",
        subsystem: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Cerca tra i failure modes per testo, sottosistema o stato.
        Ordina per RPN decrescente.
        """
        entries = self.get_dfmea_entries()
        query_lower = query.lower().strip()
        filtered = []

        for item in entries:
            if subsystem and subsystem.lower() not in item.get("subsystem", "").lower():
                continue
            if status and status.upper() != item.get("mitigation_status", "").upper():
                continue

            # Se c'è una query testuale, verifica se compare nei campi principali
            if query_lower:
                searchable_text = " ".join([
                    str(item.get("id", "")),
                    str(item.get("failure_mode", "")),
                    str(item.get("potential_cause", "")),
                    str(item.get("potential_effect", "")),
                    str(item.get("recommended_action", "")),
                    str(item.get("component", "")),
                    str(item.get("subsystem", ""))
                ]).lower()
                if query_lower not in searchable_text:
                    # Token search se corrispondenza esatta fallisce
                    words = query_lower.split()
                    if not any(w in searchable_text for w in words if len(w) > 3):
                        continue

            filtered.append(item)

        # Calcolo RPN effettivo per ordinamento
        def get_rpn(item):
            res_rpn = item.get("residual_scoring", {}).get("rpn")
            if res_rpn is not None:
                return res_rpn
            return item.get("initial_scoring", {}).get("rpn", 0)

        filtered.sort(key=get_rpn, reverse=True)
        return filtered[:limit]

    def format_dfmea_summary(self, items: List[Dict[str, Any]]) -> str:
        """Formatta una lista di failure mode in testo leggibile e naturale."""
        if not items:
            return "Non ho trovato failure mode corrispondenti ai criteri indicati."

        lines = [f"Ho trovato {len(items)} failure mode rilevanti nella DFMEA:"]
        for fm in items:
            fm_id = fm.get("id", "N/D")
            title = fm.get("failure_mode", "N/D")
            subsys = fm.get("subsystem", "N/D")
            status = fm.get("mitigation_status", "OPEN")
            init_rpn = fm.get("initial_scoring", {}).get("rpn", "N/D")
            res_rpn = fm.get("residual_scoring", {}).get("rpn")
            rpn_str = f"RPN residuo: {res_rpn}" if res_rpn is not None else f"RPN iniziale: {init_rpn}"
            action = fm.get("recommended_action", "N/D")

            lines.append(
                f"- **{fm_id}** [{subsys}] (Stato: {status}, {rpn_str}): {title}\n"
                f"  *Causa:* {fm.get('potential_cause', 'N/D')}\n"
                f"  *Azione raccomandata:* {action}"
            )
        return "\n".join(lines)

    # =========================================================================
    # 2. ECO (Engineering Change Orders)
    # =========================================================================

    def get_all_ecos(self) -> List[Dict[str, Any]]:
        """Estrae tutti gli ECO dai file in docs/ecos/*.md."""
        ecos = []
        if not self.ecos_dir.exists():
            return ecos

        for eco_file in self.ecos_dir.glob("*.md"):
            try:
                content = eco_file.read_text(encoding="utf-8", errors="ignore")
                # I singoli ECO sono separati da '## ' o '## 📈 '
                sections = re.split(r'\n##\s+(?:📈\s*)?', content)
                for sec in sections[1:]:  # Salta intestazione file
                    lines = sec.strip().splitlines()
                    if not lines:
                        continue
                    header = lines[0].strip()
                    # Estrai ID e Titolo
                    eco_id = "N/D"
                    eco_title = header
                    if ":" in header:
                        parts = header.split(":", 1)
                        eco_id = parts[0].strip().strip("`")
                        eco_title = parts[1].strip()

                    # Cerca autore, data, sottosistema, stato
                    sec_text = "\n".join(lines)
                    is_marcus = "Marcus" in sec_text and ("autonomamente" in sec_text.lower() or "antigravity" in sec_text.lower())
                    subsystem_match = re.search(r'\*?\*?Sottosistema:?\*?\*?\s*`?([^`\n\r]+)`?', sec_text, re.IGNORECASE)
                    status_match = re.search(r'\*?\*?Stato:?\*?\*?\s*([^`\n\r]+)', sec_text, re.IGNORECASE)

                    ecos.append({
                        "id": eco_id,
                        "title": eco_title,
                        "file": eco_file.name,
                        "is_generated_by_marcus": is_marcus,
                        "subsystem": subsystem_match.group(1).strip() if subsystem_match else "Generale",
                        "status": status_match.group(1).strip() if status_match else "N/D",
                        "raw_content": sec_text[:800]
                    })
            except Exception as e:
                logger.error(f"Errore parsing file ECO {eco_file}: {e}")

        return ecos

    def search_ecos(
        self,
        query: str = "",
        only_marcus: bool = False,
        subsystem: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Cerca tra gli ECO archiviati."""
        all_ecos = self.get_all_ecos()
        query_lower = query.lower().strip()
        results = []

        for eco in all_ecos:
            if only_marcus and not eco["is_generated_by_marcus"]:
                continue
            if subsystem and subsystem.lower() not in eco["subsystem"].lower():
                continue
            if query_lower:
                text = f"{eco['id']} {eco['title']} {eco['subsystem']} {eco['raw_content']}".lower()
                if query_lower not in text:
                    words = query_lower.split()
                    if not any(w in text for w in words if len(w) > 3):
                        continue
            results.append(eco)

        return results[:limit]

    def format_ecos_summary(self, items: List[Dict[str, Any]]) -> str:
        """Formatta una sintesi naturale degli ECO trovati."""
        if not items:
            return "Nessun Engineering Change Order (ECO) trovato per i criteri specificati."

        lines = [f"Ho trovato {len(items)} ECO nel registro storico:"]
        for eco in items:
            author_badge = "🤖 [Generato autonomamente da Marcus]" if eco["is_generated_by_marcus"] else "👤 [Team Ingegneria]"
            lines.append(
                f"- **{eco['id']}**: {eco['title']}\n"
                f"  *Autore:* {author_badge} | *Sottosistema:* `{eco['subsystem']}` | *Stato:* {eco['status']}"
            )
        return "\n".join(lines)

    # =========================================================================
    # 3. Lesson Learned (docs/lessons/*.md)
    # =========================================================================

    def search_lessons(self, query: str = "", limit: int = 3) -> List[Dict[str, Any]]:
        """Cerca sezioni rilevanti nei file di lezioni apprese."""
        results = []
        if not self.lessons_dir.exists():
            return results

        query_lower = query.lower().strip()
        words = [w for w in query_lower.split() if len(w) > 3]

        for lesson_file in self.lessons_dir.glob("*.md"):
            try:
                content = lesson_file.read_text(encoding="utf-8", errors="ignore")
                # Splitta per paragrafi o intestazioni ##
                sections = re.split(r'\n(?=##\s+)', content)
                for sec in sections:
                    sec_lower = sec.lower()
                    score = 0
                    if query_lower and query_lower in sec_lower:
                        score += 3
                    for w in words:
                        if w in sec_lower:
                            score += 1

                    if score > 0 or not query_lower:
                        # Estrai titolo sezione
                        lines = sec.strip().splitlines()
                        sec_title = lines[0].replace("#", "").strip() if lines else lesson_file.name
                        results.append({
                            "file": lesson_file.name,
                            "title": sec_title,
                            "content": sec.strip()[:1000],
                            "score": score
                        })
            except Exception as e:
                logger.error(f"Errore lettura lessons {lesson_file}: {e}")

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # =========================================================================
    # 4. Schede Tecniche (docs/specs/SPEC-XX.md)
    # =========================================================================

    def search_specs(self, query: str = "", spec_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Cerca informazioni e vincoli nelle schede tecniche SPEC."""
        results = []
        if not self.specs_dir.exists():
            return results

        query_lower = query.lower().strip()

        for spec_file in self.specs_dir.glob("SPEC-*.md"):
            if spec_id and spec_id.lower() not in spec_file.name.lower():
                continue
            try:
                content = spec_file.read_text(encoding="utf-8", errors="ignore")
                # Estrai sezioni chiave: Zona Rossa, Verde, Gialla
                red_zone = ""
                green_zone = ""
                yellow_zone = ""

                rz_match = re.search(r'(##\s+3\.\s+🔴\s+ZONA\s+ROSSA.*?)(?=\n##\s+|$)', content, re.DOTALL)
                if rz_match:
                    red_zone = rz_match.group(1).strip()

                gz_match = re.search(r'(##\s+4\.\s+🟢\s+ZONA\s+VERDE.*?)(?=\n##\s+|$)', content, re.DOTALL)
                if gz_match:
                    green_zone = gz_match.group(1).strip()

                yz_match = re.search(r'(##\s+5\.\s+🟡\s+ZONA\s+GIALLA.*?)(?=\n##\s+|$)', content, re.DOTALL)
                if yz_match:
                    yellow_zone = yz_match.group(1).strip()

                # Se query fornita, controlla occorrenze
                if query_lower:
                    if (query_lower not in content.lower() and
                            not any(w in content.lower() for w in query_lower.split() if len(w) > 3)):
                        continue

                results.append({
                    "spec_id": spec_file.stem,
                    "file": spec_file.name,
                    "red_zone": red_zone,
                    "green_zone": green_zone,
                    "yellow_zone": yellow_zone,
                    "full_snippet": content[:1200]
                })
            except Exception as e:
                logger.error(f"Errore lettura scheda {spec_file}: {e}")

        return results

    # =========================================================================
    # 5. Diario Evolutivo & Quote Token (docs/evolution/)
    # =========================================================================

    def get_evolution_summary(self) -> Dict[str, Any]:
        """Restituisce lo stato corrente dell'auto-evoluzione, diario e quota token."""
        journal_file = self.evolution_dir / "evolution_journal.md"
        ledger_file = self.evolution_dir / "token_quota_ledger.json"
        checkpoint_file = self.evolution_dir / "evolution_checkpoint.json"

        summary: Dict[str, Any] = {
            "journal_entries": [],
            "quota_status": {},
            "has_pending_checkpoint": False,
            "pending_checkpoint": None
        }

        # 1. Diario di bordo
        if journal_file.exists():
            try:
                text = journal_file.read_text(encoding="utf-8", errors="ignore")
                # Estrai gli ultimi cicli
                entries = re.split(r'\n###\s+', text)
                summary["journal_entries"] = [e.strip() for e in entries[1:4]]
            except Exception as e:
                logger.error(f"Errore lettura diario evolutivo: {e}")

        # 2. Checkpoint sospesi
        if checkpoint_file.exists():
            try:
                data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                if data.get("status") == "SUSPENDED_QUOTA_90_PERCENT":
                    summary["has_pending_checkpoint"] = True
                    summary["pending_checkpoint"] = data
            except Exception:
                pass

        # 3. Quota tracker 4h
        if ledger_file.exists():
            try:
                data = json.loads(ledger_file.read_text(encoding="utf-8"))
                entries = data.get("entries", [])
                # Somma token ultime 4h
                now = __import__("time").time()
                recent = [e for e in entries if e.get("timestamp", 0) >= now - (4 * 3600)]
                used_tokens = sum(e.get("total_tokens", 0) for e in recent)
                summary["quota_status"] = {
                    "used_tokens_4h": used_tokens,
                    "entries_count": len(recent)
                }
            except Exception:
                pass

        return summary

    # =========================================================================
    # 6. Lettura Sicura di Qualsiasi File del Workspace
    # =========================================================================

    def read_workspace_file(self, relative_path: str, max_lines: int = 200) -> Tuple[bool, str]:
        """
        Legge in modo protetto un file all'interno del workspace.
        Rifiuta severamente file segreti (.env, secrets.yaml) e tentativi di path traversal.
        """
        # Sanitizzazione
        cleaned_path = relative_path.replace("\\", "/").strip().lstrip("/")
        path_obj = Path(cleaned_path)

        # Controllo file proibiti
        if path_obj.name.lower() in FORBIDDEN_FILES or any(part.lower() in FORBIDDEN_FILES for part in path_obj.parts):
            logger.warning(f"Tentativo di lettura file segreto bloccato: {relative_path}")
            return False, "Accesso negato: questo file contiene segreti o credenziali private protette da SPEC-05 e SPEC-00."

        target = (self.workspace_root / path_obj).resolve()

        # Verifica contenimento nel workspace
        try:
            target.relative_to(self.workspace_root.resolve())
        except ValueError:
            return False, "Accesso negato: percorso esterno al workspace di Marcus."

        if not target.exists() or not target.is_file():
            return False, f"File non trovato nel workspace: {cleaned_path}"

        try:
            with open(target, "r", encoding="utf-8", errors="ignore") as f:
                lines = [f.readline() for _ in range(max_lines)]
                content = "".join(lines)
            return True, content
        except Exception as e:
            return False, f"Errore durante la lettura del file: {e}"

    # =========================================================================
    # 7. Risolutore di Query in Linguaggio Naturale
    # =========================================================================

    def answer_documentation_query(self, query: str) -> str:
        """
        Analizza la domanda dell'utente e genera una risposta naturale e dettagliata
        basata sui documenti ufficiali del robot.
        """
        q_lower = query.lower()

        # 1. Domande sulla DFMEA / Failure Modes / Rischi
        if any(k in q_lower for k in ["fmea", "dfmea", "failure", "guasto", "guasti", "rpn", "rischio", "rischi"]):
            # Cerca sottosistema menzionato
            subsystem = None
            for sub in ["nav", "slam", "audio", "vui", "vision", "hailo", "motori", "actuation", "power", "bms", "system"]:
                if sub in q_lower:
                    subsystem = sub
                    break
            items = self.search_dfmea(query=query, subsystem=subsystem, limit=3)
            return self.format_dfmea_summary(items)

        # 2. Domande sugli ECO / Modifiche Storiche / Cambiamenti
        if any(k in q_lower for k in ["eco", "engineering change", "modifiche", "cambiamenti"]):
            only_marcus = any(k in q_lower for k in ["tu", "tuoi", "marcus", "autonomi", "da te"])
            items = self.search_ecos(query=query, only_marcus=only_marcus, limit=3)
            return self.format_ecos_summary(items)

        # 3. Domande su Regole, Schede Tecniche (SPEC) e Zone di Rischio
        if any(k in q_lower for k in ["scheda tecnica", "specifica", "spec", "zona rossa", "zona verde", "zona gialla", "regola", "regole"]):
            specs = self.search_specs(query=query)
            if specs:
                first = specs[0]
                res = f"Dalla scheda tecnica **{first['spec_id']}**:\n"
                if "rossa" in q_lower and first["red_zone"]:
                    res += f"\n{first['red_zone']}"
                elif "verde" in q_lower and first["green_zone"]:
                    res += f"\n{first['green_zone']}"
                elif "gialla" in q_lower and first["yellow_zone"]:
                    res += f"\n{first['yellow_zone']}"
                else:
                    res += f"\n{first['full_snippet'][:600]}..."
                return res
            return "Non ho trovato schede tecniche corrispondenti al tema richiesto."

        # 4. Domande su Lezioni Apprese / Lesson Learned
        if any(k in q_lower for k in ["lezione", "lezioni", "lesson", "imparato", "appreso"]):
            lessons = self.search_lessons(query=query, limit=2)
            if lessons:
                lines = [f"Ecco cosa abbiamo appreso nei collaudi tecnici ({lessons[0]['file']}):"]
                for l in lessons:
                    lines.append(f"\n### {l['title']}\n{l['content'][:500]}...")
                return "\n".join(lines)

        # 5. Domande sull'Auto-Evoluzione, Diario e Quota
        if any(k in q_lower for k in ["evoluzione", "automiglioramento", "quota", "token", "diario", "checkpoint"]):
            evo = self.get_evolution_summary()
            q_info = evo.get("quota_status", {})
            used_4h = q_info.get("used_tokens_4h", 0)
            res = (
                f"**Stato Auto-Evoluzione di Marcus:**\n"
                f"- Quota token 4h consumata: ~{used_4h:,} token (soglia di guardia 90%).\n"
                f"- Checkpoint attività in sospeso: {'Sì (pronto alla ripresa)' if evo['has_pending_checkpoint'] else 'Nessuno'}.\n"
            )
            if evo["journal_entries"]:
                res += f"\n*Ultimo ciclo nel diario evolutivo:*\n{evo['journal_entries'][0][:300]}..."
            return res

        # Ricerca generale combinata
        dfmea_results = self.search_dfmea(query=query, limit=2)
        if dfmea_results:
            return self.format_dfmea_summary(dfmea_results)

        lessons = self.search_lessons(query=query, limit=1)
        if lessons:
            return f"Dalla documentazione tecnica ({lessons[0]['file']}):\n\n{lessons[0]['content'][:600]}..."

        return (
            "Ho consultato l'archivio della documentazione del robot (DFMEA, ECO, Schede Tecniche e Lezioni), "
            f"ma non ho trovato riferimenti esatti per '{query}'. Puoi specificare se cerchi un guasto FMEA, un ECO o una regola di sistema?"
        )
