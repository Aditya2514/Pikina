"""
Plugins: a new feature is one folder, with no edits to Pikina's core.

    <plugin>/
        plugin.json          {"name", "version", "api_version", "description"}
        capabilities/        one <name>.json manifest + one <name>.py handler per command

Built-in plugins live in this package. Your own live in <data folder>/plugins/
and stay off until their name is listed in settings.json under "plugins_enabled".
"""
import json
from dataclasses import dataclass
from pathlib import Path

from pikina import paths

API_VERSION = 1
BUILTIN_DIR = Path(__file__).resolve().parent


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    path: Path
    source: str  # "builtin" or "user"

    @property
    def capabilities_dir(self) -> Path:
        return self.path / "capabilities"

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "description": self.description,
                "source": self.source, "path": str(self.path)}


def user_plugins_dir() -> Path:
    return paths.data_dir() / "plugins"


def enabled_user_plugins() -> set:
    try:
        settings = json.loads(paths.data_file(paths.SETTINGS).read_text(encoding="utf-8"))
        return set(settings.get("plugins_enabled", []))
    except (OSError, ValueError):
        return set()


def _read_plugin(folder: Path, source: str) -> Plugin:
    meta_file = folder / "plugin.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    for key in ("name", "version", "api_version"):
        if key not in meta:
            raise ValueError(f"plugin.json is missing '{key}'")
    if meta["api_version"] != API_VERSION:
        raise ValueError(f"needs plugin API {meta['api_version']}, this Pikina provides {API_VERSION}")
    if not (folder / "capabilities").is_dir():
        raise ValueError("has no capabilities/ folder")
    return Plugin(name=meta["name"], version=str(meta["version"]),
                  description=meta.get("description", ""), path=folder, source=source)


def discover() -> tuple[list[Plugin], list[dict], list[str]]:
    """
    Find plugins to load.
    Returns (plugins, errors, disabled): errors are {"plugin", "error"} dicts for
    folders that couldn't be read; disabled are user plugins not switched on.
    """
    plugins, errors, disabled, seen = [], [], [], set()
    enabled = enabled_user_plugins()
    sources = [(BUILTIN_DIR, "builtin"), (user_plugins_dir(), "user")]
    for base, source in sources:
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if (p / "plugin.json").is_file()):
            try:
                plugin = _read_plugin(folder, source)
            except Exception as exc:
                errors.append({"plugin": folder.name, "error": str(exc)})
                continue
            if plugin.name in seen:
                errors.append({"plugin": plugin.name, "error": "another plugin already uses this name"})
                continue
            if source == "user" and plugin.name not in enabled:
                disabled.append(plugin.name)
                continue
            seen.add(plugin.name)
            plugins.append(plugin)
    return plugins, errors, disabled
