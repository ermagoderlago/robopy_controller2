#!/usr/bin/env python3
"""
Robot AI Services - Circuit Breaker & Retry
=============================================
Pattern di resilienza per le chiamate LLM.

Contiene:
  - retry_with_backoff: decoratore async con classificazione errori
  - CircuitBreaker: implementazione HALF_OPEN canonica enterprise-grade

Estratto da llm_service.py per migliorare la leggibilità e separazione dei concern.
"""

import asyncio
import functools
import threading
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Decoratore retry con classificazione degli errori
# ---------------------------------------------------------------------------
def retry_with_backoff(
    max_retries:    int   = 3,
    initial_delay:  float = 1.0,
    backoff_factor: float = 2.0,
):
    """
    Ritenta la chiamata in caso di errori transitori.

    - PERMISSION_DENIED / API_KEY_INVALID / INVALID_ARGUMENT → fail-fast immediato
    - RESOURCE_EXHAUSTED / 429                               → backoff 3× aggiuntivo
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            delay    = initial_delay
            last_err = None
            for attempt in range(max_retries):
                try:
                    return await func(self, *args, **kwargs)
                except Exception as e:
                    last_err = e
                    str_e    = str(e)

                    if any(tag in str_e for tag in [
                        "PERMISSION_DENIED",
                        "API_KEY_INVALID",
                        "INVALID_ARGUMENT",
                    ]):
                        raise

                    if "RESOURCE_EXHAUSTED" in str_e or "429" in str_e:
                        delay *= 3

                    self.get_logger().warning(
                        f"Retry {attempt + 1}/{max_retries} fallito: {e}. "
                        f"Riprovo in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor

            raise last_err
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker asincrono — HALF_OPEN canonico enterprise-grade
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """
    Tre stati: CLOSED → OPEN → HALF_OPEN → CLOSED.

    Comportamento HALF_OPEN canonico:
      - Solo UNA chiamata di test (probe) viene lasciata passare.
      - Tutte le altre vengono bloccate finché la probe non decide il destino.
      - Probe OK  → CLOSED, failures = 0.
      - Probe KO  → OPEN, nuovo recovery_timeout.

    Il lock asyncio viene assegnato via _init_async_resources() per evitare
    il bug di associazione al loop sbagliato (Python < 3.10).
    """

    def __init__(
        self,
        name:              str,
        failure_threshold: int   = 5,
        recovery_timeout:  float = 60.0,
    ):
        self.name              = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.failures          = 0
        self.last_failure_time: float = 0.0

        self._state           = "CLOSED"
        self._state_lock      = threading.Lock()          # lettura thread-safe da ROS
        self._probe_in_flight = False                     # flag HALF_OPEN canonico
        self._lock: Optional[asyncio.Lock] = None         # assegnato da _init_async_resources

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    @state.setter
    def state(self, value: str):
        with self._state_lock:
            self._state = value

    async def call_async(self, func, *args, **kwargs):
        if self._lock is None:
            raise RuntimeError(
                f"CircuitBreaker[{self.name}] non inizializzato: "
                "chiama _init_async_resources() prima dell'uso."
            )

        async with self._lock:
            current = self._state

            if current == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self._state           = "HALF_OPEN"
                    self._probe_in_flight = False
                    current               = "HALF_OPEN"
                else:
                    raise Exception(
                        f"Circuit breaker [{self.name}] OPEN — troppe chiamate fallite"
                    )

            if current == "HALF_OPEN":
                if self._probe_in_flight:
                    raise Exception(
                        f"Circuit breaker [{self.name}] HALF_OPEN — "
                        "probe già in volo, attendi l'esito prima di riprovare"
                    )
                self._probe_in_flight = True   # una sola probe alla volta

        try:
            result = await func(*args, **kwargs)

            async with self._lock:
                if self._state == "HALF_OPEN":
                    self._state           = "CLOSED"
                    self.failures         = 0
                    self._probe_in_flight = False

            return result

        except Exception as e:
            async with self._lock:
                self.failures         += 1
                self.last_failure_time  = time.time()
                self._probe_in_flight   = False

                if self.failures >= self.failure_threshold or self._state == "HALF_OPEN":
                    self._state = "OPEN"
            raise
