"""
Level-4+ consent gatekeeper.
Shows a native Windows MessageBoxW before any destructive or irreversible action.
Irreversibility matters more than permission level — see blueprint §4.4.
"""
import ctypes

_MB_YESNO       = 0x04
_MB_ICONWARNING = 0x30
_IDYES          = 6


def request_consent(tool: str, description: str, params: dict) -> bool:
    """
    Show a native Windows confirmation dialog.
    Returns True only if the user explicitly clicks YES.
    """
    param_lines   = "\n".join(f"  {k}: {v}" for k, v in params.items()) or "  (no parameters)"
    message = (
        f"⚠  HIGH-RISK / IRREVERSIBLE ACTION\n\n"
        f"Tool:        {tool}\n"
        f"Description: {description}\n\n"
        f"Parameters:\n{param_lines}\n\n"
        f"This action may be irreversible. Proceed?"
    )
    result = ctypes.windll.user32.MessageBoxW(
        0,
        message,
        "Pikina OS — Confirm Action",
        _MB_YESNO | _MB_ICONWARNING,
    )
    return result == _IDYES
