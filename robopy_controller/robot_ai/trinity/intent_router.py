import re
from enum import Enum, auto
from dataclasses import dataclass
from robot_ai.utils import get_logger

logger = get_logger(__name__)

class IntentCategory(Enum):
    CODING = auto()
    NAVIGATION = auto()
    CONVERSATION = auto()
    SMART_HOME = auto()
    DIAGNOSTIC = auto()
    MEMORY = auto()
    GENERAL = auto()

@dataclass
class RetrievalConfig:
    rag_enabled: bool = True
    rag_knowledge_enabled: float = 0.5
    cag_hardware: float = 0.5
    cag_ros: float = 0.5
    cag_error: float = 0.5
    cag_environment: float = 0.5
    cag_ha: float = 0.5
    mag_episodic: float = 0.5
    mag_facts: float = 0.5
    mag_profile: float = 0.5

class IntentRouter:
    """Classifies user queries to optimize retrieval routing."""
    
    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)
        # Lightweight keyword/regex matching patterns (no ML needed)
        self.patterns = {
            IntentCategory.CODING: re.compile(r'\b(code|python|debug|error|exception|script|function|class)\b', re.IGNORECASE),
            IntentCategory.NAVIGATION: re.compile(r'\b(go to|move|navigate|room|kitchen|living room|map|location|where is)\b', re.IGNORECASE),
            IntentCategory.CONVERSATION: re.compile(r'\b(hello|hi|how are you|feel|feeling|joke|chat)\b', re.IGNORECASE),
            IntentCategory.SMART_HOME: re.compile(r'\b(light|lights|turn on|turn off|temperature|thermostat|device|smart plug)\b', re.IGNORECASE),
            IntentCategory.DIAGNOSTIC: re.compile(r'\b(system status|battery|health|broken|failed|cpu|ram|logs|diagnostic)\b', re.IGNORECASE),
            IntentCategory.MEMORY: re.compile(r'\b(remember|recall|past|yesterday|last time|did you|have you)\b', re.IGNORECASE),
        }

    def classify(self, text: str) -> IntentCategory:
        """Classify a given text into an IntentCategory."""
        if not text:
            return IntentCategory.GENERAL
            
        for category, pattern in self.patterns.items():
            if pattern.search(text):
                self.logger.debug(f"Classified '{text}' as {category.name}")
                return category
                
        self.logger.debug(f"Could not explicitly classify '{text}', defaulting to GENERAL")
        return IntentCategory.GENERAL

    def get_retrieval_config(self, category: IntentCategory) -> RetrievalConfig:
        """Return which modules to query and with what priority/budget."""
        config = RetrievalConfig()
        
        if category == IntentCategory.CODING:
            config.rag_knowledge_enabled = 1.0
            config.cag_error = 1.0
            config.cag_environment = 0.2
            config.mag_episodic = 0.2
        elif category == IntentCategory.NAVIGATION:
            config.cag_environment = 1.0
            config.mag_episodic = 0.8
            config.rag_knowledge_enabled = 0.2
        elif category == IntentCategory.CONVERSATION:
            config.mag_profile = 1.0
            config.mag_episodic = 0.8
            config.rag_knowledge_enabled = 0.5
        elif category == IntentCategory.SMART_HOME:
            config.cag_ha = 1.0
            config.mag_profile = 0.8
            config.rag_knowledge_enabled = 0.2
        elif category == IntentCategory.DIAGNOSTIC:
            config.cag_hardware = 1.0
            config.cag_ros = 1.0
            config.cag_error = 1.0
            config.rag_knowledge_enabled = 0.5
        elif category == IntentCategory.MEMORY:
            config.mag_episodic = 1.0
            config.mag_facts = 1.0
            config.rag_knowledge_enabled = 0.2
        elif category == IntentCategory.GENERAL:
            # Keep defaults for balanced retrieval
            pass
            
        return config
