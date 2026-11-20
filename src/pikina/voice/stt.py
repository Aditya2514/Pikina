"""
Speech to text with whisper.cpp (https://github.com/ggml-org/whisper.cpp), run as a
separate program so Pikina needs no extra Python packages for voice.

    binary  PIKINA_WHISPER_BIN, else whisper-cli / whisper-cpp on PATH
    model   PIKINA_WHISPER_MODEL, else "voice_model" in settings.json,
            else the best ggml-*.bin in <data folder>/models
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

from pikina import paths

BINARIES = ("whisper-cli", "whisper-cpp", "whisper.cpp")
# Preferred models first: English-only models are faster and more accurate for English.
MODEL_PREFERENCE = ("base.en", "small.en", "tiny.en", "base", "small", "tiny", "medium.en", "medium")
TIMEOUT = 120


def models_dir() -> Path:
    return paths.data_dir() / "models"


def find_whisper() -> str | None:
    override = os.getenv("PIKINA_WHISPER_BIN", "").strip()
    if override:
        p = Path(override).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        return shutil.which(override)
    for name in BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_model() -> str | None:
    override = os.getenv("PIKINA_WHISPER_MODEL", "").strip()
    if not override:
        from pikina import settings
        value = settings.get("voice_model")
        override = value.strip() if isinstance(value, str) else ""
    if override:
        p = Path(override).expanduser()
        return str(p) if p.is_file() else None
    folder = models_dir()
    for name in MODEL_PREFERENCE:
        p = folder / f"ggml-{name}.bin"
        if p.is_file():
            return str(p)
    others = sorted(folder.glob("ggml-*.bin")) if folder.is_dir() else []
    return str(others[0]) if others else None


# Non-speech annotations whisper writes: [BLANK_AUDIO], (music), *laughs*, ♪ ... ♪
_NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|♪[^♪]*♪?")
_TODO = re.compile(r"\bto-?do(s|'s)?\b", re.I)


def clean_transcript(text: str) -> str:
    """
    Turn whisper's sentence-style output into a command the router matches.
    Whisper writes "Work on Pikina." and "Add a to-do, ..."; Tier 1 wants
    "work on pikina" and "todo", so drop annotations, the closing punctuation
    and the hyphen in "to-do". Case is left alone (the router ignores it, and
    names like file names keep theirs).
    """
    text = _NOISE.sub(" ", text or "")
    text = _TODO.sub(lambda m: "todos" if m.group(1) else "todo", text)
    text = " ".join(text.split())
    return text.strip(" \t.,!?;:\"'“”‘’…-")


def transcribe(wav: Path, binary: str | None = None, model: str | None = None,
               language: str = "en", timeout: float = TIMEOUT) -> str:
    """Text spoken in a 16 kHz mono WAV, cleaned for the router ('' if nothing was said)."""
    from pikina.voice import HINTS, VoiceError
    binary = binary or find_whisper()
    if not binary:
        raise VoiceError(f"whisper.cpp isn't installed: {HINTS['whisper']}.")
    model = model or find_model()
    if not model:
        raise VoiceError(f"No speech model found: {HINTS['model']}.")
    argv = [binary, "-m", model, "-f", str(wav), "-l", language, "-nt", "-np",
            "-t", str(max(1, min(4, os.cpu_count() or 1)))]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise VoiceError(f"whisper.cpp took longer than {timeout:.0f} s; try a smaller model (tiny.en).") from exc
    except OSError as exc:
        raise VoiceError(f"Couldn't run {binary}: {exc.strerror or exc}.") from exc
    if done.returncode != 0:
        last = (done.stderr or "").strip().splitlines()
        raise VoiceError("whisper.cpp failed" + (f": {last[-1]}" if last else f" (exit code {done.returncode})."))
    return clean_transcript(done.stdout)
