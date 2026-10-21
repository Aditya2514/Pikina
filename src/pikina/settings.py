"""
settings.json in the data folder: one reader and one writer for every part of Pikina.

Writes are locked and atomic (temp file + os.replace), so two requests saving at
once can't lose each other's keys, and a reader never sees a half-written file.
A file that doesn't parse is never silently replaced by a smaller one.
"""
import json
import os
import threading

from pikina import paths

_lock = threading.Lock()
_cache: dict = {"key": None, "data": {}}


class SettingsError(Exception):
    """settings.json exists but isn't valid JSON; saving would wipe it."""


def _file():
    return paths.data_file(paths.SETTINGS)


def _load(strict: bool) -> dict:
    f = _file()
    try:
        st = f.stat()
    except OSError:
        return {}
    key = (str(f), st.st_mtime_ns, st.st_size)
    if _cache["key"] == key:
        return dict(_cache["data"])
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings.json must hold a JSON object")
    except (OSError, ValueError) as exc:
        if strict:
            raise SettingsError(f"{f} is not valid JSON ({exc}); fix or delete it first.") from exc
        return {}
    _cache["key"], _cache["data"] = key, data
    return dict(data)


def read() -> dict:
    """The whole settings dict; {} if the file is missing or unreadable."""
    return _load(strict=False)


def get(key: str, default=None):
    return read().get(key, default)


def update(changes: dict) -> dict:
    """Merge top-level keys into settings.json and return the new dict."""
    with _lock:
        current = _load(strict=True)
        current.update(changes)
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(tmp, f)
        _cache["key"] = None
        return dict(current)
