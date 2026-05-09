import asyncio
from pathlib import Path
import json
import datetime
import logging
from typing import Any, Dict, List, Optional

from robopy_controller.robot_ai.skills.base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode, Capability

logger = logging.getLogger("robot_ai.skills.terminal_skill")

SCRIPT_DIR = Path("/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/script")
TERMINAL_MD = SCRIPT_DIR / "terminal.md"
FILE_INDEX_PATH = Path("/mnt/ssd/robopy_controller_host/robopy_controller/logs/file_index.json")

class TerminalSkill(BaseSkill):
    """
    Skill for creating, executing, and fixing python/bash scripts iteratively.
    """

    def __init__(self, memory_manager, llm_service):
        super().__init__()
        self._memory_manager = memory_manager
        self._llm_service = llm_service

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="terminal_skill",
            description="Crea, esegue e itera su script python o bash per compiti generici a terminale.",
            keywords=["script", "terminale", "programma", "codice python", "codice bash"],
            priority=8,
            capabilities=[Capability.MEMORY_RW],
        )

    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        tl = text.lower()
        if "script" in tl or "codice python" in tl or "programma" in tl:
            return 0.85
        if "terminale" in tl or "bash" in tl:
            return 0.8
        return 0.0

    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        context = context or {}
        action = context.get("action")
        task = context.get("task", text)
        
        if action == "run_existing":
            filename = context.get("filename")
            return await self._run_script(filename, task)
            
        if action == "find_file":
            filename = context.get("filename")
            return await self._find_file_in_index(filename)

        if action == "read_file":
            filepath = context.get("filepath")
            return await self._read_file_content(filepath)

        return await self._create_and_run_script(task, text)

    def _read_registry(self) -> List[Dict[str, Any]]:
        if not TERMINAL_MD.exists():
            return []
        try:
            content = TERMINAL_MD.read_text(encoding="utf-8")
            entries = []
            for block in content.split("---"):
                if not block.strip():
                    continue
                try:
                    entries.append(json.loads(block))
                except json.JSONDecodeError:
                    pass
            return entries
        except Exception as e:
            logger.error(f"Error reading terminal.md: {e}")
            return []

    def _write_registry(self, entries: List[Dict[str, Any]]):
        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        out = ""
        for e in entries:
            out += json.dumps(e, ensure_ascii=False) + "\n---\n"
        TERMINAL_MD.write_text(out, encoding="utf-8")

    async def _evaluate_risk(self, task: str) -> tuple[bool, str, bool, list]:
        if not self._llm_service:
            return False, "LLM Service non disponibile", False, []
            
        prompt = f"""
        Valuta il seguente task richiesto dall'utente per un robot Raspberry Pi: '{task}'.
        È pericoloso? Cerca di cancellare file di sistema? Installa librerie non sicure?
        Rispondi ESATTAMENTE e SOLO con un JSON di questo tipo, senza formattazione markdown:
        {{"safe": true/false, "reason": "...", "requires_pip": true/false, "pip_packages": []}}
        """
        try:
            response = await self._llm_service.generate(prompt, max_tokens=1024)
            txt = response.text
            start = txt.find("{")
            end = txt.rfind("}") + 1
            if start != -1 and end != -1:
                data = json.loads(txt[start:end])
                return data.get("safe", False), data.get("reason", "Unknown"), data.get("requires_pip", False), data.get("pip_packages", [])
        except Exception as e:
            logger.error(f"Errore valutazione rischio: {e}")
            return False, f"Errore valutazione: {e}", False, []
        return False, "Analisi fallita", False, []

    async def _generate_script(self, task: str, previous_code: str = "", error_log: str = "") -> str:
        if not self._llm_service:
            return ""
            
        prompt = f"Scrivi un singolo script Python per questo task: {task}\n"
        prompt += "Stampa l'output su stdout. Non usare input iterativi. Sii conciso e robusto.\n"
        prompt += "Restituisci SOLO codice python all'interno di un blocco ```python ... ```\n"
        if previous_code:
            prompt += f"Il codice precedente era:\n```python\n{previous_code}\n```\n"
        if error_log:
            prompt += f"Ha generato questo errore:\n{error_log}\nCorreggilo."
            
        try:
            response = await self._llm_service.generate(prompt, max_tokens=2048)
            txt = response.text
            start = txt.find("```python")
            if start != -1:
                end = txt.find("```", start + 9)
                return txt[start+9:end].strip()
            # fallback try
            start = txt.find("```")
            if start != -1:
                end = txt.find("```", start + 3)
                return txt[start+3:end].strip()
            return txt
        except Exception as e:
            logger.error(f"Errore generazione LLM: {e}")
            return ""

    async def _create_and_run_script(self, task: str, user_request: str) -> SkillResult:
        entries = self._read_registry()
        valid_entries = [e for e in entries if e.get("status") == "approvato"]
        
        if valid_entries and self._llm_service:
            registry_str = "\n".join([f"- {e['filename']}: {e['task']} (Descrizione: {e.get('description', '')})" for e in valid_entries])
            match_prompt = f"L'utente ha chiesto questo task: '{task}'.\nHai a disposizione questi script già pronti:\n{registry_str}\n\nRispondi SOLO con il nome del file esatto (es. task_123.py) se uno di questi script risolve la richiesta. Se nessuno è adatto, rispondi 'NONE'."
            try:
                resp = await self._llm_service.generate(match_prompt, max_tokens=20)
                matched_file = resp.text.strip()
                if matched_file != "NONE" and any(e["filename"] == matched_file for e in valid_entries):
                    logger.info(f"Reusing existing script: {matched_file}")
                    return await self._run_script(matched_file, task)
            except Exception as e:
                logger.warning(f"Errore match script esistente: {e}")
                
        safe, reason, req_pip, pip_pkgs = await self._evaluate_risk(task)
        if not safe:
            return SkillResult.failure_result(f"Rischio rilevato: {reason}", speak=f"Non posso eseguire questa azione per motivi di sicurezza: {reason}")
            
        if req_pip:
            return SkillResult.failure_result("Richiede installazione librerie", speak=f"Questo task richiede l'installazione delle librerie: {', '.join(pip_pkgs)}. Confermi?")

        code = await self._generate_script(task)
        if not code:
            return SkillResult.failure_result("Errore generazione", speak="Non sono riuscito a generare lo script per il task.")
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"task_{timestamp}.py"
        filepath = SCRIPT_DIR / filename
        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(code, encoding="utf-8")
        
        # Genera una descrizione breve per il file md
        description = task
        try:
            desc_resp = await self._llm_service.generate(f"Riassumi in una riga lo scopo di questo script python: {task}", max_tokens=50)
            if desc_resp and desc_resp.text:
                description = desc_resp.text.strip()
        except Exception:
            pass

        entry = {
            "date": datetime.datetime.now().isoformat(),
            "filename": filename,
            "task": task,
            "description": description,
            "user_request": user_request,
            "status": "in lavorazione"
        }
        entries.append(entry)
        self._write_registry(entries)
        
        success, output = await self._execute_and_iterate(filepath, code, task)
        
        if success:
            entry["status"] = "approvato"
            self._write_registry(entries)
            if self._memory_manager:
                try:
                    await self._memory_manager.store_background(
                        f"script {task}", f"Creato script {filename} per: {task}. Output: {output[:100]}", "task"
                    )
                except Exception as e:
                    logger.error(f"Errore RAG store: {e}")
            
            # Genera risposta conversazionale
            speak_text = f"Ho eseguito il controllo. L'output è: {output[:100]}"
            try:
                nat_resp = await self._llm_service.generate(f"L'utente ha chiesto: {task}\nL'output dello script è: {output}\nRispondi all'utente in modo naturale, conversazionale e conciso riportando il risultato. Non usare markdown.", max_tokens=150)
                if nat_resp and nat_resp.text:
                    speak_text = nat_resp.text.strip()
            except Exception:
                pass
                
            return SkillResult.success_result(message=f"Script eseguito. Output: {output}", speak=speak_text)
        else:
            return SkillResult.failure_result("Fallimento iterazioni", speak="Non sono riuscito a completare lo script, proverò a risolverlo stanotte.")

    async def _execute_and_iterate(self, filepath: Path, code: str, task: str) -> tuple[bool, str]:
        for i in range(3):
            proc = await asyncio.create_subprocess_shell(
                f"python3 {filepath}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            out_str = stdout.decode('utf-8')
            err_str = stderr.decode('utf-8')
            
            if proc.returncode == 0:
                return True, out_str
                
            code = await self._generate_script(task, code, err_str)
            if not code:
                break
            filepath.write_text(code, encoding="utf-8")
            
        return False, "Massimo tentativi raggiunti"

    async def _run_script(self, filename: str, task: str) -> SkillResult:
        filepath = SCRIPT_DIR / filename
        if not filepath.exists():
            return SkillResult.failure_result(f"File {filename} non trovato.")
            
        proc = await asyncio.create_subprocess_shell(
            f"python3 {filepath}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            output = stdout.decode('utf-8')
            speak_text = f"Risultato: {output[:100]}"
            if self._llm_service:
                try:
                    nat_resp = await self._llm_service.generate(f"L'utente ha chiesto: {task}\nL'output dello script è: {output}\nRispondi all'utente in modo naturale, conversazionale e conciso riportando i dati salienti. Non usare markdown.", max_tokens=150)
                    if nat_resp and nat_resp.text:
                        speak_text = nat_resp.text.strip()
                except Exception:
                    pass
            return SkillResult.success_result(message=f"Output: {output}", speak=speak_text)
        else:
            return SkillResult.failure_result(message=f"Errore: {stderr.decode()}", speak="Lo script ha restituito un errore.")

    async def _find_file_in_index(self, filename: str) -> SkillResult:
        """[Point 5] Search for a file in the nightly-generated index."""
        if not FILE_INDEX_PATH.exists():
            return SkillResult.failure_result("Indice dei file non ancora generato. Verrà creato stanotte.")
        
        try:
            with open(FILE_INDEX_PATH, "r", encoding="utf-8") as f:
                index = json.load(f)
            
            files = index.get("files", {})
            # Cerca per nome esatto o pattern semplice
            matches = []
            for f_name, f_path in files.items():
                if filename.lower() in f_name.lower():
                    if isinstance(f_path, list):
                        matches.extend(f_path)
                    else:
                        matches.append(f_path)
            
            if not matches:
                return SkillResult.failure_result(f"Nessun file trovato corrispondente a '{filename}'.")
            
            msg = f"Trovati {len(matches)} file:\n" + "\n".join(matches[:10])
            speak = f"Ho trovato {len(matches)} file con quel nome. Quale vuoi che legga?"
            return SkillResult.success_result(message=msg, speak=speak)
        except Exception as e:
            return SkillResult.failure_result(f"Errore lettura indice: {e}")

    async def _read_file_content(self, filepath: str) -> SkillResult:
        """[Point 5] Read the content of a file found via index."""
        path = Path(filepath)
        if not path.exists():
            return SkillResult.failure_result(f"Il file {filepath} non esiste.")
        
        # Sicurezza: impedisci la lettura di file fuori dal workspace o sensibili
        if any(part in path.parts for part in ['.ssh', '.gnupg', 'etc', 'var']):
             return SkillResult.failure_result("Accesso negato a directory di sistema.")

        try:
            content = path.read_text(encoding="utf-8")
            # Troncamento se troppo lungo per il prompt
            if len(content) > 3000:
                content = content[:3000] + "\n... [TRONCATO]"
            
            return SkillResult.success_result(
                message=f"Contenuto di {path.name}:\n{content}",
                speak=f"Ho letto il file {path.name}. Contiene {len(content)} caratteri. Cosa vuoi sapere?"
            )
        except Exception as e:
            return SkillResult.failure_result(f"Errore lettura file: {e}")

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "run_existing", "find_file", "read_file"],
                    "description": "Azione: create (nuovo script), run_existing (esegui), find_file (cerca path), read_file (leggi contenuto)."
                },
                "task": {
                    "type": "string",
                    "description": "Descrizione del task (per create/run_existing)."
                },
                "filename": {
                    "type": "string",
                    "description": "Nome del file da cercare o eseguire."
                },
                "filepath": {
                    "type": "string",
                    "description": "Percorso assoluto del file da leggere (per read_file)."
                }
            },
            "required": ["action"]
        }
