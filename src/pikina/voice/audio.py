"""
Microphone recording and WAV checks for voice input.

whisper.cpp reads 16 kHz, mono, 16-bit WAV, so every recorder is asked for
exactly that. Recording stops when the caller's wait() returns (the CLI waits
for Enter) or after max_seconds, whichever comes first. The recorder gets
SIGINT so it can finish the WAV header before exiting.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RATE = 16000
MAX_SECONDS = 30
# RMS on the 16-bit scale below which a recording counts as silence. Speech into a
# laptop mic is usually in the hundreds to thousands; a muted mic is near 0.
SILENCE_RMS = 80


@dataclass(frozen=True)
class Recorder:
    name: str
    build: Callable[[str], list]

    def argv(self, out: str) -> list:
        return self.build(out)


RECORDERS = [
    Recorder("pw-record", lambda out: ["pw-record", "--rate", str(RATE), "--channels", "1",
                                       "--format", "s16", out]),
    Recorder("parecord",  lambda out: ["parecord", f"--rate={RATE}", "--channels=1",
                                       "--format=s16le", "--file-format=wav", out]),
    Recorder("arecord",   lambda out: ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1",
                                       "-t", "wav", out]),
]


def find_recorder() -> Recorder | None:
    """First recorder on PATH, or the one named in PIKINA_RECORDER."""
    wanted = os.getenv("PIKINA_RECORDER", "").strip()
    for rec in RECORDERS:
        if wanted and rec.name != wanted:
            continue
        if shutil.which(rec.name):
            return rec
    return None


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGINT if sys.platform != "win32" else signal.SIGTERM)
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)
    except OSError:
        pass


def record(out: Path, wait: Callable[[], object], max_seconds: float = MAX_SECONDS,
           recorder: Recorder | None = None) -> Path:
    """Record from the default microphone into out until wait() returns or max_seconds pass."""
    from pikina.voice import HINTS, VoiceError
    rec = recorder or find_recorder()
    if rec is None:
        raise VoiceError(f"No audio recorder found: {HINTS['recorder']}.")
    try:
        # its own session, so Ctrl+C in the terminal doesn't kill it before the header is written
        proc = subprocess.Popen(rec.argv(str(out)), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as exc:
        raise VoiceError(f"Couldn't start {rec.name}: {exc.strerror or exc}.") from exc
    timer = threading.Timer(max_seconds, _stop, [proc])
    timer.daemon = True
    timer.start()
    try:
        try:
            proc.wait(timeout=0.3)   # a recorder that can't open the mic exits at once
        except subprocess.TimeoutExpired:
            wait()
    finally:
        timer.cancel()
        _stop(proc)
    err = (proc.stderr.read() if proc.stderr else "").strip()
    if not out.exists() or out.stat().st_size <= 44:
        detail = err.splitlines()[-1] if err else f"{rec.name} exited with code {proc.returncode}"
        raise VoiceError(f"Nothing was recorded ({detail}). Is a microphone connected and not muted?")
    return out


def inspect(path: Path) -> dict:
    """Format, length and loudness of a WAV file. Raises wave.Error / EOFError for non-WAV data."""
    with wave.open(str(path), "rb") as w:
        rate, channels, width, frames = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        data = w.readframes(frames)
    rms = None
    if width == 2 and data:
        samples = array("h")
        samples.frombytes(data[: len(data) - len(data) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 if samples else 0.0
    return {"rate": rate, "channels": channels, "width": width,
            "seconds": frames / rate if rate else 0.0, "rms": rms}


def whisper_ready(info: dict) -> bool:
    return info["rate"] == RATE and info["channels"] == 1 and info["width"] == 2


def prepare(path: Path, workdir: Path) -> tuple[Path, dict]:
    """
    A 16 kHz mono 16-bit WAV for whisper.cpp and its inspect() info. Other files
    (another WAV format, mp3, ogg, m4a...) are converted with ffmpeg when it's installed.
    """
    from pikina.voice import VoiceError
    try:
        info = inspect(path)
        if whisper_ready(info):
            return path, info
    except (wave.Error, EOFError):
        pass
    except OSError as exc:
        raise VoiceError(f"Couldn't read {path}: {exc.strerror or exc}.") from exc
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VoiceError(f"{path.name} isn't a 16 kHz mono WAV. Install ffmpeg so Pikina can convert it.")
    out = workdir / "converted.wav"
    done = subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                           "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0 or not out.exists():
        last = (done.stderr or "").strip().splitlines()
        raise VoiceError(f"ffmpeg couldn't convert {path.name}" + (f": {last[-1]}" if last else "."))
    return out, inspect(out)


def is_silent(info: dict, threshold: float = SILENCE_RMS) -> bool:
    return info.get("rms") is not None and info["rms"] < threshold


def temp_dir():
    return tempfile.TemporaryDirectory(prefix="pikina-voice-")
