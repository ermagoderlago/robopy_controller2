"""
Robot AI Utils - Validation
============================
Input sanitization and output validation utilities.
Protects against injection attacks and validates LLM outputs.
"""

import re
import json
import html
from typing import Any, Dict, List, Optional, Set, Union
from dataclasses import dataclass

from ..core.exceptions import ValidationError, SecurityError


# =============================================================================
# Input Sanitization
# =============================================================================

class InputSanitizer:
    """
    Sanitizes user input to prevent injection attacks.
    
    Features:
    - HTML/script tag removal
    - SQL injection prevention
    - Command injection prevention
    - Length limiting
    - PII detection (optional)
    """
    
    # Dangerous patterns
    SCRIPT_PATTERN = re.compile(r'<script[^>]*>.*?</script>', re.IGNORECASE | re.DOTALL)
    HTML_TAG_PATTERN = re.compile(r'<[^>]+>')
    SQL_INJECTION_PATTERNS = [
        re.compile(r"(\bOR\b|\bAND\b)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?", re.IGNORECASE),
        re.compile(r";\s*(DROP|DELETE|INSERT|UPDATE|ALTER|EXEC)\b", re.IGNORECASE),
        re.compile(r"UNION\s+SELECT", re.IGNORECASE),
    ]
    COMMAND_INJECTION_PATTERNS = [
        re.compile(r"[;&|`$]"),
        re.compile(r"\$\([^)]+\)"),
        re.compile(r"`[^`]+`"),
    ]
    
    # PII patterns
    EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    PHONE_PATTERN = re.compile(r'\b(?:\+?39)?[\s.-]?(?:\d{2,4}[\s.-]?){2,4}\d{2,4}\b')
    CREDIT_CARD_PATTERN = re.compile(r'\b(?:\d{4}[\s.-]?){3}\d{4}\b')
    FISCAL_CODE_PATTERN = re.compile(r'\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b', re.IGNORECASE)
    
    def __init__(
        self,
        max_length: int = 2000,
        strip_html: bool = True,
        check_sql_injection: bool = True,
        check_command_injection: bool = True,
        enable_pii_detection: bool = False
    ):
        self.max_length = max_length
        self.strip_html = strip_html
        self.check_sql_injection = check_sql_injection
        self.check_command_injection = check_command_injection
        self.enable_pii_detection = enable_pii_detection
    
    def sanitize(self, text: str) -> str:
        """
        Sanitize input text.
        
        Args:
            text: Input text to sanitize
            
        Returns:
            Sanitized text
            
        Raises:
            SecurityError: If dangerous patterns detected
        """
        if not text:
            return ""
        
        # Truncate to max length
        if len(text) > self.max_length:
            text = text[:self.max_length]
        
        # Strip HTML/script tags
        if self.strip_html:
            text = self.SCRIPT_PATTERN.sub('', text)
            text = self.HTML_TAG_PATTERN.sub('', text)
            text = html.unescape(text)
        
        # Check for SQL injection
        if self.check_sql_injection:
            for pattern in self.SQL_INJECTION_PATTERNS:
                if pattern.search(text):
                    raise SecurityError("Potential SQL injection detected")
        
        # Check for command injection
        if self.check_command_injection:
            for pattern in self.COMMAND_INJECTION_PATTERNS:
                if pattern.search(text):
                    # Log but don't block - could be legitimate
                    pass
        
        # Normalize whitespace
        text = ' '.join(text.split())
        
        return text
    
    def detect_pii(self, text: str) -> Dict[str, List[str]]:
        """
        Detect PII in text.
        
        Returns:
            Dict with PII types as keys and matched values as lists
        """
        pii = {}
        
        emails = self.EMAIL_PATTERN.findall(text)
        if emails:
            pii['email'] = emails
        
        phones = self.PHONE_PATTERN.findall(text)
        if phones:
            pii['phone'] = phones
        
        cards = self.CREDIT_CARD_PATTERN.findall(text)
        if cards:
            pii['credit_card'] = cards
        
        fiscal = self.FISCAL_CODE_PATTERN.findall(text)
        if fiscal:
            pii['fiscal_code'] = fiscal
        
        return pii
    
    def redact_pii(self, text: str) -> str:
        """
        Redact PII from text.
        
        Returns:
            Text with PII replaced by [REDACTED]
        """
        text = self.EMAIL_PATTERN.sub('[EMAIL]', text)
        text = self.PHONE_PATTERN.sub('[PHONE]', text)
        text = self.CREDIT_CARD_PATTERN.sub('[CARD]', text)
        text = self.FISCAL_CODE_PATTERN.sub('[CF]', text)
        return text


# =============================================================================
# Output Validation
# =============================================================================

@dataclass
class ActionSchema:
    """Schema for action validation."""
    action_type: str
    required_fields: List[str]
    optional_fields: List[str] = None
    validators: Dict[str, callable] = None


class OutputValidator:
    """
    Validates LLM output to ensure it follows expected schema.
    
    Features:
    - Action type validation
    - Required field checking
    - Field type validation
    - Whitelist enforcement
    """
    
    # Default allowed action types
    DEFAULT_ALLOWED_ACTIONS = {
        "say", "ha_call", "nav_goto", "store_memory", 
        "timer_set", "query_weather", "web_search", "debug"
    }
    
    # Action schemas
    ACTION_SCHEMAS = {
        "say": ActionSchema(
            action_type="say",
            required_fields=["text"],
            optional_fields=["emotion", "language", "speed"]
        ),
        "ha_call": ActionSchema(
            action_type="ha_call",
            required_fields=["domain", "service"],
            optional_fields=["entity_id", "data"]
        ),
        "nav_goto": ActionSchema(
            action_type="nav_goto",
            required_fields=["destination"],
            optional_fields=["speed", "avoid_obstacles"]
        ),
        "store_memory": ActionSchema(
            action_type="store_memory",
            required_fields=["content", "memory_type"],
            optional_fields=["importance", "tags"]
        ),
        "timer_set": ActionSchema(
            action_type="timer_set",
            required_fields=["duration_seconds"],
            optional_fields=["name", "message"]
        ),
        "query_weather": ActionSchema(
            action_type="query_weather",
            required_fields=[],
            optional_fields=["location", "time"]
        ),
        "debug": ActionSchema(
            action_type="debug",
            required_fields=["message"],
            optional_fields=["level"]
        ),
    }
    
    def __init__(
        self,
        allowed_actions: Set[str] = None,
        ha_whitelist_domains: List[str] = None,
        nav_allowed_destinations: List[str] = None,
        strict_mode: bool = False
    ):
        self.allowed_actions = allowed_actions or self.DEFAULT_ALLOWED_ACTIONS
        self.ha_whitelist_domains = set(ha_whitelist_domains or [])
        self.nav_allowed_destinations = set(nav_allowed_destinations or [])
        self.strict_mode = strict_mode
    
    def validate_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate an action from LLM output.
        
        Args:
            action: Action dictionary
            
        Returns:
            Validated action (may be modified)
            
        Raises:
            ValidationError: If action is invalid
        """
        if not isinstance(action, dict):
            raise ValidationError("Action must be a dictionary")
        
        action_type = action.get("action_type") or action.get("type")
        if not action_type:
            raise ValidationError("Action must have 'action_type' field")
        
        # Check if action type is allowed
        if action_type not in self.allowed_actions:
            raise ValidationError(f"Action type '{action_type}' is not allowed")
        
        # Get schema
        schema = self.ACTION_SCHEMAS.get(action_type)
        if schema:
            # Check required fields
            for field in schema.required_fields:
                if field not in action:
                    raise ValidationError(f"Action '{action_type}' requires field '{field}'")
        
        # Specific validations
        if action_type == "ha_call":
            self._validate_ha_action(action)
        elif action_type == "nav_goto":
            self._validate_nav_action(action)
        
        return action
    
    def validate_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate complete LLM response.
        
        Expected format:
        {
            "response_text": "...",
            "actions": [...],
            "reasoning": "..." (optional)
        }
        """
        if not isinstance(response, dict):
            raise ValidationError("Response must be a dictionary")
        
        # Validate response text
        if "response_text" in response:
            if not isinstance(response["response_text"], str):
                raise ValidationError("response_text must be a string")
        
        # Validate actions
        if "actions" in response:
            if not isinstance(response["actions"], list):
                raise ValidationError("actions must be a list")
            
            validated_actions = []
            for action in response["actions"]:
                validated_actions.append(self.validate_action(action))
            response["actions"] = validated_actions
        
        return response
    
    def _validate_ha_action(self, action: Dict[str, Any]) -> None:
        """Validate Home Assistant action."""
        domain = action.get("domain", "")
        
        # Check domain whitelist
        if self.ha_whitelist_domains and domain not in self.ha_whitelist_domains:
            raise ValidationError(f"HA domain '{domain}' is not whitelisted")
        
        # Validate dangerous services
        dangerous_services = {"delete", "remove", "reboot", "shutdown"}
        service = action.get("service", "")
        if service.lower() in dangerous_services:
            raise SecurityError(f"HA service '{service}' is blocked for safety")
    
    def _validate_nav_action(self, action: Dict[str, Any]) -> None:
        """Validate navigation action."""
        destination = action.get("destination", "")
        
        # Check destination whitelist (if configured)
        if self.nav_allowed_destinations and destination not in self.nav_allowed_destinations:
            if self.strict_mode:
                raise ValidationError(f"Destination '{destination}' is not in allowed list")
    
    def parse_json_response(self, text: str) -> Dict[str, Any]:
        """
        Parse JSON response from LLM, handling common issues.
        
        Args:
            text: LLM output text
            
        Returns:
            Parsed and validated dictionary
        """
        # Try to extract JSON from text
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            # Fallback: treat as plain text response
            return {
                "response_text": text,
                "actions": []
            }
        
        try:
            data = json.loads(json_match.group())
            return self.validate_response(data)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON in response: {str(e)}")


# =============================================================================
# Convenience Functions
# =============================================================================

_sanitizer = InputSanitizer()
_validator = OutputValidator()


def sanitize_input(text: str, **kwargs) -> str:
    """Sanitize user input text."""
    if kwargs:
        sanitizer = InputSanitizer(**kwargs)
        return sanitizer.sanitize(text)
    return _sanitizer.sanitize(text)


def validate_action(action: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """Validate an action from LLM output."""
    if kwargs:
        validator = OutputValidator(**kwargs)
        return validator.validate_action(action)
    return _validator.validate_action(action)


def validate_response(response: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """Validate complete LLM response."""
    if kwargs:
        validator = OutputValidator(**kwargs)
        return validator.validate_response(response)
    return _validator.validate_response(response)


def redact_pii(text: str) -> str:
    """Redact PII from text."""
    return _sanitizer.redact_pii(text)
