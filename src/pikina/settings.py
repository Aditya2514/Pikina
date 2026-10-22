"""
settings.json in the data folder: one reader and one writer for every part of Pikina.

Writes are atomic (temp file + os.replace) and locked across threads AND processes
(the CLI and the server both save), so two saves can't lose each other's keys and
a reader never sees a half-written file. A file that doesn't parse is never
silently replaced: saving refuses until it's fixed, and read_checked() reports it
so privacy switches can fail closed (see pikina.activity.recorder).
"""
import json
import os
import sys
import threading
import time
from contextlib import contextmanager

from pikina import paths

_lock = threading.Lock()
_cache: dict = {"key": None, "data": {}}
_last_good: dict = {}   # settings path -> last dict that parsed, for fail-closed callers


class SettingsError(Exception):
    """settings.json exists but isn't valid JSON; saving would wipe it."""


def _file():
    return paths.data_file(paths.SETTINGS)


def _parse(f) -> dict:
    data = json.loads(f.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("settings.json must hold a JSON object")
    return data


def read_checked() -> tuple[dict, bool]:
    """
    (settings, ok). ok is False when settings.json exists but can't be read or parsed;
    the dict is then the last version that did parse in this process ({} if none).
    """
    f = _file()
    try:
        st = f.stat()
    except FileNotFoundError:
        return {}, True
    except OSError:
        return dict(_last_good.get(str(f), {})), False
    key = (str(f), st.st_mtime_ns, st.st_size)
    if _cache["key"] == key:
        return dict(_cache["data"]), True
    try:
        data = _parse(f)
    except (OSError, ValueError):
        return dict(_last_good.get(str(f), {})), False
    _cache["key"], _cache["data"] = key, data
    _last_good[str(f)] = data
    return dict(data), True


def read() -> dict:
    """The whole settings dict; {} if the file is missing or unreadable (see read_checked)."""
    data, ok = read_checked()
    return data if ok else {}


def get(key: str, default=None):
    return read().get(key, default)


@contextmanager
def _process_lock(f):
    """An advisory lock on settings.json.lock so the CLI and the server don't interleave saves."""
    lock_path = f.with_name(f.name + ".lock")
    handle = open(lock_path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            deadline = time.monotonic() + 5
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _replace(tmp, f) -> None:
    # On Windows a reader that has settings.json open makes os.replace fail for a moment.
    for attempt in range(10):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == 9:
                raise
            time.sleep(0.05)


def edit_json(f, mutate) -> dict:
    """
    Locked (threads and processes), atomic read-modify-write of a JSON object file.
    mutate(data) changes the dict in place. A file that exists but doesn't parse is
    never overwritten: SettingsError. Other write problems raise OSError.
    """
    f.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _process_lock(f):
        if f.exists():
            try:
                current = _parse(f)
            except (OSError, ValueError) as exc:
                raise SettingsError(f"{f} is not valid JSON ({exc}); fix or delete it first.") from exc
        else:
            current = {}
        mutate(current)
        tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
            _replace(tmp, f)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return current


def read_json(f) -> dict:
    """Read a JSON object file: {} if missing; SettingsError if it exists but doesn't parse."""
    if not f.exists():
        return {}
    try:
        return _parse(f)
    except (OSError, ValueError) as exc:
        raise SettingsError(f"{f} is not valid JSON ({exc}); fix or delete it first.") from exc


def update(changes: dict) -> dict:
    """Merge top-level keys into settings.json and return the new dict. Raises SettingsError or OSError."""
    f = _file()
    current = edit_json(f, lambda data: data.update(changes))
    _cache["key"] = None
    _last_good[str(f)] = dict(current)
    return dict(current)
