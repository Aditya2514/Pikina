"""
Linux side of the OS bridge: app resolution, consent fallback, screen-lock
fallback chain and the clipboard daemon. Skipped on Windows.
"""
import sys
import subprocess

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux-specific behaviour")

from core.osbridge import apps, consent as consent_mod, user_dirs  # noqa: E402
from core.daemons import clipboard_linux  # noqa: E402


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))

    def make(name, script="exit 0"):
        exe = bin_dir / name
        exe.write_text(f"#!/bin/sh\n{script}\n")
        exe.chmod(0o755)
        return exe
    return make


def test_friendly_names_map_to_installed_linux_apps(fake_bin):
    fake_bin("kate")
    assert apps.resolve_linux_command("notepad")[0].endswith("/kate")
    fake_bin("gnome-calculator")
    assert apps.resolve_linux_command("calculator")[0].endswith("/gnome-calculator")


def test_unknown_app_reports_not_found(fake_bin):
    result = apps.launch("definitely-not-installed")
    assert result["status"] == "error" and "not found" in result["reason"]


def test_desktop_entry_lookup(tmp_path, monkeypatch):
    d = tmp_path / "applications"
    d.mkdir()
    (d / "org.example.Spotify.desktop").write_text(
        "[Desktop Entry]\nName=Spotify\nExec=/opt/spotify/spotify %U\nType=Application\n")
    monkeypatch.setattr(apps, "_DESKTOP_DIRS", [d])
    assert apps.find_desktop_entry("spotify") == ["/opt/spotify/spotify"]


def test_launch_detaches_process(fake_bin, tmp_path):
    marker = tmp_path / "ran.txt"
    fake_bin("gedit", f'echo "$@" > {marker}')
    result = apps.launch("notepad")
    assert result["status"] == "ok" and result["pid"] > 0
    import time
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists()


def test_lock_screen_tries_lockers_in_order(fake_bin):
    fake_bin("loginctl", "exit 1")
    fake_bin("xdg-screensaver", "exit 0")
    result = apps.lock_screen()
    assert result["status"] == "ok" and "xdg-screensaver" in result["message"]


def test_lock_screen_without_any_locker(fake_bin):
    assert apps.lock_screen()["status"] == "error"


def test_consent_denies_when_nobody_can_be_asked(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(consent_mod, "_in_foreground_terminal", lambda: False)
    # (The autouse fixture replaces consent.ask, so check the two askers it chains.)
    assert consent_mod._ask_linux_gui("msg") is None
    assert consent_mod._ask_terminal("msg") is None


def test_consent_uses_zenity_exit_code(fake_bin, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    fake_bin("zenity", "exit 0")
    assert consent_mod._ask_linux_gui("Proceed?") is True
    fake_bin("zenity", "exit 1")
    assert consent_mod._ask_linux_gui("Proceed?") is False


def test_user_dirs_only_returns_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setattr("core.osbridge._xdg_user_dir", lambda key: None)
    assert user_dirs() == [tmp_path / "Downloads"]


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, topic, payload, provenance):
        self.events.append((topic, payload, provenance))


def _daemon_with_clipboard(monkeypatch, contents, types="text/plain"):
    state = {"text": contents, "types": types}

    def fake_run(cmd):
        return state["types"] if "--list-types" in cmd or "TARGETS" in cmd else state["text"]

    monkeypatch.setattr(clipboard_linux, "_run", fake_run)
    bus = _Bus()
    d = clipboard_linux.ClipboardDaemon(bus)
    d.backend = clipboard_linux._Backend(["wl-paste", "--list-types"], ["wl-paste"])
    return d, bus, state


def test_clipboard_publishes_only_changes_as_untrusted(monkeypatch):
    d, bus, state = _daemon_with_clipboard(monkeypatch, "copied before startup")
    d._poll(publish=False)          # startup snapshot is not ingested
    d._poll()                       # unchanged -> nothing
    state["text"] = "new text"
    d._poll()
    d._poll()                       # same again -> nothing
    assert [(t, p["content"], prov) for t, p, prov in bus.events] == [
        ("clipboard.copied", "new text", "UNTRUSTED_DATA")]


def test_clipboard_skips_password_manager_secrets(monkeypatch):
    d, bus, state = _daemon_with_clipboard(monkeypatch, "hunter2",
                                           types="text/plain\nx-kde-passwordManagerHint")
    d._poll()
    assert bus.events == []


def test_clipboard_caps_size(monkeypatch):
    d, bus, _ = _daemon_with_clipboard(monkeypatch, "x" * 20_000)
    d._poll()
    assert len(bus.events[0][1]["content"]) == 10_000
