# Calendar Integration & Extension Plan

This document outlines the architectural plan to link the **Chronology (Calendar) system** with other core subsystems of Pikina OS (Tasks, Memory/Context Assembly, Profiles/Quiet Hours, and the upcoming Multi-Agent Planner).

---

## 1. Subsystem Integrations

```
                     ┌───────────────────────────┐
                     │ Multi-Agent Planner (4f)  │
                     │  - Reads calendar slots   │
                     │  - Blocks out execution   │
                     └─────────────┬─────────────┘
                                   │
                                   ▼
┌─────────────────┐  Query  ┌──────────────┐  Prune/Sweep  ┌──────────────────┐
│ Context Assembly│ ◄───────┤   CALENDAR   ├──────────────►│ Forgetting Engine│
│ (Memory Prompts)│         │   DATABASE   │               │ (Purge old clips)│
└─────────────────┘         └──────┬───────┘               └──────────────────┘
                                   │
                                   ▼
                     ┌─────────────┴─────────────┐
                     │    To-Do Subsystem        │
                     │  - Maps tasks with dates  │
                     │  - Renders tasks on grid  │
                     └───────────────────────────┘
```

---

### A. To-Do Subsystem Integration (Dual-Feed Grid)
* **Goal:** Render to-do tasks with explicit `due_date` values directly on the calendar grid alongside calendar events, mimicking Google Calendar's "Tasks" layer.
* **Mechanism:**
  * Modify `GET /api/calendar` in `backend_server.py` to retrieve pending items from `todo_store` with non-empty `due_date` values.
  * Append them to the returned event array with a custom class `fc-event-todo-item` and a checkbox emoji prefix (e.g. `☐ Buy groceries`).
  * If a user completes a task, the calendar grid refetches and updates the status to a checked emoji (`☑ Buy groceries`).

---

### B. Context Assembly & Recall Integration (Historical Anchors)
* **Goal:** Allow the LLM to access your schedule when answering open-ended queries (e.g. "what was I working on last Friday?").
* **Mechanism:**
  * Update `assemble_context` in `core/context/retrieval.py` to query `CalendarStore.query_range` for the preceding 3 days and next 3 days relative to current local time.
  * Inject these events into the assembled prompt under a new section:
    ```text
    Recent and upcoming schedule:
    - 2026-07-20 14:00 [work]: Sync with Design Team
    - 2026-07-21 09:00 [holiday]: Public Holiday
    ```

---

### C. Multi-Agent Task Planner (Phase 4f)
* **Goal:** The upcoming Task Planner needs to schedule actions into free time blocks on the user's calendar.
* **Mechanism:**
  * **[NEW] Capability `calendar.query_free_busy`:** Evaluates a target time range (e.g. "tomorrow between 9am and 5pm") and returns list of open slots.
  * **[NEW] Capability `calendar.schedule_execution_block`:** Creates a blocked calendar event representing a scheduled agent task execution, preventing overlapping schedules.

---

### D. Preferences Profile & Quiet Hours Validation (AVL Interceptor)
* **Goal:** Prevent scheduling work events during user-declared quiet hours.
* **Mechanism:**
  * Update `validate_model_action` in `core/validation/schema_check.py` to intercept `calendar.add_event` and `calendar.update_event`.
  * If the proposed time conflicts with `prefs.update`'s `quiet_hours` list (e.g. event at 23:00 when quiet hours are 22:00-08:00) and category is `work`/`college`, the AVL validator raises a validation warning or holds for confirmation.

---

## 2. SQLite Database Schema Readiness

The current table `calendar_events` already possesses sufficient fields to support this integration:
* `id` (primary key UUID)
* `title` (text)
* `date` (ISO date)
* `time` (HH:MM)
* `type` (category mapping)
* `source` (`user` vs planner/system)
* `recurring` (`none`, `daily`, `weekly`, `monthly`, `yearly`)
* `description` (long text)

*No database migration is required to begin implementing the integrations.*
