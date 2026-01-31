"""
Robot AI RAG - Metadata Manager
================================
Manages structured metadata for memories.
Supports entity extraction, tagging, and type inference.
"""

import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from .memory_store import MemoryType


@dataclass
class Entity:
    """Extracted entity from text."""
    text: str
    entity_type: str
    start: int = 0
    end: int = 0
    confidence: float = 1.0


class MetadataManager:
    """
    Manages metadata extraction and enrichment for memories.
    
    Features:
    - Entity extraction (names, locations, times)
    - Automatic tagging
    - Memory type inference
    - Importance scoring
    
    Usage:
        manager = MetadataManager()
        metadata = manager.extract("User's birthday is March 15th")
        # {'entities': [...], 'tags': ['personal', 'date'], 'type': 'user_preference'}
    """
    
    # Entity patterns
    PATTERNS = {
        "time": [
            re.compile(r'\b(\d{1,2}[:.]\d{2})\b'),
            re.compile(r'\b(mattina|pomeriggio|sera|notte)\b', re.IGNORECASE),
            re.compile(r'\b(ore? \d{1,2})\b', re.IGNORECASE),
        ],
        "date": [
            re.compile(r'\b(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\b'),
            re.compile(r'\b(oggi|domani|ieri|dopodomani)\b', re.IGNORECASE),
            re.compile(r'\b(lunedì|martedì|mercoledì|giovedì|venerdì|sabato|domenica)\b', re.IGNORECASE),
            re.compile(r'\b(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\b', re.IGNORECASE),
        ],
        "location": [
            re.compile(r'\b(cucina|soggiorno|camera|bagno|studio|garage|giardino|corridoio|ingresso)\b', re.IGNORECASE),
            re.compile(r'\bin (\w+)\b', re.IGNORECASE),
            re.compile(r'\ba (\w+)\b', re.IGNORECASE),
        ],
        "person": [
            re.compile(r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b'),  # Capitalized names
        ],
        "number": [
            re.compile(r'\b(\d+(?:[,.]\d+)?)\s*(%|gradi|°C|euro|€)?\b'),
        ],
    }
    
    # Keywords for tagging
    TAG_KEYWORDS = {
        "preferenza": ["piace", "preferisco", "amo", "adoro", "odio", "detesto", "preferenza"],
        "musica": ["musica", "canzone", "artista", "playlist", "album", "spotify"],
        "luce": ["luce", "luci", "lampada", "lampadina", "accendi", "spegni", "illuminazione"],
        "temperatura": ["temperatura", "caldo", "freddo", "climatizzatore", "riscaldamento", "gradi"],
        "routine": ["sempre", "ogni giorno", "ogni sera", "abitudine", "solito", "di solito"],
        "promemoria": ["ricorda", "ricordami", "promemoria", "non dimenticare", "appuntamento"],
        "meteo": ["meteo", "tempo", "pioggia", "sole", "temperatura", "previsioni"],
        "navigazione": ["vai", "porta", "spostati", "muoviti", "vieni", "seguimi"],
        "persona": ["nome", "chiamami", "sono", "famiglia", "amico", "collega"],
    }
    
    # Type inference keywords
    TYPE_KEYWORDS = {
        MemoryType.USER_PREFERENCE: ["piace", "preferisco", "amo", "odio", "voglio", "preferenza"],
        MemoryType.ROUTINE: ["sempre", "ogni", "solito", "abitudine", "routine"],
        MemoryType.LEARNED_FACT: ["è", "sono", "significa", "vuol dire", "si chiama"],
        MemoryType.TASK: ["devo", "ricordami", "promemoria", "fare", "comprare"],
        MemoryType.LOCATION: ["cucina", "soggiorno", "camera", "bagno", "stanza"],
        MemoryType.PERSON: ["si chiama", "nome è", "famiglia", "amico", "moglie", "marito"],
    }
    
    def __init__(self, known_entities: Dict[str, Set[str]] = None):
        """
        Initialize metadata manager.
        
        Args:
            known_entities: Pre-known entities by type (optional)
        """
        self.known_entities = known_entities or defaultdict(set)
    
    def extract(self, text: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Extract metadata from text.
        
        Args:
            text: Text to analyze
            context: Additional context (location, time, etc.)
            
        Returns:
            Dictionary with extracted metadata
        """
        metadata = {
            "entities": [],
            "tags": [],
            "inferred_type": None,
            "importance": 0.5,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        
        # Extract entities
        entities = self._extract_entities(text)
        metadata["entities"] = [
            {"text": e.text, "type": e.entity_type, "confidence": e.confidence}
            for e in entities
        ]
        
        # Extract tags
        metadata["tags"] = self._extract_tags(text)
        
        # Infer memory type
        metadata["inferred_type"] = self._infer_type(text, entities)
        
        # Calculate importance
        metadata["importance"] = self._calculate_importance(text, entities, metadata["tags"])
        
        # Add context if provided
        if context:
            metadata["context"] = context
        
        return metadata
    
    def _extract_entities(self, text: str) -> List[Entity]:
        """Extract entities from text using patterns."""
        entities = []
        text_lower = text.lower()
        
        for entity_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    entity = Entity(
                        text=match.group(1) if match.groups() else match.group(),
                        entity_type=entity_type,
                        start=match.start(),
                        end=match.end()
                    )
                    entities.append(entity)
        
        # Check against known entities
        for entity_type, known in self.known_entities.items():
            for known_entity in known:
                if known_entity.lower() in text_lower:
                    entities.append(Entity(
                        text=known_entity,
                        entity_type=entity_type,
                        confidence=0.9
                    ))
        
        # Deduplicate
        seen = set()
        unique = []
        for e in entities:
            key = (e.text.lower(), e.entity_type)
            if key not in seen:
                seen.add(key)
                unique.append(e)
        
        return unique
    
    def _extract_tags(self, text: str) -> List[str]:
        """Extract tags based on keywords."""
        tags = []
        text_lower = text.lower()
        
        for tag, keywords in self.TAG_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    tags.append(tag)
                    break
        
        return list(set(tags))
    
    def _infer_type(self, text: str, entities: List[Entity]) -> Optional[str]:
        """Infer memory type from content."""
        text_lower = text.lower()
        
        scores = defaultdict(float)
        
        # Score based on keywords
        for memory_type, keywords in self.TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    scores[memory_type] += 1
        
        # Score based on entities
        entity_types = {e.entity_type for e in entities}
        if "location" in entity_types:
            scores[MemoryType.LOCATION] += 0.5
        if "person" in entity_types:
            scores[MemoryType.PERSON] += 0.5
        if "time" in entity_types or "date" in entity_types:
            scores[MemoryType.ROUTINE] += 0.3
        
        if not scores:
            return MemoryType.CONVERSATION.value
        
        best_type = max(scores, key=scores.get)
        return best_type.value
    
    def _calculate_importance(self, text: str, entities: List[Entity], tags: List[str]) -> float:
        """Calculate importance score (0-1)."""
        score = 0.5  # Base score
        
        # Increase for personal preferences
        if "preferenza" in tags:
            score += 0.2
        
        # Increase for tasks
        if "promemoria" in tags:
            score += 0.15
        
        # Increase for persons mentioned
        person_entities = [e for e in entities if e.entity_type == "person"]
        if person_entities:
            score += 0.1
        
        # Increase for specific times/dates
        time_entities = [e for e in entities if e.entity_type in ("time", "date")]
        if time_entities:
            score += 0.1
        
        # Decrease for short, generic content
        if len(text) < 20:
            score -= 0.1
        
        return max(0.1, min(1.0, score))
    
    def add_known_entity(self, entity_type: str, entity: str) -> None:
        """Add a known entity."""
        self.known_entities[entity_type].add(entity)
    
    def get_related_memories(
        self, 
        metadata: Dict[str, Any],
        all_memories: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Find related memories based on shared entities/tags.
        
        Args:
            metadata: Metadata of current memory
            all_memories: All stored memory metadata
            
        Returns:
            List of related memory metadata, sorted by relevance
        """
        current_entities = {e["text"].lower() for e in metadata.get("entities", [])}
        current_tags = set(metadata.get("tags", []))
        
        scored = []
        for mem in all_memories:
            score = 0
            
            # Score shared entities
            mem_entities = {e["text"].lower() for e in mem.get("entities", [])}
            shared_entities = current_entities & mem_entities
            score += len(shared_entities) * 2
            
            # Score shared tags
            mem_tags = set(mem.get("tags", []))
            shared_tags = current_tags & mem_tags
            score += len(shared_tags)
            
            if score > 0:
                scored.append((mem, score))
        
        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored]
