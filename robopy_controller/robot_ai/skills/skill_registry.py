"""
Robot AI Skills - Skill Registry
==================================
Plugin system for skill management with hot-reload support.
"""

import os
import re
import importlib
import importlib.util
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from .base_skill import BaseSkill, SkillMetadata, SkillResult
from ..core.event_bus import EventBus, EventType
from ..utils.logging_utils import get_logger


class SkillRegistry:
    """
    Registry for managing and discovering skills.
    
    Features:
    - Automatic skill discovery from directories
    - Hot-reload when skill files change
    - Priority-based skill matching
    - Statistics aggregation
    
    Usage:
        registry = SkillRegistry()
        registry.discover("/path/to/skills")
        
        # Find matching skill
        skill = registry.find_best_match("accendi la luce")
        
        # Execute
        result = await skill.safe_execute("accendi la luce", context)
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self.logger = get_logger("skill_registry")
        self.event_bus = EventBus()
        
        self._skills: Dict[str, BaseSkill] = {}
        self._skill_paths: Dict[str, Path] = {}  # skill_name -> file path
        self._skill_mtimes: Dict[str, float] = {}  # path -> mtime
        
        self._watch_thread: Optional[threading.Thread] = None
        self._watching = False
        self._watch_interval = 5.0
        
        self._initialized = True
    
    def register(self, skill: BaseSkill) -> bool:
        """
        Register a skill instance.
        
        Args:
            skill: Skill instance to register
            
        Returns:
            True if registered successfully
        """
        with self._lock:
            name = skill.name
            
            if name in self._skills:
                self.logger.warning(f"Skill '{name}' already registered, replacing")
            
            self._skills[name] = skill
            
            self.event_bus.publish(EventType.SKILL_REGISTERED, {
                "name": name,
                "description": skill.description
            })
            
            self.logger.info(f"Registered skill: {name}")
            return True
    
    def unregister(self, name: str) -> bool:
        """
        Unregister a skill by name.
        
        Args:
            name: Skill name
            
        Returns:
            True if unregistered successfully
        """
        with self._lock:
            if name not in self._skills:
                return False
            
            del self._skills[name]
            self._skill_paths.pop(name, None)
            
            self.event_bus.publish(EventType.SKILL_UNREGISTERED, {"name": name})
            self.logger.info(f"Unregistered skill: {name}")
            return True
    
    def get(self, name: str) -> Optional[BaseSkill]:
        """Get a skill by name."""
        return self._skills.get(name)
    
    def get_all(self, enabled_only: bool = True) -> List[BaseSkill]:
        """Get all registered skills."""
        skills = list(self._skills.values())
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        return skills
    
    def discover(self, directory: str, recursive: bool = True) -> int:
        """
        Discover and load skills from a directory.
        
        Args:
            directory: Path to skills directory
            recursive: Whether to search subdirectories
            
        Returns:
            Number of skills discovered
        """
        skills_dir = Path(directory)
        if not skills_dir.exists():
            self.logger.warning(f"Skills directory not found: {directory}")
            return 0
        
        count = 0
        pattern = "*.py"
        
        if recursive:
            files = skills_dir.rglob(pattern)
        else:
            files = skills_dir.glob(pattern)
        
        for file_path in files:
            if file_path.name.startswith("_"):
                continue
            if file_path.name == "base_skill.py":
                continue
            
            try:
                loaded = self._load_skill_file(file_path)
                count += loaded
            except Exception as e:
                self.logger.error(f"Failed to load skill from {file_path}: {e}")
        
        self.logger.info(f"Discovered {count} skills from {directory}")
        return count
    
    def _load_skill_file(self, file_path: Path) -> int:
        """
        Load skills from a Python file.
        
        Returns:
            Number of skills loaded
        """
        module_name = f"robot_ai_skill_{file_path.stem}"
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                return 0
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find skill classes
            count = 0
            for name in dir(module):
                obj = getattr(module, name)
                
                if (isinstance(obj, type) and 
                    issubclass(obj, BaseSkill) and 
                    obj is not BaseSkill):
                    
                    # Instantiate and register
                    try:
                        skill = obj()
                        self.register(skill)
                        self._skill_paths[skill.name] = file_path
                        self._skill_mtimes[str(file_path)] = file_path.stat().st_mtime
                        count += 1
                    except Exception as e:
                        self.logger.error(f"Failed to instantiate {name}: {e}")
            
            return count
            
        except Exception as e:
            self.logger.error(f"Failed to load module {file_path}: {e}")
            return 0
    
    def find_best_match(
        self,
        text: str,
        context: Dict[str, Any] = None,
        min_confidence: float = 0.3
    ) -> Optional[BaseSkill]:
        """
        Find the best matching skill for input text.
        
        Args:
            text: User input text
            context: Additional context
            min_confidence: Minimum match confidence
            
        Returns:
            Best matching skill or None
        """
        matches = self.find_matches(text, context, min_confidence)
        return matches[0][0] if matches else None
    
    def find_matches(
        self,
        text: str,
        context: Dict[str, Any] = None,
        min_confidence: float = 0.1
    ) -> List[tuple]:
        """
        Find all matching skills with confidence scores.
        
        Args:
            text: User input text
            context: Additional context
            min_confidence: Minimum match confidence
            
        Returns:
            List of (skill, confidence) tuples, sorted by confidence
        """
        matches = []
        
        for skill in self.get_all(enabled_only=True):
            try:
                confidence = skill.match(text, context or {})
                
                if confidence >= min_confidence:
                    # Apply priority boost
                    adjusted = confidence + (skill.priority * 0.01)
                    matches.append((skill, adjusted))
                    
            except Exception as e:
                self.logger.error(f"Error matching skill {skill.name}: {e}")
        
        # Sort by confidence (descending)
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches
    
    async def execute(
        self,
        text: str,
        context: Dict[str, Any] = None,
        skill_name: str = None
    ) -> Optional[SkillResult]:
        """
        Execute a skill matching the input.
        
        Args:
            text: User input text
            context: Additional context
            skill_name: Specific skill to execute (optional)
            
        Returns:
            SkillResult or None if no match
        """
        if skill_name:
            skill = self.get(skill_name)
            if not skill:
                return None
        else:
            skill = self.find_best_match(text, context)
            if not skill:
                return None
        
        # Publish event
        self.event_bus.publish(EventType.SKILL_STARTED, {
            "name": skill.name,
            "text": text[:100]
        })
        
        # Execute
        result = await skill.safe_execute(text, context or {})
        
        # Publish completion
        event_type = EventType.SKILL_COMPLETED if result.success else EventType.SKILL_FAILED
        self.event_bus.publish(event_type, {
            "name": skill.name,
            "success": result.success,
            "duration_ms": result.duration_ms
        })
        
        return result
    
    def start_watching(self, interval: float = 5.0) -> None:
        """Start watching for skill file changes (hot-reload)."""
        if self._watching:
            return
        
        self._watching = True
        self._watch_interval = interval
        
        def watch_loop():
            while self._watching:
                self._check_for_changes()
                time.sleep(self._watch_interval)
        
        self._watch_thread = threading.Thread(target=watch_loop, daemon=True)
        self._watch_thread.start()
        self.logger.info("Started watching for skill changes")
    
    def stop_watching(self) -> None:
        """Stop watching for skill file changes."""
        self._watching = False
        if self._watch_thread:
            self._watch_thread.join(timeout=1.0)
    
    def _check_for_changes(self) -> None:
        """Check for modified skill files and reload."""
        for skill_name, file_path in list(self._skill_paths.items()):
            if not file_path.exists():
                # File deleted, unregister skill
                self.unregister(skill_name)
                continue
            
            current_mtime = file_path.stat().st_mtime
            path_key = str(file_path)
            
            if path_key in self._skill_mtimes:
                if current_mtime > self._skill_mtimes[path_key]:
                    # File modified, reload
                    self.logger.info(f"Reloading modified skill: {skill_name}")
                    self.unregister(skill_name)
                    self._load_skill_file(file_path)
    
    def reload(self, skill_name: str) -> bool:
        """
        Reload a specific skill.
        
        Args:
            skill_name: Name of skill to reload
            
        Returns:
            True if reloaded successfully
        """
        if skill_name not in self._skill_paths:
            return False
        
        file_path = self._skill_paths[skill_name]
        self.unregister(skill_name)
        return self._load_skill_file(file_path) > 0
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get aggregated statistics for all skills."""
        stats = {
            "total_skills": len(self._skills),
            "enabled_skills": len(self.get_all(enabled_only=True)),
            "skills": {}
        }
        
        for skill in self._skills.values():
            stats["skills"][skill.name] = skill.get_statistics()
        
        return stats
    
    def get_function_declarations(self) -> List[Dict[str, Any]]:
        """Get all skills as LLM function declarations."""
        return [skill.to_function_declaration() for skill in self.get_all()]

    def get_summary(self) -> str:
        """Get a text summary of all registered skills for LLM context."""
        skills = self.get_all(enabled_only=True)
        summary = "## Abilità Disponibili (Skills)\n"
        for skill in skills:
            md = skill.get_metadata()
            summary += f"- **{md.name}**: {md.description} (Keywords: {', '.join(md.keywords)})\n"
        return summary
