"""dev.free_port — stop whatever listens on a port. Permission level 3 without rollback, so it always asks first."""
import os
import sys
import importlib.util
from pathlib import Path

import psutil

# Sibling capability files aren't a package; load the shared helper by path.
_spec = importlib.util.spec_from_file_location("devtools_ports", Path(__file__).with_name("dev_ports.py"))
_ports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ports)

parse = _ports.parse


def run(params: dict) -> dict:
    try:
        port = int(params.get("port"))
    except (TypeError, ValueError):
        return {"status": "error", "reason": "Which port? For example: free port 3000"}

    rows = _ports.listening(port)
    if not rows:
        return {"status": "error", "reason": f"Nothing is listening on port {port}."}
    if any(r["pid"] is None for r in rows):
        return {"status": "error", "reason": f"Port {port} is held by another user's program; freeing it needs sudo."}

    protected = {os.getpid(), os.getppid(), 1}
    if any(r["pid"] in protected for r in rows):
        return {"status": "error", "reason": f"Port {port} belongs to Pikina itself. Use the kill switch or quit Pikina instead."}

    procs = []
    for r in rows:
        try:
            procs.append(psutil.Process(r["pid"]))
        except psutil.NoSuchProcess:
            pass
    names = ", ".join(f"{p.name()} (pid {p.pid})" for p in procs)
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    _, still = psutil.wait_procs(alive, timeout=2)
    if still:
        return {"status": "error", "reason": f"Couldn't stop {names} on port {port}."}
    forced = " (it had to be force-stopped)" if alive else ""
    return {"status": "ok", "port": port, "stopped": [p.pid for p in procs],
            "message": f"Stopped {names}; port {port} is free{forced}."}
