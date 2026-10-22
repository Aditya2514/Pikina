"""
Opening things for the project switcher, per OS: an editor on a folder, a terminal
in a folder (optionally running a command, then staying open), a Hyprland/Sway
workspace, and web links. Everything is started detached and never waited on.
"""
import json
import os
import re
import shlex
import shutil
import subprocess

from pikina.osbridge import IS_WINDOWS, apps

# Terminal emulators we know how to point at a folder, in the order we try them.
# Each builder gets (exe, folder, command_argv or None) and returns the full argv.
_TERMINALS = {
    "kitty":          lambda exe, d, cmd: [exe, "--directory", d] + (cmd or []),
    "alacritty":      lambda exe, d, cmd: [exe, "--working-directory", d] + (["-e"] + cmd if cmd else []),
    "foot":           lambda exe, d, cmd: [exe, "--working-directory", d] + (cmd or []),
    "footclient":     lambda exe, d, cmd: [exe, "--working-directory", d] + (cmd or []),
    "wezterm":        lambda exe, d, cmd: [exe, "start", "--cwd", d] + (["--"] + cmd if cmd else []),
    "ghostty":        lambda exe, d, cmd: [exe, f"--working-directory={d}"] + (["-e"] + cmd if cmd else []),
    "gnome-terminal": lambda exe, d, cmd: [exe, f"--working-directory={d}"] + (["--"] + cmd if cmd else []),
    "konsole":        lambda exe, d, cmd: [exe, "--workdir", d] + (["-e"] + cmd if cmd else []),
    "xfce4-terminal": lambda exe, d, cmd: [exe, f"--working-directory={d}"] + (["-x"] + cmd if cmd else []),
    "xterm":          lambda exe, d, cmd: [exe] + (["-e"] + cmd if cmd else []),   # runs with cwd=d
}
_WORKSPACE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_URL = re.compile(r"^https?://[^\s\"'<>]+$", re.I)
# Editors that run inside a terminal: started detached they'd run invisibly, so they get one.
TERMINAL_EDITORS = {"vim", "nvim", "neovim", "vi", "helix", "hx", "nano", "micro", "kak", "kakoune", "emacs-nw"}
_CMD_META = set('&|<>^%!"()')  # cmd.exe would misread these in a folder passed to a .cmd/.bat


def _spawn(argv: list, cwd: str | None = None) -> int:
    """Start a program detached from Pikina; returns its pid."""
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd)
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
            getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs).pid


def find_terminal(preferred: str | None = None, env=None) -> tuple[str, str] | None:
    """(name, path) of the terminal to use: your choice, then $TERMINAL, then the first one installed."""
    env = os.environ if env is None else env
    order = []
    for choice in (preferred, env.get("TERMINAL")):
        if choice:
            order.append(os.path.basename(str(choice)).strip().lower())
    order += list(_TERMINALS)
    for name in order:
        if name in _TERMINALS:
            exe = shutil.which(name)
            if exe:
                return name, exe
    return None


def shell_command(command: str, env=None) -> list:
    """
    argv that runs `command` in your shell and then leaves you in that shell. Ctrl+C stops
    the command but not the terminal: a tiny sh wrapper catches INT (with a no-op trap, so
    the command itself still gets Ctrl+C) and then becomes your interactive shell.
    """
    env = os.environ if env is None else env
    shell = env.get("SHELL") or "/bin/sh"
    return ["/bin/sh", "-c", 'trap : INT; "$1" -c "$2"; exec "$1"', "sh", shell, command]


def terminal_argv(name: str, exe: str, folder: str, command: str | None = None, env=None) -> list:
    return _TERMINALS[name](exe, folder, shell_command(command, env) if command else None)


def open_terminal(folder: str, command: str | None = None, preferred: str | None = None) -> dict:
    try:
        return _open_terminal(folder, command, preferred)
    except OSError as exc:
        return {"status": "error", "reason": f"Couldn't start a terminal ({exc.strerror or type(exc).__name__})."}


def _open_terminal(folder: str, command: str | None, preferred: str | None) -> dict:
    if IS_WINDOWS:
        wt = shutil.which("wt")
        if wt:
            argv = [wt, "-d", folder] + (["cmd", "/K", command] if command else [])
            return {"status": "ok", "terminal": "wt", "pid": _spawn(argv)}
        argv = ["cmd", "/K", command] if command else ["cmd", "/K"]
        proc = subprocess.Popen(argv, cwd=folder, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return {"status": "ok", "terminal": "cmd", "pid": proc.pid}
    found = find_terminal(preferred)
    if not found:
        return {"status": "error", "reason": "No terminal found (install kitty, foot or alacritty, or set $TERMINAL)."}
    name, exe = found
    return {"status": "ok", "terminal": name, "pid": _spawn(terminal_argv(name, exe, folder, command), cwd=folder)}


def open_editor(editor: str, folder: str, terminal: str | None = None) -> dict:
    """Open `folder` in an editor given by friendly name ("vs code", "code") or executable.
    Terminal editors (nvim, helix, ...) are opened inside a terminal."""
    name = os.path.basename(editor.strip()).lower()
    if name in TERMINAL_EDITORS and not IS_WINDOWS:
        if not shutil.which(editor.strip()):
            return {"status": "error", "reason": f"Editor not found: '{editor}'."}
        result = open_terminal(folder, f"{shlex.quote(editor.strip())} .", terminal)
        return dict(result, editor=name) if result.get("status") == "ok" else result
    if IS_WINDOWS:
        names = apps.WINDOWS_APPS.get(editor.lower(), [editor])
        exe = next((shutil.which(n) for n in names if shutil.which(n)), None)
        argv = [exe] if exe else None
        if exe and exe.lower().endswith((".cmd", ".bat")) and _CMD_META & set(folder):
            return {"status": "error", "reason": "This folder's name has characters (& | < > ^ % ! \" ( )) "
                                                 "that the editor's launcher can't pass safely on Windows."}
    else:
        argv = apps.resolve_linux_command(editor)
    if not argv:
        return {"status": "error", "reason": f"Editor not found: '{editor}'."}
    try:
        pid = _spawn(list(argv) + [folder], cwd=folder)
    except OSError as exc:
        return {"status": "error", "reason": f"Couldn't start '{editor}' ({exc.strerror or type(exc).__name__})."}
    return {"status": "ok", "editor": os.path.basename(argv[0]), "pid": pid}


def open_url(url: str) -> dict:
    if not _URL.match(url or ""):
        return {"status": "error", "reason": "Only http(s) links can be opened."}
    try:
        if IS_WINDOWS:
            os.startfile(url)  # noqa: S606 — the default browser
            return {"status": "ok"}
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            return {"status": "error", "reason": "xdg-open not found."}
        argv = [opener, url] if opener.endswith("xdg-open") else [opener, "open", url]
        return {"status": "ok", "pid": _spawn(argv)}
    except OSError as exc:
        return {"status": "error", "reason": f"Couldn't open the link ({exc.strerror or type(exc).__name__})."}


def switch_workspace(workspace, env=None) -> dict:
    """Jump to a Hyprland or Sway workspace, so what opens next lands there."""
    env = os.environ if env is None else env
    ws = str(workspace).strip()
    if not _WORKSPACE.match(ws):
        return {"status": "error", "reason": f"Not a workspace name: '{ws}'."}
    if env.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        hyprctl = shutil.which("hyprctl")
        if ws in _hyprland_active_workspace(hyprctl):
            return {"status": "ok", "workspace": ws}  # already there: don't trigger back-and-forth
        argv = [hyprctl, "dispatch", "workspace", ws if ws.isdigit() else f"name:{ws}"]
    elif (env.get("SWAYSOCK") or env.get("I3SOCK")) and (shutil.which("swaymsg") or shutil.which("i3-msg")):
        tool = shutil.which("swaymsg") or shutil.which("i3-msg")
        target = ["number", ws] if ws.isdigit() else [ws]
        argv = [tool, "workspace", "--no-auto-back-and-forth"] + target
    else:
        return {"status": "skipped", "reason": "Workspaces need Hyprland or Sway."}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "reason": f"Couldn't switch workspace ({type(exc).__name__})."}
    if done.returncode != 0:
        return {"status": "error", "reason": "The compositor refused the workspace switch."}
    return {"status": "ok", "workspace": ws}


def _hyprland_active_workspace(hyprctl: str) -> set:
    """The current workspace's id and name (empty if it can't be read)."""
    try:
        out = subprocess.run([hyprctl, "activeworkspace", "-j"], capture_output=True, text=True, timeout=2)
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(v) for v in (data.get("id"), data.get("name")) if v not in (None, "")}
