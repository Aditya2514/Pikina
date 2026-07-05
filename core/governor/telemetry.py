"""
Resource Governor — live system telemetry via psutil.
These are the same numbers the dashboard shows and the cost function uses.
"""
import os
import psutil
from datetime import datetime, timezone


def get_telemetry() -> dict:
    """
    Returns a snapshot of current system resource usage.
    Safe to call from any thread.
    """
    cpu_pct = psutil.cpu_percent(interval=0.05)
    ram     = psutil.virtual_memory()
    
    # Use system root drive on Windows to avoid network mount timeout
    root_drive = os.path.abspath(os.sep)
    disk       = psutil.disk_usage(root_drive)

    battery = None
    try:
        bat = psutil.sensors_battery()
        if bat:
            secs = bat.secsleft
            battery = {
                "percent":  round(bat.percent, 1),
                "plugged":  bat.power_plugged,
                "secs_left": secs if secs not in (
                    psutil.POWER_TIME_UNLIMITED,
                    psutil.POWER_TIME_UNKNOWN,
                ) else None,
            }
    except Exception:
        pass

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu": {
            "percent":    round(cpu_pct, 1),
            "core_count": psutil.cpu_count(logical=True),
        },
        "ram": {
            "total_gb": round(ram.total / 1e9, 1),
            "used_gb":  round(ram.used  / 1e9, 1),
            "percent":  round(ram.percent, 1),
        },
        "disk": {
            "total_gb": round(disk.total / 1e9, 1),
            "used_gb":  round(disk.used  / 1e9, 1),
            "percent":  round(disk.percent, 1),
        },
        "battery": battery,
    }
