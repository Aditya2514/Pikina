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
- Linux support (consent dialogs, app launching, screen lock, clipboard daemon).
- Tier 2 (Ollama) fallback in the desktop app, with model warm-up and JSON mode.
- Alias chains run as transactions and roll back on failure.
- `add alias <name> -> step ; step`, `launch <app>`, `import ics <file>`.
- Working kill switch; token-protected local API.
- Interactive 3D dashboard background (toggle in Settings).
- pytest suite and GitHub Actions CI on Linux and Windows.

### Fixed
- Calculator returned wrong results for `^` and broke on `max`/`exp`; it no longer uses `eval`.
- `find_file` searched only the last folder; name searches only matched prefixes.
- Weekday due dates ("by friday") resolved to the past.
- Several dependencies were missing from the requirements.

### Removed
- Empty placeholder folders and `startup_debug.vbs`.

## [0.3.x] and earlier

Phases 1–4e, developed July–August 2026. See `docs/dev-log/`.
