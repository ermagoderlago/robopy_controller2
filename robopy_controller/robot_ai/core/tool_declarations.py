"""
Dichiarazioni tool per Gemini function calling.
Devono corrispondere esattamente allo schema Gemini SDK.
Sprint 0 Hardening.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class ToolParameter:
    """Definizione parametro per i tool Gemini."""
    name: str
    type: str  # "string", "number", "boolean", "object", "array"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None


@dataclass
class ToolDefinition:
    """Definizione tool compatibile con Gemini SDK."""
    name: str
    description: str
    parameters: List[ToolParameter]
    
    def to_gemini_schema(self) -> Dict[str, Any]:
        """Converte nello schema API Gemini."""
        properties = {}
        required = []
        
        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            
            if param.required:
                required.append(param.name)
        
        schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        
        return schema


class ToolRegistry:
    """
    Registro centrale di tutti i tool disponibili per Gemini.
    
    Registra i tool di default (execute_skill, query_memory, get_ha_state)
    e permette di aggiungerne di personalizzati.
    """
    
    def __init__(self):
        """Initialize the tool registry."""
        self.tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """Registra i tool che Gemini può invocare."""
        
        # Tool 1: Esegui Skill
        self.register(ToolDefinition(
            name='execute_skill',
            description='Esegue una skill (azione) nel sistema robot. '
                        'Usa questo per controllare luci, navigation, ecc.',
            parameters=[
                ToolParameter(
                    name='skill_name',
                    type='string',
                    description='Nome della skill da eseguire',
                    required=True,
                ),
                ToolParameter(
                    name='parameters',
                    type='object',
                    description='Parametri per la skill',
                    required=True,
                ),
                ToolParameter(
                    name='timeout_seconds',
                    type='number',
                    description='Tempo massimo di esecuzione in secondi',
                    required=False,
                ),
            ]
        ))

        # Tool 2: Interroga Memoria
        self.register(ToolDefinition(
            name='query_memory',
            description='Cerca nella memoria del robot fatti e interazioni.',
            parameters=[
                ToolParameter(
                    name='query',
                    type='string',
                    description='Query di ricerca',
                    required=True,
                ),
                ToolParameter(
                    name='limit',
                    type='number',
                    description='Numero massimo risultati (default 5)',
                    required=False,
                ),
                ToolParameter(
                    name='type',
                    type='string',
                    description='Filtra per tipo di memoria',
                    enum=['episodic', 'semantic'],
                    required=False,
                ),
            ]
        ))

        # Tool 3: Stato Home Assistant
        self.register(ToolDefinition(
            name='get_ha_state',
            description='Interroga lo stato dei dispositivi Home Assistant.',
            parameters=[
                ToolParameter(
                    name='device_type',
                    type='string',
                    description='Tipo dispositivo (es. light, cover)',
                    required=True,
                ),
                ToolParameter(
                    name='location',
                    type='string',
                    description='Stanza/posizione (es. soggiorno)',
                    required=False,
                ),
            ]
        ))
    
    def register(self, tool: ToolDefinition) -> None:
        """Registra un tool."""
        if tool.name in self.tools:
            raise ValueError(f"Tool {tool.name} già registrato")
        self.tools[tool.name] = tool
    
    def get_tools_for_gemini(self) -> List[Dict[str, Any]]:
        """Esporta tutti i tool nel formato Gemini SDK."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.to_gemini_schema(),
            }
            for tool in self.tools.values()
        ]
    
    def validate_tool_call(self, tool_name: str, parameters: Dict[str, Any]) -> Optional[str]:
        """Valida i parametri di una chiamata tool. Restituisce errore se invalida."""
        tool = self.tools.get(tool_name)
        if not tool:
            return f"Tool sconosciuto: {tool_name}"
        
        # Controlla parametri obbligatori
        for param in tool.parameters:
            if param.required and param.name not in parameters:
                return f"Parametro obbligatorio mancante: {param.name}"
        
        # Valida tipi parametri (base)
        for param in tool.parameters:
            if param.name in parameters:
                value = parameters[param.name]
                if param.type == "string" and not isinstance(value, str):
                    return f"Parametro {param.name} deve essere stringa, ricevuto {type(value).__name__}"
                elif param.type == "number" and not isinstance(value, (int, float)):
                    return f"Parametro {param.name} deve essere numero, ricevuto {type(value).__name__}"
                
                # Controlla enum
                if param.enum and value not in param.enum:
                    return f"Parametro {param.name}: valore '{value}' non in {param.enum}"
        
        return None
    
    def get_tool_names(self) -> List[str]:
        """Restituisce lista nomi tool disponibili."""
        return list(self.tools.keys())
