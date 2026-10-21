"""
Focus listeners (pikina.osbridge.focus): backend detection, the private-window check,
and every backend against a fake: a fake Hyprland / sway compositor on AF_UNIX
sockets, a fake `xprop` on PATH, and injected Win32 calls (plus a smoke test of the
real ctypes path on Windows).

Throughout, window titles must never reach on_focus, stdout/stderr or the source object
(including source.failure).
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from pikina.osbridge import focus
from pikina.osbridge.focus import FocusSource, detect_backend, is_private_window, make_source
from pikina.osbridge.focus import hyprland, sway, x11, windows

needs_unix = pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX sockets / sh scripts")

# Every window title used below; none of them may leak anywhere.
TITLES = ("bank.kdbx", "main.py", "other.py", "Mozilla Firefox", "secret-project", "htop", "Task Manager",
          "lengthy-query", "Display is 42")
DEAD_PID = 2**31 - 1  # above any pid_max
# Long enough that only a full read keeps the "Private Browsing" suffix (Hyprland and the
# old Windows code both cut at 1024).
LONG_TITLE = "lengthy-query " * 80
LONG_PRIVATE = LONG_TITLE + "— Mozilla Firefox Private Browsing"


class Recorder:
    """on_focus stand-in: records calls and the thread they arrived on."""

    def __init__(self):
        self.calls = []
        self.threads = set()
        self._cond = threading.Condition()

    def __call__(self, app, private, pid):
        with self._cond:
            self.calls.append((app, private, pid))
            self.threads.add(threading.current_thread().name)
            self._cond.notify_all()

    def wait_for(self, n, timeout=3.0) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: len(self.calls) >= n, timeout)


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def stop_quickly(src: FocusSource, limit=2.0):
    t0 = time.monotonic()
    src.stop()
    src.join(limit + 0.5)
    elapsed = time.monotonic() - t0
    assert not src.is_alive(), "source did not stop"
    assert elapsed < limit, f"stop took {elapsed:.2f}s"


def assert_no_titles(src, capsys, rec):
    out = capsys.readouterr()
    blob = out.out + out.err + repr(vars(src)) + repr(rec.calls)
    for title in TITLES:
        assert title not in blob


@pytest.fixture
def short_dir():
    """AF_UNIX paths must stay under ~107 bytes, so use /tmp directly."""
    d = tempfile.mkdtemp(dir="/tmp", prefix="pkf")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def xprop_bin(tmp_path):
    """A directory with an executable (do-nothing) xprop, for detect_backend."""
    d = tmp_path / "xbin"
    d.mkdir()
    exe = d / "xprop"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    return str(d)


def dead_socket(path: Path) -> None:
    """A socket file nobody listens on (what a crashed / exited compositor leaves behind)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()


def write_lock(runtime: Path, sig: str, pid: int, wayland="wayland-1", mtime=None) -> Path:
    d = runtime / "hypr" / sig
    d.mkdir(parents=True, exist_ok=True)
    lock = d / "hyprland.lock"
    lock.write_text(f"{pid}\n{wayland}\n")
    if mtime is not None:
        os.utime(lock, (mtime, mtime))
    return d


# --------------------------------------------------------------------------- detect_backend
def test_detect_windows_first():
    assert detect_backend({}, "win32") == ("windows", None)
    assert detect_backend({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}, "win32") == ("windows", None)


def test_detect_hyprland_by_signature_even_with_display():
    env = {"HYPRLAND_INSTANCE_SIGNATURE": "abc", "WAYLAND_DISPLAY": "wayland-1",
           "XDG_SESSION_TYPE": "wayland", "DISPLAY": ":1"}
    assert detect_backend(env, "linux") == ("hyprland", None)
    assert detect_backend({"HYPRLAND_INSTANCE_SIGNATURE": "abc", "DISPLAY": ":1"}, "linux") == ("hyprland", None)


def test_detect_hyprland_by_live_lock(tmp_path):
    write_lock(tmp_path, "sig", os.getpid())
    env = {"XDG_RUNTIME_DIR": str(tmp_path), "WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":1"}
    assert detect_backend(env, "linux") == ("hyprland", None)


def test_detect_ignores_dead_or_foreign_hyprland_lock(tmp_path):
    write_lock(tmp_path, "dead", DEAD_PID)
    write_lock(tmp_path, "other", os.getpid(), wayland="wayland-7")
    backend, reason = detect_backend({"XDG_RUNTIME_DIR": str(tmp_path), "WAYLAND_DISPLAY": "wayland-1"}, "linux")
    assert backend is None and "Wayland" in reason


def test_detect_sway_and_i3():
    assert detect_backend({"SWAYSOCK": "/run/user/1000/sway-ipc.sock", "WAYLAND_DISPLAY": "wayland-1"},
                          "linux") == ("sway", None)
    assert detect_backend({"I3SOCK": "/run/user/1000/i3/ipc-socket.1", "DISPLAY": ":0"}, "linux") == ("sway", None)


@needs_unix
def test_detect_other_wayland_is_unsupported_and_never_x11(xprop_bin):
    # GNOME/KDE on Wayland: DISPLAY (XWayland) and xprop exist, but X11 would only see X11 windows.
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland",
           "XDG_CURRENT_DESKTOP": "GNOME", "PATH": xprop_bin}
    backend, reason = detect_backend(env, "linux")
    assert backend is None
    assert "Wayland" in reason and "not supported" in reason
    backend, reason = detect_backend({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0", "PATH": xprop_bin}, "linux")
    assert backend is None and "Wayland" in reason


@needs_unix
def test_detect_x11_needs_xprop(xprop_bin, tmp_path):
    assert detect_backend({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11", "PATH": xprop_bin}, "linux") == ("x11", None)
    empty = tmp_path / "empty"
    empty.mkdir()
    backend, reason = detect_backend({"DISPLAY": ":0", "PATH": str(empty)}, "linux")
    assert backend is None and "install xorg-xprop" in reason
    backend, reason = detect_backend({"DISPLAY": ":0"}, "linux")
    assert backend is None and "install xorg-xprop" in reason


def test_detect_no_graphical_session():
    assert detect_backend({}, "linux") == (None, "no graphical session")
    assert detect_backend({"XDG_SESSION_TYPE": "tty", "PATH": "/usr/bin"}, "linux") == (None, "no graphical session")
    backend, reason = detect_backend({}, "darwin")
    assert backend is None and reason


@needs_unix
def test_detect_defaults_to_process_env(monkeypatch, xprop_bin):
    for var in ("HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR", "SWAYSOCK", "I3SOCK",
                "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("PATH", xprop_bin)
    assert detect_backend() == ("x11", None)


# --------------------------------------------------------------------------- is_private_window
@pytest.mark.parametrize("app,title,expected", [
    ("firefox", "Bank — Mozilla Firefox Private Browsing", True),
    ("firefox", "Bank - Mozilla Firefox (Private Browsing)", True),          # older Firefox
    ("org.mozilla.firefox", "Mozilla Firefox Private Browsing", True),
    ("librewolf", "Bank — LibreWolf Private Browsing", True),
    ("firefox.exe", "Bank — Mozilla Firefox Private Browsing  ", True),
    ("firefox", "Bank — Mozilla Firefox", False),
    ("firefox", "Private Browsing tips — Mozilla Firefox", False),            # only the suffix counts
    ("msedge.exe", "Bank and 2 more pages - [InPrivate] - Microsoft Edge", True),
    ("Tor Browser", "Tor Browser", True),                                    # always private
    ("google-chrome", "Bank - Google Chrome", False),                        # incognito undetectable
    ("Brave-browser", "New Tab - Brave", False),
    ("Code", "main.py - Visual Studio Code", False),
    ("firefox", None, False),
    (None, None, False),
    (None, "", False),
])
def test_is_private_window(app, title, expected):
    assert is_private_window(app, title) is expected


def test_chromium_limitation_is_documented():
    assert "Chromium" in is_private_window.__doc__ and "can NOT" in is_private_window.__doc__


# --------------------------------------------------------------------------- make_source / base
def test_make_source_returns_unstarted_daemon_threads():
    expected = {"hyprland": hyprland.HyprlandSource, "sway": sway.SwaySource,
                "x11": x11.X11Source, "windows": windows.WindowsSource}
    for backend, cls in expected.items():
        src = make_source(backend, Recorder())
        assert type(src) is cls and isinstance(src, FocusSource)
        assert src.daemon and not src.is_alive()
        src.stop()
        src.stop()  # safe twice, and before start
    with pytest.raises(ValueError):
        make_source("gnome", Recorder())


def test_importing_backends_starts_no_threads():
    code = ("import threading\n"
            "import pikina.osbridge.focus.windows, pikina.osbridge.focus.hyprland\n"
            "import pikina.osbridge.focus.sway, pikina.osbridge.focus.x11\n"
            "assert threading.active_count() == 1, threading.enumerate()\n")
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)


def test_callback_errors_dont_kill_the_source(capsys):
    src = make_source("windows", lambda app, private, pid: (_ for _ in ()).throw(RuntimeError("bank.kdbx")))
    src._foreground_window = lambda: 1
    src._window_pid = lambda hwnd: 10
    src._process_name = lambda pid: "KeePassXC.exe"
    src._window_title = lambda hwnd: "bank.kdbx - KeePassXC"
    src.poll_once()  # must not raise
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "bank.kdbx" not in out


@pytest.mark.parametrize("app,expected", [
    ("firefox", True), ("org.mozilla.firefox", True), ("firefox-esr", True), ("firefoxdeveloperedition", True),
    ("librewolf", True), ("io.gitlab.librewolf-community", True), ("floorp", True), ("one.ablaze.floorp", True),
    ("waterfox", True), ("zen", True), ("zen-alpha", True), ("app.zen_browser.zen", True),
    ("Mullvad Browser", True), ("mullvadbrowser", True), ("Tor Browser", True), ("torbrowser", True),
    ("Code", False), ("gnome-text-editor", False), ("org.gnome.SystemMonitor", False), ("Navigator", False),
    ("zenity", False), ("chromium", False), (None, False), ("", False),
])
def test_is_firefox_family(app, expected):
    assert focus.is_firefox_family(app) is expected


def test_run_survives_loop_errors_reports_them_and_recovers(capsys):
    gate = threading.Event()

    class Flaky(FocusSource):
        backend = "flaky"
        runs = 0

        def _loop(self):
            self.runs += 1
            if self.runs == 1:
                self._emit("Code", "main.py - Visual Studio Code", 7)
                raise RuntimeError("bank.kdbx")         # message must never be shown
            if self.runs == 2:
                raise OSError("bank.kdbx")
            gate.wait(3)
            self.failure = None                         # "connected" again
            self._emit("Code", "main.py - Visual Studio Code", 7)
            self._stop_event.wait()

    rec = Recorder()
    src = Flaky(rec, initial_backoff=0.01)
    assert src.failure is None
    src.start()
    try:
        assert wait_until(lambda: src.runs == 3)
        assert src.is_alive()
        assert src.failure == "listener error (OSError), retrying"
        assert rec.calls == [("Code", False, 7), (None, False, None)]   # nothing current while broken
        gate.set()
        assert rec.wait_for(3)
        assert rec.calls[2] == ("Code", False, 7)
        assert src.failure is None
    finally:
        gate.set()
        stop_quickly(src)
    out = capsys.readouterr().out
    assert out.count("flaky listener error (RuntimeError), retrying") == 1
    assert out.count("flaky listener error (OSError), retrying") == 1
    blob = out + repr(vars(src)) + repr(rec.calls)
    assert "bank.kdbx" not in blob and "main.py" not in blob


# --------------------------------------------------------------------------- Hyprland
class FakeHyprland:
    """Serves .socket.sock (one-shot j/activewindow) and pushes .socket2.sock lines."""

    def __init__(self, runtime: Path, sig="pk", lock=True):
        self.dir = runtime / "hypr" / sig
        self.dir.mkdir(parents=True)
        if lock:  # a live instance: our own pid is certainly alive
            (self.dir / "hyprland.lock").write_text(f"{os.getpid()}\nwayland-1\n")
        self.active = {}
        self.requests = []
        self.fail_requests = False   # close .socket.sock connections without a reply
        self.hang_requests = False   # accept .socket.sock connections, never answer (wedged)
        self.held = []
        self.event_clients = []
        self._closing = False
        self._servers = [self._listen(self.dir / ".socket.sock", self._serve_request),
                         self._listen(self.dir / ".socket2.sock", self.event_clients.append)]

    def _listen(self, path, handler):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        srv.listen(8)
        srv.settimeout(0.05)
        threading.Thread(target=self._accept_loop, args=(srv, handler), daemon=True).start()
        return srv

    def _accept_loop(self, srv, handler):
        while not self._closing:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            handler(conn)

    def _serve_request(self, conn):
        if self.hang_requests:
            self.held.append(conn)
            return
        conn.settimeout(1)
        try:
            self.requests.append(conn.recv(1024))
            if not self.fail_requests:
                conn.sendall(json.dumps(self.active).encode())
        finally:
            conn.close()

    def push(self, data):
        self.event_clients[-1].sendall(data.encode() if isinstance(data, str) else data)

    def drop(self):
        conn = self.event_clients[-1]
        conn.shutdown(socket.SHUT_RDWR)
        conn.close()

    def close(self):
        self._closing = True
        for s in self._servers + self.event_clients + self.held:
            try:
                s.close()
            except OSError:
                pass


def hypr_env(runtime, sig="pk"):
    return {"XDG_RUNTIME_DIR": str(runtime), "HYPRLAND_INSTANCE_SIGNATURE": sig}


def s2_line(cls, title) -> bytes:
    """An activewindow line as Hyprland 0.56 writes it: `event>>data.substr(0, 1024)`."""
    return b"activewindow>>" + f"{cls},{title}".encode()[:1024] + b"\n"


@needs_unix
def test_hyprland_seed_events_dedupe_private_and_reconnect(short_dir, capsys):
    hypr = FakeHyprland(short_dir)
    hypr.active = {"class": "org.keepassxc.KeePassXC", "pid": 4242, "title": "bank.kdbx - KeePassXC"}
    rec = Recorder()
    src = make_source("hyprland", rec, env=hypr_env(short_dir), initial_backoff=0.05)
    src.start()
    try:
        assert rec.wait_for(1)
        assert rec.calls == [("org.keepassxc.KeePassXC", False, 4242)]
        assert hypr.requests == [b"j/activewindow"]
        assert wait_until(lambda: len(hypr.event_clients) == 1)

        hypr.push("workspace>>2\nactivewindowv2>>55d0a1\n"
                  "activewindow>>Code,main.py - Visual Studio Code\n")
        hypr.push("activewindow>>Code,other.py - Visual Studio Code\n")           # title-only change
        hypr.push("activewindow>>firefox,Bank — Mozilla Firefox Private Browsing\n")
        hypr.push("activewindow>>kit")                                            # split across reads
        time.sleep(0.05)
        hypr.push("ty,~/secret-project\nactivewindow>>,\n")                       # nothing focused
        hypr.push("activewindow>>Code,main.py - Visual Studio Code\n")
        assert rec.wait_for(6)
        assert rec.calls == [
            ("org.keepassxc.KeePassXC", False, 4242),
            ("Code", False, None),
            ("firefox", True, None),
            ("kitty", False, None),
            (None, False, None),
            ("Code", False, None),
        ]
        assert src.failure is None

        # Hyprland closes the event socket: nothing is current while disconnected, then
        # reconnect (after backoff) and re-seed.
        hypr.active = {"class": "Alacritty", "pid": 99, "title": "htop"}
        hypr.drop()
        assert rec.wait_for(8)
        assert rec.calls[6:] == [(None, False, None), ("Alacritty", False, 99)]
        assert wait_until(lambda: len(hypr.event_clients) == 2)
        assert len(hypr.requests) == 2
        assert src.failure is None
        hypr.push("activewindow>>Code,main.py - Visual Studio Code\n")
        assert rec.wait_for(9)
        assert rec.calls[-1] == ("Code", False, None)
        assert rec.threads == {src.name}  # always on the source's own thread
    finally:
        stop_quickly(src)
        hypr.close()
    assert_no_titles(src, capsys, rec)


@needs_unix
def test_hyprland_empty_seed_means_nothing_focused(short_dir):
    hypr = FakeHyprland(short_dir)
    hypr.active = {}
    rec = Recorder()
    src = make_source("hyprland", rec, env=hypr_env(short_dir))
    src.start()
    try:
        assert rec.wait_for(1)
        assert rec.calls == [(None, False, None)]
    finally:
        stop_quickly(src)
        hypr.close()


@needs_unix
def test_hyprland_stop_is_prompt_without_compositor(tmp_path):
    rec = Recorder()
    src = make_source("hyprland", rec, env=hypr_env(tmp_path, "missing"))
    src.start()
    time.sleep(0.1)  # now sleeping in the 1 s backoff
    assert wait_until(lambda: src.failure == "no running Hyprland instance found (retrying)")
    stop_quickly(src, limit=0.5)
    assert rec.calls == []


@needs_unix
@pytest.mark.parametrize("stale_lock", [False, True], ids=["no-lock", "live-pid-dead-socket"])
def test_hyprland_skips_a_dead_signature_dir(short_dir, stale_lock, capsys):
    # Our env names an instance that is gone, but Hyprland never unlinks .socket2.sock.
    old = short_dir / "hypr" / "old"
    old.mkdir(parents=True)
    dead_socket(old / ".socket2.sock")
    if stale_lock:  # even a live-looking lock doesn't help when nothing accepts connections
        write_lock(short_dir, "old", os.getpid(), mtime=time.time() + 60)
    env = {**hypr_env(short_dir, "old"), "WAYLAND_DISPLAY": "wayland-1"}
    rec = Recorder()
    src = make_source("hyprland", rec, env=env, initial_backoff=0.05)
    src.start()
    hypr = None
    try:
        expected = ("can't reach Hyprland's event socket (retrying)" if stale_lock
                    else "no running Hyprland instance found (retrying)")
        assert wait_until(lambda: src.failure == expected)
        assert rec.calls == []

        hypr = FakeHyprland(short_dir, sig="new")   # Hyprland restarted under a new signature
        hypr.active = {"class": "Alacritty", "pid": 99, "title": "htop"}
        assert rec.wait_for(1)
        assert rec.calls == [("Alacritty", False, 99)]
        assert hypr.requests == [b"j/activewindow"]
        assert src.failure is None
    finally:
        stop_quickly(src)
        if hypr is not None:
            hypr.close()
    assert_no_titles(src, capsys, rec)


@needs_unix
def test_hyprland_truncated_title_is_reread_or_fails_closed(short_dir, capsys):
    assert b"Private Browsing" not in s2_line("firefox", LONG_PRIVATE)  # the premise: S2 cut it off
    hypr = FakeHyprland(short_dir)
    hypr.active = {"class": "kitty", "pid": 1234, "title": "htop"}
    rec = Recorder()
    src = make_source("hyprland", rec, env=hypr_env(short_dir))
    src.start()
    try:
        assert rec.wait_for(1)
        assert wait_until(lambda: len(hypr.event_clients) == 1)

        # A cut line is not trusted: j/activewindow (full title, plus pid) decides.
        hypr.active = {"class": "firefox", "pid": 88, "title": LONG_PRIVATE}
        hypr.push(s2_line("firefox", LONG_PRIVATE))
        assert rec.wait_for(2)
        hypr.active = {"class": "firefox", "pid": 88, "title": LONG_TITLE + "— Mozilla Firefox"}
        hypr.push(s2_line("firefox", LONG_TITLE + "— Mozilla Firefox"))
        assert rec.wait_for(3)
        assert rec.calls[1:] == [("firefox", True, 88), ("firefox", False, 88)]
        assert hypr.requests == [b"j/activewindow"] * 3

        # The re-read fails: fail closed for Firefox-family browsers only.
        hypr.fail_requests = True
        hypr.push("activewindow>>kitty,htop\n")
        hypr.push(s2_line("firefox", LONG_TITLE + "— Mozilla Firefox"))
        hypr.push(s2_line("Code", LONG_TITLE))
        hypr.push("activewindow>>firefox,Bank — Mozilla Firefox\n")      # short lines are trusted
        assert rec.wait_for(7)
        assert rec.calls[3:] == [
            ("kitty", False, None),
            ("firefox", True, None),
            ("Code", False, None),
            ("firefox", False, None),
        ]
        assert len(hypr.requests) == 5
    finally:
        stop_quickly(src)
        hypr.close()
    assert_no_titles(src, capsys, rec)


@needs_unix
def test_hyprland_stop_is_prompt_while_a_reread_hangs(short_dir, capsys):
    # A burst of cut lines means one j/activewindow re-read each; with a wedged compositor
    # every one would wait out its timeout unless stop() can cut them short.
    hypr = FakeHyprland(short_dir)
    hypr.active = {"class": "kitty", "pid": 1234, "title": "htop"}
    rec = Recorder()
    src = make_source("hyprland", rec, env=hypr_env(short_dir))
    src.start()
    try:
        assert rec.wait_for(1)
        assert wait_until(lambda: len(hypr.event_clients) == 1)
        hypr.hang_requests = True
        hypr.push(s2_line("firefox", LONG_PRIVATE) * 5)
        assert wait_until(lambda: len(hypr.held) == 1)      # stuck in the first re-read
    finally:
        stop_quickly(src, limit=0.5)
        hypr.close()
    assert rec.calls == [("kitty", False, 1234)]            # nothing reported while stopping
    assert_no_titles(src, capsys, rec)


def test_hyprland_socket_dir_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(hyprland, "LEGACY_BASE", tmp_path / "no-legacy")
    old = write_lock(tmp_path, "old", os.getpid(), mtime=1_000_000)
    new = write_lock(tmp_path, "new", os.getpid(), mtime=2_000_000)
    dead = write_lock(tmp_path, "dead", DEAD_PID, mtime=3_000_000)
    for d in (old, new, dead):
        (d / ".socket2.sock").touch()
    env = {"XDG_RUNTIME_DIR": str(tmp_path), "WAYLAND_DISPLAY": "wayland-1"}
    assert hyprland.socket_dir({**env, "HYPRLAND_INSTANCE_SIGNATURE": "old"}) == old     # signature wins
    assert hyprland.socket_dir({**env, "HYPRLAND_INSTANCE_SIGNATURE": "stale"}) == new   # newest live lock
    assert hyprland.socket_dir(env) == new
    assert hyprland.socket_dir({**env, "WAYLAND_DISPLAY": "wayland-9"}) is None          # other session
    assert hyprland.socket_dir({**env, "HYPRLAND_INSTANCE_SIGNATURE": "../new"}) == new  # no path tricks
    # Hyprland leaves .socket2.sock behind when it exits: without a live lock it doesn't count.
    gone = tmp_path / "hypr" / "gone"
    gone.mkdir()
    (gone / ".socket2.sock").touch()
    assert hyprland.socket_dir({**env, "HYPRLAND_INSTANCE_SIGNATURE": "gone"}) == new     # no lock
    assert hyprland.socket_dir({**env, "HYPRLAND_INSTANCE_SIGNATURE": "dead"}) == new     # dead pid
    assert hyprland.candidate_dirs({**env, "HYPRLAND_INSTANCE_SIGNATURE": "old"}) == [old, new]
    assert hyprland.candidate_dirs({**env, "HYPRLAND_INSTANCE_SIGNATURE": "gone"}) == [new, old]

    legacy = tmp_path / "legacy"
    (legacy / "v039").mkdir(parents=True)
    (legacy / "v039" / ".socket2.sock").touch()
    monkeypatch.setattr(hyprland, "LEGACY_BASE", legacy)                                 # Hyprland < 0.40
    assert hyprland.socket_dir({"HYPRLAND_INSTANCE_SIGNATURE": "v039"}) == legacy / "v039"  # no lock: ok
    (legacy / "v039" / "hyprland.lock").write_text(f"{DEAD_PID}\n")
    assert hyprland.socket_dir({"HYPRLAND_INSTANCE_SIGNATURE": "v039"}) is None             # dead lock


def test_hyprland_maybe_truncated():
    assert hyprland.maybe_truncated(s2_line("firefox", LONG_PRIVATE).rstrip(b"\n"))
    assert not hyprland.maybe_truncated(b"activewindow>>firefox,Bank - Mozilla Firefox")
    assert hyprland.maybe_truncated(b"activewindow>>" + b"x" * 1008)    # whole line capped at 1022
    assert not hyprland.maybe_truncated(b"activewindow>>" + b"x" * 1007)


def test_hyprland_parse_event():
    assert hyprland.parse_event(b"activewindow>>Code,a, b>>c") == ("Code", "a, b>>c")
    assert hyprland.parse_event(b"activewindow>>,") == (None, "")
    assert hyprland.parse_event(b"activewindowv2>>55d0a1") is None
    assert hyprland.parse_event(b"garbage") is None


# --------------------------------------------------------------------------- sway / i3
def sway_tree(focused):
    return {"type": "root", "nodes": [{"type": "output", "name": "eDP-1", "nodes": [
        {"type": "workspace", "name": "1", "focused": False, "nodes": [
            {"type": "con", "focused": False, "app_id": "kitty", "pid": 1234, "name": "~/secret-project"},
            focused,
        ], "floating_nodes": [
            {"type": "floating_con", "focused": False, "app_id": "pavucontrol", "pid": 55, "name": "Volume"},
        ]},
    ]}]}


class FakeSway:
    """Minimal i3-ipc server: answers SUBSCRIBE / GET_TREE and pushes events."""

    def __init__(self, path: Path):
        self.path = str(path)
        self.tree = {}
        self.messages = []
        self.conns = []
        self._wlock = threading.Lock()
        self._closing = False
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.path)
        self._srv.listen(8)
        self._srv.settimeout(0.05)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while not self._closing:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _recv_exact(conn, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def _serve(self, conn):
        while True:
            header = self._recv_exact(conn, sway.HEADER.size)
            if header is None:
                return
            magic, length, msg_type = sway.HEADER.unpack(header)
            payload = self._recv_exact(conn, length) if length else b""
            self.messages.append((msg_type, payload))
            if msg_type == sway.SUBSCRIBE:
                self.send(conn, sway.SUBSCRIBE, {"success": True})
            elif msg_type == sway.GET_TREE:
                self.send(conn, sway.GET_TREE, self.tree)

    def send(self, conn, msg_type, obj):
        data = json.dumps(obj).encode()
        with self._wlock:
            conn.sendall(struct.pack("=6sII", b"i3-ipc", len(data), msg_type) + data)

    def event(self, obj, msg_type=sway.EVENT_WINDOW):
        self.send(self.conns[-1], msg_type, obj)

    def shutdown(self):
        conn = self.conns[-1]
        self.event({"change": "exit"}, sway.EVENT_SHUTDOWN)
        conn.shutdown(socket.SHUT_RDWR)
        conn.close()

    def close(self):
        self._closing = True
        for s in [self._srv] + self.conns:
            try:
                s.close()
            except OSError:
                pass


def focus_event(container):
    return {"change": "focus", "container": container}


@needs_unix
def test_sway_seed_events_dedupe_private_and_reconnect(short_dir, capsys):
    srv = FakeSway(short_dir / "sway.sock")
    srv.tree = sway_tree({"type": "con", "focused": True, "app_id": "org.keepassxc.KeePassXC",
                          "pid": 4242, "name": "bank.kdbx - KeePassXC"})
    rec = Recorder()
    src = make_source("sway", rec, env={"SWAYSOCK": srv.path}, initial_backoff=0.05)
    src.start()
    try:
        assert rec.wait_for(1)
        assert rec.calls == [("org.keepassxc.KeePassXC", False, 4242)]
        types = [t for t, _ in srv.messages]
        assert types == [sway.SUBSCRIBE, sway.GET_TREE]
        assert json.loads(srv.messages[0][1]) == ["window", "workspace", "shutdown"]
        assert src.failure is None

        srv.event({"change": "title", "container": {"app_id": "kitty", "pid": 1234, "name": "htop"}})
        srv.event(focus_event({"app_id": None, "window_properties": {"class": "Code", "instance": "code"},
                               "pid": 77, "name": "main.py - Visual Studio Code"}))       # XWayland
        srv.event(focus_event({"app_id": None, "window_properties": {"class": "Code"},
                               "pid": 77, "name": "other.py - Visual Studio Code"}))      # title only
        srv.event(focus_event({"app_id": "firefox", "pid": 88,
                               "name": "Bank — Mozilla Firefox Private Browsing"}))
        srv.event(focus_event({"window_properties": {"class": "URxvt"}, "name": "htop"}))  # i3: no pid
        assert rec.wait_for(4)
        assert rec.calls == [
            ("org.keepassxc.KeePassXC", False, 4242),
            ("Code", False, 77),
            ("firefox", True, 88),
            ("URxvt", False, None),
        ]

        # Shutdown event: nothing is current while disconnected; reconnect and re-seed.
        srv.tree = sway_tree({"type": "con", "focused": True, "app_id": "Alacritty", "pid": 99, "name": "htop"})
        srv.shutdown()
        assert rec.wait_for(6)
        assert rec.calls[4:] == [(None, False, None), ("Alacritty", False, 99)]
        assert wait_until(lambda: len(srv.conns) == 2)
        assert wait_until(lambda: [t for t, _ in srv.messages].count(sway.SUBSCRIBE) == 2)
        assert wait_until(lambda: src.failure is None)
        srv.event(focus_event({"app_id": "kitty", "pid": 1234, "name": "~/secret-project"}))
        assert rec.wait_for(7)
        assert rec.calls[-1] == ("kitty", False, 1234)
        assert rec.threads == {src.name}
    finally:
        stop_quickly(src)
        srv.close()
    assert_no_titles(src, capsys, rec)


@needs_unix
def test_sway_stop_is_prompt_without_compositor(tmp_path):
    rec = Recorder()
    src = make_source("sway", rec, env={"SWAYSOCK": str(tmp_path / "nope.sock")})
    src.start()
    time.sleep(0.1)
    assert wait_until(lambda: src.failure == "can't reach the sway/i3 IPC socket (retrying)")
    stop_quickly(src, limit=0.5)
    assert rec.calls == []


EMPTY_WORKSPACE = {"type": "root", "nodes": [{"type": "output", "nodes": [
    {"type": "workspace", "name": "2", "focused": True, "nodes": []}]}]}


@needs_unix
def test_sway_empty_workspace_means_nothing_focused(short_dir, capsys):
    srv = FakeSway(short_dir / "sway.sock")
    srv.tree = sway_tree({"type": "con", "focused": True, "app_id": "Alacritty", "pid": 99, "name": "htop"})
    rec = Recorder()
    src = make_source("sway", rec, env={"SWAYSOCK": srv.path})
    src.start()

    def tree_requests():
        return [t for t, _ in srv.messages].count(sway.GET_TREE)

    try:
        assert rec.wait_for(1)
        # Switching to an empty workspace sends only workspace::focus (no window::focus).
        srv.tree = EMPTY_WORKSPACE
        srv.event({"change": "rename", "current": {"name": "2"}}, sway.EVENT_WORKSPACE)   # ignored
        srv.event({"change": "focus", "current": {"type": "workspace", "name": "2"}}, sway.EVENT_WORKSPACE)
        assert rec.wait_for(2)
        assert rec.calls[-1] == (None, False, None)
        assert tree_requests() == 2

        srv.event(focus_event({"app_id": "firefox", "pid": 88, "name": "Bank — Mozilla Firefox"}))
        assert rec.wait_for(3)
        assert rec.calls[-1] == ("firefox", False, 88)

        # Closing the last window on a workspace sends only window::close.
        srv.tree = EMPTY_WORKSPACE
        srv.event({"change": "close", "container": {"app_id": "firefox", "pid": 88,
                                                    "name": "Bank — Mozilla Firefox"}})
        assert rec.wait_for(4)
        assert rec.calls[-1] == (None, False, None)
        assert tree_requests() == 3

        # A close that leaves another window focused just re-seeds to it.
        srv.tree = sway_tree({"type": "con", "focused": True, "app_id": "Alacritty", "pid": 99, "name": "htop"})
        srv.event({"change": "close", "container": {"app_id": "kitty", "pid": 1234, "name": "htop"}})
        assert rec.wait_for(5)
        assert rec.calls == [("Alacritty", False, 99), (None, False, None), ("firefox", False, 88),
                             (None, False, None), ("Alacritty", False, 99)]
    finally:
        stop_quickly(src)
        srv.close()
    assert_no_titles(src, capsys, rec)


@needs_unix
def test_sway_finds_a_restarted_sway_when_swaysock_is_stale(short_dir):
    uid = os.getuid()
    dead = short_dir / f"sway-ipc.{uid}.{DEAD_PID}.sock"
    dead_socket(dead)                                                   # newer, but its sway is gone
    os.utime(dead, (time.time() + 60, time.time() + 60))
    srv = FakeSway(short_dir / f"sway-ipc.{uid}.{os.getpid()}.sock")    # the running sway
    srv.tree = sway_tree({"type": "con", "focused": True, "app_id": "Alacritty", "pid": 99, "name": "htop"})
    env = {"SWAYSOCK": str(short_dir / f"sway-ipc.{uid}.1.sock"), "XDG_RUNTIME_DIR": str(short_dir)}
    assert sway.live_sway_sockets(env) == [srv.path]
    rec = Recorder()
    src = make_source("sway", rec, env=env)
    src.start()
    try:
        assert rec.wait_for(1)
        assert rec.calls == [("Alacritty", False, 99)]
        assert src.failure is None
    finally:
        stop_quickly(src)
        srv.close()
    # i3 has no such naming scheme: $I3SOCK alone is never second-guessed.
    i3 = make_source("sway", Recorder(), env={"I3SOCK": "/nonexistent", "XDG_RUNTIME_DIR": str(short_dir)})
    assert i3._socket_paths() == ["/nonexistent"]


def test_sway_tree_helpers():
    tree = sway_tree({"type": "floating_con", "focused": True, "app_id": None, "pid": 9,
                      "window_properties": {"class": "Steam"}, "name": "Steam"})
    assert sway.window_of(sway.focused_node(tree)) == ("Steam", "Steam", 9)
    assert sway.window_of({"type": "workspace", "name": "2", "focused": True}) is None
    assert sway.focused_node({"nodes": []}) is None


# --------------------------------------------------------------------------- X11 (fake xprop)
_WINDOWS_PROPS = {
    "0x1a00003": 'WM_CLASS(STRING) = "keepassxc", "KeePassXC"\n'
                 "_NET_WM_PID(CARDINAL) = 4242\n"
                 '_NET_WM_NAME(UTF8_STRING) = "bank.kdbx - KeePassXC"\n',
    "0x2c00005": 'WM_CLASS(STRING) = "code", "Code"\n'
                 "_NET_WM_PID(CARDINAL) = 77\n"
                 '_NET_WM_NAME(UTF8_STRING) = "main.py - Visual Studio Code"\n',
    "0x2c00006": 'WM_CLASS(STRING) = "code", "Code"\n'
                 "_NET_WM_PID(CARDINAL) = 77\n"
                 '_NET_WM_NAME(UTF8_STRING) = "other.py \\"x\\" - Visual Studio Code"\n',
    "0x3e00001": 'WM_CLASS(STRING) = "Navigator", "firefox"\n'
                 "_NET_WM_PID(CARDINAL) = 88\n"
                 '_NET_WM_NAME(UTF8_STRING) = "Bank — Mozilla Firefox Private Browsing"\n',
}
_SPY_RUNS = [
    # first child: prints a burst, then exits (the source must restart it)
    ["_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1a00003",
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1a00003",     # same id again
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2c00005",
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2c00006",     # same app, other window
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3e00001",
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x5000001",     # vanished before we asked
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0"],
    # second child: keeps running until terminated
    ["_NET_ACTIVE_WINDOW:  not found.",
     "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1a00003"],
]
_FAKE_XPROP = """#!{python}
import os, sys, time
STATE = {state!r}
WINDOWS = {windows!r}
SPY_RUNS = {spy_runs!r}
args = sys.argv[1:]
if args == ["-root", "-spy", "_NET_ACTIVE_WINDOW"]:
    counter = os.path.join(STATE, "spy-count")
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    for line in SPY_RUNS[min(n, len(SPY_RUNS) - 1)]:
        print(line, flush=True)
    if n == 0:
        sys.exit(0)
    time.sleep(30)
elif args[:1] == ["-id"] and args[2:] == ["WM_CLASS", "_NET_WM_PID", "_NET_WM_NAME"]:
    with open(os.path.join(STATE, "queries"), "a") as f:
        f.write(args[1] + "\\n")
    if args[1] not in WINDOWS:
        sys.exit(1)
    sys.stdout.write(WINDOWS[args[1]])
else:
    sys.exit(2)
"""


@pytest.fixture
def fake_xprop(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    exe = bin_dir / "xprop"
    exe.write_text(_FAKE_XPROP.format(python=sys.executable, state=str(state),
                                      windows=_WINDOWS_PROPS, spy_runs=_SPY_RUNS))
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return state


@needs_unix
def test_x11_spy_queries_dedupe_private_and_restart(fake_xprop, capsys):
    rec = Recorder()
    src = make_source("x11", rec, initial_backoff=0.05)
    src.start()
    try:
        assert rec.wait_for(5)
        assert rec.calls == [
            ("KeePassXC", False, 4242),
            ("Code", False, 77),
            ("firefox", True, 88),
            (None, False, None),
            ("KeePassXC", False, 4242),   # after the spy child was restarted
        ]
        assert (fake_xprop / "spy-count").read_text() == "2"
        queries = (fake_xprop / "queries").read_text().split()
        assert queries == ["0x1a00003", "0x2c00005", "0x2c00006", "0x3e00001", "0x5000001", "0x1a00003"]
        assert rec.threads == {src.name}
        assert src.failure is None   # the second spy child is running
    finally:
        stop_quickly(src)
    assert_no_titles(src, capsys, rec)


@needs_unix
def test_x11_without_xprop_keeps_retrying_and_stops(tmp_path, monkeypatch, xprop_bin):
    monkeypatch.setenv("PATH", str(tmp_path))
    rec = Recorder()
    src = make_source("x11", rec)
    src.start()
    time.sleep(0.1)
    assert wait_until(lambda: src.failure == "can't run xprop (retrying)")
    stop_quickly(src, limit=0.5)
    assert rec.calls == []

    monkeypatch.setenv("PATH", xprop_bin)   # an xprop that exits at once without output
    src = make_source("x11", rec, initial_backoff=0.02)
    src.start()
    try:
        assert wait_until(lambda: src.failure == "xprop keeps exiting (retrying)")
        assert src.is_alive()
    finally:
        stop_quickly(src)
    assert rec.calls == []


def test_x11_parsers():
    assert x11.parse_spy_line("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3A00007\n") == 0x3A00007
    assert x11.parse_spy_line("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0") is x11._NOTHING
    assert x11.parse_spy_line("_NET_ACTIVE_WINDOW:  not found.") is x11._NOTHING
    assert x11.parse_spy_line("") is None
    app, title, pid = x11.parse_window_props(
        'WM_CLASS(STRING) = "sun-awt-X11-XFramePeer", "jetbrains-idea"\n'
        "_NET_WM_PID:  not found.\n"
        '_NET_WM_NAME(UTF8_STRING) = "a \\"b\\" c"\n')
    assert (app, title, pid) == ("jetbrains-idea", 'a "b" c', None)
    assert x11.parse_window_props("WM_CLASS:  not found.\n") == (None, None, None)


# --------------------------------------------------------------------------- Windows (injected calls)
class FakeWin32:
    """Stands in for user32 + psutil; hwnd 0 = no foreground window.

    106 / 109 are UWP frames (ApplicationFrameHost.exe, pid 900): 106 hosts Calculator's
    CoreWindow (108, pid 950) next to a child of its own (107); 109 hosts nothing (suspended).
    """
    pids = {101: 4242, 102: 77, 103: 77, 104: 88, 105: 5, 106: 900, 107: 900, 108: 950, 109: 900, 110: 900}
    names = {4242: "KeePassXC.exe", 77: "Code.exe", 88: "firefox.exe",   # pid 5: access denied
             900: "ApplicationFrameHost.exe", 950: "CalculatorApp.exe"}
    titles = {101: "bank.kdbx - KeePassXC", 102: "main.py - Visual Studio Code",
              103: "other.py - Visual Studio Code", 104: "Bank — Mozilla Firefox Private Browsing",
              105: "Task Manager", 106: "Display is 42", 109: "Display is 42"}
    children = {106: [107, 108], 109: [110]}

    def __init__(self):
        self.hwnd = 101

    def install(self, src):
        src._setup = lambda: None
        src._foreground_window = lambda: self.hwnd
        src._window_pid = self.pids.get
        src._window_title = self.titles.get
        src._child_windows = lambda hwnd: list(self.children.get(hwnd, []))
        src._process_name = self.names.get


class FakeUser32:
    """Just enough of user32 for the real _window_title / _child_windows code."""

    def __init__(self, titles=None, children=None):
        self.titles = titles or {}
        self.children = children or {}

    def GetWindowTextLengthW(self, hwnd):
        return len(self.titles.get(hwnd, ""))

    def GetWindowTextW(self, hwnd, buf, size):
        text = self.titles.get(hwnd, "")[:max(size - 1, 0)]
        buf.value = text
        return len(text)

    def EnumChildWindows(self, hwnd, callback, lparam):
        for child in self.children.get(hwnd, []):
            if not callback(child, lparam):
                break
        return 1


def test_windows_poll_logic(capsys):
    rec = Recorder()
    src = make_source("windows", rec)
    fake = FakeWin32()
    fake.install(src)
    for hwnd in (101, 101, 102, 103, 104, 105, 0, None):
        fake.hwnd = hwnd
        src.poll_once()
    assert rec.calls == [
        ("KeePassXC.exe", False, 4242),
        ("Code.exe", False, 77),
        ("firefox.exe", True, 88),
        (None, False, 5),          # process name unavailable
    ]                              # hwnd 0 / None -> (None, False) again: deduped
    assert_no_titles(src, capsys, rec)


def test_windows_reads_the_whole_title(capsys):
    assert len(LONG_PRIVATE) > 1024
    rec = Recorder()
    src = make_source("windows", rec)
    fake = FakeWin32()
    fake.install(src)
    del src._window_title                                   # use the real method on a fake user32
    src._user32 = FakeUser32(titles={104: LONG_PRIVATE, 102: "main.py - Visual Studio Code"})
    assert src._window_title(104) == LONG_PRIVATE
    assert src._window_title(999) is None
    for hwnd in (104, 102):
        fake.hwnd = hwnd
        src.poll_once()
    assert rec.calls == [("firefox.exe", True, 88), ("Code.exe", False, 77)]
    assert_no_titles(src, capsys, rec)


def test_windows_uwp_apps_are_named_after_the_hosted_app(capsys):
    rec = Recorder()
    src = make_source("windows", rec)
    fake = FakeWin32()
    fake.install(src)
    for hwnd in (106, 106, 101, 109, 106):
        fake.hwnd = hwnd
        src.poll_once()
    assert rec.calls == [
        ("CalculatorApp.exe", False, 950),   # not "ApplicationFrameHost.exe"
        ("KeePassXC.exe", False, 4242),
        (None, False, None),                 # a frame with no hosted app: unknown, not the frame host
        ("CalculatorApp.exe", False, 950),
    ]
    assert_no_titles(src, capsys, rec)


def test_windows_child_windows_enumeration(monkeypatch):
    import ctypes
    src = make_source("windows", Recorder())
    # Off Windows there is no WINFUNCTYPE; CFUNCTYPE has the same calling shape for the fake.
    src._enum_proc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    src._user32 = FakeUser32(children={106: [107, 108], 7: list(range(1, 50))})
    assert src._child_windows(106) == [107, 108]
    assert src._child_windows(5) == []
    monkeypatch.setattr(windows, "_MAX_CHILDREN", 3)
    assert src._child_windows(7) == [1, 2, 3]


def test_windows_failing_polls_report_failure_and_recover():
    rec = Recorder()
    src = make_source("windows", rec)
    src.poll_interval = 0.01
    fake = FakeWin32()
    fake.install(src)
    broken = threading.Event()

    def foreground():
        if broken.is_set():
            raise OSError("bank.kdbx")
        return fake.hwnd

    src._foreground_window = foreground
    src.start()
    try:
        assert rec.wait_for(1)
        assert src.failure is None
        broken.set()
        assert wait_until(lambda: src.failure == "can't read the foreground window (retrying)")
        assert rec.wait_for(2)
        assert rec.calls[1] == (None, False, None)        # nothing current while we can't see
        broken.clear()
        assert wait_until(lambda: src.failure is None)
        assert rec.wait_for(3)
        assert rec.calls == [("KeePassXC.exe", False, 4242), (None, False, None), ("KeePassXC.exe", False, 4242)]
    finally:
        stop_quickly(src, limit=0.5)


def test_windows_thread_polls_and_stops_quickly():
    rec = Recorder()
    src = make_source("windows", rec)
    src.poll_interval = 0.02
    fake = FakeWin32()
    fake.install(src)
    src.start()
    try:
        assert rec.wait_for(1)
        fake.hwnd = 102
        assert rec.wait_for(2)
        assert rec.calls == [("KeePassXC.exe", False, 4242), ("Code.exe", False, 77)]
        assert rec.threads == {src.name}
    finally:
        src.poll_interval = 1.0
        time.sleep(0.05)  # let it enter the 1 s wait
        stop_quickly(src, limit=0.5)


def test_windows_process_name_handles_psutil_errors(monkeypatch):
    import psutil
    src = make_source("windows", Recorder())
    assert src._process_name(os.getpid())

    def raiser(exc):
        def fake(pid):
            raise exc
        return fake

    monkeypatch.setattr(psutil, "Process", raiser(psutil.NoSuchProcess(123)))
    assert src._process_name(123) is None
    monkeypatch.setattr(psutil, "Process", raiser(psutil.AccessDenied(123)))
    assert src._process_name(123) is None


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows failure path")
def test_windows_source_off_windows_reports_failure_and_keeps_retrying(capsys):
    rec = Recorder()
    src = make_source("windows", rec, initial_backoff=0.02)
    src.start()
    try:
        assert wait_until(lambda: src.failure is not None)
        assert src.failure.startswith("listener error (") and src.failure.endswith("), retrying")
        time.sleep(0.15)                                   # several retries
        assert src.is_alive()
    finally:
        stop_quickly(src, limit=0.5)
    assert rec.calls == []
    out = capsys.readouterr().out
    assert out.count("[Focus] windows listener error") == 1   # reported once, not on every retry


@pytest.mark.skipif(sys.platform != "win32", reason="the real user32 calls only exist on Windows")
def test_windows_real_ctypes_path_smoke():
    rec = Recorder()
    src = make_source("windows", rec)
    src._setup()
    hwnd = src._foreground_window()                        # may be None/0 on a headless runner
    if hwnd:
        pid = src._window_pid(hwnd)
        assert pid is None or (isinstance(pid, int) and pid > 0)
        title = src._window_title(hwnd)
        assert title is None or isinstance(title, str)
        assert all(isinstance(h, int) for h in src._child_windows(hwnd))
    # NULL parent = all top-level windows: exercises the EnumChildWindows callback for real.
    tops = src._child_windows(None)
    assert isinstance(tops, list) and all(isinstance(h, int) for h in tops)
    assert len(tops) <= windows._MAX_CHILDREN
    src.poll_once()
    assert len(rec.calls) <= 1
    for app, private, pid in rec.calls:
        assert app is None or isinstance(app, str)
        assert isinstance(private, bool)
        assert pid is None or (isinstance(pid, int) and pid > 0)


def test_focus_package_exports():
    assert set(focus.BACKENDS) == {"hyprland", "sway", "x11", "windows"}
    assert callable(focus.detect_backend) and callable(focus.make_source)
