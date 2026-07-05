import json
from core.eventbus.bus import EventBus
from core.memory.vector_store import VectorStore
from core.mcm.provenance import TRUSTED, UNTRUSTED

class TrustEngine:
    """
    The Trust Engine sits between the EventBus and the Memory Systems.
    It evaluates the provenance of every event.
    - UNTRUSTED_DATA (clipboard, file watcher) goes to the Ephemeral Store.
    - TRUSTED_COMMAND (explicit user inputs) goes to the Permanent Store.
    """
    def __init__(self, bus: EventBus):
        self.bus = bus
        self.vector_store = VectorStore()
        
        # Pre-load the embedding model in the main thread.
        # Lazy-loading PyTorch/SentenceTransformers in a daemon thread (like EventBus._safe_call)
        # causes deadlocks and silent crashes on Windows.
        _ = self.vector_store.model

        
        # Subscribe to all events. 
        # Using a wildcard or subscribing to specific sensory/user topics.
        # For now, we subscribe to specific known topics that generate memory context.
        self.bus.subscribe("clipboard.copied", self._handle_event)
        self.bus.subscribe("fs.file_created", self._handle_event)
        self.bus.subscribe("fs.file_modified", self._handle_event)
        self.bus.subscribe("user.command", self._handle_event)

    def _handle_event(self, event: dict):
        event_id = event["id"]
        provenance = event["provenance"]
        topic = event["topic"]
        payload = event["payload"]
        
        # Convert the payload to a semantic string
        # e.g., clipboard.copied -> "Copied text: Hello World"
        # e.g., fs.file_created -> "File created at path: C:\..."
        if topic == "clipboard.copied":
            content = f"Copied text: {payload.get('content', '')}"
        elif topic.startswith("fs.file"):
            action = topic.split("_")[1]
            content = f"File {action} at path: {payload.get('path', '')}"
        elif topic == "user.command":
            content = f"User explicitly stated: {payload.get('text', '')}"
        else:
            content = json.dumps(payload)
            
        if not content.strip():
            return
            
        # Route to memory tiers based on Trust Fabric rules
        if provenance == TRUSTED:
            # High trust (>= 0.90) routes to Permanent Store
            self.vector_store.add_permanent(id=event_id, content=content)
        elif provenance == UNTRUSTED:
            # Low trust (< 0.50) routes to Ephemeral Store
            self.vector_store.add_ephemeral(id=event_id, content=content)

    def start(self):
        print("[TrustEngine] Started and listening to EventBus.")
