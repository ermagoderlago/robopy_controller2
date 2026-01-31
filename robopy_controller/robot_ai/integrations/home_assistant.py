"""
Robot AI Integrations - Home Assistant
========================================
Home Assistant integration client based on existing homeassistant_node.py
Uses WebSocket API for real-time communication.
"""

import asyncio
import json
import time
import threading
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from ..core.config_manager import ConfigManager
from ..core.exceptions import HomeAssistantError
from ..core.event_bus import EventBus, EventType
from ..core.circuit_breaker import CircuitBreakerRegistry
from ..utils.logging_utils import get_logger


@dataclass
class HAEntity:
    """Home Assistant entity."""
    entity_id: str
    domain: str
    state: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    last_updated: float = 0


@dataclass
class HAServiceCall:
    """Represents a HA service call."""
    domain: str
    service: str
    entity_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


class HomeAssistantClient:
    """
    Home Assistant WebSocket client.
    
    Features:
    - Persistent WebSocket connection
    - Service calls (lights, switches, climate, etc.)
    - State change subscriptions
    - Entity discovery
    - Whitelist validation
    
    Usage:
        client = HomeAssistantClient()
        await client.connect()
        
        # Call service
        await client.call_service("light", "turn_on", entity_id="light.cucina")
        
        # Get state
        state = await client.get_state("light.cucina")
    """
    
    def __init__(self, config_manager: ConfigManager = None):
        if not HAS_WEBSOCKETS:
            raise ImportError("websockets is required: pip install websockets")
        
        self.logger = get_logger("ha_client")
        self.config = config_manager or ConfigManager()
        self.ai_config = self.config.get_config()
        self.event_bus = EventBus()
        
        # Connection state
        self._websocket = None
        self._is_connected = False
        self._message_id = 1
        self._pending_requests: Dict[int, asyncio.Future] = {}
        
        # Entity cache
        self._entities: Dict[str, HAEntity] = {}
        self._whitelist_domains: Set[str] = set(self.ai_config.home_assistant.whitelist_domains)
        self._whitelist_entities: Set[str] = set(self.ai_config.home_assistant.whitelist_entities)
        
        # Callbacks
        self._state_callbacks: List[Callable] = []
        
        # Connection management
        self._reconnect_task: Optional[asyncio.Task] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._shutdown = False
        
        # Circuit breaker
        self._breaker = CircuitBreakerRegistry().get_or_create(
            "home_assistant",
            failure_threshold=self.ai_config.circuit_breaker.ha_failure_threshold,
            recovery_timeout=self.ai_config.circuit_breaker.recovery_timeout
        )
        
        self.logger.info("Home Assistant client initialized")
    
    @property
    def is_connected(self) -> bool:
        return self._is_connected and self._websocket is not None
    
    @property
    def url(self) -> str:
        return self.ai_config.home_assistant.url
    
    @property
    def token(self) -> str:
        return self.ai_config.secrets.ha_token or self.ai_config.home_assistant.token
    
    async def connect(self) -> bool:
        """
        Connect to Home Assistant.
        
        Returns:
            True if connected successfully
        """
        if self._is_connected:
            return True
        
        if not self.token:
            self.logger.error("Home Assistant token not configured")
            return False
        
        try:
            self.logger.info(f"Connecting to Home Assistant: {self.url}")
            
            self._websocket = await websockets.connect(
                self.url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            )
            
            # Handshake
            hello = await self._websocket.recv()
            hello_data = json.loads(hello)
            
            if hello_data.get("type") != "auth_required":
                raise HomeAssistantError("Unexpected handshake response")
            
            # Authenticate
            await self._authenticate()
            
            self._is_connected = True
            self._shutdown = False
            
            # Start listener
            self._listener_task = asyncio.create_task(self._message_listener())
            
            # Publish event
            self.event_bus.publish(EventType.HA_CONNECTED, {"url": self.url})
            
            self.logger.info("Connected to Home Assistant")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to connect to Home Assistant: {e}")
            self._is_connected = False
            raise HomeAssistantError(f"Connection failed: {str(e)}")
    
    async def disconnect(self) -> None:
        """Disconnect from Home Assistant."""
        self._shutdown = True
        self._is_connected = False
        
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None
        
        self.event_bus.publish(EventType.HA_DISCONNECTED, {})
        self.logger.info("Disconnected from Home Assistant")
    
    async def _authenticate(self) -> None:
        """Authenticate with Home Assistant."""
        auth_msg = {
            "type": "auth",
            "access_token": self.token
        }
        await self._websocket.send(json.dumps(auth_msg))
        
        response = await self._websocket.recv()
        result = json.loads(response)
        
        if result.get("type") != "auth_ok":
            error = result.get("message", "Unknown error")
            raise HomeAssistantError(f"Authentication failed: {error}")
        
        self.logger.info("Authenticated with Home Assistant")
    
    async def _message_listener(self) -> None:
        """Listen for incoming messages."""
        while not self._shutdown and self._websocket:
            try:
                message = await asyncio.wait_for(
                    self._websocket.recv(),
                    timeout=30.0
                )
                await self._handle_message(json.loads(message))
                
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("WebSocket connection closed")
                self._is_connected = False
                break
            except Exception as e:
                if not self._shutdown:
                    self.logger.error(f"Error in message listener: {e}")
                break
        
        # Try to reconnect
        if not self._shutdown:
            asyncio.create_task(self._reconnect())
    
    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming message."""
        msg_type = data.get("type")
        msg_id = data.get("id")
        
        # Handle response to pending request
        if msg_id and msg_id in self._pending_requests:
            future = self._pending_requests.pop(msg_id)
            if not future.done():
                if data.get("success", True):
                    future.set_result(data)
                else:
                    future.set_exception(HomeAssistantError(data.get("error", {}).get("message", "Unknown error")))
        
        # Handle events
        if msg_type == "event":
            event = data.get("event", {})
            await self._handle_event(event)
    
    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Handle HA event."""
        event_type = event.get("event_type")
        event_data = event.get("data", {})
        
        if event_type == "state_changed":
            entity_id = event_data.get("entity_id")
            new_state = event_data.get("new_state", {})
            
            if entity_id:
                # Update cache
                self._entities[entity_id] = HAEntity(
                    entity_id=entity_id,
                    domain=entity_id.split(".")[0],
                    state=new_state.get("state", ""),
                    attributes=new_state.get("attributes", {}),
                    last_updated=time.time()
                )
                
                # Notify callbacks
                for callback in self._state_callbacks:
                    try:
                        callback(entity_id, new_state.get("state"))
                    except Exception as e:
                        self.logger.error(f"Error in state callback: {e}")
                
                # Publish event
                self.event_bus.publish(EventType.HA_EVENT_RECEIVED, {
                    "entity_id": entity_id,
                    "state": new_state.get("state")
                })
    
    async def _reconnect(self) -> None:
        """Attempt to reconnect."""
        while not self._shutdown and not self._is_connected:
            try:
                self.logger.info("Attempting to reconnect to Home Assistant...")
                await asyncio.sleep(5)
                await self.connect()
                break
            except Exception as e:
                self.logger.error(f"Reconnection failed: {e}")
    
    async def _send_request(self, data: Dict[str, Any], timeout: float = None) -> Dict[str, Any]:
        """Send request and wait for response."""
        if not self.is_connected:
            raise HomeAssistantError("Not connected to Home Assistant")
        
        msg_id = self._message_id
        self._message_id += 1
        
        data["id"] = msg_id
        
        # Create future for response
        future = asyncio.Future()
        self._pending_requests[msg_id] = future
        
        try:
            await self._websocket.send(json.dumps(data))
            
            timeout = timeout or self.ai_config.home_assistant.request_timeout
            return await asyncio.wait_for(future, timeout=timeout)
            
        except asyncio.TimeoutError:
            self._pending_requests.pop(msg_id, None)
            raise HomeAssistantError("Request timeout")
    
    def _validate_access(self, domain: str, entity_id: str = None) -> bool:
        """Validate access to domain/entity."""
        # Check entity whitelist first
        if entity_id and self._whitelist_entities:
            if entity_id in self._whitelist_entities:
                return True
        
        # Check domain whitelist
        if self._whitelist_domains and domain not in self._whitelist_domains:
            return False
        
        return True
    
    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str = None,
        data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Call a Home Assistant service.
        
        Args:
            domain: Service domain (light, switch, etc.)
            service: Service name (turn_on, turn_off, etc.)
            entity_id: Target entity (optional)
            data: Service data (optional)
            
        Returns:
            Response from HA
        """
        # Validate access
        if not self._validate_access(domain, entity_id):
            raise HomeAssistantError(f"Access denied for domain '{domain}'")
        
        request = {
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        
        if entity_id:
            request["target"] = {"entity_id": entity_id}
        
        if data:
            request["service_data"] = data
        
        self.logger.debug(f"Calling service: {domain}.{service} on {entity_id}")
        
        try:
            result = await self._breaker.call_async(self._send_request, request)
            self.logger.info(f"Service call successful: {domain}.{service}")
            return result
        except Exception as e:
            self.logger.error(f"Service call failed: {e}")
            raise
    
    async def get_state(self, entity_id: str) -> Optional[str]:
        """
        Get current state of an entity.
        
        Args:
            entity_id: Entity ID
            
        Returns:
            Entity state or None
        """
        # Check cache first
        if entity_id in self._entities:
            return self._entities[entity_id].state
        
        # Fetch from HA
        request = {
            "type": "get_states"
        }
        
        try:
            result = await self._send_request(request)
            
            for state in result.get("result", []):
                eid = state.get("entity_id")
                self._entities[eid] = HAEntity(
                    entity_id=eid,
                    domain=eid.split(".")[0] if eid else "",
                    state=state.get("state", ""),
                    attributes=state.get("attributes", {}),
                    last_updated=time.time()
                )
            
            return self._entities.get(entity_id, HAEntity(entity_id, "", "")).state
            
        except Exception as e:
            self.logger.error(f"Failed to get state: {e}")
            return None
    
    async def get_entities(self, domain: str = None) -> List[HAEntity]:
        """
        Get all entities, optionally filtered by domain.
        
        Args:
            domain: Filter by domain (optional)
            
        Returns:
            List of entities
        """
        # Fetch fresh data
        await self.get_state("dummy")
        
        entities = list(self._entities.values())
        
        if domain:
            entities = [e for e in entities if e.domain == domain]
        
        return entities
    
    async def subscribe_events(self, event_type: str = "state_changed") -> None:
        """Subscribe to Home Assistant events."""
        request = {
            "type": "subscribe_events",
            "event_type": event_type
        }
        
        await self._send_request(request)
        self.logger.info(f"Subscribed to events: {event_type}")
    
    def on_state_change(self, callback: Callable) -> None:
        """Register callback for state changes."""
        self._state_callbacks.append(callback)
    
    # Convenience methods
    
    async def turn_on(self, entity_id: str, **kwargs) -> Dict[str, Any]:
        """Turn on an entity."""
        domain = entity_id.split(".")[0]
        return await self.call_service(domain, "turn_on", entity_id, kwargs if kwargs else None)
    
    async def turn_off(self, entity_id: str) -> Dict[str, Any]:
        """Turn off an entity."""
        domain = entity_id.split(".")[0]
        return await self.call_service(domain, "turn_off", entity_id)
    
    async def toggle(self, entity_id: str) -> Dict[str, Any]:
        """Toggle an entity."""
        domain = entity_id.split(".")[0]
        return await self.call_service(domain, "toggle", entity_id)
    
    async def set_brightness(self, entity_id: str, brightness_pct: int) -> Dict[str, Any]:
        """Set light brightness."""
        return await self.call_service(
            "light", "turn_on", entity_id,
            {"brightness_pct": brightness_pct}
        )
    
    async def set_temperature(self, entity_id: str, temperature: float) -> Dict[str, Any]:
        """Set climate temperature."""
        return await self.call_service(
            "climate", "set_temperature", entity_id,
            {"temperature": temperature}
        )
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get client statistics."""
        return {
            "connected": self.is_connected,
            "url": self.url,
            "cached_entities": len(self._entities),
            "circuit_breaker": self._breaker.get_status() if self._breaker else None
        }
