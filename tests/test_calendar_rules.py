"""Calendar event types and repeats: one list everywhere, and no repeat that silently doesn't repeat."""
import pytest

from pikina.calendar.calendar_store import EVENT_TYPES, RECURRENCE
from pikina.server import create_app

H = {"X-Pikina-Token": "t"}


@pytest.fixture
def client():
    return create_app(start_daemons=False, enable_tier2=False, api_token="t").test_client()


def cap(client, tool, params):
    return client.post("/api/capability", json={"tool": tool, "params": params}, headers=H).get_json()


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_every_listed_type_is_kept(client, event_type):
    res = cap(client, "calendar.add_event", {"title": "x", "date": "2026-11-10", "type": event_type})
    assert res["event"]["type"] == event_type


def test_unknown_type_falls_back_to_personal(client):
    assert cap(client, "calendar.add_event", {"title": "x", "date": "2026-11-10", "type": "bogus"})["event"]["type"] == "personal"


def test_unsupported_repeats_are_refused_not_dropped(client):
    assert RECURRENCE == ("none", "yearly")
    res = cap(client, "calendar.add_event", {"title": "w", "date": "2026-11-10", "recurring": "weekly"})
    assert res["status"] == "error" and "yearly" in res["reason"]
    ev = cap(client, "calendar.add_event", {"title": "y", "date": "2026-11-10", "type": "appointment"})["event"]
    res = cap(client, "calendar.update_event", {"event_id": ev["id"], "title": "y", "date": "2026-11-11", "recurring": "monthly"})
    assert res["status"] == "error"
    res = cap(client, "calendar.update_event", {"event_id": ev["id"], "title": "y", "date": "2026-11-11",
                                                "recurring": "yearly", "type": "appointment"})
    assert res["status"] == "ok"


def test_manifests_and_store_agree():
    import json
    from pathlib import Path
    from pikina.registry import loader
    for name in ("calendar_add.json", "calendar_update.json"):
        props = json.loads((Path(loader._CAPABILITIES_DIR) / name).read_text())["params_schema"]["properties"]
        assert tuple(props["type"]["enum"]) == EVENT_TYPES
        assert tuple(props["recurring"]["enum"]) == RECURRENCE
