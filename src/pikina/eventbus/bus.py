"""
Local Event Bus — thread-safe singleton pub/sub broker.
Every event published here is persisted to the SQLite replay store.
Nothing is fire-and-forget: every dispatch is logged.
"""
import uuid
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List

from .replay import ReplayStore


class EventBus:
    """
    Singleton pub/sub broker with persistent replay log.
    Usage:
        bus = EventBus()
        bus.subscribe("fs.download_created", my_handler)
        bus.publish("fs.download_created", payload={...}, provenance="UNTRUSTED_DATA")
    """
    _instance     = None
    _creation_lock = threading.Lock()

    def __new__(cls):
        with cls._creation_lock:
            if cls._instance is None:
                inst              = super().__new__(cls)
                inst._subscribers: Dict[str, List[Callable]] = {}
                inst._bus_lock    = threading.Lock()
                inst.replay       = ReplayStore()
                cls._instance     = inst
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, handler: Callable) -> None:
        """Register a handler for a topic. Multiple handlers per topic allowed."""
        with self._bus_lock:
            self._subscribers.setdefault(topic, []).append(handler)

    def unsubscribe(self, topic: str, handler: Callable) -> None:
        """Remove a specific handler from a topic."""
        with self._bus_lock:
            if topic in self._subscribers:
                self._subscribers[topic] = [
                    h for h in self._subscribers[topic] if h != handler
                ]

    def publish(
        self,
        topic: str,
        payload: dict,
        provenance: str,
        permission_level: int = 0,
        failure_class: str = None,
    ) -> str:
        """
        Publish an event. Persists to SQLite, then dispatches to subscribers.
        Returns the event UUID.
        """
        event = {
            "id":            str(uuid.uuid4()),
            "topic":         topic,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "provenance":    provenance,
            "payload":       payload,
            "failure_class": failure_class,
        }
        self.replay.append(event, permission_level=permission_level)
        self._dispatch(topic, event)
        return event["id"]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dispatch(self, topic: str, event: dict) -> None:
        with self._bus_lock:
            handlers = list(self._subscribers.get(topic, []))
        for handler in handlers:
            threading.Thread(
                target=self._safe_call,
                args=(handler, event),
                daemon=True,
            ).start()

    @staticmethod
    def _safe_call(handler: Callable, event: dict) -> None:
        try:
            handler(event)
        except Exception as exc:
            print(f"[EventBus] Handler '{handler.__name__}' raised: {exc}")
