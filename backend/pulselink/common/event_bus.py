"""Event-bus seam.

Defines the ``EventBus`` Protocol plus an ``InMemoryEventBus`` used by the
offline demo. In the budget production profile this maps to Amazon EventBridge.
Domain events (transfusion recorded, donor responded, voice outcome) flow
through this seam so re-learning and re-scoring stay loosely coupled.

The concrete event payloads and the EventBridge adapter are implemented in
later tasks; this file establishes the seam and a working in-memory bus.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Protocol, runtime_checkable

Handler = Callable[[dict[str, Any]], None]


@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe seam. Maps to Amazon EventBridge in production."""

    def publish(self, event_type: str, payload: dict[str, Any]) -> None: ...

    def subscribe(self, event_type: str, handler: Handler) -> None: ...


class InMemoryEventBus:
    """Synchronous in-memory event bus for the offline demo."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        for handler in list(self._handlers.get(event_type, [])):
            handler(payload)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)
