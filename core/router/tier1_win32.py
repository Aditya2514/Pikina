"""
Tier 1 Intent Router — deterministic phrase-to-capability mapping.
No models. No network. No AI. Pure regex matching.
A Tier 1 command executes with the model layer completely unloaded.
"""
import re
from typing import Optional, Callable, Tuple

from core.registry.loader import CapabilityRegistry
from core.registry.gatekeeper import request_consent
from core.eventbus.bus import EventBus
from core.mcm.provenance import TRUSTED

# ---------------------------------------------------------------------------
# Route table
# Each entry: (compiled_regex, tool_name, params_builder_fn)
# params_builder_fn receives the re.Match and returns a params dict.
# ---------------------------------------------------------------------------

def _app_params(match) -> dict:
    """Resolve matched app name to an executable."""
    name = match.group("app").strip().lower()
    return {"path": name}  # open_app.py handles alias resolution


ROUTES: list[Tuple[re.Pattern, str, Callable]] = [
    # Open VS Code (explicit variants first)
    (re.compile(r"open\s+(?P<app>vs\s*code|vscode)", re.I),          "app.open",          _app_params),
    # Open any named app
    (re.compile(r"open\s+(?P<app>[\w\s]+)",           re.I),          "app.open",          _app_params),
    # Lock screen
    (re.compile(r"lock\s+(my\s+)?(screen|workstation|pc|computer|laptop)", re.I),
                                                                       "system.lock_screen", lambda m: {}),
    # Find file
    (re.compile(r"find\s+(file\s+)?(?P<name>[\w.*\-]+)", re.I),       "fs.find_file",      lambda m: {"name": m.group("name"), "root": "."}),
]


class Tier1Router:
    def __init__(self, registry: Optional[CapabilityRegistry] = None):
        self._registry = registry or CapabilityRegistry()
        self._bus      = EventBus()

    def route(self, text: str) -> dict:
        """
        Match text against ROUTES and execute the first match.
        Returns a result dict. Never blocks waiting for a model.
        """
        text = text.strip()

        for pattern, tool, params_fn in ROUTES:
            match = pattern.fullmatch(text) or pattern.match(text)
            if match:
                return self._execute(tool, params_fn(match), raw=text)

        return {
            "status":  "no_match",
            "message": f"No Tier 1 route matched: '{text}'",
            "hint":    "Try: 'open vs code', 'lock screen', 'find file resume.pdf'",
        }

    def _execute(self, tool: str, params: dict, raw: str) -> dict:
        try:
            manifest = self._registry.get_manifest(tool)
        except KeyError:
            return {"status": "error", "reason": f"Unknown tool in route table: '{tool}'"}

        level = manifest["permission_level"]

        # Gate Level 4+ and Level 3+ non-reversible actions with native Windows consent dialog
        if level >= 4 or (level >= 3 and not manifest.get("supports_rollback", True)):
            if not request_consent(tool, manifest["description"], params):
                self._bus.publish(
                    topic="router.denied",
                    payload={"tool": tool, "params": params, "raw": raw},
                    provenance=TRUSTED,
                    permission_level=level,
                )
                return {"status": "denied", "reason": "User declined consent dialog."}

        self._bus.publish(
            topic="router.executing",
            payload={"tool": tool, "params": params, "raw": raw, "tier": 1},
            provenance=TRUSTED,
            permission_level=level,
        )

        result = self._registry.execute(tool, params)

        self._bus.publish(
            topic="router.result",
            payload={"tool": tool, "result": result, "tier": 1},
            provenance=TRUSTED,
            permission_level=level,
        )

        return result
