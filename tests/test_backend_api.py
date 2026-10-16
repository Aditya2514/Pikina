"""
Backend HTTP API: token protection, the consent gate on /api/capability,
Tier 2 fallback for unmatched commands, and the kill switch.
"""
import os

import pytest

os.environ["PIKINA_NO_DAEMONS"] = "1"
os.environ.setdefault("PIKINA_TIER2", "1")

try:
    import backend_server  # noqa: E402
except SystemExit:  # another backend instance holds the single-instance lock
    pytest.skip("A Pikina backend is already running on this machine", allow_module_level=True)


@pytest.fixture
def client():
    backend_server.app.config["TESTING"] = True
    return backend_server.app.test_client()


@pytest.fixture
def auth():
    return {"X-Pikina-Token": backend_server.API_TOKEN}


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


def test_capability_endpoint_goes_through_consent_gate(client, auth, monkeypatch, consent):
    registry = backend_server.registry
    real_get = type(registry).get_manifest
    monkeypatch.setattr(registry, "get_manifest",
                        lambda t: dict(real_get(registry, t), permission_level=4) if t == "todo.add" else real_get(registry, t))
    consent.answer = False
    res = client.post("/api/capability", json={"tool": "todo.add", "params": {"text": "x"}}, headers=auth)
    assert res.get_json()["status"] == "denied"
    assert len(consent.calls) == 1


def test_unmatched_command_falls_through_to_tier2(client, auth, monkeypatch):
    calls = []
    monkeypatch.setattr(backend_server.tier2, "route",
                        lambda text: calls.append(text) or {"status": "ok", "tier": 2})
    res = client.post("/api/command", json={"text": "can you launch something to write text"}, headers=auth)
    assert res.get_json() == {"status": "ok", "tier": 2}
    assert calls == ["can you launch something to write text"]


def test_tier1_command_does_not_touch_tier2(client, auth, monkeypatch):
    monkeypatch.setattr(backend_server.tier2, "route", lambda text: pytest.fail("Tier 2 should not run"))
    res = client.post("/api/command", json={"text": "calculate 6 * 7"}, headers=auth)
    assert res.get_json()["result"] == 42


def test_kill_switch_halts_commands(client, auth):
    assert client.post("/api/killswitch", headers=auth).get_json()["kill_switch"] is True
    res = client.post("/api/command", json={"text": "calculate 1 + 1"}, headers=auth).get_json()
    assert res["status"] == "denied"
    res = client.post("/api/capability", json={"tool": "utility.calculate", "params": {"expr": "1+1"}},
                      headers=auth).get_json()
    assert res["status"] == "denied"
