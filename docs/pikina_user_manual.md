# Pikina OS — User Manual & Technical Capabilities Guide (Phase 4e)

Welcome to the comprehensive user manual for **Pikina OS**, a personal operating system layer on Windows. This manual documents every feature, command phrasing, backend daemon, and safety mechanism built into the system as of Phase 4e.

---

## 🛠️ Core Capabilities & Command Registry

Pikina OS offers 19 distinct commands spanning file management, to-do lists, calendar integration, preference profiles, math calculations, and system utilities.

Commands are categorized by **Permission Levels**:
* **Level 0 (Low Risk):** Runs immediately.
* **Level 1 (Medium Risk):** Modifies settings or runs local executables.
* **Level 4+ (High Risk):** Triggers a native Windows confirmation popup before running.

---

### 1. Application Controls (`app.open`)

Launches Windows applications by executable name or path.

* **Permission Level:** 1
* **Command Syntax:**
  * `open <app_name>`
  * `start <app_name>`
  * `launch <app_name>`
* **Examples:**
  * `open notepad`
  * `start calc`
  * `launch vscode`
  * `can you launch something to write some text` (Ollama routes to `notepad`)

---

### 2. File Operations (`fs.find_file`)

Searches the local filesystem for files matching a name pattern.

* **Permission Level:** 0
* **Command Syntax:**
  * `find file <name>`
  * `look up files with <name> in the name`
  * `search for <name>`
* **Examples:**
  * `find file resume.pdf`
  * `search for *.csv`
  * `look up files with document in the name`

---

### 3. Task Management (`todo.*`)

An integrated, persistent to-do list categorizable by urgency buckets (`today`, `tomorrow`, `this_week`, `backlog`).

#### Add Task (`todo.add`)
* **Permission Level:** 0
* **Syntax:** `add todo <text>`, `todo <text>`, `add a task to <text>`
* **Examples:**
  * `add todo call the project sponsor`
  * `add a task to buy groceries by tonight`

#### Complete Task (`todo.complete`)
* **Permission Level:** 0
* **Syntax:** `mark task <text_or_id> as done`, `complete <text_or_id>`
* **Examples:**
  * `mark task buy groceries as done`
  * `complete c693836e`

#### List Tasks (`todo.list`)
* **Permission Level:** 0
* **Syntax:** `list todos`, `list done todos`, `list all tasks`
* **Examples:**
  * `list todos` (lists pending tasks)
  * `show done tasks`
  * `list all tasks`

#### Remove Task (`todo.remove`)
* **Permission Level:** 1
* **Syntax:** `remove todo <text_or_id>`, `delete task <text_or_id>`
* **Examples:**
  * `remove todo call the project sponsor`
  * `delete task c693836e`

---

### 4. Calendar Management (`calendar.*`)

A calendar system for recording, querying, and updating personal appointments. Supports importing from standard `.ics` exports.

#### Add Event (`calendar.add_event`)
* **Permission Level:** 0
* **Syntax:** `add event <title> on <date> at <time>`
* **Examples:**
  * `add event project check-in on 2026-07-20 at 14:00`
  * `add meeting sync with design team`

#### Query Calendar (`calendar.query`)
* **Permission Level:** 0
* **Syntax:** `what do I have this week`, `what is on <date>`
* **Examples:**
  * `what do I have this week`
  * `what is on 2026-07-20`

#### Update Event (`calendar.update_event`)
* **Permission Level:** 0
* **Syntax:** (Automatically handled via natural language routing)
* **Example:**
  * `change check-in time to 15:00`

#### Remove Event (`calendar.remove_event`)
* **Permission Level:** 1
* **Syntax:** `remove event <title>`, `delete meeting <title>`
* **Examples:**
  * `remove event project check-in`

#### Import Calendar (`calendar.import_ics`)
* **Permission Level:** 0
* **Syntax:** `import ics <file_path>`
* **Example:**
  * `import ics C:\Users\user\Downloads\invite.ics`

---

### 5. Multi-Step Aliases (`alias.*`)

Define a sequence of steps under a single keyword trigger.

#### Add Alias (`alias.add`)
* **Permission Level:** 0
* **Syntax:** `add alias <trigger> -> <step_1> ; <step_2>`
* **Example:**
  * `add alias morning -> start the stopwatch ; open notepad`

#### List Aliases (`alias.list`)
* **Permission Level:** 0
* **Syntax:** `list aliases`, `show aliases`

#### Remove Alias (`alias.remove`)
* **Permission Level:** 1
* **Syntax:** `remove alias <trigger>`
* **Example:**
  * `remove alias morning`

---

### 6. Preferences & Profile Profile (`prefs.update`)

Updates parameters governing conversational behavior, verbosity, and system preferences.

* **Permission Level:** 0
* **Command Syntax & Examples:**
  * `call me Aditya` (preferred name)
  * `be formal` / `be casual` (formality)
  * `be concise` / `be detailed` (verbosity)
  * `quiet hours from 22:00 to 08:00` (quiet hours)

---

### 7. Memory Recall (`memory.recall`)

Recall stored facts and events from semantic vector memory.

* **Permission Level:** 0
* **Syntax:** `recall <query>`
* **Examples:**
  * `recall what I did yesterday`
  * `where did I save my project brief`

---

### 8. System Utilities (`utility.*` & `system.*`)

#### Math Calculator (`utility.calculate`)
* **Permission Level:** 0
* **Syntax:** `calculate <expression>`, `calc <expression>`
* **Example:**
  * `calculate (45 * 2) + 10`

#### Stopwatch (`utility.stopwatch`)
* **Permission Level:** 0
* **Syntax:** `start stopwatch`, `pause stopwatch`, `reset stopwatch`

#### Lock Screen (`system.lock_screen`)
* **Permission Level:** 1
* **Syntax:** `lock screen`, `lock workstation`, `lock pc`

---

## 🧠 System Architecture & Advanced Features

### 1. Two-Tier Command Routing

Pikina OS runs an automated fallback pipeline to ensure speed and local privacy:
* **Tier 1 (Deterministic Router):** Command strings are first checked against regex patterns. If matched, they run instantly (sub-millisecond latency) without invoking AI.
* **Tier 2 (Local LLM Fallback):** If no regex matches, the command falls back to local **Ollama (`llama3`)**. It translates the user phrasing into a structured tool call.
* **`no_match` Fallback:** If you ask something out-of-scope (e.g., "tell me a joke"), the model yields `no_match` and the router gracefully declines rather than crashing or hallucinating a wrong command.

### 2. Dual-Tier Memory System
* **Vector Store (`vector_memory.db`):** 
  * *Ephemeral Tier:* Captures short-term files modifications, recent clipboard contents, and system logs.
  * *Permanent Tier:* Holds explicit declarations, settings, and promoted memories.
  * *Promotion Engine:* If an event in the Ephemeral Tier is corroborated $\ge 3$ times, it is promoted to the Permanent Tier.
  * *Forgetting Engine:* Automatically runs background sweeps to decay and delete old ephemeral vectors.
* **Knowledge Graph:** Maps structural facts using a NetworkX graph serialized to JSON.
* **Context Assembly:** Pulls structural facts and semantic memories, fits them into a token-budget (~2000 tokens), and feeds them to the LLM to ground its decisions.

### 3. Safety & AVL (Action Validation Layer)
No model output can run directly. Every LLM recommendation passes through the AVL:
* **Level Gating:** If the model claims a lower permission level than registered to execute a command, it is blocked.
* **Consent Gate:** If a command is Level 4+ (high risk) or Level 3+ non-reversible, execution is paused and routes through a native Win32 `MessageBoxW` dialog asking for human confirmation.
* **Replay Store Audit:** All executions, holds, approvals, and denials are logged to `replay_store.sqlite` under the `validation.rejected` topic.

### 4. Transactions and Rollbacks
When executing multi-step plans (e.g. alias chains):
* If any step fails, execution halts immediately.
* A transactional rollback manager triggers `rollback_action` callbacks in reverse order (LIFO), returning the system safely to its original state.
* Rollback metrics and statuses are fully audited in the event bus.

---

## 🕵️ Background Daemons

Pikina OS runs two silent Windows daemons:
1. **Clipboard Monitor:** Listens via Windows event messages (`WM_CLIPBOARDUPDATE`) for copies. Automatically ignores content marked by password managers (`ExcludeClipboardContentFromMonitor`) and enforces a 10,000-character cap.
2. **File Watcher:** Monitors file creation, modification, and deletions across the workspace in real-time, logging activity directly to the Ephemeral Vector Store.
