"""
dev.doctor — a quick health check of your coding environment, and optionally of
one project folder. Read-only: it reports problems and never changes anything.
.env files are only checked for missing key names; values are never read out.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"
_ICON = {OK: "✓", WARN: "!", FAIL: "✗"}


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def _first_line(cmd, cwd=None):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else ""


def _system_checks() -> list:
    checks = []
    v = sys.version_info
    checks.append(_check("Python", OK if v >= (3, 10) else FAIL,
                         f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 10) else " (Pikina needs 3.10+)")))
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.append(_check("Virtualenv", OK if in_venv else WARN,
                         "Pikina runs inside a virtualenv" if in_venv else
                         "Pikina runs on the system Python; a virtualenv keeps its packages separate"))

    desktop = os.environ.get("XDG_CURRENT_DESKTOP")
    if sys.platform.startswith("linux") and desktop:
        checks.append(_check("Desktop", OK, f"{desktop} on {os.environ.get('XDG_SESSION_TYPE', 'unknown')}"))

    for tool, args, required in [("git", ["--version"], True), ("node", ["--version"], False),
                                 ("npm", ["--version"], False), ("gh", ["--version"], False)]:
        if shutil.which(tool):
            checks.append(_check(tool, OK, _first_line([tool, *args]) or "installed"))
        else:
            checks.append(_check(tool, FAIL if required else WARN, "not installed"))

    if sys.platform.startswith("linux"):
        wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        clip = shutil.which("wl-paste") if wayland else (shutil.which("xclip") or shutil.which("xsel"))
        checks.append(_check("Clipboard tool", OK if clip else WARN,
                             os.path.basename(clip) if clip else
                             ("install wl-clipboard" if wayland else "install xclip") + " so the clipboard watcher works"))
        dialog = shutil.which("zenity") or shutil.which("kdialog") or shutil.which("yad")
        checks.append(_check("Consent dialog", OK if dialog else WARN,
                             os.path.basename(dialog) if dialog else
                             "install zenity, otherwise high-risk actions are always denied"))
        if "hyprland" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            checks.append(_check("hyprctl", OK if shutil.which("hyprctl") else WARN,
                                 "available" if shutil.which("hyprctl") else "not found on PATH"))

    checks.append(_tracker_check())
    checks.append(_ollama_check())
    checks.append(_memory_check())
    checks.append(_disk_check())
    luks = _encryption_check()
    if luks:
        checks.append(luks)
    return checks


def _tracker_check():
    """Can the opt-in active-app tracker run in this session? (It never reads titles into storage.)"""
    try:
        from pikina import settings
        from pikina.osbridge.focus import detect_backend
        backend, reason = detect_backend()
        on = bool(settings.get("window_tracking", False))
    except Exception as exc:
        return _check("App tracking", WARN, f"couldn't check ({type(exc).__name__})")
    if backend:
        return _check("App tracking", OK, f"works here via {backend}; " + ("on" if on else "off (opt-in, in Settings)"))
    return _check("App tracking", WARN if on else OK, f"not available: {reason}")


def _ollama_check():
    import requests
    base = os.getenv("PIKINA_OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("PIKINA_OLLAMA_MODEL", "llama3")
    try:
        tags = requests.get(f"{base}/api/tags", timeout=2).json().get("models", [])
    except Exception:
        return _check("Ollama", WARN, f"not reachable at {base}; Tier 2 is offline")
    names = [m.get("name", "") for m in tags]
    if any(n == model or n.split(":")[0] == model for n in names):
        return _check("Ollama", OK, f"running, model '{model}' is pulled")
    return _check("Ollama", WARN, f"running, but model '{model}' isn't pulled: ollama pull {model}")


def _memory_check():
    if importlib.util.find_spec("sentence_transformers") is None:
        return _check("Memory", WARN, 'memory extra not installed: pip install "pikina[memory]"')
    hub = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    if (hub / "models--sentence-transformers--all-MiniLM-L6-v2").is_dir():
        return _check("Memory", OK, "embedding model downloaded")
    return _check("Memory", WARN, "embedding model missing: python -m pikina.memory.download")


def _disk_check():
    from pikina import paths
    free_gb = shutil.disk_usage(paths.data_dir()).free / 1e9
    status = OK if free_gb >= 5 else WARN if free_gb >= 1 else FAIL
    return _check("Free disk", status, f"{free_gb:.1f} GB free where Pikina keeps its data")


def _encryption_check():
    if not sys.platform.startswith("linux") or not shutil.which("lsblk"):
        return None
    try:
        out = subprocess.run(["lsblk", "-rno", "FSTYPE"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if "crypto_LUKS" in out.split():
        return _check("Disk encryption", OK, "a LUKS-encrypted partition is present")
    return _check("Disk encryption", WARN, "no LUKS-encrypted partition found; a stolen laptop would be readable")


def _env_keys(file: Path) -> set:
    keys = set()
    for line in file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].removeprefix("export ").strip())
    return keys


def _project_checks(root: Path) -> list:
    checks = []
    if (root / ".git").exists():
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v2", "--branch"],
                                capture_output=True, text=True, timeout=10).stdout.splitlines()
        branch = next((l.split()[2] for l in status if l.startswith("# branch.head")), "?")
        ahead = behind = 0
        for l in status:
            if l.startswith("# branch.ab"):
                _, _, a, b = l.split()
                ahead, behind = int(a), -int(b)
        changed = sum(1 for l in status if not l.startswith("#"))
        parts = [f"on {branch}", f"{changed} uncommitted change{'s' if changed != 1 else ''}"]
        if ahead:
            parts.append(f"{ahead} commit{'s' if ahead != 1 else ''} not pushed")
        if behind:
            parts.append(f"{behind} behind upstream, pull first")
        checks.append(_check("Git", WARN if behind else OK, ", ".join(parts)))

    if any((root / f).exists() for f in ("pyproject.toml", "requirements.txt", "setup.py")):
        venv = next((d for d in (".venv", "venv") if (root / d).is_dir()), None)
        checks.append(_check("Python env", OK if venv else WARN,
                             f"{venv}/ exists" if venv else "no .venv/ or venv/ folder; create one with python -m venv .venv"))

    if (root / "package.json").exists():
        has_modules = (root / "node_modules").is_dir()
        checks.append(_check("Node packages", OK if has_modules else WARN,
                             "node_modules/ installed" if has_modules else "node_modules/ missing; run npm install"))

    example = root / ".env.example"
    if example.exists():
        env = root / ".env"
        if not env.exists():
            checks.append(_check(".env", WARN, "missing; copy .env.example to .env and fill it in"))
        else:
            missing = sorted(_env_keys(example) - _env_keys(env))
            checks.append(_check(".env", WARN if missing else OK,
                                 f"missing keys: {', '.join(missing)}" if missing else "has every key from .env.example"))
    if not checks:
        checks.append(_check("Project", WARN, "no git repo, Python or Node project found here"))
    return checks


def run(params: dict) -> dict:
    checks = _system_checks()
    project = None
    if params.get("path"):
        root = Path(os.path.expanduser(params["path"].strip().strip("\"'"))).resolve()
        if not root.is_dir():
            return {"status": "error", "reason": f"No folder at {root}."}
        project = str(root)
        checks += [dict(c, name=f"{c['name']} ({root.name})") for c in _project_checks(root)]

    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (OK, WARN, FAIL)}
    summary = f"{counts[OK]} ok, {counts[WARN]} warning{'s' if counts[WARN] != 1 else ''}, {counts[FAIL]} problem{'s' if counts[FAIL] != 1 else ''}"
    order = {FAIL: 0, WARN: 1, OK: 2}
    lines = [f"{_ICON[c['status']]} {c['name']}: {c['detail']}" for c in sorted(checks, key=lambda c: order[c["status"]])]
    return {"status": "ok", "summary": summary, "project": project, "checks": checks,
            "message": summary + "\n" + "\n".join(lines)}
