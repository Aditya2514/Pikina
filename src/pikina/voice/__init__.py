"""
Voice input (Phase 5a): speak a command in the terminal instead of typing it.

    pikina> voice              record from the microphone until you press Enter
    pikina> voice clip.wav     transcribe a recording instead (no microphone needed)
    pikina> voice check        say what is installed and what is missing

Everything runs locally: a recorder that ships with the desktop (pw-record,
parecord or arecord) captures 16 kHz mono WAV, and whisper.cpp turns it into
text. The text then goes through the normal router as a spoken command
(provenance "user_spoke", activity source "voice"). Nothing here needs the
backend server, the desktop app or a compositor.
"""
from pikina.voice.audio import find_recorder
from pikina.voice.stt import find_model, find_whisper


class VoiceError(Exception):
    """Something in the voice pipeline failed; the message says how to fix it."""


HINTS = {
    "recorder": "install pipewire (pw-record), pulseaudio-utils (parecord) or alsa-utils (arecord)",
    "whisper":  "install whisper.cpp so whisper-cli is on PATH (or set PIKINA_WHISPER_BIN)",
    "model":    "download a speech model: python -m pikina.voice.download",
}


def check() -> dict:
    """What the voice pipeline would use right now; None means missing."""
    rec = find_recorder()
    return {
        "recorder": rec.name if rec else None,
        "whisper":  find_whisper(),
        "model":    find_model(),
    }


def missing(status: dict | None = None) -> list[str]:
    """Fix hints for every missing piece, in the order they matter."""
    status = check() if status is None else status
    return [HINTS[k] for k in ("recorder", "whisper", "model") if not status.get(k)]
