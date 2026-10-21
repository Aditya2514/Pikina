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
| Add a feature | A plugin folder — see **Writing a plugin** below. No core files change. |
| Add a core command | `src/pikina/registry/capabilities/` — one `<name>.json` manifest + one `<name>.py` with `run(params) -> dict`. |
| Touch anything OS-specific | `src/pikina/osbridge/` only. Nothing else may import `win32*` or call desktop tools. |
| Store something on disk | Use `pikina.paths.data_file(...)`. Never write next to the code or into the working directory. |
| Change the UI | `desktop/src/renderer/` |
| Add an HTTP endpoint | `create_app()` in `src/pikina/server.py` |

## Capability manifests

Every manifest declares `permission_level` (0–5), `supports_rollback` and, if it
can be undone, `rollback_action` + `rollback_params`. Level 4+, and Level 3
without rollback, always ask the user before running. Read-only capabilities
set `"read_only": true`. See `src/pikina/registry/manifest_schema.json`.

## Writing a plugin

A plugin is one folder:

```
my_plugin/
├── plugin.json            {"name": "my_plugin", "version": "0.1.0", "api_version": 1, "description": "..."}
└── capabilities/
    ├── hello_say.json     the manifest (tool name, permission level, typed phrases)
    └── hello_say.py       def run(params) -> dict
```

- **Built-in plugins** live in `src/pikina/plugins/` (see `devtools/` for a full example).
- **Your own plugins** go in `<data folder>/plugins/` and stay **off** until you list
  them in `<data folder>/settings.json`: `{"plugins_enabled": ["my_plugin"]}`.
  `pikina plugins` shows what loaded, what's off and what was skipped.
- A broken plugin (bad JSON, bad regex, no `run`, wrong `api_version`, or a tool name
  someone else already uses) is skipped as a whole and reported; Pikina still starts.

**Typed phrases** go in the manifest, so they work without Ollama:

```json
"patterns": [
  {"regex": "say\\s+hello(?:\\s+to\\s+(?P<name>.+))?$", "priority": 400},
  {"regex": "greet$", "priority": 400, "params": {"name": "you"}}
]
```

- Phrases are matched case-insensitively from the start of the command.
- Named groups become params (trimmed). `params` adds fixed values.
- Lower `priority` is tried first. Core phrases use 10–370; pick something above
  that unless your phrase must beat a core one (e.g. `what's on port N` uses 250 so
  it wins over the calendar's `what's on <date>` at 270).
- An optional `parse(params) -> params` in the `.py` file can clean values up
  (convert to `int`, split dates) before `run` sees them.

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
