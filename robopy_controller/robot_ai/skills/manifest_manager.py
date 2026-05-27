"""
Robot AI Skills - Manifest Manager
====================================
Gestione atomica del file skills_manifest.json per le skill generate.
"""

import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger("robot_ai.manifest_manager")


class ManifestManager:
    """
    Gestore del registro centrale skill generate (skills_manifest.json).

    Il manifest traccia tutte le skill generate, il loro stato di
    approvazione, versione del prompt, capability e topic usati.

    Operazioni di scrittura sono atomiche (write temp + rename)
    per evitare corruzione del file.
    """

    def __init__(self, manifest_path: Optional[Path] = None):
        if manifest_path is None:
            # Default path relativo al package
            manifest_path = (
                Path(__file__).parent / "active" / "skills_manifest.json"
            )
        self.manifest_path = manifest_path
        self._ensure_manifest_exists()

    def _ensure_manifest_exists(self):
        """Crea il manifest se non esiste."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self._write_manifest({})

    def get_manifest(self) -> Dict[str, Any]:
        """Legge e restituisce l'intero manifest."""
        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logger.warning("Manifest corrotto o mancante, creazione nuovo.")
            self._write_manifest({})
            return {}

    def add_skill(
        self,
        name: str,
        file_name: str,
        version: str = "1.0",
        prompt_version: str = "MARCUS_PROMPT_v2.1",
        rak_hash: str = "",
        capabilities: Optional[List[str]] = None,
        topics_sub: Optional[List[str]] = None,
        topics_pub: Optional[List[str]] = None,
        iterations_needed: int = 1,
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Aggiunge una skill al manifest con enabled: false.

        Args:
            name: Nome univoco della skill
            file_name: Nome del file Python
            version: Versione della skill
            prompt_version: Versione del prompt Marcus usato
            rak_hash: Hash del contesto RAK
            capabilities: Lista capability dichiarate
            topics_sub: Topic sottoscritti
            topics_pub: Topic pubblicati
            iterations_needed: Numero iterazioni di generazione necessarie
            notes: Note aggiuntive

        Returns:
            Entry del manifest appena creata
        """
        manifest = self.get_manifest()
        now = datetime.utcnow().isoformat() + "Z"

        entry = {
            "file": file_name,
            "version": version,
            "generated_at": now,
            "prompt_version": prompt_version,
            "rak_hash": rak_hash,
            "capabilities": capabilities or [],
            "topics_sub": topics_sub or [],
            "topics_pub": topics_pub or [],
            "enabled": False,
            "notes": notes or "In attesa di test manuale post-deploy",
            "iterations_needed": iterations_needed,
            "approved_by": "",
            "approved_at": "",
        }

        manifest[name] = entry
        self._write_manifest(manifest)
        logger.info(f"Skill '{name}' aggiunta al manifest (enabled=false)")
        return entry

    def enable_skill(self, name: str, approved_by: str = "utente") -> bool:
        """
        Abilita una skill nel manifest.

        Args:
            name: Nome della skill
            approved_by: Chi ha approvato

        Returns:
            True se la skill è stata trovata e abilitata
        """
        manifest = self.get_manifest()
        if name not in manifest:
            logger.warning(f"Skill '{name}' non trovata nel manifest")
            return False

        manifest[name]["enabled"] = True
        manifest[name]["approved_by"] = approved_by
        manifest[name]["approved_at"] = datetime.utcnow().isoformat() + "Z"
        self._write_manifest(manifest)
        logger.info(f"Skill '{name}' abilitata da {approved_by}")
        return True

    def disable_skill(self, name: str) -> bool:
        """Disabilita una skill nel manifest."""
        manifest = self.get_manifest()
        if name not in manifest:
            return False

        manifest[name]["enabled"] = False
        self._write_manifest(manifest)
        logger.info(f"Skill '{name}' disabilitata")
        return True

    def remove_skill(self, name: str) -> bool:
        """Rimuove una skill dal manifest."""
        manifest = self.get_manifest()
        if name not in manifest:
            return False

        del manifest[name]
        self._write_manifest(manifest)
        logger.info(f"Skill '{name}' rimossa dal manifest")
        return True

    def get_enabled_skills(self) -> Dict[str, Any]:
        """Restituisce solo le skill con enabled=true."""
        manifest = self.get_manifest()
        return {
            name: entry
            for name, entry in manifest.items()
            if entry.get("enabled", False)
        }

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Restituisce i dati di una skill specifica."""
        return self.get_manifest().get(name)

    def update_skill(self, name: str, **kwargs) -> bool:
        """Aggiorna campi specifici di una skill."""
        manifest = self.get_manifest()
        if name not in manifest:
            return False

        for key, value in kwargs.items():
            manifest[name][key] = value

        self._write_manifest(manifest)
        return True

    def _write_manifest(self, data: Dict[str, Any]):
        """
        Scrittura atomica del manifest.

        Scrive su file temporaneo nella stessa directory, poi rinomina.
        Se il rename fallisce (Windows), fa fallback su scrittura diretta.
        """
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Atomic write: temp file + rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.manifest_path.parent),
                suffix=".tmp",
                prefix="manifest_",
            )
            try:
                with open(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

                # Rename (atomic on most FS)
                tmp = Path(tmp_path)
                tmp.replace(self.manifest_path)
            except Exception:
                # Cleanup temp file on failure
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass
                raise

        except OSError:
            # Fallback: direct write (non-atomic but works on all platforms)
            with open(self.manifest_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
