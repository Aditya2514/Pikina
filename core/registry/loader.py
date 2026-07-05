"""
Capability Registry loader.
Scans core/registry/capabilities/ for paired manifest (.json) + handler (.py) files.
Adding a new tool = write one .json + one .py — zero changes to router or MCM.
"""
import json
import importlib.util
from pathlib import Path
from typing import Dict, Any

_CAPABILITIES_DIR = Path(__file__).parent / "capabilities"


class CapabilityRegistry:
    def __init__(self):
        self._manifests: Dict[str, dict] = {}
        self._handlers:  Dict[str, Any]  = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_all(self) -> None:
        for json_file in sorted(_CAPABILITIES_DIR.glob("*.json")):
            py_file = json_file.with_suffix(".py")
            if not py_file.exists():
                print(f"[Registry] WARN: '{json_file.name}' has no handler '{py_file.name}' — skipping.")
                continue

            manifest = json.loads(json_file.read_text(encoding="utf-8"))
            tool     = manifest["tool"]

            self._manifests[tool] = manifest

            spec   = importlib.util.spec_from_file_location(f"cap_{json_file.stem}", py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._handlers[tool] = module

            print(f"[Registry] [OK] {tool:30s} (level {manifest['permission_level']})")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_manifest(self, tool: str) -> dict:
        if tool not in self._manifests:
            raise KeyError(f"Unknown tool: '{tool}'")
        return self._manifests[tool]

    def get_handler(self, tool: str):
        if tool not in self._handlers:
            raise KeyError(f"No handler for: '{tool}'")
        return self._handlers[tool]

    def list_tools(self) -> list:
        return [
            {
                "tool":             m["tool"],
                "description":      m["description"],
                "permission_level": m["permission_level"],
                "supports_rollback":m["supports_rollback"],
                "requires_network": m["requires_network"],
            }
            for m in self._manifests.values()
        ]

    def execute(self, tool: str, params: dict) -> dict:
        """
        Execute a capability. Caller is responsible for permission gating.
        Returns the handler's result dict.
        """
        handler = self.get_handler(tool)
        if not hasattr(handler, "run"):
            raise AttributeError(f"Handler for '{tool}' must expose a 'run(params) -> dict' function.")
        return handler.run(params)
