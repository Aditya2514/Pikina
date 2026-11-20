"""
Voice input (Phase 5a). The recorder and whisper.cpp are separate programs, so
these tests put small fake programs on PATH in their place: no microphone, no
model and no compositor are needed.
"""
import json
import math
import shutil
import stat
import struct
import sys
import textwrap
import wave
from pathlib import Path

import pytest

from pikina import voice
from pikina.voice import VoiceError, audio, command, stt

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def write_wav(path: Path, seconds=1.0, rate=16000, channels=1, amplitude=3000, freq=440):
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        s = int(amplitude * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", s) * channels
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def fake_program(bindir: Path, name: str, body: str) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    p.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.fixture
def bindir(isolated_env, monkeypatch):
    """An empty PATH except for the fakes a test adds. The fakes are #! scripts, so POSIX only."""
    if sys.platform == "win32":
        pytest.skip("fake recorder/whisper programs are #! scripts")
    d = isolated_env / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    for var in ("PIKINA_WHISPER_BIN", "PIKINA_WHISPER_MODEL", "PIKINA_RECORDER"):
        monkeypatch.delenv(var, raising=False)
    return d


def fake_whisper(bindir: Path, says="List to-dos.", exit_code=0, log: Path | None = None):
    return fake_program(bindir, "whisper-cli", f"""
        import json, sys
        if {str(log) if log else ''!r}:
            open({str(log) if log else ''!r}, "w").write(json.dumps(sys.argv[1:]))
        if {exit_code}:
            print("error: failed to open model", file=sys.stderr)
            sys.exit({exit_code})
        print({says!r})
    """)


def fake_model(isolated_env, name="base.en") -> Path:
    d = isolated_env / "models"
    d.mkdir(exist_ok=True)
    p = d / f"ggml-{name}.bin"
    p.write_bytes(b"model")
    return p


class FakeMCM:
    def __init__(self):
        self.calls = []

    def receive(self, text, source="user_typed", origin="typed"):
        self.calls.append((text, source, origin))
        return {"status": "ok", "message": "done"}


def answers(*replies):
    it = iter(replies)
    asked = []

    def ask(prompt=""):
        asked.append(prompt)
        return next(it)
    ask.asked = asked
    return ask


# ---------------------------------------------------------------------------
# transcript cleaning: whisper writes sentences, the router wants commands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, cleaned", [
    (" Work on Pikina.\n", "Work on Pikina"),
    ("Doctor.", "Doctor"),
    ("List to-dos.", "List todos"),
    ("Add to-do buy milk.", "Add todo buy milk"),
    ("[BLANK_AUDIO]", ""),
    (" (music) Open VS Code! ", "Open VS Code"),
    ("find file resume.pdf.", "find file resume.pdf"),
    ("What's on port 5000?", "What's on port 5000"),
    ("\"open firefox\"", "open firefox"),
    ("", ""),
])
def test_clean_transcript(raw, cleaned):
    assert stt.clean_transcript(raw) == cleaned


@pytest.mark.parametrize("spoken, tool", [
    ("Doctor.", "dev.doctor"),
    ("List to-dos.", "todo.list"),
    ("What's on port 5000?", "dev.ports"),
])
def test_cleaned_transcripts_match_tier1(registry, spoken, tool):
    from pikina.router.tier1 import match_route
    routed = match_route(stt.clean_transcript(spoken), registry)
    assert routed and routed[0] == tool


def test_raw_whisper_output_would_miss(registry):
    from pikina.router.tier1 import match_route
    assert match_route("Doctor.", registry) is None
    assert match_route("Work on Pikina.", registry) == ("project.open", {"name": "Pikina."})
    assert match_route(stt.clean_transcript("Work on Pikina."), registry) == ("project.open", {"name": "Pikina"})



# ---------------------------------------------------------------------------
# finding whisper.cpp, the model and a recorder
# ---------------------------------------------------------------------------

def test_nothing_found_on_a_bare_system(bindir):
    status = voice.check()
    assert status == {"recorder": None, "whisper": None, "model": None}
    assert voice.missing(status) == [voice.HINTS["recorder"], voice.HINTS["whisper"], voice.HINTS["model"]]


def test_finds_whisper_on_path_and_by_override(bindir, isolated_env, monkeypatch):
    w = fake_whisper(bindir)
    assert stt.find_whisper() == str(w)
    other = fake_program(isolated_env / "elsewhere", "my-whisper", "print('x')")
    monkeypatch.setenv("PIKINA_WHISPER_BIN", str(other))
    assert stt.find_whisper() == str(other)
    monkeypatch.setenv("PIKINA_WHISPER_BIN", str(isolated_env / "missing"))
    assert stt.find_whisper() is None


def test_model_preference_and_overrides(bindir, isolated_env, monkeypatch):
    assert stt.find_model() is None
    fake_model(isolated_env, "tiny.en")
    assert stt.find_model().endswith("ggml-tiny.en.bin")
    fake_model(isolated_env, "base.en")
    assert stt.find_model().endswith("ggml-base.en.bin")       # base.en beats tiny.en

    custom = isolated_env / "custom.bin"
    custom.write_bytes(b"m")
    from pikina import settings
    settings.update({"voice_model": str(custom)})
    assert stt.find_model() == str(custom)
    monkeypatch.setenv("PIKINA_WHISPER_MODEL", str(isolated_env / "nope.bin"))
    assert stt.find_model() is None                             # a wrong override isn't silently replaced


def test_recorder_order_and_override(bindir, monkeypatch):
    assert audio.find_recorder() is None
    fake_program(bindir, "arecord", "")
    fake_program(bindir, "pw-record", "")
    assert audio.find_recorder().name == "pw-record"            # PipeWire first
    monkeypatch.setenv("PIKINA_RECORDER", "arecord")
    assert audio.find_recorder().name == "arecord"


def test_every_recorder_asks_for_16k_mono_s16():
    for rec in audio.RECORDERS:
        argv = " ".join(rec.argv("/tmp/x.wav"))
        assert "16000" in argv and argv.endswith("/tmp/x.wav")
        assert any(a in ("1", "--channels=1") for a in rec.argv("o"))   # one channel


# ---------------------------------------------------------------------------
# WAV checks
# ---------------------------------------------------------------------------

def test_inspect_and_silence(isolated_env):
    loud = audio.inspect(write_wav(isolated_env / "loud.wav"))
    assert loud["rate"] == 16000 and loud["channels"] == 1 and abs(loud["seconds"] - 1) < 0.01
    assert loud["rms"] > 1000 and not audio.is_silent(loud)
    quiet = audio.inspect(write_wav(isolated_env / "quiet.wav", amplitude=0))
    assert audio.is_silent(quiet)


def test_prepare_keeps_whisper_ready_wav(isolated_env):
    src = write_wav(isolated_env / "ok.wav")
    wav, info = audio.prepare(src, isolated_env)
    assert wav == src and audio.whisper_ready(info)


def test_prepare_without_ffmpeg_explains(bindir, isolated_env):
    src = write_wav(isolated_env / "cd.wav", rate=44100, channels=2, seconds=0.2)
    with pytest.raises(VoiceError, match="ffmpeg"):
        audio.prepare(src, isolated_env)
    junk = isolated_env / "notes.mp3"
    junk.write_bytes(b"not audio")
    with pytest.raises(VoiceError, match="ffmpeg"):
        audio.prepare(junk, isolated_env)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_prepare_converts_with_ffmpeg(isolated_env):
    src = write_wav(isolated_env / "cd.wav", rate=44100, channels=2, seconds=0.5)
    wav, info = audio.prepare(src, isolated_env)
    assert wav != src and audio.whisper_ready(info) and abs(info["seconds"] - 0.5) < 0.05


# ---------------------------------------------------------------------------
# recording (fake recorders that behave like pw-record / arecord)
# ---------------------------------------------------------------------------

RECORDER_UNTIL_SIGINT = """
    import math, signal, struct, sys, time, wave
    out = sys.argv[-1]
    stop = []
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))
    w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    t0 = time.time()
    while not stop and time.time() - t0 < 20:
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 3))) for i in range(1600)))
        time.sleep(0.05)
    w.close()
"""


def test_record_stops_when_wait_returns(bindir, isolated_env):
    fake_program(bindir, "pw-record", RECORDER_UNTIL_SIGINT)
    waited = []
    out = audio.record(isolated_env / "a.wav", wait=lambda: waited.append(1) or __import__("time").sleep(0.4))
    assert waited == [1]
    info = audio.inspect(out)
    assert info["seconds"] > 0.1 and not audio.is_silent(info)


def test_record_stops_by_itself_after_the_limit(bindir, isolated_env):
    import time
    fake_program(bindir, "pw-record", RECORDER_UNTIL_SIGINT)
    started = time.time()
    out = audio.record(isolated_env / "a.wav", wait=lambda: time.sleep(1.5), max_seconds=0.5)
    assert out.exists() and time.time() - started < 5


def test_recorder_that_cant_open_the_mic(bindir, isolated_env):
    fake_program(bindir, "arecord", """
        import sys
        print("arecord: main:830: audio open error: No such file or directory", file=sys.stderr)
        sys.exit(1)
    """)
    with pytest.raises(VoiceError, match="audio open error"):
        audio.record(isolated_env / "a.wav", wait=lambda: pytest.fail("must not wait for a dead recorder"))


def test_no_recorder_installed(bindir, isolated_env):
    with pytest.raises(VoiceError, match="No audio recorder"):
        audio.record(isolated_env / "a.wav", wait=lambda: None)


# ---------------------------------------------------------------------------
# transcription
# ---------------------------------------------------------------------------

def test_transcribe_runs_whisper_cli(bindir, isolated_env):
    log = isolated_env / "argv.json"
    fake_whisper(bindir, says=" Doctor.", log=log)
    model = fake_model(isolated_env)
    wav = write_wav(isolated_env / "a.wav")
    assert stt.transcribe(wav) == "Doctor"
    argv = json.loads(log.read_text())
    assert argv[argv.index("-m") + 1] == str(model)
    assert argv[argv.index("-f") + 1] == str(wav)
    assert "-nt" in argv and "-np" in argv and argv[argv.index("-l") + 1] == "en"


def test_transcribe_failures_say_what_to_do(bindir, isolated_env):
    wav = write_wav(isolated_env / "a.wav")
    with pytest.raises(VoiceError, match="whisper.cpp isn't installed"):
        stt.transcribe(wav)
    fake_whisper(bindir, exit_code=3)
    with pytest.raises(VoiceError, match="No speech model"):
        stt.transcribe(wav)
    fake_model(isolated_env)
    with pytest.raises(VoiceError, match="failed to open model"):
        stt.transcribe(wav)


# ---------------------------------------------------------------------------
# the `voice` command
# ---------------------------------------------------------------------------

def test_voice_check_lists_each_piece(bindir, isolated_env):
    lines = []
    assert command.handle(["check"], FakeMCM(), out=lines.append) is None
    text = "\n".join(lines)
    assert sum(l.lstrip().startswith("✗") for l in lines) == 3 and "Voice input is off" in text
    fake_whisper(bindir)
    fake_model(isolated_env)
    fake_program(bindir, "arecord", "")
    lines.clear()
    command.handle(["check"], FakeMCM(), out=lines.append)
    assert not any(l.lstrip().startswith("✗") for l in lines) and "Ready" in lines[-1]


def test_voice_without_setup_falls_back_to_typing(bindir, isolated_env):
    mcm, lines = FakeMCM(), []
    ask = answers()
    assert command.handle([], mcm, ask=ask, out=lines.append) is None
    assert mcm.calls == [] and ask.asked == []                  # never asked to speak into nothing
    assert "isn't set up" in lines[0] and "still type" in "\n".join(lines)


def test_voice_file_confirms_then_runs_as_spoken(bindir, isolated_env):
    fake_whisper(bindir, says="List to-dos.")
    fake_model(isolated_env)
    clip = write_wav(isolated_env / "clip.wav")
    mcm, lines = FakeMCM(), []
    result = command.handle([str(clip)], mcm, ask=answers(""), out=lines.append)
    assert result["status"] == "ok"
    assert mcm.calls == [("List todos", "user_spoke", "voice")]
    assert 'Heard: "List todos"' in "\n".join(lines)


def test_voice_file_declined_is_not_run(bindir, isolated_env):
    fake_whisper(bindir, says="remove todo buy milk")
    fake_model(isolated_env)
    mcm = FakeMCM()
    assert command.handle([str(write_wav(isolated_env / "c.wav"))], mcm, ask=answers("n"), out=lambda s: None) is None
    assert mcm.calls == []


def test_voice_confirm_can_be_turned_off(bindir, isolated_env):
    (isolated_env / "settings.json").write_text(json.dumps({"voice_confirm": False}))
    fake_whisper(bindir, says="doctor")
    fake_model(isolated_env)
    mcm = FakeMCM()
    command.handle([str(write_wav(isolated_env / "c.wav"))], mcm, ask=answers(), out=lambda s: None)
    assert mcm.calls == [("doctor", "user_spoke", "voice")]


def test_silence_is_reported_not_transcribed(bindir, isolated_env):
    log = isolated_env / "argv.json"
    fake_whisper(bindir, says="Thank you.", log=log)              # whisper's classic silence hallucination
    fake_model(isolated_env)
    mcm, lines = FakeMCM(), []
    command.handle([str(write_wav(isolated_env / "q.wav", amplitude=0))], mcm, ask=answers(), out=lines.append)
    assert mcm.calls == [] and not log.exists()
    assert "silence" in lines[0] and "muted" in lines[0]


def test_empty_transcript(bindir, isolated_env):
    fake_whisper(bindir, says="[BLANK_AUDIO]")
    fake_model(isolated_env)
    mcm, lines = FakeMCM(), []
    command.handle([str(write_wav(isolated_env / "c.wav"))], mcm, ask=answers(), out=lines.append)
    assert mcm.calls == [] and "Didn't catch" in lines[-1]


def test_missing_file(bindir, isolated_env):
    lines = []
    command.handle([str(isolated_env / "nope.wav")], FakeMCM(), out=lines.append)
    assert "No file" in lines[0]


def test_voice_from_the_microphone(bindir, isolated_env):
    fake_program(bindir, "pw-record", RECORDER_UNTIL_SIGINT)
    fake_whisper(bindir, says="Doctor.")
    fake_model(isolated_env)
    mcm, lines = FakeMCM(), []
    ask = answers("", "")                                         # Enter to stop, Enter to run
    command.handle([], mcm, ask=ask, out=lines.append)
    assert mcm.calls == [("Doctor", "user_spoke", "voice")]
    assert "Listening (pw-record)" in lines[0]


def test_piped_stdin_does_not_hang_or_crash(bindir, isolated_env):
    fake_program(bindir, "pw-record", RECORDER_UNTIL_SIGINT)
    fake_whisper(bindir, says="Doctor.")
    fake_model(isolated_env)

    def eof(prompt=""):
        raise EOFError
    mcm = FakeMCM()
    command.handle([], mcm, ask=eof, out=lambda s: None)
    assert mcm.calls == []                                        # no answer means not confirmed


def test_spoken_command_runs_through_the_real_router(bindir, isolated_env, registry):
    from pikina.activity.store import ActivityStore
    from pikina.mcm.orchestrator import Orchestrator
    from pikina.router.tier1 import Tier1Router
    fake_whisper(bindir, says="List to-dos.")
    fake_model(isolated_env)
    mcm = Orchestrator(router=Tier1Router(registry=registry))
    result = command.handle([str(write_wav(isolated_env / "c.wav"))], mcm, ask=answers(""), out=lambda s: None)
    assert result["status"] == "ok"
    assert [r["source"] for r in ActivityStore().recent()] == ["voice"]


# ---------------------------------------------------------------------------
# doctor and the model download
# ---------------------------------------------------------------------------

def test_doctor_reports_voice(bindir, isolated_env, registry):
    from pikina.plugins.devtools.capabilities import dev_doctor
    check = dev_doctor._voice_check()
    assert check["status"] == "warn" and "python -m pikina.voice.download" in check["detail"]
    fake_whisper(bindir)
    fake_model(isolated_env)
    fake_program(bindir, "parecord", "")
    check = dev_doctor._voice_check()
    assert check["status"] == "ok" and "parecord" in check["detail"] and "ggml-base.en.bin" in check["detail"]


class FakeResponse:
    def __init__(self, chunks, total=None, fail=False):
        self.chunks, self.fail = chunks, fail
        self.headers = {"content-length": str(total if total is not None else sum(map(len, chunks)))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.fail:
            import requests
            raise requests.HTTPError("404")

    def iter_content(self, chunk_size):
        yield from self.chunks


def test_download_model(isolated_env, monkeypatch):
    import requests
    from pikina.voice import download
    got = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: got.append(url) or FakeResponse([b"ab", b"cd"]))
    assert download.download("tiny.en", out=lambda s: None) == 0
    target = isolated_env / "models" / "ggml-tiny.en.bin"
    assert target.read_bytes() == b"abcd" and got[0].endswith("/ggml-tiny.en.bin")
    assert stt.find_model() == str(target)
    assert download.download("tiny.en", out=lambda s: None) == 0 and len(got) == 1   # not downloaded twice


def test_download_cut_short_leaves_nothing(isolated_env, monkeypatch):
    import requests
    from pikina.voice import download
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResponse([b"ab"], total=10))
    assert download.download("base.en", out=lambda s: None) == 1
    assert list((isolated_env / "models").iterdir()) == []
    assert download.download("huge", out=lambda s: None) == 2
