# Pikina OS — User Manual & Technical Capabilities Guide (v0.4)

Welcome to the comprehensive user manual for **Pikina OS**, a personal operating system layer for Windows and Linux. This manual documents every feature, command phrasing, backend daemon, and safety mechanism built into the system as of Phase 4e.

---

## 🛠️ Core Capabilities & Command Registry

Pikina OS offers 19 distinct commands spanning file management, to-do lists, calendar integration, preference profiles, math calculations, and system utilities.

Commands are categorized by **Permission Levels**:
* **Level 0 (Low Risk):** Runs immediately.
* **Level 1 (Medium Risk):** Modifies settings or runs local executables.
* **Level 4+ (High Risk):** Triggers a native confirmation popup before running (`MessageBoxW` on Windows, `zenity`/`kdialog` on Linux). Level 3 actions without rollback ask too. If no dialog can be shown, the action is denied.

---

### 1. Application Controls (`app.open`)

Launches applications by friendly name, executable name or path. On Linux, `notepad`, `calculator`, `paint`, `explorer` and `terminal` map to whichever matching app is installed (gedit/Kate/…, GNOME Calculator/KCalc/…), and other names are looked up on `PATH` and in installed `.desktop` entries.

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
* **Syntax:** `mark task <text_or_id> as done`, `mark <text> as complete`, `complete <text_or_id>`
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
* **Syntax:** `what do I have this week`, `what's on <date>`
* **Examples:**
  * `what do I have this week`
  * `what's on 2026-07-20`

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
* **Syntax:** `add alias <trigger> -> <step_1> ; <step_2>` — every step must be a Tier 1 command (no Ollama phrasing).
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
* **Linux:** tries `loginctl lock-session`, `xdg-screensaver lock`, the desktop's ScreenSaver D-Bus API, `xflock4` and `dm-tool lock` in turn.

### 9. Clipboard History (`clipboard.history`)
* **Permission Level:** 0
* **Syntax:** `clipboard`, `clip history`, `what did I copy` (or the **CLIP** button)
* Shows what you copied in the last 7 days, newest first. Copied text is never run as a command.

### 10. Developer Tools (the built-in `devtools` plugin)

#### Doctor (`dev.doctor`)
* **Permission Level:** 0
* **Syntax:** `doctor`, `check my dev setup`, `doctor for ~/code/my-app`
* Checks Python, git, Node, `gh`, clipboard and dialog tools, Ollama and its model, the memory model,
  free disk space and disk encryption. With a folder it also checks the project's git branch and
  unpushed/behind commits, its virtualenv, `node_modules`, and which `.env` keys are missing
  (names only — values are never read out).

#### Ports (`dev.ports`)
* **Permission Level:** 0
* **Syntax:** `what's on port 3000`, `who is using port 5432`, `list ports`

#### Free a Port (`dev.free_port`)
* **Permission Level:** 3, no undo — **always asks first**
* **Syntax:** `free port 3000`, `kill whatever's on port 8080`
* Stops the program listening on that port. Refuses Pikina's own backend and other users' programs.

### 11. Activity Log (`activity.*`)
* `activity`, `activity this week`, `what did I do today`: what has been recorded, so you can check the data is clean (Permission Level 0).
* `pause activity` / `resume activity`: stop or restart recording, including after a restart (Level 1).
* `forget my activity`: delete everything recorded. **Always asks first** (Level 4).
* App tracking is opt-in, in Settings, and records app names only. What is and isn't recorded: [activity-log.md](activity-log.md).

### 12. Project Switcher (`project.*`, the built-in `workspace` plugin)
* `add project pikina at ~/code/pikina` (or `add project ~/code/pikina`): saves the folder.
  If the project has a dev command (`npm run dev`, `pnpm run dev`, `yarn dev`, `bun run dev`,
  `python manage.py runserver`), it is detected and stored.
* `work on pikina` (also `open project pikina`, `switch to pikina`) does these in order:
  1. switches to the project's workspace (Hyprland or Sway);
  2. opens the folder in VS Code;
  3. opens a terminal there and starts the dev server in it. When you stop the server with
     Ctrl+C, the terminal stays open.
  4. opens its apps and links;
  5. shows git status: branch, uncommitted files, and commits not pushed or behind (as of
     your last fetch).
* **The dev command runs code from the project, so Pikina asks you once.** It asks again
  if the command or the folder changes. If you say no, the terminal opens without it.
* `<name> mode` (e.g. `college mode`) opens a project. A project can have no folder at all,
  just apps and links. If you have no project by that name, the request goes to the local
  model as usual.
* `list projects` and `remove project <name>`. Removing a project only forgets the entry;
  your folder is never touched.
* Switching to a project never closes what another one opened.
* **Everything else lives in `projects.json`** in your data folder: workspace number,
  editor, terminal (`true`, a terminal name such as `"foot"`, or `false`), dev command,
  apps and links. Example:
  ```json
  {"projects": {
    "pikina":  {"path": "~/code/pikina", "workspace": 2, "dev": null},
    "college": {"workspace": 5, "editor": null, "terminal": false,
                "apps": ["obsidian"], "urls": ["https://onlinedegree.iitm.ac.in"]}
  }}
  ```
* Terminals Pikina knows: kitty, alacritty, foot, wezterm, ghostty, gnome-terminal,
  konsole, xfce4-terminal and xterm. It uses `$TERMINAL` first. On Windows it uses Windows
  Terminal or `cmd`.

### Plugins
`pikina plugins` lists loaded, switched-off and skipped plugins. Your own plugins go in the
`plugins/` folder inside your Pikina data folder and only load once their name is in
`settings.json` under `"plugins_enabled"`. How to write one: `CONTRIBUTING.md` → *Writing a plugin*.

---

## 🧠 System Architecture & Advanced Features

### 1. Two-Tier Command Routing

Pikina OS runs an automated fallback pipeline to ensure speed and local privacy:
* **Tier 1 (Deterministic Router):** Command strings are first checked against regex patterns. If matched, the capability runs directly, with no model call.
* **Tier 2 (Local LLM Fallback):** If no regex matches, the command falls back to local **Ollama** (model from `PIKINA_OLLAMA_MODEL`, default `llama3`). It translates the user phrasing into a structured tool call. The model is loaded when the backend starts and kept in memory, so the first command doesn't pay the 10–30 s load time. If Ollama isn't running, you get a "model offline" reply.
* **`no_match` Fallback:** If you ask something out-of-scope (e.g., "tell me a joke"), the model yields `no_match` and the router gracefully declines rather than crashing or hallucinating a wrong command.

### 2. Dual-Tier Memory System
* **Vector Store (`vector_memory.db`):** 
  * *Ephemeral Tier:* Captures recent clipboard contents and file creations/modifications.
  * *Permanent Tier:* Holds memories promoted from the ephemeral tier.
  * *Promotion Engine:* If an event in the Ephemeral Tier is corroborated $\ge 3$ times, it is promoted to the Permanent Tier.
  * *Forgetting Engine:* An hourly background sweep deletes ephemeral vectors older than 24 hours, promoting the corroborated ones first.
* **Knowledge Graph:** A NetworkX graph serialized to JSON. Context assembly reads it, but the app does not add facts to it yet.
* **Context Assembly:** Pulls structural facts and semantic memories, fits them into a token-budget (~2000 tokens), and feeds them to the LLM to ground its decisions.

### 3. Safety & AVL (Action Validation Layer)
No model output can run directly. Every LLM recommendation passes through the AVL:
* **Level Gating:** If the model claims a lower permission level than registered to execute a command, it is blocked.
* **Consent Gate:** If a command is Level 4+ (high risk) or Level 3+ non-reversible, execution is paused for a native confirmation dialog (`MessageBoxW` on Windows; `zenity`, `kdialog` or `yad` on Linux; a terminal prompt in the CLI). The same rule applies to typed commands, Ollama-proposed actions, alias steps and dashboard buttons.
* **Kill Switch:** The ⏻ button halts every action and stops the background daemons until Pikina is restarted.
* **Replay Store Audit:** All executions, holds, approvals, and denials are logged to `replay_store.sqlite` under the `validation.rejected` topic.

### 4. Transactions and Rollbacks
Alias chains run as transactions:
* If any step fails (or you decline its confirmation dialog), execution halts immediately.
* Completed steps are undone in reverse order: an added task is removed, an added event is deleted, a new alias is removed, a changed preference is restored. Read-only steps (lists, queries, calculations) are skipped.
* Some actions can't be undone (launching an app, locking the screen). Rollback stops there and the reply says it was only partial.
* Every step and rollback is audited in the event bus (`transaction.*` topics).

---

## 🕵️ Background Daemons

Pikina OS runs two silent background daemons:
1. **Clipboard Monitor:** On Windows it listens for `WM_CLIPBOARDUPDATE`; on Linux it checks `wl-paste` (Wayland) or `xclip`/`xsel` (X11) once a second. Content marked by password managers (`ExcludeClipboardContentFromMonitor` / `x-kde-passwordManagerHint`) is ignored, and a 10,000-character cap applies.
2. **File Watcher:** Monitors file creation, modification, and deletions across the workspace in real-time, logging activity directly to the Ephemeral Vector Store.
