from core.preferences.profile_manager import PreferencesProfileManager

def run(params: dict) -> dict:
    field = params.get("field", "").strip().lower()
    value = params.get("value")

    if not field:
        return {"status": "error", "reason": "Missing required param: 'field'"}
    if value is None:
        return {"status": "error", "reason": "Missing required param: 'value'"}

    return PreferencesProfileManager.update_field(field, value)
