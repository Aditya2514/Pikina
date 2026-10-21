"""
Capability Registry.

Loads paired manifest (.json) + handler (.py) files from Pikina's core
capabilities folder and from every enabled plugin (see pikina.plugins).
Adding a command = one .json + one .py; its typed phrases live in the
manifest's "patterns", so neither the router nor the MCM changes.
"""
import json
import os
import re
import importlib.util
from pathlib import Path
from typing import Dict, Any

_CAPABILITIES_DIR = Path(__file__).parent / "capabilities"


class CapabilityRegistry:
    def __init__(self, load_plugins: bool = True):
        self._manifests: Dict[str, dict] = {}
        self._handlers:  Dict[str, Any]  = {}
        self._origin:    Dict[str, str]  = {}   # tool -> "core" or plugin name
        self._routes = None
        self.plugins: list = []
        self.plugin_errors: list = []
        self.disabled_plugins: list = []
        self._load_dir(_CAPABILITIES_DIR, origin="core")
        if load_plugins:
            self._load_plugins()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _read_dir(self, folder: Path, origin: str):
        """Read every manifest/handler pair in a folder. Raises on the first broken one."""
        manifests, handlers = {}, {}
        for json_file in sorted(folder.glob("*.json")):
            py_file = json_file.with_suffix(".py")
            if not py_file.exists():
                raise ValueError(f"'{json_file.name}' has no handler '{py_file.name}'")
            manifest = json.loads(json_file.read_text(encoding="utf-8"))
            tool = manifest["tool"]
            for pattern in manifest.get("patterns", []):
                re.compile(pattern["regex"], re.I)  # a bad regex fails here, not at command time
            spec = importlib.util.spec_from_file_location(f"cap_{origin}_{json_file.stem}", py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, "run"):
                raise ValueError(f"'{py_file.name}' has no run(params) function")
            manifests[tool], handlers[tool] = manifest, module
        return manifests, handlers

    def _load_dir(self, folder: Path, origin: str) -> None:
        manifests, handlers = self._read_dir(folder, origin)
        self._add(manifests, handlers, origin)

    def _add(self, manifests: dict, handlers: dict, origin: str) -> None:
        for tool, manifest in manifests.items():
            if tool in self._manifests:
                raise ValueError(f"tool '{tool}' is already provided by '{self._origin[tool]}'")
        for tool, manifest in manifests.items():
            self._manifests[tool] = manifest
            self._handlers[tool] = handlers[tool]
            self._origin[tool] = origin
            if os.getenv("PIKINA_DEBUG"):
                print(f"[Registry] [OK] {tool:30s} (level {manifest['permission_level']}, {origin})")
        self._routes = None

    def _load_plugins(self) -> None:
        from pikina import plugins as plugin_mod
        found, errors, disabled = plugin_mod.discover()
        self.plugin_errors.extend(errors)
        self.disabled_plugins = disabled
        for plugin in found:
            try:
                manifests, handlers = self._read_dir(plugin.capabilities_dir, plugin.name)
                self._add(manifests, handlers, plugin.name)  # all of a plugin's tools, or none
                self.plugins.append(plugin)
            except Exception as exc:
                self.plugin_errors.append({"plugin": plugin.name, "error": f"{type(exc).__name__}: {exc}"})
        for err in self.plugin_errors:
            print(f"[Plugins] Skipped '{err['plugin']}': {err['error']}")

    def routes(self) -> list:
        """
        Every typed phrase from every manifest, as (regex, tool, static_params, handler),
        lowest priority number first; equal priorities keep load order (core first).
        """
        if self._routes is None:
            table = []
            for order, (tool, manifest) in enumerate(self._manifests.items()):
                for pattern in manifest.get("patterns", []):
                    table.append((pattern.get("priority", 1000), order,
                                  re.compile(pattern["regex"], re.I), tool,
                                  dict(pattern.get("params", {})), self._handlers[tool]))
            table.sort(key=lambda r: (r[0], r[1]))
            self._routes = [(rx, tool, static, handler) for _, _, rx, tool, static, handler in table]
        return self._routes

    def origin(self, tool: str) -> str:
        return self._origin.get(tool, "unknown")

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
                "source":           self._origin.get(m["tool"], "core"),
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
