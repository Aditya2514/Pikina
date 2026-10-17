"""
Consent gatekeeper for high-risk actions.
Shows a native confirmation dialog (MessageBoxW on Windows, zenity/kdialog on Linux)
before any destructive or irreversible action.
Irreversibility matters more than permission level — see blueprint §4.4.
"""
from pikina.osbridge import consent


def needs_consent(manifest: dict) -> bool:
    """
    The single gating rule shared by every execution path (Tier 1, Tier 2, API):
    Level 4+ always asks; Level 3 asks when the action cannot be rolled back.
    """
    level = manifest.get("permission_level", 0)
    return level >= 4 or (level >= 3 and not manifest.get("supports_rollback", False))


def request_consent(tool: str, description: str, params: dict) -> bool:
    """
    Show a native confirmation dialog.
    Returns True only if the user explicitly says yes.
    """
    param_lines = "\n".join(f"  {k}: {v}" for k, v in params.items()) or "  (no parameters)"
    message = (
        f"⚠  HIGH-RISK / IRREVERSIBLE ACTION\n\n"
        f"Tool:        {tool}\n"
        f"Description: {description}\n\n"
        f"Parameters:\n{param_lines}\n\n"
        f"This action may be irreversible. Proceed?"
    )
    return consent.ask(message)
