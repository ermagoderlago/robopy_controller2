from robot_ai.integrations import HomeAssistantClient
from robot_ai.core import EventBus, EventType, Event
from robot_ai.utils import get_logger

class HAContextUpdater:
    def __init__(self, ha_client: HomeAssistantClient, event_bus: EventBus):
        self.ha_client = ha_client
        self.event_bus = event_bus
        self._ha_context_cache = ""
        self._logger = get_logger("ha_context")
        self._subscribe_events()

    def _subscribe_events(self):
        # Iscrizione all'evento per invalidare e forzare un aggiornamento immediato della cache
        self.event_bus.subscribe(EventType.HA_EVENT_RECEIVED, self._on_ha_event)

    async def _on_ha_event(self, event: Event):
        event_data = event.data
        self._logger.debug(f"HA Event received, invalidating cache. {event_data}")
        await self.update()

    def get_context_string(self) -> str:
        return self._ha_context_cache

    async def update(self):
        """Aggiorna il contesto leggendo le entità tramite HA Client"""
        try:
            states = await self.ha_client.get_states()
            if not states:
                return
                
            # Priorità agli stati attivi per non tagliare fuori dispositivi accesi/attivi
            priority_states = {'on', 'playing', 'cleaning', 'open', 'home', 'active'}
            sorted_states = sorted(
                states,
                key=lambda s: 0 if str(s.state).lower() in priority_states else 1
            )
            
            entities = []
            for s in sorted_states:
                eid = s.entity_id
                state_val = s.state
                domain = s.domain
                # Filtra entità
                if domain in ['light', 'switch', 'sensor', 'media_player', 'climate', 'vacuum']:
                    name = s.attributes.get('friendly_name', eid)
                    unit = s.attributes.get('unit_of_measurement', '')
                    unit_str = f" {unit}" if unit else ""
                    entities.append(f"- {name} ({eid}): {state_val}{unit_str}")
                    # Limita a 30 entità prioritarie
                    if len(entities) >= 30:
                        break
            
            self._ha_context_cache = "[HOME ASSISTANT CONTEXT]\nStato dispositivi:\n" + "\n".join(entities)
            self._logger.debug("HA Context updated.")
            
        except Exception as e:
            self._logger.debug(f"Errore aggiornamento contesto HA: {e}")
