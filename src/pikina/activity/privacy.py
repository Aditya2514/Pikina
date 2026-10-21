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
from pikina import settings
from pikina.osbridge.apps import canonical_app

DEFAULT_EXCLUDED_APPS = {
    # password managers and secret stores
    "keepassxc", "keepass", "keepass2", "keepassx", "bitwarden", "1password", "proton-pass",
    "enpass", "lastpass", "dashlane", "nordpass", "seahorse", "secrets", "kwalletmanager",
    "kwalletmanager5", "passwordsafe", "gopass", "qtpass",
    # authenticator apps
    "authy", "authenticator", "ente-auth", "otpclient",
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


def user_excluded_apps() -> set:
    extra = settings.get("activity_exclude_apps", []) or []
    if not isinstance(extra, list):
        return set()
    out = set()
    for name in extra:
        if isinstance(name, str) and name.strip():
            out.add(name.strip().lower())
            canon = canonical_app(name)
            if canon:
                out.add(canon)
    return out


def is_excluded(raw: str | None, canonical: str | None, extra: set | None = None) -> bool:
    names = {n for n in ((raw or "").strip().lower(), canonical or "") if n}
    blocked = DEFAULT_EXCLUDED_APPS | (extra if extra is not None else user_excluded_apps())
    if names & blocked:
        return True
    return any(n.startswith("pinentry") for n in names)


def is_pikina(raw: str | None, canonical: str | None) -> bool:
    return bool({(raw or "").strip().lower(), canonical or ""} & PIKINA_APPS)
