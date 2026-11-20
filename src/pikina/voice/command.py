"""
The `voice` command of the pikina terminal.

A spoken command is never run without being shown first: whisper can mishear,
and "remove todo ..." misheard is still a removal. By default you confirm with
Enter; set "voice_confirm": false in settings.json to skip that.
"""
from pathlib import Path
from typing import Callable

from pikina.voice import HINTS, VoiceError, audio, check, missing, stt

USAGE = ("  voice            speak a command (press Enter when you're done)\n"
         "  voice <file>     run the command spoken in a recording (no microphone needed)\n"
         "  voice check      what voice input uses here, and what's missing")


def setting(key, default):
    from pikina import settings
    value = settings.get(key, default)
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        return value if ok else default
    return value.strip() if isinstance(value, str) and value.strip() else default


def _wait_for_enter(ask):
    def wait():
        try:
            ask("")
        except EOFError:   # no terminal (piped input): stop at once rather than hang
            pass
    return wait


class SilenceError(VoiceError):
    """The recording is (near) silent, so whisper would only guess."""


def transcribe_file(src: Path, workdir: Path, progress: Callable[[], None] = lambda: None) -> str:
    """Cleaned text spoken in src ('' if no words). Raises VoiceError / SilenceError."""
    wav, info = audio.prepare(src, workdir)
    if audio.is_silent(info):
        raise SilenceError(f"Heard only silence ({info['seconds']:.1f} s, level {info['rms']:.0f}). "
                           "Check that the microphone isn't muted and is the default input.")
    progress()
    return stt.transcribe(wav, language=setting("voice_language", "en"))


def status_lines(status: dict | None = None) -> list[str]:
    status = check() if status is None else status
    lines = []
    for key, label in (("recorder", "Recorder"), ("whisper", "whisper.cpp"), ("model", "Speech model")):
        lines.append(f"{'✓' if status.get(key) else '✗'} {label}: {status.get(key) or HINTS[key]}")
    lines.append("Ready: say 'voice'." if not missing(status) else
                 "Voice input is off until the ✗ lines are fixed. 'voice <file.wav>' needs only whisper.cpp and a model.")
    return lines


def handle(args: list[str], mcm, ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> dict | None:
    """Run one `voice ...` command. Returns the router result, or None if nothing ran."""
    if args and args[0].lower() == "check":
        for line in status_lines():
            out(f"  {line}")
        return None

    try:
        with audio.temp_dir() as tmp:
            tmp = Path(tmp)
            if args:
                src = Path(" ".join(args).strip("\"'")).expanduser()
                if not src.is_file():
                    out(f"  [X] No file at {src}.")
                    return None
            else:
                # check whisper before asking the user to speak, so they don't talk for nothing
                status = check()
                gaps = missing(status)
                if gaps:
                    out("  [X] Voice input isn't set up yet:")
                    for g in gaps:
                        out(f"      - {g}")
                    out("      You can still type commands. 'voice check' shows what's installed.")
                    return None
                limit = setting("voice_max_seconds", audio.MAX_SECONDS)
                out(f"  🎙  Listening ({status['recorder']}). Speak, then press Enter. Stops by itself after {limit} s.")
                src = audio.record(tmp / "spoken.wav", wait=_wait_for_enter(ask), max_seconds=limit)

            text = transcribe_file(src, tmp, progress=lambda: out("  …transcribing"))
    except VoiceError as exc:
        out(f"  [X] {exc}")
        return None
    except KeyboardInterrupt:
        out("  Cancelled.")
        return None

    if not text:
        out("  [X] Didn't catch any words. Try again, a little closer to the mic.")
        return None
    out(f'  Heard: "{text}"')
    if setting("voice_confirm", True):
        try:
            answer = ask("  Run it? [Enter = yes, n = no] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in ("", "y", "yes"):
            out("  Not run.")
            return None
    return mcm.receive(text, source="user_spoke", origin="voice")
