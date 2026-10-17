import json
import threading
from pathlib import Path

from pikina import paths


def _profile_file() -> Path:
    return paths.data_file(paths.PROFILE)

class PreferencesProfileManager:
    _lock = threading.Lock()

    @classmethod
    def load(cls) -> dict:
        with cls._lock:
            try:
                if _profile_file().exists():
                    return json.loads(_profile_file().read_text(encoding="utf-8"))
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
            _profile_file().write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def update_field(cls, key: str, value) -> dict:
        # Whitelist and validation
        valid_keys = {"preferred_name", "formality", "verbosity", "humor", "quiet_hours"}
        if key not in valid_keys:
            return {"status": "error", "reason": f"Unknown profile preference key: '{key}'"}

        data = cls.load()
        previous_value = data.get(key)

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
        return {
            "status": "ok",
            "message": f"Updated profile key '{key}' to: {data[key]}",
            "profile": data,
            "previous_value": previous_value,  # lets a transaction roll this change back
        }
