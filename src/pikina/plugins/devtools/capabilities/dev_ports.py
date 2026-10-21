"""dev.ports — which program is listening on which port."""
import psutil


def listening(port: int | None = None) -> list[dict]:
    """Listening TCP sockets, one row per (port, pid). pid is None for other users' processes."""
    try:
        conns = psutil.net_connections(kind="tcp")
    except psutil.AccessDenied:
        conns = []
    rows, seen = [], set()
    for c in conns:
        if not c.laddr or c.status != psutil.CONN_LISTEN:
            continue
        if port is not None and c.laddr.port != port:
            continue
        key = (c.laddr.port, c.pid)
        if key in seen:
            continue  # IPv4 and IPv6 sockets of the same program
        seen.add(key)
        name = None
        if c.pid:
            try:
                name = psutil.Process(c.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        rows.append({"port": c.laddr.port, "address": c.laddr.ip, "pid": c.pid, "process": name})
    return sorted(rows, key=lambda r: (r["port"], r["pid"] or 0))


def describe(row: dict) -> str:
    if row["pid"] is None:
        return f"port {row['port']}: a program owned by another user (needs sudo to see)"
    return f"port {row['port']}: {row['process'] or 'unknown'} (pid {row['pid']})"


def parse(params: dict) -> dict:
    if "port" in params:
        params["port"] = int(params["port"])
    return params


def run(params: dict) -> dict:
    port = params.get("port")
    if port is not None:
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"status": "error", "reason": f"'{port}' isn't a port number."}
    rows = listening(port)
    if port is not None and not rows:
        return {"status": "ok", "port": port, "ports": [], "message": f"Nothing is listening on port {port}."}
    if not rows:
        return {"status": "ok", "ports": [], "message": "No programs are listening on any port."}
    lines = [describe(r) for r in rows[:25]]
    if len(rows) > 25:
        lines.append(f"…and {len(rows) - 25} more")
    return {"status": "ok", "port": port, "ports": rows, "message": "\n".join(lines)}
