"""
X11 focus listener via xprop (package xorg-xprop / x11-utils).

`xprop -root -spy _NET_ACTIVE_WINDOW` prints one line per active-window change
(the first line is the current window). For each new window id we run
`xprop -id <wid> WM_CLASS _NET_WM_PID _NET_WM_NAME`: app = the second WM_CLASS
string (the class), pid from _NET_WM_PID; _NET_WM_NAME (the title) is only used
for the private-window check. The spy child is restarted with backoff if it exits.
"""
from __future__ import annotations

import re
import subprocess
import threading

from pikina.osbridge.focus import FocusSource

SPY_CMD = ["xprop", "-root", "-spy", "_NET_ACTIVE_WINDOW"]
_QUERY_TIMEOUT = 2.0
_WID = re.compile(r"window id # (0x[0-9a-fA-F]+)")
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_PID = re.compile(r"=\s*(\d+)")
_NOTHING = object()  # "nothing focused"


def parse_spy_line(line: str):
    """Window id (int) for a spy line, _NOTHING for 0x0 / "not found", None to ignore the line."""
    m = _WID.search(line)
    if m:
        wid = int(m.group(1), 16)
        return wid if wid else _NOTHING
    if "not found" in line:
        return _NOTHING
    return None


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def parse_window_props(text: str) -> tuple[str | None, str | None, int | None]:
    """(app, title, pid) from `xprop -id <wid> WM_CLASS _NET_WM_PID _NET_WM_NAME` output."""
    app = title = pid = None
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if not sep:
            continue  # "<PROP>:  not found."
        if name.startswith("WM_CLASS"):
            strings = _QUOTED.findall(value)
            if strings:
                app = _unescape(strings[1] if len(strings) > 1 else strings[0]) or None
        elif name.startswith("_NET_WM_PID"):
            m = _PID.search(line)
            if m:
                pid = int(m.group(1))
        elif name.startswith("_NET_WM_NAME"):
            strings = _QUOTED.findall(value)
            if strings:
                title = _unescape(strings[0])
    return app, title, pid


class X11Source(FocusSource):
    backend = "x11"

    def __init__(self, on_focus, **options):
        super().__init__(on_focus, **options)
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    # -- stop ------------------------------------------------------------------
    def _interrupt(self) -> None:
        with self._proc_lock:
            proc = self._proc
        if proc is not None:
            self._kill(proc)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(2)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(1)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass

    # -- main loop -------------------------------------------------------------
    def _loop(self) -> None:
        backoff = self._backoff()
        while not self.stopping:
            proc = self._spawn()
            if proc is not None:
                try:
                    if self._follow(proc):
                        backoff.reset()
                except Exception:
                    pass  # any surprise = child lost; restart below
                finally:
                    self._kill(proc)
                    if proc.stdout is not None:
                        proc.stdout.close()
                    with self._proc_lock:
                        if self._proc is proc:
                            self._proc = None
            if self.stopping:
                break
            self._stop_event.wait(backoff.next())

    def _spawn(self) -> subprocess.Popen | None:
        try:
            proc = subprocess.Popen(SPY_CMD, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, text=True, bufsize=1,
                                    encoding="utf-8", errors="replace")
        except OSError:
            return None
        with self._proc_lock:
            if self.stopping:
                self._kill(proc)
                if proc.stdout is not None:
                    proc.stdout.close()
                return None
            self._proc = proc
        return proc

    def _follow(self, proc: subprocess.Popen) -> bool:
        """Read spy lines until the child exits or stop(); True if it produced any window line."""
        produced = False
        last = None
        for line in proc.stdout:
            if self.stopping:
                break
            wid = parse_spy_line(line)
            if wid is None:
                continue
            produced = True
            if wid == last:
                continue
            last = wid
            if wid is _NOTHING:
                self._emit(None, None, None)
                continue
            props = self._query(wid)
            if props is not None:  # None: the window vanished; the next spy line says what's next
                self._emit(*props)
        return produced

    def _query(self, wid: int):
        """(app, title, pid) of a window, or None if xprop failed."""
        try:
            out = subprocess.run(["xprop", "-id", hex(wid), "WM_CLASS", "_NET_WM_PID", "_NET_WM_NAME"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, timeout=_QUERY_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return parse_window_props(out.stdout.decode("utf-8", errors="replace"))
