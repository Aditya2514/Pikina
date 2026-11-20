"""
pikina-voice: press once to listen, press again to stop. These run the real
entry point as separate processes (that's how a compositor keybind runs it),
with fake pw-record, whisper-cli and notify-send on PATH.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_voice import RECORDER_UNTIL_SIGINT, fake_model, fake_program, fake_whisper, write_wav

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pikina-voice is Linux only")

NOTIFY = """
    import json, os, sys
    with open(os.environ["NOTIFY_LOG"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
"""


@pytest.fixture
def env(isolated_env, monkeypatch):
    bindir = isolated_env / "bin"
    runtime = isolated_env / "run"
    runtime.mkdir()
    fake_program(bindir, "pw-record", RECORDER_UNTIL_SIGINT)
    fake_program(bindir, "notify-send", NOTIFY)
    fake_whisper(bindir, says="List to-dos.")
    fake_model(isolated_env)
    e = {k: v for k, v in os.environ.items() if not k.startswith(("PIKINA_WHISPER", "PIKINA_RECORDER"))}
    e.update(PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}", PIKINA_DATA_DIR=str(isolated_env),
             XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY="wayland-test",
             NOTIFY_LOG=str(isolated_env / "notify.log"))
    e.pop("DISPLAY", None)
    return e


def run(env, *args, wait=True):
    cmd = [sys.executable, "-m", "pikina.voice.hotkey", *args]
    if wait:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def notes(env):
    log = Path(env["NOTIFY_LOG"])
    return [json.loads(l)[-2:] for l in log.read_text().splitlines()] if log.exists() else []


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def no_confirm(isolated_env):
    (isolated_env / "settings.json").write_text(json.dumps({"voice_confirm": False}))


def test_press_twice_runs_the_spoken_command(env, isolated_env):
    no_confirm(isolated_env)
    pidfile = Path(env["XDG_RUNTIME_DIR"]) / "pikina-voice.pid"
    first = run(env, wait=False)
    assert wait_for(pidfile.exists), first.communicate(timeout=5)
    time.sleep(0.5)                                      # "speak"
    assert run(env).returncode == 0                      # second press stops it
    out, err = first.communicate(timeout=30)
    assert first.returncode == 0, out + err
    assert not pidfile.exists()
    titles = [t for t, _ in notes(env)]
    assert titles[0] == "Listening…" and titles[-1] == '"List todos"'
    assert notes(env)[-1][1] == "No matching tasks found."

    from pikina.activity.store import ActivityStore
    assert [r["source"] for r in ActivityStore().recent()] == ["voice"]


def test_without_a_dialog_the_command_is_not_run(env, isolated_env):
    # voice_confirm is on by default; no zenity/kdialog/yad and no terminal means "no"
    clip = write_wav(isolated_env / "c.wav")
    done = run(env, str(clip))
    assert done.returncode == 0
    assert notes(env)[-1] == ["Not run", '"List todos"']


def test_file_mode_and_failures_are_notified(env, isolated_env):
    no_confirm(isolated_env)
    assert run(env, str(isolated_env / "missing.wav")).returncode == 1
    assert notes(env)[-1][0] == "Voice input" and "No file" in notes(env)[-1][1]
    quiet = write_wav(isolated_env / "q.wav", amplitude=0)
    assert run(env, str(quiet)).returncode == 1
    assert "silence" in notes(env)[-1][1]


def test_not_set_up_says_what_is_missing(env, isolated_env):
    (isolated_env / "models" / "ggml-base.en.bin").unlink()
    done = run(env)
    assert done.returncode == 1
    title, body = notes(env)[-1]
    assert title == "Voice input isn't set up" and "pikina.voice.download" in body
    assert not (Path(env["XDG_RUNTIME_DIR"]) / "pikina-voice.pid").exists()


def test_stale_pidfile_is_ignored(env, isolated_env):
    no_confirm(isolated_env)
    pidfile = Path(env["XDG_RUNTIME_DIR"]) / "pikina-voice.pid"
    pidfile.write_text("999999")                         # a process that's gone
    assert run(env, "--stop").returncode == 0
    assert not pidfile.exists()


def test_never_signals_a_foreign_process(env, isolated_env):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pidfile = Path(env["XDG_RUNTIME_DIR"]) / "pikina-voice.pid"
        pidfile.write_text(str(sleeper.pid))
        assert run(env, "--stop").returncode == 0
        assert sleeper.poll() is None                    # not a pikina process: left alone
    finally:
        sleeper.kill()


def test_works_without_notify_send(env, isolated_env):
    no_confirm(isolated_env)
    (Path(env["PATH"].split(os.pathsep)[0]) / "notify-send").unlink()
    done = run(env, str(write_wav(isolated_env / "c.wav")))
    assert done.returncode == 0 and '[Pikina voice] "List todos"' in done.stdout
