"""
Tier 1 Intent Router — deterministic phrase-to-capability mapping.
No models. No network. No AI. Pure regex matching.
A Tier 1 command executes with the model layer completely unloaded.
"""
import re
from typing import Optional, Callable, Tuple

from core.registry.loader import CapabilityRegistry
from core.registry.gatekeeper import request_consent
from core.eventbus.bus import EventBus
from core.mcm.provenance import TRUSTED
from core.router.alias_resolver import AliasResolver

# ---------------------------------------------------------------------------
# Route table
# Each entry: (compiled_regex, tool_name, params_builder_fn)
# params_builder_fn receives the re.Match and returns a params dict.
# ---------------------------------------------------------------------------

def _app_params(match) -> dict:
    """Resolve matched app name to an executable."""
    name = match.group("app").strip().lower()
    return {"path": name}  # open_app.py handles alias resolution


def _todo_add_params(match) -> dict:
    text = match.group("text").strip()
    bucket = "backlog"
    due_date = None
    
    # Try to extract "bucket today/tomorrow/this_week/backlog"
    bucket_match = re.search(r"\b(bucket|in)\s+(today|tomorrow|this_week|backlog)\b", text, re.I)
    if bucket_match:
        bucket = bucket_match.group(2).lower()
        # Remove bucket pattern from text
        text = text[:bucket_match.start()] + text[bucket_match.end():]
        
    # Try to extract "due <date>" or "by <date>" at the end
    due_match = re.search(r"\b(due|by|at)\s+(?P<date>.+)$", text, re.I)
    if due_match:
        due_date = due_match.group("date").strip()
        text = text[:due_match.start()].strip()
        
    return {"text": text.strip(), "bucket": bucket, "due_date": due_date}


def _calendar_add_params(match) -> dict:
    title = match.group("title").strip()
    date_str = match.group("date").strip()
    return {"title": title, "date": date_str}


def _calendar_range_params(match) -> dict:
    range_type = match.group("range").strip().lower()
    from datetime import date, timedelta
    today = date.today()
    if "next" in range_type:
        start_date = (today + timedelta(days=7)).isoformat()
        end_date = (today + timedelta(days=14)).isoformat()
    else:
        start_date = today.isoformat()
        end_date = (today + timedelta(days=7)).isoformat()
    return {"start_date": start_date, "end_date": end_date}


ROUTES: list[Tuple[re.Pattern, str, Callable]] = [
    # Open VS Code (explicit variants first)
    (re.compile(r"open\s+(?P<app>vs\s*code|vscode)", re.I),          "app.open",          _app_params),
    # Open any named app
    (re.compile(r"open\s+(?P<app>[\w\s]+)",           re.I),          "app.open",          _app_params),
    # Lock screen
    (re.compile(r"lock\s+(my\s+)?(screen|workstation|pc|computer|laptop)", re.I),
                                                                       "system.lock_screen", lambda m: {}),
    # Find file
    (re.compile(r"find\s+(file\s+)?(?P<name>.+)", re.I),              "fs.find_file",      lambda m: {"name": m.group("name")}),
    # Recall memory
    (re.compile(r"recall\s+(?P<query>.+)", re.I),                     "memory.recall",     lambda m: {"query": m.group("query")}),
    # --- Phase 3.5a: Alias management ---
    # List aliases
    (re.compile(r"(list|show)\s+aliases?", re.I),                     "alias.list",        lambda m: {}),
    # Remove alias  — capture everything after 'remove alias'
    (re.compile(r"remove\s+alias\s+(?P<trigger>.+)", re.I),           "alias.remove",      lambda m: {"trigger": m.group("trigger")}),
    # --- Phase 3.5b: To-Do management ---
    # Add to-do
    (re.compile(r"(?:add\s+)?(?:todo|task)\s+(?P<text>.+)", re.I),     "todo.add",          _todo_add_params),
    # Complete to-do
    (re.compile(r"(?:mark\s+)?(?:complete|done)\s+(?P<text>.+)", re.I), "todo.complete",     lambda m: {"text": m.group("text")}),
    # List done to-dos
    (re.compile(r"(?:list|show)\s+done\s+(?:todos|tasks|list)", re.I), "todo.list",         lambda m: {"status": "done"}),
    # List all to-dos (done + pending)
    (re.compile(r"(?:list|show)\s+all\s+(?:todos|tasks|list)", re.I),  "todo.list",         lambda m: {"status": "all"}),
    # List pending to-dos
    (re.compile(r"(?:list|show)\s+(?:todos|tasks|list)", re.I),        "todo.list",         lambda m: {"status": "pending"}),
    # Remove to-do
    (re.compile(r"(?:remove|delete)\s+(?:todo|task)\s+(?P<text>.+)", re.I), "todo.remove",    lambda m: {"text": m.group("text")}),
    # --- Phase 3.5c: Calendar management ---
    # Add event (explicit "on" or "at" connector)
    (re.compile(r"add\s+(?:appointment|event|meeting)\s+(?P<title>.+?)\s+(?:on|at|date)\s+(?P<date>.+)", re.I), "calendar.add_event", _calendar_add_params),
    # Add event (fallback/simple trigger)
    (re.compile(r"add\s+(?:appointment|event|meeting)\s+(?P<title>.+)", re.I), "calendar.add_event", lambda m: {"title": m.group("title"), "date": ""}),
    # Query calendar range ("this week", "next week")
    (re.compile(r"what(?:\s+do\s+I\s+have|\s+'s|\s+is\s+happening)?\s+(?P<range>(?:this|next)\s+week)\b", re.I), "calendar.query", _calendar_range_params),
    # Query calendar date
    (re.compile(r"what(?:'s|is)\s+on\s+(?P<date>.+)", re.I),          "calendar.query",    lambda m: {"date": m.group("date")}),
    # Remove event
    (re.compile(r"(?:remove|delete)\s+(?:event|appointment)\s+(?P<text>.+)", re.I), "calendar.remove_event", lambda m: {"text": m.group("text")}),
    # --- Phase 3.5f: Preferences Profile management ---
    # Preferred name: "call me Aditya" or "call me John"
    (re.compile(r"call\s+me\s+(?P<name>.+)", re.I),                   "prefs.update",      lambda m: {"field": "preferred_name", "value": m.group("name")}),
    # Formality: "be formal" or "be casual"
    (re.compile(r"be\s+(?P<form>casual|formal|neutral)", re.I),       "prefs.update",      lambda m: {"field": "formality", "value": m.group("form")}),
    # Verbosity: "be concise" or "be detailed"
    (re.compile(r"be\s+(?P<verb>concise|detailed|balanced)", re.I),   "prefs.update",      lambda m: {"field": "verbosity", "value": m.group("verb")}),
    # Quiet hours: "quiet hours from 23:00 to 07:00"
    (re.compile(r"quiet\s+hours\s+from\s+(?P<start>\d{2}:\d{2})\s+to\s+(?P<end>\d{2}:\d{2})", re.I), "prefs.update", lambda m: {"field": "quiet_hours", "value": [m.group("start"), m.group("end")]}),
]


class Tier1Router:
    def __init__(self, registry: Optional[CapabilityRegistry] = None):
        self._registry       = registry or CapabilityRegistry()
        self._bus            = EventBus()
        self._alias_resolver = AliasResolver()

    def route(self, text: str) -> dict:
        """
        Match text against ROUTES and execute the first match.
        Alias phrases are checked FIRST, before the regex route table.
        Returns a result dict. Never blocks waiting for a model.
        """
        text = text.strip()

        # --- Phase 3.5a: Alias check (runs before regex ROUTES) ---
        alias = self._alias_resolver.resolve(text)
        if alias:
            return self._execute_alias(alias, raw=text)

        for pattern, tool, params_fn in ROUTES:
            match = pattern.fullmatch(text) or pattern.match(text)
            if match:
                return self._execute(tool, params_fn(match), raw=text)

        return {
            "status":  "no_match",
            "message": f"No Tier 1 route matched: '{text}'",
            "hint":    "Try: 'open vs code', 'lock screen', 'find file resume.pdf', 'list aliases'",
        }

    def _execute(self, tool: str, params: dict, raw: str) -> dict:
        try:
            manifest = self._registry.get_manifest(tool)
        except KeyError:
            return {"status": "error", "reason": f"Unknown tool in route table: '{tool}'"}

        level = manifest["permission_level"]

        # Gate Level 4+ and Level 3+ non-reversible actions with native Windows consent dialog
        if level >= 4 or (level >= 3 and not manifest.get("supports_rollback", True)):
            if not request_consent(tool, manifest["description"], params):
                self._bus.publish(
                    topic="router.denied",
                    payload={"tool": tool, "params": params, "raw": raw},
                    provenance=TRUSTED,
                    permission_level=level,
                )
                return {"status": "denied", "reason": "User declined consent dialog."}

        self._bus.publish(
            topic="router.executing",
            payload={"tool": tool, "params": params, "raw": raw, "tier": 1},
            provenance=TRUSTED,
            permission_level=level,
        )

        result = self._registry.execute(tool, params)

        self._bus.publish(
            topic="router.result",
            payload={"tool": tool, "result": result, "tier": 1},
            provenance=TRUSTED,
            permission_level=level,
        )

        return result

    def _execute_alias(self, alias: dict, raw: str) -> dict:
        """
        Execute a multi-step alias sequence.
        Every step runs through the full permission/consent gate.
        Stops on first failure and reports which step failed.
        """
        steps   = alias.get("steps", [])
        trigger = alias.get("trigger", raw)
        results = []

        self._bus.publish(
            topic="alias.started",
            payload={"trigger": trigger, "steps": len(steps), "raw": raw},
            provenance=TRUSTED,
            permission_level=0,
        )

        for i, step in enumerate(steps):
            tool   = step.get("tool", "")
            params = step.get("params", {})
            result = self._execute(tool, params, raw=f"{raw} [alias step {i+1}/{len(steps)}]")
            results.append({"step": i + 1, "tool": tool, "result": result})

            # Stop the sequence on any failure or denial
            if result.get("status") in ("error", "denied"):
                return {
                    "status":  result["status"],
                    "reason":  f"Alias '{trigger}' stopped at step {i+1} ({tool}): {result.get('reason', result.get('message', 'unknown error'))}",
                    "steps_completed": i,
                    "steps_total":     len(steps),
                    "results":         results,
                }

        return {
            "status":          "ok",
            "message":         f"Alias '{trigger}' completed all {len(steps)} step(s).",
            "steps_completed": len(steps),
            "steps_total":     len(steps),
            "results":         results,
        }
