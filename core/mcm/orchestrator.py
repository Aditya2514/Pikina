"""
MCM Orchestrator — receives tagged events, enforces the provenance hard rule,
and routes trusted commands to the Intent Router.
This is the only component allowed to ask a clarifying question before acting.
"""
from core.mcm.provenance import tag, TRUSTED
from core.eventbus.bus import EventBus


class Orchestrator:
    def __init__(self, router=None):
        self._router = router
        self._bus    = EventBus()

    def set_router(self, router) -> None:
        self._router = router

    def receive(self, text: str, source: str = "user_typed") -> dict:
        """
        Entry point for all input.
        1. Tags provenance.
        2. Enforces the hard rule: untrusted input never executes.
        3. Routes trusted commands to the Tier 1 router.
        Returns a result dict.
        """
        provenance = tag(source)

        self._bus.publish(
            topic="mcm.received",
            payload={"text": text, "source": source, "provenance": provenance},
            provenance=provenance,
        )

        if provenance != TRUSTED:
            # Log it — but summarize only, never execute.
            self._bus.publish(
                topic="mcm.rejected",
                payload={"text": text[:200], "reason": "untrusted_provenance", "source": source},
                provenance=provenance,
            )
            return {
                "status":  "rejected",
                "reason":  "untrusted_provenance",
                "summary": (
                    f"[MCM] Input from '{source}' is UNTRUSTED_DATA. "
                    "This content has been logged but will not execute. "
                    "Only content you type or speak directly can trigger actions."
                ),
            }

        if self._router is None:
            return {"status": "error", "reason": "no_router_configured"}

        return self._router.route(text)
