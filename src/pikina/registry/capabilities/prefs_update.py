from pikina.preferences.profile_manager import PreferencesProfileManager

def run(params: dict) -> dict:
    field = params.get("field", "").strip().lower()
    value = params.get("value")

    if not field:
        return {"status": "error", "reason": "Missing required param: 'field'"}
    if value is None:
        return {"status": "error", "reason": "Missing required param: 'value'"}

    return PreferencesProfileManager.update_field(field, value)


def parse(params: dict) -> dict:
    """"quiet hours from 23:00 to 07:00" arrives as start/end; the store wants a list."""
    if "start" in params and "end" in params:
        params["value"] = [params.pop("start"), params.pop("end")]
    return params
