"""
Action keys: what the activity log stores instead of the command you typed.

A key is "<tool>" or "<tool>:<qualifier>", e.g. "app.open:code", "fs.find_file:*.pdf",
"dev.free_port:3000". It keeps what predicts your next step (which app, which file
type, which port) and drops everything personal (to-do text, event titles, search
words, file names, paths, calculator input, your name).

Default-deny: a tool without a rule here — including any user plugin — is logged
by its tool id only. A qualifier must be one short lowercase token; anything else
becomes "?". Every core and built-in tool has an explicit rule (a test checks).
"""
import hashlib
import hmac
import re
import shutil
from datetime import date, datetime, timedelta

from pikina.activity import privacy
from pikina.osbridge.apps import canonical_app, known_app

_TOKEN = re.compile(r"^[a-z0-9*.][a-z0-9._*+-]{0,31}$")
_TOOL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# File types worth predicting. Anything else is "*.?" so a dotted name like
# "john.smith" can't leak a surname as an "extension".
FILE_TYPES = {
    "pdf", "doc", "docx", "odt", "rtf", "txt", "md", "tex", "bib", "epub",
    "xls", "xlsx", "ods", "csv", "tsv", "ppt", "pptx", "odp", "key", "pages", "numbers",
    "jpg", "png", "gif", "webp", "svg", "bmp", "tiff", "heic", "ico", "psd", "xcf", "raw",
    "mp3", "wav", "flac", "ogg", "m4a", "aac", "opus", "mp4", "mkv", "webm", "mov", "avi",
    "zip", "tar", "tar.gz", "tgz", "tar.xz", "tar.bz2", "tar.zst", "gz", "xz", "bz2", "7z", "rar", "zst",
    "iso", "img", "deb", "rpm", "appimage", "exe", "msi", "dmg", "apk", "jar",
    "py", "ipynb", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "kt", "c", "h", "cpp", "hpp", "cc",
    "cs", "go", "rs", "rb", "php", "swift", "scala", "lua", "r", "jl", "dart", "zig", "hs", "ml",
    "sh", "bash", "zsh", "fish", "ps1", "bat", "sql", "db", "sqlite",
    "html", "css", "scss", "sass", "json", "yaml", "toml", "ini", "cfg", "conf", "xml",
    "lock", "log", "env", "pem", "crt", "pub", "gpg", "asc", "kdbx", "ics", "vcf",
}
_COMPOUND = ("tar.gz", "tar.xz", "tar.bz2", "tar.zst")
_SYNONYM_EXT = {"jpeg": "jpg", "yml": "yaml", "htm": "html", "markdown": "md", "tif": "tiff"}
_LAUNCH_WRAPPERS = {"flatpak", "env", "sh", "bash", "snap", "gtk-launch", "gio", "xdg-open", "kioclient5",
                    "kioclient", "dex", "cmd", "start", "python", "python3"}

_STOPWATCH = {"start", "pause", "reset"}
_PREF_FIELDS = {"preferred_name", "formality", "verbosity", "humor", "quiet_hours"}


def _token(value) -> str:
    s = str(value).strip().lower()
    return s if _TOKEN.match(s) else "?"


def _join(tool: str, qualifier: str | None) -> str:
    return f"{tool}:{qualifier}" if qualifier else tool


# ---------------------------------------------------------------------------
# Per-tool rules
# ---------------------------------------------------------------------------

def _app_open(params: dict, result: dict | None) -> str:
    """
    A known app id, or the name of a program that is actually installed (on PATH).
    Never the typed text as such: "open resume.pdf" or a path must not leak into the log.
    """
    name = str(params.get("path") or "")
    app = known_app(name)
    if app:
        return privacy.EXCLUDED if privacy.is_excluded(name, app) else app
    for candidate in ((result or {}).get("launched"), name):
        if not candidate:
            continue
        candidate = str(candidate).strip().strip("\"'")
        base = re.split(r"[\\/]", candidate)[-1]
        for suffix in (".exe", ".cmd", ".bat", ".appimage"):
            if base.lower().endswith(suffix):
                base = base[: -len(suffix)]
        if not base or base.lower() in _LAUNCH_WRAPPERS:
            continue
        app = known_app(base)
        if app or shutil.which(base) or shutil.which(base.lower()):
            app = app or canonical_app(base)
            # password managers and your own exclusions are never named, not even as "opened"
            return privacy.EXCLUDED if privacy.is_excluded(base, app) else _token(app)
    if name and privacy.is_excluded(name, canonical_app(name)):
        return privacy.EXCLUDED
    return "?"


def file_type(name: str) -> str:
    """'*.pdf' for 'resume.PDF', '*' for no extension, '*.?' for an unknown one, '.*' for dotfiles."""
    last = re.split(r"[\\/]", str(name).strip().strip("\"'"))[-1].lower()
    glob = last.startswith("*")
    last = last.strip("*")
    if not last or "." not in last:
        return "*"
    if last.startswith(".") and last.count(".") == 1 and not glob:  # a dotfile such as .bashrc
        return ".env" if last == ".env" else ".*"
    for compound in _COMPOUND:
        if last.endswith("." + compound):
            return "*." + compound
    ext = last.rsplit(".", 1)[1]
    ext = _SYNONYM_EXT.get(ext, ext)
    return f"*.{ext}" if ext in FILE_TYPES else "*.?"


def _port(params: dict) -> str | None:
    value = params.get("port")
    if value in (None, ""):
        return None
    try:
        port = int(str(value).strip().removeprefix("port").strip())
    except ValueError:
        return "?"
    if not 0 < port < 65536:
        return "?"
    return "ephemeral" if port >= 32768 else str(port)


def _calendar_query(params: dict, when: date) -> str:
    if params.get("start_date") or params.get("end_date"):
        return "range"
    d = str(params.get("date") or "").strip().lower()
    if d in ("", "today"):
        return "today"
    if d == "tomorrow":
        return "tomorrow"
    try:
        asked = date.fromisoformat(d[:10])
    except ValueError:
        return "date"
    if asked == when:
        return "today"
    if asked == when + timedelta(days=1):
        return "tomorrow"
    return "date"


_alias_secrets: dict = {}


def _alias_secret() -> bytes:
    from pikina.activity.store import ActivityStore  # lazy: the store doesn't depend on keys
    store = ActivityStore(timeout=2.0)
    path = str(store.path)
    if path not in _alias_secrets:
        _alias_secrets[path] = (store.meta("alias_key") or "").encode("ascii")
    return _alias_secrets[path]


def alias_hash(trigger) -> str:
    """
    Aliases are stored by a keyed hash of their trigger, not the trigger itself: a trigger
    is free text you wrote ("text sarah"). The key is random per install and never
    exported, so the hash can't be reversed with a word list. The summary shows the
    name again by hashing your current aliases; a deleted alias stays anonymous.
    """
    s = " ".join(str(trigger or "").lower().split())
    if not s:
        return "?"
    return hmac.new(_alias_secret(), s.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


_BARE = {
    "todo.add", "todo.complete", "todo.remove", "todo.list",
    "calendar.add_event", "calendar.remove_event", "calendar.update_event", "calendar.import_ics",
    "clipboard.history", "memory.recall", "utility.calculate", "system.lock_screen",
    "alias.add", "alias.list", "alias.remove",
    "activity.summary", "activity.pause", "activity.forget",
}
RULES = {"app.open", "fs.find_file", "calendar.query", "utility.stopwatch", "prefs.update",
         "dev.doctor", "dev.ports", "dev.free_port", "alias.run"} | _BARE


def action_key(tool: str, params: dict | None, result: dict | None = None, when: datetime | None = None) -> str:
    """The privacy-safe, phrasing-independent key for one action."""
    tool = str(tool or "")
    if not _TOOL.match(tool):
        return "?"
    params = params if isinstance(params, dict) else {}
    result = result if isinstance(result, dict) else None

    if tool == "app.open":
        return _join(tool, _app_open(params, result))
    if tool == "fs.find_file":
        return _join(tool, file_type(params.get("name", "")))
    if tool == "calendar.query":
        return _join(tool, _calendar_query(params, (when or datetime.now()).astimezone().date()))
    if tool == "utility.stopwatch":
        action = str(params.get("action", "")).strip().lower()
        return _join(tool, action if action in _STOPWATCH else "?")
    if tool == "prefs.update":
        field = str(params.get("field", "")).strip().lower()
        return _join(tool, field if field in _PREF_FIELDS else "?")
    if tool == "dev.doctor":
        return _join(tool, "project" if params.get("path") else None)
    if tool in ("dev.ports", "dev.free_port"):
        return _join(tool, _port(params))
    if tool == "alias.run":
        return _join(tool, alias_hash(params.get("trigger")))
    return tool  # _BARE tools and anything unknown: the tool id only
