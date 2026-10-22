"""
MCM Orchestrator — receives tagged events, enforces the provenance hard rule,
and routes trusted commands to the Intent Router.
This is the only component allowed to ask a clarifying question before acting.
"""
from pikina.mcm.provenance import tag, TRUSTED
from pikina.mcm import killswitch
from pikina.eventbus.bus import EventBus


def _mark_live_recording() -> None:
    """Open the activity store before this command's first audit event, so a later backfill
    never imports a command that the live activity log saw (or skipped while paused)."""
    try:
        from pikina.activity.store import ActivityStore
        ActivityStore(timeout=0.5)
    except Exception:
        pass


class Orchestrator:
    def __init__(self, router=None, tier2_router=None):
        self._router = router
        self._tier2_router = tier2_router
        self._bus    = EventBus()

    def set_router(self, router) -> None:
        self._router = router

    def set_tier2_router(self, tier2_router) -> None:
        self._tier2_router = tier2_router

    def receive(self, text: str, source: str = "user_typed", origin: str = "typed") -> dict:
        """
        Entry point for all input.
        1. Tags provenance.
        2. Enforces the hard rule: untrusted input never executes.
        3. Routes trusted commands to the Tier 1 router.
        4. If Tier 1 doesn't match, falls back to the Tier 2 local LLM router.
        source decides trust (provenance); origin only says which surface sent it
        (typed, quick_panel, cli) for the activity log, and never affects trust.
        Returns a result dict.
        """
        provenance = tag(source)
        _mark_live_recording()

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

        if killswitch.is_engaged():
            return {"status": "denied", "reason": "Kill switch is engaged — all actions are halted."}

        if self._router is None:
            return {"status": "error", "reason": "no_router_configured"}

        result = self._router.route(text, origin=origin)
        if result.get("status") == "no_match" and self._tier2_router is not None:
            # Fall back to Tier 2 local LLM call
            return self._tier2_router.route(text, origin=origin)

        return result
