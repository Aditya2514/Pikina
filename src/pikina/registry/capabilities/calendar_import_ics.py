from pikina.calendar.ics_import import import_ics

def run(params: dict) -> dict:
    filepath = params.get("filepath", "").strip()

    if not filepath:
        return {"status": "error", "reason": "Missing required param: 'filepath'"}

    # Run the import
    import os
    result = import_ics(os.path.expanduser(filepath))
    if isinstance(result, dict) and result.get("status") == "ok" and "message" not in result:
        n, bad = result.get("added_count", 0), result.get("skipped_errors", 0)
        result["message"] = f"Imported {n} event{'s' if n != 1 else ''}" + (f", skipped {bad}." if bad else ".")
    return result


def parse(params: dict) -> dict:
    """Allow a quoted path: import ics "~/Downloads/my invite.ics"."""
    params["filepath"] = params["filepath"].strip("\"'")
    return params
