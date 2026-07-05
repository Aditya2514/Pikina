# Pikina OS — Phase 1 + 1.5 Verification Checklist

## 1. Event Bus & Replay
- [x] Publish a real event manually and confirm it's queryable via `pikina.py replay` within seconds.
- [x] Kill the Python process mid-publish and restart. Confirm no corrupted/half-written row in the SQLite store (WAL mode / connection safety).
- [x] Confirm events older than retention window prune (tested with backdated rows).
- [x] Confirm Level 4+ events are retained after prune, while ordinary Level 0-1 events are removed.

## 2. Provenance & Orchestrator Hard Rule
- [x] Instruction-like file content is summarized/logged, never dispatched to a capability (`mcm.rejected`).
- [x] Instruction-like clipboard string tagged `UNTRUSTED_DATA` and not acted on.
- [x] Code trace/verification: `TRUSTED_COMMAND` is the only path that reaches `router.route()`.

## 3. Permission Engine & Gatekeeper
- [x] Trigger Level 4+ capability: `MessageBoxW` dialog appears and execution pauses until click.
- [x] Click "No"/Cancel: action does not execute, logged as rejected/cancelled event.
- [x] Level 0-1 capability executes with zero dialog.

## 4. Capability Registry Manifest Completeness
- [x] `open_app.json`: `estimated_cost`, `requires_network`, `supports_rollback`, `rollback_action`.
- [x] `lock_screen.json`: `estimated_cost`, `requires_network`, `supports_rollback`, `rollback_action`.
- [x] `find_file.json`: `estimated_cost`, `requires_network`, `supports_rollback`, `rollback_action`.

## 5. `find_file` Performance Bug
- [x] Scope `find_file` to sensible roots (Desktop, Documents, Downloads, or specified root) with max_depth limit & fast return (<0.005s).
- [x] Ensure capability itself is fixed at root cause.

## 6. Action Validation Layer & Failure Classification
- [x] Malformed proposed action returns typed `FailureClass`.
- [x] Failure classes (Validation, Permission, Recoverable, Infrastructure, Unclassified) verified.

## 7. Resource Governor & Telemetry
- [x] `pikina.py telemetry` matches system values without drive scan timeout.
- [x] Cost function reads manifest `estimated_cost` / `requires_network`.

## 8. Frontend — Quick Panel
- [x] Hotkey (`Ctrl+Shift+Space`) summons/dismisses panel.
- [x] Typed command matches CLI path and logs identical event envelope.
- [x] Quick grid icon buttons (mic, clip, tasks, snip, log) wired to active feedback/capabilities.
- [x] Kill-switch button visually distinct and halts router/daemons.

## 9. Frontend — Dashboard Restraint Check
- [x] Metric cards & deadline list high-contrast and legible; glow reserved for Arc Reactor.
- [x] Telemetry & weather live polling verified.

## 10. Zero-AI Constraint
- [x] Fully offline/zero-Ollama operation verified for all Tier 1 commands and event logging.

## 11. Process & Exit Condition
- [x] All verification items checked and documented.
