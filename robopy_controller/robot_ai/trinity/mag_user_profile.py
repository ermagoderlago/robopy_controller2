"""
User Profile Engine for MAG.
"""

from typing import Dict, Any
import threading
from robot_ai.utils import get_logger

logger = get_logger(__name__)

class UserProfileEngine:
    def __init__(self, db: Any):
        self._db = db
        self._lock = threading.RLock()
        self.categories = [
            "code_style",
            "language",
            "preferred_baudrate",
            "technical_level",
            "preferred_tools"
        ]

    def update_preference(self, user_name: str, key: str, value: str, source: str = "conversation") -> None:
        with self._lock:
            if hasattr(self._db, 'upsert_user_preference'):
                self._db.upsert_user_preference(user_name, key, value, source=source)
            elif hasattr(self._db, 'update_user_preference'):
                self._db.update_user_preference(user_name, key, value, source=source)
            else:
                logger.info(f"Updated preference for {user_name}: {key}={value} (source: {source})")

    def get_profile_summary(self, user_name: str) -> str:
        with self._lock:
            prefs = {}
            if hasattr(self._db, 'get_user_profile'):
                prefs = self._db.get_user_profile(user_name)
            elif hasattr(self._db, 'get_user_preferences'):
                prefs = self._db.get_user_preferences(user_name)
            
            if not prefs:
                return f"No profile data available for {user_name}."

            summary = f"USER PROFILE ({user_name}):\n"
            for k, v in prefs.items():
                summary += f"- {k}: {v}\n"
            return summary
