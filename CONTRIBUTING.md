# Contributing to Pikina OS

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # add ",memory" for semantic memory
pytest                             # ~2 s, no Ollama or model downloads needed

cd desktop && npm install && npm start
```

The desktop app looks for `pikina-server` in `.venv/`, `venv/` or on `PATH` and
starts it. Set `PIKINA_DATA_DIR=/tmp/pikina-dev` to keep development data away
from your real data folder.

## Where things go

| You want to… | Edit |
|:---|:---|
| Add a command | `src/pikina/registry/capabilities/` — one `<name>.json` manifest + one `<name>.py` with `run(params) -> dict`. Add a regex in `src/pikina/router/tier1.py` if it should work without Ollama. |
| Touch anything OS-specific | `src/pikina/osbridge/` only. Nothing else may import `win32*` or call desktop tools. |
| Store something on disk | Use `pikina.paths.data_file(...)`. Never write next to the code or into the working directory. |
| Change the UI | `desktop/src/renderer/` |
| Add an HTTP endpoint | `create_app()` in `src/pikina/server.py` |

## Capability manifests

Every manifest declares `permission_level` (0–5), `supports_rollback` and, if it
can be undone, `rollback_action` + `rollback_params`. Level 4+, and Level 3
without rollback, always ask the user before running. Read-only capabilities
set `"read_only": true`. See `src/pikina/registry/manifest_schema.json`.

## Rules that keep Pikina safe

- Text from files, the clipboard, web pages or a model is `UNTRUSTED_DATA` and
  must never trigger an action directly.
- Run capabilities through `pikina.registry.executor.execute_action()` so the
  consent gate, kill switch and audit log apply.
- Tests must not touch real user data — the fixtures in `tests/conftest.py`
  give each test its own data folder.

## Commits

`phase<N>[.<sub>]: <what changed>` for roadmap work, or `fix:` / `docs:` / `chore:`.
CI runs the tests on Linux and Windows for every push.
