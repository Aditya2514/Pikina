# Changelog

All notable changes to Pikina OS. Versions follow [Semantic Versioning](https://semver.org/).

## [0.4.0] — Unreleased

### Changed
- **Project layout.** Python code is now an installable package in `src/pikina/`
  (`pip install -e .`), with `pikina` (terminal) and `pikina-server` (backend)
  commands. The Electron app moved from `frontend/` to `desktop/`.
- **User data location.** Everything Pikina saves now lives in a per-user folder
  (`%LOCALAPPDATA%\Pikina`, `~/.local/share/pikina`), not inside the code.
  Data from older versions is copied over automatically on first start.
- The desktop app starts and stops the backend itself.
- Semantic memory is an optional extra: `pip install "pikina[memory]"`.
- Dependencies are declared in `pyproject.toml` (`requirements.txt` removed).

### Added
- **Activity log (learning layer, Phase 1).** Every action run through Pikina is logged to
  `activity.db` as a normalised, privacy-safe key (`app.open:code`, `fs.find_file:*.pdf`), with
  its source (typed, quick panel, CLI, alias, dashboard click), outcome, local hour and weekday,
  and a session id. Typed text, to-do text, titles, file names, paths and alias names are never
  stored. Past commands are rebuilt once from the replay log. Rows are deleted after 90 days.
  `activity`, `pause activity` / `resume activity` and `forget my activity` (asks first);
  `GET /api/activity`, `/api/activity/export`, `POST /api/activity/settings`; CLI
  `activity export <file>` and `activity backfill`. See `docs/activity-log.md`.
- **Opt-in active-app tracker:** off by default. Records app names only, never window titles.
  Works on Hyprland (event socket), Sway/i3 (IPC), X11 (`xprop`) and Windows. Password
  managers, unlock prompts and English Firefox private windows are recorded as `excluded`.
  It has a REC/PAUSED pill in the dashboard title bar, switches in Settings and a tray item.
  It stops with the kill switch.
- `doctor` reports whether app tracking can run in your session.
- **Plugins.** A feature is now one folder with a `plugin.json` and its capabilities.
  Built-in plugins ship in `src/pikina/plugins/`; your own go in `<data folder>/plugins/`
  and stay off until listed in `settings.json` → `plugins_enabled`. Broken plugins are
  skipped and reported, never crash Pikina. `pikina plugins` and `GET /api/plugins` list them.
- **Typed phrases live in each capability's manifest** (`patterns`), replacing the
  hard-coded route table in the Tier 1 router.
- **devtools plugin:** `doctor` (checks Python, git, Node, Ollama, memory model, disk,
  disk encryption, and — with `doctor for <folder>` — a project's git state, venv,
  node_modules and missing `.env` keys, by name only), `what's on port N`, `list ports`,
  and `free port N` (always asks first; won't touch Pikina itself or other users' programs).
- `clipboard` / `what did I copy` shows recent clipboard history.
- Linux support (consent dialogs, app launching, screen lock, clipboard daemon).
- Tier 2 (Ollama) fallback in the desktop app, with model warm-up and JSON mode.
- Alias chains run as transactions and roll back on failure.
- `add alias <name> -> step ; step`, `launch <app>`, `import ics <file>`.
- Working kill switch; token-protected local API.
- Interactive 3D dashboard background (toggle in Settings).
- pytest suite and GitHub Actions CI on Linux and Windows.

### Fixed
- `open vs code` failed on Arch, whose package installs `code-oss`.
- Saving settings could lose keys when two saves overlapped, and a hand-edit with a typo was
  silently replaced. Settings are now written atomically under a lock, and a broken file is
  never overwritten.
- The dashboard's CLIP button did nothing; it now shows clipboard history.
- Replies without a `message` were shown as raw JSON in the dashboard and quick panel.
- Calculator returned wrong results for `^` and broke on `max`/`exp`; it no longer uses `eval`.
- `find_file` searched only the last folder; name searches only matched prefixes.
- Weekday due dates ("by friday") resolved to the past.
- Several dependencies were missing from the requirements.

### Removed
- Empty placeholder folders and `startup_debug.vbs`.

## [0.3.x] and earlier

Phases 1–4e, developed July–August 2026. See `docs/dev-log/`.
