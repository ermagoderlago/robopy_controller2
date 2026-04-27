import re
from typing import Optional

class InputSanitizer:
    """
    Sanitizes and validates user input for the AI system.
    """
    
    def __init__(self):
        pass

    def sanitize(self, text: Optional[str]) -> str:
        """
        Clean input text by removing excessive whitespace and common noise.
        
        Args:
            text: Raw input string
            
        Returns:
            Sanitized string
        """
        if not text:
            return ""
            
        # Basic cleanup
        clean_text = text.strip()
        
        # Remove multiple spaces
        clean_text = re.sub(r'\s+', ' ', clean_text)
        
        return clean_text

    def validate_command(self, text: str) -> bool:
        """
        Check if command is valid/safe to process.

        Args:
            text: Input command
            
        Returns:
            True if valid
        """
        if not text:
            return False
            
        # Basic length check
        if len(text) < 2:
            return False
            
        return True
