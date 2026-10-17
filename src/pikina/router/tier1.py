"""
Tier 1 Intent Router — deterministic phrase-to-capability mapping.
No models. No network. No AI. Pure regex matching.
A Tier 1 command executes with the model layer completely unloaded.
"""
import re
import uuid
from typing import Optional, Callable, Tuple

from pikina.registry.loader import CapabilityRegistry
from pikina.registry.executor import execute_action
from pikina.transactions.plan_executor import Plan, PlanStep, PlanExecutor
from pikina.transactions.rollback_spec import resolve_rollback_params, RollbackSpecError
from pikina.eventbus.bus import EventBus
from pikina.mcm.provenance import TRUSTED
from pikina.router.alias_resolver import AliasResolver

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
    # Open/start/launch Calculator (must be before generic open to avoid falling through to model)
    (re.compile(r"(?:open|start|launch|run)\s+(?:the\s+)?(?P<app>calc(?:ulator)?)", re.I), "app.open", _app_params),
    # Open/start/launch Paint
    (re.compile(r"(?:open|start|launch|run)\s+(?:the\s+)?(?P<app>paint)", re.I),           "app.open", _app_params),
    # Open/start/launch Notepad (so model never sees it)
    (re.compile(r"(?:open|start|launch|run)\s+(?:the\s+)?(?P<app>notepad)", re.I),         "app.open", _app_params),
    # Open / launch any named app ("start"/"run" are left out: "start stopwatch" must not launch an app)
    # Capped at 3 words so longer requests ("launch something to write text") go to Tier 2.
    (re.compile(r"(?:open|launch)\s+(?:the\s+)?(?P<app>[\w.+-]+(?:\s+[\w.+-]+){0,2})\s*$", re.I), "app.open", _app_params),
    # Lock screen
    (re.compile(r"lock\s+(my\s+)?(screen|workstation|pc|computer|laptop)", re.I),
                                                                       "system.lock_screen", lambda m: {}),
    # Find file (several natural-language triggers, with or without file extension)
    (re.compile(r"(?:find|look\s+up|search\s+for|where\s+is)\s+(?:files?\s+(?:with\s+)?|file\s+called\s+)?(?P<name>[\w\s.*-]+\.\w+)", re.I), "fs.find_file", lambda m: {"name": m.group("name").strip()}),
    (re.compile(r"(?:look\s+up|search\s+for)\s+files?\s+(?:with\s+|named?\s+|called\s+)?(?P<name>[\w\s*.-]+?)(?:\s+in\s+(?:the\s+)?name)?$", re.I), "fs.find_file", lambda m: {"name": m.group("name").strip()}),
    (re.compile(r"find\s+(?:file\s+)?(?P<name>.+)", re.I),             "fs.find_file",      lambda m: {"name": m.group("name")}),
    # Import an .ics calendar file: "import ics ~/Downloads/invite.ics"
    (re.compile(r"import\s+(?:an?\s+)?(?:ics|calendar|invite)(?:\s+file)?\s+(?:from\s+)?(?P<path>.+)", re.I),
                                                                       "calendar.import_ics", lambda m: {"filepath": m.group("path").strip().strip("\"'")}),
    # Recall memory
    (re.compile(r"recall\s+(?P<query>.+)", re.I),                     "memory.recall",     lambda m: {"query": m.group("query")}),
    # --- Phase 3.5a: Alias management ---
    # List aliases
    (re.compile(r"(list|show)\s+aliases?", re.I),                     "alias.list",        lambda m: {}),
    # Remove alias  — capture everything after 'remove alias'
    (re.compile(r"remove\s+alias\s+(?P<trigger>.+)", re.I),           "alias.remove",      lambda m: {"trigger": m.group("trigger")}),
    # --- Phase 3.5b: To-Do management ---
    # Add to-do (handles 'add a task to X', 'add todo X', 'todo X')
    (re.compile(r"add\s+a\s+task\s+(?:to\s+)?(?P<text>.+)", re.I),    "todo.add",          _todo_add_params),
    (re.compile(r"(?:add\s+)?(?:todo|task)\s+(?P<text>.+)", re.I),     "todo.add",          _todo_add_params),
    # Complete to-do: "mark task call mom as done", "mark buy milk as complete"
    (re.compile(r"mark\s+(?:the\s+)?(?:(?:task|todo)\s+)?(?P<text>.+?)\s+as\s+(?:done|complete[d]?|finished)$", re.I), "todo.complete", lambda m: {"text": m.group("text")}),
    # Complete to-do: "complete buy milk", "mark done buy milk"
    (re.compile(r"(?:mark\s+)?(?:complete|done)\s+(?P<text>.+)", re.I), "todo.complete",     lambda m: {"text": m.group("text")}),
    # Pending to-dos in natural phrasing: "list all pending tasks", "show my open todos", "what are my tasks"
    (re.compile(r"(?:list|show)\s+(?:all\s+)?(?:my\s+)?(?:pending|open|remaining|unfinished)\s+(?:todos|tasks|to-dos)", re.I), "todo.list", lambda m: {"status": "pending"}),
    (re.compile(r"what\s+(?:are|is)\s+(?:my|on\s+my)\s+(?:tasks|todos|to-dos|to-do\s+list|todo\s+list)\??$", re.I), "todo.list", lambda m: {"status": "pending"}),
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
    # --- Utilities (Stopwatch & Calculator) ---
    # Math expressions
    (re.compile(r"(?:calculate|calc)\s+(?P<expr>.+)", re.I),           "utility.calculate", lambda m: {"expr": m.group("expr")}),
    # Stopwatch start
    (re.compile(r"(?:start|start\s+the)\s+stopwatch", re.I),           "utility.stopwatch", lambda m: {"action": "start"}),
    # Stopwatch pause
    (re.compile(r"(?:stop|pause|stop\s+the|pause\s+the)\s+stopwatch", re.I), "utility.stopwatch", lambda m: {"action": "pause"}),
    # Stopwatch reset
    (re.compile(r"reset\s+(the\s+)?stopwatch", re.I),                  "utility.stopwatch", lambda m: {"action": "reset"}),
]


# "add alias morning -> start the stopwatch ; open notepad"
_ADD_ALIAS = re.compile(r"add\s+alias\s+(?P<trigger>.+?)\s*->\s*(?P<steps>.+)", re.I)


def match_route(text: str):
    """Return (tool, params) for the first Tier 1 route matching text, or None. Does not execute."""
    text = text.strip()
    for pattern, tool, params_fn in ROUTES:
        match = pattern.fullmatch(text) or pattern.match(text)
        if match:
            return tool, params_fn(match)
    return None


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

        define = _ADD_ALIAS.fullmatch(text)
        if define:
            return self._define_alias(define.group("trigger"), define.group("steps"), raw=text)

        routed = match_route(text)
        if routed:
            tool, params = routed
            return self._execute(tool, params, raw=text)

        return {
            "status":  "no_match",
            "message": f"No Tier 1 route matched: '{text}'",
            "hint":    "Try: 'open vs code', 'lock screen', 'find file resume.pdf', 'list aliases'",
        }

    def _define_alias(self, trigger: str, steps_text: str, raw: str) -> dict:
        """Turn 'step one ; step two' into tool calls using the Tier 1 route table."""
        steps = []
        for part in (p.strip() for p in steps_text.split(";")):
            if not part:
                continue
            routed = match_route(part)
            if routed is None or routed[0].startswith("alias."):
                return {"status": "error",
                        "reason": f"Alias step '{part}' is not a Tier 1 command, so it can't be saved."}
            steps.append({"tool": routed[0], "params": routed[1]})
        if not steps:
            return {"status": "error", "reason": "An alias needs at least one step after '->'."}
        result = self._execute("alias.add", {"trigger": trigger.strip(), "steps": steps}, raw=raw)
        if result.get("status") == "ok":
            result["steps"] = steps
        return result

    def _execute(self, tool: str, params: dict, raw: str) -> dict:
        return execute_action(self._registry, tool, params, raw=raw, tier=1, provenance=TRUSTED)

    def _execute_alias(self, alias: dict, raw: str) -> dict:
        """
        Execute a multi-step alias as a transaction.
        Every step runs through the full permission/consent gate.
        If a step fails or is denied, the completed steps are rolled back
        in reverse order using each capability's manifest rollback recipe.
        """
        steps   = alias.get("steps", [])
        trigger = alias.get("trigger", raw)

        self._bus.publish(
            topic="alias.started",
            payload={"trigger": trigger, "steps": len(steps), "raw": raw},
            provenance=TRUSTED,
            permission_level=0,
        )

        plan_steps = []
        for i, step in enumerate(steps):
            tool = step.get("tool", "")
            try:
                manifest = self._registry.get_manifest(tool)
            except KeyError:
                manifest = {}
            plan_steps.append(PlanStep(
                step_id=f"{i + 1}",
                tool=tool,
                params=step.get("params", {}),
                rollback_action=manifest.get("rollback_action") if manifest.get("supports_rollback") else None,
                read_only=bool(manifest.get("read_only", False)),
            ))

        def run_step(plan_step: PlanStep) -> bool:
            plan_step.result = self._execute(
                plan_step.tool, plan_step.params,
                raw=f"{raw} [alias step {plan_step.step_id}/{len(steps)}]",
            )
            return plan_step.result.get("status") not in ("error", "denied")

        def undo_step(plan_step: PlanStep) -> bool:
            manifest = self._registry.get_manifest(plan_step.tool)
            try:
                undo_params = resolve_rollback_params(
                    manifest.get("rollback_params", {}), plan_step.params, plan_step.result)
            except RollbackSpecError:
                return False
            undo = execute_action(
                self._registry, plan_step.rollback_action, undo_params,
                raw=f"rollback of {plan_step.tool} (alias '{trigger}')", tier=1,
                provenance=TRUSTED, gate=False,  # undoing the user's own change
            )
            return undo.get("status") == "ok"

        plan = Plan(id=f"alias:{trigger}:{uuid.uuid4().hex[:8]}", steps=plan_steps)
        PlanExecutor(registry=self._registry, execution_callback=run_step,
                     rollback_callback=undo_step).execute(plan)

        results = [{"step": int(s.step_id), "tool": s.tool, "result": s.result, "status": s.status}
                   for s in plan_steps if s.result is not None]
        completed = sum(1 for s in plan_steps if s.status in ("done", "rolled_back", "no_rollback_needed",
                                                             "rollback_failed", "no_rollback_available"))

        if plan.status == "completed":
            return {
                "status":          "ok",
                "message":         f"Alias '{trigger}' completed all {len(steps)} step(s).",
                "steps_completed": len(steps),
                "steps_total":     len(steps),
                "results":         results,
            }

        failed = next(s for s in plan_steps if s.status == "failed")
        reason = failed.result.get("reason", failed.result.get("message", "unknown error"))
        rollback_note = {
            "rolled_back":           "Earlier steps were rolled back.",
            "partially_rolled_back": "Some earlier steps could NOT be rolled back — check the results.",
            "failed":                "",
        }.get(plan.status, "")
        return {
            "status":          failed.result.get("status", "error"),
            "reason":          f"Alias '{trigger}' stopped at step {failed.step_id} ({failed.tool}): {reason}. {rollback_note}".strip(),
            "steps_completed": completed,
            "steps_total":     len(steps),
            "rollback":        plan.status,
            "results":         results,
        }
