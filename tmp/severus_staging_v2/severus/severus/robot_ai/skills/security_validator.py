"""
Robot AI Skills - Security Validator
=====================================
Validazione AST per sicurezza e conformità delle skill generate.
Verifica import vietati, chiamate pericolose e conformità al contratto BaseSkill.
"""

import ast
import logging
from dataclasses import dataclass, field
from typing import List, Optional


logger = logging.getLogger("robot_ai.security_validator")


@dataclass
class ValidationResult:
    """Risultato della validazione di una skill."""
    is_safe: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    class_name: Optional[str] = None
    has_get_metadata: bool = False
    has_match: bool = False
    has_execute: bool = False

    @property
    def is_valid(self) -> bool:
        """La skill è sicura E conforme al contratto BaseSkill."""
        return (
            self.is_safe
            and self.has_get_metadata
            and self.has_match
            and self.has_execute
            and self.class_name is not None
        )

    def summary(self) -> str:
        """Riassunto testuale del risultato."""
        lines = []
        if self.errors:
            lines.append("ERRORI:")
            for e in self.errors:
                lines.append(f"  - {e}")
        if self.warnings:
            lines.append("WARNING:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        if not self.errors and not self.warnings:
            lines.append("Nessun problema rilevato.")

        lines.append(f"Classe trovata: {self.class_name or 'NESSUNA'}")
        lines.append(f"get_metadata(): {'SI' if self.has_get_metadata else 'NO'}")
        lines.append(f"match(): {'SI' if self.has_match else 'NO'}")
        lines.append(f"execute(): {'SI' if self.has_execute else 'NO'}")
        return "\n".join(lines)


class SecurityValidator(ast.NodeVisitor):
    """
    Validatore AST per skill generate.

    Controlla:
    1. Import vietati (os, sys, subprocess, shutil)
    2. Funzioni pericolose (eval, exec, open, __import__)
    3. Ereditarietà da BaseSkill
    4. Presenza metodi obbligatori (get_metadata, match, execute)
    5. execute() deve essere async
    """

    FORBIDDEN_IMPORTS = frozenset({'os', 'sys', 'subprocess', 'shutil'})
    FORBIDDEN_CALLS = frozenset({'eval', 'exec', 'open', '__import__'})

    REQUIRED_METHODS = {'get_metadata', 'match', 'execute'}

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self._skill_class_name: Optional[str] = None
        self._found_methods: set = set()
        self._async_methods: set = set()
        self._in_skill_class: bool = False

    def validate(self, source_code: str) -> ValidationResult:
        """
        Valida il codice sorgente di una skill.

        Args:
            source_code: Codice Python della skill

        Returns:
            ValidationResult con dettagli della validazione
        """
        self.errors = []
        self.warnings = []
        self._skill_class_name = None
        self._found_methods = set()
        self._async_methods = set()
        self._in_skill_class = False

        # Phase 1: Parse AST
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return ValidationResult(
                is_safe=False,
                errors=[f"Errore di sintassi Python: {e}"],
            )

        # Phase 2: Visit AST nodes
        self.visit(tree)

        # Phase 3: Contratto BaseSkill
        if self._skill_class_name is None:
            self.errors.append(
                "Nessuna classe che eredita da BaseSkill trovata nel modulo"
            )

        if 'execute' in self._found_methods and 'execute' not in self._async_methods:
            self.errors.append(
                "Il metodo execute() deve essere 'async def execute(...)'"
            )

        # Phase 4: Warning per metodi mancanti (non bloccanti)
        missing = self.REQUIRED_METHODS - self._found_methods
        for method in missing:
            self.errors.append(f"Metodo obbligatorio mancante: {method}()")

        return ValidationResult(
            is_safe=len(self.errors) == 0,
            errors=list(self.errors),
            warnings=list(self.warnings),
            class_name=self._skill_class_name,
            has_get_metadata='get_metadata' in self._found_methods,
            has_match='match' in self._found_methods,
            has_execute='execute' in self._found_methods,
        )

    # --- AST visitors ---

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root_module = alias.name.split('.')[0]
            if root_module in self.FORBIDDEN_IMPORTS:
                self.errors.append(f"Import vietato: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            root_module = node.module.split('.')[0]
            if root_module in self.FORBIDDEN_IMPORTS:
                self.errors.append(f"Import vietato: from {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check direct function calls: eval(), exec(), open(), __import__()
        if isinstance(node.func, ast.Name):
            if node.func.id in self.FORBIDDEN_CALLS:
                self.errors.append(f"Funzione pericolosa: {node.func.id}()")

        # Check method calls: obj.__import__()
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in self.FORBIDDEN_CALLS:
                self.errors.append(
                    f"Funzione pericolosa: .{node.func.attr}()"
                )

        # Check print() — warning only, not blocking
        if isinstance(node.func, ast.Name) and node.func.id == 'print':
            self.warnings.append(
                "Uso di print() rilevato. Usare self.get_logger() o logging."
            )

        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        # Check if this class inherits from BaseSkill
        inherits_baseskill = False
        for base in node.bases:
            base_name = None
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr

            if base_name == 'BaseSkill':
                inherits_baseskill = True
                break

        if inherits_baseskill:
            self._skill_class_name = node.name
            self._in_skill_class = True

            # Scan methods of this class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in self.REQUIRED_METHODS:
                        self._found_methods.add(item.name)
                    if isinstance(item, ast.AsyncFunctionDef):
                        self._async_methods.add(item.name)

            self._in_skill_class = False

        self.generic_visit(node)


def validate_skill_source(source_code: str) -> ValidationResult:
    """
    Funzione di convenienza per validare codice sorgente di una skill.

    Args:
        source_code: Codice Python della skill

    Returns:
        ValidationResult
    """
    validator = SecurityValidator()
    return validator.validate(source_code)
