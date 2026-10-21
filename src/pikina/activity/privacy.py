"""
Apps the window tracker never records, and Pikina's own windows.

Password managers, unlock prompts and private-browsing windows are written as
the placeholder "excluded" — the app name is not kept, and that time is not
credited to the app you were in before. Add your own with the
"activity_exclude_apps" list in settings.json (e.g. a banking app).

Limits, stated plainly: Firefox private windows are detected from the window
title in English UIs only (the title is checked in memory and never stored).
Chrome, Brave and Edge incognito windows look identical to normal ones from
outside the browser; exclude the whole browser if that matters to you.
"""
import re

from pikina import settings
from pikina.osbridge.apps import canonical_app

DEFAULT_EXCLUDED_APPS = {
    # password managers and secret stores
    "keepassxc", "keepass", "keepass2", "keepassx", "bitwarden", "1password", "proton-pass",
    "enpass", "lastpass", "dashlane", "nordpass", "seahorse", "secrets", "kwalletmanager",
    "kwalletmanager5", "passwordsafe", "gopass", "qtpass",
    "keeweb", "buttercup", "keysmith",
    # authenticator apps
    "authy", "authenticator", "ente-auth", "enteauth", "io.ente.auth", "otpclient",
    # unlock and auth prompts
    "gcr-prompter", "pinentry", "pinentry-qt", "pinentry-gtk", "pinentry-gtk-2", "pinentry-gnome3",
    "polkit-gnome-authentication-agent-1", "polkit-kde-authentication-agent-1", "hyprpolkitagent",
    "lxqt-policykit-agent", "credentialuibroker", "consent",
    # browsers that are private by design
    "tor-browser", "torbrowser", "mullvad-browser",
}

# Electron's window class for the HUD and the quick panel (from desktop/package.json "name").
PIKINA_APPS = {"pikina-desktop", "pikina", "pikina-os", "electron"}

EXCLUDED = "excluded"   # placeholder written instead of an excluded app's name
NOTHING = "none"        # nothing focused (empty workspace)
PAUSED = "paused"       # marks where a pause began, so that time isn't credited to the last app

# Chromium-family "app mode" windows (a site opened as an app) get an id like
# chrome-chatgpt.com__-Default or brave-<extension id>-Default: that names the site.
_WEB_APP = re.compile(r"^(chrome|chromium|brave|msedge|vivaldi|opera|thorium)-.+-[^-]+$")
_CHANNEL = re.compile(r"-(stable|beta|unstable|dev|nightly|canary|snapshot|bin|git)$")


def web_app_browser(raw: str | None) -> str | None:
    """'chrome' for 'chrome-chatgpt.com__-Default', else None. The site part is never kept."""
    m = _WEB_APP.match((raw or "").strip().lower())
    return m.group(1) if m else None


def _variants(name: str | None) -> set:
    """The forms an app name is matched under: raw lowercase, canonical id, without a channel suffix."""
    out = set()
    if not name:
        return out
    raw = " ".join(str(name).strip().lower().split())
    if not raw:
        return out
    out.add(raw)
    browser = web_app_browser(raw)
    if browser:
        out.add(browser)
        out.add(canonical_app(browser) or browser)
    canon = canonical_app(raw)
    if canon:
        out.add(canon)
    for n in list(out):
        stripped = _CHANNEL.sub("", n)
        if stripped != n:
            out.add(stripped)
            out.add(canonical_app(stripped) or stripped)
    return out


def user_excluded_apps() -> set:
    """Your additions from settings.json. If the file can't be read, the last good list is used."""
    data, _ok = settings.read_checked()
    extra = data.get("activity_exclude_apps", [])
    if isinstance(extra, str):
        extra = [extra]
    if not isinstance(extra, list):
        return set()
    out = set()
    for name in extra:
        if isinstance(name, str):
            out |= _variants(name)
    return out


def is_excluded(raw: str | None, canonical: str | None = None, extra: set | None = None) -> bool:
    names = _variants(raw) | _variants(canonical)
    blocked = DEFAULT_EXCLUDED_APPS | (extra if extra is not None else user_excluded_apps())
    if names & blocked:
        return True
    return any(n.startswith("pinentry") for n in names)


def is_pikina(raw: str | None, canonical: str | None) -> bool:
    return bool({(raw or "").strip().lower(), canonical or ""} & PIKINA_APPS)
