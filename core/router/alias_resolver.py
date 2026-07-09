"""
Alias Resolver — Phase 3.5a
Loads user-defined trigger phrases from aliases.json and resolves
exact-phrase matches before the Tier 1 regex router runs.
"""
import json
import threading
from pathlib import Path

ALIASES_FILE = Path(__file__).parent / "aliases.json"

class AliasResolver:
    def __init__(self):
        self._lock = threading.Lock()
        self._aliases: list[dict] = []
        self._load()

    def _load(self):
        """Load aliases from disk. Safe to call at any time."""
        try:
            data = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
            with self._lock:
                self._aliases = data.get("aliases", [])
        except (FileNotFoundError, json.JSONDecodeError):
            with self._lock:
                self._aliases = []

    def _save(self):
        """Persist current aliases list to disk."""
        with self._lock:
            data = {"aliases": self._aliases}
        ALIASES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def resolve(self, text: str) -> dict | None:
        """
        Check if text exactly matches a defined alias trigger (case-insensitive).
        Returns the alias dict if matched, None otherwise.
        """
        normalized = text.strip().lower()
        with self._lock:
            for alias in self._aliases:
                if alias.get("trigger", "").strip().lower() == normalized:
                    return alias
        return None

    def list_aliases(self) -> list[dict]:
        with self._lock:
            return list(self._aliases)

    def add_alias(self, trigger: str, steps: list[dict]) -> dict:
        """
        Add a new alias. Returns error if trigger already exists.
        Each step: {"tool": "app.open", "params": {"path": "vscode"}}
        """
        normalized = trigger.strip().lower()
        with self._lock:
            for alias in self._aliases:
                if alias.get("trigger", "").strip().lower() == normalized:
                    return {"status": "error", "reason": f"Alias '{trigger}' already exists. Remove it first."}
            self._aliases.append({"trigger": trigger.strip(), "steps": steps})
        self._save()
        return {"status": "ok", "message": f"Alias '{trigger}' saved with {len(steps)} step(s)."}

    def remove_alias(self, trigger: str) -> dict:
        """Remove an alias by trigger phrase. Returns error if not found."""
        normalized = trigger.strip().lower()
        with self._lock:
            before = len(self._aliases)
            self._aliases = [
                a for a in self._aliases
                if a.get("trigger", "").strip().lower() != normalized
            ]
            removed = before - len(self._aliases)
        if removed == 0:
            return {"status": "error", "reason": f"No alias found with trigger '{trigger}'."}
        self._save()
        return {"status": "ok", "message": f"Alias '{trigger}' removed."}
