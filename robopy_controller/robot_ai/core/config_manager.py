"""
Robot AI Core - Configuration Manager
======================================
Configuration management with Pydantic and environment variables.
Supports hot-reload and profile switching.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
import yaml
import threading
import time

from .exceptions import ConfigurationError


# =============================================================================
# Configuration Models (Pydantic)
# =============================================================================

class SecretsConfig(BaseModel):
    """Secrets configuration - loaded from environment."""
    gemini_api_key: str = Field(default="", description="Gemini API key")
    google_tts_key: str = Field(default="", description="Google TTS API key")
    google_asr_key: str = Field(default="", description="Google ASR API key")
    ha_token: str = Field(default="", description="Home Assistant token")
    weather_api_key: str = Field(default="", description="Weather API key")
    deepseek_api_key: str = Field(default="", description="DeepSeek API key")


class RobotConfig(BaseModel):
    """Robot identity configuration."""
    name: str = "MARCUS"
    full_name: str = "Modular Autonomous Robotic Control Unit System"
    creator: str = "Luca Suffia"
    model: str = "NVIDIA PC"
    version: str = "1.0.0"


class PersonalizationConfig(BaseModel):
    """Personalization configuration."""
    user_name: str = "Luca"
    tone: str = "informale"
    communication_style: str = "concise"
    preferred_language: str = "it-IT"
    greet_on_ready: bool = True
    proactive_suggestions: bool = True


class PersonalityConfig(BaseModel):
    """Personality configuration."""
    traits: List[str] = ["helpful", "polite", "proactive", "adaptable"]
    humor_level: float = 0.3
    formality_level: float = 0.4
    proactiveness_level: float = 0.7


class LLMConfig(BaseModel):
    """LLM configuration."""
    provider: str = "gemini"
    model: str = "gemini-2.0-flash"
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 0.95
    top_k: int = 40
    enable_grounding: bool = False
    enable_function_calling: bool = True
    max_context_length: int = 128000
    system_prompt_cache: bool = True
    two_stage_reasoning: bool = True
    timeout: float = 25.0


class TTSConfig(BaseModel):
    """TTS configuration."""
    enabled: bool = True
    provider: str = "google"
    language: str = "it-IT"
    voice: str = "it-IT-Standard-B"
    speaking_rate: float = 1.0
    pitch: float = 0.0
    volume_gain_db: float = 0.0
    enable_ssml: bool = True
    enable_emotion: bool = True


class ASRConfig(BaseModel):
    """ASR configuration."""
    enabled: bool = True
    provider: str = "google"
    language: str = "it-IT"
    enable_vad: bool = True
    vad_threshold: float = 0.5
    enable_streaming: bool = True
    streaming_chunk_ms: int = 100
    max_alternatives: int = 3
    silence_duration: float = 0.8


class AudioConfig(BaseModel):
    """Audio configuration."""
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration: float = 0.1
    vad_threshold: float = 0.3
    silence_duration: float = 0.5
    input_device: Optional[str] = None
    output_device: Optional[str] = None


class VisionConfig(BaseModel):
    """Vision configuration."""
    enabled: bool = True
    frame_rate: float = 1.0
    resolution: List[int] = [320, 240]
    enable_object_detection: bool = True
    enable_face_recognition: bool = True
    enable_video_analysis: bool = True
    max_frames: int = 5
    jpeg_quality: int = 85


class FaceRecognitionConfig(BaseModel):
    """Face recognition configuration."""
    enabled: bool = False
    known_faces_dir: str = "/home/robopy/severus/known_faces"
    confidence_high: float = 0.80
    confidence_low: float = 0.60
    tolerance: float = 0.5
    recognition_interval: float = 10.0  # seconds between recognition attempts


class VisualMemoryConfig(BaseModel):
    """Visual Memory configuration."""
    enabled: bool = True
    update_frequency: float = 0.1  # Hz (10s interval for API limits)
    min_motion_threshold: float = 0.1  # Linear velocity m/s
    min_angular_threshold: float = 0.2  # Angular velocity rad/s
    confidence_threshold: float = 0.6
    startup_analysis: bool = True


class MemoryConfig(BaseModel):
    """Memory/RAG configuration."""
    provider: str = "chromadb"
    persist_dir: str = "/home/robopy/severus/ChromaDB"
    collection_name: str = "robot_memories"
    embedding_model: str = "gemini"
    embedding_dimension: int = 3072  # gemini-embedding-001 output dimension
    use_float16: bool = True
    use_hnsw: bool = True
    hnsw_ef_construction: int = 200
    hnsw_m: int = 16
    auto_consolidation: bool = True
    consolidation_interval_hours: int = 168  # Weekly
    max_memories: int = 10000
    temporal_decay_rate: float = 0.1


class RAGConfig(BaseModel):
    """RAG retrieval configuration."""
    enabled: bool = True  # Uses google-genai SDK with gemini-embedding-001
    strategy: Literal["semantic", "hybrid", "temporal"] = "hybrid"
    top_k: int = 5
    min_score: float = 0.15
    semantic_weight: float = 0.7
    temporal_weight: float = 0.2
    metadata_weight: float = 0.1
    enable_metadata_filtering: bool = True


class HomeAssistantConfig(BaseModel):
    """Home Assistant configuration."""
    enabled: bool = True
    url: str = "ws://192.168.1.45:8123/api/websocket"
    token: str = ""
    whitelist_domains: List[str] = ["light", "switch", "sensor", "climate", "media_player"]
    whitelist_entities: List[str] = []
    discovery_interval: int = 3600
    request_timeout: float = 5.0
    auto_discovery: bool = True


class NavigationConfig(BaseModel):
    """Navigation configuration."""
    enabled: bool = True
    provider: str = "nav2"
    waypoints_file: str = ""
    use_vision_correction: bool = True
    use_social_layers: bool = True
    social_distance: float = 1.5
    recovery_attempts: int = 3


class PerformanceConfig(BaseModel):
    """Performance configuration for Pi 5."""
    max_io_workers: int = 4
    max_cpu_workers: int = 2
    embedding_cache_size: int = 256
    system_prompt_cache: bool = True
    use_float16_embeddings: bool = True
    use_hnsw: bool = True
    enable_compression: bool = True
    batch_embedding_size: int = 10


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration."""
    llm_failure_threshold: int = 3
    tts_failure_threshold: int = 3
    asr_failure_threshold: int = 3
    ha_failure_threshold: int = 5
    recovery_timeout: int = 30
    half_open_max_attempts: int = 1


class SecurityConfig(BaseModel):
    """Security configuration."""
    enable_pii_redaction: bool = True
    enable_input_sanitization: bool = True
    enable_output_validation: bool = True
    allowed_action_types: List[str] = ["say", "ha_call", "nav_goto", "store_memory", "debug"]
    max_input_length: int = 2000


class PluginsConfig(BaseModel):
    """Plugins configuration."""
    enabled: bool = True
    skills_dir: str = ""
    auto_discover: bool = True
    hot_reload: bool = True
    skill_timeout: float = 10.0


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    format: str = "json"
    log_dir: str = "/home/robopy/severus/log"
    max_size_mb: int = 100
    backup_count: int = 5
    enable_ros_logging: bool = True


class MonitoringConfig(BaseModel):
    """Monitoring configuration."""
    enable_metrics: bool = True
    metrics_publish_rate: float = 1.0
    enable_health_checks: bool = True
    health_check_interval: int = 5
    enable_profiling: bool = False


class DebugConfig(BaseModel):
    """Debug configuration."""
    enabled: bool = False
    artifacts_dir: str = "/home/robopy/severus/tmp/robot_ai_debug"
    save_audio: bool = True
    save_images: bool = True
    save_prompts: bool = True
    debug_session_duration: int = 60


class EnvironmentConfig(BaseModel):
    """Environment configuration."""
    timezone: str = "Europe/Rome"
    location: str = "home"
    weather_provider: str = "openweathermap"
    enable_ambient_awareness: bool = True


class DeepSeekConfig(BaseModel):
    """DeepSeek LLM configuration for nightly collaborative analysis."""
    enabled: bool = True
    model: str = "deepseek-chat"
    temperature: float = 0.7
    max_tokens: int = 8192
    timeout: int = 60


class AIConfig(BaseModel):
    """Main AI configuration - aggregates all sub-configs."""
    
    # Profile
    profile: Literal["performance", "battery_saver", "privacy_mode"] = "performance"
    
    # Components
    robot: RobotConfig = RobotConfig()
    personalization: PersonalizationConfig = PersonalizationConfig()
    personality: PersonalityConfig = PersonalityConfig()
    llm: LLMConfig = LLMConfig()
    tts: TTSConfig = TTSConfig()
    asr: ASRConfig = ASRConfig()
    audio: AudioConfig = AudioConfig()
    vision: VisionConfig = VisionConfig()
    visual_memory: VisualMemoryConfig = VisualMemoryConfig()
    face_recognition: FaceRecognitionConfig = FaceRecognitionConfig()
    memory: MemoryConfig = MemoryConfig()
    rag: RAGConfig = RAGConfig()
    home_assistant: HomeAssistantConfig = HomeAssistantConfig()
    navigation: NavigationConfig = NavigationConfig()
    performance: PerformanceConfig = PerformanceConfig()
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()
    security: SecurityConfig = SecurityConfig()
    plugins: PluginsConfig = PluginsConfig()
    logging: LoggingConfig = LoggingConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    debug: DebugConfig = DebugConfig()
    environment: EnvironmentConfig = EnvironmentConfig()
    deepseek: DeepSeekConfig = DeepSeekConfig()
    
    # Secrets (loaded from environment)
    secrets: SecretsConfig = SecretsConfig()


# =============================================================================
# Configuration Manager
# =============================================================================

class ConfigManager:
    """
    Configuration manager with hot reload support.
    
    Features:
    - Load from YAML file
    - Override from environment variables
    - Profile switching (performance, battery_saver, privacy_mode)
    - Hot-reload on file change
    - Validation with Pydantic
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, config_path: str = None, profile: str = "performance"):
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        self._config_path = Path(config_path) if config_path else None
        self._profile = profile
        self._config: Optional[AIConfig] = None
        self._last_modified = 0.0
        self._watch_thread = None
        self._watching = False
        self._callbacks = []
        self._initialized = True
    
    def load(self) -> AIConfig:
        """Load configuration from file and environment."""
        try:
            config_dict = {}
            
            # Load from YAML if file exists
            if self._config_path and self._config_path.exists():
                with open(self._config_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f) or {}
                
                # Apply profile if specified
                if self._profile and 'profiles' in yaml_config:
                    profile_config = yaml_config.get('profiles', {}).get(self._profile, {})
                    # Deep merge profile into base config
                    yaml_config = self._deep_merge(yaml_config, profile_config)
                    yaml_config.pop('profiles', None)
                
                config_dict = yaml_config
                self._last_modified = self._config_path.stat().st_mtime
            
            # Load secrets from environment
            secrets = SecretsConfig(
                gemini_api_key=os.getenv('GEMINI_API_KEY', ''),
                google_tts_key=os.getenv('GOOGLE_TTS_KEY', ''),
                google_asr_key=os.getenv('GOOGLE_ASR_KEY', ''),
                ha_token=os.getenv('HA_TOKEN', ''),
                weather_api_key=os.getenv('WEATHER_API_KEY', ''),
                deepseek_api_key=os.getenv('DEEPSEEK_API_KEY', ''),
            )
            config_dict['secrets'] = secrets.model_dump()
            
            # Create config with defaults and overrides
            self._config = AIConfig(**config_dict)
            
            return self._config
            
        except Exception as e:
            raise ConfigurationError(f"Failed to load config: {str(e)}")
    
    def get_config(self) -> AIConfig:
        """Get current config, loading if necessary."""
        if self._config is None:
            self.load()
        return self._config
    
    def reload(self) -> AIConfig:
        """Force reload configuration."""
        return self.load()
    
    def check_for_updates(self) -> bool:
        """Check if config file has been modified."""
        if not self._config_path or not self._config_path.exists():
            return False
        
        current_modified = self._config_path.stat().st_mtime
        return current_modified > self._last_modified
    
    def reload_if_changed(self) -> Optional[AIConfig]:
        """Reload config if file has changed."""
        if self.check_for_updates():
            config = self.load()
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(config)
                except Exception:
                    pass
            return config
        return None
    
    def update_config(self, updates: Dict[str, Any]) -> AIConfig:
        """Update configuration dynamically."""
        if self._config is None:
            self.load()
        
        config_dict = self._config.model_dump()
        config_dict = self._deep_merge(config_dict, updates)
        self._config = AIConfig(**config_dict)
        
        return self._config
    
    def set_profile(self, profile: str) -> AIConfig:
        """Switch configuration profile."""
        self._profile = profile
        return self.load()
    
    def register_callback(self, callback) -> None:
        """Register callback for config changes."""
        self._callbacks.append(callback)
    
    def start_watching(self, interval: float = 5.0) -> None:
        """Start watching config file for changes."""
        if self._watching:
            return
        
        self._watching = True
        
        def watch_loop():
            while self._watching:
                self.reload_if_changed()
                time.sleep(interval)
        
        self._watch_thread = threading.Thread(target=watch_loop, daemon=True)
        self._watch_thread.start()
    
    def stop_watching(self) -> None:
        """Stop watching config file."""
        self._watching = False
        if self._watch_thread:
            self._watch_thread.join(timeout=1.0)
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of warnings."""
        warnings = []
        config = self.get_config()
        
        # Check required API keys
        if not config.secrets.gemini_api_key:
            warnings.append("Gemini API key is not set (env: GEMINI_API_KEY)")
        
        if config.tts.provider == "google" and not config.secrets.google_tts_key:
            warnings.append("Google TTS API key is not set (env: GOOGLE_TTS_KEY)")
        
        if config.asr.provider == "google" and not config.secrets.google_asr_key:
            warnings.append("Google ASR API key is not set (env: GOOGLE_ASR_KEY)")
        
        if config.home_assistant.enabled and not config.secrets.ha_token:
            warnings.append("Home Assistant token is not set (env: HA_TOKEN)")
        
        if config.deepseek.enabled and not config.secrets.deepseek_api_key:
            warnings.append("DeepSeek API key is not set (env: DEEPSEEK_API_KEY)")
        
        # Check directories
        memory_dir = Path(config.memory.persist_dir)
        if not memory_dir.parent.exists():
            warnings.append(f"Memory persist directory parent does not exist: {memory_dir}")
        
        # Performance warnings for Pi 5
        if config.performance.max_io_workers > 8:
            warnings.append("max_io_workers > 8 may cause high context switching on Pi 5")
        
        if config.performance.max_cpu_workers > 4:
            warnings.append("max_cpu_workers > 4 exceeds Pi 5 core count")
        
        return warnings
    
    def _deep_merge(self, base: dict, override: dict) -> dict:
        """Deep merge two dictionaries."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def to_yaml(self) -> str:
        """Export current config to YAML string."""
        config = self.get_config()
        config_dict = config.model_dump()
        # Remove secrets from export
        config_dict.pop('secrets', None)
        return yaml.dump(config_dict, default_flow_style=False, allow_unicode=True)
    
    def save(self, path: str = None) -> None:
        """Save current config to file."""
        save_path = Path(path) if path else self._config_path
        if not save_path:
            raise ConfigurationError("No config path specified")
        
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(self.to_yaml())
