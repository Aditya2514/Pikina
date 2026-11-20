"""
Voice from a keyboard shortcut, with no terminal: press once to start listening,
press again to stop. Pikina then shows what it heard, asks before running it
(unless "voice_confirm" is false) and shows the reply as a desktop notification.

    pikina-voice            start listening, or stop if already listening
    pikina-voice --stop     stop listening (does nothing if not listening)
    pikina-voice <file>     run the command spoken in a recording

Hyprland:  bind = SUPER, V, exec, ~/code/pikina/.venv/bin/pikina-voice
Sway:      bindsym $mod+v exec ~/code/pikina/.venv/bin/pikina-voice

The listening instance writes its pid to $XDG_RUNTIME_DIR/pikina-voice.pid; the
second press sends it SIGUSR1. Linux only.
"""
import os
import signal
import sys
import threading
from pathlib import Path

from pikina import paths
from pikina.voice import VoiceError, audio, check, missing
from pikina.voice.command import setting, transcribe_file

PIDFILE = "pikina-voice.pid"


def _say(title: str, body: str = "", urgent: bool = False, seconds: float | None = None) -> None:
    """Notification when there's a desktop; always also printed (a keybind's output goes to its log)."""
    from pikina.osbridge.notify import notify
    print(f"[Pikina voice] {title}" + (f": {body}" if body else ""), flush=True)
    notify(title, body, urgent=urgent, seconds=seconds)


def pidfile() -> Path:
    runtime = os.getenv("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime and Path(runtime).is_dir() else paths.data_dir()
    return base / PIDFILE


def _is_ours(pid: int) -> bool:
    """Alive, and (where /proc exists) really a Pikina process, not a reused pid."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False          # someone else's process: never signal it
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        try:
            return b"pikina" in cmdline.read_bytes()
        except OSError:
            return False
    return True


def listening_pid() -> int | None:
    f = pidfile()
    try:
        pid = int(f.read_text().strip())
    except (OSError, ValueError):
        return None
    if pid != os.getpid() and _is_ours(pid):
        return pid
    f.unlink(missing_ok=True)   # stale: that process is gone
    return None


def _claim() -> bool:
    """Write our pid, atomically. False if another instance got there first."""
    f = pidfile()
    try:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    return True


def _release() -> None:
    f = pidfile()
    try:
        if f.read_text().strip() == str(os.getpid()):
            f.unlink()
    except OSError:
        pass


def _run(text: str) -> int:
    if not text:
        _say("Didn't catch that", "No words were recognised. Try again, a little closer to the mic.")
        return 1
    if setting("voice_confirm", True):
        from pikina.osbridge import consent
        if not consent.ask(f'Pikina heard:\n\n"{text}"\n\nRun it?'):
            _say("Not run", f'"{text}"')
            return 0
    from pikina.cli import make_orchestrator, result_text
    _, mcm = make_orchestrator()
    try:
        result = mcm.receive(text, source="user_spoke", origin="voice")
    except Exception as exc:  # report it; a keybind has nowhere else to show it
        _say("Command failed", f"{type(exc).__name__}: {exc}", urgent=True)
        return 1
    failed = result.get("status") in ("error", "denied", "rejected")
    _say(f'"{text}"', result_text(result) or str(result.get("status", "")), urgent=failed)
    return 1 if failed else 0


def _listen(stop: threading.Event) -> int:
    status = check()
    gaps = missing(status)
    if gaps:
        _say("Voice input isn't set up", "; ".join(gaps) + ". Run 'pikina' and type 'voice check'.", urgent=True)
        return 1
    if not _claim():
        # another press won the race; treat this press as "stop"
        pid = listening_pid()
        if pid:
            os.kill(pid, signal.SIGUSR1)
        return 0
    limit = setting("voice_max_seconds", audio.MAX_SECONDS)
    try:
        _say("Listening…", f"Press the shortcut again when you're done (stops after {limit} s).", seconds=limit)
        with audio.temp_dir() as tmp:
            tmp = Path(tmp)
            try:
                src = audio.record(tmp / "spoken.wav", wait=lambda: stop.wait(limit + 1), max_seconds=limit)
            finally:
                _release()   # a new press may start listening while we transcribe
            text = transcribe_file(src, tmp)
    except VoiceError as exc:
        _release()
        _say("Voice input", str(exc), urgent=True)
        return 1
    return _run(text)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        print("pikina-voice is for Linux desktops. On Windows, type 'voice' in the pikina terminal.")
        return 2
    from pikina.server import load_env
    load_env()

    if argv and argv[0] == "--stop":
        pid = listening_pid()
        if pid:
            os.kill(pid, signal.SIGUSR1)
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv:
        src = Path(" ".join(argv)).expanduser()
        if not src.is_file():
            _say("Voice input", f"No file at {src}.", urgent=True)
            return 1
        try:
            with audio.temp_dir() as tmp:
                text = transcribe_file(src, Path(tmp))
        except VoiceError as exc:
            _say("Voice input", str(exc), urgent=True)
            return 1
        return _run(text)

    pid = listening_pid()
    if pid:                                   # second press: stop that recording
        os.kill(pid, signal.SIGUSR1)
        return 0
    stop = threading.Event()
    signal.signal(signal.SIGUSR1, lambda *_: stop.set())
    return _listen(stop)


if __name__ == "__main__":
    sys.exit(main())
