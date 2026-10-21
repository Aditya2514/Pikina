"""
clipboard.history — the last few things you copied, newest first.
Copies are recorded by the clipboard daemon as UNTRUSTED_DATA: they're shown,
never executed. Password-manager secrets are skipped by the daemon.
"""
from datetime import datetime

from pikina.eventbus.replay import ReplayStore

_PREVIEW = 120


def _when(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%d %b %H:%M")
    except ValueError:
        return ts[:16]


def run(params: dict) -> dict:
    try:
        limit = max(1, min(int(params.get("limit", 10)), 50))
    except (TypeError, ValueError):
        limit = 10
    events = ReplayStore().query(since_minutes=7 * 24 * 60, topic="clipboard.copied",
                                 limit=limit, newest_first=True)
    items = [{"copied_at": e["timestamp"], "text": e["payload"].get("content", "")} for e in events]
    if not items:
        return {"status": "ok", "items": [],
                "message": "Nothing copied in the last 7 days (or the clipboard watcher isn't running)."}
    lines = []
    for it in items:
        one_line = " ".join(it["text"].split())
        preview = one_line if len(one_line) <= _PREVIEW else one_line[:_PREVIEW - 1] + "…"
        lines.append(f"{_when(it['copied_at'])}  {preview}")
    return {"status": "ok", "items": items, "message": "\n".join(lines)}
