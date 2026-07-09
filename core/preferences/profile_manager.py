import json
import threading
from pathlib import Path

PROFILE_FILE = Path(__file__).parent / "profile.json"

class PreferencesProfileManager:
    _lock = threading.Lock()

    @classmethod
    def load(cls) -> dict:
        with cls._lock:
            try:
                if PROFILE_FILE.exists():
                    return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
            # Default profile fallback
            return {
                "preferred_name": "User",
                "formality": "neutral",
                "verbosity": "balanced",
                "humor": "none",
                "quiet_hours": ["23:00", "07:00"]
            }

    @classmethod
    def save(cls, data: dict) -> None:
        with cls._lock:
            PROFILE_FILE.parent.mkdir(exist_ok=True)
            PROFILE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def update_field(cls, key: str, value) -> dict:
        # Whitelist and validation
        valid_keys = {"preferred_name", "formality", "verbosity", "humor", "quiet_hours"}
        if key not in valid_keys:
            return {"status": "error", "reason": f"Unknown profile preference key: '{key}'"}

        data = cls.load()

        if key == "formality":
            val = str(value).lower().strip()
            if val not in ("casual", "neutral", "formal"):
                return {"status": "error", "reason": f"Invalid formality value '{value}'. Choose from: casual, neutral, formal."}
            data[key] = val

        elif key == "verbosity":
            val = str(value).lower().strip()
            if val not in ("concise", "balanced", "detailed"):
                return {"status": "error", "reason": f"Invalid verbosity value '{value}'. Choose from: concise, balanced, detailed."}
            data[key] = val

        elif key == "humor":
            val = str(value).lower().strip()
            if val not in ("none", "occasional", "frequent"):
                return {"status": "error", "reason": f"Invalid humor value '{value}'. Choose from: none, occasional, frequent."}
            data[key] = val

        elif key == "quiet_hours":
            if not isinstance(value, list) or len(value) != 2:
                return {"status": "error", "reason": "Quiet hours must be a list of two string times, e.g. ['23:00', '07:00']"}
            data[key] = value

        else:
            # preferred_name
            data[key] = str(value).strip()

        cls.save(data)
        return {"status": "ok", "message": f"Updated profile key '{key}' to: {data[key]}", "profile": data}
