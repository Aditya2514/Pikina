from pikina.calendar.ics_import import import_ics

def run(params: dict) -> dict:
    filepath = params.get("filepath", "").strip()

    if not filepath:
        return {"status": "error", "reason": "Missing required param: 'filepath'"}

    # Run the import
    import os
    return import_ics(os.path.expanduser(filepath))
