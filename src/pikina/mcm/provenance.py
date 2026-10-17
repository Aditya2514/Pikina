"""
Provenance tagging — the first gate every event passes through.

Hard rule (from blueprint §4.1):
  UNTRUSTED_DATA may be summarized, indexed, or displayed.
  It can NEVER trigger a tool call or change a permission tier.
  Only TRUSTED_COMMAND initiates action.
"""

PROVENANCE_MAP: dict[str, str] = {
    "user_typed":       "TRUSTED_COMMAND",
    "user_spoke":       "TRUSTED_COMMAND",
    "file_contents":    "UNTRUSTED_DATA",
    "clipboard_text":   "UNTRUSTED_DATA",
    "web_page_content": "UNTRUSTED_DATA",
    "model_output":     "UNTRUSTED_DATA",  # Until it passes Action Validation Layer
}

TRUSTED   = "TRUSTED_COMMAND"
UNTRUSTED = "UNTRUSTED_DATA"


def tag(source: str) -> str:
    """
    Return the provenance label for a source key.
    Unknown sources are treated as UNTRUSTED_DATA — never default to trusted.
    """
    return PROVENANCE_MAP.get(source, UNTRUSTED)


def assert_trusted(provenance: str) -> None:
    """Raise PermissionError if provenance is not TRUSTED_COMMAND."""
    if provenance != TRUSTED:
        raise PermissionError(
            f"Action attempted from untrusted provenance '{provenance}'. "
            "Only TRUSTED_COMMAND may initiate tool calls."
        )
