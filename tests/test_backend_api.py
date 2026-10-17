"""
Backend HTTP API: token protection, the consent gate on /api/capability,
Tier 2 fallback for unmatched commands, and the kill switch.
"""
import pytest

from pikina.server import create_app


@pytest.fixture
def app():
    app = create_app(start_daemons=False, enable_tier2=True, api_token="test-token")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def backend(app):
    return app.extensions["pikina"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth():
    return {"X-Pikina-Token": "test-token"}


def test_requests_without_token_are_rejected(client):
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/command", json={"text": "lock screen"}).status_code == 401
    assert client.post("/api/capability", data='{"tool": "app.open", "params": {"path": "x"}}',
                       content_type="text/plain").status_code == 401


def test_wrong_token_rejected(client):
    assert client.get("/api/status", headers={"X-Pikina-Token": "guess"}).status_code == 401


def test_status_reports_tier2_and_kill_switch(client, auth):
    body = client.get("/api/status", headers=auth).get_json()
    assert body["status"] == "online"
    assert body["tier2"]["enabled"] is True
    assert body["kill_switch"] is False


def test_capability_endpoint_goes_through_consent_gate(client, auth, backend, monkeypatch, consent):
    registry = backend.registry
    real_get = type(registry).get_manifest
    monkeypatch.setattr(registry, "get_manifest",
                        lambda t: dict(real_get(registry, t), permission_level=4) if t == "todo.add" else real_get(registry, t))
    consent.answer = False
    res = client.post("/api/capability", json={"tool": "todo.add", "params": {"text": "x"}}, headers=auth)
    assert res.get_json()["status"] == "denied"
    assert len(consent.calls) == 1


def test_unmatched_command_falls_through_to_tier2(client, auth, backend, monkeypatch):
    calls = []
    monkeypatch.setattr(backend.tier2, "route",
                        lambda text: calls.append(text) or {"status": "ok", "tier": 2})
    res = client.post("/api/command", json={"text": "can you launch something to write text"}, headers=auth)
    assert res.get_json() == {"status": "ok", "tier": 2}
    assert calls == ["can you launch something to write text"]


def test_tier1_command_does_not_touch_tier2(client, auth, backend, monkeypatch):
    monkeypatch.setattr(backend.tier2, "route", lambda text: pytest.fail("Tier 2 should not run"))
    res = client.post("/api/command", json={"text": "calculate 6 * 7"}, headers=auth)
    assert res.get_json()["result"] == 42


def test_kill_switch_halts_commands(client, auth):
    assert client.post("/api/killswitch", headers=auth).get_json()["kill_switch"] is True
    res = client.post("/api/command", json={"text": "calculate 1 + 1"}, headers=auth).get_json()
    assert res["status"] == "denied"
    res = client.post("/api/capability", json={"tool": "utility.calculate", "params": {"expr": "1+1"}},
                      headers=auth).get_json()
    assert res["status"] == "denied"


def test_settings_are_stored_in_the_data_folder(client, auth, isolated_env):
    client.post("/api/settings", json={"background3d": False}, headers=auth)
    assert client.get("/api/settings", headers=auth).get_json() == {"background3d": False}
    assert (isolated_env / "settings.json").exists()


def test_importing_server_has_no_side_effects():
    import importlib
    import threading
    before = {t.name for t in threading.enumerate()}
    importlib.reload(importlib.import_module("pikina.server"))
    assert {t.name for t in threading.enumerate()} - before == set()
