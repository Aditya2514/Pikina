"""
Tier 1 Intent Router — deterministic phrase-to-capability mapping.
No models. No network. No AI. Pure regex matching, with the phrases declared in
each capability's manifest. A Tier 1 command runs with the model layer unloaded.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from pikina.activity.recorder import record

from pikina.registry.loader import CapabilityRegistry
from pikina.registry.executor import execute_action
from pikina.transactions.plan_executor import Plan, PlanStep, PlanExecutor
from pikina.transactions.rollback_spec import resolve_rollback_params, RollbackSpecError
from pikina.eventbus.bus import EventBus
from pikina.mcm.provenance import TRUSTED
from pikina.router.alias_resolver import AliasResolver

# "add alias morning -> start the stopwatch ; open notepad"
_ADD_ALIAS = re.compile(r"add\s+alias\s+(?P<trigger>.+?)\s*->\s*(?P<steps>.+)", re.I)


def match_route(text: str, registry: CapabilityRegistry):
    """
    Return (tool, params) for the first typed phrase that matches, or None. Does not execute.
    Phrases come from each capability manifest's "patterns" (core and plugins alike):
    named regex groups become params, a pattern's "params" adds fixed ones, and the
    capability's optional parse(params) gets the last word.
    """
    text = text.strip()
    for pattern, tool, static, handler in registry.routes():
        match = pattern.fullmatch(text) or pattern.match(text)
        if not match:
            continue
        params = dict(static)
        params.update({k: v.strip() for k, v in match.groupdict().items() if v is not None})
        if hasattr(handler, "parse"):
            params = handler.parse(params)
        return tool, params
    return None


class Tier1Router:
    def __init__(self, registry: Optional[CapabilityRegistry] = None):
        self._registry       = registry or CapabilityRegistry()
        self._bus            = EventBus()
        self._alias_resolver = AliasResolver()

    def route(self, text: str, origin: str = "typed") -> dict:
        """
        Match text against the manifest phrases and execute the first match.
        Alias phrases are checked FIRST, before the regex route table.
        origin (typed, quick_panel, cli) is passed on to the activity log.
        Returns a result dict. Never blocks waiting for a model.
        """
        text = text.strip()

        # Aliases are checked before any manifest phrase
        alias = self._alias_resolver.resolve(text)
        if alias:
            return self._execute_alias(alias, raw=text, origin=origin)

        define = _ADD_ALIAS.fullmatch(text)
        if define:
            return self._define_alias(define.group("trigger"), define.group("steps"), raw=text, origin=origin)

        routed = match_route(text, self._registry)
        if routed:
            tool, params = routed
            return self._execute(tool, params, raw=text, origin=origin)

        return {
            "status":  "no_match",
            "message": f"No Tier 1 route matched: '{text}'",
            "hint":    "Try: 'open vs code', 'find file resume.pdf', 'doctor', 'what's on port 5000', 'list aliases'",
        }

    def _define_alias(self, trigger: str, steps_text: str, raw: str, origin: str = "typed") -> dict:
        """Turn 'step one ; step two' into tool calls using the Tier 1 route table."""
        steps = []
        for part in (p.strip() for p in steps_text.split(";")):
            if not part:
                continue
            routed = match_route(part, self._registry)
            if routed is None or routed[0].startswith("alias."):
                return {"status": "error",
                        "reason": f"Alias step '{part}' is not a Tier 1 command, so it can't be saved."}
            steps.append({"tool": routed[0], "params": routed[1]})
        if not steps:
            return {"status": "error", "reason": "An alias needs at least one step after '->'."}
        result = self._execute("alias.add", {"trigger": trigger.strip(), "steps": steps}, raw=raw, origin=origin)
        if result.get("status") == "ok":
            result["steps"] = steps
        return result

    def _execute(self, tool: str, params: dict, raw: str, origin: str = "typed", group_id: str | None = None) -> dict:
        return execute_action(self._registry, tool, params, raw=raw, tier=1, provenance=TRUSTED,
                              origin=origin, group_id=group_id)

    def _execute_alias(self, alias: dict, raw: str, origin: str = "typed") -> dict:
        """
        Execute a multi-step alias as a transaction.
        Every step runs through the full permission/consent gate.
        If a step fails or is denied, the completed steps are rolled back
        in reverse order using each capability's manifest rollback recipe.
        """
        steps   = alias.get("steps", [])
        trigger = alias.get("trigger", raw)
        started = datetime.now(timezone.utc)
        group_id = "a-" + uuid.uuid4().hex[:10]  # links the steps to this run in the activity log

        started_id = self._bus.publish(
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
                origin="alias", group_id=group_id,
            )
            # no_match: the step's command wasn't for anything you have (e.g. "dark mode"): a failure
            return plan_step.result.get("status") not in ("error", "denied", "no_match")

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
                log_activity=False,              # an undo is not something you chose to do
            )
            return undo.get("status") == "ok"

        plan = Plan(id=f"alias:{trigger}:{uuid.uuid4().hex[:8]}", steps=plan_steps)
        PlanExecutor(registry=self._registry, execution_callback=run_step,
                     rollback_callback=undo_step).execute(plan)

        results = [{"step": int(s.step_id), "tool": s.tool, "result": s.result, "status": s.status}
                   for s in plan_steps if s.result is not None]
        completed = sum(1 for s in plan_steps if s.status in ("done", "rolled_back", "no_rollback_needed",
                                                             "rollback_failed", "no_rollback_available"))

        def log_run(outcome):
            record("alias.run", {"trigger": trigger}, source=origin, tier=1, outcome=outcome,
                   origin_event=started_id, group_id=group_id, ts=started)

        if plan.status == "completed":
            log_run("ok")
            return {
                "status":          "ok",
                "message":         f"Alias '{trigger}' completed all {len(steps)} step(s).",
                "steps_completed": len(steps),
                "steps_total":     len(steps),
                "results":         results,
            }

        failed = next(s for s in plan_steps if s.status == "failed")
        log_run("denied" if failed.result.get("status") == "denied" else "error")
        reason = failed.result.get("reason", failed.result.get("message", "unknown error"))
        rollback_note = {
            "rolled_back":           "Earlier steps were rolled back.",
            "partially_rolled_back": "Some earlier steps could NOT be rolled back — check the results.",
            "failed":                "",
        }.get(plan.status, "")
        return {
            # a step that matched nothing makes the alias fail; it must not send the trigger to the model
            "status":          "error" if failed.result.get("status") == "no_match" else failed.result.get("status", "error"),
            "reason":          f"Alias '{trigger}' stopped at step {failed.step_id} ({failed.tool}): {reason}. {rollback_note}".strip(),
            "steps_completed": completed,
            "steps_total":     len(steps),
            "rollback":        plan.status,
            "results":         results,
        }
